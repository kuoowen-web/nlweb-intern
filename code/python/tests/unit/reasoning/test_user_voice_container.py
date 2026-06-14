"""UserVoice dataclass 與 LiveResearchStageState.user_voice 接線測試（Phase 1）。

Plan: lr-user-voice-container-and-4-fixes-plan.md
CEO OQ 2 拍板：revise_instructions: Dict[int, List[str]]（accumulate，同段多次改保留歷史）
"""
import pytest
from reasoning.live_research.stage_state import (
    LiveResearchStageState,
    UserVoice,
)


class TestUserVoiceDefaults:
    def test_default_factory_yields_empty_voice(self):
        v = UserVoice()
        assert v.citation_style is None
        assert v.stage2_feedback == []
        assert v.revise_instructions == {}

    def test_state_default_user_voice_is_empty(self):
        s = LiveResearchStageState()
        assert isinstance(s.user_voice, UserVoice)
        assert s.user_voice.citation_style is None
        assert s.user_voice.stage2_feedback == []
        assert s.user_voice.revise_instructions == {}


class TestUserVoiceRoundtrip:
    def test_roundtrip_empty(self):
        s = LiveResearchStageState()
        restored = LiveResearchStageState.from_dict(s.to_dict())
        assert restored.user_voice.citation_style is None
        assert restored.user_voice.stage2_feedback == []
        assert restored.user_voice.revise_instructions == {}

    def test_roundtrip_with_all_fields(self):
        """OQ 2 拍板：revise_instructions 是 Dict[int, List[str]]，accumulate。"""
        s = LiveResearchStageState()
        s.user_voice.citation_style = "author_year"
        s.user_voice.stage2_feedback.append({"round": "0", "text": "補 Greenpeace"})
        s.user_voice.revise_instructions[3] = ["第 3 段太短，補 IAEA"]
        restored = LiveResearchStageState.from_dict(s.to_dict())
        assert restored.user_voice.citation_style == "author_year"
        assert restored.user_voice.stage2_feedback == [
            {"round": "0", "text": "補 Greenpeace"}
        ]
        assert restored.user_voice.revise_instructions == {3: ["第 3 段太短，補 IAEA"]}

    def test_int_key_serialize_then_restore(self):
        """revise_instructions int key → JSON str → int 還原（List[str] value 保留順序）。"""
        s = LiveResearchStageState()
        s.user_voice.revise_instructions[0] = ["a"]
        s.user_voice.revise_instructions[10] = ["b", "c"]
        d = s.to_dict()
        # JSON 化後 key 是 str
        assert d["user_voice"]["revise_instructions"] == {"0": ["a"], "10": ["b", "c"]}
        restored = LiveResearchStageState.from_dict(d)
        # restore 回 int key
        assert restored.user_voice.revise_instructions == {0: ["a"], 10: ["b", "c"]}

    def test_accumulate_same_section_two_instructions(self):
        """OQ 2 acceptance：同段改 2 次 → list 長度 = 2，順序保留。"""
        s = LiveResearchStageState()
        # 第一次 revise
        s.user_voice.revise_instructions.setdefault(2, []).append("太短，補數據")
        # 第二次 revise 同段
        s.user_voice.revise_instructions.setdefault(2, []).append("改太長了，刪一半")
        assert len(s.user_voice.revise_instructions[2]) == 2
        assert s.user_voice.revise_instructions[2][0] == "太短，補數據"
        assert s.user_voice.revise_instructions[2][1] == "改太長了，刪一半"
        # roundtrip 後保留
        restored = LiveResearchStageState.from_dict(s.to_dict())
        assert restored.user_voice.revise_instructions[2] == [
            "太短，補數據", "改太長了，刪一半"
        ]


