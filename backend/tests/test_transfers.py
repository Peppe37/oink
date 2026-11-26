"""Money moved between one's own accounts is still one's money."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from oink.core.db import Account, Database, Rate, Transaction, User
from oink.core.security import create_user
from oink.services.ledger import NewMovement, add_movements
from oink.services.reports import Book, compare
from oink.services.transfers import match_transfers, untracked


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path / 'oink.db'}")
    database.create()
    return database


def _setup(db: Database, *specs: tuple[str, str, str]) -> tuple[int, list[int]]:
    with db.sessions() as s:
        user = create_user(s, "giu", "giu-password-12")
        user.name = "Giuseppe Rossi"
        ids = []
        for name, institution, currency in specs:
            acc = Account(user_id=user.id, name=name, institution=institution, currency=currency)
            s.add(acc)
            s.flush()
            ids.append(acc.id)
        s.commit()
        return user.id, ids


def _add(db: Database, account_id: int, *moves: NewMovement) -> None:
    with db.sessions() as s:
        add_movements(s, s.get(Account, account_id), list(moves), "import")


def _txs(db: Database) -> list[Transaction]:
    with db.sessions() as s:
        return list(s.scalars(select(Transaction).order_by(Transaction.id)))


def test_the_two_legs_of_a_transfer_are_paired_and_not_spending(db: Database) -> None:
    uid, (rev, tr) = _setup(
        db, ("Revolut", "Revolut", "EUR"), ("Trade Republic", "Trade Republic", "EUR")
    )
    _add(db, rev,
         NewMovement(date(2026, 11, 2), 250000, "Stipendio ACME", counterparty="ACME"),
         NewMovement(date(2026, 11, 3), -100000, "To Trade Republic Bank GmbH"),
         NewMovement(date(2026, 11, 3), -4500, "Esselunga"))  # fmt: skip
    _add(db, tr, NewMovement(date(2026, 11, 4), 100000, "Bonifico in entrata Giuseppe Rossi"))
    out, inc = (
        next(t for t in _txs(db) if t.amount == -100000),
        next(t for t in _txs(db) if t.amount == 100000),
    )
    assert (out.category, out.category_by, out.transfer_id) == ("transfers", "transfer", inc.id)
    assert (inc.category, inc.transfer_id) == ("transfers", out.id)
    with db.sessions() as s:
        month = compare(Book.of(s, s.get(User, uid)), "2026-11")["current"]
    # only the groceries are spending, only the salary is income: the 1,000 € moved don't count
    assert month["spending"] == 45.0 and month["income"] == 2500.0


def test_no_pair_without_transfer_wording_or_when_ambiguous(db: Database) -> None:
    _uid, (a, b, c) = _setup(
        db, ("Conto A", "", "EUR"), ("Conto B", "", "EUR"), ("Conto C", "", "EUR")
    )
    _add(db, a, NewMovement(date(2026, 11, 3), -5000, "Ristorante Da Mario"))
    _add(db, b, NewMovement(date(2026, 11, 3), 5000, "Rimborso cena Luca"))
    assert all(t.transfer_id is None and t.category_by != "transfer" for t in _txs(db))
    _add(db, b, NewMovement(date(2026, 11, 10), 20000, "Giroconto"))
    _add(db, c, NewMovement(date(2026, 11, 10), 20000, "Giroconto"))
    _add(db, a, NewMovement(date(2026, 11, 10), -20000, "Giroconto"))
    assert all(
        t.transfer_id is None for t in _txs(db) if abs(t.amount) == 20000
    )  # two partners: left alone


def test_transfers_across_currencies_and_the_persons_choice_wins(
    db: Database, tmp_path: Path
) -> None:
    uid, (eur, usd) = _setup(db, ("Revolut EUR", "Revolut", "EUR"), ("Wise USD", "Wise", "USD"))
    with db.sessions() as s:
        s.add(Rate(code="USD", eur=Decimal("0.92")))
        s.commit()
    _add(db, eur, NewMovement(date(2026, 11, 5), -46000, "Transfer to Wise"))
    _add(db, usd, NewMovement(date(2026, 11, 6), 49900, "Money added from EUR"))
    usd_leg = next(t for t in _txs(db) if t.currency == "USD")
    assert usd_leg.transfer_id is not None
    client_db = db
    with client_db.sessions() as s:
        from oink.services.transfers import unlink

        tx = s.get(Transaction, usd_leg.id)
        tx.category, tx.category_by = "income", "user"
        unlink(s, tx)
        s.commit()
        assert match_transfers(s, s.get(User, uid)) == 0  # the person said no: it stays income
    eur_leg = next(t for t in _txs(db) if t.currency == "EUR")
    assert eur_leg.transfer_id is None  # still money sent to own Wise, just no partner now


def test_money_sent_to_an_untracked_own_broker_is_a_transfer_not_spending(db: Database) -> None:
    uid, (rev,) = _setup(db, ("Revolut", "Revolut", "EUR"))
    _add(db, rev,
         NewMovement(date(2026, 11, 3), -30000, "To Trade Republic Bank GmbH"),
         NewMovement(date(2026, 11, 4), -1200, "Revolut Bar"))  # fmt: skip
    sent = next(t for t in _txs(db) if t.amount == -30000)
    assert sent.category == "transfers" and sent.transfer_id is None
    assert next(t for t in _txs(db) if t.amount == -1200).category_by != "transfer"
    with db.sessions() as s:
        user = s.get(User, uid)
        found = untracked(s, user, since=date(2026, 10, 1))
        assert found == [{"institution": "Trade Republic", "currency": "EUR", "count": 1,
                          "total": "300.00", "last": "2026-11-03"}]  # fmt: skip
        assert compare(Book.of(s, user), "2026-11")["current"]["spending"] == 12.0


def test_the_api_shows_the_pair_and_frees_it_on_correction(owner: TestClient) -> None:
    a = owner.post("/api/accounts", json={"name": "Revolut", "institution": "Revolut"}).json()
    b = owner.post(
        "/api/accounts", json={"name": "Intesa", "institution": "Intesa Sanpaolo"}
    ).json()
    out = owner.post(
        "/api/transactions",
        json={
            "account_id": a["id"],
            "day": "2026-11-03",
            "amount": "-50",
            "description": "Giroconto verso Intesa",
        },
    ).json()
    inc = owner.post(
        "/api/transactions",
        json={
            "account_id": b["id"],
            "day": "2026-11-03",
            "amount": "50",
            "description": "Giroconto da Revolut",
        },
    ).json()
    listed = {t["id"]: t for t in owner.get("/api/transactions").json()}
    assert (
        listed[out["id"]]["transfer_id"] == inc["id"]
        and listed[inc["id"]]["category"] == "transfers"
    )
    owner.patch(f"/api/transactions/{inc['id']}", json={"category": "income", "learn": False})
    listed = {t["id"]: t for t in owner.get("/api/transactions").json()}
    assert listed[out["id"]]["transfer_id"] is None and listed[out["id"]]["category"] == ""


def test_equal_balanced_own_transfers_are_excluded_without_guessing_pairs(db: Database) -> None:
    uid, (a, b) = _setup(db, ("Revolut", "Revolut", "EUR"), ("Intesa", "Intesa", "EUR"))
    # Two same-day own transfers are unambiguous as a group, but their individual
    # reference numbers cannot be recovered from a truncated destination bank feed.
    _add(
        db,
        a,
        NewMovement(date(2026, 11, 3), -1000, "Bonifico verso Intesa"),
        NewMovement(date(2026, 11, 3), -1000, "Bonifico verso Intesa"),
    )
    _add(
        db,
        b,
        NewMovement(date(2026, 11, 3), 1000, "Accredito bonifico A"),
        NewMovement(date(2026, 11, 3), 1000, "Accredito bonifico B"),
    )
    txs = _txs(db)
    assert len(txs) == 4
    assert all(t.category == "transfers" for t in txs)
    with db.sessions() as s:
        month = compare(Book.of(s, s.get(User, uid)), "2026-11")["current"]
    assert month["income"] == 0 and month["spending"] == 0
