"""Task 12 golden: _send_progress mutate + raise semantics preserved after
routing the send through send_sse(path="progress").

Two most-critical equivalences (plan §Task 12 Step 1):
  1. user_friendly_sse flag ON -> message gets user_message/progress mutated
     (this mutate STAYS in _send_progress caller, not in send_sse).
  2. disconnect after send -> raise ResearchCancelledError (injected via
     on_disconnect factory; core/sse/send.py does not import reasoning).
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from reasoning.orchestrator_base import OrchestratorBase, ResearchCancelledError  # noqa: E402


def _make_handler(connection_alive: bool, user_id="u"):
    handler = MagicMock()
    wrapper = MagicMock()
    wrapper.connection_alive = connection_alive
    handler.http_handler = wrapper
    del handler.request_handler
    handler.message_sender = MagicMock()
    handler.message_sender.send_message = AsyncMock()
    ev = asyncio.Event()
    if connection_alive:
        ev.set()
    handler.connection_alive_event = ev
    handler._soft_interrupt_event = None
    return handler


@pytest.mark.asyncio
async def test_send_progress_delegates_send_to_message_sender():
    handler = _make_handler(connection_alive=True)
    orch = OrchestratorBase(handler)
    msg = {"message_type": "research_phase", "phase": "writer", "status": "started"}
    await orch._send_progress(msg)
    handler.message_sender.send_message.assert_awaited_once()
    sent = handler.message_sender.send_message.call_args[0][0]
    assert sent["message_type"] == "research_phase"


@pytest.mark.asyncio
async def test_send_progress_raises_on_disconnect_after_send():
    # disconnect detected after send -> ResearchCancelledError (injected factory).
    handler = _make_handler(connection_alive=False)
    orch = OrchestratorBase(handler)
    with pytest.raises(ResearchCancelledError):
        await orch._send_progress({"message_type": "progress", "stage": "test"})


@pytest.mark.asyncio
async def test_send_progress_mutate_stays_in_caller(monkeypatch):
    # user_friendly_sse flag ON -> _send_progress mutates user_message/progress
    # BEFORE delegating send. Verify the mutated dict reaches send_message.
    import reasoning.orchestrator_base as ob

    fake_cfg = MagicMock()
    fake_cfg.reasoning_params.get.return_value = {"user_friendly_sse": True}
    monkeypatch.setattr(ob, "CONFIG", fake_cfg)
    # Give ProgressConfig a known stage
    monkeypatch.setattr(ob.ProgressConfig, "STAGES",
                        {"analyst_analyzing": {"message": "分析中", "weight": 0.3}})
    monkeypatch.setattr(ob.ProgressConfig, "calculate_progress",
                        staticmethod(lambda stage, it, total: 30))

    handler = _make_handler(connection_alive=True)
    orch = OrchestratorBase(handler)
    msg = {"message_type": "progress", "stage": "analyst_analyzing"}
    await orch._send_progress(msg)
    sent = handler.message_sender.send_message.call_args[0][0]
    assert sent["user_message"] == "分析中"
    assert sent["progress"] == 30


@pytest.mark.asyncio
async def test_send_progress_send_exception_swallowed_not_raised():
    # send failure is non-critical -> logged, not raised (connection still alive).
    handler = _make_handler(connection_alive=True)
    handler.message_sender.send_message.side_effect = RuntimeError("transport")
    orch = OrchestratorBase(handler)
    await orch._send_progress({"message_type": "progress", "stage": "x"})  # must NOT raise
