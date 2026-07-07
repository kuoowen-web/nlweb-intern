# test_lr_recollect_dispatch.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from reasoning.live_research.orchestrator import LiveResearchOrchestrator
from reasoning.live_research.stage_state import LiveResearchStageState
from reasoning.schemas_live import ContextMap


def _make_orch():
    orch = LiveResearchOrchestrator.__new__(LiveResearchOrchestrator)
    orch.dry_run = False
    orch.features = {}  # _recollect_cap 讀此 → default 2
    orch.handler = MagicMock(); orch.handler.query_params = {}
    orch._emit_narration = AsyncMock()
    orch._emit_checkpoint = AsyncMock()
    orch._persist_checkpoint_boundary = AsyncMock()
    orch._run_stage_5 = AsyncMock()
    orch._run_stage_1 = AsyncMock(side_effect=lambda s, *a, **k: s)
    orch._stage5_remaining_count = MagicMock(return_value=0)
    orch._resolve_chapter_source = MagicMock(return_value=([], False))
    return orch


def _make_state():
    state = LiveResearchStageState()
    state.current_stage = 5
    state.stage_status = "checkpoint"
    state.written_sections = [{"section_index": 0, "title": "舊"}]
    state.last_completed_section_index = 0
    # E (in-house C-1)：evidence_id 必填，缺 → deserialize_evidence_pool ValidationError
    # 炸在進 reset 前（拿錯 traceback）。EvidencePoolEntry.evidence_id 是 required 欄位。
    state.evidence_pool_json = '{"1": {"evidence_id": 1, "url": "u", "title": "t"}}'
    # 必設：dispatch deserialize ContextMap，否則炸在 model_validate_json
    state.context_map_json = ContextMap(
        research_question="台灣光電", topics=[]
    ).model_dump_json()
    return state


def test_recollect_user_first_round_emits_consent_not_reset():
    orch = _make_orch()
    state = _make_state()
    orch._parse_revision_intent = AsyncMock(
        return_value={"action": "recollect", "reason": "資料不足"})
    # 既有 Stage 5 meta-intent abort guard 在 _parse_revision_intent 之前先打 LLM；
    # 「資料不夠去多查」在 prod 被判 substantive（非 abort）→ 續行到 _parse_revision_intent。
    # 測試環境無 LLM key → patch 成 substantive 對齊 prod 行為（不弱化斷言）。
    with patch("reasoning.live_research.orchestrator._classify_meta_intent",
               new=AsyncMock(return_value="substantive")):
        result = asyncio.run(
            orch._handle_stage_5_response(state, "資料不夠去多查", False))
    # 第一輪：只 emit consent，設 pending flag，不 reset、不呼 _run_stage_1
    assert result.pending_recollect_confirmation is True
    assert result.current_stage == 5
    assert result.written_sections != []
    orch._run_stage_1.assert_not_awaited()
    assert result.stage_status == "checkpoint"


def test_recollect_user_confirm_round_resets_and_reenters_stage1():
    orch = _make_orch()
    state = _make_state()
    state.pending_recollect_confirmation = True  # 已在等確認
    # 強確認詞 → 不需打 _classify_meta_intent（_looks_like_recollect_confirm 先命中）
    result = asyncio.run(
        orch._handle_stage_5_response(state, "確認", False))
    # 第二輪「確認」：reset + 退 Stage 1 + 呼 _run_stage_1 帶 seed
    assert result.current_stage == 1
    assert result.written_sections == []
    assert result.pending_recollect_confirmation is False
    assert result.recollect_count == 1
    orch._run_stage_1.assert_awaited_once()
    _, kwargs = orch._run_stage_1.call_args
    assert "seed_evidence_pool" in kwargs and "seed_counter" in kwargs
    assert kwargs["seed_counter"] == 1  # max(pool keys)=1


