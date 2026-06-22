"""
Critic Agent - Quality review and compliance checking for the Actor-Critic system.

Includes Phase 2 CoV (Chain of Verification) for fact-checking verifiable claims.
"""

from typing import Dict, Any, List, Optional, TYPE_CHECKING
from reasoning.agents.base import BaseReasoningAgent
from reasoning.schemas import CriticReviewOutput
from reasoning.prompts.critic import CriticPromptBuilder
from reasoning.prompts.cov import CoVPromptBuilder

if TYPE_CHECKING:
    # Track F (sprint 2026-05-28) — forward-ref 避免 runtime import
    from reasoning.schemas_live import (
        LiveWriterSectionOutput,
        CriticSectionReview,
        TimeRange,
    )


class CriticAgent(BaseReasoningAgent):
    """
    Critic Agent responsible for reviewing drafts and ensuring quality.

    The Critic evaluates drafts for logical consistency, source compliance,
    and unified discovery-based requirements (mode-based filtering removed 2026-04).
    """

    def __init__(self, handler, timeout: int = 120):  # 30 -> 60 -> 120: 對齊 base.py:168，消除 subclass < base 矛盾（真實值靠 config critic_timeout=120）
        """
        Initialize Critic Agent.

        Args:
            handler: Request handler with LLM configuration
            timeout: Timeout in seconds for LLM calls
        """
        super().__init__(
            handler=handler,
            agent_name="critic",
            timeout=timeout,
            max_retries=3
        )
        self.prompt_builder = CriticPromptBuilder()
        self.cov_prompt_builder = CoVPromptBuilder()

    async def review_consistency(
        self,
        current_map_summary: str,
        initial_map_summary: str,
        recent_events: List[str]
    ):
        """
        Check for research direction drift (Live Research mode only).

        Parallel to run_cov_verification() — a standalone method that can be
        called every time Analyst produces output (always-on consistency layer).

        Args:
            current_map_summary: Current ContextMap summary string
            initial_map_summary: Initial ContextMap summary string (version 0)
            recent_events: Recent research events list for context

        Returns:
            ConsistencyReview instance
        """
        from reasoning.prompts.consistency import ConsistencyPromptBuilder
        from reasoning.schemas_live import ConsistencyReview

        builder = ConsistencyPromptBuilder()
        prompt = builder.build_consistency_check_prompt(
            current_map_summary=current_map_summary,
            initial_map_summary=initial_map_summary,
            recent_events=recent_events
        )

        result, retry_count, fallback_used = await self.call_llm_validated(
            prompt=prompt,
            response_schema=ConsistencyReview,
            level="high"
        )

        self.logger.info(
            f"Consistency Monitor: drift_level={result.drift_level}, "
            f"recommended_action={result.recommended_action} "
            f"(retries={retry_count}, fallback={fallback_used})"
        )
        return result

    async def review(
        self,
        draft: str,
        query: str,
        mode: str,
        analyst_output=None,  # Optional: Full analyst output with argument_graph
        formatted_context: str = "",  # Phase 2 CoV: Source context for claim verification
        enable_live_research: bool = False  # Task 5: Live Research mode flag
    ) -> CriticReviewOutput:
        """
        Enhanced review with optional structured weaknesses (Phase 2) and CoV (Phase 2 CoV).

        Args:
            draft: Draft content to review
            query: Original user query
            mode: Research mode (kept for signature compatibility, value ignored since 2026-04)
            analyst_output: Optional AnalystResearchOutput with argument_graph
            formatted_context: Formatted source context for CoV verification
            enable_live_research: Enable Live Research consistency section and CriticReviewOutputLive schema

        Returns:
            CriticReviewOutput (or Enhanced version if feature enabled)
        """
        # Import CONFIG here to avoid circular dependency
        from core.config import CONFIG

        enable_structured = CONFIG.reasoning_params.get("features", {}).get("structured_critique", False)
        enable_cov = CONFIG.reasoning_params.get("features", {}).get("cov_lite_enabled", False)

        # Extract optional fields from analyst_output using getattr with default
        argument_graph = getattr(analyst_output, 'argument_graph', None) if analyst_output else None
        knowledge_graph = getattr(analyst_output, 'knowledge_graph', None) if analyst_output else None
        gap_resolutions = getattr(analyst_output, 'gap_resolutions', None) if analyst_output else None

        # Phase 2 CoV: Run claim verification if enabled
        cov_verification = None
        cov_summary = ""
        if enable_cov and formatted_context:
            self.logger.info("CoV: Running Chain of Verification")

            # Send SSE progress: CoV verifying
            await self._send_progress({
                "message_type": "intermediate_result",
                "stage": "cov_verifying"
            })

            cov_verification = await self.run_cov_verification(
                draft=draft,
                formatted_context=formatted_context
            )
            if cov_verification:
                # Build summary to append to review prompt
                cov_summary = self.cov_prompt_builder.build_verification_summary_for_critic(
                    cov_verification
                )
                self.logger.info(
                    f"CoV: Verification complete - "
                    f"verified={cov_verification.get('verified_count', 0)}, "
                    f"unverified={cov_verification.get('unverified_count', 0)}, "
                    f"contradicted={cov_verification.get('contradicted_count', 0)}"
                )

                # Send SSE progress: CoV complete
                await self._send_progress({
                    "message_type": "intermediate_result",
                    "stage": "cov_complete",
                    "verified_count": cov_verification.get('verified_count', 0),
                    "unverified_count": cov_verification.get('unverified_count', 0),
                    "contradicted_count": cov_verification.get('contradicted_count', 0)
                })

        # Build the review prompt from PDF (pages 16-21)
        review_prompt = self.prompt_builder.build_review_prompt(
            draft=draft,
            query=query,
            mode=mode,
            argument_graph=argument_graph,
            knowledge_graph=knowledge_graph,  # Phase KG
            enable_structured_weaknesses=enable_structured,
            gap_resolutions=gap_resolutions,  # Stage 5
            enable_live_research=enable_live_research  # Task 5
        )

        # Append CoV summary to review prompt if available
        if cov_summary:
            review_prompt += cov_summary

        # Choose schema based on feature flags (Live Research takes highest priority)
        if enable_live_research:
            from reasoning.schemas_live import CriticReviewOutputLive
            response_schema = CriticReviewOutputLive
        elif enable_cov and cov_verification:
            from reasoning.schemas_enhanced import CriticReviewOutputEnhancedCoV
            response_schema = CriticReviewOutputEnhancedCoV
        elif enable_structured:
            from reasoning.schemas_enhanced import CriticReviewOutputEnhanced
            response_schema = CriticReviewOutputEnhanced
        else:
            response_schema = CriticReviewOutput

        # Call LLM with validation
        result, retry_count, fallback_used = await self.call_llm_validated(
            prompt=review_prompt,
            response_schema=response_schema,
            level="high"
        )

        # Log TypeAgent metrics for analytics
        self.logger.debug(f"TypeAgent metrics: retries={retry_count}, fallback={fallback_used}")

        # Phase 2 CoV: Attach verification results to output and auto-escalate
        if enable_cov and cov_verification:
            # Add CoV issues to logical_gaps
            cov_issues = []
            for r in cov_verification.get("results", []):
                status = r.get("status", "")
                if status == "unverified":
                    cov_issues.append(f"[CoV 未驗證] {r.get('claim', '')}")
                elif status == "contradicted":
                    cov_issues.append(f"[CoV 矛盾] {r.get('claim', '')}")

            if cov_issues:
                result_logical_gaps = list(result.logical_gaps) if result.logical_gaps else []
                result_logical_gaps.extend(cov_issues)

                # Auto-escalate based on CoV results
                contradicted_count = cov_verification.get("contradicted_count", 0)
                unverified_count = cov_verification.get("unverified_count", 0)

                new_status = result.status
                if contradicted_count > 0:
                    new_status = "REJECT"
                    self.logger.warning(f"CoV: Auto-escalating to REJECT due to {contradicted_count} contradicted claims")
                elif unverified_count >= 3 and result.status == "PASS":
                    new_status = "WARN"
                    self.logger.warning(f"CoV: Escalating to WARN due to {unverified_count} unverified claims")

                # Rebuild result with CoV data
                from reasoning.schemas_enhanced import CriticReviewOutputEnhancedCoV, CoVVerificationOutput
                cov_output = CoVVerificationOutput(
                    results=[
                        self._dict_to_verification_result(r)
                        for r in cov_verification.get("results", [])
                    ],
                    summary=cov_verification.get("summary", ""),
                    verified_count=cov_verification.get("verified_count", 0),
                    unverified_count=cov_verification.get("unverified_count", 0),
                    contradicted_count=cov_verification.get("contradicted_count", 0)
                )

                # B1 (C2+C6): preserve runtime type + all fields. If result is
                # already a CoV/Live instance, model_copy keeps its type (incl.
                # CriticReviewOutputLive.narration_transition) and updates in place.
                # Otherwise (base/Enhanced, no cov_verification field), upgrade to
                # CriticReviewOutputEnhancedCoV via model_dump() — carries every
                # existing field (structured_weaknesses, etc.) without hand-listing,
                # so future base fields are not silently dropped.
                if hasattr(result, "cov_verification"):
                    result = result.model_copy(update={
                        "status": new_status,
                        "logical_gaps": result_logical_gaps,
                        "cov_verification": cov_output,
                    })
                else:
                    result = CriticReviewOutputEnhancedCoV(**{
                        **result.model_dump(),
                        "status": new_status,
                        "logical_gaps": result_logical_gaps,
                        "cov_verification": cov_output,
                    })

        # Auto-escalate based on critical weaknesses (Phase 2)
        if hasattr(result, 'structured_weaknesses') and result.structured_weaknesses:
            critical_count = sum(1 for w in result.structured_weaknesses if w.severity == "critical")
            thresholds = CONFIG.reasoning_params.get("critique_thresholds", {})
            max_critical = thresholds.get("critical_weakness_count", 2)

            if critical_count >= max_critical and result.status != "REJECT":
                self.logger.warning(f"Auto-escalating to REJECT: {critical_count} critical weaknesses")
                # B1: model_copy preserves runtime type + all unchanged fields
                # (incl. cov_verification when result is already a CoV instance),
                # instead of re-constructing a narrower type and dropping data.
                result = result.model_copy(update={
                    "status": "REJECT",
                    "critique": result.critique + f"\n\n[自動升級至 REJECT：{critical_count} 個嚴重問題]",
                })

        # RSN-4: Mark verification status when CoV was enabled but failed
        if enable_cov and cov_verification and cov_verification.get("verification_status") == "unverified":
            self.logger.warning("CoV verification failed - marking result as unverified")
            # Attach verification status as dynamic attributes for frontend notification
            result.__dict__["verification_status"] = "unverified"
            result.__dict__["verification_message"] = cov_verification.get(
                "verification_message", "本報告未經完整事實驗證"
            )

        return result

    async def _extract_verifiable_claims(
        self,
        draft: str
    ) -> List[Dict[str, Any]]:
        """
        Extract verifiable claims from draft using LLM.

        Phase 2 CoV: Uses LLM to identify factual claims that can be verified
        against sources (numbers, dates, entities, events, statistics, quotes).

        Args:
            draft: The research draft to extract claims from

        Returns:
            List of claim dictionaries with keys:
            - claim: str (the factual claim)
            - claim_type: str (number, date, person, organization, event, statistic, quote)
            - source_reference: Optional[int] (citation ID if mentioned)
            - context: Optional[str] (surrounding context)
            - subject_entity: Optional[str] (歸屬實體主詞；無明確主詞時 None；A-2 張冠李戴比對用)
        """
        from reasoning.schemas_enhanced import ClaimsList

        self.logger.info("CoV: Extracting verifiable claims from draft")

        # Build extraction prompt
        extraction_prompt = self.cov_prompt_builder.build_claim_extraction_prompt(draft)

        # Call LLM with validation
        result, retry_count, fallback_used = await self.call_llm_validated(
            prompt=extraction_prompt,
            response_schema=ClaimsList,
            level="high"
        )

        self.logger.info(
            f"CoV: Extracted {len(result.claims)} claims "
            f"(retries={retry_count}, fallback={fallback_used})"
        )

        # Convert to list of dicts for easier processing
        claims_list = []
        for claim in result.claims:
            claims_list.append({
                "claim": claim.claim,
                "claim_type": claim.claim_type.value if hasattr(claim.claim_type, 'value') else str(claim.claim_type),
                "source_reference": claim.source_reference,
                "context": claim.context,
                "subject_entity": claim.subject_entity,  # A-2: 攜帶主詞穿越抽取→驗證斷層
            })

        return claims_list

    async def _verify_claims_against_sources(
        self,
        claims: List[Dict[str, Any]],
        formatted_context: str
    ) -> Dict[str, Any]:
        """
        Verify extracted claims against available sources using LLM.

        Phase 2 CoV: Uses LLM to semantically compare each claim against
        source content and determine verification status.

        Args:
            claims: List of extracted claims to verify
            formatted_context: Formatted source context with citation markers

        Returns:
            CoVVerificationOutput as dict with keys:
            - results: List of verification results
            - summary: Summary string
            - verified_count: int
            - unverified_count: int
            - contradicted_count: int
        """
        from reasoning.schemas_enhanced import CoVVerificationOutput

        if not claims:
            self.logger.info("CoV: No claims to verify")
            return {
                "results": [],
                "summary": "No verifiable claims found in draft",
                "verified_count": 0,
                "unverified_count": 0,
                "contradicted_count": 0
            }

        self.logger.info(f"CoV: Verifying {len(claims)} claims against sources")

        # Build verification prompt
        verification_prompt = self.cov_prompt_builder.build_claim_verification_prompt(
            claims=claims,
            formatted_context=formatted_context
        )

        # Call LLM with validation
        result, retry_count, fallback_used = await self.call_llm_validated(
            prompt=verification_prompt,
            response_schema=CoVVerificationOutput,
            level="high"
        )

        self.logger.info(
            f"CoV: Verification complete - "
            f"verified={result.verified_count}, "
            f"unverified={result.unverified_count}, "
            f"contradicted={result.contradicted_count}"
        )

        # Convert to dict
        return {
            "results": [
                {
                    "claim": r.claim,
                    "status": r.status.value if hasattr(r.status, 'value') else str(r.status),
                    "evidence": r.evidence,
                    "source_id": r.source_id,
                    "explanation": r.explanation,
                    "confidence": r.confidence
                }
                for r in result.results
            ],
            "summary": result.summary,
            "verified_count": result.verified_count,
            "unverified_count": result.unverified_count,
            "contradicted_count": result.contradicted_count
        }

    async def run_cov_verification(
        self,
        draft: str,
        formatted_context: str
    ) -> Optional[Dict[str, Any]]:
        """
        Run complete Chain of Verification process.

        This is the main entry point for CoV, called from review() when
        cov_lite_enabled is True.

        Args:
            draft: The research draft to verify
            formatted_context: Formatted source context with citation markers

        Returns:
            CoVVerificationOutput as dict, or None if CoV fails
        """
        try:
            # Step 1: Extract verifiable claims
            claims = await self._extract_verifiable_claims(draft)

            if not claims:
                self.logger.info("CoV: No verifiable claims found, skipping verification")
                return {
                    "results": [],
                    "summary": "No verifiable claims found in draft",
                    "verified_count": 0,
                    "unverified_count": 0,
                    "contradicted_count": 0
                }

            # Step 2: Verify claims against sources
            verification_output = await self._verify_claims_against_sources(
                claims=claims,
                formatted_context=formatted_context
            )

            return verification_output

        except Exception as e:
            self.logger.warning(f"CoV verification failed: {e}", exc_info=True)
            # RSN-4: Return a degraded result with verification status
            # so downstream can mark the response as unverified
            return {
                "results": [],
                "summary": f"事實驗證失敗：{str(e)}",
                "verified_count": 0,
                "unverified_count": 0,
                "contradicted_count": 0,
                "verification_status": "unverified",
                "verification_message": "本報告未經完整事實驗證"
            }

    # ========================================================================
    # Track F (sprint 2026-05-28) — F1 per-section publish gate + F3 LR wrapper
    # ========================================================================

    async def review_section_publish_gate(
        self,
        section: "LiveWriterSectionOutput",
        section_index: int,
        chapter_evidence_text: str,
        warned_critic_claims: Optional[List[Dict]] = None,  # C-2 (NF-2 R2 fix: dict)
        time_constraint: Optional["TimeRange"] = None,  # I-7
    ) -> "CriticSectionReview":
        """Track F F1 per-section Critic publish gate。

        跑 high-level LLM call 對 single section 做 claim-level critic review。
        LLM call 失敗 → **C-3 fail-loud fallback**：verdict=WARN（**不可 PASS** —
        違反 CLAUDE.md「不可 Silent Fail」，user 必須看見「critic 未跑」marker）。

        Args:
            section: writer 寫完 + T5 entity guard 跑完的 section
            section_index: section 在 written_sections 中的 index
            chapter_evidence_text: 該章 evidence_pool subset 全文（title + snippet）
            warned_critic_claims: BAB Critic 已 WARN 的 GroundedClaim **dict** 清單（
                C-2，NF-2 R2 fix 2026-05-29：model_dump 過的 dict，dict access 不可
                attr access）
            time_constraint: user_selected 時間範圍（Track E land 後生效，I-7）

        Returns:
            CriticSectionReview
        """
        from reasoning.schemas_live import CriticSectionReview

        prompt = self.prompt_builder.build_section_publish_gate_prompt(
            section=section,
            chapter_evidence_text=chapter_evidence_text,
            warned_critic_claims=warned_critic_claims,
            time_constraint=time_constraint,
        )

        try:
            review, retry_count, fallback_used = await self.call_llm_validated(
                prompt=prompt,
                response_schema=CriticSectionReview,
                level="high",  # F-AMB-5 LOCKED: F1 critic 用 high
            )
        except Exception as e:
            # C-3: 不可 silent fail PASS — 走 WARN 讓 user 看見 marker
            self.logger.warning(
                f"[LIVE RESEARCH F1] review_section_publish_gate LLM call failed (non-fatal): "
                f"{type(e).__name__}: {e}; fail-loud fallback verdict=WARN"
            )
            return CriticSectionReview(
                section_index=section_index,
                verdict="WARN",  # fail-loud: WARN 走 marker path，user 看得到
                overall_explanation=(
                    f"F1 critic LLM call failed ({type(e).__name__}: {e}); "
                    f"auto-WARN per fail-loud discipline — 此章 critic 未跑，"
                    f"請人工 review 或重跑此章節（CLAUDE.md「不可 Silent Fail」紀律）"
                ),
            )

        # 確保 caller 傳入的 section_index 寫對（LLM 可能偷懶回 0）
        review = review.model_copy(update={"section_index": section_index})

        self.logger.info(
            f"[LIVE RESEARCH F1] section {section_index} verdict={review.verdict} "
            f"claim_issues={len(review.claim_issues)} "
            f"(retries={retry_count}, fallback={fallback_used})"
        )
        return review

    async def run_cov_for_lr_section(
        self,
        section_content: str,
        chapter_evidence_text: str,
    ) -> Optional[Dict[str, Any]]:
        """Track F F3 (sprint 2026-05-28): LR per-section CoV-lite wrapper。

        Reuse DR run_cov_verification — section_content 作 draft，
        chapter_evidence_text 作 formatted_context。LR per-section path 不走
        DR critic.review() 主迴圈，所以需 LR-specific entry point。

        Returns:
            CoV verification result dict (verified_count / unverified_count /
            contradicted_count / results / summary) or None on failure.
        """
        try:
            return await self.run_cov_verification(
                draft=section_content,
                formatted_context=chapter_evidence_text,
            )
        except Exception as e:
            self.logger.warning(
                f"[LIVE RESEARCH F3] run_cov_for_lr_section failed (non-fatal): "
                f"{type(e).__name__}: {e}"
            )
            return None

    def _dict_to_verification_result(self, d: Dict[str, Any]):
        """
        Convert a verification result dict to ClaimVerificationResult model.

        Args:
            d: Dict with claim verification data

        Returns:
            ClaimVerificationResult instance
        """
        from reasoning.schemas_enhanced import ClaimVerificationResult, VerificationStatus

        # Map status string to enum
        status_str = d.get("status", "unverified")
        status_map = {
            "verified": VerificationStatus.VERIFIED,
            "unverified": VerificationStatus.UNVERIFIED,
            "contradicted": VerificationStatus.CONTRADICTED,
            "partially_verified": VerificationStatus.PARTIALLY_VERIFIED
        }
        status = status_map.get(status_str, VerificationStatus.UNVERIFIED)

        return ClaimVerificationResult(
            claim=d.get("claim", ""),
            status=status,
            evidence=d.get("evidence"),
            source_id=d.get("source_id"),
            explanation=d.get("explanation", "No explanation provided"),
            confidence=d.get("confidence", "medium")
        )

    async def _send_progress(self, message: Dict[str, Any]) -> None:
        """
        Send SSE progress message to frontend.

        Args:
            message: Progress message dict with message_type, stage, etc.
        """
        try:
            if hasattr(self.handler, 'message_sender'):
                await self.handler.message_sender.send_message(message)
        except Exception as e:
            # Progress messages are non-critical - log but don't crash
            self.logger.warning(f"Progress message send failed (non-critical): {e}")
