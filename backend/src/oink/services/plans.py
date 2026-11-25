"""An accumulation plan (PAC) is seen in the movements of the account it is paid from.

A broker's cash account read through the bank link (Trade Republic) shows each purchase
only as a date and an amount. Given the plan (security, amount, day of the month, account),
Oink matches those debits deterministically: same amount (to the cent), within a few days of
the plan's day, one per period, from the start to the end of the plan. A matched debit is
an investment, never spending, and the plan's units are worked out from the security's
close on that day: «about 2.31 shares bought by the plan since it started».

Debits that repeat with the same amount around the same day of the month and match no plan
are offered as plans to set up."""

from __future__ import annotations

import calendar
import logging
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from oink.core.db import Account, FixedCost, Holding, Plan, Transaction, User
from oink.core.money import MoneyError, from_minor, to_minor
from oink.services.ledger import INTERNAL
from oink.services.markets import MarketError, close_on, daily_closes

log = logging.getLogger("oink.plans")
BEFORE, AFTER = 2, 6  # days around the plan's day a purchase can be booked
STEP = {"weekly": 7}


def expected_days(plan: Plan, until: date) -> list[date]:
    """When the plan should have bought, from its start to its end (or `until`)."""
    last = min(plan.end or until, until)
    day = plan.day or plan.start.day
    out: list[date] = []
    if plan.frequency == "weekly":
        current = plan.start
        while current <= last:
            out.append(current)
            current += timedelta(days=7)
        return out
    months = {"monthly": 1, "quarterly": 3, "yearly": 12}.get(plan.frequency, 1)
    year, month = plan.start.year, plan.start.month
    while True:
        current = date(year, month, min(day, calendar.monthrange(year, month)[1]))
        if current > last:
            break
        if current >= plan.start - timedelta(days=BEFORE):
            out.append(current)
        month += months
        year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
    return out


def match_executions(db: Session, user: User, today: date | None = None) -> int:
    """Link each plan's purchases in its account; returns how many were newly matched."""
    today = today or date.today()
    plans = [
        p
        for p in db.scalars(
            select(Plan).where(Plan.user_id == user.id, Plan.account_id.is_not(None))
        )
    ]
    matched = 0
    for plan in plans:
        account = db.get(Account, plan.account_id)
        if account is None:
            continue
        try:
            amount = -to_minor(plan.amount, account.currency)
        except MoneyError:
            continue
        days = expected_days(plan, today)
        if not days:
            continue
        candidates = list(
            db.scalars(
                select(Transaction).where(
                    Transaction.account_id == account.id,
                    Transaction.amount.between(amount - 1, amount + 1),
                    Transaction.day >= days[0] - timedelta(days=BEFORE),
                    Transaction.day <= days[-1] + timedelta(days=AFTER),
                )
            )
        )
        taken = {t.day for t in candidates if t.plan_id == plan.id}
        free = [
            t
            for t in candidates
            if t.plan_id is None and (t.category_by != "user" or t.category in INTERNAL)
        ]
        for expected in days:
            if any(
                expected - timedelta(days=BEFORE) <= d <= expected + timedelta(days=AFTER)
                for d in taken
            ):
                continue
            window = [
                t
                for t in free
                if expected - timedelta(days=BEFORE) <= t.day <= expected + timedelta(days=AFTER)
            ]
            if not window:
                continue
            best = min(window, key=lambda t: (abs((t.day - expected).days), t.id))
            best.plan_id, best.category, best.category_by = plan.id, "investments", "plan"
            free.remove(best)
            taken.add(best.day)
            matched += 1
    db.commit()
    return matched


async def plan_positions(db: Session, user: User) -> dict[int, dict[str, Any]]:
    """Per plan: executions seen, money invested, units bought (from each day's close)."""
    out: dict[int, dict[str, Any]] = {}
    for plan in db.scalars(select(Plan).where(Plan.user_id == user.id)):
        txs = list(
            db.scalars(
                select(Transaction).where(Transaction.plan_id == plan.id).order_by(Transaction.day)
            )
        )
        invested = sum((-from_minor(t.amount, t.currency) for t in txs), Decimal(0))
        units: Decimal | None = Decimal(0)
        if txs:
            try:
                closes = await daily_closes(plan.symbol)
            except MarketError:
                closes = {}
            for t in txs:
                price = close_on(closes, t.day)
                if price is None or price <= 0 or units is None:
                    units = None
                    continue
                units += -from_minor(t.amount, t.currency) / price
        out[plan.id] = {
            "executions": len(txs),
            "invested": str(invested),
            "units": str(round(units, 6)) if units is not None and txs else None,
            "last": txs[-1].day.isoformat() if txs else None,
            "days": [t.day.isoformat() for t in txs],
        }
    return out


