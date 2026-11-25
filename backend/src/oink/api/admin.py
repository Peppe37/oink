"""Sign-in with SSO (OpenID Connect) and the AI settings the admin manages in the app."""

from __future__ import annotations

import re
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from oink.api.deps import Caller, admin_only, browser_only, get_db
from oink.core.config import Settings
from oink.core.db import Setting, User
from oink.core.oidc import OIDC, OIDCError
from oink.core.security import COOKIE, Vault, new_session, user_count
from oink.services.ai import AI, AI_KEYS, PROVIDERS, AIConfig, AIError

router = APIRouter(prefix="/api")


# -- AI settings stored in the app ----------------------------------------------------------
def ai_store(sessions: sessionmaker[Session], vault: Vault) -> Any:
    def load() -> AIConfig | None:
        with sessions() as db:
            rows = {
                r.key: r.value for r in db.scalars(select(Setting).where(Setting.key.in_(AI_KEYS)))
            }
        if not rows.get("ai.model"):
            return None
        key = ""
        if rows.get("ai.api_key"):
            try:
                key = str(vault.open(rows["ai.api_key"]).get("key", ""))
            except Exception:  # a broken key must not break the app
                key = ""
        return AIConfig(rows["ai.model"], rows.get("ai.base_url", ""), key,
                        rows.get("ai.send_descriptions", "true") == "true", "app")  # fmt: skip

    return load


class AISettings(BaseModel):
    model: str = Field(min_length=3, max_length=120)
    base_url: str = Field(default="", max_length=300)
    api_key: str | None = Field(default=None, max_length=500)  # None = keep the saved one
    send_descriptions: bool = True


def _ai(request: Request) -> AI:
    ai: AI = request.app.state.ai
    return ai


@router.get("/system/ai", dependencies=[Depends(admin_only)])
def get_ai(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    cfg = _ai(request).config()
    saved = db.get(Setting, "ai.api_key")
    return {"source": cfg.source if cfg.model else "off", "model": cfg.model,
            "base_url": cfg.base_url, "has_key": bool(saved and saved.value),
            "send_descriptions": cfg.send_descriptions, "providers": PROVIDERS}  # fmt: skip


def _put(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


@router.put("/system/ai", dependencies=[Depends(admin_only)])
def set_ai(body: AISettings, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    vault: Vault = request.app.state.vault
    _put(db, "ai.model", body.model.strip())
    _put(db, "ai.base_url", body.base_url.strip())
    _put(db, "ai.send_descriptions", "true" if body.send_descriptions else "false")
    if body.api_key is not None:
        _put(
            db,
            "ai.api_key",
            vault.seal({"key": body.api_key.strip()}) if body.api_key.strip() else "",
        )
    db.commit()
    return get_ai(request, db)


@router.delete("/system/ai", dependencies=[Depends(admin_only)])
def reset_ai(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Forget the app settings: back to the ones in .env (if any)."""
    for key in AI_KEYS:
        if (row := db.get(Setting, key)) is not None:
            db.delete(row)
    db.commit()
    return get_ai(request, db)


@router.post("/system/ai/test", dependencies=[Depends(admin_only)])
async def test_ai(request: Request) -> dict[str, str]:
    try:
        reply = await _ai(request).complete("Reply with the single word OK.", "ping")
    except AIError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"reply": reply[:80]}


# -- SSO -----------------------------------------------------------------------------------
def _oidc(request: Request) -> OIDC:
    oidc: OIDC = request.app.state.oidc
    if not oidc.enabled:
        raise HTTPException(404, "sso_off")
    return oidc


@router.get("/auth/oidc/start", include_in_schema=False)
async def oidc_start(
    request: Request, link: bool = False, db: Session = Depends(get_db)
) -> RedirectResponse:
    from oink.core.security import session_user

    oidc = _oidc(request)
    link_user = None
    if link:
        user = session_user(db, request.cookies.get(COOKIE))
        if user is None:
            raise HTTPException(401, "login_required")
        link_user = user.id
    try:
        return RedirectResponse(await oidc.start(link_user), 303)
    except OIDCError as exc:
        raise HTTPException(502, str(exc)) from exc


def _username(claims: dict[str, Any], db: Session) -> str:
    base = str(
        claims.get("preferred_username") or str(claims.get("email", "")).split("@")[0] or "user"
    )
    base = re.sub(r"[^a-z0-9._-]", "", base.lower())[:32] or "user"
    if len(base) < 2:
        base = f"user{base}"
    name, n = base, 1
    while db.scalar(select(User).where(User.username == name)) is not None:
        n += 1
        name = f"{base}{n}"
    return name


@router.get("/auth/oidc/callback", include_in_schema=False)
async def oidc_callback(
    request: Request,
    state: str = "",
    code: str = "",
    error: str = "",
    db: Session = Depends(get_db),
) -> RedirectResponse:
    oidc = _oidc(request)
    settings: Settings = request.app.state.settings
    if error or not code:
        return RedirectResponse(f"/?sso=error&reason={error or 'code'}", 303)
    try:
        claims, link_user = await oidc.finish(state, code)
    except OIDCError as exc:
        return RedirectResponse(f"/?sso=error&reason={exc}", 303)
    sub = str(claims["sub"])
    user = db.scalar(select(User).where(User.oidc_sub == sub))
    if link_user is not None:
        target = db.get(User, link_user)
        if target is None or (user is not None and user.id != target.id):
            return RedirectResponse("/settings?sso=taken", 303)
        target.oidc_sub, target.email = sub, str(claims.get("email") or target.email)
        db.commit()
        return RedirectResponse("/settings?sso=linked", 303)
    if user is None:
        if not settings.oidc_auto_create and user_count(db) > 0:
            return RedirectResponse("/?sso=error&reason=no_account", 303)
        user = User(
            username=_username(claims, db),
            name=str(claims.get("name") or claims.get("given_name") or "")[:80],
            is_admin=oidc.is_admin(claims) or user_count(db) == 0,
            base_currency=settings.currency,
            oidc_sub=sub,
            email=str(claims.get("email") or ""),
            password_hash="",
        )
        db.add(user)
        db.commit()
    elif user.disabled:
        return RedirectResponse("/?sso=error&reason=disabled", 303)
    elif oidc.is_admin(claims) and not user.is_admin:
        user.is_admin = True
        db.commit()
    cookie = new_session(db, user, settings.session_days)
    response = RedirectResponse("/", 303)
    response.set_cookie(COOKIE, cookie, max_age=settings.session_days * 86400, httponly=True,
                        samesite="lax", secure=settings.public_url.startswith("https"))  # fmt: skip
    return response


@router.post("/auth/oidc/unlink")
def oidc_unlink(
    who: Caller = Depends(browser_only), db: Session = Depends(get_db)
) -> dict[str, bool]:
    user = db.get(User, who.user_id)
    assert user is not None
    if not user.password_hash:
        raise HTTPException(422, "set_password_first")
    user.oidc_sub = None
    db.commit()
    return {"ok": True}


_ = secrets
