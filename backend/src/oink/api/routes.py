"""The HTTP API, used by the web app (session cookie) and by other apps (scoped tokens).
Every data route is scoped to the calling user. OpenAPI docs at /api/docs."""

from __future__ import annotations

import secrets
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from oink.api.deps import Caller, admin_only, browser_only, caller, get_db, require
from oink.api.schemas import (
    AccountIn,
    AccountOut,
    AccountPatch,
    Ask,
    BankAccess,
    BankLink,
    ConnectionOut,
    ImportOut,
    InviteAccept,
    KeyLink,
    Login,
    PasswordChange,
    ProfilePatch,
    ReportOut,
    RequestOut,
    Setup,
    Signup,
    TempPassword,
    TokenIn,
    TokenOut,
    TransactionIn,
    TransactionOut,
    TransactionPatch,
    UserIn,
    UserOut,
    UserPatch,
    user_out,
)
from oink.core.config import Settings
from oink.core.db import (
    Account,
    ApiToken,
    Connection,
    Report,
    Setting,
    StatementRequest,
    Transaction,
    User,
)
from oink.core.money import CRYPTO, FIAT, MoneyError, as_text, normalise_code, to_minor
from oink.core.security import (
    COOKIE,
    SCOPES,
    AuthError,
    Vault,
    authenticate,
    clean_username,
    create_invite,
    create_token,
    create_user,
    end_session,
    invite_user,
    new_session,
    session_user,
    set_password,
    temporary_password,
    unlock,
    user_count,
    verify_password,
)
from oink.providers import PROVIDERS, ProviderError
from oink.providers.enablebanking import COUNTRIES, EnableBanking
from oink.services import fx, updates
from oink.services import reports as rep
from oink.services.ai import AI, AIError
from oink.services.connections import (
    banking,
    finish_bank,
    forget_pending,
    link_with_key,
    save_banking,
    sync,
)
from oink.services.importer import ImportError_, parse_statement
from oink.services.jobs import categorize_pending, monthly_report
from oink.services.ledger import (
    CATEGORIES,
    INTERNAL,
    NewMovement,
    add_movements,
    learn_name,
    learn_rule,
)
from oink.services.review import review
from oink.services.statements import MAX_FILE, apply, pick_account
from oink.services.transfers import match_transfers, untracked
from oink.services.transfers import unlink as unlink_transfer

api = APIRouter(prefix="/api")
MONTH = r"^\d{4}-\d{2}$"


