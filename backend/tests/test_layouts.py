"""Bank layouts read from the PDF text: invented statements in the banks' own shape."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from oink.services import layouts
from oink.services.importer import valid_iban


def iban(country: str, bban: str) -> str:
    for check in range(100):
        candidate = f"{country}{check:02d}{bban}"
        if valid_iban(candidate):
            return candidate
    raise AssertionError


RV_IBAN = iban("IT", "K0366901600000000001234")
TR_IBAN = iban("IT", "L0367401600000000005678")

REVOLUT = f"""Estratto conto personalizzato
 1 ott 2026 - 31 ott 2026
Generato il 1 nov 2026
MARIO ROSSI
Conti correnti Riepiloghi
Conto personale (EUR)
Numero di conto (IBAN) {RV_IBAN} Data di apertura 1 ott 2026
Nome dell'istituto finanziario Revolut Bank UAB (Italian Branch)
Saldo di apertura 100,00€
Saldo di chiusura 52,60€
Conti deposito Riepiloghi
Deposito senza vincoli (EUR)
Saldo di apertura 0,00€
Saldo di chiusura 30,00€
Conti correnti Estratti conto delle operazioni
Conto personale (EUR)
Riepilogo delle transazioni
Data Descrizione Categoria
Denaro in 
entrata
/uscita
Saldo Imposte ritenute Altre imposte Costi
2 ott 2026 Ferrovie Appulo Lucane Esercente -1,70€ 98,30€ 0,00€ 0,00€ 0,00€
2 ott 2026 Ferrovie Appulo Lucane Esercente -1,70€ 96,60€ 0,00€ 0,00€ 0,00€
3 ott 2026 Pagamento da parte di LUCA 
BIANCHI
Altri 12,00€ 108,60€ 0,00€ 0,00€ 0,00€
Estratto conto personalizzato
 1 ott 2026 - 31 ott 2026