class TestUserVoiceTargetWordCount:
    """Blocker A root fix (2026-05-19)：UserVoice.target_word_count typed channel。"""

    def test_default_target_word_count_is_none(self):
        v = UserVoice()
        assert v.target_word_count is None

    def test_state_default_target_word_count_is_none(self):
        s = LiveResearchStageState()
        assert s.user_voice.target_word_count is None

    def test_roundtrip_with_target_word_count(self):
        s = LiveResearchStageState()
        s.user_voice.target_word_count = 5000
        d = s.to_dict()
        assert d["user_voice"]["target_word_count"] == 5000
        restored = LiveResearchStageState.from_dict(d)
        assert restored.user_voice.target_word_count == 5000

    def test_from_dict_missing_target_word_count(self):
        """舊 row 沒 target_word_count → 預設 None（backward compat）。"""
        old_dict = {"user_voice": {"citation_style": "author_year"}}
        s = LiveResearchStageState.from_dict(old_dict)
        assert s.user_voice.target_word_count is None
        assert s.user_voice.citation_style == "author_year"

    def test_from_dict_dirty_target_word_count_string(self):
        """容錯：string number → cast int；非數字 → None。"""
        s1 = LiveResearchStageState.from_dict(
            {"user_voice": {"target_word_count": "5000"}}
        )
        assert s1.user_voice.target_word_count == 5000
        s2 = LiveResearchStageState.from_dict(
            {"user_voice": {"target_word_count": "abc"}}
        )
        assert s2.user_voice.target_word_count is None

    def test_from_dict_dirty_target_word_count_zero_or_negative(self):
        """容錯：0 / 負數 → None（schema 要求 >= 1）。"""
        s0 = LiveResearchStageState.from_dict(
            {"user_voice": {"target_word_count": 0}}
        )
        assert s0.user_voice.target_word_count is None
        s_neg = LiveResearchStageState.from_dict(
            {"user_voice": {"target_word_count": -100}}
        )
        assert s_neg.user_voice.target_word_count is None


class TestStage4FormatPayloadTargetWordCount:
    """Blocker A root fix (2026-05-19)：Stage4FormatPayload.target_word_count 欄位。

    User reply「APA 引用格式，五千字左右」→ LLM output mixed payload
    (citation_style_extracted + target_word_count) → schema validation pass。
    舊 fixture 沒此欄位 → default None backward compat。
    """

    def test_payload_accepts_target_word_count(self):
        from reasoning.schemas_live import Stage4FormatPayload
        p = Stage4FormatPayload(target_word_count=5000)
        assert p.target_word_count == 5000

    def test_payload_target_word_count_default_none(self):
        from reasoning.schemas_live import Stage4FormatPayload
        p = Stage4FormatPayload()
        assert p.target_word_count is None

    def test_payload_apa_plus_five_thousand_words(self):
        """User reply「APA 引用格式，五千字左右」典型 mixed payload。"""
        from reasoning.schemas_live import Stage4FormatPayload
        p = Stage4FormatPayload(
            citation_style_extracted="author_year",
            target_word_count=5000,
        )
        assert p.citation_style_extracted == "author_year"
        assert p.target_word_count == 5000
        assert p.special_elements == []

    def test_payload_target_word_count_zero_rejected(self):
        """target_word_count=0 違反 ge=1 → ValidationError。"""
        from pydantic import ValidationError
        from reasoning.schemas_live import Stage4FormatPayload
        with pytest.raises(ValidationError):
            Stage4FormatPayload(target_word_count=0)

    def test_response_adjust_format_with_word_count(self):
        """Stage4Response action=adjust_format + word count 合法。"""
        from reasoning.schemas_live import (
            Stage4Response, Stage4ResponseAction, Stage4FormatPayload,
        )
        r = Stage4Response(
            action=Stage4ResponseAction.adjust_format,
            format_content=Stage4FormatPayload(
                citation_style_extracted="author_year",
                target_word_count=5000,
            ),
        )
        assert r.action == Stage4ResponseAction.adjust_format
        assert r.format_content.target_word_count == 5000

    def test_payload_legacy_fixture_no_word_count_field(self):
        """舊 fixture / dict 沒 target_word_count → model_validate default None。"""
        from reasoning.schemas_live import Stage4FormatPayload
        # 模擬舊 fixture / DB row 沒新欄位
        legacy_dict = {
            "format_spec_extracted": "每段 500 字",
            "citation_style_extracted": "numeric",
            "special_elements": [],
        }
        p = Stage4FormatPayload.model_validate(legacy_dict)
        assert p.target_word_count is None


