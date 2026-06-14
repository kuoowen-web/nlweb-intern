"""Track F F1 per-section Critic publish gate."""
import os
import sys

import pytest

from reasoning.live_research import lr_copy

# N-9 紀律：fixture suite 位於同目錄；conftest 加 `code/python` 進 sys.path 但
# 不加 tests/ 子目錄。本 test 直接把 fixture file 目錄加進 sys.path 讓
# fixture module 可 import (避免改 conftest 影響全 repo)。
_FIXTURE_DIR = os.path.dirname(__file__)
if _FIXTURE_DIR not in sys.path:
    sys.path.insert(0, _FIXTURE_DIR)


def test_critic_rejected_status_enum():
    from reasoning.schemas_live import LiveWriterSectionOutput
    out = LiveWriterSectionOutput(
        section_title="x", section_content="y", sources_used=[],
        confidence_level="High", status="critic_rejected",
    )
    assert out.status == "critic_rejected"


def test_claim_level_issue_schema():
    from reasoning.schemas_live import ClaimLevelIssue
    issue = ClaimLevelIssue(
        claim_type="numeric",
        claim_text="2018 年 5 GW",
        severity="reject",
        explanation="evidence 未支撐",
    )
    assert issue.claim_type == "numeric"
    assert issue.severity == "reject"


def test_critic_section_review_schema():
    from reasoning.schemas_live import CriticSectionReview, ClaimLevelIssue
    rv = CriticSectionReview(
        section_index=2,
        verdict="REJECT",
        claim_issues=[ClaimLevelIssue(
            claim_type="numeric", claim_text="x",
            severity="reject", explanation="",
        )],
        overall_explanation="多筆數字無據",
    )
    assert rv.section_index == 2
    assert rv.verdict == "REJECT"
    assert len(rv.claim_issues) == 1


def test_critic_section_review_minimal():
    from reasoning.schemas_live import CriticSectionReview
    rv = CriticSectionReview(section_index=0, verdict="PASS")
    assert rv.claim_issues == []
    assert rv.cov_verification_summary is None


# ============================================================================
# Step 7: F1 prompt builder
# ============================================================================

def test_section_publish_gate_prompt_contains_section_content():
    from reasoning.prompts.critic import CriticPromptBuilder
    from reasoning.schemas_live import LiveWriterSectionOutput
    builder = CriticPromptBuilder()
    section = LiveWriterSectionOutput(
        section_title="x", section_content="弗萊堡 5 GW",
        sources_used=[1], confidence_level="Medium",
    )
    prompt = builder.build_section_publish_gate_prompt(
        section=section,
        chapter_evidence_text="[1] 弗萊堡 — 推動綠能",
    )
    assert "弗萊堡 5 GW" in prompt
    assert "弗萊堡 — 推動綠能" in prompt


def test_section_publish_gate_prompt_lists_six_claim_types():
    """prompt 必須明列 6 個 claim 類型，否則 LLM 無法分類。"""
    from reasoning.prompts.critic import CriticPromptBuilder
    from reasoning.schemas_live import LiveWriterSectionOutput
    builder = CriticPromptBuilder()
    section = LiveWriterSectionOutput(
        section_title="x", section_content="y", sources_used=[],
        confidence_level="Medium",
    )
    prompt = builder.build_section_publish_gate_prompt(
        section=section, chapter_evidence_text="z",
    )
    # 列 6 個 claim 類型供 LLM 分類
    for kw in ("數字", "時間", "因果", "比較", "預測", "評價"):
        assert kw in prompt, f"prompt 缺 claim 類型關鍵字: {kw}"


def test_section_publish_gate_prompt_includes_warned_critic_claims():
    """C-2: F1 prompt 必須含「BAB Critic 已 WARN 的 claim 清單」段，
    讓 F1 對 from_warned_critic_review=True 的 claim 做嚴格驗證。

    NF-2 R2 fix: warned_critic_claims 是 List[Dict] 不是 List[GroundedClaim] —
    helper 必須 dict access (`_c.get(...)`) 不可 attr access (`getattr(_c, ...)`)。
    """
    from reasoning.prompts.critic import CriticPromptBuilder
    from reasoning.schemas_live import LiveWriterSectionOutput, GroundedClaim
    builder = CriticPromptBuilder()
    section = LiveWriterSectionOutput(
        section_title="x", section_content="德國 2018 年 5 GW",
        sources_used=[1], confidence_level="Medium",
    )
    # 模擬從 state.evidence_usage flatten 取的 dict entries（GroundedClaim.model_dump()）
    warned = [
        GroundedClaim(
            claim="德國裝置容量 5 GW",
            reasoning_type="induction",
            confidence="low",
            source_topic="t1",
            source_iteration=1,
            from_warned_critic_review=True,
            critic_status="WARN",
        ).model_dump(),
    ]
    prompt = builder.build_section_publish_gate_prompt(
        section=section,
        chapter_evidence_text="[1] 德國能源",
        warned_critic_claims=warned,
    )
    assert "BAB Critic" in prompt and "WARN" in prompt
    assert "德國裝置容量 5 GW" in prompt


