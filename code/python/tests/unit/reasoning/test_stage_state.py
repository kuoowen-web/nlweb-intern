"""Tests for LiveResearchStageState serialization."""

import json
import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from reasoning.live_research.stage_state import LiveResearchStageState


class TestStageStateSerialization:
    def test_default_state(self):
        state = LiveResearchStageState()
        assert state.current_stage == 0
        assert state.stage_status == "pending"
        assert state.context_map_json == ""

    def test_round_trip(self):
        state = LiveResearchStageState(
            current_stage=2,
            stage_status="checkpoint",
            checkpoint_prompt="你覺得結構如何？",
            context_map_json='{"research_question": "test", "version": 1}',
            initial_context_map_json='{"research_question": "test", "version": 0}',
            completed_sections=["topic-1"],
            executed_searches=["台灣綠能"],
        )
        d = state.to_dict()
        restored = LiveResearchStageState.from_dict(d)
        assert restored.current_stage == 2
        assert restored.stage_status == "checkpoint"
        assert restored.completed_sections == ["topic-1"]
        assert restored.executed_searches == ["台灣綠能"]

    def test_from_dict_missing_fields_use_defaults(self):
        """部分欄位缺失時使用預設值。"""
        restored = LiveResearchStageState.from_dict({"current_stage": 3})
        assert restored.current_stage == 3
        assert restored.stage_status == "pending"
        assert restored.context_map_json == ""

    def test_to_dict_is_json_serializable(self):
        state = LiveResearchStageState(current_stage=1)
        serialized = json.dumps(state.to_dict())
        assert isinstance(serialized, str)

    def test_advance_stage(self):
        state = LiveResearchStageState(current_stage=1, stage_status="completed")
        state.advance_to_stage(2)
        assert state.current_stage == 2
        assert state.stage_status == "in_progress"

    def test_set_checkpoint(self):
        state = LiveResearchStageState(current_stage=1, stage_status="in_progress")
        state.set_checkpoint("你覺得結構如何？")
        assert state.stage_status == "checkpoint"
        assert state.checkpoint_prompt == "你覺得結構如何？"


class TestVP7WriterFields:
    """VP-7: Stage 5 writer state fields.

    Fields: stage_5_writer_running, last_completed_section_index.
    Must round-trip and have correct defaults.
    Note: stage_5_stop_requested removed 2026-06-04 (placebo — stop mechanism removed).
    """

    def test_writer_fields_default_values(self):
        """Both fields have safe defaults for new sessions."""
        s = LiveResearchStageState()
        assert s.stage_5_writer_running is False
        assert s.last_completed_section_index == -1

    def test_writer_fields_roundtrip(self):
        """Set non-default values, dump, restore, assert preserved."""
        s = LiveResearchStageState(
            current_stage=5,
            stage_5_writer_running=True,
            last_completed_section_index=3,
        )
        restored = LiveResearchStageState.from_dict(s.to_dict())
        assert restored.stage_5_writer_running is True
        assert restored.last_completed_section_index == 3

    def test_writer_fields_backward_compat_old_rows(self):
        """Old DB rows lacking the new fields fall back to defaults (no KeyError)."""
        old_row = {
            "current_stage": 5,
            "stage_status": "checkpoint",
            "written_sections": [{"section_index": 0, "title": "t0", "content": "..."}],
            # NOTE: no stage_5_* keys, no last_completed_section_index
        }
        s = LiveResearchStageState.from_dict(old_row)
        assert s.stage_5_writer_running is False
        assert s.last_completed_section_index == -1


class TestVP7PerSectionCheckpointFields:
    """VP-7: writer per-section checkpoint flow reversal.

    新增 stage5_waiting_for_user 用來標記「writer 寫完一段、paused 等
    user reply」狀態，跨 request 持久化 + 提供 debug log。
    """

    def test_stage5_waiting_for_user_default_false(self):
        s = LiveResearchStageState()
        assert s.stage5_waiting_for_user is False

    def test_stage5_waiting_for_user_roundtrip(self):
        s = LiveResearchStageState(
            current_stage=5,
            stage5_waiting_for_user=True,
        )
        restored = LiveResearchStageState.from_dict(s.to_dict())
        assert restored.stage5_waiting_for_user is True

    def test_stage5_waiting_for_user_backward_compat_old_rows(self):
        """Old DB row lacking the new key falls back to default False."""
        old_row = {
            "current_stage": 5,
            "stage_status": "checkpoint",
            # NOTE: no stage5_waiting_for_user key
        }
        s = LiveResearchStageState.from_dict(old_row)
        assert s.stage5_waiting_for_user is False


