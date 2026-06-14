"""Task 3 (DR-parity): writer prompt 注入 KG 摘要（純字串組裝，無 LLM call）。"""
import pytest


def _make_kg():
    from reasoning.schemas_enhanced import (
        KnowledgeGraph, Entity, Relationship, EntityType, RelationType,
    )
    e1 = Entity(entity_id="e1", name="台積電", entity_type=EntityType.ORGANIZATION, description="半導體製造商")
    e2 = Entity(entity_id="e2", name="輝達", entity_type=EntityType.ORGANIZATION, description="GPU 設計商")
    rel = Relationship(
        source_entity_id="e1", target_entity_id="e2",
        relation_type=(
            RelationType.SUPPLIES_TO if hasattr(RelationType, "SUPPLIES_TO")
            else list(RelationType)[0]
        ),
        description="代工供應",
    )
    return KnowledgeGraph(entities=[e1, e2], relationships=[rel])


def test_kg_summary_injected_into_prompt():
    from reasoning.prompts.writer import WriterPromptBuilder
    builder = WriterPromptBuilder()
    kg = _make_kg()
    prompt = builder.build_section_compose_prompt(
        section_title="第一章",
        section_outline="大綱",
        relevant_findings="某發現 [1]",
        analyst_citations=[1],
        knowledge_graph=kg,
    )
    assert "台積電" in prompt
    assert "輝達" in prompt
    # KG 摘要明示不可作引用依據
    assert "不可作為引用依據" in prompt or "不可作為引用" in prompt


def test_kg_none_no_block():
    from reasoning.prompts.writer import WriterPromptBuilder
    builder = WriterPromptBuilder()
    prompt = builder.build_section_compose_prompt(
        section_title="第一章", section_outline="大綱",
        relevant_findings="某發現 [1]", analyst_citations=[1],
        knowledge_graph=None,
    )
    # 沒 KG 時不加 KG block（不出現 KG 標題）
    assert "實體關係背景" not in prompt


def test_all_compose_section_callsites_pass_kg():
    """M-3：orchestrator._write_section 的四個 compose_section callsite 全部帶 knowledge_graph=。

    AST 靜態驗（無 LLM）：normal / entity-rewrite / specificity-rewrite / synthesis-rewrite
    四個 callsite 任一漏帶 → rewrite 後 writer 掉 KG → 跨章一致性被破壞。涵蓋 rewrite branch。
    """
    import ast
    from pathlib import Path
    orch = (
        Path(__file__).resolve().parents[3]
        / "reasoning" / "live_research" / "orchestrator.py"
    )
    tree = ast.parse(orch.read_text(encoding="utf-8"))
    callsites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "compose_section":
                kw_names = {kw.arg for kw in node.keywords}
                callsites.append((node.lineno, "knowledge_graph" in kw_names))
    # 四個 callsite 全覆蓋
    assert len(callsites) == 4, f"預期 4 個 compose_section callsite，實得 {len(callsites)}: {callsites}"
    missing = [ln for ln, has_kg in callsites if not has_kg]
    assert not missing, f"以下 compose_section callsite 漏帶 knowledge_graph=: lines {missing}"


def test_compose_section_forwards_kg_to_builder():
    """compose_section（agent）把 knowledge_graph 透傳給 build_section_compose_prompt（無 LLM）。"""
    import inspect
    from reasoning.agents.writer import WriterAgent
    sig = inspect.signature(WriterAgent.compose_section)
    assert "knowledge_graph" in sig.parameters
