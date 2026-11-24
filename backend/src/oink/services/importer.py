"""Statements in: CSV of the common Italian/European layouts read directly; PDFs and
unknown layouts read by the AI. Everything goes through `ledger.add_movements`."""

from __future__ import annotations

import contextlib
import csv
import io
from dataclasses import dataclass
from datetime import date, datetime

from oink.services.ai import AI, AIError
from oink.services.ledger import NewMovement, to_cents

DATE_HEADERS = (
    "data contabile",
    "data operazione",
    "data",
    "date",
    "booking date",
    "data valuta",
    "value date",
    "fecha",
)
AMOUNT_HEADERS = ("importo", "amount", "importo (eur)", "importo eur", "betrag", "importe")
OUT_HEADERS = ("uscite", "addebiti", "dare", "debit", "addebito")
IN_HEADERS = ("entrate", "accrediti", "avere", "credit", "accredito")
TEXT_HEADERS = (
    "descrizione",
    "descrizione operazione",
    "causale",
    "description",
    "dettagli",
    "details",
    "concetto",
    "operazione",
)
PARTY_HEADERS = ("beneficiario", "controparte", "esercente", "payee", "merchant", "ordinante")
DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%Y")


class ImportError_(ValueError):
    pass


@dataclass
class Parsed:
    movements: list[NewMovement]
    balance_cents: int | None = None
    by_ai: bool = False


def parse_date(text: str) -> date:
    text = text.strip()[:10]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"data non leggibile: {text!r}")


def _find(header: list[str], names: tuple[str, ...]) -> int | None:
    low = [h.strip().lower().strip('"') for h in header]
    for name in names:
        if name in low:
            return low.index(name)
    for i, h in enumerate(low):  # «Importo in EUR», «Data contabile (gg/mm/aaaa)»
        if any(h.startswith(name) for name in names):
            return i
    return None


def parse_csv(raw: bytes) -> list[NewMovement]:
    text = raw.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t|")
        delimiter = dialect.delimiter
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
        raise ImportError_("intestazione del CSV non riconosciuta")
    p = _find(header, PARTY_HEADERS)
    movements = []
    for row in rows[start + 1 :]:
        try:
            day = parse_date(row[d])
            if a is not None:
                cents = to_cents(row[a])
            else:
                assert o is not None and i is not None
                out = to_cents(row[o]) if row[o].strip() else 0
                inn = to_cents(row[i]) if row[i].strip() else 0
                cents = inn - abs(out)
        except (ValueError, IndexError):
            continue  # totals, notes and blank lines inside the export
        description = row[t].strip() if t is not None and t < len(row) else ""
        party = row[p].strip() if p is not None and p < len(row) else ""
        movements.append(
            NewMovement(
                day=day,
                amount_cents=cents,
                description=description or party or "movimento",
                counterparty=party,
            )
        )
    if not movements:
        raise ImportError_("nessun movimento leggibile nel CSV")
    return movements


def pdf_text(raw: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(raw))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except (PdfReadError, ValueError, OSError) as exc:
        raise ImportError_("PDF illeggibile o protetto da password") from exc


async def parse_statement(filename: str, raw: bytes, ai: AI) -> Parsed:
    name = filename.lower()
    if name.endswith((".csv", ".txt", ".tsv")):
        try:
            return Parsed(parse_csv(raw))
        except ImportError_:
            if not ai.enabled:
                raise
            text = raw.decode("utf-8-sig", errors="replace")
    elif name.endswith(".pdf"):
        text = pdf_text(raw)
        if not text.strip():
            raise ImportError_("il PDF non contiene testo (è una scansione?)")
        if not ai.enabled:
            raise ImportError_("per leggere i PDF serve l'IA (OINK_AI_MODEL)")
    else:
        raise ImportError_("formato non supportato: carica un CSV o un PDF")
    try:
        rows = await ai.extract_movements(text)
    except AIError as exc:
        raise ImportError_(str(exc)) from exc
    movements, balance = [], None
    for row in rows:
        if "_balance" in row:
            with contextlib.suppress(ValueError):
                balance = to_cents(str(row["_balance"]))
            continue
        try:
            movements.append(
                NewMovement(
                    day=parse_date(str(row["day"])),
                    amount_cents=to_cents(str(row["amount"])),
                    description=str(row.get("description") or "movimento"),
                    counterparty=str(row.get("counterparty") or ""),
                )
            )
        except (KeyError, ValueError):
            continue
    if not movements:
        raise ImportError_("nessun movimento trovato nell'estratto")
    return Parsed(movements, balance, by_ai=True)


def guess_month(movements: list[NewMovement]) -> str:
    """The month most movements belong to («2026-11»)."""
    months = [m.day.strftime("%Y-%m") for m in movements]
    return max(set(months), key=months.count) if months else ""
