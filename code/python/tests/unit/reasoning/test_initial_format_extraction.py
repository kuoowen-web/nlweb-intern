"""LR 初始 query 格式 spec 抽取層 — schema / 落庫 / 零變化測試。"""
import pytest
from reasoning.schemas_live import (
    InitialFormatSpec,
    InitialChapterSpec,
    SpecialElementSpec,
)


class TestInitialFormatSpecSchema:
    def test_empty_default_all_none(self):
        spec = InitialFormatSpec()
        assert spec.chapters == []
        assert spec.total_word_count is None
        assert spec.citation_style is None
        assert spec.special_elements == []

    def test_has_meaningful_spec_true_when_any_field_set(self):
        assert InitialFormatSpec().has_meaningful_spec() is False
        assert InitialFormatSpec(total_word_count=7000).has_meaningful_spec() is True
        assert InitialFormatSpec(
            citation_style="author_year"
        ).has_meaningful_spec() is True
        assert InitialFormatSpec(
            chapters=[InitialChapterSpec(name="前言")]
        ).has_meaningful_spec() is True
        assert InitialFormatSpec(
            special_elements=[SpecialElementSpec(type="table")]
        ).has_meaningful_spec() is True

    def test_chapter_word_target_captured(self):
        # AR B1: 逐章字數必須抽得出（下游 outline planner 真消費）
        spec = InitialFormatSpec(
            chapters=[InitialChapterSpec(name="前言", word_target=2000)]
        )
        assert spec.chapters[0].word_target == 2000

    def test_chapter_word_target_rejects_below_one(self):
        with pytest.raises(Exception):
            InitialChapterSpec(name="前言", word_target=0)

    def test_chapters_and_special_elements_use_submodels(self):
        spec = InitialFormatSpec(
            chapters=[InitialChapterSpec(name="國內案例", description="台灣個案")],
            special_elements=[
                SpecialElementSpec(type="table", target_chapter="結果與討論")
            ],
        )
        assert spec.chapters[0].name == "國內案例"
        assert spec.special_elements[0].type == "table"
        assert spec.special_elements[0].target_chapter == "結果與討論"

    def test_total_word_count_rejects_below_one(self):
        with pytest.raises(Exception):
            InitialFormatSpec(total_word_count=0)


from reasoning.prompts.stage1_format_extract import (
    Stage1FormatExtractPromptBuilder,
)


class TestExtractPromptBuilder:
    def test_prompt_embeds_query(self):
        builder = Stage1FormatExtractPromptBuilder()
        prompt = builder.build_extract_prompt(
            "幫我寫一份五章報告，總共約7000字，第四章放比較表格，用APA引用"
        )
        assert "五章報告" in prompt
        assert "7000" in prompt

    def test_prompt_states_conservative_default(self):
        builder = Stage1FormatExtractPromptBuilder()
        prompt = builder.build_extract_prompt("台灣綠能發展現況")
        # 沒指定格式時必須 null / 空 — prompt 要明示保守 default
        assert "沒" in prompt or "未指定" in prompt or "null" in prompt

    def test_count_only_chapters_empty(self):
        # AR round 2 SF1-a：章數-only 輸入（無標題）→ prompt 明確指示 chapters 留空
        builder = Stage1FormatExtractPromptBuilder()
        prompt = builder.build_extract_prompt("幫我分成五章")
        # prompt 必須說明只有章數、沒有標題時 chapters 留空
        assert "章數" in prompt or "標題" in prompt or "空" in prompt

    def test_explicit_chapter_names_described(self):
        # AR round 2 SF1-b：明確列章名 → prompt 說明這些名稱應進 chapters
        builder = Stage1FormatExtractPromptBuilder()
        prompt = builder.build_extract_prompt(
            "幫我寫三章：前言、分析、結論"
        )
        assert "章節標題" in prompt or "明確" in prompt or "原文" in prompt

    def test_no_format_at_all_yields_conservative_instruction(self):
        # AR round 2 SF1-c：完全沒提格式 → prompt 清楚說全欄位 null / 空
        builder = Stage1FormatExtractPromptBuilder()
        prompt = builder.build_extract_prompt("台灣半導體產業的未來")
        assert "null" in prompt or "空" in prompt or "沒提" in prompt


