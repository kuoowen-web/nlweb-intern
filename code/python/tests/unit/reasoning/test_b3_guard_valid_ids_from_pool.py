"""Track B3：Hallucination Guard valid_ids 改用 evidence_pool。

RED tests — 驗 B3 expected 行為：
- evidence_pool 有 ID 10 (合法，但不在 ContextMap.topics.evidence_ids 聯集)
  → writer 引用 ID 10，guard 不應 strip（舊行為：topics 聯集只 30 筆 → ID 10 不在 → 誤 strip）
- ContextMap.topics.evidence_ids 只有少數 ID，但 evidence_pool 有更多 ID
  → valid_ids 應來自 evidence_pool.keys()，不是 topics 聯集
- 空 evidence_pool → valid_ids 為空（B3 後行為一致：pool 空則沒有合法 ID）
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from reasoning.schemas_live import (
    ContextMap,
    ContextMapTopic,
    EvidencePoolEntry,
    serialize_evidence_pool,
)


def _make_orchestrator():
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    handler = MagicMock()
    handler.site = "all"
    handler.query_params = {}
    handler.message_sender = None
    handler.connection_alive_event = None
    handler.http_handler = None
    return LiveResearchOrchestrator(handler=handler, dry_run=True)


def _make_context_map_with_few_ids():
    """ContextMap.topics 只有 ID 1、2（模擬 topics 聯集遠少於 pool）。"""
    return ContextMap(
        research_question="台灣綠能",
        topics=[
            ContextMapTopic(
                topic_id="t1", name="緒論", domain="能源", relevance="core",
                evidence_ids=[1, 2],   # 只有 1,2（模擬 30 筆中的 subset）
            ),
        ],
        version=1,
    )


@pytest.mark.asyncio
async def test_b3_pool_id_not_in_topics_not_stripped():
    """B3 核心：evidence_pool 含 ID 10，但 ContextMap.topics 只有 [1,2]。
    B3 前：valid_ids={1,2} → writer 引用 10 被 guard 誤 strip。
    B3 後：valid_ids=pool.keys()={1,2,10} → ID 10 合法，不 strip。
    """
    orch = _make_orchestrator()
    context_map = _make_context_map_with_few_ids()

    # Pool 含 ID 1,2,10（10 在 pool 中是合法的，但不在 topics.evidence_ids）
    pool = {
        1: EvidencePoolEntry(evidence_id=1, title="E1", url="https://e1.com"),
        2: EvidencePoolEntry(evidence_id=2, title="E2", url="https://e2.com"),
        10: EvidencePoolEntry(evidence_id=10, title="E10", url="https://e10.com"),
    }

    from reasoning.schemas_live import LiveWriterSectionOutput

    # 模擬 writer 輸出：引用了 ID 10
    fake_section = LiveWriterSectionOutput(
        section_title="緒論",
        section_content="台電的報告顯示... [1]。E10 的分析... [10]。",
        sources_used=[1, 10],
        confidence_level="High",
    )

    # 直接測試 _write_section 使用的 valid_ids 計算
    # B3 後：valid_ids 應包含 10（來自 evidence_pool.keys()）
    # 透過 apply_hallucination_guard 驗證：傳入 pool.keys() 作為 valid_ids，10 應保留
    from reasoning.live_research.hallucination_guard import apply_hallucination_guard

    # B3 行為：valid_ids 從 pool.keys() 取
    b3_valid_ids = set(pool.keys())
    corrected_b3, was_corrected_b3 = apply_hallucination_guard(fake_section, b3_valid_ids)

    # B3 後：ID 10 在 valid_ids → 不 strip
    assert not was_corrected_b3, (
        "B3 FAIL: with pool-based valid_ids, ID 10 should be valid (not stripped)"
    )
    assert 10 in corrected_b3.sources_used, (
        f"B3 FAIL: ID 10 should remain in sources_used after guard, got {corrected_b3.sources_used}"
    )

    # 對照：舊行為（topics 聯集 valid_ids）
    old_valid_ids = {1, 2}  # 只有 topics.evidence_ids
    corrected_old, was_corrected_old = apply_hallucination_guard(fake_section, old_valid_ids)

    # 舊行為：ID 10 不在 topics 聯集 → 被 strip → was_corrected=True
    assert was_corrected_old, (
        "B3 contrast test: old behavior should strip ID 10 (not in topics.evidence_ids)"
    )
    assert 10 not in corrected_old.sources_used, (
        "B3 contrast: old behavior should have removed ID 10"
    )


@pytest.mark.asyncio
async def test_b3_write_section_uses_pool_for_valid_ids():
    """B3 整合：_write_section 內部的 valid_ids 計算應從 evidence_pool 取，
    使得 pool 中合法的 ID 不被 hallucination_guard 誤 strip。

    驗證：section 引用 ID 10（在 pool 但不在 topics）→ dry_run 輸出 sources_used 含 10。
    """
    orch = _make_orchestrator()
    context_map = _make_context_map_with_few_ids()

    pool = {
        1: EvidencePoolEntry(evidence_id=1, title="E1", url="https://e1.com"),
        2: EvidencePoolEntry(evidence_id=2, title="E2", url="https://e2.com"),
        10: EvidencePoolEntry(evidence_id=10, title="E10", url="https://e10.com"),
    }

    from reasoning.schemas_live import LiveWriterSectionOutput

    # dry_run 時 _write_section 產 dummy section (sources_used=[1])，
    # 透過 apply_hallucination_guard(dummy, valid_ids) 再 check。
    # 我們 patch apply_hallucination_guard，捕捉傳入的 valid_ids。
    captured_valid_ids = {}

    from reasoning.live_research import hallucination_guard as hg_mod

    original_fn = hg_mod.apply_hallucination_guard

    def fake_guard(section, valid_evidence_ids):
        captured_valid_ids["ids"] = set(valid_evidence_ids)
        return original_fn(section, valid_evidence_ids)

    with patch.object(hg_mod, "apply_hallucination_guard", side_effect=fake_guard), \
         patch.object(orch, "_emit_narration", new=AsyncMock()):
        await orch._write_section(
            context_map=context_map,
            topic=context_map.topics[0],  # ContextMapTopic 模式
            style_features=None,
            format_specs=None,
            evidence_pool=pool,
            chapter_index=None,  # 非 chapter_override
            book_outline=None,
            current_chapter_index=0,
        )

    # B3 後：valid_ids 應包含 pool 中所有 ID（{1, 2, 10}），不只是 topics 聯集（{1, 2}）
    assert 10 in captured_valid_ids.get("ids", set()), (
        f"B3 FAIL: valid_ids passed to guard should include pool ID 10, "
        f"got: {captured_valid_ids.get('ids')}"
    )
    assert captured_valid_ids.get("ids") >= {1, 2, 10}, (
        f"B3 FAIL: expected valid_ids to be superset of {{1,2,10}}, "
        f"got {captured_valid_ids.get('ids')}"
    )


def test_b3_valid_ids_empty_pool():
    """B3 邊界：evidence_pool=None/empty → valid_ids=empty（guard 仍能處理）。"""
    from reasoning.live_research.hallucination_guard import apply_hallucination_guard
    from reasoning.schemas_live import LiveWriterSectionOutput

    section = LiveWriterSectionOutput(
        section_title="T",
        section_content="content [1]",
        sources_used=[1],
        confidence_level="High",
    )
    # pool 為空時 valid_ids 也應為空
    corrected, was_corrected = apply_hallucination_guard(section, set())
    # ID 1 不在空 valid_ids → 應被 strip
    assert was_corrected is True
    assert 1 not in corrected.sources_used
