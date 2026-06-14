"""Tests for LR Stage 6 references master list (Task 10).

涵蓋：
- 給定 mock state（pool + sections with sources_used）→ _build_references_block markdown 正確
- 空 pool → 空字串
- pool 有 entries 但 sections 無 sources_used → 空字串
- sources_used 含 pool 沒有的 ID → 顯示「來源遺失」警示
- 多 section 共用同 ID 只列一次，順序為第一次出現的順序
"""

import pytest
from unittest.mock import MagicMock

from reasoning.live_research import lr_copy
from reasoning.live_research.stage_state import LiveResearchStageState
from reasoning.schemas_live import EvidencePoolEntry, serialize_evidence_pool


def _make_orchestrator():
    """最小化 orchestrator instance（不跑 LLM）。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    handler = MagicMock()
    handler.site = "all"
    handler.query_params = {}
    handler.message_sender = None
    handler.connection_alive_event = None
    handler.request_handler = None
    return LiveResearchOrchestrator(handler=handler, dry_run=True)


def test_references_block_with_evidence_pool():
    """正常 case：pool + sections 引用 → markdown 含「## 參考文獻」段落 + 每條 URL。"""
    orch = _make_orchestrator()

    pool = {
        1: EvidencePoolEntry(
            evidence_id=1, title="光電發展", url="https://a.com/1",
            source_domain="a.com",
        ),
        2: EvidencePoolEntry(
            evidence_id=2, title="風電進度", url="https://b.com/2",
            source_domain="b.com",
        ),
    }
    state = LiveResearchStageState(
        evidence_pool_json=serialize_evidence_pool(pool),
        written_sections=[
            {"section_index": 0, "title": "T1", "content": "...", "sources_used": [1, 2]},
        ],
    )

    block = orch._build_references_block(state)

    assert "## 參考文獻" in block
    assert "[1]" in block and "光電發展" in block and "https://a.com/1" in block
    assert "[2]" in block and "風電進度" in block and "https://b.com/2" in block


def test_references_block_empty_pool():
    """空 pool → 空字串。"""
    orch = _make_orchestrator()
    state = LiveResearchStageState(evidence_pool_json="")
    assert orch._build_references_block(state) == ""


def test_references_block_no_sources_used():
    """Pool 有 entries 但 sections 沒引用 → B1 改：列「研究時搜尋到的相關資料」附後段。

    B1 DR parity（sprint 2026-05-28）：evidence_pool 非空就應該列出全部條目。
    無被引用條目時不產生「## 參考文獻」主段，但 pool 非空仍產生附後段。
    """
    orch = _make_orchestrator()
    pool = {1: EvidencePoolEntry(evidence_id=1, title="A", url="https://a")}
    state = LiveResearchStageState(
        evidence_pool_json=serialize_evidence_pool(pool),
        written_sections=[
            {"section_index": 0, "title": "T", "content": "x", "sources_used": []},
        ],
    )
    block = orch._build_references_block(state)
    # B1 新行為：pool 非空 → 列附後段
    assert "研究時搜尋到的相關資料" in block
    assert "[1]" in block
    assert "A" in block


def test_references_block_phantom_citation():
    """sources_used 含 pool 沒有的 ID → 顯示「來源遺失」警示行（no silent fail）。"""
    orch = _make_orchestrator()
    pool = {1: EvidencePoolEntry(evidence_id=1, title="A", url="https://a")}
    state = LiveResearchStageState(
        evidence_pool_json=serialize_evidence_pool(pool),
        written_sections=[
            {"section_index": 0, "title": "T", "content": "x", "sources_used": [1, 99]},
        ],
    )
    block = orch._build_references_block(state)
    assert "[1]" in block
    assert "[99]" in block
    assert lr_copy.REFERENCE_MISSING_SENTINEL in block


