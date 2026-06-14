"""O4+O4-C 合併版: LR user-facing 文案集中模組測試。

驗 marker / sentinel 保留、插值正確、dedup regex 新舊雙匹配。
jargon 殘留由 test_live_orchestrator.py::test_lr_user_facing_strings_have_no_dev_jargon
的 AST 全檔掃描把關（Task D），此處不重複列 forbidden list。
"""
import re

from reasoning.live_research import lr_copy


def test_blocked_no_evidence_variants_keep_marker():
    assert lr_copy.BLOCKED_NO_EVIDENCE_ENTRY.startswith("[本章資料不足]")
    assert lr_copy.BLOCKED_NO_EVIDENCE_POST_RENDER.startswith("[本章資料不足]")
    # 兩變體語意不同（完全沒資料 vs 有資料但整理不出佐證），不可相同
    assert lr_copy.BLOCKED_NO_EVIDENCE_ENTRY != lr_copy.BLOCKED_NO_EVIDENCE_POST_RENDER


def test_critic_rejected_content_prefix_count_examples():
    t = lr_copy.critic_rejected_content(3, ["甲說法", "乙說法" * 30, "丙", "丁", "戊", "己"])
    assert t.startswith(lr_copy.CRITIC_REJECTED_PREFIX)
    assert lr_copy.CRITIC_REJECTED_PREFIX == "[本章內容未通過查核]"
    assert "3 處說法" in t          # 筆數插值保留
    assert "「甲說法」" in t          # 例句保留
    assert "己" not in t             # 例句最多 5 筆
    assert ("乙說法" * 30)[:31] not in t  # 單句截斷 30 字
    # 不可新增 not-in sentinel 字串
    assert "[本章內容無法驗證]" not in t


def test_methodology_notes_keep_interpolation():
    assert "自動查核系統發生故障" in lr_copy.GROUNDING_UNAVAILABLE_NOTE
    t = lr_copy.degraded_low_confidence_note(["弗萊堡", "千葉"])
    assert "弗萊堡" in t and "千葉" in t and "正文保留未改動" in t
    t2 = lr_copy.partial_removed_note(2, ["北萊茵"])
    assert t2.startswith("[部分內容已移除：")
    assert "2 句" in t2 and "北萊茵" in t2


def test_warn_marker_prefix_and_dedup_regex_match_old_and_new():
    m = lr_copy.warn_marker(4, "說明" * 100)
    assert m.startswith(lr_copy.WARN_MARKER_PREFIX)
    # 字面錨（S4-M W1）：與 CRITIC_REJECTED_PREFIX / REFERENCE_MISSING_SENTINEL /
    # [本章資料不足] 同級防禦 — 解耦後文案被亂改，此處仍會報警
    assert lr_copy.WARN_MARKER_PREFIX == "[查核提醒："
    assert "4 處說法" in m
    assert len(m) < len(lr_copy.WARN_MARKER_PREFIX) + 150  # explanation 截斷 100 字
    # dedup regex 必須同時匹配新 marker 與舊 session 殘留的舊 marker
    assert re.search(lr_copy.WARN_MARKER_DEDUP_RE, m)
    assert re.search(
        lr_copy.WARN_MARKER_DEDUP_RE,
        f"{lr_copy.LEGACY_WARN_MARKER_PREFIX} 2 筆 claim 待驗證 — xxx]",
    )


def test_banners_keep_interpolation():
    n1 = lr_copy.problematic_chapters_narration(2, "「甲」（資料不足）, 「乙」（驗證失敗）")
    assert "2 個章節未能完成" in n1 and "「甲」（資料不足）" in n1
    h1 = lr_copy.problematic_chapters_header(2, "- 第 1 章「甲」（資料不足）")
    assert "**本報告有 2 個章節未能完成**" in h1
    assert "- 第 1 章「甲」（資料不足）" in h1
    assert h1.rstrip().endswith("---")  # header 與正文的分隔線保留


def test_hallucination_narration_consistent_wording():
    # 與 methodology_note 的「較低」用語一致，user 才找得到對應段落
    assert "信心較低" in lr_copy.HALLUCINATION_CORRECTED_NARRATION
    assert "較低" in lr_copy.GROUNDING_UNAVAILABLE_NOTE


def test_reference_missing_entry_keeps_sentinel_and_eid():
    assert lr_copy.REFERENCE_MISSING_SENTINEL == "來源遺失"
    t = lr_copy.reference_missing_entry(7)
    assert t.startswith("[7] ")
    assert lr_copy.REFERENCE_MISSING_SENTINEL in t
