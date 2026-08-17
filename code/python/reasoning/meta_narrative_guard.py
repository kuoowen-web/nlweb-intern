"""Deterministic meta-narrative 偵測（票 2026-08-13-c）。

獨立模組，供 orchestrator.py（缺陷 A：迴圈內 + post-loop sanitize）與
agents/critic.py（缺陷 B：review status override）共用，避免兩者互相 import
形成循環依賴（orchestrator.py 已 import critic.py 建構 self.critic）。

⚠ 票 2026-08-14-a：本檔的兩段替換文案已移至
`core/contracts/user_facing_copy`（user-facing 文案單一事實源）。
該模組是**零 import 的純字串模組**（那是它自己的硬約束），所以從本檔
import 它不可能重新引入循環依賴——方向是安全的。
"""

from core.contracts.user_facing_copy import (
    CRITIC_FIELD_SANITIZED,
    DR_REPORT_SANITIZED,
)

# field marker：schema 內部欄位名 / instructor 錯誤訊息關鍵字。
# 涵蓋 should-fix 1 補的兩個遺漏欄位：sources_used（schemas.py:93，
# WriterComposeOutput 的欄位，Writer 层同型 reask 污染也可能命中同一詞）、
# relationships（schemas_enhanced.py:302，KnowledgeGraph 的欄位）。
_META_NARRATIVE_FIELD_MARKERS = (
    "DRAFT_READY", "SEARCH_REQUIRED", "Validation Error",
    "reasoning_chain", "citations_used", "argument_graph",
    "gap_resolutions", "knowledge_graph", "sources_used", "relationships",
)
# tone marker：後設語氣句式，中英文各一份（should-fix 2：LLM 若用英文
# 改寫自白，純中文 marker 會漏網）。
_META_NARRATIVE_TONE_MARKERS = (
    "欄位應填", "欄位為空", "驗證規則", "因此被系統退回", "被系統退回",
    "違反了", "Recall the function", "fix the errors",
    "正確的回覆格式", "正式回覆內容", "依序說明每個欄位",
    "was previously rejected", "violates the validation rule",
    "which requires at least", "corrected response",
)


def looks_like_meta_narrative(text: str) -> bool:
    """判斷文字是否疑似 instructor reask 污染產生的系統/schema 自白。

    組合訊號判準：field marker 與 tone marker 都至少命中一個才判 True——
    單一類命中不足（field marker 單獨出現 = 研究內容剛好提及技術詞彙；
    tone marker 單獨出現 = 研究內容剛好談驗證/schema 主題本身，兩者都是
    合法研究內容，不該被誤傷）。
    """
    if not text:
        return False
    field_hit = any(marker in text for marker in _META_NARRATIVE_FIELD_MARKERS)
    tone_hit = any(marker in text for marker in _META_NARRATIVE_TONE_MARKERS)
    return field_hit and tone_hit


def sanitize_meta_narrative_draft(draft: str) -> str:
    """命中 looks_like_meta_narrative 時 full-replace 成中性使用者文案。

    形態抄 LR lr_copy.py::critic_rejected_content —— 確定性替換，不嘗試
    保留原文任何片段、不回注 LLM 重寫（重寫仍可能再次觸發同一污染源）。
    冪等：替換後的中性文案不含任何 field/tone marker，重複呼叫是 no-op。

    ⚠ R3 修訂（回應 AR R2 blocker BR2-1，見 Task A3 的完整說明）：這段文案
    會在 Task A3（以及 R4 新增的 Task A4）被直接塞進
    `WriterComposeOutput.final_report`，該欄位有 `min_length=200` 硬約束
    （`schemas.py:78-82`），且 `WriterComposeOutputEnhanced` 繼承該約束未覆寫
    （`schemas_enhanced.py:230-235` 親查確認）。

    ⚠ 文案本體已於票 2026-08-14-a 移至
    `core/contracts/user_facing_copy.DR_REPORT_SANITIZED`（user-facing 文案
    單一事實源）。本函式只負責「判定命中就整段替換」這個動作。

    ⛔ 仍然成立的硬約束：替換文會被塞進 `WriterComposeOutput.final_report`，
    該欄位 `min_length=200`（`schemas.py:78-82`），`WriterComposeOutputEnhanced`
    繼承未覆寫（`schemas_enhanced.py:230-235`）。鎖住它的測試已隨文案一起
    搬到 `tests/unit/contracts/test_user_facing_copy.py::
    test_meta_narrative_replacement_meets_writer_schema_min_length`。
    **不要在本檔重複寫一份字數斷言**——兩份會漂。
    """
    if not looks_like_meta_narrative(draft):
        return draft
    return DR_REPORT_SANITIZED


