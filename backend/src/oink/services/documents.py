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

import asyncio
import base64
import contextlib
import hashlib
import logging
import mimetypes
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from oink.core.db import Account, Document, Transaction, User
from oink.services import layouts
from oink.services.ai import AI, AIError
from oink.services.importer import (
    ImportError_,
    Parsed,
    Row,
    find_ibans,
    parse_csv,
    parse_date,
    parse_ofx,
    parse_pdf_lines,
    pdf_text,
)
from oink.services.statements import Outcome, apply, pick_account
from oink.services.stores import Cache, StoreError, Stores
from oink.services.verify import (
    Verdict,
    balances,
    check_invoice,
    check_payslip,
    check_statement,
    rows_from_tables,
)

log = logging.getLogger("oink.documents")

KINDS = (
    "statement",
    "payslip",
    "contract",
    "invoice",
    "receipt",
    "tax",
    "insurance",
    "investment",
    "other",
)
TEXT_TYPES = (".pdf", ".csv", ".tsv", ".txt", ".ofx", ".qfx", ".md", ".json", ".xml")
IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
ACCEPTED = TEXT_TYPES + IMAGE_TYPES
STATEMENT_TYPES = (".pdf", ".csv", ".tsv", ".txt", ".ofx", ".qfx")
MAX_FILE = 25 * 1024 * 1024
CHUNK = 1100
OVERLAP = 180

#: Keywords that file a document when there is no AI (any of the five languages).
KEYWORDS: dict[str, tuple[str, ...]] = {
    "payslip": (
        "busta paga",
        "cedolino",
        "netto in busta",
        "retribuzione",
        "payslip",
        "pay slip",
        "net pay",
        "gehaltsabrechnung",
        "lohnabrechnung",
        "bulletin de paie",
        "fiche de paie",
        "nómina",
        "imponibile previdenziale",
    ),
    "statement": (
        "estratto conto",
        "saldo iniziale",
        "saldo finale",
        "account statement",
        "opening balance",
        "closing balance",
        "kontoauszug",
        "relevé de compte",
        "extracto",
        "movimenti del periodo",
        "elenco movimenti",
    ),
    "contract": (
        "contratto",
        "condizioni generali",
        "agreement",
        "contract",
        "vertrag",
        "contrat",
        "recesso",
        "sottoscrizione",
        "clausola",
    ),
    "invoice": (
        "fattura",
        "invoice",
        "rechnung",
        "facture",
        "factura",
        "partita iva",
        "imponibile",
        "iva ",
        "vat ",
    ),
    "receipt": ("scontrino", "ricevuta", "receipt", "quittung", "reçu", "recibo"),
    "tax": (
        "730",
        "modello unico",
        "agenzia delle entrate",
        "f24",
        "certificazione unica",
        "tax return",
        "steuererklärung",
        "impôt",
        "declaración de la renta",
        "irpef",
    ),
    "insurance": (
        "polizza",
        "assicurazione",
        "insurance",
        "versicherung",
        "assurance",
        "seguro",
        "premio annuo",
    ),
    "investment": (
        "rendiconto titoli",
        "dossier titoli",
        "portafoglio",
        "isin",
        "depot",
        "portfolio",
        "contract note",
        "eseguito",
        "piano di accumulo",
        "savings plan",
        "sparplan",
    ),
}
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


_stores: Stores | None = None  # set once at startup (`configure`): engine, vectors, graph, cache


def configure(stores: Stores | None) -> None:
    global _stores
    _stores = stores


def stores_in_use(stores: Stores | None = None) -> Stores | None:
    return stores or _stores


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


