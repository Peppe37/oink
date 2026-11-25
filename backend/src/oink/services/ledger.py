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
    db: Session, account: Account, movements: list[NewMovement], source: str,
    document_id: int | None = None,
) -> AddResult:  # fmt: skip
    """Add what is new, skip what is already there:

    - the same movement seen before (its fingerprint), or
    - the same day and amount already in the account from any source or file (a second
      statement of the same month, a CSV after the PDF, a movement typed by hand), or
    - the same amount within a day from another source (the bank link books a card
      payment a day after the statement shows it).

    Identical movements inside one batch (two coffees) each count once. Within a day the
    newest is shown first, whatever order the bank or file lists them in."""
    if movements and movements[0].day > movements[-1].day:
        movements = list(reversed(movements))  # newest first (bank APIs): store oldest first
    movements = sorted(movements, key=lambda m: m.day)  # stable: keeps the order within a day
    rules = list(db.scalars(select(Rule).where(Rule.user_id == account.user_id)))
    seen = set(
        db.scalars(select(Transaction.fingerprint).where(Transaction.account_id == account.id))
    )
    counts: Counter[tuple[date, int, str]] = Counter()
    same: Counter[tuple[date, int]] = Counter()
    other: Counter[tuple[date, int]] = Counter()
    blank: dict[tuple[date, int], list[Transaction]] = {}  # existing ones without wording
    if movements:
        start = min(m.day for m in movements) - timedelta(days=1)
        end = max(m.day for m in movements) + timedelta(days=1)
        for tx in db.scalars(
            select(Transaction).where(
                Transaction.account_id == account.id,
                Transaction.source != "pending",  # provisional: the booked copy replaces it
                Transaction.day >= start,
                Transaction.day <= end,
            )
        ):
            same[(tx.day, tx.amount)] += 1
            if tx.source != source:
                other[(tx.day, tx.amount)] += 1
            if not tx.description.strip("—- ") and not tx.counterparty:
                blank.setdefault((tx.day, tx.amount), []).append(tx)

    def enrich(at: tuple[date, int], m: NewMovement) -> None:
        """A duplicate with words where the existing copy has none (Trade Republic through
        the bank link): the existing movement takes the statement's wording."""
        found = blank.get(at)
        if found and m.description.strip("—- "):
            tx = found.pop()
            tx.description, tx.counterparty = (
                m.description.strip()[:500],
                m.counterparty.strip()[:200],
            )

    added, duplicates = [], 0
    for m in movements:
        key = (m.day, m.amount, normalise(m.description))
        counts[key] += 1
        fp = fingerprint(m.day, m.amount, m.description, counts[key] - 1, m.external_id)
        exact = (m.day, m.amount)
        if fp in seen or same[exact] > 0:
            if same[exact] > 0:
                same[exact] -= 1
                other[exact] = max(0, other[exact] - 1)
                enrich(exact, m)
            duplicates += 1
            continue
        near = next(
            (
                k
                for k in (
                    (m.day - timedelta(days=1), m.amount),
                    (m.day + timedelta(days=1), m.amount),
                )
                if other[k] > 0
            ),
            None,
        )
        if near is not None:
            other[near] -= 1
            same[near] = max(0, same[near] - 1)
            enrich(near, m)
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
            document_id=document_id,
        )
        db.add(tx)
        added.append(tx)
    db.flush()
    for tx in added:
        tx.seq = tx.id  # inserted oldest first: a later id is a later movement of the day
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
