"""Unit tests for mock_bab=True loading from real prod fixture (session 5767ae4a).

TDD: Tests written BEFORE implementation change.
Expected fixture: code/python/tests/fixtures/lr_mock_bab_real/
  - evidence_pool.json: 36 real evidence entries from prod
  - context_map.json: real ContextMap (18 topics, v8)
  - book_outline.json: 5-chapter outline
  - evidence_usage.json: 35 evidence ids / 147 grounded claims (chapter-override writer needs this)

These tests will FAIL (red) until orchestrator._load_mock_bab_fixture() and
._load_mock_evidence_pool_fixture() are updated to load from the new fixture path.
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


# ============================================================================
# Test 1: mock_bab loads 36 evidence entries (real fixture, not 21 fake)
# ============================================================================

@pytest.mark.asyncio
async def test_mock_bab_real_fixture_loads_36_evidence():
    """mock_bab=True loads exactly 36 evidence entries from real prod fixture."""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = _make_handler()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
    orch.mock_bab = True

    state = LiveResearchStageState()
    orch._emit_stage_change = AsyncMock()
    orch._emit_checkpoint = AsyncMock()

    state = await orch._run_stage_1(state, query="台灣綠能農漁村衝突", initial_items=None)

    assert state.evidence_pool_json != "", "evidence_pool_json should not be empty"
    restored = deserialize_evidence_pool(state.evidence_pool_json)
    assert len(restored) == 36, f"Expected 36 entries, got {len(restored)}"
    assert set(restored.keys()) == set(range(1, 37)), (
        f"Expected keys 1-36, got {sorted(restored.keys())}"
    )


# ============================================================================
# Test 2: mock_bab loads context_map with correct research_question (real fixture)
# ============================================================================

@pytest.mark.asyncio
async def test_mock_bab_real_fixture_context_map_research_question():
    """mock_bab=True loads real context_map with correct research_question from prod."""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = _make_handler()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
    orch.mock_bab = True

    state = LiveResearchStageState()
    orch._emit_stage_change = AsyncMock()
    orch._emit_checkpoint = AsyncMock()

    state = await orch._run_stage_1(state, query="台灣綠能農漁村衝突", initial_items=None)

    assert state.context_map_json != "", "context_map_json should not be empty"
    cm = ContextMap.model_validate_json(state.context_map_json)

    # Real fixture research_question contains "能源轉型" and "農漁村"
    rq = cm.research_question
    assert "能源轉型" in rq or "農漁村" in rq or "綠能" in rq, (
        f"Expected real fixture research_question about energy/agriculture conflict, got: {rq[:80]}"
    )
    # Real fixture has 18 topics (not 3 fake topics)
    assert len(cm.topics) >= 10, (
        f"Expected >=10 topics from real fixture, got {len(cm.topics)}"
    )


# ============================================================================
# Test 3: evidence pool entries have correct schema fields (EvidencePoolEntry)
# ============================================================================

@pytest.mark.asyncio
async def test_mock_bab_real_fixture_evidence_entries_valid_schema():
    """All 36 evidence entries deserialize correctly into EvidencePoolEntry."""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = _make_handler()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
    orch.mock_bab = True

    state = LiveResearchStageState()
    orch._emit_stage_change = AsyncMock()
    orch._emit_checkpoint = AsyncMock()

    state = await orch._run_stage_1(state, query="x", initial_items=None)

    restored = deserialize_evidence_pool(state.evidence_pool_json)
    assert len(restored) == 36

    for eid, entry in restored.items():
        assert entry.evidence_id == eid, f"evidence_id mismatch: entry {eid}"
        assert entry.url != "", f"url empty for evidence_id={eid}"
        assert entry.title != "", f"title empty for evidence_id={eid}"
        # source field should be valid Literal
        assert entry.source in ("internal", "web", "wiki", "llm_knowledge"), (
            f"Invalid source for evidence_id={eid}: {entry.source}"
        )


# ============================================================================
# Test 4: context_map topic evidence_ids are all covered in the pool
# ============================================================================

@pytest.mark.asyncio
async def test_mock_bab_real_fixture_topic_evidence_ids_in_pool():
    """All topic.evidence_ids in real fixture context_map are present in evidence_pool."""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = _make_handler()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
    orch.mock_bab = True

    state = LiveResearchStageState()
    orch._emit_stage_change = AsyncMock()
    orch._emit_checkpoint = AsyncMock()

    state = await orch._run_stage_1(state, query="x", initial_items=None)

    cm = ContextMap.model_validate_json(state.context_map_json)
    restored = deserialize_evidence_pool(state.evidence_pool_json)
    pool_keys = set(restored.keys())

    used_eids = set()
    for topic in cm.topics:
        used_eids.update(topic.evidence_ids)

    # Only check IDs that are within the 36-entry range (context_map may ref IDs
    # that don't exist in pool for extended topics — only check 1-36)
    in_range_eids = {eid for eid in used_eids if 1 <= eid <= 36}
    missing = in_range_eids - pool_keys
    assert not missing, (
        f"Topic evidence_ids {missing} referenced but not in pool (1-36 range)"
    )


# ============================================================================
# Test 5 (TDD RED): mock_bab loads evidence_usage with 35 int keys / 147 claims
# ============================================================================

@pytest.mark.asyncio
async def test_mock_bab_loads_evidence_usage_35_keys_147_claims():
    """mock_bab=True → state.evidence_usage 含 35 個 int key、147 claims。

    TDD RED: 此測試在 orchestrator mock_bab 分支尚未載入 evidence_usage 前應 FAIL。
    chapter-override（5 章）路徑的 writer 硬依賴 state.evidence_usage，
    缺它 → body 章「[本章資料不足]」空轉，over-block 測不到。
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = _make_handler()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
    orch.mock_bab = True

    state = LiveResearchStageState()
    orch._emit_stage_change = AsyncMock()
    orch._emit_checkpoint = AsyncMock()

    state = await orch._run_stage_1(state, query="台灣綠能農漁村衝突", initial_items=None)

    # 驗 key 數量（35 個 evidence id 有 grounded claims）
    assert len(state.evidence_usage) == 35, (
        f"Expected 35 evidence_usage keys, got {len(state.evidence_usage)}"
    )

    # 驗 key 型別為 int（JSON 載入 str key 必須轉回 int）
    for k in state.evidence_usage:
        assert isinstance(k, int), (
            f"evidence_usage key must be int, got {type(k).__name__}: {k!r}"
        )

    # 驗 total claims 數量
    total_claims = sum(len(claims) for claims in state.evidence_usage.values())
    assert total_claims == 147, (
        f"Expected 147 total grounded claims, got {total_claims}"
    )


