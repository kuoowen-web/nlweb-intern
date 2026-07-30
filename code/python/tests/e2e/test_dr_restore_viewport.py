"""
E2E: DR session 切換 → 報告捲入視口首屏（票 2026-07-28-d，scroll-only 修復）。

驗的是 `loadSavedSession` DR restore 分支的 **viewport bring-in** 行為：
點入一個已存在的 DR session（含 research_report + chat_history），報告 render 進
`#researchView` 後視口是否被捲到報告首屏。

紅綠 gate（TDD）：
  - 修法前（news-search.js 無 scrollIntoView）：`#researchView` top 在視口下方兩屏外
    （rvTop > innerHeight）→ reportInView 斷言 FAIL（red baseline）。
  - 修法後（rAF + scrollIntoView）：報告捲入視口（rvTop <= innerHeight）→ PASS（green）。

fixture 切點（memory「fixture 切在 raw data 蒐集、不切在 LLM reasoning」）：
  **不真跑 DR**（不燒 LLM 錢、不跑數分鐘推論鏈）。改用「已種好 / 既有的 DR session」
  （DB search_sessions row 含 research_report + chat_history），腳本只點入該 session
  觸發 loadSavedSession restore 路徑——這正是 bug 所在路徑。DR session 由
  scratchpad/seed_dr.py 種入本地 PG（見 SEEDED_DR_SESSION_ID）。

R2 裁定：DR session 的 chat 為本 session 合法資料還原（DB chat_history 有值 + DR 分支
本就顯示 chat container），**非殘影、不清**。故本腳本除主 AC reportInView 外，另驗
chatDataIntact（chat replay 未受 scroll 改動干擾，節點數對應 DB chat_history 長度）。

env：E2E_BASE_URL、E2E_EMAIL、E2E_PASSWORD（conftest fixture 讀取，fail-loud）。
需 server（本地 8001）+ 瀏覽器 + 真帳號（本地手動，不進 CI）。
"""
import time

from playwright.sync_api import Page

# ── DR restore viewport selector 常數區（單一事實來源；chrome-devtools MCP 探索
#    2026-07-28 回寫，登入 http://localhost:8001/app 實走確認）───────────────────
# MCP 探索確認的關鍵事實：
# - sidebar session 列表的**可點列** = `.left-sidebar-session-item`，帶屬性
#   `data-sidebar-session-id`（**非** `data-session-id`——後者只在 rename/share/delete
#   下拉選單 item 上）。點該列觸發 loadSavedSession（click handler 由 addEventListener
#   掛載，非 inline onclick）。
# - session 標題在 `.left-sidebar-session-title` span（用於辨識 DR session）。
# - `#researchView` = DR 報告 render 容器；`#chatMessages` = chat replay 容器
#   （.chat-message 節點）。
# - `window.getChatHistory` **不可及**（module scope）→ chatDataIntact 改用 DOM
#   `.chat-message` 節點數對照已知 DB chat_history 長度（plan R2 閉環註記）。
SESSION_ROW = ".left-sidebar-session-item[data-sidebar-session-id]"


def _row_selector(sid: str) -> str:
    return f'.left-sidebar-session-item[data-sidebar-session-id="{sid}"]'


# ── 種好的 DR session（scratchpad/seed_dr.py + update_seed.py 種入本地 PG）──────
# admin@twdubao.com 名下、含 research_report(1613 字) + chat_history(6 則)。
# chat 6 則把 #researchView top 推到視口下方（rvTop 964 > innerH 732，MCP 實測
# bugRepro:true）→ 同時提供 reportInView red baseline + chatDataIntact 6 節點對照。
SEEDED_DR_SESSION_ID = "90334949-53ee-49a2-be52-59074868ab05"
SEEDED_DR_CHAT_LEN = 6  # 種入的 chat_history 長度（DOM .chat-message 節點數應對應）


def _settle_rv_top(page: Page, timeout_ms: int = 2500) -> float:
    """Poll #researchView top 直到穩定（連續兩次差 <2px 視為 settle）或 timeout。

    smooth scroll 是動畫——死等固定秒數 flaky，改用 poll rvTop 穩定判定。
    """
    deadline = time.monotonic() + timeout_ms / 1000
    prev = None
    while time.monotonic() < deadline:
        top = page.evaluate(
            "() => { const rv = document.getElementById('researchView');"
            " return rv ? rv.getBoundingClientRect().top : null; }"
        )
        if top is not None and prev is not None and abs(top - prev) < 2:
            return top
        prev = top
        page.wait_for_timeout(100)
    return prev if prev is not None else float("inf")


