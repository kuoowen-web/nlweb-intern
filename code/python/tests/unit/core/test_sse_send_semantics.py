import pytest
from unittest.mock import AsyncMock, MagicMock
from core.sse.send import send_sse


def _handler(streaming=True, uid="user-x"):
    h = MagicMock()
    h.streaming = streaming
    h.user_id = uid
    h.http_handler = MagicMock()
    h.http_handler.write_stream = AsyncMock()
    return h


@pytest.mark.asyncio
async def test_path2_ad_hoc_injects_user_id_and_raw_writes():
    h = _handler()
    msg = {"message_type": "begin-nlweb-response", "query": "q"}
    await send_sse(h, msg, path="ad_hoc")
    # 語義複刻 message_senders.py:135-138: inject_user_id then raw write_stream
    sent = h.http_handler.write_stream.call_args[0][0]
    assert sent["user_id"] == "user-x"
    assert sent["message_type"] == "begin-nlweb-response"


@pytest.mark.asyncio
async def test_path2_streaming_guard_early_returns():
    # message_senders.py:123: if not (streaming and http_handler): return
    h = _handler(streaming=False)
    await send_sse(h, {"message_type": "progress"}, path="ad_hoc")
    h.http_handler.write_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_path2_write_exception_swallowed_not_raised():
    # message_senders.py:139-140: except → logger.warning, no re-raise
    h = _handler()
    h.http_handler.write_stream.side_effect = RuntimeError("boom")
    # must NOT raise
    await send_sse(h, {"message_type": "progress"}, path="ad_hoc")


import os  # noqa: E402
from unittest.mock import patch  # noqa: E402,F401


@pytest.mark.asyncio
async def test_typed_validate_off_is_byte_equivalent(monkeypatch):
    # flag OFF（預設）：payload 原樣送出，零行為變化（鐵律）
    monkeypatch.setattr("core.sse.send.TYPED_VALIDATE_ON", False)
    h = _handler()
    msg = {"message_type": "begin-nlweb-response", "query": "q"}
    await send_sse(h, msg, path="ad_hoc")
    sent = h.http_handler.write_stream.call_args[0][0]
    assert sent["message_type"] == "begin-nlweb-response"  # 未經 model 改形


@pytest.mark.asyncio
async def test_typed_validate_on_raises_on_bad_shape_in_dev(monkeypatch):
    # flag ON + 非 prod：形狀不符**已註冊 model** 的 payload → model_validate raise（fail-loud）
    # 🔧R3（R2-BLK-3）：Task 3 當下 _REGISTRY 只有 begin-nlweb-response（Task 1 建），
    # intermediate_result 要 Task 5 才註冊。舊版用 intermediate_result → 落 base SseEnvelope
    # （extra=allow + 全 optional）**不 raise** = 假設錯（test 假紅）。改用 begin-nlweb-response
    # + type-mismatch payload：BeginNlwebResponse 的 query 是 Optional[str]，餵 dict 觸發
    # Pydantic v2 ValidationError。此 test 在任何 Task 都成立（不依賴尚未註冊的 model）。
    from core.sse.send import SseTypedValidationError
    monkeypatch.setattr("core.sse.send.TYPED_VALIDATE_ON", True)
    monkeypatch.setattr("core.sse.send._IS_PROD", False)
    h = _handler()
    # 🔧R5（R4-SF-1）：斷言**精確型別** SseTypedValidationError，非寬 Exception。
    # 假綠向量：R4 分類學（emit_sse/progress 的分類 except）依賴 _typed_validate raise 的
    # 是**專用例外**；若實作退回一般 ValueError/Pydantic ValidationError，寬 Exception 斷言
    # 仍綠，但 emit_sse/progress 的 `except SseTypedValidationError` 對它不觸發、落 `except
    # Exception` → 被 fallback/吞、不穿透 → 分類學前提被打穿卻無 test 攔住。精確型別鎖死此前提。
    with pytest.raises(SseTypedValidationError):
        # query 期待 str，餵 dict → BeginNlwebResponse.model_validate raise，
        # 由 _typed_validate 包成 SseTypedValidationError（dev/CI 分支）
        await send_sse(h, {"message_type": "begin-nlweb-response", "query": {"bad": "shape"}}, path="ad_hoc")


# ─────────────────────────── Task 4: progress / full / raw_api ───────────────────────────


@pytest.mark.asyncio
async def test_path_full_delegates_to_message_sender():
    # message_senders.py:332-365 full path：委派 MessageSender.send_message（權威實作）。
    h = MagicMock()
    h.message_sender = MagicMock(); h.message_sender.send_message = AsyncMock()
    await send_sse(h, {"message_type": "intermediate_result", "stage": "writer_composing"}, path="full")
    h.message_sender.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_path_progress_raises_injected_on_disconnect_after_send():
    # orchestrator_base.py:136-138: send 後偵測斷線 → raise caller 注入的 exception factory。
    # 🔧R3：core 不 import reasoning；用 stub factory 注入（Task 12 才傳真 ResearchCancelledError）。
    class _Stub(Exception):
        pass
    h = MagicMock()
    h.message_sender = MagicMock(); h.message_sender.send_message = AsyncMock()
    h.http_handler = MagicMock(); h.http_handler.connection_alive = False
    with pytest.raises(_Stub):
        await send_sse(h, {"message_type": "research_phase", "phase": "writer", "status": "started"},
                       path="progress", on_disconnect=lambda: _Stub("disconnected"))


