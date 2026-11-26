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
from tests.conftest import PASSWORD, token

CSV = (
    b"Conto IT60 X054 2811 1010 0000 0123 456\n"
    b"Data contabile;Descrizione;Importo\n"
    b"03/11/2026;PAGAMENTO POS ESSELUNGA;-45,20\n"
    b"05/11/2026;STIPENDIO ACME;2.100,00\n"
)


def test_first_run_creates_the_service_admin(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path, update_check=False, enable_banking_key_file=tmp_path / "x"
    )
    with TestClient(create_app(settings, background=False), base_url="https://t") as c:
        status = c.get("/api/auth/status").json()
        assert status["configured"] and status["signup"]
        assert (
            c.post("/api/auth/login", json={"username": "admin", "password": "oink"}).status_code
            == 200
        )
        me = c.get("/api/me").json()
        assert me["is_admin"] and me["must_change_password"]
        assert c.get("/api/users").json()["detail"] == "password_change_required"
        reply = c.post("/api/auth/password", json={"current": "oink", "new": "una-nuova-password"})
        assert reply.status_code == 200, reply.text
        assert not c.get("/api/me").json()["must_change_password"]
        assert c.get("/api/users").status_code == 200
        assert c.get("/api/accounts").status_code == 403  # the admin has no finances
        assert (
            c.post("/api/tokens", json={"name": "x", "scopes": ["accounts:read"]}).status_code
            == 403
        )


def test_people_sign_up_unless_the_admin_closes_it(client: TestClient, admin: TestClient) -> None:
    other = TestClient(client.app, base_url="https://oink.test")
    body = {"username": "Luca", "password": "luca-password-1", "base_currency": "usd"}
    assert other.post("/api/auth/signup", json=body).status_code == 200
    me = other.get("/api/me").json()
    assert me["username"] == "luca" and not me["is_admin"] and me["base_currency"] == "USD"
    assert other.post("/api/auth/signup", json=body).json()["detail"] == "username_taken"
    assert admin.put("/api/system/signup", json={"open": False}).status_code == 200
    assert not client.get("/api/auth/status").json()["signup"]
    body["username"] = "anna"
    assert other.post("/api/auth/signup", json=body).status_code == 403


def test_the_admin_helps_someone_locked_out(owner: TestClient, admin: TestClient) -> None:
    other = TestClient(owner.app, base_url="https://oink.test")
    wrong = {"username": "giuseppe", "password": "sbagliata-sbagliata"}
    for _ in range(8):
        assert other.post("/api/auth/login", json=wrong).status_code == 401
    good = {"username": "giuseppe", "password": PASSWORD}
    assert other.post("/api/auth/login", json=good).json()["detail"] == "account_locked"
    me = next(u for u in admin.get("/api/users").json() if u["username"] == "giuseppe")
    assert me["locked"]
    assert admin.post(f"/api/users/{me['id']}/unlock").json()["locked"] is False
    assert other.post("/api/auth/login", json=good).status_code == 200
    # renamed, and a temporary password to change at once
    assert (
        admin.patch(f"/api/users/{me['id']}", json={"username": "Giu", "name": "Giu"}).status_code
        == 200
    )
    temp = admin.post(f"/api/users/{me['id']}/password", json={}).json()["password"]
    assert other.get("/api/accounts").status_code == 401  # sessions closed
    assert (
        other.post("/api/auth/login", json={"username": "giu", "password": temp}).status_code == 200
    )
    assert other.get("/api/accounts").json()["detail"] == "password_change_required"
    other.post("/api/auth/password", json={"current": temp, "new": "di-nuovo-mia-1"})
    assert other.get("/api/accounts").status_code == 200


def test_login_needs_the_right_password(client: TestClient) -> None:
    reply = client.post("/api/auth/login", json={"username": "admin", "password": "nope-nope-nope"})
    assert reply.status_code == 401 and reply.json()["detail"] == "wrong_credentials"
    assert client.get("/api/accounts").status_code == 401