def test_recollect_pending_substantive_falls_through_not_swallowed():
    """A (3方共識)：pending=True 時 user 回非確認/非取消的實質訴求（「改第3段」）
    → 清 pending、fall through 到既有 dispatch（_parse_revision_intent 正常路由），
    **不被當取消靜默丟棄**。違反 LR「不漏使用者任何一句話」鐵律是 v1 死因。

    驗法：mock _parse_revision_intent 回自包含的 structure_change 分支（emit
    narration + checkpoint + return，不碰 writer 內部），斷言它**被 await**（user
    的話有被既有路由接住）+ 未走 recollect dispatch（_run_stage_1 未呼）。
    用 structure_change 避免依賴 revise_section inline 寫作內部（_write_section 等）。"""
    orch = _make_orch()
    state = _make_state()
    state.pending_recollect_confirmation = True
    orch._parse_revision_intent = AsyncMock(
        return_value={"action": "structure_change", "reason": "改結構"})
    with patch("reasoning.live_research.orchestrator._classify_meta_intent",
               new=AsyncMock(return_value="substantive")):
        result = asyncio.run(
            orch._handle_stage_5_response(state, "改第3段", False))
    # pending 清掉、未走 recollect dispatch、user 訴求被 _parse_revision_intent 接住
    assert result.pending_recollect_confirmation is False
    orch._run_stage_1.assert_not_awaited()
    orch._parse_revision_intent.assert_awaited()  # 訴求被正常路由，非被吞


def test_recollect_pending_explicit_cancel_returns_to_checkpoint():
    """A/B：pending=True 時明確取消（_classify_meta_intent=abort_cancel）→ 清 pending、
    不 reset、回常規 Stage 5 checkpoint，narration 用 RECOLLECT_CANCELLED_NARRATION。"""
    orch = _make_orch()
    state = _make_state()
    state.pending_recollect_confirmation = True
    with patch("reasoning.live_research.orchestrator._classify_meta_intent",
               new=AsyncMock(return_value="abort_cancel")):
        result = asyncio.run(
            orch._handle_stage_5_response(state, "算了不用了", False))
    assert result.pending_recollect_confirmation is False
    assert result.current_stage == 5
    assert result.written_sections != []  # 沒清章節
    orch._run_stage_1.assert_not_awaited()


def test_recollect_pending_meta_none_stays_at_checkpoint_no_dispatch():
    """B: pending=True 時 _classify_meta_intent 回傳 None (LLM 故障)
    → 絕不能 fall through 或走 bounded affirmative，必須重設 pending flag，
    emit narration，並 emit 原本的 RECOLLECT_CONSENT_PROMPT checkpoint。"""
    import reasoning.live_research.lr_copy as lr_copy
    orch = _make_orch()
    state = _make_state()
    state.pending_recollect_confirmation = True
    
    # 短肯定形狀句，原本若 meta=None，在 abort 檢查時會 bypass，然後在段3 命中 affirmative shape
    user_message = "嗯"
    
    with patch("reasoning.live_research.orchestrator._classify_meta_intent",
               new=AsyncMock(return_value=None)):
        result = asyncio.run(
            orch._handle_stage_5_response(state, user_message, False))
            
    # 斷言：未呼叫 _run_stage_1（沒 dispatch），flag 被重設為 True（讓 user 再確認）
    assert result.pending_recollect_confirmation is True
    assert result.current_stage == 5
    assert result.written_sections != []  # 沒清章節
    orch._run_stage_1.assert_not_awaited()
    # 斷言 emit_narration 用了 LLM_UNAVAILABLE_NARRATION
    orch._emit_narration.assert_awaited_with(lr_copy.LLM_UNAVAILABLE_NARRATION)
    # 斷言 checkpoint set 了 RECOLLECT_CONSENT_PROMPT
    assert result.checkpoint_prompt == lr_copy.RECOLLECT_CONSENT_PROMPT



def test_recollect_capped_blocks_at_intent_and_narrates():
    """cap 預檢在 recollect intent 分支（consent 之前）：已達 cap → 直接 block，
    不進 consent、不設 pending、明確 narration（非 silent）。"""
    orch = _make_orch()
    state = _make_state()
    state.recollect_count = 2  # 已達 cap
    orch._parse_revision_intent = AsyncMock(
        return_value={"action": "recollect", "reason": "資料不足"})
    # 同上：patch meta-intent guard 成 substantive 對齊 prod（無 LLM key 測試環境）
    with patch("reasoning.live_research.orchestrator._classify_meta_intent",
               new=AsyncMock(return_value="substantive")):
        result = asyncio.run(
            orch._handle_stage_5_response(state, "資料不夠去多查", False))
    # cap：不 reset、不退 Stage 1、不設 pending，明確 narration
    orch._run_stage_1.assert_not_awaited()
    assert result.current_stage == 5
    assert result.pending_recollect_confirmation is False
    assert result.recollect_count == 2
    narrated = " ".join(str(c.args[0]) for c in orch._emit_narration.call_args_list)
    assert "兩輪" in narrated or "上限" in narrated or "重新規劃" in narrated


