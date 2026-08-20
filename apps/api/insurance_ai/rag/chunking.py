"""Document parsing + chunking.

Markdown documents carry front-matter (title/product/category). Chunking is
paragraph-aware with a target size and overlap so citations map to coherent spans.
Chunk size/overlap are configurable and evaluated (see docs/rag.md), not guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ParsedDoc:
    title: str
    product_type: str | None
    category: str
    body: str


@dataclass
class Chunk:
    index: int
    content: str


_FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_document(raw: str, *, fallback_category: str, fallback_title: str) -> ParsedDoc:
    meta: dict[str, str] = {}
    body = raw
    m = _FRONT_MATTER.match(raw)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip().lower()] = v.strip()
        body = raw[m.end() :]
    return ParsedDoc(
        title=meta.get("title", fallback_title),
        product_type=meta.get("product") or None,
        category=meta.get("category", fallback_category),
        body=body.strip(),
    )


def chunk_text(text: str, *, chunk_size: int, overlap: int) -> list[Chunk]:
    """Greedy paragraph packing with character overlap between adjacent chunks."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if not current:
            current = para
        elif len(current) + len(para) + 2 <= chunk_size:
            current += "\n\n" + para
        else:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            current = (tail + "\n\n" + para).strip() if tail else para
    if current:
        chunks.append(current)
    # Hard-split any oversized single paragraph.
    out: list[Chunk] = []
    for c in chunks:
        if len(c) <= chunk_size * 1.5:
            out.append(Chunk(index=len(out), content=c))
        else:
            for i in range(0, len(c), chunk_size):
                out.append(Chunk(index=len(out), content=c[i : i + chunk_size]))
    return out
