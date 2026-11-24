"""Request-level dependencies: the database session, who is calling, and permissions.
Every data route works on `Caller.user_id`: users never see each other's money."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from oink.core.db import User
from oink.core.security import COOKIE, SCOPES, session_user, token_for


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.db.sessions() as session:
        yield session


@dataclass
class Caller:
    user: User
    browser: bool  # signed in with the password: every scope of that user
    scopes: frozenset[str]
    token_name: str = ""

    @property
    def user_id(self) -> int:
        return self.user.id


def caller(request: Request, db: Session = Depends(get_db)) -> Caller:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        found = token_for(db, auth[7:].strip())
        if found is None:
            raise HTTPException(401, "token_invalid")
        token, user = found
        return Caller(
            user=user, browser=False, scopes=frozenset(token.scopes), token_name=token.name
        )
    session = session_user(db, request.cookies.get(COOKIE))
    if session is not None:
        return Caller(user=session, browser=True, scopes=frozenset(SCOPES))
    raise HTTPException(401, "login_required")


def require(scope: str) -> Callable[[Caller], Caller]:
    assert scope in SCOPES, scope

    def check(who: Caller = Depends(caller)) -> Caller:
        if scope not in who.scopes:
            raise HTTPException(403, f"scope_missing:{scope}")
        return who

    return check


def browser_only(who: Caller = Depends(caller)) -> Caller:
    """Things a token must never do (tokens, passwords, linking a bank)."""
    if not who.browser:
        raise HTTPException(403, "browser_only")
    return who


def admin_only(who: Caller = Depends(browser_only)) -> Caller:
    if not who.user.is_admin:
        raise HTTPException(403, "admin_only")
    return who
