"""Tool base types: context, result envelope, and the executable Tool wrapper.

Design goals (spec §22): typed inputs/outputs, argument validation, authorization
enforcement, structured errors, execution logging, independent testability.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from insurance_ai.security.authorization import AuthContext, AuthorizationError
from insurance_ai.security.session import Session

ArgsT = TypeVar("ArgsT", bound=BaseModel)


@dataclass
class Source:
    """A grounded source reference surfaced to the user/admin."""

    citation: str
    document_id: str | None = None
    chunk_id: str | None = None
    score: float | None = None
    snippet: str | None = None


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    error_code: str | None = None
    sources: list[Source] = field(default_factory=list)

    @classmethod
    def success(cls, data: dict[str, Any], message: str = "", sources=None) -> ToolResult:
        return cls(ok=True, data=data, message=message, sources=sources or [])

    @classmethod
    def failure(cls, code: str, message: str) -> ToolResult:
        return cls(ok=False, error_code=code, message=message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "message": self.message,
            "error_code": self.error_code,
            "sources": [s.__dict__ for s in self.sources],
        }


@dataclass
class ToolContext:
    """Everything a tool needs, injected by the runtime (not the LLM)."""

    session: Session
    db: AsyncSession
    providers: Any = None
    conversation_id: str | None = None
    # sink for ToolExecution records; runtime attaches DB-backed logger
    log_sink: Callable[[dict[str, Any]], Awaitable[None]] | None = None

    @property
    def auth(self) -> AuthContext:
        return AuthContext(session=self.session, db=self.db)


class Tool(Generic[ArgsT]):
    """Wraps a tool function with validation, auth error handling, and logging."""

    def __init__(
        self,
        name: str,
        description: str,
        args_model: type[ArgsT],
        func: Callable[[ToolContext, ArgsT], Awaitable[ToolResult]],
        *,
        requires_verification: bool = False,
        agents: tuple[str, ...] = (),
    ) -> None:
        self.name = name
        self.description = description
        self.args_model = args_model
        self.func = func
        self.requires_verification = requires_verification
        self.agents = agents

    async def run(self, ctx: ToolContext, raw_args: dict[str, Any]) -> ToolResult:
        start = time.perf_counter()
        result: ToolResult
        try:
            args = self.args_model.model_validate(raw_args)
        except ValidationError as e:
            result = ToolResult.failure("invalid_arguments", _fmt_validation(e))
        else:
            try:
                result = await self.func(ctx, args)
            except AuthorizationError as e:
                result = ToolResult.failure(e.code, e.message)
            except Exception as e:  # never leak a stack trace to the caller
                result = ToolResult.failure("tool_error", f"The {self.name} action failed.")
                result.data = {"exception": type(e).__name__}
        latency_ms = (time.perf_counter() - start) * 1000
        await self._log(ctx, raw_args, result, latency_ms)
        return result

    async def _log(
        self, ctx: ToolContext, raw_args: dict, result: ToolResult, latency_ms: float
    ) -> None:
        if ctx.log_sink is None:
            return
        await ctx.log_sink(
            {
                "conversation_id": ctx.conversation_id,
                "tool_name": self.name,
                "arguments": _redact(raw_args),
                "result": result.to_dict(),
                "ok": result.ok,
                "error_code": result.error_code,
                "latency_ms": latency_ms,
            }
        )


_SENSITIVE_KEYS = {"otp_code", "date_of_birth", "card_number", "cvv", "ssn"}


def _redact(args: dict[str, Any]) -> dict[str, Any]:
    return {k: ("***" if k in _SENSITIVE_KEYS else v) for k, v in args.items()}


def _fmt_validation(e: ValidationError) -> str:
    parts = [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]
    return "Invalid arguments — " + "; ".join(parts)