from unittest.mock import AsyncMock, MagicMock


class TestAssociatorExtract:
    @pytest.mark.asyncio
    async def test_extract_calls_llm_validated_with_schema(self):
        from reasoning.agents.associator import AssociatorAgent

        handler = MagicMock()
        agent = AssociatorAgent(handler=handler)
        expected = InitialFormatSpec(total_word_count=7000)
        agent.call_llm_validated = AsyncMock(return_value=(expected, 0, False))

        result = await agent.extract_initial_format_spec("總共7000字")

        assert result is expected
        # 驗證走 InitialFormatSpec schema + low level
        _, kwargs = agent.call_llm_validated.call_args
        assert kwargs["response_schema"] is InitialFormatSpec
        assert kwargs["level"] == "low"


class TestApplyInitialFormatSpec:
    def _make_orch(self, monkeypatch=None):
        # 借既有 orchestrator 建構 helper；若 import 重，直接 new 一個最小 instance
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        from reasoning.live_research.stage_state import LiveResearchStageState
        # AR round 2 SF3：patch AssociatorAgent 防意外真 client 初始化
        # （dry_run=False 會建真 AssociatorAgent，可能觸發真 client setup）
        if monkeypatch is not None:
            import reasoning.agents.associator as _assoc_mod
            monkeypatch.setattr(
                _assoc_mod, "AssociatorAgent", MagicMock(return_value=MagicMock()),
                raising=False,
            )
        handler = MagicMock()
        orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
        state = LiveResearchStageState()
        return orch, state

    def test_apply_full_spec_writes_existing_fields(self, monkeypatch):
        orch, state = self._make_orch(monkeypatch)
        spec = InitialFormatSpec(
            chapters=[
                InitialChapterSpec(name="前言", description="背景", word_target=1500),
                InitialChapterSpec(name="結果與討論"),
            ],
            total_word_count=7000,
            citation_style="author_year",
            special_elements=[
                SpecialElementSpec(type="table", target_chapter="結果與討論",
                                   description="五國比較"),
            ],
        )
        orch._apply_initial_format_spec(state, spec)

        # chapters → format_specs.chapters，形狀 {name, outline[, word_target]}
        # AR B1：有 word_target 的章寫出該 key、沒有的不寫
        assert state.format_specs["chapters"] == [
            {"name": "前言", "outline": "背景", "word_target": 1500},
            {"name": "結果與討論", "outline": ""},
        ]
        # 總字數 → user_voice + format_specs mirror
        assert state.user_voice.target_word_count == 7000
        assert state.format_specs["target_word_count"] == 7000
        # 引用格式 → user_voice
        assert state.user_voice.citation_style == "author_year"
        # special_elements → format_specs，形狀 {type, target_chapter, description}
        assert state.format_specs["special_elements"] == [
            {"type": "table", "target_chapter": "結果與討論",
             "description": "五國比較"},
        ]

    def test_apply_empty_spec_no_mutation(self, monkeypatch):
        orch, state = self._make_orch(monkeypatch)
        before = dict(state.format_specs or {})
        orch._apply_initial_format_spec(state, InitialFormatSpec())
        assert dict(state.format_specs or {}) == before
        assert state.user_voice.target_word_count is None
        assert state.user_voice.citation_style is None
        assert "chapters" not in (state.format_specs or {})
        assert "special_elements" not in (state.format_specs or {})

    def test_apply_partial_spec_only_writes_present(self, monkeypatch):
        orch, state = self._make_orch(monkeypatch)
        orch._apply_initial_format_spec(
            state, InitialFormatSpec(citation_style="numeric")
        )
        assert state.user_voice.citation_style == "numeric"
        assert state.user_voice.target_word_count is None
        assert "chapters" not in (state.format_specs or {})
        assert "special_elements" not in (state.format_specs or {})


