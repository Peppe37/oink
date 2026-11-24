"""Statements in, movements out, with as little help as possible:

- CSV exports of European banks and OFX/QFX files are read directly;
- PDFs are read by the AI when there is one, otherwise by a line parser (date … amount)
  that guesses the sign from the wording; those imports are marked as `estimated`;
- the IBAN printed on the statement picks the account by itself.

Amounts stay decimals here; they become minor units once the account (and so the
currency) is known. Everything ends in `ledger.add_movements`."""

from __future__ import annotations

import contextlib
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from oink.core.money import MoneyError, parse_decimal, to_minor
from oink.services.ai import AI, AIError
from oink.services.ledger import NewMovement

DATE_HEADERS = ("data contabile", "data operazione", "data", "date", "booking date",
                "buchungstag", "datum", "fecha", "fecha operación", "date opération",
                "data valuta", "value date", "valuta", "started date",
                "completed date")  # fmt: skip
AMOUNT_HEADERS = ("importo", "amount", "importo (eur)", "importo eur", "betrag", "importe",
                  "montant", "valore")  # fmt: skip
OUT_HEADERS = ("uscite", "addebiti", "dare", "debit", "addebito", "soll", "débit", "cargo")
IN_HEADERS = ("entrate", "accrediti", "avere", "credit", "accredito", "haben", "crédit", "abono")
TEXT_HEADERS = ("descrizione", "descrizione operazione", "causale", "description", "dettagli",
                "details", "concetto", "operazione", "verwendungszweck", "buchungstext",
                "libellé", "libelle", "reference")  # fmt: skip
PARTY_HEADERS = ("beneficiario", "controparte", "esercente", "payee", "merchant", "ordinante",
                 "empfänger", "auftraggeber", "bénéficiaire", "counterparty")  # fmt: skip
DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d.%m.%y",
                "%m/%d/%Y", "%Y%m%d")  # fmt: skip

IBAN = re.compile(r"\b([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30})\b")
_DATE = r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}"
_AMOUNT = r"[-+−]?\s?(?:\d{1,3}(?:[.' ]\d{3})+|\d+)[,.]\d{2}"
PDF_LINE = re.compile(
    rf"^\s*(?P<d1>{_DATE})\s+(?:(?P<d2>{_DATE})\s+)?(?P<text>.+?)\s+"
    rf"(?P<amount>{_AMOUNT})\s*(?P<trail>[-+])?\s*(?:€|EUR)?\s*$"
)
OUT_WORDS = ("pagamento", "addebito", "prelievo", "commission", "canone", "imposta", "spese",
             "disposto", "a favore di", "payment", "purchase", "withdrawal", "fee", "pos ",
             "sdd", "card", "carta", "lastschrift", "abbuchung", "paiement", "retrait", "pago",
             "compra", "cargo")  # fmt: skip
IN_WORDS = ("accredito", "stipendio", "a vostro favore", "ricevuto", "rimborso", "refund",
            "salary", "deposit", "interessi", "versamento", "gutschrift", "gehalt", "virement reçu",
            "salaire", "abono", "nómina", "incoming")  # fmt: skip
CLOSING = re.compile(
    rf"(saldo (?:contabile )?finale|saldo al|closing balance|endsaldo|solde final|saldo final)"
    rf"(?:{_DATE}|[^\d\-+\n])*?(?P<amount>{_AMOUNT})",
    re.IGNORECASE,
)


