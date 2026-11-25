"""Numbers are read and checked by rules, never by the AI.

- Movements of a statement come from its tables (Docling) or from the bank's own export
  (CSV/OFX), read by the same header rules as the importer.
- Every document that carries totals is reconciled:
  statement  opening balance + movements = closing balance
  payslip    gross − deductions = net pay
  invoice    taxable amount + VAT = total
- A document passes only when its totals add up (or it is a structured bank export, which
  carries no totals). Anything else goes to «To review», with what didn't add up: the AI
  may explain it and suggest a fix there, the person decides."""

from __future__ import annotations

import contextlib
import csv
import io
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from oink.core.money import parse_decimal
from oink.services.importer import ImportError_, Row, parse_csv

_AMOUNT = r"[-+−]?\s?(?:\d{1,3}(?:[.' ]\d{3})+|\d+)[,.]\d{2}"
OPENING = re.compile(
    r"(saldo\s+(?:contabile\s+)?iniziale|saldo\s+precedente|saldo\s+di\s+apertura|saldo\s+al\s+\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\s*\(?iniziale|"
    r"opening\s+balance|balance\s+brought\s+forward|anfangssaldo|alter\s+kontostand|"
    r"solde\s+(?:initial|précédent|precedent)|ancien\s+solde|saldo\s+inicial|saldo\s+anterior)"
    rf"[^\d\-+−\n]{{0,40}}(?P<amount>{_AMOUNT})",
    re.IGNORECASE,
)
CLOSING = re.compile(
    r"(saldo\s+(?:contabile\s+)?finale|saldo\s+di\s+chiusura|nuovo\s+saldo|closing\s+balance|"
    r"balance\s+carried\s+forward|endsaldo|neuer\s+kontostand|solde\s+final|nouveau\s+solde|"
    r"saldo\s+final)"
    rf"[^\d\-+−\n]{{0,40}}(?P<amount>{_AMOUNT})",
    re.IGNORECASE,
)
GROSS = re.compile(
    rf"(retribuzione\s+lorda|totale\s+competenze|lordo|gross\s+pay|bruttobezug|brutto|salaire\s+brut|salario\s+bruto|devengos)[^\d\n]{{0,40}}(?P<amount>{_AMOUNT})",
    re.IGNORECASE,
)
DEDUCTIONS = re.compile(
    rf"(totale\s+trattenute|trattenute|total\s+deductions|deductions|abzüge|retenues|deducciones)[^\d\n]{{0,40}}(?P<amount>{_AMOUNT})",
    re.IGNORECASE,
)
NET = re.compile(
    rf"(netto\s+(?:in\s+busta|a\s+pagare|del\s+mese)|net\s+pay|nettobezug|auszahlung|net\s+à\s+payer|net\s+a\s+payer|líquido\s+a\s+percibir)[^\d\n]{{0,40}}(?P<amount>{_AMOUNT})",
    re.IGNORECASE,
)
TAXABLE = re.compile(
    rf"(imponibile|subtotal|taxable\s+amount|nettobetrag|montant\s+ht|base\s+imponible)[^\d\n]{{0,40}}(?P<amount>{_AMOUNT})",
    re.IGNORECASE,
)
VAT = re.compile(
    rf"(\biva\b|\bvat\b|mwst|ust\.?|\btva\b)[^\d\n]{{0,30}}(?:\d{{1,2}}\s?%[^\d\n]{{0,20}})?(?P<amount>{_AMOUNT})",
    re.IGNORECASE,
)
TOTAL = re.compile(
    rf"(totale\s+(?:documento|fattura|da\s+pagare)|total\s+(?:due|amount)|gesamtbetrag|montant\s+ttc|total\s+a\s+pagar)[^\d\n]{{0,40}}(?P<amount>{_AMOUNT})",
    re.IGNORECASE,
)

CENT = Decimal("0.01")
PAY_TOLERANCE = Decimal("1.00")  # payslips round to the euro (arrotondamenti)


@dataclass
class Check:
    name: str  # statement_balance | payslip_net | invoice_total | statement_found
    ok: bool | None  # None: could not be checked
    detail: dict[str, str] = field(default_factory=dict)


