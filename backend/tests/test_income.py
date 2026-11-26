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


def _session(owner: TestClient):  # type: ignore[no-untyped-def]
    return owner.app.state.db.sessions()  # type: ignore[attr-defined]


def test_the_bank_link_and_the_statement_are_one_payment_even_days_apart(
    owner: TestClient,
) -> None:
    from datetime import date

    from oink.core.db import Account
    from oink.services.ledger import NewMovement, add_movements

    acc_id = owner.post("/api/accounts", json={"name": "Revolut", "balance": "0"}).json()["id"]
    with _session(owner) as db:
        account = db.get(Account, acc_id)
        linked = [NewMovement(date(2026, 9, 3), -6880, "Trenitalia"),
                  NewMovement(date(2026, 9, 6), -1030, "IP Italiana Petroli")]  # fmt: skip
        add_movements(db, account, linked, "linked")
        statement = [NewMovement(date(2026, 9, 1), -6880, "Trenitalia - Lefrecce"),  # 2 days off
                     NewMovement(date(2026, 9, 1), -6880, "Trenitalia - Lefrecce"),  # a second ticket
                     NewMovement(date(2026, 9, 2), -1030, "IP Italiana Petroli")]  # fmt: skip
        result = add_movements(db, account, statement, "import")
    assert len(result.added) == 1 and result.duplicates == 2  # only the second ticket is new


def test_old_doubles_are_merged_but_what_the_person_did_is_kept(owner: TestClient) -> None:
    from datetime import date

    from oink.core.db import Account, Transaction
    from oink.services.ledger import merge_duplicates

    acc_id = owner.post("/api/accounts", json={"name": "Revolut", "balance": "0"}).json()["id"]
    with _session(owner) as db:
        account = db.get(Account, acc_id)

        def add(day: int, amount: int, source: str, text: str, **kw: str) -> Transaction:
            tx = Transaction(user_id=account.user_id, account_id=acc_id, day=date(2026, 9, day),
                             amount=amount, description=text, source=source,
                             fingerprint=f"{source}-{day}-{amount}-{text}", **kw)  # fmt: skip
            db.add(tx)
            db.flush()
            return tx

        plain = add(3, -1000, "linked", "Bar"), add(1, -1000, "import", "Bar")
        edited_statement = add(5, -2000, "import", "Cinema", category="leisure", category_by="user")
        edited_link = add(8, -2000, "linked", "Cinema")
        far = (
            add(3, -500, "linked", "Pane"),
            add(20, -500, "import", "Pane"),
        )  # 17 days: two payments
        db.commit()
        preview = merge_duplicates(db, acc_id)
        assert len(preview) == 2 and db.get(Transaction, plain[1].id) is not None  # nothing yet
        merge_duplicates(db, acc_id, apply=True)
        ids = {t.id for t in db.query(Transaction).all()}
    assert plain[1].id not in ids and plain[0].id in ids  # the statement's copy went
    assert edited_statement.id in ids and edited_link.id not in ids  # the edited one stayed
    assert far[0].id in ids and far[1].id in ids


def test_the_dedupe_endpoint_previews_then_merges(owner: TestClient) -> None:
    acc = owner.post("/api/accounts", json={"name": "Revolut", "balance": "0"}).json()["id"]
    preview = owner.post("/api/transactions/dedupe").json()
    assert preview == {"merged": 0, "amount": 0, "applied": False}
    assert owner.post("/api/transactions/dedupe", params={"apply": "true"}).json()["applied"]
    assert acc


def test_two_payments_of_the_same_amount_days_apart_stay_two(owner: TestClient) -> None:
    from datetime import date

    from oink.core.db import Account
    from oink.services.ledger import NewMovement, add_movements

    acc_id = owner.post("/api/accounts", json={"name": "Revolut", "balance": "0"}).json()["id"]
    with _session(owner) as db:
        account = db.get(Account, acc_id)
        add_movements(db, account, [NewMovement(date(2026, 8, 30), -2100, "Sindros Dimostheni"),
                                    NewMovement(date(2026, 9, 14), -7500, "Dynamyk Fitness Club")],
                      "linked")  # fmt: skip
        result = add_movements(
            db, account,
            [NewMovement(date(2026, 8, 25), -2100, "Festeesaporimurgia"),  # 5 days, other payee
             NewMovement(date(2026, 9, 19), -7500, "Dynamyk Fitness Club")],  # 5 days, same payee
            "import",
        )  # fmt: skip
    assert [t.description for t in result.added] == ["Festeesaporimurgia"]
    assert result.duplicates == 1


