"""Wise with a personal API token (Settings → API tokens on wise.com, read only).
Balances always work; statements may need strong authentication in the EU, and then only
balances are synced (movements come from the monthly statement instead)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import httpx

from oink.core.money import to_minor
from oink.providers.base import Field, Kind, Provider, ProviderError, RemoteAccount, request
from oink.services.ledger import NewMovement

API = "https://api.wise.com"


@dataclass
class Wise(Provider):
    id: str = "wise"
    name: str = "Wise"
    kind: Kind = "fintech"
    fields: tuple[Field, ...] = (Field("api_token"),)
    docs: str = "https://wise.com/your-account/integrations-and-tools/api-tokens"
    movements_supported: bool = True
    client: httpx.AsyncClient | None = field(default=None, repr=False)

    def _auth(self, creds: dict[str, str]) -> dict[str, str]:
        return {"Authorization": f"Bearer {creds.get('api_token', '')}"}

    async def _profile(self, creds: dict[str, str]) -> int:
        profiles = await request(
            self.client, "GET", f"{API}/v2/profiles", headers=self._auth(creds)
        )
        personal = [p for p in profiles if p.get("type", "").upper() == "PERSONAL"] or profiles
        if not personal:
            raise ProviderError("no_profile")
        return int(personal[0]["id"])

    async def accounts(self, creds: dict[str, str]) -> list[RemoteAccount]:
        pid = await self._profile(creds)
        balances = await request(
            self.client,
            "GET",
            f"{API}/v4/profiles/{pid}/balances",
            params={"types": "STANDARD"},
            headers=self._auth(creds),
        )
        return [
            RemoteAccount(
                uid=f"{pid}:{b['id']}",
                name=f"Wise {b.get('currency', '')}",
                currency=str(b.get("currency", "EUR")),
                kind="checking",
                balance=str((b.get("amount") or {}).get("value", "0")),
            )
            for b in balances
        ]

    async def balance(self, creds: dict[str, str], account: RemoteAccount) -> str | None:
        for acc in await self.accounts(creds):
            if acc.uid == account.uid:
                return acc.balance
        return None

    async def movements(
        self, creds: dict[str, str], account: RemoteAccount, since: date
    ) -> list[NewMovement]:
        pid, bid = account.uid.split(":", 1)
        end = datetime.now(UTC)
        start = max(datetime.combine(since, datetime.min.time(), UTC), end - timedelta(days=360))
        try:
            data = await request(
                self.client,
                "GET",
                f"{API}/v1/profiles/{pid}/balance-statements/{bid}/statement.json",
                params={
                    "currency": account.currency,
                    "intervalStart": start.isoformat().replace("+00:00", "Z"),
                    "intervalEnd": end.isoformat().replace("+00:00", "Z"),
                    "type": "COMPACT",
                },
                headers=self._auth(creds),
            )
        except ProviderError as exc:
            if str(exc) == "credentials_rejected":  # SCA required: balances only
                return []
            raise
        found = []
        for tx in data.get("transactions", []):
            try:
                amount = to_minor(str(tx["amount"]["value"]), account.currency)
                day = date.fromisoformat(str(tx["date"])[:10])
            except (KeyError, ValueError):
                continue
            details = tx.get("details") or {}
            merchant = (details.get("merchant") or {}).get("name") or ""
            found.append(
                NewMovement(
                    day=day,
                    amount=amount,
                    description=str(details.get("description") or merchant or "Wise"),
                    counterparty=merchant,
                    external_id=str(tx.get("referenceNumber") or ""),
                )
            )
        return found
