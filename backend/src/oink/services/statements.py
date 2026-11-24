"""A parsed statement lands in an account: movements added (duplicates skipped), balance
updated for manual accounts, the month's statement request closed.

The inbox: drop files in `data/inbox/<username>/` (a synced folder, a scanner, a mail
rule…). Every few minutes Oink imports them into the account whose IBAN is printed on the
statement, or the account named by a subfolder (`inbox/<username>/<account id>/`). Imported
files move to `done/`, the others to `unmatched/` with a note saying why."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from oink.core.db import Account, StatementRequest, User
from oink.services.ai import AI
from oink.services.importer import ImportError_, Parsed, parse_statement
from oink.services.ledger import add_movements

log = logging.getLogger("oink.statements")
INBOX_TYPES = (".csv", ".tsv", ".txt", ".pdf", ".ofx", ".qfx")
MAX_FILE = 15 * 1024 * 1024


@dataclass
class Outcome:
    account_id: int
    added: int
    duplicates: int
    month: str
    by_ai: bool
    estimated: bool
    balance_updated: bool
    request_closed: bool


def pick_account(db: Session, user: User, parsed: Parsed, account_id: int | None) -> Account:
    accounts = list(
        db.scalars(select(Account).where(Account.user_id == user.id, Account.archived.is_(False)))
    )
    if account_id is not None:
        for acc in accounts:
            if acc.id == account_id:
                return acc
        raise ImportError_("account_unknown")
    by_iban = [a for a in accounts if a.iban and a.iban.replace(" ", "").upper() in parsed.ibans]
    if len(by_iban) == 1:
        return by_iban[0]
    raise ImportError_("account_not_recognised")


def apply(db: Session, account: Account, parsed: Parsed) -> Outcome:
    result = add_movements(db, account, parsed.movements(account.currency), "import")
    month = parsed.month
    balance_updated = False
    balance = parsed.balance_minor(account.currency)
    if balance is not None and account.source == "manual":
        account.balance, account.balance_at = balance, datetime.now(UTC)
        balance_updated = True
    elif result.added and account.source == "manual" and account.balance is not None:
        account.balance += sum(t.amount for t in result.added)
    if not account.iban and len(parsed.ibans) == 1:
        account.iban = parsed.ibans[0]  # next time this statement finds its account alone
    req = db.scalar(
        select(StatementRequest).where(
            StatementRequest.account_id == account.id,
            StatementRequest.month == month,
            StatementRequest.status == "open",
        )
    )
    if req is not None:
        req.status, req.done_at = "done", datetime.now(UTC)
    db.commit()
    return Outcome(
        account_id=account.id,
        added=len(result.added),
        duplicates=result.duplicates,
        month=month,
        by_ai=parsed.by_ai,
        estimated=parsed.estimated,
        balance_updated=balance_updated,
        request_closed=req is not None,
    )


def _move(file: Path, folder: Path, reason: str = "") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / file.name
    if target.exists():
        target = folder / f"{file.stem}-{datetime.now(UTC):%Y%m%d%H%M%S}{file.suffix}"
    shutil.move(str(file), target)
    if reason:
        target.with_suffix(target.suffix + ".why.txt").write_text(reason + "\n")


async def scan_inbox(db: Session, inbox: Path, ai: AI) -> int:
    """Import every waiting file; returns how many were imported."""
    imported = 0
    for user in db.scalars(select(User).where(User.disabled.is_(False))):
        home = inbox / user.username
        if not home.is_dir():
            home.mkdir(parents=True, exist_ok=True)
            continue
        waiting = [p for p in home.iterdir() if p.is_file() and p.suffix.lower() in INBOX_TYPES]
        for sub in home.iterdir():
            if sub.is_dir() and sub.name.isdigit():
                waiting += [p for p in sub.iterdir() if p.suffix.lower() in INBOX_TYPES]
        for file in waiting:
            hint = int(file.parent.name) if file.parent != home else None
            try:
                if file.stat().st_size > MAX_FILE:
                    raise ImportError_("file_too_big")
                parsed = await parse_statement(file.name, file.read_bytes(), ai)
                account = pick_account(db, user, parsed, hint)
                outcome = apply(db, account, parsed)
            except ImportError_ as exc:
                _move(file, home / "unmatched", exc.code)
                continue
            _move(file, home / "done" / outcome.month)
            imported += 1
            log.info("inbox: %s → account %s (+%s)", file.name, account.id, outcome.added)
    return imported
