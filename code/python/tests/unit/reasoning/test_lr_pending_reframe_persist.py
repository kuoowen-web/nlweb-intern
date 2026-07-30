"""LR _handle_pending_reframe confirm/cancel 分支 persist 補洞回歸鎖
（plan: lr-pending-reframe-persist，backlog 2026-07-16-e）。

修復意圖：confirm/cancel 分支改動 state（清 pending / 套 context_map）後
未呼叫 _persist_checkpoint_boundary → 跨 request load 到舊 state、幽靈
pending_reframe 重現。四個受影響分支（窮舉表 B3/B6/B7/B8）return 前補 persist。

靜態 sweep test（test_lr_checkpoint_persist_coverage）抓不到本 bug：它只從
set_checkpoint/complete_stage 往回錨 durable boundary，本 leaked 分支走
_emit_checkpoint 不走 set_checkpoint，故逃過檢查。此檔用 behavioral 斷言鎖行為。
"""
import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from reasoning.live_research.orchestrator import LiveResearchOrchestrator
from reasoning.live_research.stage_state import LiveResearchStageState
from reasoning.schemas_live import ContextMapRevisionOperation


def _reframe_op(n_chapters=3):
    """有效 reframe_structure op：new_chapters 供 confirm apply。"""
    chapters = [
        {"name": f"第{i}章", "description": f"desc{i}", "relevance": "core"}
        for i in range(1, n_chapters + 1)
    ]
    return ContextMapRevisionOperation(
        op_type="reframe_structure",
        new_chapters=chapters,
        new_research_question="重組後研究問題",
    )


def _cm_json():
    """最小合法 ContextMap（供 confirm 分支 model_validate_json）。"""
    return json.dumps({
        "research_question": "原問題",
        "topics": [
            {"topic_id": "t1", "name": "T1", "domain": "d", "relevance": "core",
             "evidence_ids": [], "description": ""},
        ],
        "working_hypothesis": "",
    })


def _pending_state(reframe_op, *, current_stage=4):
    """已設 pending_reframe_json、stage_status=checkpoint 的 state（模擬 load 回來）。"""
    s = LiveResearchStageState()
    s.current_stage = current_stage
    s.stage_status = "checkpoint"
    s.context_map_json = _cm_json()
    s.pending_reframe_json = reframe_op.model_dump_json()
    s.pending_reframe_proposal_markdown = "舊提案 markdown"
    s.checkpoint_prompt = "原 stage checkpoint prompt"
    s.format_specs = {}
    return s


def _base_orch(confirm_intent):
    """共用 orch：__new__ 繞 __init__，注入 AsyncMock 依賴。"""
    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    orch.dry_run = True
    orch._emit_narration = AsyncMock()
    orch._emit_checkpoint = AsyncMock()
    orch._persist_checkpoint_boundary = AsyncMock()
    orch._classify_confirmation_intent = AsyncMock(return_value=confirm_intent)
    # confirm keyword shortcut 用真實 module-level 函式，"OK" 會命中 → 直接 confirm；
    # 給 cancel 測試時用非 shortcut 訊息讓它 fall 到 _classify_confirmation_intent。
    return orch


@pytest.mark.asyncio
async def test_confirm_stage4_reframe_persists_after_apply():
    """B6：confirm + target_stage==4 → 套 reframe（改 context_map）+ 清 pending，
    return 前必須 persist（否則新結構不落 DB、幽靈 pending 攔下一句）。"""
    orch = _base_orch("confirm")
    orch._context_map_to_outline = MagicMock(return_value="outline")
    orch._format_delta_summary = MagicMock(return_value="delta")
    op = _reframe_op(3)
    state = _pending_state(op, current_stage=4)

    result = await orch._handle_pending_reframe(state, "OK", target_stage=4)

    assert result.pending_reframe_json == ""          # pending 清除
    assert "第1章" in result.context_map_json          # 新結構已套進 context_map
    orch._persist_checkpoint_boundary.assert_awaited()  # ← 修復前這裡會 FAIL


@pytest.mark.asyncio
async def test_cancel_stage4_clears_pending_and_persists():
    """B8：cancel + target_stage==4 → 清 pending + re-emit 原 checkpoint，
    return 前必須 persist（否則下一輪「OK」把已取消的 reframe apply 進去 — 票核心後果①）。"""
    orch = _base_orch("cancel")
    op = _reframe_op(3)
    state = _pending_state(op, current_stage=4)

    # 「不要了」非 confirm shortcut → 走 _classify_confirmation_intent（回 cancel）
    result = await orch._handle_pending_reframe(state, "不要了取消", target_stage=4)

    assert result.pending_reframe_json == ""                 # pending 清除
    assert result.pending_reframe_proposal_markdown == ""
    orch._persist_checkpoint_boundary.assert_awaited()        # ← 修復前 FAIL


