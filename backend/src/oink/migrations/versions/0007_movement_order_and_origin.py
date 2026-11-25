"""movements keep their order within the day and the document they came from

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("document_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("seq", sa.Integer(), nullable=True))
        batch_op.create_index("ix_transactions_document_id", ["document_id"], unique=False)
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE transactions SET seq = id"))
    # Bank links listed the newest first, so they were stored newest first: within each day
    # reverse them (ids stay in the same range, so their order against other rows holds).
    bind.execute(
        sa.text(
            "UPDATE transactions SET seq = (SELECT MIN(t2.id) + MAX(t2.id) FROM transactions t2 "
            "WHERE t2.account_id = transactions.account_id AND t2.day = transactions.day "
            "AND t2.source IN ('linked', 'pending')) - id WHERE source IN ('linked', 'pending')"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_index("ix_transactions_document_id")
        batch_op.drop_column("seq")
        batch_op.drop_column("document_id")
