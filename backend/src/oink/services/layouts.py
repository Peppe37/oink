"""Statements whose layout Oink knows, read straight from the PDF's text: fast (no layout
model, a 95-page statement in seconds) and exact.

Every reader checks itself: each row's balance must equal the previous one plus the row's
amount, and the totals printed on the statement must match. A statement that doesn't
pass is not read here (the general readers and Docling try it, and «To review» catches
what still doesn't add up).

- Revolut «Estratto conto personalizzato» / account statement: the main account's table
  (date, description, category, amount, balance). Pockets, the savings deposit and the
  Robo-Advisor are noted, not imported; moves to and from the savings deposit are marked
  as savings, so they never count as spending.
- Trade Republic «Estratto conto»: every amount comes from the change of the balance, so
  no sign is guessed; savings plan executions keep ISIN, name and quantity in their
  wording, which is what the savings plan proposals read."""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from oink.services.importer import Row, find_ibans

MONTHS = {
    "gen": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6, "lug": 7, "ago": 8,
    "set": 9, "ott": 10, "nov": 11, "dic": 12,
    "jan": 1, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "dec": 12,
}  # fmt: skip
_MON = "|".join(MONTHS)
_NUM = r"-?\d{1,3}(?:\.\d{3})*,\d{2}"
CENT = Decimal("0.01")


@dataclass
class Layout:
    bank: str
    rows: list[Row]
    opening: Decimal | None
    closing: Decimal | None
    ibans: list[str]
    period: str = ""
    #: securities bought or sold (Trade Republic): {"day", "isin", "name", "quantity", "amount"}
    trades: list[dict[str, str]] = field(default_factory=list)
    #: what was seen and left out on purpose: pockets, deposits, robo-advisor
    skipped: list[str] = field(default_factory=list)
    #: rows that are moves between the person's own accounts at the same bank
    savings: set[int] = field(default_factory=set)
    #: the account holder as printed (money to or from this name is the person's own)
    holder: str = ""


def _num(text: str) -> Decimal:
    return Decimal(text.replace(".", "").replace(",", "."))


def _day(d: str, mon: str, year: str) -> date:
    return date(int(year), MONTHS[mon.lower()[:3]], int(d))


def read(text: str) -> Layout | None:
    """The statement read by the reader of its bank, or None (unknown or not adding up)."""
    text = text.replace("\xa0", " ").replace("\u202f", " ")  # PDFs put odd spaces before «€»
    for reader in (revolut, trade_republic):
        with contextlib.suppress(ValueError, InvalidOperation, KeyError):
            found = reader(text)
            if found is not None:
                return found
    return None


# -- Revolut ------------------------------------------------------------------------------
_RV_START = re.compile(rf"^(\d{{1,2}}) ({_MON}) (\d{{4}}) (.*)$", re.IGNORECASE)
_RV_TAIL = re.compile(
    rf"^(?P<head>.*?)\s*(?P<amount>{_NUM})€ (?P<balance>{_NUM})€(?: {_NUM}€){{3}}\s*$"
)
_RV_TOTAL = re.compile(rf"^Totale (?P<balance>{_NUM})€")
_RV_SECTION = re.compile(r"^(?P<name>.+?) \((?P<cur>[A-Z]{3})\)\s*$")
_RV_NOISE = (
    "Estratto conto personalizzato", "Generato il", "Data Descrizione", "Denaro in",
    "entrata", "/uscita", "Saldo Imposte", "Riepilogo delle transazioni", "Account statement",
    "Generated on", "Date Description", "Money in", "Money out", "Balance Taxes",
)  # fmt: skip
#: Revolut's category column, printed after the description
_RV_CATEGORIES = (
    "Interessi o rendimenti", "Prelievo contanti", "Esercente", "Ricarica", "Altri",
    "Deposito", "Trasferimento", "Rimborso", "Commissioni", "Merchant", "Top-up", "Other",
    "Transfer", "Refund", "Fees", "Interest or returns", "Cash withdrawal",
)  # fmt: skip
_RV_SAVINGS = re.compile(r"\b(conto deposito|deposito senza vincoli|savings|instant access)", re.I)
_RV_WHO = re.compile(
    r"^(?:Pagamento (?:da parte di|a favore di|da|a)|Payment (?:from|to)|Transfer (?:from|to))\s+(.+)$",
    re.IGNORECASE,
)


