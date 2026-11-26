"""Search the movements the way a person types, in any of the five languages.

A query is cut into pieces and each piece is read in every way it could be meant; a movement
matches when EVERY piece is satisfied by at least one of its readings:

- amounts, in any format: «12,50», «12.50», «1.234,56», «1 234,56», «€12», «12 euro»,
  «-50» (money out), «>100», «sotto 20», «tra 50 e 100», «circa 30», and numbers in words
  («dodici euro», «twenty five»). A whole number also finds its cents («12» finds 12,50),
  unless it carries a currency («12 euro» is exactly 12);
- dates: «2026-11-26», «26/11/2026», «26.11.26», «26/11», «26 nov», «26 novembre 2026»,
  «novembre 2026», «novembre», «2026», «ieri», «oggi», «mese scorso», «last week»,
  «ultimi 30 giorni», with months in Italian, English, German, French or Spanish,
  abbreviated or misspelled («novmbre», «sett», «ottobr»);
- words: found inside the wording of a movement, also misspelled (similar words), and by
  meaning: «benzina» finds the category Fuel, «mangiare» Restaurants and bars, «treno»
  Transport, whatever the payee is called;
- «entrate», «incassi», «income» (money in) and «spese», «uscite», «pagamenti» (money out).

Everything is deterministic. `understand` says how each piece was read, so the person sees
what was looked for."""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from oink.core.db import Transaction
from oink.core.money import from_minor

# -- vocabulary ---------------------------------------------------------------------------
MONTHS: dict[str, int] = {}
for number, names in {
    1: "gennaio january januar janvier enero",
    2: "febbraio february februar fevrier febrero",
    3: "marzo march marz mars",
    4: "aprile april avril abril",
    5: "maggio may mai mayo",
    6: "giugno june juni juin junio",
    7: "luglio july juli juillet julio",
    8: "agosto august aout",
    9: "settembre september septembre septiembre setiembre",
    10: "ottobre october oktober octobre octubre",
    11: "novembre november novembre noviembre",
    12: "dicembre december dezember decembre diciembre",
}.items():
    for name in names.split():
        MONTHS[name] = number
#: abbreviations people really write («sett», «dic.», «gen»)
for abbreviation, number in {
    "gen": 1, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6, "jun": 6,
    "lug": 7, "jul": 7, "ago": 8, "aug": 8, "set": 9, "sett": 9, "sep": 9, "ott": 10,
    "oct": 10, "okt": 10, "nov": 11, "dic": 12, "dec": 12, "dez": 12, "ene": 1, "avr": 4,
    "juil": 7, "dez.": 12,
}.items():  # fmt: skip
    MONTHS[abbreviation] = number

STOP = set(
    [
        "di",
        "da",
        "a",
        "il",
        "lo",
        "la",
        "i",
        "gli",
        "le",
        "un",
        "una",
        "uno",
        "per",
        "con",
        "in",
        "su",
        "tra",
        "fra",
        "del",
        "della",
        "dei",
        "delle",
        "dello",
        "al",
        "alla",
        "alle",
        "ai",
        "e",
        "ed",
        "o",
        "the",
        "of",
        "and",
        "for",
        "to",
        "from",
        "on",
        "at",
        "an",
        "my",
        "mio",
        "mia",
        "miei",
        "mie",
        "che",
        "der",
        "die",
        "das",
        "und",
        "von",
        "mit",
        "le",
        "les",
        "de",
        "du",
        "des",
        "et",
        "en",
        "el",
        "los",
        "las",
        "y",
        "para",
        "por",
        "con",
    ]
)
IN_WORDS = set(
    [
        "entrata",
        "entrate",
        "incasso",
        "incassi",
        "incassato",
        "accredito",
        "accrediti",
        "entrati",
        "guadagno",
        "guadagni",
        "income",
        "incomes",
        "received",
        "earned",
        "einnahmen",
        "revenus",
        "ingresos",
    ]
)
OUT_WORDS = set(
    [
        "spesa",
        "spese",
        "uscita",
        "uscite",
        "pagamento",
        "pagamenti",
        "addebito",
        "addebiti",
        "speso",
        "spesi",
        "spent",
        "expense",
        "expenses",
        "payment",
        "payments",
        "ausgaben",
        "depenses",
        "gastos",
    ]
)

