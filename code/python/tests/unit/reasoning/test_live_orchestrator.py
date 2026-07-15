"""Tests for LiveResearchOrchestrator — 6-Stage controller."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from reasoning.live_research.orchestrator import (
    LiveResearchOrchestrator,
    _looks_like_confirmation,
    META_INTENT_SKIP,
)
from reasoning.live_research import lr_copy
from reasoning.live_research.stage_state import LiveResearchStageState
from reasoning.schemas_live import ContextMap, ContextMapTopic


def _make_context_map():
    return ContextMap(
        research_question="台灣綠能衝突",
        topics=[
            ContextMapTopic(topic_id="t1", name="土地使用", domain="能源政策", relevance="core"),
        ],
        version=1,
    )


class TestLiveResearchOrchestrator:
    @pytest.fixture
    def mock_handler(self):
        handler = MagicMock()
        handler.query = "台灣綠能衝突"
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        handler.connection_alive_event = MagicMock()
        handler.connection_alive_event.is_set = MagicMock(return_value=True)
        handler.query_params = {}
        handler.site = "all"
        handler.final_retrieved_items = []
        handler._save_state = AsyncMock()  # _persist_progress awaits this
        return handler

    @pytest.fixture
    def orchestrator(self, mock_handler):
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=mock_handler)
            return orch

    @pytest.mark.asyncio
    async def test_generate_report_title_success(self, orchestrator, monkeypatch):
        """LLM 回標題 → helper 回 (title, was_generated=True)。"""
        from reasoning.schemas_live import ContextMap, ContextMapTopic
        cm = ContextMap(research_question="台灣綠能衝突", topics=[
            ContextMapTopic(topic_id="t1", name="土地", domain="能源", relevance="core"),
        ], version=1)
        written = [{"section_index": 0, "title": "前言", "chapter_summary": "背景說明",
                    "content": "x", "status": "drafted"}]
        monkeypatch.setattr(
            "reasoning.live_research.orchestrator.ask_llm",
            AsyncMock(return_value={"title": "綠能開發與土地正義的拉鋸"}),
        )
        title, was_generated = await orchestrator._generate_report_title(cm, written)
        assert title == "綠能開發與土地正義的拉鋸"
        assert was_generated is True

    @pytest.mark.asyncio
    async def test_generate_report_title_llm_fail_degrades(
        self, orchestrator, monkeypatch, capfd
    ):
        """LLM 拋例外 → 回 (research_question, False) + logger.warning（不 silent fail）。
        NOTE: orchestrator 用自訂 LazyLogger（propagate=False，背景 thread 寫 JSON），
        caplog 抓不到 → 依專案慣例（test_deep_research_ambiguity.py）用 capfd + flush。
        """
        import time
        from reasoning.schemas_live import ContextMap, ContextMapTopic
        cm = ContextMap(research_question="台灣綠能衝突", topics=[
            ContextMapTopic(topic_id="t1", name="土地", domain="能源", relevance="core"),
        ], version=1)
        monkeypatch.setattr(
            "reasoning.live_research.orchestrator.ask_llm",
            AsyncMock(side_effect=RuntimeError("boom")),
        )
        title, was_generated = await orchestrator._generate_report_title(cm, [])
        assert title == "台灣綠能衝突"
        assert was_generated is False
        time.sleep(0.2)  # 讓背景 JSON logger worker flush
        out, err = capfd.readouterr()
        assert "report title" in (out + err).lower()  # 不 silent fail

    @pytest.mark.asyncio
    async def test_generate_report_title_empty_response_degrades(
        self, orchestrator, monkeypatch, capfd
    ):
        """LLM 回空/純空白標題 → 回 (research_question, False) + warning。
        NOTE: 同上，自訂 LazyLogger 用 capfd 抓（caplog 抓不到）。
        """
        import time
        from reasoning.schemas_live import ContextMap, ContextMapTopic
        cm = ContextMap(research_question="台灣綠能衝突", topics=[
            ContextMapTopic(topic_id="t1", name="土地", domain="能源", relevance="core"),
        ], version=1)
        monkeypatch.setattr(
            "reasoning.live_research.orchestrator.ask_llm",
            AsyncMock(return_value={"title": "   "}),
        )
        title, was_generated = await orchestrator._generate_report_title(cm, [])
        assert title == "台灣綠能衝突"
        assert was_generated is False
        time.sleep(0.2)
        out, err = capfd.readouterr()
        assert "report title" in (out + err).lower()

    @pytest.mark.asyncio
    async def test_generate_report_title_sanitizes_multiline_and_truncates(
        self, orchestrator, monkeypatch
    ):
        """AR P2：LLM 回多行/含 markdown 標記/超長 → 後處理成單行 markdown-safe + 截斷。"""
        from reasoning.schemas_live import ContextMap, ContextMapTopic
        cm = ContextMap(research_question="Q", topics=[
            ContextMapTopic(topic_id="t1", name="n", domain="d", relevance="core"),
        ], version=1)
        # 多行 + 前導 # + 超長（>40 字）
        raw = "# 綠能\n開發與土地正義\n" + "拉" * 50
        monkeypatch.setattr(
            "reasoning.live_research.orchestrator.ask_llm",
            AsyncMock(return_value={"title": raw}),
        )
        title, was_generated = await orchestrator._generate_report_title(cm, [])
        assert was_generated is True
        assert "\n" not in title  # 換行摺成空格
        assert not title.startswith("#")  # 前導 markdown 標記剝除
        assert len(title) <= 40  # 超長截斷

    def test_init(self, orchestrator):
        assert orchestrator is not None

    def test_inherits_orchestrator_base(self, orchestrator):
        """LiveResearchOrchestrator 應繼承 OrchestratorBase。"""
        from reasoning.orchestrator_base import OrchestratorBase
        assert isinstance(orchestrator, OrchestratorBase)

    def test_handler_set_by_base(self, orchestrator, mock_handler):
        """self.handler 由 OrchestratorBase.__init__ 設定。"""
        assert orchestrator.handler is mock_handler

    def test_logger_set_by_base(self, orchestrator):
        """self.logger 由 OrchestratorBase.__init__ 設定。"""
        assert orchestrator.logger is not None

    def test_has_features_attribute(self, orchestrator):
        """orchestrator 應有 features dict。"""
        assert hasattr(orchestrator, "features")
        assert isinstance(orchestrator.features, dict)

    def test_has_max_bab_iterations(self, orchestrator):
        """orchestrator 應有 max_bab_iterations 屬性。"""
        assert hasattr(orchestrator, "max_bab_iterations")
        assert orchestrator.max_bab_iterations > 0

    @pytest.mark.asyncio
    async def test_start_creates_stage_1(self, orchestrator):
        """start() 應該進入 Stage 1 並產出 checkpoint。"""
        mock_cm = _make_context_map()
        orchestrator._run_stage_1 = AsyncMock(return_value=LiveResearchStageState(
            current_stage=1,
            stage_status="checkpoint",
            context_map_json=mock_cm.model_dump_json(),
        ))
        state = await orchestrator.start(query="台灣綠能衝突")
        assert state.current_stage == 1
        assert state.stage_status == "checkpoint"

    @pytest.mark.asyncio
    async def test_start_calls_run_stage_1(self, orchestrator):
        """start() 必須呼叫 _run_stage_1。"""
        mock_cm = _make_context_map()
        orchestrator._run_stage_1 = AsyncMock(return_value=LiveResearchStageState(
            current_stage=1,
            stage_status="checkpoint",
            context_map_json=mock_cm.model_dump_json(),
        ))
        await orchestrator.start(query="台灣綠能衝突")
        orchestrator._run_stage_1.assert_called_once()

    @pytest.mark.asyncio
    async def test_continue_from_checkpoint_stage_1(self, orchestrator):
        """continue_from_checkpoint 從 Stage 1 checkpoint 前進到 Stage 2。"""
        prev_state = LiveResearchStageState(
            current_stage=1,
            stage_status="checkpoint",
            context_map_json=_make_context_map().model_dump_json(),
            initial_context_map_json=_make_context_map().model_dump_json(),
        )
        orchestrator._run_stage_2 = AsyncMock(return_value=LiveResearchStageState(
            current_stage=2, stage_status="checkpoint"
        ))
        # 非空 user_message 走 _parse_stage_1_intent（真 LLM）；斷 key 後 mock 回 confirm
        # 讓 stage1→2 advance（本測試標的是 continue 推進，非 intent 解析本身）。
        from reasoning.schemas_live import Stage1ParsedIntent
        orchestrator._parse_stage_1_intent = AsyncMock(
            return_value=Stage1ParsedIntent(action="confirm", operations=[], summary="OK")
        )
        state = await orchestrator.continue_from_checkpoint(
            prev_state, user_message="結構很好，繼續"
        )
        assert state.current_stage == 2

    @pytest.mark.asyncio
    async def test_auto_continue(self, orchestrator):
        """auto_continue=True 時用預設值繼續。"""
        prev_state = LiveResearchStageState(
            current_stage=1,
            stage_status="checkpoint",
            context_map_json=_make_context_map().model_dump_json(),
            initial_context_map_json=_make_context_map().model_dump_json(),
        )
        orchestrator._run_stage_2 = AsyncMock(return_value=LiveResearchStageState(
            current_stage=2, stage_status="checkpoint"
        ))
        state = await orchestrator.continue_from_checkpoint(
            prev_state, user_message="", auto_continue=True
        )
        assert state.current_stage == 2

    @pytest.mark.asyncio
    async def test_continue_not_at_checkpoint_returns_unchanged(self, orchestrator):
        """不在 checkpoint 狀態時 continue 不做任何事。"""
        state = LiveResearchStageState(
            current_stage=1,
            stage_status="in_progress",
        )
        result = await orchestrator.continue_from_checkpoint(state, user_message="test")
        assert result.current_stage == 1
        assert result.stage_status == "in_progress"

    @pytest.mark.asyncio
    async def test_continue_from_checkpoint_stage_2_to_3(self, orchestrator):
        """Stage 2 checkpoint → 進入 Stage 3。"""
        prev_state = LiveResearchStageState(
            current_stage=2,
            stage_status="checkpoint",
            context_map_json=_make_context_map().model_dump_json(),
            initial_context_map_json=_make_context_map().model_dump_json(),
        )
        orchestrator._run_stage_3 = AsyncMock(return_value=LiveResearchStageState(
            current_stage=3, stage_status="checkpoint"
        ))
        state = await orchestrator.continue_from_checkpoint(
            prev_state, user_message="OK"
        )
        assert state.current_stage == 3

    @pytest.mark.asyncio
    async def test_continue_from_checkpoint_stage_3_to_4(self, orchestrator):
        """Stage 3 checkpoint → 進入 Stage 4。"""
        prev_state = LiveResearchStageState(
            current_stage=3,
            stage_status="checkpoint",
            context_map_json=_make_context_map().model_dump_json(),
        )
        orchestrator._run_stage_4 = AsyncMock(return_value=LiveResearchStageState(
            current_stage=4, stage_status="checkpoint"
        ))
        state = await orchestrator.continue_from_checkpoint(
            prev_state, user_message=""
        )
        assert state.current_stage == 4

    @pytest.mark.asyncio
    async def test_continue_from_checkpoint_stage_4_to_5(self, orchestrator):
        """Stage 4 checkpoint → 進入 Stage 5。"""
        prev_state = LiveResearchStageState(
            current_stage=4,
            stage_status="checkpoint",
            context_map_json=_make_context_map().model_dump_json(),
        )
        orchestrator._run_stage_5 = AsyncMock(return_value=LiveResearchStageState(
            current_stage=5, stage_status="checkpoint"
        ))
        state = await orchestrator.continue_from_checkpoint(
            prev_state, user_message=""
        )
        assert state.current_stage == 5

    @pytest.mark.asyncio
    async def test_continue_from_checkpoint_stage_5_to_6(self, orchestrator):
        """Stage 5 checkpoint → 進入 Stage 6。

        前提：Stage 5 所有段落已寫完（last_completed_section_index == 0 對應
        _make_context_map() 的 1 個 topic），user_message="" 走 auto-continue 路徑，
        「all sections written」→ complete_stage() → _run_stage_6。
        """
        prev_state = LiveResearchStageState(
            current_stage=5,
            stage_status="checkpoint",
            context_map_json=_make_context_map().model_dump_json(),
            last_completed_section_index=0,  # 1 topic → 1 section → index 0 = done
        )
        orchestrator._run_stage_6 = AsyncMock(return_value=LiveResearchStageState(
            current_stage=6, stage_status="completed"
        ))
        state = await orchestrator.continue_from_checkpoint(
            prev_state, user_message=""
        )
        assert state.current_stage == 6

    @pytest.mark.asyncio
    async def test_all_stages_complete_after_stage_6(self, orchestrator):
        """Stage 6 checkpoint → all stages complete。"""
        prev_state = LiveResearchStageState(
            current_stage=6,
            stage_status="checkpoint",
            context_map_json=_make_context_map().model_dump_json(),
        )
        state = await orchestrator.continue_from_checkpoint(
            prev_state, user_message=""
        )
        assert state.stage_status == "completed"

    @pytest.mark.asyncio
    async def test_stage6_export_header_chapter_one_based(
        self, orchestrator, monkeypatch
    ):
        """Bug G production-path：真跑 _run_stage_6（不 AsyncMock 它），驗匯出 header 1-based。

        AR R4 Codex blocker：helper test 只證組裝邏輯對，不證 orchestrator 傳對參數給
        helper。只有真跑 _run_stage_6 抓得到「接線參數錯誤」。patch 周邊 side effects
        （非 _run_stage_6 本身），捕捉 export markdown 斷言「第 4 章」非「第 3/0 章」。
        """
        # patch side effects（非 _run_stage_6 本身）
        orchestrator._emit_stage_change = AsyncMock()
        orchestrator._emit_narration = AsyncMock()
        orchestrator._persist_checkpoint_boundary = AsyncMock()
        orchestrator._build_references_block = lambda *a, **k: ""
        orchestrator._generate_report_title = AsyncMock(return_value=("測試標題", True))
        captured = []
        monkeypatch.setattr(
            "reasoning.live_research.orchestrator.emit_sse",
            AsyncMock(side_effect=lambda handler, payload: captured.append(payload)),
        )
        # 最小 valid state：valid context_map_json + problematic written_section index=3
        state = LiveResearchStageState(
            current_stage=5,
            context_map_json=_make_context_map().model_dump_json(),
            written_sections=[
                {
                    "section_index": 3,
                    "title": "結果與討論",
                    "status": "guard_failed",
                    "content": "本章內容。",
                },
            ],
        )
        await orchestrator._run_stage_6(state)
        # 從 emit_sse 捕捉到的 live_research_export payload 取 final_report markdown
        export_payloads = [
            p for p in captured
            if p.get("message_type") == "live_research_export"
        ]
        assert export_payloads, f"未捕捉到 export payload；captured={captured!r}"
        export_md = export_payloads[0]["content"]
        assert "第 4 章" in export_md       # index 3 → 第 4 章（接線對）
        assert "第 3 章" not in export_md and "第 0 章" not in export_md

    @pytest.mark.asyncio
    async def test_stage6_h1_uses_generated_title_query_as_subtitle(
        self, orchestrator, monkeypatch
    ):
        """Stage 6：H1 = 生成標題；原始查詢降為副標（CEO 拍板呈現）。"""
        orchestrator._emit_stage_change = AsyncMock()
        orchestrator._emit_narration = AsyncMock()
        orchestrator._persist_checkpoint_boundary = AsyncMock()
        orchestrator._build_references_block = lambda *a, **k: ""
        # helper 回 (title, was_generated=True)
        orchestrator._generate_report_title = AsyncMock(
            return_value=("綠能開發與土地正義的拉鋸", True)
        )
        captured = []
        monkeypatch.setattr(
            "reasoning.live_research.orchestrator.emit_sse",
            AsyncMock(side_effect=lambda handler, payload: captured.append(payload)),
        )
        state = LiveResearchStageState(
            current_stage=5,
            context_map_json=_make_context_map().model_dump_json(),  # research_question="台灣綠能衝突"
            written_sections=[
                {"section_index": 0, "title": "前言", "chapter_summary": "背景",
                 "content": "本章內容。", "status": "drafted"},
            ],
        )
        await orchestrator._run_stage_6(state)
        export_md = [p for p in captured
                     if p.get("message_type") == "live_research_export"][0]["content"]
        assert "# 綠能開發與土地正義的拉鋸" in export_md   # H1 = 生成標題
        assert "> 原始查詢：台灣綠能衝突" in export_md      # 原始查詢降為副標
        assert "# 台灣綠能衝突" not in export_md          # 不再是 H1
        assert state.generated_report_title == "綠能開發與土地正義的拉鋸"  # 純標題值存 state

    @pytest.mark.asyncio
    async def test_stage6_title_degraded_falls_back_to_query_h1_no_subtitle(
        self, orchestrator, monkeypatch
    ):
        """helper 降級（was_generated=False）→ H1 退回原查詢、抑制副標、state 存空字串。"""
        orchestrator._emit_stage_change = AsyncMock()
        orchestrator._emit_narration = AsyncMock()
        orchestrator._persist_checkpoint_boundary = AsyncMock()
        orchestrator._build_references_block = lambda *a, **k: ""
        # helper 回 (research_question, was_generated=False) 模擬降級
        orchestrator._generate_report_title = AsyncMock(
            return_value=("台灣綠能衝突", False)
        )
        captured = []
        monkeypatch.setattr(
            "reasoning.live_research.orchestrator.emit_sse",
            AsyncMock(side_effect=lambda handler, payload: captured.append(payload)),
        )
        state = LiveResearchStageState(
            current_stage=5,
            context_map_json=_make_context_map().model_dump_json(),
            written_sections=[
                {"section_index": 0, "title": "前言", "content": "x", "status": "drafted"},
            ],
        )
        await orchestrator._run_stage_6(state)
        export_md = [p for p in captured
                     if p.get("message_type") == "live_research_export"][0]["content"]
        assert "# 台灣綠能衝突" in export_md          # H1 退回原始查詢
        assert "> 原始查詢：" not in export_md        # 降級抑制副標（不冗餘）
        assert state.generated_report_title == ""    # 降級 → state 存空字串（P1-1）

    @pytest.mark.asyncio
    async def test_stage6_title_llm_fail_end_to_end_warns_and_empty_state(
        self, orchestrator, monkeypatch
    ):
        """P1-3 端到端：不 mock helper，patch ask_llm raise → 真跑降級全鏈路。
        驗 H1=query + 無副標 + logger.warning（不 silent fail）+ state=""。
        NOTE: 自訂 LazyLogger 走「背景 worker thread + async queue」dispatch，warning 最終
        會由 worker thread 走底層 logging.getLogger("live_research.orchestrator")（其 StreamHandler
        綁 sys.stdout）非同步 emit。原 plan 用 capfd 抓 fd 級輸出，但該 StreamHandler 的 stream
        參照會被前一個真跑 _run_stage_6 的 sibling test 汙染成舊 capture 物件，fd 級抓不到
        （sibling ordering 決定成敗，非 silent-fail 本身）。改用「掛 handler 到底層 logger +
        poll 等 worker flush」抓 LogRecord — 觀測點在 log source，不受 stdout 重綁影響，穩定。
        另注意 LoggerUtility.__init__ 首次建構會「clear 既有 handlers」，故必須先 pre-warm
        （逼 worker 端 real_logger 快取建好）後再掛我的 handler，否則會被 lazy 建構清掉。"""
        import logging as _logging
        import time
        from misc.logger.logging_config_helper import _get_async_processor
        orchestrator._emit_stage_change = AsyncMock()
        orchestrator._emit_narration = AsyncMock()
        orchestrator._persist_checkpoint_boundary = AsyncMock()
        orchestrator._build_references_block = lambda *a, **k: ""
        # 關鍵：不 mock _generate_report_title，改 patch 底層 ask_llm raise
        monkeypatch.setattr(
            "reasoning.live_research.orchestrator.ask_llm",
            AsyncMock(side_effect=RuntimeError("LLM down")),
        )
        captured = []
        monkeypatch.setattr(
            "reasoning.live_research.orchestrator.emit_sse",
            AsyncMock(side_effect=lambda handler, payload: captured.append(payload)),
        )
        # pre-warm：逼 worker 端 real_logger 建好（其 __init__ 會 clear handlers），之後我掛的
        # handler 才不會被 lazy 建構清掉。直接呼叫 worker 的 _get_real_logger 確保快取存在。
        _proc = _get_async_processor()
        _proc._get_real_logger("live_research.orchestrator")
        # 掛捕捉 handler 到底層 non-propagating logger（worker thread emit 於此，見 docstring）。
        # handler 全程掛著（含 assert 期間），等 worker flush 而非提前移除。
        log_records = []

        class _CaptureHandler(_logging.Handler):
            def emit(self, record):
                log_records.append(record.getMessage())

        _capture = _CaptureHandler()
        _capture.setLevel(_logging.WARNING)
        _underlying = _logging.getLogger("live_research.orchestrator")
        _prev_level = _underlying.level
        _underlying.addHandler(_capture)
        if _underlying.level > _logging.WARNING:
            _underlying.setLevel(_logging.WARNING)
        try:
            state = LiveResearchStageState(
                current_stage=5,
                context_map_json=_make_context_map().model_dump_json(),
                written_sections=[
                    {"section_index": 0, "title": "前言", "content": "x", "status": "drafted"},
                ],
            )
            await orchestrator._run_stage_6(state)
            export_md = [p for p in captured
                         if p.get("message_type") == "live_research_export"][0]["content"]
            assert "# 台灣綠能衝突" in export_md
            assert "> 原始查詢：" not in export_md
            assert state.generated_report_title == ""
            # poll 等背景 worker thread 把 warning dispatch 到底層 logger（非同步 queue）
            _deadline = time.time() + 3.0
            while time.time() < _deadline and not any(
                "report title" in m.lower() for m in log_records
            ):
                time.sleep(0.05)
            assert any("report title" in m.lower() for m in log_records)  # 不 silent fail
        finally:
            _underlying.removeHandler(_capture)
            _underlying.setLevel(_prev_level)

    @pytest.mark.asyncio
    async def test_emit_checkpoint_called_in_stage_1(self, orchestrator, mock_handler):
        """Stage 1 完成時應推送 live_research_checkpoint SSE event。"""
        mock_engine_cm = _make_context_map()

        with patch("reasoning.live_research.orchestrator.BABLoopEngine") as MockEngine:
            engine_instance = MagicMock()
            engine_instance.run_loop = AsyncMock(return_value=mock_engine_cm)
            engine_instance.initial_context_map = mock_engine_cm
            engine_instance.executed_searches = []
            MockEngine.return_value = engine_instance

            state = LiveResearchStageState()
            state.advance_to_stage(1)
            await orchestrator._run_stage_1(state, query="台灣綠能衝突")

        # Should have sent checkpoint SSE
        calls = mock_handler.message_sender.send_message.call_args_list
        checkpoint_calls = [
            c for c in calls
            if c.args and c.args[0].get("message_type") == "live_research_checkpoint"
        ]
        assert len(checkpoint_calls) >= 1
        assert checkpoint_calls[0].args[0]["stage"] == 1

    @pytest.mark.asyncio
    async def test_emit_checkpoint_default_no_new_sample_button(self, orchestrator, mock_handler):
        """預設 show_new_sample_button=False，SSE payload 帶 False。"""
        await orchestrator._emit_checkpoint(stage=1, proposal="p")
        sent = mock_handler.message_sender.send_message.call_args[0][0]
        assert sent["show_new_sample_button"] is False

    @pytest.mark.asyncio
    async def test_emit_checkpoint_new_sample_button_true(self, orchestrator, mock_handler):
        """Stage 3 風格 checkpoint 帶 show_new_sample_button=True。"""
        await orchestrator._emit_checkpoint(stage=3, proposal="p", show_new_sample_button=True)
        sent = mock_handler.message_sender.send_message.call_args[0][0]
        assert sent["show_new_sample_button"] is True

    @pytest.mark.asyncio
    async def test_context_map_to_outline(self, orchestrator):
        """_context_map_to_outline 產出可讀的 outline 字串。"""
        cm = _make_context_map()
        outline = orchestrator._context_map_to_outline(cm)
        assert "台灣綠能衝突" in outline
        assert "土地使用" in outline

    @pytest.mark.asyncio
    async def test_format_initial_items_empty(self, orchestrator):
        """空 items 時 _format_initial_items 回傳 None。"""
        result = orchestrator._format_initial_items([])
        assert result is None

    @pytest.mark.asyncio
    async def test_format_initial_items_non_empty(self, orchestrator):
        """非空 items 時 _format_initial_items 回傳格式化字串。"""
        items = [{"name": "測試文章", "description": "描述"}]
        result = orchestrator._format_initial_items(items)
        assert result is not None
        assert "測試文章" in result

    @pytest.mark.asyncio
    async def test_stage_4_auto_continue_sets_default_format(self, orchestrator):
        """Stage 4 auto-continue 時設定預設格式。"""
        state = LiveResearchStageState(current_stage=4, stage_status="in_progress")
        result = await orchestrator._handle_stage_4_response(state, "", auto_continue=True)
        assert "default" in result.format_specs or "markdown" in str(result.format_specs)

    @pytest.mark.asyncio
    async def test_stage_3_skip_style_analysis_on_empty_message(self, orchestrator):
        """Stage 3 空訊息時跳過 style analysis。"""
        state = LiveResearchStageState(current_stage=3, stage_status="in_progress")
        result = await orchestrator._handle_stage_3_response(state, "", auto_continue=False)
        assert result.style_features_json == ""
        assert result.stage_status == "completed"

    @pytest.mark.asyncio
    async def test_stage_3_adjust_preserves_untouched_dimensions(self, orchestrator):
        """#6 regression: adjust reply ('引用是 APA 格式') must NOT overwrite
        dimensions the user did not mention. Only the cited dimension should
        change; the other 6 must be preserved.

        Tests that:
        1. adjust action dispatches to _run_style_analysis_merge (not _run_style_analysis)
        2. The merge result (7 dims with updated 引用習慣) is stored and re-emitted
        3. stage_status stays at checkpoint (awaiting next confirm round)
        """
        from unittest.mock import AsyncMock, patch
        from reasoning.schemas_live import StyleAnalysisOutput, StyleFeature

        # Seed state with a 7-dimension analysis
        original_features = [
            StyleFeature(dimension="句式結構", observation="短句為主", instruction="保持短句"),
            StyleFeature(dimension="用詞層次", observation="學術精準", instruction="使用術語"),
            StyleFeature(dimension="段落節奏", observation="先總後分", instruction="保持節奏"),
            StyleFeature(dimension="論證風格", observation="演繹", instruction="先原則後推導"),
            StyleFeature(dimension="語氣立場", observation="客觀", instruction="第三人稱"),
            StyleFeature(dimension="引用習慣", observation="目前 numeric", instruction="用 [N]"),
            StyleFeature(dimension="結構偏好", observation="小標題", instruction="每節加小標"),
        ]
        original_analysis = StyleAnalysisOutput(
            features=original_features,
            overall_tone="學術嚴謹",
        )

        state = LiveResearchStageState(
            current_stage=3,
            stage_status="checkpoint",
            style_features_json=original_analysis.model_dump_json(),
        )

        # Simulate intent parse: action = adjust (引用 dimension only)
        adjust_intent = {
            "action": "adjust",
            "adjustments": [{"dimension": "引用習慣", "new_instruction": "改用 APA (作者, 年份)"}],
            "reason": "user 指定 APA 格式",
        }

        # Simulate the LLM merge result: 7 dims with only 引用習慣 updated to APA
        merged_features = [
            StyleFeature(dimension="句式結構", observation="短句為主", instruction="保持短句"),
            StyleFeature(dimension="用詞層次", observation="學術精準", instruction="使用術語"),
            StyleFeature(dimension="段落節奏", observation="先總後分", instruction="保持節奏"),
            StyleFeature(dimension="論證風格", observation="演繹", instruction="先原則後推導"),
            StyleFeature(dimension="語氣立場", observation="客觀", instruction="第三人稱"),
            StyleFeature(dimension="引用習慣", observation="APA 格式", instruction="改用 APA (作者, 年份)"),
            StyleFeature(dimension="結構偏好", observation="小標題", instruction="每節加小標"),
        ]
        merged_analysis = StyleAnalysisOutput(
            features=merged_features,
            overall_tone="學術嚴謹",
        )

        with patch.object(
            orchestrator, "_parse_style_confirmation_intent",
            new=AsyncMock(return_value=adjust_intent),
        ), patch.object(
            orchestrator, "_run_style_analysis_merge",
            new=AsyncMock(return_value=merged_analysis),
        ):
            result = await orchestrator._handle_stage_3_response(
                state, "引用是 APA 格式", auto_continue=False,
            )

        # Must stay at checkpoint (waiting for confirmation of revised analysis)
        assert result.stage_status == "checkpoint"

        # Parse the updated features
        updated = StyleAnalysisOutput.model_validate_json(result.style_features_json)
        updated_dims = {f.dimension: f.instruction for f in updated.features}

        # The 6 untouched dimensions must survive
        assert "句式結構" in updated_dims, "句式結構 was lost"
        assert "用詞層次" in updated_dims, "用詞層次 was lost"
        assert "段落節奏" in updated_dims, "段落節奏 was lost"
        assert "論證風格" in updated_dims, "論證風格 was lost"
        assert "語氣立場" in updated_dims, "語氣立場 was lost"
        assert "結構偏好" in updated_dims, "結構偏好 was lost"

        # The cited dimension must reflect the adjustment
        assert "引用習慣" in updated_dims
        # The new instruction must reference APA
        assert "APA" in updated_dims["引用習慣"], (
            f"引用習慣 instruction should mention APA, got: {updated_dims.get('引用習慣')}"
        )

    # ──── Stage 3 round-2 LLM fail / bad-dict (None 分流, #21) ────

    @pytest.mark.asyncio
    async def test_stage_3_round2_llm_fail_stays_checkpoint(self, orchestrator):
        """Stage 3 第二輪 intent-parse LLM 失敗（None）時，必須停在 Stage 3
        checkpoint + emit lr_copy.LLM_UNAVAILABLE_NARRATION，絕不可 silent confirm 推進
        到 Stage 4（與 Stage 1/4/5 的 None 分流紀律一致；#21 教訓）。
        """
        from unittest.mock import AsyncMock, patch
        from reasoning.schemas_live import StyleAnalysisOutput, StyleFeature

        analysis = StyleAnalysisOutput(
            features=[
                StyleFeature(dimension="句式結構", observation="短句為主", instruction="保持短句"),
            ],
            overall_tone="學術嚴謹",
        )
        state = LiveResearchStageState(
            current_stage=3,
            stage_status="checkpoint",
            style_features_json=analysis.model_dump_json(),
        )
        state.set_checkpoint("準確嗎？需要調整的話告訴我。")

        emitted = []
        with patch.object(
            orchestrator, "_parse_style_confirmation_intent",
            new=AsyncMock(return_value=None),
        ), patch.object(
            orchestrator, "_emit_narration",
            new=AsyncMock(side_effect=lambda msg: emitted.append(msg)),
        ), patch.object(
            orchestrator, "_emit_checkpoint",
            new=AsyncMock(),
        ):
            result = await orchestrator._handle_stage_3_response(
                state, "第三項不夠準", auto_continue=False,
            )

        # 必須停在 Stage 3 checkpoint，未推進
        assert result.current_stage == 3
        assert result.stage_status == "checkpoint"
        # 必須 emit 系統端 narration（非 silent）
        assert lr_copy.LLM_UNAVAILABLE_NARRATION in emitted
        # 分析結果未被動到
        assert result.style_features_json == analysis.model_dump_json()

    @pytest.mark.asyncio
    async def test_stage_3_round2_confirm_still_advances(self, orchestrator):
        """迴歸：LLM 成功且 user 確認（action=confirm）時，Stage 3 仍須正常
        complete_stage 推進（None 分流不可誤傷正常 confirm 路徑）。
        """
        from unittest.mock import AsyncMock, patch
        from reasoning.schemas_live import StyleAnalysisOutput, StyleFeature

        analysis = StyleAnalysisOutput(
            features=[
                StyleFeature(dimension="句式結構", observation="短句為主", instruction="保持短句"),
            ],
            overall_tone="學術嚴謹",
        )
        state = LiveResearchStageState(
            current_stage=3,
            stage_status="checkpoint",
            style_features_json=analysis.model_dump_json(),
        )
        state.set_checkpoint("準確嗎？")

        with patch.object(
            orchestrator, "_parse_style_confirmation_intent",
            new=AsyncMock(return_value={"action": "confirm", "reason": "分析準確"}),
        ), patch.object(
            orchestrator, "_emit_narration", new=AsyncMock(),
        ), patch.object(
            orchestrator, "_emit_checkpoint", new=AsyncMock(),
        ):
            result = await orchestrator._handle_stage_3_response(
                state, "準確，請繼續", auto_continue=False,
            )

        # confirm → complete_stage → 不再停 checkpoint（推進）
        assert result.stage_status != "checkpoint"

    @pytest.mark.asyncio
    async def test_parse_style_confirmation_intent_empty_response_returns_none(self, orchestrator):
        """_parse_style_confirmation_intent：ask_llm 回空（None/falsy）時，
        parser 本身必須直接回 None，而非偽造 confirm dict。
        直接測 parser 不走 caller mock，確保 Task 2 Step 1 改寫有效。
        """
        from unittest.mock import AsyncMock, patch

        # 模擬 ask_llm 回空（None）
        with patch("core.llm.ask_llm", new=AsyncMock(return_value=None)):
            result = await orchestrator._parse_style_confirmation_intent(
                user_message="這樣可以",
                style_features_json='{"features": [], "overall_tone": "測試"}',
            )

        assert result is None, (
            f"empty ask_llm response must return None (not a dict), got: {result!r}"
        )

    @pytest.mark.asyncio
    async def test_parse_style_confirmation_intent_exception_returns_none(self, orchestrator):
        """_parse_style_confirmation_intent：ask_llm 拋 exception（API timeout/429）
        時，parser except 分支必須回 None，而非偽造 confirm dict。
        """
        from unittest.mock import AsyncMock, patch

        # 模擬 ask_llm 拋出 API 錯誤
        with patch(
            "core.llm.ask_llm",
            new=AsyncMock(side_effect=Exception("openai: rate limit exceeded")),
        ):
            result = await orchestrator._parse_style_confirmation_intent(
                user_message="這樣可以",
                style_features_json='{"features": [], "overall_tone": "測試"}',
            )

        assert result is None, (
            f"exception in ask_llm must return None (not a dict), got: {result!r}"
        )

    @pytest.mark.asyncio
    async def test_parse_style_confirmation_intent_missing_action_returns_none(self, orchestrator):
        """LLM 回合法 dict 但缺 action → 當 parse-fail 回 None（不可讓 caller default confirm）。"""
        from unittest.mock import AsyncMock, patch
        with patch("core.llm.ask_llm", new=AsyncMock(return_value={"reason": "confused"})):
            result = await orchestrator._parse_style_confirmation_intent(
                user_message="這樣可以", style_features_json='{"features": []}',
            )
        assert result is None, f"missing action must return None, got: {result!r}"

    @pytest.mark.asyncio
    async def test_parse_style_confirmation_intent_invalid_action_returns_none(self, orchestrator):
        """LLM 回 action 不在 enum（如 'maybe'）→ 當 parse-fail 回 None。"""
        from unittest.mock import AsyncMock, patch
        with patch("core.llm.ask_llm", new=AsyncMock(return_value={"action": "maybe"})):
            result = await orchestrator._parse_style_confirmation_intent(
                user_message="這樣可以", style_features_json='{"features": []}',
            )
        assert result is None, f"invalid action must return None, got: {result!r}"

    @pytest.mark.asyncio
    async def test_parse_style_confirmation_intent_redo_returns_none(self, orchestrator):
        """收緊 enum 的行為斷言：LLM 回舊的 redo → action 不在收緊後 enum
        → 視為 parse-fail 回 None（fail-loud，不 silent confirm）。

        這同時是本 Task「收緊 enum」的 TDD driver：
        - 收緊前（enum 含 redo）：redo 通過守門 → 回 intent dict（非 None）→ FAIL（紅）。
        - 收緊後（enum 只剩 confirm/adjust）：redo 落入 invalid → 回 None → PASS（綠）。
        """
        from unittest.mock import AsyncMock, patch
        with patch("core.llm.ask_llm",
                   new=AsyncMock(return_value={"action": "redo", "reason": "x"})):
            out = await orchestrator._parse_style_confirmation_intent(
                "重來", '{"features":[]}'
            )
        assert out is None

    # ──── UX-6: Stage 4 confirmation round 不蓋寫 format_specs ────

    @pytest.mark.asyncio
    async def test_pending_format_confirmation_short_ok_advances(self, orchestrator):
        """flag=True + msg="OK" → 直接 complete_stage，format_specs 不變。

        TypeAgent refactor (2026-05-19)：confirm_format action 由
        `_classify_stage_4_response` 解出，dispatcher 嚴格按 action 路由，
        不再用 `_classify_confirmation_intent` + `_parse_stage_4_intent` 混合分流。
        """
        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction, Stage4ConfirmTarget,
        )

        original_specs = {"user_specified": "五章 / 7000 字 / 含表格 / APA"}
        state = LiveResearchStageState(
            current_stage=4,
            stage_status="checkpoint",
            format_specs=dict(original_specs),
            pending_format_confirmation=True,
        )
        orchestrator._classify_stage_4_response = AsyncMock(
            return_value=Stage4Response(
                action=Stage4ResponseAction.confirm_format,
                confirm_target=Stage4ConfirmTarget.format,
            )
        )
        result = await orchestrator._handle_stage_4_response(
            state, "OK", auto_continue=False
        )
        assert result.format_specs == original_specs
        assert result.pending_format_confirmation is False
        assert result.stage_status == "completed"

    @pytest.mark.asyncio
    async def test_pending_format_confirmation_short_chinese_advances(self, orchestrator):
        """中文「好的」typed action confirm_format → short-circuit advance。"""
        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction, Stage4ConfirmTarget,
        )

        original_specs = {"user_specified": "APA + 表格"}
        state = LiveResearchStageState(
            current_stage=4,
            stage_status="checkpoint",
            format_specs=dict(original_specs),
            pending_format_confirmation=True,
        )
        orchestrator._classify_stage_4_response = AsyncMock(
            return_value=Stage4Response(
                action=Stage4ResponseAction.confirm_format,
                confirm_target=Stage4ConfirmTarget.format,
            )
        )
        result = await orchestrator._handle_stage_4_response(
            state, "好的", auto_continue=False
        )
        assert result.format_specs == original_specs
        assert result.pending_format_confirmation is False
        assert result.stage_status == "completed"

    @pytest.mark.asyncio
    async def test_pending_format_confirmation_long_message_reparses(self, orchestrator):
        """flag=True + 長訊息「再加一章關於監管」 → typed action new_structure_request。

        TypeAgent refactor：classifier 直接產出 typed action，不再「confirm classifier
        → 視 user reply 是否像 confirm」混合路徑。
        """
        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction, Stage4StructuralPayload, ChapterSpec,
        )

        state = LiveResearchStageState(
            current_stage=4,
            stage_status="checkpoint",
            format_specs={"user_specified": "APA + 表格"},
            pending_format_confirmation=True,
        )
        orchestrator._classify_stage_4_response = AsyncMock(
            return_value=Stage4Response(
                action=Stage4ResponseAction.new_structure_request,
                structural_content=Stage4StructuralPayload(
                    new_chapters=[
                        ChapterSpec(name="前言"),
                        ChapterSpec(name="監管"),
                        ChapterSpec(name="結論"),
                    ],
                ),
            )
        )
        orchestrator._try_stage_4_reframe_entry_typed = AsyncMock(return_value=state)
        await orchestrator._handle_stage_4_response(
            state, "再加一章關於監管", auto_continue=False
        )
        # typed dispatcher 直接 route → reframe entry typed
        orchestrator._try_stage_4_reframe_entry_typed.assert_called_once()

    @pytest.mark.asyncio
    async def test_mixed_path_sets_pending_flag(self, orchestrator):
        """new_structure_request + format_content 一起 → reframe entry typed + 寫 format_specs。"""
        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction, Stage4StructuralPayload,
            Stage4FormatPayload, ChapterSpec,
        )

        state = LiveResearchStageState(
            current_stage=4,
            stage_status="checkpoint",
            pending_format_confirmation=False,
        )
        orchestrator._classify_stage_4_response = AsyncMock(
            return_value=Stage4Response(
                action=Stage4ResponseAction.new_structure_request,
                structural_content=Stage4StructuralPayload(
                    new_chapters=[
                        ChapterSpec(name="第 1 章"),
                        ChapterSpec(name="第 2 章"),
                        ChapterSpec(name="第 3 章"),
                        ChapterSpec(name="第 4 章"),
                        ChapterSpec(name="第 5 章"),
                    ],
                ),
                format_content=Stage4FormatPayload(
                    format_spec_extracted="五章 / 7000 字 / 含表格",
                ),
            )
        )
        orchestrator._try_stage_4_reframe_entry_typed = AsyncMock(return_value=state)
        await orchestrator._handle_stage_4_response(
            state, "我要五章 7000 字含表格然後再加一個結論", auto_continue=False
        )
        # typed entry called with structural + format payload
        orchestrator._try_stage_4_reframe_entry_typed.assert_called_once()

    def test_looks_like_confirmation_keywords(self):
        """各 confirmation keyword 應 return True。"""
        assert _looks_like_confirmation("OK") is True
        assert _looks_like_confirmation("ok") is True
        assert _looks_like_confirmation("好") is True
        assert _looks_like_confirmation("好的") is True
        assert _looks_like_confirmation("確認") is True
        assert _looks_like_confirmation("沒問題") is True
        assert _looks_like_confirmation("就這樣") is True
        assert _looks_like_confirmation("可以") is True
        assert _looks_like_confirmation("go") is True
        assert _looks_like_confirmation("  OK  ") is True  # strip 後仍 match

    def test_looks_like_confirmation_long_messages(self):
        """長訊息（>10 chars）即使含 keyword 也應 return False。"""
        # 「OK 但我想加表格」是常見邊界 case — 不應誤判 confirmation
        assert _looks_like_confirmation("OK 但我想加表格") is False
        assert _looks_like_confirmation("好但我想要再加一個章節") is False
        assert _looks_like_confirmation("再加一章關於監管") is False

    def test_looks_like_confirmation_empty_and_unrelated(self):
        """空字串或無 keyword 的短訊息應 return False。"""
        assert _looks_like_confirmation("") is False
        assert _looks_like_confirmation("   ") is False
        assert _looks_like_confirmation("再加") is False
        assert _looks_like_confirmation("不要") is False

    # ──── Backward Navigation (plan: lr-backward-nav, 2026-06-19) ────

    @pytest.mark.asyncio
    async def test_continue_nav_back_one_resets_to_prev_stage(self, orchestrator):
        # orchestrator = 既有 instance fixture（已 patch AssociatorAgent + mock_handler）
        state = LiveResearchStageState()
        state.current_stage = 4
        state.stage_status = "checkpoint"
        state.book_outline_json = '{"old": true}'
        state.context_map_json = '{"rq": "x", "topics": []}'
        state.evidence_pool_json = '{"1": {"keep": "me"}}'

        new_state = await orchestrator.continue_from_checkpoint(
            state, user_message="", auto_continue=False, nav_action="back_one"
        )
        # back_one from Stage 4 → target Stage 3, checkpoint emitted, no forward run
        assert new_state.current_stage == 3
        assert new_state.stage_status == "checkpoint"
        assert new_state.book_outline_json == ""        # Stage 4 輸出清
        assert new_state.evidence_pool_json == '{"1": {"keep": "me"}}'  # pool 保留

    @pytest.mark.asyncio
    async def test_continue_nav_restart_emits_confirm_first(self, orchestrator):
        # #4：restart 先發確認，不立即清章節
        state = LiveResearchStageState()
        state.current_stage = 5
        state.stage_status = "checkpoint"
        state.written_sections = [{"section_index": 0, "title": "保留待確認"}]
        state.context_map_json = '{"rq": "x", "topics": []}'

        new_state = await orchestrator.continue_from_checkpoint(
            state, user_message="", auto_continue=False, nav_action="restart"
        )
        # 第一輪：只 emit confirm，set pending flag，章節未清
        assert new_state.pending_restart_confirmation is True
        assert new_state.written_sections != []   # 尚未清
        assert new_state.stage_status == "checkpoint"
        assert new_state.current_stage == 5       # 尚未退回

    @pytest.mark.asyncio
    async def test_continue_nav_back_at_stage_1_is_noop(self, orchestrator):
        # 邊界：Stage 1 back_one 無更早 stage → narration + 維持原 checkpoint
        state = LiveResearchStageState()
        state.current_stage = 1
        state.stage_status = "checkpoint"
        state.context_map_json = '{"rq": "x", "topics": []}'
        new_state = await orchestrator.continue_from_checkpoint(
            state, user_message="", auto_continue=False, nav_action="back_one"
        )
        assert new_state.current_stage == 1
        assert new_state.stage_status == "checkpoint"

    @pytest.mark.asyncio
    async def test_nav_restart_confirm_clears_to_stage_1(self, orchestrator):
        orch = orchestrator
        state = LiveResearchStageState()
        state.current_stage = 5
        state.stage_status = "checkpoint"
        state.pending_restart_confirmation = True   # 第一輪已 set
        state.written_sections = [{"section_index": 0, "title": "待清"}]
        state.evidence_pool_json = '{"1": {"keep": "me"}}'
        state.context_map_json = '{"rq": "x", "topics": []}'

        # 用含 token 的強確認詞「確認」走段1 快路徑（不打 LLM），避免依賴 _classify_meta_intent
        new_state = await orch.continue_from_checkpoint(
            state, user_message="確認", auto_continue=False
        )
        assert new_state.current_stage == 1           # 退回 Stage 1
        assert new_state.written_sections == []        # 章節清
        assert new_state.evidence_pool_json == '{"1": {"keep": "me"}}'  # pool 保留
        assert new_state.pending_restart_confirmation is False

    @pytest.mark.asyncio
    async def test_nav_restart_cancel_keeps_sections(self, orchestrator):
        orch = orchestrator
        state = LiveResearchStageState()
        state.current_stage = 5
        state.stage_status = "checkpoint"
        state.pending_restart_confirmation = True
        state.written_sections = [{"section_index": 0, "title": "保留"}]
        state.context_map_json = '{"rq": "x", "topics": []}'

        with patch(
            "reasoning.live_research.orchestrator._classify_meta_intent",
            new=AsyncMock(return_value="abort_cancel"),
        ):
            new_state = await orch.continue_from_checkpoint(
                state, user_message="算了，不要了", auto_continue=False
            )
        assert new_state.written_sections != []        # 取消 → 不動
        assert new_state.current_stage == 5
        assert new_state.pending_restart_confirmation is False

    @pytest.mark.asyncio
    async def test_nav_restart_confirm_llm_down_does_not_clear_sections(
        self, orchestrator
    ):
        # ★ B1 silent-fail 紅線回歸：LLM 故障（_classify_meta_intent 回 None）+ user 打短肯定詞
        # 「好」→ 絕不可落入 _looks_like_bounded_affirmative_shape → reset_to_stage(1) 清章節。
        # 必 fail-loud（停 checkpoint、發系統旁白、章節原封不動、pending flag 保留）。
        orch = orchestrator
        state = LiveResearchStageState()
        state.current_stage = 5
        state.stage_status = "checkpoint"
        state.pending_restart_confirmation = True
        state.written_sections = [{"section_index": 0, "title": "務必保留"}]
        state.evidence_pool_json = '{"1": {"keep": "me"}}'
        state.context_map_json = '{"rq": "x", "topics": []}'

        # 模擬 LLM API 失敗：_classify_meta_intent 回 None（fail-loud 觸發條件）
        with patch(
            "reasoning.live_research.orchestrator._classify_meta_intent",
            new=AsyncMock(return_value=None),
        ):
            new_state = await orch.continue_from_checkpoint(
                state, user_message="好", auto_continue=False
            )
        # 斷言：章節未清、stage 未退、pool 保留、停在 checkpoint、pending flag 仍在
        assert new_state.written_sections == [{"section_index": 0, "title": "務必保留"}]
        assert new_state.current_stage == 5
        assert new_state.stage_status == "checkpoint"
        assert new_state.evidence_pool_json == '{"1": {"keep": "me"}}'
        assert new_state.pending_restart_confirmation is True   # 未被消費成「確認」
        # fail-loud narration 已發（系統端文案，非靜默）
        sent = [
            c.args
            for c in orch.handler.message_sender.send_message.call_args_list
        ]
        assert any(
            lr_copy.LLM_UNAVAILABLE_NARRATION in str(s) for s in sent
        )


# ════════════════════════════════════════════════════════════════════════════
# spec §4.10: Stage 4 special_elements parsing + dispatch (2026-05-16)
# ════════════════════════════════════════════════════════════════════════════


class TestStage4SpecialElementsParsing:
    """spec §4.10：_parse_stage_4_intent 解 special_elements + dispatch 寫入
    state.format_specs。解 E2E v4 VP-3「writer 對 user 特殊格式訴求沒紀律」。
    """

    @pytest.fixture
    def mock_handler(self):
        handler = MagicMock()
        handler.query = "測試研究問題"
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        handler.connection_alive_event = MagicMock()
        handler.connection_alive_event.is_set = MagicMock(return_value=True)
        handler.query_params = {}
        handler.site = "all"
        handler.final_retrieved_items = []
        handler._save_state = AsyncMock()  # plan: durable boundary persist awaits this
        return handler

    @pytest.fixture
    def orchestrator(self, mock_handler):
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=mock_handler)

    @pytest.mark.asyncio
    async def test_classify_stage_4_response_dry_run_returns_unclear(self, orchestrator):
        """TypeAgent refactor (2026-05-19) — 取代舊 `_parse_stage_4_intent` dry_run test.
        dry_run 模式 → safe default 'unclear'（不打 LLM）。
        """
        orchestrator.dry_run = True
        state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")
        response = await orchestrator._classify_stage_4_response(state, "最後加一個 5 國比較表")
        assert response.action.value == "unclear"
        assert response.clarifying_question

    @pytest.mark.asyncio
    async def test_handle_stage_4_adjust_format_writes_special_elements(self, orchestrator):
        """TypeAgent: adjust_format action 含 special_elements → 寫進 state.format_specs。"""
        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction, Stage4FormatPayload, SpecialElementSpec,
        )

        state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")
        orchestrator._classify_stage_4_response = AsyncMock(
            return_value=Stage4Response(
                action=Stage4ResponseAction.adjust_format,
                format_content=Stage4FormatPayload(
                    format_spec_extracted="最後加一個 5 國比較表",
                    special_elements=[
                        SpecialElementSpec(
                            type="table",
                            target_chapter="結果與討論",
                            description="5 國能源比較表",
                        ),
                    ],
                ),
            )
        )

        result = await orchestrator._handle_stage_4_response(
            state, "最後加一個 5 國比較表", auto_continue=False
        )

        assert result.format_specs["special_elements"] == [
            {
                "type": "table",
                "target_chapter": "結果與討論",
                "description": "5 國能源比較表",
            },
        ]
        assert result.format_specs["user_specified"] == "最後加一個 5 國比較表"
        assert result.stage_status == "completed"

    @pytest.mark.asyncio
    async def test_handle_stage_4_new_structure_propagates_special_elements(self, orchestrator):
        """TypeAgent: new_structure_request + format_content → typed reframe entry propagate."""
        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction,
            Stage4StructuralPayload, Stage4FormatPayload,
            SpecialElementSpec, ChapterSpec,
        )

        state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")
        orchestrator._classify_stage_4_response = AsyncMock(
            return_value=Stage4Response(
                action=Stage4ResponseAction.new_structure_request,
                structural_content=Stage4StructuralPayload(
                    new_chapters=[
                        ChapterSpec(name="第 1 章"),
                        ChapterSpec(name="第 2 章"),
                        ChapterSpec(name="第 3 章"),
                        ChapterSpec(name="第 4 章"),
                        ChapterSpec(name="第 5 章"),
                    ],
                ),
                format_content=Stage4FormatPayload(
                    format_spec_extracted="第 3 章用表格",
                    special_elements=[
                        SpecialElementSpec(
                            type="table",
                            target_chapter="第 3 章",
                            description="三家公司比較",
                        ),
                    ],
                ),
            )
        )
        orchestrator._try_stage_4_reframe_entry_typed = AsyncMock(return_value=state)

        await orchestrator._handle_stage_4_response(
            state, "改成 5 章，第 3 章用表格比較三家公司", auto_continue=False
        )
        orchestrator._try_stage_4_reframe_entry_typed.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_stage_4_adjust_format_empty_special_elements_no_overwrite(
        self, orchestrator
    ):
        """TypeAgent: adjust_format + special_elements 空 list → 既有 format_specs
        的 special_elements 不被覆寫（保留前輪訴求）。
        """
        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction, Stage4FormatPayload,
        )

        # state 已有前輪寫入的 special_elements
        state = LiveResearchStageState(
            current_stage=4,
            stage_status="checkpoint",
            format_specs={
                "special_elements": [
                    {
                        "type": "table",
                        "target_chapter": "結果",
                        "description": "前輪訴求",
                    },
                ],
            },
        )
        # 此輪 user 只說「APA」沒提 element，typed action 為 adjust_format + empty elements
        orchestrator._classify_stage_4_response = AsyncMock(
            return_value=Stage4Response(
                action=Stage4ResponseAction.adjust_format,
                format_content=Stage4FormatPayload(
                    format_spec_extracted="APA 引用",
                    special_elements=[],
                ),
            )
        )

        result = await orchestrator._handle_stage_4_response(
            state, "APA 引用", auto_continue=False
        )

        # 既有 special_elements 不被空 list 覆寫（_merge_format_specs_user only overwrites on non-empty）
        assert result.format_specs["special_elements"] == [
            {
                "type": "table",
                "target_chapter": "結果",
                "description": "前輪訴求",
            },
        ]
        assert result.format_specs["user_specified"] == "APA 引用"


# ════════════════════════════════════════════════════════════════════════════
# spec §4.10: _write_section special_elements per-chapter filter (2026-05-16)
# ════════════════════════════════════════════════════════════════════════════


class TestWriteSectionSpecialElementsFilter:
    """spec §4.10：_write_section 從 state.format_specs['special_elements']
    filter 出 target_chapter match 當前 section 的 elements，
    傳進 writer.compose_section(special_elements_for_chapter=...)。

    Match 策略：target_chapter 空字串 → 全章節注入；
    否則 exact match 或雙向 substring 容錯。
    """

    @pytest.fixture
    def mock_handler(self):
        h = MagicMock()
        h.query = "Q"
        h.message_sender = MagicMock()
        h.message_sender.send_message = AsyncMock()
        h.connection_alive_event = MagicMock()
        h.connection_alive_event.is_set = MagicMock(return_value=True)
        h.query_params = {}
        h.site = "all"
        h.final_retrieved_items = []
        h._save_state = AsyncMock()  # plan: durable boundary persist awaits this
        return h

    @pytest.fixture
    def orch(self, mock_handler):
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            # dry_run=False 才會走 writer.compose_section path（dry_run 用 dummy short-circuit）
            return LiveResearchOrchestrator(handler=mock_handler, dry_run=False)

    def _build_cm(self):
        return ContextMap(
            research_question="Q",
            topics=[
                ContextMapTopic(
                    topic_id="t0", name="前言", domain="d",
                    relevance="core", evidence_ids=[1],
                ),
                ContextMapTopic(
                    topic_id="t1", name="結果與討論", domain="d",
                    relevance="core", evidence_ids=[2],
                ),
            ],
            version=1,
        )

    @pytest.mark.asyncio
    async def test_target_chapter_exact_match_only_in_matched_section(
        self, orch
    ):
        """element target_chapter='結果與討論' → 只在寫該章時注入，前言章注入空 list。"""
        from reasoning.schemas_live import LiveWriterSectionOutput

        cm = self._build_cm()
        format_specs = {
            "special_elements": [
                {
                    "type": "table",
                    "target_chapter": "結果與討論",
                    "description": "5 國比較",
                },
            ],
        }

        captured_args: list = []

        async def fake_compose(**kw):
            captured_args.append(kw.get("special_elements_for_chapter"))
            return LiveWriterSectionOutput(
                section_title=kw["section_title"],
                section_content="...",
                sources_used=[],
                confidence_level="Medium",
            )

        # Patch WriterAgent.compose_section 直接
        with patch(
            "reasoning.agents.writer.WriterAgent"
        ) as MockWriter:
            instance = MockWriter.return_value
            instance.compose_section = AsyncMock(side_effect=fake_compose)

            # 寫第一章「前言」— element target=「結果與討論」不 match
            await orch._write_section(
                context_map=cm,
                topic=cm.topics[0],  # 前言
                style_features=None,
                format_specs=format_specs,
            )
            # 寫第二章「結果與討論」— match
            await orch._write_section(
                context_map=cm,
                topic=cm.topics[1],
                style_features=None,
                format_specs=format_specs,
            )

        assert len(captured_args) == 2
        # 前言章：不 match → 空 list
        assert captured_args[0] == []
        # 結果與討論章：match → 包含該 element
        assert captured_args[1] == [
            {
                "type": "table",
                "target_chapter": "結果與討論",
                "description": "5 國比較",
            },
        ]

    @pytest.mark.asyncio
    async def test_target_chapter_substring_no_longer_matches(self, orch):
        """R2（2026-07，C-7）新契約：Stage 5 filter 改 exact 命中，**不再**雙向 substring 容錯。
        user 寫短版「結果」vs section「結果與討論」→ 不注入（交 Stage 5 後衛診斷）。
        substring 對不到章名的病根改由 Stage 4 澄清把 target 定位成章名原文根治。"""
        from reasoning.schemas_live import LiveWriterSectionOutput

        cm = self._build_cm()
        format_specs = {
            "special_elements": [
                {
                    "type": "table",
                    "target_chapter": "結果",  # 短版，非章名原文
                    "description": "5 國",
                },
            ],
        }

        captured_args: list = []

        async def fake_compose(**kw):
            captured_args.append(kw.get("special_elements_for_chapter"))
            return LiveWriterSectionOutput(
                section_title=kw["section_title"],
                section_content="...",
                sources_used=[],
                confidence_level="Medium",
            )

        with patch(
            "reasoning.agents.writer.WriterAgent"
        ) as MockWriter:
            instance = MockWriter.return_value
            instance.compose_section = AsyncMock(side_effect=fake_compose)
            # 寫「結果與討論」章
            await orch._write_section(
                context_map=cm,
                topic=cm.topics[1],
                style_features=None,
                format_specs=format_specs,
            )

        # exact 契約：短版「結果」≠ section「結果與討論」→ 不注入（不再 substring 容錯）
        assert captured_args[0] == []

    @pytest.mark.asyncio
    async def test_target_chapter_empty_injects_into_all_sections(self, orch):
        """element target_chapter='' (unspecified) → 全章節注入（不 leak）。"""
        from reasoning.schemas_live import LiveWriterSectionOutput

        cm = self._build_cm()
        format_specs = {
            "special_elements": [
                {
                    "type": "table",
                    "target_chapter": "",  # unspecified
                    "description": "比較表",
                },
            ],
        }

        captured_args: list = []

        async def fake_compose(**kw):
            captured_args.append(kw.get("special_elements_for_chapter"))
            return LiveWriterSectionOutput(
                section_title=kw["section_title"],
                section_content="...",
                sources_used=[],
                confidence_level="Medium",
            )

        with patch(
            "reasoning.agents.writer.WriterAgent"
        ) as MockWriter:
            instance = MockWriter.return_value
            instance.compose_section = AsyncMock(side_effect=fake_compose)
            for t in cm.topics:
                await orch._write_section(
                    context_map=cm,
                    topic=t,
                    style_features=None,
                    format_specs=format_specs,
                )

        # 兩章都收到該 element（unspecified → all-injection）
        assert len(captured_args) == 2
        for arg in captured_args:
            assert arg == [
                {
                    "type": "table",
                    "target_chapter": "",
                    "description": "比較表",
                },
            ]


# ════════════════════════════════════════════════════════════════════════════
# UX-4: Stage 5 Writer Loop Cancellation (spec §4.7)
# ════════════════════════════════════════════════════════════════════════════


async def _drive_stage_5_to_completion(orch, state, max_iterations: int = 20):
    """VP-7 helper: 反覆呼叫 single-step `_run_stage_5` 直到 last_completed
    達到 total-1（或 idempotent guard 命中），用來在 unit test 模擬 user 連續
    reply「繼續」的完整 dialog loop。

    回傳最終 state。安全上限避免無限迴圈。
    """
    from reasoning.schemas_live import ContextMap as _CM
    cm = _CM.model_validate_json(state.context_map_json)
    writer_sections, _ = orch._resolve_chapter_source(cm, state.format_specs)
    total = len(writer_sections)

    for _ in range(max_iterations):
        state = await orch._run_stage_5(state)
        if state.last_completed_section_index >= total - 1:
            # 再呼叫一次觸發 idempotent final-checkpoint emit（若需要）
            # — 但 happy path 寫到最後一段時已 emit final checkpoint，無需再跑
            return state
    raise AssertionError(
        f"VP-7 driver exceeded {max_iterations} iterations without finishing"
    )


def _make_stage_5_context_map(n: int = 3):
    """Build ContextMap with N core topics for Stage 5 testing."""
    return ContextMap(
        research_question="Q",
        topics=[
            ContextMapTopic(
                topic_id=f"t{i}",
                name=f"topic-{i}",
                domain="d",
                relevance="core",
                description=f"desc {i}",
            )
            for i in range(n)
        ],
        version=1,
    )


class TestStage5UserStop:
    """VP-7: writer per-section checkpoint flow reversal.

    新 flow：`_run_stage_5` 每次只寫一段 + emit per-section checkpoint
    + 把 state.stage5_waiting_for_user 設成 True，等 user reply 再呼叫。
    舊「stop flag in mid-loop」測試已 obsolete（沒有 mid-loop 可 stop），
    保留 disconnect / cancelled / resume / final-section 行為測試。
    """

    @pytest.fixture
    def mock_handler(self):
        h = MagicMock()
        h.query = "Q"
        h.message_sender = MagicMock()
        h.message_sender.send_message = AsyncMock()
        h.connection_alive_event = MagicMock()
        h.connection_alive_event.is_set = MagicMock(return_value=True)
        h.query_params = {}
        h.site = "all"
        h.final_retrieved_items = []
        h._save_state = AsyncMock()
        h._load_state = AsyncMock(return_value=None)  # default: no reload signal
        return h

    @pytest.fixture
    def orch(self, mock_handler):
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=mock_handler, dry_run=True)

    @pytest.mark.asyncio
    async def test_stage_5_first_call_writes_one_section_emits_per_section_checkpoint(self, orch, mock_handler):
        """VP-7 D-A：第一次進 Stage 5 只寫第 1 段、emit mini-checkpoint、return。"""
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
        )


        result = await orch._run_stage_5(state)

        # 只寫一段
        assert len(result.written_sections) == 1
        assert result.last_completed_section_index == 0
        # paused 等 user reply
        assert result.stage5_waiting_for_user is True
        assert result.stage_status == "checkpoint"
        # writer_running 已清
        assert result.stage_5_writer_running is False

        # 驗證 writer_status emit
        emits = [
            c.args[0]
            for c in mock_handler.message_sender.send_message.call_args_list
            if c.args[0].get("message_type") == "live_research_writer_status"
        ]
        statuses = [e["status"] for e in emits]
        assert "started" in statuses
        assert "section_done" in statuses
        # 第 1/N 段（非最後一段）不該 emit all_done
        assert "all_done" not in statuses

        # 驗證 emit 了 per-section checkpoint
        checkpoints = [
            c.args[0]
            for c in mock_handler.message_sender.send_message.call_args_list
            if c.args[0].get("message_type") == "live_research_checkpoint"
        ]
        assert len(checkpoints) == 1
        assert checkpoints[0]["stage"] == 5

    @pytest.mark.asyncio
    async def test_stage_5_mid_section_checkpoint_no_direct_export(self, orch, mock_handler):
        """#11：中段（非最後一段）checkpoint 不該提供「直接匯出」選項 —— 寫到一半就給
        匯出 = 鼓勵交殘缺報告。中段只留 (1) 繼續寫 (2) 修改已寫的某段，完全不提匯出。"""
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
        )


        result = await orch._run_stage_5(state)

        # 寫了第 1/3 段 → 中段 checkpoint
        assert result.last_completed_section_index == 0
        checkpoints = [
            c.args[0]
            for c in mock_handler.message_sender.send_message.call_args_list
            if c.args[0].get("message_type") == "live_research_checkpoint"
        ]
        assert len(checkpoints) == 1
        proposal = checkpoints[0]["proposal"]
        # #11 核心：中段不得出現匯出選項（連「直接匯出」字眼都不該有）
        assert "直接匯出" not in proposal, f"中段 checkpoint 不該有直接匯出選項: {proposal}"
        assert "匯出" not in proposal, f"中段 checkpoint 完全不該提匯出: {proposal}"
        # 仍保留兩個合法選項
        assert "繼續寫" in proposal
        assert "修改" in proposal

    @pytest.mark.asyncio
    async def test_stage_5_final_section_checkpoint_still_offers_export(self, orch, mock_handler):
        """#11 regression：最後一段寫完的 final checkpoint 仍該提供匯出
        （匯出僅在全部寫完才出現，不可因 #11 連 final 的匯出也拿掉）。"""
        cm = _make_stage_5_context_map(n=1)  # 1 段 → 寫完即最後一段 → all_done 分支
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
        )


        result = await orch._run_stage_5(state)

        checkpoints = [
            c.args[0]
            for c in mock_handler.message_sender.send_message.call_args_list
            if c.args[0].get("message_type") == "live_research_checkpoint"
        ]
        assert len(checkpoints) == 1
        proposal = checkpoints[0]["proposal"]
        # 全寫完 → final checkpoint 仍提供匯出
        assert "匯出" in proposal, f"全寫完的 final checkpoint 應提供匯出: {proposal}"

    @pytest.mark.asyncio
    async def test_stage_5_continue_writes_next_section(self, orch, mock_handler):
        """VP-7：state.last_completed=0 → 第二次跑 _run_stage_5 寫第 2 段 + mini-checkpoint。"""
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=0,
            written_sections=[{
                "section_index": 0, "title": "topic-0",
                "content": "...", "sources_used": [], "confidence_level": "Medium",
                "chapter_summary": "",
            }],
        )


        result = await orch._run_stage_5(state)

        # 累計 2 段（1 carried + 1 new）
        assert len(result.written_sections) == 2
        assert result.last_completed_section_index == 1
        assert result.stage5_waiting_for_user is True
        assert result.stage_status == "checkpoint"

        # writer_status started 應反映 completed=1 (resume-aware)
        emits = [
            c.args[0]
            for c in mock_handler.message_sender.send_message.call_args_list
            if c.args[0].get("message_type") == "live_research_writer_status"
        ]
        started = [e for e in emits if e["status"] == "started"][0]
        assert started["completed"] == 1
        # 不是最後一段：no all_done
        assert "all_done" not in [e["status"] for e in emits]

    @pytest.mark.asyncio
    async def test_stage_5_skip_intent_unfinished_asks_clarify_no_export(self, orch, mock_handler):
        """user 在 Stage 5 說「跳過/不用了」(META_INTENT_SKIP) 且報告未寫完：
        不可靜默 fall-through 到 _parse_revision_intent（可能被誤判成 done → 匯出半成品）。
        必須停原地、emit 釐清 narration、不 complete_stage。"""
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=0,  # 寫了 1/3 段 → remaining=2
            written_sections=[
                {"section_index": 0, "title": "topic-0", "content": "x", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
            ],
        )
        # _parse_revision_intent 在 dry_run 下回 done；若 SKIP 誤 fall-through 會 complete_stage。
        # spy：SKIP 分支正確時 _parse_revision_intent 完全不該被呼叫。
        parse_spy = AsyncMock(return_value={"action": "done", "reason": "spy"})
        orch._parse_revision_intent = parse_spy

        meta_mock = AsyncMock(return_value=META_INTENT_SKIP)
        with patch("reasoning.live_research.orchestrator._classify_meta_intent",
                   new=meta_mock):
            result = await orch._handle_stage_5_response(state, "跳過", auto_continue=False)

        # 訊息確實穿過 shortcut 層、到達 meta-intent dispatch（「跳過」不在任何 shortcut frozenset）
        meta_mock.assert_awaited_once()
        # 不靜默匯出：釘死 checkpoint 語意（不只 != completed，防未來 regress 成 in_progress 仍 pass）
        assert result.current_stage == 5
        assert result.stage_status == "checkpoint"
        assert result.checkpoint_prompt
        # 不混進 revision intent
        assert parse_spy.call_count == 0
        # emit 了釐清 narration
        sent = [
            c.args[0] for c in mock_handler.message_sender.send_message.call_args_list
        ]
        narrations = [
            m for m in sent if m.get("message_type") == "live_research_narration"
        ]
        assert any("繼續寫" in m.get("text", "") for m in narrations), \
            f"未寫完 SKIP 應提供「繼續寫」釐清選項, got {narrations}"
        # 未寫完不可出現匯出邀請
        assert not any("匯出" in m.get("text", "") for m in narrations), \
            f"未寫完 SKIP narration 不該邀請匯出, got {narrations}"

    @pytest.mark.asyncio
    async def test_stage_5_skip_intent_all_done_offers_accept_not_silent_export(self, orch, mock_handler):
        """全寫完時 user 說「跳過」：給「接受/繼續編輯」釐清，但仍不靜默 complete_stage，
        也不混進 _parse_revision_intent。"""
        cm = _make_stage_5_context_map(n=2)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=1,  # 2/2 寫完 → remaining=0
            written_sections=[
                {"section_index": 0, "title": "topic-0", "content": "x", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
                {"section_index": 1, "title": "topic-1", "content": "y", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
            ],
        )
        parse_spy = AsyncMock(return_value={"action": "done", "reason": "spy"})
        orch._parse_revision_intent = parse_spy

        meta_mock = AsyncMock(return_value=META_INTENT_SKIP)
        with patch("reasoning.live_research.orchestrator._classify_meta_intent",
                   new=meta_mock):
            result = await orch._handle_stage_5_response(state, "不用了", auto_continue=False)

        meta_mock.assert_awaited_once()  # 「不用了」不在 shortcut frozenset，必達 meta dispatch
        assert result.current_stage == 5
        assert result.stage_status == "checkpoint"
        assert result.checkpoint_prompt
        assert parse_spy.call_count == 0
        sent = [
            c.args[0] for c in mock_handler.message_sender.send_message.call_args_list
        ]
        narrations = [
            m for m in sent if m.get("message_type") == "live_research_narration"
        ]
        assert any("接受" in m.get("text", "") for m in narrations), \
            f"全寫完 SKIP 應提供「接受」選項, got {narrations}"

    @pytest.mark.asyncio
    async def test_stage_5_llm_done_unfinished_blocked_no_export(self, orch, mock_handler):
        """D-2026-06-11 決策 4：自然語句（「好了就這樣」）被 LLM 判 done 但報告未寫完：
        不可 complete_stage（匯出半成品），必須停 checkpoint + emit 釐清文案。
        對照：整句「完成」走 export keyword shortcut 已被 #11B block —— 本 gate 把
        語意等價的 LLM-done 路徑拉到同一政策（中途完全不給匯出）。"""
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=0,  # 寫了 1/3 段 → remaining=2
            written_sections=[
                {"section_index": 0, "title": "topic-0", "content": "x", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
            ],
        )
        parse_mock = AsyncMock(return_value={"action": "done", "reason": "user 表達結束"})
        orch._parse_revision_intent = parse_mock
        # 取捨 (a) 被否決：不可硬轉 continue（_run_stage_5 不該被呼叫）
        run_5_mock = AsyncMock(side_effect=lambda s: s)
        orch._run_stage_5 = run_5_mock

        with patch("reasoning.live_research.orchestrator._classify_meta_intent",
                   new=AsyncMock(return_value="substantive")):
            result = await orch._handle_stage_5_response(state, "好了就這樣", auto_continue=False)

        # 確實走到 LLM intent parse（「好了就這樣」不在任何 shortcut frozenset、非 SKIP/ABORT）
        parse_mock.assert_awaited_once()
        # 不匯出半成品：釘死 checkpoint 三件組（防 regress 成 in_progress 仍 pass）
        assert result.current_stage == 5
        assert result.stage_status == "checkpoint"
        assert result.checkpoint_prompt == lr_copy.stage5_done_unfinished_gate_prompt(2)
        # 不硬轉 continue
        run_5_mock.assert_not_called()
        # narration = lr_copy 單一事實源逐字 emit + 含可行動選項鍵詞「繼續寫」
        narrations = [
            c.args[0].get("text", "")
            for c in mock_handler.message_sender.send_message.call_args_list
            if c.args[0].get("message_type") == "live_research_narration"
        ]
        assert lr_copy.stage5_done_unfinished_gate_prompt(2) in narrations
        assert any("繼續寫" in t for t in narrations)

    @pytest.mark.asyncio
    async def test_stage_5_llm_done_all_written_still_completes(self, orch, mock_handler):
        """Regression pin：全寫完時 LLM-done 照舊 complete_stage 進 Stage 6
        （completeness gate 只攔未寫完，不可誤傷正常結束）。"""
        cm = _make_stage_5_context_map(n=2)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=1,  # 2/2 寫完 → remaining=0
            written_sections=[
                {"section_index": 0, "title": "topic-0", "content": "x", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
                {"section_index": 1, "title": "topic-1", "content": "y", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
            ],
        )
        parse_mock = AsyncMock(return_value={"action": "done", "reason": "ok"})
        orch._parse_revision_intent = parse_mock

        with patch("reasoning.live_research.orchestrator._classify_meta_intent",
                   new=AsyncMock(return_value="substantive")):
            result = await orch._handle_stage_5_response(state, "好了就這樣", auto_continue=False)

        parse_mock.assert_awaited_once()
        assert result.stage_status == "completed"

    @pytest.mark.asyncio
    async def test_stage_5_writes_final_section_emits_final_checkpoint(self, orch, mock_handler):
        """VP-7：寫到最後一段 → emit all_done + final checkpoint「進入匯出？」"""
        cm = _make_stage_5_context_map(n=3)
        # 已寫 0+1，準備寫第 2 段（last）
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=1,
            written_sections=[
                {"section_index": 0, "title": "topic-0", "content": "x", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
                {"section_index": 1, "title": "topic-1", "content": "y", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
            ],
        )


        result = await orch._run_stage_5(state)
        assert len(result.written_sections) == 3
        assert result.last_completed_section_index == 2
        assert result.stage5_waiting_for_user is True
        assert result.stage_status == "checkpoint"

        emits = [
            c.args[0]
            for c in mock_handler.message_sender.send_message.call_args_list
            if c.args[0].get("message_type") == "live_research_writer_status"
        ]
        statuses = [e["status"] for e in emits]
        assert "all_done" in statuses
        # final checkpoint proposal 應含「匯出」字樣
        checkpoints = [
            c.args[0]
            for c in mock_handler.message_sender.send_message.call_args_list
            if c.args[0].get("message_type") == "live_research_checkpoint"
        ]
        assert len(checkpoints) == 1
        assert "匯出" in checkpoints[0]["proposal"]

    @pytest.mark.asyncio
    async def test_synthesis_chapter_receives_all_prior_summaries(self, orch, mock_handler):
        """B(b): 寫第 3 章（synthesis）時，_write_section 收到的 all_prior_chapter_summaries
        含前 2 章的 chapter_summary（不只最後一章）。"""
        from reasoning.schemas_live import LiveWriterSectionOutput

        captured = {}

        async def fake_write_section(**kw):
            captured["all_prior"] = kw.get("all_prior_chapter_summaries")
            return (
                LiveWriterSectionOutput(
                    section_title="topic-2", section_content="x" * 300,
                    sources_used=[1], confidence_level="High", status="drafted",
                ),
                False,
            )

        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=1,
            written_sections=[
                {"section_index": 0, "title": "topic-0", "content": "x",
                 "sources_used": [], "confidence_level": "Medium",
                 "chapter_summary": "第1章摘要", "entities": ["苗栗"]},
                {"section_index": 1, "title": "topic-1", "content": "y",
                 "sources_used": [], "confidence_level": "Medium",
                 "chapter_summary": "第2章摘要", "entities": ["德國北萊茵"]},
            ],
        )

        orch._write_section = fake_write_section

        await orch._run_stage_5(state)

        assert captured["all_prior"] == ["第1章摘要", "第2章摘要"]

    @pytest.mark.asyncio
    async def test_stage_5_disconnect_capped_stops_without_write(self, orch, mock_handler):
        """plan: lr-sse-reconnect-resume — 斷線**且已達防呆上限** → 停、標 capped、不寫。

        舊行為「斷線就 abort」已改：斷線只標離線；唯有達上限才停。此處用 already-capped
        state（offline_checkpoint_advances 已達 max）驗「達上限即停」。
        """
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
            offline_since=1.0,                  # 久遠 → wall cap 也會觸發
            offline_checkpoint_advances=1,      # 已達 default max=1
        )

        mock_handler.connection_alive_event.is_set = MagicMock(return_value=False)

        result = await orch._run_stage_5(state)

        # 達上限：停、標 capped、不寫 section
        assert result.offline_capped is True
        assert result.offline_cap_reason in ("next_checkpoint", "wall_seconds")
        assert len(result.written_sections) == 0
        assert result.last_completed_section_index == -1
        assert result.stage_5_writer_running is False

    @pytest.mark.asyncio
    async def test_stage_5_cancelled_error_propagates(self, orch, mock_handler):
        """CancelledError 發生在 _write_section 過程中必須 re-raise + writer_running 清掉。"""
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
        )


        async def cancel_immediately(*args, **kwargs):
            raise asyncio.CancelledError("simulated disconnect")

        orch._write_section = cancel_immediately

        with pytest.raises(asyncio.CancelledError):
            await orch._run_stage_5(state)

        # writer_running cleared via finally
        assert state.stage_5_writer_running is False

    @pytest.mark.asyncio
    async def test_stage_5_writer_llm_fail_degrades_gracefully(self, orch, mock_handler):
        """Stage 5 writer LLM-fail（ValueError / TimeoutError）不冒泡到 caller，
        改 emit narration + re-emit per-section checkpoint（對齊 Stage 3 pattern）。

        Sentry issue 7537040772 root cause 防護：
        base.py:398 raise ValueError("LLM returned empty response...") 不可噴給 user。
        """
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
        )

        # Mock _write_section raise ValueError（模擬 LLM empty response / timeout）
        async def fail_writer(**kw):
            raise ValueError("LLM returned empty response. This usually indicates an error in the LLM provider.")

        orch._write_section = fail_writer

        # _run_stage_5 不應 raise — 應 degrade 並 return state at checkpoint
        result = await orch._run_stage_5(state)

        # 1. 不冒泡：上方 await 沒 raise
        # 2. state 停在 checkpoint（waiting_for_user = True）
        assert result.stage5_waiting_for_user is True, (
            "stage5_waiting_for_user 應為 True（checkpoint 狀態）"
        )
        # 3. checkpoint_prompt 含「重試」字樣
        assert result.checkpoint_prompt is not None, "checkpoint_prompt 不應為 None"
        assert "重試" in result.checkpoint_prompt or "再試" in result.checkpoint_prompt, (
            f"checkpoint_prompt 應含重試提示，實際：{result.checkpoint_prompt!r}"
        )
        # 4. last_completed_section_index 不應前進（失敗段不計完成）
        assert result.last_completed_section_index == -1, (
            f"LLM-fail 不應將該段標為完成，last_completed={result.last_completed_section_index}"
        )
        # 5. _emit_narration 被呼叫（含 LLM unavailable 文案）
        narration_calls = [
            str(call)
            for call in mock_handler.message_sender.send_message.call_args_list
        ]
        assert any(
            "暫時" in c or "請稍候" in c or "再試" in c
            for c in narration_calls
        ), f"應 emit LLM unavailable narration，實際呼叫：{narration_calls}"

    @pytest.mark.asyncio
    async def test_stage_5_writer_timeout_degrades_gracefully(self, orch, mock_handler):
        """asyncio.TimeoutError 同樣觸發降級（不冒泡）。"""
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
        )

        async def timeout_writer(**kw):
            raise asyncio.TimeoutError("inner_timeout=80 exceeded")

        orch._write_section = timeout_writer

        result = await orch._run_stage_5(state)

        assert result.stage5_waiting_for_user is True
        assert result.last_completed_section_index == -1

    @pytest.mark.asyncio
    async def test_stage_5_all_done_idempotent_when_resumed_after_finish(self, orch, mock_handler):
        """VP-7：若 state.last_completed == total-1（已全部寫完），再呼叫 _run_stage_5
        應直接 emit final checkpoint，不再呼叫 _write_section。"""
        cm = _make_stage_5_context_map(n=2)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=1,
            written_sections=[
                {"section_index": 0, "title": "topic-0", "content": "x", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
                {"section_index": 1, "title": "topic-1", "content": "y", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
            ],
        )


        # _write_section 不應被呼叫
        write_mock = AsyncMock()
        orch._write_section = write_mock

        result = await orch._run_stage_5(state)
        write_mock.assert_not_called()
        assert result.stage5_waiting_for_user is True
        assert result.stage_status == "checkpoint"
        # final checkpoint emit 過
        checkpoints = [
            c.args[0]
            for c in mock_handler.message_sender.send_message.call_args_list
            if c.args[0].get("message_type") == "live_research_checkpoint"
        ]
        assert len(checkpoints) == 1
        assert "匯出" in checkpoints[0]["proposal"]


class TestStage5IntentShortcutsAndFallback:
    """VP-7 Phase 3: short-confirm keyword shortcut + revise target fallback。"""

    @pytest.fixture
    def mock_handler(self):
        h = MagicMock()
        h.query = "Q"
        h.message_sender = MagicMock()
        h.message_sender.send_message = AsyncMock()
        h.connection_alive_event = MagicMock()
        h.connection_alive_event.is_set = MagicMock(return_value=True)
        h.query_params = {}
        h.site = "all"
        h.final_retrieved_items = []
        h._save_state = AsyncMock()
        h._load_state = AsyncMock(return_value=None)
        return h

    @pytest.fixture
    def orch(self, mock_handler):
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=mock_handler, dry_run=True)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("msg", ["繼續", "OK", "好", "下一段", "繼續寫", "next", "ok"])
    async def test_short_continue_shortcut_skips_llm_and_runs_stage_5(self, orch, msg):
        """短 confirm 訊息 → 不打 LLM，直接 _run_stage_5（寫下一段）。"""
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=0,
            written_sections=[{
                "section_index": 0, "title": "topic-0", "content": "x",
                "sources_used": [], "confidence_level": "Medium", "chapter_summary": "",
            }],
        )
        # LLM intent parser 不應該被打到
        parse_mock = AsyncMock(return_value={"action": "done"})
        orch._parse_revision_intent = parse_mock

        run_5_mock = AsyncMock(side_effect=lambda s: s)
        orch._run_stage_5 = run_5_mock

        await orch._handle_stage_5_response(state, msg, auto_continue=False)

        parse_mock.assert_not_called()
        run_5_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_long_message_does_not_trigger_continue_shortcut(self, orch):
        """訊息 >15 字含「繼續」 → 不走 shortcut，仍打 LLM。"""
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=0,
            written_sections=[{
                "section_index": 0, "title": "topic-0", "content": "x",
                "sources_used": [], "confidence_level": "Medium", "chapter_summary": "",
            }],
        )
        parse_mock = AsyncMock(return_value={"action": "done"})
        orch._parse_revision_intent = parse_mock

        long_msg = "繼續寫但是第 1 段太短，先幫我補充背景脈絡再進下一段"
        # 非-shortcut 文案會打 _classify_meta_intent；unit 套件斷 key 後不打真 LLM，
        # pin 為 substantive 讓流程走到 _parse_revision_intent（本測試標的）。
        with patch("reasoning.live_research.orchestrator._classify_meta_intent",
                   new=AsyncMock(return_value="substantive")):
            await orch._handle_stage_5_response(state, long_msg, auto_continue=False)
        parse_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_revise_section_target_missing_emits_clarifying_question(self, orch, mock_handler):
        """FIX-6 (Cayenne #14, 2026-05-29)：反轉舊 D-D silent fallback。

        舊行為（已廢）：LLM 回 revise_section 但 target_index=null → 靜默 fallback 到
        last_completed_section_index 改一段。Cayenne 實測會改錯地方。
        新行為：target_index=None → emit clarifying question 列出已寫章節、停在
        checkpoint、**不 mutate 任何 section**（不呼叫 _write_section）、不推進。
        """
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=1,  # 剛寫完第 1 段（index）
            written_sections=[
                {"section_index": 0, "title": "topic-0", "content": "x", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
                {"section_index": 1, "title": "topic-1", "content": "y", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
            ],
        )
        # LLM 回 revise_section 但沒給 target_index
        orch._parse_revision_intent = AsyncMock(return_value={
            "action": "revise_section",
            "target_index": None,
            "instruction": "太短",
            "reason": "vague",
        })

        # _write_section 不該被呼叫（不可 silent mutate）
        write_called = {"hit": False}
        from reasoning.schemas_live import LiveWriterSectionOutput  # noqa: F401
        async def fake_write(context_map, topic, **kw):
            write_called["hit"] = True
            raise AssertionError("FIX-6: target 不明時不應 mutate/寫任何 section")
        orch._write_section = fake_write

        # 非-shortcut 文案會打 _classify_meta_intent；斷 key 後 pin substantive 不打真 LLM。
        with patch("reasoning.live_research.orchestrator._classify_meta_intent",
                   new=AsyncMock(return_value="substantive")):
            result = await orch._handle_stage_5_response(state, "再修一下", auto_continue=False)

        # 不 mutate 任何 section
        assert write_called["hit"] is False
        # 停在 checkpoint 等 user 指明
        assert result.stage_status == "checkpoint"
        # checkpoint prompt 為 clarifying question，列出已寫章節（1-based 顯示）
        assert "請指明要修改哪一段" in result.checkpoint_prompt
        assert "第 1 章「topic-0」" in result.checkpoint_prompt
        assert "第 2 章「topic-1」" in result.checkpoint_prompt

    @pytest.mark.asyncio
    async def test_revise_section_target_index_out_of_range_clamps(self, orch, mock_handler):
        """LLM 回 revise_section 但 target_index > total-1 → clamp 到 last_completed。"""
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=2,
            written_sections=[
                {"section_index": 0, "title": "topic-0", "content": "x", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
                {"section_index": 1, "title": "topic-1", "content": "y", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
                {"section_index": 2, "title": "topic-2", "content": "z", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
            ],
        )
        orch._parse_revision_intent = AsyncMock(return_value={
            "action": "revise_section",
            "target_index": 99,  # 1-based 99 → 0-based 98，仍超出範圍 → clamp
            "instruction": "改第 99",
            "reason": "x",
        })

        captured = {"target_name": None}
        from reasoning.schemas_live import LiveWriterSectionOutput
        async def fake_write(context_map, topic, **kw):
            captured["target_name"] = topic.name if hasattr(topic, "name") else topic["name"]
            return (
                LiveWriterSectionOutput(
                    section_title=captured["target_name"],
                    section_content="r",
                    sources_used=[],
                    confidence_level="Medium",
                    chapter_summary="",
                ),
                False,
            )
        orch._write_section = fake_write

        with patch("reasoning.live_research.orchestrator._classify_meta_intent",
                   new=AsyncMock(return_value="substantive")):
            await orch._handle_stage_5_response(state, "改不存在的段", auto_continue=False)
        # Clamp → last_completed (=2) → topic-2
        assert captured["target_name"] == "topic-2"

    @pytest.mark.asyncio
    async def test_revise_user_says_section_2_edits_zero_based_index_1(self, orch, mock_handler):
        """Bug 1 (off-by-one): user 說「第 2 段」→ LLM 回 1-based target_index=2
        → 消費端轉 0-based → 改 writer_sections[1]（使用者語義的第 2 段）。

        修前：target_index 被當 0-based → 改 [2]（實際第 3 段）。
        """
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=2,
            written_sections=[
                {"section_index": 0, "title": "topic-0", "content": "x", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
                {"section_index": 1, "title": "topic-1", "content": "y", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
                {"section_index": 2, "title": "topic-2", "content": "z", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
            ],
        )
        # 使用者口語「第 2 段」→ 新 prompt 契約：LLM 回 1-based 段號 = 2
        orch._parse_revision_intent = AsyncMock(return_value={
            "action": "revise_section",
            "target_index": 2,
            "instruction": "第 2 段太短",
            "reason": "user 指定第 2 段",
        })

        captured = {"target_name": None}
        from reasoning.schemas_live import LiveWriterSectionOutput
        async def fake_write(context_map, topic, **kw):
            captured["target_name"] = topic.name if hasattr(topic, "name") else topic["name"]
            return (
                LiveWriterSectionOutput(
                    section_title=captured["target_name"],
                    section_content="r",
                    sources_used=[],
                    confidence_level="Medium",
                    chapter_summary="",
                ),
                False,
            )
        orch._write_section = fake_write

        # 非-shortcut 文案會打 _classify_meta_intent；斷 key 後 pin substantive 不打真 LLM。
        with patch("reasoning.live_research.orchestrator._classify_meta_intent",
                   new=AsyncMock(return_value="substantive")):
            result = await orch._handle_stage_5_response(state, "第 2 段太短", auto_continue=False)
        # 使用者語義第 2 段 = 0-based index 1 = topic-1
        assert captured["target_name"] == "topic-1"
        # user_voice 用轉換後的 0-based key 存
        assert result.user_voice.revise_instructions.get(1) == ["第 2 段太短"]

    # ────────────────────────────────────────────────────────────────────
    # Bug #14 root fix: continue / export shortcut 改「正規化後完全匹配白名單」
    # 取代 substring + veto 枚舉（reward hack）。
    # 這些 case 直接測 module-level helper（純邏輯，無 LLM、無 orchestrator）。
    # ────────────────────────────────────────────────────────────────────

    @pytest.mark.parametrize("msg", [
        "好", "好的", "OK", "ok", "Ok", "繼續", "下一段", "下一章",
        "next", "Next", "go", "Go", "繼續寫", "接著寫", "往下寫",
        # 正規化：尾部標點 / 空白應被剝除後仍命中
        "好。", "繼續！", "  繼續  ", "OK!", "下一段～", "繼續寫，",
    ])
    def test_continue_shortcut_whitelist_hits_pure_confirm(self, msg):
        from reasoning.live_research.orchestrator import _looks_like_continue_shortcut
        assert _looks_like_continue_shortcut(msg) is True

    @pytest.mark.parametrize("msg", [
        # 帶內容的句子一律 fall through 到 LLM（不該命中 shortcut）
        "這段不太好", "這段怪怪的", "語氣太硬", "這裡卡卡的", "第2段重講",
        "不錯,繼續", "不錯，繼續", "好像哪裡怪", "繼續加強第三段",
        "好是好但太短", "繼續寫但先補背景", "好，但這段重寫",
        # 否定確認也不該命中（spec: 「不錯,繼續」含「繼續」substring 但整句非純確認）
        "繼續下一段然後改第一段", "OK 但我想加表格",
        # 空 / 純標點
        "", "   ", "。", "，",
    ])
    def test_continue_shortcut_whitelist_rejects_content_bearing(self, msg):
        from reasoning.live_research.orchestrator import _looks_like_continue_shortcut
        assert _looks_like_continue_shortcut(msg) is False

    @pytest.mark.parametrize("msg", [
        "匯出", "export", "Export", "下載", "下一階段", "下一個階段",
        "結束", "完成", "匯出。", "  export  ", "完成！",
    ])
    def test_export_shortcut_whitelist_hits_pure_export(self, msg):
        from reasoning.live_research.orchestrator import _looks_like_export_shortcut
        assert _looks_like_export_shortcut(msg) is True

    @pytest.mark.parametrize("msg", [
        # 「完成」太泛：句中提及完成不該觸發 export（spec 明確點名）
        "第2段還沒完成", "這段還沒完成", "完成度不夠", "還沒完成欸",
        # 帶內容句一律 fall through
        "幫我匯出前先改第一段", "結束前再補一段", "下載完整版但先改標題",
        "", "   ",
    ])
    def test_export_shortcut_whitelist_rejects_content_bearing(self, msg):
        from reasoning.live_research.orchestrator import _looks_like_export_shortcut
        assert _looks_like_export_shortcut(msg) is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("msg", [
        "這段不太好", "這段怪怪的", "語氣太硬", "這裡卡卡的",
        "第2段重講", "不錯,繼續", "好像哪裡怪", "繼續加強第三段",
        "好是好但太短",
    ])
    async def test_content_bearing_msg_falls_through_to_llm(self, orch, msg):
        """Bug #14 整合：任何帶內容的句子（無論含不含 continue 詞）都不可被
        continue/export shortcut 吃掉，必須 fall through 到 _parse_revision_intent。

        修前（substring + veto 枚舉）：「好像哪裡怪」含「好」無 veto → 誤命中 continue；
        「不錯,繼續」含「繼續」但被「不」veto 攔（對的理由錯了）。
        """
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=0,
            written_sections=[{
                "section_index": 0, "title": "topic-0", "content": "x",
                "sources_used": [], "confidence_level": "Medium", "chapter_summary": "",
            }],
        )
        parse_mock = AsyncMock(return_value={
            "action": "revise_section", "target_index": None,
            "instruction": msg, "reason": "vague",
        })
        orch._parse_revision_intent = parse_mock
        # shortcut 命中會打 _run_stage_5 / complete_stage — 都不該發生
        run_5_mock = AsyncMock(side_effect=lambda s: s)
        orch._run_stage_5 = run_5_mock

        # meta-intent 接線必然成本：非-shortcut 文案會打 _classify_meta_intent。
        # 補 mock 讓測試不打真 LLM（pass-through to _parse_revision_intent 即是測試目標）。
        with patch("reasoning.live_research.orchestrator._classify_meta_intent",
                   new=AsyncMock(return_value="substantive")):
            await orch._handle_stage_5_response(state, msg, auto_continue=False)

        parse_mock.assert_called_once()
        run_5_mock.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("msg", ["好", "OK", "繼續", "下一段", "繼續寫", "next", "往下寫"])
    async def test_pure_confirm_still_hits_continue_shortcut(self, orch, msg):
        """Regression：純確認仍命中 continue shortcut（不打 LLM）。"""
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=0,
            written_sections=[{
                "section_index": 0, "title": "topic-0", "content": "x",
                "sources_used": [], "confidence_level": "Medium", "chapter_summary": "",
            }],
        )
        parse_mock = AsyncMock(return_value={"action": "done"})
        orch._parse_revision_intent = parse_mock
        run_5_mock = AsyncMock(side_effect=lambda s: s)
        orch._run_stage_5 = run_5_mock

        await orch._handle_stage_5_response(state, msg, auto_continue=False)
        parse_mock.assert_not_called()
        run_5_mock.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("msg", ["匯出", "export", "下載", "完成", "下一階段"])
    async def test_pure_export_still_completes_stage(self, orch, msg):
        """Regression：純匯出詞仍命中 export shortcut → complete_stage。"""
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=2,
            written_sections=[
                {"section_index": 0, "title": "topic-0", "content": "x", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
                {"section_index": 1, "title": "topic-1", "content": "y", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
                {"section_index": 2, "title": "topic-2", "content": "z", "sources_used": [], "confidence_level": "Medium", "chapter_summary": ""},
            ],
        )
        parse_mock = AsyncMock(return_value={"action": "done"})
        orch._parse_revision_intent = parse_mock
        run_5_mock = AsyncMock(side_effect=lambda s: s)
        orch._run_stage_5 = run_5_mock

        result = await orch._handle_stage_5_response(state, msg, auto_continue=False)
        parse_mock.assert_not_called()
        run_5_mock.assert_not_called()
        # complete_stage → stage_status 標記 completed（不走 revise / continue）
        assert result.stage_status == "completed"

    @pytest.mark.asyncio
    async def test_revise_vague_negative_not_eaten_by_continue_shortcut(self, orch):
        """Bug 2：「這段不太好」含「好」+ ≤15 字，不可被 continue shortcut 吃掉，
        必須走到 _parse_revision_intent（→ revise_section / target None → clarifying）。

        修前：substring 「好」命中 continue shortcut → 跳過 intent parse 直接寫下一段。
        """
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=0,
            written_sections=[{
                "section_index": 0, "title": "topic-0", "content": "x",
                "sources_used": [], "confidence_level": "Medium", "chapter_summary": "",
            }],
        )
        # intent parser 必須被打到（不被 continue shortcut 吃掉）
        parse_mock = AsyncMock(return_value={
            "action": "revise_section",
            "target_index": None,
            "instruction": "不太好",
            "reason": "vague",
        })
        orch._parse_revision_intent = parse_mock

        # continue shortcut 命中會打 _run_stage_5 寫新段 — 不該發生
        run_5_mock = AsyncMock(side_effect=lambda s: s)
        orch._run_stage_5 = run_5_mock

        # 非-shortcut 文案會打 _classify_meta_intent；斷 key 後 pin substantive 不打真 LLM。
        with patch("reasoning.live_research.orchestrator._classify_meta_intent",
                   new=AsyncMock(return_value="substantive")):
            await orch._handle_stage_5_response(state, "這段不太好", auto_continue=False)

        parse_mock.assert_called_once()
        run_5_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_revision_intent_none_emits_system_unavailable_not_blame_user(
        self, orch
    ):
        """#20 改善：_parse_revision_intent 回 None = LLM API 失敗（系統端）→
        narration 該說「系統暫時無法處理」，不該怪 user「我沒看懂」。停在 checkpoint。"""
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=0,
            written_sections=[{
                "section_index": 0, "title": "topic-0", "content": "x",
                "sources_used": [], "confidence_level": "Medium", "chapter_summary": "",
            }],
        )
        orch._parse_revision_intent = AsyncMock(return_value=None)  # API fail

        result = await orch._handle_stage_5_response(
            state, "幫我把第 1 段改得更有條理一點", auto_continue=False
        )

        assert result.stage_status == "checkpoint"
        sent = [
            c.args[0]
            for c in orch.handler.message_sender.send_message.call_args_list
        ]
        narrations = [
            m for m in sent if m.get("message_type") == "live_research_narration"
        ]
        assert any("系統暫時無法處理" in m.get("text", "") for m in narrations), \
            f"expect system-unavailable narration, got {narrations}"
        assert not any("沒看懂" in m.get("text", "") for m in narrations), \
            f"API fail 不該說「我沒看懂」（怪 user），got {narrations}"

    @pytest.mark.asyncio
    async def test_revision_intent_empty_action_still_emits_no_understand(self, orch):
        """真模糊路徑不變：LLM 成功但回 dict 且 action 空 → user 表達模糊 →
        維持「我沒看懂」，停在 checkpoint。"""
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=0,
            written_sections=[{
                "section_index": 0, "title": "topic-0", "content": "x",
                "sources_used": [], "confidence_level": "Medium", "chapter_summary": "",
            }],
        )
        # dict 但 action 空（LLM 成功但判不出意圖）
        orch._parse_revision_intent = AsyncMock(return_value={"reason": "unclear"})

        # 2026-06-11: pin meta-intent 為 substantive（同檔 L1873-1877 紀律：非-shortcut
        # 文案會打 _classify_meta_intent，unit test 必須 mock 不打真 LLM）。本測試標的
        # 是 _parse_revision_intent 回空 action 那層；不 pin 的話「再說啦」會被真 LLM
        # 判 skip → 走 SKIP 釐清分支（by-design），測不到本層。
        with patch(
            "reasoning.live_research.orchestrator._classify_meta_intent",
            new=AsyncMock(return_value="substantive"),
        ):
            result = await orch._handle_stage_5_response(
                state, "嗯這個喔再說啦", auto_continue=False
            )

        assert result.stage_status == "checkpoint"
        sent = [
            c.args[0]
            for c in orch.handler.message_sender.send_message.call_args_list
        ]
        narrations = [
            m for m in sent if m.get("message_type") == "live_research_narration"
        ]
        assert any("沒看懂" in m.get("text", "") for m in narrations), \
            f"expect '我沒看懂' vague narration, got {narrations}"
        assert not any("系統暫時無法處理" in m.get("text", "") for m in narrations), \
            f"真模糊不該說系統錯誤, got {narrations}"

    @pytest.mark.asyncio
    async def test_parse_revision_prompt_covers_relative_deixis(self, mock_handler):
        """O11 contract：_parse_revision_intent 的 prompt 必須把相對指代
        （前面那段/上一段/下一段）正面點名納入 target_index null 紀律，
        不可只靠 low model 反向推導排除。鎖死字串防退化。
        """
        from unittest.mock import AsyncMock, patch
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator

        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=mock_handler, dry_run=False)

        captured = {"prompt": None}

        async def _capture(prompt, schema, **kwargs):
            captured["prompt"] = prompt
            return {"action": "revise_section", "target_index": None,
                    "instruction": "x", "reason": "vague"}

        written = [
            {"section_index": 0, "title": "topic-0"},
            {"section_index": 1, "title": "topic-1"},
        ]
        with patch("core.llm.ask_llm", new=AsyncMock(side_effect=_capture)):
            await orch._parse_revision_intent("前面那段怪怪的", written)

        p = captured["prompt"]
        assert p is not None, "ask_llm 未被呼叫，prompt 沒組出來"
        # 相對指代必須被正面點名（至少這幾個方向詞 + null 指示）
        assert "前面那段" in p
        assert "上一段" in p
        assert "相對指代" in p
        # 區隔：絕對位置序數仍要保留（不可誤刪既有「最後一段」整數行為）
        assert "最後一段" in p
        assert "倒數第二段" in p
        # 近指代既有紀律不可被覆蓋
        assert "近指代" in p


