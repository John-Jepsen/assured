"""Schema creation + pgvector bootstrap.

For local/dev/test we use ``create_all`` (fast, portable). Production Docker runs
the same DDL plus the pgvector migration in ``migrations/``. Enabling the pgvector
extension is attempted best-effort and skipped silently on SQLite / when absent.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from insurance_ai.db.base import Base


async def create_schema(engine: AsyncEngine) -> None:
    # Attempt the pgvector extension in an ISOLATED transaction: if it is not
    # installed the failure must not poison the create_all transaction.
    if engine.dialect.name == "postgresql":
        try:
            async with engine.begin() as conn:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception:
            pass  # pgvector optional; numpy/BM25 backend still works
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