# ============================================================================
# 路 3 (P-回顧): final_report_markdown — Stage 6 後端組好的整份 full_report
# markdown 字串落 DB，回顧時前端直接讀、不重組。
# ============================================================================


class TestFinalReportMarkdownField:
    """路 3：Stage 6 整份報告 markdown 字串持久化。"""

    def test_final_report_markdown_roundtrip(self):
        """final_report_markdown 經 to_dict → from_dict 完整保留（含中文/換行/KG fence）。"""
        report = (
            "# 台灣綠能政策研究\n\n## 第一章\n內容[1]。\n\n"
            "## 參考文獻\n[1] 來源 — example.com https://example.com\n\n"
            "---\n\n## 知識圖譜 (Knowledge Graph)\n\n```json\n{\n  \"entities\": []\n}\n```\n"
        )
        s = LiveResearchStageState(current_stage=6, final_report_markdown=report)
        restored = LiveResearchStageState.from_dict(s.to_dict())
        assert restored.final_report_markdown == report

    def test_final_report_markdown_default_empty(self):
        """新建 state 預設 final_report_markdown == ''（未跑到 Stage 6）。"""
        s = LiveResearchStageState()
        assert s.final_report_markdown == ""

    def test_final_report_markdown_backward_compat_old_session(self):
        """欄位上線前的舊 session（dict 無 final_report_markdown key）→ from_dict fallback ''。"""
        old_dict = {"current_stage": 6, "stage_status": "completed"}
        s = LiveResearchStageState.from_dict(old_dict)
        assert s.final_report_markdown == ""

    def test_final_report_markdown_bare_backslash_roundtrip(self):
        """Minor (plan §致命陷阱5): 裸反斜線 / unicode / JSON 內容字元落 DB round-trip 不被轉義破壞。"""
        report = (
            "# 報告\n\n含裸反斜線 C:\\Users\\test 與 \\n 字面 與 正則 \\d+ 與 \\t。\n\n"
            "```json\n{\"path\": \"C:\\\\Users\", \"regex\": \"\\\\w+\"}\n```\n"
            "Unicode：中文 與 emoji。\n"
        )
        s = LiveResearchStageState(current_stage=6, final_report_markdown=report)
        restored = LiveResearchStageState.from_dict(s.to_dict())
        assert restored.final_report_markdown == report


# ============================================================================
# Track A (LR DR-parity sprint 2026-05-28) — grounding schema additions
# ============================================================================


