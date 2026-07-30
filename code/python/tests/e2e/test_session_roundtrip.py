"""
E2E: session 存→重載→還原 round-trip 三場景（票 2026-07-28-g）。

十輪 session 持久化工程從沒把「存→重載→還原」round-trip 放進自動化驗收——本檔補上。
每場景鏈路：有內容的 session（活建或種子）→ reload 頁面 → sidebar 點開該 session →
斷言「行為面」還原（DOM 內容真的回來，不用網路請求發生過之類 proxy）。

三場景與其「存」半邊的機制（誠實標記，非全走同一條）：

1. search — **全活體 round-trip**（唯一活建場景）：真打一個搜尋（庫內必有結果的
   通用詞）→ 等 SSE 串流結束 → 等前端 debounced save 真落 server（POST 回填
   _serverId + session_history PUT 落地，皆以 ground truth 輪詢確認，不猜 sleep）
   → reload → sidebar 點開 → 斷言結果卡片區非空 + query 文字還原回 #searchInput。

2. DR — **種子 session**（沿用 test_dr_restore_viewport.py 既有慣例 = e2e 腳本群
   對 DR 的既有「mock」機制：fixture 切在 raw data 蒐集層、不 mock reasoning、
   不燒 LLM 錢）。前端 deep-research.js 無 mock 參數、後端唯一替代是
   NLWEB_ALLOW_REAL_LLM=1 真跑（燒錢，禁）→ 「產生內容」由已種入本地 PG 的
   DR session 承接（含 research_report + chat_history，admin 名下）。

3. LR — **種子 session（真跑 state 複本）**。票規格首選 dry_run，但親讀後端
   （methods/live_research.py::_create_lr_session / _save_state）確認 dry_run
   **設計上不落 PG**：session 用 bare UUID 不建 DB row、state 只進
   _DRY_RUN_STATE_STORE in-memory；mock_lr 同為 in-memory（routes/api.py
   _mock_lr_sessions）且不發 live_research_session_created。而登入態 reload 後
   sidebar 只讀 server list（session-manager.js loadSessions 登入路徑不 fallback
   localStorage）→ dry_run/mock 起跑的 session **結構性無法**在 reload 後從
   sidebar 點回來（這不是 bug，是 dry_run 的零 PG 依賴設計）。低成本補法 =
   把一個**真跑留下**的 LR session（stage 3 checkpoint、含 lr_dialog_snapshot）
   複製成專用種子列（不動 CEO 原始 session），reload→點開→斷言 #lrChat 還原
   非空（snapshot 重播 + resume notice，行為面）。

種子列（本地 PG search_sessions，admin@twdubao.com 名下；消失時 fail-loud 附說明）：
  DR: 90334949-53ee-49a2-be52-59074868ab05  「[E2E] DR 捲入視口種子（含 chat）」
      （scratchpad/seed_dr.py 種入，與 test_dr_restore_viewport.py 共用）
  LR: e2ee10a7-3c5d-4b6e-9f2a-8d41c0a5b901  「[E2E] LR 還原種子（真跑 stage3
      checkpoint 複本）」（2026-07-28 由真跑 session ace8dd9d 整列複製而來——
      live_research_state stage=3/status=checkpoint/schema_version=2 +
      lr_dialog_snapshot 3 則，皆為真跑產物非手造）

env：E2E_BASE_URL、E2E_EMAIL、E2E_PASSWORD（conftest fixture 讀取，fail-loud）。
需 server + 瀏覽器 + 真帳號（本地手動，不進 CI；norecursedirs 排除，顯式路徑跑）。
"""
import os
import time

import pytest
from playwright.sync_api import Page, expect

