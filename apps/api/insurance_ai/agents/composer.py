"""Compose a grounded, concise answer draft from specialist turns.

The draft contains only facts produced by tools/RAG. It is then streamed to the
client (and phrased by a real LLM if one is configured). Sources are attached
structurally, never fabricated.
"""

from __future__ import annotations

from insurance_ai.agents.base import AgentTurn
from insurance_ai.tools.base import Source


def dedupe_sources(sources: list[Source]) -> list[Source]:
    seen: set[str] = set()
    out: list[Source] = []
    for s in sources:
        key = s.citation + (s.chunk_id or "")
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def compose(turns: list[AgentTurn], verified: bool) -> tuple[str, list[Source], bool, bool]:
    """Return (draft_answer, sources, needs_verification, escalated)."""
    needs_verification = any(t.needs_verification for t in turns)
    escalated = any(t.escalated for t in turns)
    sources = dedupe_sources([s for t in turns for s in t.sources])

    if needs_verification:
        draft = (
            "Before I can share account-specific details, I need to verify your identity. "
            "Please provide your policy number and ZIP code (a date of birth or one-time code "
            "also works)."
        )
        return draft, [], True, escalated

    segments: list[str] = []
    for turn in turns:
        # Prefer concrete facts; fall back to a clarification if the turn produced none.
        turn_facts = [f for f in turn.facts if f and f.strip()]
        if turn_facts:
            segments.extend(turn_facts)
        elif turn.clarification:
            segments.append(turn.clarification)

    if not segments:
        # No grounded facts and no clarification — be honest, offer escalation.
        draft = (
            "I don't have enough verified information to answer that confidently. "
            "I can connect you with a licensed representative if you'd like."
        )
        return draft, sources, False, escalated

    # Concise: cap the number of stitched facts; keep order.
    draft = " ".join(dict.fromkeys(segments))
    return draft, sources, False, escalated