def test_section_publish_gate_prompt_includes_time_constraint():
    """I-7: F1 prompt 在 time_constraint 提供時，明列範圍 + 對範圍外時間敏感。"""
    from reasoning.prompts.critic import CriticPromptBuilder
    from reasoning.schemas_live import LiveWriterSectionOutput
    from types import SimpleNamespace
    builder = CriticPromptBuilder()
    section = LiveWriterSectionOutput(
        section_title="x", section_content="2018 的事",
        sources_used=[], confidence_level="Medium",
    )
    tc = SimpleNamespace(start_date="2024-01", end_date="2026-05", user_selected=True)
    prompt = builder.build_section_publish_gate_prompt(
        section=section,
        chapter_evidence_text="evidence",
        time_constraint=tc,
    )
    assert "2024-01" in prompt and "2026-05" in prompt
    # prompt 必須明示對範圍外時間敏感
    assert "範圍外" in prompt or "時間範圍" in prompt


# ============================================================================
# Step 8: agent method
# ============================================================================

@pytest.mark.asyncio
async def test_review_section_publish_gate_pass():
    """LLM 回 verdict=PASS → review.verdict == 'PASS'。"""
    from reasoning.agents.critic import CriticAgent
    from reasoning.schemas_live import LiveWriterSectionOutput, CriticSectionReview
    from unittest.mock import MagicMock

    handler = MagicMock(query_params={})
    agent = CriticAgent(handler)

    async def fake_call_llm_validated(prompt, response_schema, level):
        return (
            CriticSectionReview(section_index=0, verdict="PASS", overall_explanation="all clean"),
            0,  # retry_count
            False,  # fallback_used
        )
    agent.call_llm_validated = fake_call_llm_validated

    section = LiveWriterSectionOutput(
        section_title="x", section_content="德國能源轉型",
        sources_used=[1], confidence_level="High",
    )
    review = await agent.review_section_publish_gate(
        section=section,
        section_index=0,
        chapter_evidence_text="[1] 德國能源轉型",
    )
    assert review.verdict == "PASS"


@pytest.mark.asyncio
async def test_review_section_publish_gate_llm_fail_returns_warn_fallback():
    """C-3: LLM call 失敗 → fallback verdict=WARN（不 silent fail，user 看得到 marker）。

    違反 CLAUDE.md「不可 Silent Fail」就是 fallback PASS：user 不知道 critic 沒跑。
    Fallback WARN → 走 WARN branch 加 marker → user 看見「此章 critic 未跑」。
    """
    from reasoning.agents.critic import CriticAgent
    from reasoning.schemas_live import LiveWriterSectionOutput
    from unittest.mock import MagicMock

    handler = MagicMock(query_params={})
    agent = CriticAgent(handler)

    async def fake_fail(prompt, response_schema, level):
        raise RuntimeError("LLM timeout")
    agent.call_llm_validated = fake_fail

    section = LiveWriterSectionOutput(
        section_title="x", section_content="y", sources_used=[],
        confidence_level="Medium",
    )
    review = await agent.review_section_publish_gate(
        section=section, section_index=0, chapter_evidence_text="z",
    )
    # fallback WARN（非 PASS），讓 user 透過 methodology_note marker 看到「critic 未跑」
    assert review.verdict == "WARN"
    assert "F1" in review.overall_explanation
    assert "fail" in review.overall_explanation.lower() or "未跑" in review.overall_explanation


