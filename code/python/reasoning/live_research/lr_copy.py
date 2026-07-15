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
    "若希望補強這一章，可以回覆我「再去找更多資料」，我會回頭重新蒐集。"
)

BLOCKED_NO_EVIDENCE_POST_RENDER = (
    BLOCKED_NO_EVIDENCE_PREFIX + " 系統雖然找到了一些相關資料，但無法從中"
    "整理出足以佐證本章論述的具體內容，已略過自動撰寫，"
    "以避免產生無依據的內容。"
    "若希望補強這一章，可以回覆我「再去找更多資料」，我會回頭重新蒐集。"
)

CRITIC_REJECTED_PREFIX = "[本章內容未通過查核]"


def critic_rejected_content(issue_count: int, claim_texts: Sequence[str]) -> str:
    """F1 REJECT 整章替換文。例句最多 5 筆、每句截 30 字（沿用既有上限）。"""
    examples = "、".join(f"「{t[:30]}」" for t in claim_texts[:5])
    return (
        f"{CRITIC_REJECTED_PREFIX} 系統逐句查核時發現本章有 "
        f"{issue_count} 處說法無法以現有資料佐證"
        f"（例如：{examples}），為避免錯誤資訊擴散，未保留本章內容。"
        f"建議：若覺得是資料本身不夠，可以直接回覆我「再去找更多資料」，"
        f"我會回頭重新蒐集後再重寫；或告訴我直接用現有資料重寫這一章。"
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

# --- 章節字數明顯超標、內容照常保留的透明化旁白（2026-07 regression 修復，改回軟約束）---
# 硬切會截斷正文使用者要不回（CEO「切掉就不能用了」），改回「只透明化、不砍 content」。
# no silent fail：仍誠實告知 user「本章比預期長」，但明說內容完整保留。
# 純白話、零開發術語（lr_copy 全檔 AST jargon guard + 本函式 per-function forbidden 掃描）。


def chapter_word_overshoot_narration(
    chapter_title: str, target: int, actual: int
) -> str:
    """章節偏長旁白（內容照常保留，不切）。target=規劃字數，actual=實際字數（已剝引用標記）。"""
    return (
        f"提醒：「{chapter_title}」這一章寫成約 {actual} 字，"
        f"比規劃的約 {target} 字長一些，內容我完整保留、沒有刪節。"
        f"如果你希望更精簡，可以告訴我「這章縮短一點」，我再幫你改寫。"
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

# 初始格式抽取 LLM 故障旁白（AR round 1 B3 — no-silent-fail）。
# 抽取失敗時退回現行 LLM 自由發揮（方向安全），但「故障≠user 需求被接受」——
# user 須知其格式需求這次沒被結構化解析。與既有降級旁白慣例一致
# （見 GROUNDING_EXTRACTION_FAILED_NARRATION:102），由 _maybe_extract_initial_format
# except 路徑 _emit_narration 發出。語意保真：沒美化成「已照你的格式」。
INITIAL_FORMAT_EXTRACTION_FAILED_NARRATION = (
    "提醒：我這次沒能完整解析你提到的格式需求（章節、字數或引用方式等），"
    "會先照一般方式安排研究結構。如果有特定格式要求，等一下確認研究結構時"
    "再直接告訴我就好。"
)


# 引用格式 enum → user-facing 中文（無內部術語）
_CITATION_STYLE_LABEL = {
    "author_year": "作者—年份（如 APA）格式",
    "numeric": "數字編號格式",
    "footnote": "腳註格式",
    "none": "不附引用",
}

# special element type → user-facing 中文
_SPECIAL_ELEMENT_LABEL = {
    "table": "表格",
    "list": "清單",
    "chart": "圖表",
    "diagram": "流程圖",
    "code_block": "程式碼區塊",
}


def initial_format_confirmation_line(
    chapter_names: Sequence[str],
    total_word_count,
    citation_style,
    special_elements: Sequence[dict],
) -> str:
    """組裝 Stage 1 初始格式抽取的確認句（併入研究結構提案 proposal）。

    只列實際抽到的維度。所有 caller 已確認至少一項非空（has_meaningful_spec）。
    純字串組裝，禁開發術語（test_lr_user_facing_strings_have_no_dev_jargon 掃描）。
    """
    parts: List[str] = []
    if chapter_names:
        names = "、".join(chapter_names)
        parts.append(f"{len(chapter_names)} 章（{names}）")
    if total_word_count:
        parts.append(f"全文約 {total_word_count} 字")
    if citation_style and citation_style in _CITATION_STYLE_LABEL:
        parts.append(f"引用採{_CITATION_STYLE_LABEL[citation_style]}")
    for elem in special_elements or []:
        if not isinstance(elem, dict):
            continue
        label = _SPECIAL_ELEMENT_LABEL.get(elem.get("type", ""), "")
        if not label:
            continue
        target = (elem.get("target_chapter") or "").strip()
        if target:
            parts.append(f"在「{target}」加入{label}")
        else:
            parts.append(f"加入{label}")

    body = "；".join(parts)
    return (
        f"\n\n---\n\n我會照你指定的格式來寫：{body}。這樣對嗎？"
        f"如果要調整，直接告訴我就好。"
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


def _sanitize_warn_explanation(text: str) -> str:
    """移除 explanation 內會破壞 marker 結構的方括號（AR R1 blocker）。

    WARN_MARKER_DEDUP_RE 的 [^\\]]* 遇到 body 內的 raw ] 會提前結束 match。
    對稱全形替換保留可讀性（[ ] → （ ）），不直接刪以免語意斷裂。
    """
    if not text:
        return ""
    return text.replace("]", "）").replace("[", "（")


def warn_marker(issue_count: int, explanation: str) -> str:
    """F1 WARN marker（append 進 methodology_note）。

    sanitize explanation 內的 [ ] 防破壞 dedup regex（AR R1 blocker）。

    2026-06-19：移除 100 字截斷（_WARN_EXPLANATION_MAX）。截斷原是 Bug B
    修「孤立括號半句」時隨手帶進的副產物，非設計需求——critic 查核說明是給
    使用者看「為何本章有疑慮」的，攔腰砍掉反傷信任，應完整輸出。
    sanitize 仍保留（dedup regex 靠 ] 當邊界，留 raw ] 會破壞 marker 結構）。
    """
    sanitized = _sanitize_warn_explanation(explanation or "")
    return (
        f"{WARN_MARKER_PREFIX}本章有 {issue_count} "
        f"處說法待進一步確認 — {sanitized}"
        f"（內容已保留；若希望這些待確認處更有依據，可回覆「再去找更多資料」讓我補搜）]"
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


# O5a 路徑(2)：KG（知識圖譜）merge 失敗時的 user-facing 降級旁白。
# per-run 只播一次（dedup flag 在 loop_engine._reset_per_run_dedup_flags）。
KG_MERGE_DEGRADED_NARRATION = (
    "提醒：這一輪的新資料未能併入知識圖譜，"
    "圖譜可能少了這部分內容，但文字研究仍會照常進行。"
)


# 檢索「出錯」（例外被 catch、該筆查詢被跳過）時的 user-facing 降級旁白
# （2026-06-20 prod：embedding 雙 provider 同失敗 → _execute_search 內
# retriever_search 拋例外 → 該筆查詢 silent 跳過，user 以為有補到資料）。
# 與 SEARCH_REQUIRED_DEGRADED_NARRATION（補搜「無結果」）語義不同，不可混用：
# 那是「查了但沒找到」，這是「查詢本身失敗」。
# per-run 只播一次（dedup flag 在 loop_engine._reset_per_run_dedup_flags）。
RETRIEVAL_ERROR_DEGRADED_NARRATION = (
    "提醒：這一輪有部分資料查詢暫時失敗，可能因此少蒐集到一些資料，"
    "研究仍會以現有資料繼續進行。"
)


# Stage 5 退回補搜（plan: lr-stage5-backward-recollect）。user-facing 文案，
# 不暴露 BAB / analyst / loop / engine 等內部術語。

# user 主動要求補搜 → confirm checkpoint（informed consent，清章節不可逆）。
RECOLLECT_CONSENT_PROMPT = (
    "了解，這部分需要再去找更多資料。要這麼做的話，我會回頭重新蒐集、"
    "重新整理並改寫，**目前已經寫好的章節會被重新撰寫取代**。\n"
    "如果想保留現在的內容，請先自行複製一份。\n"
    "確認要重新蒐集資料、重寫報告嗎？（回覆「確認」開始，或告訴我其他想法）"
)

# 系統判斷資料不足、自主退回補搜 → narration 告知（不停下等確認）。
RECOLLECT_NARRATION = (
    "我發現這部分的資料還不夠完整，正在回頭重新蒐集更多相關資料，"
    "再依新資料重新整理、改寫，目前的草稿會跟著更新。這會花一點時間。"
)

# 同一次研究補搜已達上限 → block + 明確告知（非 silent）。
RECOLLECT_CAPPED_NARRATION = (
    "這次研究已經重新蒐集過兩輪資料，仍然不夠充分。"
    "通常這代表目前可用的資料本身有限，再補也難有突破。"
    "建議調整研究問題的方向，或開一個新的研究重新規劃。"
)

# user 在 consent round 明確取消補搜 → 回常規 Stage 5 checkpoint（J，Codex #9：
# 從 orchestrator 硬編抽進 lr_copy 集中管理）。
RECOLLECT_CANCELLED_NARRATION = (
    "好的，那就先不重新蒐集，維持現在已經寫好的內容。"
)


# --- Backward Navigation（plan: lr-backward-nav, CEO 拍板 2026-06-19）---
# user-facing 文案，禁開發術語（test_lr_user_facing_strings_have_no_dev_jargon 掃描：
# 禁 BAB / grounding / evidence / LLM / Analyst / query / entity 等）。

# #2 退回上一階段通用通知（不打 LLM；接既有 showLRCheckpoint render path）。
# {stage_label} 由 orchestrator 帶入該 stage 的中文名稱（如「文筆設定」）。
NAV_BACK_NOTICE = (
    "好的，已經回到上一個階段。先前在這之後做的設定與草稿會重新整理，"
    "你可以在這裡重新調整，再繼續往下進行。"
)

# #3 Full restart（回 Stage 1）提醒：沿用已蒐集資料重新規劃；換差很多的新題目建議開新 session。
NAV_RESTART_NOTICE = (
    "好的，回到最開始重新規劃。這次會沿用先前已經整理好的資料，不重新蒐集，"
    "你可以重新確認或調整研究的主題與架構。\n"
    "如果你想換一個方向差很多的全新題目、不再需要先前的資料，"
    "建議直接開一個新的研究，會更乾淨俐落。"
)

# #4 清空已寫章節前的確認提示（複用 pending_recollect_confirmation 兩段式 confirm）。
NAV_RESTART_CONFIRM_PROMPT = (
    "重新規劃會清空目前已經寫好的章節（先前蒐集的資料會保留）。\n"
    "如果想保留現在的章節內容，請先自行複製一份。\n"
    "確定要重新規劃嗎？（回覆「確認」開始，或告訴我其他想法）"
)


# --- Stage 3 style analysis O7 input-type 守門（輸入被判定不是寫作範本）降級旁白 ---
# 語意降級通道（LLM 成功判定輸入非範本），與 LLM_UNAVAILABLE_NARRATION（系統失敗）分離：
# 前者 user 該換輸入，後者 user 該重試——文案不可混用。

STYLE_INPUT_NOT_SAMPLE_FIRST_NARRATION = (
    "你這句看起來比較像是想法或指令，而不是一段可以分析的文筆範本。"
    "如果想設定寫作風格，請貼一段你喜歡的文章或段落；"
    "或回覆「用預設就好」由我採用通用的學術風格。"
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


def problematic_chapter_line(section_index, title: str, reason_zh: str) -> str:
    """組單行「- 第 N 章「標題」（原因）」。章號 1-based（Bug G）。

    section_index 為 0-based int → +1；缺值 / 非 int（如 "?"）→ 退化 "?" 不 crash
    （AR R1 nit：用 type(x) is int 而非 isinstance，排除 bool 被當 int +1）。
    AR R2 should-fix：title 可能 None / 含換行 → normalize 成單行，否則破壞 markdown list。
    """
    ch = section_index + 1 if type(section_index) is int else "?"
    safe_title = " ".join(str(title or "?").split())  # None/換行 → 單行
    return f"- 第 {ch} 章「{safe_title}」（{reason_zh}）"


# AR R3/R4：把整個 problematic 組裝迴圈抽成 helper（不只單行）。
# reason_map 由 caller 傳入（= orchestrator 的 _PROBLEMATIC_REASON_ZH）—— AR R4 Codex:
# 不要在 lr_copy 重複定義 reason map，留 orchestrator 單一來源傳入，避免 drift。
def build_problematic_chapters_md(problematic: list, reason_map: dict) -> str:
    """組 problematic chapters 的 markdown 區塊（被 _run_stage_6 呼叫）。

    AR R3：整段組裝抽出來，test 直接測這個 = 測到 production path（非只測單行 helper）。
    reason_map 由 caller 傳（= orchestrator 的 _PROBLEMATIC_REASON_ZH），避免重複定義。
    """
    lines = []
    for s in problematic:
        reason_zh = reason_map.get(s.get("status", "?"), "未完成")
        lines.append(
            problematic_chapter_line(s.get("section_index"), s.get("title"), reason_zh)
        )
    return "\n".join(lines)


# --- References 缺項 ---

REFERENCE_MISSING_SENTINEL = "來源遺失"  # test 契約 sentinel（test_lr_references_block.py）


def reference_missing_entry(eid) -> str:
    """phantom citation 缺項行 — 保留 [eid] 編號，缺項不可變 silent（no silent fail）。"""
    return (
        f"[{eid}] *（{REFERENCE_MISSING_SENTINEL}：報告引用了編號 {eid}，"
        f"但系統來源庫中找不到對應資料）*"
    )


# --- R2 表格章節指涉澄清問句（2026-07，雙層 clarification 機制）---
# 純白話、零開發術語（lr_copy 全檔 AST jargon guard）。


def special_element_confirm_question(resolved_titles) -> str:
    """LLM 語意判 clear 時的確認式問句（接住 confident-wrong，friction 極低）。
    resolved_titles：LLM 判到的章名 list（一或多個，合併成一句）。"""
    if isinstance(resolved_titles, str):
        resolved_titles = [resolved_titles]
    joined = "、".join(f"「{t}」" for t in resolved_titles)
    return (
        f"我理解你想把特殊格式（表格／圖表）放在 {joined} 這（幾）章，對嗎？"
        f"如果是，回覆「對」我就放進去，如果想換一章，直接告訴我章名。"
    )


def special_element_clarification_question(unresolved_targets, chapter_names) -> str:
    """LLM 判 uncertain / 對不到時的完整枚舉問句（列暫定章名請 user 選，no silent fail）。"""
    joined = "、".join(f"「{t}」" for t in unresolved_targets)
    options = "、".join(f"{i + 1}）{n}" for i, n in enumerate(chapter_names))
    return (
        f"你想把特殊格式（如表格）放在哪一章呢？你剛提到的{joined}我一時對應不準。"
        f"目前規劃的章節有：{options}。請回覆章名或第幾個，我就放進去。"
    )


def special_element_target_unmatched_narration(unmatched_targets) -> str:
    """outline 定案後 target 對不到任何章的誠實旁白（no silent fail）。
    時點在寫章節前、已知會 filter 掉 → 語意要準（「不會自動放進」而非「可能沒放」）。"""
    joined = "、".join(f"「{t}」" for t in unmatched_targets)
    return (
        f"提醒：你指定要放表格／圖表的章節（{joined}）我對應不到最終章節，"
        f"因此不會自動放進報告。如果還需要，請用明確的章名再告訴我一次。"
    )
