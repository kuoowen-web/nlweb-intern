"""O5+O5b 合併版：LR SSE emit 雙路 fallback + 不可 silent 測試。

涵蓋：
- helper 本體三條路徑（sender OK / sender None→write_stream / 兩路皆無→WARN）
- 例外語意（收斂點 1）：sender 拋例外也 fallback；兩路皆拋→WARN 不 raise
- fallback 成功 info log（收斂點 2）
- export payload 原樣到 write_stream（收斂點 4 折衷 + 收斂點 8）
- per call-site delivery tests（收斂點 4）：7 個可直測 emit 點各一條
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

import reasoning.live_research.sse_emit as sse_mod
from reasoning.live_research.sse_emit import emit_sse


def _make_handler(with_sender: bool, with_http: bool):
    handler = MagicMock()
    if with_sender:
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
    else:
        handler.message_sender = None
    if with_http:
        handler.http_handler = MagicMock()
        handler.http_handler.write_stream = AsyncMock()
    else:
        handler.http_handler = None
    return handler


@pytest.mark.asyncio
async def test_sender_present_uses_message_sender():
    handler = _make_handler(with_sender=True, with_http=True)
    payload = {"message_type": "live_research_narration", "text": "hi"}

    sent = await emit_sse(handler, payload)

    assert sent is True
    handler.message_sender.send_message.assert_awaited_once_with(payload)
    handler.http_handler.write_stream.assert_not_called()


@pytest.mark.asyncio
async def test_sender_none_falls_back_to_write_stream():
    handler = _make_handler(with_sender=False, with_http=True)
    payload = {"message_type": "live_research_checkpoint", "stage": 1}

    sent = await emit_sse(handler, payload)

    assert sent is True
    handler.http_handler.write_stream.assert_awaited_once_with(payload)


@pytest.mark.asyncio
async def test_fallback_success_logs_info():
    """收斂點 2：fallback 成功必有 logger.info — 降級必有訊息，不可無痕降級。

    ordering 免疫（full-scan-2026-07 收尾）：sse_emit 的 logger 是
    logging.getLogger(__name__)，全套 ordering 下祖先 logger propagate 被前面
    測試設 False → caplog（掛 root）records 空、假紅。改用 patch.object 直攔
    logger.info 呼叫捕捉訊息 token，繞過全域 propagation 過濾（lessons-testing-review
    §184 輕解）。行為斷言逐字不變（訊息含 message_type + "fallback"）。"""
    handler = _make_handler(with_sender=False, with_http=True)
    payload = {"message_type": "live_research_narration", "text": "hi"}

    with patch.object(sse_mod, "logger") as mock_logger:
        sent = await emit_sse(handler, payload)

    assert sent is True
    info_msgs = [str(c.args[0]) for c in mock_logger.info.call_args_list if c.args]
    assert any(
        "live_research_narration" in m and "fallback" in m.lower()
        for m in info_msgs
    ), f"expected an INFO naming the fallback, got: {info_msgs}"


@pytest.mark.asyncio
async def test_emit_sse_contract_violation_reraises_no_fallback():
    # 🔧R4（R3-BLK-A / §0.1b 分支 3a）：send_message 拋 SseTypedValidationError
    # （contract violation）→ emit_sse **re-raise 穿透、不 fallback**，
    # http_handler.write_stream 不被呼叫。
    #
    # 收尾（full-scan-2026-07）ordering 免疫：SseTypedValidationError 必須取
    # **sse_emit 模組實際綁定的那個 class ref**（= emit_sse 的 except 用的同一物件），
    # 不可 `from core.sse.send import`。否則若前面某測試（test_api_raw_sse_migration）
    # 對 core.sse.send 做 importlib.reload，會產生新 class 物件，raise 新 class 而
    # except 綁舊 class → isinstance 不成立 → 落 except Exception 走 fallback → 假紅。
    from reasoning.live_research.sse_emit import SseTypedValidationError
    handler = _make_handler(with_sender=True, with_http=True)
    handler.message_sender.send_message = AsyncMock(
        side_effect=SseTypedValidationError("bad shape"))
    with pytest.raises(SseTypedValidationError):
        await emit_sse(handler, {"message_type": "live_research_narration", "text": "x"})
    handler.http_handler.write_stream.assert_not_awaited()  # 未 fallback、未送 raw payload


@pytest.mark.asyncio
async def test_emit_sse_transport_failure_still_falls_back():
    # 🔧R4（§0.1b 分支 3b）：send_message 拋一般 Exception（transport failure）→
    # 維持現行 fallback 語義，http_handler.write_stream 被呼叫、不 raise（研究不斷）。
    handler = _make_handler(with_sender=True, with_http=True)
    handler.message_sender.send_message = AsyncMock(side_effect=RuntimeError("conn dead"))
    ok = await emit_sse(handler, {"message_type": "live_research_narration", "text": "x"})
    handler.http_handler.write_stream.assert_awaited_once()  # 走 transport fallback
    assert ok is True


@pytest.mark.asyncio
async def test_sender_raises_falls_back_to_write_stream():
    """收斂點 1：sender 拋例外也 fallback。

    安全性證據（message_senders.py:358-361）：send_message 內唯一的 delivery
    呼叫 write_stream 被內層 try/except 吞掉例外 → 例外傳得出來 ⇒ 必未送達
    ⇒ fallback 重送無 double-send。
    """
    handler = _make_handler(with_sender=True, with_http=True)
    handler.message_sender.send_message = AsyncMock(side_effect=RuntimeError("boom"))
    payload = {"message_type": "live_research_section", "section_index": 0}

    sent = await emit_sse(handler, payload)

    assert sent is True
    handler.http_handler.write_stream.assert_awaited_once_with(payload)


@pytest.mark.asyncio
async def test_both_paths_unavailable_logs_warning_and_not_silent():
    # ordering 免疫（同 test_fallback_success_logs_info）：patch.object 攔 logger.warning
    # 繞過 caplog propagation 污染；行為斷言逐字不變（WARN 含 message_type + "dropped"）。
    handler = _make_handler(with_sender=False, with_http=False)
    payload = {"message_type": "live_research_stage_change", "stage": 3}

    with patch.object(sse_mod, "logger") as mock_logger:
        sent = await emit_sse(handler, payload)

    assert sent is False
    # 不可 silent：必須留下含 message_type 的 WARN
    warn_msgs = [str(c.args[0]) for c in mock_logger.warning.call_args_list if c.args]
    assert any(
        "live_research_stage_change" in m and "dropped" in m.lower()
        for m in warn_msgs
    ), f"expected a WARN naming the dropped message_type, got: {warn_msgs}"


@pytest.mark.asyncio
async def test_sender_and_fallback_both_raise_logs_warning():
    """收斂點 5：取代原 invalid placeholder — 兩路皆拋例外時必留 WARN、回 False、不 raise。

    ordering 免疫（同上）：patch.object 攔 logger.warning；行為斷言逐字不變。"""
    handler = _make_handler(with_sender=True, with_http=True)
    handler.message_sender.send_message = AsyncMock(side_effect=RuntimeError("s"))
    handler.http_handler.write_stream = AsyncMock(side_effect=RuntimeError("w"))
    payload = {"message_type": "live_research_narration", "text": "x"}

    with patch.object(sse_mod, "logger") as mock_logger:
        sent = await emit_sse(handler, payload)

    assert sent is False
    warn_msgs = [str(c.args[0]) for c in mock_logger.warning.call_args_list if c.args]
    assert any(
        "live_research_narration" in m and "dropped" in m.lower()
        for m in warn_msgs
    ), f"expected a WARN naming the dropped message_type, got: {warn_msgs}"


@pytest.mark.asyncio
async def test_export_payload_fields_reach_write_stream_verbatim():
    """收斂點 4 折衷 + 收斂點 8：export payload（含 knowledge_graph，Track D D1）
    原樣到 write_stream。

    Stage 6 export inline 無獨立函式（在 _run_stage_6 內，需真 LLM 才能觸發），
    無法 per call-site 直測 — 改 helper-level payload test 鎖行為契約；
    inline 改寫本身由 Task 3 的強制 read-diff + smoke 驗（deviation 明標，
    見「Deviation 明標」節）。
    """
    handler = _make_handler(with_sender=False, with_http=True)
    kg = {"entities": [{"id": "e1"}], "relations": []}
    payload = {
        "message_type": "live_research_export",
        "format": "markdown",
        "content": "# 報告",
        "knowledge_graph": kg,
    }

    sent = await emit_sse(handler, payload)

    assert sent is True
    delivered = handler.http_handler.write_stream.call_args[0][0]
    assert delivered is payload  # 同一 dict 原樣傳遞，無複製無改寫
    assert delivered["knowledge_graph"] == kg  # Track D D1 欄位原樣保留


# ---------------------------------------------------------------------------
# per call-site delivery tests（收斂點 4）：
# 每個可直測 emit 點各一條「sender None → write_stream 必須收到」delivery test，
# 證明 call site 真的接上了 emit_sse（不只 helper 本體對）。
# orchestrator 5 條在此；loop_engine 2 條見 Task 4；
# Stage 6 export inline 為第 8 點，無獨立函式 → helper-level payload test
# （test_export_payload_fields_reach_write_stream_verbatim）+ Task 3 read-diff。
# ---------------------------------------------------------------------------

def _make_handler_no_sender_with_http():
    h = MagicMock()
    h.message_sender = None
    h.http_handler = MagicMock()
    h.http_handler.write_stream = AsyncMock()
    return h


@pytest.mark.asyncio
async def test_orch_emit_narration_delivers_via_write_stream_when_no_sender():
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    h = _make_handler_no_sender_with_http()
    orch = LiveResearchOrchestrator(handler=h, dry_run=False)

    await orch._emit_narration("測試敘述")

    h.http_handler.write_stream.assert_awaited_once()
    payload = h.http_handler.write_stream.call_args[0][0]
    assert payload["message_type"] == "live_research_narration"
    assert payload["text"] == "測試敘述"


@pytest.mark.asyncio
async def test_orch_emit_narration_empty_text_still_noop():
    """空字串不送（合法 no-op，非 silent-fail）— 不可 Regress 清單 #2。
    註：此測試在改寫前後皆綠（舊行為空字串也不送），非 red-first，是 regress 鎖。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    h = _make_handler_no_sender_with_http()
    orch = LiveResearchOrchestrator(handler=h, dry_run=False)

    await orch._emit_narration("")

    h.http_handler.write_stream.assert_not_called()


