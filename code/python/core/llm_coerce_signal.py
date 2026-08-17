# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""LLM 數值欄位 coerce 失敗的品質訊號 sink（票 2026-08-01-k）。

**為什麼這個事件特別該報警**：coerce 失敗代表 LLM 產出了 **schema 沒宣告的形態**
（`"score": "0-100 整數"` 卻回 `'70分'`）。這是**上游品質訊號**，不只是一個要防禦
的髒值——它指向 prompt 退化 / 模型換版 / provider 回應格式漂移。移除欄位讓系統
不崩，但**不修好上游**；沒有告警的話系統會安靜地把一批 item 降到 0 分，而沒有人
知道排序品質正在爛掉。

**為什麼落 DB 不落 log**：`logger.warning` 印在 server log ＝ 無人看得到，且該 log
連留都留不住（票 2026-08-01-j：prod `/app/logs` 未掛 volume，每次 redeploy 全滅）。
訊號寫進 `guardrail_events` 表才留得住、才有巡檢可查。**本模組刻意不依賴 log 檔
持久化，因此不依賴票 -j。** 票 -j 做完只是多一條 log 佐證通道，不改變本模組行為。

**節流設計**：per-field 記憶體聚合 + **窗末 timer flush（單一路徑）**。每次失敗只累加
`_pending` 並確保該 field 有一個 `loop.call_later` 的窗末 timer；**寫 DB 只發生在窗末**。
**沒有「首次立即 flush」的特例**——那個特例會讓 prod（每天數筆 query、間隔遠超窗長）
的每個事件都成為「窗內首次」而恆回 `count=1`。窗長 300s 的理由見
`docs/in progress/plans/llm-numeric-coerce-drop-and-alert-plan.md` §3。

⚠ **能力邊界（誠實描述）**：
  - 有 running loop 時（**生產路徑恆成立**——全 repo 34 個 ask_llm 呼叫點全是
    `await`，已實跑 grep 驗證；且無 worker-thread 路徑碰 LLM）：窗末自動 flush，
    該窗計數必落 DB。
  - **graceful shutdown**：`drain_all()` 已接進
    `webserver/aiohttp_server.py::_on_shutdown`（排在 `_on_cleanup` 關 DB pool 之前，
    aiohttp 保證此順序）→ **redeploy 不丟**。
  - **硬 kill**（SIGKILL / OOM / 斷電）：不執行任何 hook，該窗累積會丟。
    **本模組不宣稱 at-least-once**——要真正不丟就得逐件寫 DB（正是本模組要避免的洗版）。
  - **無 loop 時**：**不自動 flush**。該分支在生產不可達（見上），只服務同步單元測試，
    入口是 `drain_pending_for_test_only()`。兩條路徑**不等價**，此處不假裝等價。

