"""Tests for _classify_meta_intent helper and Stage 3/5 integration."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from reasoning.live_research.orchestrator import _classify_meta_intent, META_INTENT_SKIP, META_INTENT_ABORT, META_INTENT_SUBSTANTIVE
from reasoning.live_research import lr_copy


def _handler():
    h = MagicMock()
    h.query_params = {}
    return h


@pytest.mark.asyncio
async def test_classify_skip_use_default():
    """『用預設的學術風格就好』→ skip_use_default。"""
    with patch("core.llm.ask_llm", new=AsyncMock(return_value={"category": "skip_use_default", "reason": "x"})):
        result = await _classify_meta_intent("用預設的學術風格就好", _handler())
    assert result == META_INTENT_SKIP


@pytest.mark.asyncio
async def test_classify_abort_cancel():
    """『算了』→ abort_cancel。"""
    with patch("core.llm.ask_llm", new=AsyncMock(return_value={"category": "abort_cancel", "reason": "x"})):
        result = await _classify_meta_intent("算了", _handler())
    assert result == META_INTENT_ABORT


@pytest.mark.asyncio
async def test_classify_substantive():
    """實質內容 → substantive。"""
    with patch("core.llm.ask_llm", new=AsyncMock(return_value={"category": "substantive", "reason": "x"})):
        result = await _classify_meta_intent("台灣的能源政策正處於轉型的十字路口，這是我寫過的一段。", _handler())
    assert result == META_INTENT_SUBSTANTIVE


@pytest.mark.asyncio
async def test_classify_llm_failure_returns_none():
    """LLM API 失敗（回空）→ None，不可預設成某類意圖蒙混（不可 silent fail）。"""
    with patch("core.llm.ask_llm", new=AsyncMock(return_value=None)):
        result = await _classify_meta_intent("算了", _handler())
    assert result is None


@pytest.mark.asyncio
async def test_classify_llm_exception_returns_none():
    """LLM 拋例外 → None（fail-loud：caller 沿用 #21 系統端文案，不怪 user）。"""
    with patch("core.llm.ask_llm", new=AsyncMock(side_effect=RuntimeError("boom"))):
        result = await _classify_meta_intent("算了", _handler())
    assert result is None


@pytest.mark.asyncio
async def test_classify_unknown_category_returns_substantive():
    """LLM 回未知 category → 保守當 substantive（不誤攔正常輸入；abort 走專屬詞，見紀律）。"""
    with patch("core.llm.ask_llm", new=AsyncMock(return_value={"category": "weird", "reason": "x"})):
        result = await _classify_meta_intent("一些內容", _handler())
    assert result == META_INTENT_SUBSTANTIVE


# ──── Task 2: Stage 3 首次回覆接線 ────

from reasoning.live_research.orchestrator import LiveResearchOrchestrator
from reasoning.live_research.stage_state import LiveResearchStageState


def _orch():
    h = _handler()
    h.message_sender = MagicMock()
    h.message_sender.send_message = AsyncMock()
    with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
        return LiveResearchOrchestrator(handler=h)


@pytest.mark.asyncio
async def test_stage3_skip_use_default_does_not_analyze():
    """#16：『用預設就好』→ 不跑 style analysis，用預設往下（style_features_json 空 + completed）。"""
    orch = _orch()
    state = LiveResearchStageState(current_stage=3, stage_status="in_progress")
    orch._run_style_analysis = AsyncMock()  # 不該被呼叫
    with patch("reasoning.live_research.orchestrator._classify_meta_intent",
               new=AsyncMock(return_value="skip_use_default")):
        result = await orch._handle_stage_3_response(state, "用預設的學術風格就好", auto_continue=False)
    assert result.style_features_json == ""
    assert result.stage_status == "completed"
    orch._run_style_analysis.assert_not_called()


