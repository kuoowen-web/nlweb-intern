"""B1: Critic.review() critical-weakness auto-escalate must not drop
cov_verification. Data-structure correctness (no current production consumer
of the typed field — downstream uses __dict__['verification_status']), so this
guards future consumers and type integrity.

LLM-safe: review()'s LLM calls are bypassed by injecting state directly is not
possible (review is monolithic), so we mock call_llm_validated + CoV helpers.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from reasoning.agents.critic import CriticAgent
from reasoning.schemas_enhanced import (
    CriticReviewOutputEnhanced,
    CriticReviewOutputEnhancedCoV,
    StructuredWeakness,
)


def _make_critic():
    handler = MagicMock()
    return CriticAgent(handler=handler, timeout=60)


def _config():
    cfg = MagicMock()
    cfg.reasoning_params = {
        "features": {"structured_critique": True, "cov_lite_enabled": True},
        "critique_thresholds": {"critical_weakness_count": 2},
    }
    return cfg


def _two_critical_weaknesses():
    return [
        StructuredWeakness(node_id="n1", weakness_type="insufficient_evidence",
                           severity="critical", explanation="x" * 25),
        StructuredWeakness(node_id="n2", weakness_type="logical_leap",
                           severity="critical", explanation="y" * 25),
    ]


@pytest.mark.asyncio
async def test_cov_preserved_through_critical_weakness_escalation():
    critic = _make_critic()

    # LLM returns a CoV-schema result WITH structured_weaknesses (2 critical)
    # and PASS status (so the escalation branch at 257 is reachable).
    llm_result = CriticReviewOutputEnhancedCoV(
        status="PASS",
        critique="c" * 60,
        suggestions=["s"],
        mode_compliance="符合",
        logical_gaps=[],
        source_issues=[],
        structured_weaknesses=_two_critical_weaknesses(),
        cov_verification=None,
    )
    critic.call_llm_validated = AsyncMock(return_value=(llm_result, 0, False))

    # CoV verification returns unverified issues (drives Point 1 rebuild),
    # but no contradictions (so status escalates to WARN, not REJECT — keeps
    # status != REJECT so Point 2 escalation fires).
    cov_dict = {
        "results": [{"status": "unverified", "claim": "k", "explanation": "e"}],
        "summary": "s",
        "verified_count": 0,
        "unverified_count": 3,
        "contradicted_count": 0,
    }
    critic.run_cov_verification = AsyncMock(return_value=cov_dict)
    critic.cov_prompt_builder.build_verification_summary_for_critic = MagicMock(return_value="cov-sum")
    critic._send_progress = AsyncMock()

    with patch("core.config.CONFIG", _config()):
        result = await critic.review(
            draft="draft text",
            query="Q",
            mode="discovery",
            analyst_output=None,
            formatted_context="[1] source",
        )

    # After escalation, status is REJECT (2 critical weaknesses) ...
    assert result.status == "REJECT"
    # ... AND cov_verification survived (type stayed CoV, field non-None).
    assert isinstance(result, CriticReviewOutputEnhancedCoV)
    assert result.cov_verification is not None
    assert result.cov_verification.unverified_count == 3


@pytest.mark.asyncio
async def test_cov_point1_preserves_narration_transition_on_live():
    """C2: when review() runs with enable_live_research=True, the LLM result is
    CriticReviewOutputLive (has narration_transition). Point 1's CoV rebuild must
    NOT drop narration_transition (the old hand-listed constructor did)."""
    from reasoning.schemas_live import CriticReviewOutputLive

    critic = _make_critic()
    llm_result = CriticReviewOutputLive(
        status="PASS",
        critique="c" * 60,
        suggestions=["s"],
        mode_compliance="符合",
        logical_gaps=[],
        source_issues=[],
        structured_weaknesses=None,
        cov_verification=None,
        narration_transition="讀豹發現這裡需要轉折",
    )
    critic.call_llm_validated = AsyncMock(return_value=(llm_result, 0, False))
    # unverified (not contradicted) → escalate to WARN, drives Point 1 rebuild
    cov_dict = {
        "results": [{"status": "unverified", "claim": "k", "explanation": "e"}],
        "summary": "s", "verified_count": 0,
        "unverified_count": 3, "contradicted_count": 0,
    }
    critic.run_cov_verification = AsyncMock(return_value=cov_dict)
    critic.cov_prompt_builder.build_verification_summary_for_critic = MagicMock(return_value="cov-sum")
    critic._send_progress = AsyncMock()

    with patch("core.config.CONFIG", _config()):
        result = await critic.review(
            draft="draft text", query="Q", mode="discovery",
            analyst_output=None, formatted_context="[1] source",
            enable_live_research=True,
        )

    # Point 1 ran (cov_verification populated) and kept Live type + narration.
    assert isinstance(result, CriticReviewOutputLive)
    assert result.cov_verification is not None
    assert result.narration_transition == "讀豹發現這裡需要轉折"


@pytest.mark.asyncio
async def test_critical_weakness_escalation_without_cov_still_rejects():
    """Regression: when CoV is OFF, escalation still upgrades to REJECT and
    keeps structured_weaknesses (model_copy preserves them)."""
    critic = _make_critic()
    llm_result = CriticReviewOutputEnhanced(
        status="PASS",
        critique="c" * 60,
        suggestions=["s"],
        mode_compliance="符合",
        logical_gaps=[],
        source_issues=[],
        structured_weaknesses=_two_critical_weaknesses(),
    )
    critic.call_llm_validated = AsyncMock(return_value=(llm_result, 0, False))
    critic._send_progress = AsyncMock()

    cfg = MagicMock()
    cfg.reasoning_params = {
        "features": {"structured_critique": True, "cov_lite_enabled": False},
        "critique_thresholds": {"critical_weakness_count": 2},
    }
    with patch("core.config.CONFIG", cfg):
        result = await critic.review(
            draft="draft text", query="Q", mode="discovery",
            analyst_output=None, formatted_context="",
        )

    assert result.status == "REJECT"
    assert isinstance(result, CriticReviewOutputEnhanced)
    assert len(result.structured_weaknesses) == 2
    assert "[自動升級至 REJECT" in result.critique