@pytest.mark.asyncio
async def test_confirm_apply_rejected_clears_pending_and_persists(monkeypatch):
    """B3：confirm 但 reframe apply 被拒（mutated_cm is None）→ 清 pending + re-emit
    checkpoint，return 前必須 persist（同型漏；ts=1·4 無 caller 兜底）。"""
    orch = _base_orch("confirm")
    op = _reframe_op(3)
    state = _pending_state(op, current_stage=4)

    # 強制 mutation 引擎 reject（真實簽名 reject 時回 (None, None, warnings)），隔離 B3 分支。
    # B3 分支只讀 warnings[0]，mutated_cm is None 時不觸及 delta，故 delta=None 安全。
    import reasoning.live_research.orchestrator as orch_mod
    monkeypatch.setattr(
        orch_mod, "_apply_context_map_revisions",
        lambda cm, ops, summary: (None, None, ["模擬 reject"]),
    )

    result = await orch._handle_pending_reframe(state, "OK", target_stage=4)

    assert result.pending_reframe_json == ""            # 清 pending
    assert result.pending_reframe_proposal_markdown == ""
    orch._persist_checkpoint_boundary.assert_awaited()   # ← 修復前 FAIL


@pytest.mark.asyncio
async def test_cancel_stage2_no_incomplete_topic_persists():
    """B7:2087：cancel + target_stage==2 且無未完成 topic → 防呆 return。
    caller 對 stage_status==checkpoint 有兜底，但此分支自身補 persist 為防禦
    （不依賴 incoming stage_status；idempotent）。
    註（AR R1）：真實流程 caller 會再 persist 一次 = double-persist，經驗證無害——
    _persist_checkpoint_boundary 冪等 + offline 計數 per-call guard（:5171）不雙計；
    本 test 用 assert_awaited（at-least-once）不受影響。"""
    orch = _base_orch("cancel")
    orch._run_stage_2 = AsyncMock(side_effect=AssertionError("不該走 _run_stage_2"))
    orch._has_incomplete_core_topics = MagicMock(return_value=False)
    op = _reframe_op(3)
    state = _pending_state(op, current_stage=2)
    state.stage2_drift_paused = True

    result = await orch._handle_pending_reframe(state, "不要重組了", target_stage=2)

    assert result.pending_reframe_json == ""
    assert result.stage2_drift_paused is False      # 防呆分支顯式清 drift flag
    orch._persist_checkpoint_boundary.assert_awaited()   # ← 修復前 FAIL


@pytest.mark.asyncio
async def test_confirm_stage1_still_persists_via_complete_stage():
    """B4 回歸鎖：confirm target_stage==1 走 complete_stage → 既有 persist（:2011）
    不被本次改動影響（confirm 分支共用 mutation 段沒被誤動）。"""
    orch = _base_orch("confirm")
    op = _reframe_op(3)
    state = _pending_state(op, current_stage=1)

    result = await orch._handle_pending_reframe(state, "OK", target_stage=1)

    assert result.pending_reframe_json == ""
    assert result.stage_status == "completed"        # complete_stage 生效
    assert "第1章" in result.context_map_json          # 新結構已套
    orch._persist_checkpoint_boundary.assert_awaited()


@pytest.mark.asyncio
async def test_adjust_fallthrough_no_mutation_still_reemits():
    """B10 回歸鎖：adjust fall-through（既非 confirm 也非 cancel、非單章微調）
    無 state mutation、re-emit checkpoint 保留 pending，不需 persist（也不誤加）。"""
    orch = _base_orch("adjust")   # _classify_confirmation_intent 回非 confirm/cancel
    orch._parse_per_chapter_reframe_edit = AsyncMock(return_value=None)  # 非單章微調
    op = _reframe_op(3)
    state = _pending_state(op, current_stage=4)

    result = await orch._handle_pending_reframe(state, "嗯我再想想這個方向", target_stage=4)

    assert result.pending_reframe_json != ""          # pending 保留（不清）
    orch._emit_checkpoint.assert_awaited()             # re-emit 提案 checkpoint
    # 🔧 AR R1 Codex SF-3：鎖「不誤加」——executor 若誤在 B10 補 persist，此行抓到
    orch._persist_checkpoint_boundary.assert_not_awaited()
