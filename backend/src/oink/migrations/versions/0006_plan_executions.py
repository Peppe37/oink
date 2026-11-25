"""plans know their account and day; their executions are matched in the movements

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("plans", schema=None) as batch_op:
        batch_op.add_column(sa.Column("account_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("day", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("end", sa.Date(), nullable=True))
        batch_op.create_foreign_key(
            "fk_plans_account_id", "accounts", ["account_id"], ["id"], ondelete="SET NULL"
        )
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("plan_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_column("plan_id")
    with op.batch_alter_table("plans", schema=None) as batch_op:
        batch_op.drop_constraint("fk_plans_account_id", type_="foreignkey")
        batch_op.drop_column("end")
        batch_op.drop_column("day")
        batch_op.drop_column("account_id")