#: what each category means to a person (Italian, English and a few more)
LEXICON: dict[str, str] = {
    "groceries": "spesa supermercato supermercati alimentari alimentare cibo esselunga coop "
    "conad carrefour lidl eurospin famila pam grocery groceries supermarket food market "
    "lebensmittel courses epicerie supermercado",
    "restaurants": "ristorante ristoranti bar caffe cafe pizza pizzeria sushi pranzo cena colazione "
    "mangiare mangiato aperitivo birra pub trattoria osteria fastfood kebab panino gelato "
    "restaurant restaurants dinner lunch breakfast coffee eating dining essen restaurante",
    "home": "casa affitto mutuo condominio arredo mobili ikea home rent mortgage furniture miete "
    "loyer alquiler",
    "bills": "bolletta bollette luce gas acqua elettricita internet telefono cellulare fibra tim "
    "vodafone iliad wind fastweb enel eni bill bills electricity water phone rechnung facture "
    "factura",
    "transport": "trasporti trasporto treno treni bus autobus metro taxi uber tram biglietto "
    "trenitalia italo flixbus aereo volo transport train ticket zug transporte",
    "car": "auto macchina automobile assicurazione bollo meccanico gommista parcheggio parking "
    "autostrada telepass pedaggio revisione car insurance mechanic toll",
    "fuel": "benzina carburante gasolio diesel distributore rifornimento pieno gpl fuel gas petrol "
    "gasoline station tankstelle carburant gasolina",
    "health": "salute medico dottore farmacia dentista visita ospedale analisi palestra fitness "
    "gym health doctor pharmacy dentist hospital arzt gesundheit sante salud",
    "leisure": "svago divertimento cinema teatro concerto videogiochi giochi gaming steam "
    "playstation netflix libri hobby sport leisure fun movies games entertainment freizeit "
    "loisirs ocio",
    "travel": "viaggio viaggi vacanza vacanze hotel albergo airbnb volo voli ryanair booking "
    "travel trip holiday vacation flight urlaub voyage viaje",
    "subscriptions": "abbonamento abbonamenti subscription subscriptions spotify netflix disney "
    "prime dazn youtube icloud apple google chatgpt claude anthropic openai ovh hosting dominio "
    "abo abonnement suscripcion",
    "shopping": "shopping acquisti acquisto amazon aliexpress vestiti abbigliamento scarpe zalando "
    "shein vinted ebay negozio shopping clothes shoes store kleidung vetements ropa",
    "education": "istruzione scuola universita corso corsi libri tasse universitarie iscrizione "
    "school university course tuition education schule ecole escuela",
    "gifts": "regalo regali donazione donazioni beneficenza gift gifts donation charity geschenk "
    "cadeau regalo",
    "taxes": "tasse tassa imposte imposta tributi f24 irpef iva taxes tax steuern impots impuestos",
    "fees": "commissione commissioni costi canone spese bancarie fee fees commission charges "
    "gebuhr frais comision",
    "loans": "finanziamento finanziamenti prestito prestiti rata rate mutuo loan loans installment "
    "kredit pret prestamo klarna",
    "salary": "stipendio stipendi busta paga salario salary salaries paycheck wage gehalt salaire "
    "sueldo competenze",
    "income": "entrate entrata reddito rimborso rimborsi income refund refunds einkommen revenu "
    "ingreso",
    "transfers": "trasferimento trasferimenti bonifico bonifici giroconto giroconti transfer "
    "transfers umbuchung virement transferencia",
    "savings": "risparmio risparmi salvadanaio deposito accantonato savings saved sparen epargne "
    "ahorro",
    "investments": "investimento investimenti etf azioni titoli borsa pac piano accumulo "
    "investment investments stocks shares trade republic scalable invest",
    "crypto": "crypto bitcoin btc ethereum eth criptovalute cripto young platform kraken binance",
    "other": "altro varie varie other misc",
}
_SYNONYMS: dict[str, set[str]] = {}
for _slug, _words in LEXICON.items():
    for _w in _words.split():
        _SYNONYMS.setdefault(_w, set()).add(_slug)

