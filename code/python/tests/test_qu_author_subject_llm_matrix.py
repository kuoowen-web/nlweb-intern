# -*- coding: utf-8 -*-
"""QU author/subject 判定真 LLM 矩陣（票 2026-07-28-e）。

覆蓋 QueryUnderstanding prompt 第 3 節（作者/記者偵測）的 LLM 判定路徑——
regex fast path 之外唯一的 author 判定來源（query_understanding.py:333-342）。
矩陣繞過 regex，以 _build_hints 無命中時的原文餵 prompt，直接鎖 LLM 行為
（lessons-live-research B1：prompt 行為改動必真 LLM 矩陣驗，字串檢查證不了 LLM 行為）。

Gate：燒真 LLM 錢（33 案 × level=low ≈ US$0.02/輪，紅+綠全程 << US$1）。
設 NLWEB_ALLOW_REAL_LLM=1 才跑，CI 不收（慣例對齊 tests/test_llm_api_decisions.py）。

Run: NLWEB_ALLOW_REAL_LLM=1 pytest tests/test_qu_author_subject_llm_matrix.py -v

紀律（plan D6）：不 retry、不放寬斷言。同案兩輪獨立跑判定不穩 = prompt 訊號不足，
stop-and-report 回 plan 層補訊號，不得砍案例或改期望交差。
"""
import asyncio
import os
from datetime import datetime

import pytest

from core.llm import ask_llm
from core.prompts import fill_prompt, find_prompt

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(
        os.environ.get("NLWEB_ALLOW_REAL_LLM") != "1",
        reason="燒真 LLM 錢（矩陣 ≈ US$0.02/輪），設 NLWEB_ALLOW_REAL_LLM=1 才跑",
    ),
]

ITEM_TYPE = "{http://nlweb.ai/base}Item"
# 對齊 query_understanding.py:_build_hints 無 regex 命中時的原文（:290）
NO_REGEX_HINT = "Regex 預分析：無高信度結果，請完整分析所有欄位。"


def _judge(query: str):
    """取 default site 的 QueryUnderstanding prompt（= prod site=all 經
    prompt_default_fallback 實際用的同一份），填變數後真 LLM 判定。

    fill_prompt 的 pr_dict 覆蓋全部三個變數；若未來 prompt 新增變數而 pr_dict
    未覆蓋，handler=None 會 AttributeError fail-loud（刻意，不 silent 漏填）。
    """
    prompt_str, ans_struc = find_prompt("default", ITEM_TYPE, "QueryUnderstanding")
    assert prompt_str is not None, "QueryUnderstanding prompt 不可達"
    filled = fill_prompt(prompt_str, None, {
        "system.current_date": datetime.now().strftime("%Y-%m-%d"),
        "request.query": query,
        "system.query_analysis_hints": NO_REGEX_HINT,
    })
    resp = asyncio.run(ask_llm(filled, ans_struc, level="low",
                               timeout=30, max_length=1024))
    assert resp, f"LLM 空回應：{query!r}"
    author = resp.get("author") or {}
    detected = str(author.get("detected", "false")).lower() == "true"
    return detected, author.get("name")


# (id, query, 期望 name)
AUTHOR_POSITIVE = [
    ("P1_sandwich", "記者王家瑜的報導", "王家瑜"),
    ("P2_prefix_sandwich", "幫我找記者林彥良寫的文章", "林彥良"),
    ("P3_name_title", "王小明記者的報導", "王小明"),
    ("P4_colon", "作者：張三豐", "張三豐"),
    ("P5_editor_comment", "編輯陳大文的評論", "陳大文"),
    ("P6_possessive_article", "王家瑜的文章", "王家瑜"),
    ("P7_wrote", "王家瑜寫的文章", "王家瑜"),
    ("P8_zhuanxie", "林資傑撰寫的報導", "林資傑"),
    ("P9_column", "陳文茜的專欄", "陳文茜"),
    ("P10_english_by", "articles by John Smith", "John Smith"),
]

