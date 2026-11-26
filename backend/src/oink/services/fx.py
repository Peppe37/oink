"""Exchange rates, free and keyless: official reference rates via Frankfurter for fiat,
and CoinGecko for crypto. Stored as «1 unit = x EUR» and refreshed hourly by the background job
for the currencies people actually hold. Totals in a user's base currency use the latest
rate (an estimate, shown as such when a rate is missing)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from oink.core.db import Account, Holding, Plan, Quote, Rate, User, aware
from oink.core.money import CRYPTO, convert, is_crypto

log = logging.getLogger("oink.fx")
FIAT_RATES = "https://api.frankfurter.dev/v2/rates"
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
    symbols = {s.upper() for s in db.scalars(select(Holding.symbol))}
    symbols.update(s.upper() for s in db.scalars(select(Plan.symbol)))
    held.update(db.scalars(select(Holding.currency)))
    held.update(db.scalars(select(Plan.currency)))
    held.update(db.scalars(select(Quote.currency).where(func.upper(Quote.symbol).in_(symbols))))
    return {c for c in held | set(db.scalars(select(User.base_currency))) if c}


async def ensure(db: Session, codes: set[str]) -> None:
    """Refresh missing or hour-old conversions, retaining cached rates on outages."""
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    stale = {
        c
        for c in codes
        if c != "EUR" and ((r := db.get(Rate, c)) is None or (aware(r.updated) or cutoff) <= cutoff)
    }
    if stale:
        await refresh(db, stale)


async def refresh(db: Session, codes: set[str], client: httpx.AsyncClient | None = None) -> int:
    """Fetch rates for `codes`; returns how many were updated. Failures keep the old ones."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=20)
    found: dict[str, Decimal] = {}
    try:
        fiat = sorted(c for c in codes if c != "EUR" and not is_crypto(c))
        if fiat:
            try:
                r = await client.get(FIAT_RATES, params={"base": "EUR", "quotes": ",".join(fiat)})
                r.raise_for_status()
                for item in r.json():
                    code = str(item["quote"]).upper()
                    per_eur = Decimal(str(item["rate"]))
                    if code in fiat and per_eur.is_finite() and per_eur > 0:
                        found[code] = Decimal(1) / per_eur
            except (httpx.HTTPError, ValueError, KeyError, TypeError, InvalidOperation) as exc:
                log.warning("Frankfurter rates unavailable: %s", type(exc).__name__)
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
