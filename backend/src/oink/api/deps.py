"""Request-level dependencies: the database session and the permission check."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from oink.core.security import COOKIE, SCOPES, session_valid, token_for


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.db.sessions() as session:
        yield session


@dataclass
class Caller:
    owner: bool  # the owner in the browser: every scope
    scopes: frozenset[str]
    token_name: str = ""


def caller(request: Request, db: Session = Depends(get_db)) -> Caller:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = token_for(db, auth[7:].strip())
        if token is None:
            raise HTTPException(401, "token non valido o revocato")
        return Caller(owner=False, scopes=frozenset(token.scopes), token_name=token.name)
    if session_valid(db, request.cookies.get(COOKIE)):
        return Caller(owner=True, scopes=frozenset(SCOPES))
    raise HTTPException(401, "accesso richiesto")


def require(scope: str) -> Callable[[Caller], Caller]:
    assert scope in SCOPES, scope

    def check(who: Caller = Depends(caller)) -> Caller:
        if scope not in who.scopes:
            raise HTTPException(403, f"permesso mancante: {scope}")
        return who

    return check


def owner_only(who: Caller = Depends(caller)) -> Caller:
    """Things a token must never do (manage tokens, the password)."""
    if not who.owner:
        raise HTTPException(403, "solo dal browser del proprietario")
    return who
