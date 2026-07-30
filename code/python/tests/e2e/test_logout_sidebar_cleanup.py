"""
E2E: backlog 票 2026-05-26-a 症狀②驗證 — logout 後左側欄 session 清單
是否殘留 stale `.left-sidebar-session-item` DOM。

背景：`clearUserScopedState`（static/js/core/state-sync.js）於 logout / 401
時清空 `_savedSessions`（in-memory）後呼叫 `resetMainUI()` →
`renderLeftSidebarSessions()`（static/js/features/sessions-list.js）重繪
`#leftSidebarSessions` 容器：陣列長度 0 時 `container.innerHTML = ''`。
本腳本 first-hand 實測這條鏈是否真的清空 DOM，而非只清記憶體。

斷言用 `.locator(...).count()`（DOM 節點是否存在），不用 `is_visible()`——
logout 流程稍後會呼叫 `hideMainUI()` 把整個 `#leftSidebar` 設 `display:none`，
若用可見性判斷，「側欄整層被藏起來」跟「session item 真的被清空」兩種情況
都會回傳「不可見」，無法區分「DOM 殘留但視覺隱藏」與「DOM 真的被清空」。
count() 直接讀 DOM 節點數，不受祖先 display:none 影響，才是本票要驗的東西。

真實登入／真實 logout，不 bypass。不觸發任何搜尋 / DR / LR（不燒 LLM）。
"""
from playwright.sync_api import Page, expect

SESSION_ITEM_SELECTOR = ".left-sidebar-session-item"


def test_logout_clears_sidebar_session_items(
    logged_in_page: Page, login_selectors, screenshots_dir
) -> None:
    page = logged_in_page

    # 1. 確認登入態下側欄至少有 1 個 session item，否則無法驗「殘留」
    #    （帳號需至少有 1 筆已存 session；15 個上限 render，不影響本測試）
    session_items = page.locator(SESSION_ITEM_SELECTOR)
    expect(session_items.first).to_be_attached(timeout=10000)
    count_before = session_items.count()
    assert count_before >= 1, (
        f"登入後側欄 0 個 {SESSION_ITEM_SELECTOR}（測試帳號無已存 session，"
        f"無法驗證『logout 後是否殘留』——需要至少 1 筆既有 session 才能跑本測試）。"
    )

    # 2. 開 Settings popover → 點登出（真實 UI 操作，不用 fetch 繞過）
    page.click("#btnSettings")
    page.wait_for_selector("#btnLogout", state="visible", timeout=5000)
    page.click("#btnLogout")

    # 3. 等登出完成的可判定訊號：登入態清除後 auth modal 自動彈出
    #    （showAuthModal('login') 是 _handleAuthFailure 尾端呼叫，見
    #    static/js/core/auth-manager.js:351）——用 email input 可見當訊號，
    #    比等 networkidle 穩（本 app analytics beacon 常駐連線，networkidle 不可靠）。
    page.wait_for_selector(login_selectors.email_input, state="visible", timeout=10000)

    # 4. 核心斷言：DOM 節點數（非可見性）——判定「是否殘留 stale DOM」
    count_after = page.locator(SESSION_ITEM_SELECTOR).count()

    if count_after > 0:
        page.screenshot(path=f"{screenshots_dir}/logout_sidebar_stale_dom.png")

    assert count_after == 0, (
        f"logout 後 {SESSION_ITEM_SELECTOR} 殘留 {count_after} 個 DOM 節點"
        f"（登入時 {count_before} 個）——clearUserScopedState 未能清空側欄 DOM，"
        f"票 2026-05-26-a 症狀②實測 FAIL。截圖：logout_sidebar_stale_dom.png"
    )
