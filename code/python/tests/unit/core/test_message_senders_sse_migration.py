"""Task 10 golden: the three ad-hoc emitters (send_begin_response /
send_end_response / send_progress) must send a wire dict byte-equivalent to
their pre-migration hand-built dict after being routed through
``send_sse(path="ad_hoc")``.

鐵律（plan §遷移總策略第 1 點）：flag OFF = 零行為變化。These goldens record the
CURRENT wire shape (inject_user_id + no metadata/PII/store, raw write_stream);
after routing through send_sse(path="ad_hoc") the wire dict must be unchanged.

``timestamp`` is runtime ``int(time.time()*1000)`` so we assert it is an int and
present, and compare all OTHER keys exactly (the emitters, not the fixture, own
the timestamp — see plan §0.1 path 2).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


def _make_sender(user_id="user-uuid-x", streaming=True):
    from core.utils.message_senders import MessageSender
    handler = MagicMock()
    handler.streaming = streaming
    handler.user_id = user_id
    handler.conversation_id = "conv-abc"
    handler.query = "台灣綠能發展衝突"
    handler.query_id = "q-123"
    handler.http_handler = MagicMock()
    handler.http_handler.write_stream = AsyncMock()
    return MessageSender(handler), handler


def _sent(handler):
    return handler.http_handler.write_stream.call_args[0][0]


def _assert_timestamp_then_strip(sent):
    assert "timestamp" in sent and isinstance(sent["timestamp"], int)
    out = dict(sent)
    out.pop("timestamp")
    return out


@pytest.mark.asyncio
async def test_send_begin_response_wire_equivalent():
    sender, handler = _make_sender()
    await sender.send_begin_response()
    sent = _sent(handler)
    assert _assert_timestamp_then_strip(sent) == {
        "message_type": "begin-nlweb-response",
        "conversation_id": "conv-abc",
        "query": "台灣綠能發展衝突",
        "query_id": "q-123",
        "user_id": "user-uuid-x",
    }


@pytest.mark.asyncio
async def test_send_end_response_wire_equivalent():
    sender, handler = _make_sender()
    await sender.send_end_response()
    sent = _sent(handler)
    assert _assert_timestamp_then_strip(sent) == {
        "message_type": "end-nlweb-response",
        "conversation_id": "conv-abc",
        "user_id": "user-uuid-x",
    }


@pytest.mark.asyncio
async def test_send_end_response_error_flag_wire_equivalent():
    sender, handler = _make_sender()
    await sender.send_end_response(error=True)
    sent = _sent(handler)
    assert _assert_timestamp_then_strip(sent) == {
        "message_type": "end-nlweb-response",
        "conversation_id": "conv-abc",
        "error": True,
        "user_id": "user-uuid-x",
    }


@pytest.mark.asyncio
async def test_send_progress_wire_equivalent():
    sender, handler = _make_sender()
    await sender.send_progress("searching", "搜尋新聞中", percent=42)
    sent = _sent(handler)
    assert _assert_timestamp_then_strip(sent) == {
        "message_type": "progress",
        "stage": "searching",
        "message": "搜尋新聞中",
        "percent": 42,
        "user_id": "user-uuid-x",
    }


@pytest.mark.asyncio
async def test_send_progress_no_percent_omits_key():
    sender, handler = _make_sender()
    await sender.send_progress("ranking", "排序中")
    sent = _sent(handler)
    assert "percent" not in sent
    assert _assert_timestamp_then_strip(sent) == {
        "message_type": "progress",
        "stage": "ranking",
        "message": "排序中",
        "user_id": "user-uuid-x",
    }


@pytest.mark.asyncio
async def test_anonymous_omits_user_id():
    # inject_user_id omits the key entirely when handler.user_id is None (匿名).
    sender, handler = _make_sender(user_id=None)
    await sender.send_begin_response()
    sent = _sent(handler)
    assert "user_id" not in sent


@pytest.mark.asyncio
async def test_streaming_guard_early_returns():
    # message_senders.py:123: if not (streaming and http_handler): return
    sender, handler = _make_sender(streaming=False)
    await sender.send_begin_response()
    handler.http_handler.write_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_exception_swallowed_not_raised():
    # ad-hoc emitter exception → logger.warning, no re-raise (path 2 semantics).
    sender, handler = _make_sender()
    handler.http_handler.write_stream.side_effect = RuntimeError("boom")
    await sender.send_progress("x", "y")  # must NOT raise