def test_looks_like_recollect_confirm_bounded_affirmatives():
    """K（Codex+in-house）：含 token 的 bounded affirmative parser（段1）—— 含確認 token
    的自然短肯定句命中 confirm（不落 substantive → 不二次 consent loop），含實質修改名詞 /
    過長 / 無 token 的句子不命中（無 token 者交段3 兜底）。"""
    from reasoning.live_research.orchestrator import _looks_like_recollect_confirm
    # 含確認 token 的短肯定句 → 命中（段1 快路徑，避免二次 consent loop）
    assert _looks_like_recollect_confirm("確認")
    assert _looks_like_recollect_confirm("OK。")
    assert _looks_like_recollect_confirm("好，開始吧")
    assert _looks_like_recollect_confirm("可以，請重新蒐集")  # 「重新蒐集」非修改 marker，「可以」命中
    assert _looks_like_recollect_confirm("確定")
    assert _looks_like_recollect_confirm("沒問題")
    # 含實質修改名詞 → 不命中（走 substantive fall through，不吞 user 訴求）
    assert not _looks_like_recollect_confirm("改第3段")
    assert not _looks_like_recollect_confirm("資料還是不夠，連經濟面也查")
    assert not _looks_like_recollect_confirm("好，但第2章標題要換")
    # K Round 4：無確認 token 的自然肯定句在**段1 不命中**（交段3 兜底，非段1）
    assert not _looks_like_recollect_confirm("好，那就重新蒐集吧")  # 「好」非 token、「重新蒐集」非 token
    assert not _looks_like_recollect_confirm("是的")
    assert not _looks_like_recollect_confirm("行")
    # 空 / 純標點 → 不命中
    assert not _looks_like_recollect_confirm("")
    assert not _looks_like_recollect_confirm("。。。")


def test_looks_like_bounded_affirmative_shape_no_token_naturals():
    """K Round 4（in-house R3 終驗）：無 token 的 bounded affirmative 形狀 parser（段3）。
    無確認 token 的自然肯定句命中（在 consent gate 內 + 已過 abort = 確認形狀），
    含修改 marker / 過長 → 不命中（留段4 substantive）。

    注意：本函式**不負責**排除 abort（「算了」也是無 marker 短句 → 會命中）。
    abort 由段2 的 _classify_meta_intent 先行攔截，故 production 路徑「算了」走不到段3。
    這裡只測「形狀」判定本身（非 abort 區分）。"""
    from reasoning.live_research.orchestrator import _looks_like_bounded_affirmative_shape
    # 無 token 自然肯定句 → 命中（修掉 K 漏接：之前落 substantive → 二次 consent loop）
    assert _looks_like_bounded_affirmative_shape("好，那就重新蒐集吧")
    assert _looks_like_bounded_affirmative_shape("是的")
    assert _looks_like_bounded_affirmative_shape("行")
    assert _looks_like_bounded_affirmative_shape("成")
    assert _looks_like_bounded_affirmative_shape("麻煩了")
    assert _looks_like_bounded_affirmative_shape("嗯")
    # 含實質修改名詞 → 不命中（B 原罪防護：留段4 fall through，不誤觸刪章）
    assert not _looks_like_bounded_affirmative_shape("改第3段")
    assert not _looks_like_bounded_affirmative_shape("資料還是不夠，連經濟面也查")
    assert not _looks_like_bounded_affirmative_shape("好，但第2章標題要換")
    # 過長 → 不命中（夾帶實質內容，保守走段4）
    assert not _looks_like_bounded_affirmative_shape("好的我想想看到底要不要重新蒐集這部分的資料呢")
    # 空 / 純標點 → 不命中
    assert not _looks_like_bounded_affirmative_shape("")
    assert not _looks_like_bounded_affirmative_shape("。。。")