@pytest.mark.asyncio
async def test_mock_bab_evidence_usage_value_is_list_of_dict():
    """state.evidence_usage values 是 List[Dict]（與 stage_state.py 型別契約對齊）。

    value 必須是 dict（不是 GroundedClaim model），與 loop_engine 的
    gc.model_dump() pattern 以及 render_grounding_evidence_view 的 dict 消費對齊。
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = _make_handler()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
    orch.mock_bab = True

    state = LiveResearchStageState()
    orch._emit_stage_change = AsyncMock()
    orch._emit_checkpoint = AsyncMock()

    state = await orch._run_stage_1(state, query="x", initial_items=None)

    for eid, claims in state.evidence_usage.items():
        assert isinstance(claims, list), (
            f"evidence_usage[{eid}] must be list, got {type(claims).__name__}"
        )
        for i, claim in enumerate(claims):
            assert isinstance(claim, dict), (
                f"evidence_usage[{eid}][{i}] must be dict, got {type(claim).__name__}"
            )
            # 驗必要欄位（GroundedClaim schema）
            assert "claim" in claim, f"Missing 'claim' field in evidence_usage[{eid}][{i}]"
            assert "reasoning_type" in claim, (
                f"Missing 'reasoning_type' in evidence_usage[{eid}][{i}]"
            )
            assert "confidence" in claim, (
                f"Missing 'confidence' in evidence_usage[{eid}][{i}]"
            )


# ============================================================================
# Test 7 (TDD RED): mock_bab loads book_outline — state.book_outline_json 含 5 章
# ============================================================================

@pytest.mark.asyncio
async def test_mock_bab_loads_book_outline_5_chapters():
    """mock_bab=True → state.book_outline_json 含 5 章 BookOutline（從 fixture 載入）。

    TDD RED: 在 orchestrator mock_bab 分支尚未呼叫 _load_mock_book_outline_fixture()
    前，state.book_outline_json 為空字串 → 此測試應 FAIL。
    修法後：state.book_outline_json 含完整 BookOutline，可 model_validate_json。
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import BookOutline

    handler = _make_handler()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
    orch.mock_bab = True

    state = LiveResearchStageState()
    orch._emit_stage_change = AsyncMock()
    orch._emit_checkpoint = AsyncMock()

    state = await orch._run_stage_1(state, query="台灣綠能農漁村衝突", initial_items=None)

    # 驗 state.book_outline_json 非空
    assert state.book_outline_json, (
        "state.book_outline_json should not be empty after mock_bab Stage 1 load"
    )

    # 驗可 parse 成 BookOutline
    bo = BookOutline.model_validate_json(state.book_outline_json)

    # 驗 5 章
    assert len(bo.chapters) == 5, (
        f"Expected 5 chapters from fixture, got {len(bo.chapters)}"
    )

    # 驗章節標題（fixture 的 5 章：前言 / 國內案例文獻 / 國外案例文獻 / 結果與討論 / 結論）
    titles = [c.title for c in bo.chapters]
    assert "前言" in titles, f"Expected '前言' chapter, got titles={titles}"
    assert "結論" in titles, f"Expected '結論' chapter, got titles={titles}"

    # 驗 role 結構（index 0 = intro，last = conclusion）
    assert bo.chapters[0].role == "intro", (
        f"Chapter 0 should be role='intro', got '{bo.chapters[0].role}'"
    )
    assert bo.chapters[-1].role == "conclusion", (
        f"Last chapter should be role='conclusion', got '{bo.chapters[-1].role}'"
    )


