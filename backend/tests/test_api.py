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
    old.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT)")
    old.commit()
    old.close()
    settings = Settings(data_dir=tmp_path, admin_password="una-password-lunga", update_check=False,
                        enable_banking_key_file=tmp_path / "x")  # fmt: skip
    with TestClient(create_app(settings, background=False), base_url="https://t") as c:
        assert (
            c.post(
                "/api/auth/login", json={"username": "admin", "password": "una-password-lunga"}
            ).status_code
            == 200
        )
    assert (tmp_path / "oink-0.1.db.bak").is_file()
