"""Incremental, versioned knowledge-ingestion behavior."""

from __future__ import annotations

from pathlib import Path

import pytest
from insurance_ai.config import Settings
from insurance_ai.db.init import create_schema
from insurance_ai.db.models import DocumentChunk, KnowledgeDocument
from insurance_ai.providers.base import EmbeddingProvider
from insurance_ai.rag import ingest as ingest_module
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class CountingEmbedding(EmbeddingProvider):
    name = "counting"
    dim = 3

    def __init__(self) -> None:
        self.texts = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts += len(texts)
        return [[1.0, 0.0, 0.0] for _ in texts]


def _document(title: str, body: str) -> str:
    return f"---\ntitle: {title}\nproduct: auto\ncategory: faq\n---\n\n{body}\n"


@pytest.mark.asyncio
async def test_ingestion_only_reembeds_changed_documents_and_removes_stale_sources(
    tmp_path: Path, monkeypatch
):
    knowledge = tmp_path / "knowledge"
    category = knowledge / "auto"
    category.mkdir(parents=True)
    first_path = category / "first.md"
    second_path = category / "second.md"
    first_path.write_text(_document("First", "Original coverage text."), encoding="utf-8")
    second_path.write_text(_document("Second", "Stable billing text."), encoding="utf-8")

    settings = Settings(
        embedding_provider="hash",
        embedding_model="test-embedding-v1",
        embedding_dim=3,
        rag_chunk_size=1_000,
        rag_chunk_overlap=0,
    )
    monkeypatch.setattr(ingest_module, "get_settings", lambda: settings)
    embedder = CountingEmbedding()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ingestion.db'}")
    await create_schema(engine)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as db:
        initial = await ingest_module.ingest_directory(db, knowledge, embedder)
        initial_rows = {
            row.source_path: (row.id, row.content_hash, row.ingestion_fingerprint)
            for row in (await db.scalars(select(KnowledgeDocument))).all()
        }
    assert initial == {
        "documents": 2,
        "chunks": 2,
        "created": 2,
        "updated": 0,
        "unchanged": 0,
        "deleted": 0,
    }
    assert embedder.texts == 2
    assert all(content_hash and fingerprint for _, content_hash, fingerprint in initial_rows.values())

    async with maker() as db:
        unchanged = await ingest_module.ingest_directory(db, knowledge, embedder)
        unchanged_rows = {
            row.source_path: (row.id, row.content_hash, row.ingestion_fingerprint)
            for row in (await db.scalars(select(KnowledgeDocument))).all()
        }
    assert unchanged["unchanged"] == 2
    assert unchanged["chunks"] == 0
    assert embedder.texts == 2
    assert unchanged_rows == initial_rows

    first_path.write_text(_document("First", "Revised coverage text."), encoding="utf-8")
    second_path.unlink()
    async with maker() as db:
        changed = await ingest_module.ingest_directory(db, knowledge, embedder)
        remaining = (await db.scalars(select(KnowledgeDocument))).one()
        chunk_count = await db.scalar(select(func.count()).select_from(DocumentChunk))

    assert changed == {
        "documents": 1,
        "chunks": 1,
        "created": 0,
        "updated": 1,
        "unchanged": 0,
        "deleted": 1,
    }
    assert remaining.source_path == "auto/first.md"
    assert remaining.id == initial_rows["auto/first.md"][0]
    assert remaining.content_hash != initial_rows["auto/first.md"][1]
    assert chunk_count == 1
    assert embedder.texts == 3
    await engine.dispose()
