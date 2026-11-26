"""A confirmed portfolio image corrects quantities without inventing purchases or duplicates."""

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from oink.core.db import Account, Holding, Plan, Quote, Transaction, User
from oink.services.planning import holdings_value
from oink.services.plans import extra_units, plan_positions
from oink.services.reports import Book


def test_document_positions_are_idempotent_owned_and_do_not_change_cash(
    owner: TestClient,
    second: TestClient,
) -> None:
    account = owner.post("/api/accounts", json={"name": "Broker", "balance": "302.27"}).json()["id"]
    doc = owner.post(
        "/api/documents",
        files={"file": ("portfolio.txt", b"Investment portfolio")},
        data={"kind": "investment"},
    ).json()
    body = {
        "as_of": "2026-11-26",
        "account_id": account,
        "positions": [
            {
                "symbol": "AAPL",
                "name": "Apple",
                "quantity": "1.25",
                "currency": "EUR",
                "cost": "300",
            }
        ],
    }
    from .conftest import token

    for scope in ("accounts:write", "documents:write"):
        assert (
            owner.post(
                f"/api/documents/{doc['id']}/positions", json=body, headers=token(owner, scope)
            ).status_code
            == 403
        )
    first = owner.post(f"/api/documents/{doc['id']}/positions", json=body)
    assert first.status_code == 200, first.text
    again = owner.post(f"/api/documents/{doc['id']}/positions", json=body)
    assert again.json()["holdings"] == first.json()["holdings"]
    assert owner.get("/api/accounts").json()[0]["balance"] == "302.27"
    assert second.post(f"/api/documents/{doc['id']}/positions", json=body).status_code == 404
    body["as_of"] = "2026-11-25"
    assert owner.post(f"/api/documents/{doc['id']}/positions", json=body).status_code == 422
    body["as_of"] = "2026-11-26"
    body["positions"].append(body["positions"][0].copy())
    assert owner.post(f"/api/documents/{doc['id']}/positions", json=body).status_code == 422
    with owner.app.state.db.sessions() as db:  # type: ignore[attr-defined]
        holdings = list(db.scalars(select(Holding)))
        assert len(holdings) == 1 and holdings[0].quantity == Decimal("1.25")
        assert holdings[0].document_id == doc["id"]


