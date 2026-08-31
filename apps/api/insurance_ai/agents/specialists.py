"""Concrete specialist agents with scoped tools and deterministic planners."""

from __future__ import annotations

import re

from insurance_ai.agents.base import AgentTurn, PlannedCall, Specialist
from insurance_ai.agents.intent import IntentResult, infer_product, reply_polarity
from insurance_ai.domain.enums import AgentName
from insurance_ai.tools.base import ToolContext

_GUARDRAIL = (
    "You are a licensed-style insurance support assistant for a SYNTHETIC demo. "
    "Only state facts present in the provided tool results and sources. Never invent "
    "policy terms, coverage, balances, or claim statuses. Never guarantee coverage or "
    "claim approval. If information is missing, say so and offer to escalate."
)


async def _resolve_single_policy(ctx: ToolContext, product: str | None) -> str | None:
    """For a verified session with no explicit policy, resolve the one relevant policy.

    Filters by inferred product when possible; returns a number only when exactly one
    policy matches (otherwise the agent asks which policy — no silent guessing).
    """
    if not ctx.session.is_verified or not ctx.session.customer_id:
        return None
    from sqlalchemy import select

    from insurance_ai.db.models import Policy

    rows = await ctx.db.execute(select(Policy).where(Policy.customer_id == ctx.session.customer_id))
    policies = list(rows.scalars().all())
    if product:
        matching = [p for p in policies if p.product_type == product]
        if matching:
            policies = matching
    return policies[0].policy_number if len(policies) == 1 else None


async def _resolve_customer_policies(ctx: ToolContext, product: str | None) -> list[str]:
    """All policy numbers owned by the verified customer, filtered by product when it
    narrows the set. Lets a general billing question ("what do I owe?") resolve to every
    relevant policy instead of demanding a policy number for multi-policy households.
    """
    if not ctx.session.is_verified or not ctx.session.customer_id:
        return []
    from sqlalchemy import select

    from insurance_ai.db.models import Policy

    rows = await ctx.db.execute(select(Policy).where(Policy.customer_id == ctx.session.customer_id))
    policies = list(rows.scalars().all())
    if product:
        matching = [p for p in policies if p.product_type == product]
        if matching:
            policies = matching
    return [p.policy_number for p in policies]


async def _resolve_customer_claims(ctx: ToolContext) -> list[str]:
    """Claim numbers owned by the verified customer, so a verified caller can ask about
    "my claim" without reciting a claim number.
    """
    if not ctx.session.is_verified or not ctx.session.customer_id:
        return []
    from sqlalchemy import select

    from insurance_ai.db.models import Claim

    rows = await ctx.db.execute(select(Claim).where(Claim.customer_id == ctx.session.customer_id))
    return [c.claim_number for c in rows.scalars().all()]


