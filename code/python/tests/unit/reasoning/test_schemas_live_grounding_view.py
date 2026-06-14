import pytest

from reasoning.schemas_live import render_grounding_evidence_view


class _Entry:
    def __init__(self, title, snippet):
        self.title = title
        self.snippet = snippet


def test_grounding_view_does_not_truncate_snippet():
    long_snippet = "台灣電力公司" + "X" * 400  # 遠超 200
    pool = {3: _Entry("能源政策", long_snippet)}
    usage = {3: [{"claim": "台電推綠能", "reasoning_type": "induction",
                  "confidence": "high", "critic_status": "PASS"}]}
    view = render_grounding_evidence_view(
        chapter_eids=[3], evidence_usage=usage, evidence_pool=pool,
        prior_grounded_entities=[],
    )
    # 不截斷：完整 snippet（含第 300 字）要在視圖內
    assert ("X" * 400) in view
    assert "台灣電力公司" in view


def test_grounding_view_includes_prior_grounded_entities():
    pool = {3: _Entry("能源", "台塩綠能案場")}
    usage = {3: [{"claim": "c", "reasoning_type": "induction",
                  "confidence": "high", "critic_status": "PASS"}]}
    view = render_grounding_evidence_view(
        chapter_eids=[3], evidence_usage=usage, evidence_pool=pool,
        prior_grounded_entities=["台灣電力公司", "嘉義縣"],
    )
    # 前章已 grounded 的 entity 要進視圖（讓 LLM 判同義改寫如「台電」grounded）
    assert "台灣電力公司" in view
    assert "嘉義縣" in view


def test_grounding_view_filters_reject_claims():
    pool = {3: _Entry("t", "s")}
    usage = {3: [{"claim": "bad", "reasoning_type": "induction",
                  "confidence": "low", "critic_status": "REJECT"}]}
    view = render_grounding_evidence_view(
        chapter_eids=[3], evidence_usage=usage, evidence_pool=pool,
        prior_grounded_entities=[],
    )
    # REJECT claim 不渲染（與 render_grounded_narrative 同口徑）；但 evidence 標題仍在
    assert "bad" not in view


def test_grounding_view_respects_char_budget():
    """R2：超 budget → 截斷，render 長度不超上限（含 break 註記容差）。"""
    big = "電力" * 5000  # 單筆 10000 字
    pool = {1: _Entry("a", big), 2: _Entry("b", big), 3: _Entry("c", big)}
    usage = {}
    view = render_grounding_evidence_view(
        chapter_eids=[1, 2, 3], evidence_usage=usage, evidence_pool=pool,
        prior_grounded_entities=[], char_budget=12000,
    )
    # 3 筆 × 10000 字遠超 12000 → 必截斷；長度 ≈ budget（容 break 註記 + 一個 block 容差）
    assert len(view) <= 12000 + 200
    assert "context budget" in view  # break 註記出現


def test_grounding_view_priority_keeps_cited_evidence():
    """R2：本章 analyst_citations 對應 evidence = 最高優先，budget 緊時優先保留。"""
    big = "X" * 11000
    pool = {1: _Entry("untitled-1", big), 2: _Entry("CITED-章內引用", "台鹽綠能案場")}
    view = render_grounding_evidence_view(
        chapter_eids=[1, 2], evidence_usage={}, evidence_pool=pool,
        prior_grounded_entities=[], analyst_citations=[2], char_budget=12000,
    )
    # eid 2 是本章 citation（tier 1）→ 即使 eid 1 龐大也要先裝 eid 2
    assert "CITED-章內引用" in view
    assert "台鹽綠能案場" in view


@pytest.mark.parametrize("n", [100, 200, 500])
def test_grounding_view_large_pool(n):
    """R6 production-scale：100/200/500 筆 evidence → 驗 R2 budget cap 真生效
    （render 長度不超上限、本章 citation 優先保留、超量正確截斷）。
    這是本次修改最大風險來源（配合 search→8 筆後 pool 膨脹），必須有 production-scale test。"""
    # 每筆 snippet ~600 字 → n=100 已 ~60000 字遠超 12000 budget，必觸發截斷
    pool = {i: _Entry(f"來源{i}", f"內容片段{i} " + "詳述內容" * 150) for i in range(n)}
    usage = {0: [{"claim": "c0", "reasoning_type": "induction",
                  "confidence": "high", "critic_status": "PASS"}]}
    # 把一筆高 eid 設為本章 citation → 驗它即使排在後面也因 tier 1 被優先裝進 budget
    cited_eid = n - 1
    pool[cited_eid] = _Entry("章內引用來源", "台灣電力公司推動再生能源")
    view = render_grounding_evidence_view(
        chapter_eids=list(pool.keys()), evidence_usage=usage, evidence_pool=pool,
        prior_grounded_entities=[], analyst_citations=[cited_eid], char_budget=12000,
    )
    # 1) budget cap 生效：長度不超上限（容 break 註記 + 一 block 容差）
    assert len(view) <= 12000 + 500
    # 2) 本章 citation（tier 1）優先保留，不被龐大 pool 擠掉
    assert "台灣電力公司推動再生能源" in view
    # 3) 超量正確截斷：500 筆遠超 budget → 出現省略註記
    assert "context budget" in view
