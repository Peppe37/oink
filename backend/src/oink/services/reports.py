"""Numbers, computed here (never by the AI): month summary, categories, trends, net worth,
recurring payments. Everything is per user and in the user's base currency: movements in
other currencies (or crypto) are converted at the latest known rate. Amounts in the output
are plain numbers in the base currency («12.5» = 12.50 €)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from oink.core.db import Account, Transaction, User
from oink.core.money import from_minor
from oink.services.fx import Rates
from oink.services.ledger import INTERNAL, normalise


def month_bounds(month: str) -> tuple[date, date]:
    year, mon = (int(x) for x in month.split("-"))
    start = date(year, mon, 1)
    end = date(year + (mon == 12), mon % 12 + 1, 1)
    return start, end


def previous_months(month: str, n: int) -> list[str]:
    start, _ = month_bounds(month)
    months, day = [], start
    for _ in range(n):
        day = (day - timedelta(days=1)).replace(day=1)
        months.append(day.strftime("%Y-%m"))
    return months


@dataclass
class Book:
    """One user's money, converted: what every report reads."""

    db: Session
    user: User
    rates: Rates

    @classmethod
    def of(cls, db: Session, user: User) -> Book:
        return cls(db, user, Rates.load(db))

    @property
    def base(self) -> str:
        return self.user.base_currency

    def value(self, minor: int, currency: str) -> float | None:
        """Minor units of `currency` → number in the base currency (None: no rate yet)."""
        converted = self.rates.convert(minor, currency, self.base)
        return None if converted is None else float(from_minor(converted, self.base))

    def rows(self, start: date, end: date) -> list[tuple[Transaction, float]]:
        accounts = select(Account.id).where(
            Account.user_id == self.user.id, Account.archived.is_(False)
        )
        txs = self.db.scalars(
            select(Transaction).where(
                Transaction.user_id == self.user.id,
                Transaction.day >= start,
                Transaction.day < end,
                Transaction.account_id.in_(accounts),
                Transaction.category_by != "split",  # split: its parts count instead
            )
        )
        out = []
        for t in txs:
            v = self.value(t.amount, t.currency)
            if v is not None:
                out.append((t, v))
        return out

    def accounts(self) -> list[Account]:
        return list(
            self.db.scalars(
                select(Account)
                .where(Account.user_id == self.user.id, Account.archived.is_(False))
                .order_by(Account.created)
            )
        )


def _r(value: float) -> float:
    return round(value, 2)


def month_summary(book: Book, month: str) -> dict[str, Any]:
    start, end = month_bounds(month)
    rows = book.rows(start, end)
    spending = [(t, v) for t, v in rows if v < 0 and t.category not in INTERNAL]
    income = [(t, v) for t, v in rows if v > 0 and t.category not in INTERNAL]
    by_category: dict[str, float] = defaultdict(float)
    by_merchant: dict[str, float] = defaultdict(float)
    for t, v in spending:
        by_category[t.category or ""] += -v
        by_merchant[(t.counterparty or t.description)[:40]] += -v
    out, inn = sum(-v for _, v in spending), sum(v for _, v in income)
    return {
        "month": month,
        "income": _r(inn),
        "spending": _r(out),
        "net": _r(inn - out),
        "saving_rate": round((inn - out) / inn, 3) if inn else None,
        "count": len(rows),
        "by_category": {k: _r(v) for k, v in sorted(by_category.items(), key=lambda kv: -kv[1])},
        "top_merchants": {
            k: _r(v) for k, v in sorted(by_merchant.items(), key=lambda kv: -kv[1])[:8]
        },
        "biggest": [
            {"day": t.day.isoformat(), "amount": _r(v), "what": t.description[:60]}
            for t, v in sorted(spending, key=lambda tv: tv[1])[:5]
        ],
        "uncategorised": sum(1 for t, _ in rows if not t.category),
    }


