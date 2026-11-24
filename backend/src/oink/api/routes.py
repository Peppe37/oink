"""The HTTP API, used by the web app (session cookie) and by other apps (scoped tokens).
OpenAPI docs at /api/docs."""

from __future__ import annotations

import secrets
from datetime import UTC, date, datetime
from typing import Any

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
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from oink.api.deps import Caller, get_db, owner_only, require
from oink.api.schemas import (
    AccountIn,
    AccountOut,
    AccountPatch,
    Ask,
    ConnectionOut,
    ImportOut,
    Login,
    PasswordChange,
    ReportOut,
    RequestOut,
    TokenIn,
    TokenOut,
    TransactionIn,
    TransactionOut,
    TransactionPatch,
)
from oink.core.config import Settings
from oink.core.db import Account, ApiToken, Connection, Report, StatementRequest, Transaction
from oink.core.security import (
    COOKIE,
    SCOPES,
    create_token,
    end_session,
    new_session,
    owner_password,
    set_owner_password,
    verify_password,
)
from oink.providers.enablebanking import BankError
from oink.services import reports as rep
from oink.services.ai import AI, AIError
from oink.services.importer import ImportError_, guess_month, parse_statement
from oink.services.jobs import bank_client, categorize_pending, monthly_report, sync_connection
from oink.services.ledger import CATEGORIES, NewMovement, add_movements, learn_rule

api = APIRouter(prefix="/api")
MAX_UPLOAD = 15 * 1024 * 1024


