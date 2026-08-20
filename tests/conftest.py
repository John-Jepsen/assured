"""Test fixtures: isolated schema + seed + knowledge ingest on a temp DB.

Uses SQLite by default (fast, no external service). Set INSURANCE_AI_TEST_DATABASE_URL
to run the same suite against Postgres. Providers default to mock/hash/numpy, so the
whole agent + RAG stack runs deterministically with zero model downloads.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

API_DIR = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(API_DIR))
KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "knowledge"

# Point the GLOBAL engine (used by the FastAPI app) at a throwaway SQLite DB, before
# any insurance_ai import triggers settings caching. Per-test fixtures still use their
# own isolated temp DBs.
_GLOBAL_DB = Path(__file__).resolve().parent / ".api_test.db"
os.environ.setdefault("INSURANCE_AI_DATABASE_URL", f"sqlite+aiosqlite:///{_GLOBAL_DB}")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from insurance_ai.db.init import create_schema  # noqa: E402
from insurance_ai.db.seed import seed  # noqa: E402
from insurance_ai.providers.factory import get_providers  # noqa: E402
from insurance_ai.rag.ingest import ingest_directory  # noqa: E402
from insurance_ai.security.session import Session  # noqa: E402


@pytest_asyncio.fixture
async def sessionmaker(tmp_path):
    url = os.environ.get(
        "INSURANCE_AI_TEST_DATABASE_URL",
        f"sqlite+aiosqlite:///{tmp_path/'test.db'}",
    )
    engine = create_async_engine(url, future=True)
    await create_schema(engine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await seed(s)
        await ingest_directory(s, KNOWLEDGE_DIR, get_providers().embedding)
    yield maker
    await engine.dispose()


@pytest_asyncio.fixture
async def db(sessionmaker):
    async with sessionmaker() as session:
        yield session


@pytest_asyncio.fixture
async def api_client():
    """TestClient over the real ASGI app, backed by the global test DB (seeded)."""
    from fastapi.testclient import TestClient

    from insurance_ai.api.app import app
    from insurance_ai.db.base import get_engine
    from insurance_ai.db.init import create_schema, drop_schema

    engine = get_engine()
    await drop_schema(engine)
    await create_schema(engine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await seed(s)
        await ingest_directory(s, KNOWLEDGE_DIR, get_providers().embedding)
    with TestClient(app) as client:
        yield client


@pytest_asyncio.fixture
async def global_db_seeded():
    """Seed the process-global engine (used by SessionFactory) so handlers that open
    their own sessions (voice/telephony) run against real data."""
    from insurance_ai.db.base import get_engine
    from insurance_ai.db.init import create_schema, drop_schema

    engine = get_engine()
    await drop_schema(engine)
    await create_schema(engine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await seed(s)
        await ingest_directory(s, KNOWLEDGE_DIR, get_providers().embedding)
    yield


@pytest.fixture
def new_session():
    counter = {"n": 0}

    def _make(conversation_id: str = "conv-test") -> Session:
        counter["n"] += 1
        return Session(conversation_id=f"{conversation_id}-{counter['n']}")

    return _make
