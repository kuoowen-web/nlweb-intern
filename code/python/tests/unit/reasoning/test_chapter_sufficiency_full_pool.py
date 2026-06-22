"""P2 W9（SF1）：chapter_sufficiency 改用全 pool 量判（非 len(analyst_citations)）.

全局模型下 analyst_citations 空 ≠ 沒 evidence（writer 讀全 pool）。pool 非空卻標
critical 是誤判，導致 writer 被叫保守。改用「全 pool 有料量」判。
"""


def test_chapter_sufficiency_uses_full_pool():
    from reasoning.live_research.orchestrator import _compute_chapter_sufficiency
    from reasoning.schemas_live import EvidencePoolEntry
    pool = {i: EvidencePoolEntry(evidence_id=i, title=f"T{i}", snippet="s")
            for i in range(5)}
    # analyst_citations=[]，但 evidence_pool 有 5 筆 → 不該 critical
    suff = _compute_chapter_sufficiency(analyst_citations=[], evidence_pool=pool)
    assert suff != "critical"
    assert suff == "ok"


def test_chapter_sufficiency_critical_only_when_pool_empty():
    from reasoning.live_research.orchestrator import _compute_chapter_sufficiency
    suff = _compute_chapter_sufficiency(analyst_citations=[], evidence_pool={})
    assert suff == "critical"


def test_chapter_sufficiency_thin_for_small_pool():
    from reasoning.live_research.orchestrator import _compute_chapter_sufficiency
    from reasoning.schemas_live import EvidencePoolEntry
    pool = {i: EvidencePoolEntry(evidence_id=i, title=f"T{i}", snippet="s")
            for i in range(2)}
    suff = _compute_chapter_sufficiency(analyst_citations=[1], evidence_pool=pool)
    assert suff == "thin"
