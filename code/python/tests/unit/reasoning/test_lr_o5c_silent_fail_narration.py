"""O5-C：兩條既有靜默降級路徑（gap routing except / grounding+specificity
guard outer except）降級時必須各送一次 user-facing 讀豹旁白。
本檔只測「降級時有 narration」，不改降級行為（仍 non-fatal）。
真實 LLM 觸發為 prod-only（見 plan BLOCKED-BY-PROD-RUN）。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_gap_routing_failure_emits_narration():
    """gap routing 擲例外時，必須 emit 一次降級 narration（由真實 except 區塊保證）。"""
    from reasoning.live_research.loop_engine import BABLoopEngine

    engine = BABLoopEngine.__new__(BABLoopEngine)  # 不跑 __init__

    emitted = []

    async def fake_emit(text):
        emitted.append(text)

    engine._emit_narration = fake_emit

    # 讓 _process_gap_resolutions_lr 擲例外，驗 except 區塊有 emit
    async def boom(_resolutions):
        raise RuntimeError("simulated gap routing failure")

    engine._process_gap_resolutions_lr = boom

    # __new__ 不跑 __init__ → dedup flag 必須在 test 內手動補（對應 production 的
    # __init__ 兜底；同 O5-B flag 的「直呼 unit test」情境，見 loop_engine L89-93 註解）
    engine._gap_routing_degraded_narrated = False

    # 構造一個帶 gap_resolutions 的 analyst_output，且讓 KG merge 區塊不被觸發
    analyst_output = MagicMock()
    analyst_output.gap_resolutions = [MagicMock()]
    # state=None → KG merge 區塊的 self.state is not None 守門為 False，避免干擾
    engine.state = None

    # 驅動真實的 gap routing helper（Step 3 抽出）。
    await engine._run_gap_routing_phase(analyst_output)

    assert emitted, "gap routing 失敗必須 emit 一次降級 narration"
    assert any("補強" in t or "查證" in t for t in emitted)


@pytest.mark.asyncio
async def test_gap_routing_narration_deduped_per_run():
    """s3-4 三家收斂 should-fix：持續性失敗（如 429 貫穿 run）時，gap routing
    降級旁白 per-run 只 emit 一次（防每輪轟炸）。log 每輪照記，不受 flag 影響。
    對照同檔先例 test_mini_reasoning_degradation_narration_deduped_per_run（約 L2072）。"""
    from reasoning.live_research.loop_engine import BABLoopEngine

    engine = BABLoopEngine.__new__(BABLoopEngine)
    emitted = []

    async def fake_emit(text):
        emitted.append(text)

    engine._emit_narration = fake_emit

    async def boom(_resolutions):
        raise RuntimeError("simulated persistent gap routing failure")

    engine._process_gap_resolutions_lr = boom
    engine._gap_routing_degraded_narrated = False
    engine.state = None

    analyst_output = MagicMock()
    analyst_output.gap_resolutions = [MagicMock()]

    # 模擬同一 run 內連續三輪失敗（run_loop 每輪呼叫一次）
    await engine._run_gap_routing_phase(analyst_output)
    await engine._run_gap_routing_phase(analyst_output)
    await engine._run_gap_routing_phase(analyst_output)

    assert len(emitted) == 1, \
        f"降級旁白應 per-run 只 emit 一次（防轟炸），實際 {len(emitted)} 次：{emitted}"


# ---------------------------------------------------------------------------
# Task 2（CEO 2026-06-11 拍板解凍）：_write_section guard outer except 旁白
# ---------------------------------------------------------------------------

def _make_handler():
    h = MagicMock()
    h.query_params = {}
    h.site = "all"
    h.message_sender = MagicMock()
    h.message_sender.send_message = AsyncMock()
    return h


def _make_write_section_setup():
    """最小 _write_section 驅動資料（照 test_live_orchestrator.py
    TestGroundingEvidenceViewWiring._make_setup 先例縮減）。"""
    from reasoning.schemas_live import (
        ContextMap, ContextMapTopic, BookOutline, ChapterPlan,
        EvidencePoolEntry, GroundedClaim,
    )
    from reasoning.live_research.stage_state import LiveResearchStageState

    cm = ContextMap(
        research_question="q", version=0,
        topics=[ContextMapTopic(topic_id="t", name="n", domain="d",
                                relevance="core", description="d",
                                evidence_ids=[1])],
    )
    state = LiveResearchStageState()
    state.evidence_usage = {
        1: [GroundedClaim(
            claim="台南案場推綠能", reasoning_type="induction",
            confidence="high", source_topic="t", source_iteration=1,
        ).model_dump()]
    }
    pool = {1: EvidencePoolEntry(
        evidence_id=1, title="台南案場", url="u", snippet="台南案場細節",
    )}
    book_outline = BookOutline(chapters=[
        ChapterPlan(chapter_index=0, title="前言", brief="x",
                    planned_evidence_ids=[1], role="intro"),
        ChapterPlan(chapter_index=1, title="國內案例", brief="案例分析",
                    planned_evidence_ids=[1], role="body"),
    ], overall_arc="x", redundancy_warnings=[])
    return cm, state, pool, book_outline


async def _drive_write_section_with_guard_boom(monkeypatch, times: int):
    """monkeypatch entity_grounding_check 擲非-GroundingCheckUnavailable 例外
    （模擬 render/抽取/rewrite 等環節故障），驅動真實 _write_section `times` 次，
    回 (narrated, outputs)。GCU 已由三個內層 except 局部接走，到不了 outer
    except —— 故這裡用 RuntimeError 直擲，必走 outer except。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import LiveWriterSectionOutput

    cm, state, pool, book_outline = _make_write_section_setup()

    async def fake_compose(self, **kw):
        return LiveWriterSectionOutput(
            section_title=kw["section_title"],
            section_content="台南案場推動綠能，社區持續關注後續發展。" * 5,
            sources_used=[1], confidence_level="High", status="drafted",
        )

    async def guard_boom(**kwargs):
        raise RuntimeError("simulated guard machinery failure")

    async def llm_boom(*a, **kw):
        raise RuntimeError("no real LLM in unit tests")

    monkeypatch.setattr(
        "reasoning.agents.writer.WriterAgent.compose_section", fake_compose
    )
    # _write_section 內是 function-level import（呼叫時取 module attr）→ patch 有效
    monkeypatch.setattr(
        "reasoning.live_research.hallucination_guard.entity_grounding_check",
        guard_boom,
    )
    monkeypatch.setattr("core.llm.ask_llm", llm_boom)  # 兜底：不可打真 LLM

    handler = _make_handler()
    with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
        orch = LiveResearchOrchestrator(handler=handler)
    orch.dry_run = False
    # 禁真 LLM：publish gate 的 critic/TypeAgent 走 instructor 自有 client，
    # ask_llm patch 蓋不到 → 用 feature flag 關掉（gate 在被測 except 之後，
    # 與本測試斷言無關；F-AMB-7 short-circuit 是既有行為）。
    orch.features = dict(orch.features)
    orch.features["live_research_critic_publish_gate"] = False

    narrated = []

    async def fake_narrate(text):
        narrated.append(text)
    orch._emit_narration = fake_narrate

    kwargs = dict(
        context_map=cm, topic={"name": "國內案例", "outline": "案例分析"},
        style_features=None, format_specs={}, evidence_pool=pool,
        chapter_index=1, all_evidence_ids=[1],
        book_outline=book_outline, current_chapter_index=1,
        state=state, prior_used_entities=[],
    )
    outputs = []
    for _ in range(times):
        out, _corr = await orch._write_section(**kwargs)
        outputs.append(out)
    return narrated, outputs


