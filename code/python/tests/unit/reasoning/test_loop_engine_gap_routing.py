"""Gap routing port (Track C C4): LR _process_gap_resolutions_lr.

移植自 DR `_process_gap_resolutions` (reasoning/orchestrator.py:1864-1989)，只 handle
4 類 (LLM_KNOWLEDGE / WIKIPEDIA / WEB_SEARCH / INTERNAL_SEARCH)。Stock/weather/company
6 類 LR 明示砍 — log skip 不 raise (fail-loud-with-info)。

F-2 dual-guard 紀律: 同時 monkeypatch `WIKIPEDIA_AVAILABLE` (module flag) 與
`is_available()` (instance method) 兩條 — 確保 CONFIG yaml `tier_6.wikipedia.enabled`
預設 false 不阻擋 unit test。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


def _make_engine(handler_kwargs=None):
    """F-6 紀律: 不再 inject engine.state — Track C 只寫 engine.evidence_pool dict。"""
    from reasoning.live_research.loop_engine import BABLoopEngine
    handler_kw = {
        "query_params": {},
        "site": "all",
        "enable_web_search": True,
        "enable_gap_enrichment": True,
    }
    if handler_kwargs:
        handler_kw.update(handler_kwargs)
    handler = MagicMock(**handler_kw)
    engine = BABLoopEngine(
        associator=MagicMock(), handler=handler, max_iterations=1,
    )
    engine._current_iteration = 1
    return engine


def _make_gap(resolution_type, **kwargs):
    from reasoning.schemas_enhanced import GapResolution
    return GapResolution(
        gap_type=kwargs.get("gap_type", "definition"),
        resolution=resolution_type,
        search_query=kwargs.get("search_query", None),
        llm_answer=kwargs.get("llm_answer", None),
        topic=kwargs.get("topic", None),
    )


@pytest.mark.asyncio
async def test_gap_routing_llm_knowledge_creates_virtual_doc():
    """LLM_KNOWLEDGE gap → EvidencePoolEntry(source='llm_knowledge', url='urn:llm:knowledge:*')."""
    from reasoning.schemas_enhanced import GapResolutionType
    engine = _make_engine()
    gap = _make_gap(
        GapResolutionType.LLM_KNOWLEDGE,
        topic="energy_transition",
        llm_answer="能源轉型是指…",
    )
    await engine._process_gap_resolutions_lr([gap])
    llm_entries = [e for e in engine.evidence_pool.values() if e.source == "llm_knowledge"]
    assert len(llm_entries) == 1
    assert llm_entries[0].url.startswith("urn:llm:knowledge:")
    assert "[Tier 6 | llm_knowledge]" in llm_entries[0].snippet


@pytest.mark.asyncio
async def test_gap_routing_wikipedia_uses_client(monkeypatch):
    """WIKIPEDIA gap → WikipediaClient.search() 被叫，結果建 source='wiki' entry.

    F-2 dual-guard 紀律: monkeypatch module flag + instance method 兩條。
    """
    from reasoning.schemas_enhanced import GapResolutionType
    engine = _make_engine()

    async def fake_search(self, query, **kwargs):
        return [{
            "title": "Energiewende",
            "link": "https://en.wikipedia.org/wiki/Energiewende",
            "snippet": "Energy transition in Germany...",
        }]

    monkeypatch.setattr(
        "retrieval_providers.wikipedia_client.WIKIPEDIA_AVAILABLE",
        True,
    )
    monkeypatch.setattr(
        "retrieval_providers.wikipedia_client.WikipediaClient.search",
        fake_search,
    )
    monkeypatch.setattr(
        "retrieval_providers.wikipedia_client.WikipediaClient.is_available",
        lambda self: True,
    )

    gap = _make_gap(GapResolutionType.WIKIPEDIA, search_query="Energiewende")
    await engine._process_gap_resolutions_lr([gap])
    wiki_entries = [e for e in engine.evidence_pool.values() if e.source == "wiki"]
    assert len(wiki_entries) == 1
    assert "wikipedia.org" in wiki_entries[0].url
    assert "[Tier 6 | encyclopedia]" in wiki_entries[0].snippet


@pytest.mark.asyncio
async def test_gap_routing_web_search_uses_existing_method(monkeypatch):
    """WEB_SEARCH gap → _execute_web_search 被叫 + source='web' entry."""
    from reasoning.schemas_enhanced import GapResolutionType
    engine = _make_engine()

    async def fake_web_search(self, query):
        # F-11 紀律: _execute_web_search 回傳 list of tuples (來自 GoogleSearchClient.search_all_sites)
        return [
            ("https://example.com/a", '{"description": "snippet a"}', "Title A", "example.com", []),
        ]

    monkeypatch.setattr(
        type(engine), "_execute_web_search", fake_web_search,
    )
    gap = _make_gap(GapResolutionType.WEB_SEARCH, search_query="德國風電政策 2024")
    await engine._process_gap_resolutions_lr([gap])
    web_entries = [e for e in engine.evidence_pool.values() if e.source == "web"]
    assert len(web_entries) == 1
    assert web_entries[0].url == "https://example.com/a"


@pytest.mark.asyncio
async def test_gap_routing_internal_search_passes_through():
    """INTERNAL_SEARCH gap → no-op（站內 retrieval 由 BAB main loop 處理，gap routing 不重複跑）."""
    from reasoning.schemas_enhanced import GapResolutionType
    engine = _make_engine()
    gap = _make_gap(GapResolutionType.INTERNAL_SEARCH, search_query="台灣風電")
    await engine._process_gap_resolutions_lr([gap])
    assert len(engine.evidence_pool) == 0


@pytest.mark.asyncio
async def test_gap_routing_stock_tw_logged_and_skipped(monkeypatch):
    """STOCK_TW gap → 走 fail-loud-with-info skip path + 不加 evidence.

    Note: 專案 logger 是自訂 LazyLogger 直接寫 JSON 到 handler，stdlib caplog
    / pytest capsys 都 capture 不到。改用 monkeypatch logger.info 攔截 message
    驗證 skip path 真的執行（而非沉默 continue）。
    """
    from reasoning.schemas_enhanced import GapResolutionType
    from reasoning.live_research import loop_engine as le

    captured_infos = []
    original_info = le.logger.info

    def spy_info(msg, *args, **kwargs):
        captured_infos.append(str(msg))
        return original_info(msg, *args, **kwargs)

    monkeypatch.setattr(le.logger, "info", spy_info)

    engine = _make_engine()
    gap = _make_gap(GapResolutionType.STOCK_TW, search_query="2330 台積電")
    await engine._process_gap_resolutions_lr([gap])
    assert len(engine.evidence_pool) == 0
    # 驗 skip path 真的執行（log 訊息含 Skipping + stock_tw）
    assert any(
        "Skipping" in m and "stock_tw" in m.lower()
        for m in captured_infos
    ), f"Expected skip log for STOCK_TW gap, got infos: {captured_infos}"


@pytest.mark.asyncio
async def test_gap_routing_blocked_when_enable_gap_enrichment_false():
    """enable_gap_enrichment=False → 整個 gap routing 不跑（evidence_pool 不變）."""
    from reasoning.schemas_enhanced import GapResolutionType
    engine = _make_engine(handler_kwargs={"enable_gap_enrichment": False})
    gap = _make_gap(
        GapResolutionType.LLM_KNOWLEDGE, topic="x", llm_answer="y",
    )
    await engine._process_gap_resolutions_lr([gap])
    assert len(engine.evidence_pool) == 0


@pytest.mark.asyncio
async def test_gap_routing_web_search_blocked_when_enable_web_search_false():
    """enable_gap_enrichment=True but enable_web_search=False → WEB_SEARCH gap log skip."""
    from reasoning.schemas_enhanced import GapResolutionType
    engine = _make_engine(handler_kwargs={"enable_web_search": False})
    gap = _make_gap(GapResolutionType.WEB_SEARCH, search_query="x")
    await engine._process_gap_resolutions_lr([gap])
    assert len(engine.evidence_pool) == 0


@pytest.mark.asyncio
async def test_gap_routing_web_search_runs_when_both_flags_true(monkeypatch):
    """enable_gap_enrichment=True AND enable_web_search=True → WEB_SEARCH gap 確實呼叫 _execute_web_search。

    此測試驗「雙 flag 全開」這個 prod 預期路徑（F2 接線後的常態）。
    """
    from reasoning.schemas_enhanced import GapResolutionType

    web_called = []

    async def fake_web_search(self, query):
        web_called.append(query)
        return []

    engine = _make_engine()  # enable_gap_enrichment=True, enable_web_search=True (default)
    monkeypatch.setattr(type(engine), "_execute_web_search", fake_web_search)
    gap = _make_gap(GapResolutionType.WEB_SEARCH, search_query="能源轉型案例")
    await engine._process_gap_resolutions_lr([gap])
    assert web_called == ["能源轉型案例"], f"Expected _execute_web_search called once, got: {web_called}"


@pytest.mark.asyncio
async def test_gap_routing_llm_knowledge_runs_when_both_flags_true():
    """enable_gap_enrichment=True AND enable_web_search=True → LLM_KNOWLEDGE gap 建 virtual doc。

    驗「雙 flag 全開」時 llm_knowledge 路由正常（不被 WEB_SEARCH gate 干擾）。
    """
    from reasoning.schemas_enhanced import GapResolutionType

    engine = _make_engine()  # both flags True
    gap = _make_gap(
        GapResolutionType.LLM_KNOWLEDGE,
        topic="台灣能源政策",
        llm_answer="台灣能源政策的核心目標是…",
    )
    await engine._process_gap_resolutions_lr([gap])
    llm_entries = [e for e in engine.evidence_pool.values() if e.source == "llm_knowledge"]
    assert len(llm_entries) == 1
    assert "[Tier 6 | llm_knowledge]" in llm_entries[0].snippet


@pytest.mark.asyncio
async def test_gap_routing_external_call_cap_enforced(monkeypatch):
    """超過 per-run 外部呼叫 cap 後，剩餘外部 gap 被跳過（不再打外部）。

    cap 透過 monkeypatch CONFIG.reasoning_params 設為 2；給 4 個 WEB_SEARCH gap，
    驗 _execute_web_search 只被叫 2 次。
    """
    from reasoning.schemas_enhanced import GapResolutionType
    from core.config import CONFIG

    web_called = []

    async def fake_web_search(self, query):
        web_called.append(query)
        return []

    # cap 讀取是呼叫時讀（_process_gap_resolutions_lr 內 from core.config import CONFIG），
    # setitem 替換 tier_6 dict 後可被讀到 cap=2。
    monkeypatch.setitem(
        CONFIG.reasoning_params, "tier_6",
        {"gap_routing": {"max_external_calls_per_run": 2}},
    )
    engine = _make_engine()
    monkeypatch.setattr(type(engine), "_execute_web_search", fake_web_search)
    gaps = [_make_gap(GapResolutionType.WEB_SEARCH, search_query=f"q{i}") for i in range(4)]
    await engine._process_gap_resolutions_lr(gaps)
    assert len(web_called) == 2, f"cap=2 應只打 2 次，實際 {len(web_called)}"


@pytest.mark.asyncio
async def test_gap_routing_cap_mixed_types_llm_knowledge_unaffected(monkeypatch):
    """混型 gap 清單 + cap=2：WIKIPEDIA/WEB_SEARCH 合計只打 2 次外部，
    LLM_KNOWLEDGE 不計入 cap、照常入 evidence_pool。
    """
    from reasoning.schemas_enhanced import GapResolutionType
    from core.config import CONFIG

    external_calls = []

    async def fake_web_search(self, query):
        external_calls.append(('web', query))
        return []

    async def fake_wiki(self, gaps):
        external_calls.append(('wiki', gaps[0].search_query))
        return None

    monkeypatch.setitem(
        CONFIG.reasoning_params, "tier_6",
        {"gap_routing": {"max_external_calls_per_run": 2}},
    )
    engine = _make_engine()
    monkeypatch.setattr(type(engine), "_execute_web_search", fake_web_search)
    monkeypatch.setattr(type(engine), "_execute_wikipedia_searches_lr", fake_wiki)
    gaps = [
        _make_gap(GapResolutionType.WIKIPEDIA, search_query='w1'),
        _make_gap(GapResolutionType.WEB_SEARCH, search_query='s1'),
        _make_gap(GapResolutionType.LLM_KNOWLEDGE, topic='t', llm_answer='a...'),
        _make_gap(GapResolutionType.WIKIPEDIA, search_query='w2'),
    ]
    await engine._process_gap_resolutions_lr(gaps)
    assert len(external_calls) == 2, f"cap=2 外部只能 2 次，實際 {external_calls}"
    llm_entries = [e for e in engine.evidence_pool.values() if e.source == 'llm_knowledge']
    assert len(llm_entries) == 1, "LLM_KNOWLEDGE 不計入 cap，應照常入池"
