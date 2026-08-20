"""Global tool registry + per-agent tool scoping.

Agents receive only their relevant tools (spec §4: do not expose every tool to
every agent). ``tools_for_agent`` returns the scoped subset.
"""

from __future__ import annotations

from insurance_ai.tools.base import Tool

REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    if tool.name in REGISTRY:
        raise ValueError(f"Duplicate tool: {tool.name}")
    REGISTRY[tool.name] = tool
    return tool


def get_tool(name: str) -> Tool | None:
    return REGISTRY.get(name)


def tools_for_agent(agent: str) -> list[Tool]:
    return [t for t in REGISTRY.values() if agent in t.agents]


def load_all_tools() -> None:
    """Import tool modules so their ``register`` calls run. Idempotent."""
    from insurance_ai.tools import (  # noqa: F401
        account,
        billing,
        claims,
        escalation,
        knowledge,
        policy,
        scheduling,
    )
