"""
Unit tests for DR gap-classification evidence injection (防線二).
純函式層：假 raw_results / 寫實 item 形狀 + 真分類 prompt 行為。不打 DB、不跑 pipeline。

LLM key gate 由 code/python/tests/conftest.py 管（:37-51：未設 NLWEB_ALLOW_REAL_LLM=1
時全域清空 9 個 provider key → 打 LLM 的 test fail-loud）。本檔燒錢 test 命名含 `live`
+ skipif gate；便宜層（純函式 / 接線）不打 LLM。

item 形狀來源（2026-07-05 親讀 postgres_client.py:791-825 + baseHandler.py:597-606 驗）：
- raw dict：內層 _search_docs 產（帶 vector_score/text_score/keyword_hit）——Signal A 吃這個。
- 6-item list：search() 在 AGGREGATOR_KEEP_SCORES='1'（預設）時的 return
  [url, schema_str, title, source, vector_or_None, scores_dict]；scores_dict['vector_score']。
- 4/5-tuple：flag='0' 的 legacy return，無 scores。
- 私有檔 4-element list [url, json_str, name, site]：baseHandler prepend，無 scores。
"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from retrieval_providers.postgres_client import max_real_vector_score


class TestMaxRealVectorScore:
    def test_excludes_zero_filled_text_path_dict(self):
        # 純 pg_bigm text-path raw dict：vector_score 填 0.0，必須被排除。
        rows = [{"vector_score": 0.0, "keyword_hit": True} for _ in range(50)]
        assert max_real_vector_score(rows) is None  # abstain, 不是 0.0

    def test_returns_max_of_real_vectors_dict(self):
        rows = [
            {"vector_score": 0.0, "keyword_hit": True},   # text-path, 排除
            {"vector_score": 0.42, "keyword_hit": False},
            {"vector_score": 0.71, "keyword_hit": True},
        ]
        assert max_real_vector_score(rows) == 0.71

    def test_six_item_list_reads_scores_dict(self):
        # search() 轉換後的 6-item list：分數在 index 5 的 scores_dict。
        rows = [
            ["u1", "{}", "t1", "src", None,
             {"vector_score": 0.0, "bm25_score": 0.3, "keyword_boost": 0.0,
              "temporal_boost": 0.0, "final_retrieval_score": 0.3}],   # text-path, 排除
            ["u2", "{}", "t2", "src", [0.1, 0.2],
             {"vector_score": 0.63, "bm25_score": 0.0, "keyword_boost": 0.0,
              "temporal_boost": 0.0, "final_retrieval_score": 0.63}],
        ]
        assert max_real_vector_score(rows) == 0.63

    def test_legacy_4_5_tuple_skipped(self):
        # AGGREGATOR_KEEP_SCORES='0' 的 legacy 形狀：無 scores → 略過 → abstain。
        rows = [
            ["u1", "{}", "t1", "src"],               # 4-tuple
            ["u2", "{}", "t2", "src", [0.1, 0.2]],   # 5-tuple（index 4 是 vector，非分數）
        ]
        assert max_real_vector_score(rows) is None

    def test_private_4_element_list_skipped(self):
        # baseHandler prepend 的私有檔 4-element list：無 scores → 略過，不炸。
        rows = [["u", '{"text":"..."}', "私有檔", "site"]]
        assert max_real_vector_score(rows) is None

    def test_mixed_shapes(self):
        # 私有檔(略過) + 6-item(讀到 0.55) + raw dict(讀到 0.71) + text-path(排除)。
        rows = [
            ["priv", '{"text":"x"}', "私有檔", "site"],                # 私有檔, 略過
            ["u1", "{}", "t1", "src", None,
             {"vector_score": 0.55, "bm25_score": 0.0, "keyword_boost": 0.0,
              "temporal_boost": 0.0, "final_retrieval_score": 0.55}],  # 6-item
            {"vector_score": 0.71, "keyword_hit": True},               # raw dict
            {"vector_score": 0.0, "keyword_hit": True},                # text-path, 排除
        ]
        assert max_real_vector_score(rows) == 0.71

    def test_empty(self):
        assert max_real_vector_score([]) is None


from unittest.mock import MagicMock
from reasoning.orchestrator import DeepResearchOrchestrator


def _make_orch(low_rel=False, low_kw=False, items=None):
    orch = MagicMock()
    orch.logger = MagicMock()
    orch.handler = MagicMock()
    orch.handler.low_relevance_warning = low_rel
    orch.handler.low_keyword_match_warning = low_kw
    orch.handler.final_retrieved_items = items if items is not None else []
    # bind real helper
    orch._build_retrieval_evidence_summary = (
        DeepResearchOrchestrator._build_retrieval_evidence_summary.__get__(orch)
    )
    return orch


def _six(vec, n=1):
    """helper：造 n 筆 search() 轉換後的寫實 6-item list（分數在 index 5）。"""
    return [
        ["u", "{}", "t", "src", None,
         {"vector_score": vec, "bm25_score": 0.3, "keyword_boost": 0.0,
          "temporal_boost": 0.0, "final_retrieval_score": max(vec, 0.3)}]
        for _ in range(n)
    ]


def _private(n=1):
    """helper：造 baseHandler prepend 的私有檔 4-element list（無分數）。"""
    return [["priv", '{"text":"x"}', "私有檔", "site"] for _ in range(n)]


class TestEvidenceSummary:
    def test_weak_evidence_flags_present_realistic_shape(self):
        # 寫實：search() 轉換後的 6-item list（分數在 index 5），91 筆但弱。
        items = _six(0.31, 91)
        orch = _make_orch(low_rel=True, low_kw=False, items=items)
        text = orch._build_retrieval_evidence_summary()
        assert text is not None
        assert "91" in text                 # 命中筆數
        assert "關聯性較弱" in text or "低關聯" in text  # Signal A 判定文字
        assert "0.31" in text               # 最高真實向量分數（從 index 5 讀出）

    def test_strong_evidence_no_weak_flag_realistic_shape(self):
        items = _six(0.82, 40)
        orch = _make_orch(low_rel=False, low_kw=False, items=items)
        text = orch._build_retrieval_evidence_summary()
        assert "40" in text
        assert "0.82" in text
        # 不得誤導成「弱」
        assert "關聯性較弱" not in text and "低關聯" not in text

    def test_private_prepend_not_counted_in_top_score(self):
        # 私有檔 4-element list 混入：計入筆數但取分數時被略過（不炸、不誤讀）。
        items = _private(2) + _six(0.60, 3)
        orch = _make_orch(low_rel=False, low_kw=False, items=items)
        text = orch._build_retrieval_evidence_summary()
        assert "5" in text          # 命中筆數 = 2 私有 + 3 公開
        assert "0.60" in text       # 最高分只從 6-item 讀出，私有檔被略過

    def test_legacy_tuple_no_scores_abstains_top(self):
        # AGGREGATOR_KEEP_SCORES='0' 的 legacy 4/5-tuple：無分數 → top abstain（顯示純關鍵字命中）。
        items = [["u", "{}", "t", "src"] for _ in range(20)]  # 4-tuple
        orch = _make_orch(low_rel=False, low_kw=False, items=items)
        text = orch._build_retrieval_evidence_summary()
        assert "20" in text
        assert "無真實向量證據" in text  # top is None → abstain 文字，不假造分數

    def test_raw_dict_shape_compatible(self):
        # Signal A 路徑的 raw dict 也能被 helper 正確吃（同一函式雙形狀相容）。
        items = [{"vector_score": 0.45, "keyword_hit": True}] * 12
        orch = _make_orch(low_rel=True, low_kw=False, items=items)
        text = orch._build_retrieval_evidence_summary()
        assert "12" in text
        assert "0.45" in text

    def test_zero_items(self):
        orch = _make_orch(items=[])
        text = orch._build_retrieval_evidence_summary()
        # 0 筆由防線一 short-circuit 處理；此 helper 給 None，不注入誤導文字
        assert text is None


from reasoning.prompts.analyst import AnalystPromptBuilder


class TestPromptWiring:
    def test_evidence_injected_into_gap_instructions(self):
        b = AnalystPromptBuilder()
        prompt = b.build_research_prompt(
            query="高端訓",
            formatted_context="[1] 無關資料...",
            mode="discovery",
            enable_gap_enrichment=True,
            enable_web_search=True,
            retrieval_evidence="## 站內檢索證據強弱\n- 站內命中筆數：91\n- ⚠️ Signal A：站內結果關聯性較弱",
        )
        assert "站內命中筆數：91" in prompt
        assert "Signal A" in prompt
        # 新規則文字必須在 prompt 內
        assert "具名實體" in prompt or "專有名詞" in prompt

    def test_no_evidence_gracefully_absent(self):
        b = AnalystPromptBuilder()
        prompt = b.build_research_prompt(
            query="什麼是通貨膨脹",
            formatted_context="[1] ...",
            mode="discovery",
            enable_gap_enrichment=True,
            enable_web_search=True,
            retrieval_evidence=None,
        )
        # None 時不注入證據摘要區塊，但新規則文字仍在（規則是常駐的）
        assert "站內命中筆數" not in prompt
        assert "具名實體" in prompt or "專有名詞" in prompt


# ── 真 LLM 分類對照集（燒錢，CEO gate）──────────────────────────────────────
# gate = NLWEB_ALLOW_REAL_LLM=1 env（本 repo 真慣例；無 --run-live 機制，見
# code/python/tests/conftest.py:49-51 全域清 key）。命名含 `live`，用 -k "live" 選取。
import pytest

# 對照集：專有名詞組（弱證據 → 應 WEB_SEARCH）+ 常識組（→ 應維持 LLM_KNOWLEDGE）
EVIDENCE_CLASSIFICATION_CASES = [
    # (case_id, query, retrieval_evidence, expected_resolution)
    ("proper_noun_weak", "高端訓 是誰",
     "## 站內檢索證據強弱（供 gap 分類參考，非 binding）\n- 站內命中筆數：91\n- 最高真實向量相關分數：0.31\n- ⚠️ Signal A：站內結果關聯性較弱（最高向量分數低於品質門檻）",
     "web_search"),
    ("commonsense_no_weak", "什麼是通貨膨脹",
     "## 站內檢索證據強弱（供 gap 分類參考，非 binding）\n- 站內命中筆數：40\n- 最高真實向量相關分數：0.78\n- 站內證據關聯性正常",
     "llm_knowledge"),
]


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("NLWEB_ALLOW_REAL_LLM") != "1",
    reason="燒真 LLM 錢，CEO gate（設 NLWEB_ALLOW_REAL_LLM=1 才跑）",
)
@pytest.mark.parametrize("case_id,query,evidence,expected", EVIDENCE_CLASSIFICATION_CASES)
async def test_classification_with_evidence_live(case_id, query, evidence, expected):
    """真 LLM：注入 evidence 後，gap 分類是否符合對照集期望。

    gate = NLWEB_ALLOW_REAL_LLM=1 env（本 repo 真慣例；無 --run-live 機制）。
    handler 用 create_mock_handler(for_live_test=True)（既有 live helper）。
    formatted_context = 一段站內無關資料，傳 retrieval_evidence=evidence，跑 research()，
    斷言 gap_resolutions 中至少一個 resolution.value == expected。
    web_search case 另斷言其 requires_web_search is True（web 未啟用時的降級標註契約）。
    """
    from reasoning.agents.analyst import AnalystAgent
    from tests.test_llm_api_decisions import create_mock_handler  # 既有 live handler helper

    handler = create_mock_handler(for_live_test=True)
    handler.query = query
    agent = AnalystAgent(handler, timeout=120)  # 位置參數，非 handler= kwarg
    result = await agent.research(
        query=query,
        formatted_context="[1] （站內無關資料，僅供測試弱證據情境）",
        mode="discovery",
        enable_web_search=True,
        retrieval_evidence=evidence,
    )
    gaps = result.gap_resolutions or []
    resolutions = [g.resolution.value for g in gaps]
    assert expected in resolutions, (
        f"{case_id}: expected '{expected}' in {resolutions} "
        f"(evidence-driven classification failed)"
    )
    # Codex should-fix #2：web_search case 須帶 requires_web_search True（降級標註契約）
    if expected == "web_search":
        web_gaps = [g for g in gaps if g.resolution.value == "web_search"]
        assert any(g.requires_web_search is True for g in web_gaps), (
            f"{case_id}: web_search gap 缺 requires_web_search=True "
            f"(降級標註契約未滿足)"
        )


# ============================================================================
# §v3 防線三：web_search gap resolution 執行優先權修復
# ============================================================================

import types
from unittest.mock import AsyncMock, MagicMock
import pytest
from reasoning.orchestrator import DeepResearchOrchestrator, _normalize_web_query
from reasoning.schemas_enhanced import GapResolution, GapResolutionType


def _gap(resolution, search_query, requires_web=True):
    return GapResolution(
        gap_type="test_gap",
        resolution=resolution,
        search_query=search_query,
        reason="test",
        requires_web_search=requires_web,
    )


def _make_orch_v3(process_side_effect=None):
    """Bind the real helper onto a MagicMock orchestrator with mocked deps."""
    orch = MagicMock()
    orch.logger = MagicMock()
    orch.source_map = {1: {"url": "u1"}}
    # _process_gap_resolutions is the reused engine; mock it, assert calls.
    orch._process_gap_resolutions = AsyncMock(side_effect=process_side_effect)
    # _format_context_shared returns (formatted_str, source_map_dict)
    orch._format_context_shared = MagicMock(
        return_value=("[1] ...", {1: {"url": "u1"}, 2: {"url": "web"}})
    )
    orch._resolve_web_search_gaps_in_loop = (
        DeepResearchOrchestrator._resolve_web_search_gaps_in_loop.__get__(orch)
    )
    return orch


def _state(current_context=None):
    st = types.SimpleNamespace()
    st.current_context = current_context if current_context is not None else [{"url": "u1"}]
    st.source_map = {1: {"url": "u1"}}
    st.formatted_context = "[1] ..."
    st.mode = "discovery"
    st.tracer = None
    st.query_id = "q-test"
    return st


class TestResolveWebSearchGapsInLoop:
    @pytest.mark.asyncio
    async def test_web_gap_is_executed_via_process_gap_resolutions(self):
        # response 帶一個 web_search gap → helper 必須呼叫 _process_gap_resolutions 且回 True（有補到）。
        # mock 簽名需含 web_searched_queries kwarg（helper 呼叫時會傳，見所有權契約）。
        async def _append_one(response, mode, current_context, enable_web_search, tracer, query_id,
                              web_searched_queries=None):
            current_context.append({"url": "web", "title": "web result"})
            if web_searched_queries is not None:
                for g in response.gap_resolutions:
                    web_searched_queries.add(_normalize_web_query(g.search_query))
        orch = _make_orch_v3(process_side_effect=_append_one)
        st = _state()
        seen = set()
        response = types.SimpleNamespace(
            gap_resolutions=[_gap(GapResolutionType.WEB_SEARCH, "高端訓 是誰")]
        )
        added = await orch._resolve_web_search_gaps_in_loop(
            response=response, mode=st.mode, state=st,
            enable_web_search=True, web_searched_queries=seen,
            tracer=None, query_id=st.query_id,
        )
        assert added is True
        orch._process_gap_resolutions.assert_awaited_once()
        # dedup 集合被填入 normalize 後的 query（引擎 schedule 後 mark，所有權契約）
        assert _normalize_web_query("高端訓 是誰") in seen

    @pytest.mark.asyncio
    async def test_no_web_gap_is_noop(self):
        # 只有 llm_knowledge gap（無 web_search）→ 不呼叫 _process_gap_resolutions，回 False。
        orch = _make_orch_v3()
        st = _state()
        response = types.SimpleNamespace(
            gap_resolutions=[_gap(GapResolutionType.LLM_KNOWLEDGE, None, requires_web=False)]
        )
        added = await orch._resolve_web_search_gaps_in_loop(
            response=response, mode=st.mode, state=st,
            enable_web_search=True, web_searched_queries=set(),
            tracer=None, query_id=st.query_id,
        )
        assert added is False
        orch._process_gap_resolutions.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_web_disabled_is_noop_visible(self):
        # enable_web_search=False → 不呼叫引擎、回 False（可見降級，非 silent）。
        orch = _make_orch_v3()
        st = _state()
        response = types.SimpleNamespace(
            gap_resolutions=[_gap(GapResolutionType.WEB_SEARCH, "x 查詢")]
        )
        added = await orch._resolve_web_search_gaps_in_loop(
            response=response, mode=st.mode, state=st,
            enable_web_search=False, web_searched_queries=set(),
            tracer=None, query_id=st.query_id,
        )
        assert added is False
        orch._process_gap_resolutions.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_helper_delegates_dedup_to_engine(self):
        # R3：dedup/cap 不在 helper（已全移引擎收集迴圈）。helper 有 web gap 就呼叫引擎、傳共享 set，
        #     由引擎決定實際是否送 Google。此 test 驗 helper 把整批 web gap 交給引擎（不自行 filter）。
        #     「已搜過 query 不重打 Google」的權威驗證在 TestCrossPathDedup（引擎層）。
        async def _noop_engine(response, mode, current_context, enable_web_search, tracer, query_id,
                               web_searched_queries=None):
            # 模擬引擎：這輪 query 已在 set（dedup 命中）→ 不 append 任何 source。
            pass
        orch = _make_orch_v3(process_side_effect=_noop_engine)
        st = _state()
        seen = {_normalize_web_query("高端訓 是誰")}  # 前輪已搜
        response = types.SimpleNamespace(
            gap_resolutions=[_gap(GapResolutionType.WEB_SEARCH, "高端訓 是誰")]
        )
        added = await orch._resolve_web_search_gaps_in_loop(
            response=response, mode=st.mode, state=st,
            enable_web_search=True, web_searched_queries=seen,
            tracer=None, query_id=st.query_id,
        )
        # helper 仍呼叫引擎（把 dedup 決策交給引擎收集迴圈）；引擎沒補 source → helper 回 False。
        orch._process_gap_resolutions.assert_awaited_once()
        assert added is False

    @pytest.mark.asyncio
    async def test_added_zero_returns_false_no_reformat_needed(self):
        # web 有 gap 但 _process_gap_resolutions 沒補到任何 source（Google 空）→ 回 False。
        async def _append_nothing(response, mode, current_context, enable_web_search, tracer, query_id,
                                  web_searched_queries=None):
            pass  # nothing added（模擬 Google 回空；非 test 佔位）
        orch = _make_orch_v3(process_side_effect=_append_nothing)
        st = _state()
        response = types.SimpleNamespace(
            gap_resolutions=[_gap(GapResolutionType.WEB_SEARCH, "冷門 查詢")]
        )
        added = await orch._resolve_web_search_gaps_in_loop(
            response=response, mode=st.mode, state=st,
            enable_web_search=True, web_searched_queries=set(),
            tracer=None, query_id=st.query_id,
        )
        assert added is False  # 沒補到 source
        orch._process_gap_resolutions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_gap_resolutions_attr_is_noop(self):
        # response 無 gap_resolutions 屬性/為空 → 安全 no-op，回 False。
        orch = _make_orch_v3()
        st = _state()
        response = types.SimpleNamespace(gap_resolutions=[])
        added = await orch._resolve_web_search_gaps_in_loop(
            response=response, mode=st.mode, state=st,
            enable_web_search=True, web_searched_queries=set(),
            tracer=None, query_id=st.query_id,
        )
        assert added is False
        orch._process_gap_resolutions.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_isolation_on_web_docs_stashed_not_full_rebuild(self):
        # B-ISO snapshot-before（R2 真解）：isolation 模式下 helper 不全量重建、不自己 append source_map。
        # 引擎（_process_gap_resolutions → _execute_web_searches:2282-2288）已把 web docs append 進
        # source_map（start_id=max+1）；helper 用 snapshot before_max 取引擎已登記區間、用引擎的實際 id
        # 組 pending_web_formatted。驗：無同 doc 雙 ID、len 正確、marker id == 引擎登記 id。
        #
        # ★ 用真 _format_context_shared（純函式）驗真實 marker id；mock _process_gap_resolutions
        #   模擬引擎「extend current_context + append source_map(max+1)」的雙動作。
        WEB_DOC = {"url": "web1", "title": "高端訓 web result", "site": "Web",
                   "description": "[Tier 6 | web_reference] ..."}

        async def _engine_like_append(response, mode, current_context, enable_web_search,
                                      tracer, query_id, web_searched_queries=None):
            # 模擬引擎 :2282-2288：extend context + append source_map（start_idx = max+1）。
            current_context.append(WEB_DOC)
            start_idx = max(orch.source_map.keys(), default=0) + 1
            orch.source_map[start_idx] = WEB_DOC   # self.source_map is state.source_map（同一 dict）
            if web_searched_queries is not None:
                for g in response.gap_resolutions:
                    web_searched_queries.add(_normalize_web_query(g.search_query))

        orch = _make_orch_v3(process_side_effect=_engine_like_append)
        # 用真 _format_context_shared 純函式（驗真實 marker id 對齊）
        orch._format_context_shared = DeepResearchOrchestrator._format_context_shared.__get__(orch)
        orch._get_current_time_header = MagicMock(return_value="")  # 純函式前綴，避免 MagicMock 相加炸
        st = _state()
        st.enable_isolation = True          # ★ isolation ON（現有六 test 皆 non-isolation）
        st.source_map = {1: {"url": "u1", "title": "站內 doc", "site": "site",
                             "description": "站內"}}  # 既有站內 doc，id=1
        orch.source_map = st.source_map      # ★ 同一 dict reference（模擬 :456-458）
        seen = set()
        response = types.SimpleNamespace(
            gap_resolutions=[_gap(GapResolutionType.WEB_SEARCH, "高端訓 是誰")]
        )
        added = await orch._resolve_web_search_gaps_in_loop(
            response=response, mode=st.mode, state=st,
            enable_web_search=True, web_searched_queries=seen,
            tracer=None, query_id=st.query_id,
        )
        assert added is True
        # (1) source_map 恰含 2 筆（站內 id=1 + web id=2），無同 doc 雙 ID（R1 bug 會變 3 筆：1 + 引擎2 + helper3）。
        assert len(st.source_map) == 2, f"雙重 ID 未根除：{sorted(st.source_map.keys())}"
        assert sorted(st.source_map.keys()) == [1, 2]
        # (2) 既有站內 doc（id=1）未被逐出，web doc 用引擎登記的 id=2（before_max=1 → id=2）。
        assert 1 in st.source_map
        assert st.source_map[2] is WEB_DOC       # 引擎登記的正是 WEB_DOC，helper 沒另建一份
        # (3) pending_web_formatted 被填，且其 citation marker id == 引擎登記 id（=2），不是 helper 另分配的 id。
        assert getattr(st, "pending_web_formatted", None) is not None
        assert "[2]" in st.pending_web_formatted   # marker 用引擎的實際 id
        assert "[3]" not in st.pending_web_formatted  # helper 沒再分配第二區間
        # (4) formatted_context 未被全量覆寫（isolation 只暫存 pending，不寫 state.formatted_context）。
        assert st.formatted_context == "[1] ..."   # _state() 初值，未動


class TestCrossPathDedup:
    """B-DEDUP-SCOPE + R3 記帳集中：normalize/response 內部去重/跨路徑 dedup/cap/mark 全在
    _process_gap_resolutions 收集迴圈。同 query 在 SEARCH_REQUIRED helper 與 DRAFT_READY :857
    主路徑不會各打一次。斷言只看引擎層（收集迴圈過濾後送進 _execute_web_searches 的 gaps）。"""

    def _engine_orch(self):
        """bind 真 _process_gap_resolutions，mock 下游 _execute_web_searches 記錄實收 gaps。"""
        orch = MagicMock()
        orch.logger = MagicMock()
        orch.source_map = {1: {"url": "u1"}}
        orch._execute_web_searches = AsyncMock()  # 記錄實收 gaps，不打真 Google
        # 其他分支（stock/wiki/...）不觸發，mock 掉避免 side effect
        for m in ("_execute_stock_tw_searches", "_execute_stock_global_searches",
                  "_execute_weather_tw_searches", "_execute_weather_global_searches",
                  "_execute_company_tw_searches", "_execute_company_global_searches",
                  "_execute_wikipedia_searches"):
            setattr(orch, m, AsyncMock())
        orch._process_gap_resolutions = (
            DeepResearchOrchestrator._process_gap_resolutions.__get__(orch)
        )
        return orch

    @pytest.mark.asyncio
    async def test_engine_skips_already_searched_query(self):
        orch = self._engine_orch()
        seen = {"高端訓 是誰"}   # helper 這輪已搜過（跨路徑共享同一 set）
        response = types.SimpleNamespace(
            gap_resolutions=[_gap(GapResolutionType.WEB_SEARCH, "高端訓 是誰")]
        )
        await orch._process_gap_resolutions(
            response=response, mode="discovery", current_context=[{"url": "u1"}],
            enable_web_search=True, tracer=None, query_id="q",
            web_searched_queries=seen,
        )
        # 已搜過 query → 引擎過濾掉 → _execute_web_searches 收到空（或根本不呼叫）。
        if orch._execute_web_searches.await_count:
            sent_gaps = orch._execute_web_searches.await_args.args[0]
            assert sent_gaps == [], f"已搜 query 不該再送引擎：{[g.search_query for g in sent_gaps]}"

    @pytest.mark.asyncio
    async def test_new_query_is_sent_and_marked(self):
        orch = self._engine_orch()
        seen: set = set()
        response = types.SimpleNamespace(
            gap_resolutions=[_gap(GapResolutionType.WEB_SEARCH, "新實體 查詢")]
        )
        await orch._process_gap_resolutions(
            response=response, mode="discovery", current_context=[{"url": "u1"}],
            enable_web_search=True, tracer=None, query_id="q",
            web_searched_queries=seen,
        )
        # 未搜過 → 送進引擎；收集決定送出的當下 query 被 mark 進 set（R3：mark 在收集迴圈，非 _execute_web_searches）。
        orch._execute_web_searches.assert_awaited_once()
        sent_gaps = orch._execute_web_searches.await_args.args[0]
        assert [g.search_query for g in sent_gaps] == ["新實體 查詢"]
        assert "新實體 查詢" in seen

    @pytest.mark.asyncio
    async def test_intra_response_duplicate_query_sent_once(self):
        # R3 in-house S-1：同一 response 內重複 query（DRAFT_READY 也可能）→ 收集迴圈只送第一個，
        # 不 double-call Google。斷言引擎層送出的 gaps 只含一份。
        orch = self._engine_orch()
        seen: set = set()
        response = types.SimpleNamespace(
            gap_resolutions=[
                _gap(GapResolutionType.WEB_SEARCH, "高端訓 是誰"),
                _gap(GapResolutionType.WEB_SEARCH, "高端訓 是誰"),   # 同 response 內重複
                _gap(GapResolutionType.WEB_SEARCH, "  高端訓 是誰  "),  # normalize 後也是同一個
            ]
        )
        await orch._process_gap_resolutions(
            response=response, mode="discovery", current_context=[{"url": "u1"}],
            enable_web_search=True, tracer=None, query_id="q",
            web_searched_queries=seen,
        )
        orch._execute_web_searches.assert_awaited_once()
        sent_gaps = orch._execute_web_searches.await_args.args[0]
        assert [g.search_query for g in sent_gaps] == ["高端訓 是誰"], (
            f"同 response 重複 query 應只送一份：{[g.search_query for g in sent_gaps]}"
        )
        # mark 只記一個 normalize key。
        assert seen == {_normalize_web_query("高端訓 是誰")}

    @pytest.mark.asyncio
    async def test_dedup_key_normalized_strip_collapse_casefold(self):
        # Codex should-fix：normalize = strip + 內部 whitespace collapse + casefold。
        # set 內為 normalize 後的 key；帶前後空白 / 內部多空白 / 大小寫變體的同 query 都應命中 dedup。
        from reasoning.orchestrator import _normalize_web_query
        canonical = _normalize_web_query("高端訓 是誰")
        orch = self._engine_orch()
        seen = {canonical}
        for variant in ("  高端訓 是誰  ", "高端訓　是誰", "高端訓  是誰"):  # 前後空白 / 全形空白 / 內部多空白
            orch._execute_web_searches.reset_mock()
            response = types.SimpleNamespace(
                gap_resolutions=[_gap(GapResolutionType.WEB_SEARCH, variant)]
            )
            await orch._process_gap_resolutions(
                response=response, mode="discovery", current_context=[{"url": "u1"}],
                enable_web_search=True, tracer=None, query_id="q",
                web_searched_queries=seen,
            )
            if orch._execute_web_searches.await_count:
                sent_gaps = orch._execute_web_searches.await_args.args[0]
                assert sent_gaps == [], f"normalize 後應命中 dedup，不重送：{variant!r}"

    @pytest.mark.asyncio
    async def test_normalize_web_query_pure_function(self):
        # 純函式行為（集中一處，避免各點 normalize 不一致）。
        from reasoning.orchestrator import _normalize_web_query
        assert _normalize_web_query("  高端訓 是誰  ") == _normalize_web_query("高端訓 是誰")
        assert _normalize_web_query("高端訓  是誰") == _normalize_web_query("高端訓 是誰")  # 內部多空白 collapse
        assert _normalize_web_query("ABC def") == _normalize_web_query("abc DEF")           # casefold
        assert _normalize_web_query("") == ""
        assert _normalize_web_query(None) == ""                                             # None 安全


class TestWhitespaceOnlyQuerySkip:
    """SF2（land-review should-fix，Codex）：whitespace-only search_query（normalize 成空 key）
    在 dedup/cap/mark **之前** skip + log，避免空 query 佔 cap slot 並被 mark。斷言引擎層。"""

    def _engine_orch(self):
        orch = MagicMock()
        orch.logger = MagicMock()
        orch.source_map = {1: {"url": "u1"}}
        orch._execute_web_searches = AsyncMock()
        for m in ("_execute_stock_tw_searches", "_execute_stock_global_searches",
                  "_execute_weather_tw_searches", "_execute_weather_global_searches",
                  "_execute_company_tw_searches", "_execute_company_global_searches",
                  "_execute_wikipedia_searches"):
            setattr(orch, m, AsyncMock())
        orch._process_gap_resolutions = (
            DeepResearchOrchestrator._process_gap_resolutions.__get__(orch)
        )
        return orch

    @pytest.mark.asyncio
    async def test_whitespace_only_query_not_sent_not_marked_not_capped(self):
        # whitespace-only（含全形空白）normalize → 空 key → 不進 web_search_gaps、不佔 cap、不 mark。
        orch = self._engine_orch()
        seen: set = set()
        response = types.SimpleNamespace(
            gap_resolutions=[
                _gap(GapResolutionType.WEB_SEARCH, "   "),       # 半形空白
                _gap(GapResolutionType.WEB_SEARCH, "　　"),       # 全形空白
                _gap(GapResolutionType.WEB_SEARCH, "有效 查詢"),  # 對照：有效 query 正常送
            ]
        )
        await orch._process_gap_resolutions(
            response=response, mode="discovery", current_context=[{"url": "u1"}],
            enable_web_search=True, tracer=None, query_id="q",
            web_searched_queries=seen,
        )
        # 只有有效 query 被送進引擎，兩個空 query 全 skip。
        orch._execute_web_searches.assert_awaited_once()
        sent_gaps = orch._execute_web_searches.await_args.args[0]
        assert [g.search_query for g in sent_gaps] == ["有效 查詢"], (
            f"whitespace-only query 不該進 web_search_gaps：{[g.search_query for g in sent_gaps]}"
        )
        # mark set 只含有效 query 的 normalize key（空 key '' 不得被 mark → 不佔 cap slot）。
        assert seen == {_normalize_web_query("有效 查詢")}, (
            f"空 query 不該被 mark（否則佔 cap slot）：{seen}"
        )
        assert "" not in seen, "空 key '' 被誤 mark → 佔 cap slot"
        # visible log（empty-after-normalize skip，非 silent）。
        assert any(
            "empty-after-normalize" in str(c) for c in orch.logger.info.call_args_list
        ), "whitespace-only skip 應有 visible log（empty-after-normalize）"


class TestWebCapLog:
    """B-CAP + R3：run 級 cap 在**引擎收集迴圈**執行（DRAFT_READY 直進引擎的 web gap 也被 cap，
    修 Codex B1 bypass）。cap 觸發時 visible log（cap_skipped），不 silent。斷言只看引擎層。"""

    def _engine_orch(self):
        """bind 真 _process_gap_resolutions，mock 下游 _execute_web_searches 記錄實收 gaps。"""
        orch = MagicMock()
        orch.logger = MagicMock()
        orch.source_map = {1: {"url": "u1"}}
        orch._execute_web_searches = AsyncMock()
        for m in ("_execute_stock_tw_searches", "_execute_stock_global_searches",
                  "_execute_weather_tw_searches", "_execute_weather_global_searches",
                  "_execute_company_tw_searches", "_execute_company_global_searches",
                  "_execute_wikipedia_searches"):
            setattr(orch, m, AsyncMock())
        orch._process_gap_resolutions = (
            DeepResearchOrchestrator._process_gap_resolutions.__get__(orch)
        )
        return orch

    @pytest.mark.asyncio
    async def test_draft_ready_web_gaps_capped_at_engine(self, monkeypatch):
        # ★ R3 核心：DRAFT_READY 主路徑（:857）直呼 _process_gap_resolutions，餵 >cap 個 distinct web gap，
        #   引擎收集迴圈只送前 cap 個到 _execute_web_searches，其餘 cap_skipped + warning log。
        import reasoning.orchestrator as orch_mod
        cap = 3  # monkeypatch CONFIG 降 cap 加速（不依賴 config default 6）
        monkeypatch.setattr(
            orch_mod.CONFIG, "reasoning_params",
            {"tier_6": {"gap_routing": {"max_external_calls_per_run": cap}}},
            raising=False,
        )
        orch = self._engine_orch()
        seen: set = set()
        # 5 個 distinct web query（> cap=3）
        response = types.SimpleNamespace(
            gap_resolutions=[_gap(GapResolutionType.WEB_SEARCH, f"實體{i} 查詢") for i in range(5)]
        )
        await orch._process_gap_resolutions(
            response=response, mode="discovery", current_context=[{"url": "u1"}],
            enable_web_search=True, tracer=None, query_id="q",
            web_searched_queries=seen,
        )
        # 引擎只送前 cap 個到 _execute_web_searches。
        orch._execute_web_searches.assert_awaited_once()
        sent_gaps = orch._execute_web_searches.await_args.args[0]
        assert len(sent_gaps) == cap, f"cap={cap} 應只送 {cap} 個，實送 {len(sent_gaps)}"
        # mark set 也只記 cap 個。
        assert len(seen) == cap
        # cap 觸發 → visible warning（cap_skipped，非 silent）。
        assert orch.logger.warning.called
        assert any("cap" in str(c).lower() for c in orch.logger.warning.call_args_list), (
            "cap 觸發應有 visible log（cap_skipped）"
        )

    @pytest.mark.asyncio
    async def test_cap_already_full_skips_all(self, monkeypatch):
        # web_searched_queries 已滿 cap（前輪已搜滿）→ 新 web gap 全 cap_skipped，不送引擎。
        import reasoning.orchestrator as orch_mod
        cap = 3
        monkeypatch.setattr(
            orch_mod.CONFIG, "reasoning_params",
            {"tier_6": {"gap_routing": {"max_external_calls_per_run": cap}}},
            raising=False,
        )
        orch = self._engine_orch()
        seen = {f"q{i}" for i in range(cap)}  # 已滿
        response = types.SimpleNamespace(
            gap_resolutions=[_gap(GapResolutionType.WEB_SEARCH, "新查詢")]
        )
        await orch._process_gap_resolutions(
            response=response, mode="discovery", current_context=[{"url": "u1"}],
            enable_web_search=True, tracer=None, query_id="q",
            web_searched_queries=seen,
        )
        # 全被 cap 擋 → _execute_web_searches 收到空（或不呼叫）。
        if orch._execute_web_searches.await_count:
            sent_gaps = orch._execute_web_searches.await_args.args[0]
            assert sent_gaps == [], f"cap 已滿不該再送：{[g.search_query for g in sent_gaps]}"
        assert orch.logger.warning.called  # visible log（非 silent）


# ============================================================================
# §v3 Task V3-2：接線進 actor-critic loop 的 SEARCH_REQUIRED 分支（兩源合流）
# ============================================================================


class TestSearchRequiredWebConfluence:
    """控制流：SEARCH_REQUIRED（站內補搜）語境下 web_search gap 仍被兌現。

    load-bearing 斷言 = 一個「status==SEARCH_REQUIRED + new_queries 非空 + web_search gap」的 response
    餵給 _resolve_web_search_gaps_in_loop 時，web gap 被送進 _process_gap_resolutions（不因 status/
    new_queries 存在而丟棄）。策略 A：聚焦接線點，mock 引擎驗呼叫，不打真 API、不組半真 loop。
    v2 base 無 helper → fail；v3 → pass。
    """

    @pytest.mark.asyncio
    async def test_web_gap_resolved_even_when_status_search_required(self):
        # 模擬引擎補一筆 web doc（讓 helper 回 True 並走完 re-format 分支）。
        async def _append_one(response, mode, current_context, enable_web_search, tracer, query_id,
                              web_searched_queries=None):
            current_context.append({"url": "web", "title": "web result"})
            if web_searched_queries is not None:
                for g in response.gap_resolutions:
                    web_searched_queries.add(_normalize_web_query(g.search_query))

        orch = _make_orch_v3(process_side_effect=_append_one)
        st = _state()
        seen: set = set()
        # ★ 關鍵：response 同時帶 SEARCH_REQUIRED + new_queries（站內補搜）+ web_search gap ——
        #   正是高端訓 bug 的混合態。helper 必須忽略 status/new_queries、只兌現 web gap。
        response = types.SimpleNamespace(
            status="SEARCH_REQUIRED",
            new_queries=["高端訓 補搜"],
            gap_resolutions=[_gap(GapResolutionType.WEB_SEARCH, "高端訓 是誰")],
        )
        added = await orch._resolve_web_search_gaps_in_loop(
            response=response, mode=st.mode, state=st,
            enable_web_search=True, web_searched_queries=seen,
            tracer=None, query_id=st.query_id,
        )
        # load-bearing：web gap 被送進兌現引擎（即使 status==SEARCH_REQUIRED）。
        assert added is True
        orch._process_gap_resolutions.assert_awaited_once()
        sent = orch._process_gap_resolutions.await_args
        passed_gaps = sent.kwargs["response"].gap_resolutions
        assert any(
            g.resolution == GapResolutionType.WEB_SEARCH and g.search_query == "高端訓 是誰"
            for g in passed_gaps
        ), "SEARCH_REQUIRED 語境下 web gap 未被送進兌現引擎（高端訓 zero-Google bug 未修）"
        # 引擎收到的是「只含 web gap 的 filtered response」，不含 internal new_queries 汙染。
        assert "高端訓 是誰" in seen


class TestMaxIterationsOneBoundary:
    """B-LAST：max_iterations=1 邊界（:794），SEARCH_REQUIRED + 站內 secondary 0 + web 有 gap 時，
    web docs 不被 :794「資料不足」error return 丟棄（web_added 退一格，保證一次 Analyst pass 消費）。
    半真 loop：patch 全部外部依賴，跑一輪 _phase_actor_critic_loop。v2 base :794 直接 error return（fail）；
    v3 退一格不丟棄（pass）。"""

    def _loop_orch(self, monkeypatch, research_side_effect, secondary_empty=True):
        """組最小 orchestrator：bind 真 _phase_actor_critic_loop + _resolve_web_search_gaps_in_loop，
        mock 全部外部呼叫（analyst / retriever_search / _process_gap_resolutions / progress / tracer）。"""
        orch = MagicMock()
        orch.logger = MagicMock()
        orch.source_map = {1: {"url": "u1"}}
        orch.formatted_context = "[1] 站內"
        # analyst.research：依 side_effect 回不同 response（第一次 SEARCH_REQUIRED+web gap）
        orch.analyst = MagicMock()
        orch.analyst.research = AsyncMock(side_effect=research_side_effect)
        orch.analyst.revise = AsyncMock(side_effect=research_side_effect)
        # 站內 secondary search 回空（走 :779 else）
        import reasoning.orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "retriever_search",
                            AsyncMock(return_value=([] if secondary_empty else [("u", "{}", "t", "s")])),
                            raising=False)
        orch.source_filter = MagicMock()
        orch.source_filter.filter_and_enrich = MagicMock(return_value=[])
        # web 兌現引擎：補一筆 web doc + append source_map（模擬 :2282-2288）
        # Codex R5：mock 必須模擬 R3 引擎層 dedup——已 mark 的 query 零 append（否則
        # max_iterations=1 退一格後第二輪又 append → web_added 恆 True → 測試自己不收斂）
        async def _proc(response, mode, current_context, enable_web_search, tracer, query_id,
                        web_searched_queries=None):
            if web_searched_queries is not None:
                keys = [_normalize_web_query(g.search_query) for g in response.gap_resolutions]
                new_keys = [k for k in keys if k not in web_searched_queries]
                if not new_keys:
                    return  # 引擎 dedup skip：零 append → helper 算出 added=0 → web_added=False → 收斂
                for k in new_keys:
                    web_searched_queries.add(k)
            current_context.append({"url": "web", "title": "web", "site": "Web", "description": "..."})
            start_idx = max(orch.source_map.keys(), default=0) + 1
            orch.source_map[start_idx] = current_context[-1]
        orch._process_gap_resolutions = AsyncMock(side_effect=_proc)
        # 純函式 / 無害 async 樁
        orch._format_context_shared = DeepResearchOrchestrator._format_context_shared.__get__(orch)
        orch._resolve_web_search_gaps_in_loop = (
            DeepResearchOrchestrator._resolve_web_search_gaps_in_loop.__get__(orch)
        )
        orch._check_connection = MagicMock()
        orch._send_progress = AsyncMock()
        orch._emit_phase_event = AsyncMock()
        orch._get_current_time_header = MagicMock(return_value="")
        return orch

    @pytest.mark.asyncio
    async def test_last_iteration_794_web_added_not_discarded(self, monkeypatch):
        calls = {"n": 0}

        def _research(*a, **k):
            calls["n"] += 1
            # 每次都回 SEARCH_REQUIRED + web gap + 空 draft（模擬站內查無的高端訓）
            return types.SimpleNamespace(
                status="SEARCH_REQUIRED",
                draft="",
                new_queries=["高端訓 補搜"],
                missing_information=["高端訓 背景"],
                gap_resolutions=[_gap(GapResolutionType.WEB_SEARCH, "高端訓 是誰")],
                citations_used=[],
            )

        orch = self._loop_orch(monkeypatch, _research, secondary_empty=True)
        state = types.SimpleNamespace(
            max_iterations=1, iteration=0, reject_count=0,
            query="高端訓", mode="discovery", temporal_context=None,
            enable_kg=False, enable_web_search=True, query_id="q",
            tracer=None, iteration_logger=MagicMock(), enable_isolation=False,  # Codex R5：base :671 無條件呼叫 state.iteration_logger.log_agent_output，None 會在抵達目標路徑前先 AttributeError
            current_context=[{"url": "u1", "title": "站內", "site": "s", "description": "無關"}],
            source_map={1: {"url": "u1"}}, formatted_context="[1] 站內",
            draft=None, review=None, response=None, seen_citation_ids=set(),
            analyst_citations=[], early_return=None,
        )
        orch.source_map = state.source_map
        result = await DeepResearchOrchestrator._phase_actor_critic_loop(orch, state)
        # 協調員 R5-post 裁決（2026-07-06）：perpetual-empty mock 下，deduped 第二 pass
        # （web_added=False）終結於 :794「資料不足」是**正確產品行為**——消費過 web 仍寫不出
        # 就誠實放棄，不無限迴圈。故**不**斷言終態無 error（原「無法完成研究 not in desc」斷言
        # 與 R5 dedup-aware _proc 修正互相矛盾，已移除）。真正區分 v2/v3 的 load-bearing 斷言
        # 是下方 calls["n"] >= 2（v2 base 第一輪即 error return = 1；v3 退一格多一次 Analyst
        # pass 消費 web docs = 2）+ web docs in context。
        # web doc 已進 context（供下輪 Analyst 消費）
        assert any(it.get("url") == "web" for it in result.current_context), "web docs 未進 context"
        # 至少發生一次 web 兌現
        orch._process_gap_resolutions.assert_awaited()
        # SF-2（Codex）：不只驗 docs 還在——退一格保命的**目的**是讓 Analyst 於下一輪被再次呼叫消費 web docs。
        # 故斷言 research（含 revise，side_effect 共用計數）call count >= 2（初次 + 退一格後的下一輪 pass）。
        assert calls["n"] >= 2, (
            f"Analyst 未於下一輪被再次呼叫（research call count={calls['n']}）—— "
            f"web docs 進 context 但沒有 pass 消費 = B-LAST 保命未達目的"
        )


class TestSiblingBranchBLastRetreat:
    """B-LAST 孿生洞（land-review blocker）：SEARCH_REQUIRED + web gap + **站內 secondary 有結果**
    （走 :911 `if secondary_results:` 分支，非 :965 else 查無分支）時，:963 `iteration += 1; continue`
    缺 web_added 退一格 → 最後一輪補的 web+站內源在 while 邊界被丟棄、以 no-results 收場。
    這是 :995 「站內查無」case 的完全同構孿生，只是觸發在姊妹分支。

    半真 loop（復用 TestMaxIterationsOneBoundary._loop_orch，secondary_empty=False → 站內有結果）：
    max_iterations=2 + perpetual SEARCH_REQUIRED + web gap + 站內 secondary 非空。
    修法前（:911 分支無退一格）：research 只跑 2 次（iter0→iteration=1, iter1→iteration=2→退出），
    最後一輪補的源無下一輪 Analyst pass 消費 → fail。
    修法後（:911 分支加 `if web_added: iteration -= 1`）：第一輪 web pass 退一格 → 多一次 Analyst
    pass 消費（research call count == 3）→ pass。
    """

    @pytest.mark.asyncio
    async def test_last_iteration_911_web_added_not_discarded(self, monkeypatch):
        calls = {"n": 0}

        def _research(*a, **k):
            calls["n"] += 1
            # 每次都回 SEARCH_REQUIRED + web gap + 空 draft（模擬站內有結果但仍缺具名實體的高端訓）
            return types.SimpleNamespace(
                status="SEARCH_REQUIRED",
                draft="",
                new_queries=["高端訓 補搜"],
                missing_information=["高端訓 背景"],
                gap_resolutions=[_gap(GapResolutionType.WEB_SEARCH, "高端訓 是誰")],
                citations_used=[],
                reasoning_chain="",
            )

        # ★ secondary_empty=False → 站內 secondary search 回非空 → 走 :911 `if secondary_results:` 分支
        boundary = TestMaxIterationsOneBoundary()
        orch = boundary._loop_orch(monkeypatch, _research, secondary_empty=False)
        # :1308 no-results 頁樁（觀測終態是否以 no-results 收場）
        orch._format_friendly_no_data_result = MagicMock(
            return_value=[{"@type": "Item", "name": "Deep Research 資料不足"}]
        )
        orch._format_error_result = MagicMock(
            return_value=[{"@type": "Item", "name": "error"}]
        )
        state = types.SimpleNamespace(
            max_iterations=2, iteration=0, reject_count=0,
            query="高端訓", mode="discovery", temporal_context=None,
            enable_kg=False, enable_web_search=True, query_id="q",
            tracer=None, iteration_logger=MagicMock(), enable_isolation=False,
            current_context=[{"url": "u1", "title": "站內", "site": "s", "description": "無關"}],
            source_map={1: {"url": "u1"}}, formatted_context="[1] 站內",
            draft=None, review=None, response=None, seen_citation_ids=set(),
            analyst_citations=[], early_return=None, pending_web_formatted=None,
        )
        orch.source_map = state.source_map
        await DeepResearchOrchestrator._phase_actor_critic_loop(orch, state)
        # web doc 已進 context（引擎補源）
        assert any(it.get("url") == "web" for it in state.current_context), "web docs 未進 context"
        orch._process_gap_resolutions.assert_awaited()
        # load-bearing（同 :911 孿生洞判定，對齊 TestMaxIterationsOneBoundary SF-2）：
        # 退一格保命的**目的**是讓 Analyst 於下一輪被再次呼叫消費補源。
        # 修法前 :911 無退一格 → research 只跑 2 次（iter0→1, iter1→2→退出）= 補源無 pass 消費。
        # 修法後退一格 → 多一次 pass → research call count >= 3。
        assert calls["n"] >= 3, (
            f"Analyst 未因 :911 退一格而多跑一次 pass 消費補源（research call count={calls['n']}）—— "
            f"最後一輪補的 web+站內源在 while 邊界被丟棄 = B-LAST 孿生洞未修"
        )


class TestWebAddedNameError:
    """Codex R4 blocker：DRAFT_READY + 空 draft + 無 web gap 路徑，
    :829 guard 讀 web_added 時不得 NameError（每輪迴圈頂顯式初始化為 False）。
    v2 base 無初始化 → NameError；v3 顯式初始化 → 走原 empty-draft 邏輯，iteration 正常推進。
    """

    @pytest.mark.asyncio
    async def test_draft_ready_empty_draft_no_web_no_nameerror(self, monkeypatch):
        """DRAFT_READY + 空 draft + 無 web_search gap → 不拋 NameError，走原 empty-draft 邏輯。

        load-bearing：:829 guard (`if not draft or not draft.strip():`) 讀 `web_added`
        時，web_added 必須已在迴圈頂被初始化（此路徑從不進 SEARCH_REQUIRED 分支 / 從不執行
        4b 賦值）。NameError = init 未顯式落在迴圈頂（Codex R4 blocker 未修）。
        """
        calls = {"n": 0}

        def _research_draft_ready_empty(*a, **k):
            calls["n"] += 1
            # DRAFT_READY + 空 draft + 無 web gap（純常識查詢，無具名實體）
            return types.SimpleNamespace(
                status="DRAFT_READY",
                draft="",          # 空 draft → 觸發 :829 guard → 需讀 web_added
                new_queries=[],
                missing_information=[],
                gap_resolutions=[],  # 無 web gap → helper 不被呼叫 → web_added 僅靠迴圈頂 init
                citations_used=[],
            )

        # 組最小 loop orchestrator（復用 TestMaxIterationsOneBoundary._loop_orch 模式）
        orch = MagicMock()
        orch.logger = MagicMock()
        orch.source_map = {1: {"url": "u1"}}
        orch.formatted_context = "[1] 站內"
        orch.analyst = MagicMock()
        orch.analyst.research = AsyncMock(side_effect=_research_draft_ready_empty)
        orch.analyst.revise = AsyncMock(side_effect=_research_draft_ready_empty)
        import reasoning.orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "retriever_search", AsyncMock(return_value=[]), raising=False)
        orch.source_filter = MagicMock()
        orch.source_filter.filter_and_enrich = MagicMock(return_value=[])
        orch._process_gap_resolutions = AsyncMock()
        orch._format_context_shared = DeepResearchOrchestrator._format_context_shared.__get__(orch)
        orch._resolve_web_search_gaps_in_loop = (
            DeepResearchOrchestrator._resolve_web_search_gaps_in_loop.__get__(orch)
        )
        orch._check_connection = MagicMock()
        orch._send_progress = AsyncMock()
        orch._emit_phase_event = AsyncMock()
        orch._get_current_time_header = MagicMock(return_value="")

        state = types.SimpleNamespace(
            max_iterations=2, iteration=0, reject_count=0,
            query="什麼是通貨膨脹", mode="discovery", temporal_context=None,
            enable_kg=False, enable_web_search=True, query_id="q",
            tracer=None, iteration_logger=MagicMock(), enable_isolation=False,  # Codex R5：base :671 無條件呼叫 state.iteration_logger.log_agent_output，None 會在抵達目標路徑前先 AttributeError
            current_context=[{"url": "u1", "title": "站內", "site": "s", "description": "..."}],
            source_map={1: {"url": "u1"}}, formatted_context="[1] 站內",
            draft=None, review=None, response=None, seen_citation_ids=set(),
            analyst_citations=[], early_return=None,
        )
        orch.source_map = state.source_map

        # 不得拋 NameError（NameError = web_added 未在迴圈頂顯式初始化）。
        try:
            result = await DeepResearchOrchestrator._phase_actor_critic_loop(orch, state)
        except NameError as e:
            pytest.fail(
                f"NameError：web_added 未在迴圈頂顯式初始化（Codex R4 blocker 未修）: {e}"
            )

        # iteration 正常推進（走原 empty-draft 邏輯，不因 web_added 讀值崩潰）。
        # max_iterations=2 + 空 draft 兩輪 → 耗盡後正常結束（early_return 或 draft=None 皆可，
        # 重點是不炸 NameError）。loop 至少跑一輪 research。
        assert calls["n"] >= 1, "research 未被呼叫（loop 未啟動）"
        # ★ web_search gap 從未出現 → _process_gap_resolutions 不被呼叫（非 web 路徑行為保持原樣）。
        orch._process_gap_resolutions.assert_not_awaited()


class TestIsolationSecondaryEmptyReformat:
    """修訂 2（Codex B3）：isolation + web_added + 站內 secondary 空 → :785 else 分支不全量重建，
    web 段直接當 formatted_context（source_map 保持引擎登記，無組合重複、web 不重複）。"""

    @pytest.mark.asyncio
    async def test_isolation_secondary_empty_uses_web_segment_no_rebuild(self):
        WEB_DOC = {"url": "web1", "title": "高端訓 web result", "site": "Web",
                   "description": "[Tier 6 | web_reference] ..."}

        async def _engine_like_append(response, mode, current_context, enable_web_search,
                                      tracer, query_id, web_searched_queries=None):
            current_context.append(WEB_DOC)
            start_idx = max(orch.source_map.keys(), default=0) + 1
            orch.source_map[start_idx] = WEB_DOC   # 引擎登記 web doc（id=2）
            if web_searched_queries is not None:
                for g in response.gap_resolutions:
                    web_searched_queries.add(_normalize_web_query(g.search_query))

        orch = _make_orch_v3(process_side_effect=_engine_like_append)
        orch._format_context_shared = DeepResearchOrchestrator._format_context_shared.__get__(orch)
        orch._get_current_time_header = MagicMock(return_value="")  # 純函式需字串前綴，避免 MagicMock 相加炸
        st = _state()
        st.enable_isolation = True
        st.source_map = {1: {"url": "u1", "title": "站內 doc", "site": "site", "description": "站內"}}
        orch.source_map = st.source_map
        seen = set()
        response = types.SimpleNamespace(
            gap_resolutions=[_gap(GapResolutionType.WEB_SEARCH, "高端訓 是誰")]
        )
        # step 1：helper 兌現 web（isolation → 設 pending_web_formatted，marker id=2）
        web_added = await orch._resolve_web_search_gaps_in_loop(
            response=response, mode=st.mode, state=st,
            enable_web_search=True, web_searched_queries=seen,
            tracer=None, query_id=st.query_id,
        )
        assert web_added is True
        assert getattr(st, "pending_web_formatted", None) is not None

        # step 2：模擬 :785 else 分支（站內 secondary 空）的分流邏輯（逐字對齊 V3-2 4b'）。
        pending_web = getattr(st, "pending_web_formatted", None)
        if getattr(st, "enable_isolation", False) and pending_web:
            st.formatted_context = pending_web
            st.pending_web_formatted = None
        else:
            st.formatted_context, st.source_map = orch._format_context_shared(st.current_context)

        # load-bearing：formatted 只含 web marker [2]（引擎登記 id）+ web doc 標題，不含站內 [1]、無重複 web 區塊。
        # 格式為 "[{id}] {site} - {title}\n{snippet}"（_format_context_shared:217），故驗 [2] + 標題子串。
        assert "[2]" in st.formatted_context, "web marker（引擎登記 id）應在 formatted"
        assert "[1]" not in st.formatted_context, "isolation 下不應含舊站內 doc marker（SEC-6：只看本迭代新 docs）"
        assert st.formatted_context.count("高端訓 web result") == 1, "web 區塊（標題）不應重複（不全量重建）"
        assert "站內 doc" not in st.formatted_context, "isolation 下不應含站內 doc（只看本迭代新 docs）"
        assert st.pending_web_formatted is None, "SF1：消費即清"
        # source_map 未被全量重建覆寫：站內 id=1 + 引擎登記 web id=2 皆在，無雙套 id。
        assert sorted(st.source_map.keys()) == [1, 2]


# ============================================================================
# 防線四（v4）：最後一輪強制 best-effort 寫稿（final-pass 政策）
# ============================================================================
import types
from unittest.mock import AsyncMock, MagicMock
import pytest
from reasoning.prompts.analyst import AnalystPromptBuilder
from reasoning.orchestrator import DeepResearchOrchestrator
from reasoning.schemas_enhanced import GapResolution, GapResolutionType


class TestFinalPassPromptInjection:
    """v4 防線四：final_pass=True 注入「最後一輪必須寫稿」指示；False 時 byte-identical。
    根解重點：注入走無條件路徑，不受 enable_gap_enrichment gate（B1 行為釘死）。"""

    def _build(self, final_pass, enable_gap_enrichment=True):
        b = AnalystPromptBuilder()
        return b.build_research_prompt(
            query="高端訓 是誰",
            formatted_context="[1] 高端訓，品牌行銷作者……",
            mode="discovery",
            enable_gap_enrichment=enable_gap_enrichment,
            enable_web_search=True,
            final_pass=final_pass,
        )

    def test_final_pass_true_injects_directive(self):
        prompt = self._build(final_pass=True)
        # 最後一輪指示的 load-bearing 字串（穩定中文子串，round-trip 等值）
        assert "最後一輪" in prompt
        assert "FINAL PASS" in prompt
        assert "DRAFT_READY" in prompt
        # 禁 SEARCH_REQUIRED 的明確指示存在
        assert "嚴禁" in prompt and "SEARCH_REQUIRED" in prompt
        # 中和情況 A 的明文（修訂 1 步驟 2）
        assert "情況 A" in prompt and "不適用" in prompt
        # 反制 gap「web 未啟用→不寫」（修訂 1 步驟 3）
        assert "即使" in prompt and "web_search 未啟用" in prompt
        # grounding 紅線措辭仍在（不放寬）
        assert "現有資料不足以確認" in prompt
        assert "寧可短報告" in prompt
        # Codex nit：措辭改「可交付的草稿」
        assert "可交付" in prompt

    def test_final_pass_true_survives_gap_enrichment_off(self):
        # 修訂 1 步驟 5（B1 行為釘死）：enable_gap_enrichment=False 時 final-pass 指示仍在
        # （根解 = 無條件路徑注入，不被 gap flag gate 靜默失效）。
        prompt = self._build(final_pass=True, enable_gap_enrichment=False)
        assert "FINAL PASS" in prompt
        assert "最後一輪強制產出" in prompt

    def test_final_pass_false_no_directive(self):
        prompt = self._build(final_pass=False)
        # 非最後一輪：不注入 final-pass 指示區塊
        assert "FINAL PASS" not in prompt
        assert "最後一輪強制產出" not in prompt

    def test_final_pass_false_byte_identical_to_default(self, monkeypatch):
        # final_pass=False 與完全不傳（default）產出 byte-identical（穿透 default 不改行為）。
        # build_research_prompt :78 用 generate_boundary_token()（P1-4 isolation）每次回隨機 token，
        # 是 prompt 內唯一的非確定性來源；固定它才能純粹驗「final_pass 分支不改行為」的契約。
        import reasoning.prompts.analyst as prompts_mod
        monkeypatch.setattr(prompts_mod, "generate_boundary_token", lambda: "FIXED_BOUNDARY_TOKEN")
        b = AnalystPromptBuilder()
        common = dict(
            query="什麼是通貨膨脹",
            formatted_context="[1] ...",
            mode="discovery",
            enable_gap_enrichment=True,
            enable_web_search=True,
        )
        assert b.build_research_prompt(**common, final_pass=False) == b.build_research_prompt(**common)

    def test_final_pass_appended_after_previous_draft(self):
        # 修訂 3（SF2）：組裝順序釘死——帶 previous_draft 的 build 中，
        # FINAL PASS 段必須出現在 previous_draft 段之後（不被 previous_draft 敘事沖淡指令權重）。
        b = AnalystPromptBuilder()
        prompt = b.build_research_prompt(
            query="高端訓 是誰",
            formatted_context="[1] 高端訓，品牌行銷作者……",
            mode="discovery",
            enable_gap_enrichment=True,
            enable_web_search=False,
            previous_draft="舊草稿內容：高端訓曾撰寫品牌行銷文章。",
            final_pass=True,
        )
        # "你之前的分析草稿（參考用）" 是 previous_draft 段的穩定錨點子串（:101）
        prev_draft_marker = "你之前的分析草稿（參考用）"
        final_pass_marker = "FINAL PASS"
        assert prev_draft_marker in prompt, "previous_draft 段未注入"
        assert final_pass_marker in prompt, "FINAL PASS 段未注入"
        assert prompt.rfind(final_pass_marker) > prompt.rfind(prev_draft_marker), (
            "FINAL PASS 段應在 previous_draft 段之後（指令權重優先）"
        )


class TestFinalPassRevisionInjection:
    """v4 修訂 3：revise 路徑孿生死路——build_revision_prompt 也穿透 final_pass、注入同一 helper。"""

    def _revision(self, final_pass):
        b = AnalystPromptBuilder()
        # build_revision_prompt 需要一個 review 物件；用 SimpleNamespace 提供必要欄位。
        review = types.SimpleNamespace(
            critique="需補資料", suggestions=[], logical_gaps=[],
            source_issues=[], mode_compliance=True,
        )
        return b.build_revision_prompt(
            original_draft="舊草稿",
            review=review,
            formatted_context="[1] 高端訓……",
            original_query="高端訓 是誰",
            final_pass=final_pass,
        )

    def test_revision_final_pass_true_injects(self):
        prompt = self._revision(final_pass=True)
        assert "FINAL PASS" in prompt
        assert "DRAFT_READY" in prompt
        assert "嚴禁" in prompt and "SEARCH_REQUIRED" in prompt

    def test_revision_final_pass_false_no_directive(self):
        prompt = self._revision(final_pass=False)
        assert "FINAL PASS" not in prompt


class TestFinalPassLoopWiring:
    """v4 防線四：actor-critic loop 在最後一輪把 final_pass=True 傳進 research/enriched/revise 三家族。
    半真 loop：patch 外部依賴，斷言 research/revise 收到的 final_pass kwarg。
    v2/v3 base 無 final_pass 參數穿透 → 收不到 True（fail）；v4 → True（pass）。"""

    def _loop_orch(self, monkeypatch, research_side_effect):
        orch = MagicMock()
        orch.logger = MagicMock()
        orch.source_map = {1: {"url": "u1"}}
        orch.formatted_context = "[1] 站內"
        orch.analyst = MagicMock()
        orch.analyst.research = AsyncMock(side_effect=research_side_effect)
        orch.analyst.revise = AsyncMock(side_effect=research_side_effect)
        import reasoning.orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "retriever_search", AsyncMock(return_value=[]), raising=False)
        orch.source_filter = MagicMock()
        orch.source_filter.filter_and_enrich = MagicMock(return_value=[])
        orch._process_gap_resolutions = AsyncMock()
        orch._resolve_web_search_gaps_in_loop = AsyncMock(return_value=False)
        orch._format_context_shared = DeepResearchOrchestrator._format_context_shared.__get__(orch)
        orch._build_retrieval_evidence_summary = MagicMock(return_value=None)
        orch._check_connection = MagicMock()
        orch._send_progress = AsyncMock()
        orch._emit_phase_event = AsyncMock()
        orch._get_current_time_header = MagicMock(return_value="")
        orch._format_friendly_no_data_result = MagicMock(return_value=[{"@type": "Item"}])
        orch._format_error_result = MagicMock(return_value=[{"@type": "Item"}])
        return orch

    def _base_state(self, max_iterations):
        return types.SimpleNamespace(
            max_iterations=max_iterations, iteration=0, reject_count=0,
            query="高端訓", mode="discovery", temporal_context=None,
            enable_kg=False, enable_web_search=True, query_id="q",
            tracer=None, iteration_logger=MagicMock(), enable_isolation=False,
            current_context=[{"url": "u1", "title": "站內", "site": "s", "description": "無關"}],
            source_map={1: {"url": "u1"}}, formatted_context="[1] 站內",
            draft=None, review=None, response=None, seen_citation_ids=set(),
            analyst_citations=[], early_return=None, pending_web_formatted=None,
        )

    @pytest.mark.asyncio
    async def test_final_pass_true_on_last_iteration(self, monkeypatch):
        # max_iterations=1 → 第一輪即最後一輪 → research 收到 final_pass=True。
        seen_flags = []

        def _research(*a, **k):
            seen_flags.append(k.get("final_pass"))
            # DRAFT_READY 讓 loop 正常收（避免 SEARCH_REQUIRED 補搜路徑干擾）
            return types.SimpleNamespace(
                status="DRAFT_READY", draft="草稿內容", new_queries=[],
                missing_information=[], gap_resolutions=[], citations_used=[],
                reasoning_chain="",
            )

        orch = self._loop_orch(monkeypatch, _research)
        # Critic 一輪 PASS 收斂
        orch.critic = MagicMock()
        orch.critic.review = AsyncMock(return_value=types.SimpleNamespace(
            status="PASS", critique="", suggestions=[], logical_gaps=[],
            source_issues=[], mode_compliance=True,
        ))
        state = self._base_state(max_iterations=1)
        orch.source_map = state.source_map
        await DeepResearchOrchestrator._phase_actor_critic_loop(orch, state)
        assert seen_flags, "research 未被呼叫"
        assert seen_flags[0] is True, f"最後一輪 research 應收 final_pass=True，實收 {seen_flags[0]}"

    @pytest.mark.asyncio
    async def test_final_pass_false_on_non_last_iteration(self, monkeypatch):
        # max_iterations=3 + 第一輪 DRAFT_READY 直接 PASS 收斂 → 第一輪非最後 → final_pass=False。
        seen_flags = []

        def _research(*a, **k):
            seen_flags.append(k.get("final_pass"))
            return types.SimpleNamespace(
                status="DRAFT_READY", draft="草稿", new_queries=[],
                missing_information=[], gap_resolutions=[], citations_used=[],
                reasoning_chain="",
            )

        orch = self._loop_orch(monkeypatch, _research)
        orch.critic = MagicMock()
        orch.critic.review = AsyncMock(return_value=types.SimpleNamespace(
            status="PASS", critique="", suggestions=[], logical_gaps=[],
            source_issues=[], mode_compliance=True,
        ))
        state = self._base_state(max_iterations=3)
        orch.source_map = state.source_map
        await DeepResearchOrchestrator._phase_actor_critic_loop(orch, state)
        assert seen_flags[0] is False, f"第一輪（非最後）應 final_pass=False，實收 {seen_flags[0]}"

    @pytest.mark.asyncio
    async def test_final_pass_true_after_v3_extra_pass_retreat(self, monkeypatch):
        # v3 extra-pass 退一格情境：max_iterations=1 + SEARCH_REQUIRED + web_added 退一格 →
        # 下一輪（退一格後）final_pass 仍為 True（每輪頂重算，退一格自然蓋到）。
        seen_flags = []
        call = {"n": 0}

        def _research(*a, **k):
            seen_flags.append(k.get("final_pass"))
            call["n"] += 1
            if call["n"] == 1:
                # 第一輪：SEARCH_REQUIRED（觸發站內補搜 + web_added 退一格）
                return types.SimpleNamespace(
                    status="SEARCH_REQUIRED", draft="", new_queries=["補搜"],
                    missing_information=["缺"], gap_resolutions=[], citations_used=[],
                    reasoning_chain="",
                )
            # 退一格後的下一輪：DRAFT_READY 收斂
            return types.SimpleNamespace(
                status="DRAFT_READY", draft="best-effort 草稿", new_queries=[],
                missing_information=[], gap_resolutions=[], citations_used=[],
                reasoning_chain="",
            )

        orch = self._loop_orch(monkeypatch, _research)
        # helper 回 True（模擬 web_added，觸發 :980 退一格）
        orch._resolve_web_search_gaps_in_loop = AsyncMock(return_value=True)
        orch.critic = MagicMock()
        orch.critic.review = AsyncMock(return_value=types.SimpleNamespace(
            status="PASS", critique="", suggestions=[], logical_gaps=[],
            source_issues=[], mode_compliance=True,
        ))
        state = self._base_state(max_iterations=1)
        orch.source_map = state.source_map
        await DeepResearchOrchestrator._phase_actor_critic_loop(orch, state)
        # 兩輪 research（初次 SEARCH_REQUIRED + 退一格後的下一輪），兩輪 final_pass 都應為 True
        # （iteration+1>=max_iterations，max_iterations=1 時第 0 輪即 True；退一格後仍第 0 輪 → True）
        assert len(seen_flags) >= 2, f"退一格後應有第二輪 research，實 {len(seen_flags)}"
        assert all(f is True for f in seen_flags), (
            f"max_iterations=1 每輪都是最後一輪，final_pass 應全 True，實 {seen_flags}"
        )

    @pytest.mark.asyncio
    async def test_enriched_rerun_receives_final_pass(self, monkeypatch):
        # 修訂 4：驅動 gap_resolution_added_data=True（模擬 Stage 5 補料）→ enriched re-run 觸發。
        # 斷言 enriched re-run 呼叫（research_with_enriched_data 路徑）收到 final_pass。
        # max_iterations=1 → 該輪即最後一輪 → enriched re-run final_pass=True。
        enriched_flags = []
        call = {"n": 0}

        def _research(*a, **k):
            call["n"] += 1
            # 第一次 = 初次 research（回 gap_resolutions 觸發 Stage 5 補料 + 空 draft 讓流程進 enriched re-run）
            # 第二次 = enriched re-run（記錄它的 final_pass）
            if call["n"] >= 2:
                enriched_flags.append(k.get("final_pass"))
                return types.SimpleNamespace(
                    status="DRAFT_READY", draft="enriched 後的草稿", new_queries=[],
                    missing_information=[], gap_resolutions=[], citations_used=[],
                    reasoning_chain="",
                )
            # 初次：帶一個 web_search gap_resolution，讓 orchestrator 走 Stage 5 補料 → enriched re-run
            return types.SimpleNamespace(
                status="DRAFT_READY", draft="初稿", new_queries=[],
                missing_information=[],
                gap_resolutions=[GapResolution(
                    gap_type="current_data",
                    resolution=GapResolutionType.WEB_SEARCH,
                    reason="缺近況，需要網路搜尋補充",
                    requires_web_search=True,
                    search_query="高端訓 近況",
                )],
                citations_used=[], reasoning_chain="",
            )

        orch = self._loop_orch(monkeypatch, _research)
        # 模擬 Stage 5 補料成功：_process_gap_resolutions 讓 current_context 增長 → gap_resolution_added_data=True。
        # executor 親讀 :1069 gap_resolution_added_data = context_after > context_before，
        # 用 side_effect 在被呼叫時 append 一筆到 state.current_context（讓 context_after > context_before）。
        state = self._base_state(max_iterations=1)

        async def _add_data(*a, **k):
            state.current_context.append({"url": "web1", "title": "高端訓 Yahoo 專欄", "site": "yahoo", "description": "品牌行銷作者"})
        orch._process_gap_resolutions = AsyncMock(side_effect=_add_data)
        orch.critic = MagicMock()
        orch.critic.review = AsyncMock(return_value=types.SimpleNamespace(
            status="PASS", critique="", suggestions=[], logical_gaps=[],
            source_issues=[], mode_compliance=True,
        ))
        orch.source_map = state.source_map
        await DeepResearchOrchestrator._phase_actor_critic_loop(orch, state)
        assert enriched_flags, "enriched re-run 未被呼叫（gap_resolution_added_data 未驅動）"
        assert enriched_flags[0] is True, (
            f"最後一輪 enriched re-run 應收 final_pass=True，實收 {enriched_flags[0]}"
        )

    @pytest.mark.asyncio
    async def test_final_pass_true_last_iter_without_web_added(self, monkeypatch):
        # 修訂 5：web_added=False（沒補 web）時最後一輪仍 final_pass=True 對照 case。
        # 驗 final_pass 判定純看 iteration+1>=max_iterations，與 web_added / 退一格無關。
        # 建模脆弱性註記：本 case 與 test_final_pass_true_after_v3_extra_pass_retreat（web_added=True）
        # 成對——一個退一格、一個不退，兩者最後一輪都 final_pass=True，確保判定不依賴 web pass。
        seen_flags = []

        def _research(*a, **k):
            seen_flags.append(k.get("final_pass"))
            return types.SimpleNamespace(
                status="DRAFT_READY", draft="草稿", new_queries=[],
                missing_information=[], gap_resolutions=[], citations_used=[],
                reasoning_chain="",
            )

        orch = self._loop_orch(monkeypatch, _research)
        orch._resolve_web_search_gaps_in_loop = AsyncMock(return_value=False)  # 不補 web
        orch.critic = MagicMock()
        orch.critic.review = AsyncMock(return_value=types.SimpleNamespace(
            status="PASS", critique="", suggestions=[], logical_gaps=[],
            source_issues=[], mode_compliance=True,
        ))
        state = self._base_state(max_iterations=1)
        orch.source_map = state.source_map
        await DeepResearchOrchestrator._phase_actor_critic_loop(orch, state)
        assert seen_flags[0] is True, (
            f"web_added=False 但最後一輪仍應 final_pass=True（判定不依賴 web pass），實收 {seen_flags[0]}"
        )

    @pytest.mark.asyncio
    async def test_revise_receives_final_pass_on_last_iteration(self, monkeypatch):
        # 修訂 2（SF1）：最後一輪走 review.status=="REJECT" → analyst.revise() 路徑，
        # 斷言 revise 被呼叫且收到 final_pass=True（最後一輪）。
        # AR R3（Codex + in-house 兩家獨立同處方）：max_iterations 必須 = 2——revise 只發生在
        # 「上一輪 REJECT → 下一輪迴圈頂（:739）」，max=1 時迴圈在 :1263 iteration+=1 後即退出，
        # revise 永不觸發。展開驗證：iter 0（final_pass=False）research → REJECT → iter 1
        # 進 revise 分支（final_pass = 1+1>=2 = True）→ critic 第二次 PASS 收斂。
        revise_kwargs = {}

        async def _revise(*a, **k):
            revise_kwargs.update(k)
            return types.SimpleNamespace(
                status="DRAFT_READY", draft="revise 後草稿", new_queries=[],
                missing_information=[], gap_resolutions=[], citations_used=[],
                reasoning_chain="",
            )

        orch = self._loop_orch(monkeypatch, lambda *a, **k: types.SimpleNamespace(
            status="DRAFT_READY", draft="初稿", new_queries=[],
            missing_information=[], gap_resolutions=[], citations_used=[],
            reasoning_chain="",
        ))
        orch.analyst.revise = AsyncMock(side_effect=_revise)
        # Critic 第一輪 REJECT → 觸發 revise；第二次（若有）PASS 收斂
        reject_called = {"n": 0}
        async def _critic_review(*a, **k):
            reject_called["n"] += 1
            if reject_called["n"] == 1:
                return types.SimpleNamespace(
                    status="REJECT", critique="需補充", suggestions=["補充資料"],
                    logical_gaps=[], source_issues=[], mode_compliance=True,
                )
            return types.SimpleNamespace(
                status="PASS", critique="", suggestions=[],
                logical_gaps=[], source_issues=[], mode_compliance=True,
            )
        orch.critic = MagicMock()
        orch.critic.review = AsyncMock(side_effect=_critic_review)
        state = self._base_state(max_iterations=2)  # AR R3：max=1 到不了 revise，見上方註解
        orch.source_map = state.source_map
        await DeepResearchOrchestrator._phase_actor_critic_loop(orch, state)
        assert revise_kwargs, "analyst.revise 未被呼叫（REJECT 路徑未觸發）"
        assert revise_kwargs.get("final_pass") is True, (
            f"最後一輪 revise 應收 final_pass=True，實收 {revise_kwargs.get('final_pass')}"
        )
