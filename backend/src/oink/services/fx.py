"""Exchange rates, free and keyless: the ECB reference rates (via Frankfurter) for fiat and
CoinGecko for crypto. Stored as «1 unit = x EUR» and refreshed hourly by the background job
for the currencies people actually hold. Totals in a user's base currency use the latest
rate (an estimate, shown as such when a rate is missing)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from oink.core.db import Account, Rate, User
from oink.core.money import CRYPTO, convert, is_crypto

log = logging.getLogger("oink.fx")
ECB = "https://api.frankfurter.dev/v1/latest"
COINGECKO = "https://api.coingecko.com/api/v3/simple/price"


class Rates:
    """The rates known now, for converting many amounts without hitting the database."""

    def __init__(self, eur: dict[str, Decimal]) -> None:
        self.eur = {"EUR": Decimal(1), **eur}

    @classmethod
    def load(cls, db: Session) -> Rates:
        return cls({r.code: r.eur for r in db.scalars(select(Rate))})

    def rate(self, source: str, target: str) -> Decimal | None:
        if source == target:
            return Decimal(1)
        a, b = self.eur.get(source), self.eur.get(target)
        return a / b if a is not None and b else None

    def convert(self, amount: int, source: str, target: str) -> int | None:
        rate = self.rate(source, target)
        return None if rate is None else convert(amount, source, target, rate)


def currencies_in_use(db: Session) -> set[str]:
    held = set(db.scalars(select(Account.currency).where(Account.archived.is_(False))))
    return held | set(db.scalars(select(User.base_currency)))


async def refresh(db: Session, codes: set[str], client: httpx.AsyncClient | None = None) -> int:
    """Fetch rates for `codes`; returns how many were updated. Failures keep the old ones."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=20)
    found: dict[str, Decimal] = {}
    try:
        fiat = sorted(c for c in codes if c != "EUR" and not is_crypto(c))
        if fiat:
            try:
                r = await client.get(ECB, params={"base": "EUR", "symbols": ",".join(fiat)})
                r.raise_for_status()
                for code, per_eur in r.json().get("rates", {}).items():
                    if per_eur:
                        found[code] = Decimal(1) / Decimal(str(per_eur))
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("ECB rates unavailable: %s", type(exc).__name__)
        crypto = {CRYPTO[c]: c for c in codes if c in CRYPTO}
        if crypto:
            try:
                r = await client.get(
                    COINGECKO, params={"ids": ",".join(sorted(crypto)), "vs_currencies": "eur"}
                )
                r.raise_for_status()
                for coin, prices in r.json().items():
                    if coin in crypto and prices.get("eur"):
                        found[crypto[coin]] = Decimal(str(prices["eur"]))
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("CoinGecko prices unavailable: %s", type(exc).__name__)
    finally:
        if own:
            await client.aclose()
    for code, eur in found.items():
        row = db.get(Rate, code)
        if row is None:
            db.add(Rate(code=code, eur=eur))
        else:
            row.eur, row.updated = eur, datetime.now(UTC)
    db.commit()
    return len(found)
