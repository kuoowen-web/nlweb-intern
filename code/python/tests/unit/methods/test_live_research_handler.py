"""Unit tests for LiveResearchHandler — task wrap + cancel.

These tests verify backend skeleton (task wrap + disconnect cancel).
Note: requestStop endpoint removed 2026-06-04 (placebo stop mechanism).
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# Insert `code/python` into sys.path so top-level packages (methods, reasoning) import
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from methods.live_research import LiveResearchHandler  # noqa: E402
from reasoning.live_research.stage_state import LiveResearchStageState  # noqa: E402


@pytest.fixture
def http_handler():
    h = MagicMock()
    h.write_stream = AsyncMock()
    return h


@pytest.fixture
def handler(http_handler):
    qp = {
        "query": "台灣綠能衝突",
        "dry_run": "true",
        "session_id": "sess-test",
    }
    h = LiveResearchHandler(qp, http_handler)
    # dry_run shortcut: skip prepare()
    h.final_retrieved_items = []
    return h


class TestTaskWrap:
    """Task 1.1 — runQuery / continueResearch must wrap orchestrator in asyncio.Task."""

    @pytest.mark.asyncio
    async def test_runQuery_wraps_in_task(self, handler, monkeypatch):
        """`runQuery` must create `self._lr_research_task` as an asyncio.Task."""
        captured = {"task": None}

        async def fake_start(query, initial_items):
            captured["task"] = handler._lr_research_task
            assert isinstance(captured["task"], asyncio.Task), \
                "orchestrator.start must be awaited inside an asyncio.Task"
            return LiveResearchStageState(current_stage=1, stage_status="checkpoint")

        import methods.live_research as lr_mod

        fake_orch = MagicMock()
        fake_orch.start = AsyncMock(side_effect=fake_start)
        monkeypatch.setattr(lr_mod, "LiveResearchOrchestrator", lambda **kw: fake_orch)

        await handler.runQuery()

        assert captured["task"] is not None
        # Task is reset to None after completion
        assert handler._lr_research_task is None

    @pytest.mark.asyncio
    async def test_continueResearch_wraps_in_task(self, handler, monkeypatch):
        """`continueResearch` must also wrap orchestrator.continue_from_checkpoint."""
        # Pre-seed dry_run store with a state to load
        from methods.live_research import _DRY_RUN_STATE_STORE
        handler.lr_session_id = "lr-sess-test"
        seeded = LiveResearchStageState(current_stage=4, stage_status="checkpoint")
        _DRY_RUN_STATE_STORE[handler.lr_session_id] = seeded.to_dict()
        handler.query_params["lr_session_id"] = handler.lr_session_id

        captured = {"task": None}

        async def fake_continue(state, user_message, auto_continue, nav_action=""):
            captured["task"] = handler._lr_research_task
            assert isinstance(captured["task"], asyncio.Task)
            return LiveResearchStageState(current_stage=5, stage_status="checkpoint")

        import methods.live_research as lr_mod

        fake_orch = MagicMock()
        fake_orch.continue_from_checkpoint = AsyncMock(side_effect=fake_continue)
        monkeypatch.setattr(lr_mod, "LiveResearchOrchestrator", lambda **kw: fake_orch)

        await handler.continueResearch(user_message="OK", auto_continue=False)
        assert captured["task"] is not None
        assert handler._lr_research_task is None


class TestCancelViaDisconnect:
    """Task 1.2 — handler.cancel() via disconnect callback aborts task."""

    @pytest.mark.asyncio
    async def test_cancel_via_disconnect(self, handler, monkeypatch):
        """Disconnect callback path: `_lr_research_task.cancel()` propagates as
        CancelledError out of runQuery."""
        cancel_received = asyncio.Event()

        async def slow_start(query, initial_items):
            try:
                await asyncio.sleep(10)  # long-running; should be cancelled
            except asyncio.CancelledError:
                cancel_received.set()
                raise
            return LiveResearchStageState(current_stage=1)

        import methods.live_research as lr_mod

        fake_orch = MagicMock()
        fake_orch.start = AsyncMock(side_effect=slow_start)
        monkeypatch.setattr(lr_mod, "LiveResearchOrchestrator", lambda **kw: fake_orch)

        # Launch runQuery as a task so we can cancel from outside
        run_task = asyncio.create_task(handler.runQuery())
        # Let runQuery wire up `_lr_research_task` first
        await asyncio.sleep(0.05)
        assert handler._lr_research_task is not None

        # Simulate disconnect callback: clear alive + cancel task
        handler.connection_alive_event.clear()
        handler._lr_research_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await run_task
        assert cancel_received.is_set()


class TestContinueResearchLegacySchemaGate:
    """Track A (sprint 2026-05-28) addendum C-3 / D: legacy schema gate。

    sprint 前舊 session (state.schema_version 缺欄位 or < 2) → continueResearch
    必須 reject 返 status=error error=legacy_schema_session，不可進 orchestrator。
    """

    @pytest.mark.asyncio
    async def test_continueResearch_rejects_legacy_v1_session(
        self, http_handler, monkeypatch,
    ):
        qp = {
            "query": "台灣綠能",
            "dry_run": "true",
            "session_id": "sess-legacy",
            "lr_session_id": "lr-legacy-v1",
        }
        handler = LiveResearchHandler(qp, http_handler)
        handler.lr_session_id = "lr-legacy-v1"

        # 注入一筆 v1 session payload (無 schema_version key → from_dict 默認 = 1)
        from methods.live_research import _DRY_RUN_STATE_STORE
        _DRY_RUN_STATE_STORE["lr-legacy-v1"] = {
            "current_stage": 5,
            "stage_status": "checkpoint",
            "research_question": "台灣綠能",
            "written_sections": [{"section_index": 0, "title": "前言", "content": "x"}],
            # 故意省略 schema_version → v1
        }

        # message_sender 注入捕捉 narration
        sent_messages = []
        sender = MagicMock()

        async def fake_send(msg):
            sent_messages.append(msg)

        sender.send_message = AsyncMock(side_effect=fake_send)
        handler.message_sender = sender

        # Spy: orchestrator.continue_from_checkpoint 不可被呼叫（gate 應 reject 在前）
        orch_called = {"flag": False}

        import methods.live_research as lr_mod

        class FakeOrch:
            async def continue_from_checkpoint(self, **kw):
                orch_called["flag"] = True
                return None

        monkeypatch.setattr(
            lr_mod, "LiveResearchOrchestrator", lambda **kw: FakeOrch(),
        )

        result = await handler.continueResearch(user_message="改第 1 章")

        # Cleanup
        _DRY_RUN_STATE_STORE.pop("lr-legacy-v1", None)

        # 1) orchestrator 不可被呼叫
        assert orch_called["flag"] is False, (
            "v1 legacy session 必須在 gate 處 reject，不可進 orchestrator"
        )
        # 2) Return value 為 error legacy_schema_session
        assert result.get("status") == "error"
        assert result.get("error") == "legacy_schema_session"
        # 3) 有 narration 提醒 user
        narrations = [
            m for m in sent_messages
            if m.get("message_type") == "live_research_narration"
        ]
        assert len(narrations) >= 1
        assert "舊" in narrations[0].get("text", "") or \
               "唯讀" in narrations[0].get("text", "") or \
               "session" in narrations[0].get("text", "").lower()

    @pytest.mark.asyncio
    async def test_continueResearch_accepts_v2_session(
        self, http_handler, monkeypatch,
    ):
        """v2 session (schema_version=2) → 正常進 orchestrator，不 reject。"""
        qp = {
            "query": "x",
            "dry_run": "true",
            "session_id": "sess-v2",
            "lr_session_id": "lr-v2-ok",
        }
        handler = LiveResearchHandler(qp, http_handler)
        handler.lr_session_id = "lr-v2-ok"

        from methods.live_research import _DRY_RUN_STATE_STORE
        _DRY_RUN_STATE_STORE["lr-v2-ok"] = {
            "current_stage": 5,
            "stage_status": "checkpoint",
            "schema_version": 2,
        }

        orch_called = {"flag": False}

        import methods.live_research as lr_mod

        class FakeOrch:
            async def continue_from_checkpoint(self, **kw):
                orch_called["flag"] = True
                return LiveResearchStageState(current_stage=6)

        monkeypatch.setattr(
            lr_mod, "LiveResearchOrchestrator", lambda **kw: FakeOrch(),
        )

        await handler.continueResearch(user_message="繼續")

        _DRY_RUN_STATE_STORE.pop("lr-v2-ok", None)

        # v2 session 必須通過 gate
        assert orch_called["flag"] is True, (
            "v2 session 必須通過 legacy gate 進入 orchestrator"
        )


    @pytest.mark.asyncio
    async def test_continueResearch_legacy_gate_fallback_to_http_handler_when_no_sender(
        self, http_handler, monkeypatch,
    ):
        """Fix 3 — legacy gate narration fallback：message_sender=None 時
        必須走 http_handler.write_stream（對齊 state_not_found gate 的雙路 pattern）。"""
        qp = {
            "query": "台灣綠能",
            "dry_run": "true",
            "session_id": "sess-legacy-nosender",
            "lr_session_id": "lr-legacy-nosender",
        }
        handler = LiveResearchHandler(qp, http_handler)
        handler.lr_session_id = "lr-legacy-nosender"
        # 明確設 message_sender=None（模擬 sender 尚未初始化）
        handler.message_sender = None

        from methods.live_research import _DRY_RUN_STATE_STORE
        _DRY_RUN_STATE_STORE["lr-legacy-nosender"] = {
            "current_stage": 5,
            "stage_status": "checkpoint",
            # 故意省略 schema_version → v1
        }

        result = await handler.continueResearch(user_message="改第 1 章")

        _DRY_RUN_STATE_STORE.pop("lr-legacy-nosender", None)

        # 1) 仍回 error
        assert result.get("status") == "error"
        assert result.get("error") == "legacy_schema_session"
        # 2) narration 必須走 http_handler.write_stream（非 message_sender）
        assert http_handler.write_stream.called, (
            "message_sender=None 時 legacy gate narration 必須 fallback 到 http_handler.write_stream"
        )
        written = [
            call.args[0] for call in http_handler.write_stream.call_args_list
        ]
        narrations = [w for w in written if w.get("message_type") == "live_research_narration"]
        assert len(narrations) >= 1, (
            f"http_handler.write_stream 應寫出 narration，實際寫出：{written}"
        )


class TestContinueResearchStateNotFound:
    """R5 — `continueResearch` 當 `_load_state` 返回 None 時：

    - 不可 silent fallback `runQuery()`（會 emit Stage 1 mock 20-topic fixture
      把 user 拉回 Stage 1，違反 CLAUDE.md no-silent-fail）
    - 必須 emit user-visible narration 明示「session 找不到」
    - 必須 return error response（status=error），不 emit Stage 1 checkpoint
    """

    @pytest.mark.asyncio
    async def test_continueResearch_missing_state_emits_narration_no_silent_runquery(
        self, http_handler, monkeypatch,
    ):
        qp = {
            "query": "台灣綠能衝突",
            "dry_run": "true",
            "session_id": "sess-missing",
            "lr_session_id": "lr-missing-xyz",
        }
        handler = LiveResearchHandler(qp, http_handler)
        handler.lr_session_id = "lr-missing-xyz"

        # 注意：dry_run store 沒有 lr-missing-xyz key → _load_state 必然回 None
        from methods.live_research import _DRY_RUN_STATE_STORE
        _DRY_RUN_STATE_STORE.pop("lr-missing-xyz", None)

        # message_sender 注入，捕捉 narration emit
        sent_messages = []
        sender = MagicMock()

        async def fake_send(msg):
            sent_messages.append(msg)

        sender.send_message = AsyncMock(side_effect=fake_send)
        handler.message_sender = sender

        # Spy runQuery — 必須不被呼叫（否則 silent fallback 回 Stage 1）
        runquery_called = {"flag": False}

        async def spy_runquery():
            runquery_called["flag"] = True
            return {}

        monkeypatch.setattr(handler, "runQuery", spy_runquery)

        result = await handler.continueResearch(user_message="進入匯出階段")

        # 1) runQuery 不可被呼叫
        assert runquery_called["flag"] is False, (
            "continueResearch 在 state 找不到時不可 silent fallback runQuery()"
        )

        # 2) 必須 emit 一則明示「找不到 session」narration
        narration_msgs = [
            m for m in sent_messages
            if m.get("message_type") == "live_research_narration"
        ]
        assert len(narration_msgs) >= 1, (
            "state 找不到時必須 emit narration，不可 silent fail"
        )
        narr_text = narration_msgs[0].get("text", "")
        assert "session" in narr_text.lower() or "研究" in narr_text, (
            f"narration 必須明示 session 找不到，實際：{narr_text!r}"
        )

        # 3) 不可 emit Stage 1 checkpoint / stage_change（不退回 Stage 1）
        stage_change_msgs = [
            m for m in sent_messages
            if m.get("message_type") == "live_research_stage_change"
        ]
        assert not any(m.get("stage") == 1 for m in stage_change_msgs), (
            "state 找不到時不可退回 Stage 1"
        )

        # 4) Return value 表示 error，不是 checkpoint
        assert result.get("status") == "error", (
            f"必須 return error response，實際：{result.get('status')!r}"
        )


class TestNonPersistWarning:
    """lr-auto-anon-no-persist-warning：未持久化 session 必須 emit 警告 narration。

    四條行為線（adversarial review 收斂 2026-06-11：Codex blocker + in-house S2）：
    1. 匿名 → fallback UUID、session_created 照常 emit、警告出現（未登入文案）
    2. 登入 + create_session 成功 → persisted=True、無警告
    3. dry_run → persisted=True、無警告（假警告防護）
    4. 登入 + create_session 拋例外 → fallback UUID、警告出現（db_error 文案，
       不得稱「你未登入」—— review S1）
    """

    @staticmethod
    def _written(http_handler):
        return [c.args[0] for c in http_handler.write_stream.call_args_list]

    @classmethod
    def _warnings(cls, http_handler):
        return [
            m for m in cls._written(http_handler)
            if m.get("message_type") == "live_research_narration"
            and "不會被儲存" in m.get("text", "")
        ]

    def _make_handler(self, http_handler, monkeypatch, qp):
        """非 dry_run handler：prepare / _save_state mock 掉（切面在 raw data 蒐集與 DB，
        不 mock 警告邏輯本身）。"""
        handler = LiveResearchHandler(qp, http_handler)
        monkeypatch.setattr(handler, "_is_dry_run", lambda: False)
        monkeypatch.setattr(handler, "_is_mock_bab", lambda: False)

        async def fake_prepare():
            handler.final_retrieved_items = []

        monkeypatch.setattr(handler, "prepare", fake_prepare)
        handler.query_done = False

        async def fake_save(state):
            return None

        monkeypatch.setattr(handler, "_save_state", fake_save)
        return handler

    def _patch_orchestrator(self, monkeypatch):
        import methods.live_research as lr_mod

        fake_orch = MagicMock()
        fake_orch.start = AsyncMock(
            return_value=LiveResearchStageState(current_stage=1, stage_status="checkpoint")
        )
        monkeypatch.setattr(lr_mod, "LiveResearchOrchestrator", lambda **kw: fake_orch)

    @pytest.mark.asyncio
    async def test_anonymous_emits_warning_and_session_created(self, http_handler, monkeypatch):
        qp = {"query": "台灣綠能", "session_id": "sess-anon"}  # 無 user_id / org_id = 匿名
        handler = self._make_handler(http_handler, monkeypatch, qp)
        self._patch_orchestrator(monkeypatch)

        await handler.runQuery()

        written = self._written(http_handler)
        created = [m for m in written if m.get("message_type") == "live_research_session_created"]
        assert len(created) == 1 and created[0].get("session_id"), (
            "fallback UUID 的 session_created 必須照常 emit（警告是附加不是取代）"
        )
        assert handler._lr_session_persisted is False
        assert handler._lr_persist_skip_reason == "anonymous"
        warnings = self._warnings(http_handler)
        assert len(warnings) == 1, "匿名 session 必須 emit 不持久化警告"
        assert "未登入" in warnings[0]["text"], "匿名分支應使用未登入文案"

    @pytest.mark.asyncio
    async def test_authenticated_create_session_ok_no_warning(self, http_handler, monkeypatch):
        qp = {
            "query": "台灣綠能",
            "session_id": "sess-auth",
            "user_id": "11111111-1111-1111-1111-111111111111",
            "org_id": "22222222-2222-2222-2222-222222222222",
        }
        handler = self._make_handler(http_handler, monkeypatch, qp)
        self._patch_orchestrator(monkeypatch)

        import core.session_service as ss_mod

        class FakeService:
            async def create_session(self, user_id, org_id, title):
                return {"id": "33333333-3333-3333-3333-333333333333"}

        monkeypatch.setattr(ss_mod, "SessionService", FakeService)

        await handler.runQuery()

        assert handler._lr_session_persisted is True
        assert handler.lr_session_id == "33333333-3333-3333-3333-333333333333"
        assert self._warnings(http_handler) == [], "登入成功路徑不得出現任何不持久化警告"

    @pytest.mark.asyncio
    async def test_dry_run_no_warning(self, handler, http_handler, monkeypatch):
        # 既有 handler fixture 即 dry_run=true（in-memory store 有效 → 視為 persisted）
        self._patch_orchestrator(monkeypatch)

        await handler.runQuery()

        assert handler._lr_session_persisted is True
        assert self._warnings(http_handler) == [], "dry_run 不得噴假警告"

    @pytest.mark.asyncio
    async def test_authenticated_db_error_warns_without_lying(self, http_handler, monkeypatch):
        """登入但 create_session 拋例外 → 警告必須出現，且不得稱「你未登入」（review S1）。"""
        qp = {
            "query": "台灣綠能",
            "session_id": "sess-dberr",
            "user_id": "11111111-1111-1111-1111-111111111111",
            "org_id": "22222222-2222-2222-2222-222222222222",
        }
        handler = self._make_handler(http_handler, monkeypatch, qp)
        self._patch_orchestrator(monkeypatch)

        import core.session_service as ss_mod

        class FakeService:
            async def create_session(self, user_id, org_id, title):
                raise RuntimeError("simulated DB outage")

        monkeypatch.setattr(ss_mod, "SessionService", FakeService)

        await handler.runQuery()

        assert handler._lr_session_persisted is False
        assert handler._lr_persist_skip_reason == "db_error"
        assert handler.lr_session_id, "fallback UUID 仍應有值（pipeline 不被擋）"
        warnings = self._warnings(http_handler)
        assert len(warnings) == 1, "db_error 路徑必須 emit 不持久化警告"
        assert "未登入" not in warnings[0]["text"], "登入 user 不得收到「你未登入」（S1）"
        assert "請先登入" not in warnings[0]["text"], "登入 user 不得收到無效行動建議（S1）"
