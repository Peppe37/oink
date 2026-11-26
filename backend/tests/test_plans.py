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


def test_only_purchases_on_a_brokers_account_are_suggested_and_marking_one_teaches_the_plan(
    db: Database,
) -> None:
    uid, broker = _broker(db)
    with db.sessions() as s:
        user = s.get(User, uid)
        current = Account(user_id=uid, name="Revolut", institution="Revolut", source="linked")
        s.add(current)
        s.commit()
        # a gym, a phone, rent: monthly and the same amount, but on a current account
        add_movements(s, current, [NewMovement(date(2026, m, 5), -3990, "Palestra FitClub") for m in (9, 10, 11)]
                      + [NewMovement(date(2026, m, 1), -60000, "Affitto") for m in (9, 10, 11)], "linked")  # fmt: skip
        found = suggestions(s, user, date(2026, 11, 20))
        assert [(sg["account"], sg["amount"]) for sg in found] == [("Trade Republic", "100")]
        # the person marks one purchase: the plan learns its account and day, the rest follow
        plan = Plan(
            user_id=uid,
            symbol="AAPL",
            amount=Decimal(100),
            frequency="monthly",
            start=date(2026, 9, 1),
        )
        s.add(plan)
        s.commit()
        tx = s.scalars(
            select(Transaction).where(
                Transaction.account_id == broker, Transaction.day == date(2026, 9, 16)
            )
        ).first()
        plans_module.link_purchase(s, tx, plan)
        plan.account_id, plan.day = broker, 16
        s.commit()
        match_executions(s, user, date(2026, 11, 20))
        assert s.query(Transaction).filter(Transaction.plan_id == plan.id).count() == 3
        plans_module.unlink_purchase(s, tx)
        match_executions(s, user, date(2026, 11, 20))
        assert s.get(Transaction, tx.id).plan_id is None  # «not a purchase»: it stays so


async def test_a_brokers_statement_turns_into_ready_plans(db: Database, monkeypatch: Any) -> None:
    uid, broker = _broker(db)
    with db.sessions() as s:
        user = s.get(User, uid)
        acc = s.get(Account, broker)
        # the statement says what the bank link didn't: the same purchases, with ISIN and name
        add_movements(s, acc, [
            NewMovement(date(2026, m, 16), -10000, f"Savings plan execution {isin} {name}, quantity 0.5")
            for m in (9, 10, 11) for isin, name in (("US0378331005", "Apple"), ("US67066G1040", "NVIDIA"), ("IE00B5BMR087", "iShares Core S&P 500"))
        ], "import")  # fmt: skip
        assert (
            s.query(Transaction).filter(Transaction.account_id == broker).count() == 10
        )  # no duplicates
        assert (
            s.query(Transaction).filter(Transaction.description.like("%US0378331005%")).count() == 3
        )  # enriched

        async def search(query: str, client: Any = None) -> list[dict[str, str]]:
            return [{"symbol": {"US0378331005": "APC.DE", "US67066G1040": "NVD.DE"}.get(query, "SXR8.DE"),
                     "name": query, "kind": "stock", "exchange": "XETRA"}]  # fmt: skip

        import oink.services.markets as markets

        monkeypatch.setattr(markets, "search", search)
        plans_module._SYMBOLS.clear()
        found = await plans_module.proposals(s, user, date(2026, 11, 20))
        ready = sorted(
            (p["symbol"], p["amount"], p["day"], p["account_id"])
            for p in found
            if p["kind"] == "ready"
        )
        assert ready == [
            ("APC.DE", "100", 16, broker),
            ("NVD.DE", "100", 16, broker),
            ("SXR8.DE", "100", 16, broker),
        ]


def test_one_plan_of_three_securities_and_a_switch_to_an_unlisted_one(owner: Any) -> None:
    acc = owner.post(
        "/api/accounts", json={"name": "Trade Republic", "institution": "Trade Republic"}
    ).json()
    for month in ("09", "10", "11"):
        for _ in range(3):
            owner.post(
                "/api/transactions",
                json={
                    "account_id": acc["id"],
                    "day": f"2026-{month}-16",
                    "amount": "-100",
                    "description": "—",
                },
            )
    plans = owner.post("/api/plans/batch", json={"account_id": acc["id"], "day": 16, "start": "2026-09-01", "items": [
        {"symbol": "NVD.DE", "name": "NVIDIA", "amount": "100"}, {"symbol": "APC.DE", "name": "Apple", "amount": "100"},
        {"symbol": "SXR8.DE", "name": "iShares Core S&P 500", "amount": "100"}]}).json()  # fmt: skip
    assert len(plans) == 3
    nvda = plans[0]["id"]
    new = owner.post(
        f"/api/plans/{nvda}/replace",
        json={"symbol": "CUSTOM:ANTHROPIC", "name": "Anthropic", "start": "2026-12-01"},
    ).json()
    assert (
        new["symbol"] == "CUSTOM:ANTHROPIC" and new["day"] == 16 and new["account_id"] == acc["id"]
    )
    listed = {p["id"]: p for p in owner.get("/api/plans").json()}
    assert listed[nvda]["end"] == "2026-11-30" and listed[nvda]["position"]["executions"] == 3
    spending = [t for t in owner.get("/api/transactions").json() if t["category"] != "investments"]
    assert spending == []  # all nine purchases are investments


def test_next_payment_excludes_replaced_component_but_includes_the_new_one() -> None:
    from oink.services.plans import next_execution

    old = Plan(
        symbol="OLD",
        amount=Decimal(100),
        start=date(2026, 11, 1),
        end=date(2026, 11, 30),
        day=16,
        frequency="monthly",
        active=True,
    )
    new = Plan(
        symbol="NEW",
        amount=Decimal(100),
        start=date(2026, 12, 1),
        day=16,
        frequency="monthly",
        active=True,
    )
    assert next_execution(old, date(2026, 11, 26)) is None
    assert next_execution(new, date(2026, 11, 26)) == date(2026, 12, 16)