# ── selector 常數區（沿用姊妹檔已探索回寫的 selector；登入 selector 唯一事實來源
#    在 conftest，本檔不複製登入 selector）─────────────────────────────────────
# 搜尋流程（test_basic_search.py 2026-07-08 MCP 探索回寫）：
SEARCH_INPUT_SELECTOR = "#searchInput"
SEARCH_SUBMIT_SELECTOR = "#btnSearch"
RESULT_ITEM_SELECTOR = "#listView .news-card:not(.skeleton-card)"  # 排除 skeleton 佔位卡
STREAM_DONE_SELECTOR = "#btnSearch"        # 串流結束後才重新顯示
LOADING_SELECTOR = "#btnStopGenerate"      # 串流中顯示；常駐 DOM，斷言用 hidden
# sidebar session 列（test_dr_restore_viewport.py 2026-07-28 MCP 探索回寫）：
SESSION_ROW = ".left-sidebar-session-item[data-sidebar-session-id]"

# ── 種子 session id（見檔頭說明；sidebar 只 render 最近 15 列，種子 updated_at
#    已設為新近值——若日後被擠出前 15 列或被刪，測試 fail-loud 附重種指引）──────
SEEDED_DR_SESSION_ID = "90334949-53ee-49a2-be52-59074868ab05"
SEEDED_LR_SESSION_ID = "e2ee10a7-3c5d-4b6e-9f2a-8d41c0a5b901"

TEST_QUERY = "台積電"  # 庫內必有結果的通用詞（票 2026-07-28-g 指定方向）

SEARCH_TIMEOUT_MS = int(os.environ.get("E2E_SEARCH_TIMEOUT_MS", "120000"))
PERSIST_TIMEOUT_MS = 30000   # debounced save（2s debounce + 網路）落 server 的等待上限
RESTORE_TIMEOUT_MS = 20000   # 點開 session 後 hydrate + DOM 還原的等待上限


def _row_selector(sid: str) -> str:
    return f'.left-sidebar-session-item[data-sidebar-session-id="{sid}"]'


def _reload_and_open_session(page: Page, sid: str, seed_hint: str) -> None:
    """reload → 等 sidebar 就緒 → 點開指定 session（round-trip 的「重載→點開」段）。"""
    page.reload()
    page.wait_for_selector(SESSION_ROW, state="visible", timeout=15000)
    # reload 會自動還原上次 session——給它 settle（姊妹檔 AC-2 同款），再點目標列
    page.wait_for_timeout(1000)
    row = page.locator(_row_selector(sid))
    if row.count() == 0:
        pytest.fail(
            f"sidebar 找不到 session 列 {sid}（{seed_hint}）。"
            f"sidebar 只 render 最近 15 列——種子可能被擠出或被刪，"
            f"需重種（見本檔檔頭種子說明）。",
            pytrace=False,
        )
    row.first.click()


# ═══════════════════════════════════════════════════════════════════════════
# 場景 1：搜尋 session（全活體 round-trip：建立→產生→存→重載→點開→還原）
# ═══════════════════════════════════════════════════════════════════════════

def _wait_new_session_server_id(page: Page, query: str, t0: int) -> str:
    """等前端 debounced POST 落地：輪詢 localStorage 直到本輪新建 entry 拿到 _serverId。

    篩選條件鎖「本輪」：title == query（新 session title = conversationHistory[0]）
    + createdAt >= 測試起點（server list 混入的舊列無 camelCase createdAt，天然排除；
    先前輪次建立的同名 session 被 t0 排除）。_serverId 為 POST 成功後回填的 PG UUID
    （session-manager.js saveSession），是「server row 已存在」的 ground truth。
    """
    deadline = time.monotonic() + PERSIST_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        sid = page.evaluate(
            """([q, t0]) => {
                try {
                    const arr = JSON.parse(localStorage.getItem('taiwanNewsSavedSessions') || '[]');
                    const hit = arr.find(s => s && s.title === q
                        && typeof s.createdAt === 'number' && s.createdAt >= t0
                        && typeof s._serverId === 'string' && s._serverId.includes('-'));
                    return hit ? hit._serverId : null;
                } catch (e) { return null; }
            }""",
            [query, t0],
        )
        if sid:
            return sid
        page.wait_for_timeout(250)
    pytest.fail(
        f"{PERSIST_TIMEOUT_MS}ms 內未見本輪新 session（title='{query}'）取得 _serverId——"
        f"debounced POST 未落地（server 掛了？401？看 console）。",
        pytrace=False,
    )


