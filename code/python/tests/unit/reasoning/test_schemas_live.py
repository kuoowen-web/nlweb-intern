"""Live Research schema tests."""
import sys
import os
import json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from reasoning.schemas_live import (
    ContextMap, ContextMapTopic, ContextMapRelation,
    ContextMapSearchSeed, ContextMapDelta,
    AssociatorBuildOutput, AssociatorDeriveOutput, AssociatorRefineOutput,
    ConsistencyReview, StyleAnalysisOutput, StyleFeature,
    LiveWriterSectionOutput, context_map_extract_for_section,
    context_map_to_summary,
)


class TestContextMap:
    def test_create_minimal(self):
        cm = ContextMap(research_question="台灣綠能衝突")
        assert cm.version == 0
        assert cm.topics == []
        assert cm.research_question == "台灣綠能衝突"

    def test_create_with_topics_and_relations(self):
        t1 = ContextMapTopic(name="德國模式", domain="能源政策")
        t2 = ContextMapTopic(name="社區共有", domain="治理模式")
        rel = ContextMapRelation(
            source_topic_id=t1.topic_id,
            target_topic_id=t2.topic_id,
            relation_type="enables"
        )
        cm = ContextMap(
            research_question="test",
            topics=[t1, t2],
            relations=[rel]
        )
        assert len(cm.topics) == 2
        assert len(cm.relations) == 1

    def test_delta_tracks_changes(self):
        delta = ContextMapDelta(
            from_version=0, to_version=1,
            added_topics=["topic-1"],
            reason="初始擷取結果新增了新維度"
        )
        assert delta.to_version == 1
        assert len(delta.added_topics) == 1

    def test_topic_has_auto_uuid(self):
        t = ContextMapTopic(name="Test", domain="D")
        assert len(t.topic_id) > 0

    def test_relation_has_auto_uuid(self):
        t1 = ContextMapTopic(name="T1", domain="D")
        t2 = ContextMapTopic(name="T2", domain="D")
        r = ContextMapRelation(
            source_topic_id=t1.topic_id,
            target_topic_id=t2.topic_id,
            relation_type="causes"
        )
        assert len(r.relation_id) > 0

    def test_context_map_serializes_to_json(self):
        t1 = ContextMapTopic(name="T1", domain="D")
        cm = ContextMap(research_question="test", topics=[t1])
        data = cm.model_dump_json()
        parsed = json.loads(data)
        assert parsed["research_question"] == "test"
        assert len(parsed["topics"]) == 1

    def test_context_map_deserializes_from_json(self):
        t1 = ContextMapTopic(name="T1", domain="D")
        cm = ContextMap(research_question="deserialize test", topics=[t1], version=2)
        data = cm.model_dump_json()
        cm2 = ContextMap.model_validate_json(data)
        assert cm2.research_question == "deserialize test"
        assert cm2.version == 2
        assert cm2.topics[0].name == "T1"

    def test_search_seed_default_status_pending(self):
        t = ContextMapTopic(name="T", domain="D")
        seed = ContextMapSearchSeed(
            query="台灣離岸風電", target_topic_id=t.topic_id, rationale="填補知識缺口"
        )
        assert seed.status == "pending"

    def test_context_map_revision_history_accumulates(self):
        delta1 = ContextMapDelta(from_version=0, to_version=1, reason="first refinement")
        delta2 = ContextMapDelta(from_version=1, to_version=2, reason="second refinement")
        cm = ContextMap(
            research_question="test",
            version=2,
            revision_history=[delta1, delta2]
        )
        assert len(cm.revision_history) == 2
        assert cm.revision_history[1].to_version == 2


