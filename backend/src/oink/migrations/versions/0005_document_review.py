"""documents carry a status and the checks made on them

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("status", sa.String(length=12), nullable=False, server_default="ready")
        )
        batch_op.add_column(sa.Column("review", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.drop_column("review")
        batch_op.drop_column("status")