def _wait_server_session_has_results(page: Page, sid: str) -> None:
    """等結果落 server：輪詢 GET /api/sessions/{sid} 直到 session_history 非空。

    還原結果卡片走 server hydrate（loadSavedSession 對登入用戶 force-hydrate），
    所以「server row 的 session_history 已含結果」是 reload 前必須到位的存檔終態
    ——用 server 回讀當 ground truth，不用固定 sleep 猜 debounce。
    """
    deadline = time.monotonic() + PERSIST_TIMEOUT_MS / 1000
    last = None
    while time.monotonic() < deadline:
        last = page.evaluate(
            """async (sid) => {
                try {
                    const res = await window.authManager.authenticatedFetch('/api/sessions/' + sid);
                    const data = await res.json();
                    if (!res.ok || !data.success || !data.session) return { err: res.status };
                    const sh = data.session.session_history;
                    return { n: Array.isArray(sh) ? sh.length : 0 };
                } catch (e) { return { err: String(e) }; }
            }""",
            sid,
        )
        if isinstance(last, dict) and last.get("n", 0) >= 1:
            return
        page.wait_for_timeout(500)
    pytest.fail(
        f"{PERSIST_TIMEOUT_MS}ms 內 server session {sid} 的 session_history 仍空"
        f"（最後回讀：{last}）——結果 PUT 未落地，round-trip 的「存」半邊失敗。",
        pytrace=False,
    )


