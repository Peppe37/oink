"""Background work, in-process (one container, no queue): bank sync, the monthly statement
requests for accounts no bank API reads, the AI categories and the monthly report."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from oink.core.config import Settings
from oink.core.db import Account, Connection, Report, StatementRequest, Transaction
from oink.providers.enablebanking import BankError, EnableBanking
from oink.services.ai import AI, AIError
from oink.services.ledger import add_movements
from oink.services.reports import facts_for_ai, previous_months

log = logging.getLogger("oink.jobs")


def bank_client(settings: Settings) -> EnableBanking | None:
    if not settings.banking_enabled:
        return None
    return EnableBanking(
        settings.enable_banking_app_id, settings.enable_banking_key_file.read_text()
    )


async def sync_connection(db: Session, bank: EnableBanking, conn: Connection) -> int:
    """Balances and new movements of every account of one bank authorisation."""
    added = 0
    valid = (
        conn.valid_until.replace(tzinfo=UTC)
        if conn.valid_until and not conn.valid_until.tzinfo
        else conn.valid_until
    )
    if valid and valid < datetime.now(UTC):
        conn.status, conn.error = "expired", "consenso scaduto: ricollega la banca"
        db.commit()
        return 0
    accounts = list(db.scalars(select(Account).where(Account.connection_id == conn.id)))
    try:
        for acc in accounts:
            last = db.scalar(
                select(Transaction.day)
                .where(Transaction.account_id == acc.id)
                .order_by(Transaction.day.desc())
                .limit(1)
            )
            since = (last - timedelta(days=5)) if last else date.today() - timedelta(days=89)
            moves = await bank.movements(acc.external_id, since)
            added += len(add_movements(db, acc.id, moves, "bank").added)
            if (balance := await bank.balance(acc.external_id)) is not None:
                acc.balance_cents, acc.balance_at = balance, datetime.now(UTC)
        conn.status, conn.error, conn.last_sync = "active", "", datetime.now(UTC)
    except BankError as exc:
        conn.status, conn.error = "error", str(exc)[:500]
    db.commit()
    return added


async def sync_all(sessions: sessionmaker[Session], settings: Settings) -> int:
    bank = bank_client(settings)
    if bank is None:
        return 0
    total = 0
    with sessions() as db:
        for conn in db.scalars(
            select(Connection).where(Connection.status.in_(("active", "error")))
        ):
            total += await sync_connection(db, bank, conn)
    return total


def open_statement_requests(db: Session, today: date, statement_day: int) -> list[StatementRequest]:
    """From `statement_day`, one request per manual account for the month just ended."""
    if today.day < statement_day:
        return []
    month = previous_months(today.strftime("%Y-%m"), 1)[0]
    created = []
    manual = db.scalars(
        select(Account).where(
            Account.source == "manual", Account.archived.is_(False), Account.kind != "cash"
        )
    )
    for acc in manual:
        exists = db.scalar(
            select(StatementRequest).where(
                StatementRequest.account_id == acc.id, StatementRequest.month == month
            )
        )
        if exists is None:
            req = StatementRequest(account_id=acc.id, month=month)
            db.add(req)
            created.append(req)
    db.commit()
    return created


async def categorize_pending(db: Session, ai: AI, limit: int = 200) -> int:
    if not ai.enabled:
        return 0
    rows = list(db.scalars(select(Transaction).where(Transaction.category == "").limit(limit)))
    if not rows:
        return 0
    try:
        found = await ai.categorize(
            [(t.id, f"{t.description} {t.counterparty}", t.amount_cents) for t in rows]
        )
    except AIError as exc:
        log.warning("categorize failed: %s", exc)
        return 0
    for t in rows:
        if (cat := found.get(t.id)) is not None:
            t.category, t.category_by = cat, "ai"
    db.commit()
    return len(found)


async def monthly_report(db: Session, ai: AI, month: str, settings: Settings) -> Report:
    facts = facts_for_ai(db, month, with_text=settings.ai_send_descriptions)
    content = await ai.monthly_report(facts)
    report = Report(month=month, kind="monthly", content=content, model=settings.ai_model)
    db.add(report)
    db.commit()
    return report


async def daily(sessions: sessionmaker[Session], settings: Settings, ai: AI) -> None:
    today = datetime.now(ZoneInfo(settings.timezone)).date()
    with sessions() as db:
        open_statement_requests(db, today, settings.statement_day)
        await categorize_pending(db, ai)
        month = previous_months(today.strftime("%Y-%m"), 1)[0]
        has_report = db.scalar(
            select(Report.id).where(Report.month == month, Report.kind == "monthly")
        )
        if ai.enabled and today.day >= settings.statement_day + 2 and not has_report:
            with contextlib.suppress(AIError):
                await monthly_report(db, ai, month, settings)


async def loop(sessions: sessionmaker[Session], settings: Settings, ai: AI) -> None:
    """Runs for the life of the app: sync every `sync_hours`, daily work every hour (idempotent)."""
    last_sync = 0.0
    while True:
        try:
            if (
                asyncio.get_running_loop().time() - last_sync > settings.sync_hours * 3600
                or not last_sync
            ):
                await sync_all(sessions, settings)
                last_sync = asyncio.get_running_loop().time()
            await daily(sessions, settings, ai)
        except Exception:
            log.exception("background job failed")
        await asyncio.sleep(3600)
