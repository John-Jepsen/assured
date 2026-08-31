"""Specialist agent base.

A specialist owns: a guardrailed system prompt, a *scoped* tool set, a deterministic
planner (message + entities → tool calls), and a grounded composer. Tool selection is
deterministic and auditable; tool authorization is enforced in the tool layer. The LLM
only phrases already-verified facts, so it cannot invent policy data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from insurance_ai.agents.intent import IntentResult
from insurance_ai.domain.enums import AgentName
from insurance_ai.tools.base import Source, ToolContext, ToolResult
from insurance_ai.tools.registry import get_tool


@dataclass
class ToolCallTrace:
    tool_name: str
    arguments: dict[str, Any]
    ok: bool
    error_code: str | None
    result_summary: str


@dataclass
class AgentTurn:
    agent: AgentName
    facts: list[str] = field(default_factory=list)  # grounded statements for composition
    sources: list[Source] = field(default_factory=list)
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    needs_verification: bool = False
    escalated: bool = False
    clarification: str | None = None
    direct_message: str | None = None  # bypass composition (e.g. verification prompt)


@dataclass
class PlannedCall:
    tool_name: str
    arguments: dict[str, Any]


class Specialist:
    name: AgentName = AgentName.GENERAL
    system_prompt: str = "You are a helpful, precise insurance support representative."

    def plan(self, message: str, intent: IntentResult, ctx: ToolContext) -> list[PlannedCall]:
        """Override: choose scoped tool calls from the message + entities."""
        return []

    async def augment(self, ctx: ToolContext, message: str, intent: IntentResult) -> None:
        """Override: resolve missing context (e.g. the verified customer's policy)."""

    async def handle(self, ctx: ToolContext, message: str, intent: IntentResult) -> AgentTurn:
        turn = AgentTurn(agent=self.name)
        await self.augment(ctx, message, intent)
        plan = self.plan(message, intent, ctx)
        for call in plan:
            tool = get_tool(call.tool_name)
            if tool is None or self.name.value not in tool.agents:
                continue  # scoping guard: never run a tool outside this agent's set
            result = await tool.run(ctx, call.arguments)
            self._absorb(turn, call.tool_name, call.arguments, result)
        self.post_process(turn, message, intent, ctx)
        return turn

    def _absorb(self, turn: AgentTurn, name: str, args: dict, result: ToolResult) -> None:
        turn.tool_calls.append(
            ToolCallTrace(
                tool_name=name,
                arguments=args,
                ok=result.ok,
                error_code=result.error_code,
                result_summary=result.message or ("ok" if result.ok else "error"),
            )
        )
        turn.sources.extend(result.sources)
        if result.ok:
            if result.message:
                turn.facts.append(result.message)
            self.fact_from_data(turn, name, result.data)
        else:
            if result.error_code == "not_verified":
                turn.needs_verification = True
            turn.facts.append(result.message)

    def fact_from_data(self, turn: AgentTurn, tool_name: str, data: dict) -> None:
        """Override to turn structured tool data into human-facing grounded facts."""

    def post_process(
        self, turn: AgentTurn, message: str, intent: IntentResult, ctx: ToolContext
    ) -> None:
        """Override for agent-specific finalization (clarifications, escalation)."""