def test_search_session_roundtrip(logged_in_page: Page) -> None:
    """搜尋 → 串流結束 → 存檔落 server → reload → sidebar 點開 → 結果卡片與 query 還原。"""
    page = logged_in_page

    t0 = page.evaluate("() => Date.now()")

    # 1) 建立 session：打一個搜尋（與 test_basic_search 同款流程與結束訊號）
    page.fill(SEARCH_INPUT_SELECTOR, TEST_QUERY)
    page.click(SEARCH_SUBMIT_SELECTOR)
    expect(page.locator(RESULT_ITEM_SELECTOR).first).to_be_visible(timeout=SEARCH_TIMEOUT_MS)
    expect(page.locator(STREAM_DONE_SELECTOR)).to_be_visible(timeout=SEARCH_TIMEOUT_MS)
    expect(page.locator(LOADING_SELECTOR)).to_be_hidden(timeout=SEARCH_TIMEOUT_MS)

    # 2) 「存」半邊落地確認（ground truth，非 sleep 猜）：POST 回填 _serverId →
    #    結果 PUT 使 server session_history 非空
    sid = _wait_new_session_server_id(page, TEST_QUERY, t0)
    _wait_server_session_has_results(page, sid)

    # 3) 重載 → sidebar 點開該 session
    _reload_and_open_session(page, sid, "本測試剛建立的搜尋 session——不該消失")

    # 4) 行為面斷言：結果卡片區非空 + query 文字還原
    expect(page.locator(RESULT_ITEM_SELECTOR).first).to_be_visible(timeout=RESTORE_TIMEOUT_MS)
    count = page.locator(RESULT_ITEM_SELECTOR).count()
    assert count >= 1, f"還原後結果卡片區為空（count={count}）"
    restored_query = page.input_value(SEARCH_INPUT_SELECTOR)
    assert restored_query == TEST_QUERY, (
        f"query 文字未還原：#searchInput='{restored_query}'，預期 '{TEST_QUERY}'"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 場景 2：DR session（種子承接「產生內容」——既有 DR e2e 慣例，禁真 LLM）
# ═══════════════════════════════════════════════════════════════════════════

def test_dr_session_roundtrip(logged_in_page: Page) -> None:
    """reload → sidebar 點開種子 DR session → #researchView 有報告內容且可視。"""
    page = logged_in_page
    _reload_and_open_session(
        page, SEEDED_DR_SESSION_ID,
        "DR 種子（scratchpad/seed_dr.py 種入，與 test_dr_restore_viewport 共用）",
    )

    # 點開後 hydrate + render 為 async——輪詢到內容出現（門檻沿用姊妹檔 hasReport>1000）
    deadline = time.monotonic() + RESTORE_TIMEOUT_MS / 1000
    m = None
    while time.monotonic() < deadline:
        m = page.evaluate(
            """() => {
                const rv = document.getElementById('researchView');
                return {
                    exists: !!rv,
                    textLen: rv ? rv.textContent.trim().length : 0,
                    display: rv ? getComputedStyle(rv).display : null,
                    visible: rv ? rv.offsetParent !== null : false,
                };
            }"""
        )
        if m["exists"] and m["textLen"] > 1000:
            break
        page.wait_for_timeout(300)

    assert m and m["exists"], "#researchView 不存在"
    assert m["textLen"] > 1000, (
        f"DR 報告未還原（#researchView textLen={m['textLen']}，門檻 1000）：{m}"
    )
    assert m["display"] == "block" and m["visible"], (
        f"#researchView 有內容但非可視狀態（display={m['display']}, visible={m['visible']}）"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 場景 3：LR session（真跑 state 複本種子；dry_run 結構性不落 PG，見檔頭）
# ═══════════════════════════════════════════════════════════════════════════

def test_lr_session_roundtrip(logged_in_page: Page) -> None:
    """reload → sidebar 點開種子 LR session → #lrChat 對話區還原非空且可視。

    種子 state = 真跑 stage 3 checkpoint → restoreLRCheckpointFromState 走
    mid-flight 路徑：重播 lr_dialog_snapshot 3 則 + resume notice + checkpoint
    reply UI（live-research.js:2506-2553）→ #lrChat 至少 2 個子節點、非佔位文案。
    """
    page = logged_in_page
    _reload_and_open_session(
        page, SEEDED_LR_SESSION_ID,
        "LR 種子（真跑 session ace8dd9d 的複本，2026-07-28 種入）",
    )

    # LR restore 有 setTimeout 排程 + 「載入研究進度中…」佔位（news-search.js:3022）——
    # 輪詢到「重播內容 + resume notice」就位（>=2 子節點且非佔位）才斷言
    deadline = time.monotonic() + RESTORE_TIMEOUT_MS / 1000
    m = None
    while time.monotonic() < deadline:
        m = page.evaluate(
            """() => {
                const c = document.getElementById('lrChat');
                if (!c) return { exists: false };
                const text = (c.textContent || '').trim();
                return {
                    exists: true,
                    children: c.children.length,
                    textLen: text.length,
                    placeholderOnly: text === '載入研究進度中…',
                    visible: c.offsetParent !== null,
                    head: text.slice(0, 120),
                };
            }"""
        )
        if (
            m.get("exists")
            and not m.get("placeholderOnly")
            and m.get("children", 0) >= 2
            and m.get("textLen", 0) > 50
        ):
            break
        page.wait_for_timeout(300)

    assert m and m.get("exists"), "#lrChat 不存在（LR view 未掛載）"
    assert not m.get("placeholderOnly"), "#lrChat 停在『載入研究進度中…』佔位——restore 未完成"
    assert m.get("children", 0) >= 2 and m.get("textLen", 0) > 50, (
        f"LR 對話區未還原（children={m.get('children')}, textLen={m.get('textLen')}，"
        f"預期 snapshot 重播 + resume notice 至少 2 節點）：head='{m.get('head')}'"
    )
    assert m.get("visible"), f"#lrChat 有內容但非可視狀態：{m}"