class TestTrackAEvidenceUsageSchema:
    """Track A Task 1: state.evidence_usage / schema_version / rejected_claims_log。"""

    def test_evidence_usage_default_empty(self):
        s = LiveResearchStageState()
        assert s.evidence_usage == {}

    def test_evidence_usage_roundtrip_via_dict(self):
        from reasoning.schemas_live import GroundedClaim
        s = LiveResearchStageState()
        s.evidence_usage = {
            7: [GroundedClaim(
                claim="x", reasoning_type="induction", confidence="high",
                source_topic="t1", source_iteration=1,
            ).model_dump()],
        }
        d = s.to_dict()
        # JSON dict key 必須 str (沿 user_voice 既有 pattern)
        assert "evidence_usage" in d
        s2 = LiveResearchStageState.from_dict(d)
        assert 7 in s2.evidence_usage
        assert s2.evidence_usage[7][0]["claim"] == "x"

    def test_evidence_usage_backward_compat_old_payload_missing_field(self):
        """舊 row 無 evidence_usage key → default empty dict。"""
        old_payload = {
            "current_stage": 2,
            "stage_status": "completed",
            # 故意省略 evidence_usage
        }
        s = LiveResearchStageState.from_dict(old_payload)
        assert s.evidence_usage == {}

    def test_schema_version_default_v2_for_new_state(self):
        """addendum C-3: 新建 state.schema_version == 2。"""
        s = LiveResearchStageState()
        assert s.schema_version == 2

    def test_schema_version_v1_for_legacy_payload_missing_field(self):
        """addendum C-3: sprint 前舊 row 無 schema_version → load 後 = 1。"""
        legacy_payload = {
            "current_stage": 5,
            "stage_status": "completed",
        }
        s = LiveResearchStageState.from_dict(legacy_payload)
        assert s.schema_version == 1

    def test_schema_version_v2_for_new_payload_with_field(self):
        new_payload = {
            "current_stage": 2,
            "stage_status": "completed",
            "schema_version": 2,
        }
        s = LiveResearchStageState.from_dict(new_payload)
        assert s.schema_version == 2

    def test_rejected_claims_log_default_empty(self):
        """Gemini C-1: rejected_claims_log forensic trail。"""
        s = LiveResearchStageState()
        assert s.rejected_claims_log == []

    def test_rejected_claims_log_roundtrip(self):
        s = LiveResearchStageState()
        s.rejected_claims_log = [
            {"topic_id": "t1", "iteration": 1, "claim_count": 2,
             "evidence_ids": [5, 7], "reason": "critic_status_reject"},
        ]
        d = s.to_dict()
        assert d["rejected_claims_log"] == [
            {"topic_id": "t1", "iteration": 1, "claim_count": 2,
             "evidence_ids": [5, 7], "reason": "critic_status_reject"},
        ]
        s2 = LiveResearchStageState.from_dict(d)
        assert s2.rejected_claims_log[0]["topic_id"] == "t1"

    def test_evidence_usage_backward_compat_real_existing_session_payload(self):
        """CEO 2026-05-28 finding：既有 prod LR session payload 真實結構 —
        written_sections 含完整章節內容、chat_history/research_report 反而空、
        無 evidence_usage 欄位。Track A schema 改動絕不可破壞此既有 row deserialize。
        """
        real_existing_payload = {
            "current_stage": 5,
            "stage_status": "completed",
            "research_question": "台灣再生能源發展",
            "evidence_pool_json": "",
            "executed_searches": [],
            "chat_history": [],
            "research_report": "",
            "written_sections": [
                {
                    "section_index": 0,
                    "title": "前言",
                    "content": "本研究探討台灣再生能源...",
                    "sources_used": [1, 2],
                },
                {
                    "section_index": 1,
                    "title": "國內案例",
                    "content": "台灣光電發展...",
                    "sources_used": [3],
                },
            ],
            # 故意省略 evidence_usage / schema_version / rejected_claims_log
        }
        s = LiveResearchStageState.from_dict(real_existing_payload)
        assert s.evidence_usage == {}
        assert s.rejected_claims_log == []
        assert s.schema_version == 1  # 舊 row 預設 v1
        assert len(s.written_sections) == 2
        assert s.written_sections[0]["title"] == "前言"
        assert s.written_sections[1]["sources_used"] == [3]


class TestV1RollbackForwardOnlyConstraint:
    """Gemini Rollback 拍板 (2026-05-28)：deliberate behavior 紀錄 — v1 code to_dict()
    會抹除 v2 欄位。本 test 不是 bug，而是 forward-only constraint 的書面證據。

    模擬 v1 code（只 dump v1 已知欄位）→ 驗 v2 欄位真的不在 output（rollback 失血場景）。
    """

    def test_v1_code_silently_drops_v2_fields_on_to_dict(self):
        from reasoning.schemas_live import GroundedClaim
        # Build v2 payload
        state = LiveResearchStageState()
        state.evidence_usage = {1: [GroundedClaim(
            claim="x", reasoning_type="induction", confidence="high",
            source_topic="t", source_iteration=1,
        ).model_dump()]}
        state.rejected_claims_log = [{
            "topic_id": "t", "iteration": 1, "claim_count": 1,
            "evidence_ids": [2], "reason": "critic_status_reject",
        }]
        state.schema_version = 2
        v2_dump = state.to_dict()
        assert "evidence_usage" in v2_dump
        assert "rejected_claims_log" in v2_dump
        assert v2_dump["schema_version"] == 2

        # Mock v1 to_dict: 只 dump sprint 前 v1 已知欄位
        # FROZEN: Represents the exact keys available in V1 before Sprint 2026-05-28.
        # DO NOT regenerate this set from current schema — purpose is to simulate
        # legacy v1 code behavior. Update only if a v3 schema is introduced.
        V1_FIELDS = frozenset({
            "current_stage", "stage_status", "checkpoint_prompt",
            "failed_intent_parse_count",
            "context_map_json", "initial_context_map_json",
            "completed_sections", "style_features_json",
            "format_specs", "pending_format_confirmation",
            "pending_reframe_json", "pending_reframe_proposal_markdown",
            "book_outline_json", "written_sections",
            "executed_searches", "evidence_pool_json",
            "hallucination_corrected",
            "stage_5_writer_running",
            "last_completed_section_index", "stage5_waiting_for_user",
            "user_voice", "created_at", "last_updated_at",
        })
        v1_simulated_dump = {k: v for k, v in v2_dump.items() if k in V1_FIELDS}
        # v2 欄位被 v1 to_dict 抹除（rollback 後失血場景）
        assert "evidence_usage" not in v1_simulated_dump
        assert "rejected_claims_log" not in v1_simulated_dump
        assert "schema_version" not in v1_simulated_dump


