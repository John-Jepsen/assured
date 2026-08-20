"""Reranking reorders real candidates by query relevance without inventing content."""

from __future__ import annotations

from insurance_ai.rag.rerank import lexical_rerank, rerank
from insurance_ai.rag.retriever import Retrieved


def _cand(cid, content, score):
    return Retrieved(chunk_id=cid, document_id="d", content=content, citation=cid,
                     score=score, product_type=None, category="auto")


def test_lexical_rerank_promotes_higher_coverage():
    # 'b' has a slightly lower base score but actually contains the query terms.
    query = "collision deductible amount for my vehicle"
    a = _cand("a", "General information about premiums and billing cycles.", 0.90)
    b = _cand("b", "Your collision deductible is the amount you pay on a vehicle claim.", 0.82)
    out = lexical_rerank(query, [a, b])
    assert out[0].chunk_id == "b", "the passage that covers the query terms should win"


def test_rerank_none_is_identity():
    a = _cand("a", "x", 0.9)
    b = _cand("b", "y", 0.8)
    out = rerank("none", "q", [a, b], "model")
    assert [c.chunk_id for c in out] == ["a", "b"]


def test_rerank_preserves_candidate_set():
    query = "rental car reimbursement"
    cands = [_cand(str(i), f"passage {i} rental", 0.5 + i * 0.01) for i in range(5)]
    out = lexical_rerank(query, cands)
    assert {c.chunk_id for c in out} == {str(i) for i in range(5)}  # no additions/losses
