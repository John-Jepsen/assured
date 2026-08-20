"""Optional reranking of retrieved candidates before taking top_k.

Modes (config ``rag_rerank``):
- ``none``          — keep the retriever's order.
- ``lexical``       — deterministic, dependency-free: blend the retrieval score with
                      query-term coverage, so a passage that actually contains the
                      query's content words is promoted over a merely-similar one.
- ``cross-encoder`` — a sentence-transformers CrossEncoder (gated by the `speech`
                      extra); highest quality, re-scores each (query, passage) pair.

Reranking only reorders real retrieved passages — it never invents content.
"""

from __future__ import annotations

import re
from functools import lru_cache

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    [
        "the",
        "a",
        "an",
        "of",
        "to",
        "in",
        "on",
        "for",
        "and",
        "or",
        "is",
        "are",
        "was",
        "be",
        "it",
        "this",
        "that",
        "with",
        "at",
        "by",
        "from",
        "as",
        "what",
        "when",
        "how",
        "why",
        "do",
        "does",
        "my",
        "your",
        "i",
        "you",
        "we",
        "insurance",
        "policy",
        "coverage",
    ]
)


def _terms(text: str) -> set[str]:
    return {t for t in _WORD.findall(text.lower()) if t not in _STOP and len(t) > 1}


def lexical_rerank(query: str, candidates: list, alpha: float = 0.7):
    """Blend base retrieval score with query-term coverage (both ~[0,1])."""
    q = _terms(query)
    if not q:
        return candidates
    rescored = []
    for c in candidates:
        coverage = len(q & _terms(c.content)) / len(q)
        combined = alpha * float(c.score) + (1 - alpha) * coverage
        rescored.append((combined, c))
    rescored.sort(key=lambda x: -x[0])
    out = []
    for combined, c in rescored:
        c.score = round(combined, 4)
        out.append(c)
    return out


@lru_cache(maxsize=1)
def _cross_encoder(model_id: str):
    from sentence_transformers import CrossEncoder

    return CrossEncoder(model_id)


def cross_encoder_rerank(query: str, candidates: list, model_id: str):
    ce = _cross_encoder(model_id)
    scores = ce.predict([(query, c.content) for c in candidates])
    ranked = sorted(zip(scores, candidates, strict=True), key=lambda x: -float(x[0]))
    out = []
    for s, c in ranked:
        c.score = round(float(s), 4)
        out.append(c)
    return out


def rerank(mode: str, query: str, candidates: list, model_id: str) -> list:
    if not candidates or mode == "none":
        return candidates
    if mode == "lexical":
        return lexical_rerank(query, candidates)
    if mode == "cross-encoder":
        return cross_encoder_rerank(query, candidates, model_id)
    return candidates
