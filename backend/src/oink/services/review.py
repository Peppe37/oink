"""«Look at my movements and tell me what to fix»: one pass over a period that proposes,
by rules first and the AI only for what rules can't tell:

- savings plans (services.plans.proposals);
- fixed costs: the same payee about once a month (or year) with a steady amount, not yet a
  fixed cost;
- categories: a movement without one, or «other», gets the category the same payee has
  elsewhere in the books; a payee paid every month is a subscription or a bill; an
  anonymous debit on a broker's account is an investment; what's left goes to the AI.

Nothing is changed here: the person picks what to apply."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from oink.core.db import Account, FixedCost, Transaction, User
from oink.core.money import from_minor
from oink.services.ai import AI, AIError
from oink.services.ledger import CATEGORIES, INTERNAL, normalise
from oink.services.plans import BROKERS, proposals

UNSET = ("", "other")


def payee(t: Transaction) -> str:
    key = normalise(t.counterparty or t.description)
    return " ".join(w for w in key.split() if not any(c.isdigit() for c in w))[:40]


async def review(
    db: Session, user: User, ai: AI, start: date | None, end: date | None
) -> dict[str, Any]:
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.user_id == user.id))}
    every = list(
        db.scalars(
            select(Transaction).where(
                Transaction.user_id == user.id, Transaction.source != "pending"
            )
        )
    )
    period = [
        t for t in every if (start is None or t.day >= start) and (end is None or t.day <= end)
    ]

    # what each payee usually is, from the movements already sorted (by the person, rules…)
    usual: dict[str, Counter[str]] = defaultdict(Counter)
    for t in every:
        if t.category and t.category not in UNSET and t.category_by in ("user", "rule", "ai"):
            usual[payee(t)][t.category] += 2 if t.category_by == "user" else 1

    # recurring payees (fixed costs and subscriptions)
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for t in every:
        if t.amount < 0 and t.category not in INTERNAL and payee(t):
            groups[payee(t)].append(t)
    known = [
        (f.match or f.name).lower()
        for f in db.scalars(select(FixedCost).where(FixedCost.user_id == user.id))
    ]
    fixed: list[dict[str, Any]] = []
    recurring: set[str] = set()
    for key, txs in groups.items():
        months = {(t.day.year, t.day.month) for t in txs}
        if len(months) < 2 or len(txs) > len(months) + 1:
            continue
        amounts = [-t.amount for t in txs]
        typical = median(amounts)
        if max(amounts) > typical * 1.5 or min(amounts) < typical / 1.5:
            continue  # not one steady price
        recurring.add(key)
        if any(k and (k in key or key in k) for k in known):
            continue
        last = max(txs, key=lambda t: t.day)
        gaps = sorted(
            (b.day - a.day).days
            for a, b in zip(
                sorted(txs, key=lambda t: t.day), sorted(txs, key=lambda t: t.day)[1:], strict=False
            )
        )
        frequency = (
            "yearly"
            if gaps and median(gaps) > 300
            else "quarterly"
            if gaps and median(gaps) > 80
            else "monthly"
        )
        if not any(start is None or t.day >= start for t in txs):
            continue
        fixed.append({
            "name": (last.counterparty or last.description)[:60], "match": key,
            "amount": str(from_minor(int(typical), last.currency)), "currency": last.currency,
            "frequency": frequency, "category": last.category if last.category not in UNSET else "subscriptions",
            "months": len(months), "last": last.day.isoformat(),
        })  # fmt: skip

    categories: list[dict[str, Any]] = []
    unknown: list[Transaction] = []
    for t in period:
        if t.category_by == "user" or t.category not in UNSET or t.transfer_id or t.plan_id:
            continue
        key = payee(t)
        account = accounts.get(t.account_id)
        proposed, reason = "", ""
        if key and usual.get(key):
            proposed, reason = usual[key].most_common(1)[0][0], "same_payee"
        elif t.amount < 0 and account is not None and any(b in f"{account.institution} {account.name}".lower() for b in BROKERS) \
                and not (t.description or "").strip("—- "):  # fmt: skip
            proposed, reason = "investments", "broker_debit"
        elif key in recurring:
            proposed, reason = "subscriptions", "recurring"
        if proposed and proposed != t.category:
            categories.append(_cat(t, proposed, reason, accounts))
        elif not proposed:
            unknown.append(t)
    if unknown and ai.can_read_text:
        try:
            found = await ai.categorize(
                [
                    (t.id, f"{t.counterparty} {t.description}".strip(), str(t.amount))
                    for t in unknown[:200]
                ]
            )
        except AIError:
            found = {}
        by_id = {t.id: t for t in unknown}
        for tx_id, category in found.items():
            if tx_id in by_id and category in CATEGORIES and category != by_id[tx_id].category:
                categories.append(_cat(by_id[tx_id], category, "ai", accounts))
    return {
        "plans": await proposals(db, user),
        "fixed": sorted(fixed, key=lambda f: -float(f["amount"])),
        "categories": sorted(categories, key=lambda c: c["day"], reverse=True),
        "checked": len(period),
    }


def _cat(
    t: Transaction, proposed: str, reason: str, accounts: dict[int, Account]
) -> dict[str, Any]:
    return {"tx_id": t.id, "day": t.day.isoformat(), "name": (t.counterparty or t.description)[:80],
            "amount": str(from_minor(t.amount, t.currency)), "currency": t.currency,
            "account": accounts[t.account_id].name if t.account_id in accounts else "",
            "current": t.category, "proposed": proposed, "reason": reason}  # fmt: skip
