"""mock_bab fixture metadata 驗證（2026-07 更新至 Cayenne prod session 8e1db658 真語料）。

原始版本驗證舊 21 筆 fixture（author/year backfill）；2026-06-09 換 5767ae4a（36 筆）；
2026-07（lr-stage1-stage4-cayenne-fix plan）換為 Cayenne 綠能命題 session 8e1db658
的 567 筆真實 evidence（internal 493 / llm_knowledge 45 / web 29）。
真實語料的 author/year 來自 retrieval metadata，部分 entry 為空（尤其 year 欄位；
llm_knowledge 類 entry 多無署名屬正常）。
測試調整為：驗證 pool 有 567 筆 + source_domain 不是 example.com 佔位符 + 結構正確。
year 欄位空字串在真實語料中合法（published_at 有 ISO 8601 日期可 fallback）。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from reasoning.live_research.stage_state import LiveResearchStageState
from reasoning.schemas_live import deserialize_evidence_pool


def _make_handler():
    h = MagicMock()
    h.site = "all"
    h.query_params = {}
    h.message_sender = None
    h.connection_alive_event = None
    h.http_handler = None
    h._save_state = AsyncMock()  # _persist_progress durable boundary awaits this
    return h


@pytest.mark.asyncio
async def test_mock_bab_fixture_has_36_entries():
    """2026-07: 真實 fixture (Cayenne session 8e1db658) 有 567 筆 evidence。

    舊 fixture 是 36 筆（5767ae4a），新 fixture 是 Cayenne prod 真語料 567 筆。
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = _make_handler()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
    orch.mock_bab = True

    state = LiveResearchStageState()
    orch._emit_stage_change = AsyncMock()
    orch._emit_checkpoint = AsyncMock()

    state = await orch._run_stage_1(state, query="x", initial_items=None)
    pool = deserialize_evidence_pool(state.evidence_pool_json)

    assert len(pool) == 567, f"Expected 567 entries, got {len(pool)}"
    assert set(pool.keys()) == set(range(1, 568)), f"Keys should be 1-567"


@pytest.mark.asyncio
async def test_mock_bab_fixture_entries_have_author():
    """真實語料中部分 entries 有 author；llm_knowledge 類 entry 多無署名屬正常。

    斷言：93/567 條 entries author 非空（實測值），門檻取 >= 80。
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = _make_handler()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
    orch.mock_bab = True

    state = LiveResearchStageState()
    orch._emit_stage_change = AsyncMock()
    orch._emit_checkpoint = AsyncMock()

    state = await orch._run_stage_1(state, query="x", initial_items=None)
    pool = deserialize_evidence_pool(state.evidence_pool_json)

    assert len(pool) == 567
    entries_with_author = [eid for eid, e in pool.items() if e.author.strip()]
    # 真實語料：93/567 有 author（llm_knowledge 類 entry 多無署名屬正常），至少 80 條應有 author
    assert len(entries_with_author) >= 80, (
        f"Expected >=80 entries with author in real fixture, got {len(entries_with_author)}"
    )


@pytest.mark.asyncio
async def test_mock_bab_fixture_entries_have_published_at():
    """真實語料：大多數 entries 有 published_at（ISO date string），可 APA 年份 fallback。

    year 欄位在真實語料中為空字串，但 published_at 提供日期供 render 使用。
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = _make_handler()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
    orch.mock_bab = True

    state = LiveResearchStageState()
    orch._emit_stage_change = AsyncMock()
    orch._emit_checkpoint = AsyncMock()

    state = await orch._run_stage_1(state, query="x", initial_items=None)
    pool = deserialize_evidence_pool(state.evidence_pool_json)

    assert len(pool) == 567
    # 部分 entries 有 published_at（學術文章/llm_knowledge 無 published_at 屬正常；實測 120/567）
    entries_with_date = [
        eid for eid, e in pool.items()
        if e.published_at and e.published_at.strip()
    ]
    assert len(entries_with_date) >= 100, (
        f"Expected >=100 entries with published_at, got {len(entries_with_date)}"
    )


@pytest.mark.asyncio
async def test_mock_bab_fixture_author_not_url_domain():
    """author 不應該是 URL domain（避免 example.com / xxx.com 偽裝成 author）。

    註：APA initial（如 'Müller, A.'）合法，因此只 reject 看起來像 domain 的字串
    （結尾為 .com / .tw / .org / .gov 等 TLD）。
    """
    import re
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator

    handler = _make_handler()
    orch = LiveResearchOrchestrator(handler=handler, dry_run=False)
    orch.mock_bab = True

    state = LiveResearchStageState()
    orch._emit_stage_change = AsyncMock()
    orch._emit_checkpoint = AsyncMock()

    state = await orch._run_stage_1(state, query="x", initial_items=None)
    pool = deserialize_evidence_pool(state.evidence_pool_json)

    domain_pat = re.compile(r"\.(com|tw|org|gov|net|edu|io|de)(\.|$)", re.IGNORECASE)
    for eid, e in pool.items():
        assert not domain_pat.search(e.author), (
            f"entry {eid} author={e.author!r} 看起來像 URL domain，不是真實 author"
        )
        assert "example.com" not in e.author.lower()
        assert "example.com" not in e.source_domain.lower(), (
            f"entry {eid} source_domain={e.source_domain!r} 仍是 example.com 佔位符"
        )
