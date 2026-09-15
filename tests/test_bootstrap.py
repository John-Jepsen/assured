"""Deployment-job tests for independently controlled demo seed and RAG ingestion."""

from __future__ import annotations

import pytest
from insurance_ai.db.init import create_schema
from insurance_ai.db.models import Customer, KnowledgeDocument
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scripts import bootstrap


async def _counts(engine) -> tuple[int, int]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        customers = await db.scalar(select(func.count()).select_from(Customer))
        documents = await db.scalar(select(func.count()).select_from(KnowledgeDocument))
    return int(customers or 0), int(documents or 0)


@pytest.mark.asyncio
async def test_production_ingestion_does_not_seed_demo_customers(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    await create_schema(engine)
    monkeypatch.setattr(bootstrap, "get_engine", lambda: engine)

    await bootstrap.main(
        bootstrap.DEFAULT_KNOWLEDGE,
        seed_demo=False,
        ingest=True,
    )

    assert await _counts(engine) == (0, 50)
    await engine.dispose()


@pytest.mark.asyncio
async def test_seed_and_ingest_jobs_are_independent_and_if_empty_is_idempotent(
    tmp_path, monkeypatch
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    await create_schema(engine)
    monkeypatch.setattr(bootstrap, "get_engine", lambda: engine)

    await bootstrap.main(
        bootstrap.DEFAULT_KNOWLEDGE,
        if_empty=True,
        seed_demo=True,
        ingest=False,
    )
    customers_after_seed, documents_after_seed = await _counts(engine)
    assert customers_after_seed == 5
    assert documents_after_seed == 0

    await bootstrap.main(
        bootstrap.DEFAULT_KNOWLEDGE,
        if_empty=True,
        seed_demo=False,
        ingest=True,
    )
    assert await _counts(engine) == (5, 50)

    # Re-running both guarded jobs leaves existing data intact.
    await bootstrap.main(
        bootstrap.DEFAULT_KNOWLEDGE,
        if_empty=True,
        seed_demo=True,
        ingest=True,
    )
    assert await _counts(engine) == (5, 50)
    await engine.dispose()
