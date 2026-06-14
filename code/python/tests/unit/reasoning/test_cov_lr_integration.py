"""Track F F3 CoV-lite LR integration.

驗證:
1. CriticAgent.run_cov_verification 既有 signature (draft / formatted_context) 對 LR 適用
2. CriticAgent.run_cov_for_lr_section LR wrapper 對 section_content / chapter_evidence_text 串接 OK
3. _run_publish_gate helper 內 F3 segment 正確呼叫 + auto-escalate verdict (contradicted → REJECT;
   unverified >= 3 → WARN)
4. F3 LLM fail 走 degraded result（verification_status='unverified'）+ 不阻塞 pipeline
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from reasoning.live_research import lr_copy


@pytest.mark.asyncio
async def test_run_cov_verification_called_with_formatted_context():
    """F3: run_cov_verification 收到 section_content 作 draft + chapter_evidence_text 作 context."""
    from reasoning.agents.critic import CriticAgent

    handler = MagicMock(query_params={})
    agent = CriticAgent(handler)

    captured = {}

    async def fake_extract(draft):
        captured["draft"] = draft
        return [{"claim": "2018 年 5 GW", "claim_type": "number"}]

    async def fake_verify(claims, formatted_context):
        captured["context"] = formatted_context
        return {
            "results": [{"claim": "x", "status": "unverified",
                         "explanation": "x", "confidence": "low"}],
            "summary": "1 unverified",
            "verified_count": 0, "unverified_count": 1, "contradicted_count": 0,
        }

    agent._extract_verifiable_claims = fake_extract
    agent._verify_claims_against_sources = fake_verify

    result = await agent.run_cov_verification(
        draft="弗萊堡 2018 年 5 GW",
        formatted_context="[1] 弗萊堡推動綠能",
    )

    assert "弗萊堡 2018 年 5 GW" in captured["draft"]
    assert "[1] 弗萊堡推動綠能" in captured["context"]
    assert result["unverified_count"] == 1


@pytest.mark.asyncio
async def test_run_cov_verification_handles_no_claims():
    """No verifiable claims → 回 0 count summary（不 crash）。"""
    from reasoning.agents.critic import CriticAgent

    handler = MagicMock(query_params={})
    agent = CriticAgent(handler)

    async def fake_extract(draft):
        return []
    agent._extract_verifiable_claims = fake_extract

    result = await agent.run_cov_verification(
        draft="抽象論述無具體 claim",
        formatted_context="evidence",
    )
    assert result["verified_count"] == 0
    assert result["unverified_count"] == 0
    assert "No verifiable claims" in result["summary"] or "no claims" in result["summary"].lower()


@pytest.mark.asyncio
async def test_run_cov_for_lr_section_wrapper_passes_through():
    """F3 LR wrapper：section_content → draft, chapter_evidence_text → formatted_context."""
    from reasoning.agents.critic import CriticAgent

    handler = MagicMock(query_params={})
    agent = CriticAgent(handler)

    captured = {}

    async def fake_run_cov(draft, formatted_context):
        captured["draft"] = draft
        captured["ctx"] = formatted_context
        return {"verified_count": 1, "unverified_count": 0, "contradicted_count": 0,
                "results": [], "summary": "ok"}

    agent.run_cov_verification = fake_run_cov

    result = await agent.run_cov_for_lr_section(
        section_content="德國綠能 40%",
        chapter_evidence_text="[1] 德國能源",
    )
    assert captured["draft"] == "德國綠能 40%"
    assert captured["ctx"] == "[1] 德國能源"
    assert result["verified_count"] == 1


@pytest.mark.asyncio
async def test_run_cov_for_lr_section_failure_returns_none():
    """F3 LR wrapper 內 run_cov_verification raise → 回 None（不 raise）。"""
    from reasoning.agents.critic import CriticAgent

    handler = MagicMock(query_params={})
    agent = CriticAgent(handler)

    async def fake_fail(draft, formatted_context):
        raise RuntimeError("LLM down")
    agent.run_cov_verification = fake_fail

    result = await agent.run_cov_for_lr_section(
        section_content="x", chapter_evidence_text="y",
    )
    assert result is None


# ============================================================================
# F3 in _run_publish_gate helper integration
# ============================================================================

@pytest.mark.asyncio
async def test_publish_gate_skips_cov_when_f1_already_reject():
    """I-5: F1 verdict=REJECT → F3 CoV skip（不對 mutated blocked 文字跑）。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import (
        LiveWriterSectionOutput, CriticSectionReview, ClaimLevelIssue,
    )

    handler = MagicMock(query_params={})
    orch = LiveResearchOrchestrator(handler=handler, dry_run=True)
    orch.features = dict(orch.features)
    orch.features["live_research_critic_publish_gate"] = True
    orch.features["cov_lite_enabled"] = True

    mock_critic = MagicMock()
    mock_critic.review_section_publish_gate = AsyncMock(
        return_value=CriticSectionReview(
            section_index=0, verdict="REJECT",
            claim_issues=[ClaimLevelIssue(
                claim_type="numeric", claim_text="x", severity="reject", explanation="",
            )],
        )
    )
    # F3 不應被 call
    mock_critic.run_cov_for_lr_section = AsyncMock(
        side_effect=AssertionError("F1 REJECT 時 F3 不應被 call")
    )
    orch._critic_agent = mock_critic

    section = LiveWriterSectionOutput(
        section_title="x", section_content="編造",
        sources_used=[1], confidence_level="Medium",
    )
    out, was_corrected = await orch._run_publish_gate(
        section_output=section, current_chapter_index=0,
        chapter_evidence_text="evidence", state=None,
    )
    assert was_corrected is True
    assert out.status == "critic_rejected"