def test_users_never_see_each_others_money(
    owner: TestClient, second: TestClient, admin: TestClient
) -> None:
    mine = owner.post("/api/accounts", json={"name": "Mine", "balance": "100"}).json()
    owner.post("/api/transactions", json={"account_id": mine["id"], "day": "2026-11-02",
                                          "amount": "-5", "description": "coffee"})  # fmt: skip
    assert second.get("/api/accounts").json() == []
    assert second.get("/api/transactions").json() == []
    assert second.patch(f"/api/accounts/{mine['id']}", json={"name": "x"}).status_code == 404
    assert second.post("/api/transactions", json={"account_id": mine["id"], "day": "2026-11-02",
                                                  "amount": "1", "description": "x"}).status_code == 404  # fmt: skip
    assert second.get("/api/users").status_code == 403  # not an admin
    assert owner.get("/api/users").status_code == 403
    assert len(admin.get("/api/users").json()) == 3  # admin, giuseppe, marta


def test_an_invite_works_once(owner: TestClient, admin: TestClient) -> None:
    url = admin.post("/api/users", json={"username": "luca"}).json()["invite_url"]
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


def test_deleting_a_user_deletes_their_data(second: TestClient, admin: TestClient) -> None:
    second.post("/api/accounts", json={"name": "Hers"})
    marta = next(u for u in admin.get("/api/users").json() if u["username"] == "marta")
    assert admin.delete(f"/api/users/{marta['id']}").status_code == 200
    assert second.get("/api/accounts").status_code == 401
    me = next(u for u in admin.get("/api/users").json() if u["username"] == "admin")
    assert admin.delete(f"/api/users/{me['id']}").status_code == 422


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


def test_system_and_updates_are_admin_only(second: TestClient, admin: TestClient) -> None:
    info = admin.get("/api/system").json()
    assert "updates" in info and info["updates"]["can_install"] is False
    assert "updates" not in second.get("/api/system").json()
    assert second.post("/api/system/update").status_code == 403
    assert admin.post("/api/system/update").status_code == 409  # nothing newer (check off)


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
    # the second Esselunga took its payee's category by itself: nothing left to propose
    assert lone["category"] == "groceries" and lone["category_by"] == "payee"
    assert not any(c["tx_id"] == lone["id"] for c in reply["categories"])


def test_fixed_costs_follow_their_payments_and_flag_a_missing_one(owner: TestClient) -> None:
    from datetime import date, timedelta

    acc = owner.post("/api/accounts", json={"name": "Revolut", "balance": "100"}).json()
    today = date.today()
    for back in (75, 45, 15):  # paid every month
        day = (today - timedelta(days=back)).isoformat()
        owner.post(
            "/api/transactions",
            json={
                "account_id": acc["id"],
                "day": day,
                "amount": "-9.99",
                "description": "Netflix.com",
            },
        )
    cost = owner.post(
        "/api/fixed-costs",
        json={
            "name": "Netflix",
            "amount": "9.99",
            "currency": "EUR",
            "frequency": "monthly",
            "match": "netflix",
        },
    ).json()
    history = owner.get(f"/api/fixed-costs/{cost['id']}/payments").json()
    assert history["count"] == 3 and history["next_due"] is not None
    assert owner.get("/api/fixed-costs/missing").json() == []  # next one is in the future
    gym = owner.post("/api/fixed-costs", json={"name": "Palestra", "amount": "39.90", "currency": "EUR", "frequency": "monthly",
                                                "match": "fitclub", "next_due": (today - timedelta(days=12)).isoformat()}).json()  # fmt: skip
    assert [m["id"] for m in owner.get("/api/fixed-costs/missing").json()] == [gym["id"]]
    owner.post(f"/api/fixed-costs/{gym['id']}/wait", json={"days": 7})
    assert owner.get("/api/fixed-costs/missing").json() == []


