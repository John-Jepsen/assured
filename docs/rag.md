# RAG Design

Retrieval-Augmented Generation grounds every knowledge answer in the platform's
curated `knowledge/` corpus. The core principle: the LLM only phrases facts that
retrieval has already surfaced. When evidence is missing, the system says so and
escalates — it never invents coverage.

## Ingestion

Source content lives as markdown files under `knowledge/`. Ingestion runs in
stages:

1. **Parse front-matter.** Each document's YAML front-matter carries metadata —
   `product` (auto, homeowners, renters, life, health, commercial, umbrella),
   `category`, title, and source identifiers. This metadata is preserved on every
   chunk for filtering and attribution.
2. **Paragraph-aware chunking.** Body text is split on paragraph boundaries, then
   packed into chunks of **~700 characters** with **120 characters of overlap**.
   Paragraph awareness keeps semantically whole units together instead of cutting
   mid-sentence; the overlap preserves context that would otherwise be lost at a
   boundary.
3. **Embed.** Each chunk is turned into a vector by the configured embedding
   provider.
4. **Store.** Chunk text, metadata, and vector are persisted for retrieval.

## Embeddings

Embeddings sit behind the `EmbeddingProvider` interface and are selected by
config:

- **HashEmbedding** (default) — a deterministic lexical hashing vectorizer,
  dimension 256. No model download, reproducible across runs, ideal for local
  development and tests.
- **sentence-transformers** — dense semantic embeddings for higher retrieval
  quality on paraphrased queries.
- **OpenAI** — hosted embeddings when an API key is configured.

Because the interface is stable, switching providers changes only vector quality,
not pipeline shape.

## Vector Storage

Vectors are stored as **JSON float arrays** with a **numpy cosine-similarity
fallback** backend for local and test environments. Every chunk also has a native
**Postgres `pgvector`** mirror and an `embedding_dim` marker. Ingestion validates
that vectors are non-empty, finite, and match the provider's declared dimension
before writing both representations.

With `vector_backend=pgvector`, filtering, cosine scoring, score thresholding,
ordering, and candidate limiting all happen in PostgreSQL. The API does not load
the corpus into memory. SQLite rejects that backend with an actionable error;
use `vector_backend=numpy` there.

The native column is intentionally unconstrained (`vector`, not `vector(512)`) so
an embedding-model change cannot make the table unreadable. Queries always filter
on `embedding_dim` and cast to the query dimension. Migration
`0002_pgvector_retrieval` backfills existing JSON embeddings, enforces agreement
between the dimension marker and native vector, and creates a partial HNSW cosine
index for the default 512-dimensional deployment. PostgreSQL can still perform a
correct exact top-k search for another dimension, but production deployments must
add a matching dimension-specific expression index in a follow-up migration before
switching models. Re-ingest the entire corpus as part of that model change; never
mix embeddings from different model revisions even when their dimensions match.

## Retrieval

A query is embedded with the same provider used at ingestion, then compared by
**cosine similarity**:

- **top_k = 4** — the four highest-scoring chunks are considered.
- **min_score threshold** — chunks below the threshold are discarded, so weak
  matches never reach the composer.
- **Product / category filtering** — front-matter metadata narrows the candidate
  set (e.g., an auto-policy question only searches auto content), improving
  precision and preventing cross-product leakage.

The pgvector backend requests `max(top_k, rag_candidate_pool)` rows so optional
reranking sees the same candidate count as the in-process backend. The default
hash/mock embedding path continues to use BM25 when `vector_backend=numpy`; this
keeps deterministic offline retrieval unchanged.

Surviving chunks are passed to the specialist agent as **untrusted context**:
they are sanitized against prompt injection and kept separate from system, user,
and tool content.

## Source Attribution & Grounding Guardrail

Every answer cites the documents it drew from, using the identifiers carried in
front-matter. This makes responses auditable and lets a user verify coverage.

The grounding guardrail is the heart of the design: **if no chunk clears the
min_score threshold, the system does not answer from the model's parametric
memory.** Instead it returns an honest "I don't have enough information" and
routes to escalation. No invented coverage, no hallucinated limits or exclusions.

## Pipeline

```mermaid
flowchart TD
    subgraph Ingestion
        A[knowledge/ markdown] --> B[Parse front-matter]
        B --> C[Paragraph-aware chunking<br/>size 700, overlap 120]
        C --> D[Embed chunks]
        D --> E[(Vector store<br/>JSON arrays / pgvector)]
    end

    subgraph Query
        Q[User query] --> R[Embed query]
        R --> S[Cosine retrieval<br/>top_k 4]
        E --> S
        S --> T{Product / category filter<br/>+ min_score threshold}
        T -->|matches| U[Sanitize as untrusted context]
        U --> V[Specialist agent composes<br/>grounded answer + sources]
        T -->|no evidence| W["I don't have enough info"<br/>+ escalation]
    end
```

## Tradeoffs

**Chunk size.** Smaller chunks raise precision — retrieved text is tightly
scoped — but risk severing context a correct answer depends on. Larger chunks
carry more context but dilute the embedding, dragging in off-topic sentences that
lower similarity scores. The 700/120 setting is a middle ground for
paragraph-length policy prose; overlap buys back the context that hard boundaries
lose.

**top_k.** A higher `top_k` improves recall (more chance the right passage is
present) at the cost of precision and prompt budget — extra chunks add noise the
composer must ignore. `top_k = 4` keeps the context focused while covering
multi-part answers.

**min_score.** A high threshold favors honesty (fewer weak matches, more "I don't
know") over coverage; a low threshold does the reverse and risks weakly-grounded
answers. It is the primary dial for the precision/recall balance.

## Evaluation

Tune these parameters against a labeled question set drawn from the knowledge
corpus:

- **Retrieval quality** — recall@k (is the gold chunk in the top_k?) and
  precision@k across candidate chunk sizes and thresholds.
- **Grounding correctness** — on questions with *no* supporting document, confirm
  the system abstains and escalates rather than fabricating an answer. This
  false-answer rate is the metric the guardrail exists to protect.
- **Attribution accuracy** — cited sources actually contain the stated facts.
- **Threshold sweeps** — plot min_score against answer rate vs. false-answer rate
  to pick the operating point that maximizes coverage without sacrificing
  grounding.

Because HashEmbedding is deterministic, retrieval regressions are reproducible in
CI, and provider swaps can be A/B evaluated against the same fixtures.
