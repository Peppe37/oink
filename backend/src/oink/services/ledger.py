"""Adding movements: one entry point for every source (bank, import, API, manual), with
duplicate detection and the owner's category rules."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from oink.core.db import Rule, Transaction

#: The default categories (the owner can use any other name).
CATEGORIES = [
    "spesa",
    "ristoranti",
    "casa",
    "bollette",
    "trasporti",
    "auto",
    "salute",
    "svago",
    "viaggi",
    "abbonamenti",
    "shopping",
    "istruzione",
    "regali",
    "tasse",
    "commissioni",
    "stipendio",
    "entrate",
    "trasferimenti",
    "risparmio",
    "investimenti",
    "altro",
]


def to_cents(value: str | float | int | Decimal) -> int:
    """«-1.234,56», «1,234.56», «12.5», 12.5 → cents. Raises ValueError when unreadable."""
    if isinstance(value, int):
        return value * 100
    if isinstance(value, float | Decimal):
        number = Decimal(str(value))
    else:
        text = value.strip().replace("€", "").replace(" ", "").replace(" ", "")
        negative = text.startswith("(") and text.endswith(")")
        text = text.strip("()")
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):  # 1.234,56
                text = text.replace(".", "").replace(",", ".")
            else:  # 1,234.56
                text = text.replace(",", "")
        elif "," in text:
            text = text.replace(",", ".")
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"importo non leggibile: {value!r}") from exc
        if negative:
            number = -number
    return int((number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def euros(cents: int) -> float:
    return cents / 100


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def fingerprint(
    day: date, cents: int, description: str, occurrence: int, external: str = ""
) -> str:
    """Stable id of a movement: the bank's id when there is one, otherwise day+amount+text
    (+ the n-th identical one of that day, so two equal coffees stay two)."""
    raw = external or f"{day.isoformat()}|{cents}|{normalise(description)}|{occurrence}"
    return hashlib.sha1(raw.encode()).hexdigest()


@dataclass
class NewMovement:
    day: date
    amount_cents: int
    description: str
    counterparty: str = ""
    currency: str = "EUR"
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
    db: Session, account_id: int, movements: list[NewMovement], source: str
) -> AddResult:
    rules = list(db.scalars(select(Rule)))
    seen = set(
        db.scalars(select(Transaction.fingerprint).where(Transaction.account_id == account_id))
    )
    counts: Counter[tuple[date, int, str]] = Counter()
    added, duplicates = [], 0
    for m in movements:
        key = (m.day, m.amount_cents, normalise(m.description))
        counts[key] += 1
        fp = fingerprint(m.day, m.amount_cents, m.description, counts[key] - 1, m.external_id)
        if fp in seen:
            duplicates += 1
            continue
        seen.add(fp)
        category, by = m.category, "user" if m.category else ""
        if not category and (found := rule_category(rules, f"{m.description} {m.counterparty}")):
            category, by = found, "rule"
        tx = Transaction(
            account_id=account_id,
            day=m.day,
            amount_cents=m.amount_cents,
            currency=m.currency,
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
    return AddResult(added=added, duplicates=duplicates)


def learn_rule(db: Session, tx: Transaction, category: str) -> None:
    """The owner corrected a category: remember it for the same merchant."""
    pattern = normalise(tx.counterparty or tx.description)[:60]
    pattern = re.sub(r"\d{4,}|\b\d{2}/\d{2}(/\d{2,4})?\b", "", pattern).strip()
    if len(pattern) < 3:
        return
    rule = db.scalar(select(Rule).where(Rule.pattern == pattern))
    if rule is None:
        db.add(Rule(pattern=pattern, category=category))
    else:
        rule.category = category
    db.commit()
