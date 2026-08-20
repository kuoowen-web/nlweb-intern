"""
Live Research Handler — HTTP handler for conversation-driven research.

Inherits from DeepResearchHandler to reuse:
- Retrieval infrastructure (prepare(), final_retrieved_items)
- Temporal detection
- SSE streaming (message_sender)
- Connection management (connection_alive_event)

Two entry points:
- runQuery(): Start new research (Stage 1)
- continueResearch(): Continue from checkpoint
"""

import asyncio
import json
import time
import uuid
from typing import Optional

# In-memory state store for dry_run mode (no PG required)
_DRY_RUN_STATE_STORE: dict = {}

from methods.deep_research import DeepResearchHandler
from misc.logger.logging_config_helper import get_configured_logger
from reasoning.live_research.orchestrator import LiveResearchOrchestrator
from reasoning.live_research.stage_state import LiveResearchStageState

logger = get_configured_logger("live_research_handler")


# ── 斷線重連接管（plan: lr-reconnect-continue-takeover, 2026-08-20）─────────────
# 病灶：client 斷線 → detach → 背景 task 續跑到下個 checkpoint；期間 route 層並行 slot
# **仍佔住**（spec §7.3.1 路 A：slot 綁 task 終態，防同 session 並行雙寫）。使用者網路
# 恢復後按「繼續研究」→ POST /continue 撞 `lr_user:{uid}` slot（DR_USER_LIMIT=1）→ 429
# → 前端顯示「連線出了狀況…請再送一次繼續」→ 使用者連按三次全失敗，斷線保護形同未執行。
#
# 治本：**同一 lr_session + 同一 user 又送來新的 continue** 時，這個請求就是「使用者用一條
# 新連線回來接手同一場研究」——接管：cancel 舊 task → 等它真的結束（舊 route 的 done-callback
# 才會釋放 slot）→ 才放行新請求。並行雙寫不變量**加強**而非放寬：舊 task 已終結才啟動新
# task，比「擋掉新請求」更早收斂。
#
# 刻意**不**把「已偵測到 client 離線」列為條件——理由見 takeover_detached_lr_task 內
# `_looked_online` 段（斷線偵測本身不可靠，要求它就等於在最常見情境下不修）。
#
# 邊界（刻意不接管，維持原 429）：
#   - user_id 不符 = 不是同一個人（防猜 UUID 殺別人的研究）。
#   - cancel 後逾時仍沒結束 = 不確定舊 task 死透，寧可擋（回 'timeout'，route 照常走 429）。
#
# 安全性：cancel 舊 task 不等於丟進度——orchestrator 每個 durable boundary 都
# `_persist_checkpoint_boundary` 落 DB，resume 用 `next_i = last_completed_section_index + 1`
# 冪等續跑。且舊 task 本來就在「離線防呆上限」下跑到下個 checkpoint 就會停，接管只是把
# 「離線續燒」提早收掉，與防燒錢目標同向。
_ACTIVE_LR_TASKS: dict = {}   # lr_session_id -> (asyncio.Task, LiveResearchHandler)

# 接管時等舊 task 真正結束的上限秒數。逾時不接管（不可假設它死了就放行——那就是雙寫）。
LR_TAKEOVER_TIMEOUT_SECONDS = 10.0


def _register_lr_task(lr_session_id: Optional[str], task: "asyncio.Task", handler) -> None:
    """登記背景 LR task，供跨 request 的重連接管查詢。lr_session_id 為空則不登記。"""
    if not lr_session_id:
        return
    _ACTIVE_LR_TASKS[lr_session_id] = (task, handler)


def _unregister_lr_task(lr_session_id: Optional[str], task: "asyncio.Task") -> None:
    """撤銷登記。只在登記的 task 就是自己時才刪——接管後 registry 已換成新 task，
    舊 task 的 done-callback 不可把新的一併刪掉。"""
    if not lr_session_id:
        return
    entry = _ACTIVE_LR_TASKS.get(lr_session_id)
    if entry is not None and entry[0] is task:
        _ACTIVE_LR_TASKS.pop(lr_session_id, None)