@pytest.mark.asyncio
async def test_path_progress_missing_on_disconnect_fails_loud_on_disconnect():
    # 🔧R3：progress 斷線分支未注入 on_disconnect = 接線 bug → fail-loud（不偷 import reasoning）。
    h = MagicMock()
    h.message_sender = MagicMock(); h.message_sender.send_message = AsyncMock()
    h.http_handler = MagicMock(); h.http_handler.connection_alive = False
    with pytest.raises(RuntimeError, match="requires on_disconnect"):
        await send_sse(h, {"message_type": "research_phase"}, path="progress")  # 未傳 on_disconnect


# 🔧（AR R1 SF1，三席同抓）鎖住「無 message_sender → 不 send」分支。
# 陷阱：MagicMock() 對任意屬性 hasattr 恆 True（orchestrator_base.py:129 是 hasattr），
# 用 MagicMock 建 handler 會恆走 send 分支，no-send 分支從沒被測到 = 假綠。
# 用 SimpleNamespace（真的沒有 message_sender 屬性）才鎖得住。
@pytest.mark.asyncio
async def test_path_progress_no_message_sender_does_not_send_and_still_checks_disconnect():
    import types
    class _Stub(Exception):
        pass
    # handler 明確不含 message_sender（hasattr → False），http_handler 仍在
    h = types.SimpleNamespace()
    h.http_handler = MagicMock(); h.http_handler.connection_alive = True
    # 不該 raise AttributeError、不該嘗試 send；連線正常故不 raise disconnect
    await send_sse(h, {"message_type": "research_phase"}, path="progress",
                   on_disconnect=lambda: _Stub("x"))
    # 斷線時（即使無 message_sender）仍要做 disconnect check → raise 注入的 factory
    h2 = types.SimpleNamespace()
    h2.http_handler = MagicMock(); h2.http_handler.connection_alive = False
    with pytest.raises(_Stub):
        await send_sse(h2, {"message_type": "research_phase"}, path="progress",
                       on_disconnect=lambda: _Stub("disconnected"))


# 🔧R5F（R5-SF-C，對稱 emit_sse Task 13 Step 1c）：progress 分類 except 的回歸測試。
# R4-BLK-A 的修法（Task 4 Step 3 progress 分支加 `except SseTypedValidationError: raise`
# 於 `except Exception` 之前）land 後**必須有 test 鎖住**——否則未來 edit 移除那一行、
# progress 的 contract violation 又被 broad except 吞降 warning，無 test 紅。這正是 R1-R5
# 的「broad except 吞 contract violation」家族核心，progress 修法留白無牙 = 修法覆蓋面缺口。
# 兩半斷言（比照 emit_sse Task 13 Step 1c 的 contract-violation 半）：
#   (a) send_message 拋 SseTypedValidationError（contract violation）→ progress 分支 re-raise
#       穿透，不落 `except Exception` 的 warning-swallow 分支。
#   (b) 用 caplog 佐證：未落 "send_sse(progress) send failed" 的 logger.warning（未被吞降）。
@pytest.mark.asyncio
async def test_path_progress_reraises_typed_validation_error(caplog):
    # 🔧R5F（R5-SF-C）：progress 走 send_message → 內部 _typed_validate 的 dev/CI raise
    # 是 SseTypedValidationError（contract violation），progress 分支必須 re-raise 穿透、
    # 不吞降 warning（對稱 emit_sse test_emit_sse_contract_violation_reraises_no_fallback）。
    from core.sse.send import SseTypedValidationError
    h = MagicMock()
    h.message_sender = MagicMock()
    h.message_sender.send_message = AsyncMock(side_effect=SseTypedValidationError("bad shape"))
    h.http_handler = MagicMock(); h.http_handler.connection_alive = True
    with caplog.at_level("WARNING"):
        with pytest.raises(SseTypedValidationError):
            await send_sse(h, {"message_type": "research_phase", "phase": "writer"},
                           path="progress", on_disconnect=lambda: RuntimeError("x"))
    # 斷言未落 warning-swallow 分支（`except Exception` body 的 logger.warning 未觸發）
    assert not any("send_sse(progress) send failed" in r.message for r in caplog.records), (
        "SseTypedValidationError 被 progress 的 `except Exception` 吞降 warning — "
        "分類 except（`except SseTypedValidationError: raise`）被移除或失效，contract violation 不 fail-loud")
