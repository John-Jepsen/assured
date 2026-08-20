"""Deterministic intent detection + entity extraction.

Routing is rule-based and scored, not left to model whim — this makes routing
testable and auditable. Multiple intents in one message are supported (spec §4):
"what's my claim status and my next payment" → {claims, billing}.

A real LLM can *augment* classification when configured, but the deterministic
signal is always computed and is authoritative for security-relevant routing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from insurance_ai.domain.enums import AgentName

# intent -> (agent, keyword patterns)
_INTENT_RULES: dict[str, tuple[AgentName, list[str]]] = {
    "policy_info": (
        AgentName.POLICY,
        [
            r"\bpolic(y|ies)\b",
            r"coverage",
            r"\bcover(s|ed|ing)?\b",
            r"\bdeductible",
            r"\blimit",
            r"\bexclusion",
            r"\brenew",
            r"\beffective date",
            r"\bvehicle\b",
            r"\brental car",
            r"\bchange .*(vehicle|car|policy)",
            r"\badd .*(driver|vehicle)",
            r"\bbeneficiar",
        ],
    ),
    "claim": (
        AgentName.CLAIMS,
        [
            r"\bclaim",
            r"\bfile a\b",
            r"\baccident",
            r"\badjuster",
            r"\bfnol",
            r"\bfirst notice",
            r"\bdamage\b",
            r"\bloss\b",
            r"\bdispute",
        ],
    ),
    "billing": (
        AgentName.BILLING,
        [
            r"\bbill",
            r"\bpay(ment|ing)?\b",
            r"\bpremium",
            r"\bbalance",
            r"\bautopay",
            r"\binvoice",
            r"\bcharge",
            r"\bpast due",
            r"\bmake a payment",
            r"\bnext payment",
            r"\bincrease",
            r"\bwent up",
            r"\bcost more",
        ],
    ),
    "account": (
        AgentName.ACCOUNT,
        [
            r"\bmy (account|profile|policies|info)",
            r"\bcontact info",
            r"\bupdate my (email|phone|address)",
            r"\bcommunication preference",
            r"\bwhat policies",
        ],
    ),
    "scheduling": (
        AgentName.SCHEDULING,
        [
            r"\bschedule",
            r"\bappointment",
            r"\bcall me",
            r"\bcall with",
            r"\bbook a",
            r"\breschedule",
            r"\bcancel .*(appointment|call)",
        ],
    ),
    "escalation": (
        AgentName.ESCALATION,
        [
            r"\bhuman\b",
            r"\bagent\b(?!.*call)",
            r"\brepresentative",
            r"\bspeak to (a|someone)",
            r"\bmanager",
            r"\bcomplaint",
            r"\bthis is ridiculous",
            r"\bunacceptable",
        ],
    ),
    "general": (
        AgentName.GENERAL,
        [
            r"\bwhat is\b",
            r"\bwhat does\b",
            r"\bhow do i\b",
            r"\bexplain\b",
            r"\bmean(s|ing)?\b",
            r"\bhelp\b",
            r"\bfaq\b",
            r"\bterminolog",
            r"\bdifference between",
        ],
    ),
}

_POLICY_RE = re.compile(r"\b([A-Z]{3,5}-\d{4,6})\b", re.IGNORECASE)
_CLAIM_RE = re.compile(r"\bCLAIM-\d{4,6}\b", re.IGNORECASE)
_INVOICE_RE = re.compile(r"\bINV-[A-Z0-9-]+\b", re.IGNORECASE)
_ZIP_RE = re.compile(r"\b(\d{5})\b")
_AMOUNT_RE = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)")
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


@dataclass
class Entities:
    policy_numbers: list[str] = field(default_factory=list)
    claim_numbers: list[str] = field(default_factory=list)
    invoice_numbers: list[str] = field(default_factory=list)
    zips: list[str] = field(default_factory=list)
    amounts: list[float] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)


@dataclass
class IntentResult:
    intents: list[str]
    agents: list[AgentName]
    scores: dict[str, int]
    entities: Entities
    ambiguous: bool

    @property
    def primary_agent(self) -> AgentName:
        return self.agents[0] if self.agents else AgentName.GENERAL


def extract_entities(text: str) -> Entities:
    ent = Entities()
    # claim/invoice numbers are also matched by the generic policy regex; classify first
    ent.claim_numbers = [m.group(0).upper() for m in _CLAIM_RE.finditer(text)]
    ent.invoice_numbers = [m.group(0).upper() for m in _INVOICE_RE.finditer(text)]
    for m in _POLICY_RE.finditer(text):
        val = m.group(1).upper()
        if val.startswith(("CLAIM-", "INV-")):
            continue
        ent.policy_numbers.append(val)
    # Remove structured IDs before scanning free numbers, so the digits embedded in
    # a policy/claim/invoice number are not misread as a ZIP or amount.
    scrubbed = _POLICY_RE.sub(" ", text)
    scrubbed = _CLAIM_RE.sub(" ", scrubbed)
    scrubbed = _INVOICE_RE.sub(" ", scrubbed)
    ent.zips = _ZIP_RE.findall(scrubbed)
    ent.amounts = [float(a) for a in _AMOUNT_RE.findall(scrubbed)]
    ent.dates = _DATE_RE.findall(text)
    return ent


_PRODUCT_HINTS: dict[str, list[str]] = {
    "auto": [r"\bauto\b", r"\bcar\b", r"\bvehicle", r"\bcollision", r"\bdriver", r"\brental car"],
    "homeowners": [r"\bhome(owners)?\b", r"\bhouse\b", r"\bdwelling", r"\broof", r"\bproperty"],
    "renters": [r"\brenter", r"\brental (insurance|policy)", r"\bapartment", r"\btenant"],
    "life": [r"\blife insurance", r"\bbeneficiar", r"\bdeath benefit", r"\bterm life"],
    "health": [
        r"\bhealth\b",
        r"\bmedical\b",
        r"\bcopay",
        r"\bcoinsurance",
        r"\bdeductible.*health",
    ],
    "commercial": [r"\bcommercial", r"\bbusiness\b", r"\bgeneral liability", r"\bworkers comp"],
    "umbrella": [r"\bumbrella", r"\bexcess liability"],
}


def infer_product(text: str) -> str | None:
    """Best-effort product classification for RAG filtering. None if unclear."""
    best, best_score = None, 0
    for product, patterns in _PRODUCT_HINTS.items():
        score = sum(1 for p in patterns if re.search(p, text, re.IGNORECASE))
        if score > best_score:
            best, best_score = product, score
    return best


def detect(text: str) -> IntentResult:
    scores: dict[str, int] = {}
    for intent, (_agent, patterns) in _INTENT_RULES.items():
        score = sum(1 for p in patterns if re.search(p, text, re.IGNORECASE))
        if score:
            scores[intent] = score

    if not scores:
        return IntentResult(
            intents=["general"],
            agents=[AgentName.GENERAL],
            scores={},
            entities=extract_entities(text),
            ambiguous=True,
        )

    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    top_score = ordered[0][1]
    # multi-intent: keep intents within 1 point of the top and above 0
    selected = [i for i, s in ordered if s >= max(1, top_score - 1)]
    # "general" is a fallback. Drop it only when the message carries account context
    # (a policy/claim/invoice id, or "my ..."), so account-specific asks lead with a
    # specialist while pure terminology questions still reach the general agent.
    entities = extract_entities(text)
    has_account_context = bool(
        entities.policy_numbers
        or entities.claim_numbers
        or entities.invoice_numbers
        or re.search(r"\bmy\b", text, re.IGNORECASE)
    )
    if len(selected) > 1 and "general" in selected and has_account_context:
        selected = [i for i in selected if i != "general"]
    agents: list[AgentName] = []
    for intent in selected:
        agent = _INTENT_RULES[intent][0]
        if agent not in agents:
            agents.append(agent)
    ambiguous = len(selected) > 1 and ordered[0][1] == (ordered[1][1] if len(ordered) > 1 else -1)
    return IntentResult(
        intents=selected,
        agents=agents,
        scores=scores,
        entities=extract_entities(text),
        ambiguous=ambiguous,
    )
