"""層 2：斷線偵測死碼修復——handler 屬性應為 http_handler 非 request_handler。

死碼真相：orchestrator_base / loop_engine 三處 `_check_connection` / `_send_progress`
讀 `getattr(self.handler, 'request_handler', None)`——handler 上真實屬性是 `http_handler`
（baseHandler.py 設定），`request_handler` 全繼承鏈不存在 → getattr 恆 None →
`if wrapper and ...` 恆 False → 斷線 guard 死碼、永不觸發。

本測試在「唯一斷線訊號是 http_handler.connection_alive=False」的情境下驗證斷線會
raise ResearchCancelledError。修屬性名前（讀 request_handler）：guard 拿 None → 不 raise
→ FAIL；修後（讀 http_handler）：guard 生效 → raise → PASS。
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from reasoning.orchestrator_base import OrchestratorBase, ResearchCancelledError  # noqa: E402


def _make_handler(connection_alive: bool):
    """建一個具備真實屬性名（http_handler）的 fake handler。

    刻意 NOT 設 request_handler：若 code 仍讀 request_handler，
    getattr 回 None → guard 死碼 → 不 raise → test 失敗，證明死碼未修。
    """
    handler = MagicMock()
    wrapper = MagicMock()
    wrapper.connection_alive = connection_alive
    handler.http_handler = wrapper
    # 明確移除 request_handler，模擬生產 handler（無此屬性）
    del handler.request_handler
    # message_sender 存在但 send 成功（不影響斷線 guard 判定）
    handler.message_sender = MagicMock()
    handler.message_sender.send_message = AsyncMock()
    # _check_connection 也讀 connection_alive_event：斷線時亦視為斷
    ev = asyncio.Event()
    if connection_alive:
        ev.set()
    handler.connection_alive_event = ev
    handler._soft_interrupt_event = None
    return handler


@pytest.mark.asyncio
async def test_send_progress_raises_on_disconnect():
    """client 斷線（http_handler.connection_alive=False）→ _send_progress 應 raise ResearchCancelledError。"""
    handler = _make_handler(connection_alive=False)
    orch = OrchestratorBase(handler)
    with pytest.raises(ResearchCancelledError):
        await orch._send_progress({"message_type": "progress", "stage": "test"})


@pytest.mark.asyncio
async def test_send_progress_ok_when_connected():
    """連線正常時 _send_progress 不 raise。"""
    handler = _make_handler(connection_alive=True)
    orch = OrchestratorBase(handler)
    await orch._send_progress({"message_type": "progress", "stage": "test"})  # 不應拋


def test_check_connection_raises_on_wrapper_disconnect():
    """_check_connection 讀 http_handler.connection_alive=False → raise。

    刻意讓 connection_alive_event 保持 set（模擬兩套狀態不同步的 half-open：
    唯一反映斷線的是 wrapper flag）——這樣 event guard 不會掩蓋 wrapper 死碼未修，
    真正證偽死碼。修屬性名前：event set + wrapper 死碼 → 不 raise（FAIL）；
    修後：wrapper flag 生效 → raise（PASS）。
    """
    handler = _make_handler(connection_alive=False)
    # 專測 wrapper flag 路徑：event 仍 set，唯一能偵測斷線的是 http_handler.connection_alive。
    handler.connection_alive_event.set()
    orch = OrchestratorBase(handler)
    with pytest.raises(ResearchCancelledError):
        orch._check_connection()


@pytest.mark.asyncio
async def test_loop_engine_check_connection_offline_returns_signal_not_raise():
    """LR BAB loop（BABLoopEngine）的 _check_connection 純斷線時回 "offline"（不 raise）。

    plan: lr-disconnect-midstage-persist（R1 AR SHOULD-FIX 1）—— 本測試原名
    test_loop_engine_check_connection_raises_on_disconnect，斷言純斷線 raise。
    該行為正是 Stage 2 蒸發 bug 的成因之一（offline 被當 error 处理，蒸發已累積
    evidence）。純斷線改為 cooperative stop（回傳 "offline"，不 raise）后，此測試
    更新期望值以反映新行為，其餘測試意圯（wrapper flag signal 確實被讀到、確實
    觸發反應）不變。

    同樣讓 connection_alive_event 保持 set（模擬 event 沒同步、只有 wrapper flag
    反映斷線），使 loop_engine 的 event guard 不會掩蓋 wrapper 死碼未修測試意圖。
    """
    from reasoning.live_research import loop_engine as le_mod

    handler = _make_handler(connection_alive=False)
    # 專測 wrapper flag 路徑：event 保持 set。
    handler.connection_alive_event.set()
    handler._soft_interrupt_event = None  # 純斷線，非 soft-interrupt

    engine = le_mod.BABLoopEngine.__new__(le_mod.BABLoopEngine)
    engine.handler = handler
    result = engine._check_connection()
    assert result == "offline"  # NOT raised — cooperative stop（plan: lr-disconnect-midstage-persist）


def test_lr_handler_has_http_handler_not_request_handler():
    """LR handler（繼承鏈同 DR）必須有 http_handler、無 request_handler。
    這是層 2 死碼修復對 LR 生效的前提。"""
    from methods.live_research import LiveResearchHandler
    h = LiveResearchHandler({"query": ["x"], "dry_run": "true"}, MagicMock())
    assert hasattr(h, "http_handler"), "LR handler 無 http_handler，死碼修復對 LR 無效"
    assert not hasattr(h, "request_handler"), "LR handler 竟有 request_handler（非預期）"
