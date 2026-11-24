"""Who may do what. Two kinds of callers:

- the owner in the browser: a session cookie (HttpOnly, SameSite=Strict), every scope;
- other apps (Jarvis...): `Authorization: Bearer oink_...` tokens with fine-grained scopes.

Tokens and cookies are random 256-bit values; only their SHA-256 is stored.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from oink.core.db import ApiToken, LoginSession, Setting

COOKIE = "oink_session"
TOKEN_PREFIX = "oink_"
PASSWORD_KEY = "owner_password"

#: Every scope a token can carry. Reads and writes are separate on purpose: an assistant
#: that only reports needs no write access.
SCOPES: dict[str, str] = {
    "accounts:read": "Vedere conti e saldi",
    "accounts:write": "Creare e modificare conti",
    "transactions:read": "Vedere i movimenti",
    "transactions:write": "Aggiungere, correggere e categorizzare movimenti",
    "imports:write": "Caricare estratti conto",
    "requests:read": "Vedere le richieste di estratto conto",
    "requests:write": "Chiudere o saltare le richieste di estratto conto",
    "reports:read": "Leggere resoconti e statistiche",
    "reports:write": "Generare resoconti con l'IA",
    "ai:ask": "Fare domande all'IA sulle tue finanze",
    "banks:read": "Vedere i collegamenti bancari",
    "banks:write": "Avviare sincronizzazioni bancarie",
}

_hasher = PasswordHasher()


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored: str, password: str) -> bool:
    try:
        return _hasher.verify(stored, password)
    except VerifyMismatchError:
        return False


def owner_password(db: Session) -> str | None:
    row = db.get(Setting, PASSWORD_KEY)
    return row.value if row else None


def set_owner_password(db: Session, password: str) -> None:
    if len(password) < 10:
        raise ValueError("La password deve avere almeno 10 caratteri.")
    row = db.get(Setting, PASSWORD_KEY)
    if row is None:
        db.add(Setting(key=PASSWORD_KEY, value=hash_password(password)))
    else:
        row.value = hash_password(password)
    db.commit()


def new_session(db: Session, days: int) -> str:
    cookie = secrets.token_urlsafe(32)
    db.add(LoginSession(id=sha256(cookie), expires=datetime.now(UTC) + timedelta(days=days)))
    db.commit()
    return cookie


def session_valid(db: Session, cookie: str | None) -> bool:
    if not cookie:
        return False
    row = db.get(LoginSession, sha256(cookie))
    if row is None:
        return False
    expires = row.expires if row.expires.tzinfo else row.expires.replace(tzinfo=UTC)
    return expires > datetime.now(UTC)


def end_session(db: Session, cookie: str | None) -> None:
    if cookie and (row := db.get(LoginSession, sha256(cookie))) is not None:
        db.delete(row)
        db.commit()


def create_token(db: Session, name: str, scopes: list[str]) -> tuple[ApiToken, str]:
    unknown = sorted(set(scopes) - set(SCOPES))
    if unknown:
        raise ValueError(f"Permessi sconosciuti: {', '.join(unknown)}")
    if not scopes:
        raise ValueError("Scegli almeno un permesso.")
    secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = ApiToken(
        name=name.strip()[:80] or "token",
        prefix=secret[:12],
        hash=sha256(secret),
        scopes=sorted(set(scopes)),
    )
    db.add(token)
    db.commit()
    return token, secret


def token_for(db: Session, secret: str) -> ApiToken | None:
    if not secret.startswith(TOKEN_PREFIX):
        return None
    token = db.scalar(select(ApiToken).where(ApiToken.hash == sha256(secret)))
    if token is None or token.revoked:
        return None
    token.last_used = datetime.now(UTC)
    db.commit()
    return token