@pytest.mark.asyncio
async def test_review_section_publish_gate_overrides_section_index():
    """LLM 偷懶回 section_index=0；caller 傳入 5 → review.section_index = 5。"""
    from reasoning.agents.critic import CriticAgent
    from reasoning.schemas_live import LiveWriterSectionOutput, CriticSectionReview
    from unittest.mock import MagicMock

    handler = MagicMock(query_params={})
    agent = CriticAgent(handler)

    async def fake_call(prompt, response_schema, level):
        return (
            CriticSectionReview(section_index=0, verdict="PASS"),
            0, False,
        )
    agent.call_llm_validated = fake_call

    section = LiveWriterSectionOutput(
        section_title="x", section_content="y", sources_used=[],
        confidence_level="Medium",
    )
    review = await agent.review_section_publish_gate(
        section=section, section_index=5, chapter_evidence_text="z",
    )
    assert review.section_index == 5


# ============================================================================
# Step 9: F1 對 fixture suite 跑 mock test + short-circuit test
# ============================================================================

@pytest.mark.asyncio
async def test_review_section_publish_gate_against_fixture_suite():
    """Track F §2 fail-loud fixture suite — 6 個 claim-level fixture 在 mock LLM
    回對應 verdict 下能正確 propagate 進 CriticSectionReview。

    (此 test 走 mock LLM，固定每 fixture 期望的 verdict。真實 LLM 對 prompt 的
    sensitivity 放在 nightly soak 跑，不在 unit test 阻塞。)
    """
    from reasoning.agents.critic import CriticAgent
    from reasoning.schemas_live import CriticSectionReview, ClaimLevelIssue
    from test_claim_fabrication_fixtures import ALL_FIXTURES
    from unittest.mock import MagicMock

    handler = MagicMock(query_params={})

    for fid, fx in ALL_FIXTURES:
        # S-3: F-CL-7 是 guard_failed short-circuit fixture，本 test 走
        # agent method（不過 helper），short-circuit 行為在
        # test_run_publish_gate_short_circuit_guard_failed 驗證。
        if fid == "F-CL-7":
            continue

        agent = CriticAgent(handler)
        expected_action = fx["expected_critic_action"]  # reject / warn / pass
        verdict_map = {"reject": "REJECT", "warn": "WARN", "pass": "PASS"}
        expected_verdict = verdict_map[expected_action]

        async def fake_call(prompt, response_schema, level, _verdict=expected_verdict, _fx=fx):
            issues = []
            if _verdict in ("REJECT", "WARN"):
                issues.append(ClaimLevelIssue(
                    claim_type=_fx["expected_fabrication_type"],
                    claim_text=_fx["section"].section_content[:50],
                    severity="reject" if _verdict == "REJECT" else "warn",
                    explanation=_fx["explanation"],
                ))
            return (
                CriticSectionReview(
                    section_index=0,
                    verdict=_verdict,
                    claim_issues=issues,
                    overall_explanation=_fx["explanation"],
                ),
                0, False,
            )

        agent.call_llm_validated = fake_call
        review = await agent.review_section_publish_gate(
            section=fx["section"],
            section_index=0,
            chapter_evidence_text=fx["evidence_text"],
        )
        assert review.verdict == expected_verdict, (
            f"{fid}: expected {expected_verdict}, got {review.verdict}"
        )