def test_a_missing_fixed_cost_can_be_paid_in_cash_or_skipped(owner: TestClient) -> None:
    from datetime import date, timedelta

    today = date.today()
    cash = owner.post(
        "/api/accounts", json={"name": "Contanti", "kind": "cash", "balance": "210"}
    ).json()
    due = (today - timedelta(days=12)).isoformat()
    gym = owner.post("/api/fixed-costs", json={"name": "Palestra", "amount": "50", "currency": "EUR",
                                                "frequency": "monthly", "match": "palestra", "next_due": due}).json()  # fmt: skip
    # paid in cash, and the cash balance already counts it
    reply = owner.post(f"/api/fixed-costs/{gym['id']}/paid",
                       json={"account_id": cash["id"], "day": due, "balance_updated": True})  # fmt: skip
    assert reply.status_code == 200, reply.text
    assert owner.get("/api/fixed-costs/missing").json() == []
    history = owner.get(f"/api/fixed-costs/{gym['id']}/payments").json()
    assert history["count"] == 1 and history["payments"][0]["elsewhere"]
    assert history["next_due"] > due
    accounts = {a["id"]: a for a in owner.get("/api/accounts").json()}
    assert Decimal(accounts[cash["id"]]["balance"]) == Decimal("210")
    # a month off: skipped, nothing spent
    tv = owner.post("/api/fixed-costs", json={"name": "Disney+", "amount": "8.99", "currency": "EUR",
                                               "frequency": "monthly", "match": "disney", "next_due": due}).json()  # fmt: skip
    assert owner.post(f"/api/fixed-costs/{tv['id']}/skip").status_code == 200
    assert owner.get("/api/fixed-costs/missing").json() == []
    history = owner.get(f"/api/fixed-costs/{tv['id']}/payments").json()
    assert history["skipped"] == [due] and history["count"] == 0


def test_a_terminal_code_gets_its_real_name_and_fuel_is_known(owner: TestClient) -> None:
    acc = owner.post("/api/accounts", json={"name": "Revolut", "balance": "100"}).json()
    first = owner.post(
        "/api/transactions",
        json={
            "account_id": acc["id"],
            "day": "2026-11-23",
            "amount": "-15.10",
            "description": "44941 Palo Del Colle - S",
        },
    ).json()
    renamed = owner.patch(
        f"/api/transactions/{first['id']}",
        json={"counterparty": "IP Italiana Petroli", "category": "fuel", "learn": True},
    ).json()
    assert renamed["counterparty"] == "IP Italiana Petroli" and renamed["category"] == "fuel"
    later = owner.post(
        "/api/transactions",
        json={
            "account_id": acc["id"],
            "day": "2026-11-30",
            "amount": "-40",
            "description": "44941 Palo Del Colle - S",
        },
    ).json()
    assert later["counterparty"] == "IP Italiana Petroli" and later["category"] == "fuel"
    eni = owner.post(
        "/api/transactions",
        json={
            "account_id": acc["id"],
            "day": "2026-11-28",
            "amount": "-30",
            "description": "Enilive Station Bari",
        },
    ).json()
    assert eni["category"] == "fuel"


def test_a_movement_splits_into_parts_and_back(owner: TestClient) -> None:
    acc = owner.post("/api/accounts", json={"name": "Revolut", "balance": "100"}).json()
    ovh = owner.post(
        "/api/transactions",
        json={
            "account_id": acc["id"],
            "day": "2026-11-02",
            "amount": "-18.92",
            "description": "Ovhcloud",
        },
    ).json()
    bad = owner.post(
        f"/api/transactions/{ovh['id']}/split",
        json={
            "parts": [
                {"amount": "7.92", "description": "VPS"},
                {"amount": "10", "description": "Dominio"},
            ]
        },
    )
    assert bad.status_code == 422  # parts must add up
    parts = owner.post(f"/api/transactions/{ovh['id']}/split", json={"parts": [
        {"amount": "7.92", "description": "VPS", "category": "subscriptions"},
        {"amount": "11", "description": "Dominio", "category": "subscriptions"}]}).json()  # fmt: skip
    assert [(p["counterparty"], p["amount"]) for p in parts] == [
        ("VPS", "-7.92"),
        ("Dominio", "-11.00"),
    ]
    shown = owner.get("/api/transactions").json()
    assert sorted(r["counterparty"] for r in shown) == ["Dominio", "VPS"]  # the original is hidden
    assert shown[0]["balance_after"] == "81.08"  # the balance counts the split once
    assert owner.delete(f"/api/transactions/{parts[0]['id']}/split").json() == {"ok": True}
    assert [r["id"] for r in owner.get("/api/transactions").json()] == [ovh["id"]]


