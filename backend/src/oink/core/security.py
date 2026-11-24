"""Who may do what.

- People sign in with username and password: a session cookie (HttpOnly, SameSite=Strict).
  The first user is the admin; the admin invites the others with one-time links.
- Other apps (an assistant…): `Authorization: Bearer oink_...` tokens, each acting for one
  user with fine-grained scopes.

Cookies, tokens and invites are random 256-bit values; only their SHA-256 is stored.
Credentials of linked accounts (API keys) are encrypted with the instance key.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from oink.core.db import ApiToken, Invite, LoginSession, User, aware

COOKIE = "oink_session"
TOKEN_PREFIX = "oink_"
MIN_PASSWORD = 10
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,39}$")
NEVER = datetime(1970, 1, 1, tzinfo=UTC)

#: Every scope a token can carry. Reads and writes are separate on purpose: an assistant
#: that only reports needs no write access. Labels are translated by the web app.
SCOPES: tuple[str, ...] = (
    "accounts:read",
    "accounts:write",
    "transactions:read",
    "transactions:write",
    "imports:write",
    "requests:read",
    "requests:write",
    "reports:read",
    "reports:write",
    "ai:ask",
    "connections:read",
    "connections:write",
)

_hasher = PasswordHasher()


class AuthError(ValueError):
    """A user-facing problem; `code` is translated by the web app."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD:
        raise AuthError("password_too_short")
    return _hasher.hash(password)


def verify_password(stored: str, password: str) -> bool:
    try:
        return bool(stored) and _hasher.verify(stored, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


# -- users -----------------------------------------------------------------------------
def user_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User)) or 0


def clean_username(username: str) -> str:
    name = username.strip().lower()
    if not USERNAME.match(name):
        raise AuthError("username_invalid")
    return name


def create_user(
    db: Session,
    username: str,
    password: str | None,
    *,
    admin: bool = False,
    name: str = "",
    language: str = "",
    base_currency: str = "EUR",
) -> User:
    username = clean_username(username)
    if db.scalar(select(User).where(User.username == username)) is not None:
        raise AuthError("username_taken")
    user = User(
        username=username,
        name=name.strip()[:80] or username,
        password_hash=hash_password(password) if password else "",
        is_admin=admin,
        language=language,
        base_currency=base_currency,
    )
    db.add(user)
    db.commit()
    return user


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username.strip().lower()))
    if user is None or user.disabled:
        _hasher.hash("timing-equaliser")  # same cost whether the user exists or not
        return None
    return user if verify_password(user.password_hash, password) else None


def set_password(db: Session, user: User, password: str) -> None:
    user.password_hash = hash_password(password)
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    db.commit()


# -- invites ---------------------------------------------------------------------------
def create_invite(db: Session, user: User, days: int = 7) -> str:
    secret = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(days=days)
    db.add(Invite(hash=sha256(secret), user_id=user.id, expires=expires))
    db.commit()
    return secret


def invite_user(db: Session, secret: str) -> tuple[Invite, User] | None:
    invite = db.scalar(select(Invite).where(Invite.hash == sha256(secret)))
    if invite is None or invite.used or (aware(invite.expires) or NEVER) < datetime.now(UTC):
        return None
    user = db.get(User, invite.user_id)
    return (invite, user) if user is not None and not user.disabled else None


# -- sessions --------------------------------------------------------------------------
def new_session(db: Session, user: User, days: int) -> str:
    cookie = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(days=days)
    db.add(LoginSession(id=sha256(cookie), user_id=user.id, expires=expires))
    db.commit()
    return cookie


def session_user(db: Session, cookie: str | None) -> User | None:
    if not cookie:
        return None
    row = db.get(LoginSession, sha256(cookie))
    if row is None or (aware(row.expires) or NEVER) <= datetime.now(UTC):
        return None
    user = db.get(User, row.user_id)
    return None if user is None or user.disabled else user


def end_session(db: Session, cookie: str | None) -> None:
    if cookie and (row := db.get(LoginSession, sha256(cookie))) is not None:
        db.delete(row)
        db.commit()


# -- API tokens ------------------------------------------------------------------------
def create_token(db: Session, user: User, name: str, scopes: list[str]) -> tuple[ApiToken, str]:
    if set(scopes) - set(SCOPES):
        raise AuthError("scope_unknown")
    if not scopes:
        raise AuthError("scope_missing")
    secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = ApiToken(
        user_id=user.id,
        name=name.strip()[:80] or "token",
        prefix=secret[:12],
        hash=sha256(secret),
        scopes=sorted(set(scopes)),
    )
    db.add(token)
    db.commit()
    return token, secret


def token_for(db: Session, secret: str) -> tuple[ApiToken, User] | None:
    if not secret.startswith(TOKEN_PREFIX):
        return None
    token = db.scalar(select(ApiToken).where(ApiToken.hash == sha256(secret)))
    if token is None or token.revoked:
        return None
    user = db.get(User, token.user_id)
    if user is None or user.disabled:
        return None
    token.last_used = datetime.now(UTC)
    db.commit()
    return token, user


# -- secrets at rest -------------------------------------------------------------------
class Vault:
    """Encrypts linked-account credentials. The key is `OINK_SECRET_KEY` or, if unset, a key
    generated once in the data folder (keep that folder's backups private)."""

    def __init__(self, key: str, data_dir: Path) -> None:
        if not key:
            path = data_dir / "secret.key"
            if not path.is_file():
                path.write_text(Fernet.generate_key().decode())
                os.chmod(path, 0o600)
            key = path.read_text().strip()
        else:  # any passphrase → a Fernet key
            key = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()).decode()
        self._fernet = Fernet(key.encode())

    def seal(self, data: dict[str, Any]) -> str:
        return self._fernet.encrypt(json.dumps(data).encode()).decode()

    def open(self, sealed: str) -> dict[str, Any]:
        if not sealed:
            return {}
        try:
            value = json.loads(self._fernet.decrypt(sealed.encode()))
        except (InvalidToken, ValueError) as exc:
            raise AuthError("secret_unreadable") from exc
        return value if isinstance(value, dict) else {}
