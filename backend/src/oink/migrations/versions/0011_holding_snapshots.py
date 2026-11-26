"""Dated portfolio snapshots with document provenance, without rebuilding money tables.

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("holdings", sa.Column("as_of", sa.Date(), nullable=True))
    op.add_column("holdings", sa.Column("document_id", sa.Integer(), nullable=True))
    op.add_column("holdings", sa.Column("account_id", sa.Integer(), nullable=True))
    op.add_column(
        "holdings",
        sa.Column("quantity_known", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("holdings", sa.Column("snapshot_value", sa.String(48), nullable=True))


def downgrade() -> None:
    op.drop_column("holdings", "snapshot_value")
    op.drop_column("holdings", "quantity_known")
    op.drop_column("holdings", "account_id")
    op.drop_column("holdings", "document_id")
    op.drop_column("holdings", "as_of")
