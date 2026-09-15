"""Schema creation + pgvector bootstrap.

For local/dev/test we use ``create_all`` (fast, portable); production uses the
versioned Alembic migrations. PostgreSQL always stores the native vector mirror,
even when retrieval uses numpy, so a missing extension is a schema error rather
than a condition to hide. SQLite uses the model's JSON type variant.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from insurance_ai.db.base import Base


async def create_schema(engine: AsyncEngine) -> None:
    if engine.dialect.name == "postgresql":
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
