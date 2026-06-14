"""EvidencePoolEntry.source field (Track C C1).

Track C C1: 為 EvidencePoolEntry 加 source: Literal["internal","web","wiki","llm_knowledge"]
欄位。default="internal" 保 backward-compat。沿 Track A C-4 / Track E E-AMB-3 lemma
「擴張（新增帶 default 的 Literal 欄位）≠ 修改」。
"""
import pytest
from pydantic import ValidationError


def test_evidence_pool_entry_source_default_internal():
    """default 為 'internal' — Track A 既有 evidence 條目 backward-compat。"""
    from reasoning.schemas_live import EvidencePoolEntry
    e = EvidencePoolEntry(evidence_id=1, title="T", url="u")
    assert e.source == "internal"


def test_evidence_pool_entry_source_explicit_set():
    from reasoning.schemas_live import EvidencePoolEntry
    for src in ("internal", "web", "wiki", "llm_knowledge"):
        e = EvidencePoolEntry(evidence_id=1, source=src)
        assert e.source == src


def test_evidence_pool_entry_source_invalid_raises():
    from reasoning.schemas_live import EvidencePoolEntry
    with pytest.raises(ValidationError):
        EvidencePoolEntry(evidence_id=1, source="external")  # 不在 Literal 中


def test_evidence_pool_entry_source_roundtrip():
    from reasoning.schemas_live import EvidencePoolEntry
    e = EvidencePoolEntry(evidence_id=2, title="T", source="wiki")
    d = e.model_dump()
    assert d["source"] == "wiki"
    e2 = EvidencePoolEntry.model_validate(d)
    assert e2.source == "wiki"


def test_evidence_pool_entry_backward_compat_missing_source():
    """舊 payload 無 source 欄位 → load 後 default 為 'internal'。"""
    from reasoning.schemas_live import EvidencePoolEntry
    # 模擬舊 row（無 source key）
    old_row = {"evidence_id": 3, "title": "old", "url": "x"}
    e = EvidencePoolEntry.model_validate(old_row)
    assert e.source == "internal"
