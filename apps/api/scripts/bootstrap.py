"""Create schema, seed synthetic data, and ingest the knowledge base.

Usage (from apps/api):
    python -m scripts.bootstrap [--knowledge DIR]

Idempotent: safe to re-run. Uses the configured DATABASE_URL.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from insurance_ai.db.base import get_engine
from insurance_ai.db.init import create_schema
from insurance_ai.db.seed import seed
from insurance_ai.observability import get_logger
from insurance_ai.providers.factory import get_providers
from insurance_ai.rag.ingest import ingest_directory

log = get_logger("bootstrap")
DEFAULT_KNOWLEDGE = Path(__file__).resolve().parents[3] / "knowledge"


async def main(knowledge_dir: Path, if_empty: bool = False) -> None:
    from sqlalchemy import func, select

    from insurance_ai.db.models import Customer

    engine = get_engine()
    await create_schema(engine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        if if_empty:
            existing = await db.scalar(select(func.count()).select_from(Customer))
            if existing:
                log.info("seed_skipped", reason="already_seeded", customers=int(existing))
                await engine.dispose()
                print(f"Schema ready; {existing} customers already present (skipped seeding).")
                return
        counts = await seed(db)
        log.info("seeded", **counts)
        rag = await ingest_directory(db, knowledge_dir, get_providers().embedding)
        log.info("ingested", **rag)
    await engine.dispose()
    print(f"Bootstrap complete: {counts} seeded, {rag} ingested from {knowledge_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--knowledge", type=Path, default=DEFAULT_KNOWLEDGE)
    parser.add_argument("--if-empty", action="store_true",
                        help="Only seed/ingest when the database has no customers yet.")
    args = parser.parse_args()
    asyncio.run(main(args.knowledge, if_empty=args.if_empty))