@dataclass
class Verdict:
    status: str  # "ok" | "review"
    checks: list[Check]
    facts: dict[str, str] = field(default_factory=dict)

    @property
    def issues(self) -> list[Check]:
        return [c for c in self.checks if c.ok is not True]

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "checks": [asdict(c) for c in self.checks]}


def _amount(pattern: re.Pattern[str], text: str, last: bool = False) -> Decimal | None:
    found = list(pattern.finditer(text))
    if not found:
        return None
    match = found[-1] if last else found[0]
    with contextlib.suppress(ValueError):
        return parse_decimal(match["amount"].replace("−", "-").replace(" ", ""))
    return None


def rows_from_tables(tables: list[dict[str, Any]]) -> list[Row]:
    """Movements from the tables Docling found, read by the importer's header rules."""
    out: list[Row] = []
    for table in tables:
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";")
        header = table.get("header") or []
        if header and not all(re.fullmatch(r"\d+", h or "") for h in header):
            writer.writerow(header)
        for row in table.get("rows") or []:
            writer.writerow(row)
        with contextlib.suppress(ImportError_):
            out.extend(parse_csv(buffer.getvalue().encode()))
    return out


def balances(text: str) -> tuple[Decimal | None, Decimal | None]:
    return _amount(OPENING, text), _amount(CLOSING, text, last=True)


def check_statement(rows: list[Row], text: str, structured: bool) -> Verdict:
    if not rows:
        return Verdict("review", [Check("statement_found", False, {"reason": "no_movements"})])
    opening, closing = balances(text)
    total = sum((r.amount for r in rows), Decimal(0))
    facts = {"movements": str(len(rows)), "sum": str(total)}
    if opening is not None:
        facts["opening_balance"] = str(opening)
    if closing is not None:
        facts["closing_balance"] = str(closing)
    if opening is not None and closing is not None:
        expected = opening + total
        ok = abs(expected - closing) <= CENT
        check = Check("statement_balance", ok, {
            "opening": str(opening), "movements": str(total), "expected": str(expected),
            "closing": str(closing), "difference": str(closing - expected),
        })  # fmt: skip
        return Verdict("ok" if ok else "review", [check], facts)
    check = Check("statement_balance", None, {"reason": "balances_not_found"})
    # a bank's own export (CSV/OFX) has no totals to check: it is trusted as it is
    return Verdict("ok" if structured else "review", [check], facts)


def check_payslip(text: str) -> Verdict:
    gross, deductions, net = (
        _amount(GROSS, text),
        _amount(DEDUCTIONS, text),
        _amount(NET, text, last=True),
    )
    facts = {
        k: str(v)
        for k, v in (("gross_pay", gross), ("deductions", deductions), ("net_pay", net))
        if v is not None
    }
    if gross is not None and deductions is not None and net is not None:
        expected = gross - abs(deductions)
        ok = abs(expected - net) <= PAY_TOLERANCE
        detail = {
            "gross": str(gross),
            "deductions": str(deductions),
            "expected": str(expected),
            "net": str(net),
        }
        return Verdict("ok" if ok else "review", [Check("payslip_net", ok, detail)], facts)
    reason = "net_not_found" if net is None else "parts_not_found"
    status = "ok" if net is not None else "review"
    return Verdict(status, [Check("payslip_net", None, {"reason": reason})], facts)


def check_invoice(text: str) -> Verdict:
    taxable, vat, total = (
        _amount(TAXABLE, text),
        _amount(VAT, text),
        _amount(TOTAL, text, last=True),
    )
    facts = {
        k: str(v)
        for k, v in (("taxable", taxable), ("vat", vat), ("total", total))
        if v is not None
    }
    if taxable is not None and vat is not None and total is not None:
        expected = taxable + vat
        ok = abs(expected - total) <= CENT
        detail = {
            "taxable": str(taxable),
            "vat": str(vat),
            "expected": str(expected),
            "total": str(total),
        }
        return Verdict("ok" if ok else "review", [Check("invoice_total", ok, detail)], facts)
    return Verdict("ok", [Check("invoice_total", None, {"reason": "parts_not_found"})], facts)
