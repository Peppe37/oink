"""Money parsing, recurring payments, AI wiring and the Enable Banking mapping."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.orm import Session

from oink.core.config import Settings
from oink.core.db import Account, Database
from oink.providers.enablebanking import EnableBanking, to_movement
from oink.services.ai import AI
from oink.services.importer import parse_csv, parse_statement
from oink.services.ledger import NewMovement, add_movements, to_cents
from oink.services.reports import facts_for_ai, recurring


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("-1.234,56", -123456),
        ("1,234.56", 123456),
        ("12,5", 1250),
        ("€ 3", 300),
        ("(45,00)", -4500),
        ("0.1", 10),
    ],
)
def test_to_cents(text: str, cents: int) -> None:
    assert to_cents(text) == cents


def test_csv_with_separate_debit_and_credit_columns() -> None:
    raw = (
        "Data,Descrizione,Uscite,Entrate\n01/11/2026,Affitto,700.00,\n02/11/2026,Rimborso,,25.50\n"
    )
    rows = parse_csv(raw.encode())
    assert [(r.description, r.amount_cents) for r in rows] == [
        ("Affitto", -70000),
        ("Rimborso", 2550),
    ]


def db_with_account(tmp_path: Path) -> tuple[Session, int]:
    database = Database(f"sqlite:///{tmp_path / 't.db'}")
    database.create()
    session = database.sessions()
    acc = Account(name="Conto")
    session.add(acc)
    session.commit()
    return session, acc.id


def test_recurring_finds_subscriptions_and_price_increases(tmp_path: Path) -> None:
    db, acc = db_with_account(tmp_path)
    today = date(2026, 11, 20)
    moves = []
    for i, cents in enumerate([1399, 1399, 1299, 1299]):
        moves.append(
            NewMovement(
                day=today - timedelta(days=30 * i),
                amount_cents=-cents,
                description="SPOTIFY P1234",
                counterparty="Spotify",
            )
        )
    moves.append(NewMovement(day=today, amount_cents=-5000, description="Una tantum"))
    add_movements(db, acc, moves, "import")
    [sub] = recurring(db, today=today)
    assert sub["name"] == "Spotify" and sub["increased"] and sub["monthly_cents"] == 1349


def test_without_descriptions_the_ai_sees_only_numbers(tmp_path: Path) -> None:
    db, acc = db_with_account(tmp_path)
    add_movements(
        db,
        acc,
        [
            NewMovement(
                day=date(2026, 11, 3),
                amount_cents=-4000,
                description="Farmacia Rossi",
                category="salute",
            )
        ],
        "api",
    )
    facts = facts_for_ai(db, "2026-11", with_text=False)
    assert "Farmacia" not in json.dumps(facts) and facts["current"]["by_category"] == {
        "salute": 4000
    }


async def test_pdf_goes_through_the_ai(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ai = AI(Settings(data_dir=tmp_path, ai_model="openai/test"))

    async def fake_json(system: str, user: str) -> Any:
        assert "Bonifico" in user
        return {
            "movements": [{"day": "2026-11-04", "amount": -100, "description": "Bonifico"}],
            "balance": 900,
        }

    monkeypatch.setattr(ai, "json", fake_json)
    monkeypatch.setattr("oink.services.importer.pdf_text", lambda raw: "04/11 Bonifico -100")
    parsed = await parse_statement("e.pdf", b"%PDF", ai)
    assert (
        parsed.by_ai
        and parsed.balance_cents == 90000
        and parsed.movements[0].amount_cents == -10000
    )


def test_enable_banking_transaction_mapping() -> None:
    move = to_movement(
        {
            "transaction_amount": {"amount": "12.50", "currency": "EUR"},
            "credit_debit_indicator": "DBIT",
            "booking_date": "2026-11-05",
            "creditor": {"name": "Esselunga"},
            "remittance_information": ["POS 123"],
            "entry_reference": "abc",
        }
    )
    assert move is not None and move.amount_cents == -1250 and move.counterparty == "Esselunga"
    assert move.external_id == "abc" and move.description == "POS 123"


async def test_enable_banking_signs_requests_and_pages_transactions() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    pages = iter(
        [
            {
                "transactions": [
                    {
                        "transaction_amount": {"amount": "5"},
                        "credit_debit_indicator": "CRDT",
                        "booking_date": "2026-11-01",
                        "status": "BOOK",
                    }
                ],
                "continuation_key": "k",
            },
            {
                "transactions": [
                    {
                        "transaction_amount": {"amount": "7"},
                        "credit_debit_indicator": "DBIT",
                        "booking_date": "2026-11-02",
                        "status": "PDNG",
                    }
                ]
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        claims = jwt.decode(
            request.headers["authorization"][7:],
            key.public_key(),
            algorithms=["RS256"],
            audience="api.enablebanking.com",
        )
        assert claims["iss"] == "enablebanking.com"
        assert jwt.get_unverified_header(request.headers["authorization"][7:])["kid"] == "app-1"
        return httpx.Response(200, json=next(pages))

    bank = EnableBanking(
        "app-1", pem, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    moves = await bank.movements("uid-1", date(2026, 11, 1))
    assert [m.amount_cents for m in moves] == [500]  # the pending one waits
