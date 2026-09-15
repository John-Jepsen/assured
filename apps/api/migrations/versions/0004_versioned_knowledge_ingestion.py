"""track knowledge content and ingestion fingerprints

Revision ID: 0004_versioned_knowledge_ingestion
Revises: 0003_conversation_ownership
Create Date: 2026-09-15

Existing rows intentionally remain null. The next versioned ingestion run computes both
hashes and replaces only the affected chunks.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_versioned_knowledge_ingestion"
down_revision = "0003_conversation_ownership"
branch_labels = None
depends_on = None


def _column_names(bind) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns("knowledge_documents")}


def upgrade() -> None:
    columns = _column_names(op.get_bind())
    if "content_hash" not in columns:
        op.add_column(
            "knowledge_documents",
            sa.Column("content_hash", sa.String(length=64), nullable=True),
        )
    if "ingestion_fingerprint" not in columns:
        op.add_column(
            "knowledge_documents",
            sa.Column("ingestion_fingerprint", sa.String(length=64), nullable=True),
        )


def downgrade() -> None:
    columns = _column_names(op.get_bind())
    if "ingestion_fingerprint" in columns:
        op.drop_column("knowledge_documents", "ingestion_fingerprint")
    if "content_hash" in columns:
        op.drop_column("knowledge_documents", "content_hash")
