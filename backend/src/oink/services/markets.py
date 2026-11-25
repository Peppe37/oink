"""Market data, free and keyless: Yahoo Finance's public chart and search endpoints for
stocks, ETFs, funds, listed bonds, indices and crypto («VWCE.DE», «AAPL», «BTC-EUR»).

Prices are cached: the latest quote for 15 minutes, monthly history for a day. The numbers
feed the investments page, net worth, backtests, simulations and what the AI is told."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from oink.core.db import Price, Quote, aware

log = logging.getLogger("oink.markets")
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
SEARCH = "https://query2.finance.yahoo.com/v1/finance/search"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Oink self-hosted finance)"}
QUOTE_TTL = timedelta(minutes=15)
HISTORY_TTL = timedelta(hours=20)
KINDS = {"EQUITY": "stock", "ETF": "etf", "MUTUALFUND": "fund", "CRYPTOCURRENCY": "crypto",
         "INDEX": "index", "BOND": "bond", "CURRENCY": "currency", "FUTURE": "future"}  # fmt: skip


SUBUNITS = {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ILA": "ILS"}


class MarketError(RuntimeError):
    pass


async def _get(client: httpx.AsyncClient | None, url: str, **params: Any) -> Any:
    own = client is None
    client = client or httpx.AsyncClient(timeout=20, headers=HEADERS)
    try:
        r = await client.get(url, params=params, headers=HEADERS)
    except httpx.HTTPError as exc:
        raise MarketError(f"unreachable:{type(exc).__name__}") from exc
    finally:
        if own:
            await client.aclose()
    if r.status_code == 404:
        raise MarketError("symbol_unknown")
    if r.status_code >= 400:
        raise MarketError(f"http_{r.status_code}")
    return r.json()


async def search(query: str, client: httpx.AsyncClient | None = None) -> list[dict[str, str]]:
    data = await _get(client, SEARCH, q=query, quotesCount=12, newsCount=0, listsCount=0)
    out = []
    for q in data.get("quotes", []):
        if not q.get("symbol"):
            continue
        out.append(
            {
                "symbol": q["symbol"],
                "name": q.get("longname") or q.get("shortname") or q["symbol"],
                "kind": KINDS.get(str(q.get("quoteType", "")).upper(), "stock"),
                "exchange": q.get("exchDisp") or q.get("exchange") or "",
            }
        )
    return out


def _chart(data: dict[str, Any]) -> dict[str, Any]:
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        raise MarketError("symbol_unknown")
    return dict(result[0])


async def quote(db: Session, symbol: str, client: httpx.AsyncClient | None = None) -> Quote:
    symbol = symbol.strip().upper()
    row = db.get(Quote, symbol)
    if (
        row is not None
        and (aware(row.updated) or datetime.min.replace(tzinfo=UTC)) > datetime.now(UTC) - QUOTE_TTL
    ):
        return row
    try:
        chart = _chart(await _get(client, CHART.format(symbol=symbol), range="5d", interval="1d"))
    except MarketError:
        if row is not None:
            return row  # stale but better than nothing
        raise
    meta = chart.get("meta", {})
    price = meta.get("regularMarketPrice")
    if price is None:
        raise MarketError("no_price")
    value, currency = Decimal(str(price)), str(meta.get("currency") or "")
    if currency in SUBUNITS:  # London quotes in pence, Johannesburg in cents…
        currency, value = SUBUNITS[currency], value / 100
    if row is None:
        row = Quote(symbol=symbol, price=value)
        db.add(row)
    row.price = value
    row.currency = currency.upper()
    row.name = str(meta.get("longName") or meta.get("shortName") or row.name or symbol)[:160]
    row.updated = datetime.now(UTC)
    db.commit()
    return row


async def history(
    db: Session, symbol: str, years: int = 20, client: httpx.AsyncClient | None = None
) -> list[tuple[date, Decimal]]:
    """Monthly closes, oldest first (adjusted for splits and dividends when available)."""
    symbol = symbol.strip().upper()
    rows = list(db.scalars(select(Price).where(Price.symbol == symbol).order_by(Price.day)))
    fresh = db.get(Quote, f"H:{symbol}")
    if (
        rows
        and fresh
        and (aware(fresh.updated) or datetime.min.replace(tzinfo=UTC))
        > datetime.now(UTC) - HISTORY_TTL
    ):
        return [(r.day, r.close) for r in rows]
    try:
        chart = _chart(
            await _get(client, CHART.format(symbol=symbol), range=f"{years}y", interval="1mo")
        )
    except MarketError:
        if rows:
            return [(r.day, r.close) for r in rows]
        raise
    stamps = chart.get("timestamp") or []
    ind = chart.get("indicators", {})
    closes = ((ind.get("adjclose") or [{}])[0].get("adjclose")) or (
        (ind.get("quote") or [{}])[0].get("close") or []
    )
    points: dict[date, Decimal] = {}
    for ts, close in zip(stamps, closes, strict=False):
        if close is None:
            continue
        day = datetime.fromtimestamp(ts, UTC).date().replace(day=1)
        points[day] = Decimal(str(round(float(close), 6)))
    if not points:
        raise MarketError("no_history")
    db.execute(delete(Price).where(Price.symbol == symbol))
    db.add_all(Price(symbol=symbol, day=d, close=c) for d, c in points.items())
    marker = db.get(Quote, f"H:{symbol}")
    if marker is None:
        db.add(Quote(symbol=f"H:{symbol}", price=Decimal(0), updated=datetime.now(UTC)))
    else:
        marker.updated = datetime.now(UTC)
    db.commit()
    return sorted(points.items())