@pytest.mark.asyncio
async def test_write_section_guard_exception_emits_narration(monkeypatch):
    """o5c Task 2：guard 區段擲非-GCU 例外被 outer except 吞時，必須補一次
    即時旁白（文案 = lr_copy 單一事實源），且降級仍 non-fatal（section 照常
    回傳、status 不變）。注意 2026-06-10 根因修正：publish gate 在 try 外照跑，
    文案斷言用 lr_copy 常數，不重複字面。"""
    from reasoning.live_research import lr_copy

    narrated, outputs = await _drive_write_section_with_guard_boom(monkeypatch, times=1)

    assert lr_copy.SECTION_GUARD_ERROR_NARRATION in narrated, (
        f"guard outer except 必須 emit 降級旁白；實際旁白={narrated}"
    )
    # non-fatal 行為不變：section 照常回傳、不被整章封鎖
    assert getattr(outputs[0], "status", "drafted") == "drafted"


@pytest.mark.asyncio
async def test_write_section_guard_exception_narration_deduped_per_run(monkeypatch):
    """o5c Task 2 dedup：同一 run（同 orchestrator instance）多章連續觸發
    outer except 時，旁白只 emit 一次（防多章轟炸）；log 每章照記不受影響。"""
    from reasoning.live_research import lr_copy

    narrated, _outputs = await _drive_write_section_with_guard_boom(monkeypatch, times=3)

    hits = [t for t in narrated if t == lr_copy.SECTION_GUARD_ERROR_NARRATION]
    assert len(hits) == 1, (
        f"guard 降級旁白應 per-run 恰一次（防轟炸），實際 {len(hits)} 次：{narrated}"
    )
