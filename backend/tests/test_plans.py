"""A savings plan is recognised in the broker's movements: never spending, units counted."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from oink.core.db import Account, Database, Plan, Transaction, User
from oink.core.security import create_user
from oink.services import plans as plans_module
from oink.services.ledger import NewMovement, add_movements
from oink.services.plans import expected_days, match_executions, plan_positions, suggestions
from oink.services.reports import Book, compare


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path / 'oink.db'}")
    database.create()
    return database


def _broker(db: Database) -> tuple[int, int]:
    with db.sessions() as s:
        user = create_user(s, "giu", "giu-password-12")
        acc = Account(
            user_id=user.id, name="Trade Republic", institution="Trade Republic", source="linked"
        )
        s.add(acc)
        s.commit()
        # Trade Republic through the bank link: only dates and amounts
        moves = [
            NewMovement(date(2026, m, d), -10000, "—", external_id="")
            for m in (9, 10, 11)
            for d in (16, 16, 16)
        ]
        moves += [NewMovement(date(2026, 10, 3), -2500, "—")]  # something else
        add_movements(s, acc, moves, "linked")
        return user.id, acc.id


def test_the_days_a_plan_should_have_bought() -> None:
    plan = Plan(
        symbol="X",
        amount=Decimal(100),
        frequency="monthly",
        start=date(2026, 9, 16),
        day=16,
        end=date(2026, 11, 30),
    )
    assert expected_days(plan, date(2027, 1, 20)) == [
        date(2026, 9, 16),
        date(2026, 10, 16),
        date(2026, 11, 16),
    ]
    short = Plan(
        symbol="X", amount=Decimal(100), frequency="monthly", start=date(2026, 1, 31), day=31
    )
    assert expected_days(short, date(2026, 3, 1))[1] == date(2026, 2, 28)


async def test_three_plans_of_100_on_the_16th_are_investments(
    db: Database, monkeypatch: Any
) -> None:
    uid, acc = _broker(db)
    with db.sessions() as s:
        user = s.get(User, uid)
        assert (
            suggestions(s, user, date(2026, 11, 20))[0]["per_month"] == 3
        )  # «3 × 100 € around the 16th»
        for symbol in ("NVDA", "AAPL", "^GSPC"):
            s.add(Plan(user_id=uid, symbol=symbol, amount=Decimal(100), frequency="monthly",
                       start=date(2026, 9, 1), day=16, account_id=acc))  # fmt: skip
        s.commit()
        assert match_executions(s, user, date(2026, 11, 20)) == 9
        assert match_executions(s, user, date(2026, 11, 20)) == 0  # stable
        txs = list(s.scalars(select(Transaction).where(Transaction.account_id == acc)))
        assert sum(1 for t in txs if t.category == "investments" and t.plan_id) == 9
        other = next(t for t in txs if t.amount == -2500)
        assert other.plan_id is None
        assert suggestions(s, user, date(2026, 11, 20)) == []  # nothing left to suggest

        async def closes(symbol: str, years: int = 3, client: Any = None) -> dict[date, Decimal]:
            return {
                date(2026, 9, 16): Decimal(50),
                date(2026, 10, 16): Decimal(100),
                date(2026, 11, 13): Decimal(200),
            }

        monkeypatch.setattr(plans_module, "daily_closes", closes)
        positions = await plan_positions(s, user)
        nvda = next(p for p in s.scalars(select(Plan)) if p.symbol == "NVDA")
        # 100/50 + 100/100 + 100/200 (the 16th is a Monday: no close on the 15th needed)
        assert positions[nvda.id]["executions"] == 3 and Decimal(
            positions[nvda.id]["units"]
        ) == Decimal("3.5")
        spending = compare(Book.of(s, user), "2026-11")["current"]["spending"]
        assert spending == 0  # the plan's purchases are not spending
