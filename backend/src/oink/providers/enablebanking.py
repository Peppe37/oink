"""Enable Banking: PSD2 access to European banks, free for linking your own accounts.

Setup: create an application at https://enablebanking.com (restricted production for
personal use), register `<OINK_PUBLIC_URL>/api/banks/callback` as redirect URL, download the
private key to `OINK_ENABLE_BANKING_KEY_FILE` and set `OINK_ENABLE_BANKING_APP_ID`.
Each request is signed with a short-lived RS256 JWT (the app id is the key id).
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import jwt

from oink.services.ledger import NewMovement, to_cents

API = "https://api.enablebanking.com"
CONSENT_DAYS = 180


class BankError(RuntimeError):
    pass


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
            raise BankError(f"Enable Banking non raggiungibile: {type(exc).__name__}") from exc
        finally:
            if self._client is None:
                await client.aclose()
        if response.status_code >= 400:
            raise BankError(f"Enable Banking {response.status_code}: {response.text[:200]}")
        return response.json()

    async def banks(self, country: str) -> list[dict[str, Any]]:
        data = await self._call("GET", "/aspsps", params={"country": country})
        return [
            {"name": a["name"], "country": a["country"], "logo": a.get("logo", "")}
            for a in data.get("aspsps", [])
        ]

    async def start(self, bank: str, country: str, state: str, redirect_url: str) -> str:
        """The bank's login page the owner must open."""
        valid = (datetime.now(UTC) + timedelta(days=CONSENT_DAYS)).isoformat()
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
        """{session_id, valid_until, accounts: [{uid, name, iban, currency}]}"""
        data = await self._call("POST", "/sessions", json={"code": code})
        accounts = []
        for acc in data.get("accounts", []):
            iban = (acc.get("account_id") or {}).get("iban") or ""
            accounts.append(
                {
                    "uid": acc["uid"],
                    "iban": iban,
                    "currency": acc.get("currency") or "EUR",
                    "name": acc.get("name")
                    or acc.get("product")
                    or (f"Conto …{iban[-4:]}" if iban else "Conto"),
                }
            )
        valid = (data.get("access") or {}).get("valid_until")
        return {"session_id": data["session_id"], "valid_until": valid, "accounts": accounts}

    async def balance(self, uid: str) -> int | None:
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
                return to_cents(str(b["balance_amount"]["amount"]))
            except (KeyError, ValueError):
                continue
        return None

    async def movements(self, uid: str, since: date) -> list[NewMovement]:
        found: list[NewMovement] = []
        params: dict[str, str] = {"date_from": since.isoformat()}
        for _ in range(50):  # pages
            data = await self._call("GET", f"/accounts/{uid}/transactions", params=params)
            for tx in data.get("transactions", []):
                if tx.get("status") not in (None, "BOOK"):
                    continue  # pending ones change: wait until booked
                movement = to_movement(tx)
                if movement is not None:
                    found.append(movement)
            key = data.get("continuation_key")
            if not key:
                break
            params["continuation_key"] = key
        return found


def to_movement(tx: dict[str, Any]) -> NewMovement | None:
    try:
        amount = to_cents(str(tx["transaction_amount"]["amount"]))
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
        amount_cents=amount,
        description=description or party or "movimento",
        counterparty=party,
        currency=tx["transaction_amount"].get("currency", "EUR"),
        external_id=str(tx.get("entry_reference") or tx.get("transaction_id") or ""),
    )
