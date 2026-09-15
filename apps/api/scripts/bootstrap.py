"""Seed synthetic data and/or ingest the knowledge base after migrations run.

Usage (from apps/api):
    python -m scripts.bootstrap [--knowledge DIR] [--skip-seed] [--skip-ingest]

The deployment must apply Alembic migrations before invoking this command. Production
knowledge jobs use ``--skip-seed`` so synthetic customer data can never be introduced as
a side effect of ingestion.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from insurance_ai.db.base import get_engine
from insurance_ai.db.seed import seed
from insurance_ai.observability import get_logger
from insurance_ai.providers.factory import get_providers
from insurance_ai.rag.ingest import ingest_directory

log = get_logger("bootstrap")
DEFAULT_KNOWLEDGE = Path(__file__).resolve().parents[3] / "knowledge"


async def main(
    knowledge_dir: Path,
    *,
    if_empty: bool = False,
    seed_demo: bool = True,
    ingest: bool = True,
) -> None:
    from sqlalchemy import func, select

    from insurance_ai.db.models import Customer, KnowledgeDocument

    engine = get_engine()
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        seed_counts = None
        rag_counts = None
        if seed_demo:
            existing_customers = await db.scalar(select(func.count()).select_from(Customer))
            if if_empty and existing_customers:
                log.info(
                    "seed_skipped",
                    reason="already_seeded",
                    customers=int(existing_customers),
                )
            else:
                seed_counts = await seed(db)
                log.info("seeded", **seed_counts)

        if ingest:
            existing_documents = await db.scalar(
                select(func.count()).select_from(KnowledgeDocument)
            )
            if if_empty and existing_documents:
                log.info(
                    "ingest_skipped",
                    reason="already_ingested",
                    documents=int(existing_documents),
                )
            else:
                rag_counts = await ingest_directory(
                    db, knowledge_dir, get_providers().embedding
                )
                log.info(
                    "ingested",
                    provider=get_providers().embedding.name,
                    **rag_counts,
                )
    await engine.dispose()
    print(
        f"Bootstrap complete: seed={seed_counts or 'skipped'}, "
        f"ingest={rag_counts or 'skipped'}."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--knowledge", type=Path, default=DEFAULT_KNOWLEDGE)
    parser.add_argument(
        "--if-empty",
        action="store_true",
        help="Skip each selected operation when its target table already contains data.",
    )
    parser.add_argument(
        "--skip-seed",
        action="store_true",
        help="Do not load synthetic demo customers (required for production ingestion).",
    )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Do not ingest the bundled knowledge corpus.",
    )
    args = parser.parse_args()
    if args.skip_seed and args.skip_ingest:
        parser.error("at least one of seed or ingest must be enabled")
    asyncio.run(
        main(
            args.knowledge,
            if_empty=args.if_empty,
            seed_demo=not args.skip_seed,
            ingest=not args.skip_ingest,
        )
    )