@pytest.mark.asyncio
async def test_stage3_substantive_runs_analysis():
    """實質範本 → 照常跑 style analysis（停在 checkpoint 等確認）。"""
    orch = _orch()
    state = LiveResearchStageState(current_stage=3, stage_status="in_progress")
    fake_style = MagicMock()
    fake_style.model_dump_json = MagicMock(return_value='{"features":[]}')
    fake_style.overall_tone = "學術"
    fake_style.features = []
    orch._run_style_analysis = AsyncMock(return_value=fake_style)
    with patch("reasoning.live_research.orchestrator._classify_meta_intent",
               new=AsyncMock(return_value="substantive")):
        result = await orch._handle_stage_3_response(state, "這是我寫過的一段學術文字……", auto_continue=False)
    orch._run_style_analysis.assert_awaited_once()
    assert result.stage_status == "checkpoint"


@pytest.mark.asyncio
async def test_stage3_meta_intent_llm_failure_fail_loud():
    """#16 + 不可 silent fail：helper 回 None → 系統端文案 + 停 checkpoint，不分析不推進。"""
    orch = _orch()
    state = LiveResearchStageState(current_stage=3, stage_status="in_progress")
    orch._run_style_analysis = AsyncMock()
    with patch("reasoning.live_research.orchestrator._classify_meta_intent",
               new=AsyncMock(return_value=None)):
        result = await orch._handle_stage_3_response(state, "用預設就好", auto_continue=False)
    orch._run_style_analysis.assert_not_called()
    assert result.stage_status == "checkpoint"  # 停原地，不 advance
    assert result.style_features_json == ""


# ──── Task 3: Stage 5 abort guardrail ────

from tests.unit.reasoning.test_live_orchestrator import _make_context_map


@pytest.mark.asyncio
async def test_stage5_abort_does_not_export():
    """『算了』→ 絕不 complete_stage/匯出，停在 Stage 5 checkpoint 問確認。"""
    orch = _orch()
    state = LiveResearchStageState(current_stage=5, stage_status="in_progress")
    state.written_sections = [{"section_index": 0, "title": "t", "content": "c"}]
    state.context_map_json = _make_context_map().model_dump_json()
    parse_mock = AsyncMock(return_value={"action": "done"})  # 若被打到 = bug
    orch._parse_revision_intent = parse_mock
    with patch("reasoning.live_research.orchestrator._classify_meta_intent",
               new=AsyncMock(return_value="abort_cancel")):
        result = await orch._handle_stage_5_response(state, "算了", auto_continue=False)
    assert result.stage_status == "checkpoint"   # 停原地，未 complete
    assert result.current_stage == 5             # 未進 Stage 6
    parse_mock.assert_not_called()               # abort 攔在 _parse_revision_intent 之前


@pytest.mark.asyncio
async def test_stage5_abort_llm_failure_does_not_export():
    """meta helper 回 None（LLM 失敗）→ 不可放行匯出，fail-loud 停 checkpoint。"""
    orch = _orch()
    state = LiveResearchStageState(current_stage=5, stage_status="in_progress")
    state.written_sections = [{"section_index": 0, "title": "t", "content": "c"}]
    state.context_map_json = _make_context_map().model_dump_json()
    orch._parse_revision_intent = AsyncMock(return_value={"action": "done"})
    with patch("reasoning.live_research.orchestrator._classify_meta_intent",
               new=AsyncMock(return_value=None)):
        result = await orch._handle_stage_5_response(state, "算了", auto_continue=False)
    assert result.stage_status == "checkpoint"
    assert result.current_stage == 5


