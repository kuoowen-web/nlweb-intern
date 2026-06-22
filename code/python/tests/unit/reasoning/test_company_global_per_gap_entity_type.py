"""Regression: company_global 每個 gap 必須用「自己的」entity_type 搜尋。

舊行為（per-gap mutable list）：search 前一刻把 entity_type 設成「這個 gap 的」
type，所以同名但不同 type 的兩個 gap 各用各的。

Task 6 重構曾改成 name-keyed map（entity_type_map[name] = type），對同名 gap
last-write-wins → 兩個 gap 都用最後那個 type。這條 test 鎖住嚴格 per-gap parity：
同名不同 type 時各自用自己的 type。name-keyed map 版會 FAIL，per-gap 版 PASS。
"""

import asyncio
import logging
import sys
import types
from types import SimpleNamespace

import pytest

from reasoning.orchestrator import DeepResearchOrchestrator


class _RecordingWikidataClient:
    """假 WikidataClient：捕捉每次 search 的 (name, entity_type)。"""

    calls = []

    def __init__(self):
        pass

    def is_available(self):
        return True

    async def search(self, name, entity_type=None, query_id=None):
        type(self).calls.append((name, entity_type))
        return []  # 無結果，避免動到 source_map / context 也無妨


@pytest.fixture
def patched_wikidata(monkeypatch):
    """讓 importlib.import_module('retrieval_providers.wikidata_client') 拿到假 client。"""
    _RecordingWikidataClient.calls = []
    fake_mod = types.ModuleType("retrieval_providers.wikidata_client")
    fake_mod.WikidataClient = _RecordingWikidataClient
    monkeypatch.setitem(sys.modules, "retrieval_providers.wikidata_client", fake_mod)
    return _RecordingWikidataClient


def _bare_orchestrator():
    """不跑重的 __init__（會建 LLM agents），只裝方法路徑會碰到的屬性。"""
    orch = object.__new__(DeepResearchOrchestrator)
    orch.logger = logging.getLogger("test.company_global")
    orch.source_map = {}
    return orch


def test_duplicate_name_different_entity_type_each_uses_own_type(patched_wikidata):
    """兩個同名（'Acme'）但不同 entity_type 的 gap，各自用自己的 type 搜尋。

    name-keyed map 版會把兩次都搜成最後一個 type（last-write-wins）→ FAIL。
    per-gap parity 版兩次各用各的 → PASS。
    """
    orch = _bare_orchestrator()

    gaps = [
        SimpleNamespace(api_params={"name": "Acme", "type": "company"}, search_query=None),
        SimpleNamespace(api_params={"name": "Acme", "type": "person"}, search_query=None),
    ]

    asyncio.run(
        orch._execute_company_global_searches(gaps=gaps, current_context=[])
    )

    calls = patched_wikidata.calls
    assert calls == [("Acme", "company"), ("Acme", "person")], (
        f"每個 gap 應用自己的 entity_type；實際： {calls}"
    )


def test_distinct_names_each_uses_own_type(patched_wikidata):
    """sanity：不同名也各用各的 type（基本 per-gap 行為）。"""
    orch = _bare_orchestrator()

    gaps = [
        SimpleNamespace(api_params={"name": "Foo", "type": "company"}, search_query=None),
        SimpleNamespace(api_params={"name": "Bar", "type": "person"}, search_query=None),
    ]

    asyncio.run(
        orch._execute_company_global_searches(gaps=gaps, current_context=[])
    )

    assert patched_wikidata.calls == [("Foo", "company"), ("Bar", "person")]


def test_default_entity_type_company_when_type_missing(patched_wikidata):
    """api_params 無 'type' → 預設 'company'（與舊 .get('type', 'company') 一致）。"""
    orch = _bare_orchestrator()

    gaps = [
        SimpleNamespace(api_params={"name": "Baz"}, search_query=None),
        SimpleNamespace(api_params=None, search_query="FallbackName"),
    ]

    asyncio.run(
        orch._execute_company_global_searches(gaps=gaps, current_context=[])
    )

    assert patched_wikidata.calls == [("Baz", "company"), ("FallbackName", "company")]


def test_empty_name_gap_skipped(patched_wikidata):
    """無 name 也無 search_query 的 gap 被跳過（不呼叫 search）。"""
    orch = _bare_orchestrator()

    gaps = [
        SimpleNamespace(api_params=None, search_query=None),
        SimpleNamespace(api_params={"name": "Qux", "type": "company"}, search_query=None),
    ]

    asyncio.run(
        orch._execute_company_global_searches(gaps=gaps, current_context=[])
    )

    assert patched_wikidata.calls == [("Qux", "company")]