class TestStage5ContinueWritingAction:
    """Task 2.4 — `continue_writing` action triggers re-entry into _run_stage_5."""

    @pytest.fixture
    def mock_handler(self):
        h = MagicMock()
        h.query = "Q"
        h.message_sender = MagicMock()
        h.message_sender.send_message = AsyncMock()
        h.connection_alive_event = MagicMock()
        h.connection_alive_event.is_set = MagicMock(return_value=True)
        h.query_params = {}
        h.site = "all"
        h.final_retrieved_items = []
        h._save_state = AsyncMock()
        h._load_state = AsyncMock(return_value=None)
        return h

    @pytest.fixture
    def orch(self, mock_handler):
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=mock_handler, dry_run=True)

    @pytest.mark.asyncio
    async def test_handle_stage_5_continue_writing_action(self, orch):
        """`action=continue_writing` → re-runs _run_stage_5."""
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=0,
            written_sections=[{
                "section_index": 0, "title": "topic-0",
                "content": "...", "sources_used": [], "confidence_level": "Medium",
            }],
        )
        # Mock LLM intent parsing to return continue_writing
        orch._parse_revision_intent = AsyncMock(
            return_value={"action": "continue_writing", "reason": "user 說繼續"}
        )

        run_5_mock = AsyncMock(side_effect=lambda s: s)
        orch._run_stage_5 = run_5_mock

        result = await orch._handle_stage_5_response(state, "繼續寫", auto_continue=False)

        # _run_stage_5 must be called
        run_5_mock.assert_called_once()


