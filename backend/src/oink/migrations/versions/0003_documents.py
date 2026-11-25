"""documents and their full-text index

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime", sa.String(length=100), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("stored", sa.String(length=255), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("issuer", sa.String(length=160), nullable=False),
        sa.Column("doc_date", sa.Date(), nullable=True),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("imported", sa.Integer(), nullable=True),
        sa.Column("import_note", sa.String(length=60), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "sha256", name="uq_documents_user_sha"),
    )
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_documents_user_id"), ["user_id"], unique=False)
    # Full-text index of the documents' text, in chunks (kept in sync by services.documents).
    op.execute(
        "CREATE VIRTUAL TABLE doc_chunks USING fts5(text, document_id UNINDEXED, "
        "user_id UNINDEXED, idx UNINDEXED, tokenize='unicode61 remove_diacritics 2')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS doc_chunks")
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_documents_user_id"))
    op.drop_table("documents")
