"""票 2026-07-28-f Fix 2：Phase 1 證據池相關性 gate 測試。

垃圾池 fixture 複刻 prod ccdb5ab6 形狀：查「邱啟新」，β-path 補回的池全是
沾邊 wiki 條目（李登輝 / 彭明敏 / 動漫），description 帶 [Tier 6 | encyclopedia] 前綴。
Mock 模式沿 test_dr_zero_results_web_search.py：MagicMock orchestrator 綁真方法，
LLM 呼叫 patch core.llm.ask_llm（helper 內局部 import，call-time lookup）。
"""

import json
import os
import sys

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from reasoning.research_state import ResearchState
from reasoning.orchestrator import DeepResearchOrchestrator


# === Fixtures ===

QUERY = "請找出台大邱啟新副教授的公開發言"


def make_garbage_wiki_pool():
    """全垃圾池：3 筆沾邊 wiki 條目（prod 事故形狀）。"""
    items = [
        {
            "url": "https://zh.wikipedia.org/wiki/李登輝",
            "title": "[維基百科] 李登輝",
            "site": "Wikipedia",
            "description": "[Tier 6 | encyclopedia] 李登輝，中華民國政治人物，曾任中華民國總統……",
            "_reasoning_metadata": {
                "tier": 6, "type": "encyclopedia",
                "original_source": "Wikipedia", "gap_query": QUERY,
            },
        },
        {
            "url": "https://zh.wikipedia.org/wiki/彭明敏",
            "title": "[維基百科] 彭明敏",
            "site": "Wikipedia",
            "description": "[Tier 6 | encyclopedia] 彭明敏，台灣政治人物與法學者……",
            "_reasoning_metadata": {
                "tier": 6, "type": "encyclopedia",
                "original_source": "Wikipedia", "gap_query": QUERY,
            },
        },
        {
            "url": "https://zh.wikipedia.org/wiki/某動漫作品",
            "title": "[維基百科] 某動漫作品",
            "site": "Wikipedia",
            "description": "[Tier 6 | encyclopedia] 日本漫畫作品，講述主角……",
            "_reasoning_metadata": {
                "tier": 6, "type": "encyclopedia",
                "original_source": "Wikipedia", "gap_query": QUERY,
            },
        },
    ]
    source_map = {i + 1: item for i, item in enumerate(items)}
    return items, source_map


def make_mixed_pool():
    """混合池：2 筆相關（1 dict + 1 站內 list-row 形狀）+ 2 筆垃圾 wiki。"""
    relevant_dict = {
        "url": "https://news.example/1",
        "title": "邱啟新談都市規劃",
        "site": "自由時報",
        "description": "台大副教授邱啟新表示，都市更新應兼顧居住正義……",
    }
    relevant_row = [
        "https://news.example/2",
        json.dumps({"description": "邱啟新出席公聽會指出，社宅政策……"}, ensure_ascii=False),
        "邱啟新出席公聽會",
        "聯合報",
    ]
    garbage1, garbage2 = make_garbage_wiki_pool()[0][:2]
    items = [relevant_dict, relevant_row, garbage1, garbage2]
    return items, {i + 1: it for i, it in enumerate(items)}


def make_state(**overrides):
    defaults = dict(
        query=QUERY,
        mode="discovery",
        items=[],
        current_context=[],
        formatted_context="",
        source_map={},
        query_id="rg_q_001",
        enable_web_search=True,
        tracer=None,
    )
    defaults.update(overrides)
    return ResearchState(**defaults)


def make_gate_orch():
    """綁真 gate helper 的 mock orchestrator。"""
    orch = MagicMock()
    orch.logger = MagicMock()
    orch.handler = MagicMock()
    orch.handler.query_params = {}
    orch.formatted_context = ""
    orch.source_map = {}
    orch._format_context_shared = MagicMock(
        return_value=("[1] rebuilt", {1: {"url": "kept"}})
    )
    # digest budget 是 class 常數；MagicMock 不會 fall through 到 class，顯式綁真值。
    orch._RELEVANCE_GATE_DIGEST_CHAR_BUDGET = (
        DeepResearchOrchestrator._RELEVANCE_GATE_DIGEST_CHAR_BUDGET
    )
    # 真 builder（純函式）+ 真 shape 抽取 + 真 gate
    orch._create_no_results_response = (
        DeepResearchOrchestrator._create_no_results_response.__get__(orch)
    )
    orch._extract_item_fields = (
        DeepResearchOrchestrator._extract_item_fields.__get__(orch)
    )
    orch._relevance_gate_source_pool = (
        DeepResearchOrchestrator._relevance_gate_source_pool.__get__(orch)
    )
    return orch


