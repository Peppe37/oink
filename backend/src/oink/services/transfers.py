"""Money moved between your own accounts is still your money.

A transfer from Revolut to Trade Republic shows up twice: -300 on one account, +300 on the
other. Neither is spending nor income. Oink pairs the two legs deterministically:

- opposite amounts (same currency: exact; different currencies: within 2% at today's rate),
- within four days of each other, on two different accounts of the same person,
- and at least one side worded like a transfer («giroconto», «bonifico», «top up», «to
  Trade Republic», the other account's IBAN or name, the person's own full name — from the
  profile or as printed on their statements),
- and only one candidate left: when two legs could match equally well, nothing is paired.

Both legs become category `transfers` (or keep `savings`/`investments` if they had it),
marked `category_by = "transfer"` and linked by `transfer_id`. Reports already leave these
categories out of income and spending, and net worth adds the accounts up, so moving
money around never looks like losing it.

An outgoing transfer to one of your own institutions that is not in Oink yet (e.g. to Trade
Republic when only Revolut is tracked) is also a transfer, without a partner: Oink lists
these so the account can be added, instead of counting them as spending.

Money coming in from the person themself is not income either, even without a partner in
Oink: a transfer from their own name or IBAN, a top-up from their own card (Google Pay,
Apple Pay), cash paid in at the bank, a deposit on an exchange or broker account."""

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


#: money a person pays in themself: a top-up from their own card, cash at the bank
OWN_IN = re.compile(
    r"\b(ricarica di (google|apple) pay|top.?up (by|with|from) card|money added from"
    r"|added from card|vers(amento)?\.? ?(contanti|cont\.|sport)|cash deposit"
    r"|deposito contanti|bareinzahlung|^ deposito $"
    r"|dépôt d'espèces|ingreso en efectivo)",
)
#: on an exchange or broker account, money in or out is the person's own
BROKER_MOVES = re.compile(r"\b(deposit|withdrawal|deposito|prelievo|versamento|bonifico)\b")


class Owner:
    """What identifies a person's own accounts in the wording of a movement."""

    def __init__(
        self, user: User, accounts: Iterable[Account], holders: Iterable[str] = ()
    ) -> None:
        self.accounts = {a.id: a for a in accounts}
        self.ibans = {_compact(a.iban): a.id for a in self.accounts.values() if len(a.iban) > 10}
        self.names: list[str] = []
        for raw in [user.name or "", *holders]:
            parts = [p for p in normalise(raw).split() if len(p) > 1]
            if len(parts) >= 2:  # «Giuseppe Lapietra» and «Lapietra Giuseppe»
                for name in (" ".join(parts), " ".join(reversed(parts))):
                    if name not in self.names:
                        self.names.append(name)
        self.full_name = self.names[0] if self.names else ""

    def own_name(self, text: str) -> bool:
        return any(n in text for n in self.names)

    def money_in(self, t: Transaction) -> bool:
        """Money the person paid in themself, with no partner leg in Oink."""
        text = _text(t)
        if self.own_name(text) or any(i in text.replace(" ", "") for i in self.ibans):
            return True
        if OWN_IN.search(text):
            return True
        acc = self.accounts.get(t.account_id)
        return bool(acc and acc.kind in ("crypto", "investment") and BROKER_MOVES.search(text))

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
        if self.own_name(text):
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
        if self.own_name(text):
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
    owner = Owner(user, accounts, holders(db, user))
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
            # Equal own transfers on the same day can be certain as a balanced group,
            # while their individual references remain unknown. Exclude the group
            # without inventing one-to-one links; unequal or cross-account groups wait.
            same_outs = [
                o
                for o in outs
                if o.transfer_id is None
                and o.account_id == out.account_id
                and o.day == out.day
                and o.amount == out.amount
                and o.currency == out.currency
            ]
            if (
                len(nearest) == len(same_outs)
                and len({i.account_id for i in nearest}) == 1
                and all(i.day == out.day and i.currency == out.currency for i in nearest)
                and all(
                    owner.institution(o)
                    or any(n in _text(o) for n in owner.names_of(nearest[0].account_id))
                    for o in same_outs
                )
            ):
                for leg in [*same_outs, *nearest]:
                    _mark(leg, None)
                taken.update(i.id for i in nearest)
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
    # money paid in by the person themself (their name, their card, cash at the bank)
    for inn in ins:
        free = inn.transfer_id is None and inn.category_by not in ("user", "transfer")
        if inn.day >= start and free and owner.money_in(inn):
            _mark(inn, None)
    db.commit()
    return paired


def holders(db: Session, user: User) -> list[str]:
    """Account holders printed on this person's statements."""
    from oink.core.db import Document

    out = []
    for facts in db.scalars(select(Document.facts).where(Document.user_id == user.id)):
        name = (facts or {}).get("account_holder") if isinstance(facts, dict) else None
        if isinstance(name, str) and name.strip() and name.strip() not in out:
            out.append(name.strip())
    return out


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
    owner = Owner(user, accounts, holders(db, user))
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