fire-and-forget：任何失敗只寫 Python logger，永不往上拋——品質訊號不得弄壞請求路徑。
"""

import asyncio
import time
from typing import Any, Dict, Optional

from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("llm_coerce_signal")

# 跨模組契約：analytics endpoint 與 scripts/llm_coerce_alert_check.py 都靠這個值
# 查詢 / 比對。改它 = 改 DB 查詢條件；單改一邊會靜默失聯。
# ⚠ 有人改這個字面值時，tests/unit/core/test_llm_coerce_signal.py::
#   test_event_type_constant_is_stable 會紅。
# ⚠ **改名需同時處理既有列**：guardrail_events 裡已寫入的舊 event_type 不會自動跟著
#   改，改名後 endpoint 查不到歷史資料。要改就配一支 UPDATE（這是 migration 議題，
#   測試擋不住——誠實標注，見 coding-conventions §5.5）。
COERCE_EVENT_TYPE = "llm_numeric_coerce_failed"

# 聚合窗長（秒）。刻意寫成模組級常數而非 config：這是「聚合粒度」不是「營運參數」，
# 要調就改 code 過 review，避免變成沒人看的 config 旋鈕。
_MIN_INTERVAL_S = 300

# per-field 狀態。單 process 記憶體聚合——多 worker 時各自聚合，最壞是每 worker
# 每窗一列，仍遠低於逐件寫入。
_pending: Dict[str, Dict[str, Any]] = {}
_last_flush_at: Dict[str, float] = {}   # 診斷用（最後一次 flush 時刻），不參與節流判定
_timers: Dict[str, Any] = {}            # field -> asyncio.TimerHandle（窗末 flush 排程）
# 有 loop 時持有進行中的 task（防被 GC 回收）；完成即 discard，故不會無界增長。
# in-house S-1 / Codex SF2：前一版用 list 且只 append → 生產路徑永不呼叫
# drain_pending_for_test_only（其 docstring 自陳「測試用」）→ 只進不出。改 set + discard。
_inflight: set = set()
# 無 loop 時（同步呼叫）的 payload 佇列，由 drain_pending_for_test_only() 收。
_queued: list = []


def reset_for_test() -> None:
    """清空模組級狀態並取消排程中的 timer。測試專用（fixture 呼叫），生產路徑不呼叫。"""
    for handle in _timers.values():
        try:
            handle.cancel()
        except Exception:  # noqa: BLE001
            pass
    _timers.clear()
    _pending.clear()
    _last_flush_at.clear()
    _inflight.clear()
    _queued.clear()


def pending_count(field: str) -> int:
    """回傳某欄位目前累積未 flush 的次數。測試與診斷用。"""
    entry = _pending.get(field)
    return entry["count"] if entry else 0


def report_coerce_failure(*, field: str, raw_value: Any, schema_desc: Any) -> None:
    """記一次 coerce 失敗。同步、永不拋、永不阻塞請求路徑。

    **單一路徑**：每次失敗都只累加到 `_pending`，並確保該 field 有一個窗末 timer；
    真正寫 DB 只發生在窗末 timer 觸發時。**沒有「首次立即 flush」的特例。**

    為什麼不做「首次立即 flush」（AR R1 三席同抓 + 實跑）：prod 是每天 4.6 筆 query、
    兩筆間隔遠超 300s 窗 → **每個事件都是「窗內首次」** → 每次都立即 flush 帶
    `count=1`，其餘 29 件累在 pending 等一個永遠不來的下一次。結果是 **`count` 在 prod
    恆為 1**，endpoint 那套「加總 details.count」的邏輯完全空轉。改成單一路徑後，
    一個 query 的 30 件 = **一列 `count=30`**（實跑驗證見 plan §3）。

    Args:
        field:       schema 宣告為數值的欄位名
        raw_value:   LLM 實際回傳的（轉不動的）原值
        schema_desc: 該欄位的 schema 描述字串
    """
    try:
        entry = _pending.get(field)
        if entry is None:
            # 新窗開始：`first_raw` 語義 = **本窗首個**樣本。flush 時整個 entry 會被
            # pop 掉，下一窗重建 → 不會出現「這一窗的 count 配上一窗的 raw」的錯歸屬。
            entry = {"count": 0, "first_raw": raw_value, "schema_desc": schema_desc}
            _pending[field] = entry
        entry["count"] += 1
        _ensure_window_end_drain(field)
    except Exception as exc:  # noqa: BLE001 — fire-and-forget
        logger.error(f"report_coerce_failure failed (field={field}): {exc}", exc_info=True)


def _ensure_window_end_drain(field: str) -> None:
    """替 field 排一個窗末 flush（本 loop 上已排過就不重排）。無 running loop 時 no-op。

    ⚠ **必須驗 handle 屬於「當前且未關閉」的 loop**（AR R2 BLOCKER 5）。
    `TimerHandle` 綁在建立它的 loop 上，而 **loop 關閉時 handle 不會從 `_timers` 消失**。
    若只寫 `if field in _timers: return`，一旦有人在 `asyncio.run()` 內觸發過一次，
    那個 stale handle 會**永久擋住**之後所有排程：

        run #1: scheduled          → loop 關閉，handle 留在 _timers
        run #2: skipped (has timer) ← 已中毒
        run #3: skipped
        後續任何 async 排程: skipped → pending 只增不減，**零 emit 零 log**

    引爆點是真實存在的：`tests/unit/test_ranking_string_score_coercion.py` 有 8 個
    `asyncio.run()`、走真的 `report_coerce_failure`、`_MIN_INTERVAL_S` 是真的 300
    → 每個都在窗末前關 loop。而 `_pending`/`_timers` 是 module-level，**跨檔案洩漏**。

    這是「只在跨 loop 邊界才顯形的靜默失效」——正是本 plan 一路在治的病種。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 無 loop：不自動 flush（能力邊界，見模組 docstring）。累積仍在 _pending，
        # 由呼叫端的 drain_pending_for_test_only() / drain_all() 收。
        return

    handle = _timers.get(field)
    if handle is not None:
        handle_loop = getattr(handle, "_loop", None)
        if handle_loop is loop and not loop.is_closed():
            return  # 本 loop 上已有有效 timer，不重排
        # stale（別的 loop / 已關閉的 loop）→ 取消並重排，否則永久中毒
        try:
            handle.cancel()
        except Exception:  # noqa: BLE001
            pass
        _timers.pop(field, None)

    _timers[field] = loop.call_later(_MIN_INTERVAL_S, _on_window_end, field)


