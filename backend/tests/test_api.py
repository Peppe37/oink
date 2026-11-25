from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from oink.app import create_app
from oink.core.config import Settings
from oink.core.db import Rate
from oink.providers import PROVIDERS
from oink.providers.base import Field, Provider, RemoteAccount
from tests.conftest import token

CSV = (
    b"Conto IT60 X054 2811 1010 0000 0123 456\n"
    b"Data contabile;Descrizione;Importo\n"
    b"03/11/2026;PAGAMENTO POS ESSELUNGA;-45,20\n"
    b"05/11/2026;STIPENDIO ACME;2.100,00\n"
)


def test_first_run_creates_the_admin(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path, update_check=False, enable_banking_key_file=tmp_path / "x"
    )
    with TestClient(create_app(settings, background=False), base_url="https://t") as c:
        assert c.get("/api/auth/status").json()["configured"] is False
        reply = c.post(
            "/api/auth/setup",
            json={"username": "Giu", "password": "abbastanza-lunga", "language": "it",
                  "base_currency": "eur"},
        )  # fmt: skip
        assert reply.status_code == 200, reply.text
        status = c.get("/api/auth/status").json()
        assert status["user"]["username"] == "giu" and status["user"]["is_admin"]
        assert status["user"]["base_currency"] == "EUR"
        assert (
            c.post("/api/auth/setup", json={"username": "x", "password": "y" * 12}).status_code
            == 409
        )


def test_login_needs_the_right_password(client: TestClient) -> None:
    reply = client.post("/api/auth/login", json={"username": "admin", "password": "nope-nope-nope"})
    assert reply.status_code == 401 and reply.json()["detail"] == "wrong_credentials"
    assert client.get("/api/accounts").status_code == 401


def test_users_never_see_each_others_money(owner: TestClient, second: TestClient) -> None:
    mine = owner.post("/api/accounts", json={"name": "Mine", "balance": "100"}).json()
    owner.post("/api/transactions", json={"account_id": mine["id"], "day": "2026-11-02",
                                          "amount": "-5", "description": "coffee"})  # fmt: skip
    assert second.get("/api/accounts").json() == []
    assert second.get("/api/transactions").json() == []
    assert second.patch(f"/api/accounts/{mine['id']}", json={"name": "x"}).status_code == 404
    assert second.post("/api/transactions", json={"account_id": mine["id"], "day": "2026-11-02",
                                                  "amount": "1", "description": "x"}).status_code == 404  # fmt: skip
    assert second.get("/api/users").status_code == 403  # not an admin
    assert len(owner.get("/api/users").json()) == 2


def test_an_invite_works_once(owner: TestClient) -> None:
    url = owner.post("/api/users", json={"username": "luca"}).json()["invite_url"]
    assert url.startswith("https://oink.test/invite/")
    secret = url.rsplit("/", 1)[1]
    other = TestClient(owner.app, base_url="https://oink.test")
    assert other.get(f"/api/auth/invite/{secret}").json()["username"] == "luca"
    assert (
        other.post("/api/auth/invite", json={"token": secret, "password": "short"}).status_code
        == 422
    )
    assert (
        other.post(
            "/api/auth/invite", json={"token": secret, "password": "luca-password-1"}
        ).status_code
        == 200
    )
    again = TestClient(owner.app, base_url="https://oink.test")
    assert (
        again.post(
            "/api/auth/invite", json={"token": secret, "password": "luca-password-2"}
        ).status_code
        == 404
    )


def test_deleting_a_user_deletes_their_data(owner: TestClient, second: TestClient) -> None:
    second.post("/api/accounts", json={"name": "Hers"})
    marta = next(u for u in owner.get("/api/users").json() if u["username"] == "marta")
    assert owner.delete(f"/api/users/{marta['id']}").status_code == 200
    assert second.get("/api/accounts").status_code == 401
    me = next(u for u in owner.get("/api/users").json() if u["username"] == "admin")
    assert owner.delete(f"/api/users/{me['id']}").status_code == 422


def test_tokens_have_scopes_and_act_for_their_user(owner: TestClient, second: TestClient) -> None:
    owner.post("/api/accounts", json={"name": "Admin's"})
    second.post("/api/accounts", json={"name": "Marta's"})
    read = token(second, "accounts:read")
    names = [a["name"] for a in owner.get("/api/accounts", headers=read).json()]
    assert names == ["Marta's"]
    assert owner.post("/api/accounts", json={"name": "x"}, headers=read).status_code == 403
    assert (
        owner.post(
            "/api/tokens", json={"name": "x", "scopes": ["accounts:read"]}, headers=read
        ).status_code
        == 403
    )
    assert owner.post("/api/tokens", json={"name": "x", "scopes": ["root"]}).status_code == 422