_UNITS_IT = {
    "zero": 0, "uno": 1, "un": 1, "una": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5,
    "sei": 6, "sette": 7, "otto": 8, "nove": 9, "dieci": 10, "undici": 11, "dodici": 12,
    "tredici": 13, "quattordici": 14, "quindici": 15, "sedici": 16, "diciassette": 17,
    "diciotto": 18, "diciannove": 19, "venti": 20, "trenta": 30, "quaranta": 40,
    "cinquanta": 50, "sessanta": 60, "settanta": 70, "ottanta": 80, "novanta": 90,
}  # fmt: skip
_UNITS_EN = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}  # fmt: skip


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.replace("’", "'").replace("`", "'")


def _it_number(word: str) -> int | None:
    """«dodici», «ventidue», «centoventi», «milleduecento», «duemila»: an Italian number
    written as one word (up to 999,999)."""
    if word in _UNITS_IT:
        return _UNITS_IT[word]
    thousands = 0
    rest = word
    if "mila" in rest:
        head, _, rest = rest.partition("mila")
        multiplier = _it_number(head) if head else None
        if multiplier is None:
            return None
        thousands = multiplier * 1000
    elif rest.startswith("mille"):
        thousands, rest = 1000, rest[5:]
    if not rest:
        return thousands or None
    total = 0
    if "cent" in rest:
        head, _, rest = rest.partition("cent")
        hundreds = 1 if head == "" else _UNITS_IT.get(head)
        if hundreds is None or hundreds > 9:
            return None
        total += hundreds * 100
        if rest.startswith("o"):  # cento / cent'
            rest = rest[1:]
    if rest:
        # tens glued to units: «ventidue», «trentotto» (the final vowel of the ten drops)
        found = None
        for ten_word, ten in _UNITS_IT.items():
            if ten < 20 or ten % 10:
                continue
            for stem in (ten_word, ten_word[:-1]):
                if rest.startswith(stem):
                    unit = rest[len(stem) :]
                    if unit == "":
                        found = ten
                    elif unit in _UNITS_IT and _UNITS_IT[unit] < 10 and _UNITS_IT[unit] > 0:
                        found = ten + _UNITS_IT[unit]
                    if found is not None:
                        break
            if found is not None:
                break
        if found is None:
            if rest in _UNITS_IT:
                found = _UNITS_IT[rest]
            else:
                return None
        total += found
    return thousands + total


def _words_to_number(words: list[str]) -> int | None:
    """English «twenty five», «one hundred twelve» or one Italian word."""
    if len(words) == 1:
        return _it_number(words[0]) if words[0] not in _UNITS_EN else _UNITS_EN[words[0]]
    total = current = 0
    for w in words:
        if w in _UNITS_EN:
            current += _UNITS_EN[w]
        elif w == "hundred":
            current = max(current, 1) * 100
        elif w == "thousand":
            total += max(current, 1) * 1000
            current = 0
        elif w == "and":
            continue
        else:
            return None
    return total + current


# -- how a piece can be read ---------------------------------------------------------------
Pred = Callable[["Row"], bool]


@dataclass
class Row:
    """A movement prepared once for matching."""

    tx: Transaction
    text: str
    words: list[str]
    amount: Decimal  # absolute value, in its own currency
    out: bool


@dataclass
class Piece:
    kind: str  # amount | date | word | category | direction
    label: str
    alts: list[Pred] = field(default_factory=list)


@dataclass
class Understood:
    pieces: list[Piece]

    def chips(self) -> list[dict[str, str]]:
        return [{"kind": p.kind, "label": p.label} for p in self.pieces]