class TestExtractForSection:
    def test_extracts_specified_topics(self):
        t1 = ContextMapTopic(name="Focused", domain="D", relevance="core")
        t2 = ContextMapTopic(name="Unrelated", domain="D", relevance="peripheral")
        cm = ContextMap(research_question="test", topics=[t1, t2])
        summary = context_map_extract_for_section(cm, section_topic_ids=[t1.topic_id])
        assert "Focused" in summary
        assert "Unrelated" not in summary

    def test_includes_relation_neighbors(self):
        t1 = ContextMapTopic(name="Main", domain="D", relevance="core")
        t2 = ContextMapTopic(name="Neighbor", domain="D", relevance="supporting")
        rel = ContextMapRelation(
            source_topic_id=t1.topic_id, target_topic_id=t2.topic_id,
            relation_type="causes"
        )
        cm = ContextMap(research_question="test", topics=[t1, t2], relations=[rel])
        summary = context_map_extract_for_section(cm, section_topic_ids=[t1.topic_id])
        assert "Main" in summary
        assert "Neighbor" in summary

    def test_excludes_non_neighbor_topics(self):
        t1 = ContextMapTopic(name="Main", domain="D", relevance="core")
        t2 = ContextMapTopic(name="Isolated", domain="D", relevance="supporting")
        # No relation between t1 and t2
        cm = ContextMap(research_question="test", topics=[t1, t2])
        summary = context_map_extract_for_section(cm, section_topic_ids=[t1.topic_id])
        assert "Main" in summary
        assert "Isolated" not in summary

    def test_contains_version_header(self):
        cm = ContextMap(research_question="version test", version=3)
        summary = context_map_extract_for_section(cm, section_topic_ids=[])
        assert "v3" in summary

    def test_contains_research_question(self):
        cm = ContextMap(research_question="specific question here")
        summary = context_map_extract_for_section(cm, section_topic_ids=[])
        assert "specific question here" in summary

    def test_relation_displayed_in_output(self):
        t1 = ContextMapTopic(name="Alpha", domain="D", relevance="core")
        t2 = ContextMapTopic(name="Beta", domain="D", relevance="supporting")
        rel = ContextMapRelation(
            source_topic_id=t1.topic_id, target_topic_id=t2.topic_id,
            relation_type="causes"
        )
        cm = ContextMap(research_question="test", topics=[t1, t2], relations=[rel])
        summary = context_map_extract_for_section(cm, section_topic_ids=[t1.topic_id])
        assert "causes" in summary
        assert "Alpha" in summary
        assert "Beta" in summary


class TestContextMapToSummary:
    def test_summary_includes_all_topics(self):
        t1 = ContextMapTopic(name="Topic1", domain="D", relevance="core")
        t2 = ContextMapTopic(name="Topic2", domain="D", relevance="peripheral")
        cm = ContextMap(research_question="test", topics=[t1, t2])
        summary = context_map_to_summary(cm)
        assert "Topic1" in summary
        assert "Topic2" in summary

    def test_summary_includes_working_hypothesis(self):
        cm = ContextMap(research_question="test", working_hypothesis="hypothesis text")
        summary = context_map_to_summary(cm)
        assert "hypothesis text" in summary


class TestAssociatorOutputs:
    def test_build_output_roundtrip(self):
        cm = ContextMap(research_question="test roundtrip")
        output = AssociatorBuildOutput(
            context_map=cm,
            narration="我建立了初始知識圖。"
        )
        data = output.model_dump_json()
        recovered = AssociatorBuildOutput.model_validate_json(data)
        assert recovered.context_map.research_question == "test roundtrip"
        assert recovered.narration == "我建立了初始知識圖。"

    def test_derive_output_roundtrip(self):
        t = ContextMapTopic(name="T", domain="D")
        seed = ContextMapSearchSeed(
            query="search query", target_topic_id=t.topic_id, rationale="why"
        )
        output = AssociatorDeriveOutput(
            search_seeds=[seed],
            narration="我推導了搜尋計畫。"
        )
        data = output.model_dump_json()
        recovered = AssociatorDeriveOutput.model_validate_json(data)
        assert len(recovered.search_seeds) == 1
        assert recovered.narration == "我推導了搜尋計畫。"

    def test_refine_output_roundtrip(self):
        cm = ContextMap(research_question="refined", version=1)
        delta = ContextMapDelta(from_version=0, to_version=1, reason="new evidence")
        output = AssociatorRefineOutput(
            updated_context_map=cm,
            delta=delta,
            is_stable=False,
            narration="B 被精煉為 B'。"
        )
        data = output.model_dump_json()
        recovered = AssociatorRefineOutput.model_validate_json(data)
        assert recovered.is_stable is False
        assert recovered.delta.reason == "new evidence"
        assert recovered.narration == "B 被精煉為 B'。"

    def test_refine_output_stable_flag(self):
        cm = ContextMap(research_question="stable test", version=5)
        delta = ContextMapDelta(from_version=4, to_version=5, reason="minor tweak")
        output = AssociatorRefineOutput(
            updated_context_map=cm,
            delta=delta,
            is_stable=True,
            narration="已穩定。"
        )
        assert output.is_stable is True


