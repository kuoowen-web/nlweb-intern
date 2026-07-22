"""Live Research SSE 出口 — 單一 emit 通道，雙路 fallback + 不可 silent。

O5+O5b 合併 fix（2026-06-10）：orchestrator / loop_engine 的 8 處 emit 點過去用
`getattr(handler, 'message_sender', None)`，sender 為 None 時整段靜默 no-op、
連 log 都沒有 → user 端 UI 更新無聲蒸發，違反 CLAUDE.md「不可 silent fail」。

本 module 把 8 處 emit 點收斂到單一 `emit_sse`：
  1) 優先 handler.message_sender.send_message（與正常 SSE 路徑一致）
  2) sender 為 None **或拋例外** → fallback handler.http_handler.write_stream
     （fallback 成功時 logger.info 留痕 — 「降級必有訊息」）
  3) 兩路皆不可用/皆失敗 → logger.warning 標明丟失的 message_type（不可 silent）

例外語意（2026-06-10 adversarial review 收斂，採「例外也 fallback」）：
sender.send_message 拋例外時 fallback 重送**不會造成前端 double-send**。
證據（core/utils/message_senders.py:358-361）：send_message 內唯一的客戶端
delivery 呼叫 `write_stream`（line 359）被自己的 try/except 包住且吞掉例外
（except 內只 clear connection_alive_event，不 re-raise）→ 例外能傳出
send_message 的位置全在 delivery 之前（add_message_metadata /
filter_message_pii / store_message / _send_headers_if_needed）→
sender 拋例外 ⇒ 訊息必未送達 client ⇒ fallback 重送安全。

Deviation 明標：本 helper 是 methods/live_research.py entry 層雙路樣板的
**超集** — entry 層 `elif` 只在 sender 為 None 時 fallback、例外時不 fallback；
本 helper 在例外時也 fallback（理由如上 + 「user 不丟訊息」為最高準則）。

Tradeoff 明標（2026-06-10 review）：fallback 走 raw write_stream，bypass
MessageSender 的 add_message_metadata / filter_message_pii / store_message
三層（entry 層 raw write_stream 雙路先例相同；Trigger G 對 absent user_id
為 skip identity check，前端仍正常消費）。詳見 plan 的
「PII/metadata bypass tradeoff」節。
"""

import logging
from typing import Any, Dict

from core.sse.send import SseTypedValidationError  # reasoning->core 正向依賴，無反向

logger = logging.getLogger(__name__)


async def emit_sse(handler: Any, payload: Dict[str, Any]) -> bool:
    """推送一筆 SSE payload 到前端，雙路 fallback。

    Returns:
        True  = 已成功送出（任一路成功）
        False = 兩路皆不可用或皆失敗（已 log WARN，未 silent）
    """
    msg_type = payload.get("message_type", "<unknown>")

    # 離線早退（plan: lr-sse-reconnect-resume, 2026-06-15）：client 斷線後 emit 直接 drop，
    # **不**進 message_sender / fallback。放最前面是關鍵：否則離線後每條 emit 仍各打一條
    # WARN，Stage 5 大量 narration 把 log 打爆。重連 render 完全走 state-based restore
    # （不做 event 緩衝/replay）。
    alive_evt = getattr(handler, "connection_alive_event", None)
    if alive_evt is not None and not alive_evt.is_set():
        logger.debug(
            f"[LIVE RESEARCH] client offline; dropping SSE "
            f"{msg_type!r} (state persisted separately)"
        )
        return False

    sender = getattr(handler, "message_sender", None)
    if sender is not None:
        try:
            await sender.send_message(payload)
            return True
        except SseTypedValidationError:
            # 🔧R4（R3-BLK-A）contract violation：typed 驗證失敗是開發期 bug，要 fail-loud。
            # **不吞、不 fallback**——re-raise 穿透，讓 dev/CI 真的紅（與 G9「dev/CI fail-loud」
            # 一致）。flag OFF 時 _typed_validate 是 no-op、永不產此例外 → 此 except 永不觸發
            # → 零行為變化。prod 時 _typed_validate 內部已 log-loud + return 原始 payload、
            # 不 raise 此例外 → 不斷流。
            raise
        except Exception as e:
            # 不吞掉：先記再嘗試 fallback。
            # 例外 ⇒ 必未送達（見 module docstring 的 message_senders.py:358-361
            # 證據）⇒ fallback 重送無 double-send。
            logger.warning(
                f"[LIVE RESEARCH] message_sender.send_message failed for "
                f"{msg_type!r}: {e}; trying http_handler fallback"
            )

    http_handler = getattr(handler, "http_handler", None)
    if http_handler is not None:
        try:
            await http_handler.write_stream(payload)
            # 收斂點 2：降級必有訊息 — fallback 成功也要留痕
            logger.info(
                f"[LIVE RESEARCH] {msg_type!r} emitted via http_handler fallback "
                f"(message_sender unavailable or failed)"
            )
            return True
        except Exception as e:
            logger.warning(
                f"[LIVE RESEARCH] http_handler.write_stream fallback also failed "
                f"for {msg_type!r}: {e}; SSE payload dropped"
            )
            return False

    # 兩路皆無 → 絕不 silent，留下含 message_type 的 WARN
    logger.warning(
        f"[LIVE RESEARCH] SSE payload dropped — both message_sender and "
        f"http_handler unavailable; message_type={msg_type!r} not delivered to client"
    )
    return False