def matches_instructor_reask_signature(text: str) -> bool:
    """第一層判準（BR1-1 修訂，票 2026-08-14）：instructor（專案 venv 鎖
    1.15.3，見 plan「驗偽：instructor 模板字面內容」段環境事實更正）
    `reask_responses_tools()`（`v2/providers/openai/handlers.py:156-191`）
    在欄位驗證失敗（如 min_length）時產生的 reask 訊息模板，同時含以下
    三段字面內容：

        f"Validation Error found:\n{exception}\n"
        "Recall the function correctly, fix the errors with "
        f"{tool_call.arguments}{details}"

    三段同時出現，機率上不是正常研究批評的自然產物（親讀套件原始碼確認，
    非推測）。不受 draft_is_dirty 影響——即使 draft 本身已判定髒，這個
    欄位本體若也獨立命中同一組 reask 特徵，代表它自己的 min_length
    validator 也觸發了 reask，與 draft 的污染是兩件獨立的事，必須攔截
    （修 BR1-1：原方向 A 用 draft_is_dirty 整段放行，漏判此情境）。

    已知窄縫：instructor 還有另一種模板（response is None 時，無
    "with"），本函式不覆蓋——那個分支對應 API 層級失敗，不是 critique/
    explanation 的 min_length reask 必經路徑，不在本 plan 攻擊面內
    （見 plan Self-Review 已知代價段）。
    """
    return (
        "Validation Error found" in text
        and "Recall the function correctly" in text
        and "fix the errors with" in text
    )


def sanitize_critic_field_text(text: str) -> str:
    """命中 looks_like_meta_narrative 時，把 Critic 的 critique/explanation
    欄位 full-replace 成中性文案。

    nit 2（AR R2）判斷結果：本函式只用 looks_like_meta_narrative 判斷是否
    替換，未含第一層 matches_instructor_reask_signature——刻意維持，不改。
    理由：呼叫端（critic.py 的 review()）已經跑過完整的兩層判準
    （_critique_field_is_polluted）才決定是否呼叫本函式；本函式收到呼叫
    時「要不要替換」這件事已經確定，本函式只需要單一職責地完成
    full-replace 動作，不需要重新跑一次上游已經跑過的判準邏輯。若本函式
    自己也疊一層 matches_instructor_reask_signature 判斷，會產生兩個問題：
    (1) 職責重疊——呼叫端已判斷「污染」才呼叫本函式，本函式再自己判斷一次
    「其實不是污染就不換」，語意上互相打架；(2) 若未來呼叫端邏輯改變（例如
    新增第三層判準），本函式的第二次判斷會跟呼叫端不同步，反而更難維護。
    現有字串下不影響行為，維持現狀。

    與 sanitize_meta_narrative_draft() 分開一支的理由：那份文案的語境是
    『研究報告整理失敗』（給使用者看的完整報告替代內容），語意對準
    WriterComposeOutput.final_report 這種報告本體欄位。critique/explanation
    是 Critic 對 draft 的評論文字，語境應該是『這則評論本身無法正常產出』，
    直接沿用報告語境的文案會文不對題（讀者看到『本次研究在整理階段發生
    內部處理問題』出現在應該是『評論』的欄位裡，邏輯不通）。

    ⚠ 文案本體已於票 2026-08-14-a 移至
    `core/contracts/user_facing_copy.CRITIC_FIELD_SANITIZED`（user-facing 文案
    單一事實源）。本函式只負責「判定命中就整段替換」這個動作。

    ⛔ 仍然成立的長度需求：critique `min_length=50`／explanation
    `min_length=20`，本文案遠高於兩者。**不要在本檔重複寫一份字數斷言**
    ——兩份會漂（本 docstring 原稿就曾誤寫「236 字元」，那是 DR 那支的數字）。
    真正 load-bearing 的論證是「遠高於 min_length」而非某個精確數字。
    """
    if not looks_like_meta_narrative(text):
        return text
    return CRITIC_FIELD_SANITIZED


