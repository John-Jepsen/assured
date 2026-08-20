"""Run the full YAML evaluation suite as part of `make test`.

Uses the global test DB (SQLite via conftest). Asserts every case passes, so a
regression in routing/grounding/authorization fails CI, not just `make eval`.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_evaluation_suite_all_pass():
    from insurance_ai.evals.runner import run_suite

    outcome = await run_suite(persist=False, dispose=False)
    assert outcome["total"] >= 30, "expected a substantial eval suite"
    failures = [(cid, fails) for cid, ok, fails in outcome["results"] if not ok]
    assert not failures, f"eval failures: {failures}"
