"""MCP server: Oink as tools for AI assistants (Claude, ChatGPT, Jarvis, any MCP client).

Streamable HTTP at `POST /mcp` (JSON-RPC 2.0, JSON responses, stateless), authenticated with
an Oink token (`Authorization: Bearer oink_…`): each tool needs the same scope as the API
route it mirrors, so a read-only token gives a read-only assistant."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from oink.api.deps import get_db
from oink.core.db import Account, Document, Transaction, User
from oink.core.money import MoneyError, as_text, to_minor
from oink.core.security import token_for
from oink.services import documents as docs
from oink.services import planning
from oink.services import reports as rep
from oink.services.ai import AI, AIError
from oink.services.ledger import NewMovement, add_movements
from oink.services.markets import MarketError, history
from oink.services.simulate import MonteCarlo, SimulationError, backtest, monthly_returns
from oink.services.updates import current_version

router = APIRouter()
PROTOCOL = "2025-06-18"
Handler = Callable[[Session, User, AI, dict[str, Any]], Awaitable[Any]]


class Tool:
    def __init__(
        self, name: str, scope: str, description: str, schema: dict[str, Any], run: Handler
    ):
        self.name, self.scope, self.description, self.schema, self.run = (
            name,
            scope,
            description,
            schema,
            run,
        )


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or []}


async def _overview(db: Session, user: User, ai: AI, a: dict[str, Any]) -> Any:
    book = rep.Book.of(db, user)
    return {"net_worth": rep.net_worth(book), "month": rep.compare(book, date.today().strftime("%Y-%m")),
            "recurring": rep.recurring(book)}  # fmt: skip


async def _accounts(db: Session, user: User, ai: AI, a: dict[str, Any]) -> Any:
    rows = db.scalars(
        select(Account).where(Account.user_id == user.id, Account.archived.is_(False))
    )
    return [{"id": x.id, "name": x.name, "kind": x.kind, "currency": x.currency,
             "balance": as_text(x.balance, x.currency), "institution": x.institution} for x in rows]  # fmt: skip


async def _transactions(db: Session, user: User, ai: AI, a: dict[str, Any]) -> Any:
    q = select(Transaction).where(Transaction.user_id == user.id).order_by(Transaction.day.desc())
    if a.get("month"):
        start, end = rep.month_bounds(str(a["month"]))
        q = q.where(Transaction.day >= start, Transaction.day < end)
    if a.get("category"):
        q = q.where(Transaction.category == str(a["category"]))
    if a.get("text"):
        like = f"%{a['text']}%"
        q = q.where(or_(Transaction.description.ilike(like), Transaction.counterparty.ilike(like)))
    rows = db.scalars(q.limit(min(int(a.get("limit", 50)), 500)))
    return [{"id": t.id, "day": t.day.isoformat(), "amount": as_text(t.amount, t.currency),
             "currency": t.currency, "description": t.description, "counterparty": t.counterparty,
             "category": t.category, "account_id": t.account_id} for t in rows]  # fmt: skip


async def _add_transaction(db: Session, user: User, ai: AI, a: dict[str, Any]) -> Any:
    acc = db.get(Account, int(a["account_id"]))
    if acc is None or acc.user_id != user.id:
        raise ValueError("account_unknown")
    try:
        amount = to_minor(str(a["amount"]), acc.currency)
    except MoneyError as exc:
        raise ValueError("amount_invalid") from exc
    result = add_movements(db, acc, [NewMovement(date.fromisoformat(str(a["day"])), amount,
                           str(a["description"]), category=str(a.get("category", "")))], "api")  # fmt: skip
    if result.added and acc.source == "manual" and acc.balance is not None:
        acc.balance += amount
        db.commit()
    return {"added": len(result.added), "duplicates": result.duplicates}


async def _picture(db: Session, user: User, ai: AI, a: dict[str, Any]) -> Any:
    return await planning.picture(db, rep.Book.of(db, user))


async def _holdings(db: Session, user: User, ai: AI, a: dict[str, Any]) -> Any:
    return await planning.holdings_value(db, rep.Book.of(db, user))


async def _fixed(db: Session, user: User, ai: AI, a: dict[str, Any]) -> Any:
    return planning.fixed_costs(db, rep.Book.of(db, user))


async def _simulate(db: Session, user: User, ai: AI, a: dict[str, Any]) -> Any:
    try:
        series = await history(db, str(a["symbol"]), 20)
        mc = MonteCarlo(monthly_returns(series), int(a.get("years", 10)), float(a.get("initial", 0)),
                        float(a.get("monthly", 0)), 2000).run()  # fmt: skip
        mc.pop("bands", None)
        return {"backtest": {k: v for k, v in backtest(series, float(a.get("monthly", 0)),
                float(a.get("initial", 0))).items() if k != "points"}, "monte_carlo": mc}  # fmt: skip
    except (MarketError, SimulationError) as exc:
        raise ValueError(str(exc)) from exc


async def _ask(db: Session, user: User, ai: AI, a: dict[str, Any]) -> Any:
    facts = await planning.picture(db, rep.Book.of(db, user))
    try:
        return await ai.answer(str(a["question"]), facts, user.language or "en")
    except AIError as exc:
        raise ValueError(str(exc)) from exc


async def _documents(db: Session, user: User, ai: AI, a: dict[str, Any]) -> Any:
    query = select(Document).where(Document.user_id == user.id)
    if a.get("kind"):
        query = query.where(Document.kind == str(a["kind"]))
    if a.get("query"):
        found = {h["document_id"] for h in docs.search(db, user, str(a["query"]), limit=50)}
        query = query.where(Document.id.in_(found))
    rows = db.scalars(
        query.order_by(Document.doc_date.desc().nulls_last()).limit(int(a.get("limit") or 50))
    )
    return [docs.to_dict(d) for d in rows]


async def _search_documents(db: Session, user: User, ai: AI, a: dict[str, Any]) -> Any:
    hits = docs.search(db, user, str(a["query"]), int(a.get("limit") or 8), a.get("kind") or None)
    return [{k: v for k, v in h.items() if k != "snippet"} for h in hits]


async def _ask_documents(db: Session, user: User, ai: AI, a: dict[str, Any]) -> Any:
    try:
        return await docs.ask(db, user, str(a["question"]), ai)
    except docs.DocumentError as exc:
        raise ValueError(exc.code) from exc


async def _get_document(db: Session, user: User, ai: AI, a: dict[str, Any]) -> Any:
    doc = db.get(Document, int(a["id"]))
    if doc is None or doc.user_id != user.id:
        raise ValueError("document_unknown")
    out = docs.to_dict(doc, with_text=True)
    out["text"] = out["text"][:20000]
    return out


async def _upload_document(db: Session, user: User, ai: AI, a: dict[str, Any]) -> Any:
    name = str(a["filename"])
    if a.get("content_base64"):
        try:
            raw = base64.b64decode(str(a["content_base64"]), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("content_base64 is not valid base64") from exc
    else:
        raw = str(a.get("text") or "").encode()
    try:
        stored = await docs.store(
            db, user, ai.settings.documents_dir, name, raw, ai,
            kind=a.get("kind") or None, account_id=a.get("account_id"), source="mcp",
        )  # fmt: skip
    except docs.DocumentError as exc:
        raise ValueError(exc.code) from exc
    out = docs.to_dict(stored.document)
    out["duplicate"] = stored.duplicate
    out["import"] = stored.outcome.__dict__ if stored.outcome else None
    return out


TOOLS = [
    Tool("get_overview", "reports:read", "Net worth, this month vs usual, subscriptions.", _obj({}), _overview),
    Tool("list_accounts", "accounts:read", "Accounts with balances (decimal strings, own currency).", _obj({}), _accounts),
    Tool("search_transactions", "transactions:read", "Movements, newest first. month=YYYY-MM.",
         _obj({"text": {"type": "string"}, "month": {"type": "string"}, "category": {"type": "string"},
               "limit": {"type": "integer"}}), _transactions),
    Tool("add_transaction", "transactions:write", "Record a movement (negative amount = money out).",
         _obj({"account_id": {"type": "integer"}, "day": {"type": "string", "format": "date"},
               "amount": {"type": "string"}, "description": {"type": "string"},
               "category": {"type": "string"}}, ["account_id", "day", "amount", "description"]), _add_transaction),
    Tool("get_financial_picture", "reports:read", "The whole picture: liquidity, emergency fund, "
         "investments, plans, fixed costs, lifestyle, risk profile.", _obj({}), _picture),
    Tool("list_holdings", "accounts:read", "Investments priced at market, in the base currency.", _obj({}), _holdings),
    Tool("list_fixed_costs", "reports:read", "Fixed costs and their monthly total.", _obj({}), _fixed),
    Tool("simulate_plan", "reports:read", "Backtest + Monte Carlo of investing in a symbol (Yahoo "
         "style, e.g. VWCE.DE, BTC-EUR): initial capital and/or a monthly amount.",
         _obj({"symbol": {"type": "string"}, "initial": {"type": "number"}, "monthly": {"type": "number"},
               "years": {"type": "integer"}}, ["symbol"]), _simulate),
    Tool("ask_oink", "ai:ask", "Ask Oink's AI about the user's money.", _obj({"question": {"type": "string"}}, ["question"]), _ask),
    Tool("list_documents", "documents:read", "The user's financial documents (statements, payslips, "
         "contracts, invoices, tax, insurance, investment papers), newest first; kind filters, query "
         "searches their text.", _obj({"kind": {"type": "string", "enum": list(docs.KINDS)},
                                       "query": {"type": "string"}, "limit": {"type": "integer"}}), _documents),
    Tool("search_documents", "documents:read", "Full-text search in the documents: the matching passages "
         "with their document.", _obj({"query": {"type": "string"}, "kind": {"type": "string"},
                                      "limit": {"type": "integer"}}, ["query"]), _search_documents),
    Tool("ask_documents", "documents:read", "Answer a question from the documents (e.g. «net pay in "
         "October?», «when can I cancel the gym contract?»), citing them.",
         _obj({"question": {"type": "string"}}, ["question"]), _ask_documents),
    Tool("get_document", "documents:read", "One document with its text (first 20k characters).",
         _obj({"id": {"type": "integer"}}, ["id"]), _get_document),
    Tool("upload_document", "documents:write", "File a document (PDF, CSV, OFX, image, text) as base64 "
         "or plain text. Statements are imported into their account too.",
         _obj({"filename": {"type": "string"}, "content_base64": {"type": "string"}, "text": {"type": "string"},
               "kind": {"type": "string", "enum": list(docs.KINDS)}, "account_id": {"type": "integer"}},
              ["filename"]), _upload_document),
]  # fmt: skip
BY_NAME = {t.name: t for t in TOOLS}


def _reply(id_: Any, result: Any = None, error: tuple[int, str] | None = None) -> dict[str, Any]:
    if error:
        return {"jsonrpc": "2.0", "id": id_, "error": {"code": error[0], "message": error[1]}}
    return {"jsonrpc": "2.0", "id": id_, "result": result}


@router.get("/mcp", include_in_schema=False)
def mcp_get() -> Response:
    return Response(status_code=405, headers={"Allow": "POST"})


@router.post("/mcp", include_in_schema=False)
async def mcp(request: Request, db: Session = Depends(get_db)) -> Response:
    auth = request.headers.get("authorization", "")
    found = token_for(db, auth[7:].strip()) if auth.lower().startswith("bearer ") else None
    if found is None:
        return JSONResponse({"error": "token_invalid"}, 401, headers={"WWW-Authenticate": "Bearer"})
    token, user = found
    try:
        message = await request.json()
    except ValueError:
        return JSONResponse(_reply(None, error=(-32700, "parse error")), 400)
    batch = message if isinstance(message, list) else [message]
    ai: AI = request.app.state.ai
    out = []
    for msg in batch:
        method, id_ = msg.get("method"), msg.get("id")
        if id_ is None:  # notification
            continue
        if method == "initialize":
            out.append(_reply(id_, {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                                    "serverInfo": {"name": "oink", "version": current_version()},
                                    "instructions": "Personal finance of the signed-in Oink user. "
                                    "Amounts are decimal strings in each account's currency."}))  # fmt: skip
        elif method == "ping":
            out.append(_reply(id_, {}))
        elif method == "tools/list":
            tools = [t for t in TOOLS if t.scope in token.scopes]
            out.append(_reply(id_, {"tools": [{"name": t.name, "description": t.description,
                                              "inputSchema": t.schema} for t in tools]}))  # fmt: skip
        elif method == "tools/call":
            params = msg.get("params") or {}
            tool = BY_NAME.get(str(params.get("name")))
            if tool is None or tool.scope not in token.scopes:
                out.append(_reply(id_, error=(-32602, "unknown tool or missing scope")))
                continue
            try:
                data = await tool.run(db, user, ai, params.get("arguments") or {})
                text = (
                    data
                    if isinstance(data, str)
                    else json.dumps(data, ensure_ascii=False, default=str)
                )
                out.append(
                    _reply(id_, {"content": [{"type": "text", "text": text}], "isError": False})
                )
            except (ValueError, KeyError, TypeError) as exc:
                out.append(
                    _reply(
                        id_,
                        {"content": [{"type": "text", "text": f"error: {exc}"}], "isError": True},
                    )
                )
        else:
            out.append(_reply(id_, error=(-32601, f"method not found: {method}")))
    if not out:
        return Response(status_code=202)
    return JSONResponse(out if isinstance(message, list) else out[0])