async def find(
    db: Session,
    user: User,
    question: str,
    limit: int = 8,
    kind: str | None = None,
    stores: Stores | None = None,
) -> list[dict[str, Any]]:
    """Hybrid when the engine and Qdrant are there: BGE-M3 dense (meaning) + sparse (exact
    words and numbers) from Qdrant, SQLite full-text, and documents the graph links to
    names in the question, fused by reciprocal rank. Full-text alone otherwise."""
    fts = search(db, user, question, limit * 2, kind)
    stores = stores_in_use(stores)
    if stores is None or not stores.hybrid:
        return fts[:limit]
    try:
        dense, sparse = (await embed(stores, [question]))[0]
        semantic = await stores.vectors.search(user.id, dense, sparse, limit * 2, kind)
    except StoreError as exc:
        log.warning("hybrid search failed, full-text only: %s", exc)
        return fts[:limit]
    words = [w for w in re.findall(r"[\w]+", question.lower()) if len(w) > 3 and w not in STOPWORDS]
    linked = set(await stores.graph.related(user.id, words)) if stores.graph.enabled else set()
    score: dict[tuple[int, int], float] = {}
    by_key: dict[tuple[int, int], dict[str, Any]] = {}
    for ranking in (semantic, fts):
        for rank, hit in enumerate(ranking):
            key = (int(hit["document_id"]), int(hit.get("idx", hit.get("chunk", 0))))
            score[key] = score.get(key, 0) + 1 / (60 + rank)
            by_key.setdefault(key, hit)
    for key in score:
        if key[0] in linked:
            score[key] += 1 / 60
    titles = {
        d.id: d
        for d in db.scalars(
            select(Document).where(
                Document.id.in_({k[0] for k in score}), Document.user_id == user.id
            )
        )
    }
    out = []
    for key in sorted(score, key=lambda k: -score[k]):
        doc = titles.get(key[0])
        if doc is None:
            continue
        hit = by_key[key]
        text_ = str(hit.get("text") or "")
        out.append(
            {
                "document_id": doc.id,
                "title": doc.title,
                "kind": doc.kind,
                "date": doc.doc_date.isoformat() if doc.doc_date else None,
                "chunk": key[1],
                "snippet": hit.get("snippet") or (text_[:220] + ("…" if len(text_) > 220 else "")),
                "text": text_,
            }
        )
        if len(out) >= limit:
            break
    return out


async def ask(
    db: Session,
    user: User,
    question: str,
    ai: AI,
    history: list[dict[str, str]] | None = None,
    stores: Stores | None = None,
) -> dict[str, Any]:
    """Answer from the user's documents, with the passages used as sources."""
    hits = await find(db, user, question, 8, None, stores)
    if not hits and history:  # a follow-up («and in October?») borrows the earlier question
        last = next((h["content"] for h in reversed(history) if h.get("role") == "user"), "")
        hits = await find(db, user, f"{last} {question}", 8, None, stores)
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


@dataclass
class Extraction:
    text: str
    tables: list[dict[str, Any]]
    by_engine: bool


async def read_file(filename: str, raw: bytes, ai: AI, stores: Stores | None) -> Extraction:
    """Docling when the engine is there (layout, tables, OCR of scans and photos),
    the built-in readers otherwise. Cached by content, so a document is read once."""
    name = filename.lower()
    if stores is not None and stores.engine.enabled and name.endswith((".pdf", *IMAGE_TYPES)):
        key = Cache.key_of("extract", hashlib.sha256(raw).hexdigest())
        cached = await stores.cache.get("extract", key)
        try:
            data = cached or await stores.engine.extract(filename, raw)
        except StoreError as exc:
            log.warning("engine extract failed (%s): built-in reader", exc)
        else:
            if cached is None:
                await stores.cache.set("extract", key, data)
            body = str(data.get("markdown") or data.get("text") or "")
            if body.strip() or not name.endswith(".pdf"):
                return Extraction(body, list(data.get("tables") or []), True)
    return Extraction(await extract_text(filename, raw, ai), [], False)


async def known_layout(filename: str, raw: bytes) -> tuple[layouts.Layout, str] | None:
    """A bank statement whose layout Oink knows, read from the PDF's own text (no Docling),
    with that text."""
    if not filename.lower().endswith(".pdf"):
        return None
    try:
        text = await asyncio.to_thread(pdf_text, raw)
    except ImportError_:
        return None
    found = layouts.read(text)
    if found is None and text.strip():
        # any other generated statement: the general line rules, trusted only if its
        # balances add up (otherwise Docling reads its tables)
        with contextlib.suppress(ImportError_):
            parsed = parse_pdf_lines(text)
            if parsed.rows and not parsed.estimated:
                opening, closing = balances(text)
                if check_statement(parsed.rows, text, False).status == "ok":
                    found = layouts.Layout("", parsed.rows, opening, closing,
                                           parsed.ibans or find_ibans(text))  # fmt: skip
    return (found, text) if found is not None else None


