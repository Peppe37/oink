"""Searching movements the way people type: any format, misspelled, by meaning."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from oink.core.db import Transaction
from oink.core.money import to_minor
from oink.services import search

TODAY = date(2026, 11, 26)


def tx(day: str, amount: str, text: str, category: str = "") -> Transaction:
    return Transaction(
        user_id=1, account_id=1, day=date.fromisoformat(day), amount=to_minor(Decimal(amount), "EUR"),
        currency="EUR", description=text, counterparty="", category=category, note="",
        fingerprint=f"{day}{amount}{text}",
    )  # fmt: skip


ROWS = [
    tx("2026-11-03", "-12.50", "Esselunga Palo del Colle", "groceries"),
    tx("2026-11-24", "-15.10", "IP Italiana Petroli", "fuel"),
    tx("2026-10-08", "-1234.56", "Mooney Pagopa Checkout"),
    tx("2026-10-15", "-50.00", "Amazon", "shopping"),
    tx("2025-11-03", "-20.00", "Pizzeria Da Mario", "restaurants"),
    tx("2026-09-01", "1574.00", "Pagamento da GD SERVICES", "salary"),
    tx("2026-11-25", "200.00", "Pagamento da GIUSEPPE LAPIETRA", "transfers"),
    tx("2026-11-26", "-9.99", "Spotify", "subscriptions"),
]


def found(query: str) -> set[str]:
    return {t.description.split()[0] for t in search.search(ROWS, query, TODAY)}


@pytest.mark.parametrize(
    "query",
    ["12,50", "12.50", "12.5", "€12,50", "€ 12,50", "12,50 euro", "12,50€", "12,50 eur"],
)
def test_an_amount_in_any_format(query: str) -> None:
    assert "Esselunga" in found(query)


@pytest.mark.parametrize("query", ["1234,56", "1.234,56", "1 234,56", "1,234.56", "1234.56 euro"])
def test_thousands_and_decimals_in_any_style(query: str) -> None:
    assert found(query) == {"Mooney"}


def test_a_whole_number_finds_its_cents_but_with_a_currency_it_is_exact() -> None:
    assert "Esselunga" in found("12") and "Esselunga" not in found("12 euro")
    assert found("50 euro") == {"Amazon"}


def test_amounts_in_words() -> None:
    assert found("cinquanta euro") == {"Amazon"}
    assert found("fifty euro") == {"Amazon"}
    assert "Esselunga" not in found("dodici euro")
    assert search._it_number("milleduecento") == 1200
    assert search._it_number("centoventidue") == 122
    assert search._it_number("duemila") == 2000
    assert search._it_number("trentotto") == 38


def test_ranges_and_comparators() -> None:
    assert found("tra 10 e 16") == {"Esselunga", "IP"}
    assert found("sopra 1000") == {"Mooney", "Pagamento"}
    assert found("< 10") == {"Spotify"}
    assert found("circa 50") == {"Amazon"}
    assert found("-50") == {"Amazon"}


@pytest.mark.parametrize(
    "query",
    ["2026-11-03", "03/11/2026", "3/11/2026", "03.11.26", "3 nov", "3 novembre 2026",
     "3 di novembre", "3 novmbre", "03/11"],
)  # fmt: skip
def test_a_date_in_any_format(query: str) -> None:
    assert "Esselunga" in found(query)
    assert "Pizzeria" not in found(query) or query in (
        "3 nov",
        "3 novembre",
        "03/11",
        "3 novmbre",
        "3 di novembre",
    )


def test_months_and_years_even_misspelled() -> None:
    assert found("novembre 2026") == {"Esselunga", "IP", "Pagamento", "Spotify"}
    assert found("novmbre 2025") == {"Pizzeria"}
    assert found("ottobre") == {"Mooney", "Amazon"}
    assert found("sett") == {"Pagamento"}
    assert found("2025") == {"Pizzeria"}
    assert found("november 2026") == found("novembre 2026")


def test_relative_dates() -> None:
    assert found("oggi") == {"Spotify"}
    assert found("ieri") == {"Pagamento"}
    assert found("mese scorso") == {"Mooney", "Amazon"}
    assert found("quest'anno") != found("anno scorso")
    assert found("ultimi 3 giorni") == {"IP", "Pagamento", "Spotify"}


def test_words_by_meaning_and_with_typos() -> None:
    assert found("benzina") == {"IP"}
    assert found("mangiare") == {"Pizzeria"}
    assert found("supermercato") == {"Esselunga"}
    assert found("stipendio") == {"Pagamento"}
    assert found("esselunga") == {"Esselunga"}
    assert found("eselunga") == {"Esselunga"}  # a typo
    assert found("amazn") == {"Amazon"}
    assert found("abbonamenti") == {"Spotify"}


def test_direction_words() -> None:
    assert found("entrate") == {"Pagamento"}
    assert found("spese novembre") == {"Esselunga", "IP", "Spotify", "Pizzeria"}  # any November


def test_pieces_combine() -> None:
    assert found("esselunga novembre 12,50") == {"Esselunga"}
    assert found("benzina ottobre") == set()
    assert found("spese sopra 12 novembre 2026") == {"Esselunga", "IP"}


def test_the_query_is_explained() -> None:
    chips = search.understand("esselunga 3 nov 12,50 euro", TODAY).chips()
    assert [c["kind"] for c in chips] == ["date", "amount", "category"]
    assert chips[0]["label"] == "3 novembre"


def test_the_movements_list_searches_like_a_person(owner) -> None:  # type: ignore[no-untyped-def]
    acc = owner.post("/api/accounts", json={"name": "Conto", "balance": "0"}).json()["id"]
    for day, amount, text in (("2026-11-03", "-12.50", "Esselunga"), ("2026-10-08", "-1234.56", "Mooney"),
                              ("2026-11-24", "-15.10", "IP Italiana Petroli")):  # fmt: skip
        owner.post(
            "/api/transactions",
            json={"account_id": acc, "day": day, "amount": amount, "description": text},
        )

    def names(q: str) -> list[str]:
        return [t["description"] for t in owner.get("/api/transactions", params={"q": q}).json()]

    assert names("1.234,56") == ["Mooney"]
    assert names("12,50 3 novmbre") == ["Esselunga"]
    assert names("ottobre") == ["Mooney"]
    assert names("eselunga") == ["Esselunga"]
    assert (
        owner.get("/api/transactions/understand", params={"q": "3 nov 12,50"}).json()["chips"][0][
            "kind"
        ]
        == "date"
    )
    assert len(names("")) == 3
