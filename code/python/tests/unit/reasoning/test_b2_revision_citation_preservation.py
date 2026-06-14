"""Track B2：Revision 引用保存。

RED tests — 驗 B2 expected 行為：
- _write_section 增加 prior_sources_used 參數
- revise path：chapter_index > 0 時，若 prior_sources_used 非空，
  analyst_citations fallback 到 prior_sources_used（白名單過濾）而非 []。
- prior_sources_used=[] → 行為不變（analyst_citations=[]，視情況 blocked）。
- prior_sources_used=None → 非 revise path，行為不變（chapter_index>0 仍 []）。

實作策略：
- _write_section 加 prior_sources_used: Optional[List[int]] = None 參數
- chapter_override 分支 analyst_citations=[] 後：若 prior_sources_used 非空，
  改用 [eid for eid in prior_sources_used if eid in valid_ids]
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from reasoning.live_research.stage_state import LiveResearchStageState
from reasoning.schemas_live import (
    ContextMap,
    ContextMapTopic,
    EvidencePoolEntry,
    serialize_evidence_pool,
)


def _make_context_map():
    return ContextMap(
        research_question="台灣綠能",
        topics=[
            ContextMapTopic(
                topic_id="t1", name="緒論", domain="能源", relevance="core",
                evidence_ids=[1, 2, 3, 4, 5],
            ),
        ],
        version=1,
    )


def test_b2_write_section_accepts_prior_sources_used_param():
    """_write_section signature 必須接受 prior_sources_used 參數。"""
    import inspect
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    sig = inspect.signature(LiveResearchOrchestrator._write_section)
    assert "prior_sources_used" in sig.parameters, (
        "B2 FAIL: _write_section must accept prior_sources_used parameter"
    )
    # 預設值應為 None（向後兼容）
    default = sig.parameters["prior_sources_used"].default
    assert default is None, (
        f"B2 FAIL: prior_sources_used default should be None, got {default!r}"
    )


@pytest.mark.asyncio
async def test_b2_revise_chapter_override_index_gt0_uses_prior_sources():
    """B2 核心：revise path chapter_index=1（>0），prior_sources_used=[3,4]
    → C-1 gate 不 block（analyst_citations fallback 非空），section 成功寫出。

    驗證方式：dry_run mode 下 C-1 gate 通過 → status='drafted'（非 blocked_no_evidence）。
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = MagicMock()
    handler.site = "all"
    handler.query_params = {}
    handler.message_sender = None
    handler.connection_alive_event = None
    handler.request_handler = None

    with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
        # dry_run=True：C-1 gate 通過後走 dry_run 分支（無需 LLM）
        orch = LiveResearchOrchestrator(handler=handler, dry_run=True)

    context_map = _make_context_map()
    pool = {
        i: EvidencePoolEntry(evidence_id=i, title=f"E{i}", url=f"https://e{i}.com")
        for i in range(1, 6)
    }

    with patch.object(orch, "_emit_narration", new=AsyncMock()):
        section_out, was_corrected = await orch._write_section(
            context_map=context_map,
            topic={"name": "第二章", "outline": "分析各政策"},
            style_features=None,
            format_specs=None,
            evidence_pool=pool,
            chapter_index=1,        # >0 原本會設 analyst_citations=[]
            all_evidence_ids=[1, 2, 3, 4, 5],
            book_outline=None,
            current_chapter_index=1,
            revise_instruction="請加強引用數量",
            prior_section_content="第二章舊內容",
            prior_sources_used=[3, 4],   # B2 新參數：prior_sources_used 非空
        )

    # B2 core：C-1 gate 不應 block（prior_sources_used=[3,4] fallback 讓 analyst_citations 非空）
    assert section_out.status != "blocked_no_evidence", (
        f"B2 FAIL: chapter_index=1 with prior_sources_used=[3,4] "
        f"→ should NOT be blocked_no_evidence, got status={section_out.status!r}"
    )
    # dry_run mode 下應成功寫出 drafted section
    assert section_out.section_title == "第二章", (
        f"B2 FAIL: expected section_title='第二章', got {section_out.section_title!r}"
    )


@pytest.mark.asyncio
async def test_b2_revise_empty_prior_sources_blocked():
    """prior_sources_used=[] → analyst_citations 仍空 → blocked_no_evidence（行為不變）。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = MagicMock()
    handler.site = "all"
    handler.query_params = {}
    handler.message_sender = None
    handler.connection_alive_event = None
    handler.request_handler = None

    with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
        orch = LiveResearchOrchestrator(handler=handler, dry_run=True)

    context_map = _make_context_map()
    pool = {i: EvidencePoolEntry(evidence_id=i, title=f"E{i}", url=f"https://e{i}.com")
            for i in range(1, 6)}

    with patch.object(orch, "_emit_narration", new=AsyncMock()):
        section_out, _ = await orch._write_section(
            context_map=context_map,
            topic={"name": "第二章", "outline": "..."},
            style_features=None,
            format_specs=None,
            evidence_pool=pool,
            chapter_index=1,
            all_evidence_ids=[1, 2, 3, 4, 5],
            book_outline=None,
            current_chapter_index=1,
            revise_instruction="修改請求",
            prior_section_content="舊內容",
            prior_sources_used=[],   # 空 → 行為不變
        )

    # prior_sources_used=[] → no fallback → blocked_no_evidence
    assert section_out.status == "blocked_no_evidence", (
        f"B2: prior_sources_used=[] should result in blocked_no_evidence, "
        f"got status={section_out.status!r}"
    )


@pytest.mark.asyncio
async def test_b2_non_revise_path_still_blocked():
    """非 revise path（prior_sources_used=None）：chapter_index=1 → blocked_no_evidence（不變）。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = MagicMock()
    handler.site = "all"
    handler.query_params = {}
    handler.message_sender = None
    handler.connection_alive_event = None
    handler.request_handler = None

    with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
        orch = LiveResearchOrchestrator(handler=handler, dry_run=True)

    context_map = _make_context_map()
    pool = {i: EvidencePoolEntry(evidence_id=i, title=f"E{i}", url=f"https://e{i}.com")
            for i in range(1, 6)}

    with patch.object(orch, "_emit_narration", new=AsyncMock()):
        section_out, _ = await orch._write_section(
            context_map=context_map,
            topic={"name": "第二章", "outline": "..."},
            style_features=None,
            format_specs=None,
            evidence_pool=pool,
            chapter_index=1,
            all_evidence_ids=[1, 2, 3, 4, 5],
            book_outline=None,
            current_chapter_index=1,
            revise_instruction=None,
            prior_section_content=None,
            prior_sources_used=None,   # 非 revise path
        )

    # 非 revise：chapter_index=1 沒有 prior_sources_used → 仍 blocked
    assert section_out.status == "blocked_no_evidence", (
        f"B2: non-revise path chapter_index=1 should still be blocked, "
        f"got status={section_out.status!r}"
    )
