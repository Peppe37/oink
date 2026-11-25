"""How to connect the banks and apps people ask about most. `psd2` = through Enable
Banking (pick the bank, log in there); `key` = an API key in Oink; `statement` = export
from the app (CSV/PDF) and drop it in Oink or in the inbox folder.

Trade Republic, Deblock and Young Platform publish no official API Oink could use: when
Enable Banking lists them (Trade Republic and Deblock have IBAN accounts) the bank flow
works, otherwise their statements are read by the importer."""

from __future__ import annotations

DIRECTORY: list[dict[str, str]] = [
    {"name": "Revolut", "country": "IT", "method": "psd2", "search": "Revolut"},
    {"name": "BPER Banca", "country": "IT", "method": "psd2", "search": "BPER"},
    {"name": "Intesa Sanpaolo", "country": "IT", "method": "psd2", "search": "Intesa"},
    {"name": "UniCredit", "country": "IT", "method": "psd2", "search": "UniCredit"},
    {"name": "Fineco", "country": "IT", "method": "psd2", "search": "Fineco"},
    {"name": "N26", "country": "IT", "method": "psd2", "search": "N26"},
    {"name": "Hype", "country": "IT", "method": "psd2", "search": "Hype"},
    {"name": "Trade Republic", "country": "DE", "method": "psd2_or_statement", "search": "Trade Republic",
     "export": "App → Profile → Documents → account statement (PDF), or Settings → Export transactions (CSV)"},
    {"name": "Deblock", "country": "FR", "method": "psd2_or_statement", "search": "Deblock",
     "export": "App → Account → Statements (PDF)"},
    {"name": "Young Platform", "country": "IT", "method": "statement", "search": "",
     "export": "Web app → Wallet → History → Export (CSV)"},
    {"name": "Wise", "country": "", "method": "key", "search": "wise"},
    {"name": "Kraken", "country": "", "method": "key", "search": "kraken"},
    {"name": "Binance", "country": "", "method": "key", "search": "binance"},
]  # fmt: skip