def statement_rows(doc: Document, raw: bytes, extraction: Extraction) -> tuple[list[Row], bool]:
    """Movements read by rules: the bank's export as it is, or the statement's tables."""
    name = doc.filename.lower()
    with contextlib.suppress(ImportError_):
        if name.endswith((".ofx", ".qfx")) or raw.lstrip()[:9].upper().startswith(b"OFXHEADER"):
            return parse_ofx(raw), True
        if name.endswith((".csv", ".tsv", ".txt")):
            return parse_csv(raw), True
    if extraction.tables:
        rows = rows_from_tables(extraction.tables)
        if rows:
            return rows, False
    return parse_pdf_lines(extraction.text).rows, False


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
    stores: Stores | None = None,
    later: bool = False,
) -> Stored:
    """File a document (once: the same bytes twice give the first copy back). With
    `later` the file is saved and `status` is «processing»: call `process` afterwards."""
    filename = Path(filename or "document").name[:255]
    if len(raw) > MAX_FILE:
        raise DocumentError("file_too_big")
    if not raw:
        raise DocumentError("file_empty")
    if not filename.lower().endswith(ACCEPTED):
        raise DocumentError("format_unsupported")
    if kind is not None and kind not in KINDS:
        raise DocumentError("kind_unknown")
    if account_id is not None:
        _own_account(db, user, account_id)
    digest = hashlib.sha256(raw).hexdigest()
    same = db.scalar(select(Document).where(Document.user_id == user.id, Document.sha256 == digest))
    if same is not None:
        return Stored(same, duplicate=True)
    doc = Document(
        user_id=user.id,
        filename=filename,
        size=len(raw),
        sha256=digest,
        stored="",
        mime=mimetypes.guess_type(filename)[0] or "application/octet-stream",
        text="",
        title=title_from(filename),
        kind=kind or "other",
        source=source,
        facts={},
        status="processing",
        review={"wanted_kind": kind, "account_id": account_id, "import": import_statement},
    )
    db.add(doc)
    db.flush()
    target = folder / str(user.id) / f"{doc.id}{Path(filename).suffix.lower()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    doc.stored = str(target.relative_to(folder))
    db.commit()
    if later:
        return Stored(doc)
    outcome = await process(db, user, doc, raw, ai, stores_in_use(stores))
    return Stored(doc, outcome=outcome)


async def process(
    db: Session, user: User, doc: Document, raw: bytes, ai: AI, stores: Stores | None
) -> Outcome | None:
    """Read, file, check by rules, import what adds up, index. Never raises for a bad file:
    the document ends «ready», «review» (with what to look at) or «error»."""
    wanted = dict(doc.review or {})
    kind, account_id = wanted.get("wanted_kind"), wanted.get("account_id")
    known = await known_layout(doc.filename, raw)
    layout = known[0] if known else None
    try:
        extraction = (
            Extraction(known[1], [], False)
            if known
            else await read_file(doc.filename, raw, ai, stores)
        )
    except DocumentError as exc:
        doc.status, doc.review = "error", {**wishes(doc), "error": exc.code}
        db.commit()
        return None
    body = extraction.text
    doc.text = body
    doc.kind = kind or ("statement" if layout is not None else guess_kind(doc.filename, body))
    if layout is not None and layout.holder:
        doc.facts = {**(doc.facts or {}), "account_holder": layout.holder}
    if layout is not None and layout.bank:
        doc.issuer = layout.bank
        doc.title = f"{layout.bank} · {layout.period}" if layout.period else doc.title
    doc.doc_date = guess_date(body)
    if body.strip() and ai.can_read_text:  # words for people: title, issuer, summary
        try:
            _apply_description(doc, await ai.describe_document(body, doc.filename))
        except AIError as exc:
            log.info("document description failed: %s", exc)
        if kind:
            doc.kind = kind  # the person's choice wins over the AI's
    verdict: Verdict | None = None
    outcome: Outcome | None = None
    rows: list[Row] = []
    if doc.kind == "statement" and layout is not None:
        rows = layout.rows
        verdict = check_statement(rows, body, False, (layout.opening, layout.closing))
    elif doc.kind == "statement":
        rows, structured = statement_rows(doc, raw, extraction)
        verdict = check_statement(rows, body, structured)
    elif doc.kind == "payslip":
        verdict = check_payslip(body)
    elif doc.kind in ("invoice", "receipt"):
        verdict = check_invoice(body)
    review: dict[str, Any] = {"engine": extraction.by_engine}
    if verdict is not None:
        doc.facts = {**(doc.facts or {}), **verdict.facts}  # numbers by rules win over the AI's
        review.update(verdict.as_dict())
    if rows:
        review["rows"] = [
            [r.day.isoformat(), str(r.amount), r.description, r.counterparty, r.category]
            for r in rows
        ]
        review["ibans"] = layout.ibans if layout is not None else find_ibans(body)
    if layout is not None and layout.bank:
        review["layout"] = {"bank": layout.bank, "skipped": layout.skipped,
                            "trades": layout.trades, "savings": len(layout.savings)}  # fmt: skip
    if doc.kind == "investment" and body.strip() and ai.can_read_text:
        from oink.services.portfolio_snapshots import propose

        try:
            positions = await propose(body, ai)
            if positions:
                review["positions"] = positions
        except AIError as exc:
            log.info("portfolio proposal failed: %s", exc)
    doc.review = review
    doc.status = "review" if verdict is not None and verdict.status == "review" else "ready"
    if doc.kind == "statement" and rows and wanted.get("import", True) and doc.status == "ready":
        outcome = import_into(db, user, doc, account_id)
    elif account_id is not None:
        doc.account_id = account_id
    if doc.status == "review" and ai.can_read_text and verdict is not None:
        await _ai_review(doc, verdict, ai, user)
    index(db, doc)
    db.commit()
    if stores is not None:
        await index_stores(db, doc, stores)
    return outcome