# (id, query)
SUBJECT_NEGATIVE = [
    ("N1_cayenne1", "請找出台大邱啟新副教授的公開發言"),
    ("N2_cayenne2", "找出政治大學王慧敏的公開發言"),
    ("N3_fayan", "賴清德的發言"),
    ("N4_kanfa", "郭台銘的看法"),
    ("N5_zenmeshuo", "柯文哲怎麼說"),
    ("N6_lichang", "張忠謀的立場"),
    ("N7_xiangguan", "侯友宜的相關報導"),
    ("N8_xinwen", "黃仁勳的新聞"),
    ("N9_tanhua", "陳建仁教授的談話"),
    ("N10_dui_pinglun", "媒體對賴清德的評論"),
]

# (id, query, 期望 detected, 期望 name；name 只在 detected=True 時斷言)
BOUNDARY = [
    ("B1_cayenne3_mixed", "請找出台灣大學邱啟新曾經的發言或作品", False, None),
    ("B2_bare_baodao", "王家瑜的報導", False, None),
    ("B3_scholar_wrote", "邱啟新副教授寫的文章", True, "邱啟新"),
    ("B4_fabiao", "找出王慧敏發表的文章", True, "王慧敏"),
    ("B5_mixed_toushu", "郭台銘的發言與投書", False, None),
    ("B6_media_name", "聯合報的王家瑜", False, None),
]

# 泛化驗偽組（CEO 質疑 case-specific 後加入）：詞彙全不在 prompt 字面，
# 全對 = 概念泛化生效；錯 = 背列表，stop-and-report 重寫 prompt（禁把詞塞進 prompt 續命）。
GENERALIZATION = [
    ("G1_xiangfa", "賴清德的想法", False, None),
    ("G2_zhuzhang", "郭台銘的主張", False, None),
    ("G3_taidu", "張忠謀對半導體產業的態度", False, None),
    ("G4_jinkuang", "柯文哲的近況", False, None),
    ("G5_zhuanfang", "黃仁勳的專訪", False, None),
    ("G6_toushu_wenzhang", "陳時中投書媒體的文章", True, "陳時中"),
    # D1 保守側驗證（協調員裁決後期望翻轉，見 plan AR 紀錄）：零著作物訊號歧義句依 D1「歧義一律 false」。
    ("G7_tougao_pure", "陳時中投稿媒體", False, None),
]


@pytest.mark.parametrize("case_id,query,name",
                         AUTHOR_POSITIVE, ids=[c[0] for c in AUTHOR_POSITIVE])
def test_author_positive(case_id, query, name):
    detected, got = _judge(query)
    assert detected is True, f"{query!r} 應判 author，實得 detected=False"
    assert got == name, f"{query!r} 名字抽取：got {got!r}, expected {name!r}"


@pytest.mark.parametrize("case_id,query",
                         SUBJECT_NEGATIVE, ids=[c[0] for c in SUBJECT_NEGATIVE])
def test_subject_negative(case_id, query):
    detected, got = _judge(query)
    assert detected is False, (
        f"{query!r} 是 subject 查詢，誤判 author（name={got!r}）→ strict 空手紅線")


@pytest.mark.parametrize("case_id,query,exp_detected,exp_name",
                         BOUNDARY, ids=[c[0] for c in BOUNDARY])
def test_boundary(case_id, query, exp_detected, exp_name):
    detected, got = _judge(query)
    assert detected is exp_detected, (
        f"{query!r}: got detected={detected}, expected {exp_detected}（name={got!r}）")
    if exp_detected:
        assert got == exp_name, f"{query!r} 名字抽取：got {got!r}, expected {exp_name!r}"


@pytest.mark.parametrize("case_id,query,exp_detected,exp_name",
                         GENERALIZATION, ids=[c[0] for c in GENERALIZATION])
def test_generalization(case_id, query, exp_detected, exp_name):
    """泛化驗偽組：詞彙全不在 prompt 字面。失敗＝概念未生效（背列表），
    stop-and-report 重寫 prompt——禁把失敗案詞彙補進 prompt 交差。"""
    detected, got = _judge(query)
    assert detected is exp_detected, (
        f"{query!r}: got detected={detected}, expected {exp_detected}（name={got!r}）"
        "——泛化失敗＝prompt 靠字面非概念")
    if exp_detected:
        assert got == exp_name, f"{query!r} 名字抽取：got {got!r}, expected {exp_name!r}"
