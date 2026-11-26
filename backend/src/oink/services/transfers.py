"""Money moved between your own accounts is still your money.

A transfer from Revolut to Trade Republic shows up twice: -300 on one account, +300 on the
other. Neither is spending nor income. Oink pairs the two legs deterministically:

- opposite amounts (same currency: exact; different currencies: within 2% at today's rate),
- within four days of each other, on two different accounts of the same person,
- and at least one side worded like a transfer («giroconto», «bonifico», «top up», «to
  Trade Republic», the other account's IBAN or name, the person's own full name),
- and only one candidate left: when two legs could match equally well, nothing is paired.

Both legs become category `transfers` (or keep `savings`/`investments` if they had it),
marked `category_by = "transfer"` and linked by `transfer_id`. Reports already leave these
categories out of income and spending, and net worth adds the accounts up, so moving
money around never looks like losing it.

An outgoing transfer to one of your own institutions that is not in Oink yet (e.g. to Trade
Republic when only Revolut is tracked) is also a transfer, without a partner: Oink lists
these so the account can be added, instead of counting them as spending."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from oink.core.db import Account, Transaction, User
from oink.core.money import as_text
from oink.services.fx import Rates
from oink.services.ledger import INTERNAL, normalise

WINDOW = timedelta(days=4)
FX_TOLERANCE = Decimal("0.02")
LOOKBACK = timedelta(days=150)
#: Wording of a transfer in the five languages and on the usual apps.
HINTS = ("giroconto", "trasferimento", "transfer", "bonifico", "top up", "top-up", "topup",
         "ricarica", "a favore di", "verso", "money added", "added from", "to ", "from ",
         "deposit", "versamento", "prelievo", "withdrawal", "sepa", "istantaneo", "instant",
         "virement", "überweisung", "umbuchung", "transferencia", "traspaso", "pocket",
         "vault", "savings", "salvadanaio", "exchanged")  # fmt: skip
#: Banks, brokers and wallets people move their own money to. Seeing one of these in an
#: outgoing transfer means «to my account there», even when that account is not in Oink.
INSTITUTIONS = ("revolut", "trade republic", "deblock", "young platform", "intesa sanpaolo",
                "isybank", "bper", "unicredit", "fineco", "n26", "wise", "hype", "bunq",
                "scalable", "directa", "degiro", "satispay", "kraken", "binance", "coinbase",
                "bitpanda", "mediolanum", "bancoposta", "poste italiane", "ing ", "illimity",
                "widiba", "credem", "banca sella", "che banca", "buddybank", "webank",
                "mooney", "paypal", "vivid", "lydia", "sumeria", "boursorama", "trading 212",
                "xtb", "etoro")  # fmt: skip


def _text(t: Transaction) -> str:
    return f" {normalise(t.description)} {normalise(t.counterparty)} "


def _compact(iban: str) -> str:
    return re.sub(r"\s", "", iban).lower()


class Owner:
    """What identifies a person's own accounts in the wording of a movement."""

    def __init__(self, user: User, accounts: Iterable[Account]) -> None:
        self.accounts = {a.id: a for a in accounts}
        self.ibans = {_compact(a.iban): a.id for a in self.accounts.values() if len(a.iban) > 10}
        parts = [p for p in normalise(user.name or "").split() if len(p) > 1]
        self.full_name = " ".join(parts) if len(parts) >= 2 else ""

    def names_of(self, account_id: int) -> list[str]:
        acc = self.accounts.get(account_id)
        if acc is None:
            return []
        return [n for n in (normalise(acc.name), normalise(acc.institution)) if len(n) >= 3]

    def hint(self, t: Transaction, other: int | None = None) -> bool:
        text = _text(t)
        compact = text.replace(" ", "")
        if any(iban in compact for iban in self.ibans):
            return True
        if self.full_name and self.full_name in text:
            return True
        worded = any(h in text for h in HINTS)
        if other is not None and worded and any(n in text for n in self.names_of(other)):
            return True
        return worded

    def institution(self, t: Transaction) -> str:
        """The own institution an outgoing transfer goes to ('' if none is named)."""
        text = _text(t)
        if not any(h in text for h in HINTS):
            return ""
        compact = text.replace(" ", "")
        for iban, acc_id in self.ibans.items():
            if iban in compact:
                return self.accounts[acc_id].institution or self.accounts[acc_id].name
        if self.full_name and self.full_name in text:
            return "?"
        here = self.accounts.get(t.account_id)
        own_here = normalise(f"{here.institution} {here.name}") if here else ""
        for raw in INSTITUTIONS:
            name = raw.strip()
            if f" {name} " in text:
                if name in own_here:
                    continue  # «Revolut» on a Revolut account names the app, not a destination
                return name.title()
        return ""


