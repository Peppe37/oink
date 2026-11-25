"""The financial documents hub: statements, payslips, contracts, invoices, tax papers…

A document is stored as it came (`data/documents/<user id>/`), its text is extracted
(PDF text, CSV/OFX/plain text; photos and scans through the AI when the model sees images),
filed (kind, title, date, issuer, key facts: by the AI when there is one, by keywords
otherwise) and split into chunks in an SQLite FTS5 index, so any question can find the
right passages without sending anything anywhere. The chat over the documents sends only
the few passages it found to the AI.

A statement is also imported into its account (the same path as `services.statements`):
uploading the monthly PDF is enough to keep an account up to date without a bank link."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import logging
import mimetypes
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from oink.core.db import Account, Document, User
from oink.services.ai import AI, AIError
from oink.services.importer import ImportError_, parse_date, parse_statement, pdf_text
from oink.services.statements import Outcome, apply, pick_account

log = logging.getLogger("oink.documents")

KINDS = ("statement", "payslip", "contract", "invoice", "receipt", "tax", "insurance",
         "investment", "other")  # fmt: skip
TEXT_TYPES = (".pdf", ".csv", ".tsv", ".txt", ".ofx", ".qfx", ".md", ".json", ".xml")
IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
ACCEPTED = TEXT_TYPES + IMAGE_TYPES
STATEMENT_TYPES = (".pdf", ".csv", ".tsv", ".txt", ".ofx", ".qfx")
MAX_FILE = 25 * 1024 * 1024
CHUNK = 1100
OVERLAP = 180

#: Keywords that file a document when there is no AI (any of the five languages).
KEYWORDS: dict[str, tuple[str, ...]] = {
    "payslip": ("busta paga", "cedolino", "netto in busta", "retribuzione", "payslip", "pay slip",
                "net pay", "gehaltsabrechnung", "lohnabrechnung", "bulletin de paie",
                "fiche de paie", "nómina", "imponibile previdenziale"),
    "statement": ("estratto conto", "saldo iniziale", "saldo finale", "account statement",
                  "opening balance", "closing balance", "kontoauszug", "relevé de compte",
                  "extracto", "movimenti del periodo", "elenco movimenti"),
    "contract": ("contratto", "condizioni generali", "agreement", "contract", "vertrag",
                 "contrat", "recesso", "sottoscrizione", "clausola"),
    "invoice": ("fattura", "invoice", "rechnung", "facture", "factura", "partita iva",
                "imponibile", "iva ", "vat "),
    "receipt": ("scontrino", "ricevuta", "receipt", "quittung", "reçu", "recibo"),
    "tax": ("730", "modello unico", "agenzia delle entrate", "f24", "certificazione unica",
            "tax return", "steuererklärung", "impôt", "declaración de la renta", "irpef"),
    "insurance": ("polizza", "assicurazione", "insurance", "versicherung", "assurance",
                  "seguro", "premio annuo"),
    "investment": ("rendiconto titoli", "dossier titoli", "portafoglio", "isin", "depot",
                   "portfolio", "contract note", "eseguito", "piano di accumulo",
                   "savings plan", "sparplan"),
}  # fmt: skip
STOPWORDS = set(
    [
        "il",
        "lo",
        "la",
        "i",
        "gli",
        "le",
        "un",
        "una",
        "uno",
        "di",
        "da",
        "in",
        "con",
        "su",
        "per",
        "tra",
        "fra",
        "a",
        "e",
        "o",
        "ma",
        "che",
        "chi",
        "cosa",
        "come",
        "quando",
        "quanto",
        "quanti",
        "quale",
        "quali",
        "dove",
        "del",
        "della",
        "dei",
        "degli",
        "delle",
        "al",
        "alla",
        "ai",
        "agli",
        "alle",
        "nel",
        "nella",
        "nei",
        "nelle",
        "sul",
        "sulla",
        "mio",
        "mia",
        "miei",
        "mie",
        "ho",
        "ha",
        "hanno",
        "è",
        "sono",
        "era",
        "the",
        "a",
        "an",
        "of",
        "to",
        "in",
        "on",
        "for",
        "and",
        "or",
        "what",
        "when",
        "how",
        "much",
        "many",
        "which",
        "where",
        "is",
        "are",
        "was",
        "my",
        "me",
        "i",
        "do",
        "does",
        "did",
        "der",
        "die",
        "das",
        "und",
        "oder",
        "ist",
        "le",
        "la",
        "les",
        "de",
        "des",
        "du",
        "et",
        "ou",
        "est",
        "el",
        "los",
        "las",
        "y",
        "es",
        "mi",
    ]
)


class DocumentError(ValueError):
    """`code` is translated by the web app."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Stored:
    document: Document
    duplicate: bool = False
    outcome: Outcome | None = None


