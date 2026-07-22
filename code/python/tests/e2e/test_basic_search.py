"""
基本搜尋流程 E2E 回歸腳本。
前置：server 已起且有 indexed data。
依賴 logged_in_page fixture（真實登入，不 bypass）。
E2E 鐵律：跑到 final output——截圖與斷言都在「串流結束」之後，
不可在 SSE 還在串流時就判 PASS（縮水 acceptance，見 feedback_e2e_testing.md）。
"""
import os

from playwright.sync_api import Page, expect

# ── MCP 探索段（2026-07-08）確認的真實 selector ────────────────────────────
# sentinel 防呆：未替換前 module-level assert 直接 fail-loud。本模組只在
# 顯式跑 e2e 時才被 import（norecursedirs 保證裸 pytest 不會踩到這個 assert）。
#
# 串流結束訊號（source 驗證：static/js/features/search.js）：
# - setProcessingState(false) 在 handlePostStreamingRequest return（SSE complete）
#   之後才執行：#btnSearch 重新顯示 + #btnStopGenerate 隱藏（display:none）。
#   兩訊號都存在 → 都保留、雙重確認（plan Task 3 Step 1 二擇一條款的「都留」分支）。
# - 落選候選（誤當 acceptance 會在串流中間態誤判 PASS）：
#   * #loadingState.active——onArticles（首批結果到達）就移除，是中途訊號；
#   * #aiSummaryContent .source-info「讀豹基於 N 則報導生成」——onAnswer
#     （complete 之前）就渲染。
# - skeleton 佔位卡同樣帶 .news-card class（search.js 以 :not(.skeleton-card)
#   計數），RESULT_ITEM 必須排除，否則會把骨架卡數進結果。
_REPLACE_ME = "REPLACE_ME_AFTER_MCP_EXPLORATION"

SEARCH_INPUT_SELECTOR  = "#searchInput"                             # 搜尋輸入框（textarea）
SEARCH_SUBMIT_SELECTOR = "#btnSearch"                               # 搜尋送出 button
RESULT_ITEM_SELECTOR   = "#listView .news-card:not(.skeleton-card)" # 單條結果（排除 skeleton）
STREAM_DONE_SELECTOR   = "#btnSearch"                               # 串流結束標誌（結束後才重新顯示）
LOADING_SELECTOR       = "#btnStopGenerate"                         # 串流中標誌（結束後隱藏；常駐 DOM 故斷言用 hidden 非 count=0）
# ──────────────────────────────────────────────────────────────────────────────

_SELECTORS = {
    "SEARCH_INPUT_SELECTOR": SEARCH_INPUT_SELECTOR,
    "SEARCH_SUBMIT_SELECTOR": SEARCH_SUBMIT_SELECTOR,
    "RESULT_ITEM_SELECTOR": RESULT_ITEM_SELECTOR,
    "STREAM_DONE_SELECTOR": STREAM_DONE_SELECTOR,
    "LOADING_SELECTOR": LOADING_SELECTOR,
}
_unreplaced = [k for k, v in _SELECTORS.items() if "REPLACE_ME" in v]
assert not _unreplaced, (
    f"selector 尚未由 MCP 探索替換：{_unreplaced}。"
    f"先跑 chrome-devtools MCP 探索段（two-stage-e2e-plan.md Task 3 Step 1）"
    f"確認真實 selector 再執行。"
)

TEST_QUERY = "台灣"  # 穩定觸發結果的 query，不依賴特定新聞

SEARCH_TIMEOUT_MS = int(os.environ.get("E2E_SEARCH_TIMEOUT_MS", "120000"))


def test_basic_search_returns_results(
    logged_in_page: Page, screenshots_dir
) -> None:
    """
    輸入 query → 等 SSE 串流「結束」→ 才截圖 + 斷言至少一條結果。
    判定標準：final output 狀態的截圖（不是串流中間態）。
    """
    page = logged_in_page

    # 1. 輸入搜尋 query
    page.fill(SEARCH_INPUT_SELECTOR, TEST_QUERY)

    # 2. 送出搜尋（點擊 button，不用 keyboard dispatch 避免 isTrusted 問題）
    #    點擊後 setProcessingState(true)：#btnSearch 隱藏、#btnStopGenerate 顯示。
    page.click(SEARCH_SUBMIT_SELECTOR)

    # 3. 等第一筆結果出現（中途訊號，確認 pipeline 有動；非 acceptance）
    expect(
        page.locator(RESULT_ITEM_SELECTOR).first
    ).to_be_visible(timeout=SEARCH_TIMEOUT_MS)

    # 4. 等串流「結束」——E2E 必須到 final output（setProcessingState(false)
    #    在 SSE complete 之後才執行，兩訊號雙重確認）
    expect(page.locator(STREAM_DONE_SELECTOR)).to_be_visible(timeout=SEARCH_TIMEOUT_MS)
    expect(page.locator(LOADING_SELECTOR)).to_be_hidden(timeout=SEARCH_TIMEOUT_MS)

    # 5. final output 之後才截圖
    screenshot_path = os.path.join(screenshots_dir, "search_results.png")
    page.screenshot(path=screenshot_path)

    # 6. 斷言至少一條結果（final 狀態下計數）
    result_count = page.locator(RESULT_ITEM_SELECTOR).count()
    assert result_count >= 1, (
        f"Expected at least 1 result for query '{TEST_QUERY}', got {result_count}. "
        f"截圖：{screenshot_path}"
    )
