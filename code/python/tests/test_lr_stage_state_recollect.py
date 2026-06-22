# test_lr_stage_state_recollect.py
from reasoning.live_research.stage_state import LiveResearchStageState


def test_recollect_count_round_trip_and_old_session_default():
    # 新欄位 to_dict/from_dict 對稱
    state = LiveResearchStageState()
    state.recollect_count = 2
    state.pending_recollect_confirmation = True
    d = state.to_dict()
    assert d["recollect_count"] == 2
    assert d["pending_recollect_confirmation"] is True
    restored = LiveResearchStageState.from_dict(d)
    assert restored.recollect_count == 2
    assert restored.pending_recollect_confirmation is True
    # 舊 session（dict 無此欄位）→ fallback（recollect_count=0 / pending=False）
    old = LiveResearchStageState.from_dict({"current_stage": 5})
    assert old.recollect_count == 0
    assert old.pending_recollect_confirmation is False


def test_reset_for_recollect_clears_downstream_keeps_pool_and_audit():
    state = LiveResearchStageState()
    state.current_stage = 5
    state.stage_status = "checkpoint"
    state.checkpoint_prompt = "舊 checkpoint"
    state.failed_intent_parse_count = 3
    state.completed_sections = ["topic-0", "topic-1"]
    state.written_sections = [{"section_index": 0, "title": "舊章"}]
    state.last_completed_section_index = 2
    state.book_outline_json = '{"old": "outline"}'
    state.format_specs = {"chapters": [{"name": "舊章", "outline": "x"}], "fmt": "keep"}
    state.pending_reframe_json = '{"phantom": true}'
    state.pending_reframe_proposal_markdown = "舊提案"
    state.pending_format_confirmation = True
    state.hallucination_corrected = True
    state.stage_5_writer_running = True
    state.stage5_waiting_for_user = True
    state.executed_searches = ["q1", "q2"]
    state.evidence_usage = {1: [{"claim": "x"}]}
    state.knowledge_graph = None  # 清為 None case
    state.critic_section_reviews = {0: {"verdict": "REJECT"}}
    state.user_voice.revise_instructions = {0: ["改一下"]}
    state.user_voice.citation_style = "numeric"
    state.pending_recollect_confirmation = True  # G：reset 必須清此 guard
    # 保留類
    state.evidence_pool_json = '{"1": {"keep": "me"}}'
    state.context_map_json = '{"rq": "保留"}'
    state.style_features_json = '{"style": "keep"}'
    state.rejected_claims_log = [{"topic_id": "t0"}]
    state.consistency_drift_log = [{"drift_level": "none"}]
    state.recollect_count = 1

    state.reset_for_recollect()

    # 退回 Stage 1
    assert state.current_stage == 1
    assert state.stage_status == "in_progress"
    assert state.checkpoint_prompt == ""
    # 過期下游輸出全清
    assert state.completed_sections == []
    assert state.written_sections == []
    assert state.last_completed_section_index == -1
    assert state.book_outline_json == ""
    assert state.executed_searches == []
    assert state.failed_intent_parse_count == 0
    # format override 只清 chapters，保留其他 format key
    assert "chapters" not in state.format_specs
    assert state.format_specs.get("fmt") == "keep"
    # 幽靈 guard 全清
    assert state.pending_reframe_json == ""
    assert state.pending_reframe_proposal_markdown == ""
    assert state.pending_format_confirmation is False
    assert state.hallucination_corrected is False
    assert state.stage_5_writer_running is False
    assert state.stage5_waiting_for_user is False
    # 推理產物清（重生成）
    assert state.evidence_usage == {}
    assert state.knowledge_graph is None
    assert state.critic_section_reviews == {}
    assert state.user_voice.revise_instructions == {}
    # G：pending recollect confirm guard 清（不清 → 下輪 Stage5 回覆被錯誤攔截）
    assert state.pending_recollect_confirmation is False
    # evidence pool + 不變設定保留
    assert state.evidence_pool_json == '{"1": {"keep": "me"}}'
    assert state.context_map_json == '{"rq": "保留"}'
    assert state.style_features_json == '{"style": "keep"}'
    assert state.user_voice.citation_style == "numeric"
    # audit append-only 保留
    assert state.rejected_claims_log == [{"topic_id": "t0"}]
    assert state.consistency_drift_log == [{"drift_level": "none"}]
    # recollect_count 不在 reset 內清（cap 計數靠它）
    assert state.recollect_count == 1


def test_reset_for_recollect_does_not_pollute_prior_to_dict_snapshot():
    """C-1（in-house+Gemini）：to_dict() 對 format_specs 是淺引用（stage_state.py:283
    `"format_specs": self.format_specs`，非 copy）。若 reset 對 format_specs 做 in-place
    pop，會污染 reset **之前** 取的 snapshot（_dispatch_recollect rollback 用它）。
    rebind 修法須讓 snapshot 的 chapters 不被 reset 動到。"""
    state = LiveResearchStageState()
    state.format_specs = {"chapters": [{"name": "舊章", "outline": "x"}], "fmt": "keep"}
    # 模擬 _dispatch_recollect：reset 前先取 snapshot（rollback 用）
    snapshot = state.to_dict()
    state.reset_for_recollect()
    # reset 後 live state 的 chapters 已清
    assert "chapters" not in state.format_specs
    # 但 reset **不可**污染先前取的 snapshot —— rollback 才能完整還原 chapters
    assert "chapters" in snapshot["format_specs"]
    assert snapshot["format_specs"]["chapters"] == [{"name": "舊章", "outline": "x"}]
    # round-trip 還原 snapshot → chapters 回來（rollback 語意）
    restored = LiveResearchStageState.from_dict(snapshot)
    assert restored.format_specs.get("chapters") == [{"name": "舊章", "outline": "x"}]


def test_state_all_fields_round_trip():
    """Codex Minor-1：全 state 欄位 to_dict→from_dict round-trip 回歸測試。
    rollback（_dispatch_recollect）依賴 to_dict/from_dict 對稱，未來新增欄位若漏進
    to_dict/from_dict 會默默 rollback 不完整 → 此 test 守住對稱性。
    作法：對一個非 default 值的 state 做 round-trip，逐欄位比對 to_dict 輸出一致。"""
    state = LiveResearchStageState()
    state.current_stage = 5
    state.recollect_count = 1
    state.pending_recollect_confirmation = True
    state.written_sections = [{"section_index": 0, "title": "x"}]
    state.format_specs = {"chapters": [{"name": "c"}], "fmt": "keep"}
    state.executed_searches = ["q1"]
    state.evidence_usage = {1: [{"claim": "x"}]}
    state.critic_section_reviews = {0: {"verdict": "REJECT"}}
    state.rejected_claims_log = [{"topic_id": "t0"}]
    state.consistency_drift_log = [{"drift_level": "none"}]
    before = state.to_dict()
    after = LiveResearchStageState.from_dict(before).to_dict()
    # 全欄位 key 集合一致（新增欄位漏 serialize → key 缺 → 失敗）
    assert set(before.keys()) == set(after.keys()), (
        f"to_dict/from_dict 欄位不對稱: "
        f"only-before={set(before)-set(after)}, only-after={set(after)-set(before)}"
    )
    # 每個欄位值 round-trip 後一致
    for k in before:
        assert before[k] == after[k], f"欄位 {k} round-trip 不一致"