@pytest.mark.asyncio
async def test_run_publish_gate_short_circuit_guard_failed():
    """S-3 / F-AMB-7: F1 對 status='guard_failed' section short-circuit pass-through。

    F-CL-7 fixture 驗 helper `_run_publish_gate` 在 guard_failed section 上：
    - 不 call LLM（無 review_section_publish_gate call）
    - 不 mutate section（content / status 不變）
    - was_corrected=False
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from test_claim_fabrication_fixtures import FIXTURE_WHOLE_CHAPTER_FABRICATION
    from unittest.mock import MagicMock, AsyncMock

    handler = MagicMock(query_params={})
    orch = LiveResearchOrchestrator(handler=handler, dry_run=True)

    # 強制 F1 flag enable（dry_run config 可能 disable）
    orch.features = dict(orch.features)
    orch.features["live_research_critic_publish_gate"] = True

    # 注入 mock critic_agent — 應**完全不被 call**（short-circuit）
    mock_critic = MagicMock()
    mock_critic.review_section_publish_gate = AsyncMock(
        side_effect=AssertionError("不應被 call")
    )
    mock_critic.run_cov_for_lr_section = AsyncMock(
        side_effect=AssertionError("不應被 call")
    )
    orch._critic_agent = mock_critic

    fx = FIXTURE_WHOLE_CHAPTER_FABRICATION
    original_content = fx["section"].section_content
    original_status = fx["section"].status

    out_section, was_corrected = await orch._run_publish_gate(
        section_output=fx["section"],
        current_chapter_index=0,
        chapter_evidence_text=fx["evidence_text"],
        state=None,
    )

    assert was_corrected is False, "guard_failed short-circuit 不可標 was_corrected=True"
    assert out_section.section_content == original_content, "short-circuit 不可改 content"
    assert out_section.status == original_status, "short-circuit 不可改 status"


@pytest.mark.asyncio
async def test_run_publish_gate_disabled_flag_short_circuit():
    """F1 flag disabled → 不 call LLM、不 mutate、was_corrected=False。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import LiveWriterSectionOutput
    from unittest.mock import MagicMock, AsyncMock

    handler = MagicMock(query_params={})
    orch = LiveResearchOrchestrator(handler=handler, dry_run=True)

    # 顯式 disable F1
    orch.features = dict(orch.features)
    orch.features["live_research_critic_publish_gate"] = False

    mock_critic = MagicMock()
    mock_critic.review_section_publish_gate = AsyncMock(
        side_effect=AssertionError("不應被 call")
    )
    orch._critic_agent = mock_critic

    section = LiveWriterSectionOutput(
        section_title="x", section_content="原 content",
        sources_used=[1], confidence_level="High",
    )
    out_section, was_corrected = await orch._run_publish_gate(
        section_output=section,
        current_chapter_index=0,
        chapter_evidence_text="evidence",
        state=None,
    )
    assert was_corrected is False
    assert out_section.section_content == "原 content"
    assert out_section.status == "drafted"


@pytest.mark.asyncio
async def test_run_publish_gate_reject_mutates_to_critic_rejected():
    """F1 verdict=REJECT → content 替換 + status='critic_rejected' + was_corrected=True。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import (
        LiveWriterSectionOutput, CriticSectionReview, ClaimLevelIssue,
    )
    from unittest.mock import MagicMock, AsyncMock

    handler = MagicMock(query_params={})
    orch = LiveResearchOrchestrator(handler=handler, dry_run=True)
    orch.features = dict(orch.features)
    orch.features["live_research_critic_publish_gate"] = True
    orch.features["cov_lite_enabled"] = False  # 不跑 F3 隔離 F1 行為

    mock_critic = MagicMock()
    mock_critic.review_section_publish_gate = AsyncMock(
        return_value=CriticSectionReview(
            section_index=2,
            verdict="REJECT",
            claim_issues=[ClaimLevelIssue(
                claim_type="numeric", claim_text="2018 5GW",
                severity="reject", explanation="無據",
            )],
            overall_explanation="多筆無據",
        )
    )
    orch._critic_agent = mock_critic

    section = LiveWriterSectionOutput(
        section_title="x", section_content="弗萊堡 2018 年 5 GW",
        sources_used=[1], confidence_level="High",
    )
    state = LiveResearchStageState()
    out_section, was_corrected = await orch._run_publish_gate(
        section_output=section,
        current_chapter_index=2,
        chapter_evidence_text="[1] 弗萊堡",
        state=state,
    )
    assert was_corrected is True
    assert out_section.status == "critic_rejected"
    assert lr_copy.CRITIC_REJECTED_PREFIX in out_section.section_content
    assert out_section.sources_used == []
    # state 寫進 critic_section_reviews
    assert 2 in state.critic_section_reviews
    assert state.critic_section_reviews[2]["verdict"] == "REJECT"


@pytest.mark.asyncio
async def test_run_publish_gate_warn_adds_marker_dedup():
    """F1 verdict=WARN → methodology_note marker；既有 marker → replace 不 append (I-1)。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import (
        LiveWriterSectionOutput, CriticSectionReview, ClaimLevelIssue,
    )
    from unittest.mock import MagicMock, AsyncMock

    handler = MagicMock(query_params={})
    orch = LiveResearchOrchestrator(handler=handler, dry_run=True)
    orch.features = dict(orch.features)
    orch.features["live_research_critic_publish_gate"] = True
    orch.features["cov_lite_enabled"] = False

    mock_critic = MagicMock()
    mock_critic.review_section_publish_gate = AsyncMock(
        return_value=CriticSectionReview(
            section_index=0,
            verdict="WARN",
            claim_issues=[ClaimLevelIssue(
                claim_type="evaluative", claim_text="成效顯著",
                severity="warn", explanation="主觀評價",
            )],
            overall_explanation="評價詞待驗證",
        )
    )
    orch._critic_agent = mock_critic

    # First WARN — 無 existing marker → append
    section = LiveWriterSectionOutput(
        section_title="x", section_content="德國成效顯著",
        sources_used=[1], confidence_level="Medium",
    )
    out1, _ = await orch._run_publish_gate(
        section_output=section, current_chapter_index=0,
        chapter_evidence_text="evidence", state=None,
    )
    assert lr_copy.WARN_MARKER_PREFIX in (out1.methodology_note or "")
    assert out1.status == "drafted"

    # Second WARN run — existing marker → replace（不 double append）
    out2, _ = await orch._run_publish_gate(
        section_output=out1, current_chapter_index=0,
        chapter_evidence_text="evidence", state=None,
    )
    # Only one marker (count by prefix occurrences)
    assert (out2.methodology_note or "").count(lr_copy.WARN_MARKER_PREFIX) == 1