@pytest.mark.asyncio
async def test_orch_emit_stage_change_delivers_via_write_stream_when_no_sender():
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    h = _make_handler_no_sender_with_http()
    orch = LiveResearchOrchestrator(handler=h, dry_run=False)

    await orch._emit_stage_change(3)

    h.http_handler.write_stream.assert_awaited_once()
    payload = h.http_handler.write_stream.call_args[0][0]
    assert payload["message_type"] == "live_research_stage_change"
    assert payload["stage"] == 3


@pytest.mark.asyncio
async def test_orch_emit_checkpoint_delivers_via_write_stream_when_no_sender():
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    h = _make_handler_no_sender_with_http()
    orch = LiveResearchOrchestrator(handler=h, dry_run=False)

    await orch._emit_checkpoint(stage=1, proposal="研究提案", evidence_list=[{"id": 1}])

    h.http_handler.write_stream.assert_awaited_once()
    payload = h.http_handler.write_stream.call_args[0][0]
    assert payload["message_type"] == "live_research_checkpoint"
    assert payload["stage"] == 1
    assert payload["proposal"] == "研究提案"
    assert payload["auto_continue_option"] is True
    assert payload["evidence_list"] == [{"id": 1}]


@pytest.mark.asyncio
async def test_orch_emit_section_delivers_via_write_stream_when_no_sender():
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    h = _make_handler_no_sender_with_http()
    orch = LiveResearchOrchestrator(handler=h, dry_run=False)
    section = MagicMock()
    section.section_title = "第一章"
    section.section_content = "內容"
    section.sources_used = [1, 2]
    section.methodology_note = "L3 WARN: 樣本不足"

    from reasoning.live_research.stage_state import LiveResearchStageState
    state = LiveResearchStageState()  # 空 pool → citation_sources == {}
    await orch._emit_section(0, section, state)

    h.http_handler.write_stream.assert_awaited_once()
    payload = h.http_handler.write_stream.call_args[0][0]
    assert payload["message_type"] == "live_research_section"
    assert payload["section_index"] == 0
    assert payload["title"] == "第一章"
    assert payload["content"] == "內容"
    assert payload["sources"] == [1, 2]
    # O2 / O2-TF: citation_sources 必隨 section event（空 pool → {} 非 None）
    assert payload["citation_sources"] == {}
    # 收斂點 8：#4 fix（2026-05-29）methodology_note 必須原樣保留
    assert payload["methodology_note"] == "L3 WARN: 樣本不足"


