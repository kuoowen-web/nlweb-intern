"""Integration tests for UX-4 / VP-7 Live Research Stage 5 stop / disconnect / resume.

VP-7 反轉後，`_run_stage_5` 改 single-step（每次只寫一段）。本檔案使用
`_drive_to_completion` helper 模擬 user 連續 continue 走完全部段落。
保留 stop button + disconnect + resume 整合測試。

Scenarios (dry_run mode, full handler + orchestrator + state I/O):
1. happy path — driver 跑完全部段，all_done emit
2. user stop（vestigial）— stop flag set 後 single-step 仍寫一段 + checkpoint
3. user disconnect — task.cancel() / alive=False 後 writer 不寫
4. resume — last_completed=0 → 下一次 single-step 寫第 1 段
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from methods.live_research import LiveResearchHandler, _DRY_RUN_STATE_STORE  # noqa: E402
from reasoning.live_research.orchestrator import LiveResearchOrchestrator  # noqa: E402
from reasoning.live_research.stage_state import LiveResearchStageState  # noqa: E402
from reasoning.schemas_live import ContextMap, ContextMapTopic  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _make_cm(n: int = 3) -> ContextMap:
    return ContextMap(
        research_question="Q",
        topics=[
            ContextMapTopic(
                topic_id=f"t{i}", name=f"section-{i}", domain="d",
                relevance="core", description=f"desc-{i}",
            )
            for i in range(n)
        ],
        version=1,
    )


def _make_handler(lr_session_id: str) -> LiveResearchHandler:
    qp = {
        "query": "Q",
        "dry_run": "true",
        "session_id": "sess-i",
        "lr_session_id": lr_session_id,
    }
    http_handler = MagicMock()
    http_handler.write_stream = AsyncMock()
    h = LiveResearchHandler(qp, http_handler)
    h.lr_session_id = lr_session_id
    h.final_retrieved_items = []
    # message_sender mock for SSE captures
    h.message_sender = MagicMock()
    h.message_sender.send_message = AsyncMock()
    h.connection_alive_event = MagicMock()
    h.connection_alive_event.is_set = MagicMock(return_value=True)
    return h


def _seed_state(lr_session_id: str, n_sections: int = 3) -> None:
    """Seed dry_run store with a Stage-5-ready state (CM has N topics)."""
    state = LiveResearchStageState(
        current_stage=5,
        stage_status="in_progress",
        context_map_json=_make_cm(n_sections).model_dump_json(),
    )
    _DRY_RUN_STATE_STORE[lr_session_id] = state.to_dict()


async def _drive_to_completion(orch, state, max_iter: int = 20):
    """VP-7: drive single-step `_run_stage_5` until last_completed == total - 1."""
    cm = ContextMap.model_validate_json(state.context_map_json)
    writer_sections, _ = orch._resolve_chapter_source(cm, state.format_specs)
    total = len(writer_sections)
    for _ in range(max_iter):
        state = await orch._run_stage_5(state)
        if state.last_completed_section_index >= total - 1:
            return state
    raise AssertionError(f"driver exceeded {max_iter} iterations")


# ──────────────────────────────────────────────────────────────────────────────
# Scenarios
# ──────────────────────────────────────────────────────────────────────────────


class TestStage5HappyPath:
    """Scenario 1 — VP-7 driver 走完全部段，最後 all_done emit。"""

    @pytest.mark.asyncio
    async def test_happy_path_all_sections_written(self):
        lr_id = "int-happy-1"
        _seed_state(lr_id, n_sections=3)
        handler = _make_handler(lr_id)

        orch = LiveResearchOrchestrator(handler=handler, dry_run=True)
        state = LiveResearchStageState.from_dict(_DRY_RUN_STATE_STORE[lr_id])

        result = await _drive_to_completion(orch, state)

        assert len(result.written_sections) == 3
        assert result.last_completed_section_index == 2
        assert result.stage_status == "checkpoint"
        assert result.stage_5_writer_running is False
        assert result.stage5_waiting_for_user is True

        emits = [
            c.args[0]
            for c in handler.message_sender.send_message.call_args_list
            if c.args[0].get("message_type") == "live_research_writer_status"
        ]
        statuses = [e["status"] for e in emits]
        assert statuses.count("section_done") == 3
        assert "all_done" in statuses


class TestStage5Disconnect:
    """Scenario 3 — disconnect 處理。"""

    @pytest.mark.asyncio
    async def test_disconnect_unwinds_writer(self):
        """task.cancel() during single-step writer → CancelledError 必須 propagate
        + writer_running 清掉。"""
        lr_id = "int-disc-1"
        _seed_state(lr_id, n_sections=3)
        handler = _make_handler(lr_id)

        orch = LiveResearchOrchestrator(handler=handler, dry_run=True)
        state = LiveResearchStageState.from_dict(_DRY_RUN_STATE_STORE[lr_id])

        # Wrap _run_stage_5 in a task so we can cancel it externally
        run_task = asyncio.create_task(orch._run_stage_5(state))
        await asyncio.sleep(0.02)  # let writer enter
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task

        # state reflects cleanup
        assert state.stage_5_writer_running is False
        assert len(state.written_sections) <= 1

    @pytest.mark.asyncio
    async def test_alive_event_false_skips_write(self):
        """connection_alive_event.is_set()==False 進場 → return early，不寫、不 emit。"""
        lr_id = "int-disc-2"
        _seed_state(lr_id, n_sections=3)
        handler = _make_handler(lr_id)

        handler.connection_alive_event.is_set = MagicMock(return_value=False)

        orch = LiveResearchOrchestrator(handler=handler, dry_run=True)
        state = LiveResearchStageState.from_dict(_DRY_RUN_STATE_STORE[lr_id])
        result = await orch._run_stage_5(state)

        # 不寫
        assert len(result.written_sections) == 0
        assert result.last_completed_section_index == -1
        # 不 emit writer_status started / section_done
        emits = [
            c.args[0]
            for c in handler.message_sender.send_message.call_args_list
            if c.args[0].get("message_type") == "live_research_writer_status"
        ]
        assert emits == []


class TestStage5Resume:
    """Scenario 4 — VP-7：last_completed=0 → 下次 single-step 寫第 1 段。"""

    @pytest.mark.asyncio
    async def test_resume_writes_next_section_in_single_step(self):
        lr_id = "int-resume-1"
        # Seed with 1 section already written
        cm = _make_cm(n=3)
        prior = LiveResearchStageState(
            current_stage=5,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=0,
            written_sections=[{
                "section_index": 0, "title": "section-0",
                "content": "...", "sources_used": [], "confidence_level": "Medium",
                "chapter_summary": "",
            }],
        )
        _DRY_RUN_STATE_STORE[lr_id] = prior.to_dict()

        handler = _make_handler(lr_id)
        orch = LiveResearchOrchestrator(handler=handler, dry_run=True)

        state = LiveResearchStageState.from_dict(_DRY_RUN_STATE_STORE[lr_id])
        result = await orch._run_stage_5(state)

        # Single-step：寫第 1 段 → 累計 2 段
        assert len(result.written_sections) == 2
        assert result.last_completed_section_index == 1
        emits = [
            c.args[0]
            for c in handler.message_sender.send_message.call_args_list
            if c.args[0].get("message_type") == "live_research_writer_status"
        ]
        started = [e for e in emits if e["status"] == "started"][0]
        assert started["completed"] == 1