@pytest.mark.asyncio
async def test_run_publish_gate_pass_no_mutation():
    """F1 verdict=PASS → section 不變、was_corrected=False、state.critic_section_reviews 寫入。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.live_research.stage_state import LiveResearchStageState
    from reasoning.schemas_live import LiveWriterSectionOutput, CriticSectionReview
    from unittest.mock import MagicMock, AsyncMock

    handler = MagicMock(query_params={})
    orch = LiveResearchOrchestrator(handler=handler, dry_run=True)
    orch.features = dict(orch.features)
    orch.features["live_research_critic_publish_gate"] = True
    orch.features["cov_lite_enabled"] = False

    mock_critic = MagicMock()
    mock_critic.review_section_publish_gate = AsyncMock(
        return_value=CriticSectionReview(
            section_index=1, verdict="PASS", overall_explanation="clean",
        )
    )
    orch._critic_agent = mock_critic

    section = LiveWriterSectionOutput(
        section_title="x", section_content="clean content",
        sources_used=[1], confidence_level="High",
    )
    state = LiveResearchStageState()
    out, was_corrected = await orch._run_publish_gate(
        section_output=section, current_chapter_index=1,
        chapter_evidence_text="evidence", state=state,
    )
    assert was_corrected is False
    assert out.section_content == "clean content"
    assert out.status == "drafted"
    assert state.critic_section_reviews[1]["verdict"] == "PASS"


# ============================================================================
# Task 2: 空 evidence 路徑行為定義 + deterministic 短路
# ============================================================================


@pytest.mark.asyncio
async def test_run_publish_gate_empty_evidence_short_circuits_no_llm():
    """Task 2: chapter_evidence_text 空 → deterministic 短路，不 call F1/F3，降級標註。

    驗 plumbing：空 evidence 路徑不進 LLM（critic 被 call 即 fail）；不驗 LLM 判斷力。
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import LiveWriterSectionOutput
    from unittest.mock import MagicMock, AsyncMock

    handler = MagicMock(query_params={})
    orch = LiveResearchOrchestrator(handler=handler, dry_run=True)
    orch.features = dict(orch.features)
    orch.features["live_research_critic_publish_gate"] = True
    orch.features["cov_lite_enabled"] = True

    mock_critic = MagicMock()
    mock_critic.review_section_publish_gate = AsyncMock(
        side_effect=AssertionError("空 evidence 不應 call F1")
    )
    mock_critic.run_cov_for_lr_section = AsyncMock(
        side_effect=AssertionError("空 evidence 不應 call F3")
    )
    orch._critic_agent = mock_critic

    section = LiveWriterSectionOutput(
        section_title="x", section_content="某些正文",
        sources_used=[], confidence_level="High",
    )
    # 空字串（safe-init 在 entity-guard try 早期 raise 的情境）
    out, was_corrected = await orch._run_publish_gate(
        section_output=section,
        current_chapter_index=0,
        chapter_evidence_text="",
        state=None,
    )
    assert out.section_content == "某些正文", "短路不改正文"
    assert out.confidence_level == "Low"
    assert lr_copy.PUBLISH_GATE_NO_EVIDENCE_NOTE in (out.methodology_note or "")
    assert was_corrected is True


