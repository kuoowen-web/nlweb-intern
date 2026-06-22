# test_lr_reset_to_stage.py — reset_to_stage(target) 清除真值表回歸
from reasoning.live_research.stage_state import LiveResearchStageState


def _fully_populated_state():
    s = LiveResearchStageState()
    s.current_stage = 5
    s.stage_status = "checkpoint"
    s.checkpoint_prompt = "舊 checkpoint"
    s.failed_intent_parse_count = 3
    s.completed_sections = ["topic-0"]
    s.written_sections = [{"section_index": 0, "title": "舊章"}]
    s.last_completed_section_index = 2
    s.book_outline_json = '{"old": "outline"}'
    s.style_features_json = '{"style": "keep-or-clear"}'
    s.executed_searches = ["q1", "q2"]
    s.format_specs = {"chapters": [{"name": "舊章"}], "fmt": "keep"}
    s.pending_reframe_json = '{"phantom": true}'
    s.pending_reframe_proposal_markdown = "舊提案"
    s.pending_format_confirmation = True
    s.hallucination_corrected = True
    s.stage_5_writer_running = True
    s.stage5_waiting_for_user = True
    s.pending_recollect_confirmation = True
    s.evidence_usage = {1: [{"claim": "x"}]}
    s.critic_section_reviews = {0: {"verdict": "REJECT"}}
    s.user_voice.revise_instructions = {0: ["改一下"]}
    # 保留類
    s.evidence_pool_json = '{"1": {"keep": "me"}}'
    s.context_map_json = '{"rq": "保留"}'
    s.initial_context_map_json = '{"rq": "保留-init"}'
    s.user_voice.citation_style = "numeric"
    s.rejected_claims_log = [{"topic_id": "t0"}]
    s.consistency_drift_log = [{"drift_level": "none"}]
    s.recollect_count = 1
    return s


def test_reset_to_stage_1_equals_restart_keeps_evidence_and_context():
    s = _fully_populated_state()
    s.reset_to_stage(1)
    assert s.current_stage == 1
    assert s.stage_status == "in_progress"
    # #3 復用 evidence + context：restart 保留 pool + context_map
    assert s.evidence_pool_json == '{"1": {"keep": "me"}}'
    assert s.context_map_json == '{"rq": "保留"}'
    # Stage 2+ 輸出清（#4 清 Stage 2+，保留 evidence pool）
    assert s.style_features_json == ""
    assert s.written_sections == []
    assert s.book_outline_json == ""
    assert s.last_completed_section_index == -1
    assert s.executed_searches == []
    # 7 個 guard 欄位（#1）全清
    assert s.pending_reframe_json == ""
    assert s.pending_reframe_proposal_markdown == ""
    assert s.pending_format_confirmation is False
    assert s.stage5_waiting_for_user is False
    # format chapters 清、其他 format key 保留
    assert "chapters" not in s.format_specs
    assert s.format_specs.get("fmt") == "keep"
    # cap 計數 / audit 保留
    assert s.recollect_count == 1
    assert s.rejected_claims_log == [{"topic_id": "t0"}]


def test_reset_to_stage_3_keeps_style_and_searches():
    s = _fully_populated_state()
    s.reset_to_stage(3)
    assert s.current_stage == 3
    # target=3：style_features + executed_searches 保留（Stage 3 是 target，含以上保留）
    assert s.style_features_json == '{"style": "keep-or-clear"}'
    assert s.executed_searches == ["q1", "q2"]
    # Stage 4+ 輸出清
    assert s.book_outline_json == ""
    assert s.written_sections == []
    assert "chapters" not in s.format_specs
    # guard 全清
    assert s.pending_reframe_json == ""
    assert s.pending_format_confirmation is False


def test_reset_to_stage_4_clears_book_outline_for_consumer_risk():
    # REVISE #4：退回 Stage 4 改大綱 → 必清 book_outline_json，
    # 否則重入 Stage 5 `if not state.book_outline_json:` 用舊大綱
    s = _fully_populated_state()
    s.reset_to_stage(4)
    assert s.current_stage == 4
    assert s.book_outline_json == ""
    assert s.written_sections == []
    assert s.style_features_json == '{"style": "keep-or-clear"}'  # Stage 3 保留


def test_reset_to_stage_does_not_pollute_prior_snapshot():
    # C-1 同 reset_for_recollect：format_specs rebind 不污染先前 to_dict 淺引用
    s = LiveResearchStageState()
    s.format_specs = {"chapters": [{"name": "舊章"}], "fmt": "keep"}
    snapshot = s.to_dict()
    s.reset_to_stage(1)
    assert "chapters" not in s.format_specs
    assert "chapters" in snapshot["format_specs"]