STOPPING: set[int] = set()  # documents the person asked to stop reading


def wishes(doc: Document) -> dict[str, Any]:
    """What the person asked at upload (kind, account, import): kept for «read again»."""
    review = doc.review or {}
    return {k: review[k] for k in ("wanted_kind", "account_id", "import") if k in review}


async def process_later(sessions: Any, doc_id: int, ai: AI, folder: Path) -> None:
    """The slow part of an upload (reading with Docling, embeddings), after the reply."""
    with sessions() as db:
        doc = db.get(Document, doc_id)
        user = db.get(User, doc.user_id) if doc else None
        if doc is None or user is None:
            return
        try:
            await process(db, user, doc, path_of(folder, doc).read_bytes(), ai, stores_in_use())
        except asyncio.CancelledError:
            # «Stop» from the app marks it; a shutdown (an update) leaves it «processing»,
            # so it is read again at the next start
            log.warning("reading of document %s cancelled (%s)", doc_id,
                        "stop" if doc_id in STOPPING else "shutdown")  # fmt: skip
            db.rollback()
            doc = db.get(Document, doc_id)
            if doc is not None and doc.status == "processing" and doc_id in STOPPING:
                doc.status, doc.review = "error", {**wishes(doc), "error": "stopped"}
                db.commit()
            STOPPING.discard(doc_id)
            raise
        except Exception:  # never leave a document «processing» forever
            log.exception("processing document %s failed", doc_id)
            db.rollback()
            doc = db.get(Document, doc_id)
            if doc is not None:
                doc.status, doc.review = "error", {"error": "processing_failed"}
                db.commit()


async def _ai_review(doc: Document, verdict: Verdict, ai: AI, user: User) -> None:
    """The AI looks at what didn't add up and suggests where the mistake is. It changes
    nothing: its note is shown next to the document for the person to decide."""
    rows = (doc.review or {}).get("rows", [])
    try:
        note = await ai.review_document(
            doc.kind,
            [c.__dict__ for c in verdict.issues],
            rows[:120],
            doc.text[:12000],
            user.language or "en",
        )
    except AIError as exc:
        log.info("AI review failed: %s", exc)
        return
    doc.review = {**(doc.review or {}), "ai": note}