def test_any_currency_and_crypto(owner: TestClient) -> None:
    btc = owner.post(
        "/api/accounts", json={"name": "Cold", "currency": "btc", "balance": "0.5"}
    ).json()
    assert btc["currency"] == "BTC" and btc["kind"] == "crypto" and btc["balance"] == "0.50000000"
    yen = owner.post(
        "/api/accounts", json={"name": "Tokyo", "currency": "JPY", "balance": "1200"}
    ).json()
    assert yen["balance"] == "1200"
    tx = owner.post("/api/transactions", json={"account_id": btc["id"], "day": "2026-11-01",
                                               "amount": "-0.00012345", "description": "fee"}).json()  # fmt: skip
    assert tx["amount"] == "-0.00012345"
    assert owner.get("/api/accounts").json()[0]["balance"] == "0.49987655"


def test_net_worth_is_converted_to_the_base_currency(owner: TestClient) -> None:
    with owner.app.state.db.sessions() as s:  # type: ignore[attr-defined]
        s.add_all([Rate(code="USD", eur=Decimal("0.9")), Rate(code="BTC", eur=Decimal("60000"))])
        s.commit()
    owner.post("/api/accounts", json={"name": "EUR", "balance": "100"})
    owner.post("/api/accounts", json={"name": "USD", "currency": "USD", "balance": "100"})
    owner.post("/api/accounts", json={"name": "BTC", "currency": "BTC", "balance": "0.001"})
    owner.post("/api/accounts", json={"name": "SOL", "currency": "SOL", "balance": "2"})
    worth = owner.get("/api/reports/overview").json()["net_worth"]
    assert worth["currency"] == "EUR"
    assert worth["total"] == 250.0  # 100 + 90 + 60
    assert worth["missing_rates"] == ["SOL"]
    owner.patch("/api/me", json={"base_currency": "USD"})
    assert owner.get("/api/reports/overview").json()["net_worth"]["total"] == round(250 / 0.9, 2)


def test_a_statement_finds_its_account_by_iban(owner: TestClient) -> None:
    owner.post("/api/accounts", json={"name": "Other"})
    acc = owner.post(
        "/api/accounts", json={"name": "Main", "iban": "IT60 X054 2811 1010 0000 0123 456"}
    ).json()
    reply = owner.post("/api/imports", files={"file": ("nov.csv", CSV, "text/csv")})
    assert reply.status_code == 200, reply.text
    out = reply.json()
    assert out["account_id"] == acc["id"] and out["added"] == 2 and out["month"] == "2026-11"
    again = owner.post("/api/imports", files={"file": ("nov.csv", CSV, "text/csv")}).json()
    assert again["added"] == 0 and again["duplicates"] == 2


def test_a_statement_without_a_known_iban_asks_for_the_account(owner: TestClient) -> None:
    acc = owner.post("/api/accounts", json={"name": "Main"}).json()
    reply = owner.post("/api/imports", files={"file": ("nov.csv", CSV, "text/csv")})
    assert reply.status_code == 422 and reply.json()["detail"] == "account_not_recognised"
    ok = owner.post("/api/imports", data={"account_id": str(acc["id"])},
                    files={"file": ("nov.csv", CSV, "text/csv")})  # fmt: skip
    assert ok.json()["added"] == 2
    # the IBAN was learnt: next time it goes alone
    assert owner.get("/api/accounts").json()[0]["iban"] == "IT60X0542811101000000123456"


class FakeExchange(Provider):
    def __init__(self) -> None:
        super().__init__(id="fake", name="Fake", kind="exchange", fields=(Field("api_key"),))

    async def accounts(self, creds: dict[str, str]) -> list[RemoteAccount]:
        if creds["api_key"] != "good":
            from oink.providers.base import ProviderError

            raise ProviderError("credentials_rejected")
        return [RemoteAccount("BTC", "BTC", "BTC", "crypto", balance="0.25")]


def test_linking_with_an_api_key(owner: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setitem(PROVIDERS, "fake", FakeExchange())
    bad = owner.post(
        "/api/connections/key", json={"provider": "fake", "credentials": {"api_key": "x"}}
    )
    assert bad.status_code == 422 and bad.json()["detail"] == "credentials_rejected"
    ok = owner.post(
        "/api/connections/key", json={"provider": "fake", "credentials": {"api_key": "good"}}
    )
    assert ok.status_code == 200 and ok.json()["status"] == "active"
    acc = owner.get("/api/accounts").json()[0]
    assert acc["currency"] == "BTC" and acc["balance"] == "0.25000000" and acc["source"] == "linked"
    with owner.app.state.db.sessions() as s:  # type: ignore[attr-defined]
        from oink.core.db import Connection

        assert "good" not in s.get(Connection, ok.json()["id"]).secret  # encrypted at rest
    read = token(owner, "connections:read")
    assert owner.post("/api/connections/key", headers=read,
                      json={"provider": "fake", "credentials": {"api_key": "good"}}).status_code == 403  # fmt: skip


def test_system_and_updates_are_admin_only(owner: TestClient, second: TestClient) -> None:
    info = owner.get("/api/system").json()
    assert "updates" in info and info["updates"]["can_install"] is False
    assert "updates" not in second.get("/api/system").json()
    assert second.post("/api/system/update").status_code == 403
    assert owner.post("/api/system/update").status_code == 409  # nothing newer (check off)


def test_an_oink_0_1_database_is_set_aside(tmp_path: Path) -> None:
    old = sqlite3.connect(tmp_path / "oink.db")
    old.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    from oink.core.security import hash_password

    old.execute(
        "INSERT INTO settings VALUES ('owner_password', ?)", (hash_password("vecchia-password-01"),)
    )
    old.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT)")
    old.commit()
    old.close()
    settings = Settings(data_dir=tmp_path, admin_username="Giuseppe", update_check=False,
                        enable_banking_key_file=tmp_path / "x")  # fmt: skip
    with TestClient(create_app(settings, background=False), base_url="https://t") as c:
        login = {"username": "giuseppe", "password": "vecchia-password-01"}
        assert c.post("/api/auth/login", json=login).status_code == 200
        assert c.get("/api/me").json()["is_admin"]
    assert (tmp_path / "oink-0.1.db.bak").is_file()