class ImportError_(ValueError):
    """`code` is translated by the web app."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Row:
    day: date
    amount: Decimal
    description: str
    counterparty: str = ""
    external_id: str = ""


@dataclass
class Parsed:
    rows: list[Row]
    balance: Decimal | None = None
    by_ai: bool = False
    estimated: bool = False  # signs guessed from the wording: worth a look
    ibans: list[str] = field(default_factory=list)

    def movements(self, currency: str) -> list[NewMovement]:
        out = []
        for r in self.rows:
            with contextlib.suppress(MoneyError):
                out.append(
                    NewMovement(
                        day=r.day,
                        amount=to_minor(r.amount, currency),
                        description=r.description or r.counterparty or "—",
                        counterparty=r.counterparty,
                        external_id=r.external_id,
                    )
                )
        return out

    def balance_minor(self, currency: str) -> int | None:
        return None if self.balance is None else to_minor(self.balance, currency)

    @property
    def month(self) -> str:
        """The month most movements belong to («2026-11»)."""
        months = [r.day.strftime("%Y-%m") for r in self.rows]
        return max(set(months), key=months.count) if months else ""


def parse_date(text: str) -> date:
    text = text.strip()[:10]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unreadable date: {text!r}")


def valid_iban(candidate: str) -> bool:
    iban = candidate.replace(" ", "")
    if not 15 <= len(iban) <= 34:
        return False
    digits = "".join(str(int(c, 36)) for c in iban[4:] + iban[:4])
    return int(digits) % 97 == 1


def find_ibans(text: str) -> list[str]:
    found = []
    for match in IBAN.finditer(text.upper()):
        iban = match.group(1).replace(" ", "")
        # the regex may run into the next word: shorten until the checksum matches
        for end in range(len(iban), 14, -1):
            if valid_iban(iban[:end]):
                if iban[:end] not in found:
                    found.append(iban[:end])
                break
    return found


# -- CSV -------------------------------------------------------------------------------
def _find(header: list[str], names: tuple[str, ...]) -> int | None:
    low = [h.strip().lower().strip('"') for h in header]
    for name in names:
        if name in low:
            return low.index(name)
    for i, h in enumerate(low):  # «Importo in EUR», «Data contabile (gg/mm/aaaa)»
        if any(h.startswith(name) for name in names):
            return i
    return None


def parse_csv(raw: bytes) -> list[Row]:
    text = raw.decode("utf-8-sig", errors="replace")
    try:
        delimiter = csv.Sniffer().sniff(text[:4096], delimiters=";,\t|").delimiter
    except csv.Error:
        delimiter = ";" if text.count(";") > text.count(",") else ","
    rows = [
        r for r in csv.reader(io.StringIO(text), delimiter=delimiter) if any(c.strip() for c in r)
    ]
    # The header is the first row with a date column and an amount (or in/out) column.
    for start, header in enumerate(rows[:30]):  # noqa: B007 - used after the loop
        d, a = _find(header, DATE_HEADERS), _find(header, AMOUNT_HEADERS)
        o, i = _find(header, OUT_HEADERS), _find(header, IN_HEADERS)
        t = _find(header, TEXT_HEADERS)
        if d is not None and (a is not None or (o is not None and i is not None)):
            break
    else:
        raise ImportError_("csv_header_unknown")
    p = _find(header, PARTY_HEADERS)
    out = []
    for row in rows[start + 1 :]:
        try:
            day = parse_date(row[d])
            if a is not None:
                amount = parse_decimal(row[a])
            else:
                assert o is not None and i is not None
                spent = parse_decimal(row[o]) if row[o].strip() else Decimal(0)
                got = parse_decimal(row[i]) if row[i].strip() else Decimal(0)
                amount = got - abs(spent)
        except (ValueError, IndexError):
            continue  # totals, notes and blank lines inside the export
        description = row[t].strip() if t is not None and t < len(row) else ""
        party = row[p].strip() if p is not None and p < len(row) else ""
        out.append(Row(day, amount, description or party, party))
    if not out:
        raise ImportError_("nothing_found")
    return out


# -- OFX / QFX -------------------------------------------------------------------------
def parse_ofx(raw: bytes) -> list[Row]:
    text = raw.decode("utf-8", errors="replace")
    out = []
    for block in re.findall(
        r"<STMTTRN>(.*?)(?:</STMTTRN>|(?=<STMTTRN>)|</BANKTRANLIST>)", text, re.S
    ):

        def tag(name: str, block: str = block) -> str:
            m = re.search(rf"<{name}>([^<\r\n]*)", block)
            return m.group(1).strip() if m else ""

        try:
            day = parse_date(tag("DTPOSTED")[:8])
            amount = parse_decimal(tag("TRNAMT"))
        except ValueError:
            continue
        name, memo = tag("NAME"), tag("MEMO")
        out.append(Row(day, amount, memo or name, name, tag("FITID")))
    if not out:
        raise ImportError_("nothing_found")
    return out


# -- PDF -------------------------------------------------------------------------------
def pdf_text(raw: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(raw))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except (PdfReadError, ValueError, OSError) as exc:
        raise ImportError_("pdf_unreadable") from exc


def guess_sign(text: str, amount: Decimal) -> Decimal:
    low = f" {text.lower()} "
    if any(w in low for w in IN_WORDS):
        return abs(amount)
    if any(w in low for w in OUT_WORDS):
        return -abs(amount)
    return -abs(amount)  # most lines of a statement are spending


def parse_pdf_lines(text: str) -> Parsed:
    """Statements without AI: «date [value date] description amount» lines."""
    rows, estimated = [], False
    for line in text.splitlines():
        m = PDF_LINE.match(line)
        if not m or re.search(r"\bsaldo\b|\bbalance\b|\bsolde\b", m["text"], re.IGNORECASE):
            continue
        try:
            day = parse_date(m["d1"])
            amount = parse_decimal(m["amount"].replace("−", "-").replace(" ", ""))
        except ValueError:
            continue
        description = m["text"].strip()
        if m["trail"] == "-" or m["amount"].lstrip().startswith(("-", "−")):
            amount = -abs(amount)
        elif m["trail"] == "+" or m["amount"].lstrip().startswith("+"):
            amount = abs(amount)
        else:
            amount, estimated = guess_sign(description, amount), True
        rows.append(Row(day, amount, description))
    balance = None
    if found := CLOSING.search(text):
        with contextlib.suppress(ValueError):
            balance = parse_decimal(found["amount"])
    return Parsed(rows, balance, estimated=estimated)


async def _by_ai(text: str, ai: AI) -> Parsed:
    try:
        data = await ai.extract_movements(text)
    except AIError as exc:
        raise ImportError_("ai_failed") from exc
    rows = []
    for item in data.get("movements", []):
        try:
            rows.append(
                Row(
                    parse_date(str(item["day"])),
                    parse_decimal(str(item["amount"])),
                    str(item.get("description") or ""),
                    str(item.get("counterparty") or ""),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    balance = None
    if data.get("balance") is not None:
        with contextlib.suppress(ValueError):
            balance = parse_decimal(str(data["balance"]))
    return Parsed(rows, balance, by_ai=True)


async def parse_statement(filename: str, raw: bytes, ai: AI) -> Parsed:
    name = filename.lower()
    if name.endswith((".ofx", ".qfx")) or raw.lstrip()[:9].upper().startswith(b"OFXHEADER"):
        parsed = Parsed(parse_ofx(raw))
        text = raw.decode("utf-8", errors="replace")
    elif name.endswith((".csv", ".txt", ".tsv")):
        text = raw.decode("utf-8-sig", errors="replace")
        try:
            parsed = Parsed(parse_csv(raw))
        except ImportError_:
            if not ai.can_read_text:
                raise
            parsed = await _by_ai(text, ai)
    elif name.endswith(".pdf"):
        text = pdf_text(raw)
        if not text.strip():
            raise ImportError_("pdf_scanned")
        parsed = Parsed([])
        if ai.can_read_text:
            with contextlib.suppress(ImportError_):
                parsed = await _by_ai(text, ai)
        if not parsed.rows:
            parsed = parse_pdf_lines(text)
    else:
        raise ImportError_("format_unsupported")
    if not parsed.rows:
        raise ImportError_("nothing_found")
    parsed.ibans = find_ibans(text)
    return parsed
