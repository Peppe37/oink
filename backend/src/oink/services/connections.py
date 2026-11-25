"""Linking and syncing the places money lives: banks through Enable Banking (a redirect to
the bank), and exchanges, fintechs and wallets with a key or an address (one form)."""

from __future__ import annotations

import contextlib
import logging
import secrets
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from oink.core.config import Settings
from oink.core.db import Account, Connection, Setting, Transaction, User, aware
from oink.core.money import MoneyError, normalise_code, to_minor
from oink.core.security import Vault
from oink.providers import PROVIDERS, ProviderError, RemoteAccount
from oink.providers.enablebanking import EnableBanking
from oink.services.ledger import add_movements

log = logging.getLogger("oink.connections")
EB_APP_ID = "enablebanking.app_id"


# -- the instance's bank access (admin) --------------------------------------------------
def bank_key_file(settings: Settings) -> str:
    return str(settings.enable_banking_key_file)


def banking(db: Session, settings: Settings) -> EnableBanking | None:
    row = db.get(Setting, EB_APP_ID)
    app_id = row.value if row else settings.enable_banking_app_id
    key = settings.enable_banking_key_file
    if not app_id or not key.is_file():
        return None
    return EnableBanking(app_id, key.read_text())


def save_banking(db: Session, settings: Settings, app_id: str, private_key: str) -> None:
    path = settings.enable_banking_key_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(private_key)
    path.chmod(0o600)
    row = db.get(Setting, EB_APP_ID)
    if row is None:
        db.add(Setting(key=EB_APP_ID, value=app_id.strip()))
    else:
        row.value = app_id.strip()
    db.commit()


# -- accounts from a provider ------------------------------------------------------------
def _upsert_accounts(
    db: Session, user: User, conn: Connection, found: list[RemoteAccount], institution: str
) -> list[Account]:
    out = []
    for remote in found:
        try:
            currency = normalise_code(remote.currency)
        except MoneyError:
            continue
        acc = db.scalar(
            select(Account).where(
                Account.user_id == user.id,
                Account.connection_id == conn.id,
                Account.external_id == remote.uid,
            )
        )
        if acc is None:  # linked again (a new consent): the same account, not a copy
            acc = db.scalar(
                select(Account).where(
                    Account.user_id == user.id,
                    Account.source == "linked",
                    (Account.external_id == remote.uid)
                    | ((Account.iban == remote.iban) & (Account.iban != "")),
                )
            )
            if acc is not None:
                acc.connection_id, acc.external_id, acc.archived = conn.id, remote.uid, False
        if acc is None:
            acc = Account(
                user_id=user.id,
                name=remote.name,
                kind=remote.kind,
                institution=institution,
                iban=remote.iban,
                currency=currency,
                source="linked",
                connection_id=conn.id,
                external_id=remote.uid,
            )
            db.add(acc)
        if remote.balance is not None:
            with contextlib.suppress(MoneyError):
                acc.balance, acc.balance_at = to_minor(remote.balance, currency), datetime.now(UTC)
        out.append(acc)
    db.commit()
    return out


async def link_with_key(
    db: Session, vault: Vault, user: User, provider_id: str, creds: dict[str, str], label: str
) -> Connection:
    """Check the credentials by reading the accounts, then keep them (encrypted)."""
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        raise ProviderError("provider_unknown")
    missing = [f.name for f in provider.fields if not f.optional and not creds.get(f.name)]
    if missing:
        raise ProviderError("fields_missing")
    creds = {f.name: creds.get(f.name, "").strip() for f in provider.fields}
    found = await provider.accounts(creds)
    conn = Connection(
        user_id=user.id,
        provider=provider.id,
        label=label.strip()[:120] or provider.name,
        secret=vault.seal(creds),
        state=f"key-{secrets.token_hex(16)}",
        status="active",
        last_sync=datetime.now(UTC),
    )
    db.add(conn)
    db.commit()
    accounts = _upsert_accounts(db, user, conn, found, provider.name)
    if provider.movements_supported:
        for acc in accounts:
            remote = next(r for r in found if r.uid == acc.external_id)
            moves = await provider.movements(creds, remote, date.today() - timedelta(days=89))
            add_movements(db, acc, moves, "linked")
    return conn