class TestConsistencyReview:
    def test_all_drift_levels(self):
        for level in ["none", "minor", "moderate", "major"]:
            cr = ConsistencyReview(
                drift_level=level,
                drift_description="test",
                dubao_voice_message="test",
                recommended_action="continue",
            )
            assert cr.drift_level == level

    def test_all_recommended_actions(self):
        for action in ["continue", "pause_confirm", "refine_master_b", "abort"]:
            cr = ConsistencyReview(
                drift_level="none",
                drift_description="desc",
                dubao_voice_message="msg",
                recommended_action=action,
            )
            assert cr.recommended_action == action

    def test_affected_topics_defaults_empty(self):
        cr = ConsistencyReview(
            drift_level="none",
            drift_description="none",
            dubao_voice_message="ok",
            recommended_action="continue",
        )
        assert cr.affected_topics == []


class TestStyleAnalysis:
    def test_single_feature_is_valid(self):
        """sparse 範本只能抽 1 個特徵也合法（min_length 3→1, prod blocker fix）。"""
        result = StyleAnalysisOutput(
            features=[StyleFeature(dimension="x", observation="y", instruction="z")],
            overall_tone="test",
        )
        assert len(result.features) == 1

    def test_zero_features_rejected(self):
        """空 features（0 個）仍違反 min_length=1，schema 層 reject。
        （0→fallback 的優雅降級由 orchestrator._run_style_analysis 負責，不在 schema 層）。"""
        with pytest.raises(Exception):  # Pydantic validation error
            StyleAnalysisOutput(features=[], overall_tone="test")

    def test_valid_features(self):
        features = [
            StyleFeature(dimension=f"dim{i}", observation=f"obs{i}", instruction=f"inst{i}")
            for i in range(3)
        ]
        result = StyleAnalysisOutput(features=features, overall_tone="formal")
        assert len(result.features) == 3

    def test_max_features_validation(self):
        with pytest.raises(Exception):  # Pydantic validation error
            features = [
                StyleFeature(dimension=f"dim{i}", observation=f"obs{i}", instruction=f"inst{i}")
                for i in range(11)
            ]
            StyleAnalysisOutput(features=features, overall_tone="formal")

    def test_citation_format_default_is_numeric(self):
        """citation_format 預設為 'numeric'，向後相容。"""
        features = [
            StyleFeature(dimension=f"dim{i}", observation=f"obs{i}", instruction=f"inst{i}")
            for i in range(3)
        ]
        result = StyleAnalysisOutput(features=features, overall_tone="formal")
        assert result.citation_format == "numeric"

    def test_citation_format_accepts_enum_values(self):
        """citation_format 接受四個 enum 值。"""
        features = [
            StyleFeature(dimension=f"dim{i}", observation=f"obs{i}", instruction=f"inst{i}")
            for i in range(3)
        ]
        for fmt in ["author_year", "numeric", "footnote", "none"]:
            result = StyleAnalysisOutput(
                features=features, overall_tone="formal", citation_format=fmt
            )
            assert result.citation_format == fmt

    def test_citation_format_rejects_invalid(self):
        """citation_format 拒絕非 enum 值。"""
        features = [
            StyleFeature(dimension=f"dim{i}", observation=f"obs{i}", instruction=f"inst{i}")
            for i in range(3)
        ]
        with pytest.raises(Exception):
            StyleAnalysisOutput(
                features=features, overall_tone="formal", citation_format="apa"
            )

    def test_input_is_writing_sample_defaults_true(self):
        """新增欄位預設 True（正常路徑不受影響）。"""
        from reasoning.schemas_live import StyleAnalysisOutput, StyleFeature
        out = StyleAnalysisOutput(
            features=[StyleFeature(dimension="句式", observation="o", instruction="i")],
            overall_tone="test",
        )
        assert out.input_is_writing_sample is True

    def test_input_is_writing_sample_can_be_false(self):
        from reasoning.schemas_live import StyleAnalysisOutput, StyleFeature
        out = StyleAnalysisOutput(
            features=[StyleFeature(dimension="句式", observation="o", instruction="i")],
            overall_tone="test",
            input_is_writing_sample=False,
        )
        assert out.input_is_writing_sample is False

    def test_style_input_not_a_sample_error_exists(self):
        from reasoning.schemas_live import StyleInputNotASampleError
        assert issubclass(StyleInputNotASampleError, Exception)


class TestLiveWriterSectionOutput:
    def test_create_minimal(self):
        section = LiveWriterSectionOutput(
            section_title="Chapter 1",
            section_content="Content here."
        )
        assert section.section_title == "Chapter 1"
        assert section.sources_used == []
        assert section.confidence_level == "Medium"

    def test_sources_used_list(self):
        section = LiveWriterSectionOutput(
            section_title="Chapter 2",
            section_content="Content with sources.",
            sources_used=[1, 3, 5]
        )
        assert section.sources_used == [1, 3, 5]

    def test_confidence_levels(self):
        for level in ["High", "Medium", "Low"]:
            section = LiveWriterSectionOutput(
                section_title="T",
                section_content="C",
                confidence_level=level
            )
            assert section.confidence_level == level


