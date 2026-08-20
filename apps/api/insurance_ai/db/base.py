"""Async engine + session factory.

The vector column type adapts to the configured backend: real ``pgvector`` in
Postgres, or a JSON-encoded float array on SQLite / non-pgvector Postgres (the
``numpy`` fallback backend). This keeps the schema identical across environments.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from insurance_ai.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()

_engine = create_async_engine(
    _settings.database_url,
    echo=False,
    pool_pre_ping=True,
    future=True,
)

SessionFactory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


def get_engine():
    return _engine


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields a session and guarantees rollback on error."""
    async with SessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
