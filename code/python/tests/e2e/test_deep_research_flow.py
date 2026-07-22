"""
E2E: 深度研究（DR）完整流程 + KG 編輯 rerun 行為層（full-scan P1 批6 FE-1）。

⚠ 這兩條**燒真實 LLM 錢**（真跑 performDeepResearch 推論鏈，數分鐘 + $）——與其餘
render/HTTP 探測型 E2E 不同，預設 **skip**，需 `NLWEB_ALLOW_REAL_LLM=1` 顯式 opt-in
才跑（比照 contract test 的燒錢 gate）。

驗的是 FE-1 KG rerun bug 的**行為層**（cachebust 那檔驗的是架構根因＝版本一致；這裡驗
真跑一次後 confirmKGEdit 真的讀得到 query_id、不彈 alert）：
  test_dr_full_flow_produces_report — DR 完整跑通、產出報告 + KG（含 query_id 寫入）
  test_kg_edit_rerun_finds_query_id — 編輯 KG → confirmKGEdit → 不彈
    `alert('找不到原始研究的 query_id')`（FE-1 修復前：module 分裂讀到 null → alert）

行為鏈（親讀 knowledge-graph.js:1693-1732 / deep-research.js:1450）：
  performDeepResearch 寫 _currentResearchQueryId（instance A）→ confirmKGEdit :1727
  讀 getCurrentResearchQueryId()，null → :1732 alert '找不到原始研究的 query_id'。
  修復＝news-search / knowledge-graph 兩 importer 的 deep-research.js 版本一致 → 同
  instance → query_id 共享 → confirmKGEdit 讀得到。

alert 用 hook 攔截記錄（不觸發真 dialog block 自動化）。env：E2E_BASE_URL、E2E_EMAIL、
E2E_PASSWORD、NLWEB_ALLOW_REAL_LLM=1。
"""
import os
import time

import pytest
from playwright.sync_api import Page

# 燒錢 gate：未 opt-in 整檔 skip（真跑 DR 推論鏈 = 真 LLM 錢 + 寫 session）
pytestmark = pytest.mark.skipif(
    os.environ.get("NLWEB_ALLOW_REAL_LLM", "").strip() != "1",
    reason="DR 完整流程燒真實 LLM 錢——需 NLWEB_ALLOW_REAL_LLM=1 顯式 opt-in 才跑",
)

# ── DR / KG rerun selector 常數區（單一事實來源；MCP 探索 2026-07-21 回寫）────────
# MCP 探索確認（prod twdubao.com）的關鍵事實：
# - 「進階搜尋」按鈕的 data-mode === 'deep_research'（UI 文字≠mode 值！這是 DR 真入口，
#   非結果頁的「深度研究報告」view tab——那只是呈現視圖不觸發 DR）。mode 按鈕
#   （.mode-btn-inline）在搜尋框互動後才 visible。
# - 進階搜尋 popup 有「啟用知識圖譜」checkbox → 產出 KG（KG rerun 前提）。
# - DR 觸發 = 切 deep_research mode → 輸入問題 → 點 #btnSearch。
DR_MODE_SELECTOR = ".mode-btn-inline[data-mode='deep_research']"  # 「進階搜尋」按鈕 = DR mode
# 切 DR mode 會自動開 #advancedSearchPopup（有知識圖譜 toggle），它蓋住 #btnSearch，
# 必須先在 popup 內勾「啟用知識圖譜」→ 關 popup 才能搜尋。KG rerun 前提＝KG 有產出。
DR_ADV_POPUP_SELECTOR = "#advancedSearchPopup"
DR_KG_ENABLE_TOGGLE = "#kgToggle"        # 「啟用知識圖譜」checkbox（KG rerun 必須啟用）
DR_POPUP_CLOSE_SELECTOR = "#popupClose"  # 關進階搜尋 popup
SEARCH_INPUT_SELECTOR = "#searchInput"
SEARCH_SUBMIT_SELECTOR = "#btnSearch"
# DR 有 clarification 前置（addClarificationMessage → submitClarification → performDeepResearch）：
# 搜尋後彈澄清卡，需選一個 option chip（enable 送出鈕）再送出才真跑 DR。
DR_CLARIFY_CARD_SELECTOR = ".clarification-actions"    # 澄清卡出現訊號
DR_CLARIFY_OPTION_SELECTOR = ".option-chip[data-option-id]"  # 面向選項（選一個 enable 送出）
DR_CLARIFY_SUBMIT_SELECTOR = ".submit-clarification"  # 送出澄清 → 真跑 performDeepResearch
# ⚠ KG id prefix 是 instance 專屬（knowledge-graph.js:1988-1989）：
#   DR = createKGInstance('kg') → #kgEditToggleBtn / #kgGraphView
#   LR = createKGInstance('lrKG') → #lrKGEditToggleBtn
#   本檔跑 DR 流程，一律用 'kg' prefix（不是 lrKG——那是 LR 的，混用會永遠等不到）。
DR_KG_TOGGLE_SELECTOR = "#kgGraphView"           # DR 產出的 KG 容器（有節點 = KG 就緒）
DR_REPORT_DONE_SELECTOR = "#kgEditToggleBtn"     # KG 編輯鈕可見 = DR+KG 完成（串流結束訊號）
# KG 編輯 rerun（親讀 knowledge-graph.js:1061 addEventListener / :1693 confirmKGEdit）：
KG_EDIT_TOGGLE_SELECTOR = "#kgEditToggleBtn"     # 進入 KG 編輯模式（DR instance）
KG_CONFIRM_EDIT_SELECTOR = "#kgConfirmEditBtn"   # 確認送出 = confirmKGEdit（DR instance）
# ────────────────────────────────────────────────────────────────────────────

