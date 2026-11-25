"""Oink engine: document reading (Docling) and embeddings (BGE-M3), nothing else.

No generative AI runs here. Two models, loaded only when a request needs them and dropped
from memory after IDLE_SECONDS without use (default 5 minutes): the container idles at a
few tens of MB and takes a few seconds to load a model again.

- POST /extract   (multipart file)       → markdown, plain text, tables (rows of cells), pages
- POST /embed     {"texts": [...]}       → BGE-M3 dense (1024) + sparse (lexical weights)
- GET  /health                           → which models are in memory

Auth: `Authorization: Bearer $ENGINE_TOKEN` (the engine is meant for the compose network)."""

from __future__ import annotations

import asyncio
import ctypes
import gc
import logging
import multiprocessing as mp
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

IDLE_SECONDS = int(os.environ.get("ENGINE_IDLE_SECONDS", "300"))
MAX_TOKENS = int(os.environ.get("ENGINE_MAX_TOKENS", "1024"))
TOKEN = os.environ.get("ENGINE_TOKEN", "")
OCR = os.environ.get("ENGINE_OCR", "on") != "off"
OCR_LANGS = [x for x in os.environ.get("ENGINE_OCR_LANGS", "it,en,de,fr,es").split(",") if x]
THREADS = int(os.environ.get("ENGINE_THREADS", str(max(1, (os.cpu_count() or 2) - 1))))

log = logging.getLogger("oink.engine")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _trim() -> None:
    """Give freed memory back to the OS (glibc keeps it otherwise)."""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:
        pass


class Lazy:
    """A model in its own child process, started on first use and stopped after
    `IDLE_SECONDS` without use: stopping the process gives ALL its memory back (PyTorch and
    the libraries included), so the idle container stays at a few tens of MB."""

    def __init__(self, name: str, load: Any) -> None:
        self.name, self._load = name, load
        self._proc: Any = None
        self._conn: Any = None
        self._last = 0.0
        self._lock = threading.Lock()
        self.loads = 0

    def call(self, op: str, payload: Any) -> Any:
        with self._lock:
            if self._proc is None or not self._proc.is_alive():
                started = time.monotonic()
                ctx = mp.get_context("spawn")
                parent, child = ctx.Pipe()
                self._proc = ctx.Process(target=_worker, args=(self._load, child), daemon=True)
                self._proc.start()
                self._conn = parent
                if self._conn.recv() != "ready":
                    raise RuntimeError(f"{self.name} failed to load")
                self.loads += 1
                log.info("%s loaded in %.1fs", self.name, time.monotonic() - started)
            self._conn.send((op, payload))
            ok, result = self._conn.recv()
            self._last = time.monotonic()
        if not ok:
            raise RuntimeError(result)
        return result

    def maybe_unload(self) -> bool:
        with self._lock:
            if self._proc is not None and time.monotonic() - self._last > IDLE_SECONDS:
                self._conn.send(("stop", None))
                self._proc.join(10)
                if self._proc.is_alive():
                    self._proc.kill()
                self._proc = self._conn = None
                _trim()
                log.info("%s unloaded after %ss idle", self.name, IDLE_SECONDS)
                return True
        return False

    @property
    def loaded(self) -> bool:
        return self._proc is not None and self._proc.is_alive()


def _worker(load: Any, conn: Any) -> None:
    """Child process: load the model once, then serve until told to stop."""
    model = load()
    conn.send("ready")
    while True:
        op, payload = conn.recv()
        if op == "stop":
            return
        try:
            conn.send((True, OPS[op](model, payload)))
        except Exception as exc:  # reported to the parent as a failed request
            conn.send((False, f"{type(exc).__name__}: {exc}"[:300]))


def _load_embedder() -> Any:
    import torch
    from FlagEmbedding import BGEM3FlagModel

    torch.set_num_threads(THREADS)
    return BGEM3FlagModel("BAAI/bge-m3", use_fp16=False, devices="cpu")


def _load_converter() -> Any:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        AcceleratorOptions,
        EasyOcrOptions,
        PdfPipelineOptions,
    )
    from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = OCR
    options.ocr_options = EasyOcrOptions(lang=OCR_LANGS)
    options.do_table_structure = True
    options.table_structure_options.do_cell_matching = True
    options.accelerator_options = AcceleratorOptions(num_threads=THREADS, device="cpu")
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=options),
        }
    )


embedder = Lazy("bge-m3", _load_embedder)
converter = Lazy("docling", _load_converter)
work = asyncio.Semaphore(1)  # one heavy job at a time: the CPU is small
app = FastAPI(title="Oink engine", docs_url=None, redoc_url=None)


def auth(authorization: str = Header(default="")) -> None:
    if TOKEN and authorization != f"Bearer {TOKEN}":
        raise HTTPException(401, "token_invalid")


@app.on_event("startup")
async def _janitor() -> None:
    async def loop() -> None:
        while True:
            await asyncio.sleep(30)
            embedder.maybe_unload()
            converter.maybe_unload()

    asyncio.get_running_loop().create_task(loop())


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "loaded": {"bge-m3": embedder.loaded, "docling": converter.loaded},
            "idle_seconds": IDLE_SECONDS, "max_tokens": MAX_TOKENS}  # fmt: skip


class EmbedIn(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=256)


def _embed_with(model: Any, texts: list[str]) -> dict[str, Any]:
    out = model.encode(
        texts, batch_size=8, max_length=MAX_TOKENS, return_dense=True, return_sparse=True,
        return_colbert_vecs=False,
    )  # fmt: skip
    sparse = []
    for weights in out["lexical_weights"]:
        ids = [int(k) for k in weights]
        sparse.append({"indices": ids, "values": [float(weights[k]) for k in weights]})
    return {"dense": [list(map(float, v)) for v in out["dense_vecs"]], "sparse": sparse}


@app.post("/embed", dependencies=[Depends(auth)])
async def embed(body: EmbedIn) -> dict[str, Any]:
    async with work:
        return await asyncio.to_thread(embedder.call, "embed", body.texts)


def _extract_with(converter_: Any, path: str) -> dict[str, Any]:
    doc = converter_.convert(path).document
    tables = []
    for table in doc.tables:
        try:
            frame = table.export_to_dataframe(doc=doc)
        except TypeError:  # older docling
            frame = table.export_to_dataframe()
        header = [str(c).strip() for c in frame.columns]
        rows = [[("" if v is None else str(v)).strip() for v in row] for row in frame.itertuples(index=False)]
        page = table.prov[0].page_no if getattr(table, "prov", None) else None
        tables.append({"header": header, "rows": rows, "page": page})
    return {
        "markdown": doc.export_to_markdown(),
        "text": doc.export_to_text(),
        "tables": tables,
        "pages": len(getattr(doc, "pages", {}) or {}),
    }


@app.post("/extract", dependencies=[Depends(auth)])
async def extract(file: UploadFile = File(...)) -> dict[str, Any]:
    suffix = Path(file.filename or "document.pdf").suffix.lower() or ".pdf"
    raw = await file.read()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"document{suffix}"
        path.write_bytes(raw)
        async with work:
            try:
                return await asyncio.to_thread(converter.call, "extract", str(path))
            except Exception as exc:  # docling raises many kinds
                log.warning("extract failed: %s", type(exc).__name__)
                raise HTTPException(422, f"extract_failed:{type(exc).__name__}") from exc


OPS = {"embed": _embed_with, "extract": _extract_with}  # what a model process can do