def row_of(tx: Transaction) -> Row:
    text = normalise(f"{tx.description} {tx.counterparty} {tx.note}")
    return Row(
        tx=tx,
        text=text,
        words=[w for w in re.split(r"[^a-z0-9]+", text) if w],
        amount=abs(from_minor(tx.amount, tx.currency)),
        out=tx.amount < 0,
    )


def _amount_pred(low: Decimal, high: Decimal, sign: str = "") -> Pred:
    def pred(row: Row) -> bool:
        if sign == "out" and not row.out:
            return False
        if sign == "in" and row.out:
            return False
        return low <= row.amount <= high

    return pred


def _date_pred(
    start: date | None = None, end: date | None = None, *, year: int | None = None,
    month: int | None = None, day: int | None = None,
) -> Pred:  # fmt: skip
    def pred(row: Row) -> bool:
        d = row.tx.day
        if start is not None and d < start:
            return False
        if end is not None and d > end:
            return False
        return (
            (year is None or d.year == year)
            and (month is None or d.month == month)
            and (day is None or d.day == day)
        )

    return pred


def month_of(word: str) -> int | None:
    """A month name, abbreviation or misspelling («novmbre», «sett»)."""
    w = word.strip(".")
    if w in MONTHS:
        return MONTHS[w]
    if len(w) < 4:
        return None
    for name, number in MONTHS.items():
        if len(name) > len(w) and name.startswith(w) and len(w) >= 4:
            return number  # «novemb» → novembre
    close = difflib.get_close_matches(w, [n for n in MONTHS if len(n) >= 5], n=1, cutoff=0.8)
    return MONTHS[close[0]] if close else None


_CUR = r"(?:€|eur(?:o|os)?|euros?|usd|\$|dollar[io]?|gbp|£|sterline|chf|yen|jpy)"
_NUM = (
    r"[-+−]?\d{1,3}(?:[ .',](?=\d{3}(?!\d))\d{3})+(?:[.,]\d{1,2})?"  # 1.234,56 / 1 234,56
    r"|[-+−]?\d+(?:[.,]\d{1,2})?"
)


def parse_number(raw: str) -> tuple[Decimal, bool] | None:
    """«1.234,56» → (1234.56, True); «12» → (12, False). The flag says it had decimals."""
    s = raw.strip().replace("−", "-").replace("’", "'").replace(" ", "")
    s = s.lstrip("+-")
    if not s:
        return None
    if "," in s and "." in s:
        decimal = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if decimal == "," else ",", "").replace(decimal, ".")
        has = True
    elif "," in s or "." in s:
        sep = "," if "," in s else "."
        head, _, tail = s.rpartition(sep)
        if s.count(sep) > 1 or (len(tail) == 3 and len(head) >= 1 and head != "0"):
            s = s.replace(sep, "")  # thousands separator: «1.234»
            has = False
        else:
            s = f"{head}.{tail}"
            has = True
    else:
        s = s.replace("'", "")
        has = False
    try:
        return Decimal(s), has
    except InvalidOperation:
        return None


def _amount_piece(value: Decimal, has_decimals: bool, currency: bool, sign: str) -> Piece:
    exact = has_decimals or currency
    if exact:
        half = Decimal("0.005")
        low, high = value - half, value + half
        label = f"{value:,.2f}".replace(",", " ").replace(".", ",") + " €"
    else:  # «12» finds 12,00 to 12,99
        low, high = value, value + Decimal("0.99")
        label = f"{value:,.0f}".replace(",", " ") + " € (con i centesimi)"
    return Piece("amount", label, [_amount_pred(low, high, sign)])


def _clean_year(y: str) -> int | None:
    n = int(y)
    if len(y) == 2:
        n += 2000
    return n if 1990 <= n <= 2100 else None