def _measure_report(page: Page) -> dict:
    """量 #researchView 相對視口位置 + chat 節點數（settle 後）。"""
    _settle_rv_top(page)
    return page.evaluate(
        """() => {
            const rv = document.getElementById('researchView');
            const cm = document.getElementById('chatMessages');
            return {
                hasReport: rv ? rv.textContent.length > 1000 : false,
                display: rv ? getComputedStyle(rv).display : null,
                rvTop: rv ? Math.round(rv.getBoundingClientRect().top) : null,
                innerH: window.innerHeight,
                scrollY: Math.round(window.scrollY),
                chatMsgNodes: cm ? cm.querySelectorAll('.chat-message').length : 0,
            };
        }"""
    )


def _click_session(page: Page, sid: str):
    """點 sidebar 指定 session 列，觸發 loadSavedSession。"""
    page.wait_for_selector(_row_selector(sid), state="visible", timeout=10000)
    page.click(_row_selector(sid))


def _click_other_session(page: Page, avoid_sid: str):
    """點一個非目標 session（模擬『先在別 session，再切入 DR』）。"""
    rows = page.locator(SESSION_ROW)
    n = rows.count()
    for i in range(n):
        sid = rows.nth(i).get_attribute("data-sidebar-session-id")
        if sid and sid != avoid_sid:
            rows.nth(i).click()
            return sid
    return None


def _assert_dr_in_view(page: Page):
    """主斷言：報告進視口（reportInView）+ chat 合法資料還原完整（chatDataIntact）。"""
    m = _measure_report(page)
    assert m["hasReport"], f"DR 報告未 render（rvTextLen 不足）：{m}"
    assert m["display"] == "block", f"#researchView 非 display:block：{m}"
    # 主 AC：報告 top 落在視口內（修法前 FAIL = red baseline；修法後 PASS）
    assert m["rvTop"] <= m["innerH"], (
        f"reportInView FAIL：#researchView top={m['rvTop']} > innerHeight={m['innerH']}"
        f"（報告在視口下方，未捲入首屏）。修法前這是預期的 red baseline。完整量測：{m}"
    )
    # 正向：chat 合法資料還原未受 scroll 改動干擾（節點數對應 DB chat_history 長度）
    # 上捲到頁頂，確認 chat 完整可見
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(200)
    chat_nodes = page.evaluate(
        "() => { const cm = document.getElementById('chatMessages');"
        " return cm ? cm.querySelectorAll('.chat-message').length : 0; }"
    )
    assert chat_nodes == SEEDED_DR_CHAT_LEN, (
        f"chatDataIntact FAIL：chatMessages 節點數={chat_nodes} != 種入 chat_history "
        f"長度={SEEDED_DR_CHAT_LEN}（chat 合法資料還原不完整——scroll 改動不應影響 replay）"
    )


def test_ac1_reload_then_dr_report_in_view(logged_in_page: Page, base_url: str):
    """AC-1（reload→DR）：登入 → reload → 點 sidebar DR session → 報告進視口 + chat 完整。"""
    page = logged_in_page
    # reload 後回到 /app（reload 會自動還原上次 session，此處只需確保 sidebar 就緒）
    page.reload()
    page.wait_for_selector(SESSION_ROW, state="visible", timeout=15000)
    _click_session(page, SEEDED_DR_SESSION_ID)
    _assert_dr_in_view(page)


def test_ac2_switch_from_other_session_to_dr(logged_in_page: Page):
    """AC-2（其他 session → DR，不 reload）：點開任一 session → 直接切入 DR session。

    對應 runtime 對質的 repro 序（在別 session → 切 DR）——red baseline 取反主證。
    """
    page = logged_in_page
    page.wait_for_selector(SESSION_ROW, state="visible", timeout=15000)
    _click_other_session(page, avoid_sid=SEEDED_DR_SESSION_ID)
    page.wait_for_timeout(1500)  # 讓前一 session restore settle
    _click_session(page, SEEDED_DR_SESSION_ID)
    _assert_dr_in_view(page)
