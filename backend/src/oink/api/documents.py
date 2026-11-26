"""The documents hub over HTTP: upload, list, read, file, import, search and chat."""

from __future__ import annotations

import asyncio
import datetime as dt
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from oink.api.deps import Caller, get_db, require
from oink.core.db import Document
from oink.services import documents as docs
from oink.services.ai import AI

router = APIRouter(prefix="/api/documents")
_TASKS: dict[int, asyncio.Task[None]] = {}  # documents being read in the background
Kind = Literal[
    "statement",
    "payslip",
    "contract",
    "invoice",
    "receipt",
    "tax",
    "insurance",
    "investment",
    "other",
]


def _ai(request: Request) -> AI:
    ai: AI = request.app.state.ai
    return ai


def _folder(request: Request) -> Any:
    return request.app.state.settings.documents_dir


def _mine(db: Session, who: Caller, doc_id: int) -> Document:
    doc = db.get(Document, doc_id)
    if doc is None or doc.user_id != who.user_id:
        raise HTTPException(404, "document_unknown")
    return doc


def _fail(exc: docs.DocumentError) -> HTTPException:
    return HTTPException(413 if exc.code == "file_too_big" else 422, exc.code)


class DocumentPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    kind: Kind | None = None
    date: dt.date | None = None
    account_id: int | None = None
    issuer: str | None = Field(default=None, max_length=160)


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class Question(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    history: list[Turn] = Field(default_factory=list, max_length=20)


class ImportIn(BaseModel):
    account_id: int | None = None


def _outcome(stored: docs.Stored) -> dict[str, Any]:
    out = docs.to_dict(stored.document)
    out["duplicate"] = stored.duplicate
    out["import"] = stored.outcome.__dict__ if stored.outcome else None
    return out


@router.get("")
def list_documents(
    kind: Kind | None = None,
    q: str = Query(default="", max_length=200),
    year: int | None = None,
    status: Literal["processing", "ready", "review", "error"] | None = None,
    who: Caller = Depends(require("documents:read")),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    query = select(Document).where(Document.user_id == who.user_id)
    if status:
        query = query.where(Document.status == status)
    if kind:
        query = query.where(Document.kind == kind)
    if year:
        query = query.where(
            Document.doc_date >= date(year, 1, 1), Document.doc_date <= date(year, 12, 31)
        )
    if q.strip():
        found = {h["document_id"] for h in docs.search(db, who.user, q, limit=50)}
        like = f"%{q.strip()}%"
        query = query.where(
            or_(
                Document.id.in_(found),
                Document.title.ilike(like),
                Document.issuer.ilike(like),
                Document.filename.ilike(like),
            )
        )
    rows = db.scalars(
        query.order_by(Document.doc_date.desc().nulls_last(), Document.created.desc())
    )
    return [docs.to_dict(d) for d in rows]


@router.post("")
async def upload(
    request: Request,
    file: UploadFile = File(...),
    kind: Kind | None = Form(default=None),
    account_id: int | None = Form(default=None),
    import_statement: bool = Form(default=True),
    who: Caller = Depends(require("documents:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Any financial document. A statement is also imported into its account (the IBAN on it
    picks the account, or `account_id`); `import.added` says how many movements came in."""
    raw = await file.read(docs.MAX_FILE + 1)
    stores = docs.stores_in_use()
    later = stores is not None and stores.engine.enabled  # Docling takes a while: reply now
    try:
        stored = await docs.store(
            db,
            who.user,
            _folder(request),
            file.filename or "document",
            raw,
            _ai(request),
            kind=kind,
            account_id=account_id,
            import_statement=import_statement,
            source="upload" if who.browser else "api",
            later=later,
        )
    except docs.DocumentError as exc:
        raise _fail(exc) from exc
    if later and not stored.duplicate:
        _start(request.app, stored.document.id)
    return _outcome(stored)


def _start(app: Any, doc_id: int) -> None:
    """Read a document in the background (Docling can take minutes on a long statement)."""
    old = _TASKS.get(doc_id)
    if old is not None and not old.done():
        return
    task = asyncio.create_task(
        docs.process_later(
            app.state.db.sessions, doc_id, app.state.ai, app.state.settings.documents_dir
        )
    )
    _TASKS[doc_id] = task
    task.add_done_callback(lambda t: _TASKS.pop(doc_id, None) if _TASKS.get(doc_id) is t else None)


def resume(app: Any) -> None:
    """At startup: documents left «processing» by a restart are read again."""
    with app.state.db.sessions() as db:
        waiting = list(db.scalars(select(Document.id).where(Document.status == "processing")))
    stores = docs.stores_in_use()
    for doc_id in waiting:
        if stores is not None and stores.engine.enabled:
            _start(app, doc_id)
        else:
            with app.state.db.sessions() as db:
                doc = db.get(Document, doc_id)
                if doc is not None:
                    doc.status, doc.review = "error", {**docs.wishes(doc), "error": "interrupted"}
                    db.commit()


@router.get("/search")
async def search_documents(
    q: str = Query(min_length=2, max_length=300),
    kind: Kind | None = None,
    limit: int = Query(default=8, ge=1, le=30),
    who: Caller = Depends(require("documents:read")),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    hits = await docs.find(db, who.user, q, limit, kind)
    return [{k: v for k, v in h.items() if k != "text"} for h in hits]


@router.post("/ask")
async def ask_documents(
    body: Question,
    request: Request,
    who: Caller = Depends(require("documents:read")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Chat with the documents: only the passages found are sent to the AI."""
    if not who.browser and "ai:ask" not in who.scopes:
        raise HTTPException(403, "scope_missing:ai:ask")
    try:
        return await docs.ask(
            db, who.user, body.question, _ai(request), [t.model_dump() for t in body.history]
        )
    except docs.DocumentError as exc:
        raise _fail(exc) from exc


@router.get("/{doc_id}")
def get_document(
    doc_id: int, who: Caller = Depends(require("documents:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return docs.to_dict(_mine(db, who, doc_id), with_text=True)


@router.get("/{doc_id}/file")
def document_file(
    doc_id: int,
    request: Request,
    download: bool = False,
    who: Caller = Depends(require("documents:read")),
    db: Session = Depends(get_db),
) -> FileResponse:
    doc = _mine(db, who, doc_id)
    path = docs.path_of(_folder(request), doc)
    if not path.is_file():
        raise HTTPException(404, "file_missing")
    # Only PDFs and images are shown as themselves; anything else is plain text, so an
    # uploaded file can never run as a page of Oink. Chrome's PDF viewer refuses a sandbox.
    shown = (
        doc.mime if doc.mime == "application/pdf" or doc.mime.startswith("image/") else "text/plain"
    )
    headers = {"X-Content-Type-Options": "nosniff"}
    if shown != "application/pdf":
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self'"
    return FileResponse(
        path,
        media_type=shown,
        filename=doc.filename,
        content_disposition_type="attachment" if download else "inline",
        headers=headers,
    )


@router.patch("/{doc_id}")
def patch_document(
    doc_id: int,
    body: DocumentPatch,
    who: Caller = Depends(require("documents:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    doc = _mine(db, who, doc_id)
    data = body.model_dump(exclude_unset=True)
    if "account_id" in data and data["account_id"] is not None:
        try:
            docs._own_account(db, who.user, data["account_id"])
        except docs.DocumentError as exc:
            raise _fail(exc) from exc
    for key, value in data.items():
        setattr(doc, "doc_date" if key == "date" else key, value)
    docs.index(db, doc)
    db.commit()
    return docs.to_dict(doc)


@router.post("/{doc_id}/import")
async def import_document(
    doc_id: int,
    body: ImportIn,
    request: Request,
    who: Caller = Depends(require("documents:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Import (again) a statement, into the account given or the one its IBAN names."""
    doc = _mine(db, who, doc_id)
    path = docs.path_of(_folder(request), doc)
    if not path.is_file():
        raise HTTPException(404, "file_missing")
    if body.account_id is not None:
        try:
            docs._own_account(db, who.user, body.account_id)
        except docs.DocumentError as exc:
            raise _fail(exc) from exc
    if not (doc.review or {}).get("rows"):  # filed as something else: read it as a statement
        doc.review = {**(doc.review or {}), "wanted_kind": "statement", "import": False}
        await docs.process(db, who.user, doc, path.read_bytes(), _ai(request), docs.stores_in_use())
    # importing a document «to review» is the person's decision: it stands
    outcome = docs.import_into(db, who.user, doc, body.account_id)
    if outcome is not None:
        doc.kind, doc.status = "statement", "ready"
        doc.review = {**(doc.review or {}), "confirmed": True}
    db.commit()
    if outcome is None:
        raise HTTPException(422, doc.import_note or "nothing_found")
    out = docs.to_dict(doc)
    out["import"] = outcome.__dict__
    return out


@router.delete("/{doc_id}", status_code=204)
async def delete_document(
    doc_id: int,
    request: Request,
    who: Caller = Depends(require("documents:write")),
    db: Session = Depends(get_db),
) -> None:
    """Removes the file and its index. Movements imported from it stay in the account."""
    await docs.remove(db, _folder(request), _mine(db, who, doc_id))


@router.post("/{doc_id}/move")
def move_document(
    doc_id: int, body: ImportIn, who: Caller = Depends(require("documents:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:  # fmt: skip
    """Move a statement's movements to another account (it went into the wrong one)."""
    doc = _mine(db, who, doc_id)
    if body.account_id is None:
        raise HTTPException(422, "account_unknown")
    try:
        outcome = docs.move_import(db, who.user, doc, body.account_id)
    except docs.DocumentError as exc:
        raise _fail(exc) from exc
    out = docs.to_dict(doc)
    out["import"] = outcome.__dict__ if outcome else None
    return out


@router.post("/{doc_id}/reprocess")
async def reprocess_document(
    doc_id: int,
    request: Request,
    who: Caller = Depends(require("documents:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Read and check the document again (after a fix, or once the engine is up)."""
    doc = _mine(db, who, doc_id)
    path = docs.path_of(_folder(request), doc)
    if not path.is_file():
        raise HTTPException(404, "file_missing")
    task = _TASKS.get(doc_id)
    if task is not None and not task.done():
        return docs.to_dict(doc)  # already being read
    doc.status = "processing"
    db.commit()
    stores = docs.stores_in_use()
    if stores is not None and stores.engine.enabled:
        _start(request.app, doc_id)  # the slow way: in the background, like an upload
        return docs.to_dict(doc)
    await docs.process(db, who.user, doc, path.read_bytes(), _ai(request), stores)
    return docs.to_dict(doc)


@router.post("/{doc_id}/stop")
async def stop_document(
    doc_id: int,
    who: Caller = Depends(require("documents:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Stop reading a document (too long, stuck): it can be read again with «reprocess»."""
    doc = _mine(db, who, doc_id)
    task = _TASKS.pop(doc_id, None)
    if task is not None and not task.done():
        task.cancel()
    stores = docs.stores_in_use()
    if stores is not None and stores.engine.enabled:
        await stores.engine.cancel()  # Docling stops too, not only the wait for it
    db.refresh(doc)
    if doc.status == "processing":
        doc.status, doc.review = "error", {**docs.wishes(doc), "error": "stopped"}
        db.commit()
    return docs.to_dict(doc)


@router.post("/{doc_id}/dismiss")
def dismiss_review(
    doc_id: int, who: Caller = Depends(require("documents:write")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """«Checked, it's fine as it is»: out of the review list, nothing imported."""
    doc = _mine(db, who, doc_id)
    doc.status = "ready"
    doc.review = {**(doc.review or {}), "dismissed": True}
    db.commit()
    return docs.to_dict(doc)