# === Gate helper 行為 ===


class TestRelevanceGate:

    @pytest.mark.asyncio
    async def test_all_irrelevant_returns_honest_no_results(self):
        """全池不相關 → early_return = 既有誠實查無卡片（復用不重寫）。"""
        items, source_map = make_garbage_wiki_pool()
        orch = make_gate_orch()
        state = make_state(source_map=source_map, current_context=list(items))

        with patch(
            "core.llm.ask_llm",
            new=AsyncMock(return_value={"irrelevant_ids": [1, 2, 3]}),
        ):
            await orch._relevance_gate_source_pool(state)

        assert state.early_return is not None
        item = state.early_return[0]
        assert item["url"] == "internal://no-results"
        assert item["name"] == f"查無相關資料：{QUERY}"
        # 零相關走查無，不做部分重建
        orch._format_context_shared.assert_not_called()

    @pytest.mark.asyncio
    async def test_partial_filters_and_rebuilds(self):
        """部分相關 → 濾掉不相關、全量重建 + G2 sync（mirror β step 7）。"""
        items, source_map = make_mixed_pool()
        orch = make_gate_orch()
        state = make_state(source_map=source_map, current_context=list(items))

        with patch(
            "core.llm.ask_llm",
            new=AsyncMock(return_value={"irrelevant_ids": [3, 4]}),
        ):
            await orch._relevance_gate_source_pool(state)

        assert state.early_return is None
        # 池縮到相關兩筆，reference identity 保持
        assert len(state.current_context) == 2
        assert state.current_context[0] is items[0]
        assert state.current_context[1] is items[1]
        # 全量重建：用濾後的 current_context 呼叫一次
        orch._format_context_shared.assert_called_once_with(state.current_context)
        # G2：instance attr 與 state 同 reference
        assert orch.source_map is state.source_map
        assert orch.formatted_context is state.formatted_context

    @pytest.mark.asyncio
    async def test_all_relevant_pool_untouched(self):
        """irrelevant_ids=[] → 池不動、不重建。"""
        items, source_map = make_mixed_pool()
        orch = make_gate_orch()
        state = make_state(source_map=source_map, current_context=list(items))
        original_map = state.source_map

        with patch(
            "core.llm.ask_llm", new=AsyncMock(return_value={"irrelevant_ids": []})
        ):
            await orch._relevance_gate_source_pool(state)

        assert state.early_return is None
        assert state.source_map is original_map
        assert len(state.current_context) == 4
        orch._format_context_shared.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_exception_fails_open(self):
        """LLM exception → fail-open：全池保留 + warning（不可 silent、不可誤殺）。"""
        items, source_map = make_garbage_wiki_pool()
        orch = make_gate_orch()
        state = make_state(source_map=source_map, current_context=list(items))

        with patch(
            "core.llm.ask_llm", new=AsyncMock(side_effect=RuntimeError("llm down"))
        ):
            await orch._relevance_gate_source_pool(state)

        assert state.early_return is None
        assert state.source_map == source_map
        assert len(state.current_context) == 3
        orch.logger.warning.assert_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_resp",
        [
            {"nope": 1},                      # 缺 key
            {"irrelevant_ids": "not-a-list"}, # 型別錯
        ],
    )
    async def test_unparseable_response_fails_open(self, bad_resp):
        items, source_map = make_garbage_wiki_pool()
        orch = make_gate_orch()
        state = make_state(source_map=source_map, current_context=list(items))

        with patch("core.llm.ask_llm", new=AsyncMock(return_value=bad_resp)):
            await orch._relevance_gate_source_pool(state)

        assert state.early_return is None
        assert len(state.current_context) == 3
        orch.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_llm_error_sentinel_fails_open(self):
        """LLMError sentinel（falsy dict 子類）→ 顯式偵測 fail-open。"""
        from core.llm import LLMError

        items, source_map = make_garbage_wiki_pool()
        orch = make_gate_orch()
        state = make_state(source_map=source_map, current_context=list(items))

        with patch(
            "core.llm.ask_llm",
            new=AsyncMock(return_value=LLMError("timeout", "x")),
        ):
            await orch._relevance_gate_source_pool(state)

        assert state.early_return is None
        assert len(state.current_context) == 3
        orch.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_hallucinated_ids_clamped_to_judged_set(self):
        """LLM 點名不存在的 id（99）→ 只剔除實際送判的交集（3），99 忽略。"""
        items, source_map = make_garbage_wiki_pool()
        orch = make_gate_orch()
        state = make_state(source_map=source_map, current_context=list(items))

        with patch(
            "core.llm.ask_llm",
            new=AsyncMock(return_value={"irrelevant_ids": [99, 3]}),
        ):
            await orch._relevance_gate_source_pool(state)

        assert state.early_return is None
        # 剔 3、留 1/2
        assert len(state.current_context) == 2
        assert state.current_context[0] is items[0]
        assert state.current_context[1] is items[1]

    @pytest.mark.asyncio
    async def test_empty_pool_noop_no_llm_call(self):
        """空池 → gate no-op（空池由既有 β/no-results 分支處理），零 LLM 成本。"""
        orch = make_gate_orch()
        state = make_state(source_map={}, current_context=[])

        with patch("core.llm.ask_llm", new=AsyncMock()) as mock_llm:
            await orch._relevance_gate_source_pool(state)

        mock_llm.assert_not_awaited()
        assert state.early_return is None


