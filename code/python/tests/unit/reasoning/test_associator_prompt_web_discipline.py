"""Associator prompt: web source_strategy 紀律段 (Track C C2).

F-3 復驗 2026-05-28:
- method 名 `derive_search_plan_prompt`（**不是** `build_derive_prompt`）
- signature: (context_map_summary: str, executed_searches: List[str]) — 不收 ContextMap obj，
  收 markdown summary str（由 orchestrator 端用 context_map_to_summary 產出）
"""


def test_associator_prompt_contains_web_discipline_keywords():
    """source_strategy 紀律段含「國外案例 / 非台灣地名 / 國際組織」明確指引。"""
    from reasoning.prompts.associator import AssociatorPromptBuilder

    builder = AssociatorPromptBuilder()
    prompt = builder.derive_search_plan_prompt(
        context_map_summary="## 研究結構 (v0)\n核心議題: 德國能源轉型對台灣的啟示",
        executed_searches=[],
    )
    # 紀律必含的關鍵字
    assert "非台灣地名" in prompt or "國外案例" in prompt
    assert "國際組織" in prompt
    # source_strategy 三選一保留
    assert "internal" in prompt
    assert "web" in prompt
    assert "both" in prompt


def test_associator_prompt_few_shot_contains_web_example():
    """Propose-Verify few-shot 段含至少一個非台灣地名（web seed 範例）。"""
    from reasoning.prompts.associator import AssociatorPromptBuilder

    builder = AssociatorPromptBuilder()
    prompt = builder.derive_search_plan_prompt(
        context_map_summary="## 研究結構 (v0)\n核心議題: x",
        executed_searches=[],
    )
    # 既有 example 已含「德國 Energiewende」(associator.py:218) — 確保未被誤刪
    assert "Energiewende" in prompt or "Horns Rev" in prompt
