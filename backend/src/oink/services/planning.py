"""The whole picture for planning and advice: what you own (accounts + investments), what
comes back every month (fixed costs), how you live (from the movements), how much risk you
want (the questionnaire) and the plans you are running. All computed here; the AI only
explains and suggests from these facts."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from oink.core.db import FixedCost, Holding, Plan, Quote, RiskProfile
from oink.services.markets import MarketError, quote
from oink.services.reports import Book, month_summary, previous_months, recurring

log = logging.getLogger("oink.planning")
PER_MONTH = {"weekly": 52 / 12, "monthly": 1.0, "quarterly": 1 / 3, "yearly": 1 / 12}
ESSENTIAL = {"home", "bills", "groceries", "health", "transport", "car", "taxes", "fees",
             "education"}  # fmt: skip
LIQUID = {"checking", "savings", "cash"}

#: The questionnaire: id → answers score 1 (cautious) … 5 (bold). Texts live in the web app.
QUESTIONS = ("horizon", "drop", "experience", "income", "goal")
PROFILES = {1: "conservative", 2: "cautious", 3: "balanced", 4: "growth", 5: "aggressive"}
#: A reasonable stocks share for each profile (the rest in bonds/cash), a starting point.
EQUITY_SHARE = {1: 0.15, 2: 0.35, 3: 0.55, 4: 0.75, 5: 0.9}


def to_base(book: Book, amount: float | Decimal, currency: str) -> float | None:
    """An amount in any currency → number in the user's base currency."""
    from oink.core.money import MoneyError, to_minor

    try:
        return book.value(to_minor(Decimal(str(amount)), currency), currency)
    except MoneyError:
        return None


def score(answers: dict[str, int]) -> int:
    values = [min(5, max(1, int(answers[q]))) for q in QUESTIONS if q in answers]
    return round(sum(values) / len(values)) if values else 3


async def holdings_value(db: Session, book: Book, refresh: bool = True) -> dict[str, Any]:
    """Every holding priced and converted to the base currency."""
    rows = list(db.scalars(select(Holding).where(Holding.user_id == book.user.id)))
    items: list[dict[str, Any]] = []
    total = cost_total = 0.0
    for h in rows:
        price, currency, stale = h.manual_price, h.currency, False
        if price is None:
            try:
                q = await quote(db, h.symbol) if refresh else db.get(Quote, h.symbol.upper())
                if q is not None:
                    price, currency = q.price, q.currency or h.currency
            except MarketError:
                cached = db.get(Quote, h.symbol.upper())
                price, stale = (cached.price, True) if cached else (None, True)
        value_native = float(price * h.quantity) if price is not None else None
        value = to_base(book, value_native, currency) if value_native is not None else None
        cost = to_base(book, h.cost, h.currency) if h.cost is not None else None
        if value is not None:
            total += value
            cost_total += cost or 0.0
        items.append(
            {
                "id": h.id,
                "symbol": h.symbol,
                "name": h.name,
                "kind": h.kind,
                "quantity": str(h.quantity),
                "price": str(price) if price is not None else None,
                "currency": currency,
                "value": round(value, 2) if value is not None else None,
                "cost": round(cost, 2) if cost is not None else None,
                "gain": round(value - cost, 2) if value is not None and cost is not None else None,
                "stale": stale,
            }
        )
    by_kind: dict[str, float] = defaultdict(float)
    for i in items:
        if isinstance(i["value"], float):
            by_kind[str(i["kind"])] += i["value"]
    return {
        "currency": book.base,
        "total": round(total, 2),
        "cost": round(cost_total, 2),
        "gain": round(total - cost_total, 2) if cost_total else None,
        "by_kind": {k: round(v, 2) for k, v in by_kind.items()},
        "items": items,
    }


def fixed_costs(db: Session, book: Book) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(FixedCost).where(FixedCost.user_id == book.user.id).order_by(FixedCost.name)
        )
    )
    items, monthly = [], 0.0
    for f in rows:
        per_month_native = float(f.amount) * PER_MONTH.get(f.frequency, 1.0)
        value = to_base(book, per_month_native, f.currency)
        if f.active and value is not None:
            monthly += value
        items.append(
            {
                "id": f.id,
                "name": f.name,
                "amount": str(f.amount),
                "currency": f.currency,
                "frequency": f.frequency,
                "category": f.category,
                "next_due": f.next_due.isoformat() if f.next_due else None,
                "match": f.match,
                "active": f.active,
                "per_month": round(value, 2) if value is not None else None,
            }
        )
    known = {f.match.lower() or f.name.lower() for f in rows}
    suggestions = [
        r for r in recurring(book) if not any(k and k in r["name"].lower() for k in known)
    ]
    return {"currency": book.base, "monthly": round(monthly, 2), "yearly": round(monthly * 12, 2),
            "items": items, "suggestions": suggestions[:8]}  # fmt: skip


