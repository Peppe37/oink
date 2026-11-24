"""The application: API + the built web app, one process, one container."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from oink.api.routes import api
from oink.core.config import Settings, get_settings
from oink.core.db import Database
from oink.core.security import owner_password, set_owner_password
from oink.services import jobs
from oink.services.ai import AI

STATIC = Path(
    os.environ.get("OINK_STATIC_DIR", Path(__file__).resolve().parents[3] / "frontend" / "dist")
)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data: https:; style-src 'self' "
    "'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
    "connect-src 'self'; frame-ancestors 'none'",
}


def create_app(settings: Settings | None = None, *, background: bool = True) -> FastAPI:
    settings = settings or get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_url)
    db.create()
    with db.sessions() as session:
        if owner_password(session) is None and settings.admin_password:
            set_owner_password(session, settings.admin_password)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = (
            asyncio.create_task(jobs.loop(db.sessions, settings, app.state.ai))
            if background
            else None
        )
        yield
        if task is not None:
            task.cancel()

    app = FastAPI(
        title="Oink",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings, app.state.db, app.state.ai = settings, db, AI(settings)
    app.include_router(api)

    @app.middleware("http")
    async def headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    if STATIC.is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False, response_model=None)
        async def spa(path: str) -> FileResponse | JSONResponse:
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            file = (STATIC / path).resolve()
            if path and file.is_file() and STATIC.resolve() in file.parents:
                return FileResponse(file)
            return FileResponse(STATIC / "index.html")

    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        create_app(),
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("OINK_TRUSTED_PROXIES", "127.0.0.1"),
    )
