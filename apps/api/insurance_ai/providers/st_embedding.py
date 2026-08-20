"""sentence-transformers embedding adapter. Requires the `speech` extra.

Dense semantic embeddings — a drop-in upgrade over the default lexical/BM25 path
that improves retrieval relevance for paraphrased queries. Selected via
embedding_provider=sentence-transformers; embedding_dim must match the model.
"""

from __future__ import annotations

import asyncio

from insurance_ai.config import Settings
from insurance_ai.providers.base import EmbeddingProvider


class SentenceTransformerEmbedding(EmbeddingProvider):
    name = "sentence-transformers"

    def __init__(self, settings: Settings) -> None:
        from sentence_transformers import SentenceTransformer

        model_id = settings.embedding_model
        if model_id.startswith("hash"):
            model_id = "sentence-transformers/all-MiniLM-L6-v2"
        self._model = SentenceTransformer(model_id)
        self.dim = self._model.get_sentence_embedding_dimension()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        def _run():
            return self._model.encode(texts, normalize_embeddings=True).tolist()

        return await asyncio.to_thread(_run)
