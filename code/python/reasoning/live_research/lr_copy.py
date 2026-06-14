"""LR user-facing 文案單一事實源（O4+O4-C 合併，2026-06-11）。

集中所有會出現在 user 螢幕上的 LR 系統文案：
- 章節被攔下時寫進報告正文的替換文（blocked_no_evidence 兩變體、F1 REJECT）
- methodology_note ⚠ 提示列模板（guard 降級三處、F1 WARN）
- Stage 6 narration / 報告 header banner
- references 缺項行、hallucination 修正 narration
- 系統端 LLM 失敗共用旁白（quota / timeout / 空回應 / schema validate fail）

紀律：
1. 本模組所有字串都是 user 看得到的正文 / 旁白，禁止內部開發術語。
   forbidden list 與全檔掃描見
   test_live_orchestrator.py::test_lr_user_facing_strings_have_no_dev_jargon。
2. bracket marker 與「來源遺失」是 test 契約 sentinel — test 請 import 本模組常數，
   不要在 test 內複製字面。
3. 例外：LEGACY_WARN_MARKER_PREFIX 與 WARN_MARKER_DEDUP_RE 僅用於匹配舊 session
   持久化的舊 marker，永不輸出給 user，已列入掃描 allowlist。
4. 純字串組裝：禁止 import LLM / handler / state / orchestrator。
"""
from typing import List, Sequence

# --- 章節正文替換文（進 section_content：SSE 顯示 + 匯出 .md 正文） ---

BLOCKED_NO_EVIDENCE_PREFIX = "[本章資料不足]"  # test 契約 sentinel（3 處既有斷言）

BLOCKED_NO_EVIDENCE_ENTRY = (
    BLOCKED_NO_EVIDENCE_PREFIX + " 系統找不到足夠的相關新聞資料來撰寫本章，"
    "已略過自動撰寫以避免產生無依據的內容。"
)

BLOCKED_NO_EVIDENCE_POST_RENDER = (
    BLOCKED_NO_EVIDENCE_PREFIX + " 系統雖然找到了一些相關資料，但無法從中"
    "整理出足以佐證本章論述的具體內容，已略過自動撰寫，"
    "以避免產生無依據的內容。"
)

CRITIC_REJECTED_PREFIX = "[本章內容未通過查核]"


def critic_rejected_content(issue_count: int, claim_texts: Sequence[str]) -> str:
    """F1 REJECT 整章替換文。例句最多 5 筆、每句截 30 字（沿用既有上限）。"""
    examples = "、".join(f"「{t[:30]}」" for t in claim_texts[:5])
    return (
        f"{CRITIC_REJECTED_PREFIX} 系統逐句查核時發現本章有 "
        f"{issue_count} 處說法無法以現有資料佐證"
        f"（例如：{examples}），為避免錯誤資訊擴散，未保留本章內容。"
        f"建議：重寫這一章，或調整研究問題讓系統蒐集到更多相關資料後再試。"
    )


# --- methodology_note ⚠ 提示列（前端 live-research.js L566-569 顯示於章節卡） ---

# 語意保真：是「查核系統故障、本章沒被查核」，不可美化成查核通過。
GROUNDING_UNAVAILABLE_NOTE = (
    "[自動查核系統發生故障，本章內容未經完整查證；"
    "信心標示已調為「較低」，正文保留未改動]"
)

# --- 章節查核降級即時 SSE 旁白（D-2026-06-11 決策1：guard/查核故障補即時旁白） ---
# GROUNDING_UNAVAILABLE_NOTE 是寫進報告的標註；本常數是配對的「即時」旁白，
# 由 _apply_degraded_grounding_unavailable 發出（單一落點蓋三個呼叫點）。
# per-run 只播一次（orchestrator dedup flag）；之後章節若同樣退化，
# 仍由各章的標註逐章呈現，不再重複旁白。
# 語意保真：查核「沒能完成」≠ 查核通過，不可美化因果。
GROUNDING_UNAVAILABLE_NARRATION = (
    "提醒：這一章的事實查核暫時沒能完成，內容保留不動，"
    "但這一章的信心標示已調為「較低」。之後的章節若遇到同樣狀況，"
    "會直接在該章標示，不再重複提醒。"
)

# guard 區段其他環節故障（非查核系統本身失敗）的即時旁白。
# 事實對齊（o5c plan 2026-06-10 根因修正）：查核系統失敗已由上一條
# （GROUNDING_UNAVAILABLE）路徑局部接走，到不了這裡；且最後一道發佈把關
# 在此故障點之後照常執行 —— 文案不可寫成「本章完全未經把關」。
SECTION_GUARD_ERROR_NARRATION = (
    "提醒：這一章寫作過程中有部分驗證環節出了狀況、沒有全部完成，"
    "最後一道內容把關仍照常執行。建議閱讀這一章時多留意內容是否合理。"
)

