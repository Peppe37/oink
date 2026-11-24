"""Alembic environment: the engine comes from the app (`core.migrate.config`)."""

from __future__ import annotations

from alembic import context

from oink.core.db import Base

engine = context.config.attributes["engine"]
with engine.connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=True,  # SQLite: ALTER TABLE through table copies
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()
    connection.commit()