class TestOutlinePlannerTargetWordCountInjection:
    """Blocker A Phase 3 (2026-05-19)：outline planner prompt 接收 target_word_count
    作為 word budget hint，給 LLM 分配各章 target_word_count。"""

    def test_format_format_specs_includes_word_budget(self):
        """format_specs.target_word_count → prompt 含「使用者拍板總字數：約 N 字」hint。"""
        from reasoning.prompts.outline_planner import _format_format_specs
        out = _format_format_specs({"target_word_count": 5000})
        assert "5000" in out
        assert "budget" in out
        assert "總字數" in out

    def test_format_format_specs_no_budget_when_missing(self):
        from reasoning.prompts.outline_planner import _format_format_specs
        out = _format_format_specs({"user_specified": "APA"})
        assert "budget" not in out

    def test_format_format_specs_ignores_invalid_word_count(self):
        """非 int / <1 → 不注入 budget hint（容錯）。"""
        from reasoning.prompts.outline_planner import _format_format_specs
        for bad in (0, -100, "abc", None, "5000"):
            out = _format_format_specs({"target_word_count": bad})
            assert "budget" not in out, f"bad={bad!r} produced: {out!r}"

    def test_format_format_specs_combined_with_chapters(self):
        """user_specified + chapters + target_word_count 三項都注入。"""
        from reasoning.prompts.outline_planner import _format_format_specs
        out = _format_format_specs({
            "user_specified": "APA",
            "chapters": [{"name": "前言"}, {"name": "結論"}],
            "target_word_count": 7000,
        })
        assert "APA" in out
        assert "章節數：2" in out
        assert "7000" in out


class TestUserVoiceBackwardCompat:
    def test_from_dict_missing_user_voice_key(self):
        """舊 row 沒 user_voice key → 預設空 UserVoice。"""
        old_dict = {"current_stage": 1, "stage_status": "in_progress"}
        s = LiveResearchStageState.from_dict(old_dict)
        assert isinstance(s.user_voice, UserVoice)
        assert s.user_voice.citation_style is None

    def test_from_dict_null_user_voice(self):
        """user_voice = None（顯式 null）→ 預設空 UserVoice。"""
        old_dict = {"current_stage": 1, "user_voice": None}
        s = LiveResearchStageState.from_dict(old_dict)
        assert isinstance(s.user_voice, UserVoice)

    def test_from_dict_dirty_citation_style(self):
        """citation_style 髒資料（非 enum）→ None（容錯）。"""
        old_dict = {"user_voice": {"citation_style": "APA-7"}}
        s = LiveResearchStageState.from_dict(old_dict)
        assert s.user_voice.citation_style is None

    def test_from_dict_dirty_revise_key(self):
        """revise_instructions 非 int 可解 key → skip 該 entry。"""
        old_dict = {"user_voice": {"revise_instructions": {"abc": ["x"], "2": ["y"]}}}
        s = LiveResearchStageState.from_dict(old_dict)
        assert s.user_voice.revise_instructions == {2: ["y"]}

    def test_from_dict_legacy_revise_str_value(self):
        """Backward compat：舊版 schema value 是 str（不是 list）→ 自動包成 [str]。"""
        old_dict = {"user_voice": {"revise_instructions": {"2": "舊版字串"}}}
        s = LiveResearchStageState.from_dict(old_dict)
        assert s.user_voice.revise_instructions == {2: ["舊版字串"]}

    def test_existing_state_keys_still_work(self):
        """既有 state field（format_specs / context_map_json 等）roundtrip 不受影響。"""
        s = LiveResearchStageState()
        s.format_specs = {"user_specified": "APA"}
        s.context_map_json = "{}"
        s.last_completed_section_index = 2
        restored = LiveResearchStageState.from_dict(s.to_dict())
        assert restored.format_specs == {"user_specified": "APA"}
        assert restored.context_map_json == "{}"
        assert restored.last_completed_section_index == 2