def test_recollect_pending_natural_affirmative_no_second_consent_loop():
    """K：pending=True 時 user 回**含 token** 的短肯定句（「好，開始吧」靠「開始」）→
    段1 _looks_like_recollect_confirm 命中 → 直接 dispatch（不打 _classify_meta_intent、
    不重 parse 成 recollect → 不二次 consent）。"""
    orch = _make_orch()
    state = _make_state()
    state.pending_recollect_confirmation = True
    # 若誤落 substantive 重 parse，_parse_revision_intent 會被呼叫 → 用 spy 抓
    orch._parse_revision_intent = AsyncMock(
        return_value={"action": "recollect", "reason": "x"})
    result = asyncio.run(
        orch._handle_stage_5_response(state, "好，開始吧", False))
    # 段1 短肯定 → 直接 dispatch reset + 退 Stage 1，未重 parse（無二次 consent）
    assert result.current_stage == 1
    assert result.pending_recollect_confirmation is False
    orch._run_stage_1.assert_awaited_once()
    orch._parse_revision_intent.assert_not_awaited()  # 沒重新 parse → 沒二次 consent loop


def test_recollect_pending_no_token_natural_affirmative_via_stage3():
    """K Round 4（in-house R3 終驗）：pending=True 時 user 回**無 token** 的自然肯定句
    （「好，那就重新蒐集吧」）→ 段1 不命中（無 token）→ 段2 _classify_meta_intent 回非 abort
    →段3 _looks_like_bounded_affirmative_shape 命中 → 直接 dispatch。

    這正是 K 漏接的標的：之前無 token 句落 substantive fall through → _parse_revision_intent
    因含「重新蒐集」重 parse 成 recollect → recollect 分支再設 pending + 再 emit consent =
    二次 consent loop。本 test 守住「無 token 自然肯定句不再二次 consent」。"""
    orch = _make_orch()
    state = _make_state()
    state.pending_recollect_confirmation = True
    # 若誤落段4 substantive fall through 重 parse，_parse_revision_intent 會被呼叫 → spy 抓
    orch._parse_revision_intent = AsyncMock(
        return_value={"action": "recollect", "reason": "x"})
    # 段2：abort 分類器對「好，那就重新蒐集吧」回 substantive（非 abort）→ 進段3
    with patch("reasoning.live_research.orchestrator._classify_meta_intent",
               new=AsyncMock(return_value="substantive")):
        result = asyncio.run(
            orch._handle_stage_5_response(state, "好，那就重新蒐集吧", False))
    # 段3 命中 → 直接 dispatch reset + 退 Stage 1，未走段4 重 parse（無二次 consent）
    assert result.current_stage == 1
    assert result.written_sections == []
    assert result.pending_recollect_confirmation is False
    assert result.recollect_count == 1
    orch._run_stage_1.assert_awaited_once()
    orch._parse_revision_intent.assert_not_awaited()  # 無二次 consent loop


def test_recollect_pending_short_yes_via_stage3():
    """K Round 4：pending=True 時 user 回「是的」（無 token、無 marker、短）→ 段3 命中
    confirm（abort 分類器回非 abort）→ dispatch，不二次 consent。"""
    orch = _make_orch()
    state = _make_state()
    state.pending_recollect_confirmation = True
    orch._parse_revision_intent = AsyncMock(
        return_value={"action": "recollect", "reason": "x"})
    with patch("reasoning.live_research.orchestrator._classify_meta_intent",
               new=AsyncMock(return_value="substantive")):
        result = asyncio.run(
            orch._handle_stage_5_response(state, "是的", False))
    assert result.current_stage == 1
    assert result.pending_recollect_confirmation is False
    orch._run_stage_1.assert_awaited_once()
    orch._parse_revision_intent.assert_not_awaited()


def test_dispatch_recollect_rolls_back_on_run_stage_1_failure():
    """I (Codex #7)：reset + count 已 commit 後 _run_stage_1 失敗 → rollback 到入口
    snapshot（章節 + count 還原），emit error checkpoint，不留半重置 broken state。"""
    orch = _make_orch()
    state = _make_state()
    state.recollect_count = 0
    orch._run_stage_1 = AsyncMock(side_effect=RuntimeError("BAB 炸了"))
    result = asyncio.run(orch._dispatch_recollect(state))
    # rollback：章節還原、count 還原、stage 回 5（非半重置的 stage=1 + 空章節）
    assert result.written_sections != []
    assert result.recollect_count == 0
    assert result.current_stage == 5
    # 明確 error narration（非 silent）
    narrated = " ".join(str(c.args[0]) for c in orch._emit_narration.call_args_list)
    assert "問題" in narrated or "保留" in narrated
