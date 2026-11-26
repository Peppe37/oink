"""Alembic environment: the engine comes from the app (`core.migrate.config`)."""

from __future__ import annotations

from alembic import context

from oink.core.db import Base

engine = context.config.attributes["engine"]
with engine.connect() as connection:
    # SQLite rebuilds a table to alter it (copy, drop, rename): with foreign keys on, dropping
    # the old `users` would DELETE every row that references it (accounts, movements…) by
    # cascade. Off during migrations (it must be set outside a transaction), checked after.
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=True,  # SQLite: ALTER TABLE through table copies
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()
    connection.commit()
    if connection.dialect.name == "sqlite":
        broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if broken:
            raise RuntimeError(f"migration left {len(broken)} broken references")
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
