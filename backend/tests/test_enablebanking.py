"""Enable Banking: consent within each bank's limit, the whole history at link time,
the same account when a bank is linked again."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import func, select

from oink.core.db import Account, Connection, Database, Transaction
from oink.core.security import Vault, create_user
from oink.providers.enablebanking import EnableBanking
from oink.services.connections import finish_bank

KEY = (
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    .decode()
)


class FakeEB:
    def __init__(self) -> None:
        self.auth: dict[str, Any] = {}
        self.history_from: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/aspsps":
            return httpx.Response(200, json={"aspsps": [
                {"name": "Trade Republic", "country": "IT", "maximum_consent_validity": 7776000},
                {"name": "Revolut", "country": "IT", "maximum_consent_validity": 15552000}]})  # fmt: skip
        if path == "/auth":
            self.auth = json.loads(request.content)
            return httpx.Response(200, json={"url": "https://bank.example/login"})
        if path == "/sessions":
            return httpx.Response(
                200,
                json={
                    "session_id": "s1",
                    "access": {"valid_until": "2027-05-01T00:00:00Z"},
                    "accounts": [
                        {
                            "uid": "u1",
                            "account_id": {"iban": "LT123"},
                            "currency": "EUR",
                            "name": "Revolut",
                        }
                    ],
                },
            )
        if path.endswith("/transactions"):
            since = request.url.params["date_from"]
            self.history_from.append(since)
            if date.fromisoformat(since) < date.today() - timedelta(days=400):
                return httpx.Response(400, json={"message": "date_from too far in the past"})
            return httpx.Response(200, json={"transactions": [
                {"transaction_amount": {"amount": "12.00"}, "credit_debit_indicator": "DBIT", "booking_date": "2026-01-10",
                 "status": "BOOK", "entry_reference": "a", "remittance_information": ["Bar"]},
                {"transaction_amount": {"amount": "2000.00"}, "credit_debit_indicator": "CRDT", "booking_date": "2025-12-01",
                 "status": "BOOK", "entry_reference": "b", "remittance_information": ["Stipendio"]}]})  # fmt: skip
        return httpx.Response(404, json={})


@pytest.fixture
def eb() -> tuple[EnableBanking, FakeEB]:
    fake = FakeEB()
    return EnableBanking(
        "app-id", KEY, client=httpx.AsyncClient(transport=httpx.MockTransport(fake))
    ), fake


async def test_consent_never_longer_than_the_bank_allows(eb: tuple[EnableBanking, FakeEB]) -> None:
    bank, fake = eb
    await bank.start("Trade Republic", "IT", "st", "https://oink.test/cb")
    valid = datetime.fromisoformat(fake.auth["access"]["valid_until"])
    assert valid - datetime.now(valid.tzinfo) < timedelta(days=90)
    await bank.start("Revolut", "IT", "st", "https://oink.test/cb")
    valid = datetime.fromisoformat(fake.auth["access"]["valid_until"])
    assert valid - datetime.now(valid.tzinfo) > timedelta(days=150)


async def test_linking_takes_the_whole_history_and_relinking_keeps_the_account(
    eb: tuple[EnableBanking, FakeEB], tmp_path: Path
) -> None:
    bank, fake = eb
    db = Database(f"sqlite:///{tmp_path / 'oink.db'}")
    db.create()
    vault = Vault("", tmp_path)
    with db.sessions() as s:
        user = create_user(s, "giu", "giu-password-12")
        first = Connection(
            user_id=user.id, provider="enablebanking", label="Revolut", state="a", status="pending"
        )
        s.add(first)
        s.commit()
        accounts = await finish_bank(s, vault, bank, first, "code")
        assert len(accounts) == 1
        # the bank refused two years, then gave what it has: both movements, the old one too
        assert fake.history_from[0] < fake.history_from[-1]
        assert s.scalar(select(func.count()).select_from(Transaction)) == 2
        again = Connection(
            user_id=user.id, provider="enablebanking", label="Revolut", state="b", status="pending"
        )
        s.add(again)
        s.commit()
        await finish_bank(s, vault, bank, again, "code")
        assert s.scalar(select(func.count()).select_from(Account)) == 1  # the same account, moved
        assert s.scalar(select(Account.connection_id)) == again.id
        assert s.get(Connection, first.id) is None  # the old consent is gone
        assert s.scalar(select(func.count()).select_from(Transaction)) == 2  # no duplicates


def test_a_statement_over_a_linked_account_adds_only_what_the_bank_did_not(tmp_path: Path) -> None:
    from oink.services.ledger import NewMovement, add_movements

    db = Database(f"sqlite:///{tmp_path / 'oink.db'}")
    db.create()
    with db.sessions() as s:
        user = create_user(s, "giu", "giu-password-12")
        acc = Account(user_id=user.id, name="Revolut", source="linked")
        s.add(acc)
        s.commit()
        add_movements(
            s,
            acc,
            [NewMovement(date(2026, 9, 3), -1200, "CARD PAYMENT BAR", external_id="x1")],
            "linked",
        )
        result = add_movements(s, acc, [
            NewMovement(date(2026, 9, 2), -1200, "Bar Centrale"),      # the same coffee, booked a day apart
            NewMovement(date(2026, 6, 1), -5000, "Ristorante"),        # older than the link: new
            NewMovement(date(2026, 9, 3), -1200, "Bar Centrale"),      # a second coffee that day: new
        ], "import")  # fmt: skip
        assert len(result.added) == 2 and result.duplicates == 1
