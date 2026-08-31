# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**Assured** — a fully synthetic, multimodal (text + voice) AI customer-service system for an
insurance company. A multi-agent orchestrator does tool-calling and RAG over a Postgres/pgvector
DB, with deterministic identity verification, tool authorization, human escalation, an admin
dashboard, and a YAML evaluation suite. All data is synthetic; no real PII or live payments.

Monorepo: `apps/api` (Python 3.11+, FastAPI, async SQLAlchemy) and `apps/web`
(React 18 + TypeScript + Vite). Root `Makefile` drives everything.

## Commands

All from the repo root unless noted. `make setup` creates `.venv` and installs the API with dev
extras; `make setup-web` installs the frontend.

- `make seed` — create schema, seed synthetic data, ingest the knowledge base (idempotent). Run
  before `dev`/`eval` against a real DB.
- `make dev` — API on `:8000` (uvicorn, reload). `make dev-web` — Vite dev server on `:5173`.
- `make test` — full pytest suite (unit + API + voice + evals-as-tests). Runs on **SQLite with
  mock providers** by default — no DB service or model downloads needed.
- `make eval` — YAML evaluation suite (38 cases) against the configured DB, persisted to
  `evaluation_runs` and printed as a report.
- `make lint` — `ruff check`. `make up` / `make down` — full Docker Compose stack (Postgres +
  API + nginx web on `:8080`).

**Run a single test:** `.venv/bin/python -m pytest tests/test_security.py -q` (or
`... tests/test_security.py::test_name`). `pytest.ini_options` lives in `apps/api/pyproject.toml`;
`testpaths` points back at the root `tests/` dir.

**Frontend build/type-check:** `cd apps/web && npm run build` (runs `tsc` then `vite build`).

CI (`.github/workflows/ci.yml`) runs three jobs: backend lint+tests on SQLite, the full eval
suite against a real `pgvector/pgvector:pg16` service, and the frontend type-check+build.

## Configuration

Settings are a single pydantic-settings object (`insurance_ai/config.py`), env prefix
**`INSURANCE_AI_`** (e.g. `INSURANCE_AI_DATABASE_URL`, `INSURANCE_AI_LLM_PROVIDER`). See
`.env.example`. Selecting a real provider without its credentials **fails fast at startup**
(`_check_provider_credentials`). `get_settings()` and `get_providers()` are `lru_cache`d — set env
before import; tests point the global engine at a throwaway SQLite DB in `tests/conftest.py`.

Provider defaults are all deterministic mocks: `llm=mock`, `stt=mock`, `tts=mock`,
`embedding=hash`, `vector_backend=numpy`, `payment=mock`, `telephony=none`. Real adapters
(faster-whisper, Piper, OpenAI/Ollama, sentence-transformers, Stripe, Twilio) live behind optional
pip extras (`.[speech]`, `.[stripe]`, `.[telephony]`, `.[otel]`) and are **imported lazily** in
`providers/factory.py` so the core and tests never need heavy ML deps.

## Architecture — the big picture

**Request flow (`agents/orchestrator.py`):** intent detection → maybe-verify → route to specialists
→ compose grounded answer → stream. The `Orchestrator.run` produces an `OrchestratorResult`
(answer + structured `Trace`); `stream_answer` streams it. `ConversationService`
(`api/service.py`) wraps this with persistence: loads/creates a `Conversation`, syncs the in-memory
`Session`, persists user + assistant `Message`s (with the trace) and `ToolExecution` rows.

**Deterministic intent routing (`agents/intent.py`):** `detect()` is rule/keyword-based (not LLM),
returns intents + entities (policy numbers, ZIPs, dates, OTP) + the ordered list of `AgentName`s to
run. Multi-intent messages fan out to multiple specialists in one turn.

**Specialists (`agents/specialists.py`):** one per domain — policy, claims, billing, account,
scheduling, escalation, general — in `SPECIALISTS`. Each has a scoped system prompt and a
deterministic planner; they call tools and return an `AgentTurn`. `agents/composer.py` merges the
turns into a single grounded draft + sources.

**Core design principle — guardrails in code, not prompts.** The LLM never decides security. Tool
*selection* is deterministic; the model only phrases facts already returned by verified tools and
cited documents, so it cannot invent policy terms, balances, or claim statuses. When evidence is
missing, the system says so and offers to escalate.

**Security (`security/`):** two independent gates, enforced regardless of the LLM.
`verification.py` does deterministic identity verification from supplied factors (last name, ZIP,
policy #, DOB, demo OTP `123456`). `authorization.py` `authorize()` runs before every protected
tool: a **verification gate** (protected tools require a verified session) and an **ownership gate**
(any policy/claim/invoice a tool touches must belong to the session's bound `customer_id`;
cross-customer access is rejected in code and never reveals another customer's record exists).

**Tools (`tools/`):** typed, authorized, logged. A global `REGISTRY` (`tools/registry.py`);
`load_all_tools()` imports the modules so their `register()` calls run (called at app startup in
`api/app.py` lifespan). `tools_for_agent()` scopes tools per agent — agents get only their relevant
subset, never the whole registry.

**RAG (`rag/`):** `ingest.py` chunks + embeds the `knowledge/` markdown into the DB; `retriever.py`
does vector search (numpy in-process or pgvector), optional reranking (`rerank.py`, lexical or
cross-encoder). Retrieved passages become cited `Source`s; answers carry source attribution.

**Providers (`providers/`):** `base.py` defines the LLM/STT/TTS/Embedding protocols; `factory.py`
selects implementations from settings; `mock.py` is the deterministic default set. The `Providers`
bundle is resolved once at startup.

**API routes (`api/`):** `routes_chat` (streaming text), `routes_voice` (STT→agent→TTS,
voice↔voice, barge-in), `routes_admin` (traces, tools, sources, latencies, tickets, evals),
`routes_payments` (mock or Stripe test), `routes_telephony` (Twilio Media Streams), `routes_health`.
`api/app.py` is the app factory. The global exception handler never leaks a stack trace.

**Persistence:** async SQLAlchemy 2.0 models in `db/models.py`; `db/seed.py` seeds synthetic
customers/policies/claims/invoices; Alembic migrations in `apps/api/migrations/`. `enums.py` holds
the domain `StrEnum`s (products, statuses, `AgentName`, `VerificationStatus`). Adding an insurance
product = one enum member + seed/knowledge data; nothing in the agent/tool/RAG layer is hard-coded
to a product.

**Evals (`evals/*.yaml`, `insurance_ai/evals/runner.py`):** each YAML case is a multi-turn
conversation run through the real orchestrator against a freshly seeded DB; expectations assert on
the final turn's answer + trace (routing, tools used, grounding, verification, authorization,
hallucination-resistance, escalation, latency). `tests/test_evals.py` runs them under pytest too.

## Conventions

- Ruff: line length 100, rules `E,F,I,B,UP,PIE,RUF`. `Depends`/`Query`/`Path` are marked immutable
  for flake8-bugbear.
- The repo is emoji-free by deliberate policy (typographic arrows `->` `<->` are fine); keep it that
  way in README, mermaid, and UI.
- More detail lives in `docs/` (`architecture.md`, `agent-system.md`, `security.md`,
  `voice-pipeline.md`, `rag.md`, `evaluation.md`) and the top-level `README.md`.
