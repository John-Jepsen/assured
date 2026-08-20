"""Ingest knowledge/ markdown → parse → chunk → embed → persist."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from insurance_ai.config import get_settings
from insurance_ai.db.models import DocumentChunk, KnowledgeDocument
from insurance_ai.providers.base import EmbeddingProvider
from insurance_ai.rag.chunking import chunk_text, parse_document


async def ingest_directory(
    db: AsyncSession, knowledge_dir: Path, embedder: EmbeddingProvider
) -> dict[str, int]:
    settings = get_settings()
    docs = sorted(knowledge_dir.rglob("*.md"))
    # Fresh ingest: clear existing (idempotent re-runs).
    await db.execute(delete(DocumentChunk))
    await db.execute(delete(KnowledgeDocument))
    await db.commit()

    n_docs = 0
    n_chunks = 0
    for path in docs:
        rel = path.relative_to(knowledge_dir)
        category = rel.parts[0] if len(rel.parts) > 1 else "general"
        parsed = parse_document(
            path.read_text(encoding="utf-8"),
            fallback_category=category,
            fallback_title=path.stem.replace("-", " ").title(),
        )
        doc = KnowledgeDocument(
            source_path=str(rel),
            title=parsed.title,
            product_type=parsed.product_type,
            category=parsed.category,
            content=parsed.body,
        )
        db.add(doc)
        await db.flush()  # get doc.id

        chunks = chunk_text(
            parsed.body,
            chunk_size=settings.rag_chunk_size,
            overlap=settings.rag_chunk_overlap,
        )
        embeddings = await embedder.embed([c.content for c in chunks])
        for chunk, emb in zip(chunks, embeddings, strict=True):
            db.add(
                DocumentChunk(
                    document_id=doc.id,
                    chunk_index=chunk.index,
                    content=chunk.content,
                    citation=f"{parsed.title}",
                    product_type=parsed.product_type,
                    category=parsed.category,
                    embedding=emb,
                )
            )
            n_chunks += 1
        n_docs += 1
    await db.commit()
    return {"documents": n_docs, "chunks": n_chunks}
