"""Income must be income: the person's own money coming in, trades and a payee wrongly taken
for a petrol station are not."""

from __future__ import annotations

from fastapi.testclient import TestClient

from oink.core.db import Transaction
from oink.services import ledger


def _add(c: TestClient, account: int, day: str, amount: str, text: str, **extra: str) -> dict:
    reply = c.post("/api/transactions", json={"account_id": account, "day": day,
                                              "amount": amount, "description": text, **extra})  # fmt: skip
    assert reply.status_code == 200, reply.text
    return reply.json()


def test_own_money_coming_in_is_not_income(owner: TestClient) -> None:
    owner.patch("/api/me", json={"name": "Mario Rossi"})
    bank = owner.post("/api/accounts", json={"name": "Intesa", "balance": "0"}).json()["id"]
    app = owner.post("/api/accounts", json={"name": "Revolut", "balance": "0"}).json()["id"]
    salary = _add(owner, app, "2026-09-01", "1574", "Pagamento da ACME SRL", category="salary")
    own_name = _add(owner, app, "2026-09-05", "47.99", "Pagamento da MARIO ROSSI")
    reversed_name = _add(owner, app, "2026-09-06", "10", "Pagamento da ROSSI MARIO")
    topup = _add(owner, app, "2026-09-07", "110", "Ricarica di Google Pay con *3323")
    cash = _add(owner, bank, "2026-09-08", "260", "VERS.SPORT.AUT. Effettuato presso ABI 03069")
    friend = _add(owner, app, "2026-09-09", "12", "Pagamento da LUCA BIANCHI")
    owner.post("/api/transfers/match")
    by_id = {t["id"]: t for t in owner.get("/api/transactions").json()}
    for own in (own_name, reversed_name, topup, cash):
        assert by_id[own["id"]]["category"] == "transfers", by_id[own["id"]]
    assert by_id[salary["id"]]["category"] == "salary"
    assert by_id[friend["id"]]["category"] != "transfers"  # a friend paying you is income
    month = owner.get("/api/reports/summary", params={"month": "2026-09"}).json()["current"]
    assert month["income"] == 1574 + 12


def test_a_word_inside_another_is_not_a_petrol_station() -> None:
    assert ledger.rule_category([], "VERS.SPORT.AUT. Effettuato presso ABI") == ""
    assert ledger.rule_category([], "Pagamento ESSO Station Roma") == "fuel"
    assert ledger.rule_category([], "IP Italiana Petroli") == "fuel"


def test_crypto_and_investment_accounts_are_not_income(owner: TestClient) -> None:
    wallet = owner.post(
        "/api/accounts", json={"name": "Young", "kind": "crypto", "balance": "0"}
    ).json()["id"]
    bank = owner.post("/api/accounts", json={"name": "Conto", "balance": "0"}).json()["id"]
    _add(owner, wallet, "2026-10-02", "52.30", "DEPOSIT")
    _add(owner, bank, "2026-10-03", "1564", "Pagamento da ACME SRL", category="salary")
    month = owner.get("/api/reports/summary", params={"month": "2026-10"}).json()["current"]
    assert month["income"] == 1564


def test_a_wrong_rule_category_is_let_go_and_payees_keep_their_category(
    owner: TestClient,
) -> None:
    acc = owner.post("/api/accounts", json={"name": "Intesa", "balance": "0"}).json()["id"]
    wrong = _add(owner, acc, "2026-09-08", "260", "Versamento presso sportello")
    with owner.app.state.db.sessions() as db:  # type: ignore[attr-defined]
        tx = db.get(Transaction, wrong["id"])
        tx.category, tx.category_by = "fuel", "rule"  # an old keyword caught «presso»
        db.commit()
    first = _add(owner, acc, "2026-09-10", "-30", "Esselunga", category="groceries")
    later = _add(owner, acc, "2026-10-10", "-12", "ESSELUNGA SPA")
    assert later["category"] == "groceries"
    result = owner.post("/api/transfers/match").json()
    assert result["rules"] == 1
    again = {t["id"]: t for t in owner.get("/api/transactions").json()}
    assert again[wrong["id"]]["category"] != "fuel" and first["category"] == "groceries"
