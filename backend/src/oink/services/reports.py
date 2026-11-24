"""Numbers, computed here (never by the AI): month summary, categories, trends, net worth,
recurring payments. The AI only turns these facts into words and advice."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from oink.core.db import Account, Transaction
from oink.services.ledger import normalise

INTERNAL = {"trasferimenti", "risparmio", "investimenti"}  # moving money, not spending it


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


def _rows(db: Session, start: date, end: date) -> list[Transaction]:
    accounts = select(Account.id).where(Account.archived.is_(False))
    return list(
        db.scalars(
            select(Transaction).where(
                Transaction.day >= start,
                Transaction.day < end,
                Transaction.account_id.in_(accounts),
            )
        )
    )


def month_summary(db: Session, month: str) -> dict[str, Any]:
    start, end = month_bounds(month)
    rows = _rows(db, start, end)
    spending = [t for t in rows if t.amount_cents < 0 and t.category not in INTERNAL]
    income = [t for t in rows if t.amount_cents > 0 and t.category not in INTERNAL]
    by_category: dict[str, int] = defaultdict(int)
    by_merchant: dict[str, int] = defaultdict(int)
    for t in spending:
        by_category[t.category or "da classificare"] += -t.amount_cents
        by_merchant[(t.counterparty or t.description)[:40]] += -t.amount_cents
    out, inn = sum(-t.amount_cents for t in spending), sum(t.amount_cents for t in income)
    days = {t.day for t in spending}
    return {
        "month": month,
        "income_cents": inn,
        "spending_cents": out,
        "net_cents": inn - out,
        "saving_rate": round((inn - out) / inn, 3) if inn else None,
        "count": len(rows),
        "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
        "top_merchants": dict(sorted(by_merchant.items(), key=lambda kv: -kv[1])[:8]),
        "biggest": [
            {"day": t.day.isoformat(), "cents": t.amount_cents, "what": t.description[:60]}
            for t in sorted(spending, key=lambda t: t.amount_cents)[:5]
        ],
        "spending_days": len(days),
        "uncategorised": sum(1 for t in rows if not t.category),
    }


def compare(db: Session, month: str, history: int = 3) -> dict[str, Any]:
    """This month against the median of the previous `history` months, per category."""
    current = month_summary(db, month)
    past = [month_summary(db, m) for m in previous_months(month, history)]
    past = [p for p in past if p["count"]]
    categories = set(current["by_category"]).union(*(p["by_category"] for p in past))
    changes = {}
    for cat in categories:
        before = median([p["by_category"].get(cat, 0) for p in past]) if past else 0
        now = current["by_category"].get(cat, 0)
        if before or now:
            changes[cat] = {
                "now_cents": now,
                "usual_cents": int(before),
                "delta_cents": int(now - before),
            }
    return {
        "current": current,
        "usual_spending_cents": int(median([p["spending_cents"] for p in past])) if past else None,
        "usual_income_cents": int(median([p["income_cents"] for p in past])) if past else None,
        "categories": dict(sorted(changes.items(), key=lambda kv: -abs(kv[1]["delta_cents"]))),
    }


def recurring(db: Session, months: int = 4, today: date | None = None) -> list[dict[str, Any]]:
    """Payments to the same counterparty about once a month with a similar amount:
    subscriptions, bills, rent. Flags the ones whose amount went up."""
    today = today or date.today()
    window = _rows(db, today - timedelta(days=31 * months + 5), today + timedelta(days=1))
    rows = [t for t in window if t.amount_cents < 0 and t.category not in INTERNAL]
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for t in rows:
        key = normalise(t.counterparty or t.description)
        key = " ".join(w for w in key.split() if not any(c.isdigit() for c in w))[:40]
        if key:
            groups[key].append(t)
    found = []
    for key, items in groups.items():
        months_seen = {t.day.strftime("%Y-%m") for t in items}
        if len(months_seen) < 3 or len(items) > len(months_seen) + 1:
            continue
        amounts = [-t.amount_cents for t in sorted(items, key=lambda t: t.day)]
        typical = median(amounts)
        if max(amounts) > typical * 1.6:  # not one fixed price
            continue
        last = max(items, key=lambda t: t.day)
        found.append(
            {
                "name": (last.counterparty or last.description)[:60],
                "monthly_cents": int(typical),
                "last_cents": -last.amount_cents,
                "increased": amounts[-1] > amounts[0] * 1.03,
                "category": last.category,
                "last_day": last.day.isoformat(),
                "key": key,
            }
        )
    return sorted(found, key=lambda r: -r["monthly_cents"])


def net_worth(db: Session) -> dict[str, Any]:
    accounts = list(db.scalars(select(Account).where(Account.archived.is_(False))))
    total = sum(a.balance_cents or 0 for a in accounts)
    return {
        "total_cents": total,
        "accounts": [
            {
                "id": a.id,
                "name": a.name,
                "kind": a.kind,
                "balance_cents": a.balance_cents,
                "balance_at": a.balance_at,
            }
            for a in accounts
        ],
    }


def series(db: Session, months: int = 12, today: date | None = None) -> list[dict[str, Any]]:
    """Income and spending per month, oldest first (for the charts)."""
    today = today or date.today()
    current = today.strftime("%Y-%m")
    out = []
    for month in [*reversed(previous_months(current, months - 1)), current]:
        s = month_summary(db, month)
        out.append(
            {
                "month": month,
                "income_cents": s["income_cents"],
                "spending_cents": s["spending_cents"],
            }
        )
    return out


def balance_history(
    db: Session, days: int = 180, today: date | None = None
) -> list[dict[str, Any]]:
    """Total balance per day, rebuilt backwards from today's balances and the movements."""
    today = today or date.today()
    total = net_worth(db)["total_cents"]
    start = today - timedelta(days=days)
    per_day = dict(
        db.execute(
            select(Transaction.day, func.sum(Transaction.amount_cents))
            .where(Transaction.day > start)
            .group_by(Transaction.day)
        ).all()
    )
    points, running = [], total
    for i in range(days + 1):
        day = today - timedelta(days=i)
        points.append({"day": day.isoformat(), "cents": running})
        running -= per_day.get(day, 0) or 0
    return list(reversed(points))


def facts_for_ai(db: Session, month: str, *, with_text: bool) -> dict[str, Any]:
    """What the AI may see. Without text: only totals per category."""
    data = compare(db, month)
    data["recurring"] = [{k: v for k, v in r.items() if k != "key"} for r in recurring(db)]
    data["net_worth_cents"] = net_worth(db)["total_cents"]
    data["note"] = "Importi in centesimi di euro; negativi = uscite."
    if not with_text:
        data["current"].pop("top_merchants", None)
        data["current"].pop("biggest", None)
        for r in data["recurring"]:
            r["name"] = r["category"] or "ricorrente"
    return data