# ════════════════════════════════════════════════════════════════════════════
# Plan 2 Phase 2: Writer source resolution — format_specs.chapters override
# ════════════════════════════════════════════════════════════════════════════


class TestStage5ChapterSourceResolution:
    """Plan 2 Phase 2: _run_stage_5 honors format_specs.chapters override
    when present; otherwise fallback to ContextMap core_topics."""

    @pytest.fixture
    def mock_handler(self):
        h = MagicMock()
        h.query = "Q"
        h.message_sender = MagicMock()
        h.message_sender.send_message = AsyncMock()
        h.connection_alive_event = MagicMock()
        h.connection_alive_event.is_set = MagicMock(return_value=True)
        h.query_params = {}
        h.site = "all"
        h.final_retrieved_items = []
        h._save_state = AsyncMock()
        h._load_state = AsyncMock(return_value=None)
        return h

    @pytest.fixture
    def orch(self, mock_handler):
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=mock_handler, dry_run=True)

    @pytest.mark.asyncio
    async def test_run_stage_5_uses_format_specs_chapters_when_present(self, orch, mock_handler):
        """format_specs.chapters 非空 → writer 跑 chapter 數量段（不是 core_topics 數量）。

        VP-7：single-step flow → 用 _drive_stage_5_to_completion driver 模擬
        user 連續 continue。
        """
        cm = _make_stage_5_context_map(n=7)  # 7 core topics in ContextMap
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
            format_specs={
                "user_specified": "五章學術結構",
                "chapters": [
                    {"name": "前言", "outline": "研究動機"},
                    {"name": "國內案例", "outline": "台灣"},
                    {"name": "國外案例", "outline": "他國"},
                    {"name": "結果與討論", "outline": "綜合分析"},
                    {"name": "結論", "outline": "policy implication"},
                ],
            },
        )

        result = await _drive_stage_5_to_completion(orch, state)

        # Writer 應寫 5 段（chapter override），不是 7 段（core_topics）
        assert len(result.written_sections) == 5
        titles = [s["title"] for s in result.written_sections]
        assert titles == ["前言", "國內案例", "國外案例", "結果與討論", "結論"]
        assert result.last_completed_section_index == 4

        # writer_status emit 應反映 total_sections=5
        emits = [
            c.args[0]
            for c in mock_handler.message_sender.send_message.call_args_list
            if c.args[0].get("message_type") == "live_research_writer_status"
        ]
        started = [e for e in emits if e["status"] == "started"][0]
        assert started["total_sections"] == 5

    @pytest.mark.asyncio
    async def test_run_stage_5_falls_back_to_core_topics_when_no_chapters_override(self, orch, mock_handler):
        """format_specs 沒 chapters 欄位 → fallback core_topics（既有行為不變）。"""
        cm = _make_stage_5_context_map(n=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
            format_specs={"user_specified": "APA 格式"},  # 無 chapters
        )

        result = await _drive_stage_5_to_completion(orch, state)

        # Fallback core_topics → 3 段
        assert len(result.written_sections) == 3
        titles = [s["title"] for s in result.written_sections]
        assert titles == ["topic-0", "topic-1", "topic-2"]

    @pytest.mark.asyncio
    async def test_run_stage_5_falls_back_when_chapters_is_empty_list(self, orch, mock_handler):
        """format_specs.chapters = [] → 空 list 視同沒 override，fallback core_topics。"""
        cm = _make_stage_5_context_map(n=2)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
            format_specs={"chapters": []},
        )

        result = await _drive_stage_5_to_completion(orch, state)
        assert len(result.written_sections) == 2

    def test_resolve_chapter_source_returns_chapter_dicts_when_override_present(self, orch):
        """_resolve_chapter_source helper：override 路徑回傳 chapter dict list + using_override=True。"""
        cm = _make_stage_5_context_map(n=3)
        chapters = [
            {"name": "前言", "outline": "動機"},
            {"name": "結論", "outline": "結語"},
        ]
        format_specs = {"chapters": chapters}
        writer_sections, using_override = orch._resolve_chapter_source(cm, format_specs)
        assert using_override is True
        assert writer_sections == chapters

    def test_resolve_chapter_source_returns_core_topics_when_no_override(self, orch):
        """_resolve_chapter_source helper：沒 override → core_topics list + using_override=False。"""
        cm = _make_stage_5_context_map(n=3)
        writer_sections, using_override = orch._resolve_chapter_source(cm, {})
        assert using_override is False
        assert len(writer_sections) == 3
        # core_topics 是 ContextMapTopic 物件
        assert writer_sections[0].name == "topic-0"

    @pytest.mark.asyncio
    async def test_chapter_override_first_section_gets_all_evidence_ids(self, orch, mock_handler):
        """Plan 2 Phase 3 (Option B-a): chapter override 模式下
        第一章 analyst_citations = union evidence_ids，其餘 chapter = []。
        _run_stage_5 應傳 chapter_index 與 all_evidence_ids 給 _write_section。
        """
        # Build cm with topics having different evidence_ids → union = {1,2,3,4,5}
        cm = ContextMap(
            research_question="Q",
            topics=[
                ContextMapTopic(
                    topic_id="t1", name="topic-1", domain="d",
                    relevance="core", evidence_ids=[1, 2],
                ),
                ContextMapTopic(
                    topic_id="t2", name="topic-2", domain="d",
                    relevance="core", evidence_ids=[3],
                ),
                ContextMapTopic(
                    topic_id="t3", name="topic-3", domain="d",
                    relevance="supporting", evidence_ids=[4, 5],
                ),
            ],
            version=1,
        )
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
            format_specs={
                "chapters": [
                    {"name": "前言", "outline": "動機"},
                    {"name": "案例", "outline": "案例分析"},
                    {"name": "結論", "outline": "policy"},
                ],
            },
        )

        captured: list = []
        from reasoning.schemas_live import LiveWriterSectionOutput

        async def fake_write(context_map, topic, style_features, format_specs, evidence_pool=None, **kw):
            captured.append({
                "name": topic["name"] if isinstance(topic, dict) else topic.name,
                "is_dict": isinstance(topic, dict),
                "chapter_index": kw.get("chapter_index"),
                "all_evidence_ids": kw.get("all_evidence_ids"),
            })
            return (
                LiveWriterSectionOutput(
                    section_title=topic["name"] if isinstance(topic, dict) else topic.name,
                    section_content="...",
                    sources_used=[],
                    confidence_level="Medium",
                    narration="stub",
                ),
                False,
            )

        orch._write_section = fake_write


        await _drive_stage_5_to_completion(orch, state)

        assert len(captured) == 3
        assert all(c["is_dict"] for c in captured)
        assert [c["chapter_index"] for c in captured] == [0, 1, 2]
        # all_evidence_ids 應為 union (sorted)
        assert captured[0]["all_evidence_ids"] == [1, 2, 3, 4, 5]
        assert captured[1]["all_evidence_ids"] == [1, 2, 3, 4, 5]
        assert captured[2]["all_evidence_ids"] == [1, 2, 3, 4, 5]

    @pytest.mark.asyncio
    async def test_write_section_chapter_override_first_index_uses_union_evidence_ids(self, orch):
        """Plan 2 Phase 3 (legacy union-to-first): _write_section chapter override +
        chapter_index=0 → analyst_citations = all_evidence_ids。

        Track A (sprint 2026-05-28) 修正：原本 chapter_index>0 給 analyst_citations=[]
        是 Cluster 1 fabrication 根因之一，現在 C-1 gate 直接攔成 BlockedSection
        (writer 不被呼叫)。本 test 確認:
        - idx=0 仍走 union-to-first (legacy behavior, 沒 book_outline 時)
        - idx>0 + empty analyst_citations → C-1 gate → writer 不被呼叫 + status="blocked_no_evidence"
        """
        cm = ContextMap(
            research_question="Q",
            topics=[
                ContextMapTopic(
                    topic_id="t1", name="t1", domain="d",
                    relevance="core", evidence_ids=[1, 2],
                ),
                ContextMapTopic(
                    topic_id="t2", name="t2", domain="d",
                    relevance="core", evidence_ids=[3],
                ),
            ],
            version=1,
        )
        chapter_a = {"name": "前言", "outline": "intro"}
        chapter_b = {"name": "結論", "outline": "concl"}
        orch.dry_run = False

        captured: list = []
        from reasoning.schemas_live import LiveWriterSectionOutput

        async def fake_compose(**kw):
            captured.append({
                "section_title": kw.get("section_title"),
                "analyst_citations": kw.get("analyst_citations"),
                "is_chapter_override": kw.get("is_chapter_override"),
            })
            return LiveWriterSectionOutput(
                section_title=kw.get("section_title"),
                section_content="...",
                sources_used=[],
                confidence_level="Medium",
                narration="stub",
            )

        with patch("reasoning.agents.writer.WriterAgent") as MockAgent:
            inst = MockAgent.return_value
            inst.compose_section = AsyncMock(side_effect=fake_compose)

            result_a, _ = await orch._write_section(
                context_map=cm, topic=chapter_a, style_features=None,
                format_specs={}, evidence_pool=None,
                chapter_index=0, all_evidence_ids=[1, 2, 3],
            )
            result_b, _ = await orch._write_section(
                context_map=cm, topic=chapter_b, style_features=None,
                format_specs={}, evidence_pool=None,
                chapter_index=1, all_evidence_ids=[1, 2, 3],
            )

        # idx=0: writer 被呼叫, analyst_citations=union
        assert len(captured) == 1
        assert captured[0]["analyst_citations"] == [1, 2, 3]
        assert captured[0]["is_chapter_override"] is True
        # idx>0: Track A C-1 gate 攔住 (empty analyst_citations → BlockedSection)
        assert getattr(result_b, "status", None) == "blocked_no_evidence"
        assert "[本章資料不足]" in result_b.section_content


