"""outline planner prompt 契約：user 未指定字數 → 教 LLM 回 target_word_count=0。

R1 (b) 根解：outline planner 不對「user 未指定字數」的章自動塞 default，
避免系統自塞的字數觸發任何字數處理（word overshoot 旁白）。
"""


def test_prompt_tells_llm_zero_when_no_word_budget():
    """user 沒指定字數時，prompt 必須教 LLM 回 target_word_count=0，不自動塞 default。"""
    from reasoning.prompts.outline_planner import build_outline_planner_prompt
    from reasoning.schemas_live import ContextMap, ContextMapTopic

    cm = ContextMap(
        topics=[ContextMapTopic(name="前言", domain="能源政策", description="d", evidence_ids=[1])],
        relations=[],
        research_question="q",
    )
    prompt = build_outline_planner_prompt(
        chapter_source=[{"name": "前言", "outline": "o"}],
        context_map=cm,
        format_specs={},  # 無任何字數要求
    )
    # SF5：不用鬆散 `"0" in prompt`（prompt 到處有 0）。改測具體指示片段：
    assert "不要腦補" in prompt
    assert "800-1500" not in prompt
    assert '"target_word_count": 800' not in prompt


def test_prompt_allocates_when_user_specified_total():
    """對照組：user 有指定總字數 → prompt 注入「使用者拍板總字數」，教 LLM 分配（非回 0）。"""
    from reasoning.prompts.outline_planner import build_outline_planner_prompt
    from reasoning.schemas_live import ContextMap, ContextMapTopic
    cm = ContextMap(
        topics=[ContextMapTopic(name="前言", domain="能源政策", description="d", evidence_ids=[1])],
        relations=[],
        research_question="q",
    )
    prompt = build_outline_planner_prompt(
        chapter_source=[{"name": "前言", "outline": "o"}],
        context_map=cm,
        format_specs={"target_word_count": 7000},  # user 指定總字數
    )
    assert "7000" in prompt
    assert "使用者拍板總字數" in prompt