# === Phase 接線 ===


def make_phase_orch(format_research_result):
    """綁真 _phase_filter_and_prepare 的 mock orch（沿 test_dr_zero_results_web_search 模式）。"""
    orch = MagicMock()
    orch.logger = MagicMock()
    orch.formatted_context = ""
    orch.source_map = {}
    orch._filter_and_prepare_sources = AsyncMock(return_value=[])
    orch._format_research_context = AsyncMock(return_value=format_research_result)
    orch._emit_phase_event = AsyncMock()
    orch._attempt_zero_results_web_search = AsyncMock(return_value=False)
    orch._create_no_results_response = MagicMock(return_value=[{"name": "查無相關資料"}])
    orch._relevance_gate_source_pool = AsyncMock()
    return orch


class TestPhaseWiring:

    @pytest.mark.asyncio
    async def test_phase_calls_gate_on_nonempty_pool(self):
        """非空池 → gate 被 await 一次（帶 state），正常完成。"""
        pool = {1: {"url": "u1"}, 2: {"url": "u2"}}
        orch = make_phase_orch(("[1]..[2]..", pool))
        state = make_state()

        result = await DeepResearchOrchestrator._phase_filter_and_prepare(orch, state)

        orch._relevance_gate_source_pool.assert_awaited_once_with(state)
        assert result.early_return is None

    @pytest.mark.asyncio
    async def test_phase_returns_gate_early_return(self):
        """gate 設 early_return → phase 直接回傳（完成 emit 恰一次）。"""
        pool = {1: {"url": "u1"}}
        orch = make_phase_orch(("[1]..", pool))

        async def set_early(s):
            s.early_return = [{"name": "查無相關資料"}]

        orch._relevance_gate_source_pool = AsyncMock(side_effect=set_early)
        state = make_state()

        result = await DeepResearchOrchestrator._phase_filter_and_prepare(orch, state)

        assert result.early_return == [{"name": "查無相關資料"}]
        completed = [
            c for c in orch._emit_phase_event.await_args_list
            if c.args == ("filter_and_prepare", "completed")
        ]
        assert len(completed) == 1

    @pytest.mark.asyncio
    async def test_phase_empty_pool_skips_gate(self):
        """空池（β 補不到 → 走查無 early return）→ gate 不被呼叫。"""
        orch = make_phase_orch(("", {}))
        state = make_state()

        result = await DeepResearchOrchestrator._phase_filter_and_prepare(orch, state)

        orch._relevance_gate_source_pool.assert_not_awaited()
        assert result.early_return is not None


# === reference sheet 重構回歸（_extract_item_fields 抽取後行為不變） ===


class TestReferenceSheetUnchanged:

    def _orch(self):
        orch = MagicMock()
        orch.logger = MagicMock()
        orch._extract_item_fields = (
            DeepResearchOrchestrator._extract_item_fields.__get__(orch)
        )
        orch._build_critic_reference_sheet = (
            DeepResearchOrchestrator._build_critic_reference_sheet.__get__(orch)
        )
        return orch

    def test_dict_and_row_shapes_render_as_before(self):
        orch = self._orch()
        row = [
            "https://news.example/2",
            json.dumps({"description": "邱啟新出席公聽會指出……"}, ensure_ascii=False),
            "邱啟新出席公聽會",
            "聯合報",
        ]
        orch.source_map = {
            1: {"title": "邱啟新談都市規劃", "site": "自由時報",
                "description": "台大副教授邱啟新表示……", "url": "https://news.example/1"},
            2: row,
        }
        with patch("reasoning.orchestrator.CONFIG") as cfg:
            cfg.reasoning_params.get.return_value = {}
            sheet = orch._build_critic_reference_sheet([1, 2])
        assert "[1] 自由時報 - 邱啟新談都市規劃" in sheet
        assert "台大副教授邱啟新表示" in sheet
        assert "[2] 聯合報 - 邱啟新出席公聽會" in sheet
        assert "公聽會指出" in sheet
