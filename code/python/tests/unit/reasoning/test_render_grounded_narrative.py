"""Tests for render_grounded_narrative helper (Track A Task 3, sprint 2026-05-28)。

把該章 evidence_ids 對應的 GroundedClaim 渲染為 writer 可讀 markdown findings:
- REJECT claim presentation 層 filter (Gemini Critical 拍板，source 層保留 forensic)
- WARN claim 行首明標 `[confidence: low | critic_status: WARN]` (Gemini Imp-1)
- 空 evidence_pool entry / 全 REJECT batch → 該 eid 整段跳過
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))


def test_render_grounded_narrative_groups_claims_per_evidence():
    from reasoning.schemas_live import (
        render_grounded_narrative, EvidencePoolEntry, GroundedClaim,
    )
    evidence_pool = {
        1: EvidencePoolEntry(evidence_id=1, title="T1", url="u",
                             snippet="snippet1"),
        2: EvidencePoolEntry(evidence_id=2, title="T2", url="u",
                             snippet="snippet2"),
    }
    evidence_usage = {
        1: [GroundedClaim(claim="claim-A", reasoning_type="induction",
                          confidence="high", source_topic="t1",
                          source_iteration=1).model_dump()],
        2: [GroundedClaim(claim="claim-B", reasoning_type="deduction",
                          confidence="medium", source_topic="t1",
                          source_iteration=2).model_dump()],
    }
    text = render_grounded_narrative(
        chapter_eids=[1, 2],
        evidence_usage=evidence_usage,
        evidence_pool=evidence_pool,
    )
    assert "claim-A" in text
    assert "claim-B" in text
    # eid header 出現
    assert "[1]" in text or "T1" in text
    assert "[2]" in text or "T2" in text


def test_render_grounded_narrative_empty_when_no_usage():
    from reasoning.schemas_live import render_grounded_narrative
    text = render_grounded_narrative(
        chapter_eids=[1, 2], evidence_usage={}, evidence_pool={},
    )
    assert text == ""


def test_render_grounded_narrative_skips_empty_evidence_entries():
    """evidence_pool 沒 title/snippet → 跳過 (不 propagate 空條目)。"""
    from reasoning.schemas_live import (
        render_grounded_narrative, EvidencePoolEntry, GroundedClaim,
    )
    pool = {
        1: EvidencePoolEntry(evidence_id=1, title="", url="", snippet=""),
    }
    usage = {
        1: [GroundedClaim(claim="c", reasoning_type="induction",
                          confidence="high", source_topic="t",
                          source_iteration=1).model_dump()],
    }
    text = render_grounded_narrative([1], usage, pool)
    # 空 evidence entry 跳過 → 結果空
    assert text == ""


def test_render_grounded_narrative_filters_reject_claims():
    """Gemini Critical: critic_status='REJECT' 一律 filter 不渲染
    (source 層保留 forensic trail，presentation 層 filter)。"""
    from reasoning.schemas_live import (
        render_grounded_narrative, EvidencePoolEntry, GroundedClaim,
    )
    pool = {
        1: EvidencePoolEntry(evidence_id=1, title="T1", url="u", snippet="s1"),
        2: EvidencePoolEntry(evidence_id=2, title="T2", url="u", snippet="s2"),
    }
    usage = {
        1: [GroundedClaim(claim="PASS-claim", reasoning_type="induction",
                          confidence="high", source_topic="t",
                          source_iteration=1, critic_status="PASS").model_dump()],
        2: [GroundedClaim(claim="REJECT-claim", reasoning_type="induction",
                          confidence="low", source_topic="t",
                          source_iteration=1, critic_status="REJECT").model_dump()],
    }
    text = render_grounded_narrative([1, 2], usage, pool)
    # PASS claim 渲染進 writer prompt
    assert "PASS-claim" in text
    # REJECT claim 不渲染進 writer prompt (DB 仍存於 evidence_usage)
    assert "REJECT-claim" not in text
    # 整批 REJECT 的 eid block 整段跳過 ([2] T2 不出現)
    assert "[2]" not in text and "T2" not in text


def test_render_grounded_narrative_warn_claim_carries_confidence_marker():
    """Gemini Imp-1: WARN 在 narrative 行首明標 `[confidence: low | critic_status: WARN]`
    讓 writer 看到時知道要降語氣。"""
    from reasoning.schemas_live import (
        render_grounded_narrative, EvidencePoolEntry, GroundedClaim,
    )
    pool = {
        1: EvidencePoolEntry(evidence_id=1, title="T1", url="u", snippet="s1"),
    }
    usage = {
        1: [GroundedClaim(claim="warned-claim", reasoning_type="induction",
                          confidence="low", source_topic="t",
                          source_iteration=1, critic_status="WARN").model_dump()],
    }
    text = render_grounded_narrative([1], usage, pool)
    assert "warned-claim" in text
    # 行首明標 marker
    assert "[confidence: low | critic_status: WARN]" in text


def test_render_grounded_narrative_chapter_eids_no_matching_pool_skips():
    """chapter_eids 內某 eid 在 evidence_pool 無 entry → 跳過該 eid (不報錯)。"""
    from reasoning.schemas_live import (
        render_grounded_narrative, EvidencePoolEntry, GroundedClaim,
    )
    pool = {
        1: EvidencePoolEntry(evidence_id=1, title="T1", url="u", snippet="s1"),
    }
    usage = {
        1: [GroundedClaim(claim="c1", reasoning_type="induction",
                          confidence="high", source_topic="t",
                          source_iteration=1).model_dump()],
        99: [GroundedClaim(claim="c99", reasoning_type="induction",
                           confidence="high", source_topic="t",
                           source_iteration=1).model_dump()],
    }
    text = render_grounded_narrative([1, 99], usage, pool)
    assert "c1" in text
    # eid 99 在 pool 無 entry → 整段跳過
    assert "c99" not in text


def test_render_grounded_narrative_dedupes_chapter_eids():
    """chapter_eids 含重複 eid → 只渲染一次。"""
    from reasoning.schemas_live import (
        render_grounded_narrative, EvidencePoolEntry, GroundedClaim,
    )
    pool = {
        1: EvidencePoolEntry(evidence_id=1, title="T1", url="u", snippet="s1"),
    }
    usage = {
        1: [GroundedClaim(claim="c1", reasoning_type="induction",
                          confidence="high", source_topic="t",
                          source_iteration=1).model_dump()],
    }
    text = render_grounded_narrative([1, 1, 1], usage, pool)
    assert text.count("c1") == 1


# ─────────────────────────────────────────────────────────────────────────
# P2 W4：render_grounded_narrative 改全 pool + priority 排序 + char budget
# ─────────────────────────────────────────────────────────────────────────

def test_render_grounded_narrative_full_pool_with_priority_and_budget():
    from reasoning.schemas_live import render_grounded_narrative, EvidencePoolEntry
    from reasoning.schemas_live import GroundedClaim
    pool = {
        i: EvidencePoolEntry(evidence_id=i, title=f"T{i}", snippet="s" * 50)
        for i in (1, 2, 3)
    }
    usage = {
        i: [GroundedClaim(claim=f"c{i}", reasoning_type="deduction",
                          confidence="high", source_topic="t",
                          source_iteration=1, critic_status="PASS").model_dump()]
        for i in (1, 2, 3)
    }
    out = render_grounded_narrative(
        chapter_eids=[1, 2, 3], evidence_usage=usage, evidence_pool=pool,
        priority_eids=[3], char_budget=10000,
    )
    assert "T1" in out and "T2" in out and "T3" in out      # 全 pool 都渲
    assert out.index("T3") < out.index("T1")                # priority eid 3 先渲


def test_render_grounded_narrative_budget_truncates():
    """char_budget 緊 → 截斷並附明示標記（不 silent）。"""
    from reasoning.schemas_live import render_grounded_narrative, EvidencePoolEntry
    from reasoning.schemas_live import GroundedClaim
    pool = {
        i: EvidencePoolEntry(evidence_id=i, title=f"T{i}", snippet="s" * 100)
        for i in range(1, 21)
    }
    usage = {
        i: [GroundedClaim(claim=f"c{i}" * 20, reasoning_type="deduction",
                          confidence="high", source_topic="t",
                          source_iteration=1, critic_status="PASS").model_dump()]
        for i in range(1, 21)
    }
    out = render_grounded_narrative(
        chapter_eids=list(range(1, 21)), evidence_usage=usage,
        evidence_pool=pool, priority_eids=[1], char_budget=500,
    )
    assert "budget" in out                                  # 明示截斷標記
    assert len(out) < 5000                                  # 確實被 cap


def test_render_grounded_narrative_priority_none_backward_compat():
    """priority_eids=None → 行為退回現況（升冪、無 budget 截斷）。"""
    from reasoning.schemas_live import render_grounded_narrative, EvidencePoolEntry
    from reasoning.schemas_live import GroundedClaim
    pool = {
        1: EvidencePoolEntry(evidence_id=1, title="T1", snippet="s"),
        2: EvidencePoolEntry(evidence_id=2, title="T2", snippet="s"),
    }
    usage = {
        i: [GroundedClaim(claim=f"c{i}", reasoning_type="deduction",
                          confidence="high", source_topic="t",
                          source_iteration=1, critic_status="PASS").model_dump()]
        for i in (1, 2)
    }
    out = render_grounded_narrative([1, 2], usage, pool)   # 不傳 priority/budget
    assert out.index("T1") < out.index("T2")               # 升冪
    assert "budget" not in out