# === 發布審查（第三層 publish gate）自身故障的降級文案（Task 1，CEO 拍板 default=degrade-and-narrate）===
# 語意保真：是「最後一道發布審查流程本身出狀況、這章沒被審到」，不可美化成審查通過。
# 與 GROUNDING_UNAVAILABLE_NOTE（第一層 grounding 故障）區分：那是「事實查核」沒完成，
# 這是「發布審查」沒完成 —— 兩層不同，文案不可混用。
# 注意：文案常數已備（Task 1 前置）；對應 _run_publish_gate outer except 的
# degrade-and-narrate 行為分支待 CEO 故障語意 (a)/(b) 拍板後才實作，本次未接線。
PUBLISH_GATE_UNAVAILABLE_NOTE = (
    "[最後一道發布審查發生故障，本章未經發布審查；"
    "信心標示已調為「較低」，正文保留未改動]"
)

# 配對的即時 SSE 旁白（由 _run_publish_gate outer except 發出，per-run dedup）。
PUBLISH_GATE_UNAVAILABLE_NARRATION = (
    "提醒：這一章的最後發布審查暫時沒能完成，內容保留不動，"
    "但這一章的信心標示已調為「較低」。之後的章節若遇到同樣狀況，"
    "會直接在該章標示，不再重複提醒。"
)

# 抽取層（grounding 第 1 步 candidate 抽取）LLM 故障旁白（Task 3）。
# 方向安全（抽不出 candidate = 沒東西要查，已 verified 設計決策，不翻）；但 517115a7
# 精神「系統故障要讓 user 看見」要求補一句旁白 —— 故障≠通過，user 須知本章 grounding
# 因抽取故障而未實際執行。per-run dedup（orchestrator）。
GROUNDING_EXTRACTION_FAILED_NARRATION = (
    "提醒：有章節在抽取待查核名稱時系統暫時出了狀況，這幾章的事實查核未能完整執行，"
    "內容保留不動。建議閱讀時對其中的具體名稱、數字多留意。"
)

# 發布審查遇到「本章零 evidence」時的 deterministic 短路標註（Task 2）。
# 語意保真：是「沒有可供審查比對的資料來源」，不是「審查通過」。進 LLM 審零 evidence
# 無意義（純燒錢且判決不可預測），故短路不打 critic。
PUBLISH_GATE_NO_EVIDENCE_NOTE = (
    "[本章查無可供查核比對的資料來源，已略過發布審查；"
    "信心標示已調為「較低」，正文保留未改動]"
)


def degraded_low_confidence_note(ungrounded: List[str]) -> str:
    """guard 退化路徑 (a)：正文不動、降信心、標註查無佐證的名稱。"""
    return (
        f"[自動修正：信心標示已調為「較低」— 下列具體名稱在現有資料中"
        f"找不到佐證：{', '.join(ungrounded)}；正文保留未改動]"
    )


def partial_removed_note(removed: int, ungrounded: List[str]) -> str:
    """guard 主路徑 (b)：sentence-level partial block。user 仍需知道刪了幾句、哪些名稱。"""
    return (
        f"[部分內容已移除：{removed} 句提到的具體名稱"
        f"（{', '.join(ungrounded)}）在現有資料中找不到佐證，"
        f"該幾句已剔除，其餘內容保留]"
    )


WARN_MARKER_PREFIX = "[查核提醒："
LEGACY_WARN_MARKER_PREFIX = "[F1 critic WARN:"  # 舊 session 持久化殘留，僅匹配不輸出
# dedup 新舊雙匹配 — 舊 session revise 重跑時把舊 marker 替換成新版，不殘留雙 marker
WARN_MARKER_DEDUP_RE = r"\[(?:F1 critic WARN:|查核提醒：)[^\]]*\]"


def warn_marker(issue_count: int, explanation: str) -> str:
    """F1 WARN marker（append 進 methodology_note；explanation 截 100 字沿用既有上限）。"""
    return (
        f"{WARN_MARKER_PREFIX}本章有 {issue_count} "
        f"處說法待進一步確認 — {explanation[:100]}]"
    )


# 系統端 LLM 失敗共用旁白：429 quota / timeout / 空回應 / schema validate fail
# 皆屬系統端問題，非「user 講不清」——怪 user「我沒看懂」會誤導（user 重講也沒用）。
# orchestrator 直接以 lr_copy.LLM_UNAVAILABLE_NARRATION 引用（alias 已淘汰）。
LLM_UNAVAILABLE_NARRATION = (
    "抱歉，系統暫時無法處理你的要求（可能是服務忙碌），請稍候再試一次。"
)


