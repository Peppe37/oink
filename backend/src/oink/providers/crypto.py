"""Crypto: exchanges with a READ-ONLY API key (Kraken, Binance) and wallets by public
address (Bitcoin, Ethereum). Oink only reads balances; it never needs trading rights, and
the setup screen tells people to create keys without them."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx

from oink.core.money import to_minor
from oink.providers.base import Field, Kind, Provider, ProviderError, RemoteAccount, request
from oink.services.ledger import NewMovement

# Kraken's legacy asset names → common symbols.
KRAKEN_ASSETS = {
    "XXBT": "BTC", "XBT": "BTC", "XETH": "ETH", "XXDG": "DOGE", "XDG": "DOGE", "XXRP": "XRP",
    "XLTC": "LTC", "XXLM": "XLM", "XXMR": "XMR", "ZEUR": "EUR", "ZUSD": "USD", "ZGBP": "GBP",
    "ZCAD": "CAD", "ZJPY": "JPY", "ZCHF": "CHF",
}  # fmt: skip


def kraken_symbol(asset: str) -> str:
    base = asset.split(".")[0]  # ETH.F, DOT.S (earn/staking) count as the asset itself
    return KRAKEN_ASSETS.get(base, base)


def _holdings(balances: dict[str, Decimal]) -> list[RemoteAccount]:
    return [
        RemoteAccount(uid=sym, name=sym, currency=sym, kind="crypto", balance=str(amount))
        for sym, amount in sorted(balances.items())
        if amount > 0
    ]


@dataclass
class Kraken(Provider):
    id: str = "kraken"
    name: str = "Kraken"
    kind: Kind = "exchange"
    fields: tuple[Field, ...] = (Field("api_key"), Field("private_key"))
    docs: str = "https://www.kraken.com/u/security/api"
    client: httpx.AsyncClient | None = field(default=None, repr=False)

    async def accounts(self, creds: dict[str, str]) -> list[RemoteAccount]:
        path = "/0/private/Balance"
        nonce = str(int(time.time() * 1000))
        body = urllib.parse.urlencode({"nonce": nonce})
        try:
            secret = base64.b64decode(creds["private_key"])
        except (KeyError, ValueError) as exc:
            raise ProviderError("credentials_rejected") from exc
        digest = hashlib.sha256((nonce + body).encode()).digest()
        sign = hmac.new(secret, path.encode() + digest, hashlib.sha512).digest()
        data = await request(
            self.client,
            "POST",
            "https://api.kraken.com" + path,
            content=body,
            headers={
                "API-Key": creds.get("api_key", ""),
                "API-Sign": base64.b64encode(sign).decode(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        if data.get("error"):
            raise ProviderError(
                "credentials_rejected"
                if any("Invalid key" in e or "Permission" in e for e in data["error"])
                else "provider_error"
            )
        totals: dict[str, Decimal] = {}
        for asset, amount in (data.get("result") or {}).items():
            sym = kraken_symbol(asset)
            totals[sym] = totals.get(sym, Decimal(0)) + Decimal(str(amount))
        return _holdings(totals)


@dataclass
class Binance(Provider):
    id: str = "binance"
    name: str = "Binance"
    kind: Kind = "exchange"
    fields: tuple[Field, ...] = (Field("api_key"), Field("api_secret"))
    docs: str = "https://www.binance.com/en/my/settings/api-management"
    client: httpx.AsyncClient | None = field(default=None, repr=False)

    async def accounts(self, creds: dict[str, str]) -> list[RemoteAccount]:
        query = urllib.parse.urlencode(
            {"timestamp": int(time.time() * 1000), "recvWindow": 10000, "omitZeroBalances": "true"}
        )
        sign = hmac.new(
            creds.get("api_secret", "").encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        data = await request(
            self.client,
            "GET",
            f"https://api.binance.com/api/v3/account?{query}&signature={sign}",
            headers={"X-MBX-APIKEY": creds.get("api_key", "")},
        )
        totals: dict[str, Decimal] = {}
        for b in data.get("balances", []):
            amount = Decimal(str(b.get("free", 0))) + Decimal(str(b.get("locked", 0)))
            sym = str(b.get("asset", "")).upper()
            if sym.startswith("LD") and len(sym) > 3:  # Simple Earn wrappers (LDBTC)
                sym = sym[2:]
            totals[sym] = totals.get(sym, Decimal(0)) + amount
        return _holdings(totals)


@dataclass
class BitcoinAddress(Provider):
    id: str = "bitcoin"
    name: str = "Bitcoin"
    kind: Kind = "wallet"
    fields: tuple[Field, ...] = (Field("address", secret=False, placeholder="bc1q…"),)
    docs: str = "https://mempool.space"
    movements_supported: bool = True
    client: httpx.AsyncClient | None = field(default=None, repr=False)

    async def accounts(self, creds: dict[str, str]) -> list[RemoteAccount]:
        address = creds.get("address", "").strip()
        data = await request(self.client, "GET", f"https://mempool.space/api/address/{address}")
        stats = data.get("chain_stats", {})
        sats = int(stats.get("funded_txo_sum", 0)) - int(stats.get("spent_txo_sum", 0))
        return [
            RemoteAccount(
                uid=address,
                name=f"BTC …{address[-6:]}",
                currency="BTC",
                kind="crypto",
                balance=str(Decimal(sats) / Decimal(10**8)),
            )
        ]

    async def movements(
        self, creds: dict[str, str], account: RemoteAccount, since: date
    ) -> list[NewMovement]:
        address = account.uid
        txs = await request(self.client, "GET", f"https://mempool.space/api/address/{address}/txs")
        found = []
        for tx in txs:
            status = tx.get("status", {})
            if not status.get("confirmed"):
                continue
            day = datetime.fromtimestamp(status.get("block_time", 0), UTC).date()
            if day < since:
                continue
            received = sum(
                o.get("value", 0)
                for o in tx.get("vout", [])
                if o.get("scriptpubkey_address") == address
            )
            sent = sum(
                (i.get("prevout") or {}).get("value", 0)
                for i in tx.get("vin", [])
                if (i.get("prevout") or {}).get("scriptpubkey_address") == address
            )
            net = received - sent
            if net:
                found.append(
                    NewMovement(
                        day=day,
                        amount=to_minor(Decimal(net) / Decimal(10**8), "BTC"),
                        description="Bitcoin " + ("received" if net > 0 else "sent"),
                        external_id=str(tx.get("txid", "")),
                        category="crypto",
                    )
                )
        return found


@dataclass
class EthereumAddress(Provider):
    id: str = "ethereum"
    name: str = "Ethereum"
    kind: Kind = "wallet"
    fields: tuple[Field, ...] = (Field("address", secret=False, placeholder="0x…"),)
    docs: str = "https://etherscan.io"
    rpc: str = "https://ethereum-rpc.publicnode.com"
    client: httpx.AsyncClient | None = field(default=None, repr=False)

    async def accounts(self, creds: dict[str, str]) -> list[RemoteAccount]:
        address = creds.get("address", "").strip()
        if not (address.startswith("0x") and len(address) == 42):
            raise ProviderError("address_invalid")
        data = await request(
            self.client,
            "POST",
            self.rpc,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_getBalance",
                "params": [address, "latest"],
            },
        )
        if "result" not in data:
            raise ProviderError("provider_error")
        wei = int(data["result"], 16)
        return [
            RemoteAccount(
                uid=address.lower(),
                name=f"ETH …{address[-6:]}",
                currency="ETH",
                kind="crypto",
                balance=str(Decimal(wei) / Decimal(10**18)),
            )
        ]