class TestConfirmationCopy:
    def test_full_confirmation_line(self):
        from reasoning.live_research.lr_copy import (
            initial_format_confirmation_line,
        )
        line = initial_format_confirmation_line(
            chapter_names=["前言", "國內案例", "結論"],
            total_word_count=7000,
            citation_style="author_year",
            special_elements=[
                {"type": "table", "target_chapter": "國內案例"},
            ],
        )
        assert "3" in line  # 3 章
        assert "前言" in line
        assert "7000" in line
        assert "對嗎" in line or "對不對" in line
        # 無內部開發術語
        for jargon in ["format_specs", "user_voice", "InitialFormatSpec",
                       "citation_style", "special_elements", "target_chapter"]:
            assert jargon not in line

    def test_partial_only_lists_present_dimensions(self):
        from reasoning.live_research.lr_copy import (
            initial_format_confirmation_line,
        )
        line = initial_format_confirmation_line(
            chapter_names=[],
            total_word_count=5000,
            citation_style=None,
            special_elements=[],
        )
        assert "5000" in line
        # 沒章節 / 沒引用格式 / 沒特殊元素 → 不出現對應字眼
        assert "章" not in line.replace("文章", "")  # 寬鬆：沒章節維度


from unittest.mock import patch


def _make_min_context_map():
    """最小可用 ContextMap（_context_map_to_outline 只需 topics 可迭代）。
    優先對齊 test_live_orchestrator.py 既有 helper；下方為含當前必填欄位的 fallback。"""
    from reasoning.schemas_live import ContextMap, ContextMapTopic
    return ContextMap(
        research_question="台灣綠能",
        topics=[ContextMapTopic(
            topic_id="t1", name="土地使用", domain="能源",
            relevance="core", evidence_ids=[],
        )],
    )


