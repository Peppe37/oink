"""Fixed costs know their payments: each one is found in the movements (its payee text and
an amount close to the cost's), its next due date moves on by itself when it is paid, and a
payment that doesn't come when expected is flagged (Home). The person answers:

- «wait»         it will come (a few days late): asked again tomorrow;
- «paid»         paid another way (cash…): a movement is written on the account it came
                 from, so it counts as spent like any other payment;
- «skip»         not due this time (a month off, a free month): nothing spent, the next
                 due date moves on, the history shows it as skipped;
- «it's over»    cancelled: no longer expected, its history stays."""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from oink.core.db import Account, FixedCost, Transaction, User
from oink.core.money import from_minor, to_minor
from oink.services.ledger import NewMovement, add_movements, normalise

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


class FixedError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _note(cost: FixedCost, event: dict[str, Any]) -> None:
    cost.events = [*(cost.events or []), event][-120:]  # a new list: JSON columns need it


def skip(db: Session, cost: FixedCost, today: date | None = None) -> date:
    """«Not due this time»: the expected payment is dropped, the next one is expected."""
    if cost.next_due is None:
        raise FixedError("no_due_date")
    _note(cost, {"due": cost.next_due.isoformat(), "kind": "skipped",
                 "at": (today or date.today()).isoformat()})  # fmt: skip
    cost.next_due = step(cost.next_due, cost.frequency)
    cost.snoozed_until = None
    db.commit()
    return cost.next_due


def paid_elsewhere(
    db: Session, user: User, cost: FixedCost, account: Account, day: date,
    balance_updated: bool = False,
) -> Transaction:  # fmt: skip
    """«Paid another way»: the payment is written on `account` (its balance goes down,
    unless the person already counted it there), and the due date moves on."""
    amount = -to_minor(cost.amount, account.currency)
    move = NewMovement(day=day, amount=amount, description=cost.name,
                       counterparty=cost.name, category=cost.category,
                       )  # fmt: skip
    result = add_movements(db, account, [move], "manual")
    if not result.added:
        raise FixedError("duplicate")
    tx = result.added[0]
    if account.source == "manual" and account.balance is not None and not balance_updated:
        account.balance += amount
    _note(cost, {"due": cost.next_due.isoformat() if cost.next_due else day.isoformat(),
                 "kind": "paid", "tx": tx.id, "at": day.isoformat()})  # fmt: skip
    cost.snoozed_until = None
    db.commit()
    refresh(db, user)  # the payment moves the due date on, like any other
    return tx


def history(db: Session, user: User, cost: FixedCost) -> dict[str, Any]:
    costs = list(db.scalars(select(FixedCost).where(FixedCost.user_id == user.id)))
    paid = payments(db, cost, _siblings(costs, cost))
    total = sum((-from_minor(t.amount, t.currency) for t in paid), Decimal(0))
    elsewhere = {e.get("tx") for e in cost.events or [] if e.get("kind") == "paid"}
    return {
        "payments": [{"id": t.id, "day": t.day.isoformat(), "amount": str(from_minor(t.amount, t.currency)),
                      "currency": t.currency, "name": t.counterparty or t.description,
                      "elsewhere": t.id in elsewhere} for t in paid[:60]],
        "skipped": [e["due"] for e in cost.events or [] if e.get("kind") == "skipped"][::-1],
        "count": len(paid), "total": str(total),
        "next_due": cost.next_due.isoformat() if cost.next_due else None,
    }  # fmt: skip
