"""Task 11 golden: api.py DR begin/final_result/complete + 5 SSE error points
routed through ``send_sse(path="raw_api")``.

The raw_api semantics (plan §0.1 path 3): manual inject_user_id + raw
write_stream via ``handler.http_handler`` (which IS the route's ``wrapper``,
baseHandler.py:102), no guard, no exception wrapper (the caller's route
try/except stays outside).

🔧GATE G4(i): error points had NO inject before; adding user_id is safe.
Golden records BOTH states:
  - anonymous (handler.user_id falsy)  -> wire byte-identical to pre-migration
                                          (inject_user_id omits the key).
  - logged-in (handler.user_id set)    -> wire gains user_id only.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


def _handler(user_id=None):
    """Route-style handler: http_handler IS the streaming wrapper (baseHandler:102)."""
    h = MagicMock()
    h.user_id = user_id
    h.http_handler = MagicMock()
    h.http_handler.write_stream = AsyncMock()
    return h


def _sent(h):
    return h.http_handler.write_stream.call_args[0][0]


# ── DR success trio (begin / final_result / complete) ──

@pytest.mark.asyncio
async def test_dr_begin_anonymous_byte_identical():
    from core.sse.send import send_sse
    h = _handler(user_id=None)
    begin = {
        "message_type": "begin-nlweb-response",
        "query": "q",
        "conversation_id": "conv-1",
        "query_id": "q-1",
    }
    await send_sse(h, begin, path="raw_api")
    # anonymous: no user_id added -> byte-identical to hand-written wire
    assert _sent(h) == {
        "message_type": "begin-nlweb-response",
        "query": "q",
        "conversation_id": "conv-1",
        "query_id": "q-1",
    }


@pytest.mark.asyncio
async def test_dr_begin_logged_in_adds_user_id_only():
    from core.sse.send import send_sse
    h = _handler(user_id="uuid-x")
    begin = {"message_type": "begin-nlweb-response", "query": "q",
             "conversation_id": "conv-1", "query_id": "q-1"}
    await send_sse(h, begin, path="raw_api")
    assert _sent(h) == {
        "message_type": "begin-nlweb-response", "query": "q",
        "conversation_id": "conv-1", "query_id": "q-1", "user_id": "uuid-x",
    }


@pytest.mark.asyncio
async def test_dr_final_result_logged_in_adds_user_id_only():
    from core.sse.send import send_sse
    h = _handler(user_id="uuid-x")
    final = {
        "message_type": "final_result",
        "final_report": "report",
        "confidence_level": "Medium",
        "methodology": "note",
        "sources": [],
    }
    await send_sse(h, final, path="raw_api")
    sent = _sent(h)
    assert sent["user_id"] == "uuid-x"
    assert sent["message_type"] == "final_result"
    assert sent["final_report"] == "report"


@pytest.mark.asyncio
async def test_dr_complete_anonymous_byte_identical():
    from core.sse.send import send_sse
    h = _handler(user_id=None)
    await send_sse(h, {"message_type": "complete"}, path="raw_api")
    assert _sent(h) == {"message_type": "complete"}


# ── 5 SSE error points (G4(i): previously no inject) ──

@pytest.mark.asyncio
async def test_error_point_anonymous_byte_identical():
    # Pre-migration wire = {"message_type":"error","error":str(e)} with NO inject.
    # Anonymous -> raw_api still produces byte-identical wire (no user_id added).
    from core.sse.send import send_sse
    h = _handler(user_id=None)
    await send_sse(h, {"message_type": "error", "error": "boom"}, path="raw_api")
    assert _sent(h) == {"message_type": "error", "error": "boom"}


@pytest.mark.asyncio
async def test_error_point_logged_in_gains_user_id():
    # G4(i): logged-in error now carries user_id (safe: frontend ignores it,
    # search.js Trigger G correctly protects same-uid, error bypasses store).
    from core.sse.send import send_sse
    h = _handler(user_id="uuid-x")
    await send_sse(h, {"message_type": "error", "error": "boom"}, path="raw_api")
    assert _sent(h) == {"message_type": "error", "error": "boom", "user_id": "uuid-x"}


# ── IMPL-R1-BLK-A/B: error-point degradation contract ──
#
# The three deep_research/rerun error points build ``wrapper`` before ``handler``
# (handler assigned inside the try). When an error fires in that window,
# ``handler`` is None. The route must still deliver the error envelope and call
# ``finish_response`` — the pre-migration code did (it wrote via the already-built
# ``wrapper``). ``_send_raw_api_error(handler, wrapper, error_data)`` is the root
# fix: it degrades to ``wrapper.write_stream`` when handler is unbound, always
# calls finish_response, and logs LOUDLY on degradation (never silent).


def _wrapper():
    """Route streaming wrapper (has write_stream + finish_response, NO http_handler)."""
    w = MagicMock()
    w.write_stream = AsyncMock()
    w.finish_response = AsyncMock()
    return w


def _wrapper_sent(w):
    return w.write_stream.call_args[0][0]


@pytest.mark.asyncio
async def test_error_none_handler_degrades_to_wrapper_byte_identical():
    # BLK-A/B: handler is None (error before handler assignment), wrapper built.
    # Root fix must still deliver the error envelope via wrapper, byte-identical to
    # the pre-migration hand-written wire ({"message_type":"error","error":...},
    # NO user_id — there is no authenticated handler yet).
    from webserver.routes.api import _send_raw_api_error
    w = _wrapper()
    error_data = {"message_type": "error", "error": "boom"}
    await _send_raw_api_error(handler=None, wrapper=w, error_data=error_data)
    assert _wrapper_sent(w) == {"message_type": "error", "error": "boom"}


@pytest.mark.asyncio
async def test_error_none_handler_still_calls_finish_response():
    # BLK-A: finish_response must NOT be skipped in the degraded path.
    from webserver.routes.api import _send_raw_api_error
    w = _wrapper()
    await _send_raw_api_error(
        handler=None, wrapper=w,
        error_data={"message_type": "error", "error": "boom"})
    w.finish_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_error_none_handler_logs_degradation_not_silent(monkeypatch):
    # BLK-A: degradation must be LOUD (a warning), never a silent swallow.
    # api_routes uses a LazyLogger (not a std logging.Logger with caplog propagation),
    # so assert the helper actively CALLS logger.warning — the "not silent" contract.
    import webserver.routes.api as api_mod
    w = _wrapper()
    fake_logger = MagicMock(wraps=api_mod.logger)
    monkeypatch.setattr(api_mod, "logger", fake_logger)
    await api_mod._send_raw_api_error(
        handler=None, wrapper=w,
        error_data={"message_type": "error", "error": "boom"})
    assert fake_logger.warning.called, \
        "degraded (handler-unbound) error send must emit a WARNING, not be silent"


@pytest.mark.asyncio
async def test_error_bound_handler_uses_handler_path_with_user_id():
    # Handler bound (normal case): route through handler.http_handler (raw_api),
    # logged-in gains user_id, finish_response still called.
    from webserver.routes.api import _send_raw_api_error
    h = _handler(user_id="uuid-x")
    w = _wrapper()  # in real route h.http_handler IS the wrapper; here distinct to
    h.http_handler = w  # prove the handler path (not the wrapper fallback) is taken
    await _send_raw_api_error(
        handler=h, wrapper=w,
        error_data={"message_type": "error", "error": "boom"})
    assert _wrapper_sent(w) == {
        "message_type": "error", "error": "boom", "user_id": "uuid-x"}
    w.finish_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_error_no_wrapper_no_handler_is_loud_not_silent(monkeypatch):
    # Extreme window: error before wrapper even built (both None). Cannot deliver,
    # but MUST NOT raise and MUST log LOUDLY (error) — never a silent swallow.
    import webserver.routes.api as api_mod
    fake_logger = MagicMock(wraps=api_mod.logger)
    monkeypatch.setattr(api_mod, "logger", fake_logger)
    # must not raise
    await api_mod._send_raw_api_error(
        handler=None, wrapper=None,
        error_data={"message_type": "error", "error": "boom"})
    assert fake_logger.error.called, \
        "unable-to-deliver error must be logged LOUDLY (not silent)"


# ── SF-1: validate-only byte-identical anchor (explicit-null payload) ──

@pytest.mark.asyncio
async def test_raw_api_explicit_null_field_byte_identical_flag_on_vs_off(monkeypatch):
    # SF-1 (Codex): _typed_validate is validate-only — it must NOT model_dump-rewrite
    # and drop explicit-null keys. A payload carrying a message_type known to the
    # registry plus an explicit None field must produce a wire dict byte-identical
    # whether the flag is ON or OFF.
    import importlib
    import core.sse.send as send_mod

    payload_tpl = lambda: {
        "message_type": "error",
        "error": "boom",
        "detail": None,  # explicit null — model_dump(exclude_none) would drop this
    }

    # flag OFF
    monkeypatch.setenv("NLWEB_SSE_TYPED_VALIDATE", "")
    importlib.reload(send_mod)
    h_off = _handler(user_id=None)
    await send_mod.send_sse(h_off, payload_tpl(), path="raw_api")
    wire_off = h_off.http_handler.write_stream.call_args[0][0]

    # flag ON
    monkeypatch.setenv("NLWEB_SSE_TYPED_VALIDATE", "1")
    importlib.reload(send_mod)
    h_on = _handler(user_id=None)
    await send_mod.send_sse(h_on, payload_tpl(), path="raw_api")
    wire_on = h_on.http_handler.write_stream.call_args[0][0]

    assert wire_on == wire_off, \
        "flag ON must be byte-identical to OFF (validate-only; explicit-null preserved)"
    assert "detail" in wire_on and wire_on["detail"] is None, \
        "explicit-null key must survive (not dropped by a stray model_dump)"

    # restore module to test-suite default (test.sh runs with flag ON)
    importlib.reload(send_mod)