# ============================================================================
# Track E (LR DR-parity sprint 2026-05-28) — Temporal BINDING state persistence
# ============================================================================


class TestTrackETimeConstraintPersistence:
    """state.time_constraint 持久化 + backward-compat。

    Track E E1：新欄位（default None）對舊 v1 / 早期 v2 session 無作用，
    pipeline pass-through（不過濾、不注入 BINDING）。
    """

    def test_time_constraint_default_none(self):
        """新 session 預設無時間訴求。"""
        s = LiveResearchStageState()
        assert s.time_constraint is None

    def test_time_constraint_roundtrip(self):
        """set → to_dict → from_dict 不掉資料。"""
        from reasoning.schemas_live import TimeRange
        s = LiveResearchStageState()
        s.time_constraint = TimeRange(
            start_date="2024-01-01",
            raw_phrase="2024 後",
            user_selected=True,
        )
        d = s.to_dict()
        assert d["time_constraint"]["start_date"] == "2024-01-01"
        assert d["time_constraint"]["user_selected"] is True
        s2 = LiveResearchStageState.from_dict(d)
        assert s2.time_constraint is not None
        assert s2.time_constraint.start_date == "2024-01-01"
        assert s2.time_constraint.user_selected is True

    def test_time_constraint_backward_compat_missing(self):
        """舊 session（v1/v2 早期）無 time_constraint 欄位 → load 後 None。"""
        legacy_payload = {
            "current_stage": 5,
            "stage_status": "completed",
            # 故意省略 time_constraint
        }
        s = LiveResearchStageState.from_dict(legacy_payload)
        assert s.time_constraint is None

    def test_time_constraint_backward_compat_invalid_payload_falls_back_to_none(self, caplog):
        """既有 row 寫進髒資料（非 dict / invalid TimeRange） → load 後 None + log warning。

        紀律：不可 silent fail（必須 log），但不可 raise（會炸 session restore）。
        """
        import logging
        legacy_payload = {
            "current_stage": 5,
            "time_constraint": "not-a-dict-just-a-string",
        }
        with caplog.at_level(logging.WARNING):
            s = LiveResearchStageState.from_dict(legacy_payload)
        assert s.time_constraint is None


class TestTrackEEvidencePoolEntryPublishedAtCompat:
    """Codex P2：EvidencePoolEntry 加 Optional published_at 不破壞既有 consumer 載入。

    Track A frozen schema 邊界紀律驗證：「擴張（新 Optional 欄位）
    ≠ 修改（既有欄位 type / required / 移除）」。
    """

    def test_evidence_pool_entry_legacy_no_published_at_deserialize_ok(self):
        from reasoning.schemas_live import EvidencePoolEntry
        legacy_entry = {
            # 故意省略 published_at（舊 prod row 沒這欄位）
            "evidence_id": 7,
            "url": "https://example.com",
            "title": "舊文章",
            "snippet": "...",
            "source_domain": "example.com",
            "retrieved_at": "2026-05-01T10:00:00",
        }
        entry = EvidencePoolEntry.model_validate(legacy_entry)
        assert entry.published_at is None
        assert entry.evidence_id == 7
        # Round-trip：model_dump 對 None 欄位顯式輸出
        dumped = entry.model_dump()
        assert dumped.get("published_at") is None


# ============================================================================
# Track F (sprint 2026-05-28) — critic_section_reviews / consistency_drift_log
# ============================================================================

