"""The documents engine and its stores, each optional and each reachable by URL, so they
can be the bundled containers or shared instances of the homelab (Oink keeps to its own
Qdrant collection, its own FalkorDB graph and its own Redis key prefix).

- `Engine`  : Docling (reading) + BGE-M3 (dense + sparse vectors), see `engine/app.py`.
- `Vectors` : Qdrant, hybrid search (dense + sparse, fused by rank), every point tagged
              with its user and document, every query filtered by user.
- `Cache`   : Redis, for embeddings of chunks and questions and for extractions.
- `Graph`   : FalkorDB, who issued what, which account a document is about, where money
              goes: context for the chat and the reviews.

Everything degrades: a store that is down is skipped (logged), never a failed upload."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from oink.core.config import Settings

log = logging.getLogger("oink.stores")


class StoreError(RuntimeError):
    pass


# -- engine ------------------------------------------------------------------------------
class Engine:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.url = settings.engine_url.rstrip("/")
        self._headers = (
            {"Authorization": f"Bearer {settings.engine_token}"} if settings.engine_token else {}
        )
        self._transport = transport
        self.timeout = float(settings.engine_timeout)

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    async def cancel(self) -> None:
        """Stop the reading in progress (the engine drops Docling and reloads it later)."""
        with contextlib.suppress(StoreError):
            await self._post("/cancel", 30)

    async def _post(self, path: str, timeout: float, **kwargs: Any) -> Any:
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            try:
                reply = await client.post(f"{self.url}{path}", headers=self._headers, **kwargs)
            except httpx.HTTPError as exc:
                raise StoreError(f"engine_unreachable:{type(exc).__name__}") from exc
        if reply.status_code >= 400:
            raise StoreError(f"engine_{reply.status_code}")
        return reply.json()

    async def extract(self, filename: str, raw: bytes) -> dict[str, Any]:
        """{"markdown", "text", "tables": [{"header", "rows", "page"}], "pages"}"""
        data: dict[str, Any] = await self._post(
            "/extract", self.timeout, files={"file": (filename, raw)}
        )
        return data

    async def embed(self, texts: list[str]) -> list[tuple[list[float], dict[str, list[Any]]]]:
        data = await self._post("/embed", 600, json={"texts": texts})
        return list(zip(data["dense"], data["sparse"], strict=True))


# -- cache -------------------------------------------------------------------------------
class Cache:
    def __init__(self, settings: Settings) -> None:
        self.prefix = settings.cache_prefix
        self._client: Any = None
        if settings.redis_url:
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(settings.redis_url, socket_timeout=3)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @staticmethod
    def key_of(*parts: str) -> str:
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:40]

    async def get(self, kind: str, key: str) -> Any:
        if self._client is None:
            return None
        try:
            raw = await self._client.get(f"{self.prefix}{kind}:{key}")
        except Exception as exc:  # a cache never breaks the app
            log.info("cache get failed: %s", type(exc).__name__)
            return None
        return None if raw is None else json.loads(raw)

    async def set(self, kind: str, key: str, value: Any, ttl: int = 30 * 86400) -> None:
        if self._client is None:
            return
        try:
            await self._client.set(f"{self.prefix}{kind}:{key}", json.dumps(value), ex=ttl)
        except Exception as exc:
            log.info("cache set failed: %s", type(exc).__name__)


# -- vectors -----------------------------------------------------------------------------
class Vectors:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.url = settings.qdrant_url.rstrip("/")
        self.collection = settings.qdrant_collection
        key = settings.qdrant_key
        # A JWT (three dot-separated parts) goes as Bearer; a plain API key as api-key.
        if key.count(".") == 2:
            self._headers = {"Authorization": f"Bearer {key}"}
        else:
            self._headers = {"api-key": key} if key else {}
        self._transport = transport
        self._ready = False

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    async def _call(self, method: str, path: str, body: Any = None) -> Any:
        async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
            try:
                reply = await client.request(
                    method,
                    f"{self.url}/collections/{self.collection}{path}",
                    headers=self._headers,
                    json=body,
                )
            except httpx.HTTPError as exc:
                raise StoreError(f"qdrant_unreachable:{type(exc).__name__}") from exc
        if reply.status_code == 404 and path == "":
            return None
        if reply.status_code >= 400:
            raise StoreError(f"qdrant_{reply.status_code}:{reply.text[:160]}")
        return reply.json().get("result")

    async def ensure(self) -> None:
        """Create the collection when Oink may (bundled Qdrant); a shared instance has it."""
        if self._ready:
            return
        if await self._call("GET", "") is None:
            await self._call("PUT", "", {
                "vectors": {"dense": {"size": 1024, "distance": "Cosine", "on_disk": True}},
                "sparse_vectors": {"sparse": {"modifier": "idf"}}, "on_disk_payload": True,
            })  # fmt: skip
            for name in ("user_id", "document_id"):
                await self._call("PUT", "/index", {"field_name": name, "field_schema": "integer"})
        self._ready = True

    async def upsert(self, points: list[dict[str, Any]]) -> None:
        await self.ensure()
        for start in range(0, len(points), 64):
            await self._call("PUT", "/points?wait=true", {"points": points[start : start + 64]})

    async def delete_document(self, document_id: int) -> None:
        await self.ensure()
        await self._call("POST", "/points/delete?wait=true", {
            "filter": {"must": [{"key": "document_id", "match": {"value": document_id}}]}
        })  # fmt: skip

    async def search(
        self, user_id: int, dense: list[float], sparse: dict[str, list[Any]], limit: int,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:  # fmt: skip
        """Hybrid: the best of dense (meaning) and sparse (exact words, numbers, IBANs),
        fused by reciprocal rank; only this user's points."""
        await self.ensure()
        must: list[dict[str, Any]] = [{"key": "user_id", "match": {"value": user_id}}]
        if kind:
            must.append({"key": "kind", "match": {"value": kind}})
        flt = {"must": must}
        result = await self._call("POST", "/points/query", {
            "prefetch": [
                {"query": dense, "using": "dense", "limit": limit * 3, "filter": flt},
                {"query": sparse, "using": "sparse", "limit": limit * 3, "filter": flt},
            ],
            "query": {"fusion": "rrf"}, "limit": limit, "with_payload": True, "filter": flt,
        })  # fmt: skip
        return [
            dict(p.get("payload") or {}, score=p.get("score"))
            for p in (result or {}).get("points", [])
        ]