def settings_of(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def ai_of(request: Request) -> AI:
    ai: AI = request.app.state.ai
    return ai


def vault_of(request: Request) -> Vault:
    vault: Vault = request.app.state.vault
    return vault


def current_month() -> str:
    return date.today().strftime("%Y-%m")


def fail(status: int, exc: Exception) -> HTTPException:
    return HTTPException(status, getattr(exc, "code", None) or str(exc))


def public(request: Request, path: str) -> str:
    return f"{settings_of(request).public_url.rstrip('/')}{path}"


# -- auth ------------------------------------------------------------------------------
@api.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": updates.current_version()}


@api.get("/auth/status")
def auth_status(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    s = settings_of(request)
    user = session_user(db, request.cookies.get(COOKIE))
    return {
        "configured": user_count(db) > 0,
        "user": user_out(user).model_dump(mode="json") if user else None,
        "ai": ai_of(request).enabled,
        "ai_reads_text": ai_of(request).can_read_text,
        "banking": banking(db, s) is not None,
        "version": updates.current_version(),
        "sso": s.oidc_name if request.app.state.oidc.enabled else None,
        "password_login": s.password_login or not request.app.state.oidc.enabled,
        "signup": signup_open(db, s),
    }


def signup_open(db: Session, s: Settings) -> bool:
    """People may create their own account: OINK_SIGNUP, unless the admin changed it."""
    row = db.get(Setting, "signup")
    return (row.value == "on") if row is not None else s.signup


def _login(response: Response, db: Session, user: User, settings: Settings) -> None:
    cookie = new_session(db, user, settings.session_days)
    response.set_cookie(
        COOKIE,
        cookie,
        max_age=settings.session_days * 86400,
        httponly=True,
        samesite="strict",
        secure=settings.public_url.startswith("https"),
    )


@api.post("/auth/setup")
def setup(
    body: Setup, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, bool]:
    """First run only: create the admin."""
    if user_count(db) > 0:
        raise HTTPException(409, "already_configured")
    try:
        currency = normalise_code(body.base_currency)
        user = create_user(
            db,
            body.username,
            body.password,
            admin=True,
            language=body.language,
            base_currency=currency,
        )
    except (AuthError, MoneyError) as exc:
        raise fail(422, exc) from exc
    _login(response, db, user, settings_of(request))
    return {"ok": True}


@api.post("/auth/login")
def login(
    body: Login, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, bool]:
    s = settings_of(request)
    if not s.password_login and request.app.state.oidc.enabled:
        raise HTTPException(403, "sso_only")
    try:
        user = authenticate(db, body.username, body.password)
    except AuthError as exc:
        raise fail(423, exc) from exc
    if user is None:
        raise HTTPException(401, "wrong_credentials")
    _login(response, db, user, settings_of(request))
    return {"ok": True}


@api.post("/auth/signup")
def signup(
    body: Signup, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, bool]:
    """Anyone who reaches the app creates their own account (when the admin allows it)."""
    s = settings_of(request)
    if not signup_open(db, s) or (not s.password_login and request.app.state.oidc.enabled):
        raise HTTPException(403, "signup_closed")
    try:
        currency = normalise_code(body.base_currency or s.currency)
        user = create_user(
            db, body.username, body.password, name=body.name, language=body.language,
            base_currency=currency,
        )  # fmt: skip
    except (AuthError, MoneyError) as exc:
        raise fail(422, exc) from exc
    _login(response, db, user, s)
    return {"ok": True}


@api.get("/auth/invite/{token}")
def invite_info(token: str, db: Session = Depends(get_db)) -> dict[str, str]:
    found = invite_user(db, token)
    if found is None:
        raise HTTPException(404, "invite_invalid")
    return {"username": found[1].username, "name": found[1].name}


@api.post("/auth/invite")
def accept_invite(
    body: InviteAccept, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, bool]:
    found = invite_user(db, body.token)
    if found is None:
        raise HTTPException(404, "invite_invalid")
    invite, user = found
    try:
        set_password(db, user, body.password)
    except AuthError as exc:
        raise fail(422, exc) from exc
    invite.used = True
    db.commit()
    _login(response, db, user, settings_of(request))
    return {"ok": True}


@api.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, bool]:
    end_session(db, request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE)
    return {"ok": True}


@api.post("/auth/password")
def change_password(
    body: PasswordChange,
    request: Request,
    response: Response,
    who: Caller = Depends(browser_only),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    user = db.get(User, who.user_id)
    assert user is not None
    if not verify_password(user.password_hash, body.current):
        raise HTTPException(401, "wrong_password")
    try:
        set_password(db, user, body.new)
    except AuthError as exc:
        raise fail(422, exc) from exc
    _login(response, db, user, settings_of(request))  # the other sessions were closed
    return {"ok": True}


@api.get("/me", response_model=UserOut)
def me(who: Caller = Depends(caller)) -> UserOut:
    return user_out(who.user)


@api.patch("/me", response_model=UserOut)
def update_me(
    body: ProfilePatch, who: Caller = Depends(browser_only), db: Session = Depends(get_db)
) -> UserOut:
    user = db.get(User, who.user_id)
    assert user is not None
    if body.name is not None:
        user.name = body.name.strip() or user.username
    if body.language is not None:
        user.language = body.language
    if body.base_currency is not None:
        try:
            user.base_currency = normalise_code(body.base_currency)
        except MoneyError as exc:
            raise fail(422, exc) from exc
    db.commit()
    return user_out(user)


# -- users (admin) ---------------------------------------------------------------------
@api.get("/users", response_model=list[UserOut], dependencies=[Depends(admin_only)])
def list_users(db: Session = Depends(get_db)) -> list[UserOut]:
    return [user_out(u) for u in db.scalars(select(User).order_by(User.created))]


@api.post("/users", dependencies=[Depends(admin_only)])
def add_user(body: UserIn, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Creates the user and returns a one-time invite link (valid 7 days)."""
    try:
        user = create_user(
            db,
            body.username,
            None,
            name=body.name,
            base_currency=settings_of(request).currency,
        )
    except AuthError as exc:
        raise fail(422, exc) from exc
    token = create_invite(db, user)
    return {
        "user": user_out(user).model_dump(mode="json"),
        "invite_url": public(request, f"/invite/{token}"),
    }


@api.post("/users/{user_id}/invite", dependencies=[Depends(admin_only)])
def reinvite(user_id: int, request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    """A new link (also to reset a forgotten password)."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user_unknown")
    return {"invite_url": public(request, f"/invite/{create_invite(db, user)}")}


@api.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int, body: UserPatch, who: Caller = Depends(admin_only), db: Session = Depends(get_db)
) -> UserOut:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user_unknown")
    if user.id == who.user_id and body.disabled:
        raise HTTPException(422, "cannot_demote_self")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("username") is not None:
        try:
            name = clean_username(changes["username"])
        except AuthError as exc:
            raise fail(422, exc) from exc
        if name != user.username and db.scalar(select(User).where(User.username == name)):
            raise HTTPException(422, "username_taken")
        changes["username"] = name
    if changes.get("name") is not None:
        changes["name"] = changes["name"].strip() or user.username
    for key, value in changes.items():
        if value is not None:
            setattr(user, key, value)
    db.commit()
    return user_out(user)


@api.post("/users/{user_id}/password", dependencies=[Depends(admin_only)])
def temp_password(
    user_id: int, body: TempPassword, db: Session = Depends(get_db)
) -> dict[str, str]:
    """A temporary password for someone locked out: shown once, to be changed at their next
    sign-in. Their sessions are closed; their data is untouched."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user_unknown")
    password = body.password or temporary_password()
    try:
        set_password(db, user, password, temporary=True)
    except AuthError as exc:
        raise fail(422, exc) from exc
    return {"password": password}


@api.post("/users/{user_id}/unlock", response_model=UserOut, dependencies=[Depends(admin_only)])
def unlock_user(user_id: int, db: Session = Depends(get_db)) -> UserOut:
    """Opens an account that waits after too many wrong passwords, and re-enables it."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user_unknown")
    user.disabled = False
    unlock(db, user)
    return user_out(user)