def understand(query: str, today: date | None = None) -> Understood:
    """The pieces of a query and what each could mean."""
    today = today or date.today()
    q = " " + normalise(query).replace("€", " eur ").replace("$", " usd ") + " "
    pieces: list[Piece] = []

    def take(pattern: str, build: Callable[[re.Match[str]], Piece | None]) -> None:
        nonlocal q
        regex, pos = re.compile(pattern), 0
        while (m := regex.search(q, pos)) is not None:
            piece = build(m)
            if piece is None:  # not what it looked like: the other readers may want it
                pos = m.start() + 1
                continue
            pieces.append(piece)
            q = q[: m.start()] + " " + q[m.end() :]
            pos = m.start() + 1

    # relative dates
    def relative(m: re.Match[str]) -> Piece | None:
        word = m.group(1).strip()
        monday = today - timedelta(days=today.weekday())
        table: dict[str, tuple[str, date, date]] = {
            "oggi": ("oggi", today, today),
            "today": ("oggi", today, today),
            "hoy": ("oggi", today, today),
            "heute": ("oggi", today, today),
            "ieri": ("ieri", today - timedelta(1), today - timedelta(1)),
            "yesterday": ("ieri", today - timedelta(1), today - timedelta(1)),
            "ayer": ("ieri", today - timedelta(1), today - timedelta(1)),
            "gestern": ("ieri", today - timedelta(1), today - timedelta(1)),
            "hier": ("ieri", today - timedelta(1), today - timedelta(1)),
            "altro ieri": ("l'altro ieri", today - timedelta(2), today - timedelta(2)),
            "ieri l'altro": ("l'altro ieri", today - timedelta(2), today - timedelta(2)),
            "questa settimana": ("questa settimana", monday, today),
            "this week": ("questa settimana", monday, today),
            "settimana scorsa": ("settimana scorsa", monday - timedelta(7), monday - timedelta(1)),
            "scorsa settimana": ("settimana scorsa", monday - timedelta(7), monday - timedelta(1)),
            "last week": ("settimana scorsa", monday - timedelta(7), monday - timedelta(1)),
            "questo mese": ("questo mese", today.replace(day=1), today),
            "this month": ("questo mese", today.replace(day=1), today),
            "mese scorso": ("mese scorso", *_prev_month(today)),
            "scorso mese": ("mese scorso", *_prev_month(today)),
            "last month": ("mese scorso", *_prev_month(today)),
            "quest'anno": ("quest'anno", date(today.year, 1, 1), today),
            "this year": ("quest'anno", date(today.year, 1, 1), today),
            "anno scorso": (
                "anno scorso",
                date(today.year - 1, 1, 1),
                date(today.year - 1, 12, 31),
            ),
            "last year": ("anno scorso", date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)),
        }
        found = table.get(word)
        if found is None:
            return None
        return Piece("date", found[0], [_date_pred(found[1], found[2])])

    take(
        r"(?<![a-z0-9'])(oggi|today|hoy|heute|ieri l'altro|altro ieri|ieri|yesterday|ayer|gestern|hier"
        r"|questa settimana|this week|settimana scorsa|scorsa settimana|last week|questo mese"
        r"|this month|mese scorso|scorso mese|last month|quest'anno|this year|anno scorso"
        r"|last year)(?![a-z0-9])",
        relative,
    )

    # «ultimi 30 giorni», «last 3 months»
    def last_n(m: re.Match[str]) -> Piece | None:
        n = int(m.group(1))
        unit = m.group(2)
        days = n * (
            30
            if unit.startswith(("mes", "month", "mois", "monat"))
            else 365
            if unit.startswith(("ann", "year", "jahr", "an"))
            else 7
            if unit.startswith(("settiman", "week", "woche", "semain"))
            else 1
        )
        return Piece("date", f"ultimi {n} {unit}", [_date_pred(today - timedelta(days), today)])

    take(
        r"(?:ultimi|ultime|last|past|letzte|derniers|ultimos)\s+(\d{1,3})\s+"
        r"(giorni|giorno|days|day|mesi|mese|months|month|settimane|settimana|weeks|week|anni|anno|years|year)",
        last_n,
    )

    # amounts: ranges, comparators, approximations
    def between(m: re.Match[str]) -> Piece | None:
        a, b = parse_number(m[1]), parse_number(m[2])
        if a is None or b is None:
            return None
        low, high = sorted((a[0], b[0]))
        return Piece(
            "amount",
            f"{low:g}–{high:g} €",
            [_amount_pred(low, high + Decimal("0.99") if not (a[1] or b[1]) else high)],
        )

    take(
        rf"(?:tra|fra|between|zwischen|entre|da|from)\s+({_NUM})\s*{_CUR}?\s+(?:e|a|and|to|und|et|y|bis)\s+({_NUM})(?:\s*{_CUR})?",
        between,
    )
    take(rf"(?<![\d.,/-])({_NUM})\s*(?:\.\.|–|—|\s-\s)\s*({_NUM})(?![\d])", between)

    def compare(m: re.Match[str]) -> Piece | None:
        op = m[1].strip()
        n = parse_number(m[2])
        if n is None:
            return None
        value = n[0]
        big = Decimal("1e12")
        greater = op in (">", ">=", "oltre", "sopra", "piu di", "più di", "over", "above",
                         "more than", "almeno", "min", "minimo", "mehr als", "plus de", "mas de")  # fmt: skip
        if greater:
            sign = ">=" if op in (">=", "almeno", "min", "minimo") else ">"
            low = value if sign == ">=" else value + Decimal("0.01")
            return Piece("amount", f"{sign} {value:g} €", [_amount_pred(low, big)])
        sign = "<=" if op in ("<=", "max", "massimo") else "<"
        high = value if sign == "<=" else value - Decimal("0.01")
        return Piece("amount", f"{sign} {value:g} €", [_amount_pred(Decimal(0), high)])

    take(
        rf"(>=|<=|>|<|oltre|sopra|piu di|più di|over|above|more than|almeno|min|minimo|mehr als|plus de|mas de"
        rf"|sotto|meno di|under|below|less than|massimo|max|weniger als|moins de|menos de)\s*({_NUM})(?:\s*{_CUR})?",
        compare,
    )

    def approx(m: re.Match[str]) -> Piece | None:
        n = parse_number(m[1])
        if n is None:
            return None
        v = n[0]
        return Piece(
            "amount", f"circa {v:g} €", [_amount_pred(v * Decimal("0.9"), v * Decimal("1.1"))]
        )

    take(rf"(?:circa|intorno a|around|about|approx|ca\.?|~)\s*({_NUM})(?:\s*{_CUR})?", approx)

    # 2026-11-26
    take(
        r"(?<![\d.,/-])(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?![\d])",
        lambda m: _full_date(int(m[1]), int(m[2]), int(m[3])),
    )

    # 26/11/2026, 26.11.26, 26-11-2026
    def dmy(m: re.Match[str]) -> Piece | None:
        a, b = int(m[1]), int(m[2])
        year = _clean_year(m[3])
        if year is None:
            return None
        day, month = (a, b) if b <= 12 else (b, a)
        return _full_date(year, month, day)

    take(r"(?<![\d.,/-])(\d{1,2})[-/. ](\d{1,2})[-/.](\d{2,4})(?![\d])", dmy)
    take(r"(?<![\d.,/-])(\d{1,2})[/.](\d{1,2})[ ](\d{4})(?![\d])", dmy)

    # 26 novembre 2026 / 26 nov / 26 di novembre
    def day_month(m: re.Match[str]) -> Piece | None:
        month = month_of(m[2])
        day = int(m[1])
        if month is None or not 1 <= day <= 31:
            return None
        year = _clean_year(m[3]) if m[3] else None
        label = f"{day} {_MONTH_LABEL[month]}" + (f" {year}" if year else "")
        return Piece("date", label, [_date_pred(year=year, month=month, day=day)])

    take(
        r"(?<![\d.,/-])(\d{1,2})(?:o|°|st|nd|rd|th)?\s+(?:di\s+|of\s+|de\s+)?([a-z]{3,12}\.?)"
        r"(?:\s+(\d{4}|\d{2})(?![\d.,]))?",
        day_month,
    )

    # novembre 2026
    def month_year(m: re.Match[str]) -> Piece | None:
        month = month_of(m[1])
        year = _clean_year(m[2])
        if month is None or year is None:
            return None
        return Piece("date", f"{_MONTH_LABEL[month]} {year}", [_date_pred(year=year, month=month)])

    take(r"(?<![a-z0-9])([a-z]{3,12}\.?)\s+(?:del\s+|of\s+|de\s+)?(\d{4})(?![\d])", month_year)

    # 26/11 (day/month, any year); with a dot it can also be an amount (12.50)
    def day_slash_month(m: re.Match[str]) -> Piece | None:
        day, month = int(m[1]), int(m[2])
        if not (1 <= day <= 31 and 1 <= month <= 12):
            return None
        alts: list[Pred] = [_date_pred(month=month, day=day)]
        label = f"{day}/{month}"
        if m[0].strip().count(".") == 1 and len(m[2]) <= 2:
            value = parse_number(m[0].strip())
            if value:
                alts.append(_amount_pred(value[0] - Decimal("0.005"), value[0] + Decimal("0.005")))
                label = f"{day}/{month} o {m[0].strip()} €"
        return Piece("date", label, alts)

    take(r"(?<![\d.,/-])(\d{1,2})[/.-](\d{1,2})(?![\d.,/-])", day_slash_month)

    # a plain amount, with or without currency
    def plain(m: re.Match[str]) -> Piece | None:
        raw = m[2]
        n = parse_number(raw)
        if n is None:
            return None
        value, has = n
        sign = "out" if raw.strip().startswith(("-", "−")) else ""
        currency = bool(m[1] or m[3])
        piece = _amount_piece(value, has, currency, sign)
        year = _clean_year(raw) if raw.isdigit() and len(raw) == 4 else None
        if year is not None and not currency:
            piece.alts.append(_date_pred(year=year))  # «2026»: a year, or an amount
            piece.kind, piece.label = "date", f"{year} o {value:,.0f} €".replace(",", " ")
        if sign:
            piece.label = "uscita " + piece.label
        return piece

    take(rf"(?<![a-z0-9.,/-])(?:({_CUR})\s*)?({_NUM})\s*({_CUR})?(?![a-z0-9])", plain)

    # numbers in words followed by a currency: «dodici euro», «twenty five euro»
    while True:
        found = None
        for cur in re.finditer(r"(?<![a-z])(?:eur|euro|euros)(?![a-z])", q):
            before = q[: cur.start()]
            toks = list(re.finditer(r"[a-z]+", before))[-4:]
            for k in range(len(toks), 0, -1):
                span = toks[-k:]
                gaps = [before[span[i].end() : span[i + 1].start()] for i in range(len(span) - 1)]
                gaps.append(before[span[-1].end() :])
                if any(g.strip() for g in gaps):
                    continue
                value = _words_to_number([t.group() for t in span])
                if value is not None and (value or span[-1].group() == "zero"):
                    found = (span[0].start(), cur.end(), value)
                    break
            if found:
                break
        if not found:
            break
        start, end, value = found
        pieces.append(_amount_piece(Decimal(value), False, True, ""))
        q = q[:start] + " " + q[end:]

    # what is left: single words
    for raw in re.split(r"[^a-z0-9']+", q):
        word = raw.strip("'")
        if not word or word in STOP or len(word) < 2:
            continue
        pieces.append(_word_piece(word))
    return Understood(pieces)