class TestRunStage1Wiring:
    @pytest.mark.asyncio
    async def test_stage1_extracts_and_merges_confirmation(self, monkeypatch):
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        from reasoning.live_research.stage_state import LiveResearchStageState

        # AR round 2 SF2：monkeypatch 強制 live_research_dry_run=False，
        # 不依賴全域 config 現值（防 config 漂移 silent skip 抽取整段）。
        import reasoning.live_research.orchestrator as _orch_mod
        monkeypatch.setattr(_orch_mod, "live_research_dry_run", False, raising=False)

        handler = MagicMock()
        # 路 A merge 後 _persist_progress 為 async 會 `await self.handler._save_state(state)`，須補 AsyncMock。
        handler._save_state = AsyncMock()
        orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
        assert orch.dry_run is False, "monkeypatch 後 dry_run 仍為 True — 實作可能讀不同 config 路徑，執行者需追查"
        orch._emit_stage_change = AsyncMock()
        orch._emit_narration = AsyncMock()
        orch._emit_checkpoint = AsyncMock()

        spec = InitialFormatSpec(
            chapters=[InitialChapterSpec(name="前言"), InitialChapterSpec(name="結論")],
            total_word_count=7000,
        )
        orch.associator.extract_initial_format_spec = AsyncMock(return_value=spec)

        cm = _make_min_context_map()
        mock_engine = MagicMock()
        mock_engine.run_loop = AsyncMock(return_value=cm)
        mock_engine.initial_context_map = cm
        mock_engine.executed_searches = []
        mock_engine.evidence_pool = {}

        state = LiveResearchStageState()
        with patch(
            "reasoning.live_research.orchestrator.BABLoopEngine",
            return_value=mock_engine,
        ):
            state = await orch._run_stage_1(state, query="幫我寫前言和結論兩章，共 7000 字")

        # 抽取被呼叫
        orch.associator.extract_initial_format_spec.assert_awaited_once()
        # 落庫斷言
        assert [c["name"] for c in state.format_specs["chapters"]] == ["前言", "結論"]
        assert state.user_voice.target_word_count == 7000
        assert state.format_specs["target_word_count"] == 7000
        # proposal 含確認句
        orch._emit_checkpoint.assert_awaited_once()
        sent_proposal = orch._emit_checkpoint.await_args.kwargs["proposal"]
        assert "這樣對嗎" in sent_proposal
        assert "前言" in sent_proposal

    @pytest.mark.asyncio
    async def test_stage1_no_spec_proposal_unchanged(self, monkeypatch):
        """空 spec（has_meaningful_spec False）→ proposal 不附確認句、不落庫。"""
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        from reasoning.live_research.stage_state import LiveResearchStageState

        # AR round 2 SF2：monkeypatch 強制 live_research_dry_run=False
        import reasoning.live_research.orchestrator as _orch_mod
        monkeypatch.setattr(_orch_mod, "live_research_dry_run", False, raising=False)

        handler = MagicMock()
        # 路 A merge 後 _persist_progress 為 async 會 `await self.handler._save_state(state)`，須補 AsyncMock。
        handler._save_state = AsyncMock()
        orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
        assert orch.dry_run is False
        orch._emit_stage_change = AsyncMock()
        orch._emit_narration = AsyncMock()
        orch._emit_checkpoint = AsyncMock()
        orch.associator.extract_initial_format_spec = AsyncMock(
            return_value=InitialFormatSpec()
        )

        cm = _make_min_context_map()
        mock_engine = MagicMock()
        mock_engine.run_loop = AsyncMock(return_value=cm)
        mock_engine.initial_context_map = cm
        mock_engine.executed_searches = []
        mock_engine.evidence_pool = {}

        state = LiveResearchStageState()
        with patch(
            "reasoning.live_research.orchestrator.BABLoopEngine",
            return_value=mock_engine,
        ):
            state = await orch._run_stage_1(state, query="台灣綠能發展現況")

        sent_proposal = orch._emit_checkpoint.await_args.kwargs["proposal"]
        assert "這樣對嗎" not in sent_proposal
        assert "chapters" not in (state.format_specs or {})
        assert state.user_voice.target_word_count is None

    @pytest.mark.asyncio
    async def test_stage1_extraction_failure_emits_narration(self, monkeypatch):
        """AR B3：抽取 LLM 拋錯 → 發 user-visible 旁白（no-silent-fail）、proposal 不變、不落庫。"""
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        from reasoning.live_research.stage_state import LiveResearchStageState
        from reasoning.live_research import lr_copy

        # AR round 2 SF2：monkeypatch 強制 live_research_dry_run=False
        import reasoning.live_research.orchestrator as _orch_mod
        monkeypatch.setattr(_orch_mod, "live_research_dry_run", False, raising=False)

        handler = MagicMock()
        # 路 A merge 後 _persist_progress 為 async 會 `await self.handler._save_state(state)`，須補 AsyncMock。
        handler._save_state = AsyncMock()
        orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
        assert orch.dry_run is False
        orch._emit_stage_change = AsyncMock()
        orch._emit_narration = AsyncMock()
        orch._emit_checkpoint = AsyncMock()
        orch.associator.extract_initial_format_spec = AsyncMock(
            side_effect=RuntimeError("LLM down")
        )

        cm = _make_min_context_map()
        mock_engine = MagicMock()
        mock_engine.run_loop = AsyncMock(return_value=cm)
        mock_engine.initial_context_map = cm
        mock_engine.executed_searches = []
        mock_engine.evidence_pool = {}

        state = LiveResearchStageState()
        with patch(
            "reasoning.live_research.orchestrator.BABLoopEngine",
            return_value=mock_engine,
        ):
            state = await orch._run_stage_1(state, query="五章 7000 字")

        # 故障旁白被發出（非 silent）
        orch._emit_narration.assert_any_await(
            lr_copy.INITIAL_FORMAT_EXTRACTION_FAILED_NARRATION
        )
        # 降級：proposal 維持原樣（無確認句）、不落庫
        sent_proposal = orch._emit_checkpoint.await_args.kwargs["proposal"]
        assert "這樣對嗎" not in sent_proposal
        assert "chapters" not in (state.format_specs or {})

    @pytest.mark.asyncio
    async def test_stage1_dry_run_skips_extraction(self):
        """SF2：dry_run=True → 不呼叫抽取 LLM、不 mutate state、proposal 無確認句。"""
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        from reasoning.live_research.stage_state import LiveResearchStageState

        handler = MagicMock()
        # 路 A merge 後 _persist_progress 為 async 會 `await self.handler._save_state(state)`，須補 AsyncMock。
        handler._save_state = AsyncMock()
        # dry_run=True → _setup_dry_run_agents（associator=MagicMock），且 Task 6 guard skip 抽取
        orch = LiveResearchOrchestrator(handler=handler, dry_run=True)
        assert orch.dry_run is True
        orch._emit_stage_change = AsyncMock()
        orch._emit_narration = AsyncMock()
        orch._emit_checkpoint = AsyncMock()
        # 若抽取被誤呼叫 → 拋錯讓 test fail
        orch.associator.extract_initial_format_spec = AsyncMock(
            side_effect=AssertionError("dry_run 不該跑抽取")
        )

        cm = _make_min_context_map()
        mock_engine = MagicMock()
        mock_engine.run_loop = AsyncMock(return_value=cm)
        mock_engine.initial_context_map = cm
        mock_engine.executed_searches = []
        mock_engine.evidence_pool = {}

        state = LiveResearchStageState()
        with patch(
            "reasoning.live_research.orchestrator.BABLoopEngine",
            return_value=mock_engine,
        ):
            state = await orch._run_stage_1(state, query="五章 7000 字")

        orch.associator.extract_initial_format_spec.assert_not_awaited()
        sent_proposal = orch._emit_checkpoint.await_args.kwargs["proposal"]
        assert "這樣對嗎" not in sent_proposal
        assert "chapters" not in (state.format_specs or {})


