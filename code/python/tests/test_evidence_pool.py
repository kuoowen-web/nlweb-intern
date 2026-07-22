"""Tests for EvidencePoolEntry schema + serialize/deserialize helpers.

涵蓋 Task 1：基本 schema + 序列化 round-trip。
後續 Task 2/3 補進 state 持久化與 BAB 跨 iteration 累積測試。
"""

import json

import pytest

from reasoning.schemas_live import (
    EvidencePoolEntry,
    serialize_evidence_pool,
    deserialize_evidence_pool,
)


def test_evidence_pool_entry_defaults():
    """EvidencePoolEntry 全欄位預設值應該齊備（除 evidence_id 必填）。"""
    entry = EvidencePoolEntry(evidence_id=1)
    assert entry.evidence_id == 1
    assert entry.title == ""
    assert entry.url == ""
    assert entry.source_domain == ""
    assert entry.snippet == ""
    assert entry.iteration_origin == 0
    # retrieved_at 預設應為 ISO 字串
    assert isinstance(entry.retrieved_at, str)
    assert "T" in entry.retrieved_at  # ISO format heuristic


def test_evidence_pool_entry_full_payload():
    entry = EvidencePoolEntry(
        evidence_id=7,
        title="台灣再生能源報告",
        url="https://example.com/article/7",
        source_domain="example.com",
        snippet="摘要前 300 字...",
        retrieved_at="2026-05-12T00:00:00",
        iteration_origin=2,
    )
    assert entry.title == "台灣再生能源報告"
    assert entry.source_domain == "example.com"
    assert entry.iteration_origin == 2


def test_serialize_round_trip():
    """serialize → deserialize 應該還原成等價 dict。"""
    pool = {
        1: EvidencePoolEntry(evidence_id=1, title="A", url="https://a.com"),
        2: EvidencePoolEntry(evidence_id=2, title="B", url="https://b.com"),
    }
    s = serialize_evidence_pool(pool)
    assert isinstance(s, str)
    # JSON keys 必須是 str（int 在 JSON 不合法）
    raw = json.loads(s)
    assert set(raw.keys()) == {"1", "2"}

    restored = deserialize_evidence_pool(s)
    assert set(restored.keys()) == {1, 2}
    assert restored[1].title == "A"
    assert restored[2].url == "https://b.com"


def test_deserialize_empty_string():
    """空字串應回傳空 dict（兼容舊 DB row）。"""
    assert deserialize_evidence_pool("") == {}


def test_serialize_empty_dict():
    """空 dict 序列化後 deserialize 應仍是空 dict。"""
    s = serialize_evidence_pool({})
    assert deserialize_evidence_pool(s) == {}


def test_serialize_preserves_unicode():
    """繁體中文 title 經 round-trip 應正確還原（不要 \\uXXXX escape）。"""
    pool = {
        5: EvidencePoolEntry(evidence_id=5, title="再生能源發展現況"),
    }
    s = serialize_evidence_pool(pool)
    # ensure_ascii=False → 中文應該直接出現
    assert "再生能源發展現況" in s
    restored = deserialize_evidence_pool(s)
    assert restored[5].title == "再生能源發展現況"


# ============================================================================
# Task 2: LiveResearchStageState 新增 evidence_pool_json 欄位
# ============================================================================

def test_state_has_evidence_pool_default_empty():
    """新建 state 預設 evidence_pool_json == ""。"""
    from reasoning.live_research.stage_state import LiveResearchStageState
    s = LiveResearchStageState()
    assert s.evidence_pool_json == ""


def test_state_round_trip_with_evidence_pool():
    """to_dict → from_dict 應保留 evidence_pool_json 欄位內容。"""
    from reasoning.live_research.stage_state import LiveResearchStageState
    pool = {1: EvidencePoolEntry(evidence_id=1, title="X", url="https://x.com")}
    payload = serialize_evidence_pool(pool)

    s = LiveResearchStageState()
    s.evidence_pool_json = payload

    d = s.to_dict()
    assert d["evidence_pool_json"] == payload

    s2 = LiveResearchStageState.from_dict(d)
    assert s2.evidence_pool_json == payload
    restored = deserialize_evidence_pool(s2.evidence_pool_json)
    assert restored[1].title == "X"


# ============================================================================
# Task 3: BABLoopEngine 全局累積 evidence_pool + URL 去重
# ============================================================================

class _FakeHandler:
    """Minimal handler stub for BABLoopEngine.__init__."""
    site = "all"
    query_params = {}
    message_sender = None
    connection_alive_event = None
    http_handler = None


class _FakeAssociator:
    """Minimal associator stub — BAB engine `__init__` 需要但 _execute_search 測試不會用到。"""
    pass


