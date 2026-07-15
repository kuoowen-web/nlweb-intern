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
import uuid
from typing import Optional

# In-memory state store for dry_run mode (no PG required)
_DRY_RUN_STATE_STORE: dict = {}

from methods.deep_research import DeepResearchHandler
from misc.logger.logging_config_helper import get_configured_logger
from reasoning.live_research.orchestrator import LiveResearchOrchestrator
from reasoning.live_research.stage_state import LiveResearchStageState

logger = get_configured_logger("live_research_handler")


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

        try:
            # Step 1: Create server-side session with proper UUID
            self.lr_session_id = await self._create_lr_session()

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
            self._lr_research_task.add_done_callback(self._on_lr_research_complete)
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
            raise
        except Exception as e:
            logger.error(f"[LIVE RESEARCH] Error: {e}", exc_info=True)
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
            self._lr_research_task.add_done_callback(self._on_lr_research_complete)
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
            raise
        except Exception as e:
            logger.error(f"[LIVE RESEARCH] Continue error: {e}", exc_info=True)
            raise

    def _on_lr_research_complete(self, task: asyncio.Task):
        """
        Callback when background LR task completes / fails / is cancelled.

        Mirrors DR `_on_research_complete` (methods/deep_research.py:259).
        Without this callback, exceptions raised inside the task would be
        silently swallowed if the outer `await` is interrupted (asyncio
        "Task exception was never retrieved" warning).
        """
        try:
            exc = task.exception()
            if exc:
                logger.error(
                    f"[LIVE RESEARCH] Background task failed: {exc}",
                    exc_info=exc,
                )
        except asyncio.CancelledError:
            # Normal cancellation (client disconnect or user-stop) — not an error.
            logger.info(
                f"[LIVE RESEARCH] Background task cancelled: {task.get_name()}"
            )
        except asyncio.InvalidStateError:
            # Should not happen in a done-callback, but be defensive.
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