# =====================================================================
# Plan: lr-user-voice-container-and-4-fixes (Phase 2, Fix B)
# =====================================================================

class TestStage4CitationStyleEnum:
    """Fix B: Stage 4 user 講 APA -> user_voice.citation_style='author_year'。"""

    @pytest.fixture
    def mock_handler(self):
        h = MagicMock()
        h.query = "Q"
        h.message_sender = MagicMock()
        h.message_sender.send_message = AsyncMock()
        h.connection_alive_event = MagicMock()
        h.connection_alive_event.is_set = MagicMock(return_value=True)
        h.query_params = {}
        h.site = "all"
        h.final_retrieved_items = []
        h._save_state = AsyncMock()  # plan: durable boundary persist awaits this
        return h

    @pytest.fixture
    def orchestrator(self, mock_handler):
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=mock_handler)

    @pytest.mark.asyncio
    async def test_apa_message_extracts_author_year(self, orchestrator):
        """TypeAgent: adjust_format + citation_style_extracted='author_year'."""
        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction, Stage4FormatPayload,
        )

        state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")
        orchestrator._classify_stage_4_response = AsyncMock(
            return_value=Stage4Response(
                action=Stage4ResponseAction.adjust_format,
                format_content=Stage4FormatPayload(
                    format_spec_extracted="五章 / 7000 字 / 含表格 / APA",
                    citation_style_extracted="author_year",
                ),
            )
        )
        result = await orchestrator._handle_stage_4_response(
            state, "五章 / 7000 字 / 含表格 / APA", auto_continue=False
        )
        assert result.user_voice.citation_style == "author_year"

    @pytest.mark.asyncio
    async def test_numeric_message_extracts_numeric(self, orchestrator):
        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction, Stage4FormatPayload,
        )

        state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")
        orchestrator._classify_stage_4_response = AsyncMock(
            return_value=Stage4Response(
                action=Stage4ResponseAction.adjust_format,
                format_content=Stage4FormatPayload(
                    format_spec_extracted="用數字編號引用",
                    citation_style_extracted="numeric",
                ),
            )
        )
        result = await orchestrator._handle_stage_4_response(
            state, "用數字編號引用", auto_continue=False
        )
        assert result.user_voice.citation_style == "numeric"

    @pytest.mark.asyncio
    async def test_no_citation_mention_leaves_none(self, orchestrator):
        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction, Stage4FormatPayload,
        )

        state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")
        orchestrator._classify_stage_4_response = AsyncMock(
            return_value=Stage4Response(
                action=Stage4ResponseAction.adjust_format,
                format_content=Stage4FormatPayload(
                    format_spec_extracted="每段 500 字",
                    citation_style_extracted=None,
                ),
            )
        )
        result = await orchestrator._handle_stage_4_response(
            state, "每段 500 字", auto_continue=False
        )
        assert result.user_voice.citation_style is None

    @pytest.mark.asyncio
    async def test_legacy_state_without_citation_field_unchanged(self, orchestrator):
        """adjust_format 沒給 citation_style → user_voice.citation_style 保持 None。"""
        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction, Stage4FormatPayload,
        )

        state = LiveResearchStageState(current_stage=4, stage_status="checkpoint")
        orchestrator._classify_stage_4_response = AsyncMock(
            return_value=Stage4Response(
                action=Stage4ResponseAction.adjust_format,
                format_content=Stage4FormatPayload(format_spec_extracted="每段 500 字"),
            )
        )
        result = await orchestrator._handle_stage_4_response(
            state, "每段 500 字", auto_continue=False
        )
        assert result.user_voice.citation_style is None

    @pytest.mark.asyncio
    async def test_new_structure_request_also_extracts_citation(self, orchestrator):
        """TypeAgent: new_structure_request + format_content citation → 寫入 user_voice。"""
        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction,
            Stage4StructuralPayload, Stage4FormatPayload, ChapterSpec,
        )

        orchestrator._try_stage_4_reframe_entry_typed = AsyncMock(
            side_effect=lambda state, *args, **kwargs: state
        )
        state = LiveResearchStageState(
            current_stage=4,
            stage_status="checkpoint",
            context_map_json='{"research_question":"q","topics":[],"version":1}',
        )
        orchestrator._classify_stage_4_response = AsyncMock(
            return_value=Stage4Response(
                action=Stage4ResponseAction.new_structure_request,
                structural_content=Stage4StructuralPayload(
                    new_chapters=[
                        ChapterSpec(name="第 1"),
                        ChapterSpec(name="第 2"),
                        ChapterSpec(name="第 3"),
                        ChapterSpec(name="第 4"),
                        ChapterSpec(name="第 5"),
                    ],
                ),
                format_content=Stage4FormatPayload(
                    format_spec_extracted="APA 引用",
                    citation_style_extracted="author_year",
                ),
            )
        )
        result = await orchestrator._handle_stage_4_response(
            state, "改成 5 章，用 APA 引用", auto_continue=False
        )
        assert result.user_voice.citation_style == "author_year"


