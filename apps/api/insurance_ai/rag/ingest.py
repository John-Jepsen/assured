"""Ingest knowledge/ markdown → parse → chunk → embed → persist."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from insurance_ai.config import get_settings
from insurance_ai.db.models import DocumentChunk, KnowledgeDocument
from insurance_ai.providers.base import EmbeddingProvider
from insurance_ai.rag.chunking import chunk_text, parse_document
from insurance_ai.rag.vector import validate_embedding


async def ingest_directory(
    db: AsyncSession, knowledge_dir: Path, embedder: EmbeddingProvider
) -> dict[str, int]:
    """Synchronize a directory without rewriting unchanged document embeddings."""
    settings = get_settings()
    docs = sorted(knowledge_dir.rglob("*.md"))

    # Prevent overlapping production jobs from racing on the source_path unique key.
    # The transaction-scoped lock releases automatically on commit or rollback.
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(439041101)"))

    existing = {
        document.source_path: document
        for document in (await db.scalars(select(KnowledgeDocument))).all()
    }
    source_paths: set[str] = set()
    created = 0
    updated = 0
    unchanged = 0
    deleted = 0
    chunks_written = 0

    for path in docs:
        rel = path.relative_to(knowledge_dir)
        source_path = rel.as_posix()
        source_paths.add(source_path)
        category = rel.parts[0] if len(rel.parts) > 1 else "general"
        raw = path.read_text(encoding="utf-8")
        content_hash = hashlib.sha256(raw.encode()).hexdigest()
        fingerprint_material = ":".join(
            (
                content_hash,
                settings.embedding_provider,
                settings.embedding_model,
                str(embedder.dim),
                str(settings.rag_chunk_size),
                str(settings.rag_chunk_overlap),
            )
        )
        ingestion_fingerprint = hashlib.sha256(fingerprint_material.encode()).hexdigest()

        doc = existing.get(source_path)
        if doc is not None and doc.ingestion_fingerprint == ingestion_fingerprint:
            unchanged += 1
            continue

        parsed = parse_document(
            raw,
            fallback_category=category,
            fallback_title=path.stem.replace("-", " ").title(),
        )
        if doc is None:
            doc = KnowledgeDocument(source_path=source_path)
            db.add(doc)
            created += 1
        else:
            await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
            updated += 1
        doc.title = parsed.title
        doc.product_type = parsed.product_type
        doc.category = parsed.category
        doc.content = parsed.body
        doc.content_hash = content_hash
        doc.ingestion_fingerprint = ingestion_fingerprint
        await db.flush()

        chunks = chunk_text(
            parsed.body,
            chunk_size=settings.rag_chunk_size,
            overlap=settings.rag_chunk_overlap,
        )
        embeddings = await embedder.embed([c.content for c in chunks])
        for chunk, emb in zip(chunks, embeddings, strict=True):
            vector = validate_embedding(
                emb,
                expected_dim=embedder.dim,
                context=f"{embedder.name} embedding provider",
            )
            db.add(
                DocumentChunk(
                    document_id=doc.id,
                    chunk_index=chunk.index,
                    content=chunk.content,
                    citation=f"{parsed.title}",
                    product_type=parsed.product_type,
                    category=parsed.category,
                    embedding=vector,
                    embedding_vector=vector,
                    embedding_dim=len(vector),
                )
            )
            chunks_written += 1

    stale = [document for path, document in existing.items() if path not in source_paths]
    if stale:
        stale_ids = [document.id for document in stale]
        await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id.in_(stale_ids)))
        await db.execute(delete(KnowledgeDocument).where(KnowledgeDocument.id.in_(stale_ids)))
        deleted = len(stale)

    await db.commit()
    return {
        "documents": len(docs),
        "chunks": chunks_written,
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "deleted": deleted,
    }