@pytest.mark.asyncio
async def test_stage5_substantive_falls_through_to_revise():
    """實質 revise 訴求 → helper 回 substantive → 照常進 _parse_revision_intent（零行為改變）。"""
    orch = _orch()
    state = LiveResearchStageState(current_stage=5, stage_status="in_progress")
    state.written_sections = [{"section_index": 0, "title": "t", "content": "c"}]
    state.context_map_json = _make_context_map().model_dump_json()
    parse_mock = AsyncMock(return_value={"action": "done"})  # 走到既有 done path
    orch._parse_revision_intent = parse_mock
    with patch("reasoning.live_research.orchestrator._classify_meta_intent",
               new=AsyncMock(return_value="substantive")):
        result = await orch._handle_stage_5_response(state, "第2段太短，補資料", auto_continue=False)
    parse_mock.assert_awaited_once()  # 確認有 fall through 到既有 LLM parse


@pytest.mark.asyncio
async def test_stage5_export_shortcut_still_bypasses_meta_helper():
    """regression：純『匯出』frozenset shortcut 仍即時放行，不打 meta helper（#14 安全契約）。
    LR #11B 對齊：匯出需全寫完。total=1，last_completed=0 → remaining=0 → 放行。
    """
    orch = _orch()
    state = LiveResearchStageState(current_stage=5, stage_status="in_progress")
    state.written_sections = [{"section_index": 0, "title": "t", "content": "c"}]
    state.context_map_json = _make_context_map().model_dump_json()
    state.last_completed_section_index = 0  # total=1, index=0 → remaining=0 → export allowed
    meta_mock = AsyncMock(return_value="substantive")
    with patch("reasoning.live_research.orchestrator._classify_meta_intent", new=meta_mock):
        result = await orch._handle_stage_5_response(state, "匯出", auto_continue=False)
    assert result.stage_status == "completed"  # export shortcut → complete_stage
    meta_mock.assert_not_called()              # frozenset shortcut 在 meta helper 之前


@pytest.mark.asyncio
async def test_stage5_accept_shortcut_exports():
    """『接受』→ export shortcut → complete_stage（abort confirm follow-up path）。
    LR #11B 對齊：匯出需全寫完。total=1，last_completed=0 → remaining=0 → 放行。
    """
    orch = _orch()
    state = LiveResearchStageState(current_stage=5, stage_status="in_progress")
    state.written_sections = [{"section_index": 0, "title": "t", "content": "c"}]
    state.context_map_json = _make_context_map().model_dump_json()
    state.last_completed_section_index = 0  # total=1, index=0 → remaining=0 → export allowed
    meta_mock = AsyncMock(return_value="substantive")
    with patch("reasoning.live_research.orchestrator._classify_meta_intent", new=meta_mock):
        result = await orch._handle_stage_5_response(state, "接受", auto_continue=False)
    assert result.stage_status == "completed"   # 「接受」整句完全匹配 → export
    meta_mock.assert_not_called()               # frozenset shortcut 在 meta helper 之前


@pytest.mark.asyncio
async def test_stage5_accept_with_extra_content_does_not_export():
    """『接受但第2段再短一點』整句 != 「接受」→ 不命中 export shortcut，fall through（不誤匯出）。"""
    orch = _orch()
    state = LiveResearchStageState(current_stage=5, stage_status="in_progress")
    state.written_sections = [{"section_index": 0, "title": "t", "content": "c"}]
    state.context_map_json = _make_context_map().model_dump_json()
    # 整句「接受但第2段再短一點」≠ 「接受」→ fall through → 打 meta helper → substantive → parse
    parse_mock = AsyncMock(return_value={"action": "done"})
    orch._parse_revision_intent = parse_mock
    with patch("reasoning.live_research.orchestrator._classify_meta_intent",
               new=AsyncMock(return_value="substantive")):
        result = await orch._handle_stage_5_response(state, "接受但第2段再短一點", auto_continue=False)
    # Must NOT be an export shortcut hit — meta helper must have been called
    # (no export shortcut hit → meta helper was reached)
    assert result.stage_status != "completed" or parse_mock.call_count > 0  # fall through happened


# ──── Task 4: Prompt contract ────

