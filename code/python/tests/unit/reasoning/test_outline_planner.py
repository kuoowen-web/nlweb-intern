"""Tests for OutlinePlannerAgent — Plan 4 Phase 2 / Phase 4.

Phase 2: outline planner LLM call 在 Stage 5 進場時呼叫，產出 BookOutline 存 state。
Phase 4: LLM call 失敗時走 skeleton fallback + narration。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from reasoning.schemas_live import (
    BookOutline,
    ChapterPlan,
    ContextMap,
    ContextMapTopic,
    StyleAnalysisOutput,
    StyleFeature,
)


async def _drive_stage_5_to_completion(orch, state, max_iterations: int = 20):
    """VP-7 helper：single-step `_run_stage_5` 反覆呼叫直到完成。

    Plan 4 既有測試是 batch-loop based（一次寫多段），VP-7 反轉後改 single-step，
    為了避免大規模改寫測試，提供 driver 模擬 user 連續 continue。
    """
    from reasoning.schemas_live import ContextMap as _CM
    cm = _CM.model_validate_json(state.context_map_json)
    writer_sections, _ = orch._resolve_chapter_source(cm, state.format_specs)
    total = len(writer_sections)
    for _ in range(max_iterations):
        state = await orch._run_stage_5(state)
        if state.last_completed_section_index >= total - 1:
            return state
    raise AssertionError(
        f"VP-7 driver exceeded {max_iterations} iterations"
    )


def _make_cm(n_core: int = 3):
    """Build ContextMap with N core topics + evidence_ids 1..N*2。"""
    return ContextMap(
        research_question="Q",
        topics=[
            ContextMapTopic(
                topic_id=f"t{i}",
                name=f"topic-{i}",
                domain="能源",
                relevance="core",
                description=f"desc-{i}",
                evidence_ids=[i * 2 + 1, i * 2 + 2],
            )
            for i in range(n_core)
        ],
        version=1,
    )


def _make_style_features():
    return StyleAnalysisOutput(
        features=[
            StyleFeature(dimension="句式", observation="長句多", instruction="保持長句節奏"),
            StyleFeature(dimension="用詞", observation="學術", instruction="正式用語"),
            StyleFeature(dimension="段落", observation="緊湊", instruction="緊密邏輯"),
        ],
        overall_tone="學術嚴謹",
        sample_quality_note="",
        citation_format="numeric",
    )


# ============================================================================
# Phase 2: OutlinePlannerAgent.plan_outline
# ============================================================================

class TestOutlinePlannerAgent:
    """Plan 4 Phase 2: outline planner LLM call 介面 + 對齊 chapter_source。"""

    @pytest.fixture
    def mock_handler(self):
        h = MagicMock()
        h.query = "Q"
        h.query_params = {}
        h.message_sender = MagicMock()
        h.message_sender.send_message = AsyncMock()
        return h

    @pytest.mark.asyncio
    async def test_outline_planner_produces_n_chapters_aligned_with_chapter_source(
        self, mock_handler
    ):
        """chapter_source 5 章 → outline planner 產 5 章 BookOutline，title 對齊。"""
        from reasoning.agents.outline_planner import OutlinePlannerAgent

        cm = _make_cm(n_core=3)
        chapter_source = [
            {"name": "前言", "outline": "研究動機"},
            {"name": "國內案例", "outline": "台灣"},
            {"name": "國外案例", "outline": "他國"},
            {"name": "結果與討論", "outline": "綜合"},
            {"name": "結論", "outline": "policy"},
        ]
        format_specs = {
            "user_specified": "五章學術結構，APA 引用",
            "chapters": chapter_source,
        }

        # Mock ask_llm 回傳合法 BookOutline JSON
        expected_outline = BookOutline(
            chapters=[
                ChapterPlan(
                    chapter_index=0, title="前言", brief="鋪陳動機與目的",
                    target_word_count=500, planned_evidence_ids=[1],
                    transition_hint="", role="intro",
                ),
                ChapterPlan(
                    chapter_index=1, title="國內案例", brief="台灣案例聚焦",
                    target_word_count=1500, planned_evidence_ids=[1, 2, 3],
                    transition_hint="承接前言研究動機", role="body",
                ),
                ChapterPlan(
                    chapter_index=2, title="國外案例", brief="他國對照",
                    target_word_count=1500, planned_evidence_ids=[3, 4],
                    transition_hint="承接國內", role="body",
                ),
                ChapterPlan(
                    chapter_index=3, title="結果與討論", brief="綜合分析",
                    target_word_count=1000, planned_evidence_ids=[5, 6],
                    transition_hint="承接案例", role="body",
                ),
                ChapterPlan(
                    chapter_index=4, title="結論", brief="收尾與展望",
                    target_word_count=500, planned_evidence_ids=[],
                    transition_hint="收尾全文", role="conclusion",
                ),
            ],
            overall_arc="動機 → 案例 → 討論 → 結論",
            redundancy_warnings=[],
        )

        from reasoning.agents.outline_planner import OutlinePlannerAgent

        agent = OutlinePlannerAgent(mock_handler)
        with patch(
            "reasoning.agents.outline_planner.ask_llm",
            new=AsyncMock(return_value=expected_outline.model_dump()),
        ):
            outline = await agent.plan_outline(
                chapter_source=chapter_source,
                context_map=cm,
                format_specs=format_specs,
                style_features=_make_style_features(),
            )

        assert isinstance(outline, BookOutline)
        assert len(outline.chapters) == 5
        assert [c.title for c in outline.chapters] == [
            "前言", "國內案例", "國外案例", "結果與討論", "結論",
        ]
        assert outline.chapters[0].role == "intro"
        assert outline.chapters[-1].role == "conclusion"

    @pytest.mark.asyncio
    async def test_outline_planner_uses_level_low_and_max_length_4096(self, mock_handler):
        """CEO 拍板：level=low（節省成本）、max_length=4096（避免 Plan 1 truncation 地雷）。"""
        from reasoning.agents.outline_planner import OutlinePlannerAgent

        cm = _make_cm(n_core=2)
        chapter_source = [
            {"name": "A", "outline": "a"},
            {"name": "B", "outline": "b"},
        ]

        # Track ask_llm kwargs
        captured: dict = {}

        async def fake_ask(*args, **kwargs):
            captured.update(kwargs)
            return BookOutline(
                chapters=[
                    ChapterPlan(chapter_index=0, title="A", brief="a", role="intro"),
                    ChapterPlan(chapter_index=1, title="B", brief="b", role="conclusion"),
                ],
                overall_arc="A → B",
            ).model_dump()

        agent = OutlinePlannerAgent(mock_handler)
        with patch("reasoning.agents.outline_planner.ask_llm", new=fake_ask):
            await agent.plan_outline(
                chapter_source=chapter_source,
                context_map=cm,
                format_specs={},
                style_features=None,
            )

        assert captured.get("level") == "low"
        assert captured.get("max_length") == 4096


# ============================================================================
# Phase 2: _run_stage_5 — outline planner integration
# ============================================================================

class TestStage5OutlinePlannerIntegration:
    """Plan 4 Phase 2: _run_stage_5 進場呼叫 outline planner、寫入 state。"""

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
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator

        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=mock_handler, dry_run=True)

    @pytest.mark.asyncio
    async def test_run_stage_5_writes_book_outline_to_state_when_empty(
        self, orch, mock_handler
    ):
        """state.book_outline_json 為空 → outline planner 跑、寫入 state；非空 → skip 重 plan。"""
        from reasoning.live_research.stage_state import LiveResearchStageState

        cm = _make_cm(n_core=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
        )

        result = await orch._run_stage_5(state)

        # state.book_outline_json 應非空（dry_run 走 skeleton fallback 也算寫入）
        assert result.book_outline_json != ""
        outline = BookOutline.model_validate_json(result.book_outline_json)
        assert len(outline.chapters) == 3  # core_topics fallback

    @pytest.mark.asyncio
    async def test_run_stage_5_passes_outline_and_prev_summary_to_write_section(
        self, orch, mock_handler
    ):
        """Plan 4 Phase 3: _run_stage_5 把 book_outline + current_chapter_index +
        previous_chapter_summary 傳給 _write_section；每章累積 prev_summary。"""
        from reasoning.live_research.stage_state import LiveResearchStageState
        from reasoning.schemas_live import LiveWriterSectionOutput

        cm = _make_cm(n_core=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
        )

        captured: list = []

        async def fake_write(context_map, topic, style_features, format_specs, evidence_pool=None, **kw):
            captured.append({
                "name": topic["name"] if isinstance(topic, dict) else topic.name,
                "current_chapter_index": kw.get("current_chapter_index"),
                "previous_chapter_summary": kw.get("previous_chapter_summary"),
                "book_outline_present": kw.get("book_outline") is not None,
            })
            return (
                LiveWriterSectionOutput(
                    section_title=topic["name"] if isinstance(topic, dict) else topic.name,
                    section_content="content",
                    sources_used=[],
                    confidence_level="Medium",
                    narration="",
                    chapter_summary=f"摘要-{len(captured)}",
                ),
                False,
            )

        orch._write_section = fake_write

        await _drive_stage_5_to_completion(orch, state)

        assert len(captured) == 3
        # 三章都有 book_outline
        assert all(c["book_outline_present"] for c in captured)
        # current_chapter_index 對齊 0/1/2
        assert [c["current_chapter_index"] for c in captured] == [0, 1, 2]
        # prev_summary 累積：第 0 章空、第 1 章拿前章 chapter_summary "摘要-1"、第 2 章拿 "摘要-2"
        assert captured[0]["previous_chapter_summary"] == ""
        assert captured[1]["previous_chapter_summary"] == "摘要-1"
        assert captured[2]["previous_chapter_summary"] == "摘要-2"

    @pytest.mark.asyncio
    async def test_run_stage_5_persists_chapter_summary_into_written_sections(
        self, orch, mock_handler
    ):
        """written_sections[i] 應含 chapter_summary 欄位（供 resume 復原 prev_summary）。"""
        from reasoning.live_research.stage_state import LiveResearchStageState
        from reasoning.schemas_live import LiveWriterSectionOutput

        cm = _make_cm(n_core=2)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
        )

        async def fake_write(context_map, topic, style_features, format_specs, evidence_pool=None, **kw):
            return (
                LiveWriterSectionOutput(
                    section_title=topic.name,
                    section_content="content",
                    sources_used=[],
                    confidence_level="Medium",
                    narration="",
                    chapter_summary=f"摘要-{topic.name}",
                ),
                False,
            )

        orch._write_section = fake_write

        result = await _drive_stage_5_to_completion(orch, state)

        assert len(result.written_sections) == 2
        assert result.written_sections[0]["chapter_summary"] == "摘要-topic-0"
        assert result.written_sections[1]["chapter_summary"] == "摘要-topic-1"

    @pytest.mark.asyncio
    async def test_run_stage_5_resume_recovers_prev_summary_from_last_written_section(
        self, orch, mock_handler
    ):
        """Resume 路徑：state.written_sections[-1].chapter_summary 復原成 next chapter 的 prev_summary。
        舊 row（沒 chapter_summary key）→ .get(..., "") fallback 空字串。"""
        from reasoning.live_research.stage_state import LiveResearchStageState
        from reasoning.schemas_live import LiveWriterSectionOutput

        cm = _make_cm(n_core=3)
        # Resume: 已寫完第 0 段，state.last_completed_section_index=0
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
            written_sections=[
                {
                    "section_index": 0,
                    "title": "topic-0",
                    "content": "old content",
                    "sources_used": [],
                    "confidence_level": "Medium",
                    "chapter_summary": "舊摘要-0",  # 已存
                },
            ],
            last_completed_section_index=0,
        )

        captured: list = []

        async def fake_write(context_map, topic, style_features, format_specs, evidence_pool=None, **kw):
            captured.append({
                "name": topic.name,
                "previous_chapter_summary": kw.get("previous_chapter_summary"),
            })
            return (
                LiveWriterSectionOutput(
                    section_title=topic.name,
                    section_content="content",
                    sources_used=[],
                    confidence_level="Medium",
                    narration="",
                    chapter_summary=f"new-{topic.name}",
                ),
                False,
            )

        orch._write_section = fake_write

        await _drive_stage_5_to_completion(orch, state)

        # Resume skip 第 0 段、跑第 1+2 段
        assert len(captured) == 2
        # 第 1 段（resume 後第一段）prev_summary 應從 written_sections[-1].chapter_summary 復原
        assert captured[0]["previous_chapter_summary"] == "舊摘要-0"
        # 第 2 段拿前章新寫的 chapter_summary
        assert captured[1]["previous_chapter_summary"] == "new-topic-1"

    @pytest.mark.asyncio
    async def test_run_stage_5_resume_recovers_prev_summary_backward_compat(
        self, orch, mock_handler
    ):
        """Resume backward compat：舊 written_sections row 沒 chapter_summary → 空字串。"""
        from reasoning.live_research.stage_state import LiveResearchStageState
        from reasoning.schemas_live import LiveWriterSectionOutput

        cm = _make_cm(n_core=2)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
            written_sections=[
                {
                    "section_index": 0,
                    "title": "topic-0",
                    "content": "old",
                    "sources_used": [],
                    "confidence_level": "Medium",
                    # 注意：沒 chapter_summary key（Plan 4 Phase 1 之前的 session）
                },
            ],
            last_completed_section_index=0,
        )

        captured: list = []

        async def fake_write(context_map, topic, style_features, format_specs, evidence_pool=None, **kw):
            captured.append(kw.get("previous_chapter_summary"))
            return (
                LiveWriterSectionOutput(
                    section_title=topic.name,
                    section_content="...",
                    sources_used=[],
                    confidence_level="Medium",
                    chapter_summary="new",
                ),
                False,
            )

        orch._write_section = fake_write

        await orch._run_stage_5(state)

        # 第 1 段 resume 後拿 prev — 舊 row 沒 chapter_summary，fallback ""
        assert captured == [""]

    @pytest.mark.asyncio
    async def test_run_stage_5_skips_plan_when_outline_already_cached(
        self, orch, mock_handler
    ):
        """state.book_outline_json 已存在且與 writer_sections 對齊 → skip planner call
        （resume 路徑不重 plan）。"""
        from reasoning.live_research.stage_state import LiveResearchStageState

        # cm 兩個 core topics 名為 topic-0 / topic-1 (見 _make_cm)
        cm = _make_cm(n_core=2)
        # 預先 seed outline — title 對齊 cm.topics
        prebuilt = BookOutline(
            chapters=[
                ChapterPlan(chapter_index=0, title="topic-0", brief="a", role="intro"),
                ChapterPlan(chapter_index=1, title="topic-1", brief="b", role="conclusion"),
            ],
            overall_arc="A→B",
        )
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
            book_outline_json=prebuilt.model_dump_json(),
        )

        result = await orch._run_stage_5(state)

        # outline 未被 overwrite — 仍是預先 seed 的 2 章
        outline = BookOutline.model_validate_json(result.book_outline_json)
        assert len(outline.chapters) == 2
        assert outline.chapters[0].title == "topic-0"

    @pytest.mark.asyncio
    async def test_run_stage_5_reframes_stale_outline_when_chapter_count_mismatches(
        self, orch, mock_handler
    ):
        """R4 staleness fix（RCA v3 ROOT 4）：cached outline 章節數跟當前 writer_sections
        不對齊 → invalidate cache 重 plan，並 emit 明示 narration（過期重 plan）。"""
        from reasoning.live_research.stage_state import LiveResearchStageState

        # 當前 cm 3 章
        cm = _make_cm(n_core=3)
        # 殘留 outline 只有 5 章（舊 reframe 殘留）— 故意構造不合法 (role=body at idx 0)
        # 來模擬 stale / corrupted DB state；用 model_construct 繞 Track A schema
        # validator (Gemini C-2)，因為本 test 的 intent 就是「stale outline 即使
        # 不合法，orchestrator 也應該 invalidate + 重 plan，不可 silent crash」。
        stale_outline = BookOutline.model_construct(
            chapters=[
                ChapterPlan.model_construct(
                    chapter_index=i, title=f"舊章-{i}",
                    brief=f"舊 brief {i}", role="body",
                    target_word_count=0, planned_evidence_ids=[],
                    transition_hint="",
                )
                for i in range(5)
            ],
            overall_arc="舊規劃 5 章",
            redundancy_warnings=[],
        )
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
            book_outline_json=stale_outline.model_dump_json(),
        )

        result = await orch._run_stage_5(state)

        # 1) outline 應被重 plan → 章節數對齊新 writer_sections (3)
        outline = BookOutline.model_validate_json(result.book_outline_json)
        assert len(outline.chapters) == 3, (
            f"Stale outline 應 invalidate 重 plan 對齊 writer_sections=3，"
            f"實際 chapters={len(outline.chapters)}"
        )
        # 2) 不應仍是「舊章-」名（應是新 cm.topics 名稱）
        assert outline.chapters[0].title != "舊章-0", (
            "Stale outline 殘留章節名沒被替換"
        )

        # 3) 必須 emit 明示「過期重 plan」narration（不可 silent invalidate）
        narrations = [
            c.args[0].get("text", "")
            for c in mock_handler.message_sender.send_message.call_args_list
            if c.args[0].get("message_type") == "live_research_narration"
        ]
        assert any(
            ("過期" in n or "不對齊" in n or "重新規劃" in n or "重 plan" in n)
            for n in narrations
        ), (
            f"Staleness invalidate 必須 emit user-visible narration，但 narrations={narrations}"
        )

    @pytest.mark.asyncio
    async def test_run_stage_5_reframes_stale_outline_when_titles_mismatch(
        self, orch, mock_handler
    ):
        """R4 staleness fix：章節數相同但 titles 不對齊 → 同樣 invalidate 重 plan。"""
        from reasoning.live_research.stage_state import LiveResearchStageState

        cm = _make_cm(n_core=3)
        # 章節數一樣 (3) 但 title 跟 cm.topics (topic-0/1/2) 完全不對
        stale_outline = BookOutline(
            chapters=[
                ChapterPlan(chapter_index=0, title="完全不同的章 A", brief="x", role="intro"),
                ChapterPlan(chapter_index=1, title="完全不同的章 B", brief="x", role="body"),
                ChapterPlan(chapter_index=2, title="完全不同的章 C", brief="x", role="conclusion"),
            ],
            overall_arc="x",
        )
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
            book_outline_json=stale_outline.model_dump_json(),
        )

        result = await orch._run_stage_5(state)

        outline = BookOutline.model_validate_json(result.book_outline_json)
        # 章節已被替換成 cm.topics 名（skeleton fallback 走 topic.name）
        titles = [c.title for c in outline.chapters]
        assert "完全不同的章 A" not in titles, (
            f"Title-mismatch 殘留 outline 應重 plan，但實際 titles={titles}"
        )


# ============================================================================
# Phase 4: Skeleton fallback when outline planner LLM fails
# ============================================================================

class TestOutlinePlannerSkeletonFallback:
    """Plan 4 Phase 4: LLM call raise → build_skeleton_outline + 明示 narration。"""

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
    def orch_real(self, mock_handler):
        """非 dry_run orchestrator（要走真實 LLM 路徑才能觸發 fallback path）。"""
        from reasoning.live_research.orchestrator import LiveResearchOrchestrator

        with patch("reasoning.live_research.orchestrator.AssociatorAgent"):
            return LiveResearchOrchestrator(handler=mock_handler, dry_run=False)

    @pytest.mark.asyncio
    async def test_run_stage_5_outline_planner_fail_falls_back_to_skeleton(
        self, orch_real, mock_handler
    ):
        """LLM call raise → skeleton fallback + role intro/body/conclusion 自動標記 +
        narration 明示「降級」字樣。"""
        from reasoning.live_research.stage_state import LiveResearchStageState
        from reasoning.schemas_live import LiveWriterSectionOutput

        cm = _make_cm(n_core=3)
        state = LiveResearchStageState(
            current_stage=5,
            stage_status="in_progress",
            context_map_json=cm.model_dump_json(),
        )

        # Mock OutlinePlannerAgent.plan_outline raise TimeoutError
        from reasoning.agents import outline_planner as op_mod

        async def boom(*a, **kw):
            raise TimeoutError("LLM timed out")

        # Mock _write_section 立即 return（避免跑 writer real path）
        async def fake_write(context_map, topic, style_features, format_specs, evidence_pool=None, **kw):
            return (
                LiveWriterSectionOutput(
                    section_title=topic.name,
                    section_content="...",
                    sources_used=[],
                    confidence_level="Medium",
                    chapter_summary="x",
                ),
                False,
            )

        orch_real._write_section = fake_write

        with patch.object(op_mod.OutlinePlannerAgent, "plan_outline", new=boom):
            result = await orch_real._run_stage_5(state)

        # 1) state.book_outline_json 非空 — skeleton outline 已寫入
        assert result.book_outline_json != ""
        outline = BookOutline.model_validate_json(result.book_outline_json)
        # 2) Skeleton 對齊 core_topics (3 章)
        assert len(outline.chapters) == 3
        # 3) role 自動標記
        assert outline.chapters[0].role == "intro"
        assert outline.chapters[1].role == "body"
        assert outline.chapters[-1].role == "conclusion"
        # 4) overall_arc 明示 skeleton fallback
        assert "skeleton" in outline.overall_arc.lower() or "fallback" in outline.overall_arc.lower()

        # 5) narration 應含「降級」字樣（user-visible，不可 silent fail）
        narrations = [
            c.args[0].get("text", "")
            for c in mock_handler.message_sender.send_message.call_args_list
            if c.args[0].get("message_type") == "live_research_narration"
        ]
        assert any("降級" in n for n in narrations), (
            f"narration 應含『降級』，但 narrations={narrations}"
        )

    def test_build_skeleton_outline_override_mode_union_to_first_chapter(self):
        """Override 模式：第 0 章拿 union evidence_ids（沿用 Plan 2 union-to-first 邏輯
        作為 skeleton fallback 行為），其餘空。"""
        from reasoning.agents.outline_planner import build_skeleton_outline

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
        chapters = [
            {"name": "前言", "outline": "intro"},
            {"name": "本論", "outline": "body"},
            {"name": "結論", "outline": "concl"},
        ]
        outline = build_skeleton_outline(chapters, cm, {})
        assert len(outline.chapters) == 3
        assert outline.chapters[0].planned_evidence_ids == [1, 2, 3]
        assert outline.chapters[1].planned_evidence_ids == []
        assert outline.chapters[2].planned_evidence_ids == []
        # role 自動標記
        assert outline.chapters[0].role == "intro"
        assert outline.chapters[1].role == "body"
        assert outline.chapters[2].role == "conclusion"

    def test_build_skeleton_outline_fallback_mode_each_chapter_uses_own_evidence(self):
        """Fallback 模式（ContextMapTopic source）：每章拿自己的 topic.evidence_ids。"""
        from reasoning.agents.outline_planner import build_skeleton_outline

        t1 = ContextMapTopic(
            topic_id="t1", name="議題 A", domain="d",
            relevance="core", evidence_ids=[1, 2], description="d1",
        )
        t2 = ContextMapTopic(
            topic_id="t2", name="議題 B", domain="d",
            relevance="core", evidence_ids=[3, 4], description="d2",
        )
        cm = ContextMap(research_question="Q", topics=[t1, t2], version=1)
        outline = build_skeleton_outline([t1, t2], cm, {})
        assert len(outline.chapters) == 2
        assert outline.chapters[0].planned_evidence_ids == [1, 2]
        assert outline.chapters[1].planned_evidence_ids == [3, 4]
        assert outline.chapters[0].brief == "d1"
        assert outline.chapters[1].role == "conclusion"

    def test_build_skeleton_outline_single_chapter_role_intro(self):
        """單章邊緣 case：role=intro（避免 N=1 時 intro+conclusion 同章衝突）。"""
        from reasoning.agents.outline_planner import build_skeleton_outline

        cm = ContextMap(research_question="Q", topics=[], version=1)
        chapters = [{"name": "唯一章", "outline": "all"}]
        outline = build_skeleton_outline(chapters, cm, {})
        assert len(outline.chapters) == 1
        assert outline.chapters[0].role == "intro"


# ============================================================================
# Track A (LR DR-parity sprint 2026-05-28) — outline planner per-chapter
# evidence assignment + skeleton fallback keyword match + Gemini C-2 role/index
# consistency validator
# ============================================================================


class TestTrackASkeletonOutlineKeywordMatch:
    """Track A Task 2: skeleton fallback per-chapter keyword match (取代 union-to-first)。"""

    def test_build_skeleton_outline_per_chapter_keyword_match(self):
        """skeleton fallback：override 模式不再 union-to-first，按 chapter brief 關鍵字
        匹配 evidence_pool title/snippet 分配 evidence_ids。"""
        from reasoning.agents.outline_planner import build_skeleton_outline
        from reasoning.schemas_live import EvidencePoolEntry

        chapter_source = [
            {"name": "前言", "outline": "動機與目的"},
            {"name": "國內案例", "outline": "台灣再生能源"},
            {"name": "國外案例", "outline": "丹麥離岸風電"},
            {"name": "結論", "outline": "綜合"},
        ]
        cm = ContextMap(
            research_question="re",
            topics=[
                ContextMapTopic(topic_id="t1", name="台灣綠能", domain="能源",
                                relevance="core", description="x",
                                evidence_ids=[1, 2, 3]),
                ContextMapTopic(topic_id="t2", name="丹麥風電", domain="能源",
                                relevance="core", description="y",
                                evidence_ids=[4]),
            ],
            version=1,
        )
        evidence_pool = {
            1: EvidencePoolEntry(evidence_id=1, title="台灣光電進度", url="u",
                                 snippet="台灣再生能源占比上升"),
            2: EvidencePoolEntry(evidence_id=2, title="台電報告", url="u",
                                 snippet="台灣綠能政策"),
            3: EvidencePoolEntry(evidence_id=3, title="工研院綠能", url="u",
                                 snippet="台灣再生能源"),
            4: EvidencePoolEntry(evidence_id=4, title="丹麥離岸風電", url="u",
                                 snippet="丹麥案例研究"),
        }

        outline = build_skeleton_outline(
            chapter_source=chapter_source,
            context_map=cm,
            format_specs={},
            evidence_pool=evidence_pool,
        )

        # 國內案例章應拿到 1/2/3（台灣 keyword 命中），國外案例章應拿到 4
        domestic_ids = set(outline.chapters[1].planned_evidence_ids)
        foreign_ids = set(outline.chapters[2].planned_evidence_ids)
        assert 1 in domestic_ids or 2 in domestic_ids or 3 in domestic_ids
        assert 4 in foreign_ids
        # body 章不應為空（key 紀律）
        assert outline.chapters[1].planned_evidence_ids != []
        assert outline.chapters[2].planned_evidence_ids != []

    def test_build_skeleton_outline_backward_compat_no_evidence_pool(self):
        """evidence_pool=None / 不傳 → 沿舊行為（union-to-first），不破壞既有 caller。"""
        from reasoning.agents.outline_planner import build_skeleton_outline

        cm = ContextMap(
            research_question="q",
            topics=[
                ContextMapTopic(topic_id="t", name="n", domain="d",
                                relevance="core", description="d", evidence_ids=[1]),
            ],
            version=0,
        )
        chapter_source = [{"name": "前言", "outline": "x"}]
        # 不傳 evidence_pool — 應 fallback 沿舊行為
        outline = build_skeleton_outline(
            chapter_source=chapter_source,
            context_map=cm,
            format_specs={},
        )
        assert outline.chapters[0].title == "前言"
        # 第 0 章拿 union (sole chapter, 拿到 [1])
        assert 1 in outline.chapters[0].planned_evidence_ids

    def test_build_skeleton_outline_english_chapter_keyword_match(self):
        """addendum Mn-2: CJK-only regex 對英文章節失效 → 加英文 token 後英文章節也命中。"""
        from reasoning.agents.outline_planner import build_skeleton_outline
        from reasoning.schemas_live import EvidencePoolEntry

        chapter_source = [
            {"name": "Intro", "outline": "background"},
            {"name": "Denmark wind power", "outline": "case study"},
        ]
        cm = ContextMap(
            research_question="q",
            topics=[ContextMapTopic(
                topic_id="t", name="Denmark", domain="energy",
                relevance="core", description="d", evidence_ids=[1, 2],
            )],
            version=0,
        )
        evidence_pool = {
            1: EvidencePoolEntry(evidence_id=1, title="denmark offshore wind",
                                 url="u", snippet="case study"),
            2: EvidencePoolEntry(evidence_id=2, title="general report",
                                 url="u", snippet="energy policy"),
        }
        outline = build_skeleton_outline(
            chapter_source=chapter_source, context_map=cm,
            format_specs={}, evidence_pool=evidence_pool,
        )
        # 第 2 章「Denmark wind power」應該命中 evidence 1（含 denmark/wind）
        assert 1 in outline.chapters[1].planned_evidence_ids


class TestTrackAOutlinePlannerPromptInjection:
    """Track A Task 2: LLM prompt 注入 evidence_pool 全文 + 紀律段。"""

    def test_outline_planner_prompt_includes_evidence_pool_titles(self):
        from reasoning.prompts.outline_planner import build_outline_planner_prompt
        from reasoning.schemas_live import EvidencePoolEntry

        cm = ContextMap(
            research_question="re",
            topics=[ContextMapTopic(topic_id="t", name="n", domain="d",
                                    relevance="core", description="d",
                                    evidence_ids=[1, 2])],
            version=0,
        )
        evidence_pool = {
            1: EvidencePoolEntry(evidence_id=1, title="DistinctTitleAAA",
                                 url="u", snippet="snippet about X"),
            2: EvidencePoolEntry(evidence_id=2, title="DistinctTitleBBB",
                                 url="u", snippet="snippet about Y"),
        }
        prompt = build_outline_planner_prompt(
            chapter_source=[{"name": "前言", "outline": "x"}],
            context_map=cm,
            format_specs={},
            style_features=None,
            evidence_pool=evidence_pool,
        )
        assert "DistinctTitleAAA" in prompt
        assert "DistinctTitleBBB" in prompt

    def test_outline_planner_prompt_includes_grounding_discipline_block(self):
        from reasoning.prompts.outline_planner import build_outline_planner_prompt

        cm = ContextMap(
            research_question="re",
            topics=[ContextMapTopic(topic_id="t", name="n", domain="d",
                                    relevance="core", description="d",
                                    evidence_ids=[1])],
            version=0,
        )
        prompt = build_outline_planner_prompt(
            chapter_source=[{"name": "前言", "outline": "x"}],
            context_map=cm, format_specs={}, style_features=None,
        )
        # body 章必須有 evidence 紀律 — 任一關鍵字命中即可
        assert (
            "body" in prompt
            or "非結論" in prompt
            or "不可為空" in prompt
            or "稀疏" in prompt
        )


class TestTrackAGeminiC2RoleIndexValidator:
    """Gemini Critical C-2 (紅隊 #2): role/index consistency schema validator。

    雙層防禦：schema validator (本 Task 2) + Task 3 runtime double-check。
    """

    def test_chapter_plan_role_body_at_index_0_raises(self):
        """LLM 把 body 章節標 intro 繞 Task 3 → schema validator 必 raise。"""
        from reasoning.schemas_live import ChapterPlan, BookOutline
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            BookOutline(
                chapters=[
                    ChapterPlan(chapter_index=0, title="本章", brief="x",
                                planned_evidence_ids=[1], role="body"),
                    ChapterPlan(chapter_index=1, title="總結", brief="y",
                                planned_evidence_ids=[2], role="conclusion"),
                ],
                overall_arc="x", redundancy_warnings=[],
            )

    def test_chapter_plan_role_intro_at_index_2_raises(self):
        """LLM 把第 3 章標 intro → 必 raise（hallucinated role 不可繞 gate）。"""
        from reasoning.schemas_live import ChapterPlan, BookOutline
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            BookOutline(
                chapters=[
                    ChapterPlan(chapter_index=0, title="前言", brief="x",
                                planned_evidence_ids=[1], role="intro"),
                    ChapterPlan(chapter_index=1, title="本章", brief="y",
                                planned_evidence_ids=[2], role="body"),
                    ChapterPlan(chapter_index=2,
                                title="本章 2 hallucinated intro",
                                brief="z", planned_evidence_ids=[],
                                role="intro"),
                ],
                overall_arc="x", redundancy_warnings=[],
            )

    def test_chapter_plan_role_conclusion_not_last_raises(self):
        from reasoning.schemas_live import ChapterPlan, BookOutline
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            BookOutline(
                chapters=[
                    ChapterPlan(chapter_index=0, title="前言", brief="x",
                                planned_evidence_ids=[1], role="intro"),
                    ChapterPlan(chapter_index=1, title="結論 not last",
                                brief="y", planned_evidence_ids=[2],
                                role="conclusion"),
                    ChapterPlan(chapter_index=2, title="本章 after conclusion",
                                brief="z", planned_evidence_ids=[3],
                                role="body"),
                ],
                overall_arc="x", redundancy_warnings=[],
            )

    def test_chapter_plan_role_consistent_passes(self):
        """正向：role 與 index 一致 → no raise。"""
        from reasoning.schemas_live import ChapterPlan, BookOutline
        o = BookOutline(
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
        assert len(o.chapters) == 3

    def test_chapter_plan_role_intro_at_index_0_passes(self):
        """正向：role=intro + chapter_index=0 + total=1 → no raise (single-chapter book)。"""
        from reasoning.schemas_live import ChapterPlan, BookOutline
        o = BookOutline(
            chapters=[
                ChapterPlan(chapter_index=0, title="唯一章", brief="x",
                            planned_evidence_ids=[1], role="intro"),
            ],
            overall_arc="x", redundancy_warnings=[],
        )
        assert len(o.chapters) == 1


class TestTrackAEvidencePoolKeysValidatorContext:
    """addendum C-4 + Track A Task 2: planned_evidence_ids ⊆ evidence_pool.keys()
    invariant 透過 Pydantic context 強制 (caller 在 model_validate 時傳
    context={evidence_pool_keys: set(pool.keys())})。"""

    def test_planned_evidence_ids_in_pool_passes(self):
        from reasoning.schemas_live import BookOutline
        raw = {
            "chapters": [
                {"chapter_index": 0, "title": "前言", "brief": "x",
                 "planned_evidence_ids": [1, 2], "role": "intro"},
                {"chapter_index": 1, "title": "結論", "brief": "y",
                 "planned_evidence_ids": [3], "role": "conclusion"},
            ],
            "overall_arc": "x", "redundancy_warnings": [],
        }
        outline = BookOutline.model_validate(
            raw,
            context={"evidence_pool_keys": {1, 2, 3}},
        )
        assert len(outline.chapters) == 2

    def test_planned_evidence_ids_not_in_pool_raises(self):
        """LLM 幻覺 ID 99 (不在 pool) → validator raise。"""
        from reasoning.schemas_live import BookOutline
        from pydantic import ValidationError
        raw = {
            "chapters": [
                {"chapter_index": 0, "title": "前言", "brief": "x",
                 "planned_evidence_ids": [1, 99], "role": "intro"},
                {"chapter_index": 1, "title": "結論", "brief": "y",
                 "planned_evidence_ids": [3], "role": "conclusion"},
            ],
            "overall_arc": "x", "redundancy_warnings": [],
        }
        with pytest.raises(ValidationError):
            BookOutline.model_validate(
                raw,
                context={"evidence_pool_keys": {1, 2, 3}},
            )

    def test_planned_evidence_ids_no_context_skips_check(self):
        """test / dry-run 沒傳 context → skip 不驗 (允許 fixture/test 隨意構造)。"""
        from reasoning.schemas_live import BookOutline
        # 沒傳 context → 不驗 evidence_pool_keys (但 role/index validator 仍跑)
        outline = BookOutline(
            chapters=[
                {"chapter_index": 0, "title": "前言", "brief": "x",
                 "planned_evidence_ids": [99, 100], "role": "intro"},
            ],
            overall_arc="x", redundancy_warnings=[],
        )
        assert outline.chapters[0].planned_evidence_ids == [99, 100]
