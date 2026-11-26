from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from oink.core.config import Settings
from oink.core.db import Account, Database, Rate
from oink.core.money import as_text, decimals, parse_decimal, to_minor
from oink.core.security import Vault, create_user
from oink.providers.crypto import BitcoinAddress, Kraken, kraken_symbol
from oink.providers.wise import Wise
from oink.services import updates
from oink.services.ai import AI
from oink.services.fx import Rates, refresh
from oink.services.importer import (
    find_ibans,
    parse_csv,
    parse_ofx,
    parse_pdf_lines,
    valid_iban,
)
from oink.services.ledger import NewMovement, add_movements
from oink.services.reports import Book, compare, recurring
from oink.services.statements import scan_inbox


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path / 'oink.db'}")
    database.create()
    return database


# -- money -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "value"),
    [("-1.234,56", "-1234.56"), ("1,234.56", "1234.56"), ("12,5", "12.5"), ("(12,00)", "-12.00"),
     ("€ 3", "3"), ("45,20-", "-45.20"), ("1.234.567", "1234567"), ("0.00012345", "0.00012345")],
)  # fmt: skip
def test_amounts_in_every_shape(text: str, value: str) -> None:
    assert parse_decimal(text) == Decimal(value)


def test_minor_units_follow_the_currency() -> None:
    assert decimals("EUR") == 2 and decimals("JPY") == 0 and decimals("BTC") == 8
    assert decimals("KWD") == 3 and decimals("PEPE") == 8  # unknown long codes are crypto
    assert to_minor("12.345", "EUR") == 1235
    assert to_minor("0.5", "BTC") == 50_000_000
    assert as_text(-1250, "EUR") == "-12.50" and as_text(1200, "JPY") == "1200"


# -- statements ------------------------------------------------------------------------
def test_iban_checksum() -> None:
    assert valid_iban("IT60X0542811101000000123456")
    assert not valid_iban("IT61X0542811101000000123456")
    text = "Intestatario: Rossi — IBAN IT60 X054 2811 1010 0000 0123 456 — BIC XYZ"
    assert find_ibans(text) == ["IT60X0542811101000000123456"]


def test_csv_with_separate_in_and_out_columns() -> None:
    raw = (
        "Data;Descrizione;Uscite;Entrate\n01/11/2026;Affitto;650,00;\n02/11/2026;Rimborso;;12,30\n"
    )
    rows = parse_csv(raw.encode())
    assert [r.amount for r in rows] == [Decimal("-650.00"), Decimal("12.30")]


def test_german_csv() -> None:
    raw = "Buchungstag;Verwendungszweck;Betrag\n03.11.2026;REWE Markt;-23,40\n"
    rows = parse_csv(raw.encode())
    assert rows[0].day == date(2026, 11, 3) and rows[0].amount == Decimal("-23.40")


def test_ofx() -> None:
    raw = b"""OFXHEADER:100
<OFX><BANKTRANLIST>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261103120000<TRNAMT>-45.20<FITID>A1<NAME>ESSELUNGA
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261105<TRNAMT>2100.00<FITID>A2<NAME>ACME<MEMO>Salary
</BANKTRANLIST></OFX>"""
    rows = parse_ofx(raw)
    assert [(r.amount, r.external_id) for r in rows] == [
        (Decimal("-45.20"), "A1"),
        (Decimal("2100.00"), "A2"),
    ]
    assert rows[1].description == "Salary"


def test_pdf_lines_without_ai() -> None:
    text = """Estratto conto novembre 2026
Data Valuta Descrizione Importo
03/11/2026 03/11/2026 PAGAMENTO POS ESSELUNGA MILANO 45,20
05/11/2026 05/11/2026 ACCREDITO STIPENDIO ACME SRL 2.100,00
07/11/2026 BONIFICO A FAVORE DI MARIO ROSSI 120,00-
SALDO FINALE AL 30/11/2026 3.845,20"""
    parsed = parse_pdf_lines(text)
    assert [r.amount for r in parsed.rows] == [
        Decimal("-45.20"),
        Decimal("2100.00"),
        Decimal("-120.00"),
    ]
    assert parsed.balance == Decimal("3845.20") and parsed.estimated


