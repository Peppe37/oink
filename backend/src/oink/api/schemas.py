"""What the API sends and receives. Money travels as integer cents."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AccountKind = Literal["checking", "savings", "card", "cash", "investment"]


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AccountOut(Out):
    id: int
    name: str
    kind: str
    institution: str
    currency: str
    source: str
    balance_cents: int | None
    balance_at: datetime | None
    color: str
    archived: bool
    connection_id: int | None


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: AccountKind = "checking"
    institution: str = ""
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    balance_cents: int | None = None
    color: str = ""


class AccountPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    kind: AccountKind | None = None
    institution: str | None = None
    balance_cents: int | None = None
    color: str | None = None
    archived: bool | None = None


class TransactionOut(Out):
    id: int
    account_id: int
    day: date
    amount_cents: int
    currency: str
    description: str
    counterparty: str
    category: str
    category_by: str
    note: str
    source: str


class TransactionIn(BaseModel):
    account_id: int
    day: date
    amount_cents: int
    description: str = Field(min_length=1, max_length=500)
    counterparty: str = ""
    category: str = ""
    note: str = ""


class TransactionPatch(BaseModel):
    category: str | None = Field(default=None, max_length=40)
    note: str | None = None
    learn: bool = True  # also apply the category to this merchant in the future


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str]


class TokenOut(Out):
    id: int
    name: str
    prefix: str
    scopes: list[str]
    created: datetime
    last_used: datetime | None
    revoked: bool


class RequestOut(Out):
    id: int
    account_id: int
    month: str
    status: str
    created: datetime


class ReportOut(Out):
    id: int
    month: str
    kind: str
    content: str
    model: str
    created: datetime


class ConnectionOut(Out):
    id: int
    provider: str
    institution: str
    status: str
    valid_until: datetime | None
    last_sync: datetime | None
    error: str


class ImportOut(BaseModel):
    added: int
    duplicates: int
    month: str
    by_ai: bool
    balance_updated: bool
    request_closed: bool


class Login(BaseModel):
    password: str


class PasswordChange(BaseModel):
    current: str
    new: str = Field(min_length=10)


class Ask(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    month: str | None = None
