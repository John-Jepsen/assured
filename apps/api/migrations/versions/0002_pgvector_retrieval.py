"""add native pgvector retrieval columns and the default-dimension HNSW index

Revision ID: 0002_pgvector_retrieval
Revises: 0001_initial
Create Date: 2026-09-15

The native column is deliberately ``vector`` rather than ``vector(512)``. This
lets an operator change embedding models and re-ingest without first rewriting
the table type. Every row records its dimension and every retrieval query filters
on it, preventing comparisons across incompatible vector spaces.

pgvector requires an explicit dimension to build an ANN index on an unconstrained
column. This migration indexes the application's default 512-dimensional model.
Deployments using another dimension remain correct (Postgres performs exact
top-k search) and should add a follow-up migration with the same expression and
partial predicate for that dimension. Migrations intentionally do not read
runtime environment variables: doing so would make schema history irreproducible.

The guards accommodate this project's historical 0001 migration, which calls the
current SQLAlchemy metadata and may therefore have already created new columns on
a brand-new database. Existing databases still receive and backfill the columns.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0002_pgvector_retrieval"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

_DEFAULT_INDEX_DIMENSION = 512
_DIMENSION_CHECK = "ck_document_chunks_embedding_vector_dimension"
_DIMENSION_INDEX = "ix_document_chunks_embedding_dim"
_HNSW_INDEX = "ix_document_chunks_embedding_vector_hnsw_512"


def _column_names(bind) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns("document_chunks")}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _column_names(bind)

    if "embedding_vector" not in columns:
        column_type = Vector() if bind.dialect.name == "postgresql" else sa.JSON()
        op.add_column(
            "document_chunks",
            sa.Column("embedding_vector", column_type, nullable=True),
        )
    if "embedding_dim" not in columns:
        op.add_column(
            "document_chunks",
            sa.Column("embedding_dim", sa.Integer(), nullable=True),
        )

    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("document_chunks")}
    if _DIMENSION_INDEX not in indexes:
        op.create_index(_DIMENSION_INDEX, "document_chunks", ["embedding_dim"])

    # New ingestion writes both representations. Backfill the mirror for existing
    # installations so switching the backend does not require an immediate ingest.
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                UPDATE document_chunks
                SET embedding_vector = embedding::text::vector,
                    embedding_dim = json_array_length(embedding)
                WHERE embedding_vector IS NULL
                  AND embedding IS NOT NULL
                  AND json_array_length(embedding) > 0
                """
            )
        )

        checks = {
            check["name"]
            for check in sa.inspect(bind).get_check_constraints("document_chunks")
        }
        if _DIMENSION_CHECK not in checks:
            op.create_check_constraint(
                _DIMENSION_CHECK,
                "document_chunks",
                "(embedding_vector IS NULL AND embedding_dim IS NULL) OR "
                "(embedding_vector IS NOT NULL AND embedding_dim = vector_dims(embedding_vector))",
            )

        indexes = {index["name"] for index in sa.inspect(bind).get_indexes("document_chunks")}
        if _HNSW_INDEX not in indexes:
            # Not CONCURRENTLY because Alembic runs this revision transactionally.
            # Schedule a maintenance window when applying it to a populated corpus.
            op.execute(
                sa.text(
                    f"""
                    CREATE INDEX {_HNSW_INDEX}
                    ON document_chunks
                    USING hnsw ((embedding_vector::vector({_DEFAULT_INDEX_DIMENSION}))
                                vector_cosine_ops)
                    WHERE embedding_dim = {_DEFAULT_INDEX_DIMENSION}
                    """
                )
            )
    else:
        # SQLite's JSON1 functions are available in supported Python builds. The
        # mirror exists only for schema parity; numpy/BM25 still reads ``embedding``.
        op.execute(
            sa.text(
                """
                UPDATE document_chunks
                SET embedding_vector = embedding,
                    embedding_dim = json_array_length(embedding)
                WHERE embedding_vector IS NULL
                  AND embedding IS NOT NULL
                  AND json_array_length(embedding) > 0
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _column_names(bind)
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("document_chunks")}
    if bind.dialect.name == "postgresql":
        if _HNSW_INDEX in indexes:
            op.drop_index(_HNSW_INDEX, table_name="document_chunks")
        checks = {
            check["name"]
            for check in sa.inspect(bind).get_check_constraints("document_chunks")
        }
        if _DIMENSION_CHECK in checks:
            op.drop_constraint(_DIMENSION_CHECK, "document_chunks", type_="check")
    if _DIMENSION_INDEX in indexes:
        op.drop_index(_DIMENSION_INDEX, table_name="document_chunks")
    if "embedding_dim" in columns:
        op.drop_column("document_chunks", "embedding_dim")
    if "embedding_vector" in columns:
        op.drop_column("document_chunks", "embedding_vector")
