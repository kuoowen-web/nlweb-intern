"""GroundedClaim / BlockedSection / LiveWriterSectionOutput.status schema (Track A Task 1)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))


def test_grounded_claim_required_fields():
    from reasoning.schemas_live import GroundedClaim
    gc = GroundedClaim(
        claim="台灣再生能源占比達 9.5%",
        reasoning_type="induction",
        confidence="high",
        source_topic="topic-a",
        source_iteration=1,
    )
    assert gc.claim == "台灣再生能源占比達 9.5%"
    assert gc.reasoning_type == "induction"
    assert gc.confidence == "high"
    assert gc.source_topic == "topic-a"
    assert gc.source_iteration == 1
    # default critic_status = PASS
    assert gc.critic_status == "PASS"


def test_grounded_claim_serializable():
    from reasoning.schemas_live import GroundedClaim
    gc = GroundedClaim(
        claim="x", reasoning_type="deduction", confidence="medium",
        source_topic="t", source_iteration=2,
    )
    d = gc.model_dump()
    assert d["claim"] == "x"
    gc2 = GroundedClaim.model_validate(d)
    assert gc2 == gc


def test_grounded_claim_critic_status_literal():
    """addendum I-5 + Gemini C-1: critic_status accepts PASS/WARN/REJECT。"""
    from reasoning.schemas_live import GroundedClaim
    from pydantic import ValidationError
    for cs in ("PASS", "WARN", "REJECT"):
        gc = GroundedClaim(
            claim="c", reasoning_type="induction", confidence="high",
            source_topic="t", source_iteration=1, critic_status=cs,
        )
        assert gc.critic_status == cs
    with pytest.raises(ValidationError):
        GroundedClaim(
            claim="c", reasoning_type="induction", confidence="high",
            source_topic="t", source_iteration=1, critic_status="INVALID",
        )


def test_blocked_section_schema():
    """addendum C-1: BlockedSection structured output。"""
    from reasoning.schemas_live import BlockedSection
    b = BlockedSection(
        chapter_index=2,
        title="國外案例",
        status="blocked_no_evidence",
        content="[本章資料不足] 系統未為本章配置足夠 evidence，已跳過 LLM 生成以避免編造。",
        require_review=True,
    )
    assert b.chapter_index == 2
    assert b.status == "blocked_no_evidence"
    assert b.require_review is True


def test_live_writer_section_output_status_enum_default():
    """addendum C-2: LiveWriterSectionOutput.status default='drafted'。"""
    from reasoning.schemas_live import LiveWriterSectionOutput
    out = LiveWriterSectionOutput(
        section_title="x", section_content="y", sources_used=[],
        confidence_level="High",
    )
    assert out.status == "drafted"


def test_live_writer_section_output_status_enum_values():
    """status 必須是 Literal["drafted","guard_failed","blocked_no_evidence","accepted"]。"""
    from reasoning.schemas_live import LiveWriterSectionOutput
    from pydantic import ValidationError
    for s in ("drafted", "guard_failed", "blocked_no_evidence", "accepted"):
        out = LiveWriterSectionOutput(
            section_title="x", section_content="y", sources_used=[],
            confidence_level="Medium", status=s,
        )
        assert out.status == s
    with pytest.raises(ValidationError):
        LiveWriterSectionOutput(
            section_title="x", section_content="y", sources_used=[],
            confidence_level="Medium", status="invalid_status",
        )


def test_sources_used_subset_of_planned_invariant_logs_warning_runtime(monkeypatch):
    """addendum C-4 + codex Imp-1: runtime mode (no LR_STRICT_INVARIANTS env) → log warning, no raise。

    NOTE: 專案用自製 LazyLogger 不 propagate 到 root logger，所以 caplog 抓不到。
    改用 monkeypatch 攔 logger.warning 確認被呼叫且訊息含關鍵字。
    """
    monkeypatch.delenv("LR_STRICT_INVARIANTS", raising=False)
    from reasoning import schemas_live as sl
    captured = []

    def fake_warning(msg, *args, **kwargs):
        captured.append(msg if not args else (msg % args))

    monkeypatch.setattr(sl.logger, "warning", fake_warning)

    out = sl.LiveWriterSectionOutput(
        section_title="x", section_content="y", sources_used=[1, 99],
        confidence_level="Medium",
    )
    out.validate_sources_against_plan(planned_evidence_ids=[1, 2])
    assert any("99" in m and "planned" in m.lower() for m in captured)


def test_sources_used_subset_of_planned_invariant_raises_in_strict_mode(monkeypatch):
    """codex Imp-1: test/CI mode (LR_STRICT_INVARIANTS=1) → raise InvariantViolation。"""
    monkeypatch.setenv("LR_STRICT_INVARIANTS", "1")
    from reasoning.schemas_live import (
        LiveWriterSectionOutput, InvariantViolation,
    )
    out = LiveWriterSectionOutput(
        section_title="x", section_content="y", sources_used=[1, 99],
        confidence_level="Medium",
    )
    with pytest.raises(InvariantViolation) as exc_info:
        out.validate_sources_against_plan(planned_evidence_ids=[1, 2])
    assert "99" in str(exc_info.value)


def test_sources_used_zero_is_allowed():
    """sources_used contains 0 (no-citation marker) → not a violation。"""
    from reasoning.schemas_live import LiveWriterSectionOutput
    out = LiveWriterSectionOutput(
        section_title="x", section_content="y", sources_used=[0],
        confidence_level="Medium",
    )
    # 不論 strict / runtime 都應 silent pass (0 is allowed)
    out.validate_sources_against_plan(planned_evidence_ids=[1, 2])


def test_validate_sources_allows_full_pool_not_just_planned(monkeypatch):
    """P2 W8（§0 #16 R2-2）：傳 allowed_evidence_ids（全 pool）→ pool 內 planned 外的
    sources_used 不該 violation；不傳 → 向後相容退回 planned ∪ {0}。"""
    monkeypatch.setenv("LR_STRICT_INVARIANTS", "1")
    from reasoning.schemas_live import LiveWriterSectionOutput, InvariantViolation
    out = LiveWriterSectionOutput(
        section_title="x", section_content="y", sources_used=[3],
        confidence_level="Medium",
    )
    # 3 不在 planned=[1,2] 但在 pool=[1,2,3] → 傳 allowed 全 pool → 不 raise
    out.validate_sources_against_plan(
        planned_evidence_ids=[1, 2], allowed_evidence_ids=[1, 2, 3]
    )
    # 不傳 allowed_evidence_ids → 向後相容（退回 planned ∪ {0}）→ raise
    with pytest.raises(InvariantViolation):
        out.validate_sources_against_plan(planned_evidence_ids=[1, 2])


# ============================================================================
# Fix 1：from_warned_critic_review 作為 GroundedClaim 正式欄位 (T1 schema review)
# ============================================================================

def test_grounded_claim_from_warned_critic_review_default_false():
    """Fix 1 — from_warned_critic_review 欄位預設為 False。"""
    from reasoning.schemas_live import GroundedClaim
    gc = GroundedClaim(
        claim="測試 claim",
        reasoning_type="induction",
        confidence="high",
        source_topic="topic-x",
        source_iteration=1,
    )
    assert gc.from_warned_critic_review is False


def test_grounded_claim_from_warned_critic_review_true_preserved_in_model_dump():
    """Fix 1 — from_warned_critic_review=True 在 model_dump() 必須保留（不被 silent drop）。"""
    from reasoning.schemas_live import GroundedClaim
    gc = GroundedClaim(
        claim="WARN 降級 claim",
        reasoning_type="deduction",
        confidence="low",
        source_topic="topic-warn",
        source_iteration=2,
        critic_status="WARN",
        from_warned_critic_review=True,
    )
    d = gc.model_dump()
    assert "from_warned_critic_review" in d
    assert d["from_warned_critic_review"] is True


def test_grounded_claim_from_warned_critic_review_round_trip():
    """Fix 1 — model_dump → model_validate round-trip 必須保留 from_warned_critic_review=True。"""
    from reasoning.schemas_live import GroundedClaim
    original = GroundedClaim(
        claim="round-trip test",
        reasoning_type="abduction",
        confidence="medium",
        source_topic="topic-rt",
        source_iteration=3,
        critic_status="WARN",
        from_warned_critic_review=True,
    )
    d = original.model_dump()
    restored = GroundedClaim.model_validate(d)
    assert restored.from_warned_critic_review is True