# -- text --------------------------------------------------------------------------------
async def extract_text(filename: str, raw: bytes, ai: AI) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        try:
            return pdf_text(raw)
        except ImportError_ as exc:
            raise DocumentError("pdf_unreadable") from exc
    if name.endswith(IMAGE_TYPES):
        if not ai.can_read_text:
            return ""
        mime = mimetypes.guess_type(name)[0] or "image/png"
        try:
            return await ai.read_image(mime, base64.b64encode(raw).decode())
        except AIError:
            return ""
    if name.endswith(TEXT_TYPES):
        return raw.decode("utf-8-sig", errors="replace")
    raise DocumentError("format_unsupported")


def guess_kind(filename: str, body: str) -> str:
    sample = f" {filename.lower()} {body[:6000].lower()} "
    if filename.lower().endswith((".ofx", ".qfx", ".csv", ".tsv")):
        return "statement"
    scores = {k: sum(sample.count(w) for w in words) for k, words in KEYWORDS.items()}
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] else "other"


_DATE = re.compile(r"\b(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}-\d{2}-\d{2})\b")


def guess_date(body: str) -> date | None:
    for found in _DATE.finditer(body[:4000]):
        with contextlib.suppress(ValueError):
            day = parse_date(found.group(1))
            if 1990 <= day.year <= date.today().year + 1:
                return day
    return None


def title_from(filename: str) -> str:
    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    return re.sub(r"\s+", " ", stem)[:200] or filename