def _on_window_end(field: str) -> None:
    """窗末 timer callback：把該 field 累積的計數 flush 成**一列**。"""
    _timers.pop(field, None)
    try:
        _last_flush_at[field] = time.time()
        _flush_field(field)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"window-end flush failed (field={field}): {exc}", exc_info=True)


def _flush_field(field: str) -> None:
    """把 _pending[field] 打包成 payload 送出並清空該 field 的累積。"""
    entry = _pending.pop(field, None)
    if not entry:
        return
    payload = {
        "field": field,
        "count": entry["count"],
        "first_raw": entry["first_raw"],
        "schema_desc": entry["schema_desc"],
        "window_s": _MIN_INTERVAL_S,
    }
    _schedule(payload)


def _schedule(payload: Dict[str, Any]) -> None:
    """把 emit 排進當前 event loop；無 loop 時（同步測試 / worker thread）進 _queued。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _queued.append(payload)
        return
    task = loop.create_task(_safe_emit(payload))
    _inflight.add(task)
    # in-house S-1 / Codex SF2：完成即移除，否則長跑 process 無界增長。
    task.add_done_callback(_inflight.discard)


async def _safe_emit(payload: Dict[str, Any]) -> None:
    try:
        await _emit_event(**payload)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"llm coerce signal emit failed: {exc}", exc_info=True)


def drain_pending_for_test_only() -> None:
    """⚠️ **測試專用，禁止在生產路徑呼叫。** 同步把累積計數送出。

    **為什麼改名（AR R2 BLOCKER 3，agy）**：本函式用 `asyncio.run()`，它**每次建新 loop、
    結束即銷毀**。而落盤鏈上兩層都是 Singleton：`GuardrailLogger`
    （`core/guardrail_logger.py:34-41`）→ `AnalyticsDB`（`core/analytics_db.py:47-58`），
    後者在 PostgreSQL 下持有 **psycopg async connection pool**。pool 會綁到第一次
    `asyncio.run` 那個**短命 loop**，之後每次呼叫要嘛
    `RuntimeError: Event loop is closed` 要嘛洩漏連線。實跑複現：

        run#1 -> pool.loop is current loop: True
        run#2 -> pool.loop is current loop: False, closed: True

    前一版把它改成「在 loop 內呼叫就 raise」——方向對但只治了症狀（讓誤用炸得大聲），
    **根因是 `asyncio.run` 本身**，而 raise 攔不到「在無 loop 環境反覆呼叫」這條。

    **裁決：本函式只服務同步單元測試**，故改名把用途寫進名字裡。依據：生產路徑
    **恆有 running loop**——全 repo 34 個 `ask_llm` 呼叫點全是 `await`（實跑 grep 驗證），
    且無任何 worker-thread 路徑呼叫 `ask_llm`（`asyncio.to_thread` 的使用點全在
    `auth/` 的 DB / email helper，不碰 LLM）。→ **無 loop 分支在生產不可達。**

    **測試使用規範**：呼叫前必須 patch 掉 `_emit_event`（或整條 GuardrailLogger），
    **不得讓它打到真的 AnalyticsDB**——那正是上面那個 pool 綁定問題的觸發條件。

    ⚠ **在 running loop 內呼叫會 raise**（保留 fail-loud）：`asyncio.run` 無法巢狀，
    靜默吞掉會讓呼叫端看到「drain 了但什麼都沒送出」。async 測試請改用
    `await drain_all()`。（撰寫本 plan 時自驗踩到：在 `asyncio.run` 內呼叫 →
    `await_count==0` 而無任何錯誤訊息，一度誤判為設計缺陷。）
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # 無 loop —— 正確用法
    else:
        raise RuntimeError(
            "drain_pending_for_test_only() 只能在無 running loop 時呼叫；"
            "在 async 情境請改用 `await drain_all()`。"
        )

    for field in list(_pending.keys()):
        _last_flush_at[field] = time.time()
        _flush_field(field)
    items, _queued[:] = list(_queued), []
    for payload in items:
        try:
            asyncio.run(_safe_emit(payload))
        except Exception as exc:  # noqa: BLE001
            logger.error(f"drain_pending_for_test_only failed: {exc}", exc_info=True)