@pytest.mark.asyncio
async def test_run_publish_gate_whitespace_only_evidence_short_circuits():
    """Task 2: 純空白 evidence（render 出 '\\n  ' 之類）同樣短路。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import LiveWriterSectionOutput
    from unittest.mock import MagicMock, AsyncMock

    handler = MagicMock(query_params={})
    orch = LiveResearchOrchestrator(handler=handler, dry_run=True)
    orch.features = dict(orch.features)
    orch.features["live_research_critic_publish_gate"] = True
    mock_critic = MagicMock()
    mock_critic.review_section_publish_gate = AsyncMock(
        side_effect=AssertionError("純空白 evidence 不應 call F1")
    )
    orch._critic_agent = mock_critic

    section = LiveWriterSectionOutput(
        section_title="x", section_content="正文",
        sources_used=[], confidence_level="High",
    )
    out, was_corrected = await orch._run_publish_gate(
        section_output=section,
        current_chapter_index=0,
        chapter_evidence_text="   \n  \t ",
        state=None,
    )
    assert lr_copy.PUBLISH_GATE_NO_EVIDENCE_NOTE in (out.methodology_note or "")
    assert was_corrected is True


@pytest.mark.asyncio
async def test_run_publish_gate_outer_failure_degrades_and_narrates():
    """Task 1: gate body 非預期故障 → 不 silent 放行；保留正文 + 降 Low + 標註 + 旁白。

    驗 plumbing（故障→降級路徑），不驗 LLM 判斷力本身（critic 全 mock）。
    """
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import LiveWriterSectionOutput
    from unittest.mock import MagicMock, AsyncMock

    handler = MagicMock(query_params={})
    orch = LiveResearchOrchestrator(handler=handler, dry_run=True)
    orch.features = dict(orch.features)
    orch.features["live_research_critic_publish_gate"] = True
    orch.features["cov_lite_enabled"] = False

    # critic call 丟「非預期」例外（非 LLM-fail 內層已處理者）→ 觸發 outer except
    mock_critic = MagicMock()
    mock_critic.review_section_publish_gate = AsyncMock(
        side_effect=RuntimeError("simulated unexpected gate-body failure")
    )
    orch._critic_agent = mock_critic

    captured = []
    orch._emit_narration = AsyncMock(side_effect=lambda t: captured.append(t))

    section = LiveWriterSectionOutput(
        section_title="x", section_content="原始正文不可被改動",
        sources_used=[1], confidence_level="High",
    )
    out, was_corrected = await orch._run_publish_gate(
        section_output=section,
        current_chapter_index=0,
        chapter_evidence_text="[1] evidence",
        state=None,
    )

    # 1) 不可 silent 放行原文（必須降級標註）
    assert out.section_content == "原始正文不可被改動", "正文不知哪句有問題，不可亂刪"
    assert out.confidence_level == "Low", "故障必須降信心，錯誤要浮現"
    assert lr_copy.PUBLISH_GATE_UNAVAILABLE_NOTE in (out.methodology_note or "")
    assert was_corrected is True
    # 2) 即時旁白發出一次
    assert lr_copy.PUBLISH_GATE_UNAVAILABLE_NARRATION in captured


@pytest.mark.asyncio
async def test_run_publish_gate_outer_failure_narration_dedup():
    """Task 1: 多章連續故障 → 旁白只播一次（per-run dedup），標註仍逐章。"""
    from reasoning.live_research.orchestrator import LiveResearchOrchestrator
    from reasoning.schemas_live import LiveWriterSectionOutput
    from unittest.mock import MagicMock, AsyncMock

    handler = MagicMock(query_params={})
    orch = LiveResearchOrchestrator(handler=handler, dry_run=True)
    orch.features = dict(orch.features)
    orch.features["live_research_critic_publish_gate"] = True
    orch.features["cov_lite_enabled"] = False
    mock_critic = MagicMock()
    mock_critic.review_section_publish_gate = AsyncMock(
        side_effect=RuntimeError("boom")
    )
    orch._critic_agent = mock_critic
    captured = []
    orch._emit_narration = AsyncMock(side_effect=lambda t: captured.append(t))

    for idx in range(3):
        sec = LiveWriterSectionOutput(
            section_title=f"c{idx}", section_content=f"正文{idx}",
            sources_used=[], confidence_level="High",
        )
        out, _ = await orch._run_publish_gate(
            section_output=sec, current_chapter_index=idx,
            chapter_evidence_text="[1] e", state=None,
        )
        # 每章都要被標註（dedup 只作用於旁白，不作用於標註）
        assert lr_copy.PUBLISH_GATE_UNAVAILABLE_NOTE in (out.methodology_note or "")

    assert captured.count(lr_copy.PUBLISH_GATE_UNAVAILABLE_NARRATION) == 1, \
        "per-run dedup：旁白只播一次"
