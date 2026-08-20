"""Structured tool layer. Tools have typed IO, enforce authorization, and log."""

from insurance_ai.tools.registry import REGISTRY, get_tool, tools_for_agent  # noqa: F401