async def takeover_detached_lr_task(
    lr_session_id: str,
    user_id: str = "",
    timeout: float = LR_TAKEOVER_TIMEOUT_SECONDS,
) -> str:
    """重連續跑前，接管同一 lr_session 的離線背景 task。

    Returns（皆有 log，不 silent）:
      - "none"        : 沒有在跑的舊 task（正常路徑，直接放行）
      - "taken_over"  : 舊 task 已 cancel 並確認結束（放行）
      - "taken_over_stale": 同上，且舊 task 當時仍被標成在線（未偵測到的斷線，見下方說明）
      - "owner_mismatch": user_id 不符（不接管，維持 429）
      - "timeout"     : cancel 後逾時未結束（不接管，維持 429）
    """
    entry = _ACTIVE_LR_TASKS.get(lr_session_id)
    if entry is None:
        return "none"
    task, old_handler = entry
    if task.done():
        _unregister_lr_task(lr_session_id, task)
        return "none"

    old_user = getattr(old_handler, "user_id", "") or ""
    if (user_id or "") != old_user:
        logger.warning(
            f"[LIVE RESEARCH] Takeover rejected: user mismatch "
            f"(lr_session={lr_session_id}, requester={user_id!r}, owner={old_user!r})"
        )
        return "owner_mismatch"

    # 「舊 client 是否還在線」不可當作接管的硬條件——這正是本 bug 最常見的形態：
    # 使用者網路斷掉時 server **不會馬上知道**。斷線偵測只靠「往 socket 寫東西失敗」
    # （aiohttp_streaming_wrapper.start_heartbeat → write_keepalive → _mark_disconnected），
    # 而半開 TCP 上寫 13 bytes 的 keepalive 會先進 kernel 緩衝、由 TCP 重傳撐十幾分鐘才失敗
    # （前面若有 nginx，server 端 socket 更是完全看不到 client 側斷線）。也就是說：使用者
    # 網路恢復、按下「繼續」的當下，舊 task 多半**仍被標成在線**。若要求「已偵測到離線」
    # 才接管，等於在最常見的情境下不接管 → 照樣 429 → bug 沒修好。
    #
    # 改用「同 user + 同 lr_session + 又來了一個新的 continue 請求」當判準：LR 的回覆入口
    # 只在串流停下（checkpoint / 中斷 / resume）時才出現，同一個分頁不會在自己串流還在跑時
    # 送 continue。所以這個新請求代表「使用者正在用一條新連線驅動同一場研究」——舊那條
    # 就是該讓位的那條。
    #
    # 已知取捨（誠實記錄）：使用者在第二個分頁開同一場研究並按繼續時，第一個分頁正在跑的
    # 串流會被接管掉（後按的贏），研究本身不會掉——從最後一個 boundary 續跑。相對於「第二
    # 個分頁永遠 429、使用者卡死」，這個語意較合理，且正是斷線情境下唯一能自動復原的作法。
    alive = getattr(old_handler, "connection_alive_event", None)
    _looked_online = alive is not None and alive.is_set()
    if _looked_online:
        logger.warning(
            f"[LIVE RESEARCH] Reconnect takeover: 舊 task 仍被標成在線，但同 user 同 session "
            f"送來新的 continue → 判定舊連線已失效（半開 TCP / 尚未偵測到的斷線），一併接管 "
            f"(lr_session={lr_session_id})"
        )
        # 先把舊 handler 標成離線（與 _lr_mark_client_disconnected 同語意的成對設置），
        # 讓舊 task 在被 cancel 前的殘餘 emit 直接 drop、不往死連線寫。
        alive.clear()
        _old_detach = getattr(old_handler, "_lr_detach_event", None)
        if _old_detach is not None:
            _old_detach.set()

    logger.warning(
        f"[LIVE RESEARCH] Reconnect takeover: cancelling detached background task "
        f"(lr_session={lr_session_id}, task={task.get_name()}) — 進度已 per-boundary 落盤，"
        f"新的 continue 將從最後一個 boundary 續跑"
    )
    task.cancel()
    # 用 asyncio.wait（不是 gather / await task）：wait **不**取走 task 的 exception，
    # 讓 `_on_lr_research_complete` 仍是唯一的終態記錄點（不重複記、不吞例外）。
    await asyncio.wait({task}, timeout=timeout)
    if not task.done():
        logger.error(
            f"[LIVE RESEARCH] Reconnect takeover TIMEOUT: 舊 task {timeout}s 內未結束 "
            f"(lr_session={lr_session_id}) — 不放行新 continue（避免同 session 並行雙寫）"
        )
        return "timeout"

    # done-callback 是 loop.call_soon 排程的：舊 route 的 slot-release callback 要再讓出
    # 一次 event loop 才會跑到。多讓幾次（便宜、無副作用），確保 slot 在 try_acquire 前釋放。
    for _ in range(3):
        await asyncio.sleep(0)
    _unregister_lr_task(lr_session_id, task)
    _result = "taken_over_stale" if _looked_online else "taken_over"
    logger.info(
        f"[LIVE RESEARCH] Reconnect takeover done — 舊 task 已結束，放行 continue "
        f"(lr_session={lr_session_id}, result={_result})"
    )
    return _result


