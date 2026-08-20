# Changelog

All notable changes to this project are documented here.

## [0.1.0] — Initial build

### Added
- **Backend** (FastAPI, async): orchestrator + 7 specialist agents (policy, claims,
  billing, account, scheduling, general, escalation) with deterministic intent
  routing and multi-intent coordination.
- **Security**: deterministic identity verification (factor-strength rules), tool
  authorization layer (verification + ownership), cross-customer access prevention,
  RAG prompt-injection sanitization. Enforced in application code, not the LLM.
- **Tools**: 25 structured, typed, authorized, logged tools across policy/claims/
  billing/account/scheduling/escalation/knowledge.
- **RAG**: markdown ingestion → paragraph-aware chunking → embeddings → retrieval
  with source attribution. BM25 lexical default (offline) + dense-embedding path;
  numpy vector backend with a pgvector production path.
- **Providers**: LLM / STT / TTS / Embedding interfaces with mock/local defaults and
  real adapters (faster-whisper, Piper, sentence-transformers, HF transformers,
  OpenAI-compatible / Ollama).
- **Voice**: WebSocket pipeline (STT → agent → streaming TTS) with barge-in and
  per-stage latency metrics; browser Web Speech API path + server STT path.
- **Payments**: mock provider (default) + Stripe test-mode adapter. No card storage.
- **Telephony**: Twilio Media Streams bridge (gated); web works fully without it.
- **Frontend**: React + TypeScript customer chat/voice UI and admin dashboard.
- **Data**: 5 synthetic customers, 9 policies across all 7 products, claims/billing
  covering every required scenario. All labelled synthetic.
- **Knowledge base**: 50 documents across all products + billing/claims/faq.
- **Evaluation**: 38-case YAML suite (routing, policy, claims, billing, security,
  rag, conversations) with a deterministic runner; wired into the test suite.
- **Infra**: multi-container Docker Compose (Postgres/pgvector + API + web) with
  health checks and readiness-gated startup; Makefile; typed configuration.
- **Docs**: architecture, security, voice-pipeline, rag, agent-system, evaluation,
  and ADRs (agent framework, models/speech, telephony).

### Added (real-models profile)
- `docker-compose.models.yml` overlay: one-command real open models via an **Ollama**
  sidecar (LLM + `nomic-embed-text` embeddings) with a persistent model cache. No API
  keys, CPU-friendly. `ollama` embedding provider added; bootstrap re-embeds on provider
  change. Verified end-to-end on the host (grounded answers + 38/38 evals with semantic
  retrieval).

### Notes
- Lexical (BM25) retrieval is the offline default; the models profile switches to dense
  semantic embeddings (nomic) with a model-appropriate relevance threshold (0.6).