# Task 1 (DR-parity revise loop)：mini-reasoning REJECT→revise 重寫該輪推論失敗時的降級旁白。
# 與 LLM_UNAVAILABLE_NARRATION（整段 mini-reasoning 失敗）分離：此處是「revise 這一步」失敗，
# 原始推論仍會以 forensic trail 入庫並由 render 過濾，研究照常繼續。
MINI_REASONING_REVISE_DEGRADED_NARRATION = (
    "這一輪有段分析沒通過查核、嘗試重寫時又遇到狀況，已先以原樣保留紀錄並略過該段，"
    "研究會繼續往下進行。"
)


# Task 2 (DR-parity SEARCH_REQUIRED)：分析時發現某些面向的站內資料不夠、二次補查站內資料
# 失敗 / 無結果時的旁白。與整段失敗的 LLM_UNAVAILABLE_NARRATION 分離。
SEARCH_REQUIRED_DEGRADED_NARRATION = (
    "這一輪分析時發現某些面向的站內資料不夠，嘗試補查時沒有找到更多相關資料，"
    "本段會以現有資料為基礎繼續，研究照常往下進行。"
)


# --- Stage 3 style analysis O7 input-type 守門（輸入被判定不是寫作範本）降級旁白 ---
# 語意降級通道（LLM 成功判定輸入非範本），與 LLM_UNAVAILABLE_NARRATION（系統失敗）分離：
# 前者 user 該換輸入，後者 user 該重試——文案不可混用。

STYLE_INPUT_NOT_SAMPLE_FIRST_NARRATION = (
    "你這句看起來比較像是想法或指令，而不是一段可以分析的文筆範本。"
    "如果想設定寫作風格，請貼一段你喜歡的文章或段落；"
    "或回覆「用預設就好」由我採用通用的學術風格。"
)

STYLE_INPUT_NOT_SAMPLE_REDO_NARRATION = (
    "你這句看起來比較像指令而不是一段新的文筆範本，"
    "我先保留目前的分析。如果想換風格，請貼一段新的範本文字。"
)


# --- Stage 5 checkpoint 釐清文案 ---

def stage5_done_unfinished_gate_prompt(remaining: int) -> str:
    """Stage 5 LLM-done completeness gate 釐清文案（D-2026-06-11 決策 4）。

    政策對齊 #11 Part B export keyword block（未寫完不給匯出路徑）；
    與 SKIP 釐清句（「跳過這步」語境，意圖不明）區隔：本句承認 user 的
    「結束」意圖並解釋 block 原因。必含子字串「繼續寫」（unit test 斷言鍵詞，
    措辭固定「繼續寫完剩下的」—— 「繼續把剩下的寫完」不含該子字串，勿改寫）。
    """
    return (
        f"看起來你想在這裡結束，但報告還有 {remaining} 段沒寫完，"
        "要先寫完才能匯出。要我繼續寫完剩下的，還是修改已寫好的某段？"
    )


# --- Stage 6 narration / final report header banner ---

HALLUCINATION_CORRECTED_NARRATION = (
    "⚠ 撰寫過程中發現部分段落引用了未經查核的來源或佔位文字，"
    "已自動修正並調低這些段落的信心標示。建議你特別留意報告中標為"
    "「信心較低」的段落。"
)


def problematic_chapters_narration(n: int, titles: str) -> str:
    """Stage 6 SSE narration banner（titles 已含中文原因，由 caller 用 reason map 組好）。"""
    return (
        f"⚠ 報告有 {n} 個章節未能完成（{titles}）— "
        "系統自動查核時發現這些章節缺少足夠的新聞資料，或出現找不到出處的"
        "具體事實。你可以：(a) 調整研究問題，讓系統蒐集到更多相關新聞資料後"
        "重跑、(b) 單獨重寫這幾章、或 (c) 接受目前結果，直接匯出含未完成章節"
        "的報告。"
    )


def problematic_chapters_header(n: int, problems_md: str) -> str:
    """匯出 .md 報告頂部持久警告（與 narration 雙重提醒，兩處都要在）。"""
    return (
        f"⚠ **本報告有 {n} 個章節未能完成**\n\n"
        f"系統自動查核時發現以下章節缺少足夠的新聞資料，或無法核實內容：\n\n"
        f"{problems_md}\n\n"
        f"建議重新研究，或調整研究問題後再跑一次。\n\n"
        f"---\n"
    )


# --- References 缺項 ---

REFERENCE_MISSING_SENTINEL = "來源遺失"  # test 契約 sentinel（test_lr_references_block.py）


def reference_missing_entry(eid) -> str:
    """phantom citation 缺項行 — 保留 [eid] 編號，缺項不可變 silent（no silent fail）。"""
    return (
        f"[{eid}] *（{REFERENCE_MISSING_SENTINEL}：報告引用了編號 {eid}，"
        f"但系統來源庫中找不到對應資料）*"
    )
