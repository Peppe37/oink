"""Adding movements: one entry point for every source (linked account, statement, API,
manual), with duplicate detection and the user's category rules."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from oink.core.db import Account, Rule, Transaction

#: Built-in categories: stable ids, translated by the web app (each has its own icon).
#: Users may type any other name; it is shown as written.
CATEGORIES = [
    "groceries",
    "restaurants",
    "home",
    "bills",
    "transport",
    "car",
    "health",
    "leisure",
    "travel",
    "subscriptions",
    "shopping",
    "education",
    "gifts",
    "taxes",
    "fees",
    "loans",
    "salary",
    "income",
    "transfers",
    "savings",
    "investments",
    "crypto",
    "other",
]
#: Moving money between your own pockets is not spending.
INTERNAL = {"transfers", "savings", "investments", "crypto"}


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def fingerprint(
    day: date, amount: int, description: str, occurrence: int, external: str = ""
) -> str:
    """Stable id of a movement: the provider's id when there is one, otherwise
    day+amount+text (+ the n-th identical one of that day, so two equal coffees stay two)."""
    raw = external or f"{day.isoformat()}|{amount}|{normalise(description)}|{occurrence}"
    return hashlib.sha1(raw.encode()).hexdigest()


@dataclass
class NewMovement:
    day: date
    amount: int  # minor units of the account's currency
    description: str
    counterparty: str = ""
    category: str = ""
    external_id: str = ""
    note: str = ""


@dataclass
class AddResult:
    added: list[Transaction]
    duplicates: int


def rule_category(rules: list[Rule], text: str) -> str:
    low = normalise(text)
    for rule in sorted(rules, key=lambda r: -len(r.pattern)):  # the most specific wins
        if rule.pattern in low:
            return rule.category
    return ""


def add_movements(
    db: Session, account: Account, movements: list[NewMovement], source: str
) -> AddResult:
    rules = list(db.scalars(select(Rule).where(Rule.user_id == account.user_id)))
    seen = set(
        db.scalars(select(Transaction.fingerprint).where(Transaction.account_id == account.id))
    )
    counts: Counter[tuple[date, int, str]] = Counter()
    # The same money from two sources (the bank link and an uploaded statement) has
    # different wording: within ±1 day and the same amount it is the same movement.
    other: Counter[tuple[date, int]] = Counter()
    if movements:
        start = min(m.day for m in movements) - timedelta(days=1)
        end = max(m.day for m in movements) + timedelta(days=1)
        for day, amount in db.execute(
            select(Transaction.day, Transaction.amount).where(
                Transaction.account_id == account.id,
                Transaction.source != source,
                Transaction.day >= start,
                Transaction.day <= end,
            )
        ):
            other[(day, amount)] += 1
    added, duplicates = [], 0
    for m in movements:
        key = (m.day, m.amount, normalise(m.description))
        counts[key] += 1
        fp = fingerprint(m.day, m.amount, m.description, counts[key] - 1, m.external_id)
        if fp in seen:
            duplicates += 1
            continue
        near = next(
            (
                k
                for k in (
                    (m.day, m.amount),
                    (m.day - timedelta(days=1), m.amount),
                    (m.day + timedelta(days=1), m.amount),
                )
                if other[k] > 0
            ),
            None,
        )
        if near is not None:
            other[near] -= 1
            duplicates += 1
            continue
        seen.add(fp)
        category, by = m.category, "user" if m.category else ""
        if not category and (found := rule_category(rules, f"{m.description} {m.counterparty}")):
            category, by = found, "rule"
        tx = Transaction(
            user_id=account.user_id,
            account_id=account.id,
            day=m.day,
            amount=m.amount,
            currency=account.currency,
            description=m.description.strip()[:500],
            counterparty=m.counterparty.strip()[:200],
            category=category,
            category_by=by,
            note=m.note,
            source=source,
            fingerprint=fp,
        )
        db.add(tx)
        added.append(tx)
    db.commit()
    if added:  # money moved between own accounts is not spending: pair the legs
        from oink.core.db import User
        from oink.services.transfers import WINDOW, match_transfers

        user = db.get(User, account.user_id)
        if user is not None:
            match_transfers(db, user, since=min(t.day for t in added) - WINDOW)
            from oink.services.plans import match_executions

            match_executions(db, user)
    return AddResult(added=added, duplicates=duplicates)


def learn_rule(db: Session, tx: Transaction, category: str) -> None:
    """The user corrected a category: remember it for the same merchant."""
    pattern = normalise(tx.counterparty or tx.description)[:60]
    pattern = re.sub(r"\d{4,}|\b\d{2}/\d{2}(/\d{2,4})?\b", "", pattern).strip()
    if len(pattern) < 3:
        return
    rule = db.scalar(select(Rule).where(Rule.user_id == tx.user_id, Rule.pattern == pattern))
    if rule is None:
        db.add(Rule(user_id=tx.user_id, pattern=pattern, category=category))
    else:
        rule.category = category
    db.commit()