@pytest.mark.asyncio
async def test_orch_emit_writer_status_delivers_via_write_stream_when_no_sender():
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    h = _make_handler_no_sender_with_http()
    orch = LiveResearchOrchestrator(handler=h, dry_run=False)

    await orch._emit_writer_status({"status": "started", "total_sections": 3})

    h.http_handler.write_stream.assert_awaited_once()
    payload = h.http_handler.write_stream.call_args[0][0]
    assert payload["message_type"] == "live_research_writer_status"
    assert payload["status"] == "started"
    assert payload["total_sections"] == 3


@pytest.mark.asyncio
async def test_loop_emit_narration_delivers_via_write_stream_when_no_sender():
    from reasoning.live_research.loop_engine import BABLoopEngine
    h = _make_handler_no_sender_with_http()
    engine = BABLoopEngine(associator=MagicMock(), handler=h, dry_run=True)

    await engine._emit_narration("旁白")

    h.http_handler.write_stream.assert_awaited_once()
    payload = h.http_handler.write_stream.call_args[0][0]
    assert payload["message_type"] == "live_research_narration"
    assert payload["text"] == "旁白"


@pytest.mark.asyncio
async def test_loop_emit_phase_delivers_via_write_stream_when_no_sender():
    from reasoning.live_research.loop_engine import BABLoopEngine
    h = _make_handler_no_sender_with_http()
    engine = BABLoopEngine(associator=MagicMock(), handler=h, dry_run=True)

    await engine._emit_phase("filter_and_prepare", "started")

    h.http_handler.write_stream.assert_awaited_once()
    payload = h.http_handler.write_stream.call_args[0][0]
    assert payload["message_type"] == "research_phase"
    assert payload["phase"] == "filter_and_prepare"
    assert payload["status"] == "started"