Generato il 1 nov 2026
Data Descrizione Categoria
Denaro in 
entrata
/uscita
Saldo Imposte ritenute Altre imposte Costi
5 ott 2026 A EUR Conto deposito senza vincoli Altri -30,00€ 78,60€ 0,00€ 0,00€ 0,00€
9 ott 2026 IP Italiana Petroli Esercente -26,00€ 52,60€ 0,00€ 0,00€ 0,00€
Totale 52,60€ 0,00€ 0,00€ 0,00€
Estratto conto personalizzato
Auto (EUR)
Riepilogo delle transazioni
Data Descrizione Categoria
4 ott 2026 From Conto deposito senza vincoli Altri 100,00€ 100,00€ 0,00€ 0,00€ 0,00€
Totale 100,00€ 0,00€ 0,00€ 0,00€
"""

TRADE_REPUBLIC = f"""TRADE REPUBLIC BANK GMBH, BRANCH ITALY
DATA 01 nov 2026 - 25 nov 2026
IBAN {TR_IBAN}
BIC TRBKITMMXXX
ESTRATTO CONTO RIASSUNTIVO
PRODOTTO SALDO INIZIALE IN ENTRATA IN USCITA SALDO FINALE
Conto corrente 500,00\xa0€ 400,00\xa0€ 300,00\xa0€ 600,00\xa0€
TRANSAZIONI SUL CONTO
DATA TIPO DESCRIZIONE IN ENTRATA IN USCITA SALDO
02 nov 
2026 Bonifico Incoming transfer from Mario Rossi ({RV_IBAN}) 400,00\xa0€ 900,00\xa0€
16 nov 
2026 Commercio Savings plan execution US0378331005 APPLE INC., quantity: 0.3108 100,00\xa0€ 800,00\xa0€
16 nov 
2026 Commercio Savings plan execution IE00B5BMR087 iShares VII plc - iShares Core S&P 500 UCITS ETF USD (Acc), quantity: 0.136522 100,00\xa0€ 700,00\xa0€
19 nov 
2026 Bonifico Outgoing transfer for Mario Rossi ({RV_IBAN}) 100,00\xa0€ 600,00\xa0€
"""


def test_revolut_reads_the_main_account_and_checks_every_balance() -> None:
    found = layouts.read(REVOLUT)
    assert found is not None and found.bank == "Revolut"
    assert found.opening == Decimal("100.00") and found.closing == Decimal("52.60")
    assert [r.amount for r in found.rows] == [
        Decimal(x) for x in ("-1.70", "-1.70", "12.00", "-30.00", "-26.00")
    ]
    assert found.rows[2].description == "Pagamento da parte di LUCA BIANCHI"
    assert found.rows[2].counterparty == "Luca Bianchi"
    assert found.rows[3].category == "savings"  # to its own deposit: never spending
    assert found.ibans == [RV_IBAN]
    assert "Auto (EUR)" in found.skipped and "deposit" in found.skipped


def test_a_revolut_row_misread_is_not_trusted() -> None:
    assert layouts.read(REVOLUT.replace("-26,00€ 52,60€", "-25,00€ 52,60€")) is None


def test_trade_republic_signs_come_from_the_balance_and_plans_keep_their_isin() -> None:
    found = layouts.read(TRADE_REPUBLIC)
    assert found is not None and found.bank == "Trade Republic"
    assert [r.amount for r in found.rows] == [
        Decimal(x) for x in ("400.00", "-100.00", "-100.00", "-100.00")
    ]
    assert found.ibans == [TR_IBAN]  # not the other bank's IBAN printed in a transfer
    assert [t["isin"] for t in found.trades] == ["US0378331005", "IE00B5BMR087"]
    assert found.trades[0]["quantity"] == "0.3108" and found.rows[1].counterparty == "APPLE INC."


def test_a_known_statement_skips_docling_dedupes_and_proposes_plans(
    owner: TestClient, monkeypatch: Any
) -> None:
    from oink.services import documents as docs
    from oink.services import markets

    async def no_search(query: str) -> list[dict[str, str]]:
        return []  # no network in tests: the plans stay proposals with their ISIN

    monkeypatch.setattr(markets, "search", no_search)
    monkeypatch.setattr(docs, "pdf_text", lambda raw: TRADE_REPUBLIC if b"TR" in raw else REVOLUT)
    rv = owner.post("/api/accounts", json={"name": "Revolut", "institution": "Revolut",
                                           "iban": RV_IBAN, "balance": "52.60"}).json()  # fmt: skip
    tr = owner.post("/api/accounts", json={"name": "Trade Republic", "institution": "Trade Republic",
                                           "iban": TR_IBAN, "balance": "600"}).json()  # fmt: skip
    # one coffee-like movement already there (the bank link): kept once, the second is new
    owner.post("/api/transactions", json={"account_id": rv["id"], "day": "2026-10-02", "amount": "-1.70",
                                          "description": "Ferrovie Appulo Lucane"})  # fmt: skip
    reply = owner.post("/api/documents", files={"file": ("revolut.pdf", b"%PDF-1.4 RV")}).json()
    assert reply["status"] == "ready", reply
    assert reply["kind"] == "statement" and reply["issuer"] == "Revolut"
    assert reply["import"]["added"] == 4 and reply["import"]["duplicates"] == 1
    moved = [t for t in owner.get("/api/transactions", params={"account_id": rv["id"]}).json()
             if t["description"].startswith("A EUR Conto deposito")]  # fmt: skip
    assert moved and moved[0]["category"] == "savings"
    # the same statement again adds nothing
    again = owner.post(
        "/api/documents", files={"file": ("revolut-2.pdf", b"%PDF-1.4 RV again")}
    ).json()
    assert again["import"]["added"] == 0
    reply = owner.post("/api/documents", files={"file": ("tr.pdf", b"%PDF-1.4 TR")}).json()
    assert reply["status"] == "ready" and reply["account_id"] == tr["id"], reply
    proposals = owner.get("/api/plans/proposals").json()
    assert {p.get("isin") for p in proposals} >= {"US0378331005", "IE00B5BMR087"}, proposals
