"""Unit tests for mock_bab=True loading from real prod fixture.

2026-07（本 plan）：fixture 換為 Cayenne 綠能命題 session 8e1db658（2026-07-15 prod 真跑），
取代舊 5767ae4a（德日智利命題，36 筆）。舊目錄 lr_mock_bab_real/ 保留供 rollback。
Expected fixture: code/python/tests/fixtures/lr_mock_bab_cayenne_2026_07/
  - evidence_pool.json: 567 real evidence entries（internal 493 / llm_knowledge 45 / web 29）
  - context_map.json: real ContextMap（20 topics, v25）
  - book_outline.json: 3-chapter outline（前言/國際案例分析/結論）
  - evidence_usage.json: 40 evidence ids / 172 grounded claims
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from reasoning.live_research.stage_state import LiveResearchStageState
from reasoning.schemas_live import ContextMap, deserialize_evidence_pool


def _make_handler():
    h = MagicMock()
    h.site = "all"
    h.query_params = {}
    h.message_sender = None
    h.connection_alive_event = None
    h.http_handler = None
    h._save_state = AsyncMock()  # plan: durable boundary persist awaits this
    return h


def _run_mock_stage_1():
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    handler = _make_handler()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
    orch.mock_bab = True
    state = LiveResearchStageState()
    orch._emit_stage_change = AsyncMock()
    orch._emit_checkpoint = AsyncMock()
    return orch, state


# ═══ Test 1: 守門 — 567 筆真語料（Cayenne fixture，非舊 36 筆）═══

@pytest.mark.asyncio
async def test_mock_bab_real_fixture_loads_567_evidence():
    orch, state = _run_mock_stage_1()
    state = await orch._run_stage_1(state, query="能源轉型地方衝突", initial_items=None)

    assert state.evidence_pool_json != "", "evidence_pool_json should not be empty"
    restored = deserialize_evidence_pool(state.evidence_pool_json)
    assert len(restored) == 567, f"Expected 567 entries (Cayenne fixture), got {len(restored)}"
    assert set(restored.keys()) == set(range(1, 568)), "Expected contiguous keys 1-567"


# ═══ Test 2: context_map — Cayenne 命題 research_question + 20 topics ═══

@pytest.mark.asyncio
async def test_mock_bab_real_fixture_context_map_research_question():
    orch, state = _run_mock_stage_1()
    state = await orch._run_stage_1(state, query="能源轉型地方衝突", initial_items=None)

    assert state.context_map_json != ""
    cm = ContextMap.model_validate_json(state.context_map_json)
    rq = cm.research_question
    assert "能源轉型" in rq or "再生能源" in rq or "衝突" in rq, (
        f"Expected Cayenne fixture research_question, got: {rq[:80]}"
    )
    assert len(cm.topics) >= 15, f"Expected >=15 topics (actual 20), got {len(cm.topics)}"


# ═══ Test 3: evidence entries schema 有效 ═══

@pytest.mark.asyncio
async def test_mock_bab_real_fixture_evidence_entries_valid_schema():
    orch, state = _run_mock_stage_1()
    state = await orch._run_stage_1(state, query="x", initial_items=None)

    restored = deserialize_evidence_pool(state.evidence_pool_json)
    assert len(restored) == 567
    for eid, entry in restored.items():
        assert entry.evidence_id == eid, f"evidence_id mismatch: entry {eid}"
        assert entry.url != "", f"url empty for evidence_id={eid}"
        assert entry.title != "", f"title empty for evidence_id={eid}"
        assert entry.source in ("internal", "web", "wiki", "llm_knowledge"), (
            f"Invalid source for evidence_id={eid}: {entry.source}"
        )


# ═══ Test 4: topic evidence_ids 池內覆蓋 ═══

@pytest.mark.asyncio
async def test_mock_bab_real_fixture_topic_evidence_ids_in_pool():
    orch, state = _run_mock_stage_1()
    state = await orch._run_stage_1(state, query="x", initial_items=None)

    cm = ContextMap.model_validate_json(state.context_map_json)
    restored = deserialize_evidence_pool(state.evidence_pool_json)
    pool_keys = set(restored.keys())

    used_eids = set()
    for topic in cm.topics:
        used_eids.update(topic.evidence_ids)
    in_range_eids = {eid for eid in used_eids if 1 <= eid <= 567}
    missing = in_range_eids - pool_keys
    assert not missing, f"Topic evidence_ids {missing} referenced but not in pool (1-567)"


# ═══ Test 5: evidence_usage — 40 int keys / 172 claims ═══

@pytest.mark.asyncio
async def test_mock_bab_loads_evidence_usage_40_keys_172_claims():
    orch, state = _run_mock_stage_1()
    state = await orch._run_stage_1(state, query="能源轉型地方衝突", initial_items=None)

    assert len(state.evidence_usage) == 40, (
        f"Expected 40 evidence_usage keys (Cayenne fixture), got {len(state.evidence_usage)}"
    )
    for k in state.evidence_usage:
        assert isinstance(k, int), f"evidence_usage key must be int, got {type(k).__name__}: {k!r}"
    total_claims = sum(len(claims) for claims in state.evidence_usage.values())
    assert total_claims == 172, f"Expected 172 total grounded claims, got {total_claims}"


# ═══ Test 6: evidence_usage value = List[Dict]（GroundedClaim dump shape）═══

@pytest.mark.asyncio
async def test_mock_bab_evidence_usage_value_is_list_of_dict():
    orch, state = _run_mock_stage_1()
    state = await orch._run_stage_1(state, query="x", initial_items=None)

    for eid, claims in state.evidence_usage.items():
        assert isinstance(claims, list), f"evidence_usage[{eid}] must be list"
        for i, claim in enumerate(claims):
            assert isinstance(claim, dict), f"evidence_usage[{eid}][{i}] must be dict"
            assert "claim" in claim, f"Missing 'claim' in evidence_usage[{eid}][{i}]"
            assert "reasoning_type" in claim, f"Missing 'reasoning_type' in evidence_usage[{eid}][{i}]"
            assert "confidence" in claim, f"Missing 'confidence' in evidence_usage[{eid}][{i}]"


# ═══ Test 7: book_outline — 3 章（前言/國際案例分析/結論），roles intro/…/conclusion ═══

@pytest.mark.asyncio
async def test_mock_bab_loads_book_outline_3_chapters():
    from reasoning.schemas_live import BookOutline
    orch, state = _run_mock_stage_1()
    state = await orch._run_stage_1(state, query="能源轉型地方衝突", initial_items=None)

    assert state.book_outline_json, "book_outline_json should not be empty"
    bo = BookOutline.model_validate_json(state.book_outline_json)
    assert len(bo.chapters) == 3, f"Expected 3 chapters (Cayenne fixture), got {len(bo.chapters)}"
    titles = [c.title for c in bo.chapters]
    assert titles == ["前言", "國際案例分析", "結論"], f"Unexpected titles: {titles}"
    assert bo.chapters[0].role == "intro"
    assert bo.chapters[-1].role == "conclusion"


# ═══ Test 8: format_specs["chapters"] 同步 3 章（chapter-override 路徑）═══

@pytest.mark.asyncio
async def test_mock_bab_syncs_format_specs_chapters():
    orch, state = _run_mock_stage_1()
    state = await orch._run_stage_1(state, query="能源轉型地方衝突", initial_items=None)

    assert state.format_specs
    chapters = state.format_specs.get("chapters")
    assert chapters, "format_specs['chapters'] should not be empty"
    assert len(chapters) == 3, f"Expected 3 format_specs chapters, got {len(chapters)}"
    for i, ch in enumerate(chapters):
        assert isinstance(ch, dict) and ch.get("name"), f"chapters[{i}] missing 'name'"
        assert "outline" in ch, f"chapters[{i}] missing 'outline'"
    assert chapters[0]["name"] == "前言"
    assert chapters[-1]["name"] == "結論"