def extra_units(
    db: Session, plan: Plan, holding: Holding | None, positions: dict[str, Any]
) -> Decimal:
    """Units the plan added on top of a holding typed in by hand (bought after it was)."""
    if positions.get("units") is None:
        return Decimal(0)
    if holding is None:
        return Decimal(positions["units"])
    since = holding.created.date() if holding.created else date.min
    share = [d for d in positions["days"] if date.fromisoformat(d) > since]
    if not positions["days"]:
        return Decimal(0)
    return Decimal(positions["units"]) * len(share) / len(positions["days"])


#: Where plans are bought: brokers and investment apps (their cash accounts).
BROKERS = ("trade republic", "scalable", "directa", "degiro", "fineco", "young platform",
           "bitpanda", "kraken", "binance", "coinbase", "etoro", "moneyfarm", "satispay",
           "revolut invest", "trading 212", "xtb", "freedom24", "interactive brokers")  # fmt: skip
#: Wording of a purchase in a plan, in the usual languages.
PLAN_WORDS = (
    "sparplan",
    "savings plan",
    "piano di accumulo",
    "pac ",
    "esecuzione piano",
    "buy trade",
    "acquisto",
    "kauf",
    "achat",
    "compra",
    "isin",
    "etf",
    "invest",
)
ISIN = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}\d\b")


def _looks_like_purchase(t: Transaction, account: Account | None) -> bool:
    """Only a broker's account, and only debits worded like a purchase or not worded at
    all (Trade Republic through the bank link gives no description): never a bill, a
    subscription or a fixed cost of a current account."""
    text = f" {(t.description or '').lower()} {(t.counterparty or '').lower()} "
    broker = account is not None and (
        account.kind == "investment"
        or any(b in f" {account.institution} {account.name} ".lower() for b in BROKERS)
    )
    worded = any(w in text for w in PLAN_WORDS) or bool(ISIN.search(t.description or ""))
    blank = not (t.description or "").strip("—- ") and not (t.counterparty or "").strip()
    already = t.category_by in ("user", "rule") and t.category not in ("", "investments", "other")
    return not already and (
        (broker and (blank or worded)) or (worded and bool(ISIN.search(text.upper())))
    )