def _snippet(text: str, max_chars: int = 260) -> str:
    """Trim a retrieved passage to its first sentence(s) for a concise answer."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    last = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: last + 1] if last > 60 else cut).strip() + " …"


# A general "how does X work / what is X / what happens if X" question, as opposed to a
# lookup of the caller's own record. These are answered from documentation, not account
# data, so a specialist that owns the topic still grounds them via the knowledge base.
_CONCEPTUAL_RE = re.compile(
    r"\bhow (do|does|can|to|long|much)\b|\bwhat (is|are|does|happens|if)\b|"
    r"\bexplain\b|\bdifference\b|\bwork(s)?\b|\bmean(s)?\b|\bwhy\b|\bin general\b|\btypically\b"
)


def _is_conceptual(message: str) -> bool:
    return bool(_CONCEPTUAL_RE.search(message.lower()))


# Bare greetings / small talk / acknowledgements — answered with a welcome, not a
# documentation search that would "miss" and look broken.
_GREETING_RE = re.compile(
    r"^\s*(?:(?:hi|hey+|hello|howdy|yo|sup|greetings|good (?:morning|afternoon|evening)|"
    r"thanks|thank you|thx|ty|ok|okay|k|cool|great|nice|got it|nvm|there|again|folks|team)"
    r"\b[\s.!,]*)+$",
    re.IGNORECASE,
)

# Maps a coverage the caller might ask about ("does my policy cover a rental car?") to the
# coverage-type label ``lookup_coverages`` reports, so we can answer yes/no directly instead
# of only listing the schedule. First match wins; order specific concepts before general.
_COVERAGE_QUERY = [
    (re.compile(r"\brental\b"), "Rental Reimbursement", "rental car coverage"),
    (
        re.compile(r"windshield|glass"),
        "Comprehensive",
        "glass/windshield damage (covered under comprehensive)",
    ),
    (re.compile(r"tow|roadside"), "Roadside", "roadside or towing coverage"),
    (re.compile(r"\bcollision\b|crash|fender"), "Collision", "collision coverage"),
    (
        re.compile(r"comprehensive|theft|stolen|hail|weather|vandal"),
        "Comprehensive",
        "comprehensive coverage",
    ),
    (re.compile(r"\bliability\b"), "Liability", "liability coverage"),
    (re.compile(r"dwelling|structure"), "Dwelling", "dwelling coverage"),
    (
        re.compile(r"personal property|belongings|contents"),
        "Personal Property",
        "personal property coverage",
    ),
]


class PolicyAgent(Specialist):
    name = AgentName.POLICY
    system_prompt = (
        _GUARDRAIL
        + " You handle coverage, deductibles, limits, exclusions, renewals, and policy changes."
    )

    async def augment(self, ctx: ToolContext, message: str, intent: IntentResult) -> None:
        if intent.entities.policy_numbers:
            return
        m = message.lower()
        product = infer_product(message)
        # A change request targets one policy (the single relevant one); coverage/renewal
        # questions resolve *every* relevant policy so a multi-policy household sees each
        # instead of a "which policy?" prompt.
        coverage_terms = r"cover|deductible|limit|exclusion|renew|my policy|driver|dwelling|asset"
        if re.search(r"\b(add|remove|change|update)\b", m):
            resolved = await _resolve_single_policy(ctx, product)
            if resolved:
                intent.entities.policy_numbers.append(resolved)
        elif re.search(coverage_terms, m):
            intent.entities.policy_numbers.extend(await _resolve_customer_policies(ctx, product))

    def plan(self, message: str, intent: IntentResult, ctx: ToolContext) -> list[PlannedCall]:
        calls: list[PlannedCall] = []
        m = message.lower()
        coverage_q = bool(re.search(r"cover|deductible|limit|exclusion", m))
        asset_q = bool(re.search(r"vehicle|asset|driver|dwelling|insured", m))
        change_q = bool(re.search(r"change|add|remove|update .*(vehicle|driver)", m))
        pols = intent.entities.policy_numbers
        # Specific policy facts lead the answer (one lookup per relevant policy).
        for pol in pols:
            if coverage_q:
                calls.append(PlannedCall("lookup_coverages", {"policy_number": pol}))
            elif asset_q:
                calls.append(PlannedCall("lookup_insured_assets", {"policy_number": pol}))
            else:
                calls.append(PlannedCall("lookup_policy", {"policy_number": pol}))
            if change_q:
                calls.append(
                    PlannedCall(
                        "request_policy_change",
                        {
                            "policy_number": pol,
                            "change_type": "requested_change",
                            "details": message,
                        },
                    )
                )
        # Supplementary documentation — skip it when the customer's own coverage record
        # already answers the question (so a doc "miss" never tails a concrete answer), but
        # always ground a general "how does X work / what is X" question from the docs.
        answered_from_policy = bool(pols) and coverage_q
        if not answered_from_policy and (
            re.search(r"cover|rental|deductible|limit|exclusion|glass|windshield|tow", m)
            or _is_conceptual(message)
        ):
            calls.append(
                PlannedCall(
                    "search_knowledge", {"query": message, "product_type": infer_product(message)}
                )
            )
        return calls

    def fact_from_data(self, turn, tool_name, data):
        if tool_name == "lookup_coverages":
            for c in data.get("coverages", []):
                parts = [c["coverage_type"].replace("_", " ").title()]
                if c.get("limit_amount") is not None:
                    parts.append(
                        f"limit ${c['limit_amount']:,.0f}"
                        + (f" {c['per_unit']}" if c.get("per_unit") else "")
                    )
                if c.get("deductible") is not None:
                    parts.append(f"deductible ${c['deductible']:,.0f}")
                turn.facts.append(" — ".join(parts) + ".")
        if tool_name == "search_knowledge" and data.get("passages"):
            turn.facts.append(_snippet(data["passages"][0]["content"]))

    def post_process(
        self, turn: AgentTurn, message: str, intent: IntentResult, ctx: ToolContext
    ) -> None:
        m = message.lower()
        # An unverified caller asking about *their* policy: prompt to verify rather than
        # let a generic doc-search miss stand in for the account-specific answer.
        if (
            re.search(r"my (policy|coverage|deductible|limit|premium|car|vehicle|home)", m)
            and not ctx.session.is_verified
        ):
            turn.needs_verification = True
            return
        # Directly answer "do I have / does my policy cover <X>?" instead of only listing
        # the schedule: check whether the asked-about coverage is present in what we found.
        if any(tc.tool_name == "lookup_coverages" for tc in turn.tool_calls):
            for pattern, label, concept in _COVERAGE_QUERY:
                if pattern.search(m):
                    present = any(f.split(" — ")[0].strip() == label for f in turn.facts)
                    turn.facts.insert(
                        0,
                        f"Yes — your policy includes {concept}."
                        if present
                        else f"No — your policy does not include {concept}.",
                    )
                    break
        if not turn.tool_calls and re.search(r"my (policy|coverage)", m):
            turn.clarification = (
                "Which policy is this about? A policy number like AUTO-10024 helps."
            )


class ClaimsAgent(Specialist):
    name = AgentName.CLAIMS
    system_prompt = _GUARDRAIL + " You handle claim lookups, status, FNOL filing, and disputes."

    async def augment(self, ctx: ToolContext, message: str, intent: IntentResult) -> None:
        # A verified caller asking about "my claim" without a number: resolve the claim(s)
        # they own. Filing a *new* claim needs explicit loss details (handled below), so
        # skip resolution there.
        if intent.entities.claim_numbers or re.search(
            r"\bfile\b.*claim|start a claim|new claim", message.lower()
        ):
            return
        if re.search(r"claim|adjuster|dispute|status", message.lower()):
            intent.entities.claim_numbers.extend(await _resolve_customer_claims(ctx))

    def plan(self, message: str, intent: IntentResult, ctx: ToolContext) -> list[PlannedCall]:
        calls: list[PlannedCall] = []
        m = message.lower()
        claims = intent.entities.claim_numbers
        if claims and "adjuster" in m:
            calls.append(PlannedCall("get_adjuster_info", {"claim_number": claims[0]}))
        elif claims and re.search(r"dispute|disagree|wrong|denied", m):
            calls.append(PlannedCall("escalate_claim_dispute", {"claim_number": claims[0]}))
        else:
            # Status for each owned/named claim (multi-claim households see them all).
            for claim in claims:
                calls.append(PlannedCall("get_claim_status", {"claim_number": claim}))
        # Always provide the claim workflow context.
        if re.search(r"how .*(file|claim)|workflow|what happens|next step", m):
            calls.append(PlannedCall("search_knowledge", {"query": message, "category": "claims"}))
        return calls

    def fact_from_data(self, turn, tool_name, data):
        if tool_name == "get_claim_status":
            steps = data.get("next_steps") or []
            if steps:
                turn.facts.append("Next steps: " + "; ".join(steps) + ".")
        if tool_name == "search_knowledge" and data.get("passages"):
            turn.facts.append(_snippet(data["passages"][0]["content"]))

    def post_process(self, turn, message, intent, ctx):
        m = message.lower()
        if (
            re.search(r"\bfile\b.*claim|start a claim|new claim", m)
            and not intent.entities.claim_numbers
        ):
            turn.clarification = (
                "I can start a first notice of loss. What's the policy number, the type of loss, "
                "and the date it happened?"
            )
        elif not turn.tool_calls and re.search(r"my claim|claim status|status of .*claim", m):
            if not ctx.session.is_verified:
                # Don't assert absence to an unverified caller — ask them to verify.
                turn.needs_verification = True
            else:
                # Verified caller asked about a claim but owns none.
                turn.clarification = (
                    "I don't see any claims on your account. If you had a loss, I can start a "
                    "first notice of loss — just tell me the policy, the loss type, and the date."
                )


class BillingAgent(Specialist):
    name = AgentName.BILLING
    system_prompt = (
        _GUARDRAIL + " You handle premiums, balances, payment history, and payments (TEST MODE)."
    )

    async def augment(self, ctx: ToolContext, message: str, intent: IntentResult) -> None:
        if (
            not intent.entities.policy_numbers
            and not intent.entities.invoice_numbers
            and re.search(r"balance|payment|premium|autopay|bill|owe|due", message.lower())
        ):
            # Resolve every relevant policy (filtered by product when the caller named one),
            # so a multi-policy household gets each balance instead of a "which policy?" prompt.
            intent.entities.policy_numbers.extend(
                await _resolve_customer_policies(ctx, infer_product(message))
            )

    def plan(self, message: str, intent: IntentResult, ctx: ToolContext) -> list[PlannedCall]:
        calls: list[PlannedCall] = []
        m = message.lower()
        inv = intent.entities.invoice_numbers[0] if intent.entities.invoice_numbers else None
        pending = ctx.session.pending_payment
        # Answering a prior "shall I confirm this payment?": a bare "yes" completes the
        # remembered charge; a bare "no" cancels it (acknowledged in post_process). Only
        # bare replies reach here — the orchestrator has already expired the pending state
        # for anything that carries other content.
        if pending and not inv:
            polarity = reply_polarity(message)
            if polarity == "affirm":
                args: dict[str, object] = {
                    "invoice_number": pending["invoice_number"],
                    "confirm": True,
                }
                if pending.get("amount") is not None:
                    args["amount"] = pending["amount"]
                calls.append(PlannedCall("make_payment", args))
                return calls
            if polarity == "decline":
                ctx.session.pending_payment = None
                return calls
        if inv and re.search(r"pay|make a payment", m):
            # The initial request always asks to confirm first (never auto-charges, not
            # even on "please"); make_payment records the pending charge and the caller's
            # "yes" on the next turn completes it via the pending path above.
            args = {"invoice_number": inv, "confirm": False}
            if intent.entities.amounts:
                args["amount"] = intent.entities.amounts[0]
            calls.append(PlannedCall("make_payment", args))
        elif intent.entities.policy_numbers:
            tool = (
                "get_payment_history"
                if re.search(r"history|past payment", m)
                else "get_billing_status"
            )
            for p in intent.entities.policy_numbers:
                calls.append(PlannedCall(tool, {"policy_number": p}))
        # Ground general billing questions ("what happens if I miss a payment?", "how does
        # autopay work?") from the docs — but not when a specific balance/payment lookup
        # already answered, so a doc "miss" never tails a concrete number.
        did_billing_lookup = bool(calls)
        concept_q = _is_conceptual(message) or bool(
            re.search(r"grace|late fee|lapse|reinstat|refund|autopay|billing cycle|paperless", m)
        )
        if re.search(r"why .*(increase|went up|higher|more)", m) or (
            not did_billing_lookup and concept_q
        ):
            calls.append(PlannedCall("search_knowledge", {"query": message, "category": "billing"}))
        return calls

    def fact_from_data(self, turn, tool_name, data):
        if tool_name == "search_knowledge" and data.get("passages"):
            turn.facts.append(_snippet(data["passages"][0]["content"]))

    def post_process(self, turn, message, intent, ctx):
        m = message.lower()
        # A bare "no" reaches billing only when it cancels a pending payment (routed here
        # by the orchestrator); acknowledge it instead of falling through to a clarification.
        if not turn.tool_calls and reply_polarity(message) == "decline":
            turn.clarification = (
                "No problem — I won't process that payment. Is there anything else I can help with?"
            )
            return
        # Account-specific billing question from an unverified caller → prompt to verify
        # rather than ask which policy.
        if (
            re.search(r"my (balance|payment|premium|bill|autopay)|what.*i owe", m)
            and not ctx.session.is_verified
        ):
            turn.needs_verification = True
            return
        if (
            not turn.tool_calls
            and re.search(r"pay|payment|balance|premium", m)
            and not intent.entities.policy_numbers
            and not intent.entities.invoice_numbers
            and not turn.needs_verification
        ):
            turn.clarification = "Which policy's billing should I look at? A policy number helps."


class AccountAgent(Specialist):
    name = AgentName.ACCOUNT
    system_prompt = (
        _GUARDRAIL + " You handle account profile, associated policies/claims, contact updates."
    )

    def plan(self, message: str, intent: IntentResult, ctx: ToolContext) -> list[PlannedCall]:
        m = message.lower()
        calls: list[PlannedCall] = []
        if re.search(r"policies|what do i have", m):
            calls.append(PlannedCall("list_policies", {}))
        if re.search(r"claims", m):
            calls.append(PlannedCall("list_claims", {}))
        if re.search(r"my (account|profile|info)", m) and not calls:
            calls.append(PlannedCall("lookup_customer", {}))
        return calls


class SchedulingAgent(Specialist):
    name = AgentName.SCHEDULING
    system_prompt = (
        _GUARDRAIL + " You schedule agent/adjuster/claims calls and manage appointments."
    )

    def plan(self, message: str, intent: IntentResult, ctx: ToolContext) -> list[PlannedCall]:
        # Scheduling requires a concrete time; if absent, post_process asks for it.
        return []

    def post_process(self, turn, message, intent, ctx):
        if intent.entities.dates:
            turn.clarification = (
                "I can schedule that. What time on "
                f"{intent.entities.dates[0]} works, and is this with your agent "
                "or a claims adjuster?"
            )
        else:
            turn.clarification = (
                "Happy to schedule a call. What day and time work, and is this with your agent "
                "or a claims adjuster?"
            )


class GeneralAgent(Specialist):
    name = AgentName.GENERAL
    system_prompt = (
        _GUARDRAIL + " You answer general insurance questions and terminology from documentation."
    )

    def plan(self, message: str, intent: IntentResult, ctx: ToolContext) -> list[PlannedCall]:
        if _GREETING_RE.match(message):
            return []  # a greeting isn't a documentation query
        return [
            PlannedCall(
                "search_knowledge", {"query": message, "product_type": infer_product(message)}
            )
        ]

    def fact_from_data(self, turn, tool_name, data):
        if tool_name == "search_knowledge":
            for p in data.get("passages", [])[:1]:
                turn.facts.append(_snippet(p["content"]))

    def post_process(self, turn, message, intent, ctx):
        if not turn.tool_calls and _GREETING_RE.match(message):
            turn.clarification = (
                "Hi! I'm the Assured assistant. I can help with your policies, claims, billing, "
                "and payments. For anything account-specific I'll verify your identity first. "
                "What can I help you with?"
            )


class EscalationAgent(Specialist):
    name = AgentName.ESCALATION
    system_prompt = _GUARDRAIL + " You produce a structured human handoff and support ticket."

    def plan(self, message: str, intent: IntentResult, ctx: ToolContext) -> list[PlannedCall]:
        return [
            PlannedCall(
                "transfer_to_human",
                {
                    "reason": "customer_requested_human"
                    if "human" in message.lower()
                    else "escalation",
                    "urgency": "high"
                    if re.search(r"urgent|asap|immediately|angry", message.lower())
                    else "normal",
                    "summary": message[:400],
                    "intents": intent.intents,
                    "policy_number": intent.entities.policy_numbers[0]
                    if intent.entities.policy_numbers
                    else None,
                    "claim_number": intent.entities.claim_numbers[0]
                    if intent.entities.claim_numbers
                    else None,
                },
            )
        ]

    def post_process(self, turn: AgentTurn, message, intent, ctx):
        turn.escalated = True


SPECIALISTS: dict[AgentName, Specialist] = {
    AgentName.POLICY: PolicyAgent(),
    AgentName.CLAIMS: ClaimsAgent(),
    AgentName.BILLING: BillingAgent(),
    AgentName.ACCOUNT: AccountAgent(),
    AgentName.SCHEDULING: SchedulingAgent(),
    AgentName.GENERAL: GeneralAgent(),
    AgentName.ESCALATION: EscalationAgent(),
}