@pytest.mark.asyncio
async def test_publish_gate_auto_escalate_to_reject_on_contradicted():
    """F3 auto-escalate: F1 PASS but CoV contradicted > 0 → 升 REJECT。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import LiveWriterSectionOutput, CriticSectionReview

    handler = MagicMock(query_params={})
    orch = LiveResearchOrchestrator(handler=handler, dry_run=True)
    orch.features = dict(orch.features)
    orch.features["live_research_critic_publish_gate"] = True
    orch.features["cov_lite_enabled"] = True

    mock_critic = MagicMock()
    # F1 回 PASS（無 claim issues）
    mock_critic.review_section_publish_gate = AsyncMock(
        return_value=CriticSectionReview(
            section_index=0, verdict="PASS", overall_explanation="F1 clean",
        )
    )
    # F3 回 1 contradicted
    mock_critic.run_cov_for_lr_section = AsyncMock(
        return_value={
            "verified_count": 0, "unverified_count": 0, "contradicted_count": 1,
            "results": [], "summary": "1 contradicted",
        }
    )
    orch._critic_agent = mock_critic

    section = LiveWriterSectionOutput(
        section_title="x", section_content="德國 100% 綠能",
        sources_used=[1], confidence_level="High",
    )
    state = LiveResearchStageState()
    out, was_corrected = await orch._run_publish_gate(
        section_output=section, current_chapter_index=0,
        chapter_evidence_text="evidence", state=state,
    )
    # F3 escalate → REJECT → content 替換 + status='critic_rejected'
    assert out.status == "critic_rejected"
    assert was_corrected is True
    # state 寫進的 review verdict 升為 REJECT
    assert state.critic_section_reviews[0]["verdict"] == "REJECT"
    assert "auto-escalate REJECT" in state.critic_section_reviews[0]["overall_explanation"]


@pytest.mark.asyncio
async def test_publish_gate_escalate_to_warn_on_three_unverified():
    """F3 auto-escalate: F1 PASS but CoV unverified >= 3 → 升 WARN（不 block）。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import LiveWriterSectionOutput, CriticSectionReview

    handler = MagicMock(query_params={})
    orch = LiveResearchOrchestrator(handler=handler, dry_run=True)
    orch.features = dict(orch.features)
    orch.features["live_research_critic_publish_gate"] = True
    orch.features["cov_lite_enabled"] = True

    mock_critic = MagicMock()
    mock_critic.review_section_publish_gate = AsyncMock(
        return_value=CriticSectionReview(
            section_index=0, verdict="PASS",
        )
    )
    mock_critic.run_cov_for_lr_section = AsyncMock(
        return_value={
            "verified_count": 0, "unverified_count": 3, "contradicted_count": 0,
            "results": [], "summary": "3 unverified",
        }
    )
    orch._critic_agent = mock_critic

    section = LiveWriterSectionOutput(
        section_title="x", section_content="原 content",
        sources_used=[1], confidence_level="High",
    )
    state = LiveResearchStageState()
    out, was_corrected = await orch._run_publish_gate(
        section_output=section, current_chapter_index=0,
        chapter_evidence_text="evidence", state=state,
    )
    # WARN → marker 不 block, was_corrected=False
    assert out.status == "drafted"  # WARN 不改 status
    assert was_corrected is False
    assert out.section_content == "原 content"
    # methodology_note 含 marker
    assert lr_copy.WARN_MARKER_PREFIX in (out.methodology_note or "")
    # state 寫進的 review verdict 升為 WARN
    assert state.critic_section_reviews[0]["verdict"] == "WARN"


