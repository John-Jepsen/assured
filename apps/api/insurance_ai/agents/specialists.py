"""Concrete specialist agents with scoped tools and deterministic planners."""

from __future__ import annotations

import re

from insurance_ai.agents.base import AgentTurn, PlannedCall, Specialist
from insurance_ai.agents.intent import IntentResult, infer_product
from insurance_ai.domain.enums import AgentName
from insurance_ai.tools.base import ToolContext

_GUARDRAIL = (
    "You are a licensed-style insurance support assistant for a SYNTHETIC demo. "
    "Only state facts present in the provided tool results and sources. Never invent "
    "policy terms, coverage, balances, or claim statuses. Never guarantee coverage or "
    "claim approval. If information is missing, say so and offer to escalate."
)


def _policy_hint(intent: IntentResult, ctx: ToolContext) -> str | None:
    if intent.entities.policy_numbers:
        return intent.entities.policy_numbers[0]
    return None


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


def _snippet(text: str, max_chars: int = 260) -> str:
    """Trim a retrieved passage to its first sentence(s) for a concise answer."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    last = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[: last + 1] if last > 60 else cut).strip() + " …"


class PolicyAgent(Specialist):
    name = AgentName.POLICY
    system_prompt = (
        _GUARDRAIL
        + " You handle coverage, deductibles, limits, exclusions, renewals, and policy changes."
    )

    async def augment(self, ctx: ToolContext, message: str, intent: IntentResult) -> None:
        if not intent.entities.policy_numbers and re.search(
            r"cover|deductible|limit|exclusion|renew|my policy", message.lower()
        ):
            resolved = await _resolve_single_policy(ctx, infer_product(message))
            if resolved:
                intent.entities.policy_numbers.append(resolved)

    def plan(self, message: str, intent: IntentResult, ctx: ToolContext) -> list[PlannedCall]:
        calls: list[PlannedCall] = []
        pol = _policy_hint(intent, ctx)
        m = message.lower()
        # Specific policy facts lead the answer; general documentation grounds it after.
        if pol:
            if re.search(r"cover|deductible|limit|exclusion", m):
                calls.append(PlannedCall("lookup_coverages", {"policy_number": pol}))
            elif re.search(r"vehicle|asset|driver|dwelling|insured", m):
                calls.append(PlannedCall("lookup_insured_assets", {"policy_number": pol}))
            else:
                calls.append(PlannedCall("lookup_policy", {"policy_number": pol}))
            if re.search(r"change|add|remove|update .*(vehicle|driver)", m):
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
        if re.search(r"cover|rental|deductible|limit|exclusion|glass|windshield|tow", m):
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

    def post_process(self, turn: AgentTurn, message: str, intent: IntentResult) -> None:
        if not turn.tool_calls and re.search(r"my (policy|coverage)", message.lower()):
            turn.clarification = (
                "Which policy is this about? A policy number like AUTO-10024 helps."
            )


class ClaimsAgent(Specialist):
    name = AgentName.CLAIMS
    system_prompt = _GUARDRAIL + " You handle claim lookups, status, FNOL filing, and disputes."

    def plan(self, message: str, intent: IntentResult, ctx: ToolContext) -> list[PlannedCall]:
        calls: list[PlannedCall] = []
        m = message.lower()
        claim = intent.entities.claim_numbers[0] if intent.entities.claim_numbers else None
        if claim:
            if "adjuster" in m:
                calls.append(PlannedCall("get_adjuster_info", {"claim_number": claim}))
            elif re.search(r"dispute|disagree|wrong|denied", m):
                calls.append(PlannedCall("escalate_claim_dispute", {"claim_number": claim}))
            else:
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

    def post_process(self, turn, message, intent):
        m = message.lower()
        if (
            re.search(r"\bfile\b.*claim|start a claim|new claim", m)
            and not intent.entities.claim_numbers
        ):
            turn.clarification = (
                "I can start a first notice of loss. What's the policy number, the type of loss, "
                "and the date it happened?"
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
            and re.search(r"balance|payment|premium|autopay|bill", message.lower())
        ):
            resolved = await _resolve_single_policy(ctx, infer_product(message))
            if resolved:
                intent.entities.policy_numbers.append(resolved)

    def plan(self, message: str, intent: IntentResult, ctx: ToolContext) -> list[PlannedCall]:
        calls: list[PlannedCall] = []
        m = message.lower()
        pol = intent.entities.policy_numbers[0] if intent.entities.policy_numbers else None
        inv = intent.entities.invoice_numbers[0] if intent.entities.invoice_numbers else None
        if inv and re.search(r"pay|make a payment", m):
            confirm = bool(re.search(r"\b(yes|confirm|go ahead|do it|please)\b", m))
            args = {"invoice_number": inv, "confirm": confirm}
            if intent.entities.amounts:
                args["amount"] = intent.entities.amounts[0]
            calls.append(PlannedCall("make_payment", args))
        elif pol:
            if re.search(r"history|past payment", m):
                calls.append(PlannedCall("get_payment_history", {"policy_number": pol}))
            else:
                calls.append(PlannedCall("get_billing_status", {"policy_number": pol}))
        if re.search(r"why .*(increase|went up|higher|more)", m):
            calls.append(PlannedCall("search_knowledge", {"query": message, "category": "billing"}))
        return calls

    def fact_from_data(self, turn, tool_name, data):
        if tool_name == "search_knowledge" and data.get("passages"):
            turn.facts.append(_snippet(data["passages"][0]["content"]))

    def post_process(self, turn, message, intent):
        m = message.lower()
        if (
            re.search(r"pay|payment|balance|premium", m)
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

    def post_process(self, turn, message, intent):
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
        return [
            PlannedCall(
                "search_knowledge", {"query": message, "product_type": infer_product(message)}
            )
        ]

    def fact_from_data(self, turn, tool_name, data):
        if tool_name == "search_knowledge":
            for p in data.get("passages", [])[:1]:
                turn.facts.append(_snippet(p["content"]))


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

    def post_process(self, turn: AgentTurn, message, intent):
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
