"""Deterministic validation for LLM rewrites of grounded answer drafts.

The model is a presentation layer: it may make a draft more concise or natural,
but it may not change protected facts. The checks here are deliberately
conservative; an uncertain rewrite falls back to the original grounded draft.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_NUMBER_RE = re.compile(r"(?<![\w])(?P<currency>\$)?(?P<number>\d[\d,]*(?:\.\d+)?)(?P<percent>%)?")
_IDENTIFIER_RE = re.compile(
    r"\b(?=[A-Z0-9-]*[A-Z])(?=[A-Z0-9-]*\d)[A-Z0-9]+(?:-[A-Z0-9]+)+\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s<>()\]]+", re.IGNORECASE)
_CITATION_RE = re.compile(
    r"\[(?:\d+|source(?:\s+\d+)?|citation(?:\s+\d+)?|[A-Z]{2,}-\d+)\]",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(
    r"\b(?:no|not|never|neither|nor|cannot|can't|won't|isn't|aren't|doesn't|don't|"
    r"excluded?|denied|uncovered)\b",
    re.IGNORECASE,
)

# Terms whose presence materially affects an insurance, identity, or transaction
# claim. Related forms share a canonical label so modest phrasing changes pass.
_CRITICAL_TERMS: dict[str, re.Pattern[str]] = {
    "coverage": re.compile(r"\b(?:cover(?:age|ed|s|ing)?|insured?)\b", re.IGNORECASE),
    "exclusion": re.compile(r"\bexclud(?:e|ed|es|ing)|\bexclusions?\b", re.IGNORECASE),
    "deductible": re.compile(r"\bdeductibles?\b", re.IGNORECASE),
    "limit": re.compile(r"\blimits?\b", re.IGNORECASE),
    "premium": re.compile(r"\bpremiums?\b", re.IGNORECASE),
    "billing": re.compile(
        r"\b(?:bill(?:ing)?|invoice|payment|pay|paid|balance|amount due)\b", re.IGNORECASE
    ),
    "refund": re.compile(r"\brefund(?:ed|s|ing)?\b", re.IGNORECASE),
    "cancellation": re.compile(r"\bcancel(?:led|ed|s|lation|ing)?\b", re.IGNORECASE),
    "lapse": re.compile(r"\blaps(?:e|ed|es|ing)|\breinstat(?:e|ed|es|ement|ing)\b", re.IGNORECASE),
    "claim": re.compile(r"\bclaims?\b", re.IGNORECASE),
    "policy": re.compile(r"\bpolic(?:y|ies)\b", re.IGNORECASE),
    "identity": re.compile(r"\b(?:identity|verify|verified|verification)\b", re.IGNORECASE),
    "appointment": re.compile(r"\b(?:appointment|scheduled?|rescheduled?)\b", re.IGNORECASE),
    "effective_date": re.compile(r"\b(?:effective|renewal|expires?|expiration)\b", re.IGNORECASE),
    "escalation": re.compile(
        r"\b(?:licensed representative|human agent|escalat(?:e|ed|es|ion|ing)|transfer)\b",
        re.IGNORECASE,
    ),
}


@dataclass(frozen=True)
class GroundingValidation:
    valid: bool
    failures: tuple[str, ...]
    checks: dict[str, bool]

    def trace_metadata(
        self,
        *,
        fallback_used: bool,
        error_type: str | None = None,
    ) -> dict[str, object]:
        metadata: dict[str, object] = {
            "grounding_validated": self.valid,
            "fallback_used": fallback_used,
            "validation_failures": list(self.failures),
            "checks": self.checks,
        }
        if error_type:
            metadata["error_type"] = error_type
        return metadata


def _numbers(text: str) -> set[str]:
    found: set[str] = set()
    for match in _NUMBER_RE.finditer(text):
        raw = match.group("number").replace(",", "")
        try:
            value = Decimal(raw).normalize()
        except InvalidOperation:  # pragma: no cover - regex admits decimal numbers only
            continue
        marker = "$" if match.group("currency") else ""
        suffix = "%" if match.group("percent") else ""
        found.add(f"{marker}{value:f}{suffix}")
    return found


def _identifiers(text: str) -> set[str]:
    return {match.group(0).upper() for match in _IDENTIFIER_RE.finditer(text)}


def _urls(text: str) -> set[str]:
    return {match.group(0).rstrip(".,;:!?") for match in _URL_RE.finditer(text)}


def _citations(text: str) -> set[str]:
    return {match.group(0).casefold() for match in _CITATION_RE.finditer(text)}


def _critical_claim_terms(text: str) -> set[str]:
    return {name for name, pattern in _CRITICAL_TERMS.items() if pattern.search(text)}


def validate_grounded_rewrite(draft: str, rendered: str) -> GroundingValidation:
    """Return whether ``rendered`` preserves the protected facts in ``draft``.

    Both missing and newly introduced protected facts fail. This catches dropped or
    invented amounts, IDs, links/citations, domain claims, and polarity reversals.
    """
    draft = draft.strip()
    rendered = rendered.strip()
    checks: dict[str, bool] = {"non_empty": bool(rendered)}

    protected_extractors = {
        "numbers": _numbers,
        "identifiers": _identifiers,
        "urls": _urls,
        "citations": _citations,
        "critical_claims": _critical_claim_terms,
    }
    for name, extractor in protected_extractors.items():
        expected = extractor(draft)
        actual = extractor(rendered)
        checks[f"{name}_preserved"] = expected <= actual
        checks[f"{name}_not_added"] = actual <= expected

    checks["polarity_preserved"] = bool(_NEGATION_RE.search(draft)) == bool(
        _NEGATION_RE.search(rendered)
    )
    failures = tuple(name for name, passed in checks.items() if not passed)
    return GroundingValidation(valid=not failures, failures=failures, checks=checks)
