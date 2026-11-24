"""The data model (SQLite through SQLAlchemy 2). Amounts are stored in cents (integers):
no float drift on money."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Setting(Base):
    """Small key/value store: the owner's password hash, preferences."""

    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class LoginSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of the cookie
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApiToken(Base):
    """A token for another app (e.g. Jarvis), with fine-grained scopes. Only its hash is
    kept: the token itself is shown once, at creation."""

    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(16))  # shown in the list to recognise it
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_used: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Connection(Base):
    """A bank authorisation through an aggregator (PSD2 consents last up to 180 days)."""

    __tablename__ = "connections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    institution: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(64), unique=True)  # OAuth state while pending
    session_id: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(
        String(16), default="pending"
    )  # pending|active|expired|error
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(
        String(16), default="checking"
    )  # checking|savings|card|cash|investment
    institution: Mapped[str] = mapped_column(String(120), default="")
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual|bank
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("connections.id"), nullable=True)
    external_id: Mapped[str] = mapped_column(String(120), default="")
    balance_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    balance_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    color: Mapped[str] = mapped_column(String(16), default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("account_id", "fingerprint"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)  # negative = money out
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    description: Mapped[str] = mapped_column(Text)
    counterparty: Mapped[str] = mapped_column(String(200), default="")
    category: Mapped[str] = mapped_column(String(40), default="")
    category_by: Mapped[str] = mapped_column(String(8), default="")  # ""|rule|ai|user
    note: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual|bank|import|api
    fingerprint: Mapped[str] = mapped_column(String(40))
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class StatementRequest(Base):
    """«Send me this month's statement» for an account no bank API can read."""

    __tablename__ = "statement_requests"
    __table_args__ = (UniqueConstraint("account_id", "month"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    month: Mapped[str] = mapped_column(String(7))  # YYYY-MM it covers
    status: Mapped[str] = mapped_column(String(12), default="open")  # open|done|skipped
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    month: Mapped[str] = mapped_column(String(7), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="monthly")
    content: Mapped[str] = mapped_column(Text)  # Markdown
    model: Mapped[str] = mapped_column(String(80), default="")
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Rule(Base):
    """«Descriptions containing X are category Y»: learnt from the owner's corrections."""

    __tablename__ = "rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pattern: Mapped[str] = mapped_column(String(120), unique=True)
    category: Mapped[str] = mapped_column(String(40))


class Database:
    def __init__(self, url: str) -> None:
        self.engine = create_engine(url, connect_args={"check_same_thread": False})
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", _sqlite_pragmas)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def create(self) -> None:
        Base.metadata.create_all(self.engine)

    def session(self) -> Iterator[Session]:
        with self.sessions() as session:
            yield session


def _sqlite_pragmas(dbapi_connection: object, _record: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


__all__ = [
    "Account",
    "ApiToken",
    "Base",
    "Connection",
    "Database",
    "Engine",
    "LoginSession",
    "Report",
    "Rule",
    "Setting",
    "StatementRequest",
    "Transaction",
]
