"""Deterministic evaluation runner.

Loads YAML cases from evals/, runs each through the real orchestrator against a
freshly seeded DB, and checks structured expectations (routing, tools, grounding,
verification, authorization, hallucination-resistance, escalation, latency). Results
are persisted to evaluation_runs and printed as a report.

A "case" is a conversation of one or more turns; expectations are asserted on the
final turn's answer + execution trace.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from insurance_ai.agents.orchestrator import Orchestrator
from insurance_ai.db.base import get_engine
from insurance_ai.db.init import create_schema
from insurance_ai.db.models import Customer, EvaluationRun, Policy
from insurance_ai.db.seed import seed
from insurance_ai.providers.factory import get_providers
from insurance_ai.rag.ingest import ingest_directory
from insurance_ai.security.session import Session
from insurance_ai.security.verification import VerificationClaim, verify_identity
from insurance_ai.tools.registry import load_all_tools

REPO = Path(__file__).resolve().parents[4]
EVALS_DIR = REPO / "evals"
KNOWLEDGE_DIR = REPO / "knowledge"


@dataclass
class CaseResult:
    id: str
    category: str
    passed: bool = False
    failures: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    answer: str = ""


def _contains_all(haystack: str, needles: list[str]) -> list[str]:
    low = haystack.lower()
    return [n for n in needles if n.lower() not in low]


async def _verify_setup(db, sess: Session, spec: Any) -> None:
    """setup.verify: policy_number (zip auto-resolved) or an explicit factor dict."""
    if isinstance(spec, str):
        row = await db.execute(select(Policy).where(Policy.policy_number == spec.upper()))
        policy = row.scalar_one_or_none()
        if not policy:
            return
        customer = await db.get(Customer, policy.customer_id)
        claim = VerificationClaim(policy_number=spec.upper(), zip_code=customer.zip_code)
    else:
        claim = VerificationClaim(**spec)
    await verify_identity(db, sess, claim)


async def run_case(db, orch: Orchestrator, case: dict) -> CaseResult:
    result = CaseResult(id=case["id"], category=case.get("category", "general"))
    sess = Session(conversation_id=f"eval-{case['id']}")
    # Persist a Conversation row so tools that reference conversation_id (tickets,
    # escalations) satisfy their foreign key, exactly as the live service guarantees.
    from insurance_ai.db.models import Conversation

    if not await db.get(Conversation, sess.conversation_id):
        db.add(Conversation(id=sess.conversation_id, channel="eval"))
        await db.commit()
    setup = case.get("setup", {})
    if "verify" in setup:
        await _verify_setup(db, sess, setup["verify"])

    turns = case.get("turns") or [{"message": case["message"], "expect": case.get("expect", {})}]
    last = None
    t0 = time.perf_counter()
    for turn in turns:
        last = await orch.run(db, sess, turn["message"], request_id=f"eval-{case['id']}")
    result.latency_ms = (time.perf_counter() - t0) * 1000
    result.answer = last.answer

    expect = turns[-1].get("expect", case.get("expect", {}))
    trace = last.trace
    tool_names = {t["tool_name"] for t in trace.tool_calls}

    for agent in expect.get("agents_include", []):
        if agent not in trace.agents:
            result.failures.append(f"expected agent '{agent}' in {trace.agents}")
    for intent in expect.get("intents_include", []):
        if intent not in trace.intents:
            result.failures.append(f"expected intent '{intent}' in {trace.intents}")
    for tool in expect.get("tools_include", []):
        if tool not in tool_names:
            result.failures.append(f"expected tool '{tool}' in {sorted(tool_names)}")
    missing = _contains_all(last.answer, expect.get("answer_contains", []))
    if missing:
        result.failures.append(f"answer missing {missing}")
    for banned in expect.get("answer_not_contains", []):
        if banned.lower() in last.answer.lower():
            result.failures.append(f"answer must NOT contain '{banned}'")
    if "needs_verification" in expect and last.needs_verification != expect["needs_verification"]:
        result.failures.append(
            f"needs_verification={last.needs_verification}, expected {expect['needs_verification']}")
    if expect.get("sources_nonempty") and not trace.sources:
        result.failures.append("expected non-empty sources")
    if expect.get("sources_empty") and trace.sources:
        result.failures.append("expected empty sources")
    if "escalated" in expect and trace.escalated != expect["escalated"]:
        result.failures.append(f"escalated={trace.escalated}, expected {expect['escalated']}")
    if "max_latency_ms" in expect and result.latency_ms > expect["max_latency_ms"]:
        result.failures.append(
            f"latency {result.latency_ms:.0f}ms > {expect['max_latency_ms']}ms")

    result.passed = not result.failures
    return result


def load_cases(evals_dir: Path) -> list[dict]:
    cases: list[dict] = []
    for path in sorted(evals_dir.rglob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or []
        for case in data:
            case.setdefault("category", path.parent.name)
            cases.append(case)
    return cases


async def run_suite(persist: bool = True, evals_dir: Path = EVALS_DIR, dispose: bool = True) -> dict:
    load_all_tools()
    engine = get_engine()
    await create_schema(engine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        await seed(db)
        await ingest_directory(db, KNOWLEDGE_DIR, get_providers().embedding)

    orch = Orchestrator()
    cases = load_cases(evals_dir)
    results: list[CaseResult] = []
    # Each case gets a fresh session (new DB session) to avoid state bleed.
    for case in cases:
        async with maker() as db:
            results.append(await run_case(db, orch, case))

    passed = sum(1 for r in results if r.passed)
    by_cat: dict[str, list[CaseResult]] = {}
    for r in results:
        by_cat.setdefault(r.category, []).append(r)

    print("\n=== Evaluation Results ===")
    for cat, rs in sorted(by_cat.items()):
        p = sum(1 for r in rs if r.passed)
        print(f"  {cat:14s} {p}/{len(rs)}")
        for r in rs:
            if not r.passed:
                print(f"     ✗ {r.id}: {'; '.join(r.failures)}")
    print(f"\nTOTAL: {passed}/{len(results)} passed "
          f"({(passed/len(results)*100 if results else 0):.0f}%)\n")

    if persist and results:
        async with maker() as db:
            db.add(EvaluationRun(
                suite="all", total=len(results), passed=passed,
                results=[{"id": r.id, "category": r.category, "passed": r.passed,
                          "failures": r.failures, "latency_ms": round(r.latency_ms, 1)}
                         for r in results],
            ))
            await db.commit()
    if dispose:
        await engine.dispose()
    return {"total": len(results), "passed": passed,
            "results": [(r.id, r.passed, r.failures) for r in results]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args()
    outcome = asyncio.run(run_suite(persist=not args.no_persist))
    raise SystemExit(0 if outcome["passed"] == outcome["total"] else 1)