class TestWriteSectionCitationResolution:
    """Fix B: _write_section resolve citation_format 新 chain。

    Precedence: user_voice.citation_style > style_features.citation_format > 'numeric'
    """

    @pytest.fixture
    def mock_handler(self):
        h = MagicMock()
        h.query = "Q"
        h.message_sender = MagicMock()
        h.message_sender.send_message = AsyncMock()
        h.connection_alive_event = MagicMock()
        h.connection_alive_event.is_set = MagicMock(return_value=True)
        h.query_params = {}
        h.site = "all"
        h.final_retrieved_items = []
        h._save_state = AsyncMock()  # plan: durable boundary persist awaits this
        return h

    @pytest.fixture
    def orch(self, mock_handler):
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=mock_handler)

    def _make_inputs(self, citation_format_in_style="numeric"):
        from reasoning.schemas_live import (
            ContextMap, ContextMapTopic, StyleAnalysisOutput, StyleFeature
        )
        topic = ContextMapTopic(
            topic_id="t1", name="X", domain="d",
            relevance="core", evidence_ids=[1],
        )
        cm = ContextMap(research_question="rq", topics=[topic], version=1)
        # StyleAnalysisOutput.features 至少 3 個
        style = StyleAnalysisOutput(
            features=[
                StyleFeature(dimension="d1", observation="o1", instruction="i1"),
                StyleFeature(dimension="d2", observation="o2", instruction="i2"),
                StyleFeature(dimension="d3", observation="o3", instruction="i3"),
            ],
            overall_tone="t",
            citation_format=citation_format_in_style,
        )
        return cm, topic, style

    @staticmethod
    def _fake_compose_factory(captured):
        async def fake_compose_section(**kw):
            captured.update(kw)
            from reasoning.schemas_live import LiveWriterSectionOutput
            return LiveWriterSectionOutput(
                section_title=kw["section_title"],
                section_content="content",
                sources_used=[],
                confidence_level="Medium",
            )
        return fake_compose_section

    @pytest.mark.asyncio
    async def test_user_voice_wins_over_style_features(self, orch):
        cm, topic, style = self._make_inputs(citation_format_in_style="numeric")
        state = LiveResearchStageState()
        state.user_voice.citation_style = "author_year"

        captured = {}
        with patch("reasoning.agents.writer.WriterAgent") as MockWriter:
            instance = MockWriter.return_value
            instance.compose_section = AsyncMock(
                side_effect=self._fake_compose_factory(captured)
            )
            await orch._write_section(
                context_map=cm,
                topic=topic,
                style_features=style,
                format_specs={},
                evidence_pool=None,
                user_voice=state.user_voice,
            )
        assert captured["citation_format"] == "author_year"

    @pytest.mark.asyncio
    async def test_style_features_used_when_user_voice_none(self, orch):
        cm, topic, style = self._make_inputs(citation_format_in_style="footnote")
        state = LiveResearchStageState()

        captured = {}
        with patch("reasoning.agents.writer.WriterAgent") as MockWriter:
            instance = MockWriter.return_value
            instance.compose_section = AsyncMock(
                side_effect=self._fake_compose_factory(captured)
            )
            await orch._write_section(
                context_map=cm,
                topic=topic,
                style_features=style,
                format_specs={},
                evidence_pool=None,
                user_voice=state.user_voice,
            )
        assert captured["citation_format"] == "footnote"

    @pytest.mark.asyncio
    async def test_numeric_default_when_both_none(self, orch):
        cm, topic, _ = self._make_inputs()
        state = LiveResearchStageState()

        captured = {}
        with patch("reasoning.agents.writer.WriterAgent") as MockWriter:
            instance = MockWriter.return_value
            instance.compose_section = AsyncMock(
                side_effect=self._fake_compose_factory(captured)
            )
            await orch._write_section(
                context_map=cm,
                topic=topic,
                style_features=None,
                format_specs={},
                evidence_pool=None,
                user_voice=state.user_voice,
            )
        assert captured["citation_format"] == "numeric"

    @pytest.mark.asyncio
    async def test_no_user_voice_arg_backward_compat(self, orch):
        """既有 caller 沒傳 user_voice 參數 -> fallback style_features 路徑。"""
        cm, topic, style = self._make_inputs(citation_format_in_style="numeric")

        captured = {}
        with patch("reasoning.agents.writer.WriterAgent") as MockWriter:
            instance = MockWriter.return_value
            instance.compose_section = AsyncMock(
                side_effect=self._fake_compose_factory(captured)
            )
            await orch._write_section(
                context_map=cm,
                topic=topic,
                style_features=style,
                format_specs={},
                evidence_pool=None,
            )
        assert captured["citation_format"] == "numeric"


# =====================================================================
# Plan: lr-user-voice-container-and-4-fixes (Phase 4, Fix I-1)
# =====================================================================

class TestStage5RevisionInstructionWriteThrough:
    """Fix I-1: Stage 5 user 講「第 3 段太短」-> state.user_voice.revise_instructions[2]
    accumulate（OQ 2）+ writer 收到 instruction + prior content。
    """

    @pytest.fixture
    def mock_handler(self):
        h = MagicMock()
        h.query = "Q"
        h.message_sender = MagicMock()
        h.message_sender.send_message = AsyncMock()
        h.connection_alive_event = MagicMock()
        h.connection_alive_event.is_set = MagicMock(return_value=True)
        h.query_params = {}
        h.site = "all"
        h.final_retrieved_items = []
        h._save_state = AsyncMock()  # plan: durable boundary persist awaits this
        return h

    @pytest.fixture
    def orchestrator(self, mock_handler):
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=mock_handler)

    @staticmethod
    def _build_state_with_sections(n_sections=4, prior_content="prior 內容..."):
        from reasoning.schemas_live import ContextMap, ContextMapTopic
        topics = [
            ContextMapTopic(
                topic_id=f"t{i}", name=f"段 {i}", domain="d",
                relevance="core", evidence_ids=[1],
            )
            for i in range(n_sections)
        ]
        cm = ContextMap(research_question="rq", topics=topics, version=1)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="checkpoint",
            context_map_json=cm.model_dump_json(),
            last_completed_section_index=n_sections - 1,
        )
        state.written_sections = [
            {
                "section_index": i,
                "title": f"段 {i}",
                "content": prior_content if i == 2 else f"舊內容 {i}",
                "sources_used": [],
                "confidence_level": "Medium",
                "chapter_summary": "",
            }
            for i in range(n_sections)
        ]
        return state

    @pytest.mark.asyncio
    async def test_revise_section_writes_user_voice_and_pipes_instruction(self, orchestrator):
        state = self._build_state_with_sections(prior_content="前一版第 3 段")

        orchestrator._parse_revision_intent = AsyncMock(return_value={
            "action": "revise_section",
            # 新契約：LLM 回 1-based 段號。「第 3 段」→ 3 → 消費端轉 0-based index 2
            "target_index": 3,
            "instruction": "第 3 段太短，請補 IAEA 數據",
            "reason": "user 要求補充",
        })

        captured = {}

        async def fake_write_section(**kw):
            captured.update(kw)
            from reasoning.schemas_live import LiveWriterSectionOutput
            return (
                LiveWriterSectionOutput(
                    section_title="段 2",
                    section_content="new content with IAEA",
                    sources_used=[],
                    confidence_level="Medium",
                ),
                False,
            )

        orchestrator._write_section = AsyncMock(side_effect=fake_write_section)

        # 非-shortcut 文案會打 _classify_meta_intent；斷 key 後 pin substantive 不打真 LLM。
        with patch("reasoning.live_research.orchestrator._classify_meta_intent",
                   new=AsyncMock(return_value="substantive")):
            result = await orchestrator._handle_stage_5_response(
                state, "第 3 段太短，請補 IAEA 數據", auto_continue=False
            )
        # user_voice 寫入 (accumulate list)
        assert result.user_voice.revise_instructions[2] == [
            "第 3 段太短，請補 IAEA 數據"
        ]
        # _write_section 收到 revise_instruction
        assert captured.get("revise_instruction") == "第 3 段太短，請補 IAEA 數據"
        # prior content 也 pipe
        assert captured.get("prior_section_content") == "前一版第 3 段"

    @pytest.mark.asyncio
    async def test_repeated_revise_accumulates_list(self, orchestrator):
        """OQ 2 acceptance：同段 revise 兩次 → list 長度 = 2，最新 instruction 也 pipe 給 writer。"""
        state = self._build_state_with_sections()
        # 先有第一次 instruction
        state.user_voice.revise_instructions[2] = ["太短，補數據"]

        orchestrator._parse_revision_intent = AsyncMock(return_value={
            "action": "revise_section",
            # 新契約：1-based 段號 3 → 0-based index 2（對齊上方預埋的 [2] key）
            "target_index": 3,
            "instruction": "改太長了，刪一半",
            "reason": "user 二次調整",
        })

        captured = {}

        async def fake_write_section(**kw):
            captured.update(kw)
            from reasoning.schemas_live import LiveWriterSectionOutput
            return (
                LiveWriterSectionOutput(
                    section_title="段 2",
                    section_content="trimmed",
                    sources_used=[],
                    confidence_level="Medium",
                ),
                False,
            )

        orchestrator._write_section = AsyncMock(side_effect=fake_write_section)

        # 非-shortcut 文案會打 _classify_meta_intent；斷 key 後 pin substantive 不打真 LLM。
        with patch("reasoning.live_research.orchestrator._classify_meta_intent",
                   new=AsyncMock(return_value="substantive")):
            result = await orchestrator._handle_stage_5_response(
                state, "改太長了，刪一半", auto_continue=False
            )
        # Accumulate: 兩個 instruction 都在
        assert result.user_voice.revise_instructions[2] == [
            "太短，補數據",
            "改太長了，刪一半",
        ]
        # writer 收到當輪 instruction（CEO OQ 2 拍板：prompt 給 LLM 看「全 list」串起來）
        # 我們在 prompt builder 串接，orchestrator 只傳當前 instruction + 累積上下文
        # → 此處 assert orchestrator 至少把當輪 instruction pipe 給 writer。
        assert "改太長了" in captured.get("revise_instruction", "")

    @pytest.mark.asyncio
    async def test_main_run_stage_5_loop_no_revise_instruction(self, orchestrator):
        """主 _run_stage_5 writer loop call _write_section 沒傳 revise_instruction
        → fallback None（first compose path）。
        """
        # _run_stage_5 main loop 在 Phase 2 已被測（既有 TestStage5UserStop / VP-7 等），
        # 此處只確認 first-compose 路徑不會誤吃 revise_instruction。
        # 用直接 invoke _write_section 模擬 main loop call site。
        from reasoning.schemas_live import ContextMap, ContextMapTopic
        cm = ContextMap(
            research_question="rq",
            topics=[ContextMapTopic(
                topic_id="t1", name="X", domain="d", relevance="core",
                evidence_ids=[1],
            )],
            version=1,
        )
        captured = {}

        async def fake_compose_section(**kw):
            captured.update(kw)
            from reasoning.schemas_live import LiveWriterSectionOutput
            return LiveWriterSectionOutput(
                section_title=kw["section_title"],
                section_content="c",
                sources_used=[],
                confidence_level="Medium",
            )

        with patch("reasoning.agents.writer.WriterAgent") as MockWriter:
            instance = MockWriter.return_value
            instance.compose_section = AsyncMock(side_effect=fake_compose_section)
            await orchestrator._write_section(
                context_map=cm,
                topic=cm.topics[0],
                style_features=None,
                format_specs={},
                evidence_pool=None,
            )
        # First compose: revise_instruction 為 None（不出現 `## 段落修改指示` block）
        assert captured.get("revise_instruction") is None
        assert captured.get("prior_section_content") is None


# =====================================================================
# Plan: lr-user-voice-container-and-4-fixes (Phase 5, Fix I-2)
# =====================================================================

class TestStage2UserVoiceCapture:
    """Fix I-2: Stage 2 user feedback 寫進 user_voice.stage2_feedback，
    narration 改誠實版（不撒謊「已記錄」）。

    CEO OQ 1 拍板：narration 是繁體中文 user-friendly + 不撒謊。
    建議文案範例：「謝謝你的建議，我已經把它記下來，寫稿階段會盡量採用。」
    """

    @pytest.fixture
    def mock_handler(self):
        h = MagicMock()
        h.query = "Q"
        h.message_sender = MagicMock()
        h.message_sender.send_message = AsyncMock()
        h.connection_alive_event = MagicMock()
        h.connection_alive_event.is_set = MagicMock(return_value=True)
        h.query_params = {}
        h.site = "all"
        h.final_retrieved_items = []
        h._save_state = AsyncMock()  # plan: durable boundary persist awaits this
        return h

    @pytest.fixture
    def orchestrator(self, mock_handler):
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=mock_handler)

    @pytest.mark.asyncio
    async def test_user_feedback_written_to_state(self, orchestrator):
        state = LiveResearchStageState(current_stage=2, stage_status="checkpoint")
        result = await orchestrator._handle_stage_2_response(
            state, "離岸風電那段資料太少，再多找 Greenpeace 報告", auto_continue=False
        )
        assert len(result.user_voice.stage2_feedback) == 1
        entry = result.user_voice.stage2_feedback[0]
        assert entry["text"] == "離岸風電那段資料太少，再多找 Greenpeace 報告"
        # OQ 3: round 欄位保留 forward-compat，目前固定 "0"
        assert entry["round"] == "0"

    @pytest.mark.asyncio
    async def test_auto_continue_no_feedback(self, orchestrator):
        state = LiveResearchStageState(current_stage=2, stage_status="checkpoint")
        result = await orchestrator._handle_stage_2_response(
            state, "", auto_continue=True
        )
        assert result.user_voice.stage2_feedback == []

    @pytest.mark.asyncio
    async def test_empty_message_no_feedback(self, orchestrator):
        """auto_continue=False 但空訊息 → 視為跳過，不寫 feedback。"""
        state = LiveResearchStageState(current_stage=2, stage_status="checkpoint")
        result = await orchestrator._handle_stage_2_response(
            state, "   ", auto_continue=False
        )
        assert result.user_voice.stage2_feedback == []

    @pytest.mark.asyncio
    async def test_narration_honest_no_lie_about_recording(self, orchestrator):
        """Narration 不可含「已記錄」這種 unverified claim（CLAUDE.md no silent fail）。"""
        emitted = []
        orchestrator._emit_narration = AsyncMock(
            side_effect=lambda msg: emitted.append(msg)
        )
        state = LiveResearchStageState(current_stage=2, stage_status="checkpoint")
        await orchestrator._handle_stage_2_response(
            state, "補 Greenpeace 報告", auto_continue=False
        )
        assert len(emitted) >= 1
        msg = emitted[0]
        # 不能撒謊「已記錄」（前版實作）
        assert "已記錄" not in msg
        # 繁體中文 + user-friendly（CEO OQ 1）：必須含「記」相關字眼 + 不能 leak technical term
        assert "記" in msg  # 記下 / 記錄下來 等
        # 禁用字詞（CEO OQ 1 明確列出）
        for forbidden in ["retrieval", "session", "state"]:
            assert forbidden not in msg.lower(), (
                f"narration 不該 leak technical term: {forbidden}"
            )

    @pytest.mark.asyncio
    async def test_feedback_roundtrip_preserves(self, orchestrator):
        """stage2_feedback 經 to_dict / from_dict 不消失。"""
        state = LiveResearchStageState(current_stage=2, stage_status="checkpoint")
        result = await orchestrator._handle_stage_2_response(
            state, "補 Greenpeace 報告", auto_continue=False
        )
        restored = LiveResearchStageState.from_dict(result.to_dict())
        assert len(restored.user_voice.stage2_feedback) == 1
        assert restored.user_voice.stage2_feedback[0]["text"] == "補 Greenpeace 報告"
        assert restored.user_voice.stage2_feedback[0]["round"] == "0"