def test_references_block_apa_format():
    """citation_style=author_year → APA 條目「作者. (年份). 標題. 網域. URL」。"""
    from reasoning.live_research.stage_state import UserVoice

    orch = _make_orchestrator()
    pool = {
        1: EvidencePoolEntry(
            evidence_id=1, title="再生能源占比分析", url="https://udn.com/1",
            source_domain="聯合報", author="王柏仁", year="2025",
        ),
    }
    state = LiveResearchStageState(
        evidence_pool_json=serialize_evidence_pool(pool),
        written_sections=[
            {"section_index": 0, "title": "T1", "content": "...", "sources_used": [1]},
        ],
        user_voice=UserVoice(citation_style="author_year"),
    )
    block = orch._build_references_block(state)
    assert "## 參考文獻" in block
    # APA：作者. (年份). 標題.
    assert "王柏仁. (2025). 再生能源占比分析." in block
    assert "https://udn.com/1" in block
    # 不應出現數字格式的 [1] title 開頭
    assert "[1] 再生能源占比分析" not in block


def test_references_block_apa_graceful_degradation():
    """author/year 缺 → 不可輸出 (, ). 滿天；用機構名 + n.d. 合理降級。"""
    from reasoning.live_research.stage_state import UserVoice

    orch = _make_orchestrator()
    pool = {
        1: EvidencePoolEntry(
            evidence_id=1, title="某報導", url="https://cna.com.tw/x",
            source_domain="中央社", author="", year="",
        ),
    }
    state = LiveResearchStageState(
        evidence_pool_json=serialize_evidence_pool(pool),
        written_sections=[
            {"section_index": 0, "title": "T", "content": "x", "sources_used": [1]},
        ],
        user_voice=UserVoice(citation_style="author_year"),
    )
    block = orch._build_references_block(state)
    # author 空 → 用機構名（中央社）；year 空 → (n.d.)
    assert "中央社" in block
    assert "(n.d.)" in block
    # 絕不可出現空作者空年份的退化字串
    assert ". (). " not in block
    assert "(, )" not in block


def test_references_block_numeric_default_unchanged():
    """citation_style=None（預設）→ 維持既有數字格式，不破壞既有行為。"""
    orch = _make_orchestrator()
    pool = {1: EvidencePoolEntry(
        evidence_id=1, title="A", url="https://a", source_domain="a.com",
        author="作者X", year="2024",
    )}
    state = LiveResearchStageState(
        evidence_pool_json=serialize_evidence_pool(pool),
        written_sections=[
            {"section_index": 0, "title": "T", "content": "x", "sources_used": [1]},
        ],
    )
    block = orch._build_references_block(state)
    # 預設仍是 [1] title 數字格式（不是 APA）
    assert "[1] A" in block
    assert "作者X. (2024)" not in block


def test_references_dedupe_and_order():
    """多 section 共用同 ID → 只列一次，順序為第一次出現的 section 順序。"""
    orch = _make_orchestrator()
    pool = {
        1: EvidencePoolEntry(evidence_id=1, title="A", url="https://a"),
        2: EvidencePoolEntry(evidence_id=2, title="B", url="https://b"),
        3: EvidencePoolEntry(evidence_id=3, title="C", url="https://c"),
    }
    state = LiveResearchStageState(
        evidence_pool_json=serialize_evidence_pool(pool),
        written_sections=[
            # Section 0 引用 [3, 1]
            {"section_index": 0, "title": "T0", "content": "x", "sources_used": [3, 1]},
            # Section 1 引用 [1, 2]（1 已出現過，不重列；2 是新）
            {"section_index": 1, "title": "T1", "content": "x", "sources_used": [1, 2]},
        ],
    )

    block = orch._build_references_block(state)
    # 順序：[3], [1], [2]
    pos_3 = block.find("[3]")
    pos_1 = block.find("[1]")
    pos_2 = block.find("[2]")
    assert pos_3 < pos_1 < pos_2
    # 確認每個 ID 只出現一次（在 references block 內）
    assert block.count("[1] A") == 1
    assert block.count("[2] B") == 1
    assert block.count("[3] C") == 1
