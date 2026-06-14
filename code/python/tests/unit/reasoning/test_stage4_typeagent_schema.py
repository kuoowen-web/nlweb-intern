"""Target 1 — Stage4Intent strict typed schema validator tests.

Plan: lr-typeagent-refactor (2026-05-19, CEO 激進 mode — 沒 backward compat tax)
- ChapterSpec / SpecialElementSpec typed sub-models
- Stage4Intent.new_chapters / special_elements 升 typed list
- **不**加 _ELEMENT_KEYWORDS validator（OQ-2 CEO 拍板：純 typed schema + few-shot）
"""
import pytest
from pydantic import ValidationError


# ──────────────────────────────────────────────────────────────────────────────
# ChapterSpec basics
# ──────────────────────────────────────────────────────────────────────────────

def test_chapter_spec_basic():
    from reasoning.schemas_live import ChapterSpec
    ch = ChapterSpec(name="前言")
    assert ch.type == "narrative_chapter"
    assert ch.relevance == "core"


def test_chapter_spec_empty_name_rejected():
    from reasoning.schemas_live import ChapterSpec
    with pytest.raises(ValidationError):
        ChapterSpec(name="")


def test_chapter_spec_full_fields():
    from reasoning.schemas_live import ChapterSpec
    ch = ChapterSpec(name="結論", description="收尾", relevance="supporting")
    assert ch.name == "結論"
    assert ch.description == "收尾"
    assert ch.relevance == "supporting"


# ──────────────────────────────────────────────────────────────────────────────
# SpecialElementSpec basics
# ──────────────────────────────────────────────────────────────────────────────

def test_special_element_spec_basic():
    from reasoning.schemas_live import SpecialElementSpec
    e = SpecialElementSpec(type="table", description="比較表")
    assert e.target_chapter == ""
    assert e.type == "table"


def test_special_element_spec_invalid_type_rejected():
    from reasoning.schemas_live import SpecialElementSpec
    with pytest.raises(ValidationError):
        SpecialElementSpec(type="paragraph")  # 非 enum 值


def test_special_element_spec_all_enum_values():
    from reasoning.schemas_live import SpecialElementSpec
    for t in ("table", "list", "chart", "diagram", "code_block"):
        e = SpecialElementSpec(type=t)
        assert e.type == t


# ──────────────────────────────────────────────────────────────────────────────
# Stage4Intent typed coercion
# ──────────────────────────────────────────────────────────────────────────────

def test_stage4intent_dict_coerced_to_typed():
    """既有 fixture 用 dict 不帶 type → pydantic coerce + default type 補齊。"""
    from reasoning.schemas_live import Stage4Intent
    intent = Stage4Intent(
        intent="structure_change",
        new_chapters=[{"name": "前言"}, {"name": "結論"}],
        special_elements=[{"type": "table", "description": "比較表"}],
    )
    assert intent.new_chapters[0].type == "narrative_chapter"
    assert intent.new_chapters[0].name == "前言"
    assert intent.special_elements[0].type == "table"
    assert intent.special_elements[0].target_chapter == ""


def test_stage4intent_typed_mixed_reply():
    """spec acceptance — 「五章 + 比較表」mixed reply → 5 chapters + 1 element."""
    from reasoning.schemas_live import Stage4Intent
    intent = Stage4Intent(
        intent="mixed",
        new_chapters=[
            {"name": "前言"},
            {"name": "國內案例"},
            {"name": "國外案例"},
            {"name": "結果與討論"},
            {"name": "結論"},
        ],
        special_elements=[{"type": "table", "description": "5 國能源比較"}],
        format_spec_extracted="各章 1000 字",
        citation_style_extracted="author_year",
    )
    assert len(intent.new_chapters) == 5
    assert len(intent.special_elements) == 1
    assert intent.special_elements[0].type == "table"


def test_stage4intent_chapter_must_have_name():
    """typed schema — 章節名空字串 → ValidationError（min_length=1）."""
    from reasoning.schemas_live import Stage4Intent
    with pytest.raises(ValidationError):
        Stage4Intent(
            intent="structure_change",
            new_chapters=[{"name": ""}],
        )


def test_stage4intent_special_element_must_have_valid_type():
    """typed schema — special_element type 必須為 enum 值。"""
    from reasoning.schemas_live import Stage4Intent
    with pytest.raises(ValidationError):
        Stage4Intent(
            intent="format_spec",
            special_elements=[{"type": "paragraph"}],  # 不在 enum
        )