def revolut(text: str) -> Layout | None:
    if "revolut" not in text.lower():
        return None
    lines = [ln.rstrip() for ln in text.splitlines()]
    sections: list[tuple[str, str, list[str]]] = []  # (name, currency, lines)
    current: tuple[str, str, list[str]] | None = None
    for i, line in enumerate(lines):
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        sec = _RV_SECTION.match(line.strip())
        if sec and nxt.startswith(
            ("Riepilogo delle transazioni", "Transaction summary", "Riepilogo della")
        ):
            current = (sec["name"].strip(), sec["cur"], [])
            sections.append(current)
            continue
        if current is not None:
            current[2].append(line)
            if _RV_TOTAL.match(line.strip()):
                current = None
    if not sections:
        return None
    main = next(
        (s for s in sections if s[0].lower().startswith(("conto personale", "personal", "main"))),
        None,
    )
    if main is None:
        return None
    rows, balances, closing = _revolut_rows(main[2])
    if not rows or closing is None:
        return None
    opening = balances[0] - rows[0].amount
    running = opening
    for row, bal in zip(rows, balances, strict=True):
        running += row.amount
        if abs(running - bal) > CENT:
            return None  # a row misread: let the other readers try
    if abs(running - closing) > CENT:
        return None
    savings = {i for i, r in enumerate(rows) if _RV_SAVINGS.search(r.description)}
    for i in savings:
        rows[i].category = "savings"  # to or from its own savings deposit: never spending
    skipped = [f"{name} ({cur})" for name, cur, _ in sections if (name, cur) != main[:2]]
    if re.search(r"\bConti deposito\b|\bSavings accounts\b", text):
        skipped.append("deposit")
    if "Robo-Advisor" in text:
        skipped.append("Robo-Advisor")
    period = ""
    if m := re.search(
        rf"(\d{{1,2}} (?:{_MON}) \d{{4}}) - (\d{{1,2}} (?:{_MON}) \d{{4}})", text, re.I
    ):
        period = f"{m[1]} – {m[2]}"
    printed = re.search(r"\((?:IBAN)\)\s+([A-Z]{2}\d{2}[A-Z0-9]{11,30})", text)
    ibans = find_ibans(printed[1]) if printed else []
    ibans = ibans or [i for i in find_ibans(text) if i[4:9] == "03669"][:1]
    return Layout("Revolut", rows, opening, closing, ibans[:1], period, skipped=skipped,
                  savings=savings, holder=_holder_after(lines, "Generato il", "Generated on"))  # fmt: skip


def _revolut_rows(lines: list[str]) -> tuple[list[Row], list[Decimal], Decimal | None]:
    rows: list[Row] = []
    balances: list[Decimal] = []
    closing: Decimal | None = None
    pending: tuple[date, list[str]] | None = None
    for raw in lines:
        line = raw.strip()
        if (
            not line
            or line.startswith(_RV_NOISE)
            or re.fullmatch(rf"\d{{1,2}} (?:{_MON}) \d{{4}} - .*", line, re.I)
        ):
            continue
        if total := _RV_TOTAL.match(line):
            closing = _num(total["balance"])
            break
        start = _RV_START.match(line)
        if start and pending is None:
            pending = (_day(start[1], start[2], start[3]), [start[4]])
        elif pending is not None:
            pending[1].append(line)
        else:
            continue
        joined = " ".join(pending[1])
        tail = _RV_TAIL.match(joined)
        if not tail:
            continue
        head = tail["head"].strip()
        for cat in _RV_CATEGORIES:
            if head.endswith(" " + cat) or head == cat:
                head = head[: -len(cat)].strip()
                break
        who = _RV_WHO.match(head)
        rows.append(
            Row(pending[0], _num(tail["amount"]), head, who[1].strip().title() if who else head)
        )
        balances.append(_num(tail["balance"]))
        pending = None
    return rows, balances, closing


