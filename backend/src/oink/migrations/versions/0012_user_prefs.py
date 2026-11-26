"""per-person preferences: what the dashboard shows, the background and its blur

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # plain ADD COLUMN: no table copy (rebuilding `users` would cascade-delete its rows)
    op.add_column("users", sa.Column("prefs", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("users", "prefs")