class _FakeSeed:
    """Mimic SearchSeed shape for _execute_search."""
    def __init__(self, query, source_strategy="internal"):
        self.query = query
        self.source_strategy = source_strategy


@pytest.mark.asyncio
async def test_bab_engine_evidence_pool_global_counter(monkeypatch):
    """兩次 _execute_search 後 evidence_id 跨 iteration 唯一遞增。"""
    from reasoning.live_research.loop_engine import BABLoopEngine

    # Mock retriever_search to return distinct items per call
    call_count = {"n": 0}

    async def fake_search(query, site, num_results, query_params):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [
                ("https://a.com/1", '{"description":"d1"}', "Title A1", "src"),
                ("https://a.com/2", '{"description":"d2"}', "Title A2", "src"),
            ]
        return [
            ("https://b.com/3", '{"description":"d3"}', "Title B3", "src"),
        ]

    monkeypatch.setattr("core.retriever.search", fake_search)

    engine = BABLoopEngine(
        associator=_FakeAssociator(),
        handler=_FakeHandler(),
        max_iterations=2,
        enable_consistency_monitor=False,
    )

    seeds_iter1 = [_FakeSeed("q1")]
    formatted1, src_map1 = await engine._execute_search(seeds_iter1)
    assert engine._evidence_counter == 2
    assert set(engine.evidence_pool.keys()) == {1, 2}
    assert "[1]" in formatted1 and "[2]" in formatted1

    seeds_iter2 = [_FakeSeed("q2")]
    formatted2, src_map2 = await engine._execute_search(seeds_iter2)
    # 跨 iteration counter 繼續遞增，不 reset
    assert engine._evidence_counter == 3
    assert set(engine.evidence_pool.keys()) == {1, 2, 3}
    assert "[3]" in formatted2
    assert "[1]" not in formatted2  # 沒有 reset 到 1


@pytest.mark.asyncio
async def test_bab_engine_dedup_by_url(monkeypatch):
    """同 URL 在不同 iteration 出現 → 只佔一個 evidence_id（不重複收錄）。"""
    from reasoning.live_research.loop_engine import BABLoopEngine

    async def fake_search(query, site, num_results, query_params):
        # Always return the same item
        return [
            ("https://dup.com/1", '{"description":"same"}', "Same Title", "src"),
        ]

    monkeypatch.setattr("core.retriever.search", fake_search)

    engine = BABLoopEngine(
        associator=_FakeAssociator(),
        handler=_FakeHandler(),
        max_iterations=2,
        enable_consistency_monitor=False,
    )

    await engine._execute_search([_FakeSeed("q1")])
    await engine._execute_search([_FakeSeed("q2")])
    await engine._execute_search([_FakeSeed("q3")])

    assert engine._evidence_counter == 1
    assert list(engine.evidence_pool.keys()) == [1]
    assert engine.evidence_pool[1].url == "https://dup.com/1"


@pytest.mark.asyncio
async def test_bab_engine_seed_evidence_pool(monkeypatch):
    """傳入 seed_evidence_pool → counter 從 max(seed_keys) 開始。"""
    from reasoning.live_research.loop_engine import BABLoopEngine

    async def fake_search(query, site, num_results, query_params):
        return [
            ("https://new.com/x", '{"description":"d"}', "New", "src"),
        ]

    monkeypatch.setattr("core.retriever.search", fake_search)

    seed_pool = {
        5: EvidencePoolEntry(evidence_id=5, title="Seed5", url="https://seed.com/5"),
        7: EvidencePoolEntry(evidence_id=7, title="Seed7", url="https://seed.com/7"),
    }

    engine = BABLoopEngine(
        associator=_FakeAssociator(),
        handler=_FakeHandler(),
        max_iterations=1,
        enable_consistency_monitor=False,
        seed_evidence_pool=seed_pool,
        seed_counter=7,
    )

    assert engine._evidence_counter == 7
    assert set(engine.evidence_pool.keys()) == {5, 7}
    # URL → id 反查表也應該由 seed 建好
    assert engine._url_to_id.get("https://seed.com/5") == 5
    assert engine._url_to_id.get("https://seed.com/7") == 7

    # 新搜尋抓到的 evidence 應該分配 id=8（不重 5/7）
    await engine._execute_search([_FakeSeed("q")])
    assert engine._evidence_counter == 8
    assert 8 in engine.evidence_pool
    assert engine.evidence_pool[8].url == "https://new.com/x"


# ============================================================================
# F-1 (full-scan 批7): deserialize_evidence_pool corrupt-state 防護
# ── 壞 JSON / 壞 entry / 非數字 key 不該炸 pipeline，應 per-entry skip + log
#    warning，讓 caller 判空回退（與 DR 側 fail-closed 對齊）。
# ============================================================================