# -- Trade Republic -------------------------------------------------------------------------
_TR_DAY = re.compile(rf"^(\d{{1,2}}) ({_MON})\s*$", re.IGNORECASE)
_TR_ROW = re.compile(rf"^(\d{{4}}) (?P<body>.*?) (?P<amount>{_NUM}) € (?P<balance>{_NUM}) €\s*$")
_TR_SUMMARY = re.compile(
    rf"^Conto corrente (?P<open>{_NUM}) € (?P<inn>{_NUM}) € (?P<out>{_NUM}) € (?P<close>{_NUM}) €"
)
_TR_TRADE = re.compile(
    r"(?:Savings plan execution|Buy trade|Sell trade|Esecuzione piano di accumulo|Acquisto|Vendita)\s+"
    r"(?P<isin>[A-Z]{2}[A-Z0-9]{9}\d)\s+(?P<name>.+?),\s*(?:quantity|quantità):\s*(?P<qty>[\d.,]+)",
    re.IGNORECASE,
)
_TR_TYPES = ("Interessi", "Bonifico", "Rendimento", "Commercio", "Carta", "Imposte", "Commissione",
             "Interest", "Transfer", "Dividend", "Trade", "Card", "Tax", "Fee")  # fmt: skip
_TR_WHO = re.compile(r"(?:transfer from|transfer for|transfer to)\s+(.+?)\s*\(", re.IGNORECASE)


def trade_republic(text: str) -> Layout | None:
    if "trade republic" not in text.lower():
        return None
    lines = [ln.strip() for ln in text.splitlines()]
    summary = next((m for ln in lines if (m := _TR_SUMMARY.match(ln))), None)
    if summary is None:
        return None
    opening, closing = _num(summary["open"]), _num(summary["close"])
    rows: list[Row] = []
    trades: list[dict[str, str]] = []
    running = opening
    i = 0
    while i < len(lines):
        day = _TR_DAY.match(lines[i])
        if not day:
            i += 1
            continue
        # the row may wrap: join lines until one ends with «amount € balance €»
        j, body = i + 1, ""
        while j < len(lines) and j <= i + 4:
            body = f"{body} {lines[j]}".strip()
            if _TR_ROW.match(body):
                break
            j += 1
        row = _TR_ROW.match(body)
        if not row:
            i += 1
            continue
        when = _day(day[1], day[2], row[1])
        balance = _num(row["balance"])
        amount = balance - running
        if abs(abs(amount) - _num(row["amount"])) > CENT:
            return None  # the balance doesn't follow: not this layout
        running = balance
        words = row["body"]
        kind = next((t for t in _TR_TYPES if words.startswith(t + " ")), "")
        wording = words[len(kind) :].strip() if kind else words
        who = _TR_WHO.search(wording)
        trade = _TR_TRADE.search(wording)
        counterparty = who[1].strip() if who else (trade["name"].strip() if trade else wording)
        rows.append(Row(when, amount, wording, counterparty))
        if trade:
            trades.append({"day": when.isoformat(), "isin": trade["isin"], "name": trade["name"].strip(),
                           "quantity": trade["qty"].replace(",", "."), "amount": str(-amount)})  # fmt: skip
        i = j + 1
    if not rows or abs(running - closing) > CENT:
        return None
    iban = re.search(r"^IBAN\s+([A-Z]{2}\d{2}[A-Z0-9 ]{10,30})$", text, re.MULTILINE)
    ibans = [iban[1].replace(" ", "")] if iban else []
    period = ""
    if m := re.search(
        rf"DATA (\d{{1,2}} (?:{_MON}) \d{{4}}) - (\d{{1,2}} (?:{_MON}) \d{{4}})", text, re.I
    ):
        period = f"{m[1]} – {m[2]}"
    return Layout(
        "Trade Republic",
        rows,
        opening,
        closing,
        ibans,
        period,
        trades=trades,
        holder=_holder_after(lines, "BIC"),
    )


def _holder_after(lines: list[str], *markers: str) -> str:
    """The holder's name: the first line in capitals after the line that starts with one of
    `markers` (Revolut: «Generato il …», Trade Republic: «BIC …»)."""
    for i, line in enumerate(lines):
        if line.strip().startswith(markers):
            for nxt in lines[i + 1 : i + 4]:
                name = nxt.strip()
                if re.fullmatch(r"[A-ZÀ-Ý' ]{5,60}", name) and len(name.split()) >= 2:
                    return name.title()
            return ""
    return ""
