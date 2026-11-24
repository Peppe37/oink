"""Money in any currency, fiat or crypto. Amounts are stored as integers in the currency's
smallest unit we keep: cents for EUR, none for JPY, and 8 decimals for crypto (satoshi for
BTC; finer dust than that is dropped). Integers mean exact sums, also in SQL."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

#: ISO 4217 currencies that don't use 2 decimals.
ZERO_DECIMALS = {"JPY", "KRW", "HUF", "ISK", "CLP", "VND", "XOF", "XAF", "PYG", "UGX", "IDR"}
THREE_DECIMALS = {"BHD", "KWD", "OMR", "JOD", "TND", "IQD", "LYD"}
CRYPTO_DECIMALS = 8

#: Crypto assets Oink knows by symbol, with their CoinGecko id (for prices).
CRYPTO: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "USDT": "tether",
    "USDC": "usd-coin",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "DOT": "polkadot",
    "LTC": "litecoin",
    "TRX": "tron",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "TON": "the-open-network",
    "XLM": "stellar",
    "ATOM": "cosmos",
    "XMR": "monero",
    "POL": "polygon-ecosystem-token",
    "SHIB": "shiba-inu",
    "EURC": "euro-coin",
    "DAI": "dai",
}

#: Fiat currencies offered in the app (the ECB reference rates cover all of them).
FIAT = [
    "EUR", "USD", "GBP", "CHF", "JPY", "CAD", "AUD", "NZD", "SEK", "NOK", "DKK", "PLN", "CZK",
    "HUF", "RON", "BGN", "TRY", "ISK", "CNY", "HKD", "SGD", "KRW", "INR", "BRL", "MXN", "ZAR",
    "ILS", "THB", "IDR", "MYR", "PHP",
]  # fmt: skip

_CODE = re.compile(r"^[A-Z0-9]{2,10}$")


class MoneyError(ValueError):
    pass


def normalise_code(code: str) -> str:
    code = code.strip().upper()
    if not _CODE.match(code):
        raise MoneyError(f"unknown currency: {code!r}")
    return code


def is_crypto(code: str) -> bool:
    return code.upper() in CRYPTO or (code.upper() not in FIAT and len(code) != 3)


def decimals(code: str) -> int:
    code = code.upper()
    if code in ZERO_DECIMALS:
        return 0
    if code in THREE_DECIMALS:
        return 3
    if is_crypto(code):
        return CRYPTO_DECIMALS
    return 2


def parse_decimal(value: str | float | int | Decimal) -> Decimal:
    """«-1.234,56», «1,234.56», «12.5», «(12,00)», «€ 3», 12.5 → Decimal."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))
    text = re.sub(r"[^\d,.\-+()]", "", value.strip())
    negative = (text.startswith("(") and text.endswith(")")) or text.endswith("-")
    text = text.strip("()").rstrip("-")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):  # 1.234,56
            text = text.replace(".", "").replace(",", ".")
        else:  # 1,234.56
            text = text.replace(",", "")
    elif "," in text:
        whole, _, frac = text.rpartition(",")
        # «1,234» with 3 digits after a single comma is ambiguous; in statements it is
        # almost always a decimal comma, so only treat it as thousands with several groups.
        text = text.replace(",", "") if text.count(",") > 1 else f"{whole}.{frac}"
    elif text.count(".") > 1:  # 1.234.567
        text = text.replace(".", "")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise MoneyError(f"unreadable amount: {value!r}") from exc
    return -number if negative else number


def to_minor(value: str | float | int | Decimal, currency: str) -> int:
    """An amount in `currency` → integer smallest units."""
    number = parse_decimal(value)
    scale = Decimal(10) ** decimals(currency)
    return int((number * scale).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_minor(amount: int, currency: str) -> Decimal:
    return Decimal(amount) / (Decimal(10) ** decimals(currency))


def as_text(amount: int | None, currency: str) -> str | None:
    """For the API: a plain decimal string («-12.50», «0.00031250»), exact."""
    if amount is None:
        return None
    places = decimals(currency)
    value = from_minor(amount, currency)
    return f"{value:.{places}f}"


def convert(amount: int, source: str, target: str, rate: Decimal) -> int:
    """`amount` (minor units of `source`) at `rate` target-per-source → minor units of target."""
    value = from_minor(amount, source) * rate
    return to_minor(value, target)
