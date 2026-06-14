"""A specificity_check 對稱守門 unit test（Cayenne writer 具體化）。

與 entity_grounding_check（fabrication 方向）對稱：specificity_check 偵測
「evidence 有具體資訊但 prose 全抽象」（under-specification 方向）。
"""
import pytest
from reasoning.live_research.hallucination_guard import specificity_check


def _mk_section(content):
    class S:
        def __init__(self, c):
            self.section_content = c
            self.sources_used = [1]
            self.status = "drafted"
    return S(content)


def test_specificity_check_flags_all_abstract_section():
    """evidence 有具體 entity，但 prose 全抽象 → 回 True（需重寫）。"""
    section = _mk_section(
        "相關研究以分配正義、程序正義與承認正義為分析架構，"
        "綜合分析顯示溝通協調有助於降低衝突風險。" * 6  # >200 字才過 guard 長度門檻
    )
    evidence_text = "[1] 德國北萊茵案例 — 回饋金每年 2 萬歐元，依《再生能源法》第 6 條規範"
    section_entities = []  # 抽不到具體 entity（caller 已抽好傳入）
    result = specificity_check(
        section=section,
        chapter_evidence_text=evidence_text,
        section_entities=section_entities,
    )
    assert result is True


def test_specificity_check_passes_concrete_section():
    """prose 已含具體 entity → 回 False（不需重寫）。"""
    section = _mk_section("德國北萊茵案例的回饋金為每年 2 萬歐元，依《再生能源法》第 6 條。")
    evidence_text = "[1] 德國北萊茵案例 — 回饋金每年 2 萬歐元"
    section_entities = ["德國北萊茵", "再生能源法", "2 萬歐元"]
    result = specificity_check(
        section=section,
        chapter_evidence_text=evidence_text,
        section_entities=section_entities,
    )
    assert result is False


def test_specificity_check_skips_when_evidence_has_no_concrete_entity():
    """evidence 本身就沒具體 entity（純概念章）→ 不該 flag（回 False，避免誤殺）。"""
    section = _mk_section("本章討論程序正義的一般性原則。" * 20)
    evidence_text = "[1] 程序正義的理論基礎概述"  # evidence 也抽象
    result = specificity_check(
        section=section,
        chapter_evidence_text=evidence_text,
        section_entities=[],
        evidence_has_concrete=False,  # caller 判定 evidence 無具體資訊
    )
    assert result is False