async def index_stores(db: Session, doc: Document, stores: Stores) -> None:
    """Chunks into Qdrant (dense + sparse BGE-M3 vectors), links into the graph."""
    if stores.hybrid:
        pieces = chunks(doc.text) or [doc.title]
        try:
            vectors = await embed(stores, pieces)
            await stores.vectors.delete_document(doc.id)
            date_text = doc.doc_date.isoformat() if doc.doc_date else None
            await stores.vectors.upsert(
                [
                    {
                        "id": doc.id * 100_000 + i,
                        "vector": {"dense": dense, "sparse": sparse},
                        "payload": {
                            "user_id": doc.user_id,
                            "document_id": doc.id,
                            "idx": i,
                            "text": piece,
                            "title": doc.title,
                            "kind": doc.kind,
                            "date": date_text,
                        },
                    }
                    for i, (piece, (dense, sparse)) in enumerate(zip(pieces, vectors, strict=True))
                ]
            )
        except StoreError as exc:
            log.warning("vector index failed for document %s: %s", doc.id, exc)
    if stores.graph.enabled:
        account = db.get(Account, doc.account_id) if doc.account_id else None
        await stores.graph.file_document(
            doc.user_id,
            to_dict(doc),
            {"id": account.id, "name": account.name, "institution": account.institution}
            if account
            else None,
        )


async def embed(stores: Stores, texts: list[str]) -> list[tuple[list[float], dict[str, list[Any]]]]:
    """BGE-M3 vectors, cached by text (a chunk or a question is embedded once)."""
    keys = [Cache.key_of("bge-m3", t) for t in texts]
    found: dict[int, Any] = {}
    for i, key in enumerate(keys):
        hit = await stores.cache.get("emb", key)
        if hit is not None:
            found[i] = (hit[0], hit[1])
    missing = [i for i in range(len(texts)) if i not in found]
    for start in range(0, len(missing), 16):
        batch = missing[start : start + 16]
        for i, vec in zip(batch, await stores.engine.embed([texts[i] for i in batch]), strict=True):
            found[i] = vec
            await stores.cache.set("emb", keys[i], [vec[0], vec[1]], ttl=90 * 86400)
    return [found[i] for i in range(len(texts))]


def _own_account(db: Session, user: User, account_id: int) -> Account:
    account = db.get(Account, account_id)
    if account is None or account.user_id != user.id:
        raise DocumentError("account_unknown")
    return account


def parsed_of(doc: Document) -> Parsed:
    review = doc.review or {}
    rows = [
        Row(
            date.fromisoformat(r[0]), Decimal(r[1]), r[2], r[3], category=r[4] if len(r) > 4 else ""
        )
        for r in review.get("rows", [])
    ]
    closing = (doc.facts or {}).get("closing_balance")
    return Parsed(
        rows,
        Decimal(str(closing)) if closing is not None else None,
        ibans=list(review.get("ibans", [])),
    )


def institution_of(doc: Document, parsed: Parsed) -> str:
    """The bank a statement is from, by rules: the IBAN's bank code, then the names printed."""
    return str(propose_account(doc, parsed).get("institution") or "")


def _same_bank(a: str, b: str) -> bool:
    a, b = a.lower().strip(), b.lower().strip()
    return bool(a and b) and (a in b or b in a)


def check_account(
    db: Session, user: User, doc: Document, parsed: Parsed, account: Account
) -> dict[str, Any] | None:
    """Doubts about the account a statement went into: its IBAN is another account's, or
    it is printed by another bank. Shown to the person, who can move it."""
    accounts = list(
        db.scalars(select(Account).where(Account.user_id == user.id, Account.archived.is_(False)))
    )
    for iban in parsed.ibans:
        owner = next(
            (a for a in accounts if a.iban and a.iban.replace(" ", "").upper() == iban), None
        )
        if owner is not None and owner.id != account.id:
            return {"code": "iban_other_account", "suggested": owner.id, "found": iban}
    if account.iban and parsed.ibans and account.iban.replace(" ", "").upper() not in parsed.ibans:
        return {"code": "iban_differs", "found": parsed.ibans[0]}
    bank = institution_of(doc, parsed)
    if (
        bank
        and account.institution
        and not _same_bank(bank, account.institution)
        and not _same_bank(bank, account.name)
    ):
        suggested = next(
            (a.id for a in accounts if _same_bank(bank, a.institution) or _same_bank(bank, a.name)),
            None,
        )
        return {"code": "bank_differs", "found": bank, "suggested": suggested}
    return None