class LiveResearchHandler(DeepResearchHandler):
    """
    Handler for Live Research mode.

    Inherits retrieval/ranking infrastructure from DeepResearchHandler.
    Adds 6-Stage conversation-driven research orchestration.
    """

    def __init__(self, query_params, http_handler):
        super().__init__(query_params, http_handler)

        # Track C C2 (F-1 fix, 2026-05-28): extract enable_gap_enrichment per-request toggle.
        # CANNOT inherit from DR DeepResearchHandler.__init__ — DR only sets enable_kg +
        # enable_web_search (verified methods/deep_research.py:49-60).
        # Pattern follows DR's enable_web_search extraction (deep_research.py:58-60).
        # Per-request toggle (vs CONFIG yaml gap_knowledge_enrichment which is process-wide
        # Analyst prompt builder flag) — 兩層 toggle 各司其職。
        egp = query_params.get('enable_gap_enrichment', 'false')
        self.enable_gap_enrichment = egp in [True, 'true', 'True', '1']
        logger.info(f"  Enable Gap Enrichment: {self.enable_gap_enrichment}")

        self.session_id = query_params.get("session_id", "")
        self.user_id = query_params.get("user_id", "")
        self.org_id = query_params.get("org_id", "")
        # lr_session_id: server-generated UUID for state persistence (separate from frontend session_id)
        self.lr_session_id: Optional[str] = None
        # _lr_research_task holds the named asyncio.Task while the LR orchestrator runs.
        # 斷線不取消（plan: lr-sse-reconnect-resume, 2026-06-15 CEO 拍板）：client 斷線
        # **不** cancel 此 task；named task 仍保留供「使用者明確 stop」或防呆上限觸發的
        # 內部 cancel（disconnect 本身不 cancel，見 routes/api.py::_lr_mark_client_disconnected）。
        self._lr_research_task: Optional[asyncio.Task] = None
        # 連線釋放治本（plan: lr-sse-connection-release-fix, 2026-06-22）：
        # client 斷線時 set 此 event，讓 runQuery / continueResearch 的 detach-aware
        # await 偵測到並提早 return（背景 task 不 cancel、繼續跑）。與 connection_alive_event
        # 並列由 _lr_mark_client_disconnected 觸發（single source of truth）。
        self._lr_detach_event: asyncio.Event = asyncio.Event()
        # 斷線標記：首次偵測 client 離線的 server epoch 時戳（給 orchestrator 防呆上限起點）。
        # 只是「本次 request 內」傳 offline 起點給 orchestrator 的橋；真正跨 instance 防燒錢
        # 累積上限狀態進 state.offline_since / offline_capped（stage_state.py），非此 instance attr。
        self._client_offline_since: Optional[float] = None
        # 匿名 / fallback session 偵測：_create_lr_session 成功建 DB session（或 dry_run
        # in-memory store 有效）才設 True。False 時 runQuery 會 emit user-facing 警告（不可 silent fail）。
        self._lr_session_persisted: bool = False
        # 警告文案分流用：fallback 成因。"anonymous" = 未登入；"db_error" = 已登入但
        # create_session 失敗。Review S1：db_error 分支的 user 是登入的，文案不得稱「你未登入」。
        self._lr_persist_skip_reason: Optional[str] = None
        # 票 2026-07-28-k：log_query_complete 冪等 flag——task done-callback 與
        # except/早退分支可能都跑到（執行順序不定，同 event-loop thread 無並發 race），
        # 先到者記、後到者 no-op。
        self._lr_analytics_completed: bool = False
        logger.info(f"LiveResearchHandler initialized (session={self.session_id})")

    def _is_dry_run(self) -> bool:
        """Check if dry_run mode is requested via query params or config."""
        from core.config import CONFIG
        return (
            self.query_params.get("dry_run") == "true"
            or CONFIG.reasoning_params.get("features", {}).get("live_research_dry_run", False)
        )

    def _is_mock_bab(self) -> bool:
        """mock_bab：fixture 已含完整 ContextMap + searches，Stage 0 retrieval 無用。"""
        from core.config import CONFIG
        return CONFIG.reasoning_params.get("features", {}).get("live_research_mock_bab", False)

    async def _create_lr_session(self) -> str:
        """Create a server-side session with proper UUID for state persistence.

        Falls back to a bare UUID (no DB row) if session creation fails,
        so the rest of the pipeline is never blocked.
        """
        fallback_id = str(uuid.uuid4())

        # Dry-run: no DB needed; in-memory store will use this UUID as key
        if self._is_dry_run():
            self._lr_session_persisted = True  # in-memory store 有效，resume 可用，非真 fallback
            logger.info(f"[LIVE RESEARCH] Dry-run: using UUID without DB session (key={fallback_id})")
            return fallback_id

        # Only create DB session when we have real user/org IDs (UUID-compatible values).
        # Passing placeholder strings like "anonymous"/"default" fails PG UUID constraints.
        if not self.user_id or not self.org_id:
            self._lr_persist_skip_reason = "anonymous"
            logger.info(f"[LIVE RESEARCH] No user/org ID, using bare UUID without DB session (key={fallback_id})")
            return fallback_id

        try:
            from core.session_service import SessionService
            service = SessionService()
            result = await service.create_session(
                user_id=self.user_id,
                org_id=self.org_id,
                title=f"Live Research: {self.query[:50]}",
            )
            session_id = result["id"]
            self._lr_session_persisted = True
            logger.info(f"[LIVE RESEARCH] Created server session: {session_id}")
            return session_id
        except Exception as e:
            # 此分支 user 是登入的（user_id/org_id 有值才會進 try）—— skip 成因是 DB 故障
            # 而非未登入；runQuery 警告文案依此分流（review S1）。
            self._lr_persist_skip_reason = "db_error"
            logger.error(f"[LIVE RESEARCH] Failed to create DB session, using bare UUID: {e}")
            # Keep the fallback UUID. 注意（review N3）：此分支 user_id 有值，之後 _save_state
            # 的 guard 不會 skip，會對不存在的 row 跑 update_session（既有行為，本 plan 不改）。
            return fallback_id

    async def runQuery(self):
        """Start new Live Research — enters Stage 1."""
        logger.info(f"[LIVE RESEARCH] Starting: {self.query}")

        # AR R1：pre-task cancel 縫用 local flag 判斷（不可用 self._lr_research_task
        # is None——finally 非 detach 路徑會清 ref）。task 建立前被 cancel（await 期間）
        # → 無 done-callback 可記終態 → 由 except CancelledError 條件補記。
        _task_created = False
        try:
            # Step 1: Create server-side session with proper UUID
            self.lr_session_id = await self._create_lr_session()

            # 票 2026-07-28-k：把後端權威 lr_session_id 回填 queries.conversation_id
            # （跨 run 串聯 key）。紅線（lessons-live-research 雙 PG row）：掛的是
            # _create_lr_session 的權威 UUID，不是前端另建 row 的 id、不是 "sess_xxx"。
            if getattr(self, 'query_id', None) and self.lr_session_id:
                try:
                    from core.query_logger import get_query_logger
                    get_query_logger().update_query_conversation_id(
                        self.query_id, self.lr_session_id
                    )
                except Exception as e:
                    logger.warning(
                        f"[LIVE RESEARCH] Failed to backfill conversation_id (non-fatal): {e}"
                    )

            # Step 2: Notify frontend of the server-generated session UUID via direct SSE
            if self.lr_session_id and self.http_handler is not None:
                try:
                    await self.http_handler.write_stream({
                        "message_type": "live_research_session_created",
                        "session_id": self.lr_session_id,
                    })
                    logger.info(f"[LIVE RESEARCH] Sent session_created event to frontend: {self.lr_session_id}")
                except Exception as e:
                    logger.warning(f"[LIVE RESEARCH] Could not send session_created event: {e}")

            # 不可 silent fail：fallback（未登入 / DB session 建立失敗）session 不會寫 PG，
            # 之後無法 resume。明確告知 user，而非讓他以為已儲存。
            # （dry_run 已在 _create_lr_session 標記 persisted=True，不會誤觸此警告。）
            if not self._lr_session_persisted:
                if self._lr_persist_skip_reason == "anonymous":
                    warn_text = (
                        "提醒：你目前未登入，這份研究的進度不會被儲存，"
                        "之後也無法回來接續。若要保留與接續，請先登入再開始。"
                    )
                else:
                    # db_error（或未知成因）：user 可能是登入的（review S1），
                    # 文案不得稱「你未登入」、不得建議「請先登入」（無效行動建議）。
                    warn_text = (
                        "提醒：目前暫時無法建立這份研究的儲存空間，"
                        "進度不會被儲存，之後也無法回來接續。"
                    )
                if self.http_handler is not None:
                    try:
                        await self.http_handler.write_stream({
                            "message_type": "live_research_narration",
                            "text": warn_text,
                        })
                        logger.info(
                            "[LIVE RESEARCH] Emitted non-persisted-session warning "
                            f"(reason={self._lr_persist_skip_reason}, lr_session={self.lr_session_id})"
                        )
                    except Exception as e:
                        logger.warning(
                            f"[LIVE RESEARCH] Could not send non-persisted warning: {e}"
                        )
                else:
                    # 降級必留痕（review N2）：headless（http_handler=None）時警告無法送達，
                    # 至少留 log，不可無聲消失。
                    logger.warning(
                        "[LIVE RESEARCH] Non-persisted session but http_handler is None; "
                        f"user warning not delivered (reason={self._lr_persist_skip_reason}, "
                        f"lr_session={self.lr_session_id})"
                    )

            if self._is_dry_run() or self._is_mock_bab():
                mode = "Dry-run" if self._is_dry_run() else "mock_bab"
                logger.info(f"[LIVE RESEARCH] {mode}: skipping prepare()")
                self.final_retrieved_items = []
            else:
                # LR: 跳過 DR-style clarification。
                # 模糊查詢由 Associator 在 Stage 1 處理（ContextMap = clarification context）。
                self.query_params["skip_clarification"] = "true"
                # Reuse parent's prepare() for retrieval
                await self.prepare()
                if self.query_done:
                    # 早退（clarification / guardrail）也是終態——記 complete 再 return
                    self._log_lr_query_complete()
                    return self.return_value

            # Create orchestrator
            orchestrator = LiveResearchOrchestrator(handler=self, dry_run=self._is_dry_run())

            # Wrap orchestrator call in named asyncio.Task. HTTP connection stays open
            # (we still await the task). 斷線**不**取消（plan: lr-sse-reconnect-resume,
            # 2026-06-15 CEO 拍板）：disconnect 只標離線，orchestrator.start() 把 Stage 1
            # 跑到第一個 checkpoint 才停存檔。named task 仍可被「使用者明確 stop」或防呆上限
            # 內部 cancel，但 disconnect 本身不 cancel（routes/api.py::_lr_mark_client_disconnected）。
            self._lr_research_task = asyncio.create_task(
                orchestrator.start(
                    query=self.query,
                    initial_items=self.final_retrieved_items,
                ),
                name=f"lr_runQuery_{self.lr_session_id or 'unknown'}",
            )
            _task_created = True  # task 已建立 → 終態歸 done-callback（見 except CancelledError）
            self._lr_research_task.add_done_callback(self._on_lr_research_complete)
            # 重連接管登記（plan: lr-reconnect-continue-takeover）：斷線後這個 task 會 detach
            # 續跑並持續佔住 slot，使用者回來按「繼續」時要能被 takeover_detached_lr_task 找到。
            _register_lr_task(self.lr_session_id, self._lr_research_task, self)
            # Detach-aware await（plan: lr-sse-connection-release-fix, 2026-06-22）：
            # 同時等「task 完成」與「client 離線」。離線先到 → 提早 return，
            # **不** cancel task（disconnect-no-cancel 保留），task 在後台跑到下個
            # checkpoint，由其 _persist_checkpoint_boundary 落 DB。done-callback
            # 仍存活負責 exception retrieval。HTTP 連線釋放由 route 層 finish_response 收尾。
            # slot release：detach 終態交由 route 層 closure done-callback（路 A，CEO-Locked #3 重議）。
            detach_waiter = asyncio.ensure_future(self._lr_detach_event.wait())
            _detached = False
            try:
                try:
                    done, _pending = await asyncio.wait(
                        {self._lr_research_task, detach_waiter},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    if not detach_waiter.done():
                        detach_waiter.cancel()

                if self._lr_research_task not in done:
                    # Detached：client 離線，task 仍在跑。提早 return，**不** 清 _lr_research_task
                    # reference（event loop + done-callback 持有 → task 不被 GC；且 route 需讀此
                    # ref 掛 slot-release done-callback），**不** trailing save。
                    _detached = True
                    logger.info(
                        f"[LIVE RESEARCH] runQuery detached (client offline) — "
                        f"task continues in background (lr_session={self.lr_session_id})"
                    )
                    self.return_value.update({"status": "detached"})
                    return self.return_value

                try:
                    state = self._lr_research_task.result()
                except asyncio.CancelledError:
                    logger.info(
                        f"[LIVE RESEARCH] runQuery task cancelled "
                        f"(lr_session={self.lr_session_id})"
                    )
                    raise
            finally:
                # C2 修正（Gemini，2026-06-22）：清理涵蓋整個等待邏輯（含外部 CancelledError
                # 強 cancel handler coroutine 的情況），與原 code「必定清理 task 參照」契約一致。
                # **detach 路徑除外**——detach 刻意保留 reference 讓 task 不被 GC + 讓 route 掛
                # slot-release done-callback。清理只在「非 detach 的終態」做。
                if not _detached:
                    self._lr_research_task = None

            # 持久化責任歸背景 task 內部 _persist_checkpoint_boundary → _persist_progress
            # → _save_state（每 boundary 都寫、idempotent）。route-path trailing save 已移除
            # （plan: lr-sse-connection-release-fix, 2026-06-22, CEO-Locked #2）：detach 後
            # 保留會與 task 內最後寫雙寫、可能用舊 snapshot 覆寫新。
            self.return_value.update({
                "status": "checkpoint",
                "stage": state.current_stage,
                "checkpoint_prompt": state.checkpoint_prompt,
            })

            return self.return_value

        except asyncio.CancelledError:
            # Cancellation 不再來自 client disconnect（plan: lr-sse-reconnect-resume —
            # disconnect 只標離線、不 cancel）。仍可能來自「使用者明確 stop」或防呆上限觸發
            # 的內部 cancel — 屬正當降級路徑：propagate 讓 routes/api.py 的
            # CancelledError handler 收尾 SSE response。Do not log as error.
            # AR R1 三家同抓：coroutine 在 task 建立前被 cancel（_create_lr_session /
            # prepare await 期間，server shutdown 類）→ 無 done-callback 可記終態，
            # start row 懸掛。只在 task 未建立時補記；task 已建立則終態歸 done-callback
            # （無條件補掛會在 detach 前強 cancel 時把「進行中」誤標）。
            # 不可用 self._lr_research_task is None 判斷——finally 會清 ref。
            if not _task_created:
                self._log_lr_query_complete(
                    error_occurred=False, error_message="cancelled: before task creation"
                )
            raise
        except Exception as e:
            logger.error(f"[LIVE RESEARCH] Error: {e}", exc_info=True)
            # task 建立前的失敗（_create_lr_session / prepare / orchestrator 建構）兜底；
            # task 建立後 result() re-raise 時 done-callback 也會記——執行順序不保證
            # （實測 done-callback 先跑），冪等 flag 保證單次，且**必走 from_exc 分類**
            # （ResearchCancelledError 是 Exception subclass，兩掛點分類不一致會把
            # cancel 誤標成 error）。
            self._log_lr_query_complete_from_exc(e)
            raise

    async def continueResearch(self, user_message: str = "", auto_continue: bool = False, nav_action: str = ""):
        """Continue from checkpoint — processes user response and advances stage.

        nav_action: backward navigation 動作（""=正常前進 / "back_one" / "restart"，
        plan: lr-backward-nav）。透傳給 orchestrator.continue_from_checkpoint。
        """
        # Use server-generated UUID passed back from frontend for state lookup
        self.lr_session_id = self.query_params.get("lr_session_id", "") or self.lr_session_id
        logger.info(
            f"[LIVE RESEARCH] Continue: lr_session={self.lr_session_id} session={self.session_id} "
            f"auto={auto_continue} msg='{user_message[:50]}...'"
        )

        # AR R1/R2：pre-task cancel 縫（cancel 可能發生在 _load_state await 期間）。
        # 見 runQuery 同段 local flag 說明。
        _task_created = False
        try:
            # Load state from session
            state = await self._load_state()
            if state is None:
                # R5 fix（RCA v3 ROOT 5）：state 找不到時**不可** silent fallback runQuery()。
                #
                # 舊行為（已移除）：silent re-run runQuery → mock_bab path 重 emit Stage 1
                # 初始 20-topic fixture checkpoint → user 在 Stage 5 reply 但被退回 Stage 1，
                # 完全不知後端發生什麼事（違反 CLAUDE.md no-silent-fail）。
                #
                # 新行為：emit 明示 narration + 回 error response，由 frontend 決定下一步。
                # 不重 emit Stage 1，不靜默 re-run。
                logger.warning(
                    f"[LIVE RESEARCH] No state found in continueResearch() — "
                    f"lr_session_id={self.lr_session_id!r} user_id={self.user_id!r} "
                    f"org_id={self.org_id!r}；emit error narration (no silent fallback)"
                )
                # Direct SSE narration via http_handler (message_sender 可能尚未初始化)
                narration_text = (
                    "找不到先前的研究 session（可能已過期、被重置、或 SSE 連線中斷後未能恢復）。"
                    "請點「重新開始研究」重新進入新的研究流程。"
                )
                # 1) 優先用 message_sender（與 orchestrator 一致路徑）
                sender = getattr(self, "message_sender", None)
                if sender is not None:
                    try:
                        await sender.send_message({
                            "message_type": "live_research_narration",
                            "text": narration_text,
                            # terminal=True：這是「研究停在這裡、重送 continue 也不會好」的終止性
                            # 旁白。前端據此渲染終止狀態，**不可**再退化成「連線中斷…可從中斷處
                            # 繼續」（那會讓使用者無限重按繼續，正是本次 bug 的症狀之一）。
                            "terminal": True,
                        })
                    except Exception as e:
                        logger.warning(
                            f"[LIVE RESEARCH] message_sender narration emit failed: {e}"
                        )
                # 2) Fallback：直接 write_stream（message_sender 不在時仍能到前端）
                elif self.http_handler is not None:
                    try:
                        await self.http_handler.write_stream({
                            "message_type": "live_research_narration",
                            "text": narration_text,
                            "terminal": True,   # 見上方 sender 路徑說明
                        })
                    except Exception as e:
                        logger.warning(
                            f"[LIVE RESEARCH] write_stream narration emit failed: {e}"
                        )
                # Return error response — frontend 看到 status=error 應 prompt user
                # 重新開始研究，而非繼續 polling
                self.return_value.update({
                    "status": "error",
                    "error": "state_not_found",
                    "message": narration_text,
                })
                self._log_lr_query_complete(
                    error_occurred=True, error_message="state_not_found"
                )
                return self.return_value

            # addendum C-3 / D (Track A sprint 2026-05-28): legacy schema gate
            # — v1 session (schema_version < 2, sprint 前舊 session) 禁用 revise/continue
            # 操作（read-only export 由 separate endpoint 處理）。
            # User 必須匯出後封存，新需求請開新 session。
            if getattr(state, "schema_version", 1) < 2:
                legacy_msg = (
                    "此研究紀錄為舊版格式，目前僅支援讀取與匯出，無法繼續編輯。"
                    "建議匯出此份研究後，重新開始新的研究。"
                )
                logger.warning(
                    f"[LIVE RESEARCH] Rejected continueResearch on legacy schema session "
                    f"(schema_version={getattr(state, 'schema_version', 'unset')}, "
                    f"lr_session_id={self.lr_session_id!r})"
                )
                sender = getattr(self, "message_sender", None)
                if sender is not None:
                    try:
                        await sender.send_message({
                            "message_type": "live_research_narration",
                            "text": legacy_msg,
                            "terminal": True,   # 見 state_not_found 分支說明
                        })
                    except Exception as e:
                        logger.warning(
                            f"[LIVE RESEARCH] message_sender legacy narration emit failed: {e}"
                        )
                # T1 Fix 3: Fallback to http_handler.write_stream when message_sender is None
                # (對齊 state_not_found gate 的雙路 pattern，確保 narration 能到達前端)
                elif self.http_handler is not None:
                    try:
                        await self.http_handler.write_stream({
                            "message_type": "live_research_narration",
                            "text": legacy_msg,
                            "terminal": True,   # 見 state_not_found 分支說明
                        })
                    except Exception as e:
                        logger.warning(
                            f"[LIVE RESEARCH] write_stream legacy narration emit failed: {e}"
                        )
                self.return_value.update({
                    "status": "error",
                    "error": "legacy_schema_session",
                    "message": legacy_msg,
                })
                self._log_lr_query_complete(
                    error_occurred=True, error_message="legacy_schema_session"
                )
                return self.return_value

            # Create orchestrator and continue
            orchestrator = LiveResearchOrchestrator(handler=self, dry_run=self._is_dry_run())

            # Wrap in named task. 斷線**不**取消（plan: lr-sse-reconnect-resume, 2026-06-15
            # CEO 拍板）：Stage 5 writer 在此可能 in-flight（從 Stage 4 → 5 advance），但
            # client 斷線只標離線、writer 跑完當前 section 到 per-section checkpoint 才停
            # （per-section persist + idempotent resume，next_i = last_completed_section_index + 1
            # 防 double-write）。named task 仍保留供「使用者明確 stop」或防呆上限觸發的內部 cancel；
            # disconnect 本身不 cancel（舊 UX-4 cancel 用途是 SSE 收尾 abort，per-section
            # checkpoint 已是中斷點 — VP-7/693ac217e；移除 disconnect cancel 不 regress）。
            self._lr_research_task = asyncio.create_task(
                orchestrator.continue_from_checkpoint(
                    state=state,
                    user_message=user_message,
                    auto_continue=auto_continue,
                    nav_action=nav_action,
                ),
                name=f"lr_continueResearch_{self.lr_session_id or 'unknown'}",
            )
            _task_created = True  # task 已建立 → 終態歸 done-callback（見 except CancelledError）
            self._lr_research_task.add_done_callback(self._on_lr_research_complete)
            # 重連接管登記（plan: lr-reconnect-continue-takeover）：見 runQuery 同段註解。
            _register_lr_task(self.lr_session_id, self._lr_research_task, self)
            # Detach-aware await（plan: lr-sse-connection-release-fix, 2026-06-22）。見 runQuery 同段註解。
            detach_waiter = asyncio.ensure_future(self._lr_detach_event.wait())
            _detached = False
            try:
                try:
                    done, _pending = await asyncio.wait(
                        {self._lr_research_task, detach_waiter},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    if not detach_waiter.done():
                        detach_waiter.cancel()

                if self._lr_research_task not in done:
                    _detached = True
                    logger.info(
                        f"[LIVE RESEARCH] continueResearch detached (client offline) — "
                        f"task continues in background (lr_session={self.lr_session_id})"
                    )
                    self.return_value.update({"status": "detached"})
                    return self.return_value

                try:
                    state = self._lr_research_task.result()
                except asyncio.CancelledError:
                    logger.info(
                        f"[LIVE RESEARCH] continueResearch task cancelled "
                        f"(lr_session={self.lr_session_id})"
                    )
                    raise
            finally:
                # C2 修正（見 runQuery 同段註解）：清理涵蓋整個等待邏輯，detach 路徑除外。
                if not _detached:
                    self._lr_research_task = None

            # 持久化責任歸背景 task 內部（見 runQuery 同段註解）。route-path trailing save
            # 已移除（plan: lr-sse-connection-release-fix, 2026-06-22, CEO-Locked #2）。
            self.return_value.update({
                "status": "checkpoint" if state.stage_status == "checkpoint" else "completed",
                "stage": state.current_stage,
                "checkpoint_prompt": state.checkpoint_prompt,
            })

            return self.return_value

        except asyncio.CancelledError:
            # AR R1/R2 3k：coroutine 在 task 建立前被 cancel（_load_state await 期間）→
            # 無 done-callback 可記終態，start row 懸掛。只在 task 未建立時補記；
            # task 已建立則終態歸 done-callback（見 runQuery 同段完整說明）。
            if not _task_created:
                self._log_lr_query_complete(
                    error_occurred=False, error_message="cancelled: before task creation"
                )
            raise
        except Exception as e:
            logger.error(f"[LIVE RESEARCH] Continue error: {e}", exc_info=True)
            self._log_lr_query_complete_from_exc(e)  # 同 runQuery：必走分類（cancel 不誤標）
            raise

    def _log_lr_query_complete(self, error_occurred: bool = False, error_message: str = "") -> None:
        """LR run 終態單點記錄（票 2026-07-28-k）。冪等：同 handler instance 只記一次。

        可從同步 context 呼叫（done-callback）：log_query_complete 是同步 DB UPDATE，
        與 codebase 現況一致（baseHandler.py:394 亦在 async 流程內同步呼叫）。
        cost_usd 刻意不填（獨立票 2026-07-28-l）。
        """
        # getattr 防禦：與下方 query_id / start_time / final_retrieved_items 防禦同風格
        # ——涵蓋繞過 __init__ 的路徑（如 __new__ 建的 handler、未來子類），flag 缺失
        # 視為「未完成」而非 AttributeError。
        if getattr(self, '_lr_analytics_completed', False):
            return
        if not getattr(self, 'query_id', None):
            # route 未打點（直接實例化 handler 的測試/內部路徑）→ 無 parent row，跳過
            return
        self._lr_analytics_completed = True
        try:
            from core.query_logger import get_query_logger
            start = getattr(self, '_lr_analytics_query_start_time', None)
            latency_ms = (time.time() - start) * 1000 if start else 0
            get_query_logger().log_query_complete(
                query_id=self.query_id,
                latency_total_ms=latency_ms,
                num_results_retrieved=len(getattr(self, 'final_retrieved_items', None) or []),
                error_occurred=error_occurred,
                error_message=(error_message or "")[:500],
            )
        except Exception as e:
            logger.warning(f"[LIVE RESEARCH] Failed to log query complete (non-fatal): {e}")

    def _log_lr_query_complete_from_exc(self, exc: BaseException) -> None:
        """例外終態分類單點（票 2026-07-28-k）。

        **必須**由 done-callback 與 runQuery/continueResearch 的 except Exception
        兜底共用：ResearchCancelledError 繼承 Exception（orchestrator_base.py:15），
        task re-raise 時兩掛點都會跑，**執行順序不保證**（實測 done-callback 先跑：
        task 完成時 callbacks 依註冊序 call_soon，user callback 排在 awaiter wakeup
        之前）——冪等 flag 讓先到者定終態，兩處分類不一致會把 cancel 誤記成
        error 且修不回來。
        """
        from reasoning.orchestrator_base import ResearchCancelledError
        if isinstance(exc, (ResearchCancelledError, asyncio.CancelledError)):
            self._log_lr_query_complete(
                error_occurred=False, error_message=f"cancelled: {exc}"
            )
        else:
            self._log_lr_query_complete(error_occurred=True, error_message=str(exc))

    def _on_lr_research_complete(self, task: asyncio.Task):
        """Callback when background LR task completes / fails / is cancelled.

        Mirrors DR `_on_research_complete`. Without this callback, exceptions
        raised inside the task would be silently swallowed.

        plan: lr-disconnect-midstage-persist — 分流：
        - asyncio.CancelledError（明確 cancel）→ info（正常）。
        - ResearchCancelledError（soft-interrupt / 殘留斷線例外）→ info + 提示
          進度已由背景 task 內部 per-boundary persist（Task 3）落盤，非資料遺失。
        - 其他 exception → error（真異常）。**進度仍靠 Task 3 每 topic persist
          兜底**——callback 不做 async persist（done-callback 是同步 context，
          且 in-memory state 落盤責任已在 orchestrator 內部每 boundary 完成）。
        """
        from reasoning.orchestrator_base import ResearchCancelledError
        # 重連接管撤銷登記（plan: lr-reconnect-continue-takeover）：done-callback 是所有終態
        # （完成 / 失敗 / cancel）的必經點，放這裡才不會漏。`_unregister_lr_task` 只刪「登記的
        # 就是自己」的 entry，接管後新 task 的登記不會被舊 task 的 callback 誤刪。
        _unregister_lr_task(getattr(self, "lr_session_id", None), task)
        try:
            exc = task.exception()
            if exc is None:
                self._log_lr_query_complete()
                return
            if isinstance(exc, ResearchCancelledError):
                self._log_lr_query_complete_from_exc(exc)
                logger.info(
                    f"[LIVE RESEARCH] Background task stopped by disconnect/interrupt "
                    f"(lr_session={getattr(self, 'lr_session_id', None)}): {exc}. "
                    f"進度已由 per-boundary persist 落盤（可從中途進度手動續跑）。"
                )
                return
            self._log_lr_query_complete_from_exc(exc)
            logger.error(
                f"[LIVE RESEARCH] Background task failed "
                f"(lr_session={getattr(self, 'lr_session_id', None)}): {exc}",
                exc_info=exc,
            )
        except asyncio.CancelledError:
            self._log_lr_query_complete(
                error_occurred=False, error_message="cancelled: task cancelled"
            )
            logger.info(
                f"[LIVE RESEARCH] Background task cancelled: {task.get_name()}"
            )
        except asyncio.InvalidStateError:
            pass

    async def _save_state(self, state: LiveResearchStageState):
        """存 state 到 session — 直接呼叫 SessionService（CEO：不需要 ContextMapStore wrapper）。

        Uses self.lr_session_id (server-generated UUID) for DB persistence.
        Dry-run mode: use in-memory store (no PG required).
        """
        session_id = self.lr_session_id or self.session_id

        # Dry-run: use in-memory store keyed by lr_session_id
        if self._is_dry_run():
            store_key = session_id or "dry_run_default"
            _DRY_RUN_STATE_STORE[store_key] = state.to_dict()
            logger.info(f"[LIVE RESEARCH] Dry-run: state saved in-memory (key={store_key}, stage={state.current_stage})")
            return

        if not session_id or not self.user_id:
            logger.warning("[LIVE RESEARCH] No lr_session_id/user_id, skip persist")
            return
        try:
            from core.session_service import SessionService
            service = SessionService()
            await service.update_session(
                session_id, self.user_id, self.org_id,
                updates={"live_research_state": state.to_dict()}
            )
        except Exception as e:
            logger.error(f"[LIVE RESEARCH] Failed to save state to session: {e}")
            raise

    async def _load_state(self) -> Optional[LiveResearchStageState]:
        """從 session 讀取 state。

        Uses self.lr_session_id (server-generated UUID passed back from frontend).
        Dry-run mode: use in-memory store (no PG required).

        LR #19 修正：不再 fallback 到 self.session_id（analytics session_id "sess_xxx"）。
        舊行為：self.lr_session_id or self.session_id → lr_session_id=None 時，
        analytics id 被送入 PG UUID 欄位查詢 → psycopg.errors.InvalidTextRepresentation crash。
        新行為：只用 self.lr_session_id；缺失 → return None → continueResearch 走 graceful narration。
        """
        session_id = self.lr_session_id

        # Dry-run: use in-memory store
        if self._is_dry_run():
            store_key = session_id or "dry_run_default"
            raw = _DRY_RUN_STATE_STORE.get(store_key)
            if not raw:
                logger.warning(f"[LIVE RESEARCH] Dry-run: no state found in-memory (key={store_key})")
                return None
            logger.info(f"[LIVE RESEARCH] Dry-run: state loaded from memory (key={store_key})")
            return LiveResearchStageState.from_dict(raw)

        if not session_id or not self.user_id:
            logger.warning("[LIVE RESEARCH] No lr_session_id/user_id, cannot load state")
            return None
        try:
            from core.session_service import SessionService
            service = SessionService()
            session = await service.get_session(session_id, self.user_id, self.org_id)
            if not session or not session.get("live_research_state"):
                return None
            raw = session["live_research_state"]
            if isinstance(raw, str):
                raw = json.loads(raw)
            return LiveResearchStageState.from_dict(raw)
        except Exception as e:
            logger.error(f"[LIVE RESEARCH] Failed to load state from session: {e}")
            raise