class SignupIn(BaseModel):
    open: bool


@api.put("/system/signup", dependencies=[Depends(admin_only)])
def set_signup(body: SignupIn, db: Session = Depends(get_db)) -> dict[str, bool]:
    row = db.get(Setting, "signup")
    value = "on" if body.open else "off"
    if row is None:
        db.add(Setting(key="signup", value=value))
    else:
        row.value = value
    db.commit()
    return {"open": body.open}


@api.delete("/users/{user_id}")
def delete_user(
    user_id: int, who: Caller = Depends(admin_only), db: Session = Depends(get_db)
) -> dict[str, bool]:
    """Deletes the user AND all their data."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "user_unknown")
    if user.id == who.user_id:
        raise HTTPException(422, "cannot_delete_self")
    db.delete(user)
    db.commit()
    return {"ok": True}


# -- tokens ----------------------------------------------------------------------------
@api.get("/scopes")
def scopes() -> list[str]:
    return list(SCOPES)


@api.get("/tokens", response_model=list[TokenOut])
def list_tokens(who: Caller = Depends(browser_only), db: Session = Depends(get_db)) -> Any:
    return list(
        db.scalars(
            select(ApiToken)
            .where(ApiToken.user_id == who.user_id, ApiToken.revoked.is_(False))
            .order_by(ApiToken.created.desc())
        )
    )


@api.post("/tokens")
def new_token(
    body: TokenIn, who: Caller = Depends(browser_only), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """The token is in the answer ONLY here: copy it now."""
    if who.user.is_admin:
        raise HTTPException(403, "admin_has_no_finances")
    try:
        token, secret = create_token(db, who.user, body.name, body.scopes)
    except AuthError as exc:
        raise fail(422, exc) from exc
    return {"token": secret, **TokenOut.model_validate(token).model_dump(mode="json")}


@api.delete("/tokens/{token_id}")
def revoke_token(
    token_id: int, who: Caller = Depends(browser_only), db: Session = Depends(get_db)
) -> dict[str, bool]:
    token = db.get(ApiToken, token_id)
    if token is None or token.user_id != who.user_id:
        raise HTTPException(404, "token_unknown")
    token.revoked = True
    db.commit()
    return {"ok": True}


# -- accounts --------------------------------------------------------------------------
@api.get("/currencies")
def currencies() -> dict[str, list[str]]:
    return {"fiat": FIAT, "crypto": sorted(CRYPTO)}


@api.get("/accounts", response_model=list[AccountOut])
def accounts(
    archived: bool = False,
    who: Caller = Depends(require("accounts:read")),
    db: Session = Depends(get_db),
) -> list[AccountOut]:
    query = select(Account).where(Account.user_id == who.user_id).order_by(Account.created)
    if not archived:
        query = query.where(Account.archived.is_(False))
    return [AccountOut.of(a) for a in db.scalars(query)]


def _clean_iban(iban: str) -> str:
    return iban.replace(" ", "").upper()


@api.post("/accounts", response_model=AccountOut)
def create_account(
    body: AccountIn, who: Caller = Depends(require("accounts:write")), db: Session = Depends(get_db)
) -> AccountOut:
    try:
        currency = normalise_code(body.currency)
        balance = to_minor(body.balance, currency) if body.balance not in (None, "") else None
    except MoneyError as exc:
        raise fail(422, exc) from exc
    account = Account(
        user_id=who.user_id,
        name=body.name,
        kind="crypto" if currency in CRYPTO and body.kind == "checking" else body.kind,
        institution=body.institution,
        iban=_clean_iban(body.iban),
        currency=currency,
        color=body.color,
        source="manual",
        balance=balance,
        balance_at=datetime.now(UTC) if balance is not None else None,
    )
    db.add(account)
    db.commit()
    return AccountOut.of(account)


def _account(db: Session, who: Caller, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if account is None or account.user_id != who.user_id:
        raise HTTPException(404, "account_unknown")
    return account


@api.patch("/accounts/{account_id}", response_model=AccountOut)
def update_account(
    account_id: int,
    body: AccountPatch,
    who: Caller = Depends(require("accounts:write")),
    db: Session = Depends(get_db),
) -> AccountOut:
    account = _account(db, who, account_id)
    changes = body.model_dump(exclude_unset=True)
    if "balance" in changes:
        value = changes.pop("balance")
        try:
            account.balance = to_minor(value, account.currency) if value not in (None, "") else None
        except MoneyError as exc:
            raise fail(422, exc) from exc
        account.balance_at = datetime.now(UTC)
    if "iban" in changes:
        changes["iban"] = _clean_iban(changes["iban"] or "")
    for key, value in changes.items():
        setattr(account, key, value)
    db.commit()
    return AccountOut.of(account)


@api.delete("/accounts/{account_id}")
def delete_account(
    account_id: int,
    who: Caller = Depends(require("accounts:write")),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    """Deletes the account and its movements (archive it to keep the history)."""
    db.delete(_account(db, who, account_id))
    db.commit()
    return {"ok": True}


# -- transactions ----------------------------------------------------------------------
def running_balances(db: Session, user: User) -> tuple[dict[int, int], dict[int, float]]:
    """Balance of its account right after each movement, and the total of all accounts
    (in the main currency) at that point: walking back from today's balances."""
    book = rep.Book.of(db, user)
    accounts = [a for a in book.accounts() if a.balance is not None]
    by_account = {a.id: a.balance or 0 for a in accounts}
    total = sum(book.value(a.balance or 0, a.currency) or 0.0 for a in accounts)
    after: dict[int, int] = {}
    total_after: dict[int, float] = {}
    for t in db.scalars(
        select(Transaction)
        .where(
            Transaction.user_id == user.id,
            Transaction.account_id.in_(by_account),
            Transaction.category_by != "split",
        )
        .order_by(Transaction.day.desc(), func.coalesce(Transaction.seq, Transaction.id).desc())
    ):
        after[t.id] = by_account[t.account_id]
        total_after[t.id] = round(total, 2)
        by_account[t.account_id] -= t.amount
        total -= book.value(t.amount, t.currency) or 0.0
    return after, total_after


