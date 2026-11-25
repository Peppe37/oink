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

from oink.api.admin import ai_store
from oink.api.admin import router as admin_router
from oink.api.mcp import router as mcp_router
from oink.api.planning import router as planning_router
from oink.api.routes import api
from oink.core.config import Settings, get_settings
from oink.core.db import Database
from oink.core.oidc import OIDC
from oink.core.security import Vault, create_user, user_count
from oink.services import jobs, updates
from oink.services.ai import AI

STATIC = Path(
    os.environ.get("OINK_STATIC_DIR", Path(__file__).resolve().parents[3] / "frontend" / "dist")
)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data: https:; "
    "style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'self'; form-action 'self'",
}


def create_app(settings: Settings | None = None, *, background: bool = True) -> FastAPI:
    settings = settings or get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_url)
    db.create(settings.admin_username)
    vault = Vault(settings.secret_key, settings.data_dir)
    with db.sessions() as session:
        if user_count(session) == 0 and settings.admin_password:
            create_user(
                session,
                settings.admin_username,
                settings.admin_password,
                admin=True,
                base_currency=settings.currency,
            )

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = (
            asyncio.create_task(jobs.loop(db.sessions, settings, app.state.ai, vault))
            if background
            else None
        )
        yield
        if task is not None:
            task.cancel()

    app = FastAPI(
        title="Oink",
        version=updates.current_version(),
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings, app.state.db = settings, db
    app.state.ai = AI(settings, ai_store(db.sessions, vault))
    app.state.vault = vault
    app.state.oidc = OIDC(settings)
    app.include_router(api)
    app.include_router(planning_router)
    app.include_router(admin_router)
    app.include_router(mcp_router)

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
            if path.startswith("api/") or path == "mcp":
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