def import_into(db: Session, user: User, doc: Document, account_id: int | None) -> Outcome | None:
    """The movements read from a statement into its account:

    1. the account chosen by the person (checked: a doubt is kept on the document);
    2. otherwise the account whose IBAN is printed on it;
    3. otherwise the only account at the same bank (kept with a note: «is it right?»);
    4. otherwise a new account is proposed, filled in from the statement."""
    parsed = parsed_of(doc)
    if not parsed.rows:
        doc.import_note = "nothing_found"
        return None
    warning: dict[str, Any] | None = None
    try:
        account = pick_account(db, user, parsed, account_id)
    except ImportError_ as exc:
        if exc.code != "account_not_recognised":
            doc.import_note = exc.code
            return None
        bank = institution_of(doc, parsed)
        same = [
            a
            for a in db.scalars(
                select(Account).where(Account.user_id == user.id, Account.archived.is_(False))
            )
            if bank and (_same_bank(bank, a.institution) or _same_bank(bank, a.name))
        ]
        if len(same) != 1:
            doc.import_note = exc.code
            doc.facts = {**(doc.facts or {}), "proposed_account": propose_account(doc, parsed)}
            return None
        account = same[0]
        warning = {"code": "account_guessed", "found": bank}
    if account_id is not None:
        warning = check_account(db, user, doc, parsed, account)
    before = account.balance
    outcome = apply(db, account, parsed, doc.id)
    facts = dict(doc.facts or {})
    facts.pop("proposed_account", None)
    doc.facts = facts
    doc.account_id, doc.imported, doc.import_note = account.id, outcome.added, ""
    review = dict(doc.review or {})
    review["account_warning"] = warning
    review["balance_before"] = {"account_id": account.id, "value": before}
    doc.review = review
    if outcome.month and doc.doc_date is None:
        doc.doc_date = date.fromisoformat(f"{outcome.month}-01")
    db.commit()
    return outcome


def move_import(db: Session, user: User, doc: Document, account_id: int) -> Outcome | None:
    """The statement went into the wrong account: take its movements back out (and the
    balance it set) and import it into the right one."""
    target = _own_account(db, user, account_id)
    review = dict(doc.review or {})
    old = db.get(Account, doc.account_id) if doc.account_id else None
    for tx in db.scalars(select(Transaction).where(Transaction.document_id == doc.id)):
        db.delete(tx)
    before = review.get("balance_before") or {}
    if old is not None and old.source == "manual" and before.get("account_id") == old.id:
        old.balance = before.get("value")
    doc.account_id, doc.imported = None, None
    review["account_warning"] = None
    doc.review = review
    db.commit()
    return import_into(db, user, doc, target.id)


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


async def remove(db: Session, folder: Path, doc: Document, stores: Stores | None = None) -> None:
    stores = stores_in_use(stores)
    if stores is not None:
        if stores.vectors.enabled:
            with contextlib.suppress(StoreError):
                await stores.vectors.delete_document(doc.id)
        await stores.graph.forget_document(doc.id)
    unindex(db, doc.id)
    with contextlib.suppress(FileNotFoundError):
        path_of(folder, doc).unlink()
    db.delete(doc)
    db.commit()


def to_dict(doc: Document, with_text: bool = False) -> dict[str, Any]:
    out = {
        "id": doc.id,
        "kind": doc.kind,
        "title": doc.title,
        "filename": doc.filename,
        "mime": doc.mime,
        "size": doc.size,
        "summary": doc.summary,
        "issuer": doc.issuer,
        "date": doc.doc_date.isoformat() if doc.doc_date else None,
        "facts": doc.facts or {},
        "account_id": doc.account_id,
        "imported": doc.imported,
        "import_note": doc.import_note,
        "source": doc.source,
        "created": doc.created.isoformat() if doc.created else None,
        "has_text": bool(doc.text.strip()),
        "status": doc.status,
    }
    review = dict(doc.review or {})
    rows = review.pop("rows", [])
    for private in ("ibans", "wanted_kind", "account_id", "import", "balance_before"):
        review.pop(private, None)
    review["movements"] = len(rows)
    out["review"] = review
    if with_text:
        out["text"] = doc.text
        out["rows"] = rows
    return out
