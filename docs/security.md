# Security & Privacy Design

This document describes how the platform protects customer data across identity
verification, tool authorization, retrieval, and logging. The design principle
throughout is simple: **security decisions are made in deterministic application
code, never delegated to the language model.** The LLM only phrases facts that
have already been verified and authorized.

> All customer records in this platform are **synthetic**. No real PII, no real
> company names, and no production customer data ever appear in the system,
> fixtures, logs, or documentation.

---

## Deterministic Identity Verification

Before this domain-level verification runs, production requests must carry an OIDC
access token. The API resolves its signing key from the configured JWKS, permits only
the configured asymmetric algorithm, and validates issuer, audience, subject, issued-at,
and expiry claims. The stable subject owns each conversation; presenting a valid token
with a different subject returns 403 even when the conversation UUID is known.

Customer routes also require the configured customer-ID claim. Administrative routes
instead require one of the configured workforce roles. Browser WebSockets authenticate
with the configured HttpOnly access-token cookie after exact Origin validation. The
local synthetic demo keeps authentication disabled explicitly; production startup
rejects that configuration.

Verification is a pure, rule-based function of the factors a caller supplies —
there is no model in the loop and no probabilistic scoring of identity.

A customer is considered verified only when **both** of these hold:

1. **At least two distinct factors** are provided, and
2. **At least one of them is a strong factor.**

| Strength | Factors |
|----------|-----------------------------------------------|
| Strong   | `policy_number`, `date_of_birth`, `otp_code`  |
| Weak     | `last_name`, `zip_code`                        |

Examples:

- `policy_number` + `zip_code` &rarr; verified (one strong, two total).
- `last_name` + `zip_code` &rarr; **rejected** (two weak, no strong factor).
- `otp_code` alone &rarr; **rejected** (only one factor).

The demo OTP is `123456`. Each factor is checked against the session-bound
customer record before it counts toward the threshold.

---

## Session Binding

When verification succeeds, the verified customer is **bound to the session**.
Every subsequent request in that session operates against that one customer
identity. A session can never be re-pointed at a different customer by anything
the caller (or the model) says. This is the foundation of cross-customer
prevention: the "who" is fixed at verification time and enforced server-side. Session
state is serialized in Redis with a configured TTL and a distributed per-conversation
lock, so verification and pending transactions survive API restarts without concurrent
lost updates.

---

## Tool Authorization Gate

Specialist agents can *select* tools deterministically, but no tool executes
until it passes the authorization gate in application code. The gate enforces
two independent checks:

1. **Verification** — the session must satisfy the verification rules above for
   any tool that touches customer data.
2. **Ownership** — the target record (policy, claim, billing account) must
   belong to the session-bound customer.

Both checks run before any side effect. If either fails, the tool is not
executed and the agent returns a safe response (typically a request for more
verification factors, or an escalation).

### Decision Flow

```mermaid
flowchart TD
    A[LLM / agent requests a tool call] --> B{Session verified?<br/>>=2 factors, >=1 strong}
    B -- No --> R1[Deny: request more<br/>verification factors]
    B -- Yes --> C{Target record owned by<br/>session-bound customer?}
    C -- No --> R2[Deny: cross-customer<br/>access blocked]
    C -- Yes --> D[Execute tool in<br/>application code]
    D --> E[Return verified facts<br/>to LLM for phrasing]
    E --> F[LLM composes response<br/>from verified facts only]
```

---

## Why Security Is NOT in the LLM

Tool **selection** is deterministic and auditable (a rule-based, scored,
multi-intent router picks the specialist and tools). Tool **authorization and
execution** live entirely in application code. The LLM:

- never decides whether a caller is verified,
- never decides whether a record may be accessed,
- never executes a tool directly, and
- only receives facts that were already fetched and authorized.

Because the model only phrases pre-verified facts, it **cannot hallucinate
policy data** or talk itself past an authorization check. Prompt injection or a
jailbreak may change wording, but it cannot change what the application code
allows.

---

## Prompt-Injection Defense for RAG

Retrieved knowledge-base documents are treated as **untrusted input**. Markdown
in `knowledge/` is parsed, chunked, embedded, and retrieved by cosine
similarity — but the retrieved text may contain adversarial instructions, so it
is defended at two layers:

- **Sanitization** — retrieved chunks are sanitized against prompt-injection
  patterns before they reach the model.
- **Content separation** — system instructions, user input, retrieved
  documents, and tool results are kept in **separate, clearly delimited
  channels**. Retrieved content is never merged into the system prompt and is
  never allowed to act as an instruction.

Retrieval is grounded: answers cite their sources, and when evidence is missing
the assistant says so honestly ("I don't have enough info") and escalates,
rather than inventing coverage.

---

## Rate Limiting

Abuse control sits in front of the agent, not inside it. Every non-preflight
`/api/*` HTTP request is bounded by source IP; the health and metrics probes
are deliberately exempt so they stay reachable during an incident.
Authenticated customer and admin routes add a per-principal bound keyed on the
OIDC subject, and each chat or voice WebSocket is bounded twice — once when the
connection is opened, and again on every chat turn or completed utterance, so
an accepted socket cannot become an unbounded generation loop. Exceeding a
limit returns HTTP 429 with `Retry-After`, or closes the socket with code 1013.

Counters are fixed-window and keyed by a SHA-256 digest of the subject or client
address, so no raw identifier reaches the counter store. With
`session_backend=redis` the counters live in Redis and are shared by every API
replica; any other backend falls back to a bounded per-process limiter that only
protects a single worker. Production configuration therefore requires Redis, and
rejects both a disabled limiter and a fail-open policy. If the counter store is
unreachable the limiter fails closed by default and `/ready` reports the API as
not ready.

---

## Secrets Handling

- All credentials (LLM/STT/TTS provider keys, Twilio, Stripe test keys) are read
  from environment variables via a local `.env` file.
- `.env` is **never committed**; only a documented example template is tracked.
- With no provider credentials configured, the system falls back to mock
  providers, so the platform runs fully offline for local and test use.

---

## PII Masking in Logs & Admin

- Sensitive fields (policy numbers, dates of birth, OTP codes, contact details)
  are **masked** in logs and in the admin view.
- The admin surface shows operational data — intent routing, per-stage latency,
  retrieval sources — without exposing raw sensitive identifiers.
- No payment card data is ever stored; payments use a mock provider by default
  and a Stripe test-mode adapter otherwise.
- The Prometheus `/metrics` endpoint is disabled unless explicitly enabled,
  requires its own bearer token rather than a customer or admin session, and
  carries only fixed low-cardinality labels (HTTP method, route template, status,
  provider, operation, outcome) so no identifier enters the metrics store.

---

## Synthetic Data Only

Every customer, policy, claim, and billing record is fabricated for
demonstration. There is no path by which real customer data enters the system.
This makes the platform safe to run, share, and inspect end-to-end while still
exercising the full security model.
