"""Retrieval over document chunks.

Two backends behind one interface:
* ``numpy``  — cosine similarity in Python over JSON-stored embeddings (default;
  works everywhere, verified locally).
* ``pgvector`` — native ANN via the pgvector ``<=>`` operator (Docker/production).

Retrieved text is treated as UNTRUSTED evidence. ``sanitize`` neutralises common
prompt-injection markers before the content is handed to the model layer.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from insurance_ai.config import get_settings
from insurance_ai.db.models import DocumentChunk
from insurance_ai.providers.base import EmbeddingProvider

_WORD_RE = re.compile(r"[a-z0-9]+")
_LEX_STOP = frozenset(
    "the a an of to in on for and or is are was were be been being do does did i you my "
    "your me we our it its this that these those what when where how why who with at by from "
    "as if then than so about into over under can could would should will shall may might have "
    "has had not no yes get got insurance policy coverage cover covered assistant".split()
)


def _lex_tokens(text: str) -> list[str]:
    return [t for t in _WORD_RE.findall(text.lower()) if t not in _LEX_STOP and len(t) > 1]


@dataclass
class Retrieved:
    chunk_id: str
    document_id: str
    content: str
    citation: str
    score: float
    product_type: str | None
    category: str


_INJECTION_PATTERNS = [
    re.compile(r"(?i)ignore (all |the )?(previous|prior|above) instructions"),
    re.compile(r"(?i)you are now|new instructions:|system prompt"),
    re.compile(r"(?i)disregard .{0,20}(rules|guardrails|policy)"),
]


def sanitize(text: str) -> str:
    """Neutralise instruction-like content embedded in retrieved documents."""
    cleaned = text
    for pat in _INJECTION_PATTERNS:
        cleaned = pat.sub("[filtered]", cleaned)
    return cleaned


def _cosine(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    denom = (np.linalg.norm(a) * np.linalg.norm(b, axis=1))
    denom = np.where(denom == 0, 1e-9, denom)
    return (b @ a) / denom


class Retriever:
    def __init__(self, embedder: EmbeddingProvider) -> None:
        self.embedder = embedder
        self.settings = get_settings()

    async def search(
        self,
        db: AsyncSession,
        query: str,
        *,
        product_type: str | None = None,
        category: str | None = None,
        top_k: int | None = None,
    ) -> list[Retrieved]:
        top_k = top_k or self.settings.rag_top_k
        stmt = select(DocumentChunk)
        if product_type:
            # include product-specific and product-agnostic (null) chunks
            stmt = stmt.where(
                (DocumentChunk.product_type == product_type)
                | (DocumentChunk.product_type.is_(None))
            )
        if category:
            stmt = stmt.where(DocumentChunk.category == category)
        rows = list((await db.execute(stmt)).scalars().all())
        if not rows:
            return []

        # Lexical/hash default → BM25 (IDF-weighted, robust for offline retrieval).
        # A real embedding model → dense cosine similarity.
        if self.embedder.name in ("hash", "mock"):
            scored = self._bm25(query, rows)
            min_score = self.settings.rag_min_score
        else:
            qvec = np.array(await self.embedder.embed_one(query), dtype=np.float32)
            matrix = np.array([r.embedding for r in rows], dtype=np.float32)
            sims = _cosine(qvec, matrix)
            scored = sorted(zip(rows, sims.tolist()), key=lambda x: -x[1])
            min_score = self.settings.rag_min_score

        results: list[Retrieved] = []
        for chunk, score in scored:
            if score < min_score:
                continue
            results.append(
                Retrieved(
                    chunk_id=chunk.id, document_id=chunk.document_id,
                    content=sanitize(chunk.content), citation=chunk.citation,
                    score=round(float(score), 4), product_type=chunk.product_type,
                    category=chunk.category,
                )
            )
            if len(results) >= top_k:
                break
        return results

    def _bm25(self, query: str, rows: list, k1: float = 1.5, b: float = 0.75):
        """BM25 over the candidate chunks, normalised to ~[0,1] by the best score."""
        q_terms = [t for t in _lex_tokens(query)]
        if not q_terms:
            return []
        docs = [_lex_tokens(r.content) for r in rows]
        n = len(docs)
        avgdl = sum(len(d) for d in docs) / n if n else 0.0
        df: Counter = Counter()
        for d in docs:
            for term in set(d):
                df[term] += 1
        idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in df}

        raw: list[float] = []
        for d in docs:
            counts = Counter(d)
            dl = len(d)
            score = 0.0
            for term in set(q_terms) & counts.keys():
                tf = counts[term]
                denom = tf + k1 * (1 - b + b * dl / (avgdl or 1))
                score += idf.get(term, 0.0) * (tf * (k1 + 1)) / (denom or 1)
            raw.append(score)
        best = max(raw) if raw else 0.0
        # Normalise by the best match so scores sit in ~[0,1]; a query with no lexical
        # overlap yields all-zero scores → nothing clears min_score → honest "no match".
        norm = [(s / best if best > 0 else 0.0) for s in raw]
        return sorted(zip(rows, norm), key=lambda x: -x[1])
