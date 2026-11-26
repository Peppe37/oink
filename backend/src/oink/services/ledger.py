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
    "fuel",
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
#: the bank link and the statement of the same payment may differ by this many days
#: (booking date against the day of the payment, weekends, card payments abroad)
NEAR_DAYS = 5
#: up to this many days apart the same amount from the other source is the same payment;
#: further (up to NEAR_DAYS) only when the payee's name has a word in common — two different
#: payments of the same amount a few days apart are common
BLIND_DAYS = 3


def _words(text: str) -> set[str]:
    return {w for w in normalise(text).split() if len(w) >= 4 and not any(c.isdigit() for c in w)}


def similar(a: str, b: str) -> bool:
    return bool(_words(a) & _words(b))


def _wording(t: Transaction) -> str:
    return f"{t.description} {t.counterparty}"


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


#: Payees every Italian (and European) statement shows: known without asking anyone.
BUILTIN = {
    "fuel": ("italiana petroli", "ip stazione", "eni station", "enilive", "eni live", "agip", "q8 ",
             "kuwait petroleum", "esso", "tamoil", "shell", "repsol", "total erg", "totalenergies",
             "carburant", "benzina", "distributore", "self service carb", "beyfin", "conad self"),
}  # fmt: skip


def rule_for(rules: list[Rule], text: str, field: str = "name") -> Rule | None:
    """The most specific rule that matches and sets `field` (name or category)."""
    low = normalise(text)
    for rule in sorted(rules, key=lambda r: -len(r.pattern)):
        if rule.pattern in low and getattr(rule, field):
            return rule
    return None


def rule_category(rules: list[Rule], text: str) -> str:
    found = rule_for(rules, text, "category")
    if found is not None:
        return found.category
    low = f" {normalise(text)} "
    # a word starts where another ends: «presso» (at, in an ATM's wording) is not «Esso»
    return next(
        (cat for cat, words in BUILTIN.items()
         if any(re.search(rf"(?<![a-z0-9]){re.escape(w)}", low) for w in words)),
        "",
    )  # fmt: skip


_DESCRIPTOR = re.compile(r"^\d{3,}\s")  # «44941 Palo Del Colle - S»: a terminal, not a name


def looks_like_terminal(text: str) -> bool:
    return bool(_DESCRIPTOR.match(text.strip()))


#: legal forms and single letters left by «S.R.L.» / «S.p.A.» once punctuation is gone
_LEGAL = {"spa", "srl", "sas", "snc", "srls", "ltd", "gmbh", "llc", "inc", "ag", "bv", "sa"}


def payee_key(text: str) -> str:
    """A payee without numbers and noise: «GD SERVICES S.R.L.» and «Gd Services» match."""
    words = [
        w for w in normalise(text).split()
        if not any(c.isdigit() for c in w) and w not in _LEGAL and len(w) > 1
    ]  # fmt: skip
    return " ".join(words)[:60]


def _payee_categories(db: Session, user_id: int) -> dict[str, str]:
    """The category each payee already has (chosen, by rule or by the AI), latest first:
    a new movement to the same payee gets it too, deterministically."""
    out: dict[str, str] = {}
    for counterparty, description, category in db.execute(
        select(Transaction.counterparty, Transaction.description, Transaction.category)
        .where(Transaction.user_id == user_id, Transaction.category != "",
               Transaction.category_by.in_(("user", "rule", "ai", "payee")))
        .order_by(Transaction.day)
    ):  # fmt: skip
        key = payee_key(counterparty or description)
        if len(key) >= 4:
            out[key] = category
    return out


def refresh_categories(db: Session, user_id: int) -> dict[str, int]:
    """Categories chosen by rules are worked out again with today's rules (a payee wrongly
    caught by an old keyword is let go), then movements without one take their payee's."""
    rules = list(db.scalars(select(Rule).where(Rule.user_id == user_id)))
    fixed = 0
    for t in db.scalars(
        select(Transaction).where(Transaction.user_id == user_id, Transaction.category_by == "rule")
    ):
        found = rule_category(rules, f"{t.description} {t.counterparty}")
        if found != t.category:
            t.category, t.category_by = found, ("rule" if found else "")
            fixed += 1
    db.commit()
    return {"rules": fixed, "payees": inherit_categories(db, user_id)}


def inherit_categories(db: Session, user_id: int) -> int:
    """Movements still without a category take their payee's: returns how many."""
    known = _payee_categories(db, user_id)
    done = 0
    for t in db.scalars(
        select(Transaction).where(Transaction.user_id == user_id, Transaction.category == "",
                                  Transaction.category_by != "split")
    ):  # fmt: skip
        found = known.get(payee_key(t.counterparty or t.description))
        if found:
            t.category, t.category_by = found, "payee"
            done += 1
    db.commit()
    return done


def _take_near(
    other: dict[tuple[date, int], list[Transaction]], m: NewMovement
) -> tuple[date, int] | None:
    """The other source's copy of this payment a few days away, nearest first: used up so
    it can't match twice. Beyond BLIND_DAYS the payee must look alike."""
    wording = f"{m.description} {m.counterparty}"
    for d in range(1, NEAR_DAYS + 1):
        for k in ((m.day - timedelta(days=d), m.amount), (m.day + timedelta(days=d), m.amount)):
            for tx in other.get(k, []):
                if d <= BLIND_DAYS or similar(wording, _wording(tx)):
                    other[k].remove(tx)
                    return k
    return None


