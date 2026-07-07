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
    m = lr_copy.warn_marker(4, "說明。" * 100)
    assert m.startswith(lr_copy.WARN_MARKER_PREFIX)
    # 字面錨（S4-M W1）：與 CRITIC_REJECTED_PREFIX / REFERENCE_MISSING_SENTINEL /
    # [本章資料不足] 同級防禦 — 解耦後文案被亂改，此處仍會報警
    assert lr_copy.WARN_MARKER_PREFIX == "[查核提醒："
    assert "4 處說法" in m
    # 2026-06-19 新契約：critic 查核說明完整輸出、不截斷、不補省略號（移除 100 字上限）。
    # 收尾仍是閉合括號 ]，不可半句 + 孤立 ]。
    assert m.endswith("]")
    assert "…]" not in m  # 不再節略 → 不補省略號
    assert ("說明。" * 100) in m  # 超長 explanation 完整保留
    # dedup regex 必須同時匹配新 marker 與舊 session 殘留的舊 marker
    assert re.search(lr_copy.WARN_MARKER_DEDUP_RE, m)
    assert re.search(
        lr_copy.WARN_MARKER_DEDUP_RE,
        f"{lr_copy.LEGACY_WARN_MARKER_PREFIX} 2 筆 claim 待驗證 — xxx]",
    )


def test_warn_marker_short_explanation_untouched():
    """短 explanation 原樣保留，不補省略號。"""
    short = "治理爭議的線索，但尚未明確建立單一責任歸屬。"
    m = lr_copy.warn_marker(1, short)
    assert short in m
    assert "…]" not in m  # 不補省略號
    assert m.endswith("]")


def test_warn_marker_long_explanation_kept_whole_no_orphan_bracket():
    """2026-06-19 新契約：超長 explanation 完整保留、不截斷、不補省略號，
    收尾仍是閉合括號 ]（不留半句 + 孤立 ]）。

    critic 查核說明是給使用者看「為何本章有疑慮」的——攔腰砍掉反傷信任，應完整輸出。
    取代舊的 Bug B 句界截斷契約（_WARN_EXPLANATION_MAX 100 字上限已移除）。
    """
    long_expl = ("第一句完整內容。" * 20) + "這是該被完整保留的第二段補述。"
    m = lr_copy.warn_marker(3, long_expl)
    assert long_expl in m          # 完整保留，一字不少
    assert "…]" not in m           # 不節略 → 不補省略號
    assert m.endswith("]")         # 收尾仍閉合括號


def test_warn_marker_sanitizes_inner_brackets_dedup_regex_intact():
    """AR R1 blocker：explanation 內含 ] 不可破壞 marker（dedup regex [^\\]]* 會提前結束）。

    驗：(1) marker body 無 raw ]（只有結尾 structural ]）；(2) DEDUP_RE 完整匹配整個 marker
    （match span == 整個 marker，非提前在 body 的 ] 結束）。
    """
    m = lr_copy.warn_marker(1, "建議引用 [4] 佐證，另見 [12] 的數據] 說明")
    # AR R2 should-fix：body 精確切 = prefix 之後到結尾 structural ] 之前
    body = m[len(lr_copy.WARN_MARKER_PREFIX):-1]
    assert "]" not in body, f"marker body 殘留 raw ]: {m!r}"
    assert "[" not in body, f"marker body 殘留 raw [: {m!r}"
    assert m.endswith("]")
    # dedup regex 必須完整匹配到整個 marker（不在 body 的 ] 提前結束）
    match = re.search(lr_copy.WARN_MARKER_DEDUP_RE, m)
    assert match is not None
    assert match.group(0) == m, f"dedup regex 提前結束，只匹配到 {match.group(0)!r}"


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


def test_problematic_chapter_line_is_one_based():
    """Bug G 回歸：章號 1-based（index=3 → 第 4 章）。測 production helper，非拷貝邏輯。"""
    # 第四段 index=3 → 第 4 章（不是第 3 章）
    assert lr_copy.problematic_chapter_line(3, "結果與討論", "驗證失敗") == "- 第 4 章「結果與討論」（驗證失敗）"
    assert lr_copy.problematic_chapter_line(0, "前言", "資料不足") == "- 第 1 章「前言」（資料不足）"


def test_problematic_chapter_line_missing_index_no_crash():
    """缺 index（"?"）退化不 crash、不印「第 None 章」、不觸發 "?"+1 TypeError。"""
    assert lr_copy.problematic_chapter_line("?", "缺 index 的章", "未完成") == "- 第 ? 章「缺 index 的章」（未完成）"


def test_problematic_chapter_line_bool_not_treated_as_int():
    """AR R1 nit：bool 是 int 子類，type() is int 排除它（避免 True+1=2 印「第 2 章」）。"""
    # bool 不該被當章號 +1，退化 "?"
    assert "第 ? 章" in lr_copy.problematic_chapter_line(True, "x", "y")


def test_problematic_chapter_line_edge_cases():
    """AR R2 should-fix：None title / "0" 字串 index / 換行 title 都不破壞 markdown。"""
    # "0" 是字串非 int → 退化 "?"（不可被當 0+1）
    assert "第 ? 章" in lr_copy.problematic_chapter_line("0", "x", "y")
    # None title → "?"，不印「第 N 章「None」」
    assert "「?」" in lr_copy.problematic_chapter_line(0, None, "y")
    # title 含換行 → 壓成單行，不破壞 markdown list（- 開頭）
    line = lr_copy.problematic_chapter_line(0, "標題\n第二行", "y")
    assert "\n" not in line


def test_build_problematic_chapters_md_one_based():
    """組裝邏輯：section_index=0 → 第 1 章。"""
    problematic = [
        {"section_index": 3, "title": "結果與討論", "status": "guard_failed"},
        {"section_index": 0, "title": "前言", "status": "blocked_no_evidence"},
    ]
    reason_map = {"guard_failed": "驗證失敗", "blocked_no_evidence": "資料不足"}
    md = lr_copy.build_problematic_chapters_md(problematic, reason_map)
    assert "第 4 章「結果與討論」" in md and "第 1 章「前言」" in md and "第 0 章" not in md


def test_chapter_word_truncated_narration_interpolates():
    # bug 2026-06-20：軟約束壓不住 → post-process truncate。truncate 後旁白要誠實
    # 告知「已節略至約 N 字」（no silent fail），不可沿用「內容照常保留」舊文案。
    n = lr_copy.chapter_word_truncated_narration("國內案例文獻", 800, 2258)
    assert "國內案例文獻" in n
    assert "800" in n  # 規劃字數
    assert "2258" in n  # 節略前實際字數
    # 語意：有節略（內容被切過），不是「照常保留」
    assert "節略" in n
    assert "字" in n
    # 不可洩漏開發術語
    for bad in ("LLM", "token", "target_word_count", "truncate", "overshoot", "guard"):
        assert bad not in n
