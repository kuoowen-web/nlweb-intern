"""Single SSE send helper.

⚠️ 鐵律（見 plan §0.1）：本 helper 不統一各路語義，而是**語義保持的 router**。
``path`` 選對應現行語義分支；typed-validate flag OFF 時逐字等價於原 caller。各路語義彼此不同
（ad_hoc 吞例外不 fallback、_send_progress raise on_disconnect），不可用單一語義覆蓋。

🔧R3（R2-BLK-1）：**無 ``path="lr"``**。LR 走 reasoning 自己的權威 ``emit_sse``、不經
send_sse（見 §依賴方向圖 / Task 13）；send_sse 從此不 import reasoning 的任何 symbol。

Typed-validate flag（🔧 AR R1 B5 + 🔧R3 B5-BLK-2 + 🔧R4 R3-BLK-A/B）：env
`NLWEB_SSE_TYPED_VALIDATE`。OFF（預設）= 零行為變化，payload 原樣進送出路徑。ON = 送出前
先 model_validate（**validate-only，不 model_dump 回寫、不改任何 byte**——🔧R4 R3-BLK-B）。
**掛點涵蓋 full**：send_sse 自身 ad_hoc/raw_api 分支在函式體內驗；``path="full"`` 委派的
``MessageSender.send_message`` 在其**內部**驗（wire dict 完成後、write 前，見 message_senders.py）
——故 §0.6 高價值 event flag ON 下真被驗證。驗證失敗：dev/CI raise `SseTypedValidationError`
（**專用例外**，讓 LR emit_sse 能分辨 contract violation vs transport failure、穿透 fallback，
🔧R4 R3-BLK-A）、prod log-loud 不斷流。

依賴方向（🔧 AR R1 B2 + 🔧R3 R2-BLK-1，見 §依賴方向圖）：本 module **靜態 + lazy 皆零
import reasoning.***。path=progress 的 disconnect-raise exception 由 caller 經
``on_disconnect`` **必填**注入（無 lazy import fallback）；core 只認 Callable，不認 reasoning 型別。
"""
import logging
import os
from typing import Any, Callable, Dict, Literal, Optional

from core.utils.message_senders import inject_user_id

logger = logging.getLogger(__name__)

Path = Literal["ad_hoc", "full", "raw_api", "progress"]

# 🔧 AR R1 B5: typed-validate flag（module-level，預設 OFF = 鐵律載體）。
# CI / scripts/test.sh 環境設 ON；dev 建議 ON；prod 預設 OFF。
TYPED_VALIDATE_ON = os.environ.get("NLWEB_SSE_TYPED_VALIDATE", "").lower() in ("1", "true", "on")
# dev/CI raise vs prod log-loud 的分流依據（見 §遷移總策略第 6 點）。
_IS_PROD = os.environ.get("NLWEB_ENV", "").lower() in ("prod", "production")


class SseTypedValidationError(Exception):
    """🔧R4（R3-BLK-A 分類學）SSE typed 驗證專用例外（住 core/sse，向下無依賴）。

    這個型別是「**contract violation**（typed 驗證失敗）」與「**transport failure**
    （sender/write_stream 送不出去）」在整條 LR 失敗路徑上的**分類鍵**：
    LR 的 `emit_sse` 對 sender 例外採「例外也 fallback」（§0.1 path 4，與 path 1/2 相反），
    若 `_typed_validate` 的 dev/CI raise 用一般 `Exception`，會被 `emit_sse:65` 的 broad
    except 當成 transport failure 吞掉、fallback raw write_stream 送出**未驗證原始 payload**
    → LR 主路徑 flag ON 下**不 fail-loud**（R3-BLK-A）。用專用例外，emit_sse 得以**先捕它
    re-raise 穿透**（見 Task 13 / §0.1 path 4 修訂），把 contract violation 與 transport
    failure 分開，兩個目標（「fail-loud」與「LR 絕不讓 transport 失敗殺研究」）不再打架。"""


