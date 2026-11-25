"""What the API sends and receives. Amounts travel as exact decimal strings in the
account's currency («-12.50», «0.00031250»); reports use plain numbers in the user's base
currency. Errors carry a short code in `detail` (translated by the web app)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from oink.core.db import Account, Transaction, User
from oink.core.money import as_text

AccountKind = Literal["checking", "savings", "card", "cash", "investment", "crypto"]
Language = Literal["", "en", "it", "de", "fr", "es"]


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserOut(Out):
    id: int
    username: str
    name: str
    is_admin: bool
    language: str
    base_currency: str
    disabled: bool
    created: datetime
    sso: bool = False


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=40)
    name: str = ""
    is_admin: bool = False


class UserPatch(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    is_admin: bool | None = None
    disabled: bool | None = None


class ProfilePatch(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    language: Language | None = None
    base_currency: str | None = Field(default=None, min_length=2, max_length=10)


class AccountOut(BaseModel):
    id: int
    name: str
    kind: str
    institution: str
    iban: str
    currency: str
    source: str
    balance: str | None
    balance_at: datetime | None
    color: str
    archived: bool
    connection_id: int | None

    @classmethod
    def of(cls, a: Account) -> AccountOut:
        return cls(
            id=a.id,
            name=a.name,
            kind=a.kind,
            institution=a.institution,
            iban=a.iban,
            currency=a.currency,
            source=a.source,
            balance=as_text(a.balance, a.currency),
            balance_at=a.balance_at,
            color=a.color,
            archived=a.archived,
            connection_id=a.connection_id,
        )


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: AccountKind = "checking"
    institution: str = ""
    iban: str = Field(default="", max_length=40)
    currency: str = Field(default="EUR", min_length=2, max_length=10)
    balance: str | float | None = None
    color: str = ""


class AccountPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    kind: AccountKind | None = None
    institution: str | None = None
    iban: str | None = Field(default=None, max_length=40)
    balance: str | float | None = None
    color: str | None = None
    archived: bool | None = None


class TransactionOut(BaseModel):
    id: int
    account_id: int
    day: date
    amount: str
    currency: str
    description: str
    counterparty: str
    category: str
    category_by: str
    note: str
    source: str
    transfer_id: int | None = None

    @classmethod
    def of(cls, t: Transaction) -> TransactionOut:
        return cls(
            id=t.id,
            account_id=t.account_id,
            day=t.day,
            amount=as_text(t.amount, t.currency) or "0",
            currency=t.currency,
            description=t.description,
            counterparty=t.counterparty,
            category=t.category,
            category_by=t.category_by,
            note=t.note,
            transfer_id=t.transfer_id,
            source=t.source,
        )


class TransactionIn(BaseModel):
    account_id: int
    day: date
    amount: str | float = Field(description="Negative = money out, in the account's currency.")
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
    language: str
    created: datetime


class ConnectionOut(Out):
    id: int
    provider: str
    label: str
    status: str
    valid_until: datetime | None
    last_sync: datetime | None
    error: str


class KeyLink(BaseModel):
    provider: str
    label: str = ""
    credentials: dict[str, str]


class BankLink(BaseModel):
    bank: str = Field(min_length=1, max_length=120)
    country: str = Field(min_length=2, max_length=2)


class BankAccess(BaseModel):
    app_id: str = Field(min_length=8, max_length=80)
    private_key: str = Field(min_length=100, max_length=20000)


class ImportOut(BaseModel):
    account_id: int
    added: int
    duplicates: int
    month: str
    by_ai: bool
    estimated: bool
    balance_updated: bool
    request_closed: bool


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=200)


class Setup(Login):
    language: Language = ""
    base_currency: str = "EUR"


class InviteAccept(BaseModel):
    token: str
    password: str = Field(min_length=1, max_length=200)


class PasswordChange(BaseModel):
    current: str
    new: str


class Ask(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    month: str | None = None


def user_out(u: User) -> UserOut:
    out = UserOut.model_validate(u)
    out.sso = bool(u.oidc_sub)
    return out
