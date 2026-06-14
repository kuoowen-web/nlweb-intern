"""Track B1：_build_references_block 改成列 evidence_pool ALL 條目。

RED tests — 驗 B1 expected 行為（改前應 FAIL）：
- 被引用的條目列在「## 參考文獻」段（與現有行為相同）
- 沒被引用的條目列在「## 研究時搜尋到的相關資料」附後段（B1 新增）
- 空 pool → 空字串（不變）
- 所有 pool 條目都被引用 → 無附後段
"""

import pytest
from unittest.mock import MagicMock

from reasoning.live_research.stage_state import LiveResearchStageState
from reasoning.schemas_live import EvidencePoolEntry, serialize_evidence_pool


def _make_orchestrator():
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    handler = MagicMock()
    handler.site = "all"
    handler.query_params = {}
    handler.message_sender = None
    handler.connection_alive_event = None
    handler.request_handler = None
    return LiveResearchOrchestrator(handler=handler, dry_run=True)


def test_b1_uncited_pool_entries_listed_in_appendix():
    """B1 核心：pool 有 3 條，sections 只引用 1、2；條目 3 沒被引用
    → 應出現「研究時搜尋到的相關資料」附後段，且 [3] 在其中。
    """
    orch = _make_orchestrator()
    pool = {
        1: EvidencePoolEntry(evidence_id=1, title="光電發展", url="https://a.com/1",
                             source_domain="a.com"),
        2: EvidencePoolEntry(evidence_id=2, title="風電進度", url="https://b.com/2",
                             source_domain="b.com"),
        3: EvidencePoolEntry(evidence_id=3, title="核能爭議", url="https://c.com/3",
                             source_domain="c.com"),
    }
    state = LiveResearchStageState(
        evidence_pool_json=serialize_evidence_pool(pool),
        written_sections=[
            {"section_index": 0, "title": "T1", "content": "...", "sources_used": [1, 2]},
        ],
    )

    block = orch._build_references_block(state)

    # 引用段仍在
    assert "## 參考文獻" in block
    assert "[1]" in block and "光電發展" in block
    assert "[2]" in block and "風電進度" in block

    # B1 新增：未引用條目列在附後段
    assert "研究時搜尋到的相關資料" in block
    assert "[3]" in block and "核能爭議" in block


def test_b1_all_pool_entries_cited_no_appendix():
    """全部 pool 條目都被引用 → 無附後段（無「研究時搜尋到的相關資料」）。"""
    orch = _make_orchestrator()
    pool = {
        1: EvidencePoolEntry(evidence_id=1, title="A", url="https://a.com"),
        2: EvidencePoolEntry(evidence_id=2, title="B", url="https://b.com"),
    }
    state = LiveResearchStageState(
        evidence_pool_json=serialize_evidence_pool(pool),
        written_sections=[
            {"section_index": 0, "title": "T", "content": "...", "sources_used": [1, 2]},
        ],
    )

    block = orch._build_references_block(state)

    assert "## 參考文獻" in block
    assert "研究時搜尋到的相關資料" not in block


def test_b1_no_cited_sections_but_pool_has_entries_lists_all_as_appendix():
    """sections 無任何引用但 pool 有 entries → 全部列在附後段。

    B1 行為：沒有 cited_ids → 不列「## 參考文獻」段，但 pool 非空
    → 列「研究時搜尋到的相關資料」附後段（pool 全部條目）。
    """
    orch = _make_orchestrator()
    pool = {
        1: EvidencePoolEntry(evidence_id=1, title="A", url="https://a.com"),
        2: EvidencePoolEntry(evidence_id=2, title="B", url="https://b.com"),
    }
    state = LiveResearchStageState(
        evidence_pool_json=serialize_evidence_pool(pool),
        written_sections=[
            {"section_index": 0, "title": "T", "content": "x", "sources_used": []},
        ],
    )

    block = orch._build_references_block(state)

    # 無引用 → 不列「## 參考文獻」
    # B1 新增：pool 非空 → 列附後段
    assert "研究時搜尋到的相關資料" in block
    assert "[1]" in block
    assert "[2]" in block


def test_b1_empty_pool_still_returns_empty_string():
    """空 pool → 空字串（不變）。"""
    orch = _make_orchestrator()
    state = LiveResearchStageState(evidence_pool_json="")
    assert orch._build_references_block(state) == ""
