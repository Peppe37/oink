"""The data model (SQLite through SQLAlchemy 2), one database for every user of the instance.

Every row that holds someone's money carries `user_id`, and every query filters on it
(see `api.deps.Caller`). Amounts are integers in the currency's smallest unit
(`core.money`): cents for EUR, 1e-8 for crypto. The schema is versioned with Alembic
(`oink/migrations`): it upgrades itself at startup, so updates keep the data."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

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
    # single sign-on (OpenID Connect: Keycloak, Authentik, Google…): the provider's subject
    oidc_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    email: Mapped[str] = mapped_column(String(255), default="")
    #: a password set by the admin (or the default one): changed at the next sign-in
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    #: wrong passwords in a row; at LOCK_AFTER the account waits until `locked_until`
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


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
    category_by: Mapped[str] = mapped_column(String(8), default="")  # ""|rule|ai|user|transfer
    note: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual|linked|import|api
    fingerprint: Mapped[str] = mapped_column(String(40))
    #: the other leg of a transfer between own accounts (services/transfers.py)
    transfer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: an execution of this accumulation plan (services/plans.py)
    plan_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: the statement or document it was imported from (to move it to another account)
    document_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    #: order within the day, newest highest (banks give no time of day)
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: a part of a movement split by the person (the original stays, hidden, so the bank's
    #: copy is still recognised; its category_by is «split»)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
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


class Document(Base):
    """A financial document: statement, payslip, contract, invoice… The file lives in
    `data/documents/<user id>/`, its text is split into chunks indexed for full-text search
    (`doc_chunks`, an SQLite FTS5 table managed by `services.documents`)."""

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("user_id", "sha256", name="uq_documents_user_sha"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="other")
    title: Mapped[str] = mapped_column(String(200), default="")
    filename: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64))
    stored: Mapped[str] = mapped_column(String(255))  # path inside data/documents
    text: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    issuer: Mapped[str] = mapped_column(String(160), default="")
    doc_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    facts: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    imported: Mapped[int | None] = mapped_column(Integer, nullable=True)  # movements added
    import_note: Mapped[str] = mapped_column(String(60), default="")  # why it was not imported
    source: Mapped[str] = mapped_column(String(16), default="upload")  # upload|inbox|api|mcp
    #: processing | ready | review (something didn't add up: see `review`) | error
    status: Mapped[str] = mapped_column(String(12), default="ready")
    #: checks by rules, the AI's note, movements read from a statement (to import on confirm)
    review: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Rule(Base):
    """«Descriptions containing X are category Y»: learnt from the user's corrections."""

    __tablename__ = "rules"
    __table_args__ = (UniqueConstraint("user_id", "pattern"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    pattern: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(40))
    #: the payee's real name, when the bank shows a terminal code («44941 Palo Del Colle - S»)
    name: Mapped[str] = mapped_column(String(120), default="")


class Rate(Base):
    """Exchange rate: 1 `code` = `eur` EUR (ECB for fiat, CoinGecko for crypto)."""

    __tablename__ = "rates"
    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    eur: Mapped[Decimal] = mapped_column(DecimalText())
    updated: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Holding(Base):
    """A position in a security or a coin: shares of an ETF, a bond, BTC on a hardware wallet.
    Priced from market data (`services/markets.py`) by its `symbol` (Yahoo Finance style:
    «VWCE.DE», «AAPL», «BTC-EUR»)."""

    __tablename__ = "holdings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(160), default="")
    kind: Mapped[str] = mapped_column(String(16), default="etf")  # stock|etf|bond|fund|crypto
    currency: Mapped[str] = mapped_column(String(10), default="EUR")
    quantity: Mapped[Decimal] = mapped_column(DecimalText(), default=Decimal(0))
    cost: Mapped[Decimal | None] = mapped_column(DecimalText(), nullable=True)  # total paid
    manual_price: Mapped[Decimal | None] = mapped_column(DecimalText(), nullable=True)
    created: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Plan(Base):
    """An accumulation plan (PAC): `amount` into `symbol` every period from `start`."""

    __tablename__ = "plans"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(160), default="")
    amount: Mapped[Decimal] = mapped_column(DecimalText())
    currency: Mapped[str] = mapped_column(String(10), default="EUR")
    frequency: Mapped[str] = mapped_column(
        String(12), default="monthly"
    )  # weekly|monthly|quarterly
    start: Mapped[date] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    #: where the money is taken from (its debits are matched as the plan's executions),
    #: on which day of the month, and until when (a plan replaced by another)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end: Mapped[date | None] = mapped_column(Date, nullable=True)
    created: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Price(Base):
    """Monthly closing prices (history for backtests and simulations), cached."""

    __tablename__ = "prices"
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    close: Mapped[Decimal] = mapped_column(DecimalText())


class Quote(Base):
    """The latest price of a symbol."""

    __tablename__ = "quotes"
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    price: Mapped[Decimal] = mapped_column(DecimalText())
    currency: Mapped[str] = mapped_column(String(10), default="")
    name: Mapped[str] = mapped_column(String(160), default="")
    updated: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class FixedCost(Base):
    """A cost that comes back every period: rent, insurance, phone, gym, mortgage…"""

    __tablename__ = "fixed_costs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    amount: Mapped[Decimal] = mapped_column(DecimalText())  # positive, per period
    currency: Mapped[str] = mapped_column(String(10), default="EUR")
    frequency: Mapped[str] = mapped_column(
        String(12), default="monthly"
    )  # monthly|quarterly|yearly|weekly
    category: Mapped[str] = mapped_column(String(40), default="")
    next_due: Mapped[date | None] = mapped_column(Date, nullable=True)
    match: Mapped[str] = mapped_column(String(120), default="")  # text that finds its payments
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    #: «wait»: a missing payment isn't flagged again until this day
    snoozed_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: what the person said about single payments: [{"due", "kind": skipped|paid, "tx"?}]
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class RiskProfile(Base):
    """The user's answers to the risk questionnaire and their goals."""

    __tablename__ = "risk_profiles"
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    answers: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    score: Mapped[int] = mapped_column(Integer, default=3)  # 1 cautious … 5 aggressive
    horizon_years: Mapped[int] = mapped_column(Integer, default=10)
    goals: Mapped[str] = mapped_column(Text, default="")
    updated: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.engine = create_engine(url, connect_args={"check_same_thread": False})
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", _sqlite_pragmas)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def create(self, admin_username: str = "admin") -> None:
        """Bring the schema to the latest version (Alembic), keeping the data."""
        from oink.core.migrate import upgrade

        upgrade(self.engine, admin_username)

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
    "FixedCost",
    "Holding",
    "Invite",
    "LoginSession",
    "Plan",
    "Price",
    "Quote",
    "Rate",
    "Report",
    "RiskProfile",
    "Rule",
    "Setting",
    "StatementRequest",
    "Transaction",
    "User",
]
