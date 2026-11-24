"""Auth, scoped tokens, accounts, movements, imports and statement requests."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from oink.services.jobs import open_statement_requests
from tests.conftest import token

CSV = (
    b"Estratto conto al 30/11/2026\n\n"
    b"Data contabile;Data valuta;Descrizione;Importo\n"
    b"03/11/2026;03/11/2026;PAGAMENTO POS ESSELUNGA MILANO;-54,30\n"
    b"05/11/2026;05/11/2026;STIPENDIO NOVEMBRE;2.150,00\n"
    b"05/11/2026;05/11/2026;CAFFE BAR;-1,20\n"
    b"05/11/2026;05/11/2026;CAFFE BAR;-1,20\n"
    b";;Saldo finale;1.234,00\n"
)


def test_everything_needs_a_login_and_the_password_is_checked(client: TestClient) -> None:
    assert client.get("/api/accounts").status_code == 401
    assert client.post("/api/auth/login", json={"password": "sbagliata"}).status_code == 401
    assert client.get("/api/auth/status").json()["configured"] is True


def test_tokens_have_separate_read_and_write_scopes(owner: TestClient) -> None:
    read = token(owner, "accounts:read")
    owner.cookies.clear()
    assert owner.get("/api/accounts", headers=read).status_code == 200
    reply = owner.post("/api/accounts", json={"name": "X"}, headers=read)
    assert reply.status_code == 403 and "accounts:write" in reply.json()["detail"]
    assert owner.get("/api/tokens", headers=read).status_code == 403  # never manage tokens
    assert (
        owner.get("/api/accounts", headers={"Authorization": "Bearer oink_nope"}).status_code == 401
    )


def test_revoked_token_stops_working(owner: TestClient) -> None:
    headers = token(owner, "accounts:read")
    [listed] = owner.get("/api/tokens").json()
    assert "token" not in listed and listed["prefix"].startswith("oink_")
    owner.delete(f"/api/tokens/{listed['id']}")
    assert owner.get("/api/accounts", headers=headers).status_code == 401


def test_unknown_scope_is_refused(owner: TestClient) -> None:
    assert owner.post("/api/tokens", json={"name": "x", "scopes": ["root"]}).status_code == 422


def test_csv_import_dedupes_keeps_twin_coffees_and_closes_the_request(owner: TestClient) -> None:
    acc = owner.post("/api/accounts", json={"name": "Conto Intesa", "balance_cents": 0}).json()
    with Session(owner.app.state.db.engine) as db:  # type: ignore[attr-defined]
        [req] = open_statement_requests(db, date(2026, 12, 1), 1)
        assert req.month == "2026-11"
    files = {"file": ("estratto.csv", CSV, "text/csv")}
    first = owner.post("/api/imports", data={"account_id": acc["id"]}, files=files).json()
    assert first["added"] == 4 and first["month"] == "2026-11" and first["request_closed"]
    again = owner.post("/api/imports", data={"account_id": acc["id"]}, files=files).json()
    assert again == {**again, "added": 0, "duplicates": 4}
    assert owner.get("/api/requests").json() == []
    salary = owner.get("/api/transactions", params={"q": "stipendio"}).json()
    assert salary[0]["amount_cents"] == 215000


def test_a_category_correction_becomes_a_rule(owner: TestClient) -> None:
    acc = owner.post("/api/accounts", json={"name": "Carta"}).json()
    tx = owner.post(
        "/api/transactions",
        json={
            "account_id": acc["id"],
            "day": "2026-11-02",
            "amount_cents": -899,
            "description": "NETFLIX.COM",
        },
    ).json()
    owner.patch(f"/api/transactions/{tx['id']}", json={"category": "abbonamenti"})
    later = owner.post(
        "/api/transactions",
        json={
            "account_id": acc["id"],
            "day": "2026-12-02",
            "amount_cents": -899,
            "description": "NETFLIX.COM",
        },
    ).json()
    assert later["category"] == "abbonamenti" and later["category_by"] == "rule"
    dup = owner.post(
        "/api/transactions",
        json={
            "account_id": acc["id"],
            "day": "2026-12-02",
            "amount_cents": -899,
            "description": "NETFLIX.COM",
            "note": "x",
        },
    )
    assert dup.status_code in (200, 409)


def test_overview_has_everything_for_the_home(owner: TestClient) -> None:
    acc = owner.post("/api/accounts", json={"name": "Conto", "balance_cents": 100000}).json()
    owner.post(
        "/api/transactions",
        json={
            "account_id": acc["id"],
            "day": date.today().isoformat(),
            "amount_cents": -2500,
            "description": "Pizzeria",
            "category": "ristoranti",
        },
    )
    data = owner.get("/api/reports/overview").json()
    assert data["net_worth"]["total_cents"] == 97500
    assert data["month"]["current"]["by_category"] == {"ristoranti": 2500}
    assert len(data["series"]) == 12 and data["balance"][-1]["cents"] == 97500


def test_ai_off_gives_a_clear_error(owner: TestClient) -> None:
    reply = owner.post("/api/ask", json={"question": "quanto ho speso?"})
    assert reply.status_code == 503 and "IA" in reply.json()["detail"]
    pdf = owner.post("/api/accounts", json={"name": "Y"}).json()
    files = {"file": ("e.pdf", b"%PDF-1.4 nothing", "application/pdf")}
    assert (
        owner.post("/api/imports", data={"account_id": pdf["id"]}, files=files).status_code == 422
    )


def test_bank_connect_is_owner_only_and_needs_configuration(owner: TestClient) -> None:
    headers = token(owner, "banks:write", "banks:read")
    owner.cookies.clear()
    assert (
        owner.post("/api/banks/connect", data={"bank": "Intesa"}, headers=headers).status_code
        == 403
    )
    assert owner.get("/api/banks", headers=headers).status_code == 503