# ============================================================================
# Track A (LR DR-parity sprint 2026-05-28) — Task 3:
# replace relevant_findings="" + C-1 deterministic gate + _is_intro_or_conclusion
# ============================================================================


class TestTrackAIsIntroOrConclusionHelper:
    """Gemini Critical 紅隊 #2 runtime double-check: _is_intro_or_conclusion(
    book_outline=None) 必回 False (codex C-1 v2 保守紀律), 不可 bypass gate。"""

    def test_is_intro_or_conclusion_none_outline_returns_false(self):
        from reasoning.live_research.orchestrator import _is_intro_or_conclusion
        assert _is_intro_or_conclusion(None, 0) is False
        assert _is_intro_or_conclusion(None, 1) is False
        assert _is_intro_or_conclusion(None, 99) is False

    def test_is_intro_or_conclusion_accepts_legit_intro_and_conclusion(self):
        from reasoning.schemas_live import BookOutline, ChapterPlan
        from reasoning.live_research.orchestrator import _is_intro_or_conclusion
        outline = BookOutline(
            chapters=[
                ChapterPlan(chapter_index=0, title="前言", brief="x",
                            planned_evidence_ids=[1], role="intro"),
                ChapterPlan(chapter_index=1, title="本章", brief="y",
                            planned_evidence_ids=[2], role="body"),
                ChapterPlan(chapter_index=2, title="結論", brief="z",
                            planned_evidence_ids=[3], role="conclusion"),
            ],
            overall_arc="x", redundancy_warnings=[],
        )
        assert _is_intro_or_conclusion(outline, 0) is True
        assert _is_intro_or_conclusion(outline, 1) is False
        assert _is_intro_or_conclusion(outline, 2) is True
        assert _is_intro_or_conclusion(outline, 99) is False  # 越界 → False

    def test_is_intro_or_conclusion_rejects_hallucinated_role_at_wrong_index(self):
        """Gemini Critical 紅隊 #2: LLM 把 body 章節標 intro 想繞 gate
        → runtime helper 必回 False (不信 role)。"""
        from reasoning.schemas_live import BookOutline, ChapterPlan
        from reasoning.live_research.orchestrator import _is_intro_or_conclusion
        # 用 model_construct 繞 schema validator 模擬 hallucinated outline
        bad_outline = BookOutline.model_construct(
            chapters=[
                ChapterPlan.model_construct(
                    chapter_index=0, title="前言", brief="x",
                    planned_evidence_ids=[1], role="intro",
                    target_word_count=0, transition_hint="",
                ),
                ChapterPlan.model_construct(
                    chapter_index=1, title="本章", brief="y",
                    planned_evidence_ids=[2], role="body",
                    target_word_count=0, transition_hint="",
                ),
                ChapterPlan.model_construct(
                    chapter_index=2, title="hallucinated intro",
                    brief="z", planned_evidence_ids=[], role="intro",
                    target_word_count=0, transition_hint="",
                ),
            ],
            overall_arc="x", redundancy_warnings=[],
        )
        assert _is_intro_or_conclusion(bad_outline, 2) is False
        assert _is_intro_or_conclusion(bad_outline, 0) is True   # 正當 intro
        assert _is_intro_or_conclusion(bad_outline, 1) is False  # 正當 body


class TestTrackAWriteSectionGroundedFindings:
    """Track A Task 3: _write_section 真實 render per-chapter findings
    替換 hardcoded relevant_findings="" + C-1 deterministic gate (BlockedSection)。"""

    @pytest.fixture
    def t3_handler(self):
        handler = MagicMock()
        handler.query_params = {}
        handler.site = "all"
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        return handler

    @pytest.mark.asyncio
    async def test_write_section_passes_grounded_findings_for_chapter_override(
        self, t3_handler, monkeypatch
    ):
        """chapter override 路徑: relevant_findings 不再空, 而是
        render_grounded_narrative(該章 planned_evidence_ids)。"""
        from reasoning.schemas_live import (
            ContextMap, ContextMapTopic, BookOutline, ChapterPlan,
            EvidencePoolEntry, GroundedClaim, LiveWriterSectionOutput,
        )

        cm = ContextMap(
            research_question="q", version=0,
            topics=[ContextMapTopic(topic_id="t1", name="n", domain="d",
                                    relevance="core", description="d",
                                    evidence_ids=[1, 2])],
        )
        state = LiveResearchStageState()
        state.evidence_usage = {
            2: [GroundedClaim(claim="UniqueGroundedClaim",
                              reasoning_type="induction",
                              confidence="high", source_topic="t1",
                              source_iteration=1).model_dump()],
        }
        evidence_pool = {
            1: EvidencePoolEntry(evidence_id=1, title="T1", url="u", snippet="s1"),
            2: EvidencePoolEntry(evidence_id=2, title="T2", url="u", snippet="s2"),
        }
        book_outline = BookOutline(
            chapters=[
                ChapterPlan(chapter_index=0, title="前言", brief="x",
                            planned_evidence_ids=[1], role="intro"),
                ChapterPlan(chapter_index=1, title="本章", brief="y",
                            planned_evidence_ids=[2], role="body"),
            ],
            overall_arc="x", redundancy_warnings=[],
        )

        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=t3_handler)
        orch.dry_run = False

        captured = {}

        async def fake_compose_section(self, **kw):
            captured.update(kw)
            return LiveWriterSectionOutput(
                section_title=kw["section_title"],
                section_content="content",
                sources_used=[2],
                confidence_level="Medium",
                narration="x",
                chapter_summary="s",
            )

        monkeypatch.setattr(
            "reasoning.agents.writer.WriterAgent.compose_section",
            fake_compose_section,
        )

        await orch._write_section(
            context_map=cm,
            topic={"name": "本章", "outline": "y"},
            style_features=None,
            format_specs={},
            evidence_pool=evidence_pool,
            chapter_index=1,
            all_evidence_ids=[1, 2],
            book_outline=book_outline,
            current_chapter_index=1,
            state=state,
        )

        # relevant_findings 必含 UniqueGroundedClaim (不再空字串)
        assert "UniqueGroundedClaim" in captured["relevant_findings"]
        # analyst_citations = [2] (per-chapter planned_evidence_ids)
        assert captured["analyst_citations"] == [2]

    @pytest.mark.asyncio
    async def test_write_section_body_chapter_empty_planned_but_pool_nonempty_calls_writer(
        self, t3_handler, monkeypatch
    ):
        """P2 W10（C-1 根治）：body chapter planned 空但 evidence_pool 非空（有 title/snippet）
        → 入口 gate 只擋 pool 真空，此處 pool 有料 → writer 仍被呼叫（讀全 pool），不擋。
        （原 addendum C-1 在 planned 空即 block 是誤判，全局模型下根因移除。）"""
        from reasoning.schemas_live import (
            ContextMap, ContextMapTopic, BookOutline, ChapterPlan,
            EvidencePoolEntry, LiveWriterSectionOutput,
        )

        cm = ContextMap(
            research_question="q", version=0,
            topics=[ContextMapTopic(topic_id="t", name="n", domain="d",
                                    relevance="core", description="d",
                                    evidence_ids=[1])],
        )
        state = LiveResearchStageState()
        state.evidence_usage = {}
        pool = {1: EvidencePoolEntry(evidence_id=1, title="T", url="u", snippet="s")}
        book_outline = BookOutline(chapters=[
            ChapterPlan(chapter_index=0, title="前言", brief="x",
                        planned_evidence_ids=[1], role="intro"),
            ChapterPlan(chapter_index=1, title="本章 body", brief="y",
                        planned_evidence_ids=[],  # 空 planned，但 pool 有料
                        role="body"),
        ], overall_arc="x", redundancy_warnings=[])

        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=t3_handler)
        orch.dry_run = False

        writer_called = {"n": 0}

        async def fake_compose(self, **kw):
            writer_called["n"] += 1
            return LiveWriterSectionOutput(
                section_title=kw["section_title"], section_content="c",
                sources_used=[1], confidence_level="Medium",
                narration="x", chapter_summary="s",
            )

        monkeypatch.setattr(
            "reasoning.agents.writer.WriterAgent.compose_section", fake_compose
        )

        result, was_corrected = await orch._write_section(
            context_map=cm, topic={"name": "本章 body", "outline": "y"},
            style_features=None, format_specs={}, evidence_pool=pool,
            chapter_index=1, all_evidence_ids=[1],
            book_outline=book_outline, current_chapter_index=1,
            state=state,
        )

        assert writer_called["n"] == 1  # pool 有料 → writer 被呼叫（不擋）
        assert getattr(result, "status", None) != "blocked_no_evidence"

    @pytest.mark.asyncio
    async def test_write_section_body_chapter_pool_truly_empty_returns_blocked(
        self, t3_handler, monkeypatch
    ):
        """P2 W10：body chapter pool 完全空 → 入口 gate 擋（真零 evidence，明確擋）。"""
        from reasoning.schemas_live import (
            ContextMap, ContextMapTopic, BookOutline, ChapterPlan,
        )

        cm = ContextMap(
            research_question="q", version=0,
            topics=[ContextMapTopic(topic_id="t", name="n", domain="d",
                                    relevance="core", description="d",
                                    evidence_ids=[])],
        )
        state = LiveResearchStageState()
        state.evidence_usage = {}
        book_outline = BookOutline(chapters=[
            ChapterPlan(chapter_index=0, title="前言", brief="x",
                        planned_evidence_ids=[], role="intro"),
            ChapterPlan(chapter_index=1, title="本章 body", brief="y",
                        planned_evidence_ids=[], role="body"),
        ], overall_arc="x", redundancy_warnings=[])

        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=t3_handler)
        orch.dry_run = False

        writer_called = {"n": 0}

        async def fake_compose(self, **kw):
            writer_called["n"] += 1
            raise AssertionError("writer must NOT be called when pool truly empty")

        monkeypatch.setattr(
            "reasoning.agents.writer.WriterAgent.compose_section", fake_compose
        )

        result, _ = await orch._write_section(
            context_map=cm, topic={"name": "本章 body", "outline": "y"},
            style_features=None, format_specs={}, evidence_pool={},
            chapter_index=1, all_evidence_ids=[],
            book_outline=book_outline, current_chapter_index=1,
            state=state,
        )

        assert writer_called["n"] == 0  # pool 真空 → writer 不被呼叫
        assert getattr(result, "status", None) == "blocked_no_evidence"
        assert "[本章資料不足]" in result.section_content

    @pytest.mark.asyncio
    async def test_write_section_intro_chapter_empty_evidence_still_calls_writer(
        self, t3_handler, monkeypatch
    ):
        """intro chapter 即使 chapter_eids 空也允許呼叫 writer (走資料不足 narration
        由 prompt 紀律處理，不走 deterministic gate)。"""
        from reasoning.schemas_live import (
            ContextMap, ContextMapTopic, BookOutline, ChapterPlan,
            EvidencePoolEntry, LiveWriterSectionOutput,
        )

        cm = ContextMap(
            research_question="q", version=0,
            topics=[ContextMapTopic(topic_id="t", name="n", domain="d",
                                    relevance="core", description="d",
                                    evidence_ids=[1])],
        )
        state = LiveResearchStageState()
        pool = {1: EvidencePoolEntry(evidence_id=1, title="T", url="u", snippet="s")}
        book_outline = BookOutline(chapters=[
            ChapterPlan(chapter_index=0, title="前言", brief="x",
                        planned_evidence_ids=[], role="intro"),
        ], overall_arc="x", redundancy_warnings=[])

        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=t3_handler)
        orch.dry_run = False

        writer_called = {"n": 0}

        async def fake_compose(self, **kw):
            writer_called["n"] += 1
            return LiveWriterSectionOutput(
                section_title="前言", section_content="背景概述",
                sources_used=[], confidence_level="Low",
            )

        monkeypatch.setattr(
            "reasoning.agents.writer.WriterAgent.compose_section", fake_compose
        )

        await orch._write_section(
            context_map=cm, topic={"name": "前言", "outline": "x"},
            style_features=None, format_specs={}, evidence_pool=pool,
            chapter_index=0, all_evidence_ids=[1],
            book_outline=book_outline, current_chapter_index=0,
            state=state,
        )
        assert writer_called["n"] == 1  # intro 允許呼叫 writer

    @pytest.mark.asyncio
    async def test_extract_entities_from_section_dedupes_and_handles_failure(
        self, monkeypatch
    ):
        """Track A Task 7: _extract_entities_from_section LLM failure → 空 list (不阻塞)。"""
        from reasoning.live_research.orchestrator import (
            _extract_entities_from_section,
        )

        async def fake_ask_llm(prompt, schema, **kwargs):
            # 含重複的 "丹麥" 驗證 dedupe
            return {"entities": ["丹麥", "台電", "丹麥", "  ", ""]}

        monkeypatch.setattr("core.llm.ask_llm", fake_ask_llm)
        ents = await _extract_entities_from_section(
            "丹麥的綠能與台電", MagicMock(query_params={})
        )
        # dedupe + 忽略空 string
        assert ents == ["丹麥", "台電"]

        # LLM failure → 空 list
        async def fake_fail(prompt, schema, **kwargs):
            raise RuntimeError("LLM timeout")

        monkeypatch.setattr("core.llm.ask_llm", fake_fail)
        ents_fail = await _extract_entities_from_section(
            "x", MagicMock(query_params={})
        )
        assert ents_fail == []

    def test_section_dict_helper_emits_all_required_keys(self):
        """Track A Task 7 addendum I-1: _section_dict 統一構造 (含 entities + status)。"""
        from reasoning.live_research.orchestrator import _section_dict
        from reasoning.schemas_live import LiveWriterSectionOutput
        out = LiveWriterSectionOutput(
            section_title="前言", section_content="content",
            sources_used=[1, 2], confidence_level="High",
            chapter_summary="50 字摘要", status="drafted",
        )
        d = _section_dict(out, section_index=0, entities=["丹麥"])
        assert d["section_index"] == 0
        assert d["title"] == "前言"
        assert d["content"] == "content"
        assert d["sources_used"] == [1, 2]
        assert d["confidence_level"] == "High"
        assert d["chapter_summary"] == "50 字摘要"
        assert d["entities"] == ["丹麥"]
        assert d["status"] == "drafted"

    def test_section_dict_helper_propagates_guard_failed_status(self):
        """guard_failed status 必須持久進 written_sections[i]['status'] (Stage 6 偵測用)。"""
        from reasoning.live_research.orchestrator import _section_dict
        from reasoning.schemas_live import LiveWriterSectionOutput
        out = LiveWriterSectionOutput(
            section_title="本章", section_content="[本章內容無法驗證] ...",
            sources_used=[], confidence_level="Low",
            status="guard_failed",
        )
        d = _section_dict(out, section_index=2, entities=[])
        assert d["status"] == "guard_failed"
        assert d["entities"] == []

    def test_written_section_entities_backward_compat_old_row_missing_key(self):
        """addendum I-1 + backward compat: 舊 written_sections 無 'entities' key →
        consumer 用 .get fallback (沒此 key 不炸)。"""
        from reasoning.live_research.stage_state import LiveResearchStageState
        s = LiveResearchStageState()
        s.written_sections = [{"section_index": 0, "title": "x", "content": "y"}]
        # consumer 必用 .get("entities", []) — 模擬 consumer 邏輯
        entities = s.written_sections[0].get("entities", [])
        assert entities == []

    @pytest.mark.asyncio
    async def test_write_section_passes_prior_used_entities_to_writer(
        self, t3_handler, monkeypatch
    ):
        """Track A Task 7: _write_section 接到 prior_used_entities 參數 → 傳給
        writer.compose_section。"""
        from reasoning.schemas_live import (
            ContextMap, ContextMapTopic, BookOutline, ChapterPlan,
            EvidencePoolEntry, GroundedClaim, LiveWriterSectionOutput,
        )

        cm = ContextMap(
            research_question="q", version=0,
            topics=[ContextMapTopic(topic_id="t", name="n", domain="d",
                                    relevance="core", description="d",
                                    evidence_ids=[1])],
        )
        state = LiveResearchStageState()
        state.evidence_usage = {
            1: [GroundedClaim(
                claim="c", reasoning_type="induction", confidence="high",
                source_topic="t", source_iteration=1,
            ).model_dump()]
        }
        pool = {1: EvidencePoolEntry(evidence_id=1, title="T", url="u", snippet="s")}
        book_outline = BookOutline(chapters=[
            ChapterPlan(chapter_index=0, title="前言", brief="x",
                        planned_evidence_ids=[1], role="intro"),
            ChapterPlan(chapter_index=1, title="結論", brief="綜合",
                        planned_evidence_ids=[1], role="conclusion"),
        ], overall_arc="x", redundancy_warnings=[])

        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=t3_handler)
        orch.dry_run = False

        captured = {}

        async def fake_compose(self, **kw):
            captured.update(kw)
            return LiveWriterSectionOutput(
                section_title=kw["section_title"],
                section_content="x", sources_used=[1],
                confidence_level="Medium",
            )

        monkeypatch.setattr(
            "reasoning.agents.writer.WriterAgent.compose_section", fake_compose
        )

        await orch._write_section(
            context_map=cm, topic={"name": "結論", "outline": "綜合"},
            style_features=None, format_specs={}, evidence_pool=pool,
            chapter_index=1, all_evidence_ids=[1],
            book_outline=book_outline, current_chapter_index=1,
            state=state,
            prior_used_entities=["丹麥", "台電"],
        )

        # writer 接到 prior_used_entities
        assert captured.get("prior_used_entities") == ["丹麥", "台電"]

    @pytest.mark.asyncio
    async def test_write_section_narrative_empty_but_pool_snippet_calls_writer(
        self, t3_handler, monkeypatch
    ):
        """P2 W10（R1）：narrative 空（evidence_usage 無對應 claim）但 evidence_pool 有
        title/snippet → writer_evidence_view 非空 → writer 仍被呼叫（用 snippet 寫），不擋。
        （raw pool 有料只是還沒 grounded claim，不該因 narrative 空就擋。）"""
        from reasoning.schemas_live import (
            ContextMap, ContextMapTopic, BookOutline, ChapterPlan,
            EvidencePoolEntry, LiveWriterSectionOutput,
        )

        cm = ContextMap(
            research_question="q", version=0,
            topics=[ContextMapTopic(topic_id="t", name="n", domain="d",
                                    relevance="core", description="d",
                                    evidence_ids=[2])],
        )
        state = LiveResearchStageState()
        state.evidence_usage = {}  # 無 claim → narrative 空，但 pool 有 snippet
        pool = {2: EvidencePoolEntry(evidence_id=2, title="T2", url="u", snippet="s2")}
        book_outline = BookOutline(chapters=[
            ChapterPlan(chapter_index=0, title="前言", brief="x",
                        planned_evidence_ids=[2], role="intro"),
            ChapterPlan(chapter_index=1, title="本章 body", brief="y",
                        planned_evidence_ids=[2], role="body"),
        ], overall_arc="x", redundancy_warnings=[])

        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=t3_handler)
        orch.dry_run = False

        writer_called = {"n": 0}

        async def fake_compose(self, **kw):
            writer_called["n"] += 1
            return LiveWriterSectionOutput(
                section_title=kw["section_title"], section_content="c",
                sources_used=[2], confidence_level="Medium",
                narration="x", chapter_summary="s",
            )

        monkeypatch.setattr(
            "reasoning.agents.writer.WriterAgent.compose_section", fake_compose
        )

        result, _ = await orch._write_section(
            context_map=cm, topic={"name": "本章 body", "outline": "y"},
            style_features=None, format_specs={}, evidence_pool=pool,
            chapter_index=1, all_evidence_ids=[2],
            book_outline=book_outline, current_chapter_index=1,
            state=state,
        )

        assert writer_called["n"] == 1  # pool 有 snippet → writer 被呼叫
        assert getattr(result, "status", None) != "blocked_no_evidence"

    @pytest.mark.asyncio
    async def test_write_section_pool_entries_empty_returns_blocked_section(
        self, t3_handler, monkeypatch
    ):
        """P2 W10：pool 有 key 但 entry 無 title/snippet 且無 claim →
        writer_evidence_view + narrative 都實質空 → post-render gate 擋（真沒料）。"""
        from reasoning.schemas_live import (
            ContextMap, ContextMapTopic, BookOutline, ChapterPlan,
            EvidencePoolEntry,
        )

        cm = ContextMap(
            research_question="q", version=0,
            topics=[ContextMapTopic(topic_id="t", name="n", domain="d",
                                    relevance="core", description="d",
                                    evidence_ids=[2])],
        )
        state = LiveResearchStageState()
        state.evidence_usage = {}
        # entry 無 title/snippet → view 跳過該 entry → view 空；narrative 也空
        pool = {2: EvidencePoolEntry(evidence_id=2, title="", url="", snippet="")}
        book_outline = BookOutline(chapters=[
            ChapterPlan(chapter_index=0, title="前言", brief="x",
                        planned_evidence_ids=[2], role="intro"),
            ChapterPlan(chapter_index=1, title="本章 body", brief="y",
                        planned_evidence_ids=[2], role="body"),
        ], overall_arc="x", redundancy_warnings=[])

        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=t3_handler)
        orch.dry_run = False

        writer_called = {"n": 0}

        async def fake_compose(self, **kw):
            writer_called["n"] += 1
            raise AssertionError("writer must NOT be called when truly empty")

        monkeypatch.setattr(
            "reasoning.agents.writer.WriterAgent.compose_section", fake_compose
        )

        result, _ = await orch._write_section(
            context_map=cm, topic={"name": "本章 body", "outline": "y"},
            style_features=None, format_specs={}, evidence_pool=pool,
            chapter_index=1, all_evidence_ids=[2],
            book_outline=book_outline, current_chapter_index=1,
            state=state,
        )

        assert writer_called["n"] == 0  # 真沒料 → writer 不被呼叫
        assert getattr(result, "status", None) == "blocked_no_evidence"


