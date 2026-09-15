"""Shared validation for vectors persisted to or queried through pgvector."""

from __future__ import annotations

import math
from collections.abc import Sequence

# pgvector's ``vector`` type accepts at most 16,000 dimensions. HNSW indexes have
# a lower limit in current pgvector releases, so dimensions above the indexed
# deployment dimension remain eligible for exact search but need an appropriate
# storage/index strategy before production use.
PGVECTOR_MAX_DIMENSIONS = 16_000


class EmbeddingDimensionError(ValueError):
    """An embedding cannot be compared with the configured/stored vector space."""


def validate_embedding(
    vector: Sequence[float],
    *,
    expected_dim: int | None = None,
    context: str = "embedding provider",
) -> list[float]:
    """Return a finite float vector or raise an actionable validation error."""
    values = [float(value) for value in vector]
    dimension = len(values)
    if dimension == 0:
        raise EmbeddingDimensionError(f"{context} returned an empty embedding")
    if dimension > PGVECTOR_MAX_DIMENSIONS:
        raise EmbeddingDimensionError(
            f"{context} returned {dimension} dimensions; pgvector supports at most "
            f"{PGVECTOR_MAX_DIMENSIONS}"
        )
    if expected_dim is not None and dimension != expected_dim:
        raise EmbeddingDimensionError(
            f"{context} returned {dimension} dimensions, but declares {expected_dim}; "
            "reconfigure the embedding model and re-ingest the knowledge base"
        )
    if not all(math.isfinite(value) for value in values):
        raise EmbeddingDimensionError(f"{context} returned a non-finite embedding value")
    return values