async def finish_bank(
    db: Session, vault: Vault, bank: EnableBanking, conn: Connection, code: str
) -> list[Account]:
    session = await bank.finish(code)
    conn.secret = vault.seal({"session_id": session["session_id"]})
    conn.status = "active"
    conn.state = f"done-{conn.id}-{secrets.token_hex(6)}"
    if session["valid_until"]:
        conn.valid_until = datetime.fromisoformat(session["valid_until"].replace("Z", "+00:00"))
    user = db.get(User, conn.user_id)
    assert user is not None
    accounts = _upsert_accounts(db, user, conn, session["accounts"], conn.label)
    # right after the login the bank gives the whole history: take it now
    for acc in accounts:
        try:
            moves = await bank.history(acc.external_id, acc.currency)
        except ProviderError as exc:
            log.warning("history of account %s not available: %s", acc.id, exc)
            continue
        add_movements(db, acc, moves, "linked")
    # older consents of the same bank whose accounts moved to this one are done
    for old in db.scalars(
        select(Connection).where(
            Connection.user_id == user.id,
            Connection.provider == "enablebanking",
            Connection.id != conn.id,
            Connection.label == conn.label,
        )
    ):
        if not db.scalar(select(Account.id).where(Account.connection_id == old.id).limit(1)):
            db.delete(old)
    db.commit()
    return accounts


def forget_pending(db: Session, user_id: int) -> None:
    """Bank logins started and never finished (closed tab, error at the bank)."""
    cutoff = datetime.now(UTC) - timedelta(minutes=30)
    for conn in db.scalars(
        select(Connection).where(Connection.user_id == user_id, Connection.status == "pending")
    ):
        created = aware(conn.created)
        if created is None or created < cutoff:
            db.delete(conn)
    db.commit()


# -- sync ----------------------------------------------------------------------------------
def _since(db: Session, acc: Account) -> date:
    last = db.scalar(
        select(Transaction.day)
        .where(Transaction.account_id == acc.id)
        .order_by(Transaction.day.desc())
        .limit(1)
    )
    return (last - timedelta(days=5)) if last else date.today() - timedelta(days=89)


async def sync(db: Session, vault: Vault, conn: Connection, bank: EnableBanking | None) -> int:
    """Balances and new movements of one connection; returns how many movements were added."""
    user = db.get(User, conn.user_id)
    if user is None:
        return 0
    added = 0
    try:
        creds = vault.open(conn.secret)
        if conn.provider == "enablebanking":
            valid = aware(conn.valid_until)
            if valid and valid < datetime.now(UTC):
                conn.status, conn.error = "expired", "consent_expired"
                db.commit()
                return 0
            if bank is None:
                raise ProviderError("banking_off")
            for acc in db.scalars(select(Account).where(Account.connection_id == conn.id)):
                moves = await bank.movements(acc.external_id, acc.currency, _since(db, acc))
                added += len(add_movements(db, acc, moves, "linked").added)
                if (balance := await bank.balance(acc.external_id, acc.currency)) is not None:
                    acc.balance, acc.balance_at = balance, datetime.now(UTC)
        else:
            provider = PROVIDERS.get(conn.provider)
            if provider is None:
                raise ProviderError("provider_unknown")
            found = await provider.accounts(creds)
            accounts = _upsert_accounts(db, user, conn, found, provider.name)
            if provider.movements_supported:
                for acc in accounts:
                    remote = next(r for r in found if r.uid == acc.external_id)
                    moves = await provider.movements(creds, remote, _since(db, acc))
                    added += len(add_movements(db, acc, moves, "linked").added)
        conn.status, conn.error, conn.last_sync = "active", "", datetime.now(UTC)
    except ProviderError as exc:
        conn.status, conn.error = "error", str(exc)[:300]
        log.warning("sync of connection %s failed: %s", conn.id, exc)
    db.commit()
    return added