def test_statement_exact_matches_are_reserved_before_near_matches(owner: TestClient) -> None:
    from datetime import date

    from oink.core.db import Account
    from oink.services.ledger import NewMovement, add_movements

    acc_id = owner.post("/api/accounts", json={"name": "Bank"}).json()["id"]
    with _session(owner) as db:
        account = db.get(Account, acc_id)
        add_movements(
            db,
            account,
            [NewMovement(date(2026, 8, day), 10000, "Savings deposit") for day in (30, 31)],
            "linked",
        )
        statement = [
            NewMovement(date(2026, 8, day), 10000, "Savings deposit") for day in (27, 30, 31)
        ]
        result = add_movements(db, account, statement, "import")
        assert [t.day for t in result.added] == [date(2026, 8, 27)]
        assert result.duplicates == 2
        again = add_movements(db, account, statement, "import")
        assert not again.added and again.duplicates == 3


def test_named_payees_are_reserved_before_amount_only_aliases(owner: TestClient) -> None:
    from datetime import date

    from oink.core.db import Account
    from oink.services.ledger import NewMovement, add_movements, merge_duplicates

    acc_id = owner.post("/api/accounts", json={"name": "Bank"}).json()["id"]
    with _session(owner) as db:
        account = db.get(Account, acc_id)
        add_movements(db, account, [NewMovement(date(2026, 8, 28), -1100, "Passoa")], "linked")
        result = add_movements(
            db,
            account,
            [
                NewMovement(date(2026, 8, 26), -1100, "E Cig Shop"),
                NewMovement(date(2026, 8, 30), -1100, "Passoa"),
            ],
            "import",
        )
        assert [t.description for t in result.added] == ["E Cig Shop"]
        assert result.duplicates == 1
        assert merge_duplicates(db, acc_id) == []


def test_reimport_restores_a_missing_identical_occurrence(owner: TestClient) -> None:
    from datetime import date

    from oink.core.db import Account
    from oink.services.ledger import NewMovement, add_movements

    acc_id = owner.post("/api/accounts", json={"name": "Bank"}).json()["id"]
    with _session(owner) as db:
        account = db.get(Account, acc_id)
        row = NewMovement(date(2026, 11, 10), -170, "Local railway")
        first = add_movements(db, account, [row, row], "import")
        db.delete(first.added[0])  # only occurrence 1 survives an old, incorrect repair
        db.commit()
        repaired = add_movements(db, account, [row, row], "import")
        assert len(repaired.added) == 1 and repaired.duplicates == 1
        assert not add_movements(db, account, [row, row], "import").added


def test_repeated_dedupe_never_goes_below_the_statement_payment_count(owner: TestClient) -> None:
    from datetime import date

    from oink.core.db import Account, Document
    from oink.services.ledger import NewMovement, add_movements, merge_duplicates

    acc_id = owner.post("/api/accounts", json={"name": "Bank"}).json()["id"]
    with _session(owner) as db:
        account = db.get(Account, acc_id)
        doc = Document(
            user_id=account.user_id,
            kind="statement",
            filename="statement.pdf",
            stored="test.pdf",
            sha256="railway",
            account_id=acc_id,
            review={
                "rows": [
                    ["2026-11-10", "-1.70", "Local railway", ""],
                    ["2026-11-10", "-1.70", "Local railway", ""],
                ]
            },
        )
        db.add(doc)
        db.commit()
        # An old importer left one duplicate, but two genuine payments must survive.
        add_movements(
            db, account, [NewMovement(date(2026, 11, 8), -170, "Local railway")], "linked"
        )
        for n in range(2):
            db.add(
                Transaction(
                    user_id=account.user_id,
                    account_id=acc_id,
                    day=date(2026, 11, 10),
                    amount=-170,
                    description="Local railway",
                    source="import",
                    fingerprint=f"railway-{n}",
                    document_id=doc.id,
                )
            )
        db.commit()
        assert len(merge_duplicates(db, acc_id, apply=True)) == 1
        assert merge_duplicates(db, acc_id, apply=True) == []
        assert db.query(Transaction).filter(Transaction.account_id == acc_id).count() == 2