async def test_reported_quantities_win_and_snapshot_only_adds_later_purchases(
    owner: TestClient,
    monkeypatch: Any,
) -> None:
    async def no_estimate(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("A statement already supplies exact quantities")

    monkeypatch.setattr("oink.services.plans.daily_closes", no_estimate)
    account = owner.post("/api/accounts", json={"name": "Broker", "balance": "100"}).json()["id"]
    with owner.app.state.db.sessions() as db:  # type: ignore[attr-defined]
        acc = db.get(Account, account)
        user = db.get(User, acc.user_id)
        plan = Plan(
            user_id=user.id,
            symbol="AAPL",
            name="Apple",
            amount=Decimal(100),
            start=date(2026, 11, 16),
            account_id=account,
        )
        db.add(plan)
        db.flush()
        for month, quantity in [(11, "0.3108"), (12, "0.2")]:
            db.add(
                Transaction(
                    user_id=user.id,
                    account_id=account,
                    plan_id=plan.id,
                    day=date(2026, month, 16),
                    amount=-10000,
                    currency="EUR",
                    description=f"Savings plan execution Apple, quantity: {quantity}",
                    fingerprint=f"purchase-{month}",
                )
            )
        db.add(Quote(symbol="AAPL", currency="EUR", price=Decimal(100)))
        db.commit()
        positions = await plan_positions(db, user)
        assert positions[plan.id]["units"] == "0.510800"
        assert positions[plan.id]["units_estimated"] is False
        holding = Holding(
            user_id=user.id,
            symbol="AAPL",
            currency="EUR",
            quantity=Decimal("1.25"),
            as_of=date(2026, 11, 26),
            account_id=account,
        )
        db.add(holding)
        db.commit()
        assert extra_units(db, plan, holding, positions[plan.id]) == Decimal("0.2")
        values = await holdings_value(db, Book.of(db, user), refresh=False)
        assert len(values["items"]) == 1
        assert values["items"][0]["quantity"] == "1.450000"
        assert values["total"] == 145


def test_image_upload_proposes_positions_but_never_writes_without_confirmation(
    owner: TestClient,
    monkeypatch: Any,
) -> None:
    ai = owner.app.state.ai  # type: ignore[attr-defined]
    monkeypatch.setattr(type(ai), "can_read_text", property(lambda self: True))

    async def read_image(*args: Any) -> str:
        return "Portfolio Apple AAPL 1.25 shares, NVIDIA quantity not shown"

    async def describe(*args: Any) -> dict[str, Any]:
        return {"kind": "investment", "title": "Positions", "facts": {}}

    async def extract(*args: Any) -> dict[str, Any]:
        return {
            "as_of": "2026-11-26",
            "positions": [
                {"symbol": "AAPL", "name": "Apple", "quantity": "1.25", "currency": "EUR"},
                {"symbol": "NVDA", "name": "NVIDIA", "quantity": None, "currency": "EUR"},
            ],
        }

    monkeypatch.setattr(ai, "read_image", read_image)
    monkeypatch.setattr(ai, "describe_document", describe)
    monkeypatch.setattr(ai, "json", extract)
    reply = owner.post("/api/documents", files={"file": ("positions.png", b"\x89PNG\r\n\x1a\n")})
    assert reply.status_code == 200, reply.text
    doc = reply.json()
    assert doc["review"]["positions"]["positions"][0]["quantity"] == "1.25"
    assert doc["review"]["positions"]["positions"][1]["quantity"] is None
    with owner.app.state.db.sessions() as db:  # type: ignore[attr-defined]
        assert db.scalar(select(Holding.id)) is None


async def test_unconvertible_estimated_plan_is_unknown_instead_of_zero(
    owner: TestClient,
    monkeypatch: Any,
) -> None:
    async def closes(*args: Any, **kwargs: Any) -> dict[date, Decimal]:
        return {date(2026, 11, 16): Decimal(100)}

    monkeypatch.setattr("oink.services.plans.daily_closes", closes)
    account = owner.post("/api/accounts", json={"name": "Broker", "balance": "100"}).json()["id"]
    with owner.app.state.db.sessions() as db:  # type: ignore[attr-defined]
        acc = db.get(Account, account)
        user = db.get(User, acc.user_id)
        plan = Plan(
            user_id=user.id,
            symbol="TESTUSD",
            amount=Decimal(100),
            start=date(2026, 11, 16),
            account_id=account,
            currency="EUR",
        )
        db.add(plan)
        db.flush()
        db.add(
            Transaction(
                user_id=user.id,
                account_id=account,
                plan_id=plan.id,
                day=date(2026, 11, 16),
                amount=-10000,
                currency="EUR",
                description="Anonymous broker purchase",
                fingerprint="usd-test",
            )
        )
        db.add(Quote(symbol="TESTUSD", currency="USD", price=Decimal(100)))
        db.commit()
        values = await holdings_value(db, Book.of(db, user), refresh=False)
        assert values["items"][0]["quantity"] is None
        assert values["items"][0]["value"] is None


async def test_value_only_screenshot_is_kept_as_a_dated_value_without_invented_units(
    owner: TestClient,
) -> None:
    account = owner.post("/api/accounts", json={"name": "Broker", "balance": "100"}).json()["id"]
    doc = owner.post(
        "/api/documents",
        files={"file": ("values.txt", b"Portfolio values")},
        data={"kind": "investment"},
    ).json()
    body = {
        "as_of": "2026-11-26",
        "account_id": account,
        "positions": [
            {
                "symbol": "CUSTOM:FIRST",
                "name": "First",
                "quantity": None,
                "market_value": "100.11",
                "currency": "EUR",
            },
            {
                "symbol": "CUSTOM:SECOND",
                "name": "Second",
                "quantity": None,
                "market_value": "200.22",
                "currency": "EUR",
                "kind": "etf",
            },
        ],
    }
    for _ in range(2):
        response = owner.post(f"/api/documents/{doc['id']}/positions", json=body)
        assert response.status_code == 200, response.text
    with owner.app.state.db.sessions() as db:  # type: ignore[attr-defined]
        acc = db.get(Account, account)
        user = db.get(User, acc.user_id)
        values = await holdings_value(db, Book.of(db, user), refresh=False)
        assert values["total"] == 300.33
        assert len(values["items"]) == 2
        assert all(
            i["quantity"] is None and i["valuation_source"] == "snapshot" for i in values["items"]
        )
        assert values["gain"] is None
        assert acc.balance == 10000
