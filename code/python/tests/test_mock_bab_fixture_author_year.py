"""mock_bab fixture metadata 驗證（2026-06-09 更新至 prod session 5767ae4a 真語料）。

原始版本驗證舊 21 筆 fixture（author/year backfill）。
2026-06-09：fixture 換為 prod session 5767ae4a 的 36 筆真實 evidence，
真實語料的 author/year 來自 retrieval metadata，部分 entry 為空（尤其 year 欄位）。
測試調整為：驗證 pool 有 36 筆 + source_domain 不是 example.com 佔位符 + 結構正確。
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
    return h


@pytest.mark.asyncio
async def test_mock_bab_fixture_has_36_entries():
    """2026-06-09: 真實 fixture (session 5767ae4a) 有 36 筆 evidence。

    舊 fixture 是 21 筆 backfill，新 fixture 是 prod 真語料 36 筆。
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

    assert len(pool) == 36, f"Expected 36 entries, got {len(pool)}"
    assert set(pool.keys()) == set(range(1, 37)), f"Keys should be 1-36"


@pytest.mark.asyncio
async def test_mock_bab_fixture_entries_have_author():
    """真實語料中大多數 entries 有 author，但部分學術文章/轉載無署名屬正常。

    斷言：27/36 條 entries author 非空（9 條確認無署名，可 fallback to source_domain）。
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

    assert len(pool) == 36
    entries_with_author = [eid for eid, e in pool.items() if e.author.strip()]
    # 真實語料：27/36 有 author（9 條學術/轉載無署名），至少 20 條應有 author
    assert len(entries_with_author) >= 20, (
        f"Expected >=20 entries with author in real fixture, got {len(entries_with_author)}"
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

    assert len(pool) == 36
    # 大多數 entries 有 published_at（學術文章無 published_at 屬正常）
    entries_with_date = [
        eid for eid, e in pool.items()
        if e.published_at and e.published_at.strip()
    ]
    assert len(entries_with_date) >= 25, (
        f"Expected >=25 entries with published_at, got {len(entries_with_date)}"
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
