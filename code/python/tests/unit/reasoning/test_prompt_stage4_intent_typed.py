"""Target 1 Phase 1.3 — Stage 4 intent prompt typed few-shot tests.

Plan: lr-typeagent-refactor (2026-05-19, CEO 拍板 OQ-2 — 不加 keyword 兜底，
靠 typed few-shot 強化 LLM 對齊）。
"""


def test_prompt_few_shot_uses_typed_chapter_spec():
    """few-shot 範例使用 typed ChapterSpec JSON（含 type literal）。"""
    from reasoning.prompts.stage4_intent import Stage4IntentPromptBuilder
    builder = Stage4IntentPromptBuilder()
    prompt = builder.build_intent_classifier_prompt("test")
    # 範例必須含 typed JSON
    assert (
        '"type":"narrative_chapter"' in prompt
        or '"type": "narrative_chapter"' in prompt
    ), "prompt 缺 typed narrative_chapter JSON 範例"


def test_prompt_few_shot_uses_typed_special_element():
    """few-shot 範例使用 typed SpecialElementSpec JSON（含 type enum 值）。"""
    from reasoning.prompts.stage4_intent import Stage4IntentPromptBuilder
    builder = Stage4IntentPromptBuilder()
    prompt = builder.build_intent_classifier_prompt("test")
    assert '"type":"table"' in prompt or '"type": "table"' in prompt


def test_prompt_has_mixed_reframe_typed_example():
    """CEO 拍板 OQ-2：強化 few-shot — 「五章 + 比較表」必須在 prompt 內示範。"""
    from reasoning.prompts.stage4_intent import Stage4IntentPromptBuilder
    builder = Stage4IntentPromptBuilder()
    prompt = builder.build_intent_classifier_prompt("test")
    # 正面範例：五章 + 比較表
    assert "比較表" in prompt
    assert "narrative_chapter" in prompt


def test_prompt_has_negative_example_for_element_not_chapter():
    """OQ-2 強化：反面範例「不應該把比較表當 chapter」明示給 LLM 看。"""
    from reasoning.prompts.stage4_intent import Stage4IntentPromptBuilder
    builder = Stage4IntentPromptBuilder()
    prompt = builder.build_intent_classifier_prompt("test")
    # 反面範例字眼 — 明示 LLM「不可」把 element 升為 chapter
    assert (
        "不可" in prompt or "❌" in prompt or "錯誤" in prompt
    ), "prompt 缺反面範例 explicit guard"
