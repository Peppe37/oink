"""The documents engine: numbers read and reconciled by rules, review queue, hybrid search."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from oink.core.config import Settings
from oink.services import documents as docs
from oink.services.stores import Cache, Engine, Graph, Stores, Vectors
from oink.services.verify import check_invoice, check_payslip, check_statement, rows_from_tables

TABLE = {"header": ["Data", "Descrizione", "Importo"], "rows": [
    ["03/11/2026", "Esselunga", "-54,30"], ["05/11/2026", "Stipendio ACME", "1.812,40"],
    ["07/11/2026", "Giroconto verso Trade Republic", "-300,00"]], "page": 1}  # fmt: skip


def statement_text(closing: str) -> str:
    return f"Revolut Bank UAB\nIBAN IT71X0306901600000000012345\nSaldo iniziale 1.000,00\nSaldo finale {closing}\n"


def test_rules_reconcile_statements_payslips_and_invoices() -> None:
    rows = rows_from_tables([TABLE])
    assert [r.amount for r in rows] == [Decimal("-54.30"), Decimal("1812.40"), Decimal("-300.00")]
    good = check_statement(rows, statement_text("2.458,10"), structured=False)
    assert (
        good.status == "ok"
        and good.checks[0].ok is True
        and good.facts["closing_balance"] == "2458.10"
    )
    bad = check_statement(rows, statement_text("2.400,00"), structured=False)
    assert bad.status == "review" and bad.checks[0].detail["difference"] == "-58.10"
    assert check_statement(rows, "nessun saldo", structured=True).status == "ok"  # a bank export
    assert check_statement(rows, "nessun saldo", structured=False).status == "review"  # a PDF
    slip = check_payslip(
        "Totale competenze 2.450,00\nTotale trattenute 637,60\nNetto in busta 1.812,40"
    )
    assert slip.status == "ok" and slip.facts == {
        "gross_pay": "2450.00",
        "deductions": "637.60",
        "net_pay": "1812.40",
    }
    assert (
        check_payslip(
            "Totale competenze 2.450,00\nTotale trattenute 480,00\nNetto in busta 1.812,40"
        ).status
        == "review"
    )
    assert check_invoice("Imponibile 100,00\nIVA 22% 22,00\nTotale documento 122,00").status == "ok"
    assert (
        check_invoice("Imponibile 100,00\nIVA 22% 22,00\nTotale documento 132,00").status
        == "review"
    )


class FakeEngineAndQdrant:
    def __init__(self) -> None:
        self.closing = "2.458,10"
        self.points: list[dict[str, Any]] = []
        self.extracts = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/extract":
            self.extracts += 1
            return httpx.Response(
                200,
                json={
                    "markdown": statement_text(self.closing),
                    "text": "",
                    "tables": [TABLE],
                    "pages": 1,
                },
            )
        if path == "/embed":
            n = len(json.loads(request.content)["texts"])
            return httpx.Response(
                200,
                json={
                    "dense": [[0.1] * 1024] * n,
                    "sparse": [{"indices": [7], "values": [0.5]}] * n,
                },
            )
        if path.endswith("/collections/oink_documents") and request.method == "GET":
            return httpx.Response(200, json={"result": {"status": "green"}})
        if path.endswith("/points") and request.method == "PUT":
            self.points += json.loads(request.content)["points"]
            return httpx.Response(200, json={"result": {}})
        if path.endswith("/points/delete"):
            return httpx.Response(200, json={"result": {}})
        if path.endswith("/points/query"):
            body = json.loads(request.content)
            user = body["filter"]["must"][0]["match"]["value"]
            hits = [
                {"payload": p["payload"], "score": 1.0}
                for p in self.points
                if p["payload"]["user_id"] == user
            ]
            return httpx.Response(200, json={"result": {"points": hits[: body["limit"]]}})
        return httpx.Response(404, json={})


@pytest.fixture
def engine_on(owner: TestClient) -> Iterator[FakeEngineAndQdrant]:
    fake = FakeEngineAndQdrant()
    transport = httpx.MockTransport(fake)
    settings = Settings(engine_url="http://engine", qdrant_url="http://qdrant", qdrant_key="a.b.c")
    stores = Stores(
        Engine(settings, transport),
        Vectors(settings, transport),
        Cache(Settings()),
        Graph(Settings()),
    )
    docs.configure(stores)
    yield fake
    docs.configure(owner.app.state.stores)  # type: ignore[attr-defined]


def _wait(c: TestClient, doc_id: int) -> dict[str, Any]:
    for _ in range(100):
        doc = c.get(f"/api/documents/{doc_id}").json()
        if doc["status"] != "processing":
            return doc  # type: ignore[no-any-return]
        time.sleep(0.05)
    raise AssertionError("still processing")


def test_a_statement_that_adds_up_is_imported_one_that_doesnt_waits(
    owner: TestClient, engine_on: FakeEngineAndQdrant
) -> None:
    acc = owner.post(
        "/api/accounts",
        json={"name": "Revolut", "institution": "Revolut", "iban": "IT71X0306901600000000012345"},
    ).json()
    first = owner.post("/api/documents", files={"file": ("revolut-nov.pdf", b"%PDF-1.4 a")}).json()
    assert first["status"] == "processing"  # the reply comes before Docling is done
    done = _wait(owner, first["id"])
    assert done["status"] == "ready" and done["imported"] == 3 and done["account_id"] == acc["id"]
    assert done["review"]["checks"][0]["ok"] is True and done["review"]["engine"] is True
    assert {p["payload"]["document_id"] for p in engine_on.points} == {first["id"]}

    engine_on.closing = "2.400,00"  # this one doesn't add up: nothing is imported by itself
    second = _wait(
        owner,
        owner.post("/api/documents", files={"file": ("revolut-dic.pdf", b"%PDF-1.4 b")}).json()[
            "id"
        ],
    )
    assert second["status"] == "review" and second["imported"] is None
    assert second["review"]["checks"][0]["detail"]["difference"] == "-58.10"
    queue = owner.get("/api/documents", params={"status": "review"}).json()
    assert [d["id"] for d in queue] == [second["id"]]
    # the person looked and decides to import it anyway
    confirmed = owner.post(
        f"/api/documents/{second['id']}/import", json={"account_id": acc["id"]}
    ).json()
    assert confirmed["status"] == "ready" and confirmed["review"]["confirmed"] is True
    assert owner.get("/api/documents", params={"status": "review"}).json() == []

    hits = owner.get("/api/documents/search", params={"q": "Esselunga"}).json()
    assert hits and hits[0]["document_id"] in (first["id"], second["id"])