def lifestyle(book: Book, months: int = 6, today: date | None = None) -> dict[str, Any]:
    """How the user lives, from the last full months of movements."""
    today = today or date.today()
    current = today.strftime("%Y-%m")
    summaries = [month_summary(book, m) for m in previous_months(current, months)]
    summaries = [s for s in summaries if s["count"]]
    if not summaries:
        return {"months": 0}
    spending = [s["spending"] for s in summaries]
    income = [s["income"] for s in summaries]
    essential = discretionary = 0.0
    by_category: dict[str, float] = defaultdict(float)
    for s in summaries:
        for cat, v in s["by_category"].items():
            by_category[cat] += v / len(summaries)
            if cat in ESSENTIAL:
                essential += v
            else:
                discretionary += v
    total = essential + discretionary
    avg_spend, avg_income = median(spending), median(income)
    return {
        "months": len(summaries),
        "income": round(avg_income, 2),
        "spending": round(avg_spend, 2),
        "saving_rate": round((avg_income - avg_spend) / avg_income, 3) if avg_income else None,
        "essential_share": round(essential / total, 3) if total else None,
        "top_categories": {
            k: round(v, 2) for k, v in sorted(by_category.items(), key=lambda kv: -kv[1])[:8]
        },
        "income_stability": round(min(income) / max(income), 2) if income and max(income) else None,
    }


async def picture(db: Session, book: Book) -> dict[str, Any]:
    """Everything for the planner and the AI advisor, in the base currency."""
    from oink.services.reports import net_worth

    worth = net_worth(book)
    liquid = sum(a["value"] or 0 for a in worth["accounts"] if a["kind"] in LIQUID and a["value"])
    invest = await holdings_value(db, book)
    fixed = fixed_costs(db, book)
    life = lifestyle(book)
    profile = db.get(RiskProfile, book.user.id)
    plans = list(
        db.scalars(select(Plan).where(Plan.user_id == book.user.id, Plan.active.is_(True)))
    )
    plan_monthly = 0.0
    for p in plans:
        v = to_base(book, float(p.amount) * PER_MONTH.get(p.frequency, 1.0), p.currency)
        plan_monthly += v or 0.0
    spending = life.get("spending") or fixed["monthly"] or 0
    risk = profile.score if profile else None
    return {
        "currency": book.base,
        "net_worth": round(worth["total"] + invest["total"], 2),
        "cash_and_accounts": worth["total"],
        "liquid": round(liquid, 2),
        "investments": invest["total"],
        "investments_by_kind": invest["by_kind"],
        "emergency_months": round(liquid / spending, 1) if spending else None,
        "fixed_costs_monthly": fixed["monthly"],
        "fixed_share_of_income": round(fixed["monthly"] / life["income"], 3)
        if life.get("income")
        else None,
        "plans": [
            {
                "symbol": p.symbol,
                "name": p.name,
                "amount": str(p.amount),
                "currency": p.currency,
                "frequency": p.frequency,
                "since": p.start.isoformat(),
            }
            for p in plans
        ],
        "plans_monthly": round(plan_monthly, 2),
        "lifestyle": life,
        "risk": {
            "score": risk,
            "profile": PROFILES.get(risk or 0),
            "horizon_years": profile.horizon_years if profile else None,
            "goals": profile.goals if profile else "",
            "suggested_equity_share": EQUITY_SHARE.get(risk or 0),
        },
    }


def due_soon(db: Session, user_id: int, days: int = 7) -> list[FixedCost]:
    today = date.today()
    return list(
        db.scalars(
            select(FixedCost).where(
                FixedCost.user_id == user_id,
                FixedCost.active.is_(True),
                FixedCost.next_due.is_not(None),
                FixedCost.next_due <= today + timedelta(days=days),
            )
        )
    )


def advance_due(cost: FixedCost) -> None:
    """Move next_due past today, by the cost's period."""
    if cost.next_due is None:
        return
    step = {"weekly": 7, "monthly": 0, "quarterly": 0, "yearly": 0}[cost.frequency]
    months = {"monthly": 1, "quarterly": 3, "yearly": 12}.get(cost.frequency, 0)
    while cost.next_due < date.today():
        if step:
            cost.next_due += timedelta(days=step)
        else:
            y, m = divmod(cost.next_due.month - 1 + months, 12)
            day = min(cost.next_due.day, 28)
            cost.next_due = cost.next_due.replace(year=cost.next_due.year + y, month=m + 1, day=day)


_ = Decimal  # re-exported type for callers
