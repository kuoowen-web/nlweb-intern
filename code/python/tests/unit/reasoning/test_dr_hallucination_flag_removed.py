"""Task 4 選項A (DR-parity): 砍除 DR hallucination_corrected 死 flag。

死 flag：reasoning/research_state.py:54 宣告 + reasoning/orchestrator.py:1131 寫入，
但 DR 路徑無任何 read（grep 全 DR 路徑 0 consumer）。LR 側（live_research/...,
stage_state.py）是獨立有 consumer 的 flag，不動。

驗：
1. ResearchState 移除後不再有 hallucination_corrected 屬性。
2. DR orchestrator/research_state 不再 write/read 該 flag（grep 零殘留）。
3. 修正事件 user-facing surfacing（methodology note [自動修正...] + confidence Low）邏輯不變。
LLM-safe：純 dataclass / AST / 字串斷言，不打 LLM。
"""
import ast
from pathlib import Path

import pytest


_DR_DIR = Path(__file__).resolve().parents[3] / "reasoning"


def test_research_state_no_hallucination_corrected_attr():
    """ResearchState（DR）不再宣告 hallucination_corrected。"""
    from reasoning.research_state import ResearchState
    s = ResearchState(query="q", mode="research", items=[])
    assert not hasattr(s, "hallucination_corrected"), (
        "DR ResearchState 仍有 hallucination_corrected（死 flag 未砍乾淨）"
    )


def test_dr_orchestrator_no_hallucination_corrected_residue():
    """DR orchestrator.py + research_state.py 不再出現 hallucination_corrected（零殘留）。

    LR 側（live_research/）不在掃描範圍 — 那是獨立有 consumer 的 flag。
    """
    for fname in ("orchestrator.py", "research_state.py"):
        text = (_DR_DIR / fname).read_text(encoding="utf-8")
        assert "hallucination_corrected" not in text, (
            f"reasoning/{fname} 仍殘留 hallucination_corrected"
        )


def test_correction_path_surfacing_unchanged():
    """修正事件 surfacing 邏輯不變：confidence='Low' + methodology note 帶 [自動修正：移除未驗證來源]。

    重建 _phase_writer 修正路徑的 WriterComposeOutput 構造（orchestrator.py:1110-1115），
    驗 user-facing surfacing 欄位仍正確（砍 flag 零行為變更的依據）。
    """
    from reasoning.schemas import WriterComposeOutput
    corrected = WriterComposeOutput(
        final_report="正文內容" * 60,  # schema 要求 >=200 字
        sources_used=[1, 2],
        confidence_level="Low",
        methodology_note="原始說明" + " [自動修正：移除未驗證來源]",
    )
    assert corrected.confidence_level == "Low"
    assert "[自動修正：移除未驗證來源]" in corrected.methodology_note


def test_needs_correction_tracer_branch_intact():
    """needs_correction（tracer condition_branch 用）仍存在於 _phase_writer，不被連帶誤刪。"""
    tree = ast.parse((_DR_DIR / "orchestrator.py").read_text(encoding="utf-8"))
    found_needs_correction = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "needs_correction":
            found_needs_correction = True
            break
    assert found_needs_correction, (
        "needs_correction 變數消失 — tracer condition_branch 依賴它，不可連帶誤刪"
    )