class TestTrackFStateFields:
    """Track F F1 / F2 兩個新欄位的 default / round-trip / backward-compat。"""

    def test_critic_section_reviews_default_empty_dict(self):
        s = LiveResearchStageState()
        assert s.critic_section_reviews == {}

    def test_critic_section_reviews_roundtrip(self):
        s = LiveResearchStageState()
        s.critic_section_reviews[2] = {
            "section_index": 2,
            "verdict": "REJECT",
            "claim_issues": [],
            "overall_explanation": "x",
            "cov_verification_summary": None,
        }
        d = s.to_dict()
        # JSON 持久化 int key → str key（沿 evidence_usage pattern）
        assert "2" in d["critic_section_reviews"]
        s2 = LiveResearchStageState.from_dict(d)
        assert 2 in s2.critic_section_reviews
        assert s2.critic_section_reviews[2]["verdict"] == "REJECT"

    def test_critic_section_reviews_backward_compat_missing_field(self):
        """v1 / 早期 v2 session payload 無此欄位 → load 後空 dict。"""
        s = LiveResearchStageState.from_dict({})
        assert s.critic_section_reviews == {}

    def test_critic_section_reviews_non_int_key_skipped(self):
        """JSON dict key 非 int 解（垃圾資料）→ skip 該 entry，不 crash。"""
        d = {"critic_section_reviews": {"bad": {"verdict": "PASS"}, "3": {"verdict": "WARN"}}}
        s = LiveResearchStageState.from_dict(d)
        assert 3 in s.critic_section_reviews
        assert "bad" not in s.critic_section_reviews

    def test_consistency_drift_log_default_empty_list(self):
        s = LiveResearchStageState()
        assert s.consistency_drift_log == []

    def test_consistency_drift_log_roundtrip(self):
        s = LiveResearchStageState()
        s.consistency_drift_log.append({
            "stage": "stage_1",
            "iteration": 1,
            "topic_id": "",
            "drift_level": "none",
            "drift_description": "",
            "recommended_action": "continue",
            "timestamp": "2026-05-28T10:00:00",
        })
        d = s.to_dict()
        assert len(d["consistency_drift_log"]) == 1
        s2 = LiveResearchStageState.from_dict(d)
        assert s2.consistency_drift_log[0]["drift_level"] == "none"
        assert s2.consistency_drift_log[0]["stage"] == "stage_1"

    def test_consistency_drift_log_backward_compat_missing_field(self):
        s = LiveResearchStageState.from_dict({})
        assert s.consistency_drift_log == []


# ============================================================================
# LR SSE reconnect/resume (2026-06-15) — offline 防呆燒錢上限 schema fields
# ============================================================================


class TestOfflineCapFields:
    """斷線不取消 plan：離線上限計數進 DB state（CEO 拍板）。

    Fields: offline_since, offline_capped, offline_cap_reason,
    offline_checkpoint_advances. 必須 round-trip + 舊 row fallback default
    （舊 session 絕不被誤判 capped）。
    """

    def test_offline_fields_default_values(self):
        s = LiveResearchStageState()
        assert s.offline_since is None
        assert s.offline_capped is False
        assert s.offline_cap_reason == ""
        assert s.offline_checkpoint_advances == 0

    def test_offline_fields_roundtrip(self):
        s = LiveResearchStageState(
            current_stage=5,
            offline_since=1718400000.0,
            offline_capped=True,
            offline_cap_reason="next_checkpoint",
            offline_checkpoint_advances=1,
        )
        restored = LiveResearchStageState.from_dict(s.to_dict())
        assert restored.offline_since == 1718400000.0
        assert restored.offline_capped is True
        assert restored.offline_cap_reason == "next_checkpoint"
        assert restored.offline_checkpoint_advances == 1

    def test_offline_fields_backward_compat_old_rows(self):
        """舊 DB row 無 offline 欄位 → fallback default，絕不誤判 capped。"""
        old_row = {
            "current_stage": 5,
            "stage_status": "checkpoint",
            # NOTE: no offline_* keys
        }
        s = LiveResearchStageState.from_dict(old_row)
        assert s.offline_since is None
        assert s.offline_capped is False
        assert s.offline_cap_reason == ""
        assert s.offline_checkpoint_advances == 0

    def test_offline_to_dict_json_serializable(self):
        s = LiveResearchStageState(
            offline_since=1718400000.0,
            offline_capped=True,
            offline_cap_reason="wall_seconds",
            offline_checkpoint_advances=2,
        )
        json.dumps(s.to_dict())  # must not raise


class TestGeneratedReportTitleField:
    def test_generated_report_title_roundtrip(self):
        s = LiveResearchStageState(generated_report_title="有質感的標題")
        d = s.to_dict()
        assert d["generated_report_title"] == "有質感的標題"
        s2 = LiveResearchStageState.from_dict(d)
        assert s2.generated_report_title == "有質感的標題"

    def test_generated_report_title_legacy_session_defaults_empty(self):
        """舊 session（dict 無此欄位）→ from_dict fallback 空字串（backward compat）。"""
        s = LiveResearchStageState.from_dict({"current_stage": 6})
        assert s.generated_report_title == ""