@pytest.mark.asyncio
async def test_publish_gate_f3_failure_degraded_unverified():
    """I-2: F3 raise → cov_summary 走 degraded result（verification_status='unverified'）。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import LiveWriterSectionOutput, CriticSectionReview

    handler = MagicMock(query_params={})
    orch = LiveResearchOrchestrator(handler=handler, dry_run=True)
    orch.features = dict(orch.features)
    orch.features["live_research_critic_publish_gate"] = True
    orch.features["cov_lite_enabled"] = True

    mock_critic = MagicMock()
    mock_critic.review_section_publish_gate = AsyncMock(
        return_value=CriticSectionReview(
            section_index=0, verdict="PASS",
        )
    )
    mock_critic.run_cov_for_lr_section = AsyncMock(
        side_effect=RuntimeError("F3 LLM down")
    )
    orch._critic_agent = mock_critic

    section = LiveWriterSectionOutput(
        section_title="x", section_content="content",
        sources_used=[1], confidence_level="High",
    )
    state = LiveResearchStageState()
    out, was_corrected = await orch._run_publish_gate(
        section_output=section, current_chapter_index=0,
        chapter_evidence_text="evidence", state=state,
    )
    # F3 fail 不阻塞 pipeline；F1 verdict 維持 PASS（degraded result 不觸發 escalate）
    assert out.status == "drafted"
    assert was_corrected is False
    cov_summary = state.critic_section_reviews[0]["cov_verification_summary"]
    assert cov_summary is not None
    assert cov_summary.get("verification_status") == "unverified"
    assert "F3 CoV-lite failed" in cov_summary.get("verification_message", "")


@pytest.mark.asyncio
async def test_publish_gate_f3_disabled_skips_cov():
    """S-2 LR-only 子 flag: live_research_cov_lite_enabled=False → F3 skip 不 call。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import LiveWriterSectionOutput, CriticSectionReview

    handler = MagicMock(query_params={})
    orch = LiveResearchOrchestrator(handler=handler, dry_run=True)
    orch.features = dict(orch.features)
    orch.features["live_research_critic_publish_gate"] = True
    orch.features["cov_lite_enabled"] = True  # DR-level on
    orch.features["live_research_cov_lite_enabled"] = False  # LR-only off (S-2 override)

    mock_critic = MagicMock()
    mock_critic.review_section_publish_gate = AsyncMock(
        return_value=CriticSectionReview(section_index=0, verdict="PASS")
    )
    mock_critic.run_cov_for_lr_section = AsyncMock(
        side_effect=AssertionError("LR-only F3 disabled, 不應被 call")
    )
    orch._critic_agent = mock_critic

    section = LiveWriterSectionOutput(
        section_title="x", section_content="content",
        sources_used=[1], confidence_level="High",
    )
    out, was_corrected = await orch._run_publish_gate(
        section_output=section, current_chapter_index=0,
        chapter_evidence_text="evidence", state=None,
    )
    assert out.status == "drafted"
    assert was_corrected is False