# ─────────────────────────────────────────────────────────────────
# LR（Live Research）專屬第二層訊號（票 2026-08-14-c，R3 版）。
#
# R2 版曾把這份詞表併入一個組合判準（field marker × tone marker）當作
# gate——實測對「刻意設計成同時命中兩者的正常研究敘述」會誤傷（見
# lr-narration-meta-guard-plan.md 判準方向決策段），根因是 DR 的
# 「技術詞彙罕見共現」防呆前提在 LR 不成立（LR field marker 如
# confidence/delta/topics 是日常研究對話常見詞）。
#
# R3 版：這份詞表只用於 log-only 觀察訊號，不做為 sanitize 的觸發條件。
# gate 判準只用既有 matches_instructor_reask_signature()（第一層，零已知
# 誤傷）。保留這份詞表與判斷函式是為了讓 in-house 團隊未來若觀察到 prod
# 真的出現短轉述型污染（第一層漏判的已知代價），有 log 資料可以評估是否
# 需要重新收緊，不是靠現在盲猜一個門檻。
#
# 運維閉環（R4 新增，SF-3 修訂——log-only 若沒人看等於死碼）：
# - 誰看：LR 功能的 in-house 維護者（目前即本票執行/驗收方），非自動化
#   系統。log 進標準 logging（WARNING 等級，訊息前綴
#   LR_META_GUARD_SECOND_LAYER_SIGNAL），走既有 log 蒐集管線（同其他
#   WARNING 等級 log，無需新建告警通道）。
# - 什麼時候看：不是每次觸發都要即時反應（log-only 的本意就是低頻觀察，
#   非即時攔截），而是在下列時機查詢彙總：(1) 定期（例如與其他 LR log
#   健康度一起）巡檢時 grep 這個訊息前綴的出現頻率；(2) 若 CEO/使用者
#   回報「LR narration 出現看起來像系統內部訊息的怪異文字」，第一步就是
#   查這個 log 訊號有沒有命中同一時段，用來快速定位是不是本已知代價
#   （短轉述污染）發生。
# - 看到之後做什麼：若某段時間窗內命中次數異常偏高（例如單一 session
#   內重複出現、或短期內出現頻率明顯提升），代表以下兩種情況之一需要
#   評估：(a) field marker 詞表需要調整（可能新增了某個常見到會被
#   誤觸發的日常詞，需要收窄詞表）；(b) 真的觀察到短轉述污染的 prod
#   實例增多，此時才評估是否要把第二層從 log-only 升級回 gate（升級
#   前必須重新跑一次判準方向決策段的誤傷壓力測試，不可直接照搬 R2 版
#   已被證偽的組合判準）。單次命中或偶發命中不構成升級理由——這個訊號
#   本質是低頻觀察用，不是即時告警。
# ─────────────────────────────────────────────────────────────────

_LR_ASSOCIATOR_FIELD_MARKERS = (
    # 12 詞逐字取自 reasoning/prompts/associator.py 三處「敘述行為」段落
    # （:130 / :254 / :396，三處逐字核對一致）明文禁止 LLM 在 narration
    # 使用的欄位名字面（沿用票 2026-08-14-c R2 版已驗證的詞表，此次未變）。
    "topics", "relations", "is_stable", "followup_questions", "v0", "v1",
    "context_map", "search_seeds", "confidence", "delta",
    "source_topic_id", "target_topic_id",
    # 3 詞：schema 類名（非 prompt 明文詞），涵蓋「轉述污染提及 schema
    # 類名而非欄位名」這個次型態（R1/R2 已驗證此次型態存在）。
    "AssociatorBuildOutput", "AssociatorDeriveOutput", "AssociatorRefineOutput",
)


def looks_like_lr_field_pollution(text: str) -> bool:
    """LR 專屬第二層觀察訊號（log-only，非 gate）。

    field marker（LR associator 欄位詞彙）與既有 tone marker（後設語氣
    句式）都命中才算訊號——這個組合判準本身沒有變（沿用 R2 版邏輯），
    變的是**呼叫端怎麼用這個結果**：R2 版拿它當 sanitize 的觸發條件之一，
    R3 版只拿它記警告 log，不觸發任何替換動作。呼叫端見
    reasoning/live_research/lr_sanitize.py。
    """
    if not text:
        return False
    field_hit = any(marker in text for marker in _LR_ASSOCIATOR_FIELD_MARKERS)
    tone_hit = any(marker in text for marker in _META_NARRATIVE_TONE_MARKERS)
    return field_hit and tone_hit