@pytest.mark.asyncio
async def test_meta_intent_prompt_contract():
    """鎖 prompt 契約：三類 + abort 優先 + 混合句歸 substantive 紀律不可退化。"""
    captured = {}

    async def _capture(prompt, schema, **kwargs):
        captured["prompt"] = prompt
        captured["schema"] = schema
        captured["level"] = kwargs.get("level")
        return {"category": "substantive", "reason": "x"}

    with patch("core.llm.ask_llm", new=_capture):
        await _classify_meta_intent("一些訊息", _handler())

    p = captured["prompt"]
    # 三類都在 prompt
    assert "skip_use_default" in p
    assert "abort_cancel" in p
    assert "substantive" in p
    # abort 最高優先紀律
    assert "算了" in p and ("優先" in p or "最高" in p)
    # 混合句歸 substantive
    assert "混合句" in p
    # 用 low model（成本契約）
    assert captured["level"] == "low"
    # schema enum 與常數一致
    assert set(captured["schema"]["properties"]["category"]["enum"]) == {
        "skip_use_default", "abort_cancel", "substantive",
    }


# ──── Task 5: DP-12 reconcile regression ────

from reasoning.live_research.orchestrator import _looks_like_confirm_proceed_shortcut


def test_dp12_compound_confirm_still_hits_substring():
    """DP-12 reconcile：compound confirm『確認。進入寫作。』必須命中（frozenset 會漏，故保留 substring）。"""
    assert _looks_like_confirm_proceed_shortcut("確認。進入寫作。") is True
    assert _looks_like_confirm_proceed_shortcut("OK 就這樣") is True


def test_dp12_abort_does_not_hit_confirm_shortcut():
    """DP-12：『算了』含 veto 詞 → 不命中 confirm shortcut（交 LLM，不誤判 confirm 推進）。"""
    assert _looks_like_confirm_proceed_shortcut("算了") is False
    assert _looks_like_confirm_proceed_shortcut("確認後幫我把第二章改短") is False  # confirm+adjust 混合


# ──── LR #11 Part B: 寫到一半時完全不給匯出路徑 ────
# _make_context_map() 產出 1 個 core topic → _total = 1
# incomplete state: last_completed_section_index = -1 (預設)   → remaining = 1
# complete state:   last_completed_section_index = 0 (total-1) → remaining = 0


@pytest.mark.asyncio
async def test_stage5_export_shortcut_blocked_when_sections_remain():
    """LR #11B：『匯出』+ 未寫完 → NOT completed，narration 含「還有 N 段」，不 complete_stage。"""
    orch = _orch()
    state = LiveResearchStageState(current_stage=5, stage_status="in_progress")
    state.written_sections = []
    state.context_map_json = _make_context_map().model_dump_json()
    # last_completed_section_index = -1 (default) → remaining = 1
    meta_mock = AsyncMock(return_value="substantive")
    with patch("reasoning.live_research.orchestrator._classify_meta_intent", new=meta_mock):
        result = await orch._handle_stage_5_response(state, "匯出", auto_continue=False)
    assert result.stage_status == "checkpoint"  # NOT completed — blocked
    assert result.current_stage == 5            # 未進 Stage 6
    meta_mock.assert_not_called()               # export shortcut 在 meta helper 之前，但被 block


@pytest.mark.asyncio
async def test_stage5_abort_blocked_prompt_when_sections_remain():
    """LR #11B：『算了』+ 未寫完 → prompt 含「繼續寫」「修改」，不含「接受」「匯出」。"""
    orch = _orch()
    state = LiveResearchStageState(current_stage=5, stage_status="in_progress")
    state.written_sections = []
    state.context_map_json = _make_context_map().model_dump_json()
    # last_completed_section_index = -1 (default) → remaining = 1
    parse_mock = AsyncMock(return_value={"action": "done"})
    orch._parse_revision_intent = parse_mock
    with patch("reasoning.live_research.orchestrator._classify_meta_intent",
               new=AsyncMock(return_value="abort_cancel")):
        result = await orch._handle_stage_5_response(state, "算了", auto_continue=False)
    assert result.stage_status == "checkpoint"    # 停原地
    assert result.current_stage == 5
    parse_mock.assert_not_called()                # abort 攔在 _parse_revision_intent 之前
    # checkpoint_prompt 必須含「繼續寫」或「修改」，且不含「接受」「匯出」
    prompt = result.checkpoint_prompt
    assert "繼續寫" in prompt or "修改" in prompt
    assert "接受" not in prompt
    assert "匯出" not in prompt


