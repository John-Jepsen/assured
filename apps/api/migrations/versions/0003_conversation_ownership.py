"""bind conversations to authenticated principals

Revision ID: 0003_conversation_ownership
Revises: 0002_pgvector_retrieval
Create Date: 2026-09-15

Legacy rows remain null and therefore inaccessible through authenticated customer routes.
Administrators can still inspect them for migration or retention handling.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_conversation_ownership"
down_revision = "0002_pgvector_retrieval"
branch_labels = None
depends_on = None


def _column_names(bind) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns("conversations")}


def upgrade() -> None:
    bind = op.get_bind()
    if "principal_subject" not in _column_names(bind):
        op.add_column(
            "conversations",
            sa.Column("principal_subject", sa.String(), nullable=True),
        )
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("conversations")}
    if "ix_conversations_principal_subject" not in indexes:
        op.create_index(
            "ix_conversations_principal_subject",
            "conversations",
            ["principal_subject"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("conversations")}
    if "ix_conversations_principal_subject" in indexes:
        op.drop_index("ix_conversations_principal_subject", table_name="conversations")
    if "principal_subject" in _column_names(bind):
        op.drop_column("conversations", "principal_subject")
