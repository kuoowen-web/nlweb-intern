"""TimeRange schema + Stage1ParsedIntent.time_range_extracted +
EvidencePoolEntry.published_at（Track E E1，sprint 2026-05-28）。

CEO 拍 Option A：EvidencePoolEntry 加 Optional published_at 屬合理擴張，
不視為動 Track A frozen schema 結構。
"""
import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))


# ----------------------------------------------------------------------------
# TimeRange schema 基本 contract
# ----------------------------------------------------------------------------

def test_time_range_minimal():
    """user 只給 start_date（如「2024 後」）→ end_date 自動 None。"""
    from reasoning.schemas_live import TimeRange
    tr = TimeRange(start_date="2024-01-01", raw_phrase="2024 之後", user_selected=True)
    assert tr.start_date == "2024-01-01"
    assert tr.end_date is None
    assert tr.user_selected is True
    assert tr.raw_phrase == "2024 之後"


def test_time_range_both_bounds():
    """user 給範圍（如「2020-2023」）→ start/end 同存在。"""
    from reasoning.schemas_live import TimeRange
    tr = TimeRange(start_date="2020-01-01", end_date="2023-12-31", raw_phrase="2020-2023")
    assert tr.start_date == "2020-01-01"
    assert tr.end_date == "2023-12-31"


def test_time_range_invalid_iso_raises():
    """非 ISO 8601 格式 → Pydantic ValidationError。"""
    from reasoning.schemas_live import TimeRange
    with pytest.raises(ValidationError):
        TimeRange(start_date="2024/01/01", raw_phrase="x")  # 斜線不是 ISO 8601


def test_time_range_empty_string_normalized_to_none():
    """空字串視為 None（serializer 友善）。"""
    from reasoning.schemas_live import TimeRange
    tr = TimeRange(start_date="", end_date="", raw_phrase="")
    assert tr.start_date is None
    assert tr.end_date is None


def test_time_range_serializable():
    """model_dump → model_validate round-trip 不掉資料。"""
    from reasoning.schemas_live import TimeRange
    tr = TimeRange(start_date="2024-01-01", raw_phrase="2024 後", user_selected=True)
    d = tr.model_dump()
    tr2 = TimeRange.model_validate(d)
    assert tr2 == tr


# ----------------------------------------------------------------------------
# Stage1ParsedIntent.time_range_extracted 容錯
# ----------------------------------------------------------------------------

def test_stage1_parsed_intent_default_time_range_extracted_none():
    """既有 confirm fixture 不傳 time_range_extracted → 預設 None，不 break。"""
    from reasoning.schemas_live import Stage1ParsedIntent
    intent = Stage1ParsedIntent(action="confirm")
    assert intent.time_range_extracted is None


def test_stage1_parsed_intent_with_time_range():
    """user 提時間訴求 → time_range_extracted 入庫。"""
    from reasoning.schemas_live import Stage1ParsedIntent, TimeRange
    intent = Stage1ParsedIntent(
        action="confirm",
        time_range_extracted=TimeRange(
            start_date="2024-01-01",
            raw_phrase="2024 後",
            user_selected=True,
        ),
    )
    assert intent.time_range_extracted is not None
    assert intent.time_range_extracted.start_date == "2024-01-01"
    assert intent.time_range_extracted.user_selected is True


# ----------------------------------------------------------------------------
# EvidencePoolEntry.published_at（Option A）
# ----------------------------------------------------------------------------

def test_evidence_pool_entry_published_at_default_none():
    """新欄位預設 None — backward-compat（既有 row 沒此欄位）。"""
    from reasoning.schemas_live import EvidencePoolEntry
    e = EvidencePoolEntry(evidence_id=1)
    assert e.published_at is None


def test_evidence_pool_entry_published_at_set():
    """loop_engine 入庫時填 ISO date → 正常存。"""
    from reasoning.schemas_live import EvidencePoolEntry
    e = EvidencePoolEntry(evidence_id=1, published_at="2024-06-15")
    assert e.published_at == "2024-06-15"