_MONTH_LABEL = {
    1: "gennaio", 2: "febbraio", 3: "marzo", 4: "aprile", 5: "maggio", 6: "giugno",
    7: "luglio", 8: "agosto", 9: "settembre", 10: "ottobre", 11: "novembre", 12: "dicembre",
}  # fmt: skip


def _prev_month(today: date) -> tuple[date, date]:
    end = today.replace(day=1) - timedelta(days=1)
    return end.replace(day=1), end


def _full_date(year: int, month: int, day: int) -> Piece | None:
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    return Piece("date", d.strftime("%d/%m/%Y"), [_date_pred(d, d)])


def _direction_pred(out: bool) -> Pred:
    def pred(row: Row) -> bool:
        return row.out == out

    return pred


def _category_pred(slugs: frozenset[str]) -> Pred:
    def pred(row: Row) -> bool:
        return row.tx.category in slugs

    return pred


def _word_pred(word: str) -> Pred:
    def pred(row: Row) -> bool:
        return _word_in(row, word)

    return pred


def _word_piece(word: str) -> Piece:
    if word in IN_WORDS:
        return Piece("direction", "entrate", [_direction_pred(False)])
    if word in OUT_WORDS:
        return Piece("direction", "uscite", [_direction_pred(True)])
    month = month_of(word) if len(word) >= 3 and not word.isdigit() else None
    if month is not None:
        return Piece("date", _MONTH_LABEL[month], [_date_pred(month=month)])
    alts: list[Pred] = []
    slugs = _SYNONYMS.get(word, set())
    if len(word) >= 4 and not slugs:  # «ristorant», «benzin»: the start of a known word
        for known, found in _SYNONYMS.items():
            if known.startswith(word) and len(word) >= 5:
                slugs = slugs | found
    if slugs:
        alts.append(_category_pred(frozenset(slugs)))
    alts.append(_word_pred(word))
    return Piece("category" if slugs else "word", word, alts)