def settings_of(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def ai_of(request: Request) -> AI:
    ai: AI = request.app.state.ai
    return ai


def current_month() -> str:
    return date.today().strftime("%Y-%m")


# -- auth ------------------------------------------------------------------------------
@api.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@api.get("/auth/status")
def auth_status(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    from oink.core.security import session_valid

    s = settings_of(request)
    return {
        "configured": owner_password(db) is not None,
        "logged_in": session_valid(db, request.cookies.get(COOKIE)),
        "ai": s.ai_enabled,
        "banking": s.banking_enabled,
        "currency": s.currency,
    }


@api.post("/auth/setup")
def setup(
    body: Login, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, bool]:
    """First run only: choose the owner's password."""
    if owner_password(db) is not None:
        raise HTTPException(409, "già configurato")
    try:
        set_owner_password(db, body.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _login(response, db, settings_of(request))
    return {"ok": True}


@api.post("/auth/login")
def login(
    body: Login, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, bool]:
    stored = owner_password(db)
    if stored is None or not verify_password(stored, body.password):
        raise HTTPException(401, "password sbagliata")
    _login(response, db, settings_of(request))
    return {"ok": True}


def _login(response: Response, db: Session, settings: Settings) -> None:
    cookie = new_session(db, settings.session_days)
    response.set_cookie(
        COOKIE,
        cookie,
        max_age=settings.session_days * 86400,
        httponly=True,
        samesite="strict",
        secure=settings.public_url.startswith("https"),
    )


@api.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, bool]:
    end_session(db, request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE)
    return {"ok": True}


@api.post("/auth/password", dependencies=[Depends(owner_only)])
def change_password(body: PasswordChange, db: Session = Depends(get_db)) -> dict[str, bool]:
    stored = owner_password(db)
    if stored is None or not verify_password(stored, body.current):
        raise HTTPException(401, "password attuale sbagliata")
    set_owner_password(db, body.new)
    return {"ok": True}


# -- tokens ----------------------------------------------------------------------------
@api.get("/scopes")
def scopes() -> dict[str, str]:
    return SCOPES


@api.get("/tokens", response_model=list[TokenOut], dependencies=[Depends(owner_only)])
def list_tokens(db: Session = Depends(get_db)) -> list[ApiToken]:
    return list(db.scalars(select(ApiToken).order_by(ApiToken.created.desc())))


@api.post("/tokens", dependencies=[Depends(owner_only)])
def new_token(body: TokenIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """The token is in the answer ONLY here: copy it now."""
    try:
        token, secret = create_token(db, body.name, body.scopes)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"token": secret, **TokenOut.model_validate(token).model_dump(mode="json")}


@api.delete("/tokens/{token_id}", dependencies=[Depends(owner_only)])
def revoke_token(token_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    token = db.get(ApiToken, token_id)
    if token is None:
        raise HTTPException(404, "token inesistente")
    token.revoked = True
    db.commit()
    return {"ok": True}


# -- accounts --------------------------------------------------------------------------
@api.get(
    "/accounts", response_model=list[AccountOut], dependencies=[Depends(require("accounts:read"))]
)
def accounts(db: Session = Depends(get_db), archived: bool = False) -> list[Account]:
    query = select(Account).order_by(Account.created)
    if not archived:
        query = query.where(Account.archived.is_(False))
    return list(db.scalars(query))


@api.post("/accounts", response_model=AccountOut, dependencies=[Depends(require("accounts:write"))])
def create_account(body: AccountIn, db: Session = Depends(get_db)) -> Account:
    account = Account(
        **body.model_dump(),
        source="manual",
        balance_at=datetime.now(UTC) if body.balance_cents is not None else None,
    )
    db.add(account)
    db.commit()
    return account


@api.patch(
    "/accounts/{account_id}",
    response_model=AccountOut,
    dependencies=[Depends(require("accounts:write"))],
)
def update_account(account_id: int, body: AccountPatch, db: Session = Depends(get_db)) -> Account:
    account = _account(db, account_id)
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(account, key, value)
    if "balance_cents" in changes:
        account.balance_at = datetime.now(UTC)
    db.commit()
    return account


def _account(db: Session, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "conto inesistente")
    return account


# -- transactions ----------------------------------------------------------------------
@api.get(
    "/transactions",
    response_model=list[TransactionOut],
    dependencies=[Depends(require("transactions:read"))],
)
def transactions(
    db: Session = Depends(get_db),
    account_id: int | None = None,
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    category: str | None = None,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[Transaction]:
    query = select(Transaction).order_by(Transaction.day.desc(), Transaction.id.desc())
    if account_id is not None:
        query = query.where(Transaction.account_id == account_id)
    if month:
        start, end = rep.month_bounds(month)
        query = query.where(Transaction.day >= start, Transaction.day < end)
    if category is not None:
        query = query.where(Transaction.category == category)
    if q:
        like = f"%{q}%"
        query = query.where(
            or_(
                Transaction.description.ilike(like),
                Transaction.counterparty.ilike(like),
                Transaction.note.ilike(like),
            )
        )
    return list(db.scalars(query.limit(limit).offset(offset)))


@api.post(
    "/transactions",
    response_model=TransactionOut,
    dependencies=[Depends(require("transactions:write"))],
)
def add_transaction(body: TransactionIn, db: Session = Depends(get_db)) -> Transaction:
    account = _account(db, body.account_id)
    result = add_movements(
        db,
        account.id,
        [
            NewMovement(
                day=body.day,
                amount_cents=body.amount_cents,
                description=body.description,
                counterparty=body.counterparty,
                category=body.category,
                note=body.note,
                currency=account.currency,
            )
        ],
        "api",
    )
    if not result.added:
        raise HTTPException(409, "movimento già presente")
    if account.balance_cents is not None and account.source == "manual":
        account.balance_cents += body.amount_cents
        db.commit()
    return result.added[0]


@api.patch(
    "/transactions/{tx_id}",
    response_model=TransactionOut,
    dependencies=[Depends(require("transactions:write"))],
)
def update_transaction(
    tx_id: int, body: TransactionPatch, db: Session = Depends(get_db)
) -> Transaction:
    tx = db.get(Transaction, tx_id)
    if tx is None:
        raise HTTPException(404, "movimento inesistente")
    if body.category is not None:
        tx.category, tx.category_by = body.category.strip().lower(), "user"
        if body.learn and tx.category:
            learn_rule(db, tx, tx.category)
    if body.note is not None:
        tx.note = body.note
    db.commit()
    return tx


@api.get("/categories")
def categories(
    db: Session = Depends(get_db), _: Caller = Depends(require("transactions:read"))
) -> list[str]:
    used = set(db.scalars(select(Transaction.category).distinct()))
    return sorted(set(CATEGORIES) | {c for c in used if c})


@api.post("/transactions/categorize", dependencies=[Depends(require("transactions:write"))])
async def categorize(request: Request, db: Session = Depends(get_db)) -> dict[str, int]:
    return {"categorized": await categorize_pending(db, ai_of(request))}


# -- imports and statement requests ----------------------------------------------------
@api.post("/imports", response_model=ImportOut, dependencies=[Depends(require("imports:write"))])
async def import_statement(
    request: Request,
    account_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> ImportOut:
    account = _account(db, account_id)
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "file troppo grande (max 15 MB)")
    try:
        parsed = await parse_statement(file.filename or "estratto", raw, ai_of(request))
    except ImportError_ as exc:
        raise HTTPException(422, str(exc)) from exc
    result = add_movements(db, account.id, parsed.movements, "import")
    month = guess_month(parsed.movements)
    balance_updated = False
    if parsed.balance_cents is not None and account.source == "manual":
        account.balance_cents, account.balance_at = parsed.balance_cents, datetime.now(UTC)
        balance_updated = True
    req = db.scalar(
        select(StatementRequest).where(
            StatementRequest.account_id == account.id,
            StatementRequest.month == month,
            StatementRequest.status == "open",
        )
    )
    if req is not None:
        req.status, req.done_at = "done", datetime.now(UTC)
    db.commit()
    return ImportOut(
        added=len(result.added),
        duplicates=result.duplicates,
        month=month,
        by_ai=parsed.by_ai,
        balance_updated=balance_updated,
        request_closed=req is not None,
    )


@api.get(
    "/requests", response_model=list[RequestOut], dependencies=[Depends(require("requests:read"))]
)
def statement_requests(
    db: Session = Depends(get_db), status: str = "open"
) -> list[StatementRequest]:
    return list(
        db.scalars(
            select(StatementRequest)
            .where(StatementRequest.status == status)
            .order_by(StatementRequest.created)
        )
    )


@api.post(
    "/requests/{req_id}/skip",
    response_model=RequestOut,
    dependencies=[Depends(require("requests:write"))],
)
def skip_request(req_id: int, db: Session = Depends(get_db)) -> StatementRequest:
    req = db.get(StatementRequest, req_id)
    if req is None:
        raise HTTPException(404, "richiesta inesistente")
    req.status, req.done_at = "skipped", datetime.now(UTC)
    db.commit()
    return req


# -- reports ---------------------------------------------------------------------------
@api.get("/reports/summary", dependencies=[Depends(require("reports:read"))])
def summary(
    db: Session = Depends(get_db), month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$")
) -> dict[str, Any]:
    return rep.compare(db, month or current_month())


@api.get("/reports/overview", dependencies=[Depends(require("reports:read"))])
def overview(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Everything the home screen needs in one call."""
    return {
        "net_worth": rep.net_worth(db),
        "month": rep.compare(db, current_month()),
        "series": rep.series(db, 12),
        "balance": rep.balance_history(db, 180),
        "recurring": rep.recurring(db),
        "open_requests": db.scalar(
            select(func.count())
            .select_from(StatementRequest)
            .where(StatementRequest.status == "open")
        ),
    }


@api.get("/reports/recurring", dependencies=[Depends(require("reports:read"))])
def recurring(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return rep.recurring(db)


@api.get(
    "/reports", response_model=list[ReportOut], dependencies=[Depends(require("reports:read"))]
)
def list_reports(db: Session = Depends(get_db)) -> list[Report]:
    return list(db.scalars(select(Report).order_by(Report.created.desc()).limit(24)))


@api.post(
    "/reports/generate", response_model=ReportOut, dependencies=[Depends(require("reports:write"))]
)
async def generate_report(
    request: Request,
    db: Session = Depends(get_db),
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
) -> Report:
    try:
        return await monthly_report(
            db, ai_of(request), month or current_month(), settings_of(request)
        )
    except AIError as exc:
        raise HTTPException(503, str(exc)) from exc


@api.post("/ask", dependencies=[Depends(require("ai:ask"))])
async def ask(body: Ask, request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    settings = settings_of(request)
    facts = rep.facts_for_ai(
        db, body.month or current_month(), with_text=settings.ai_send_descriptions
    )
    try:
        return {"answer": await ai_of(request).answer(body.question, facts)}
    except AIError as exc:
        raise HTTPException(503, str(exc)) from exc


# -- banks -----------------------------------------------------------------------------
@api.get("/banks", dependencies=[Depends(require("banks:read"))])
async def banks(request: Request) -> list[dict[str, Any]]:
    settings = settings_of(request)
    client = bank_client(settings)
    if client is None:
        raise HTTPException(503, "collegamento bancario non configurato")
    try:
        return await client.banks(settings.enable_banking_country)
    except BankError as exc:
        raise HTTPException(502, str(exc)) from exc


@api.get(
    "/banks/connections",
    response_model=list[ConnectionOut],
    dependencies=[Depends(require("banks:read"))],
)
def connections(db: Session = Depends(get_db)) -> list[Connection]:
    return list(db.scalars(select(Connection).where(Connection.status != "pending")))


@api.post("/banks/connect", dependencies=[Depends(owner_only)])
async def connect_bank(
    request: Request, bank: str = Form(...), db: Session = Depends(get_db)
) -> dict[str, str]:
    """Only the owner, in the browser: the bank's login opens in their hands."""
    settings = settings_of(request)
    client = bank_client(settings)
    if client is None:
        raise HTTPException(503, "collegamento bancario non configurato")
    state = secrets.token_urlsafe(24)
    db.add(Connection(provider="enablebanking", institution=bank, state=state))
    db.commit()
    try:
        url = await client.start(
            bank,
            settings.enable_banking_country,
            state,
            f"{settings.public_url.rstrip('/')}/api/banks/callback",
        )
    except BankError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"url": url}


@api.get("/banks/callback", include_in_schema=False)
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
    client = bank_client(settings_of(request))
    if conn is None or client is None or not code:
        return RedirectResponse(f"/settings?bank=error&reason={error or 'stato'}", status_code=303)
    try:
        session = await client.finish(code)
    except BankError:
        conn.status = "error"
        db.commit()
        return RedirectResponse("/settings?bank=error", status_code=303)
    conn.session_id, conn.status = session["session_id"], "active"
    conn.state = f"done-{conn.id}-{secrets.token_hex(4)}"
    if session["valid_until"]:
        conn.valid_until = datetime.fromisoformat(session["valid_until"].replace("Z", "+00:00"))
    for acc in session["accounts"]:
        existing = db.scalar(select(Account).where(Account.external_id == acc["uid"]))
        if existing is None:
            db.add(
                Account(
                    name=acc["name"],
                    institution=conn.institution,
                    currency=acc["currency"],
                    source="bank",
                    connection_id=conn.id,
                    external_id=acc["uid"],
                )
            )
        else:
            existing.connection_id = conn.id
    db.commit()
    await sync_connection(db, client, conn)
    return RedirectResponse("/settings?bank=ok", status_code=303)


@api.post("/banks/sync", dependencies=[Depends(require("banks:write"))])
async def sync_banks(request: Request, db: Session = Depends(get_db)) -> dict[str, int]:
    client = bank_client(settings_of(request))
    if client is None:
        raise HTTPException(503, "collegamento bancario non configurato")
    added = 0
    for conn in db.scalars(select(Connection).where(Connection.status.in_(("active", "error")))):
        added += await sync_connection(db, client, conn)
    return {"added": added}