class TestCayenneSpecificityAndCrossSection:
    """Cayenne A specificity gate（body 章太抽象→rewrite）+ B(a) synthesis 章新-entity
    兜底（冒前文沒有的新具體 entity→rewrite）。兩者同在 entity-guard try block 內，互斥觸發。
    """

    @pytest.fixture
    def handler(self):
        h = MagicMock()
        h.query_params = {}
        h.site = "all"
        h.message_sender = MagicMock()
        h.message_sender.send_message = AsyncMock()
        return h

    def _make_body_setup(self):
        """body 章（index 1）+ 具體 evidence（含數字/書名號）setup。
        模塊5 Task 5：body 章配 3 筆 evidence（> EVIDENCE_THIN_CHAPTER_CITATIONS=2），
        使 chapter_sufficiency=='ok' → specificity guard 對此章正常生效（specificity
        只對充足章運作；薄弱章已改走 calibration skip）。此 setup 即代表「充足章」。"""
        from reasoning.schemas_live import (
            ContextMap, ContextMapTopic, BookOutline, ChapterPlan,
            EvidencePoolEntry, GroundedClaim,
        )
        cm = ContextMap(
            research_question="q", version=0,
            topics=[ContextMapTopic(topic_id="t", name="n", domain="d",
                                    relevance="core", description="d",
                                    evidence_ids=[1, 2, 3])],
        )
        state = LiveResearchStageState()
        state.evidence_usage = {
            1: [GroundedClaim(
                claim="德國北萊茵回饋金每年 2 萬歐元", reasoning_type="induction",
                confidence="high", source_topic="t", source_iteration=1,
            ).model_dump()]
        }
        # snippet 含數字 + 書名號 → evidence_has_concrete=True
        pool = {
            1: EvidencePoolEntry(
                evidence_id=1, title="德國北萊茵案例", url="u",
                snippet="回饋金每年 2 萬歐元，依《再生能源法》第 6 條",
            ),
            2: EvidencePoolEntry(evidence_id=2, title="案例二", url="u", snippet="s2"),
            3: EvidencePoolEntry(evidence_id=3, title="案例三", url="u", snippet="s3"),
        }
        book_outline = BookOutline(chapters=[
            ChapterPlan(chapter_index=0, title="前言", brief="x",
                        planned_evidence_ids=[1], role="intro"),
            ChapterPlan(chapter_index=1, title="國外案例", brief="國際案例分析",
                        planned_evidence_ids=[1, 2, 3], role="body"),  # 3 筆 → ok
        ], overall_arc="x", redundancy_warnings=[])
        return cm, state, pool, book_outline

    @pytest.mark.asyncio
    async def test_write_section_specificity_triggers_rewrite(self, handler, monkeypatch):
        """body chapter evidence 有具體資訊但 prose 全抽象 → specificity_check flag →
        auto-rewrite 一次（compose 被叫 2 次）。"""
        from reasoning.schemas_live import LiveWriterSectionOutput
        cm, state, pool, book_outline = self._make_body_setup()

        compose_calls = {"n": 0}

        async def fake_compose(self, **kw):
            compose_calls["n"] += 1
            if compose_calls["n"] == 1:
                content = "綜合分析顯示溝通很重要" * 30  # 抽象、>200 字
            else:
                content = "德國北萊茵回饋金每年 2 萬歐元" * 30  # 具體
            return LiveWriterSectionOutput(
                section_title=kw["section_title"], section_content=content,
                sources_used=[1], confidence_level="High", status="drafted",
            )

        # entity 抽取：抽象內容回 []，具體內容回 entity
        async def fake_extract(content, _handler):
            return [] if "綜合分析" in content else ["德國北萊茵", "2 萬歐元"]

        async def fake_entity_check(section, chapter_evidence_text, handler, **kw):
            return []  # 無 ungrounded，讓流程走到 specificity

        monkeypatch.setattr(
            "reasoning.agents.writer.WriterAgent.compose_section", fake_compose
        )
        monkeypatch.setattr(
            "reasoning.live_research.hallucination_guard.entity_grounding_check",
            fake_entity_check,
        )

        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=handler)
        orch.dry_run = False
        orch._extract_section_entities = fake_extract

        section_output, _ = await orch._write_section(
            context_map=cm, topic={"name": "國外案例", "outline": "國際案例分析"},
            style_features=None, format_specs={}, evidence_pool=pool,
            chapter_index=1, all_evidence_ids=[1],
            book_outline=book_outline, current_chapter_index=1,
            state=state,
        )

        assert compose_calls["n"] == 2  # 原寫 1 + specificity rewrite 1
        assert "德國北萊茵" in section_output.section_content

    @pytest.mark.asyncio
    async def test_specificity_rewrite_introducing_ungrounded_entity_blocked(
        self, handler, monkeypatch
    ):
        """specificity rewrite 後 entity_grounding_check 回非空 → Fix2 partial block。
        本例 content 為單一純未驗證句（無句尾標點 → 整段一句）→ 硬刪後過短 → 退化路徑 (a)：
        正文保留、confidence Low、status 維持 drafted（CEO 決策④：丟掉整章替換 (c)）。"""
        from reasoning.schemas_live import LiveWriterSectionOutput
        cm, state, pool, book_outline = self._make_body_setup()

        compose_calls = {"n": 0}

        async def fake_compose(self, **kw):
            compose_calls["n"] += 1
            content = ("綜合分析顯示溝通很重要" if compose_calls["n"] == 1
                       else "捏造的火星風場案例") * 30
            return LiveWriterSectionOutput(
                section_title=kw["section_title"], section_content=content,
                sources_used=[1], confidence_level="High", status="drafted",
            )

        async def fake_extract(content, _handler):
            return [] if "綜合分析" in content else ["火星風場"]

        # 第一次 entity check（rewrite 前）回 []；rewrite 後回 ungrounded
        check_calls = {"n": 0}

        async def fake_entity_check(section, chapter_evidence_text, handler, **kw):
            check_calls["n"] += 1
            return [] if check_calls["n"] == 1 else ["火星風場"]

        monkeypatch.setattr(
            "reasoning.agents.writer.WriterAgent.compose_section", fake_compose
        )
        monkeypatch.setattr(
            "reasoning.live_research.hallucination_guard.entity_grounding_check",
            fake_entity_check,
        )

        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=handler)
        orch.dry_run = False
        orch._extract_section_entities = fake_extract

        section_output, _ = await orch._write_section(
            context_map=cm, topic={"name": "國外案例", "outline": "x"},
            style_features=None, format_specs={}, evidence_pool=pool,
            chapter_index=1, all_evidence_ids=[1],
            book_outline=book_outline, current_chapter_index=1,
            state=state,
        )

        # CEO 決策④：丟掉整章替換 (c)。退化路徑 (a)：正文保留、降 Low、status 非 guard_failed。
        assert getattr(section_output, "status", "drafted") != "guard_failed"
        assert "[本章內容無法驗證]" not in section_output.section_content
        assert section_output.confidence_level == "Low"
        assert section_output.methodology_note  # 有降級註記

    @pytest.mark.asyncio
    async def test_specificity_rewrite_partial_block(self, handler, monkeypatch):
        """specificity rewrite 後剩 1 ungrounded（純未驗證句，content 夠長）→ 主路徑 (b)
        sentence-level partial：移除該句、保留其餘有據句，不整章替換。"""
        from reasoning.schemas_live import LiveWriterSectionOutput
        cm, state, pool, book_outline = self._make_body_setup()

        compose_calls = {"n": 0}

        async def fake_compose(self, **kw):
            compose_calls["n"] += 1
            if compose_calls["n"] == 1:
                content = "綜合分析顯示溝通很重要" * 30  # 抽象 → 觸發 specificity rewrite
            else:
                # rewrite 後：多句，其中一句純未驗證（火星風場），其餘有據且夠長
                # （刪 1 句後 kept 仍 >150 字，走主路徑 (b) 而非退化）。
                content = (
                    "德國北萊茵的回饋金制度每年提供穩定收益，案例顯示在地居民"
                    "對於再生能源開發的接受度，與資訊揭露程度、社區參與機制以及"
                    "利害關係人會議的透明度高度相關，這也是後續政策推動時必須"
                    "審慎處理的關鍵環節與制度設計重點，值得各地方政府參考借鏡。"
                    "捏造的火星風場案例顯示完全不存在的境外開發經驗。"
                    "整體而言，完善的雙向溝通機制與公開透明的決策流程，被絕大"
                    "多數受訪的在地利害關係人，視為化解能源開發爭議、凝聚地方"
                    "社區共識與重建彼此信任關係的核心基礎與必要前提條件。"
                )
            return LiveWriterSectionOutput(
                section_title=kw["section_title"], section_content=content,
                sources_used=[1], confidence_level="High", status="drafted",
            )

        async def fake_extract(content, _handler):
            return [] if "綜合分析" in content else ["火星風場", "德國北萊茵"]

        check_calls = {"n": 0}

        async def fake_entity_check(section, chapter_evidence_text, handler, **kw):
            check_calls["n"] += 1
            return [] if check_calls["n"] == 1 else ["火星風場"]

        monkeypatch.setattr(
            "reasoning.agents.writer.WriterAgent.compose_section", fake_compose
        )
        monkeypatch.setattr(
            "reasoning.live_research.hallucination_guard.entity_grounding_check",
            fake_entity_check,
        )

        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=handler)
        orch.dry_run = False
        orch._extract_section_entities = fake_extract

        section_output, _ = await orch._write_section(
            context_map=cm, topic={"name": "國外案例", "outline": "x"},
            style_features=None, format_specs={}, evidence_pool=pool,
            chapter_index=1, all_evidence_ids=[1],
            book_outline=book_outline, current_chapter_index=1,
            state=state,
            prior_used_entities=["德國北萊茵"],
        )

        # 主路徑 (b)：移除火星風場句、保留有據句，不整章替換、非 guard_failed。
        assert "火星風場" not in section_output.section_content
        assert "德國北萊茵的回饋金制度" in section_output.section_content
        assert "[本章內容無法驗證]" not in section_output.section_content
        assert getattr(section_output, "status", "drafted") != "guard_failed"

    def _make_synth_setup(self):
        """synthesis 章（index 1, role=conclusion）+ 具體 evidence setup。"""
        from reasoning.schemas_live import (
            ContextMap, ContextMapTopic, BookOutline, ChapterPlan,
            EvidencePoolEntry, GroundedClaim,
        )
        cm = ContextMap(
            research_question="q", version=0,
            topics=[ContextMapTopic(topic_id="t", name="n", domain="d",
                                    relevance="core", description="d",
                                    evidence_ids=[1])],
        )
        state = LiveResearchStageState()
        state.evidence_usage = {
            1: [GroundedClaim(
                claim="苗栗、德國北萊茵的回饋金機制", reasoning_type="induction",
                confidence="high", source_topic="t", source_iteration=1,
            ).model_dump()]
        }
        pool = {1: EvidencePoolEntry(
            evidence_id=1, title="案例彙整", url="u",
            snippet="苗栗案場回饋金、德國北萊茵合作社模式",
        )}
        book_outline = BookOutline(chapters=[
            ChapterPlan(chapter_index=0, title="前言", brief="x",
                        planned_evidence_ids=[1], role="intro"),
            ChapterPlan(chapter_index=1, title="結果與討論", brief="綜合討論",
                        planned_evidence_ids=[1], role="conclusion"),
        ], overall_arc="x", redundancy_warnings=[])
        return cm, state, pool, book_outline

    @pytest.mark.asyncio
    async def test_synthesis_chapter_new_entity_triggers_rewrite(self, handler, monkeypatch):
        """synthesis 章 prose 抽出「不在前章聯集」的新具體 entity → auto-rewrite 一次，
        rewrite kw 含 ungrounded_entities_revision=[新entity]。"""
        from reasoning.schemas_live import LiveWriterSectionOutput
        cm, state, pool, book_outline = self._make_synth_setup()

        compose_calls = {"n": 0, "last_kw": None}

        async def fake_compose(self, **kw):
            compose_calls["n"] += 1
            compose_calls["last_kw"] = kw
            content = ("苗栗、德國北萊茵的回饋金機制" if compose_calls["n"] > 1
                       else "彰化外海風場的新案例顯示") * 30
            return LiveWriterSectionOutput(
                section_title="結果與討論", section_content=content,
                sources_used=[1], confidence_level="High", status="drafted",
            )

        async def fake_extract(content, _handler):
            if "彰化外海風場" in content:
                return ["彰化外海風場", "苗栗"]
            return ["苗栗", "德國北萊茵"]

        async def fake_entity_check(section, chapter_evidence_text, handler, **kw):
            return []  # 過 fabrication guard，走到 (a) 兜底

        monkeypatch.setattr(
            "reasoning.agents.writer.WriterAgent.compose_section", fake_compose
        )
        monkeypatch.setattr(
            "reasoning.live_research.hallucination_guard.entity_grounding_check",
            fake_entity_check,
        )

        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=handler)
        orch.dry_run = False
        orch._extract_section_entities = fake_extract

        await orch._write_section(
            context_map=cm, topic={"name": "結果與討論", "outline": "綜合"},
            style_features=None, format_specs={}, evidence_pool=pool,
            chapter_index=1, all_evidence_ids=[1],
            book_outline=book_outline, current_chapter_index=1,
            state=state,
            prior_used_entities=["苗栗", "德國北萊茵"],
        )

        assert compose_calls["n"] >= 2
        assert "彰化外海風場" in (
            compose_calls["last_kw"].get("ungrounded_entities_revision") or []
        )

    @pytest.mark.asyncio
    async def test_synthesis_chapter_no_new_entity_no_rewrite(self, handler, monkeypatch):
        """synthesis 章 prose entity 全在前章聯集內 → (a) 不觸發 rewrite（不誤殺）。"""
        from reasoning.schemas_live import LiveWriterSectionOutput
        cm, state, pool, book_outline = self._make_synth_setup()

        compose_calls = {"n": 0}

        async def fake_compose(self, **kw):
            compose_calls["n"] += 1
            return LiveWriterSectionOutput(
                section_title="結果與討論",
                section_content="苗栗案場的回饋金機制顯示" * 30,
                sources_used=[1], confidence_level="High", status="drafted",
            )

        async def fake_extract(content, _handler):
            return ["苗栗"]  # ⊆ prior ["苗栗","德國北萊茵"]

        async def fake_entity_check(section, chapter_evidence_text, handler, **kw):
            return []

        monkeypatch.setattr(
            "reasoning.agents.writer.WriterAgent.compose_section", fake_compose
        )
        monkeypatch.setattr(
            "reasoning.live_research.hallucination_guard.entity_grounding_check",
            fake_entity_check,
        )

        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=handler)
        orch.dry_run = False
        orch._extract_section_entities = fake_extract

        await orch._write_section(
            context_map=cm, topic={"name": "結果與討論", "outline": "綜合"},
            style_features=None, format_specs={}, evidence_pool=pool,
            chapter_index=1, all_evidence_ids=[1],
            book_outline=book_outline, current_chapter_index=1,
            state=state,
            prior_used_entities=["苗栗", "德國北萊茵"],
        )

        assert compose_calls["n"] == 1  # 不觸發 rewrite


class TestStyleAnalysisSparseInput:
    """Stage 3 sparse-input 防呆（prod blocker fix 2026-05-30）。

    根因：LLM 對極短範本可能只回 1 個甚至 0 個 feature。schema 改 min_length=1
    後 1 個合法；0 個（空 features）走 orchestrator 優雅降級（fallback feature +
    明確降級訊息），不可再硬炸 ValidationError 中斷整條 LR。
    這些是 no-LLM unit test（mock ask_llm），不需 API key。
    """

    @pytest.fixture
    def mock_handler(self):
        handler = MagicMock()
        handler.query = "台灣綠能衝突"
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        handler.connection_alive_event = MagicMock()
        handler.connection_alive_event.is_set = MagicMock(return_value=True)
        handler.query_params = {}
        handler.site = "all"
        handler.final_retrieved_items = []
        handler._save_state = AsyncMock()  # plan: durable boundary persist awaits this
        return handler

    @pytest.fixture
    def orchestrator(self, mock_handler):
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=mock_handler)

    @pytest.mark.asyncio
    async def test_empty_features_does_not_raise_and_falls_back(self, orchestrator):
        """LLM 回 0 個 feature（空 list）→ 不可炸 ValidationError，須優雅降級。"""
        from reasoning.schemas_live import StyleAnalysisOutput

        empty_response = {"features": [], "overall_tone": "簡潔"}

        with patch("core.llm.ask_llm", new=AsyncMock(return_value=empty_response)):
            result = await orchestrator._run_style_analysis("短。")

        assert isinstance(result, StyleAnalysisOutput)
        assert len(result.features) >= 1, "空 features 應降級為至少 1 個 fallback feature"

    @pytest.mark.asyncio
    async def test_empty_features_fallback_has_degradation_message(self, orchestrator):
        """降級不可無聲：fallback 必須有明確降級訊息（CLAUDE.md 不可 silent fail）。"""
        empty_response = {"features": [], "overall_tone": "簡潔"}

        with patch("core.llm.ask_llm", new=AsyncMock(return_value=empty_response)):
            result = await orchestrator._run_style_analysis("短。")

        assert result.sample_quality_note, "降級必須有明確訊息，不可空白塞 fallback"
        assert ("範本較短" in result.sample_quality_note
                or "特徵有限" in result.sample_quality_note), (
            f"降級訊息應說明範本短 / 特徵有限，got: {result.sample_quality_note!r}"
        )

    @pytest.mark.asyncio
    async def test_single_feature_passes_through_normally(self, orchestrator):
        """LLM 回 1 個 feature → min_length 已改 1，正常 validate 通過（非 fallback path）。"""
        single_response = {
            "features": [
                {"dimension": "引用習慣", "observation": "常引用官方數據",
                 "instruction": "引用時標明來源機構"}
            ],
            "overall_tone": "嚴謹",
        }

        with patch("core.llm.ask_llm", new=AsyncMock(return_value=single_response)):
            result = await orchestrator._run_style_analysis("短範本。")

        assert len(result.features) == 1
        assert result.features[0].dimension == "引用習慣"


class TestGroundingEvidenceViewWiring:
    """A.2 / R1：chapter_evidence_text 改用 render_grounding_evidence_view
    （全 pool + 不截斷 + prior entities），以及 R1 fail-closed 退化。"""

    @pytest.fixture
    def handler(self):
        h = MagicMock()
        h.query_params = {}
        h.site = "all"
        h.message_sender = MagicMock()
        h.message_sender.send_message = AsyncMock()
        return h

    def _make_setup(self):
        from reasoning.schemas_live import (
            ContextMap, ContextMapTopic, BookOutline, ChapterPlan,
            EvidencePoolEntry, GroundedClaim,
        )
        cm = ContextMap(
            research_question="q", version=0,
            topics=[ContextMapTopic(topic_id="t", name="n", domain="d",
                                    relevance="core", description="d",
                                    evidence_ids=[1])],
        )
        state = LiveResearchStageState()
        # eid 1 = 本章引用，含長 snippet（驗不截斷）
        long_snippet = "台南案場細節" + "詳述內容" * 80  # 遠超 200
        state.evidence_usage = {
            1: [GroundedClaim(
                claim="台南案場推綠能", reasoning_type="induction",
                confidence="high", source_topic="t", source_iteration=1,
            ).model_dump()]
        }
        # eid 1 本章引用；eid 2 本章「未引用但在 pool」（驗全 pool，非 subset）
        pool = {
            1: EvidencePoolEntry(
                evidence_id=1, title="台南案場", url="u", snippet=long_snippet,
            ),
            2: EvidencePoolEntry(
                evidence_id=2, title="未被本章引用但在 pool",
                url="u2", snippet="跨章 evidence：別章引用的內容",
            ),
        }
        book_outline = BookOutline(chapters=[
            ChapterPlan(chapter_index=0, title="前言", brief="x",
                        planned_evidence_ids=[1], role="intro"),
            ChapterPlan(chapter_index=1, title="國內案例", brief="案例分析",
                        planned_evidence_ids=[1], role="body"),
        ], overall_arc="x", redundancy_warnings=[])
        return cm, state, pool, book_outline, long_snippet

    @pytest.mark.asyncio
    async def test_grounding_evidence_text_is_full_and_cross_chapter(
        self, handler, monkeypatch
    ):
        """chapter_evidence_text 改用 render_grounding_evidence_view：
        全 evidence pool（不只本章 subset）+ snippet 不截斷 + 含 prior_used_entities。"""
        from reasoning.schemas_live import LiveWriterSectionOutput
        cm, state, pool, book_outline, long_snippet = self._make_setup()

        captured = {"evidence_text": None}

        async def fake_compose(self, **kw):
            return LiveWriterSectionOutput(
                section_title=kw["section_title"],
                section_content="台南案場推動綠能。" * 30,
                sources_used=[1], confidence_level="High", status="drafted",
            )

        async def fake_check(section, chapter_evidence_text, handler, **kw):
            captured["evidence_text"] = chapter_evidence_text
            return []  # 全 grounded，不觸發 rewrite

        async def fake_extract(content, _handler):
            return []  # specificity path 不抽 entity，避免額外干擾

        monkeypatch.setattr(
            "reasoning.agents.writer.WriterAgent.compose_section", fake_compose
        )
        monkeypatch.setattr(
            "reasoning.live_research.hallucination_guard.entity_grounding_check",
            fake_check,
        )

        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=handler)
        orch.dry_run = False
        orch._extract_section_entities = fake_extract

        await orch._write_section(
            context_map=cm, topic={"name": "國內案例", "outline": "案例分析"},
            style_features=None, format_specs={}, evidence_pool=pool,
            chapter_index=1, all_evidence_ids=[1],
            book_outline=book_outline, current_chapter_index=1,
            state=state,
            prior_used_entities=["台灣電力公司"],
        )

        ev = captured["evidence_text"]
        assert ev is not None
        # 不截斷：完整長 snippet 在視圖內（非 [:200]）
        assert long_snippet in ev
        # 全 pool：本章未引用但在 pool 的 eid 2 也在視圖（CEO 決策②）
        assert "未被本章引用但在 pool" in ev
        # 跨章 prior grounded entity 進視圖
        assert "台灣電力公司" in ev

    @pytest.mark.asyncio
    async def test_grounding_check_failure_degrades_not_all_grounded(
        self, handler, monkeypatch
    ):
        """R1 fail-closed：語意 grounding LLM 拋 exception → section 走 DR 式退化
        （confidence=Low + methodology 標「grounding 系統驗證失敗」+ 正文保留），
        絕不回 [] 當作全 grounded（fail-open）。"""
        from reasoning.live_research import hallucination_guard as hg
        from reasoning.schemas_live import LiveWriterSectionOutput
        cm, state, pool, book_outline, _ = self._make_setup()

        original = "台電推動綠能轉型，與地方溝通密切，案場進度順利推進中。" * 5

        async def fake_compose(self, **kw):
            return LiveWriterSectionOutput(
                section_title=kw["section_title"],
                section_content=original,
                sources_used=[1], confidence_level="High", status="drafted",
            )

        # 字面捷徑 miss → 進語意層；語意層 ask_llm 爆窗
        async def fake_extract_grounding(content, handler, level="low", **kwargs):
            return ["台電"]  # 字面不在 evidence（evidence 寫全名）

        async def boom(*a, **kw):
            raise RuntimeError("context window exceeded")

        async def fake_extract_section(content, _handler):
            return []  # specificity path 不抽 entity，避免干擾

        monkeypatch.setattr(
            "reasoning.agents.writer.WriterAgent.compose_section", fake_compose
        )
        monkeypatch.setattr(hg, "_extract_entities_for_grounding", fake_extract_grounding)
        monkeypatch.setattr("core.llm.ask_llm", boom)

        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=handler)
        orch.dry_run = False
        orch._extract_section_entities = fake_extract_section

        section_output, _ = await orch._write_section(
            context_map=cm, topic={"name": "國內案例", "outline": "案例分析"},
            style_features=None, format_specs={}, evidence_pool=pool,
            chapter_index=1, all_evidence_ids=[1],
            book_outline=book_outline, current_chapter_index=1,
            state=state,
            prior_used_entities=[],
        )

        # 不丟 exception 炸 pipeline（orchestrator 內 try/except 接住）+ NOT 全 grounded
        assert section_output.confidence_level == "Low"
        assert lr_copy.GROUNDING_UNAVAILABLE_NOTE in (section_output.methodology_note or "")
        # 正文保留（非整章替換、非全刪）
        assert "台電" in section_output.section_content
        assert "[本章內容無法驗證]" not in section_output.section_content
        # 不是整章 guard_failed
        assert getattr(section_output, "status", "drafted") != "guard_failed"

    @pytest.mark.asyncio
    async def test_grounding_check_failure_emits_realtime_narration_once_per_run(
        self, handler, monkeypatch
    ):
        """D-2026-06-11 決策1（o5a F3 解凍）：GroundingCheckUnavailable 退化除了
        report 內 methodology note + 降 Low，必須補一次即時 SSE 旁白
        （單一落點 = _apply_degraded_grounding_unavailable，蓋三個 except 呼叫點）。
        per-run dedup：同一 run 多章連續退化只播一次（防轟炸）；
        各章退化仍由 methodology note 逐章標示，不受 dedup 影響。"""
        from reasoning.live_research import hallucination_guard as hg
        from reasoning.schemas_live import LiveWriterSectionOutput
        cm, state, pool, book_outline, _ = self._make_setup()

        async def fake_compose(self, **kw):
            return LiveWriterSectionOutput(
                section_title=kw["section_title"],
                section_content="台電推動綠能轉型，與地方溝通密切，案場進度推進中。" * 5,
                sources_used=[1], confidence_level="High", status="drafted",
            )

        # 字面捷徑 miss → 進語意層；語意層 ask_llm 爆 → GroundingCheckUnavailable
        async def fake_extract_grounding(content, handler, level="low", **kwargs):
            return ["台電"]

        async def boom(*a, **kw):
            raise RuntimeError("context window exceeded")

        async def fake_extract_section(content, _handler):
            return []

        monkeypatch.setattr(
            "reasoning.agents.writer.WriterAgent.compose_section", fake_compose
        )
        monkeypatch.setattr(hg, "_extract_entities_for_grounding", fake_extract_grounding)
        monkeypatch.setattr("core.llm.ask_llm", boom)

        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=handler)
        orch.dry_run = False
        orch._extract_section_entities = fake_extract_section
        # 禁真 LLM：publish gate 的 critic/TypeAgent 走 instructor 自有 client，
        # ask_llm patch 蓋不到 → 用 feature flag 關掉（gate 在被測退化點之後，
        # 與本測試斷言無關；F-AMB-7 short-circuit 是既有行為）。
        orch.features = dict(orch.features)
        orch.features["live_research_critic_publish_gate"] = False

        narrated = []

        async def fake_narrate(text):
            narrated.append(text)
        orch._emit_narration = fake_narrate

        kwargs = dict(
            context_map=cm, topic={"name": "國內案例", "outline": "案例分析"},
            style_features=None, format_specs={}, evidence_pool=pool,
            chapter_index=1, all_evidence_ids=[1],
            book_outline=book_outline, current_chapter_index=1,
            state=state, prior_used_entities=[],
        )
        out1, _ = await orch._write_section(**kwargs)
        out2, _ = await orch._write_section(**kwargs)

        # 兩章都有退化（report 內逐章標示不受 dedup 影響）
        assert lr_copy.GROUNDING_UNAVAILABLE_NOTE in (out1.methodology_note or "")
        assert lr_copy.GROUNDING_UNAVAILABLE_NOTE in (out2.methodology_note or "")
        # 即時旁白 per-run 恰好一次（文案 = lr_copy 單一事實源）
        hits = [t for t in narrated if t == lr_copy.GROUNDING_UNAVAILABLE_NARRATION]
        assert len(hits) == 1, (
            f"GCU 退化旁白應 per-run 恰一次（防多章轟炸），實際 {len(hits)} 次；"
            f"全部旁白={narrated}"
        )


