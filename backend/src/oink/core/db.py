"""The data model (SQLite through SQLAlchemy 2), one database for every user of the instance.

Every row that holds someone's money carries `user_id`, and every query filters on it
(see `api.deps.Caller`). Amounts are integers in the currency's smallest unit
(`core.money`): cents for EUR, 1e-8 for crypto. The schema is versioned with Alembic
(`oink/migrations`): it upgrades itself at startup, so updates keep the data."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator


def now() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime | None) -> datetime | None:
    """SQLite gives naive datetimes back: they were stored in UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


class DecimalText(TypeDecorator[Decimal]):
    """Exact decimals in SQLite (its NUMERIC is a float)."""

    impl = String(48)
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: object) -> str | None:
        return None if value is None else str(value)

    def process_result_value(self, value: str | None, dialect: object) -> Decimal | None:
        return None if value is None else Decimal(value)


class UTCDateTime(TypeDecorator[datetime]):
    """Datetimes stored in UTC and always read back timezone-aware (SQLite drops the zone)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(UTC)
        return value

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        return aware(value)


class Base(DeclarativeBase):
    pass


class Setting(Base):
    """Instance-wide key/value store (bank aggregator app id, update state…)."""

    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80), default="")
    password_hash: Mapped[str] = mapped_column(String(200), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    language: Mapped[str] = mapped_column(String(8), default="")  # "" = the browser's
    base_currency: Mapped[str] = mapped_column(String(10), default="EUR")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Invite(Base):
    """A one-time link the admin sends: whoever opens it chooses their password."""

    __tablename__ = "invites"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires: Mapped[datetime] = mapped_column(UTCDateTime())
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class LoginSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of the cookie
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    expires: Mapped[datetime] = mapped_column(UTCDateTime())


class ApiToken(Base):
    """A token for another app (e.g. an assistant), with fine-grained scopes, acting for one
    user. Only its hash is kept: the token itself is shown once, at creation."""

    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(16))  # shown in the list to recognise it
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    created: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    last_used: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Connection(Base):
    """A live link to where money is: a bank through PSD2, an exchange or a fintech with an
    API key, a crypto wallet by its public address. Credentials are encrypted at rest."""

    __tablename__ = "connections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(120))
    secret: Mapped[str] = mapped_column(Text, default="")  # encrypted JSON credentials
    state: Mapped[str] = mapped_column(String(64), unique=True)  # OAuth state while pending
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # pending|active|expired|error
    valid_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_sync: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    created: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16), default="checking")
    # checking|savings|card|cash|investment|crypto
    institution: Mapped[str] = mapped_column(String(120), default="")
    iban: Mapped[str] = mapped_column(String(40), default="")  # statements are matched by it
    currency: Mapped[str] = mapped_column(String(10), default="EUR")
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual|linked
    connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("connections.id", ondelete="SET NULL"), nullable=True
    )
    external_id: Mapped[str] = mapped_column(String(120), default="")
    balance: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    balance_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    color: Mapped[str] = mapped_column(String(16), default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("account_id", "fingerprint"),
        Index("ix_transactions_user_day", "user_id", "day"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    day: Mapped[date] = mapped_column(Date)
    amount: Mapped[int] = mapped_column(BigInteger)  # minor units of `currency`; < 0 = out
    currency: Mapped[str] = mapped_column(String(10), default="EUR")
    description: Mapped[str] = mapped_column(Text)
    counterparty: Mapped[str] = mapped_column(String(200), default="")
    category: Mapped[str] = mapped_column(String(40), default="")
    category_by: Mapped[str] = mapped_column(String(8), default="")  # ""|rule|ai|user
    note: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual|linked|import|api
    fingerprint: Mapped[str] = mapped_column(String(40))
    created: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class StatementRequest(Base):
    """«Send me this month's statement» for an account no API can read."""

    __tablename__ = "statement_requests"
    __table_args__ = (UniqueConstraint("account_id", "month"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    month: Mapped[str] = mapped_column(String(7))  # YYYY-MM it covers
    status: Mapped[str] = mapped_column(String(12), default="open")  # open|done|skipped
    created: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    month: Mapped[str] = mapped_column(String(7))
    kind: Mapped[str] = mapped_column(String(16), default="monthly")
    content: Mapped[str] = mapped_column(Text)  # Markdown
    model: Mapped[str] = mapped_column(String(80), default="")
    language: Mapped[str] = mapped_column(String(8), default="en")
    created: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Rule(Base):
    """«Descriptions containing X are category Y»: learnt from the user's corrections."""

    __tablename__ = "rules"
    __table_args__ = (UniqueConstraint("user_id", "pattern"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    pattern: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(40))


class Rate(Base):
    """Exchange rate: 1 `code` = `eur` EUR (ECB for fiat, CoinGecko for crypto)."""

    __tablename__ = "rates"
    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    eur: Mapped[Decimal] = mapped_column(DecimalText())
    updated: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.engine = create_engine(url, connect_args={"check_same_thread": False})
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", _sqlite_pragmas)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def create(self) -> None:
        """Bring the schema to the latest version (Alembic), keeping the data."""
        from oink.core.migrate import upgrade

        upgrade(self.engine)

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
    "Invite",
    "LoginSession",
    "Rate",
    "Report",
    "Rule",
    "Setting",
    "StatementRequest",
    "Transaction",
    "User",
]
