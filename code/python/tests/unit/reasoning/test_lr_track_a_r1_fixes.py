"""Track A R1 reviewer conditions — 4 Important findings 對應 fix 測試。

Sprint: feature/lr-dr-parity-sprint-2026-05-28
Review: code-reviewer R1 ⚠️ APPROVED WITH CONDITIONS — 4 Important fix。

對應 review findings：
- I-1: post-render gate 在 state=None + analyst_citations 非空時失效
       (rendered_via_state 不能當唯一條件)。
- I-2: guard_failed 的 model_copy update 沒 reset methodology_note，殘留前段 guard 註記。
- I-3: from_dict schema_version 偵測 — schema_version missing + evidence_usage 非空
       異常 case 視為 v2 + log ERROR。
- I-4: _is_intro_or_conclusion role/idx 不一致時 logger.warning → 升 ERROR
       (schema_validator 應 catch, 此處到達 = model_construct() bypass)。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reasoning.schemas_live import (
    BookOutline,
    ChapterPlan,
    ContextMap,
    ContextMapTopic,
    EvidencePoolEntry,
    LiveWriterSectionOutput,
)


# ============================================================================
# 共用 helpers
# ============================================================================

def _make_orchestrator(dry_run: bool = False):
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    handler = MagicMock()
    handler.site = "all"
    handler.query_params = {}
    handler.message_sender = None
    handler.connection_alive_event = None
    handler.http_handler = None
    return LiveResearchOrchestrator(handler=handler, dry_run=dry_run)


def _make_minimal_context_map():
    return ContextMap(
        research_question="台灣再生能源",
        topics=[
            ContextMapTopic(
                topic_id="t1", name="緒論", domain="能源",
                relevance="core", description="d", evidence_ids=[1, 2],
            ),
        ],
        version=0,
    )


# ============================================================================
# I-1: post-render gate 對 state=None + book_outline 有 case 失效
# ============================================================================

@pytest.mark.asyncio
async def test_i1_post_render_gate_fires_for_state_none_when_pool_view_empty():
    """I-1 + P2 W10：state=None + book_outline 非 None + body chapter，且 pool entry
    無 title/snippet（writer_evidence_view 渲不出東西）+ 無 narrative → post-render gate
    仍 fire 回 BlockedSection（不依賴 rendered_via_state）。

    P2 W10 調整（C-1 根治）：post-render gate 改判「全 pool grounding view + narrative
    都實質空」才擋。state=None 下 evidence_usage 空 → narrative 空；pool entry 無
    title/snippet → view 也空 → 兩者都空 → 擋。（pool 有實質料時改不擋，見下一 test。）
    """
    orch = _make_orchestrator(dry_run=False)
    cm = _make_minimal_context_map()

    book_outline = BookOutline(
        chapters=[
            ChapterPlan(chapter_index=0, title="前言", brief="x",
                        planned_evidence_ids=[], role="intro"),
            ChapterPlan(chapter_index=1, title="案例", brief="body",
                        planned_evidence_ids=[1, 2], role="body"),
            ChapterPlan(chapter_index=2, title="結論", brief="z",
                        planned_evidence_ids=[], role="conclusion"),
        ],
        overall_arc="test",
        redundancy_warnings=[],
    )

    # pool entry 無 title/snippet → render_grounding_evidence_view 跳過 → view 空
    pool = {
        1: EvidencePoolEntry(evidence_id=1, title="", url="", snippet=""),
        2: EvidencePoolEntry(evidence_id=2, title="", url="", snippet=""),
    }

    chapter_dict = {"name": "案例", "outline": "body content"}
    writer_called = {"called": False}

    async def fake_compose(**kw):
        writer_called["called"] = True
        return LiveWriterSectionOutput(
            section_title="案例", section_content="不應該到這裡",
            sources_used=[], confidence_level="Medium",
        )

    with patch("reasoning.agents.writer.WriterAgent") as MockWriterCls, \
         patch.object(orch, "_emit_narration", new=AsyncMock()):
        mock_writer_inst = MagicMock()
        mock_writer_inst.compose_section = AsyncMock(side_effect=fake_compose)
        MockWriterCls.return_value = mock_writer_inst

        result, _was_corrected = await orch._write_section(
            context_map=cm, topic=chapter_dict, style_features=None,
            format_specs=None, evidence_pool=pool, chapter_index=1,
            all_evidence_ids=[1, 2], book_outline=book_outline,
            current_chapter_index=1, state=None,  # state=None legacy path
        )

    assert result.status == "blocked_no_evidence", (
        f"post-render gate 應在 view+narrative 都空時 fire, got {result.status!r}"
    )
    assert writer_called["called"] is False
    assert "[本章資料不足]" in result.section_content


@pytest.mark.asyncio
async def test_i1_state_none_pool_with_snippet_calls_writer():
    """P2 W10（R1）：state=None 但 pool entry 有 title/snippet → writer_evidence_view
    非空 → writer 仍被呼叫（用 pool 視圖寫），不擋。全局模型下 raw pool 有料即可寫。"""
    orch = _make_orchestrator(dry_run=False)
    cm = _make_minimal_context_map()
    book_outline = BookOutline(
        chapters=[
            ChapterPlan(chapter_index=0, title="前言", brief="x",
                        planned_evidence_ids=[], role="intro"),
            ChapterPlan(chapter_index=1, title="案例", brief="body",
                        planned_evidence_ids=[1, 2], role="body"),
            ChapterPlan(chapter_index=2, title="結論", brief="z",
                        planned_evidence_ids=[], role="conclusion"),
        ],
        overall_arc="test", redundancy_warnings=[],
    )
    pool = {
        1: EvidencePoolEntry(evidence_id=1, title="E1", url="u", snippet="s1"),
        2: EvidencePoolEntry(evidence_id=2, title="E2", url="u", snippet="s2"),
    }
    chapter_dict = {"name": "案例", "outline": "body content"}
    writer_called = {"called": False}

    async def fake_compose(**kw):
        writer_called["called"] = True
        return LiveWriterSectionOutput(
            section_title="案例", section_content="content",
            sources_used=[1], confidence_level="Medium",
        )

    with patch("reasoning.agents.writer.WriterAgent") as MockWriterCls, \
         patch.object(orch, "_emit_narration", new=AsyncMock()):
        mock_writer_inst = MagicMock()
        mock_writer_inst.compose_section = AsyncMock(side_effect=fake_compose)
        MockWriterCls.return_value = mock_writer_inst

        result, _ = await orch._write_section(
            context_map=cm, topic=chapter_dict, style_features=None,
            format_specs=None, evidence_pool=pool, chapter_index=1,
            all_evidence_ids=[1, 2], book_outline=book_outline,
            current_chapter_index=1, state=None,
        )

    assert writer_called["called"] is True   # pool 有 snippet → writer 被呼叫
    assert result.status != "blocked_no_evidence"


@pytest.mark.asyncio
async def test_i1_contrast_legacy_book_outline_none_idx0_still_invokes_writer():
    """I-1 contrast: 紀律邊界 — book_outline=None 的純 legacy union-to-first path
    **不**走 post-render gate (仍依賴入口 gate 由 analyst_citations 判定)。

    場景: state=None + book_outline=None + chapter_index=0 + all_evidence_ids=[1,2,3]
    → analyst_citations=union=[1,2,3] → 入口 gate 不 fire (citations 非空) →
    走 else branch (state/book_outline 缺) → relevant_findings='' → post-render gate
    **不** fire (因 book_outline=None) → writer 被叫 (legacy 行為保留)。

    此 test 與既有
    test_write_section_chapter_override_first_index_uses_union_evidence_ids 對齊。
    """
    orch = _make_orchestrator(dry_run=False)
    cm = _make_minimal_context_map()
    chapter_a = {"name": "前言", "outline": "intro"}

    captured = []

    async def fake_compose(**kw):
        captured.append({
            "section_title": kw.get("section_title"),
            "analyst_citations": kw.get("analyst_citations"),
        })
        return LiveWriterSectionOutput(
            section_title=kw.get("section_title", "x"),
            section_content="...",
            sources_used=[],
            confidence_level="Medium",
            narration="stub",
        )

    with patch("reasoning.agents.writer.WriterAgent") as MockAgent, \
         patch.object(orch, "_emit_narration", new=AsyncMock()):
        inst = MockAgent.return_value
        inst.compose_section = AsyncMock(side_effect=fake_compose)

        await orch._write_section(
            context_map=cm, topic=chapter_a, style_features=None,
            format_specs={}, evidence_pool=None,
            chapter_index=0, all_evidence_ids=[1, 2, 3],
            book_outline=None,  # legacy path
            state=None,
        )

    # legacy 行為保留: writer 被叫, analyst_citations=union
    assert len(captured) == 1, (
        f"I-1 contrast FAIL: legacy book_outline=None idx=0 path 應該 invoke writer, "
        f"captured={captured}"
    )
    assert captured[0]["analyst_citations"] == [1, 2, 3]


# ============================================================================
# I-2: (已移除) guard_failed 整章替換路徑於模塊1 grounding over-block remake
#      依 CEO 決策④被拔除，改用 partial block 刪句 / DR-style 退化保留正文。
#      原 test_i2_guard_failed_resets_methodology_note 驗的是已廢除的 full-replace
#      methodology_note reset 行為（source-introspection），故隨路徑一併刪除。
#      參見 plan Task 2.2 Step 6（斷言 guard_failed 的測試需刪除或改為新斷言）。
# ============================================================================


# ============================================================================
# I-3: schema_version 偵測 — XOR 雙重檢查
# ============================================================================

def test_i3_from_dict_schema_version_missing_but_evidence_usage_nonempty_logs_error_and_treats_v2(monkeypatch):
    """I-3 fix: from_dict 偵測「schema_version missing + evidence_usage 非空」異常 case
    → log ERROR + 視為 v2（不當 v1 silently，避免 future test author 構造 v1 shape
    state with v2-specific evidence_usage 誤導 legacy gate test）。

    Fix 前：from_dict line `int(d.get('schema_version') or 1)` 該 case 落 v1，
    後續 legacy gate / validator 不會察覺 client/payload 異常。

    Fix 後：偵測異常 + log ERROR + schema_version=2 + state load 成功。

    ordering 免疫（full-scan-2026-07 收尾）：stage_state 的 logger 是
    logging.getLogger(__name__)，全套 ordering 下祖先 logger propagate 被前面測試
    設 False → caplog（掛 root）records 空、假紅（實測 `caplog records: []`）。
    改用同檔 I-4 測試的正解 pattern：monkeypatch spy stage_state.logger.error 直攔
    呼叫，繞過全域 propagation。行為斷言逐字不變（ERROR 含 schema_version）。
    """
    from reasoning.live_research import stage_state as ss_mod
    from reasoning.live_research.stage_state import LiveResearchStageState

    error_calls = []
    original_error = ss_mod.logger.error

    def spy_error(msg, *args, **kw):
        try:
            full = msg % args if args else msg
        except (TypeError, ValueError):
            full = str(msg)
        error_calls.append(str(full))
        return original_error(msg, *args, **kw)

    monkeypatch.setattr(ss_mod.logger, "error", spy_error)

    # 異常 payload: schema_version missing, evidence_usage 非空 (v2-specific data)
    anomalous_payload = {
        "current_stage": 5,
        "stage_status": "in_progress",
        # 故意省略 schema_version
        "evidence_usage": {
            "1": [{"claim": "x", "evidence_ids": [1], "confidence": "High",
                   "reasoning_type": "direct", "critic_status": "ACCEPT"}],
        },
    }

    s = LiveResearchStageState.from_dict(anomalous_payload)

    # Fix 後：視為 v2
    assert s.schema_version == 2, (
        f"I-3 FAIL: anomalous payload (schema_version missing + evidence_usage 非空) "
        f"should be treated as v2, got {s.schema_version}"
    )

    # Fix 後：log ERROR 提示 anomaly
    error_records = [m for m in error_calls if "schema_version" in m]
    assert error_records, (
        "I-3 FAIL: anomalous payload should log ERROR mentioning schema_version "
        f"(for SRE / oncall trace). error calls: {error_calls}"
    )

    # 對照 1: 正常 v1 payload (evidence_usage 空) → 仍 v1, 不 log ERROR
    error_calls.clear()
    legacy_v1_payload = {
        "current_stage": 5,
        "stage_status": "completed",
        # 省略 schema_version, 也省略 evidence_usage
    }
    s2 = LiveResearchStageState.from_dict(legacy_v1_payload)
    assert s2.schema_version == 1, (
        f"I-3 contrast: legacy v1 (no schema_version, no evidence_usage) "
        f"should remain v1, got {s2.schema_version}"
    )

    # 對照 2: 顯式 v2 (有 schema_version=2) → v2, 不 log ERROR
    error_calls.clear()
    explicit_v2_payload = {
        "current_stage": 5,
        "stage_status": "completed",
        "schema_version": 2,
        "evidence_usage": {},
    }
    s3 = LiveResearchStageState.from_dict(explicit_v2_payload)
    assert s3.schema_version == 2


# ============================================================================
# I-4: _is_intro_or_conclusion role/idx 不一致 — 升 ERROR
# ============================================================================

def test_i4_is_intro_or_conclusion_role_idx_inconsistency_logs_error(monkeypatch):
    """I-4 fix: schema_validator (BookOutline._validate_chapters_role_index_consistency)
    fail-loud raise；但 _is_intro_or_conclusion runtime 此處只 logger.warning + return False。

    Fix 後：升 ERROR — SRE 可監控頻率，這表示 model_construct() bypass 或直接 object
    creation 走 raw constructor 跳過 validator。

    紀律保持：不 raise（runtime 不要 crash），仍 return False = 當 body chapter
    走 C-1 gate（保守紀律不變）。

    注意: orchestrator.logger 是 LazyLogger wrapper, caplog 無法 capture (handlers
    隱藏在 AsyncLogProcessor 後)。改用 monkeypatch 直接 spy logger.error / warning
    method 紀錄呼叫次數。
    """
    from reasoning.live_research import orchestrator as orch_mod
    from reasoning.live_research.orchestrator import _is_intro_or_conclusion

    error_calls = []
    warning_calls = []

    original_error = orch_mod.logger.error
    original_warning = orch_mod.logger.warning

    def spy_error(msg, *args, **kw):
        # logger.error 可能用 % args 或 f-string；都記錄
        try:
            full = msg % args if args else msg
        except (TypeError, ValueError):
            full = str(msg)
        error_calls.append(full)
        return original_error(msg, *args, **kw)

    def spy_warning(msg, *args, **kw):
        try:
            full = msg % args if args else msg
        except (TypeError, ValueError):
            full = str(msg)
        warning_calls.append(full)
        return original_warning(msg, *args, **kw)

    monkeypatch.setattr(orch_mod.logger, "error", spy_error)
    monkeypatch.setattr(orch_mod.logger, "warning", spy_warning)

    # 構造 role=intro 但 idx=2 (非 0) — 紅隊 #2 場景
    # 用 model_construct 繞 schema validator（模擬 LLM 幻覺 / 直接構造）
    book_outline = BookOutline.model_construct(
        chapters=[
            ChapterPlan.model_construct(
                chapter_index=0, title="ch0", brief="b0",
                planned_evidence_ids=[], role="body",
            ),
            ChapterPlan.model_construct(
                chapter_index=1, title="ch1", brief="b1",
                planned_evidence_ids=[], role="body",
            ),
            ChapterPlan.model_construct(
                chapter_index=2, title="ch2", brief="b2",
                planned_evidence_ids=[], role="intro",  # 異常: intro at idx=2
            ),
        ],
        overall_arc="test",
        redundancy_warnings=[],
    )

    result = _is_intro_or_conclusion(book_outline, 2)

    # 紀律不變：仍 return False (當 body chapter, 走 C-1 gate)
    assert result is False, (
        f"I-4 紀律不變: inconsistency 仍 return False (當 body, 走 gate); got {result}"
    )

    # Fix 後：log level 升 ERROR (SRE 監控用)
    relevant_error = [
        m for m in error_calls
        if "CHAPTER ROLE INCONSISTENCY" in m
        or "outline_role_inconsistency" in m
        or "schema_validator" in m
    ]
    assert relevant_error, (
        f"I-4 FAIL: role/idx inconsistency should log at ERROR level "
        f"(SRE 可監控). error_calls: {error_calls}, warning_calls: {warning_calls}"
    )

    # Fix 後: 不再用 logger.warning 寫此 inconsistency (避免 double-log)
    relevant_warning = [
        m for m in warning_calls
        if "outline_role_inconsistency" in m or "CHAPTER ROLE INCONSISTENCY" in m
    ]
    assert not relevant_warning, (
        f"I-4 contrast: 升 ERROR 後不應同時 warning (避免 double log). "
        f"warning_calls: {warning_calls}"
    )
