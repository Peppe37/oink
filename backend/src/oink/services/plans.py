"""An accumulation plan (PAC) is seen in the movements of the account it is paid from.

A broker's cash account read through the bank link (Trade Republic) shows each purchase
only as a date and an amount. Given the plan (security, amount, day of the month, account),
Oink matches those debits deterministically: same amount (to the cent), within a few days of
the plan's day, one per period, from the start to the end of the plan. A matched debit is
an investment, never spending, and the plan's units are worked out from the security's
close on that day: «about 2.31 shares bought by the plan since it started».

Debits that repeat with the same amount around the same day of the month and match no plan
are offered as plans to set up."""

from __future__ import annotations

import calendar
import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from oink.core.db import Account, Holding, Plan, Transaction, User
from oink.core.money import MoneyError, from_minor, to_minor
from oink.services.ledger import INTERNAL
from oink.services.markets import MarketError, close_on, daily_closes

log = logging.getLogger("oink.plans")
BEFORE, AFTER = 2, 6  # days around the plan's day a purchase can be booked
STEP = {"weekly": 7}


def expected_days(plan: Plan, until: date) -> list[date]:
    """When the plan should have bought, from its start to its end (or `until`)."""
    last = min(plan.end or until, until)
    day = plan.day or plan.start.day
    out: list[date] = []
    if plan.frequency == "weekly":
        current = plan.start
        while current <= last:
            out.append(current)
            current += timedelta(days=7)
        return out
    months = {"monthly": 1, "quarterly": 3, "yearly": 12}.get(plan.frequency, 1)
    year, month = plan.start.year, plan.start.month
    while True:
        current = date(year, month, min(day, calendar.monthrange(year, month)[1]))
        if current > last:
            break
        if current >= plan.start - timedelta(days=BEFORE):
            out.append(current)
        month += months
        year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
    return out


def match_executions(db: Session, user: User, today: date | None = None) -> int:
    """Link each plan's purchases in its account; returns how many were newly matched."""
    today = today or date.today()
    plans = [
        p
        for p in db.scalars(
            select(Plan).where(Plan.user_id == user.id, Plan.account_id.is_not(None))
        )
    ]
    matched = 0
    for plan in plans:
        account = db.get(Account, plan.account_id)
        if account is None:
            continue
        try:
            amount = -to_minor(plan.amount, account.currency)
        except MoneyError:
            continue
        days = expected_days(plan, today)
        if not days:
            continue
        candidates = list(
            db.scalars(
                select(Transaction).where(
                    Transaction.account_id == account.id,
                    Transaction.amount.between(amount - 1, amount + 1),
                    Transaction.day >= days[0] - timedelta(days=BEFORE),
                    Transaction.day <= days[-1] + timedelta(days=AFTER),
                )
            )
        )
        taken = {t.day for t in candidates if t.plan_id == plan.id}
        free = [
            t
            for t in candidates
            if t.plan_id is None and (t.category_by != "user" or t.category in INTERNAL)
        ]
        for expected in days:
            if any(
                expected - timedelta(days=BEFORE) <= d <= expected + timedelta(days=AFTER)
                for d in taken
            ):
                continue
            window = [
                t
                for t in free
                if expected - timedelta(days=BEFORE) <= t.day <= expected + timedelta(days=AFTER)
            ]
            if not window:
                continue
            best = min(window, key=lambda t: (abs((t.day - expected).days), t.id))
            best.plan_id, best.category, best.category_by = plan.id, "investments", "plan"
            free.remove(best)
            taken.add(best.day)
            matched += 1
    db.commit()
    return matched


async def plan_positions(db: Session, user: User) -> dict[int, dict[str, Any]]:
    """Per plan: executions seen, money invested, units bought (from each day's close)."""
    out: dict[int, dict[str, Any]] = {}
    for plan in db.scalars(select(Plan).where(Plan.user_id == user.id)):
        txs = list(
            db.scalars(
                select(Transaction).where(Transaction.plan_id == plan.id).order_by(Transaction.day)
            )
        )
        invested = sum((-from_minor(t.amount, t.currency) for t in txs), Decimal(0))
        units: Decimal | None = Decimal(0)
        if txs:
            try:
                closes = await daily_closes(plan.symbol)
            except MarketError:
                closes = {}
            for t in txs:
                price = close_on(closes, t.day)
                if price is None or price <= 0 or units is None:
                    units = None
                    continue
                units += -from_minor(t.amount, t.currency) / price
        out[plan.id] = {
            "executions": len(txs),
            "invested": str(invested),
            "units": str(round(units, 6)) if units is not None and txs else None,
            "last": txs[-1].day.isoformat() if txs else None,
            "days": [t.day.isoformat() for t in txs],
        }
    return out


def extra_units(
    db: Session, plan: Plan, holding: Holding | None, positions: dict[str, Any]
) -> Decimal:
    """Units the plan added on top of a holding typed in by hand (bought after it was)."""
    if positions.get("units") is None:
        return Decimal(0)
    if holding is None:
        return Decimal(positions["units"])
    since = holding.created.date() if holding.created else date.min
    share = [d for d in positions["days"] if date.fromisoformat(d) > since]
    if not positions["days"]:
        return Decimal(0)
    return Decimal(positions["units"]) * len(share) / len(positions["days"])


def suggestions(db: Session, user: User, today: date | None = None) -> list[dict[str, Any]]:
    """Debits repeating every month with the same amount around the same day, matched by
    no plan and no transfer: probably a savings plan to set up."""
    today = today or date.today()
    since = today - timedelta(days=200)
    groups: dict[tuple[int, int, int], list[Transaction]] = defaultdict(list)
    for t in db.scalars(
        select(Transaction).where(
            Transaction.user_id == user.id,
            Transaction.amount < 0,
            Transaction.day >= since,
            Transaction.plan_id.is_(None),
            Transaction.transfer_id.is_(None),
        )
    ):
        if t.category_by == "transfer":
            continue
        groups[(t.account_id, t.amount, (t.day.day - 1) // 5)].append(t)
    out = []
    for (account_id, amount, _bucket), txs in groups.items():
        months = {(t.day.year, t.day.month) for t in txs}
        if len(months) < 2:
            continue
        account = db.get(Account, account_id)
        out.append({
            "account_id": account_id, "account": account.name if account else "",
            "amount": str(-from_minor(amount, txs[0].currency)), "currency": txs[0].currency,
            "day": round(sum(t.day.day for t in txs) / len(txs)), "months": len(months),
            "per_month": round(len(txs) / len(months)),
            "last": max(t.day for t in txs).isoformat(),
        })  # fmt: skip
    return sorted(out, key=lambda s: (-s["months"], s["account"]))
