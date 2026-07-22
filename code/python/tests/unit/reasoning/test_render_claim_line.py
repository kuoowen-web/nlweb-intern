"""Tests for _render_claim_line helper (Task 18 refactor, sprint 2026-06-18)。

抽 render_grounded_narrative / render_grounding_evidence_view 共用的 claim 行格式:
- 兩函式 claim 行皆用全形括號「（ ）」「，」「：」（無 fullwidth 維度差異）。
- 真實差異是 prefix「推論」的有無：
    - render_grounded_narrative 傳 prefix="推論" → `- 推論（rtype，conf）：claim`
    - render_grounding_evidence_view 傳 prefix=""  → `- （rtype，conf）：claim`
- WARN tag 規則共用：行首 `[confidence: low | critic_status: WARN] ` 內嵌於 dash 後、prefix 前。
- empty-skip / title-snippet 處理 NOT 在 helper（留各 caller）。
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))


def test_render_narrative_claim_has_prefix_fullwidth():
    from reasoning.schemas_live import _render_claim_line
    gc = {
        "reasoning_type": "因果",
        "confidence": "高",
        "claim": "X 導致 Y",
        "critic_status": "PASS",
    }
    assert _render_claim_line(gc, prefix="推論") == "- 推論（因果，高）：X 導致 Y"


def test_render_evidence_claim_no_prefix_fullwidth():
    from reasoning.schemas_live import _render_claim_line
    gc = {
        "reasoning_type": "因果",
        "confidence": "高",
        "claim": "X",
        "critic_status": "PASS",
    }
    # 空 prefix → dash + 一個 literal 空白 + 全形左括號，無額外空白
    assert _render_claim_line(gc, prefix="") == "- （因果，高）：X"


def test_render_narrative_warn_claim_has_tag():
    from reasoning.schemas_live import _render_claim_line
    gc = {
        "reasoning_type": "因果",
        "confidence": "低",
        "claim": "X",
        "critic_status": "WARN",
    }
    out = _render_claim_line(gc, prefix="推論")
    # WARN tag 內嵌於 dash 後、prefix 前
    assert out == "- [confidence: low | critic_status: WARN] 推論（因果，低）：X"


def test_render_evidence_warn_claim_has_tag():
    from reasoning.schemas_live import _render_claim_line
    gc = {
        "reasoning_type": "因果",
        "confidence": "低",
        "claim": "X",
        "critic_status": "WARN",
    }
    out = _render_claim_line(gc, prefix="")
    # 空 prefix WARN：dash + tag(末帶空白) + 全形左括號
    assert out == "- [confidence: low | critic_status: WARN] （因果，低）：X"


def test_render_claim_line_missing_fields_default_empty():
    """缺欄位時 .get 預設：rtype/conf/claim 空字串、critic_status 預設 PASS。"""
    from reasoning.schemas_live import _render_claim_line
    assert _render_claim_line({}, prefix="推論") == "- 推論（，）："
    assert _render_claim_line({}, prefix="") == "- （，）："


# --- Characterization：整函式輸出 byte-for-byte 鎖定（refactor 前後一致）---

def test_render_grounded_narrative_golden_with_warn():
    from reasoning.schemas_live import render_grounded_narrative, EvidencePoolEntry
    pool = {
        1: EvidencePoolEntry(evidence_id=1, title="T1", url="u", snippet="S1"),
    }
    usage = {
        1: [
            {"claim": "claim-A", "reasoning_type": "因果",
             "confidence": "高", "critic_status": "PASS"},
            {"claim": "claim-B", "reasoning_type": "歸納",
             "confidence": "低", "critic_status": "WARN"},
        ],
    }
    text = render_grounded_narrative(
        chapter_eids=[1], evidence_usage=usage, evidence_pool=pool,
    )
    expected = (
        "### [1] T1（S1）\n"
        "- 推論（因果，高）：claim-A\n"
        "- [confidence: low | critic_status: WARN] 推論（歸納，低）：claim-B"
    )
    assert text == expected


def test_render_grounding_evidence_view_golden_with_warn():
    from reasoning.schemas_live import render_grounding_evidence_view, EvidencePoolEntry
    pool = {
        1: EvidencePoolEntry(evidence_id=1, title="T1", url="u", snippet="S1"),
    }
    usage = {
        1: [
            {"claim": "claim-A", "reasoning_type": "因果",
             "confidence": "高", "critic_status": "PASS"},
            {"claim": "claim-B", "reasoning_type": "歸納",
             "confidence": "低", "critic_status": "WARN"},
        ],
    }
    text = render_grounding_evidence_view(
        chapter_eids=[1], evidence_usage=usage, evidence_pool=pool,
        prior_grounded_entities=[],
    )
    expected = (
        "### [1] T1\n"
        "S1\n"
        "- （因果，高）：claim-A\n"
        "- [confidence: low | critic_status: WARN] （歸納，低）：claim-B"
    )
    assert text == expected