@pytest.mark.asyncio
async def test_stage5_export_shortcut_allowed_when_all_sections_complete():
    """LR #11B regression：所有段落寫完後，『匯出』→ completed（匯出放行）。"""
    orch = _orch()
    state = LiveResearchStageState(current_stage=5, stage_status="in_progress")
    state.written_sections = [{"section_index": 0, "title": "t", "content": "c"}]
    state.context_map_json = _make_context_map().model_dump_json()
    state.last_completed_section_index = 0  # total=1, index=0 → remaining=0 → complete
    meta_mock = AsyncMock(return_value="substantive")
    with patch("reasoning.live_research.orchestrator._classify_meta_intent", new=meta_mock):
        result = await orch._handle_stage_5_response(state, "匯出", auto_continue=False)
    assert result.stage_status == "completed"   # 全寫完 → 匯出放行
    meta_mock.assert_not_called()               # frozenset shortcut 在 meta helper 之前


@pytest.mark.asyncio
async def test_stage5_abort_complete_prompt_when_all_sections_done():
    """LR #11B regression：『算了』+ 全寫完 → prompt 含「接受」（維持現狀）。"""
    orch = _orch()
    state = LiveResearchStageState(current_stage=5, stage_status="in_progress")
    state.written_sections = [{"section_index": 0, "title": "t", "content": "c"}]
    state.context_map_json = _make_context_map().model_dump_json()
    state.last_completed_section_index = 0  # total=1, index=0 → remaining=0
    parse_mock = AsyncMock(return_value={"action": "done"})
    orch._parse_revision_intent = parse_mock
    with patch("reasoning.live_research.orchestrator._classify_meta_intent",
               new=AsyncMock(return_value="abort_cancel")):
        result = await orch._handle_stage_5_response(state, "算了", auto_continue=False)
    assert result.stage_status == "checkpoint"    # 停原地問確認
    assert result.current_stage == 5
    parse_mock.assert_not_called()
    # 全寫完 → prompt 含「接受」（原有行為保留）
    assert "接受" in result.checkpoint_prompt


# ──── LR auto: Stage 3 _run_style_analysis LLM 空回應 soft-fail ────


@pytest.mark.asyncio
async def test_run_style_analysis_empty_response_returns_none():
    """LLM 兩級都回空 → 回 None（不 raise ValueError），交由 caller soft-fail。"""
    orch = _orch()
    with patch("core.llm.ask_llm", new=AsyncMock(return_value=None)):
        result = await orch._run_style_analysis("這是一段文筆範本")
    assert result is None


@pytest.mark.asyncio
async def test_run_style_analysis_raises_when_not_a_sample():
    """O7：LLM 回 input_is_writing_sample=False → raise StyleInputNotASampleError。"""
    from reasoning.schemas_live import StyleInputNotASampleError

    orch = _orch()
    fake_resp = {
        "features": [{"dimension": "x", "observation": "o", "instruction": "i"}],
        "overall_tone": "t",
        "input_is_writing_sample": False,
    }
    with patch("core.llm.ask_llm", new=AsyncMock(return_value=fake_resp)):
        with pytest.raises(StyleInputNotASampleError):
            await orch._run_style_analysis("語氣再生動一點")


