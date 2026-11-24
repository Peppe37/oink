"""Demo data: `python -m oink.demo` fills an EMPTY database with eight months of believable
movements (for trying the app and for screenshots). Never touches a database with data."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from oink.core.config import get_settings
from oink.core.db import Account, Database, Report, Transaction
from oink.services.ledger import NewMovement, add_movements

FIXED = [  # (day, description, counterparty, cents, category)
    (1, "AFFITTO APPARTAMENTO", "Immobiliare Bianchi", -65000, "casa"),
    (3, "ADDEBITO SDD ENEL ENERGIA", "Enel Energia", -6800, "bollette"),
    (5, "NETFLIX.COM", "Netflix", -1399, "abbonamenti"),
    (7, "SPOTIFY P2F3A", "Spotify", -1199, "abbonamenti"),
    (10, "ICLOUD+ APPLE.COM/BILL", "Apple", -299, "abbonamenti"),
    (15, "PALESTRA MCFIT", "McFit", -2990, "svago"),
    (27, "STIPENDIO", "ACME Srl", 210000, "stipendio"),
]
VARIABLE = [
    ("PAGAMENTO POS ESSELUNGA", "Esselunga", (1800, 9500), "spesa", 7),
    ("PAGAMENTO POS LIDL", "Lidl", (900, 4200), "spesa", 4),
    ("PAGAMENTO POS PIZZERIA DA GINO", "Pizzeria da Gino", (1600, 4800), "ristoranti", 3),
    ("SUSHI KO", "Sushi Ko", (2500, 6000), "ristoranti", 1),
    ("BAR CENTRALE", "Bar Centrale", (120, 650), "ristoranti", 9),
    ("TRENITALIA", "Trenitalia", (900, 4500), "trasporti", 2),
    ("Q8 CARBURANTE", "Q8", (4000, 7000), "auto", 2),
    ("AMAZON EU SARL", "Amazon", (900, 8900), "shopping", 3),
    ("FARMACIA COMUNALE", "Farmacia Comunale", (500, 3500), "salute", 1),
    ("CINEMA ODEON", "Cinema Odeon", (900, 2200), "svago", 1),
]


def run() -> None:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_url)
    db.create()
    rng = random.Random(37)
    with db.sessions() as s:
        if s.scalar(select(func.count()).select_from(Transaction)):
            print("Il database ha già dei dati: niente demo.")
            return
        main = Account(
            name="Conto Corrente", institution="Banca Demo", kind="checking", source="manual"
        )
        card = Account(name="Carta Hype", institution="Hype", kind="card", source="manual")
        savings = Account(
            name="Salvadanaio", institution="Banca Demo", kind="savings", source="manual"
        )
        s.add_all([main, card, savings])
        s.commit()
        today = date.today()
        start = (today.replace(day=1) - timedelta(days=240)).replace(day=1)
        moves_main: list[NewMovement] = []
        moves_card: list[NewMovement] = []
        day = start
        while day <= today:
            for dom, text, party, cents, cat in FIXED:
                if day.day == dom:
                    bump = (
                        100
                        if cat == "abbonamenti"
                        and party == "Netflix"
                        and day > today - timedelta(days=60)
                        else 0
                    )
                    moves_main.append(
                        NewMovement(
                            day=day,
                            amount_cents=cents - bump,
                            description=text,
                            counterparty=party,
                            category=cat,
                        )
                    )
            for text, party, (lo, hi), cat, per_month in VARIABLE:
                if rng.random() < per_month / 30:
                    target = moves_card if cat in ("ristoranti", "shopping") else moves_main
                    target.append(
                        NewMovement(
                            day=day,
                            amount_cents=-rng.randint(lo, hi),
                            description=text,
                            counterparty=party,
                            category=cat,
                        )
                    )
            if day.day == 28:
                moves_main.append(
                    NewMovement(
                        day=day,
                        amount_cents=-30000,
                        description="GIROCONTO SALVADANAIO",
                        counterparty="Salvadanaio",
                        category="risparmio",
                    )
                )
            day += timedelta(days=1)
        transfers = [
            NewMovement(
                day=m.day,
                amount_cents=-m.amount_cents,
                description="GIROCONTO DA CONTO CORRENTE",
                counterparty="Conto Corrente",
                category="risparmio",
            )
            for m in moves_main
            if m.category == "risparmio"
        ]
        add_movements(s, savings.id, transfers, "import")
        add_movements(s, main.id, moves_main, "import")
        add_movements(s, card.id, moves_card, "import")
        now = datetime.now(UTC)
        main.balance_cents, main.balance_at = 384520, now
        card.balance_cents, card.balance_at = 21340, now
        savings.balance_cents, savings.balance_at = 1250000, now
        last = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        s.add(
            Report(
                month=last,
                model="demo",
                content=(
                    "## Com'è andato il mese\nHai speso **1.612 €**, in linea con la tua media, e messo da parte "
                    "**300 €** nel salvadanaio.\n\n## Dove sono andati i soldi\n- Casa e bollette restano la voce "
                    "principale (45%).\n- Ristoranti **+38 €** rispetto al solito: tre pizze in più.\n\n"
                    "## Da rivedere\n- **Netflix è aumentato** di 1 € al mese.\n\n## Tre consigli\n1. Porta il "
                    "giroconto al salvadanaio a 350 €: il margine c'è.\n2. Metti un tetto di 120 € ai ristoranti.\n"
                    "3. Valuta se tenere Netflix e Spotify insieme (25,98 €/mese)."
                ),
            )
        )
        s.commit()
    print("Demo pronta.")


if __name__ == "__main__":
    run()