async def drain_all() -> None:
    """把所有 field 的累積計數立即 flush 完（不等窗末）。

    **已接進 server shutdown hook**（`webserver/aiohttp_server.py::_on_shutdown`，
    票 2026-08-01-k Task 6b）。必須排在 `_on_cleanup` 的 `AnalyticsDB.close()`
    **之前**——aiohttp 的順序是 on_shutdown → on_cleanup，故掛 on_shutdown 天然滿足。

    ⚠ 仍不保證：**硬 kill**（SIGKILL / OOM / 斷電）不會執行任何 hook，該窗累積會丟。
    graceful shutdown 已覆蓋，硬 kill 是記憶體聚合的固有邊界。
    """
    for field in list(_pending.keys()):
        handle = _timers.pop(field, None)
        if handle is not None:
            handle.cancel()
        _last_flush_at[field] = time.time()
        _flush_field(field)
    if _inflight:
        await asyncio.gather(*list(_inflight), return_exceptions=True)


async def _emit_event(
    *,
    field: str,
    count: int,
    first_raw: Any,
    schema_desc: Any,
    window_s: int,
) -> None:
    """寫一列 guardrail_events。沿用既有 fire-and-forget writer（自身不拋）。"""
    from core.guardrail_logger import GuardrailLogger

    await GuardrailLogger.get_instance().log_event(
        event_type=COERCE_EVENT_TYPE,
        severity="warning",
        details={
            "field": field,
            "count": count,
            "first_raw": _truncate(first_raw),
            "schema_desc": _truncate(schema_desc),
            "window_s": window_s,
        },
    )


def _truncate(value: Any, limit: int = 200) -> Optional[str]:
    """原值可能是 LLM 吐的長字串——截斷避免撐爆 details TEXT 欄位。"""
    if value is None:
        return None
    text = value if isinstance(value, str) else repr(value)
    # 截斷標記用 ASCII "..." 不用 U+2026 —— 這個值會經 JSON → DB TEXT → HTTP → LINE
    # 多層轉手，ASCII 對編碼問題免疫（採 AR R1 nit）。
    return text if len(text) <= limit else text[:limit] + "..."