# ============================================================================
# Test 8 (TDD RED): mock_bab syncs format_specs["chapters"] with 5-chapter structure
# ============================================================================

@pytest.mark.asyncio
async def test_mock_bab_syncs_format_specs_chapters():
    """mock_bab=True → format_specs["chapters"] 含 5 個 {"name": ..., "outline": ...} dict。

    TDD RED: 在 mock_bab 分支未寫入 format_specs["chapters"] 前，
    _resolve_chapter_source 看不到 chapters → 走 core_topics fallback（10 章），
    此測試應 FAIL。
    修法後：format_specs["chapters"] 有 5 個 entry，_resolve_chapter_source 走
    chapter-override 路徑。
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = _make_handler()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
    orch.mock_bab = True

    state = LiveResearchStageState()
    orch._emit_stage_change = AsyncMock()
    orch._emit_checkpoint = AsyncMock()

    state = await orch._run_stage_1(state, query="台灣綠能農漁村衝突", initial_items=None)

    # 驗 format_specs 不為空
    assert state.format_specs, (
        "state.format_specs should not be empty after mock_bab Stage 1 load"
    )

    chapters = state.format_specs.get("chapters")
    assert chapters, (
        "state.format_specs['chapters'] should not be empty after mock_bab Stage 1 load"
    )

    # 驗 5 個 chapters
    assert len(chapters) == 5, (
        f"Expected 5 format_specs chapters, got {len(chapters)}"
    )

    # 驗每個 entry 有 name + outline 欄位
    for i, ch in enumerate(chapters):
        assert isinstance(ch, dict), (
            f"format_specs.chapters[{i}] must be dict, got {type(ch).__name__}"
        )
        assert "name" in ch and ch["name"], (
            f"format_specs.chapters[{i}] missing 'name'"
        )
        assert "outline" in ch, (
            f"format_specs.chapters[{i}] missing 'outline'"
        )

    # 驗第一章名稱
    assert chapters[0]["name"] == "前言", (
        f"Expected first chapter name '前言', got '{chapters[0]['name']}'"
    )
    assert chapters[-1]["name"] == "結論", (
        f"Expected last chapter name '結論', got '{chapters[-1]['name']}'"
    )