_QID_ALERT = "找不到原始研究的 query_id"  # FE-1 失敗表現（knowledge-graph.js:1732）

_ALERT_HOOK = """() => {
    if (!window.__e2e_alert_hooked) {
        window.__e2e_alerts = [];
        window.alert = (m) => { window.__e2e_alerts.push(String(m)); };
        window.confirm = () => true;   // KG 編輯確認自動放行
        window.__e2e_alert_hooked = true;
    }
    return true;
}"""


def _run_deep_research(page: Page, query: str = "台積電先進製程與地緣政治風險"):
    """登入態 page 上真跑一次 DR（切 deep_research mode → 輸入 → 搜尋 → 等 KG 就緒）。

    回傳 kg_ready（KG 是否真產出節點）。燒 LLM 錢。DR mode 需先啟用知識圖譜才產 KG。
    query_id 是否讀得到不直接驗變數（實作細節、無可靠讀法）——改由 confirmKGEdit 行為
    驗（不彈 '找不到 query_id' alert = getCurrentResearchQueryId 讀得到 = FE-1 修復成立）。
    """
    page.evaluate(_ALERT_HOOK)

    # 1) 切到 deep_research mode（「進階搜尋」按鈕；mode 按鈕需搜尋框互動後才顯示）
    page.click(SEARCH_INPUT_SELECTOR)
    page.wait_for_selector(DR_MODE_SELECTOR, state="visible", timeout=10000)
    page.click(DR_MODE_SELECTOR)

    # 2) 進階搜尋 popup 自動開 → 勾「啟用知識圖譜」（KG rerun 前提）→ 關 popup
    page.wait_for_selector(DR_ADV_POPUP_SELECTOR, state="visible", timeout=10000)
    kg_checkbox = page.locator(DR_KG_ENABLE_TOGGLE)
    if not kg_checkbox.is_checked():
        kg_checkbox.check()
    page.click(DR_POPUP_CLOSE_SELECTOR)
    page.wait_for_selector(DR_ADV_POPUP_SELECTOR, state="hidden", timeout=5000)

    # 3) 輸入問題 + 送出（彈 clarification 澄清卡）
    page.fill(SEARCH_INPUT_SELECTOR, query)
    page.click(SEARCH_SUBMIT_SELECTOR)

    # 4) 處理 clarification 前置：選一個面向 option → 送出（→ 真跑 performDeepResearch）
    #    （DR 卡在此卡不會產 KG——第一次真跑實證，addClarificationMessage 的澄清卡）
    page.wait_for_selector(DR_CLARIFY_CARD_SELECTOR, state="visible", timeout=60000)
    page.locator(DR_CLARIFY_OPTION_SELECTOR).first.click()  # 選第一個面向 → enable 送出鈕
    submit = page.locator(DR_CLARIFY_SUBMIT_SELECTOR).first
    submit.wait_for(state="visible", timeout=5000)
    submit.click()

    # 5) 等 DR 完成——完成訊號 = KG 真的有節點（#kgGraphView 內有 SVG 圖形）。
    #    不用「編輯鈕 visible」：#kgEditToggleBtn 頁面載入即 exists（DR/LR 兩 instance
    #    的 DOM 都建了），有提前誤判風險。KG 有節點才是 DR 真跑完產出 KG 的可靠訊號。
    #    DR 是重流程（分析來源→可信度→事實查核→規劃→撰寫報告→KG），實測 >15 分鐘 →
    #    給 18 分鐘，輪詢 2s，timeout 前 dump 卡點供診斷不盲 fail。
    def _kg_node_count():
        return page.evaluate(
            """() => {
                const g = document.querySelector('#kgGraphView');
                if (!g || g.offsetParent === null) return 0;
                return g.querySelectorAll('circle, .kg-node, g.node, [class*=node]').length;
            }"""
        )

    deadline = time.monotonic() + 1080  # 18 分鐘
    kg_ready = False
    while time.monotonic() < deadline:
        if _kg_node_count() > 0:
            kg_ready = True
            break
        page.wait_for_timeout(2000)
    if not kg_ready:
        progress = page.evaluate(
            """() => {
                const panel = [...document.querySelectorAll('*')].find(
                    e => /深度研究進行中/.test(e.textContent||'') && e.offsetParent!==null
                );
                return panel ? panel.innerText.slice(0, 500) : '(DR 進度面板已消失——DR 可能已完成但未產出 KG)';
            }"""
        )
        print(f"\n[DR E2E] {int(time.monotonic()-(deadline-1080))}s 內 KG 無節點，DR 卡點：\n{progress}\n")

    return kg_ready