# -- reports and rates -----------------------------------------------------------------
def test_rates_convert_through_eur() -> None:
    rates = Rates({"USD": Decimal("0.9"), "BTC": Decimal("60000")})
    assert rates.convert(100_000_000, "BTC", "EUR") == 6_000_000
    assert rates.convert(1000, "USD", "EUR") == 900
    assert rates.convert(900, "EUR", "USD") == 1000
    assert rates.convert(1, "SOL", "EUR") is None


async def test_refresh_reads_ecb_and_coingecko(db: Database) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if "frankfurter" in str(request.url):
            return httpx.Response(200, json=[{"base": "EUR", "quote": "USD", "rate": 1.25}])
        return httpx.Response(200, json={"bitcoin": {"eur": 61000.5}})

    with db.sessions() as s:
        async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
            assert await refresh(s, {"EUR", "USD", "BTC"}, client) == 2
        assert s.get(Rate, "USD").eur == Decimal("0.8")
        assert s.get(Rate, "BTC").eur == Decimal("61000.5")


def _user_with_account(db: Database, currency: str = "EUR") -> tuple[int, Account]:
    with db.sessions() as s:
        user = create_user(s, "anna", "anna-password-1")
        acc = Account(user_id=user.id, name="Main", currency=currency)
        s.add(acc)
        s.commit()
        return user.id, acc


def test_recurring_flags_price_increases(db: Database) -> None:
    uid, acc = _user_with_account(db)
    today = date(2026, 11, 20)
    moves = [
        NewMovement(today - timedelta(days=30 * i), -1299 if i else -1399, "NETFLIX.COM", "Netflix")
        for i in range(4)
    ]
    with db.sessions() as s:
        add_movements(s, s.get(Account, acc.id), moves, "import")
        from oink.core.db import User

        found = recurring(Book.of(s, s.get(User, uid)), today=today)
    assert found[0]["name"] == "Netflix" and found[0]["increased"] and found[0]["monthly"] == 12.99


def test_compare_excludes_moving_money(db: Database) -> None:
    uid, acc = _user_with_account(db)
    with db.sessions() as s:
        add_movements(s, s.get(Account, acc.id), [
            NewMovement(date(2026, 11, 2), -5000, "groceries", category="groceries"),
            NewMovement(date(2026, 11, 3), -30000, "to savings", category="savings"),
            NewMovement(date(2026, 11, 4), 200000, "salary", category="salary"),
        ], "import")  # fmt: skip
        from oink.core.db import User

        month = compare(Book.of(s, s.get(User, uid)), "2026-11")["current"]
    assert month["spending"] == 50.0 and month["income"] == 2000.0


# -- inbox -----------------------------------------------------------------------------
async def test_inbox_imports_by_iban_and_explains_the_rest(db: Database, tmp_path: Path) -> None:
    _uid, acc = _user_with_account(db)
    with db.sessions() as s:
        s.get(Account, acc.id).iban = "IT60X0542811101000000123456"
        s.commit()
    inbox = tmp_path / "inbox"
    (inbox / "anna").mkdir(parents=True)
    (inbox / "anna" / "nov.csv").write_text(
        "IBAN IT60X0542811101000000123456\nData;Descrizione;Importo\n03/11/2026;Bar;-2,50\n"
    )
    (inbox / "anna" / "mystery.csv").write_text("Data;Descrizione;Importo\n03/11/2026;Bar;-2,50\n")
    ai = AI(Settings(data_dir=tmp_path, ai_model=""))
    with db.sessions() as s:
        assert await scan_inbox(s, inbox, ai) == 1
    assert (inbox / "anna" / "done" / "2026-11" / "nov.csv").is_file()
    assert (inbox / "anna" / "unmatched" / "mystery.csv").is_file()
    assert (
        "account_not_recognised"
        in (inbox / "anna" / "unmatched" / "mystery.csv.why.txt").read_text()
    )