def _typed_validate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Flag ON 時對 payload 做 model_validate（**validate-only，不改 payload**）。
    Flag OFF 時原樣回傳（零行為變化）。驗證失敗：dev/CI raise `SseTypedValidationError`、
    prod log-loud 照送。

    🔧R4（R3-BLK-B validate-only）：**驗證後 return 原始 `payload` 不動**（不做
    `model_dump` 回寫）。理由：`model_dump(by_alias=True, exclude_none=True)` 會 drop
    值為 None 的 key，對含 explicit-null 的合法流量改變 wire JSON + `store_message` 的
    stored shape，破「ON byte-equivalent / 零行為變化」鐵律（三席 R3-BLK-B 抓）。validate-only
    使 wire/store shape 與 OFF **完全 byte-identical**——ON 只多「驗一關」、不改任何 byte。
    模型序列化 round-trip 仍由 Task 5 fixture test 靜態驗（那裡才需要 model_dump）。

    🔧R4（R3-BLK-A）：dev/CI 一律 raise `SseTypedValidationError`（非一般 Exception），
    讓 LR 主路徑的 `emit_sse` 得以先捕它 re-raise 穿透 fallback（見 Task 13）。

    🔧R3（R2-BLK-3）strict-parser 語義：若 message_type 屬 LIVE_MODEL_TYPES 卻落 base
    SseEnvelope（漏建/漏註冊）→ dev/CI 主動 raise（不讓「該建模卻沒建」靠 extra=allow 靜默過）。
    這讓 dev-raise 契約在任何 Task 都成立（不依賴某個 model 已註冊），與 B4 精神一致。"""
    if not TYPED_VALIDATE_ON:
        return payload
    from core.sse.models import parse_sse_envelope, SseEnvelope  # core 同層，無反向依賴
    try:
        model = parse_sse_envelope(payload)  # 只驗形狀，丟棄 model（不回寫）
        # strict：宣稱為 live 卻落 base = 漏註冊，視為驗證失敗（走下方 dev-raise / prod-log）。
        mt = payload.get("message_type")
        if type(model) is SseEnvelope and mt in _live_model_types():
            raise ValueError(
                f"message_type {mt!r} ∈ LIVE_MODEL_TYPES 但落 base SseEnvelope"
                f"（模型漏建/漏註冊，flag ON 下不容靜默過）")
    except Exception as e:
        if _IS_PROD:
            # prod：log-loud 不斷流，fallback 原始 payload 照送（不因型別驗證中斷 user stream）
            # 🔧R5（R4-SF-4）：明確 `message_type=` 欄位標籤（非只 inline 插值），讓 oncall
            # 能一眼定位是**哪個 event 形狀漂移**（結構化可診斷性；prod 走 log-loud 時唯一線索）。
            logger.error(f"[sse-typed-validate] validation FAILED "
                         f"message_type={payload.get('message_type')!r}, "
                         f"sending raw payload: {e}")
            return payload
        # dev/CI：fail-loud，讓假綠無所遁形。用**專用例外**包起（R3-BLK-A 分類學）——
        # 使 LR emit_sse 能把 contract violation 與 transport failure 分開處理。
        raise SseTypedValidationError(
            f"SSE typed validation failed for "
            f"{payload.get('message_type')!r}: {e}") from e
    # 🔧R4（R3-BLK-B）：validate-only——回傳**原始 payload 不動**（wire/store byte-identical）。
    return payload


def _live_model_types():
    """lazy 讀 registry 的 LIVE_MODEL_TYPES（core 同層，無反向依賴）。
    registry.py 於 Task 8 落地前不存在 → 回空集（strict 檢查退化為 no-op，不誤 raise）。"""
    try:
        from core.sse.registry import LIVE_MODEL_TYPES
        return LIVE_MODEL_TYPES
    except ImportError:
        return frozenset()


async def send_sse(
    handler: Any,
    payload: Dict[str, Any],
    *,
    path: Path = "ad_hoc",
    on_disconnect: Optional[Callable[[], Exception]] = None,  # 🔧 B2/🔧R3: progress raise 注入槽（progress 必填）
    # 🔧R3 R2-BLK-1：無 lr_emit 參數 —— path="lr" 分支已刪，LR 直接用 reasoning 的 emit_sse。
) -> None:
    """Emit one SSE payload, replicating the legacy semantics of ``path``.

    path="ad_hoc": MessageSender.send_begin/end/progress semantics
      (message_senders.py:121-197): streaming+http_handler guard,
      inject_user_id only (bypass metadata/PII/store), raw write_stream,
      exception → logger.warning, no re-raise, no fallback.
    """
    # 🔧 AR R1 SF5：混種遷移期的結構化可觀測性——記 message_type + path + flag，
    # 讓 oncall 三個月後能區分同一 event 走哪條語義路徑 / flag 態（debug 級，不洪水）。
    logger.debug("send_sse: message_type=%r path=%r typed_validate=%s",
                 payload.get("message_type"), path, TYPED_VALIDATE_ON)
    if path == "ad_hoc":
        # message_senders.py:123
        if not (getattr(handler, "streaming", False)
                and getattr(handler, "http_handler", None) is not None):
            return
        inject_user_id(payload, handler)  # :135
        payload = _typed_validate(payload)  # 🔧 B5: flag ON 才驗證，OFF 原樣
        try:
            await handler.http_handler.write_stream(payload)  # :138
        except Exception as e:  # :139
            logger.warning(f"send_sse(ad_hoc) failed for "
                           f"{payload.get('message_type')!r}: {e}")
        return

    if path == "progress":
        # orchestrator_base.py:105-138 語義：flag-gated mutate 在 caller 端做完再進來；
        # 此處只複刻 send + disconnect-raise。
        # 🔧 B2/🔧R3：disconnect 時要 raise 的 exception 由 caller 經 on_disconnect **必填**注入
        # （factory 回 ResearchCancelledError 實例）；core 不 import reasoning，不留 lazy fallback。
        try:
            if hasattr(handler, "message_sender"):  # :129
                await handler.message_sender.send_message(payload)  # full sink → 內部 _typed_validate
        except SseTypedValidationError:
            # 🔧R5（R4-BLK-A）contract violation：與 emit_sse 同構——progress 走 send_message
            # 時，內部 _typed_validate 的 dev/CI raise 是 typed 驗證失敗（開發期 bug），要 fail-loud。
            # **不吞、不降 warning**——re-raise 穿透，讓 §0.6 progress 承載的 reasoning 進度事件
            # （research_phase 等）contract violation 在 flag ON dev/CI 下真的紅（與 emit_sse
            # Task 13 Step 1b、G9「dev/CI fail-loud」一致）。flag OFF 時 _typed_validate no-op、
            # 永不產此例外 → 此 except 永不觸發 → 零行為變化。prod 時 _typed_validate 內部已
            # log-loud + return 原始 payload、不 raise 此例外 → 不斷流。
            raise
        except Exception as e:  # :131-133 吞，不 fallback（transport failure 維持現行語義一字不動）
            logger.warning(f"send_sse(progress) send failed (non-critical): {e}")
        wrapper = getattr(handler, "http_handler", None)  # :136-138
        if wrapper and not getattr(wrapper, "connection_alive", True):
            if on_disconnect is None:
                # 🔧R3：未注入 = 接線 bug，fail-loud（不偷 import reasoning、不 silent 退化）。
                raise RuntimeError(
                    "send_sse path='progress' requires on_disconnect injection "
                    "(caller must pass an exception factory; core does not import reasoning)")
            raise on_disconnect()  # 注入的 exception factory（Task 12 傳 ResearchCancelledError）
        return

    if path == "full":
        # message_senders.py:332-365 full path：metadata + PII + store + write。
        # 直接委派 MessageSender.send_message（權威實作，內含 add_message_metadata 注入）。
        # 🔧R3（R2-BLK-2）：typed_validate 掛在 send_message **內部**（wire dict 完成後、write 前，
        # message_senders.py:342 之後），非此處——因 full 的 wire 欄位（message_id/sender_info…）
        # 由 add_message_metadata 注入，只有 send_message 內部才看得到完成的 wire dict。
        # 此掛點 Task 5 落地（見 Task 5 Step 2b）。message_senders 屬 core，import core.sse 無反向依賴。
        return await handler.message_sender.send_message(payload)

    if path == "raw_api":
        # api.py 手寫語義：手動 inject_user_id + raw write_stream，無 guard、無吞例外
        # 包裝（caller 的 route try/except 仍在外層）。
        inject_user_id(payload, handler)
        payload = _typed_validate(payload)  # 🔧 B5：raw_api 的 wire dict 此處已完成，就地驗
        await handler.http_handler.write_stream(payload)
        return

    raise NotImplementedError(f"send_sse path={path!r} not yet implemented")
