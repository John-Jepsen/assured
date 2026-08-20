"""initial schema

Materializes the full domain + platform schema from the SQLAlchemy metadata, and
enables the pgvector extension when running on Postgres (skipped elsewhere).

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-20
"""

from __future__ import annotations

from alembic import op

from insurance_ai.db.base import Base
from insurance_ai.db import models  # noqa: F401  (populate metadata)

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