# -- providers -------------------------------------------------------------------------
async def test_kraken_merges_earn_balances() -> None:
    assert kraken_symbol("XXBT") == "BTC" and kraken_symbol("ETH.F") == "ETH"

    def answer(request: httpx.Request) -> httpx.Response:
        assert request.headers["API-Key"] == "k" and request.headers["API-Sign"]
        return httpx.Response(
            200,
            json={"error": [], "result": {"XXBT": "0.1", "ETH": "1", "ETH.F": "0.5", "ZEUR": "0"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        found = await Kraken(client=client).accounts({"api_key": "k", "private_key": "c2VjcmV0"})
    assert {(a.currency, a.balance) for a in found} == {("BTC", "0.1"), ("ETH", "1.5")}


async def test_bitcoin_address_balance_and_movements() -> None:
    addr = "bc1qexample"

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/txs"):
            return httpx.Response(200, json=[{
                "txid": "t1", "status": {"confirmed": True, "block_time": 1793000000},
                "vin": [], "vout": [{"scriptpubkey_address": addr, "value": 150000}],
            }])  # fmt: skip
        return httpx.Response(
            200, json={"chain_stats": {"funded_txo_sum": 150000, "spent_txo_sum": 0}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        provider = BitcoinAddress(client=client)
        [acc] = await provider.accounts({"address": addr})
        moves = await provider.movements({"address": addr}, acc, date(2026, 1, 1))
    assert acc.balance == "0.0015" and moves[0].amount == 150000 and moves[0].external_id == "t1"


async def test_wise_without_strong_auth_still_gives_balances() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/profiles":
            return httpx.Response(200, json=[{"id": 7, "type": "PERSONAL"}])
        if path.endswith("/balances"):
            return httpx.Response(
                200, json=[{"id": 9, "currency": "GBP", "amount": {"value": 12.5}}]
            )
        return httpx.Response(403, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        wise = Wise(client=client)
        [acc] = await wise.accounts({"api_token": "t"})
        assert acc.currency == "GBP" and acc.balance == "12.5"
        assert await wise.movements({"api_token": "t"}, acc, date(2026, 1, 1)) == []


# -- secrets and updates ---------------------------------------------------------------
def test_vault_round_trip(tmp_path: Path) -> None:
    vault = Vault("", tmp_path)
    sealed = vault.seal({"api_key": "abc"})
    assert "abc" not in sealed and Vault("", tmp_path).open(sealed) == {"api_key": "abc"}
    assert oct((tmp_path / "secret.key").stat().st_mode)[-3:] == "600"


async def test_update_status_and_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(data_dir=tmp_path, update_repo="x/y")
    monkeypatch.setenv("OINK_VERSION", "0.2.0")

    async def fake_latest(_settings: Settings, client: object = None) -> dict[str, str]:
        return {"version": "0.3.1", "notes": "", "url": "", "published": ""}

    monkeypatch.setattr(updates, "latest", fake_latest)
    state = await updates.status(settings)
    assert state["update_available"] and not state["can_install"]
    settings.updater_dir.mkdir()
    import time

    (settings.updater_dir / "alive").write_text(str(int(time.time())))
    assert (await updates.status(settings))["can_install"]
    updates.request_update(settings.updater_dir, "0.3.1")
    assert json.loads((settings.updater_dir / "request.json").read_text())["version"] == "0.3.1"
    assert updates.parse("v0.10.0") > updates.parse("0.9.9")


def test_fx_currencies_include_plan_quote_currencies(db: Database) -> None:
    from oink.core.db import Holding, Plan, Quote
    from oink.services.fx import currencies_in_use

    uid, _ = _user_with_account(db)
    with db.sessions() as s:
        s.add(Holding(user_id=uid, symbol="MANUAL", currency="CHF", quantity=Decimal("1")))
        s.add(
            Plan(
                user_id=uid,
                symbol="AAPL",
                currency="EUR",
                amount=Decimal("100"),
                start=date(2026, 11, 1),
            )
        )
        s.add(Quote(symbol="AAPL", currency="USD", price=Decimal("100")))
        s.add(Quote(symbol="UNRELATED", currency="JPY", price=Decimal("100")))
        s.commit()
        assert currencies_in_use(s) == {"EUR", "CHF", "USD"}


async def test_yen_and_usd_reference_rates_preserve_minor_units_and_failures_keep_cache(
    db: Database,
) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        assert request.url.params["base"] == "EUR"
        return httpx.Response(
            200, json=[{"quote": "USD", "rate": 1.25}, {"quote": "JPY", "rate": 160}]
        )

    with db.sessions() as s:
        async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
            assert await refresh(s, {"USD", "JPY"}, client) == 2
        rates = Rates.load(s)
        assert rates.convert(100, "USD", "EUR") == 80
        assert rates.convert(160, "JPY", "EUR") == 100
        assert rates.convert(100, "EUR", "JPY") == 160
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(503))
        ) as client:
            assert await refresh(s, {"USD", "JPY"}, client) == 0
        assert Rates.load(s).convert(100, "USD", "EUR") == 80
