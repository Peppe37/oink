"""rules can rename a payee; a fixed cost's missing payment can be snoozed

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("rules", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("name", sa.String(length=120), nullable=False, server_default="")
        )
    with op.batch_alter_table("fixed_costs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("snoozed_until", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("fixed_costs", schema=None) as batch_op:
        batch_op.drop_column("snoozed_until")
    with op.batch_alter_table("rules", schema=None) as batch_op:
        batch_op.drop_column("name")