@api.get("/transactions", response_model=list[TransactionOut])
def transactions(
    account_id: int | None = None,
    month: str | None = Query(default=None, pattern=MONTH),
    category: str | None = None,
    categories: str | None = Query(default=None, max_length=400, description="comma separated"),
    direction: Literal["in", "out"] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: Literal["date", "amount_desc", "amount_asc"] = "date",
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    who: Caller = Depends(require("transactions:read")),
    db: Session = Depends(get_db),
) -> list[TransactionOut]:
    """Movements, filtered (account, period, categories, money in or out, words) and sorted
    (newest first, or by size). Each carries its account's balance right after it and the
    total of all accounts at that point."""
    order: tuple[Any, ...] = {
        "date": (Transaction.day.desc(), func.coalesce(Transaction.seq, Transaction.id).desc()),
        "amount_desc": (func.abs(Transaction.amount).desc(), Transaction.day.desc()),
        "amount_asc": (func.abs(Transaction.amount).asc(), Transaction.day.desc()),
    }[sort]
    query = (
        select(Transaction)
        .where(Transaction.user_id == who.user_id, Transaction.category_by != "split")
        .order_by(*order)
    )
    if account_id is not None:
        query = query.where(Transaction.account_id == account_id)
    if month:
        start, end = rep.month_bounds(month)
        query = query.where(Transaction.day >= start, Transaction.day < end)
    if date_from:
        query = query.where(Transaction.day >= date_from)
    if date_to:
        query = query.where(Transaction.day <= date_to)
    wanted = [c.strip().lower() for c in (categories or "").split(",") if c.strip()]
    if category is not None:
        wanted.append(category)
    if wanted:
        query = query.where(
            or_(
                Transaction.category.in_([c for c in wanted if c != "none"]),
                *([Transaction.category == ""] if "none" in wanted else []),
            )
        )
    if direction == "in":
        query = query.where(Transaction.amount > 0)
    elif direction == "out":
        query = query.where(Transaction.amount < 0)
    if q:
        like = f"%{q}%"
        query = query.where(
            or_(
                Transaction.description.ilike(like),
                Transaction.counterparty.ilike(like),
                Transaction.note.ilike(like),
            )
        )
    rows = list(db.scalars(query.limit(limit).offset(offset)))
    after, total_after = running_balances(db, who.user)
    partners = {
        tx_id: acc_id
        for tx_id, acc_id in db.execute(
            select(Transaction.id, Transaction.account_id).where(
                Transaction.id.in_([t.transfer_id for t in rows if t.transfer_id])
            )
        )
    }
    out = []
    for t in rows:
        item = TransactionOut.of(t)
        item.partner_account_id = partners.get(t.transfer_id) if t.transfer_id else None
        if t.id in after:
            item.balance_after = as_text(after[t.id], t.currency)
            item.total_after = total_after[t.id]
        out.append(item)
    return out


