"""Fixed costs know their payments: each one is found in the movements (its payee text and
an amount close to the cost's), its next due date moves on by itself when it is paid, and a
payment that doesn't come when expected is flagged (Home), until the person says «wait» or
«this cost is over»."""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from oink.core.db import FixedCost, Transaction, User
from oink.core.money import from_minor
from oink.services.ledger import normalise

EARLY, LATE = 6, 5  # a payment counts from 6 days before the due date; flagged 5 days after
MONTHS = {"monthly": 1, "quarterly": 3, "yearly": 12}


def step(day: date, frequency: str) -> date:
    if frequency == "weekly":
        return day + timedelta(days=7)
    months = MONTHS.get(frequency, 1)
    y, m = divmod(day.month - 1 + months, 12)
    year, month = day.year + y, m + 1
    return day.replace(
        year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1])
    )


def payments(db: Session, cost: FixedCost, siblings: int = 1) -> list[Transaction]:
    """Its payments, newest first: the payee text, and (when no other cost shares that
    payee, as split parts do) an amount within a quarter of the cost's."""
    key = normalise(cost.match or cost.name)
    if len(key) < 3:
        return []
    out = []
    for t in db.scalars(
        select(Transaction).where(Transaction.user_id == cost.user_id, Transaction.amount < 0, Transaction.category_by != "split")
        .order_by(Transaction.day.desc())
    ):  # fmt: skip
        if key not in normalise(f"{t.description} {t.counterparty}"):
            continue
        paid = -from_minor(t.amount, t.currency)
        if siblings == 1 and not (
            cost.amount * Decimal("0.75") <= paid <= cost.amount * Decimal("1.25")
        ):
            continue
        out.append(t)
    return out


def _siblings(costs: list[FixedCost], cost: FixedCost) -> int:
    key = normalise(cost.match or cost.name)
    return sum(1 for c in costs if normalise(c.match or c.name) == key)


def refresh(db: Session, user: User, today: date | None = None) -> list[dict[str, Any]]:
    """Move each due date past the payments that came; return the ones missing."""
    today = today or date.today()
    costs = list(
        db.scalars(
            select(FixedCost).where(FixedCost.user_id == user.id, FixedCost.active.is_(True))
        )
    )
    missing = []
    for cost in costs:
        paid = payments(db, cost, _siblings(costs, cost))
        days = sorted({t.day for t in paid})
        if cost.next_due is None and days:
            cost.next_due = step(days[-1], cost.frequency)
        if cost.next_due is None:
            continue
        guard = 0
        while any(cost.next_due - timedelta(days=EARLY) <= d for d in days) and guard < 60:
            last = max(d for d in days if d >= cost.next_due - timedelta(days=EARLY))
            if last < cost.next_due - timedelta(days=EARLY):
                break
            cost.next_due = step(cost.next_due, cost.frequency)
            days = [d for d in days if d > last]
            guard += 1
        late = cost.next_due + timedelta(days=LATE) < today
        snoozed = cost.snoozed_until is not None and cost.snoozed_until >= today
        if late and not snoozed:
            missing.append({"id": cost.id, "name": cost.name, "amount": str(cost.amount),
                            "currency": cost.currency, "due": cost.next_due.isoformat(),
                            "days_late": (today - cost.next_due).days})  # fmt: skip
    db.commit()
    return missing


def history(db: Session, user: User, cost: FixedCost) -> dict[str, Any]:
    costs = list(db.scalars(select(FixedCost).where(FixedCost.user_id == user.id)))
    paid = payments(db, cost, _siblings(costs, cost))
    total = sum((-from_minor(t.amount, t.currency) for t in paid), Decimal(0))
    return {
        "payments": [{"id": t.id, "day": t.day.isoformat(), "amount": str(from_minor(t.amount, t.currency)),
                      "currency": t.currency, "name": t.counterparty or t.description} for t in paid[:60]],
        "count": len(paid), "total": str(total),
        "next_due": cost.next_due.isoformat() if cost.next_due else None,
    }  # fmt: skip
