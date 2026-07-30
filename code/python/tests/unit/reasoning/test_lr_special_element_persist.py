"""LR _handle_stage_4_response add_special_element 全命中 happy path persist 補洞回歸鎖
（plan: lr-special-element-persist，backlog 2026-07-28-a）。

修復意圖：user 說「在某章加表格」且章名 layer1 精確命中（confirm/clarify pending
兩者皆空）→ 全命中 happy path 寫 special_elements(:3748) + pending_format_confirmation
=True(:3757) 後，只 _emit_checkpoint + return，**未 persist** → 斷線/reload 後表格
設定與 pending flag 遺失。同 handler confirm 分支(:3768)/clarify 分支(:3786)皆有
persist，唯獨全命中 happy path 漏（靜態可斷言的不對稱，同型先例 merge 718ecaa9）。

靜態 sweep test（test_lr_checkpoint_persist_coverage）抓不到本 bug：它只從
set_checkpoint/complete_stage 往回錨 durable boundary，本 leaked 分支走
_emit_checkpoint 不走 set_checkpoint，故逃過檢查。此檔用 behavioral 斷言鎖行為，
且斷言整條 persist chain 落到 handler._save_state（既有 add_special_element dispatch
test 有 mock _save_state 但從不斷言它被 await，正是本 bug 漏網主因）。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from reasoning.live_research.orchestrator import LiveResearchOrchestrator
from reasoning.live_research.stage_state import LiveResearchStageState
from reasoning.schemas_live import (
    ContextMap,
    Stage4Response,
    Stage4ResponseAction,
    Stage4FormatPayload,
    SpecialElementSpec,
    Stage4StructuralPayload,
    ChapterSpec,
)


def _base_orch():
    """real init（非 __new__）：走真實 _persist_checkpoint_boundary → _persist_progress
    → handler._save_state chain，好斷言整條 persist 真的落到 handler。
    handler 無 connection_alive_event → offline 判定為 online（alive is None），
    persist 走一般路徑（見 _persist_checkpoint_boundary :5179-5181）。"""
    handler = MagicMock()
    handler._save_state = AsyncMock()   # persist chain 終點；斷言它被 await = 真存到 DB 層
    handler.query_params = {}
    handler.message_sender = MagicMock()
    orch = LiveResearchOrchestrator(handler=handler)
    orch._emit_checkpoint = AsyncMock()
    orch._emit_narration = AsyncMock()
    orch._emit_clarification = AsyncMock(side_effect=lambda req, st: st)
    return orch, handler


def _stage4_checkpoint_state(*, chapter_name="結果與討論"):
    """已進 Stage 4 checkpoint 的 state：有效 context_map + format_specs.chapters override
    含目標章名 → add_special_element 的 target exact 命中 layer1 → 全命中 happy path。"""
    state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")
    state.set_checkpoint("格式詢問 prompt（Stage 4）")
    state.context_map_json = ContextMap(research_question="q").model_dump_json()
    state.format_specs = {"chapters": [{"name": chapter_name, "outline": ""}]}
    return state


def _add_table_response(target_chapter="結果與討論"):
    return Stage4Response(
        action=Stage4ResponseAction.add_special_element,
        format_content=Stage4FormatPayload(
            special_elements=[SpecialElementSpec(
                type="table", target_chapter=target_chapter, description="比較表"
            )],
        ),
    )


@pytest.mark.asyncio
async def test_add_special_element_all_hit_persists():
    """S4：add_special_element 全命中 happy path（章名 layer1 exact 命中、無 pending）→
    寫 special_elements + pending_format_confirmation=True，return 前必須 persist
    （否則斷線/reload 後表格設定與 pending flag 遺失 — 票核心後果）。"""
    orch, handler = _base_orch()
    orch._classify_stage_4_response = AsyncMock(return_value=_add_table_response("結果與討論"))
    state = _stage4_checkpoint_state(chapter_name="結果與討論")

    result = await orch._handle_stage_4_response(
        state, "比較表加到結果與討論章節裡", auto_continue=False,
    )

    # mutation 已發生（全命中直接寫入，不落 pending）
    assert result.format_specs["special_elements"][0]["type"] == "table"
    assert result.format_specs["special_elements"][0]["target_chapter"] == "結果與討論"
    assert result.pending_format_confirmation is True
    assert not result.pending_special_element_json     # 全命中不進 pending
    assert result.stage_status == "checkpoint"          # 不 advance
    # ← 修復前這裡會 FAIL：happy path 未 persist，整條 chain 未觸 _save_state
    handler._save_state.assert_awaited()


@pytest.mark.asyncio
async def test_reframe_entry_parse_fail_persists_bj_mutations():
    """T-fail（AR R1 Codex blocker）：B-j（adjust_chapters）委派前寫 citation_style /
    special_elements / target_word_count（:3813-3828，無 persist）→ 委派
    _try_stage_4_reframe_entry_typed 撞 context_map parse fail（:4335-4343）→
    narration + re-emit checkpoint + return，無 persist、caller early return 無兜底
    → 三項格式偏好遺失。修復 = parse-fail return 前補 persist。"""
    orch, handler = _base_orch()
    resp = Stage4Response(
        action=Stage4ResponseAction.adjust_chapters,
        structural_content=Stage4StructuralPayload(
            new_chapters=[ChapterSpec(name="新第一章"), ChapterSpec(name="新第二章")],
            summary="重組為兩章",
        ),
        format_content=Stage4FormatPayload(
            special_elements=[SpecialElementSpec(type="table", target_chapter="新第一章", description="比較表")],
            target_word_count=5000,
        ),
    )
    orch._classify_stage_4_response = AsyncMock(return_value=resp)
    state = _stage4_checkpoint_state(chapter_name="結果與討論")
    state.context_map_json = "{not valid json"   # 強制 parse fail 走 T-fail 分支

    result = await orch._handle_stage_4_response(state, "重組成兩章並加比較表約五千字", auto_continue=False)

    # B-j 前置 mutation 已發生（:3813-3828）
    assert result.format_specs["special_elements"][0]["type"] == "table"
    assert result.format_specs["target_word_count"] == 5000
    assert result.user_voice.target_word_count == 5000
    assert result.stage_status == "checkpoint"          # parse fail 停在 checkpoint
    orch._emit_checkpoint.assert_awaited()               # re-emit 原 checkpoint
    # ← 修復前這裡會 FAIL：T-fail 未 persist，mutation 不落盤
    handler._save_state.assert_awaited()


@pytest.mark.asyncio
async def test_add_special_element_confirm_pending_still_persists():
    """S2 回歸鎖：target 對不上 layer1 但 LLM resolved clear → 落 confirm pending，
    分支自身既有 persist（:3768）未被本次改動破壞。"""
    orch, handler = _base_orch()
    # target「第二部分」非 exact/唯一 substring → layer1 回 None → 讀 LLM resolved
    resp = Stage4Response(
        action=Stage4ResponseAction.add_special_element,
        format_content=Stage4FormatPayload(
            special_elements=[SpecialElementSpec(
                type="table", target_chapter="第二部分", description="比較表",
                resolved_chapter_title="結果與討論", resolution_confidence="clear",
            )],
        ),
    )
    orch._classify_stage_4_response = AsyncMock(return_value=resp)
    state = _stage4_checkpoint_state(chapter_name="結果與討論")

    result = await orch._handle_stage_4_response(state, "表格放第二部分", auto_continue=False)

    assert result.pending_special_element_json          # 落 confirm pending
    assert "confirm" in result.pending_special_element_json
    handler._save_state.assert_awaited()                # S2 自身 persist（:3768）


@pytest.mark.asyncio
async def test_add_special_element_clarify_pending_still_persists():
    """S3 回歸鎖：target 對不上且 LLM uncertain → 落 clarify pending，
    分支自身既有 persist（:3786）未被本次改動破壞。"""
    orch, handler = _base_orch()
    resp = Stage4Response(
        action=Stage4ResponseAction.add_special_element,
        format_content=Stage4FormatPayload(
            special_elements=[SpecialElementSpec(
                type="table", target_chapter="某個對不上的章", description="比較表",
                resolved_chapter_title="", resolution_confidence="uncertain",
            )],
        ),
    )
    orch._classify_stage_4_response = AsyncMock(return_value=resp)
    state = _stage4_checkpoint_state(chapter_name="結果與討論")

    result = await orch._handle_stage_4_response(state, "表格放某個對不上的章", auto_continue=False)

    assert result.pending_special_element_json          # 落 clarify pending
    assert "clarify" in result.pending_special_element_json
    handler._save_state.assert_awaited()                # S3 自身 persist（:3786）


@pytest.mark.asyncio
async def test_add_special_element_empty_payload_no_mutation_no_persist():
    """S1 回歸鎖：add_special_element 但 payload 空（no special_elements）→ 早退 fallback，
    無 state mutation、re-emit checkpoint，**不 persist**（也不誤加）。"""
    orch, handler = _base_orch()
    orch._classify_stage_4_response = AsyncMock(return_value=Stage4Response(
        action=Stage4ResponseAction.add_special_element,
        format_content=Stage4FormatPayload(special_elements=[]),
    ))
    state = _stage4_checkpoint_state()

    result = await orch._handle_stage_4_response(state, "加個東西", auto_continue=False)

    assert result.stage_status == "checkpoint"
    orch._emit_narration.assert_awaited()               # fallback narration
    orch._emit_checkpoint.assert_awaited()              # re-emit checkpoint
    # 鎖「不誤加」——S1 無 durable mutation，executor 若誤在此補 persist，此行抓到
    handler._save_state.assert_not_awaited()