@pytest.mark.asyncio
async def test_run_style_analysis_normal_when_is_sample():
    """O7：input_is_writing_sample=True → 正常回 StyleAnalysisOutput（零行為改變）。"""
    from reasoning.schemas_live import StyleAnalysisOutput

    orch = _orch()
    fake_resp = {
        "features": [{"dimension": "句式", "observation": "o", "instruction": "i"}],
        "overall_tone": "t",
        "input_is_writing_sample": True,
    }
    with patch("core.llm.ask_llm", new=AsyncMock(return_value=fake_resp)):
        out = await orch._run_style_analysis("一段真的範本文章...")
    assert isinstance(out, StyleAnalysisOutput)
    assert out.input_is_writing_sample is True


@pytest.mark.asyncio
async def test_stage3_substantive_style_analysis_fail_soft_stays_checkpoint():
    """SUBSTANTIVE 範本 + style analysis 回 None（LLM 失敗）→ soft-fail：
    停 Stage 3 checkpoint、不寫 style_features_json、不 advance、不 raise，
    且降級 narration + checkpoint 必須真的 emit（不可 silent fail）。"""
    orch = _orch()
    state = LiveResearchStageState(current_stage=3, stage_status="in_progress")
    orch._run_style_analysis = AsyncMock(return_value=None)
    orch._emit_narration = AsyncMock()
    orch._emit_checkpoint = AsyncMock()
    with patch("reasoning.live_research.orchestrator._classify_meta_intent",
               new=AsyncMock(return_value="substantive")):
        result = await orch._handle_stage_3_response(
            state, "這是我寫過的一段學術文字……", auto_continue=False
        )
    orch._run_style_analysis.assert_awaited_once()
    assert result.stage_status == "checkpoint"   # 停原地，未 advance
    assert result.current_stage == 3
    assert result.style_features_json == ""       # 沒寫入半成品
    # 不可 silent fail：user-facing 降級訊息 + checkpoint 重 emit 必須真的發生
    narrations = [c.args[0] for c in orch._emit_narration.await_args_list]
    assert lr_copy.LLM_UNAVAILABLE_NARRATION in narrations
    orch._emit_checkpoint.assert_awaited_once()
    assert orch._emit_checkpoint.await_args.kwargs.get("stage") == 3


@pytest.mark.asyncio
async def test_stage3_first_reply_non_sample_degrades_and_holds():
    """O7：首次回覆 substantive 但其實是指令 → _run_style_analysis raise sentinel →
    不寫 style_features_json、停 checkpoint、emit O7 專屬降級 narration。"""
    from reasoning.live_research import lr_copy
    from reasoning.schemas_live import StyleInputNotASampleError

    orch = _orch()
    state = LiveResearchStageState(current_stage=3, stage_status="in_progress")
    orch._run_style_analysis = AsyncMock(side_effect=StyleInputNotASampleError("x"))
    orch._emit_narration = AsyncMock()
    orch._emit_checkpoint = AsyncMock()
    with patch("reasoning.live_research.orchestrator._classify_meta_intent",
               new=AsyncMock(return_value="substantive")):
        result = await orch._handle_stage_3_response(
            state, "語氣再生動一點", auto_continue=False
        )
    assert result.stage_status == "checkpoint"   # 停原地，未 advance
    assert result.current_stage == 3
    assert result.style_features_json == ""       # 不覆蓋（保持空）
    narrations = [c.args[0] for c in orch._emit_narration.await_args_list]
    assert lr_copy.STYLE_INPUT_NOT_SAMPLE_FIRST_NARRATION in narrations
    orch._emit_checkpoint.assert_awaited_once()
    assert orch._emit_checkpoint.await_args.kwargs.get("stage") == 3


