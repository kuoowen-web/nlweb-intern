"""P2 W6：writer prompt 白名單放寬全 pool + evidence_block 迭代全 pool + L684 降級判定.

W6（最致命殘留）：
- prompt 白名單範圍 = evidence_lookup.keys()（全 pool），analyst_citations 降「優先/建議」。
- evidence_block 改迭代 evidence_lookup.keys()（★ 標 analyst_citations 優先），非 sorted(analyst_citations)。
- L684 降級判定 not analyst_citations → not evidence_lookup（C1：全 pool 有料不該誤判資料不足）。
"""
import pytest


def test_writer_prompt_whitelist_and_evidence_block_full_pool():
    from reasoning.prompts.writer import WriterPromptBuilder
    from reasoning.schemas_live import EvidencePoolEntry
    lookup = {i: EvidencePoolEntry(evidence_id=i, title=f"T{i}", snippet="s")
              for i in range(1, 11)}
    prompt = WriterPromptBuilder().build_section_compose_prompt(
        section_title="X", section_outline="o", relevant_findings="f",
        analyst_citations=[1, 2],            # 只 2 筆優先
        evidence_lookup=lookup,              # 全 pool 10 筆
        citation_format="numeric",
    )
    assert "必須是 analyst_citations 的子集" not in prompt   # 舊白名單措辭移除
    assert "T7" in prompt and "T10" in prompt               # evidence_block 含全 pool 來源（非只 1,2）
    assert "10" in prompt                                    # 白名單範圍 = 全 pool max ID


def test_writer_prompt_no_data_insufficiency_when_pool_nonempty():
    # C1（§0 #22）：analyst_citations 空但 evidence_lookup（全 pool）有料 →
    # 不該注入「本章資料不足」降級 block（writer 要用全 pool 寫具體內容）
    from reasoning.prompts.writer import WriterPromptBuilder
    from reasoning.schemas_live import EvidencePoolEntry
    lookup = {i: EvidencePoolEntry(evidence_id=i, title=f"T{i}", snippet="s")
              for i in range(1, 4)}
    prompt = WriterPromptBuilder().build_section_compose_prompt(
        section_title="X", section_outline="o", relevant_findings="f",
        analyst_citations=[],                # 空優先 tier
        evidence_lookup=lookup,              # 全 pool 有料
        citation_format="numeric",
    )
    assert "本章資料不足" not in prompt        # 不走降級 narration


def test_writer_prompt_data_insufficiency_when_pool_truly_empty():
    # 全 pool 與 grounding 都空 → 仍走「資料不足」降級（fail-loud，不可硬塞）
    from reasoning.prompts.writer import WriterPromptBuilder
    prompt = WriterPromptBuilder().build_section_compose_prompt(
        section_title="X", section_outline="o", relevant_findings="",
        analyst_citations=[], evidence_lookup={}, citation_format="numeric",
    )
    assert "本章資料不足" in prompt


def test_writer_prompt_evidence_block_stars_analyst_citations():
    """evidence_block 用 ★ 標 analyst_citations 為優先建議（軟引導），仍渲全 pool。"""
    from reasoning.prompts.writer import WriterPromptBuilder
    from reasoning.schemas_live import EvidencePoolEntry
    lookup = {i: EvidencePoolEntry(evidence_id=i, title=f"T{i}", snippet="s")
              for i in range(1, 6)}
    prompt = WriterPromptBuilder().build_section_compose_prompt(
        section_title="X", section_outline="o", relevant_findings="f",
        analyst_citations=[2],
        evidence_lookup=lookup,
        citation_format="numeric",
    )
    assert "★" in prompt                     # 優先建議標記
    # 全 pool 來源都在
    for i in range(1, 6):
        assert f"T{i}" in prompt
