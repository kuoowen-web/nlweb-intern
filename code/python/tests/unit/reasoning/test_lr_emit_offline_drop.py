"""LR SSE emit 離線早退 drop（plan: lr-sse-reconnect-resume, 2026-06-15）。

斷線後一個 stage 內 emit 可能數十條（Stage 5 大量 narration）→ log 洪水。
修法：offline pre-check 放 `emit_sse()` **最前面**，離線時 debug-drop 直接 return False，
不進 message_sender / http_handler fallback（避免每條各打一條 WARN）。
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from reasoning.live_research.sse_emit import emit_sse


def _alive_event(is_set: bool):
    evt = MagicMock()
    evt.is_set = MagicMock(return_value=is_set)
    return evt


@pytest.mark.asyncio
async def test_emit_sse_drops_when_offline():
    """connection_alive_event clear（離線）→ emit_sse 最前面早退 False，不呼叫 sender/write_stream。"""
    handler = MagicMock()
    handler.connection_alive_event = _alive_event(False)  # 離線
    sender = MagicMock()
    sender.send_message = AsyncMock()
    handler.message_sender = sender
    http = MagicMock()
    http.write_stream = AsyncMock()
    handler.http_handler = http

    result = await emit_sse(handler, {"message_type": "narration", "content": "x"})
    assert result is False
    sender.send_message.assert_not_called()
    http.write_stream.assert_not_called()


@pytest.mark.asyncio
async def test_emit_sse_delivers_when_online():
    """連線正常（alive set）→ emit_sse 照常走 message_sender，不受離線早退影響。"""
    handler = MagicMock()
    handler.connection_alive_event = _alive_event(True)  # 在線
    sender = MagicMock()
    sender.send_message = AsyncMock()
    handler.message_sender = sender

    result = await emit_sse(handler, {"message_type": "narration", "content": "x"})
    assert result is True
    sender.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_emit_sse_no_alive_event_attr_delivers():
    """handler 無 connection_alive_event attr（非 LR 路徑）→ 不誤判離線，照常送。"""
    handler = MagicMock(spec=["message_sender", "http_handler"])
    sender = MagicMock()
    sender.send_message = AsyncMock()
    handler.message_sender = sender

    result = await emit_sse(handler, {"message_type": "narration"})
    assert result is True
    sender.send_message.assert_awaited_once()