@pytest.mark.asyncio
async def test_stage3_redo_non_sample_degrades_keeps_existing():
    """O7：已有分析、判為 redo，但輸入其實是指令 → raise sentinel →
    保留既有分析、停 checkpoint、emit O7 redo 降級 narration。

    INTERIM TEST — redo 終局 plan（lr-auto-stage3-redo-reprompt-plan 或
    lr-stage3-new-sample-button-plan 後端 A）land 後，redo 分支不再呼叫
    _run_style_analysis，本 test 必紅。屆時**直接刪除本 test**（行為改由
    該 plan 的新 test 護），不要把它修綠。詳見 o7 plan 協調節。
    """
    from reasoning.live_research import lr_copy
    from reasoning.schemas_live import StyleInputNotASampleError, StyleAnalysisOutput, StyleFeature

    existing = StyleAnalysisOutput(
        features=[StyleFeature(dimension="句式", observation="o", instruction="i")],
        overall_tone="原語氣",
    ).model_dump_json()

    orch = _orch()
    state = LiveResearchStageState(
        current_stage=3, stage_status="in_progress", style_features_json=existing,
    )
    orch._run_style_analysis = AsyncMock(side_effect=StyleInputNotASampleError("x"))
    orch._emit_narration = AsyncMock()
    orch._emit_checkpoint = AsyncMock()
    orch._parse_style_confirmation_intent = AsyncMock(return_value={"action": "redo"})

    result = await orch._handle_stage_3_response(state, "不對重來", auto_continue=False)

    assert result.stage_status == "checkpoint"
    assert result.current_stage == 3
    assert result.style_features_json == existing  # 既有分析未被覆蓋
    narrations = [c.args[0] for c in orch._emit_narration.await_args_list]
    assert lr_copy.STYLE_INPUT_NOT_SAMPLE_REDO_NARRATION in narrations
    orch._emit_checkpoint.assert_awaited_once()
    assert orch._emit_checkpoint.await_args.kwargs.get("stage") == 3


@pytest.mark.asyncio
async def test_stage3_redo_style_analysis_fail_soft_stays_checkpoint():
    """已有分析、user 選 redo、LLM 空回應（回 None）→ soft-fail：
    停 Stage 3 checkpoint、保留既有 style_features_json 不覆蓋、不 raise，
    且降級 narration + checkpoint 必須真的 emit（不可 silent fail）。

    INTERIM TEST — lr-auto-stage3-redo-reprompt-plan land 後，redo 分支不再
    呼叫 _run_style_analysis，本 test 必紅。屆時**直接刪除本 test**（行為改由
    該 plan 的新 test 護），不要把它修綠。詳見 soft-fail plan Task 2b 協調節。
    """
    from reasoning.schemas_live import StyleAnalysisOutput, StyleFeature
    orch = _orch()
    # 模擬已有分析
    existing = StyleAnalysisOutput(
        overall_tone="學術正式",
        features=[StyleFeature(dimension="語氣", observation="正式", instruction="維持")],
    )
    state = LiveResearchStageState(
        current_stage=3,
        stage_status="in_progress",
        style_features_json=existing.model_dump_json(),
    )
    orch._run_style_analysis = AsyncMock(return_value=None)
    orch._emit_narration = AsyncMock()
    orch._emit_checkpoint = AsyncMock()
    # _parse_style_confirmation_intent 回 redo action
    orch._parse_style_confirmation_intent = AsyncMock(return_value={"action": "redo"})

    result = await orch._handle_stage_3_response(
        state, "重新分析吧", auto_continue=False
    )
    orch._run_style_analysis.assert_awaited_once()
    assert result.stage_status == "checkpoint"   # 停原地，未 advance
    assert result.current_stage == 3
    # 既有分析不被空結果覆蓋
    assert result.style_features_json == existing.model_dump_json()
    # 不可 silent fail：user-facing 降級訊息 + checkpoint 重 emit 必須真的發生
    narrations = [c.args[0] for c in orch._emit_narration.await_args_list]
    assert lr_copy.LLM_UNAVAILABLE_NARRATION in narrations
    orch._emit_checkpoint.assert_awaited_once()
    assert orch._emit_checkpoint.await_args.kwargs.get("stage") == 3