class TestOverrideOrdering:
    def test_later_reframe_overrides_initial_word_count(self, monkeypatch):
        """初始抽 7000 字 → 後續 reframe 句改 5000 → 5000 勝（後寫覆蓋）。"""
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator
        from reasoning.live_research.stage_state import LiveResearchStageState
        from reasoning.schemas_live import Stage1ParsedIntent

        # AR round 2 SF3：patch AssociatorAgent 防意外真 client 初始化
        import reasoning.agents.associator as _assoc_mod
        monkeypatch.setattr(
            _assoc_mod, "AssociatorAgent", MagicMock(return_value=MagicMock()),
            raising=False,
        )
        handler = MagicMock()
        orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
        state = LiveResearchStageState()

        # 初始抽取落庫
        orch._apply_initial_format_spec(
            state, InitialFormatSpec(total_word_count=7000)
        )
        assert state.user_voice.target_word_count == 7000

        # 後續 user reply reframe 句帶 total_word_count=5000（既有路徑）
        intent = Stage1ParsedIntent(action="adjust", total_word_count=5000)
        orch._apply_stage1_format_prefs(state, intent)

        assert state.user_voice.target_word_count == 5000
        assert state.format_specs["target_word_count"] == 5000

    def test_later_reframe_overrides_initial_chapters(self, monkeypatch):
        """AR SF4：初始抽 5 章 → 後續 reframe 改 3 章 → format_specs["chapters"] 被替換。

        chapters 覆蓋走的是 reframe confirm 後 _extract_chapters_from_ops 寫點
        （orchestrator.py:1428-1436），與 word_count 覆蓋（_apply_stage1_format_prefs）
        不同 code path，需獨立覆蓋。本 test 直接驗「後寫 chapters 替換前寫」語意：
        初始抽取寫 5 章 → 模擬 reframe 抽出 3 章 → format_specs["chapters"] = 3 章。
        """
        from reasoning.live_research.orchestrator import (
            LiveResearchOrchestrator,
            _extract_chapters_from_ops,
        )
        from reasoning.live_research.stage_state import LiveResearchStageState

        # AR round 2 SF3：patch AssociatorAgent 防意外真 client 初始化
        import reasoning.agents.associator as _assoc_mod
        monkeypatch.setattr(
            _assoc_mod, "AssociatorAgent", MagicMock(return_value=MagicMock()),
            raising=False,
        )
        handler = MagicMock()
        orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
        state = LiveResearchStageState()

        # 初始抽取落 5 章
        orch._apply_initial_format_spec(
            state,
            InitialFormatSpec(
                chapters=[InitialChapterSpec(name=f"第{i}章") for i in range(1, 6)]
            ),
        )
        assert len(state.format_specs["chapters"]) == 5

        # 後續 reframe：模擬一個帶 new_chapters（3 章）的 op，走既有 confirm 後寫點。
        reframe_op = MagicMock()
        reframe_op.new_chapters = [
            {"name": "甲"}, {"name": "乙"}, {"name": "丙"},
        ]
        extracted = _extract_chapters_from_ops([reframe_op])
        assert len(extracted) == 3
        # 既有 reframe confirm 寫法（:1428-1436）：非空則替換
        if extracted:
            state.format_specs["chapters"] = extracted

        # 後寫覆蓋：3 章勝
        assert [c["name"] for c in state.format_specs["chapters"]] == ["甲", "乙", "丙"]