class TestPartialBlock:
    """Fix2 (CEO 決策④): partial block — 主路徑刪純未驗證句 / 退化 DR-style 保留正文，
    丟掉整章替換 [本章內容無法驗證]。R3 句子分類 + R5 語意退化條件。"""

    @pytest.fixture
    def handler(self):
        h = MagicMock()
        h.query_params = {}
        h.site = "all"
        h.message_sender = MagicMock()
        h.message_sender.send_message = AsyncMock()
        return h

    @pytest.fixture
    def orch(self, handler):
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            o = LiveResearchOrchestrator(handler=handler)
        o.dry_run = False
        return o

    def _make_body_setup(self):
        from reasoning.schemas_live import (
            ContextMap, ContextMapTopic, BookOutline, ChapterPlan,
            EvidencePoolEntry, GroundedClaim,
        )
        cm = ContextMap(
            research_question="q", version=0,
            topics=[ContextMapTopic(topic_id="t", name="n", domain="d",
                                    relevance="core", description="d",
                                    evidence_ids=[3])],
        )
        state = LiveResearchStageState()
        state.evidence_usage = {
            3: [GroundedClaim(
                claim="台鹽綠能台南案場", reasoning_type="induction",
                confidence="high", source_topic="t", source_iteration=1,
            ).model_dump()]
        }
        pool = {3: EvidencePoolEntry(
            evidence_id=3, title="台鹽綠能", url="u",
            snippet="台鹽綠能在台南設置案場，引發養殖戶疑慮",
        )}
        book_outline = BookOutline(chapters=[
            ChapterPlan(chapter_index=0, title="前言", brief="x",
                        planned_evidence_ids=[3], role="intro"),
            ChapterPlan(chapter_index=1, title="國內案例文獻", brief="案例",
                        planned_evidence_ids=[3], role="body"),
        ], overall_arc="x", redundancy_warnings=[])
        return cm, state, pool, book_outline

    @pytest.mark.asyncio
    async def test_partial_block_keeps_grounded_sentences(self, orch, handler, monkeypatch):
        """rewrite 後仍剩 1 ungrounded entity（純未驗證句）→ 只移除該句、保留其餘 prose，
        不再整章替換為 [本章內容無法驗證]。"""
        from reasoning.schemas_live import LiveWriterSectionOutput
        cm, state, pool, book_outline = self._make_body_setup()

        async def fake_compose(self, **kw):
            # 句子夠長：刪 1 句後 kept 仍 >150 字（避免命中 _degenerate 字數下限走退化）。
            return LiveWriterSectionOutput(
                section_title="國內案例文獻",
                section_content=(
                    "台鹽綠能在台南設置漁電共生案場，引發在地養殖戶對於水質、光照"
                    "與產量的長期疑慮，地方社區也持續關注後續的環境影響評估、利害"
                    "關係人會議與資訊揭露進度，相關討論至今尚未完全平息。"
                    "某水泥公司在當地的空氣污染爭議也一併被在地居民提及探討。"
                    "整體而言，社區的充分溝通與完整資訊揭露，被多數受訪者視為化解"
                    "能源開發衝突的關鍵所在，也是後續政策推動與在地共識凝聚過程中"
                    "必須優先處理、不可迴避的核心課題與制度設計重點。"
                ),
                sources_used=[3], confidence_level="Medium", status="drafted",
            )
        monkeypatch.setattr(
            "reasoning.agents.writer.WriterAgent.compose_section", fake_compose
        )
        checks = iter([["某水泥公司"], ["某水泥公司"]])  # 1st 觸發 rewrite, 2nd 剩 1 個

        async def fake_check(section, chapter_evidence_text, handler, **kw):
            return next(checks)
        monkeypatch.setattr(
            "reasoning.live_research.hallucination_guard.entity_grounding_check",
            fake_check,
        )

        async def fake_extract(content, _handler):
            return []
        orch._extract_section_entities = fake_extract

        section_output, _ = await orch._write_section(
            context_map=cm, topic={"name": "國內案例文獻", "outline": "案例"},
            style_features=None, format_specs={}, evidence_pool=pool,
            chapter_index=1, all_evidence_ids=[3],
            book_outline=book_outline, current_chapter_index=1,
            state=state,
        )

        assert "台鹽綠能在台南設置漁電共生案場" in section_output.section_content
        assert "某水泥公司" not in section_output.section_content
        assert getattr(section_output, "status", "drafted") != "guard_failed"
        assert "[本章內容無法驗證]" not in section_output.section_content
        assert section_output.methodology_note  # 有降級註記

    @pytest.mark.asyncio
    async def test_mixed_sentence_routes_to_degraded_not_hard_delete(self, orch):
        """R3：含已驗證 entity 的混合句 → split 回報 unsafe>0 → 走退化 (a)，正文不變。"""
        from reasoning.schemas_live import LiveWriterSectionOutput
        section = LiveWriterSectionOutput(
            section_title="t",
            section_content="台鹽綠能與某水泥公司在台南共同推動案場，引發社區長期討論與關注。",
            sources_used=[3], confidence_level="Medium",
        )
        new_out, degraded = orch._apply_partial_or_degraded_block(
            section_output=section, ungrounded=["某水泥公司"],
            analyst_citations=[3], current_chapter_index=1, label="t",
            grounded_entities=["台鹽綠能"],
        )
        assert degraded is True                                  # 走退化 (a)
        assert new_out.section_content == section.section_content  # 正文一字不改
        assert new_out.confidence_level == "Low"
        assert lr_copy.degraded_low_confidence_note(["某水泥公司"]) in new_out.methodology_note

    @pytest.mark.asyncio
    async def test_high_citation_loss_routes_to_degraded(self, orch):
        """R5：刪句會流失過多 citation（citation_loss_ratio>0.5）→ 走退化 (a)，不硬刪。"""
        from reasoning.schemas_live import LiveWriterSectionOutput
        content = "某水泥公司爭議見報導[3]。其餘背景補充說明見另一份資料[4]。"
        section = LiveWriterSectionOutput(
            section_title="t", section_content=content,
            sources_used=[3, 4], confidence_level="Medium",
        )
        new_out, degraded = orch._apply_partial_or_degraded_block(
            section_output=section, ungrounded=["某水泥公司"],
            analyst_citations=[3, 4], current_chapter_index=1, label="t",
            grounded_entities=[],
        )
        # 含 citation 的句子本就被 R3 判 unsafe → 退化；正文保留
        assert degraded is True
        assert new_out.section_content == content
        assert new_out.confidence_level == "Low"

    @pytest.mark.asyncio
    async def test_degraded_path_keeps_full_pool_citations(self, orch):
        """P2 W7 I1（§0 #24）：退化路徑 sources_used 放寬到全 pool 合法集。
        sources_used=[1,3]，analyst_citations=[1]，pool={1,2,3} → 3 是 pool 內合法
        引用，不該被砍（舊版 set(analyst_citations) 交集只留 [1]）。"""
        from reasoning.schemas_live import LiveWriterSectionOutput, EvidencePoolEntry
        # 短正文 → 命中 _degenerate（kept<150）→ 走退化路徑 (a)
        section = LiveWriterSectionOutput(
            section_title="t",
            section_content="某水泥公司爭議短句[3]。",
            sources_used=[1, 3], confidence_level="Medium",
        )
        pool = {i: EvidencePoolEntry(evidence_id=i, title=f"T{i}", url="u",
                                     snippet="s") for i in (1, 2, 3)}
        new_out, degraded = orch._apply_partial_or_degraded_block(
            section_output=section, ungrounded=["某水泥公司"],
            analyst_citations=[1], current_chapter_index=1, label="t",
            grounded_entities=[], evidence_pool=pool,
        )
        assert degraded is True
        # pool 內合法引用 3 保留（不因不在 analyst_citations 被砍）
        assert 3 in new_out.sources_used
        assert 1 in new_out.sources_used

    @pytest.mark.asyncio
    async def test_grounding_unavailable_keeps_full_pool_citations(self, orch):
        """P2 W7 I1（§0 #23）：grounding-unavailable 退化路徑同樣放寬全 pool。"""
        from reasoning.schemas_live import LiveWriterSectionOutput, EvidencePoolEntry
        section = LiveWriterSectionOutput(
            section_title="t", section_content="內容[3]。",
            sources_used=[1, 3], confidence_level="Medium",
        )
        pool = {i: EvidencePoolEntry(evidence_id=i, title=f"T{i}", url="u",
                                     snippet="s") for i in (1, 2, 3)}
        new_out = await orch._apply_degraded_grounding_unavailable(
            section_output=section, analyst_citations=[1],
            current_chapter_index=1, reason="test", evidence_pool=pool,
        )
        assert 3 in new_out.sources_used        # pool 內合法引用保留
        assert 1 in new_out.sources_used


# ════════════════════════════════════════════════════════════════════════════
# 模塊5 Task 5: 條件式 writer calibration（通道 B）— orchestrator 整合
# 薄弱章傳 'thin' 且 skip specificity rewrite；充足章傳 'ok' 且 specificity 照常
# ════════════════════════════════════════════════════════════════════════════


class TestWriteSectionEvidenceSufficiencyCalibration:
    @pytest.fixture
    def t5_handler(self):
        handler = MagicMock()
        handler.query = "q"
        handler.query_params = {}
        handler.connection_alive_event = MagicMock()
        handler.connection_alive_event.is_set = MagicMock(return_value=True)
        handler.final_retrieved_items = []
        handler.site = "all"
        handler.message_sender = MagicMock()
        handler.message_sender.send_message = AsyncMock()
        return handler

    @pytest.mark.asyncio
    async def test_thin_chapter_passes_thin_sufficiency_to_writer(
        self, t5_handler, monkeypatch
    ):
        """本章 planned_evidence_ids 只有 1 筆 → _write_section 應傳
        evidence_sufficiency='thin' 給 compose_section，且不觸發 specificity
        auto-rewrite（compose 只被呼叫 1 次 — 薄弱章 skip specificity guard）。
        即便 compose 回傳一段抽不到 entity 的抽象 prose（正常會觸發 specificity），
        thin 章的 chapter_sufficiency != "ok" 守衛使 specificity gate 不進入。"""
        from reasoning.schemas_live import (
            ContextMap, ContextMapTopic, BookOutline, ChapterPlan,
            EvidencePoolEntry, GroundedClaim, LiveWriterSectionOutput,
        )

        cm = ContextMap(
            research_question="q", version=0,
            topics=[ContextMapTopic(topic_id="t1", name="n", domain="d",
                                    relevance="core", description="d",
                                    evidence_ids=[2])],
        )
        # evidence snippet 含數字 → evidence_has_concrete=True（若 gate 真的跑會 flag）
        evidence_pool = {
            2: EvidencePoolEntry(evidence_id=2, title="T2 含 2024 年數據",
                                 url="u", snippet="2024 年的具體數字 s2"),
        }
        # state.evidence_usage 讓 render_grounded_narrative 產非空 findings（否則 post-render gate 攔）
        state = LiveResearchStageState()
        state.evidence_usage = {
            2: [GroundedClaim(claim="ThinChapterClaim", reasoning_type="induction",
                              confidence="high", source_topic="t1",
                              source_iteration=1).model_dump()],
        }
        book_outline = BookOutline(
            chapters=[
                ChapterPlan(chapter_index=0, title="前言", brief="x",
                            planned_evidence_ids=[2], role="intro"),
                ChapterPlan(chapter_index=1, title="本章 body", brief="y",
                            planned_evidence_ids=[2], role="body"),  # 1 筆 → thin
            ],
            overall_arc="x", redundancy_warnings=[],
        )

        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=t5_handler)
        orch.dry_run = False
        # prose 抽不到任何具體 entity（正常會讓 specificity flag）
        orch._extract_section_entities = AsyncMock(return_value=[])

        captured = []

        async def fake_compose_section(self, **kw):
            captured.append(kw.get("evidence_sufficiency"))
            return LiveWriterSectionOutput(
                section_title=kw["section_title"],
                # 長度 >= 200 且抽象（無具體 entity），確保「若 gate 跑」會 flag
                section_content="這是一段刻意寫得相當抽象的內容，" * 20,
                sources_used=[2],
                confidence_level="Medium",
                narration="x", chapter_summary="s",
            )

        monkeypatch.setattr(
            "reasoning.agents.writer.WriterAgent.compose_section",
            fake_compose_section,
        )

        await orch._write_section(
            context_map=cm,
            topic={"name": "本章 body", "outline": "y"},
            style_features=None,
            format_specs={},
            evidence_pool=evidence_pool,
            chapter_index=1,
            all_evidence_ids=[2],
            book_outline=book_outline,
            current_chapter_index=1,
            state=state,
        )

        # 1 筆 evidence → thin；且 specificity rewrite 未觸發（compose 只 1 次）
        assert captured == ["thin"], (
            f"thin 章應只呼叫 compose 1 次且傳 'thin'，got {captured}"
        )

    @pytest.mark.asyncio
    async def test_sufficient_chapter_passes_ok_and_keeps_specificity(
        self, t5_handler, monkeypatch
    ):
        """本章 planned_evidence_ids 多筆（> 門檻）→ evidence_sufficiency='ok'；
        specificity_check 仍正常運作 — prose 抽象（無 entity）+ evidence 有具體資訊
        → specificity flag → auto-rewrite，compose 被呼叫 2 次（兩次皆傳 'ok'）。"""
        from reasoning.schemas_live import (
            ContextMap, ContextMapTopic, BookOutline, ChapterPlan,
            EvidencePoolEntry, GroundedClaim, LiveWriterSectionOutput,
        )

        cm = ContextMap(
            research_question="q", version=0,
            topics=[ContextMapTopic(topic_id="t1", name="n", domain="d",
                                    relevance="core", description="d",
                                    evidence_ids=[1, 2, 3, 4])],
        )
        evidence_pool = {
            1: EvidencePoolEntry(evidence_id=1, title="T1 含 2021 數據",
                                 url="u", snippet="2021 具體 s1"),
            2: EvidencePoolEntry(evidence_id=2, title="T2", url="u", snippet="s2"),
            3: EvidencePoolEntry(evidence_id=3, title="T3", url="u", snippet="s3"),
            4: EvidencePoolEntry(evidence_id=4, title="T4", url="u", snippet="s4"),
        }
        # state.evidence_usage 讓 render_grounded_narrative 產非空 findings（否則 post-render gate 攔）
        state = LiveResearchStageState()
        state.evidence_usage = {
            1: [GroundedClaim(claim="OkChapterClaim2021", reasoning_type="induction",
                              confidence="high", source_topic="t1",
                              source_iteration=1).model_dump()],
        }
        book_outline = BookOutline(
            chapters=[
                ChapterPlan(chapter_index=0, title="前言", brief="x",
                            planned_evidence_ids=[1], role="intro"),
                ChapterPlan(chapter_index=1, title="本章 body", brief="y",
                            planned_evidence_ids=[1, 2, 3, 4], role="body"),  # 4 筆 → ok
            ],
            overall_arc="x", redundancy_warnings=[],
        )

        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=t5_handler)
        orch.dry_run = False
        # prose 抽不到 entity → specificity 會 flag（ok 章不被守衛擋住）
        orch._extract_section_entities = AsyncMock(return_value=[])

        captured = []

        async def fake_compose_section(self, **kw):
            captured.append(kw.get("evidence_sufficiency"))
            return LiveWriterSectionOutput(
                section_title=kw["section_title"],
                section_content="這是一段刻意寫得相當抽象的內容，" * 20,
                sources_used=[1],
                confidence_level="Medium",
                narration="x", chapter_summary="s",
            )

        monkeypatch.setattr(
            "reasoning.agents.writer.WriterAgent.compose_section",
            fake_compose_section,
        )

        await orch._write_section(
            context_map=cm,
            topic={"name": "本章 body", "outline": "y"},
            style_features=None,
            format_specs={},
            evidence_pool=evidence_pool,
            chapter_index=1,
            all_evidence_ids=[1, 2, 3, 4],
            book_outline=book_outline,
            current_chapter_index=1,
            state=state,
        )

        # 4 筆 → ok；specificity flag → rewrite → compose 2 次，兩次皆 'ok'
        assert captured == ["ok", "ok"], (
            f"ok 章 specificity 應觸發 rewrite（compose 2 次且皆 'ok'），got {captured}"
        )


def test_stage6_problematic_statuses_have_reason_zh():
    """O4+O4-C 合併版 Task C（CEO 2026-06-11 拍板）：critic_rejected 納入
    Stage 6 未完成章節偵測集合，且偵測集合每個 status 都有中文原因 —
    防 raw status code 經 reason fallback 漏給 user。
    """
    from reasoning.live_research.orchestrator import (
        _PROBLEMATIC_STATUSES,
        _PROBLEMATIC_REASON_ZH,
    )
    assert set(_PROBLEMATIC_STATUSES) == {
        "blocked_no_evidence", "guard_failed", "critic_rejected",
    }
    # 偵測集合 ⊆ reason map（同步性不變式）
    assert set(_PROBLEMATIC_STATUSES) <= set(_PROBLEMATIC_REASON_ZH)


def test_lr_user_facing_strings_have_no_dev_jargon():
    """O4+O4-C 合併版: LR user-facing 字串不可含內部 jargon。

    雙檔 AST 掃描：
    1. orchestrator.py — user-facing sink 內全部字串常數片段（含多行 f-string），
       解逐行掃描在多行 f-string 下的假綠燈（S4-1 #7）。
       sink: _emit_narration(...) / parts.append(...) / lines.append(...) 呼叫、
       section_content= / methodology_note= keyword、blocked_content 等 sink 變數賦值。
       logger.* 與 docstring / 註解天然不在 sink 內，自動排除。
    2. lr_copy.py — 全檔字串常數（模組紀律=全檔皆 user-facing），
       排除 docstring 與 legacy 匹配用常數（永不輸出給 user）。
    3. orchestrator.py — 全檔 FormattedValue 動態 status 插值 guard
       （S4-M Codex B1）：f-string 插值不是字串常數，掃描 1 抓不到
       raw status 漏出；含字串常數 "status" 的插值表達式必須包在
       _PROBLEMATIC_REASON_ZH 映射內。
    """
    import ast
    from pathlib import Path

    lr_dir = (
        Path(__file__).resolve().parents[3]
        / "reasoning" / "live_research"
    )
    texts = []

    # --- 1) orchestrator.py: sink 掃描（O4 修訂版設計照搬） ---
    tree = ast.parse((lr_dir / "orchestrator.py").read_text(encoding="utf-8"))
    SINK_ASSIGN_NAMES = {
        "blocked_content", "blocked_content_f1", "_note",
        "_degrade_note", "_note_prefix", "warn_marker",
    }
    sink_nodes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "_emit_narration":
                sink_nodes.append(node)
            elif (
                isinstance(f, ast.Attribute) and f.attr == "append"
                and isinstance(f.value, ast.Name)
                and f.value.id in ("parts", "lines")
            ):
                sink_nodes.append(node)
            for kw in getattr(node, "keywords", []):
                if kw.arg in ("section_content", "methodology_note", "clarifying_question"):
                    sink_nodes.append(kw.value)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in SINK_ASSIGN_NAMES:
                    sink_nodes.append(node.value)
    for sn in sink_nodes:
        for sub in ast.walk(sn):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                texts.append(
                    (f"orchestrator:{getattr(sub, 'lineno', -1)}", sub.value)
                )

    # --- 2) lr_copy.py: 全檔字串常數掃描（合併版適配） ---
    copy_tree = ast.parse((lr_dir / "lr_copy.py").read_text(encoding="utf-8"))
    # legacy 匹配用常數：僅用於辨識舊 session 持久化的舊 marker，永不輸出給 user
    _LEGACY_ALLOWED_NAMES = {"LEGACY_WARN_MARKER_PREFIX", "WARN_MARKER_DEDUP_RE"}
    skip_ids = set()
    for node in ast.walk(copy_tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            body = node.body
            if (
                body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
            ):
                skip_ids.add(id(body[0].value))  # docstring
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in _LEGACY_ALLOWED_NAMES
            for t in node.targets
        ):
            for sub in ast.walk(node.value):
                skip_ids.add(id(sub))
    for sub in ast.walk(copy_tree):
        if (
            isinstance(sub, ast.Constant) and isinstance(sub.value, str)
            and id(sub) not in skip_ids
        ):
            texts.append((f"lr_copy:{getattr(sub, 'lineno', -1)}", sub.value))

    forbidden = [
        "BAB", "grounding", "entity", "evidence", "claim-level",
        " claim ", "confidence=Low", "confidence 降為", "placeholder",
        "query", "F1 critic", "LLM", "Analyst",
        "(guard_failed)", "(blocked_no_evidence)", "(critic_rejected)",
    ]
    allowed_substrings: list = []  # 刻意保留的技術詞白名單（目前空；新增須附理由註解）
    offenders = [
        (loc, bad, text[:80])
        for loc, text in texts
        for bad in forbidden
        if bad in text and not any(a in text for a in allowed_substrings)
    ]
    assert not offenders, f"LR user-facing 字串殘留 jargon: {offenders}"

    # --- 3) 動態 status 插值 guard（S4-M Codex B1）：FormattedValue 不是
    #     字串常數，掃描 1/2 抓不到 raw status 漏出（搬移點 9 的原始 channel；
    #     中繼變數 titles/problem_lines 也不在 sink 名單）。全檔掃 f-string
    #     插值：表達式含字串常數 "status" 者必須包在 _PROBLEMATIC_REASON_ZH
    #     映射內。已知限制：變數中轉的間接漏出需 dataflow 分析，不做 —
    #     由 Task C map-coverage test + fallback 字面「未完成」把關。
    status_leaks = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FormattedValue):
            if any(
                isinstance(sub, ast.Constant) and sub.value == "status"
                for sub in ast.walk(node)
            ):
                expr_src = ast.unparse(node)
                if "_PROBLEMATIC_REASON_ZH" not in expr_src:
                    status_leaks.append((node.lineno, expr_src[:80]))
    assert not status_leaks, (
        f"f-string 直接插值 raw status（須經 _PROBLEMATIC_REASON_ZH 映射）: "
        f"{status_leaks}"
    )


def test_legacy_stage4_reframe_entry_removed():
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    assert not hasattr(LiveResearchOrchestrator, "_try_stage_4_reframe_entry"), \
        "legacy _try_stage_4_reframe_entry 仍存在；_typed 版已取代"
    # typed 版必須仍在
    assert hasattr(LiveResearchOrchestrator, "_try_stage_4_reframe_entry_typed")


@pytest.mark.asyncio
async def test_mock_bab_initial_format_uses_fixture_not_extraction():
    """mock_bab 路徑：format_specs.chapters 為 fixture 章節，不跑初始抽取覆蓋。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState

    handler = MagicMock()
    # 路 A merge 後 _persist_progress 為 async 會 `await self.handler._save_state(state)`，須補 AsyncMock。
    handler._save_state = AsyncMock()
    # 既有 pattern：mock_bab 是 attribute（非 __init__ kwarg），post-construction 設定。
    # dry_run=False 才有真 associator instance 可掛 mock method。
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
    orch.mock_bab = True
    orch._emit_stage_change = AsyncMock()
    orch._emit_narration = AsyncMock()
    orch._emit_checkpoint = AsyncMock()
    # 若初始抽取被誤呼叫 → 拋錯，讓 test fail
    orch.associator.extract_initial_format_spec = AsyncMock(
        side_effect=AssertionError("mock_bab 不該跑初始抽取")
    )

    state = LiveResearchStageState()
    state = await orch._run_stage_1(state, query="任意 query")

    # fixture book_outline 章節（5 章）仍在
    assert len(state.format_specs["chapters"]) == 5


class TestParseRevisionIntentPromptContract:
    """段內順序調整（對調/重排/調順序/換順序）被誤判 structure_change 的 prompt 修正。

    Root cause（真 LLM 矩陣已驗證）：順序動詞 + 非段號/標題錨點（「這段」「最後一段」）
    時，low model 把「重排/對調」吸進 structure_change。現況 prompt 的 structure_change
    定義與紀律條含裸詞「重排」，且 revise_section 範例無任何「段內順序操作」例子。

    修法（純 prompt）：縮窄 structure_change 到「章節 / 章與章之間」層級；擴大
    revise_section 明確涵蓋「作用在一段之內」的順序操作（無論錨點是段號、標題、
    『這段/這部分/這裡』還是『最後一段』）。

    這是 prompt 結構 contract test：攔截送給 ask_llm 的 prompt，斷言含上述指引。
    （真 LLM 服從度由 GATED 臨時腳本另行驗證；此處只鎖 prompt 不退化。）
    """

    @pytest.fixture
    def mock_handler(self):
        h = MagicMock()
        h.query = "台灣綠能衝突"
        h.query_params = {}
        return h

    @pytest.fixture
    def orchestrator(self, mock_handler):
        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            orch = LiveResearchOrchestrator(handler=mock_handler)
        orch.dry_run = False  # dry_run=True 會 early-return done 不組 prompt
        return orch

    @staticmethod
    def _written():
        return [
            {"section_index": 0, "title": "離岸風電", "content": "x"},
            {"section_index": 1, "title": "收益分配", "content": "y"},
        ]

    async def _capture_prompt(self, orchestrator, user_message):
        captured = {}

        async def fake_ask_llm(prompt, *args, **kwargs):
            captured["prompt"] = prompt
            return {"action": "revise_section", "target_index": None, "reason": "x"}

        with patch("core.llm.ask_llm", new=fake_ask_llm):
            await orchestrator._parse_revision_intent(user_message, self._written())
        return captured["prompt"]

    @pytest.mark.asyncio
    async def test_prompt_routes_intra_section_order_change_to_revise_section(self, orchestrator):
        """段內順序操作（對調/重排/調順序/換順序，作用在一段之內）→ revise_section。"""
        prompt = await self._capture_prompt(orchestrator, "把這段的論點順序對調")
        assert "段內" in prompt, "prompt 應出現「段內」概念以區隔 revise_section"
        # 順序動詞至少有一個出現在 revise_section 引導語境
        assert any(v in prompt for v in ("對調", "調順序", "重排", "換順序")), (
            "prompt 應在 revise_section 列出順序類動詞（對調/重排/調順序/換順序）"
        )

    @pytest.mark.asyncio
    async def test_prompt_revise_section_covers_non_index_anchors(self, orchestrator):
        """revise_section 涵蓋『這段/這部分/這裡/最後一段』等非段號錨點的段內順序操作。"""
        prompt = await self._capture_prompt(orchestrator, "最後一段順序調一下")
        # 修法須明示：錨點是『這段』『最後一段』等也算段內操作，不因錨點非段號就升級 structure_change
        assert "一段之內" in prompt or "段內" in prompt, (
            "prompt 應說明順序操作只要作用在『一段之內』即 revise_section（不論錨點形式）"
        )

    @pytest.mark.asyncio
    async def test_prompt_restricts_structure_change_to_chapter_level(self, orchestrator):
        """structure_change 必須被釐清為『章節 / 章與章之間 / 整章』層級操作。"""
        prompt = await self._capture_prompt(orchestrator, "合併第1章和第3章")
        assert ("章與章" in prompt or "章節層級" in prompt or "整章" in prompt), (
            "prompt 應釐清 structure_change 限『章 / 章與章之間 / 整章』層級"
        )

    @pytest.mark.asyncio
    async def test_prompt_preserves_recollect_action(self, orchestrator):
        """致命約束 1：6-action 不可退回 5-action，recollect 必須仍在 prompt。"""
        prompt = await self._capture_prompt(orchestrator, "隨便")
        assert "recollect" in prompt, "修法不可移除 recollect action（必須維持 6-action）"

    @pytest.mark.asyncio
    async def test_prompt_preserves_target_index_null_rule(self, orchestrator):
        """致命約束 3：target_index 的近指代『這段』→ null 規則不可被破壞。"""
        prompt = await self._capture_prompt(orchestrator, "這段順序對調")
        assert "這段" in prompt and "null" in prompt, (
            "近指代『這段』→ target_index null 的規則必須仍在 prompt"
        )


# === lr-chapter-word-budget-enforcement plan：a 軟提示字數檢查 ===


def test_count_chapter_words_strips_cite_placeholders():
    from reasoning.live_research.orchestrator import _count_chapter_words
    # 純中文，無 citation
    assert _count_chapter_words("台灣綠能政策推動。") == len("台灣綠能政策推動。")
    # 含 {cite:N} placeholder — 剝除後才算（每個 placeholder 不計入字數）
    content = "再生能源占比達 32.5%{cite:1}。德國經驗{cite:12}值得借鏡。"
    expected = len("再生能源占比達 32.5%。德國經驗值得借鏡。")
    assert _count_chapter_words(content) == expected
    # 空字串
    assert _count_chapter_words("") == 0


def test_chapter_target_words_reads_from_outline():
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import BookOutline, ChapterPlan
    outline = BookOutline(
        chapters=[
            ChapterPlan(chapter_index=0, title="前言", brief="鋪陳",
                        target_word_count=500, role="intro"),
            ChapterPlan(chapter_index=1, title="國內案例", brief="文獻",
                        target_word_count=2500, role="body"),
        ],
        overall_arc="x",
    )
    assert LiveResearchOrchestrator._chapter_target_words(outline, 1) == 2500
    assert LiveResearchOrchestrator._chapter_target_words(outline, 0) == 500
    # 越界 / None → 0（未指定，不觸發檢查）
    assert LiveResearchOrchestrator._chapter_target_words(outline, 5) == 0
    assert LiveResearchOrchestrator._chapter_target_words(None, 0) == 0