def _word_in(row: Row, word: str) -> bool:
    if len(word) <= 2:
        return word in row.words
    if word in row.text:
        return True
    if len(word) >= 4:
        return any(
            abs(len(word) - len(w)) <= 2 and difflib.SequenceMatcher(None, word, w).ratio() >= 0.82
            for w in row.words
            if len(w) >= 4
        )
    return False


def matches(row: Row, parsed: Understood) -> bool:
    return all(any(alt(row) for alt in p.alts) for p in parsed.pieces)


def search(txs: list[Transaction], query: str, today: date | None = None) -> list[Transaction]:
    parsed = understand(query, today)
    if not parsed.pieces:
        return txs
    return [t for t in txs if matches(row_of(t), parsed)]


# -- the AI as a last resort ----------------------------------------------------------------
async def interpret_with_ai(ai: Any, query: str, today: date) -> Understood | None:
    """When nothing matched, the AI turns the sentence into filters (it never sees movements)."""
    if not getattr(ai, "can_read_text", False):
        return None
    data = await ai.json(
        "Turn a search over personal finance movements into JSON filters. Reply ONLY with "
        '{"words": [payee words], "categories": [from: ' + ", ".join(LEXICON) + "], "
        '"date_from": "YYYY-MM-DD" or null, "date_to": "YYYY-MM-DD" or null, '
        '"min_amount": number or null, "max_amount": number or null, '
        '"direction": "in" | "out" | null}. Today is ' + today.isoformat() + ". "
        "Amounts are absolute values. Use only what the sentence says.",
        query[:300],
    )
    if not isinstance(data, dict):
        return None
    pieces: list[Piece] = []

    def number(key: str) -> Decimal | None:
        try:
            return Decimal(str(data[key])) if data.get(key) is not None else None
        except InvalidOperation:
            return None

    for w in data.get("words") or []:
        if isinstance(w, str) and w.strip():
            pieces.append(_word_piece(normalise(w.strip())))
    cats = {c for c in data.get("categories") or [] if c in LEXICON}
    if cats:
        pieces.append(
            Piece(
                "category",
                ", ".join(sorted(cats)),
                [_category_pred(frozenset(cats))],
            )
        )
    start, end = _iso(data.get("date_from")), _iso(data.get("date_to"))
    if start or end:
        label = f"{start or '…'} → {end or '…'}"
        pieces.append(Piece("date", label, [_date_pred(start, end)]))
    low, high = number("min_amount"), number("max_amount")
    if low is not None or high is not None:
        pieces.append(
            Piece(
                "amount",
                f"{low or 0:g}–{high or '∞'} €",
                [_amount_pred(low or Decimal(0), high or Decimal("1e12"))],
            )
        )
    if data.get("direction") in ("in", "out"):
        out = data["direction"] == "out"
        pieces.append(Piece("direction", "uscite" if out else "entrate", [_direction_pred(out)]))
    return Understood(pieces) if pieces else None


def _iso(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None