def test_a_revoked_token_disappears_from_the_list(owner: TestClient) -> None:
    keep = owner.post("/api/tokens", json={"name": "Jarvis", "scopes": ["accounts:read"]}).json()
    gone = owner.post("/api/tokens", json={"name": "old", "scopes": ["accounts:read"]}).json()
    assert owner.delete(f"/api/tokens/{gone['id']}").status_code == 200
    names = [t["name"] for t in owner.get("/api/tokens").json()]
    assert names == ["Jarvis"] and keep["token"].startswith("oink_")
    assert (
        owner.get("/api/accounts", headers={"Authorization": f"Bearer {gone['token']}"}).status_code
        == 401
    )


def test_movements_filter_sort_and_carry_the_running_balance(owner: TestClient) -> None:
    acc = owner.post("/api/accounts", json={"name": "Conto", "balance": "100"}).json()
    for day, amount, cat in (
        ("2026-11-01", "-10", "groceries"),
        ("2026-11-02", "-50", "travel"),
        ("2026-11-03", "200", "salary"),
    ):
        owner.post(
            "/api/transactions",
            json={
                "account_id": acc["id"],
                "day": day,
                "amount": amount,
                "description": cat,
                "category": cat,
            },
        )
    rows = owner.get("/api/transactions").json()
    # newest first, and the balance right after each one walks back from today's balance
    assert [(r["day"], r["balance_after"]) for r in rows] == [
        ("2026-11-03", "240.00"),
        ("2026-11-02", "40.00"),
        ("2026-11-01", "90.00"),
    ]
    assert rows[0]["total_after"] == 240.0
    biggest = owner.get(
        "/api/transactions", params={"sort": "amount_desc", "direction": "out"}
    ).json()
    assert [r["amount"] for r in biggest] == ["-50.00", "-10.00"]
    picked = owner.get(
        "/api/transactions", params={"categories": "travel,salary", "date_from": "2026-11-02"}
    ).json()
    assert {r["category"] for r in picked} == {"travel", "salary"}


def test_a_fixed_cost_marked_twice_is_kept_once_and_can_be_split(owner: TestClient) -> None:
    body = {
        "name": "Ovhcloud",
        "amount": "18.92",
        "currency": "EUR",
        "frequency": "monthly",
        "match": "ovhcloud",
    }
    first = owner.post("/api/fixed-costs", json=body).json()
    again = owner.post("/api/fixed-costs", json=body).json()
    assert again == {"id": first["id"], "duplicate": True}
    parts = [
        {"name": "VPS", "amount": "7.92", "frequency": "monthly"},
        {"name": "Dominio", "amount": "132", "frequency": "yearly"},
    ]
    assert (
        len(
            owner.post(f"/api/fixed-costs/{first['id']}/split", json={"parts": parts}).json()["ids"]
        )
        == 2
    )
    costs = owner.get("/api/fixed-costs").json()
    assert sorted((i["name"], i["frequency"]) for i in costs["items"]) == [
        ("Dominio", "yearly"),
        ("VPS", "monthly"),
    ]
    assert costs["monthly"] == 18.92  # 7.92 + 132 / 12


def test_the_review_proposes_fixed_costs_and_categories(owner: TestClient) -> None:
    acc = owner.post("/api/accounts", json={"name": "Revolut", "balance": "100"}).json()
    for month in ("09", "10", "11"):
        owner.post(
            "/api/transactions",
            json={
                "account_id": acc["id"],
                "day": f"2026-{month}-05",
                "amount": "-7.92",
                "description": "Ovhcloud",
            },
        )
    owner.post(
        "/api/transactions",
        json={
            "account_id": acc["id"],
            "day": "2026-08-10",
            "amount": "-30",
            "description": "Esselunga",
            "category": "groceries",
        },
    )
    lone = owner.post(
        "/api/transactions",
        json={
            "account_id": acc["id"],
            "day": "2026-11-12",
            "amount": "-12",
            "description": "Esselunga",
        },
    ).json()
    reply = owner.post("/api/transactions/review", json={"date_from": "2026-11-01"}).json()
    assert [(f["name"], f["amount"], f["frequency"]) for f in reply["fixed"]] == [
        ("Ovhcloud", "7.92", "monthly")
    ]
    groceries = next(c for c in reply["categories"] if c["tx_id"] == lone["id"])
    assert groceries["proposed"] == "groceries" and groceries["reason"] == "same_payee"