def test_dr_full_flow_produces_report(logged_in_page: Page):
    """DR 完整流程真跑通：推論鏈完成 + KG 真產出節點（#kgGraphView 有圖）。"""
    kg_ready = _run_deep_research(logged_in_page)
    assert kg_ready, "DR 在 18 分鐘內未產出 KG 節點（DR 卡住 / 未啟用知識圖譜 / selector 過期）"
    alerts = logged_in_page.evaluate("() => window.__e2e_alerts || []")
    assert _QID_ALERT not in " ".join(alerts), f"DR 流程中彈了 query_id alert：{alerts}"


def test_kg_edit_rerun_finds_query_id(logged_in_page: Page):
    """FE-1 行為層：DR 後編輯 KG → confirmKGEdit → 不彈 '找不到 query_id' alert。

    這是 FE-1 的真實測點——confirmKGEdit :1727 讀 getCurrentResearchQueryId()，
    module 分裂時讀到 null → :1732 alert。不彈 alert = query_id 讀得到 = 修復成立。
    """
    kg_ready = _run_deep_research(logged_in_page)
    assert kg_ready, "前置 DR 未成功產出 KG，無法測 KG rerun"

    page = logged_in_page
    # 進 KG 編輯模式（DR instance 的編輯鈕 #kgEditToggleBtn）
    edit_btn = page.locator(KG_EDIT_TOGGLE_SELECTOR)
    edit_btn.wait_for(state="visible", timeout=30000)
    edit_btn.click()
    # 確認送出 = confirmKGEdit（編輯內容非重點；重點是 confirmKGEdit 讀得到 query_id）
    confirm_btn = page.locator(KG_CONFIRM_EDIT_SELECTOR)
    confirm_btn.wait_for(state="visible", timeout=10000)
    confirm_btn.click()
    page.wait_for_timeout(3000)

    alerts = page.evaluate("() => window.__e2e_alerts || []")
    assert _QID_ALERT not in " ".join(alerts), (
        f"confirmKGEdit 彈了 '{_QID_ALERT}'（FE-1 迴歸：module 分裂，getCurrentResearchQueryId "
        f"讀到 null）！alerts={alerts}"
    )