def _same_amount(a: Transaction, b: Transaction, rates: Rates) -> bool:
    if a.currency == b.currency:
        return a.amount == -b.amount
    converted = rates.convert(-a.amount, a.currency, b.currency)
    if converted is None or b.amount == 0:
        return False
    return abs(Decimal(converted - b.amount) / Decimal(b.amount)) <= FX_TOLERANCE


def _free(t: Transaction) -> bool:
    """Not paired yet and not set to something else by the person."""
    return t.transfer_id is None and (t.category_by != "user" or t.category in INTERNAL)


def _mark(t: Transaction, partner: Transaction | None) -> None:
    if t.category_by != "user":  # the person's own choice (already internal) stays
        if t.category not in ("savings", "investments", "crypto"):
            t.category = "transfers"
        t.category_by = "transfer"
    t.transfer_id = partner.id if partner is not None else None


def match_transfers(db: Session, user: User, since: date | None = None) -> int:
    """Pair the legs of transfers between this person's accounts; returns pairs made."""
    accounts = list(db.scalars(select(Account).where(Account.user_id == user.id)))
    if len(accounts) == 0:
        return 0
    owner = Owner(user, accounts)
    start = since or (date.today() - LOOKBACK)
    txs = [
        t
        for t in db.scalars(
            select(Transaction).where(
                Transaction.user_id == user.id,
                Transaction.day >= start - WINDOW,
                Transaction.category_by != "split",
            )
        )
        if _free(t)
    ]
    rates = Rates.load(db)
    outs = sorted((t for t in txs if t.amount < 0 and t.day >= start), key=lambda t: (t.day, t.id))
    ins = [t for t in txs if t.amount > 0]
    paired = 0
    taken: set[int] = set()
    for out in outs:
        candidates = [
            i
            for i in ins
            if i.id not in taken
            and i.account_id != out.account_id
            and abs(i.day - out.day) <= WINDOW
            and _same_amount(out, i, rates)
        ]
        worded = [
            i for i in candidates if owner.hint(out, i.account_id) or owner.hint(i, out.account_id)
        ]
        if not worded:
            continue
        best = min(abs((i.day - out.day).days) for i in worded)
        nearest = [i for i in worded if abs((i.day - out.day).days) == best]
        if len(nearest) != 1:
            continue  # two equally good partners: leave it to the person
        partner = nearest[0]
        taken.add(partner.id)
        _mark(out, partner)
        _mark(partner, out)
        paired += 1
    # outgoing transfers to an own institution that is not tracked in Oink
    for out in outs:
        if out.transfer_id is not None or out.category_by == "user":
            continue
        if owner.institution(out):
            _mark(out, None)
    db.commit()
    return paired


def unlink(db: Session, tx: Transaction) -> None:
    """The person says this is not a transfer: free both legs."""
    if tx.transfer_id is not None:
        partner = db.get(Transaction, tx.transfer_id)
        if partner is not None and partner.transfer_id == tx.id:
            partner.transfer_id = None
            if partner.category_by == "transfer":
                partner.category, partner.category_by = "", ""
    tx.transfer_id = None


@dataclass
class Untracked:
    institution: str
    currency: str
    count: int
    total: int
    last: date


def untracked(db: Session, user: User, since: date | None = None) -> list[dict[str, object]]:
    """Money sent to own institutions that are not in Oink yet, grouped by institution."""
    accounts = list(db.scalars(select(Account).where(Account.user_id == user.id)))
    owner = Owner(user, accounts)
    start = since or (date.today() - timedelta(days=90))
    totals: dict[tuple[str, str], Untracked] = {}
    for t in db.scalars(
        select(Transaction).where(
            Transaction.user_id == user.id,
            Transaction.day >= start,
            Transaction.amount < 0,
            Transaction.category_by == "transfer",
            Transaction.transfer_id.is_(None),
        )
    ):
        name = owner.institution(t) or "?"
        others = [normalise(f"{a.institution} {a.name}") for a in accounts if a.id != t.account_id]
        if name != "?" and any(name.lower() in o for o in others):
            continue  # that account is in Oink: its leg just hasn't arrived yet
        entry = totals.setdefault((name, t.currency), Untracked(name, t.currency, 0, 0, t.day))
        entry.count += 1
        entry.total -= t.amount
        entry.last = max(entry.last, t.day)
    return [
        {
            "institution": e.institution,
            "currency": e.currency,
            "count": e.count,
            "total": as_text(e.total, e.currency),
            "last": e.last.isoformat(),
        }
        for e in totals.values()
    ]