def suggestions(db: Session, user: User, today: date | None = None) -> list[dict[str, Any]]:
    """Purchases repeating every month with the same amount around the same day, on a
    broker's account, matched by no plan: probably a savings plan to set up."""
    today = today or date.today()
    since = today - timedelta(days=200)
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.user_id == user.id))}
    fixed = [
        f.match.lower()
        for f in db.scalars(select(FixedCost).where(FixedCost.user_id == user.id))
        if f.match
    ]
    groups: dict[tuple[int, int, int], list[Transaction]] = defaultdict(list)
    for t in db.scalars(
        select(Transaction).where(
            Transaction.user_id == user.id,
            Transaction.amount < 0,
            Transaction.day >= since,
            Transaction.plan_id.is_(None),
            Transaction.transfer_id.is_(None),
        )
    ):
        if t.category_by == "transfer" or t.source == "pending":
            continue
        if any(m in f"{t.description} {t.counterparty}".lower() for m in fixed):
            continue  # already a fixed cost
        if not _looks_like_purchase(t, accounts.get(t.account_id)):
            continue
        groups[(t.account_id, t.amount, (t.day.day - 1) // 5)].append(t)
    out = []
    for (account_id, amount, _bucket), txs in groups.items():
        months = {(t.day.year, t.day.month) for t in txs}
        if len(months) < 2:
            continue
        account = accounts.get(account_id)
        out.append({
            "account_id": account_id, "account": account.name if account else "",
            "amount": str(-from_minor(amount, txs[0].currency)), "currency": txs[0].currency,
            "day": round(sum(t.day.day for t in txs) / len(txs)), "months": len(months),
            "per_month": round(len(txs) / len(months)),
            "last": max(t.day for t in txs).isoformat(),
        })  # fmt: skip
    return sorted(out, key=lambda s: (-s["months"], s["account"]))


def link_purchase(db: Session, tx: Transaction, plan: Plan) -> None:
    """The person says: this debit is a purchase of that plan."""
    tx.plan_id, tx.category, tx.category_by = plan.id, "investments", "plan"
    if tx.transfer_id is not None:
        from oink.services.transfers import unlink

        unlink(db, tx)
    db.commit()


def unlink_purchase(db: Session, tx: Transaction) -> None:
    tx.plan_id = None
    if tx.category_by == "plan":
        tx.category, tx.category_by = "", "user"  # not a purchase: don't match it again
    db.commit()


_SYMBOLS: dict[str, dict[str, str] | None] = {}  # ISIN → market symbol, found once


async def _symbol_for(isin: str) -> dict[str, str] | None:
    if isin not in _SYMBOLS:
        from oink.services.markets import search

        try:
            hits = await search(isin)
        except MarketError:
            return None
        # prefer a European listing in euro when there is one (what an Italian buys)
        euro = [h for h in hits if h["symbol"].endswith((".DE", ".MI", ".AS", ".PA", ".F", ".SG"))]
        pick = euro or hits
        _SYMBOLS[isin] = pick[0] if pick else None
    return _SYMBOLS[isin]


async def proposals(db: Session, user: User, today: date | None = None) -> list[dict[str, Any]]:
    """Plans Oink found by itself, ready to add with one tap:

    - «ready»: purchases with the security's ISIN in their wording (a broker's statement),
      repeating with the same amount: security, amount, day, account and start are known;
    - «pick»: purchases on a broker's account without any wording (Trade Republic through
      the bank link): everything is known but the security."""
    today = today or date.today()
    since = today - timedelta(days=400)
    known = {
        (p.symbol.upper(), p.account_id)
        for p in db.scalars(select(Plan).where(Plan.user_id == user.id))
    }
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.user_id == user.id))}
    by_isin: dict[tuple[str, int], list[Transaction]] = defaultdict(list)
    for t in db.scalars(
        select(Transaction).where(
            Transaction.user_id == user.id,
            Transaction.amount < 0,
            Transaction.day >= since,
            Transaction.plan_id.is_(None),
        )
    ):
        found = ISIN.search(t.description or "")
        if found:
            by_isin[(found.group(0), t.account_id)].append(t)
    out: list[dict[str, Any]] = []
    for (isin, account_id), txs in by_isin.items():
        months = {(t.day.year, t.day.month) for t in txs}
        text = " ".join(t.description.lower() for t in txs)
        planned = any(w in text for w in ("sparplan", "savings plan", "piano", "plan"))
        if len(months) < 2 and not planned:
            continue  # one purchase only, not worded as a plan: a trade, not a plan
        amounts = Counter(t.amount for t in txs)
        amount = amounts.most_common(1)[0][0]
        hit = await _symbol_for(isin)
        symbol = hit["symbol"] if hit else isin
        if (symbol.upper(), account_id) in known:
            continue
        tail = txs[-1].description.split(isin, 1)[-1]
        name = (hit or {}).get("name") or re.sub(r"[,;].*$", "", tail).strip(" -:") or isin
        out.append({
            "kind": "ready", "isin": isin, "symbol": symbol, "name": name[:160],
            "amount": str(-from_minor(amount, txs[0].currency)), "currency": txs[0].currency,
            "day": round(sum(t.day.day for t in txs) / len(txs)), "account_id": account_id,
            "account": accounts[account_id].name if account_id in accounts else "",
            "start": min(t.day for t in txs).isoformat(), "purchases": len(txs),
        })  # fmt: skip
    for sg in suggestions(db, user, today):
        covered = sum(
            1
            for p in out
            if p["account_id"] == sg["account_id"]
            and p["amount"] == sg["amount"]
            and abs(p["day"] - sg["day"]) <= 3
        )
        for _ in range(max(0, sg["per_month"] - covered)):
            out.append({"kind": "pick", **sg})
    return out