def compare(book: Book, month: str, history: int = 3) -> dict[str, Any]:
    """This month against the median of the previous `history` months, per category."""
    current = month_summary(book, month)
    past = [month_summary(book, m) for m in previous_months(month, history)]
    past = [p for p in past if p["count"]]
    categories = set(current["by_category"]).union(*(p["by_category"] for p in past))
    changes = {}
    for cat in categories:
        before = median([p["by_category"].get(cat, 0) for p in past]) if past else 0
        now = current["by_category"].get(cat, 0)
        if before or now:
            changes[cat] = {"now": _r(now), "usual": _r(before), "delta": _r(now - before)}
    return {
        "currency": book.base,
        "current": current,
        "usual_spending": _r(median([p["spending"] for p in past])) if past else None,
        "usual_income": _r(median([p["income"] for p in past])) if past else None,
        "categories": dict(sorted(changes.items(), key=lambda kv: -abs(kv[1]["delta"]))),
    }


def recurring(book: Book, months: int = 4, today: date | None = None) -> list[dict[str, Any]]:
    """Payments to the same counterparty about once a month with a similar amount:
    subscriptions, bills, rent. Flags the ones whose amount went up."""
    today = today or date.today()
    window = book.rows(today - timedelta(days=31 * months + 5), today + timedelta(days=1))
    rows = [(t, v) for t, v in window if v < 0 and t.category not in INTERNAL]
    groups: dict[str, list[tuple[Transaction, float]]] = defaultdict(list)
    for t, v in rows:
        key = normalise(t.counterparty or t.description)
        key = " ".join(w for w in key.split() if not any(c.isdigit() for c in w))[:40]
        if key:
            groups[key].append((t, v))
    found = []
    for items in groups.values():
        months_seen = {t.day.strftime("%Y-%m") for t, _ in items}
        if len(months_seen) < 3 or len(items) > len(months_seen) + 1:
            continue
        ordered = sorted(items, key=lambda tv: tv[0].day)
        amounts = [-v for _, v in ordered]
        typical = median(amounts)
        if max(amounts) > typical * 1.6:  # not one fixed price
            continue
        last, last_v = ordered[-1]
        found.append(
            {
                "name": (last.counterparty or last.description)[:60],
                "monthly": _r(typical),
                "last": _r(-last_v),
                "increased": amounts[-1] > amounts[0] * 1.03,
                "category": last.category,
                "last_day": last.day.isoformat(),
            }
        )
    return sorted(found, key=lambda r: -r["monthly"])


def net_worth(book: Book) -> dict[str, Any]:
    total, missing, accounts = 0.0, [], []
    for a in book.accounts():
        value = book.value(a.balance, a.currency) if a.balance is not None else None
        if value is not None:
            total += value
        elif a.balance is not None:
            missing.append(a.currency)
        accounts.append({"id": a.id, "name": a.name, "kind": a.kind, "value": value})
    return {
        "currency": book.base,
        "total": _r(total),
        "accounts": accounts,
        "missing_rates": sorted(set(missing)),
    }


def series(book: Book, months: int = 12, today: date | None = None) -> list[dict[str, Any]]:
    """Income and spending per month, oldest first (for the charts)."""
    today = today or date.today()
    current = today.strftime("%Y-%m")
    out = []
    for month in [*reversed(previous_months(current, months - 1)), current]:
        s = month_summary(book, month)
        out.append({"month": month, "income": s["income"], "spending": s["spending"]})
    return out


def balance_history(book: Book, days: int = 180, today: date | None = None) -> list[dict[str, Any]]:
    """Total balance per day, rebuilt backwards from today's balances and the movements."""
    today = today or date.today()
    total = net_worth(book)["total"]
    per_day: dict[date, float] = defaultdict(float)
    for t, v in book.rows(today - timedelta(days=days - 1), today + timedelta(days=1)):
        per_day[t.day] += v
    points, running = [], total
    for i in range(days + 1):
        day = today - timedelta(days=i)
        points.append({"day": day.isoformat(), "value": _r(running)})
        running -= per_day.get(day, 0.0)
    return list(reversed(points))


def facts_for_ai(book: Book, month: str, *, with_text: bool) -> dict[str, Any]:
    """What the AI may see. Without text: only totals per category."""
    data = compare(book, month)
    data["recurring"] = recurring(book)
    data["net_worth"] = net_worth(book)["total"]
    data["note"] = f"Amounts in {book.base}; negative = money out."
    if not with_text:
        data["current"].pop("top_merchants", None)
        data["current"].pop("biggest", None)
        for r in data["recurring"]:
            r["name"] = r["category"] or "recurring"
    return data
