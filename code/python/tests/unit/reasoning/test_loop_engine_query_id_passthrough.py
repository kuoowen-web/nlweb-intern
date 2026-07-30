"""LR loop_engine query_id 透傳（票 2026-07-28-k Task 5）：
_execute_web_search / _execute_wikipedia_searches_lr 把 handler.query_id 傳給 client，
讓 client 端既有的 `if query_id:` gated tier_6_enrichment 落表生效。
不動 client 檔案（紅線：CSE 429 修法 7a44c6a4 剛 land）。"""
import pytest
from unittest.mock import MagicMock


def _make_engine(query_id="query_lr_555"):
    from reasoning.live_research.loop_engine import BABLoopEngine
    handler = MagicMock(
        query_params={},
        site="all",
        enable_web_search=True,
        enable_gap_enrichment=True,
        query_id=query_id,
    )
    engine = BABLoopEngine(associator=MagicMock(), handler=handler, max_iterations=1)
    engine._current_iteration = 1
    return engine


@pytest.mark.asyncio
async def test_execute_web_search_passes_handler_query_id(monkeypatch):
    """_execute_web_search → search_all_sites 收到 query_id kwarg = handler.query_id。"""
    captured = {}

    async def fake_search_all_sites(self, query, num_results=5, timeout=None, query_id=None):
        captured["query"] = query
        captured["query_id"] = query_id
        return []

    monkeypatch.setattr(
        "retrieval_providers.google_search_client.GoogleSearchClient.search_all_sites",
        fake_search_all_sites,
    )
    engine = _make_engine()
    await engine._execute_web_search("德國風電政策")

    assert captured["query"] == "德國風電政策"
    assert captured["query_id"] == "query_lr_555"


@pytest.mark.asyncio
async def test_execute_wikipedia_searches_passes_handler_query_id(monkeypatch):
    """_execute_wikipedia_searches_lr → WikipediaClient.search 收到 query_id kwarg。
    F-2 dual-guard 紀律：monkeypatch module flag + is_available 兩條。"""
    from reasoning.schemas_enhanced import GapResolution, GapResolutionType

    captured = {}

    async def fake_wiki_search(self, query, **kwargs):
        captured["query"] = query
        captured["query_id"] = kwargs.get("query_id")
        return []

    monkeypatch.setattr("retrieval_providers.wikipedia_client.WIKIPEDIA_AVAILABLE", True)
    monkeypatch.setattr(
        "retrieval_providers.wikipedia_client.WikipediaClient.is_available",
        lambda self: True,
    )
    monkeypatch.setattr(
        "retrieval_providers.wikipedia_client.WikipediaClient.search", fake_wiki_search,
    )

    engine = _make_engine()
    gap = GapResolution(
        gap_type="definition",
        resolution=GapResolutionType.WIKIPEDIA,
        search_query="Energiewende",
    )
    await engine._execute_wikipedia_searches_lr([gap])

    assert captured["query"] == "Energiewende"
    assert captured["query_id"] == "query_lr_555"


@pytest.mark.asyncio
async def test_execute_web_search_tolerates_missing_query_id(monkeypatch):
    """handler 無 query_id 屬性（route 未打點的舊路徑）→ 傳 None、不 raise
    （client 端 `if query_id:` gate 自然不記 tier6，行為與現況相同）。"""
    from reasoning.live_research.loop_engine import BABLoopEngine

    captured = {}

    async def fake_search_all_sites(self, query, num_results=5, timeout=None, query_id=None):
        captured["query_id"] = query_id
        return []

    monkeypatch.setattr(
        "retrieval_providers.google_search_client.GoogleSearchClient.search_all_sites",
        fake_search_all_sites,
    )

    class _BareHandler:
        query_params = {}
        site = "all"
        enable_web_search = True
        enable_gap_enrichment = True
        # 刻意無 query_id 屬性

    engine = BABLoopEngine(associator=MagicMock(), handler=_BareHandler(), max_iterations=1)
    engine._current_iteration = 1
    await engine._execute_web_search("q")

    assert captured["query_id"] is None
