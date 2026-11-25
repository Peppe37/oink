"""Enable Banking: PSD2 access to 2,500+ European banks, free for linking your own accounts.

The instance needs ONE application (the admin sets it up once, in the app or in .env):
create it at https://enablebanking.com (restricted production), register
`<OINK_PUBLIC_URL>/api/connections/callback` as redirect URL, and give Oink the app id and
private key. Then every user links banks by just picking theirs and logging in there.
Each request is signed with a short-lived RS256 JWT (the app id is the key id).
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import jwt

from oink.core.money import MoneyError, to_minor
from oink.providers.base import ProviderError, RemoteAccount
from oink.services.ledger import NewMovement

log = logging.getLogger("oink.enablebanking")
API = "https://api.enablebanking.com"
CONSENT_DAYS = 180  # asked for, capped by what each bank allows (Trade Republic: 90)
#: How far back the first sync looks: right after the login (strong authentication) banks
#: give their whole history; each is tried until the bank accepts it.
HISTORY_DAYS = (730, 540, 365, 180, 89)
COUNTRIES = ["AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GB", "GR",
             "HR", "HU", "IE", "IS", "IT", "LI", "LT", "LU", "LV", "MT", "NL", "NO", "PL",
             "PT", "RO", "SE", "SI", "SK"]  # fmt: skip


class EnableBanking:
    def __init__(self, app_id: str, private_key: str, *, client: httpx.AsyncClient | None = None):
        self.app_id = app_id
        self.private_key = private_key
        self._client = client

    def _token(self) -> str:
        issued = int(time.time())
        return jwt.encode(
            {
                "iss": "enablebanking.com",
                "aud": "api.enablebanking.com",
                "iat": issued,
                "exp": issued + 3600,
            },
            self.private_key,
            algorithm="RS256",
            headers={"kid": self.app_id},
        )

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        client = self._client or httpx.AsyncClient(timeout=30)
        try:
            response = await client.request(
                method, API + path, headers={"Authorization": f"Bearer {self._token()}"}, **kwargs
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"unreachable: {type(exc).__name__}") from exc
        finally:
            if self._client is None:
                await client.aclose()
        if response.status_code >= 400:
            # the bank's or Enable Banking's own words, for the log (never shown raw)
            log.warning(
                "Enable Banking %s %s → %s %s",
                method,
                path.split("?")[0],
                response.status_code,
                response.text[:300],
            )
        if response.status_code in (401, 403):
            raise ProviderError("credentials_rejected")
        if response.status_code >= 400:
            raise ProviderError(f"http_{response.status_code}")
        return response.json()

    async def check(self) -> None:
        """Raises when the app id or key is wrong."""
        await self._call("GET", "/application")

    async def banks(self, country: str) -> list[dict[str, Any]]:
        data = await self._call("GET", "/aspsps", params={"country": country})
        return [
            {"name": a["name"], "country": a["country"], "logo": a.get("logo", "")}
            for a in data.get("aspsps", [])
        ]

    async def consent_days(self, bank: str, country: str) -> int:
        """The longest consent this bank accepts (Enable Banking rejects a longer one)."""
        try:
            data = await self._call("GET", "/aspsps", params={"country": country})
        except ProviderError:
            return 89
        for aspsp in data.get("aspsps", []):
            if aspsp.get("name") == bank:
                seconds = aspsp.get("maximum_consent_validity") or CONSENT_DAYS * 86400
                return max(1, min(CONSENT_DAYS, int(seconds) // 86400 - 1))
        return 89

    async def start(self, bank: str, country: str, state: str, redirect_url: str) -> str:
        """The bank's login page the user must open."""
        days = await self.consent_days(bank, country)
        valid = (datetime.now(UTC) + timedelta(days=days)).isoformat()
        data = await self._call(
            "POST",
            "/auth",
            json={
                "access": {"valid_until": valid},
                "aspsp": {"name": bank, "country": country},
                "state": state,
                "redirect_url": redirect_url,
                "psu_type": "personal",
            },
        )
        return str(data["url"])

    async def finish(self, code: str) -> dict[str, Any]:
        """{session_id, valid_until, accounts: [RemoteAccount]}"""
        data = await self._call("POST", "/sessions", json={"code": code})
        accounts = []
        for acc in data.get("accounts", []):
            iban = (acc.get("account_id") or {}).get("iban") or ""
            accounts.append(
                RemoteAccount(
                    uid=acc["uid"],
                    iban=iban,
                    currency=acc.get("currency") or "EUR",
                    name=acc.get("name")
                    or acc.get("product")
                    or (f"…{iban[-4:]}" if iban else "Bank"),
                    kind="card" if acc.get("cash_account_type") == "CARD" else "checking",
                )
            )
        valid = (data.get("access") or {}).get("valid_until")
        return {"session_id": data["session_id"], "valid_until": valid, "accounts": accounts}

    async def balance(self, uid: str, currency: str) -> int | None:
        data = await self._call("GET", f"/accounts/{uid}/balances")
        balances = data.get("balances", [])
        preferred = ("CLBD", "ITBD", "ITAV", "XPCD", "CLAV")
        balances.sort(
            key=lambda b: (
                preferred.index(b.get("balance_type")) if b.get("balance_type") in preferred else 99
            )
        )
        for b in balances:
            try:
                return to_minor(str(b["balance_amount"]["amount"]), currency)
            except (KeyError, MoneyError):
                continue
        return None

    async def history(self, uid: str, currency: str) -> list[NewMovement]:
        """As much past as the bank gives: right after the login most give years."""
        # Let the aggregator find the earliest available date instead of falling
        # back to a short window when the bank rejects a requested period.
        try:
            return await self.movements(
                uid, currency, date.today() - timedelta(days=HISTORY_DAYS[0]), strategy="longest"
            )
        except ProviderError:
            pass  # keep the explicit-window fallback for banks rejecting this strategy
        last: ProviderError | None = None
        for days in HISTORY_DAYS:
            try:
                return await self.movements(uid, currency, date.today() - timedelta(days=days))
            except ProviderError as exc:
                last = exc
        raise last or ProviderError("history_unavailable")

    async def movements(
        self,
        uid: str,
        currency: str,
        since: date,
        *,
        strategy: str = "default",
        pending: bool = False,
    ) -> list[NewMovement]:
        """Booked movements; with `pending`, only the ones still waiting to be booked (a card
        payment of today: already in the balance, not yet in the booked list)."""
        found: list[NewMovement] = []
        params: dict[str, str] = {"date_from": since.isoformat()}
        if strategy != "default":
            params["strategy"] = strategy
        for _ in range(50):  # pages
            data = await self._call("GET", f"/accounts/{uid}/transactions", params=params)
            for tx in data.get("transactions", []):
                booked = tx.get("status") in (None, "BOOK")
                if booked == pending:
                    continue
                movement = to_movement(tx, currency)
                if movement is not None:
                    found.append(movement)
            key = data.get("continuation_key")
            if not key:
                break
            params["continuation_key"] = key
        return found


def to_movement(tx: dict[str, Any], currency: str) -> NewMovement | None:
    try:
        amount = to_minor(str(tx["transaction_amount"]["amount"]), currency)
        day = date.fromisoformat(str(tx.get("booking_date") or tx.get("value_date"))[:10])
    except (KeyError, ValueError, TypeError):
        return None
    if tx.get("credit_debit_indicator") == "DBIT":
        amount = -abs(amount)
    elif tx.get("credit_debit_indicator") == "CRDT":
        amount = abs(amount)
    other = tx.get("creditor") if amount < 0 else tx.get("debtor")
    party = (other or {}).get("name") or ""
    info = tx.get("remittance_information") or []
    description = " ".join(info) if isinstance(info, list) else str(info)
    return NewMovement(
        day=day,
        amount=amount,
        description=description or party or "—",
        counterparty=party,
        external_id=str(tx.get("entry_reference") or tx.get("transaction_id") or ""),
    )
