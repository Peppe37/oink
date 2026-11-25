"""Demo data: `python -m oink.demo` fills an EMPTY database with eight months of believable
movements for a user «demo» (password «demo-password»), with a USD card and some crypto,
for trying the app and for screenshots. Never touches a database with data."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from oink.core.config import get_settings
from oink.core.db import (
    Account,
    Database,
    FixedCost,
    Holding,
    Plan,
    Rate,
    Report,
    RiskProfile,
    Transaction,
)
from oink.core.security import create_user
from oink.services.ledger import NewMovement, add_movements

FIXED = [  # (day, description, counterparty, cents, category)
    (1, "RENT APARTMENT", "Bianchi Homes", -65000, "home"),
    (3, "DIRECT DEBIT ENEL ENERGIA", "Enel Energia", -6800, "bills"),
    (5, "NETFLIX.COM", "Netflix", -1399, "subscriptions"),
    (7, "SPOTIFY P2F3A", "Spotify", -1199, "subscriptions"),
    (10, "ICLOUD+ APPLE.COM/BILL", "Apple", -299, "subscriptions"),
    (15, "GYM MCFIT", "McFit", -2990, "leisure"),
    (27, "SALARY", "ACME Ltd", 210000, "salary"),
]
VARIABLE = [
    ("POS ESSELUNGA", "Esselunga", (1800, 9500), "groceries", 7),
    ("POS LIDL", "Lidl", (900, 4200), "groceries", 4),
    ("POS PIZZERIA DA GINO", "Pizzeria da Gino", (1600, 4800), "restaurants", 3),
    ("SUSHI KO", "Sushi Ko", (2500, 6000), "restaurants", 1),
    ("BAR CENTRALE", "Bar Centrale", (120, 650), "restaurants", 9),
    ("TRENITALIA", "Trenitalia", (900, 4500), "transport", 2),
    ("Q8 FUEL", "Q8", (4000, 7000), "car", 2),
    ("AMAZON EU SARL", "Amazon", (900, 8900), "shopping", 3),
    ("PHARMACY", "City Pharmacy", (500, 3500), "health", 1),
    ("CINEMA ODEON", "Cinema Odeon", (900, 2200), "leisure", 1),
]
USD = [
    ("UBER TRIP", "Uber", (900, 3200), "transport", 3),
    ("STEAM GAMES", "Steam", (500, 6000), "leisure", 1),
]


def run() -> None:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_url)
    db.create()
    rng = random.Random(37)
    with db.sessions() as s:
        if s.scalar(select(func.count()).select_from(Transaction)):
            print("The database already has data: no demo.")
            return
        user = create_user(s, "demo", "demo-password", admin=True, name="Demo")
        uid = user.id
        main = Account(user_id=uid, name="Current account", institution="Demo Bank",
                       kind="checking", iban="IT60X0542811101000000123456")  # fmt: skip
        card = Account(user_id=uid, name="Travel card", institution="Revolut", kind="card",
                       currency="USD")  # fmt: skip
        savings = Account(user_id=uid, name="Piggy bank", institution="Demo Bank", kind="savings")
        btc = Account(user_id=uid, name="Bitcoin", institution="Kraken", kind="crypto",
                      currency="BTC", source="linked")  # fmt: skip
        eth = Account(user_id=uid, name="Ethereum", institution="Ledger", kind="crypto",
                      currency="ETH", source="linked")  # fmt: skip
        s.add_all([main, card, savings, btc, eth])
        s.add_all([Rate(code="USD", eur=Decimal("0.92")), Rate(code="BTC", eur=Decimal("61500")),
                   Rate(code="ETH", eur=Decimal("2850"))])  # fmt: skip
        s.commit()
        today = date.today()
        start = (today.replace(day=1) - timedelta(days=240)).replace(day=1)
        moves_main: list[NewMovement] = []
        moves_card: list[NewMovement] = []
        day = start
        while day <= today:
            for dom, text, party, cents, cat in FIXED:
                if day.day == dom:
                    bump = 100 if party == "Netflix" and day > today - timedelta(days=60) else 0
                    moves_main.append(NewMovement(day, cents - bump, text, party, category=cat))
            for text, party, (lo, hi), cat, per_month in VARIABLE:
                if rng.random() < per_month / 30:
                    moves_main.append(
                        NewMovement(day, -rng.randint(lo, hi), text, party, category=cat)
                    )
            for text, party, (lo, hi), cat, per_month in USD:
                if rng.random() < per_month / 30:
                    moves_card.append(
                        NewMovement(day, -rng.randint(lo, hi), text, party, category=cat)
                    )
            if day.day == 28:
                moves_main.append(NewMovement(day, -30000, "TRANSFER TO PIGGY BANK", "Piggy bank",
                                              category="savings"))  # fmt: skip
            day += timedelta(days=1)
        transfers = [
            NewMovement(
                m.day,
                -m.amount,
                "TRANSFER FROM CURRENT ACCOUNT",
                "Current account",
                category="savings",
            )
            for m in moves_main
            if m.category == "savings"
        ]
        add_movements(s, savings, transfers, "import")
        add_movements(s, main, moves_main, "import")
        add_movements(s, card, moves_card, "import")
        now = datetime.now(UTC)
        main.balance, main.balance_at = 384520, now
        card.balance, card.balance_at = 21340, now
        savings.balance, savings.balance_at = 1250000, now
        btc.balance, btc.balance_at = 4_215_000, now  # 0.04215 BTC
        eth.balance, eth.balance_at = 85_000_000, now  # 0.85 ETH
        last = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        s.add(
            Report(
                user_id=uid,
                month=last,
                model="demo",
                language="en",
                content=(
                    "## How the month went\nYou spent **€1,612**, in line with your usual, and put "
                    "**€300** aside in the piggy bank.\n\n## Where the money went\n- Home and bills "
                    "are still the biggest share (45%).\n- Restaurants **+€38** against the usual: "
                    "three more pizzas.\n\n## To review\n- **Netflix went up** by €1 a month.\n\n"
                    "## Three tips\n1. Raise the monthly transfer to the piggy bank to €350: there is "
                    "room.\n2. Cap restaurants at €120.\n3. Decide whether to keep both Netflix and "
                    "Spotify (€25.98 a month)."
                ),
            )
        )
        s.add_all([
            Holding(user_id=uid, symbol="VWCE.DE", name="Vanguard FTSE All-World UCITS ETF", kind="etf",
                    currency="EUR", quantity=Decimal("42"), cost=Decimal("4650")),
            Holding(user_id=uid, symbol="AAPL", name="Apple Inc.", kind="stock", currency="USD",
                    quantity=Decimal("6"), cost=Decimal("1020")),
            Holding(user_id=uid, symbol="BTP-2033", name="BTP 4.35% 2033", kind="bond", currency="EUR",
                    quantity=Decimal("20"), cost=Decimal("2000"), manual_price=Decimal("103.2")),
            Plan(user_id=uid, symbol="VWCE.DE", name="Vanguard FTSE All-World", amount=Decimal("200"),
                 currency="EUR", frequency="monthly", start=date(2024, 1, 1)),
            FixedCost(user_id=uid, name="Rent", amount=Decimal("650"), category="home", match="rent"),
            FixedCost(user_id=uid, name="Car insurance", amount=Decimal("540"), frequency="yearly", category="car"),
            FixedCost(user_id=uid, name="Enel Energia", amount=Decimal("68"), category="bills", match="enel"),
            RiskProfile(user_id=uid, answers={"horizon": 4, "drop": 3, "experience": 3, "income": 4, "goal": 3},
                        score=3, horizon_years=15, goals="Buy a house in 6 years"),
        ])  # fmt: skip
        s.commit()
    print("Demo ready: user «demo», password «demo-password».")


if __name__ == "__main__":
    run()