def chunks(body: str) -> list[str]:
    """Paragraph-aware chunks of about CHUNK characters with a little overlap."""
    body = re.sub(r"[ \t]+", " ", body).strip()
    if not body:
        return []
    out: list[str] = []
    start = 0
    while start < len(body):
        end = min(len(body), start + CHUNK)
        if end < len(body):
            cut = max(
                body.rfind("\n", start + CHUNK // 2, end), body.rfind(". ", start + CHUNK // 2, end)
            )
            if cut > start:
                end = cut + 1
        out.append(body[start:end].strip())
        if end >= len(body):
            break
        start = max(end - OVERLAP, start + 1)
    return [c for c in out if c]


def index(db: Session, document: Document) -> None:
    db.execute(text("DELETE FROM doc_chunks WHERE document_id = :d"), {"d": document.id})
    header = " ".join(
        filter(None, [document.title, document.issuer, document.kind, document.summary])
    )
    pieces = chunks(document.text) or [header]
    for i, piece in enumerate(pieces):
        db.execute(
            text(
                "INSERT INTO doc_chunks (text, document_id, user_id, idx) VALUES (:t, :d, :u, :i)"
            ),
            {
                "t": f"{header}\n{piece}" if i == 0 else piece,
                "d": document.id,
                "u": document.user_id,
                "i": i,
            },
        )


def unindex(db: Session, document_id: int) -> None:
    db.execute(text("DELETE FROM doc_chunks WHERE document_id = :d"), {"d": document_id})


# -- filing ------------------------------------------------------------------------------
def _fold(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", word) if not unicodedata.combining(c))


def fts_query(question: str) -> str:
    """Words of a question as an FTS5 OR query; long words match by their stem
    («stipendio» finds «stipendi»), so Italian plurals and verb forms still meet."""
    words = []
    for raw in re.findall(r"[\w]+", question.lower()):
        word = _fold(raw)
        if word in STOPWORDS or (len(word) < 3 and not word.isdigit()):
            continue
        words.append(f'"{word[:-2]}"*' if len(word) > 6 else f'"{word}"*')
    return " OR ".join(dict.fromkeys(words))


def search(
    db: Session, user: User, question: str, limit: int = 8, kind: str | None = None
) -> list[dict[str, Any]]:
    query = fts_query(question)
    if not query:
        return []
    rows = db.execute(
        text(
            "SELECT document_id, idx, text, snippet(doc_chunks, 0, '«', '»', '…', 24) AS snip, "
            "bm25(doc_chunks) AS score FROM doc_chunks WHERE doc_chunks MATCH :q AND user_id = :u "
            "ORDER BY score LIMIT :n"
        ),
        {"q": query, "u": user.id, "n": limit * 3},
    ).all()
    docs = {
        d.id: d
        for d in db.scalars(
            select(Document).where(
                Document.id.in_({int(r.document_id) for r in rows}), Document.user_id == user.id
            )
        )
    }
    hits: list[dict[str, Any]] = []
    for r in rows:
        doc = docs.get(int(r.document_id))
        if doc is None or (kind and doc.kind != kind):
            continue
        hits.append(
            {
                "document_id": doc.id,
                "title": doc.title,
                "kind": doc.kind,
                "date": doc.doc_date.isoformat() if doc.doc_date else None,
                "chunk": int(r.idx),
                "snippet": r.snip,
                "text": r.text,
            }
        )
        if len(hits) >= limit:
            break
    return hits


async def ask(
    db: Session, user: User, question: str, ai: AI, history: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    """Answer from the user's documents, with the passages used as sources."""
    hits = search(db, user, question, limit=8)
    if not hits and history:  # a follow-up («and in October?») borrows the earlier question
        last = next((h["content"] for h in reversed(history) if h.get("role") == "user"), "")
        hits = search(db, user, f"{last} {question}", limit=8)
    sources = [
        {
            "n": i + 1,
            "document_id": h["document_id"],
            "title": h["title"],
            "date": h["date"],
            "snippet": h["snippet"],
        }
        for i, h in enumerate(hits)
    ]
    if not ai.can_read_text:
        return {"answer": None, "sources": sources, "ai": False}
    if not hits:
        return {"answer": None, "sources": [], "ai": True}
    passages = [{**h, "n": i + 1} for i, h in enumerate(hits)]
    try:
        answer = await ai.answer_from_documents(
            question, passages, user.language or "en", history=(history or [])[-6:]
        )
    except AIError as exc:
        raise DocumentError("ai_failed") from exc
    return {"answer": answer, "sources": sources, "ai": True}


def _apply_description(doc: Document, data: dict[str, Any]) -> None:
    if data.get("kind") in KINDS:
        doc.kind = str(data["kind"])
    if isinstance(data.get("title"), str) and data["title"].strip():
        doc.title = data["title"].strip()[:200]
    if isinstance(data.get("issuer"), str):
        doc.issuer = data["issuer"].strip()[:160]
    if isinstance(data.get("summary"), str):
        doc.summary = data["summary"].strip()
    if data.get("date"):
        with contextlib.suppress(ValueError, TypeError):
            doc.doc_date = date.fromisoformat(str(data["date"])[:10])
    if isinstance(data.get("facts"), dict):
        doc.facts = {
            str(k): v for k, v in data["facts"].items() if isinstance(v, (str, int, float))
        }


async def store(
    db: Session,
    user: User,
    folder: Path,
    filename: str,
    raw: bytes,
    ai: AI,
    *,
    kind: str | None = None,
    account_id: int | None = None,
    import_statement: bool = True,
    source: str = "upload",
) -> Stored:
    """File a document (once: the same bytes twice give the first copy back)."""
    filename = Path(filename or "document").name[:255]
    if len(raw) > MAX_FILE:
        raise DocumentError("file_too_big")
    if not raw:
        raise DocumentError("file_empty")
    if not filename.lower().endswith(ACCEPTED):
        raise DocumentError("format_unsupported")
    if kind is not None and kind not in KINDS:
        raise DocumentError("kind_unknown")
    digest = hashlib.sha256(raw).hexdigest()
    same = db.scalar(select(Document).where(Document.user_id == user.id, Document.sha256 == digest))
    if same is not None:
        return Stored(same, duplicate=True)

    body = await extract_text(filename, raw, ai)
    doc = Document(
        user_id=user.id, filename=filename, size=len(raw), sha256=digest, stored="",
        mime=mimetypes.guess_type(filename)[0] or "application/octet-stream",
        text=body, title=title_from(filename), kind=kind or guess_kind(filename, body),
        doc_date=guess_date(body), source=source, facts={},
    )  # fmt: skip
    if body.strip() and ai.can_read_text:
        try:
            _apply_description(doc, await ai.describe_document(body, filename))
        except AIError as exc:
            log.info("document description failed: %s", exc)
        if kind:
            doc.kind = kind  # the user's choice wins over the AI's
    db.add(doc)
    db.flush()
    target = folder / str(user.id) / f"{doc.id}{Path(filename).suffix.lower()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    doc.stored = str(target.relative_to(folder))

    outcome = None
    if import_statement and doc.kind == "statement" and filename.lower().endswith(STATEMENT_TYPES):
        outcome = await import_into(db, user, doc, raw, ai, account_id)
    elif account_id is not None:
        doc.account_id = _own_account(db, user, account_id).id
    index(db, doc)
    db.commit()
    return Stored(doc, outcome=outcome)


def _own_account(db: Session, user: User, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if account is None or account.user_id != user.id:
        raise DocumentError("account_unknown")
    return account


async def import_into(
    db: Session, user: User, doc: Document, raw: bytes, ai: AI, account_id: int | None
) -> Outcome | None:
    """Movements of a statement into its account; the reason is kept when it can't be done."""
    try:
        parsed = await parse_statement(doc.filename, raw, ai)
    except ImportError_ as exc:
        doc.import_note = exc.code
        return None
    try:
        account = pick_account(db, user, parsed, account_id)
    except ImportError_ as exc:
        doc.import_note = exc.code
        if exc.code == "account_not_recognised":
            # an account Oink doesn't know yet: propose it, filled in from the statement
            doc.facts = {**(doc.facts or {}), "proposed_account": propose_account(doc, parsed)}
        return None
    outcome = apply(db, account, parsed)
    facts = dict(doc.facts or {})
    facts.pop("proposed_account", None)
    doc.facts = facts
    doc.account_id, doc.imported, doc.import_note = account.id, outcome.added, ""
    if outcome.month and doc.doc_date is None:
        doc.doc_date = date.fromisoformat(f"{outcome.month}-01")
    return outcome


#: Italian bank codes (ABI, in the IBAN) of the most common banks.
ABI = {
    "03069": "Intesa Sanpaolo",
    "02008": "UniCredit",
    "05387": "BPER Banca",
    "01005": "BNL",
    "01030": "Monte dei Paschi",
    "05034": "Banco BPM",
    "03015": "Fineco",
    "07601": "Poste Italiane",
    "03062": "Banca Mediolanum",
}
KNOWN = {
    "revolut": "Revolut",
    "trade republic": "Trade Republic",
    "deblock": "Deblock",
    "young platform": "Young Platform",
    "intesa sanpaolo": "Intesa Sanpaolo",
    "isybank": "Isybank",
    "bper": "BPER Banca",
    "unicredit": "UniCredit",
    "fineco": "Fineco",
    "n26": "N26",
    "wise": "Wise",
    "hype": "Hype",
    "bunq": "bunq",
    "scalable": "Scalable Capital",
    "mediolanum": "Banca Mediolanum",
    "bancoposta": "Poste Italiane",
    "illimity": "illimity",
    "paypal": "PayPal",
    "klarna": "Klarna",
    "kraken": "Kraken",
    "satispay": "Satispay",
}


def propose_account(doc: Document, parsed: Any) -> dict[str, Any]:
    """A new account filled in from the statement itself (deterministic: IBAN, bank code,
    names printed on it, closing balance), for the person to check and confirm."""
    iban = parsed.ibans[0] if len(parsed.ibans) == 1 else ""
    institution = ""
    if iban.startswith("IT") and len(iban) >= 10:
        institution = ABI.get(iban[5:10], "")
    head = f" {(doc.issuer + ' ' + doc.text[:3000]).lower()} "
    if not institution:
        institution = next((name for key, name in KNOWN.items() if f"{key}" in head), "")
    if not institution and doc.issuer:
        institution = doc.issuer[:120]
    lowered = head
    kind = (
        "card"
        if any(w in lowered for w in ("carta di credito", "credit card", "estratto carta"))
        else (
            "investment"
            if any(w in lowered for w in ("dossier titoli", "depot", "portafoglio titoli"))
            else "checking"
        )
    )
    currency = next((c for c in ("EUR", "USD", "GBP", "CHF") if f" {c.lower()} " in lowered), "EUR")
    name = institution or (f"Conto …{iban[-4:]}" if iban else doc.title[:60])
    return {
        "name": name,
        "institution": institution,
        "iban": iban,
        "kind": kind,
        "currency": currency,
        "balance": None if parsed.balance is None else str(parsed.balance),
        "movements": len(parsed.rows),
        "month": parsed.month,
    }


def path_of(folder: Path, doc: Document) -> Path:
    return folder / doc.stored


def remove(db: Session, folder: Path, doc: Document) -> None:
    unindex(db, doc.id)
    with contextlib.suppress(FileNotFoundError):
        path_of(folder, doc).unlink()
    db.delete(doc)
    db.commit()


def to_dict(doc: Document, with_text: bool = False) -> dict[str, Any]:
    out = {
        "id": doc.id, "kind": doc.kind, "title": doc.title, "filename": doc.filename,
        "mime": doc.mime, "size": doc.size, "summary": doc.summary, "issuer": doc.issuer,
        "date": doc.doc_date.isoformat() if doc.doc_date else None, "facts": doc.facts or {},
        "account_id": doc.account_id, "imported": doc.imported, "import_note": doc.import_note,
        "source": doc.source, "created": doc.created.isoformat() if doc.created else None,
        "has_text": bool(doc.text.strip()),
    }  # fmt: skip
    if with_text:
        out["text"] = doc.text
    return out
