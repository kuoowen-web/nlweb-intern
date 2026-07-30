"""票 2026-07-28-m：LR tier6 進池相關性 gate 測試。

垃圾 wiki 池 fixture 複刻沾邊形狀：查具名人物，tier6 補回的是另一具名人物 / 動漫百科。
BABLoopEngine 綁真 gate method，LLM 呼叫 patch core.llm.ask_llm（共用核心內局部 import）。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from reasoning.live_research.loop_engine import BABLoopEngine
from reasoning.schemas_live import EvidencePoolEntry


@pytest.mark.asyncio
async def test_execute_search_direct_web_tagged_source_web():
    """🔧 R1：_execute_search direct-web 路徑進池的 entry.source 必須是 'web'（非 default internal）。

    現況 bug：web_items 與 internal 混 extend 進 all_items，建 EvidencePoolEntry 不傳
    source → 全落 default 'internal' → 本 plan gate 掃不到（source in {web,wiki} 漏這批）。
    🔧 R3：fixture 自含（_mk_search_engine，非 Task 1 的 make_engine）——本 Task 先落地。
    """

    def _mk_search_engine():
        """Task 0.5 自含最小 fixture（與 Task 1 make_engine 不同名防撞；
        欄位集 = R2 in-house 席實跑證明足以跑通 _execute_search direct-web 路徑）。"""
        handler = MagicMock()
        handler.query_params = {}
        handler.enable_web_search = True
        engine = BABLoopEngine.__new__(BABLoopEngine)
        engine.handler = handler
        engine.evidence_pool = {}
        engine._url_to_id = {}
        engine._evidence_counter = 0
        engine._current_iteration = 1
        engine.state = None
        engine.executed_searches = []
        engine._emit_narration = AsyncMock()
        return engine

    engine = _mk_search_engine()

    # seed source_strategy="web" 直走 direct-web 路徑
    class _Seed:
        query = "邱啟新"
        source_strategy = "web"
    # _execute_web_search patch 回一筆 web tuple（google_search_client 形狀）
    web_tuple = ("https://web.example/1", "{}", "邱啟新專訪", "web.example", [])
    with patch.object(
        engine, "_execute_web_search",
        new=AsyncMock(return_value=[web_tuple]),
    ):
        await engine._execute_search([_Seed()])

    # 進池那筆 source 應為 'web'
    entries = list(engine.evidence_pool.values())
    assert len(entries) == 1
    assert entries[0].source == "web", (
        f"direct-web 進池應標 source='web'，實得 {entries[0].source!r}（錯標 internal bug）"
    )


QUERY = "請找出台大邱啟新副教授的公開發言"


def make_engine():
    """最小可測 engine：綁真 gate method + 真 _emit_narration（no-op handler）。"""
    handler = MagicMock()
    handler.query_params = {}
    handler.enable_web_search = True
    engine = BABLoopEngine.__new__(BABLoopEngine)
    engine.handler = handler
    # 🔧 R1：LR gate 用 module-level `logger`（loop_engine 頂部 logging.getLogger），
    # 不碰 self.logger（那是 DR 端）。
    engine.evidence_pool = {}
    engine._url_to_id = {}
    engine._relevance_gate_judged_ids = set()
    engine._relevance_gate_narrated = False
    engine._emit_narration = AsyncMock()
    return engine


def _mk_loop_engine():
    """run_loop 層測試 fixture：真建構 engine + mock 掉 Phase 1/2/3 與收斂判定，
    一輪收斂；gate 接線與（除非 patch）gate 本體走真 code。"""
    engine = BABLoopEngine(associator=MagicMock(), handler=MagicMock(), max_iterations=1)
    engine.handler.query_params = {}
    engine._check_connection = MagicMock(return_value="online")
    engine._emit_phase = AsyncMock()
    engine._emit_narration = AsyncMock()
    engine._run_derive_phase = AsyncMock(
        return_value=MagicMock(search_seeds=[], narration="")
    )
    engine._execute_search = AsyncMock(return_value=("", {}))
    engine._run_mini_reasoning = AsyncMock(return_value=True)
    engine.enable_consistency_monitor = False
    engine.associator.refine_context_map = AsyncMock(
        return_value=MagicMock(updated_context_map=MagicMock(), narration="", is_stable=True)
    )
    return engine   # 🔧 R6-post-AR：刪重複的第二次 _emit_narration 賦值（上方已設）


def _entry(eid, title, url, source, snippet=""):
    return EvidencePoolEntry(
        evidence_id=eid, title=title, url=url,
        source_domain="wikipedia.org", snippet=snippet, source=source,
    )


def seed_garbage_wiki(engine):
    """3 筆沾邊 wiki（全 tier6）。"""
    for eid, (t, u) in enumerate([
        ("李登輝", "https://zh.wikipedia.org/wiki/李登輝"),
        ("彭明敏", "https://zh.wikipedia.org/wiki/彭明敏"),
        ("某動漫作品", "https://zh.wikipedia.org/wiki/某動漫作品"),
    ], start=1):
        engine.evidence_pool[eid] = _entry(eid, t, u, "wiki")
        engine._url_to_id[u] = eid


def seed_mixed(engine):
    """2 筆站內（internal，不該被判）+ 2 筆垃圾 wiki（tier6）。"""
    engine.evidence_pool[1] = _entry(1, "邱啟新談都更", "https://news/1", "internal")
    engine.evidence_pool[2] = _entry(2, "邱啟新公聽會", "https://news/2", "internal")
    engine.evidence_pool[3] = _entry(3, "李登輝", "https://wiki/李登輝", "wiki")
    engine.evidence_pool[4] = _entry(4, "某動漫", "https://wiki/anime", "wiki")
    for eid, e in engine.evidence_pool.items():
        engine._url_to_id[e.url] = eid


class TestLRRelevanceGate:

    @pytest.mark.asyncio
    async def test_all_tier6_irrelevant_removed(self):
        """全 tier6 不相關 → 全刪，池變空（下游 C-1 據此走查無）。"""
        engine = make_engine()
        seed_garbage_wiki(engine)
        with patch(
            "core.llm.ask_llm",
            new=AsyncMock(return_value={"irrelevant_ids": [1, 2, 3]}),
        ):
            await engine._relevance_gate_evidence_pool(QUERY)
        assert engine.evidence_pool == {}
        assert engine._url_to_id == {}
        engine._emit_narration.assert_awaited()  # 剔除有透明化旁白

    @pytest.mark.asyncio
    async def test_partial_removes_only_irrelevant_tier6(self):
        """混合池：站內不判、tier6 剔不相關；站內 2 筆 + 相關 tier6 留。"""
        engine = make_engine()
        seed_mixed(engine)
        # 只送判 tier6（eid 3,4）；LLM 判 4 不相關、3 相關
        with patch(
            "core.llm.ask_llm",
            new=AsyncMock(return_value={"irrelevant_ids": [4]}),
        ):
            await engine._relevance_gate_evidence_pool(QUERY)
        assert set(engine.evidence_pool.keys()) == {1, 2, 3}  # 站內 2 + 相關 wiki 1
        assert "https://wiki/anime" not in engine._url_to_id

    @pytest.mark.asyncio
    async def test_internal_never_judged(self):
        """池全站內、零 tier6 → gate no-op、零 LLM call。"""
        engine = make_engine()
        engine.evidence_pool[1] = _entry(1, "站內", "https://n/1", "internal")
        engine._url_to_id["https://n/1"] = 1
        with patch("core.llm.ask_llm", new=AsyncMock()) as mock_llm:
            await engine._relevance_gate_evidence_pool(QUERY)
        mock_llm.assert_not_awaited()
        assert set(engine.evidence_pool.keys()) == {1}

    @pytest.mark.asyncio
    async def test_all_relevant_pool_untouched(self):
        engine = make_engine()
        seed_garbage_wiki(engine)
        with patch(
            "core.llm.ask_llm", new=AsyncMock(return_value={"irrelevant_ids": []})
        ):
            await engine._relevance_gate_evidence_pool(QUERY)
        assert set(engine.evidence_pool.keys()) == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_llm_failure_fails_open(self):
        """LLM 掛 → fail-open 全保留（不誤殺）+ 🔧 R1 不標 judged（同 engine 內下輪重判 🔧 R3）。"""
        engine = make_engine()
        seed_garbage_wiki(engine)
        with patch(
            "core.llm.ask_llm", new=AsyncMock(side_effect=RuntimeError("down"))
        ):
            await engine._relevance_gate_evidence_pool(QUERY)
        assert set(engine.evidence_pool.keys()) == {1, 2, 3}  # 池不動
        # 🔧 R1（SF1）：fail-open 批未標記已判 → 同 engine 內下輪會重判（judged_ids 應為空）
        assert engine._relevance_gate_judged_ids == set()

    @pytest.mark.asyncio
    async def test_fail_open_batch_rejudged_next_pass(self):
        """🔧 R1（SF1）：fail-open 那輪不標 judged → **同 engine** 下輪 LLM 恢復時該批被重判。

        🔧 R3 契約邊界：本測試證的是「同 engine 內」重判；跨 engine（Stage 2 seed）
        fail-open 批一律豁免不重判（B-R3-1 裁決，見 test_seed_pool_not_rejudged + Step 1.3）。
        """
        engine = make_engine()
        seed_garbage_wiki(engine)
        # 第一輪 LLM 掛（fail-open，不標 judged）；第二輪恢復並判 1,2,3 不相關
        responses = [RuntimeError("down"), {"irrelevant_ids": [1, 2, 3]}]

        async def _flaky(*a, **k):
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        with patch("core.llm.ask_llm", new=_flaky):
            await engine._relevance_gate_evidence_pool(QUERY)   # fail-open：池不動、未標判
            assert set(engine.evidence_pool.keys()) == {1, 2, 3}
            await engine._relevance_gate_evidence_pool(QUERY)   # 重判 → 剔光
        assert engine.evidence_pool == {}

    @pytest.mark.asyncio
    async def test_second_pass_does_not_rejudge(self):
        """跨輪不重判：第一輪判過的 tier6 第二輪不再送判。"""
        engine = make_engine()
        seed_garbage_wiki(engine)
        mock = AsyncMock(return_value={"irrelevant_ids": []})
        with patch("core.llm.ask_llm", new=mock):
            await engine._relevance_gate_evidence_pool(QUERY)  # 判 1,2,3
            await engine._relevance_gate_evidence_pool(QUERY)  # 無新增 → skip
        assert mock.await_count == 1

    @pytest.mark.asyncio
    async def test_direct_web_source_entry_is_judged(self):
        """🔧 R1：_execute_search direct-web 進池的 entry（source=='web'）也被 gate 判。

        接縫測試：Task 0.5 修 direct-web 標 source='web' 後，這批 tier6 必須被 gate 掃到
        （不再因錯標 internal 漏判）。fixture 直接以 source='web' 進池模擬 Task 0.5 後狀態。
        """
        engine = make_engine()
        engine.evidence_pool[1] = _entry(
            1, "李登輝", "https://web.example/lee", "web"  # source='web'（direct-web 標記後）
        )
        engine._url_to_id["https://web.example/lee"] = 1
        with patch(
            "core.llm.ask_llm", new=AsyncMock(return_value={"irrelevant_ids": [1]})
        ):
            await engine._relevance_gate_evidence_pool(QUERY)
        assert engine.evidence_pool == {}  # source='web' 被判入 gate 並剔除

    @pytest.mark.asyncio
    async def test_seed_pool_not_rejudged(self):
        """🔧 R2（B-R2-2）：Stage 2 seed 進來的舊池 eid 不重判、不被刪。

        用真 __init__（走 seed_evidence_pool 參數）驗預載，不用 make_engine bypass——
        測的是「__init__ 把 seed keys 預載進 judged set」這個實作本身。
        🔧 R3（B-R3-1）：seed 豁免是**無條件**的——含前 engine gate fail-open 未判成
        的批（fail-open ≠ 沒被引用，重判刪除 = dangling；裁決見 Step 1.3）。
        """
        seed = {
            1: _entry(1, "李登輝", "https://wiki/李登輝", "wiki"),      # 前 stage 已判過的 tier6
            2: _entry(2, "站內舊文", "https://n/old", "internal"),
        }
        engine = BABLoopEngine(
            associator=MagicMock(), handler=MagicMock(),
            seed_evidence_pool=seed, seed_counter=2,
        )
        engine.handler.query_params = {}
        engine._emit_narration = AsyncMock()
        # __init__ 預載：seed 全部 eid 已標已判
        assert engine._relevance_gate_judged_ids == {1, 2}
        # 本 engine 新進一筆垃圾 wiki（模擬本 topic 的 tier6 補源）
        engine.evidence_pool[3] = _entry(3, "彭明敏", "https://wiki/彭明敏", "wiki")
        engine._url_to_id["https://wiki/彭明敏"] = 3
        # LLM 刻意幻覺回報 [1, 3]（含 seed id 1）——驗 clamp 只認實際送判的 3
        mock_llm = AsyncMock(return_value={"irrelevant_ids": [1, 3]})
        with patch("core.llm.ask_llm", new=mock_llm):
            await engine._relevance_gate_evidence_pool(QUERY)
        # seed 未被送判：digest（ask_llm 第一參數 prompt）只含新進 [3]、不含 seed [1]
        prompt_sent = mock_llm.await_args[0][0]
        assert "[3]" in prompt_sent and "[1]" not in prompt_sent
        # seed eid 未被刪（即使 LLM 幻覺點名 1，clamp 到 judged_ids={3} 保護 seed）
        assert 1 in engine.evidence_pool          # seed wiki 存活（未重判、幻覺點名也剔不掉）
        assert 2 in engine.evidence_pool          # seed internal 存活
        assert 3 not in engine.evidence_pool      # 新進垃圾被剔

    @pytest.mark.asyncio
    async def test_gate_cancel_propagates_from_run_loop(self):
        """🔧 R5（B-R5-1 改寫，原 R2 版測 _run_gap_routing_phase 冒泡）：gate 內
        ResearchCancelledError 必須從 run_loop 冒泡（穿迴圈 finally，不被吞）。

        對照組：gate 拋一般 Exception → run_loop 正常收斂（gate 段自帶 except 吸收）。
        """
        from reasoning.orchestrator_base import ResearchCancelledError

        engine = _mk_loop_engine()
        with patch.object(
            engine, "_relevance_gate_evidence_pool",
            new=AsyncMock(side_effect=ResearchCancelledError()),
        ):
            with pytest.raises(ResearchCancelledError):
                await engine.run_loop(query=QUERY, existing_context_map=MagicMock())

        # 對照組：一般 Exception 不冒泡（non-fatal 吸收，一輪收斂正常返回）
        engine2 = _mk_loop_engine()
        with patch.object(
            engine2, "_relevance_gate_evidence_pool",
            new=AsyncMock(side_effect=RuntimeError("gate down")),
        ):
            await engine2.run_loop(query=QUERY, existing_context_map=MagicMock())  # 不 raise 即 PASS

    @pytest.mark.asyncio
    async def test_gate_runs_without_gap_resolutions(self):
        """🔧 R5（B-R5-1，Codex 建議）：無 gap_resolutions 輪 gate 仍被呼叫並剔除
        direct-web 垃圾——鎖「無條件接線」契約，防 executor 把 gate 放回條件式路徑
        （如 _run_gap_routing_phase：無 gap 輪不跑 → direct-web 批裸奔，本測試即轉紅）。
        """
        engine = _mk_loop_engine()
        # direct-web 批：Phase 2 進池形狀（source='web'，Task 0.5 標記後）；本輪零 gap
        engine.evidence_pool[1] = _entry(1, "李登輝", "https://web.example/lee", "web")
        engine._url_to_id["https://web.example/lee"] = 1
        with patch(
            "core.llm.ask_llm", new=AsyncMock(return_value={"irrelevant_ids": [1]})
        ):
            await engine.run_loop(query=QUERY, existing_context_map=MagicMock())
        assert 1 not in engine.evidence_pool  # 無 gap 輪 gate 照跑、垃圾被剔

    @pytest.mark.asyncio
    async def test_phase3_injected_batch_swept_same_iteration(self):
        """🔧 R6-post-AR（R6-SF-1）：Phase 3 執行中新進池的 tier6 批，同輪就被 gate 掃。

        鎖「gate 在 Phase 3 之後」這半個契約——若 executor 錯把 gate 放 Phase 2 後、
        Phase 3 前，本測試轉紅（Phase 3 內 gap routing 新進的批會漏掃到 refine 前）；
        與 test_gate_runs_without_gap_resolutions（鎖無條件）合起來把插點前後都焊死。
        """
        engine = _mk_loop_engine()

        async def _inject_during_phase3(*a, **k):
            # 模擬 Phase 3 內 gap routing 進池（時點 = mini 執行中）
            engine.evidence_pool[7] = _entry(7, "某動漫", "https://wiki/anime7", "wiki")
            engine._url_to_id["https://wiki/anime7"] = 7
            return True

        engine._run_mini_reasoning = AsyncMock(side_effect=_inject_during_phase3)
        with patch(
            "core.llm.ask_llm", new=AsyncMock(return_value={"irrelevant_ids": [7]})
        ):
            await engine.run_loop(query=QUERY, existing_context_map=MagicMock())
        assert 7 not in engine.evidence_pool  # Phase 3 內進池的批同輪被掃、被剔


import os

REAL_LLM = os.getenv("NLWEB_ALLOW_REAL_LLM") == "1"


@pytest.mark.skipif(not REAL_LLM, reason="opt-in 真 LLM：NLWEB_ALLOW_REAL_LLM=1")
class TestLRRelevanceGateRealLLM:
    """真 low-tier LLM 驗判定語意（不 mock ask_llm）。燒少量 low-tier 錢。"""

    @pytest.mark.asyncio
    async def test_real_judges_wrong_person_irrelevant(self):
        """查「邱啟新」，池含「李登輝/彭明敏/動漫」→ 真模型應判全部不相關。"""
        engine = make_engine()
        seed_garbage_wiki(engine)
        await engine._relevance_gate_evidence_pool(QUERY)
        # 全垃圾應被剔光（允許模型偶留 1 筆——但至少剔除多數）
        assert len(engine.evidence_pool) <= 1

    @pytest.mark.asyncio
    async def test_real_keeps_relevant_and_drops_garbage(self):
        """混合池：相關 tier6 留、垃圾 tier6 剔（對照組防矯枉過正）。"""
        engine = make_engine()
        engine.evidence_pool[1] = _entry(
            1, "邱啟新談都市更新政策", "https://zh.wikipedia.org/wiki/都市更新",
            "wiki", snippet="都市更新，指都市計畫範圍內重建...邱啟新副教授指出...",
        )
        engine.evidence_pool[2] = _entry(
            2, "李登輝", "https://zh.wikipedia.org/wiki/李登輝",
            "wiki", snippet="李登輝，中華民國政治人物，曾任總統...",
        )
        engine._url_to_id = {e.url: eid for eid, e in engine.evidence_pool.items()}
        await engine._relevance_gate_evidence_pool(QUERY)
        # 李登輝（eid 2）應被剔；相關的都更頁應留
        assert 2 not in engine.evidence_pool