# =====================================================================
# Plan: lr-user-voice-container-and-4-fixes (Phase 3, Fix D)
# =====================================================================

class TestEvidenceRenderNoDump:
    """Fix D: ContextMap render 不機械 dump evidence_ids list literal。

    Audit (2026-05-18) 指出 writer LLM 看到「evidence: [1, 2, 3]」就會段末
    「來源: [1] [2] [3]」抄出來。改 narrative count（『3 個來源支持』）
    → LLM 看不到方便 dump 的 list literal，但仍知道「真有依據」。
    """

    def test_extract_for_section_no_list_literal(self):
        from reasoning.schemas_live import (
            ContextMap, ContextMapTopic, context_map_extract_for_section
        )
        topic = ContextMapTopic(
            topic_id="t1", name="離岸風電", domain="能源政策",
            relevance="core", evidence_ids=[1, 2, 3, 4, 5],
            confidence="high",
        )
        cm = ContextMap(research_question="rq", topics=[topic], version=1)
        out = context_map_extract_for_section(cm, ["t1"])
        # 不該出現 Python list literal
        assert "[1, 2, 3" not in out
        assert "evidence: [" not in out

    def test_extract_for_section_shows_evidence_count_not_ids(self):
        """改用 narrative count 而非 ID 列表。"""
        from reasoning.schemas_live import (
            ContextMap, ContextMapTopic, context_map_extract_for_section
        )
        topic = ContextMapTopic(
            topic_id="t1", name="X", domain="d",
            relevance="core", evidence_ids=[1, 2, 3], confidence="high",
        )
        cm = ContextMap(research_question="rq", topics=[topic], version=1)
        out = context_map_extract_for_section(cm, ["t1"])
        # 預期看到「3 個來源支持」或類似 narrative 表達（數量資訊保留）
        assert "3" in out
        # 不直接列出 ID
        assert "[1, 2, 3]" not in out

    def test_extract_for_section_no_evidence_ids_still_works(self):
        """無 evidence_ids 的 topic（既有 case）行為不變。"""
        from reasoning.schemas_live import (
            ContextMap, ContextMapTopic, context_map_extract_for_section
        )
        topic = ContextMapTopic(
            topic_id="t1", name="X", domain="d",
            relevance="core", evidence_ids=[], confidence="medium",
        )
        cm = ContextMap(research_question="rq", topics=[topic], version=1)
        out = context_map_extract_for_section(cm, ["t1"])
        # 無 evidence → 不應出現 evidence 計數 block
        assert "個來源支持" not in out

    def test_context_map_to_summary_no_list_literal(self):
        from reasoning.schemas_live import (
            ContextMap, ContextMapTopic, context_map_to_summary
        )
        topic = ContextMapTopic(
            topic_id="t1", name="X", domain="d",
            relevance="core", evidence_ids=[1, 2, 3, 4, 5],
        )
        cm = ContextMap(research_question="rq", topics=[topic], version=1)
        out = context_map_to_summary(cm)
        assert "[1, 2, 3" not in out

    def test_context_map_to_summary_shows_evidence_count(self):
        from reasoning.schemas_live import (
            ContextMap, ContextMapTopic, context_map_to_summary
        )
        topic = ContextMapTopic(
            topic_id="t1", name="X", domain="d",
            relevance="core", evidence_ids=[10, 20, 30],
        )
        cm = ContextMap(research_question="rq", topics=[topic], version=1)
        out = context_map_to_summary(cm)
        # 保留數量訊息
        assert "3" in out
        # 不機械 dump ID
        assert "[10, 20, 30]" not in out


# ============================================================================
# O5-A: ConsistencyReview monitor_degraded 旗標 (Task 1)
# ============================================================================

def test_consistency_review_monitor_degraded_default_false():
    """monitor_degraded 預設為 False，不破壞既有建構路徑。"""
    cr = ConsistencyReview(
        drift_level="none",
        drift_description="方向一致",
        dubao_voice_message="進展順利",
        recommended_action="continue",
    )
    assert cr.monitor_degraded is False


def test_consistency_review_monitor_degraded_can_set_true():
    """monitor_degraded 可顯式設為 True（降級 fallback 用）。"""
    cr = ConsistencyReview(
        drift_level="none",
        drift_description="一致性檢查失敗，預設為無漂移",
        dubao_voice_message="",
        recommended_action="continue",
        monitor_degraded=True,
    )
    assert cr.monitor_degraded is True
