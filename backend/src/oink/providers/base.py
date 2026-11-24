"""What every linked source of money looks like to Oink.

A provider turns credentials (an API key, a public address, a bank session) into accounts,
balances and movements. Amounts come back as decimal strings in the account's currency and
are converted to minor units by `core.money`, so providers never deal with exponents."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

import httpx

from oink.services.ledger import NewMovement

Kind = Literal["bank", "exchange", "wallet", "fintech"]


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class Field:
    name: str
    secret: bool = True
    placeholder: str = ""
    optional: bool = False


@dataclass
class RemoteAccount:
    uid: str
    name: str
    currency: str
    kind: str = "checking"  # Account.kind
    iban: str = ""
    balance: str | None = None  # decimal string, when the listing already has it


@dataclass
class Provider:
    """Base class; subclasses override what they support."""

    id: str = ""
    name: str = ""
    kind: Kind = "fintech"
    fields: tuple[Field, ...] = ()
    docs: str = ""  # where the user creates the key
    movements_supported: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    async def accounts(self, creds: dict[str, str]) -> list[RemoteAccount]:
        raise NotImplementedError

    async def balance(self, creds: dict[str, str], account: RemoteAccount) -> str | None:
        return account.balance

    async def movements(
        self, creds: dict[str, str], account: RemoteAccount, since: date
    ) -> list[NewMovement]:
        return []


async def request(client: httpx.AsyncClient | None, method: str, url: str, **kwargs: Any) -> Any:
    """One HTTP call with errors turned into ProviderError (never leaking credentials)."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=30)
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise ProviderError(f"unreachable: {type(exc).__name__}") from exc
    finally:
        if own:
            await client.aclose()
    if response.status_code in (401, 403):
        raise ProviderError("credentials_rejected")
    if response.status_code >= 400:
        raise ProviderError(f"http_{response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError("bad_response") from exc
