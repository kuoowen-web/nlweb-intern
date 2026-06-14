"""Track F sprint 2026-05-28 — Claim-level fabrication fixtures.

Cayenne 2026-05-27 真實 BAB 試用 session 23bb04f7 的 17 問題中的
claim-level fabrication 真案例 enum。F1 / F2 / F3 共用。

每筆 fixture 含：
- section: LiveWriterSectionOutput（fabricated content）
- evidence_text: chapter evidence_pool subset 全文
- expected_fabrication_type: numeric / temporal / causal / comparative / predictive / evaluative / other
- expected_critic_action: reject / warn / pass
- explanation: 為何此案會被 F flag（給 plan reader 看）
"""
from typing import List

from reasoning.schemas_live import LiveWriterSectionOutput


def _section(content: str, sources: List[int] = None, title: str = "test") -> LiveWriterSectionOutput:
    return LiveWriterSectionOutput(
        section_title=title,
        section_content=content,
        sources_used=sources or [],
        confidence_level="Medium",
    )


# F-CL-1: Numeric claim 推論
FIXTURE_NUMERIC_CLAIM = {
    "section": _section(
        "弗萊堡市在 2018 年達成裝置容量 5 GW [1]，是該市最大綠能成就。",
        sources=[1],
        title="國外案例",
    ),
    "evidence_text": (
        "[1] 弗萊堡綠能發展 — 弗萊堡市自 2000 年代推動綠能轉型，含光電、風電等多元配置。"
        "市政府宣示 2050 碳中和目標。"
    ),
    "expected_fabrication_type": "numeric",
    "expected_critic_action": "reject",
    "explanation": "evidence 提到弗萊堡推動綠能但未給「2018 年 = 5 GW」的具體數字組合 — "
                   "「2018」與「5 GW」分開可能在 evidence 中存在（前者作為敘事年份、後者作為其他城市數據）"
                   "但組合語意是 LLM 推論捏造。",
}

# F-CL-2: Temporal claim
FIXTURE_TEMPORAL_CLAIM = {
    "section": _section(
        "自 2018 年起，德國陸續推出 5 條再生能源政策 [2]，奠定今日基礎。",
        sources=[2],
        title="國外案例",
    ),
    "evidence_text": (
        "[2] 德國再生能源法 — 德國再生能源法 (EEG) 自 2000 年制定以來經多次修訂，"
        "近期修訂強化儲能與離岸風電配置。"
    ),
    "expected_fabrication_type": "temporal",
    "expected_critic_action": "warn",
    "explanation": "「2018」「5 條政策」可能在 evidence 中分散存在，但「自 2018 年起」"
                   "作為起算點是 LLM 加的 temporal anchor，evidence 起算點是 2000 年。",
}

# F-CL-3: Causal claim
FIXTURE_CAUSAL_CLAIM = {
    "section": _section(
        "因為 EEG 法案推動，德國再生能源占比從 10% 快速提升到 40% [3]。",
        sources=[3],
        title="政策影響",
    ),
    "evidence_text": (
        "[3] 德國能源轉型 — 德國再生能源占比近年顯著提升。EEG 法案是相關政策之一。"
        "其他因素包含碳定價、技術成本下降、社會接受度提升等。"
    ),
    "expected_fabrication_type": "causal",
    "expected_critic_action": "warn",
    "explanation": "兩端 entity（EEG、再生能源占比）都在 evidence；具體百分比是編；"
                   "更重要的是「因為 X 所以 Y」的因果連接是 LLM 加的（evidence 列多重因素）。",
}

# F-CL-4: Comparative claim
FIXTURE_COMPARATIVE_CLAIM = {
    "section": _section(
        "Horns Rev 風場規模比英國同類風場大 30% [4]，是北海最大專案。",
        sources=[4],
        title="國外案例",
    ),
    "evidence_text": (
        "[4] Horns Rev 風場 — 丹麥北海離岸風場，多期建設。英國也有類似 scale 的離岸風場。"
    ),
    "expected_fabrication_type": "comparative",
    "expected_critic_action": "reject",
    "explanation": "「30%」是無 evidence 支撐的數字；「比英國同類大」的比較關係 evidence 沒給；"
                   "「北海最大」是評價詞且無比較數據支撐。",
}

