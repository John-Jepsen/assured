"""search_knowledge tool — grounded retrieval over insurance documentation.

This tool needs no verification: it returns general insurance information (not
customer-specific data) and always attributes sources. If nothing clears the
score threshold it says so, so the agent never invents coverage.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from insurance_ai.providers.factory import get_providers
from insurance_ai.rag.retriever import Retriever
from insurance_ai.tools.base import Source, Tool, ToolContext, ToolResult
from insurance_ai.tools.registry import register

KNOWLEDGE_AGENTS = ("policy", "claims", "billing", "general", "account")


class SearchArgs(BaseModel):
    query: str = Field(..., description="Natural-language question to ground")
    product_type: str | None = Field(None, description="Optional product filter, e.g. auto")
    category: str | None = Field(None, description="Optional category filter, e.g. claims")


async def _search_knowledge(ctx: ToolContext, args: SearchArgs) -> ToolResult:
    providers = ctx.providers or get_providers()
    retriever = Retriever(providers.embedding)
    hits = await retriever.search(
        ctx.db, args.query, product_type=args.product_type, category=args.category
    )
    if not hits:
        return ToolResult.success(
            {"passages": [], "grounded": False},
            message=(
                "I couldn't find documentation that reliably answers that. I won't guess — "
                "I can connect you with a licensed representative."
            ),
        )
    return ToolResult.success(
        {
            "grounded": True,
            "passages": [
                {"content": h.content, "citation": h.citation, "score": h.score} for h in hits
            ],
        },
        message="",  # passage content is surfaced via facts/sources, not a chatty message
        sources=[
            Source(citation=h.citation, chunk_id=h.chunk_id, document_id=h.document_id,
                   score=h.score, snippet=h.content[:200])
            for h in hits
        ],
    )


register(
    Tool(
        "search_knowledge",
        "Retrieve grounded insurance documentation with source attribution.",
        SearchArgs,
        _search_knowledge,
        requires_verification=False,
        agents=KNOWLEDGE_AGENTS,
    )
)
