"""Async engine + session factory.

Document vectors use pgvector on PostgreSQL and a JSON type variant on SQLite.
The retrieval backend controls where scoring happens; it does not alter the
database schema, which keeps migrations deterministic across deployments.
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