# F-CL-5: Predictive claim
FIXTURE_PREDICTIVE_CLAIM = {
    "section": _section(
        "預計到 2030 年北萊茵將完成 10 GW 離岸風電容量 [5]。",
        sources=[5],
        title="未來展望",
    ),
    "evidence_text": (
        "[5] 北萊茵再生能源規劃 — 北萊茵推動再生能源轉型，含光電、風電等多元配置。"
        "州政府 2050 碳中和目標。"
    ),
    "expected_fabrication_type": "predictive",
    "expected_critic_action": "warn",
    "explanation": "「2030 年 10 GW」這個具體預測數字無 evidence 支撐；"
                   "evidence 只提到 2050 碳中和目標。",
}

# F-CL-6: Evaluative claim
FIXTURE_EVALUATIVE_CLAIM = {
    "section": _section(
        "德國綠能轉型成效顯著 [6]，超越其他歐盟國家。",
        sources=[6],
        title="評價",
    ),
    "evidence_text": (
        "[6] 德國能源轉型 — 德國再生能源占比 40%，比 2000 年的 7% 大幅提升。"
        "歐盟整體再生能源占比約 22%。"
    ),
    "expected_fabrication_type": "evaluative",
    "expected_critic_action": "warn",
    "explanation": "「成效顯著」是評價詞（主觀）；「超越其他歐盟國家」雖有數據支撐"
                   "（40% > 22%）但「超越」是強評價，且未列具體比較對象（其他歐盟國家是哪幾個？）。",
}

# F-CL-7: 整章 fabrication（entity-level + claim-level 並發）— S-3 紀律補加
# Track A T5 已 cover entity → guard_failed status；F1 對 guard_failed short-circuit
# pass-through，不再二次 LLM call。此 fixture 驗 F1 在 guard_failed status 章節
# **真的 short-circuit**（不浪費 LLM call、status 不變、content 不變）。
FIXTURE_WHOLE_CHAPTER_FABRICATION = {
    # 模擬已被 Track A T5 entity guard 標 guard_failed 的 section
    "section": LiveWriterSectionOutput(
        section_title="國外案例（已被 T5 entity guard 標 guard_failed）",
        section_content=(
            "[本章內容無法驗證]：系統 grounding 檢查偵測到 N 個未經證據支撐的具體 entity"
            "（清單：弗萊堡、千葉、北萊茵），已跳過該章內容。"
        ),
        sources_used=[],
        confidence_level="Low",
        status="guard_failed",  # 關鍵：已是 guard_failed
    ),
    "evidence_text": "[1] 德國能源轉型 — 與案例 entity 完全無關 evidence",
    "expected_fabrication_type": "other",  # 整章 fabrication 不對應單一 claim 類型
    "expected_critic_action": "pass",  # F1 short-circuit pass-through（不 call LLM、status 不變）
    "explanation": "Track A T5 entity guard 已 land 並標 status='guard_failed'。F1 對"
                   "guard_failed status section short-circuit pass-through（F-AMB-7）— 不"
                   "二次 LLM call、不變 status。此 fixture 驗 short-circuit 真的觸發（"
                   "避免 F1 對 deterministic blocked 文字產生奇怪 verdict）。",
}

ALL_FIXTURES = [
    ("F-CL-1", FIXTURE_NUMERIC_CLAIM),
    ("F-CL-2", FIXTURE_TEMPORAL_CLAIM),
    ("F-CL-3", FIXTURE_CAUSAL_CLAIM),
    ("F-CL-4", FIXTURE_COMPARATIVE_CLAIM),
    ("F-CL-5", FIXTURE_PREDICTIVE_CLAIM),
    ("F-CL-6", FIXTURE_EVALUATIVE_CLAIM),
    ("F-CL-7", FIXTURE_WHOLE_CHAPTER_FABRICATION),  # S-3
]


def test_fixtures_well_formed():
    """所有 fixture 都是 dict + section 是 LiveWriterSectionOutput + evidence_text 非空。"""
    assert len(ALL_FIXTURES) == 7  # S-3: 7 fixture（F-CL-1 ~ F-CL-7）
    for fid, fx in ALL_FIXTURES:
        assert isinstance(fx, dict), f"{fid} not dict"
        assert "section" in fx and isinstance(fx["section"], LiveWriterSectionOutput)
        assert "evidence_text" in fx and isinstance(fx["evidence_text"], str)
        assert fx["evidence_text"].strip()
        assert fx["expected_fabrication_type"] in {
            "numeric", "temporal", "causal", "comparative", "predictive", "evaluative", "other"
        }
        assert fx["expected_critic_action"] in {"reject", "warn", "pass"}