# -- graph -------------------------------------------------------------------------------
_SAFE = re.compile(r"[^\w .,'&@/-]", re.UNICODE)


def _lit(value: Any) -> str:
    """A Cypher literal (strings escaped; FalkorDB's parameters are text anyway)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    text = _SAFE.sub("", str(value))[:200].replace("\\", "").replace("'", "\\'")
    return f"'{text}'"


class Graph:
    def __init__(self, settings: Settings) -> None:
        self.name = settings.graph_name
        self._client: Any = None
        if settings.falkordb_url:
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(settings.falkordb_url, socket_timeout=5)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def query(self, cypher: str) -> list[list[Any]]:
        if self._client is None:
            return []
        try:
            reply = await self._client.execute_command(
                "GRAPH.QUERY", self.name, cypher, "--compact"
            )
        except Exception as exc:
            log.info("graph query failed: %s", type(exc).__name__)
            return []
        return _rows(reply)

    async def run(self, *statements: str) -> None:
        for cypher in statements:
            await self.query(cypher)

    async def file_document(
        self, user_id: int, doc: dict[str, Any], account: dict[str, Any] | None
    ) -> None:
        """(:User)-[:OWNS]->(:Document)-[:ISSUED_BY]->(:Org), -[:ABOUT]->(:Account)-[:AT]->(:Org)."""
        u, d = _lit(user_id), _lit(doc["id"])
        statements = [
            f"MERGE (u:User {{id: {u}}}) MERGE (d:Document {{id: {d}}}) "
            f"SET d.user_id = {u}, d.kind = {_lit(doc['kind'])}, d.title = {_lit(doc['title'])}, "
            f"d.date = {_lit(doc.get('date'))} MERGE (u)-[:OWNS]->(d)",
        ]
        if doc.get("issuer"):
            statements.append(
                f"MATCH (d:Document {{id: {d}}}) MERGE (o:Org {{user_id: {u}, name: {_lit(doc['issuer'].lower())}}}) "
                f"SET o.label = {_lit(doc['issuer'])} MERGE (d)-[:ISSUED_BY]->(o)"
            )
        if account:
            a = _lit(account["id"])
            statements.append(
                f"MATCH (d:Document {{id: {d}}}) MERGE (a:Account {{id: {a}}}) SET a.user_id = {u}, "
                f"a.name = {_lit(account['name'])} MERGE (d)-[:ABOUT]->(a)"
            )
            if account.get("institution"):
                statements.append(
                    f"MATCH (a:Account {{id: {a}}}) MERGE (o:Org {{user_id: {u}, name: {_lit(account['institution'].lower())}}}) "
                    f"SET o.label = {_lit(account['institution'])} MERGE (a)-[:AT]->(o)"
                )
        for key, value in (doc.get("facts") or {}).items():
            if key in ("employer", "counterparty", "provider") and isinstance(value, str) and value:
                statements.append(
                    f"MATCH (d:Document {{id: {d}}}) MERGE (o:Org {{user_id: {u}, name: {_lit(value.lower())}}}) "
                    f"SET o.label = {_lit(value)} MERGE (d)-[:MENTIONS {{as: {_lit(key)}}}]->(o)"
                )
        await self.run(*statements)

    async def forget_document(self, document_id: int) -> None:
        await self.query(f"MATCH (d:Document {{id: {_lit(document_id)}}}) DETACH DELETE d")

    async def related(self, user_id: int, words: list[str]) -> list[int]:
        """Documents linked to an organisation or account named in the question."""
        if not words:
            return []
        conds = " OR ".join(
            f"toLower(coalesce(o.name, '')) CONTAINS {_lit(w.lower())}" for w in words[:8]
        )
        rows = await self.query(
            f"MATCH (d:Document {{user_id: {_lit(user_id)}}})-[*1..2]-(o) "
            f"WHERE (o:Org OR o:Account) AND ({conds}) RETURN DISTINCT d.id LIMIT 20"
        )
        return [int(r[0]) for r in rows if r and isinstance(r[0], (int, float))]


def _rows(reply: Any) -> list[list[Any]]:
    """Compact GRAPH.QUERY reply → plain rows of scalars."""
    if not isinstance(reply, list) or len(reply) < 3:
        return []
    out = []
    for row in reply[1]:
        values = []
        for cell in row:
            if isinstance(cell, list) and len(cell) == 2:
                values.append(cell[1].decode() if isinstance(cell[1], bytes) else cell[1])
            else:
                values.append(cell)
        out.append(values)
    return out


@dataclass
class Stores:
    engine: Engine
    vectors: Vectors
    cache: Cache
    graph: Graph
    notes: list[str] = field(default_factory=list)

    @classmethod
    def of(cls, settings: Settings) -> Stores:
        return cls(Engine(settings), Vectors(settings), Cache(settings), Graph(settings))

    @property
    def hybrid(self) -> bool:
        return self.engine.enabled and self.vectors.enabled
