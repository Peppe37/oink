"""Background work, in-process (one container, no queue): the inbox every few minutes,
exchange rates and daily chores every hour (statement requests, AI categories, the monthly
report), linked accounts every `sync_hours`. Everything is idempotent and per user."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from oink.core.config import Settings
from oink.core.db import Account, Connection, Report, StatementRequest, Transaction, User
from oink.core.money import as_text
from oink.core.security import Vault
from oink.services import fx
from oink.services.ai import AI, AIError
from oink.services.connections import banking, sync
from oink.services.reports import Book, facts_for_ai, previous_months
from oink.services.statements import scan_inbox
from oink.services.transfers import match_transfers

log = logging.getLogger("oink.jobs")
INBOX_SECONDS = 300


async def sync_all(sessions: sessionmaker[Session], settings: Settings, vault: Vault) -> int:
    total = 0
    with sessions() as db:
        bank = banking(db, settings)
        for conn in db.scalars(
            select(Connection).where(Connection.status.in_(("active", "error")))
        ):
            total += await sync(db, vault, conn, bank)
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
            req = StatementRequest(user_id=acc.user_id, account_id=acc.id, month=month)
            db.add(req)
            created.append(req)
    db.commit()
    return created


async def categorize_pending(db: Session, ai: AI, user_id: int, limit: int = 200) -> int:
    if not ai.can_read_text:
        return 0
    rows = list(
        db.scalars(
            select(Transaction)
            .where(Transaction.user_id == user_id, Transaction.category == "")
            .limit(limit)
        )
    )
    if not rows:
        return 0
    try:
        found = await ai.categorize(
            [
                (t.id, f"{t.description} {t.counterparty}", as_text(t.amount, t.currency) or "0")
                for t in rows
            ]
        )
    except AIError as exc:
        log.warning("categorize failed: %s", exc)
        return 0
    for t in rows:
        if (cat := found.get(t.id)) is not None:
            t.category, t.category_by = cat, "ai"
    db.commit()
    return len(found)


async def monthly_report(
    db: Session, ai: AI, user: User, month: str, settings: Settings, language: str = ""
) -> Report:
    language = language or user.language or "en"
    facts = facts_for_ai(Book.of(db, user), month, with_text=ai.config().send_descriptions)
    content = await ai.monthly_report(facts, language)
    report = Report(
        user_id=user.id,
        month=month,
        kind="monthly",
        content=content,
        model=ai.config().model,
        language=language,
    )
    db.add(report)
    db.commit()
    return report


async def refresh_quotes(db: Session) -> int:
    """Latest prices of every symbol someone holds (cached 15 minutes by markets.quote)."""
    from oink.core.db import Holding
    from oink.services.markets import MarketError, quote

    done = 0
    for symbol in set(db.scalars(select(Holding.symbol).where(Holding.manual_price.is_(None)))):
        with contextlib.suppress(MarketError):
            await quote(db, symbol)
            done += 1
    return done


async def daily(sessions: sessionmaker[Session], settings: Settings, ai: AI) -> None:
    today = datetime.now(ZoneInfo(settings.timezone)).date()
    with sessions() as db:
        await fx.refresh(db, fx.currencies_in_use(db))
        await refresh_quotes(db)
        open_statement_requests(db, today, settings.statement_day)
        month = previous_months(today.strftime("%Y-%m"), 1)[0]
        for user in db.scalars(select(User).where(User.disabled.is_(False))):
            await categorize_pending(db, ai, user.id)
            has_report = db.scalar(
                select(Report.id).where(
                    Report.user_id == user.id, Report.month == month, Report.kind == "monthly"
                )
            )
            has_data = db.scalar(select(Transaction.id).where(Transaction.user_id == user.id))
            if (
                ai.enabled
                and has_data
                and today.day >= settings.statement_day + 2
                and not has_report
            ):
                with contextlib.suppress(AIError):
                    await monthly_report(db, ai, user, month, settings)


async def loop(sessions: sessionmaker[Session], settings: Settings, ai: AI, vault: Vault) -> None:
    """Runs for the life of the app."""
    clock = asyncio.get_running_loop().time
    last_sync = last_daily = -1e9
    while True:
        try:
            with sessions() as db:
                await scan_inbox(db, settings.inbox_dir, ai, settings.documents_dir)
            if clock() - last_sync > settings.sync_hours * 3600:
                await sync_all(sessions, settings, vault)
                last_sync = clock()
            if clock() - last_daily > 3600:
                with sessions() as db:  # also pairs transfers already in the books
                    for user in db.scalars(select(User).where(User.disabled.is_(False))):
                        match_transfers(db, user)
                await daily(sessions, settings, ai)
                last_daily = clock()
        except Exception:
            log.exception("background job failed")
        await asyncio.sleep(INBOX_SECONDS)