def test_an_upgrade_keeps_every_row(tmp_path: Path) -> None:
    """0.7.0 lost everything: rebuilding `users` in a migration cascade-deleted accounts,
    movements and documents. A database one revision behind, full of data, must come
    through the upgrade whole, with a copy of it taken first."""
    from alembic import command

    from oink.core.db import Database
    from oink.core.migrate import config

    settings = Settings(data_dir=tmp_path, admin_password=PASSWORD, update_check=False,
                        enable_banking_key_file=tmp_path / "x")  # fmt: skip
    with TestClient(create_app(settings, background=False), base_url="https://t") as c:
        c.post("/api/auth/signup", json={"username": "giu", "password": PASSWORD})
        acc = c.post("/api/accounts", json={"name": "Revolut", "balance": "100"}).json()
        c.post("/api/transactions", json={"account_id": acc["id"], "day": "2026-11-02",
                                          "amount": "-5", "description": "coffee"})  # fmt: skip
    db = Database(settings.db_url)
    command.downgrade(config(db.engine), "0009")
    with TestClient(create_app(settings, background=False), base_url="https://t") as c:
        assert (
            c.post("/api/auth/login", json={"username": "giu", "password": PASSWORD}).status_code
            == 200
        )
        assert [a["name"] for a in c.get("/api/accounts").json()] == ["Revolut"]
        assert len(c.get("/api/transactions").json()) == 1
    assert (tmp_path / "oink-before-migration-0009.db").is_file()


def test_a_past_month_report_sees_nothing_after_it(owner: TestClient) -> None:
    from datetime import date as d

    from oink.services import reports as rep

    acc = owner.post("/api/accounts", json={"name": "Conto", "balance": "1000"}).json()
    for day, amount in (("2026-09-10", "-50"), ("2026-10-10", "-70"), ("2026-11-10", "-300")):
        owner.post("/api/transactions", json={"account_id": acc["id"], "day": day,
                                              "amount": amount, "description": "Spesa"})  # fmt: skip
    app = owner.app
    with app.state.db.sessions() as db:  # type: ignore[attr-defined]
        from oink.core.db import User

        user = db.query(User).filter_by(username="giuseppe").one()
        book = rep.Book.of(db, user)
        sept = rep.facts_for_ai(book, "2026-09", with_text=True, today=d(2026, 11, 26))
        octo = rep.facts_for_ai(book, "2026-10", with_text=True, today=d(2026, 11, 26))
    assert sept["months_of_history_before"] == 0 and sept["as_of"] == "2026-09-30"
    # today 1000 - 50 - 70 - 300 = 580: at the end of September October and November hadn't happened
    assert sept["net_worth"] == 580 + 70 + 300
    assert octo["months_of_history_before"] == 1 and octo["net_worth"] == 580 + 300
    months = owner.get("/api/reports/months").json()
    assert [m["month"] for m in months] == ["2026-11", "2026-10", "2026-09"]
    assert not any(m["report"] for m in months)


def test_overview_separates_account_balances_from_partial_investments(owner: TestClient) -> None:
    owner.post("/api/accounts", json={"name": "Bank", "balance": "100"})
    owner.post("/api/accounts", json={"name": "Unknown bank"})
    owner.post(
        "/api/holdings",
        json={"symbol": "CUSTOM:EUR", "quantity": "1", "currency": "EUR", "manual_price": "20"},
    )
    owner.post(
        "/api/holdings",
        json={"symbol": "CUSTOM:USD", "quantity": "1", "currency": "USD", "manual_price": "30"},
    )
    data = owner.get("/api/reports/overview").json()
    worth = data["net_worth"]
    assert worth["accounts_total"] == 100
    assert worth["investments"] == 20 and worth["total"] == 120
    assert data["balance"][-1]["value"] == worth["accounts_total"]
    assert worth["unvalued_accounts"] == ["Unknown bank"]
    assert worth["unvalued_investments"] == ["CUSTOM:USD"]
    assert worth["missing_rates"] == ["USD"]
    assert not worth["complete"]
