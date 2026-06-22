"""EvidencePoolEntry.suggested_chapters field + OutlinePlanner 反轉回填（P2 W1）.

P2 全局 evidence 模型 W1：為 EvidencePoolEntry 加 suggested_chapters: List[int]
正向 N:M 軟標註欄位（evidence→建議章節），default_factory=list 保 backward-compat。
helper invert_allocation_to_suggested_chapters 把章→planned 反轉聚合為 eid→章。
"""
import pytest


def test_evidence_pool_entry_suggested_chapters_defaults_empty():
    from reasoning.schemas_live import EvidencePoolEntry
    e = EvidencePoolEntry(evidence_id=7, title="t", snippet="s")
    assert e.suggested_chapters == []            # 預設無建議 = 全章可用（軟性低優先）
    e2 = EvidencePoolEntry(evidence_id=8, suggested_chapters=[0, 2, 4])
    assert e2.suggested_chapters == [0, 2, 4]     # 明確允許多章 overlap


def test_old_session_evidence_pool_json_without_suggested_chapters_deserializes():
    # I4（Gemini #1）：舊 session 的 evidence_pool_json 沒有 suggested_chapters 欄位，
    # resume / reload 時必須 default_factory + from_dict fallback，不可報錯。
    from reasoning.schemas_live import deserialize_evidence_pool
    # 模擬舊版序列化（無 suggested_chapters key）
    old_blob = '{"5": {"evidence_id": 5, "title": "舊", "snippet": "s"}}'
    pool = deserialize_evidence_pool(old_blob)
    assert pool[5].suggested_chapters == []       # fallback 到空 list，不 KeyError/ValidationError


def test_invert_allocation_to_suggested_chapters_allows_multichapter():
    from reasoning.agents.outline_planner import invert_allocation_to_suggested_chapters
    per_chapter = {0: [5, 9], 1: [9], 2: [5]}
    result = invert_allocation_to_suggested_chapters(per_chapter)
    assert sorted(result[5]) == [0, 2]
    assert sorted(result[9]) == [0, 1]
    assert result.get(99, []) == []


def test_invert_roundtrip_covers_same_pairs():
    from reasoning.agents.outline_planner import invert_allocation_to_suggested_chapters
    per_chapter = {0: [1, 2], 1: [2, 3], 2: []}
    inv = invert_allocation_to_suggested_chapters(per_chapter)
    fwd = {(eid, ch) for ch, eids in per_chapter.items() for eid in eids}
    got = {(eid, ch) for eid, chs in inv.items() for ch in chs}
    assert fwd == got


def test_suggested_chapters_persisted_to_evidence_pool_json():
    # SF4/R2-4：mutate 後 serialize 回 json，deserialize 後仍帶 suggested_chapters
    # （驗回填確實持久化，serialize 點 = orchestrator outline stage 收尾）。
    from reasoning.schemas_live import (
        EvidencePoolEntry,
        serialize_evidence_pool,
        deserialize_evidence_pool,
    )
    from reasoning.agents.outline_planner import invert_allocation_to_suggested_chapters
    pool = {
        5: EvidencePoolEntry(evidence_id=5, title="A", snippet="s"),
        9: EvidencePoolEntry(evidence_id=9, title="B", snippet="s"),
    }
    per_chapter = {0: [5, 9], 2: [5]}
    suggested_map = invert_allocation_to_suggested_chapters(per_chapter)
    for eid, chapters in suggested_map.items():
        entry = pool.get(eid)
        if entry is not None:
            entry.suggested_chapters = sorted(chapters)
    blob = serialize_evidence_pool(pool)
    again = deserialize_evidence_pool(blob)
    assert again[5].suggested_chapters == [0, 2]
    assert again[9].suggested_chapters == [0]
