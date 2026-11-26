"""Schema upgrades at startup (Alembic). An update that changes the schema ships a new
revision in `oink/migrations/versions`; the app applies it before serving, so data survive.

A database from Oink 0.1 (single user, no Alembic) is copied aside as `oink-0.1.db.bak` and
replaced (0.1 was a preview); its owner's password survives as the admin account
(`OINK_ADMIN_USERNAME`, «admin» by default), so the same password keeps working."""

from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

log = logging.getLogger("oink.migrate")
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def config(engine: Engine) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.attributes["engine"] = engine
    return cfg


def _legacy(engine: Engine) -> bool:
    tables = set(inspect(engine).get_table_names())
    return "settings" in tables and "alembic_version" not in tables and "users" not in tables


def _legacy_password(engine: Engine) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT value FROM settings WHERE key = 'owner_password'")).first()
    return str(row[0]) if row else None


def _set_aside_legacy(engine: Engine) -> None:
    path = engine.url.database
    if path and Path(path).is_file():
        backup = Path(path).with_name("oink-0.1.db.bak")
        shutil.copy2(path, backup)
        log.warning("Oink 0.1 database copied to %s and replaced by the new schema", backup)
    with engine.begin() as conn:
        for table in reversed(inspect(engine).get_table_names()):
            conn.execute(text(f'DROP TABLE IF EXISTS "{table}"'))


def _pending(engine: Engine) -> str | None:
    """The current revision when an upgrade is due (None: up to date, or a new database)."""
    from alembic.script import ScriptDirectory

    tables = set(inspect(engine).get_table_names())
    if "alembic_version" not in tables:
        return None
    with engine.connect() as conn:
        row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
    current = str(row[0]) if row else None
    head = ScriptDirectory.from_config(config(engine)).get_current_head()
    return current if current and current != head else None


def _backup(engine: Engine, revision: str) -> None:
    """A consistent copy (pending WAL pages included) before the schema changes."""
    path = engine.url.database
    if not path or not Path(path).is_file():
        return
    target = Path(path).with_name(f"oink-before-migration-{revision}.db")
    source = sqlite3.connect(path)
    try:
        copy = sqlite3.connect(target)
        with copy:
            source.backup(copy)
        copy.close()
    finally:
        source.close()
    log.warning("database copied to %s before upgrading from %s", target, revision)


def upgrade(engine: Engine, admin_username: str = "admin") -> None:
    password = None
    if _legacy(engine):
        password = _legacy_password(engine)
        _set_aside_legacy(engine)
    if engine.dialect.name == "sqlite" and (revision := _pending(engine)):
        _backup(engine, revision)
    command.upgrade(config(engine), "head")
    if password:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (username, name, password_hash, is_admin, language, "
                    "base_currency, disabled, created) VALUES (:u, :u, :h, 1, '', 'EUR', 0, "
                    "CURRENT_TIMESTAMP)"
                ),
                {"u": admin_username.strip().lower() or "admin", "h": password},
            )
        log.warning("the Oink 0.1 password now signs in as %r", admin_username)