# R1 補修：本專案 deserialize_evidence_pool 用 get_configured_logger（LazyLogger,
# propagate=False + 背景 thread queue emit），pytest caplog 掛 root handler 結構上
# 抓不到 → 原三條 caplog 斷言在乾淨環境恆紅（lessons-testing-review.md:180-184 已記
# 第 2 次重犯）。改用 patch.object(schemas_live, "logger") 驗 mock.warning.called，
# 對齊 test_llm_score_coercion.py:114-119 正解——行為斷言（回空/skip）逐字不動，只
# 換 log 捕捉機制。
from unittest.mock import patch

from reasoning import schemas_live


def test_deserialize_corrupt_json_returns_empty():
    """整份 JSON 損壞（半寫入 checkpoint / DB 壞）→ 回空 dict + log warning，不上炸。"""
    corrupt = '{"1": {"evidence_id": 1, "title": "A"'  # 截斷的 JSON（JSONDecodeError）
    with patch.object(schemas_live, "logger") as mock_logger:
        result = deserialize_evidence_pool(corrupt)
    assert result == {}, "壞 JSON 應 fail-closed 回空 dict，讓 caller 走 blocked/checkpoint 回退"
    assert mock_logger.warning.called, "整份壞 JSON 應留 warning 不 silent"


def test_deserialize_bad_entry_skipped_others_survive():
    """單筆 entry 型別變形（evidence_id 非數字）→ 該筆 skip，其餘 entry 正常還原。"""
    # entry "2" 的 evidence_id 是不可轉數字的字串 → ValidationError；"1"/"3" 正常
    payload = json.dumps({
        "1": {"evidence_id": 1, "title": "Good1", "url": "https://a.com"},
        "2": {"evidence_id": "not_an_int", "title": "Bad"},
        "3": {"evidence_id": 3, "title": "Good3", "url": "https://c.com"},
    })
    with patch.object(schemas_live, "logger") as mock_logger:
        result = deserialize_evidence_pool(payload)
    assert set(result.keys()) == {1, 3}, "壞 entry 應被 skip，好 entry 存活"
    assert result[1].title == "Good1"
    assert result[3].title == "Good3"
    assert mock_logger.warning.called, "被跳過的壞 entry 應留 warning"
    # 訊息含被跳過的壞 key，供診斷（mutation 咬得住）
    warn_text = " ".join(str(c) for c in mock_logger.warning.call_args_list)
    assert "2" in warn_text, "warning 應標出被跳過的 entry key"


def test_deserialize_non_numeric_key_skipped():
    """非數字 key（int(k) ValueError）→ 該筆 skip 不炸，其餘還原。"""
    payload = json.dumps({
        "1": {"evidence_id": 1, "title": "Good1"},
        "abc": {"evidence_id": 9, "title": "BadKey"},  # int("abc") → ValueError
    })
    with patch.object(schemas_live, "logger") as mock_logger:
        result = deserialize_evidence_pool(payload)
    assert set(result.keys()) == {1}, "非數字 key entry 應 skip"
    assert mock_logger.warning.called
    warn_text = " ".join(str(c) for c in mock_logger.warning.call_args_list)
    assert "abc" in warn_text, "warning 應標出被跳過的非數字 key"


def test_deserialize_all_bad_entries_returns_empty():
    """全部 entry 都壞 → 回空 dict（caller 判空回退，非拋例外）。"""
    payload = json.dumps({
        "x": {"evidence_id": 1},
        "y": {"evidence_id": 2},
    })
    result = deserialize_evidence_pool(payload)
    assert result == {}, "全壞應回空 dict 讓 caller fail-closed"


def test_deserialize_good_data_unchanged():
    """回歸：好資料 round-trip 行為不變（防護不改變正常路徑）。"""
    pool = {
        1: EvidencePoolEntry(evidence_id=1, title="A", url="https://a.com"),
        2: EvidencePoolEntry(evidence_id=2, title="B", url="https://b.com"),
    }
    s = serialize_evidence_pool(pool)
    restored = deserialize_evidence_pool(s)
    assert set(restored.keys()) == {1, 2}
    assert restored[1].title == "A"
    assert restored[2].url == "https://b.com"


def test_state_from_dict_legacy_row():
    """舊 DB row 沒 evidence_pool_json key → from_dict 不 crash，預設空字串。"""
    from reasoning.live_research.stage_state import LiveResearchStageState
    legacy = {
        "current_stage": 1,
        "stage_status": "in_progress",
        "checkpoint_prompt": "",
        "failed_intent_parse_count": 0,
        "context_map_json": "",
        "initial_context_map_json": "",
        "completed_sections": [],
        "style_features_json": "",
        "format_specs": {},
        "written_sections": [],
        "executed_searches": [],
        "created_at": "",
        "last_updated_at": "",
    }
    s = LiveResearchStageState.from_dict(legacy)
    assert s.evidence_pool_json == ""