def add_movements(
    db: Session, account: Account, movements: list[NewMovement], source: str,
    document_id: int | None = None,
) -> AddResult:  # fmt: skip
    """Add what is new, skip what is already there:

    - the same movement seen before (its fingerprint), or
    - the same day and amount already in the account from any source or file (a second
      statement of the same month, a CSV after the PDF, a movement typed by hand), or
    - the same amount within NEAR_DAYS from another source (the bank link books a card
      payment up to a few days after the statement shows it).

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
    other: dict[tuple[date, int], list[Transaction]] = {}  # the other source's, still free
    blank: dict[tuple[date, int], list[Transaction]] = {}  # existing ones without wording
    if movements:
        start = min(m.day for m in movements) - timedelta(days=NEAR_DAYS)
        end = max(m.day for m in movements) + timedelta(days=NEAR_DAYS)
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
                other.setdefault((tx.day, tx.amount), []).append(tx)
            if (not tx.description.strip("—- ") and not tx.counterparty) or (
                source == "import"
                and tx.source == "linked"
                and looks_like_terminal(tx.counterparty or tx.description)
            ):
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

    known = _payee_categories(db, account.user_id)
    added, duplicates = [], 0
    for m in movements:
        key = (m.day, m.amount, normalise(m.description))
        counts[key] += 1
        fp = fingerprint(m.day, m.amount, m.description, counts[key] - 1, m.external_id)
        exact = (m.day, m.amount)
        if fp in seen or same[exact] > 0:
            if same[exact] > 0:
                same[exact] -= 1
                if other.get(exact):
                    other[exact].pop()
                enrich(exact, m)
            duplicates += 1
            continue
        near = _take_near(other, m)
        if near is not None:
            same[near] = max(0, same[near] - 1)
            enrich(near, m)
            duplicates += 1
            continue
        seen.add(fp)
        category, by = m.category, "user" if m.category else ""
        if not category and (found := rule_category(rules, f"{m.description} {m.counterparty}")):
            category, by = found, "rule"
        if not category and (same_payee := known.get(payee_key(m.counterparty or m.description))):
            category, by = same_payee, "payee"
        named = rule_for(rules, f"{m.description} {m.counterparty}")
        if named is not None and named.name:
            m.counterparty = named.name
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


def learn_name(db: Session, tx: Transaction, name: str) -> int:
    """«This payee is really X»: rename it here and in every movement with the same code,
    now and from now on. Returns how many movements were renamed."""
    pattern = normalise(
        tx.description
        if looks_like_terminal(tx.description)
        else (tx.counterparty or tx.description)
    )[:60]
    if len(pattern) < 3:
        tx.counterparty = name
        db.commit()
        return 1
    rule = db.scalar(select(Rule).where(Rule.user_id == tx.user_id, Rule.pattern == pattern))
    if rule is None:
        db.add(Rule(user_id=tx.user_id, pattern=pattern, category="", name=name))
    else:
        rule.name = name
    renamed = 0
    for other in db.scalars(select(Transaction).where(Transaction.user_id == tx.user_id)):
        if pattern in normalise(f"{other.description} {other.counterparty}"):
            other.counterparty = name
            renamed += 1
    db.commit()
    return renamed


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


@dataclass
class Merged:
    kept: Transaction
    dropped: Transaction


def _edited(t: Transaction, db: Session) -> bool:
    """Something the person (or a plan) did to this movement: it must not be lost."""
    return bool(
        t.category_by == "user"
        or t.plan_id is not None
        or t.note
        or db.scalar(select(Transaction.id).where(Transaction.parent_id == t.id).limit(1))
    )


def merge_duplicates(db: Session, account_id: int, *, apply: bool = False) -> list[Merged]:
    """The same payment twice in one account: once from the statement, once from the bank
    link, a few days apart (BLIND_DAYS, or NEAR_DAYS when the payee looks alike). Same
    amount, one-to-one, nearest day first. The statement's copy goes, unless only that copy carries
    something the person did (then the other goes); when both carry something, both stay.
    Returns what was (or, without `apply`, would be) merged."""
    txs = [
        t for t in db.scalars(
            select(Transaction).where(
                Transaction.account_id == account_id,
                Transaction.source.in_(("import", "linked")),
                Transaction.amount != 0,
                Transaction.category_by != "split",
                Transaction.parent_id.is_(None),
            )
        )
    ]  # fmt: skip
    pairs = []
    by_amount: dict[int, list[Transaction]] = {}
    for t in txs:
        by_amount.setdefault(t.amount, []).append(t)
    for group in by_amount.values():
        imports = [t for t in group if t.source == "import"]
        links = [t for t in group if t.source == "linked"]
        for a in imports:
            for b in links:
                gap = abs((a.day - b.day).days)
                if gap <= BLIND_DAYS or (gap <= NEAR_DAYS and similar(_wording(a), _wording(b))):
                    pairs.append((gap, a.id, b.id, a, b))
    pairs.sort(key=lambda p: p[:3])
    used: set[int] = set()
    done: list[Merged] = []
    for _, _, _, imp, link in pairs:
        if imp.id in used or link.id in used:
            continue
        used.update((imp.id, link.id))
        drop, keep = imp, link
        if _edited(imp, db) and not _edited(link, db):
            drop, keep = link, imp
        elif _edited(imp, db) and _edited(link, db):
            continue  # both carry something: the person decides
        done.append(Merged(keep, drop))
        if not apply:
            continue
        if drop.transfer_id is not None and keep.transfer_id is None:
            partner = db.get(Transaction, drop.transfer_id)
            if partner is not None:
                partner.transfer_id, keep.transfer_id = keep.id, partner.id
                keep.category, keep.category_by = drop.category, drop.category_by
        if not keep.category and drop.category:
            keep.category, keep.category_by = drop.category, drop.category_by
        db.delete(drop)
    if apply:
        db.commit()
    return done