class ReviewIn(BaseModel):
    date_from: date | None = None
    date_to: date | None = None


@api.post("/transactions/review")
async def review_movements(
    body: ReviewIn,
    request: Request,
    who: Caller = Depends(require("transactions:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Proposals for a period: savings plans, fixed costs, categories to fix."""
    return await review(db, who.user, ai_of(request), body.date_from, body.date_to)


@api.post("/transactions", response_model=TransactionOut)
def add_transaction(
    body: TransactionIn,
    who: Caller = Depends(require("transactions:write")),
    db: Session = Depends(get_db),
) -> TransactionOut:
    account = _account(db, who, body.account_id)
    try:
        amount = to_minor(body.amount, account.currency)
    except MoneyError as exc:
        raise fail(422, exc) from exc
    movement = NewMovement(
        day=body.day,
        amount=amount,
        description=body.description,
        counterparty=body.counterparty,
        category=body.category.strip().lower(),
        note=body.note,
    )
    result = add_movements(db, account, [movement], "manual" if who.browser else "api")
    if not result.added:
        raise HTTPException(409, "duplicate")
    if account.balance is not None and account.source == "manual":
        account.balance += amount
        db.commit()
    return TransactionOut.of(result.added[0])


def _tx(db: Session, who: Caller, tx_id: int) -> Transaction:
    tx = db.get(Transaction, tx_id)
    if tx is None or tx.user_id != who.user_id:
        raise HTTPException(404, "transaction_unknown")
    return tx


@api.patch("/transactions/{tx_id}", response_model=TransactionOut)
def update_transaction(
    tx_id: int,
    body: TransactionPatch,
    who: Caller = Depends(require("transactions:write")),
    db: Session = Depends(get_db),
) -> TransactionOut:
    tx = _tx(db, who, tx_id)
    if body.category is not None:
        tx.category, tx.category_by = body.category.strip().lower(), "user"
        if tx.category not in INTERNAL:
            unlink_transfer(db, tx)  # not a transfer after all: free the other leg too
        if body.learn and tx.category:
            learn_rule(db, tx, tx.category)
    if body.note is not None:
        tx.note = body.note
    if body.counterparty is not None and body.counterparty.strip():
        name = body.counterparty.strip()
        if body.learn:
            learn_name(db, tx, name)  # every movement with the same code, now and later
        else:
            tx.counterparty = name
    db.commit()
    return TransactionOut.of(tx)


class SplitPartIn(BaseModel):
    amount: str = Field(min_length=1, max_length=32)  # positive: a share of the movement
    description: str = Field(min_length=1, max_length=200)
    category: str = Field(default="", max_length=40)


class SplitTxIn(BaseModel):
    parts: list[SplitPartIn] = Field(min_length=2, max_length=12)


@api.post("/transactions/{tx_id}/split", response_model=list[TransactionOut])
def split_transaction(
    tx_id: int,
    body: SplitTxIn,
    who: Caller = Depends(require("transactions:write")),
    db: Session = Depends(get_db),
) -> list[TransactionOut]:
    """One payment, several things (a server and a domain in one OVH charge): the movement
    becomes its parts, each with its own description and category. The parts must add up
    to the movement; the original stays hidden, so the bank's copy is still recognised."""
    tx = _tx(db, who, tx_id)
    if tx.parent_id is not None or tx.category_by == "split":
        raise HTTPException(422, "already_split")
    try:
        amounts = [abs(to_minor(p.amount.replace(",", "."), tx.currency)) for p in body.parts]
    except MoneyError as exc:
        raise fail(422, exc) from exc
    if sum(amounts) != abs(tx.amount) or any(a == 0 for a in amounts):
        raise HTTPException(422, "split_total")
    sign = -1 if tx.amount < 0 else 1
    parts = []
    for i, (part, amount) in enumerate(zip(body.parts, amounts, strict=True)):
        child = Transaction(
            user_id=tx.user_id, account_id=tx.account_id, day=tx.day, amount=sign * amount,
            currency=tx.currency, counterparty=part.description.strip(),
            description=tx.counterparty or tx.description,  # still found by the payee's text
            category=part.category.strip().lower(), category_by="user" if part.category else "",
            source="split", fingerprint=f"split:{tx.id}:{i}", parent_id=tx.id,
            seq=tx.seq, document_id=tx.document_id,
        )  # fmt: skip
        db.add(child)
        parts.append(child)
    unlink_transfer(db, tx)
    tx.plan_id = None
    tx.category_by = "split"
    db.commit()
    return [TransactionOut.of(c) for c in parts]


@api.delete("/transactions/{tx_id}/split")
def unsplit_transaction(
    tx_id: int, who: Caller = Depends(require("transactions:write")), db: Session = Depends(get_db)
) -> dict[str, bool]:
    """Put a split movement back together (call it with the original or any of its parts)."""
    tx = _tx(db, who, tx_id)
    parent = db.get(Transaction, tx.parent_id) if tx.parent_id else tx
    if parent is None or parent.category_by != "split":
        raise HTTPException(422, "not_split")
    for child in db.scalars(select(Transaction).where(Transaction.parent_id == parent.id)):
        db.delete(child)
    parent.category_by = ""
    db.commit()
    return {"ok": True}


@api.delete("/transactions/{tx_id}")
def delete_transaction(
    tx_id: int,
    who: Caller = Depends(require("transactions:write")),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    tx = _tx(db, who, tx_id)
    unlink_transfer(db, tx)
    account = db.get(Account, tx.account_id)
    if account is not None and account.source == "manual" and account.balance is not None:
        account.balance -= tx.amount
    db.delete(tx)
    db.commit()
    return {"ok": True}


@api.get("/transfers/untracked")
def untracked_transfers(
    who: Caller = Depends(require("transactions:read")), db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    """Money sent to own banks/brokers that are not in Oink yet (add them to see it)."""
    return untracked(db, who.user)


@api.post("/transfers/match")
def rematch_transfers(
    who: Caller = Depends(require("transactions:write")), db: Session = Depends(get_db)
) -> dict[str, int]:
    return {"paired": match_transfers(db, who.user)}


@api.get("/categories")
def categories(
    who: Caller = Depends(require("transactions:read")), db: Session = Depends(get_db)
) -> dict[str, list[str]]:
    used = set(
        db.scalars(
            select(Transaction.category).where(Transaction.user_id == who.user_id).distinct()
        )
    )
    return {"builtin": CATEGORIES, "custom": sorted(c for c in used if c and c not in CATEGORIES)}


@api.post("/transactions/categorize")
async def categorize(
    request: Request,
    who: Caller = Depends(require("transactions:write")),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    return {"categorized": await categorize_pending(db, ai_of(request), who.user_id)}


# -- statements ------------------------------------------------------------------------
@api.post("/imports", response_model=ImportOut)
async def import_statement(
    request: Request,
    file: UploadFile = File(...),
    account_id: int | None = Form(default=None),
    who: Caller = Depends(require("imports:write")),
    db: Session = Depends(get_db),
) -> ImportOut:
    """CSV, OFX/QFX or PDF. Without `account_id` the IBAN on the statement picks the account."""
    raw = await file.read(MAX_FILE + 1)
    if len(raw) > MAX_FILE:
        raise HTTPException(413, "file_too_big")
    try:
        parsed = await parse_statement(file.filename or "statement", raw, ai_of(request))
        account = pick_account(db, who.user, parsed, account_id)
    except ImportError_ as exc:
        raise fail(422, exc) from exc
    return ImportOut(**apply(db, account, parsed).__dict__)


@api.get("/requests", response_model=list[RequestOut])
def statement_requests(
    status: str = "open",
    who: Caller = Depends(require("requests:read")),
    db: Session = Depends(get_db),
) -> Any:
    return list(
        db.scalars(
            select(StatementRequest)
            .where(StatementRequest.user_id == who.user_id, StatementRequest.status == status)
            .order_by(StatementRequest.created)
        )
    )


@api.post("/requests/{req_id}/skip", response_model=RequestOut)
def skip_request(
    req_id: int, who: Caller = Depends(require("requests:write")), db: Session = Depends(get_db)
) -> Any:
    req = db.get(StatementRequest, req_id)
    if req is None or req.user_id != who.user_id:
        raise HTTPException(404, "request_unknown")
    req.status, req.done_at = "skipped", datetime.now(UTC)
    db.commit()
    return req


# -- reports ---------------------------------------------------------------------------
@api.get("/reports/summary")
def summary(
    month: str | None = Query(default=None, pattern=MONTH),
    who: Caller = Depends(require("reports:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return rep.compare(rep.Book.of(db, who.user), month or current_month())


@api.get("/reports/overview")
async def overview(
    who: Caller = Depends(require("reports:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Everything the home screen needs in one call."""
    from oink.services.planning import holdings_value

    book = rep.Book.of(db, who.user)
    invest = await holdings_value(db, book, refresh=False)
    worth = rep.net_worth(book)
    worth["investments"] = invest["total"]
    worth["total"] = round(worth["total"] + invest["total"], 2)
    return {
        "currency": book.base,
        "net_worth": worth,
        "month": rep.compare(book, current_month()),
        "series": rep.series(book, 12),
        "balance": rep.balance_history(book, 180),
        "recurring": rep.recurring(book),
        "open_requests": db.scalar(
            select(func.count())
            .select_from(StatementRequest)
            .where(StatementRequest.user_id == who.user_id, StatementRequest.status == "open")
        ),
    }


@api.get("/reports/recurring")
def recurring(
    who: Caller = Depends(require("reports:read")), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    return rep.recurring(rep.Book.of(db, who.user))


@api.get("/reports", response_model=list[ReportOut])
def list_reports(
    who: Caller = Depends(require("reports:read")), db: Session = Depends(get_db)
) -> Any:
    return list(
        db.scalars(
            select(Report)
            .where(Report.user_id == who.user_id)
            .order_by(Report.created.desc())
            .limit(24)
        )
    )


@api.get("/reports/months")
def report_months(
    who: Caller = Depends(require("reports:read")), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    """Months with movements, newest first, and whether each has its report."""
    done = set(
        db.scalars(
            select(Report.month).where(Report.user_id == who.user_id, Report.kind == "monthly")
        )
    )
    months = rep.months_with_data(rep.Book.of(db, who.user))
    return [{"month": m, "report": m in done} for m in reversed(months)]


@api.post("/reports/generate", response_model=ReportOut)
async def generate_report(
    request: Request,
    month: str | None = Query(default=None, pattern=MONTH),
    language: str = Query(default="", max_length=8),
    who: Caller = Depends(require("reports:write")),
    db: Session = Depends(get_db),
) -> Any:
    try:
        return await monthly_report(
            db, ai_of(request), who.user, month or current_month(), settings_of(request), language
        )
    except AIError as exc:
        raise fail(503, exc) from exc


@api.post("/ask")
async def ask(
    body: Ask,
    request: Request,
    language: str = Query(default="", max_length=8),
    who: Caller = Depends(require("ai:ask")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    facts = rep.facts_for_ai(
        rep.Book.of(db, who.user),
        body.month or current_month(),
        with_text=ai_of(request).config().send_descriptions,
    )
    try:
        answer = await ai_of(request).answer(
            body.question, facts, language or who.user.language or "en"
        )
    except AIError as exc:
        raise fail(503, exc) from exc
    return {"answer": answer}


# -- connections -----------------------------------------------------------------------
@api.get("/providers")
def providers(
    request: Request, who: Caller = Depends(caller), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """What can be linked, for the «add account» wizard."""
    from oink.providers.directory import DIRECTORY

    return {
        "banking": banking(db, settings_of(request)) is not None,
        "countries": COUNTRIES,
        "directory": DIRECTORY,
        "keyed": [
            {
                "id": p.id,
                "name": p.name,
                "kind": p.kind,
                "docs": p.docs,
                "movements": p.movements_supported,
                "fields": [
                    {"name": f.name, "secret": f.secret, "placeholder": f.placeholder}
                    for f in p.fields
                ],
            }
            for p in PROVIDERS.values()
        ],
    }


@api.get("/connections", response_model=list[ConnectionOut])
def connections(
    who: Caller = Depends(require("connections:read")), db: Session = Depends(get_db)
) -> Any:
    return list(
        db.scalars(
            select(Connection).where(
                Connection.user_id == who.user_id, Connection.status != "pending"
            )
        )
    )


@api.post("/connections/key", response_model=ConnectionOut)
async def link_key(
    body: KeyLink,
    request: Request,
    who: Caller = Depends(browser_only),
    db: Session = Depends(get_db),
) -> Any:
    """An exchange, fintech or wallet: checked right away by reading its accounts."""
    try:
        return await link_with_key(
            db, vault_of(request), who.user, body.provider, body.credentials, body.label
        )
    except ProviderError as exc:
        raise fail(422, exc) from exc


@api.get("/connections/banks")
async def banks(
    request: Request,
    country: str = Query(min_length=2, max_length=2),
    who: Caller = Depends(browser_only),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    bank = banking(db, settings_of(request))
    if bank is None:
        raise HTTPException(503, "banking_off")
    try:
        return await bank.banks(country.upper())
    except ProviderError as exc:
        raise fail(502, exc) from exc


@api.post("/connections/bank")
async def link_bank(
    body: BankLink,
    request: Request,
    who: Caller = Depends(browser_only),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """The bank's own login opens in the user's hands; it comes back to /callback."""
    bank = banking(db, settings_of(request))
    if bank is None:
        raise HTTPException(503, "banking_off")
    forget_pending(db, who.user_id)
    state = secrets.token_urlsafe(24)
    db.add(Connection(user_id=who.user_id, provider="enablebanking", label=body.bank, state=state))
    db.commit()
    try:
        url = await bank.start(
            body.bank, body.country.upper(), state, public(request, "/api/connections/callback")
        )
    except ProviderError as exc:
        raise fail(502, exc) from exc
    return {"url": url}


@api.get("/connections/callback", include_in_schema=False)
async def bank_callback(
    request: Request,
    state: str = "",
    code: str = "",
    error: str = "",
    db: Session = Depends(get_db),
) -> RedirectResponse:
    conn = db.scalar(
        select(Connection).where(Connection.state == state, Connection.status == "pending")
    )
    bank = banking(db, settings_of(request))
    if conn is None or bank is None or not code:
        return RedirectResponse(f"/accounts?linked=error&reason={error or 'state'}", 303)
    try:
        accounts = await finish_bank(db, vault_of(request), bank, conn, code)
    except ProviderError:
        conn.status = "error"
        db.commit()
        return RedirectResponse("/accounts?linked=error", 303)
    if not accounts:
        # the bank said yes but showed no account: with a free (restricted) Enable Banking
        # app only the accounts linked in its control panel are visible
        db.delete(conn)
        db.commit()
        return RedirectResponse("/accounts?linked=empty", 303)
    await sync(db, vault_of(request), conn, bank)
    return RedirectResponse("/accounts?linked=ok", 303)


@api.post("/connections/sync")
async def sync_connections(
    request: Request,
    who: Caller = Depends(require("connections:write")),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    bank = banking(db, settings_of(request))
    added = 0
    for conn in db.scalars(
        select(Connection).where(
            Connection.user_id == who.user_id, Connection.status.in_(("active", "error"))
        )
    ):
        added += await sync(db, vault_of(request), conn, bank)
    await fx.refresh(db, fx.currencies_in_use(db))
    return {"added": added}


@api.delete("/connections/{conn_id}")
def unlink(
    conn_id: int,
    keep_accounts: bool = True,
    who: Caller = Depends(browser_only),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    """Forget the credentials; the accounts stay (as manual) unless asked otherwise."""
    conn = db.get(Connection, conn_id)
    if conn is None or conn.user_id != who.user_id:
        raise HTTPException(404, "connection_unknown")
    for acc in db.scalars(select(Account).where(Account.connection_id == conn.id)):
        if keep_accounts:
            acc.source, acc.connection_id = "manual", None
        else:
            db.delete(acc)
    db.delete(conn)
    db.commit()
    return {"ok": True}


# -- system (admin) --------------------------------------------------------------------
@api.get("/system")
async def system(
    request: Request, who: Caller = Depends(caller), db: Session = Depends(get_db)
) -> dict[str, Any]:
    settings = settings_of(request)
    info: dict[str, Any] = {"version": updates.current_version()}
    if who.browser and who.user.is_admin:
        info["updates"] = await updates.status(settings)
        info["ai_model"] = ai_of(request).config().model
        info["mcp_url"] = public(request, "/mcp")
        info["api_docs"] = public(request, "/api/docs")
        info["sso"] = request.app.state.oidc.enabled
        info["banking"] = banking(db, settings) is not None
        info["callback_url"] = public(request, "/api/connections/callback")
        info["inbox"] = str(settings.inbox_dir)
    return info


@api.post("/system/update", dependencies=[Depends(admin_only)])
async def install_update(request: Request) -> dict[str, Any]:
    settings = settings_of(request)
    found = await updates.status(settings)
    if not found["update_available"]:
        raise HTTPException(409, "up_to_date")
    if not found["can_install"]:
        raise HTTPException(503, "updater_missing")
    updates.request_update(settings.updater_dir, found["latest"]["version"])
    return {"ok": True, "version": found["latest"]["version"]}


@api.put("/system/banking", dependencies=[Depends(admin_only)])
async def set_banking(
    body: BankAccess, request: Request, db: Session = Depends(get_db)
) -> dict[str, bool]:
    """Enable Banking app id + private key, checked before saving."""
    try:
        await EnableBanking(body.app_id.strip(), body.private_key.strip()).check()
    except ProviderError as exc:
        raise fail(422, exc) from exc
    except ValueError as exc:  # a key PyJWT cannot read
        raise HTTPException(422, "key_invalid") from exc
    save_banking(db, settings_of(request), body.app_id, body.private_key.strip() + "\n")
    return {"ok": True}
