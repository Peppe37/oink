"""The documents hub: filing, statements imported from it, search, chat, privacy, MCP, inbox."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from oink.core.config import Settings
from oink.core.db import Database, Document
from oink.core.security import create_user
from oink.services import documents as docs
from oink.services.ai import AI
from oink.services.statements import scan_inbox

from .conftest import token

PAYSLIP = (
    "ACME S.p.A. — Cedolino paga ottobre 2026\n"
    "Dipendente: Rossi Mario\nPeriodo 01/10/2026 - 31/10/2026\n"
    "Retribuzione lorda 2.450,00\nTrattenute IRPEF 480,00\nNetto in busta 1.812,40\n"
)
CONTRACT = (
    "Contratto di abbonamento palestra FitClub\nDecorrenza 01/09/2026, durata 12 mesi.\n"
    "Recesso: preavviso scritto di 30 giorni prima della scadenza. Canone mensile 39,90.\n"
)
STATEMENT = (
    "IBAN IT60X0542811101000000123456\nData;Descrizione;Importo\n"
    "03/11/2026;Bar Centrale;-2,50\n04/11/2026;Stipendio ACME;1812,40\n"
)


def _upload(c: TestClient, name: str, body: str | bytes, **form: Any) -> Any:
    raw = body.encode() if isinstance(body, str) else body
    return c.post(
        "/api/documents", files={"file": (name, raw)}, data={k: str(v) for k, v in form.items()}
    )


def test_documents_are_filed_searched_and_kept_private(
    owner: TestClient, second: TestClient
) -> None:
    slip = _upload(owner, "cedolino-ottobre.txt", PAYSLIP)
    assert slip.status_code == 200, slip.text
    doc = slip.json()
    assert doc["kind"] == "payslip" and doc["date"] == "2026-10-01" and doc["has_text"]
    assert _upload(owner, "palestra.txt", CONTRACT).json()["kind"] == "contract"

    again = _upload(owner, "copia.txt", PAYSLIP).json()  # the same bytes: filed once
    assert again["duplicate"] and again["id"] == doc["id"]

    # search finds Italian plurals and accents; the list filters by kind and by text
    hits = owner.get(
        "/api/documents/search", params={"q": "quanto è il netto dello stipendio?"}
    ).json()
    assert hits and hits[0]["document_id"] == doc["id"]
    assert (
        owner.get("/api/documents/search", params={"q": "preavviso recesso"}).json()[0]["kind"]
        == "contract"
    )
    assert [d["kind"] for d in owner.get("/api/documents", params={"kind": "payslip"}).json()] == [
        "payslip"
    ]
    assert len(owner.get("/api/documents", params={"q": "palestra"}).json()) == 1

    # without AI the chat still finds the passages, and says the AI is off
    reply = owner.post("/api/documents/ask", json={"question": "Netto in busta di ottobre?"}).json()
    assert (
        reply["ai"] is False
        and reply["answer"] is None
        and reply["sources"][0]["document_id"] == doc["id"]
    )

    # another user sees nothing of it
    assert second.get(f"/api/documents/{doc['id']}").status_code == 404
    assert second.get(f"/api/documents/{doc['id']}/file").status_code == 404
    assert second.get("/api/documents/search", params={"q": "netto busta"}).json() == []
    assert second.get("/api/documents").json() == []

    # the original file comes back as it was, and can be renamed, re-filed, deleted
    file = owner.get(f"/api/documents/{doc['id']}/file")
    assert file.status_code == 200 and file.content == PAYSLIP.encode()
    assert (
        file.headers["content-type"].startswith("text/plain")
        and "sandbox" in file.headers["content-security-policy"]
    )
    patched = owner.patch(
        f"/api/documents/{doc['id']}", json={"title": "Busta paga ottobre", "kind": "tax"}
    )
    assert patched.json()["title"] == "Busta paga ottobre" and patched.json()["kind"] == "tax"
    assert owner.delete(f"/api/documents/{doc['id']}").status_code == 204
    assert owner.get("/api/documents/search", params={"q": "netto busta"}).json() == []


def test_a_statement_uploaded_as_document_fills_its_account(owner: TestClient) -> None:
    acc = owner.post("/api/accounts", json={"name": "Revolut", "balance": "0"}).json()
    owner.patch(f"/api/accounts/{acc['id']}", json={"iban": "IT60X0542811101000000123456"})
    reply = _upload(owner, "revolut-novembre.csv", STATEMENT).json()
    assert reply["kind"] == "statement" and reply["account_id"] == acc["id"]
    assert reply["import"]["added"] == 2 and reply["imported"] == 2

    # a statement whose account can't be told is kept, with the reason, and imported later
    orphan = _upload(
        owner, "altro.csv", "Data;Descrizione;Importo\n05/11/2026;Edicola;-1,20\n"
    ).json()
    assert orphan["import"] is None and orphan["import_note"] == "account_not_recognised"
    later = owner.post(f"/api/documents/{orphan['id']}/import", json={"account_id": acc["id"]})
    assert later.status_code == 200 and later.json()["import"]["added"] == 1


def test_documents_need_their_own_scopes_and_work_over_mcp(owner: TestClient) -> None:
    readonly = token(owner, "documents:read")
    writer = token(owner, "documents:write", "documents:read")
    other = token(owner, "accounts:read")
    assert owner.get("/api/documents", headers=other).status_code == 403
    assert _upload(owner, "x.txt", PAYSLIP).status_code == 200  # the browser has every scope

    def call(headers: dict[str, str], name: str, args: dict[str, Any]) -> Any:
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }
        return owner.post("/mcp", json=body, headers=headers).json()

    upload = {
        "filename": "palestra.pdf.txt",
        "content_base64": base64.b64encode(CONTRACT.encode()).decode(),
    }
    assert "error" in call(readonly, "upload_document", upload)
    filed = call(writer, "upload_document", upload)
    assert (
        filed["result"]["isError"] is False
        and '"kind": "contract"' in filed["result"]["content"][0]["text"]
    )
    found = call(readonly, "search_documents", {"query": "recesso palestra"})
    assert "FitClub" in found["result"]["content"][0]["text"]
    listing = call(readonly, "list_documents", {"kind": "payslip"})
    assert '"kind": "payslip"' in listing["result"]["content"][0]["text"]
    names = {t["name"] for t in owner.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                                           headers=readonly).json()["result"]["tools"]}  # fmt: skip
    assert {"list_documents", "search_documents", "ask_documents", "get_document"} <= names
    assert "upload_document" not in names


def test_the_chat_sends_only_the_passages_found(owner: TestClient, monkeypatch: Any) -> None:
    _upload(owner, "cedolino.txt", PAYSLIP)
    _upload(owner, "palestra.txt", CONTRACT)
    ai: AI = owner.app.state.ai  # type: ignore[attr-defined]
    seen: dict[str, Any] = {}

    async def answer(
        question: str, passages: list[dict[str, Any]], language: str, history: Any = None
    ) -> str:
        seen["passages"], seen["history"] = passages, history
        return "Il netto di ottobre è 1.812,40 € [1]."

    monkeypatch.setattr(type(ai), "can_read_text", property(lambda self: True))
    monkeypatch.setattr(ai, "answer_from_documents", answer)
    monkeypatch.setattr(ai, "describe_document", None)  # not called by ask
    history = [{"role": "user", "content": "Ciao"}, {"role": "assistant", "content": "Ciao!"}]
    reply = owner.post(
        "/api/documents/ask", json={"question": "netto in busta ottobre", "history": history}
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["answer"].startswith("Il netto")
    assert all("FitClub" not in p["text"] for p in seen["passages"][:1])
    assert seen["passages"][0]["title"] and seen["history"] == history


async def test_the_inbox_files_any_financial_paper(tmp_path: Path) -> None:
    db = Database(f"sqlite:///{tmp_path / 'oink.db'}")
    db.create()
    with db.sessions() as s:
        create_user(s, "anna", "anna-password-1")
        s.commit()
    inbox = tmp_path / "inbox"
    (inbox / "anna").mkdir(parents=True)
    (inbox / "anna" / "cedolino.txt").write_text(PAYSLIP)
    (inbox / "anna" / "orphan.csv").write_text(
        "Data;Descrizione;Importo\n05/11/2026;Edicola;-1,20\n"
    )
    ai = AI(Settings(data_dir=tmp_path, ai_model=""))
    with db.sessions() as s:
        assert await scan_inbox(s, inbox, ai, tmp_path / "documents") == 1
        kinds = sorted(d.kind for d in s.query(Document))
    assert kinds == ["payslip", "statement"]  # both filed; the statement waits for its account
    assert (inbox / "anna" / "done" / "2026-10" / "cedolino.txt").is_file()
    assert (inbox / "anna" / "unmatched" / "orphan.csv").is_file()


def test_query_words_match_their_stems() -> None:
    assert docs.fts_query("Quanto è lo stipendio netto?") == '"stipend"* OR "netto"*'
    assert docs.fts_query("e il") == ""
    assert docs.chunks("a" * 3000)[0] and len(docs.chunks("x. " * 1000)) > 2


def test_a_statement_of_an_unknown_account_proposes_it_filled_in(owner: TestClient) -> None:
    body = (
        "Intesa Sanpaolo - Estratto conto\nIBAN IT71X0306901600000000012345\n"
        "Data;Descrizione;Importo\n02/11/2026;Versamento contanti;200,00\n03/11/2026;Giroconto a Revolut;-200,00\n"
    )
    doc = _upload(owner, "intesa-novembre.csv", body).json()
    proposal = doc["facts"]["proposed_account"]
    assert doc["import_note"] == "account_not_recognised"
    assert (
        proposal["institution"] == "Intesa Sanpaolo"
        and proposal["iban"] == "IT71X0306901600000000012345"
    )
    assert proposal["movements"] == 2 and proposal["name"] == "Intesa Sanpaolo"
    acc = owner.post(
        "/api/accounts",
        json={k: proposal[k] for k in ("name", "institution", "iban", "kind", "currency")},
    ).json()
    done = owner.post(f"/api/documents/{doc['id']}/import", json={"account_id": acc["id"]}).json()
    assert done["import"]["added"] == 2 and "proposed_account" not in done["facts"]


def test_a_hand_made_account_fills_from_its_statement(owner: TestClient) -> None:
    acc = owner.post(
        "/api/accounts",
        json={"name": "Intesa", "institution": "Intesa Sanpaolo", "kind": "checking"},
    ).json()
    body = "IBAN IT71X0306901600000000012345\nData;Descrizione;Importo\n02/11/2026;Versamento contanti;200,00\n"
    doc = owner.post(
        "/api/documents",
        files={"file": ("intesa.csv", body.encode())},
        data={"kind": "statement", "account_id": str(acc["id"])},
    ).json()
    assert doc["imported"] == 1 and doc["account_id"] == acc["id"]
    learnt = next(a for a in owner.get("/api/accounts").json() if a["id"] == acc["id"])
    assert (
        learnt["iban"] == "IT71X0306901600000000012345"
    )  # next month the statement finds it alone
