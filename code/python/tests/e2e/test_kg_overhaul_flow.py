# code/python/tests/e2e/test_kg_overhaul_flow.py
# KG overhaul E2E：骨架初始渲染 / 下鑽 / 回骨架 / SVG 下載 / DR-LR 共用 / regression。
# 真實登入（no auth bypass，memory 紀律）；需 server + 瀏覽器 + 真帳號（本地手動、不進 CI）。
#
# 兩組（🔧 R4）：
#  A 組 = deterministic mock 渲染層測試（不燒錢、非 skip、常規可跑）：用 _render_mock_kg
#    經 window.displayKnowledgeGraph 注入 mock KG payload，驗共用渲染層（骨架/下鑽/凍結/
#    回骨架/SVG匯出/highlight/LR 共用/座標一致性）。prefix='kg' 驗 DR render，'lrKG' 驗 LR。
#  B 組 = 真跑 DR 端到端煙霧測試（貼 @real_llm 裝飾器，燒錢 opt-in、default skip）：
#    _run_dr_with_kg 沿用 test_deep_research_flow.py::_run_deep_research 的真實 DR 觸發序列
#    （切 deep_research mode → 勾 KG toggle → 送 query → 過 clarification → 輪詢 #kgGraphView）。
# 🔧 R4 BLOCKER：LR 不走真六階段 flow（親讀 live-research.js：KG 只在 Stage 6 export 才渲染，
#    真跑既燒錢又脆弱）——LR 端用 A 組 mock 注入驗共用渲染層。conftest.py 無 KG/DR trigger
#    fixture（親讀 verify），沿用 logged_in_page + 本檔 module-level helper（非 fixture）。
import os
import time

import pytest
from playwright.sync_api import Page, expect


# 🔧 R7 SF-R7-3：`test_kg_svg_download` 用 `page.expect_download()`，需 Playwright browser
# context 的 `accept_downloads=True`。pytest-playwright 新版預設已 True，但不同版本行為不一，
# 不假設——顯式覆蓋 `browser_context_args`（module scope）確保 SVG 下載測試不因 context 未開
# accept_downloads 而 timeout/丟棄下載。沿用 pytest-playwright 既有 args 再補旗標（不覆蓋掉
# viewport 等其他預設）。
@pytest.fixture(scope="module")
def browser_context_args(browser_context_args):
    return {**browser_context_args, "accept_downloads": True}


# 🔧 R4 SF-R4-5：燒錢 gate 從「整檔 pytestmark」改為「per-test 裝飾器 real_llm」。
# 原本整檔 skip 會讓 deterministic mock 測試（不燒錢）也被 skip → 常規 CI 零 KG 覆蓋。
# 改成只有「真跑 DR 推論鏈」的測試貼 @real_llm；mock 測試不貼 → 常規可跑（非 skip）。
real_llm = pytest.mark.skipif(
    os.environ.get("NLWEB_ALLOW_REAL_LLM", "").strip() != "1",
    reason="真跑 DR 推論鏈 = 真 LLM 錢 + 數分鐘~18min——需 NLWEB_ALLOW_REAL_LLM=1 顯式 opt-in 才跑",
)

# ── DR / KG selector 常數區（test-flow 專屬，非登入 selector；沿用
#    test_deep_research_flow.py 的探索結果。登入 selector 在 conftest 常數區，勿在此複製）──
DR_MODE_SELECTOR = ".mode-btn-inline[data-mode='deep_research']"  # 「進階搜尋」= DR mode（文字≠mode 值）
DR_ADV_POPUP_SELECTOR = "#advancedSearchPopup"
DR_KG_ENABLE_TOGGLE = "#kgToggle"        # 「啟用知識圖譜」checkbox（KG 產出前提）
DR_POPUP_CLOSE_SELECTOR = "#popupClose"
SEARCH_INPUT_SELECTOR = "#searchInput"
SEARCH_SUBMIT_SELECTOR = "#btnSearch"
DR_CLARIFY_CARD_SELECTOR = ".clarification-actions"          # 澄清卡出現訊號
DR_CLARIFY_OPTION_SELECTOR = ".option-chip[data-option-id]"  # 面向選項（選一個 enable 送出）
DR_CLARIFY_SUBMIT_SELECTOR = ".submit-clarification"         # 送出澄清 → 真跑 performDeepResearch
# 🔧 R4 BLOCKER：LR 沒有像 DR「送 query 就跑出 KG」的路徑（親讀 live-research.js verify，
#   見下方 _render_mock_kg 的路徑說明）。LR 的 KG 走 deterministic mock 注入（不跑六階段），
#   故本檔不需要 LR mode selector。DR 走真跑（上方 selector），LR + 常規互動走 mock。
# ─────────────────────────────────────────────────────────────────────────────

# 🔧 R4 BLOCKER + SF-R4-5：deterministic mock KG payload（不燒錢、非 skip，常規可跑）。
# 用途：(1) 驗 LR 端共用渲染層（lrKG prefix 下骨架/下鑽/下載不分叉）；
#       (2) 給常規 CI 一組「非 NLWEB_ALLOW_REAL_LLM skip」的 KG 互動覆蓋。
# 大圖確保骨架 < 全量、**多個骨架節點有隱藏鄰居（≥2 badge）**、有非中心葉節點。
# 設計避免「tie 全收灌大骨架」的陷阱（親跑 node 驗證骨架邏輯後定案，見下方數字）：
#   - 15 個 mid 互連成環（每 mid degree +2）+ hub 連所有 mid（hub degree=15，每 mid +1）
#   - 每個 mid 連 1 個獨有 leaf（mid degree=4，leaf degree=1）
#   - 總 entity = 1 hub + 15 mid + 15 leaf = 31。N = clamp(round(31*0.3),8,15) = clamp(9,8,15) = 9。
# 骨架 degree 分層明確：hub=15、15 mid=4、15 leaf=1。top-9 → cutoff=4（第 9 名是 mid）→
# tie 全收 15 個 degree=4 的 mid → **骨架 = hub + 15 mid = 16 < 31 全量**（嚴格小於 ✓）；
# 15 leaf 全隱藏（hidden=15）→ 每個 mid 各有 1 個隱藏 leaf → **15 個 mid 帶 +N badge（≥2 ✓）**；
# leaf `mid0_leaf` 是「非中心、只連 mid0、與其他全無關」→ highlight test 用它。
# （chained-verify：mock 骨架/badge 數字經 node 腳本實跑確認，非憑空推斷。）
def _mock_kg_payload():
    """確定性 KG payload（degree 分層防 tie 灌骨架；骨架 16<31、15 badge、有非中心葉）。"""
    entities = [{"entity_id": "hub", "name": "台積電", "entity_type": "concept"}]
    relationships = []
    M = 15
    for m in range(M):
        entities.append({"entity_id": f"mid{m}", "name": f"領域{m}", "entity_type": "concept"})
    for m in range(M):
        relationships.append(
            {"source_entity_id": "hub", "target_entity_id": f"mid{m}", "relation_type": "related_to"}
        )
        relationships.append(
            {"source_entity_id": f"mid{m}", "target_entity_id": f"mid{(m + 1) % M}",
             "relation_type": "related_to"}
        )
        leaf = f"mid{m}_leaf"
        entities.append({"entity_id": leaf, "name": f"細節{m}", "entity_type": "concept"})
        relationships.append(
            {"source_entity_id": f"mid{m}", "target_entity_id": leaf, "relation_type": "related_to"}
        )
    return {"entities": entities, "relationships": relationships}


_ALERT_HOOK = """() => {
    if (!window.__e2e_alert_hooked) {
        window.__e2e_alerts = [];
        window.alert = (m) => { window.__e2e_alerts.push(String(m)); };
        window.confirm = () => true;
        window.__e2e_alert_hooked = true;
    }
    return true;
}"""


def _kg_node_count(page: Page, prefix: str = "kg") -> int:
    """指定 instance（kg / lrKG）的 graph view 內節點數（>0 = KG 就緒）。

    🔧 R7 NIT：只數 `.kg-node`（每個節點 = 一個 `g.kg-node` group，renderKGGraphView 畫節點
    處 `.attr('class','kg-node')` verify）。原本的 `circle, .kg-node, g.node, [class*=node]`
    會 overcount——同一節點被 `.kg-node`（group）+ 內含 `circle` + `[class*=node]`（class 含
    "node" 的任意元素，如 kg-node-label）重複命中。本 helper 只判「>0 = KG 就緒」時 overcount
    無害，但精確計數（其他斷言比可見節點數）需 `.kg-node` 單一選擇器才不虛高。
    """
    return page.evaluate(
        """(gid) => {
            const g = document.querySelector('#' + gid);
            if (!g || g.offsetParent === null) return 0;
            return g.querySelectorAll('.kg-node').length;
        }""",
        f"{prefix}GraphView",
    )


def _wait_kg_ready(page: Page, prefix: str, deadline_s: int) -> bool:
    """輪詢 #<prefix>GraphView 直到有節點或 deadline（鏡像 _run_deep_research 的等待）。"""
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if _kg_node_count(page, prefix) > 0:
            return True
        page.wait_for_timeout(2000)
    return False


def _run_dr_with_kg(page: Page, query: str) -> Page:
    """登入態 page 真跑一次 DR 並啟用 KG，回傳同一 page（KG 就緒後）。

    🔧 R3 BLOCKER：內聯 test_deep_research_flow.py::_run_deep_research 的真實觸發序列。
    KG 未就緒（18 分鐘內無節點）→ 直接 fail（帶進度診斷），不讓後續斷言在無 KG 上瞎跑。
    """
    page.evaluate(_ALERT_HOOK)
    # 1) 切 deep_research mode（mode 按鈕需搜尋框互動後才顯示）
    page.click(SEARCH_INPUT_SELECTOR)
    page.wait_for_selector(DR_MODE_SELECTOR, state="visible", timeout=10000)
    page.click(DR_MODE_SELECTOR)
    # 2) 進階搜尋 popup 自動開 → 勾「啟用知識圖譜」→ 關 popup
    page.wait_for_selector(DR_ADV_POPUP_SELECTOR, state="visible", timeout=10000)
    kg_checkbox = page.locator(DR_KG_ENABLE_TOGGLE)
    if not kg_checkbox.is_checked():
        kg_checkbox.check()
    page.click(DR_POPUP_CLOSE_SELECTOR)
    page.wait_for_selector(DR_ADV_POPUP_SELECTOR, state="hidden", timeout=5000)
    # 3) 輸入問題 + 送出（彈 clarification）
    page.fill(SEARCH_INPUT_SELECTOR, query)
    page.click(SEARCH_SUBMIT_SELECTOR)
    # 4) clarification 前置：選一面向 → 送出 → 真跑 performDeepResearch
    page.wait_for_selector(DR_CLARIFY_CARD_SELECTOR, state="visible", timeout=60000)
    page.locator(DR_CLARIFY_OPTION_SELECTOR).first.click()
    submit = page.locator(DR_CLARIFY_SUBMIT_SELECTOR).first
    submit.wait_for(state="visible", timeout=5000)
    submit.click()
    # 5) 等 DR 完成 = #kgGraphView 有節點（18 分鐘上限，鏡像 _run_deep_research）
    if not _wait_kg_ready(page, "kg", 1080):
        progress = page.evaluate(
            """() => {
                const panel = [...document.querySelectorAll('*')].find(
                    e => /深度研究進行中/.test(e.textContent||'') && e.offsetParent!==null
                );
                return panel ? panel.innerText.slice(0, 500) : '(DR 進度面板已消失——可能已完成但未產出 KG)';
            }"""
        )
        pytest.fail(f"DR 18 分鐘內 KG 無節點（DR 卡住/未啟用 KG/selector 過期）。卡點：\n{progress}", pytrace=False)
    return page


def _render_mock_kg(page: Page, prefix: str = "kg") -> Page:
    """🔧 R4 BLOCKER + SF-R4-5：直接注入 mock KG payload 到共用渲染層（不跑推論、不燒錢）。

    LR→KG 真實路徑（親讀 live-research.js verify）：
      - LR 執行 = `performLiveResearch(query)`（:2910）→ 啟動**六階段對話式**流程
        （主搜尋框隱藏，user 透過 lrCheckpointReply 逐階段互動推進）。
      - KG **只在 Stage 6 收到 `live_research_export` SSE event 時**渲染（handleLiveResearchSSE
        :2761-2774）：`if (data.knowledge_graph) displayKnowledgeGraph(data.knowledge_graph,
        { containerPrefix: 'lrKG' })`。即「送個 query」**不會**像 DR 那樣直接跑出 KG——
        必須跑完整六階段互動到 export 才有 KG。
      - 故 LR E2E 走真 flow 既極燒錢（六階段推論 + 更久）又極脆弱（checkpoint 回覆內容
        不可預測、selector 未探索）。

    正解 = deterministic mock：`window.displayKnowledgeGraph` 已在 news-search.js:337 無條件
    暴露到 window（`window.displayKnowledgeGraph = displayKnowledgeGraph`，親讀 verify）。
    直接 `window.displayKnowledgeGraph(mockKg, {containerPrefix: prefix})` 注入，驗的是
    **DR/LR 共用的同一份渲染層**（骨架選擇 / 下鑽 / 增量佈局 / SVG 匯出）在指定 prefix 下
    正確工作——這正是「DR/LR 共用渲染層不分叉」要驗的核心，不需真跑 LR 六階段推論
    （符合 memory「fixture 切在 raw data 蒐集、不切在 LLM reasoning」——mock 掉貴的推論、
    直接餵 KG payload 給渲染層）。prefix='lrKG' 驗 LR 端；prefix='kg' 驗 DR render 層。

    🔧 R5 BLOCKER2（親讀 displayKnowledgeGraph body :301-388 + HTML + CSS verify）：
    displayKnowledgeGraph 只 un-hide **KG 自己的容器**（`_kgId('DisplayContainer')`，:354
    `container.style.display='block'`），**完全不碰父層 view**。而父層 view 預設 display:none、
    只有 `.active` 才顯示：
      - DR：`#kgGraphView` 的父層 = `#resultsSection`（class `.results-section`，HTML :506；
        CSS `.results-section.active{display:block}` :1409）
      - LR：`#lrKGGraphView` 的父層 = `#liveResearchView`（class `.live-research-view`，HTML :676；
        CSS `.live-research-view.active{display:block}` :1878）
    首頁登入後無 active view → KG SVG 即使 attached 也**不可見** → `wait_for_selector(..., svg)`
    預設等 visible 會**卡死**。故注入 mock **之前**先給對應父層 view 加 `.active`（真實 id/class，
    非幻想——上方行號皆親讀 verify）。DR prefix→`#resultsSection`；lrKG prefix→`#liveResearchView`。
    """
    payload = _mock_kg_payload()
    ok = page.evaluate(
        """({ kg, prefix }) => {
            if (typeof window.displayKnowledgeGraph !== 'function') return false;
            // 🔧 R5 BLOCKER2：先激活父層 view（否則 KG 容器在 display:none 的 view 內不可見）。
            // 真實 id/class（親讀 news-search-prototype.html + news-search.css verify）：
            //   DR('kg') → #resultsSection.results-section；LR('lrKG') → #liveResearchView.live-research-view
            // 🔧 fix（Zoe MCP 親驗父層鏈 2026-07-21）：#liveResearchView **巢狀在** #resultsSection
            //   (display:none) 內。只激活 #liveResearchView 不夠——祖先 #resultsSection 仍 display:none
            //   會擋住整條鏈使 svg hidden。故 lrKG 時同時激活外層 #resultsSection + 內層 #liveResearchView。
            const parentIds = (prefix === 'lrKG')
                ? ['resultsSection', 'liveResearchView']
                : ['resultsSection'];
            for (const pid of parentIds) {
                const p = document.getElementById(pid);
                if (!p) return false;   // 父層 view 不存在 = 前端結構變了，fail-loud
                p.classList.add('active');
            }
            // 🔧 R7-post-AR（Codex R7 hardening #2）：清 KG hidden 偏好，防前一個
            // browser context / localStorage 殘留讓 #{prefix}DisplayContainer 保持隱藏
            // → mock E2E flaky（displayKnowledgeGraph body 有 dataset.userHidden 判斷會 short-circuit）。
            try {
                localStorage.removeItem('nlweb-kg-hidden');
                localStorage.removeItem('nlweb-lr-kg-hidden');
            } catch (e) { /* localStorage 不可用時忽略 */ }
            const cont = document.getElementById(prefix + 'DisplayContainer');
            if (cont && cont.dataset) delete cont.dataset.userHidden;
            window.displayKnowledgeGraph(kg, { containerPrefix: prefix });
            return true;
        }""",
        {"kg": payload, "prefix": prefix},
    )
    assert ok, (
        "mock KG 注入失敗：window.displayKnowledgeGraph 未暴露（news-search.js:337 bridge 應"
        f"無條件掛載）或父層 view（{'liveResearchView' if prefix == 'lrKG' else 'resultsSection'}）"
        "不存在——前端載入異常或版本/結構不符"
    )
    # 🔧 R5 BLOCKER2：父層 view 已加 .active（helper 內），此 svg 現在可見 → wait visible 不卡死。
    page.wait_for_selector(f"#{prefix}GraphView svg", state="visible", timeout=10000)
    return page


# ═══════════════════════════════════════════════════════════════════════════
# A 組：deterministic mock 渲染層測試（🔧 R4 SF-R4-5 — 不燒錢、非 skip、常規可跑）
# 用 window.displayKnowledgeGraph 注入 mock KG payload，驗共用渲染層行為。
# prefix='kg' 驗 DR 端 render；test_kg_render_layer_no_fork_under_lrkg_prefix 用 prefix='lrKG' 驗渲染層。
# 這組是常規 CI 的 KG 互動覆蓋（不依賴真 LLM），取代原本全 skip 的困境。
# ═══════════════════════════════════════════════════════════════════════════

def test_kg_skeleton_initial_render(logged_in_page: Page):
    """初始 graph view 只畫骨架（大圖時節點數 < 全體），list view 仍全量。"""
    page = _render_mock_kg(logged_in_page, "kg")
    graph_nodes = page.locator("#kgGraphView .kg-node").count()
    # 切列表 view 數全量實體
    page.click('#kgViewToggle .kg-view-btn[data-view="list"]')
    list_items = page.locator("#kgDisplayContent .kg-section:first-child .kg-item").count()
    assert graph_nodes < list_items  # mock 31 entity → 骨架 16 嚴格小於全量


def test_kg_expand_on_click(logged_in_page: Page):
    """點有 +N badge 的節點 → 可見節點數增加（展開鄰居）。"""
    page = _render_mock_kg(logged_in_page, "kg")
    before = page.locator("#kgGraphView .kg-node").count()
    # 點第一個帶 badge 的節點
    badge_node = page.locator("#kgGraphView .kg-node:has(.kg-hidden-badge)").first
    badge_node.click()
    page.wait_for_timeout(500)
    after = page.locator("#kgGraphView .kg-node").count()
    assert after > before


def test_kg_incremental_layout_freezes_existing(logged_in_page: Page):
    """D=增量佈局：展開一個節點後，另一個既有節點的座標必須不變（凍結，保留空間記憶）。

    🔧 R2 SF-2：用 data-entity-id 穩定定位 B（不用 .nth(i)）——re-render 後 badge 集合
    會變（A 展開後 hiddenNeighborCount 歸 0、不再有 badge），索引會漂移指向不同實體。
    先記 A、B 的 entity_id，點 A 後用 B 的 entity_id 重新定位比對 transform。
    """
    page = _render_mock_kg(logged_in_page, "kg")
    badge_nodes = page.locator("#kgGraphView .kg-node:has(.kg-hidden-badge)")
    assert badge_nodes.count() >= 2, "需要至少兩個可展開節點才能驗凍結"
    a_id = badge_nodes.nth(0).get_attribute("data-entity-id")
    b_id = badge_nodes.nth(1).get_attribute("data-entity-id")
    assert a_id and b_id and a_id != b_id
    # B 用 entity_id 穩定定位，記錄展開前 transform
    b_before = page.locator(f'#kgGraphView .kg-node[data-entity-id="{b_id}"]').get_attribute("transform")
    # 點 A（用 entity_id 定位）觸發增量展開
    page.locator(f'#kgGraphView .kg-node[data-entity-id="{a_id}"]').click()
    page.wait_for_timeout(600)
    # 展開後用 B 的 entity_id 重新定位（不受 badge 集合變動影響），比對 transform
    b_after = page.locator(f'#kgGraphView .kg-node[data-entity-id="{b_id}"]').get_attribute("transform")
    assert b_before == b_after, (
        f"增量佈局失敗：既有節點 B({b_id}) 座標被移動 {b_before} -> {b_after}"
    )


def test_kg_reset_to_skeleton(logged_in_page: Page):
    """展開後「回骨架」按鈕出現，點了回初始骨架。"""
    page = _render_mock_kg(logged_in_page, "kg")
    skeleton_count = page.locator("#kgGraphView .kg-node").count()
    page.locator("#kgGraphView .kg-node:has(.kg-hidden-badge)").first.click()
    page.wait_for_timeout(500)
    expect(page.locator("#kgResetSkeletonBtn")).to_be_visible()
    page.click("#kgResetSkeletonBtn")
    page.wait_for_timeout(500)
    assert page.locator("#kgGraphView .kg-node").count() == skeleton_count


def test_kg_svg_download(logged_in_page: Page, tmp_path):
    """點下載 → 取得 .svg 檔，含中文（mock 用「台積電」）與 <style>。

    🔧 R7 SF-R7-3：`page.expect_download()` 依賴 context `accept_downloads=True`——本檔
    module-scope `browser_context_args` fixture 已顯式開啟（見檔頭），故此處下載可被攔截。
    """
    page = _render_mock_kg(logged_in_page, "kg")
    with page.expect_download() as dl_info:
        page.click("#kgDownloadBtn")
    download = dl_info.value
    assert download.suggested_filename.endswith(".svg")
    path = tmp_path / download.suggested_filename
    download.save_as(path)
    content = path.read_text(encoding="utf-8")
    assert "<?xml" in content
    assert "<style>" in content
    assert 'xmlns="http://www.w3.org/2000/svg"' in content
    assert "台積電" in content  # 中文 entity 名不亂碼（mock payload 的 hub name）


def test_kg_regression_highlight_still_works(logged_in_page: Page):
    """無隱藏鄰居的節點點擊 → 既有 highlight 生效（🔧 R4 SF-R4-2 真斷言，錨現行 DOM）。

    🔧 R4 SF-R4-2：親讀 highlightNode（knowledge-graph.js:799-831）verify——它**只改
    opacity/stroke/stroke-width/marker-end 屬性，不加任何 class**（`.kg-node-selected`
    全 static/ 樹零匹配，R3 引用的是幻想 class）。故斷言只用「真實會變的屬性」：
    非鄰居節點 opacity → 0.2、非連接邊 opacity → 0.15。
    設計：先「顯示全部」讓所有節點可見（無 hidden → 點擊走 highlight 非 expand），再點一個
    **非中心葉節點** `mid0_leaf`（它只連 mid0，與其他 14 個 mid 及其 leaf 全無關 → 那些會被 dim）。
    """
    page = _render_mock_kg(logged_in_page, "kg")
    # 顯示全部 → 全節點可見、無 +N badge → 點擊走 highlight 分支（非 expand）
    page.click("#kgShowAllBtn")
    page.wait_for_timeout(500)
    total_nodes = page.locator("#kgGraphView .kg-node").count()
    assert total_nodes > 2, "需要 >2 節點才有『與焦點無關』的節點可被 dim"
    # 點一個非中心葉節點（mock 的 hub/mid 連很多；葉 mid0_leaf 只連 mid0 → 有大量無關節點）
    leaf = page.locator('#kgGraphView .kg-node[data-entity-id="mid0_leaf"]')
    assert leaf.count() == 1, "mock 葉節點 mid0_leaf 應存在"
    leaf.click()
    # 🔧 fix（Zoe MCP 親驗 2026-07-21）：等待須 > DBLCLICK_DELAY_MS(320)——click 走 delayed
    # single-click timer（Phase 2 加），300ms < 320ms 會在 highlight 套用前就斷言 → flaky（親測
    # dimAt300=0、dimAt450=29）。對齊本檔其他 click 測試的 500ms 裕度。
    page.wait_for_timeout(500)
    # 真 invariant：至少一個無關節點 opacity=0.2 或無關邊 opacity=0.15（highlightNode 真實行為）
    dimmed_nodes = page.locator('#kgGraphView .kg-node[opacity="0.2"]').count()
    dimmed_edges = page.locator('#kgGraphView .kg-link[opacity="0.15"]').count()
    assert dimmed_nodes > 0 or dimmed_edges > 0, (
        f"點葉節點後既無節點 opacity=0.2、也無邊 opacity=0.15——highlightNode 未生效"
        f"（dimmed_nodes={dimmed_nodes} dimmed_edges={dimmed_edges}，total={total_nodes}）"
    )


def test_kg_list_and_zoom_pan_intact(logged_in_page: Page):
    """list toggle + zoom/pan 既有功能不壞。"""
    page = _render_mock_kg(logged_in_page, "kg")
    page.click('#kgViewToggle .kg-view-btn[data-view="list"]')
    expect(page.locator("#kgDisplayContent")).to_be_visible()
    page.click('#kgViewToggle .kg-view-btn[data-view="graph"]')
    expect(page.locator("#kgGraphView")).to_be_visible()


def test_kg_render_layer_no_fork_under_lrkg_prefix(logged_in_page: Page):
    """🔧 R4 BLOCKER + 🔧 R5 SF-R5-3：渲染層在 lrKG prefix 下不分叉（mock payload 注入）。

    🔧 R5 SF-R5-3 命名精確化：test 名從 `test_kg_lr_shares_render_path` 改為
    `..._render_layer_no_fork_under_lrkg_prefix`，避免誤讀成「LR 端到端共用」。
    **本測試只驗**：同一份 render code 在 `lrKG` DOM id prefix 下產出正確結構（骨架 < 全量、
    有 +N badge、下載按鈕存在）。**不驗** LR 六階段流程 / SSE parsing / 真呼叫 bridge——那些
    是 LR flow 的職責，走真跑（本檔不涵蓋，見 B 組只有 DR 真跑）。用 mock 注入 prefix='lrKG'
    （LR 真實路徑是 Stage 6 export 才 displayKnowledgeGraph(kg, {containerPrefix:'lrKG'})）。
    """
    page = _render_mock_kg(logged_in_page, "lrKG")
    lr_nodes = page.locator("#lrKGGraphView .kg-node").count()
    assert lr_nodes > 0, "LR 端 lrKG 渲染層未產出節點"
    # 骨架 16 < 全量 31 → 有隱藏鄰居（15 leaf）→ 有 +N badge（共用渲染層在 lrKG 也生效）
    assert page.locator("#lrKGGraphView .kg-node:has(.kg-hidden-badge)").count() > 0
    expect(page.locator("#lrKGDownloadBtn")).to_be_visible()


def test_kg_position_consistency_no_sanitize_warning(logged_in_page: Page):
    """🔧 R4 SF-R4-4：正常路徑下 subgraph.entities 與 nodePositions 一致——展開後不觸發
    `[KG] precomputedPositions 缺...` 補位 warning（座標一致性 invariant 成立）。

    捕捉 console：注入 mock KG → 展開一個 badge 節點（觸發 placeNewNodes 增量佈局）→
    斷言全程無「precomputedPositions 缺」warning（有 = layoutSkeleton/placeNewNodes 沒補齊
    座標、SF-R4-4 補位分支被觸發 = 一致性契約破了）。
    """
    warnings: list[str] = []
    logged_in_page.on(
        "console",
        lambda msg: warnings.append(msg.text) if "precomputedPositions 缺" in msg.text else None,
    )
    page = _render_mock_kg(logged_in_page, "kg")
    # 展開一個帶 badge 的節點（走 placeNewNodes → 新節點應被補上座標）
    page.locator("#kgGraphView .kg-node:has(.kg-hidden-badge)").first.click()
    page.wait_for_timeout(600)
    assert not warnings, (
        f"SF-R4-4 補位分支被觸發（座標一致性破）：{warnings}——"
        f"placeNewNodes/layoutSkeleton 未替 subgraph 全 entity 補齊座標"
    )


def test_kg_dblclick_badge_node_stable_focus(logged_in_page: Page):
    """🔧 R6 BLOCKER（原 R5 SF-R5-1）：雙擊有 badge 的節點 → 穩定 focus 為該節點單鄰域，不被
    第一次 click 的 expand 干擾；再雙擊 → 回骨架。

    mock 的 mid0 帶 badge（mid0_leaf 隱藏）。🔧 R6：正確修法 = delayed single-click timer——
    click 用 setTimeout(DBLCLICK_DELAY_MS=320ms) 延遲、dblclick 進來先 clearTimeout 取消 pending
    click。若沒 timer（或用 R5 失效的 `event.detail>1`——它攔不住第一次 detail=1 的 click），雙擊時
    第一次 click 會先 expand+rerender → dblclick 目標被重建 → focus 丟失/不穩（可見集停在 expand
    後的 ~17，而非 focus 單鄰域）。本測試的 `dblclick()` 兩次 click 間隔 << 320ms → dblclick 在
    timer 到期前清掉 pending click → 只跑 focus。之後 `wait_for_timeout(500)` > 320ms：若 timer 沒
    被清會補跑 expand 使 count≠5，故這個斷言能真的 catch timer 失效（🔧 R7 SF-R7-1：delay 從 220→320
    後 500ms 仍 > delay，此斷言的 catch 能力不變）。
    chained-verify（node 實跑）：mid0 focus 單鄰域 = {hub, mid0, mid0_leaf, mid1, mid14} = 5 節點；
    骨架 = 16。focus 生效 → 5；focus 丟失（timer 沒清）→ 16/17。用這個數字差區分。
    """
    page = _render_mock_kg(logged_in_page, "kg")
    skeleton_count = page.locator("#kgGraphView .kg-node").count()  # 骨架 16
    mid0 = page.locator('#kgGraphView .kg-node[data-entity-id="mid0"]')
    assert mid0.count() == 1, "mock mid0（帶 badge）應在骨架中"
    # 雙擊 mid0 → 應 focus 為單鄰域（5 節點），非 expand 累積
    mid0.dblclick()
    page.wait_for_timeout(500)
    focused_count = page.locator("#kgGraphView .kg-node").count()
    assert focused_count == 5, (
        f"雙擊 mid0 後可見節點數={focused_count}，預期 5（focus 單鄰域）——"
        f"非 5 表示 focus 被第一次 click 的 expand 干擾（SF-R5-1 未生效，"
        f"骨架={skeleton_count}）"
    )
    # 再雙擊 mid0（此時已聚焦於 mid0）→ 回骨架
    page.locator('#kgGraphView .kg-node[data-entity-id="mid0"]').dblclick()
    page.wait_for_timeout(500)
    assert page.locator("#kgGraphView .kg-node").count() == skeleton_count, "再雙擊應回骨架"


def test_kg_single_click_after_delay_expands(logged_in_page: Page):
    """🔧 R7 SF-R7-1：synthetic timing regression——單擊（兩擊間隔 > DBLCLICK_DELAY_MS，
    不構成雙擊）確實觸發 expand，證明 delayed single-click timer 到期後有跑動作、延遲值本身
    不會誤把慢速兩次單擊吞成雙擊。

    設計：對一個帶 badge 的節點做「一次 click → 等 > 320ms 讓 timer 到期 → 斷言 expand 已發生」。
    這是 dblclick 穩定測試（間隔 << delay）的對照組（間隔 >> delay）：
      - dblclick 測試（間隔 << 320ms）：dblclick clearTimeout 取消 pending click → 只 focus。
      - 本測試（單一 click，等 > 320ms）：timer 到期 → 跑 expand 分支 → 可見節點數增加。
    兩條合起來夾住「timer 延遲既不太短（誤觸 expand）也不太長（單擊無反應）」的行為窗。
    若延遲值改動破壞單擊語意（例如 timer 從不到期、或 handler 沒把 expand 放進 timer callback），
    本測試會 catch（after == before）。
    """
    page = _render_mock_kg(logged_in_page, "kg")
    before = page.locator("#kgGraphView .kg-node").count()
    badge_node = page.locator("#kgGraphView .kg-node:has(.kg-hidden-badge)").first
    badge_node.click()  # 單一 click，不接第二擊
    # 等明顯大於 DBLCLICK_DELAY_MS(320) 讓 pending single-click timer 到期跑 expand
    page.wait_for_timeout(700)
    after = page.locator("#kgGraphView .kg-node").count()
    assert after > before, (
        f"單擊等待 timer 到期後未 expand（after={after} == before={before}）——"
        f"delayed single-click timer 到期未跑動作，或延遲值破壞單擊語意"
    )


# ═══════════════════════════════════════════════════════════════════════════
# B 組：真跑 DR 端到端煙霧測試（🔧 R4 — @real_llm，燒錢 opt-in、default skip）
# 驗真實 DR flow 確實能跑到產出 KG（A 組已覆蓋 render 層互動；此組只驗端到端接通）。
# ═══════════════════════════════════════════════════════════════════════════

@real_llm
def test_kg_dr_real_flow_produces_kg(logged_in_page: Page):
    """真跑一次 DR（燒錢）→ #kgGraphView 確實產出骨架節點（端到端接通煙霧測試）。"""
    page = _run_dr_with_kg(logged_in_page, query="台積電先進製程與地緣政治風險")
    page.wait_for_selector("#kgGraphView svg", timeout=60000)
    assert page.locator("#kgGraphView .kg-node").count() > 0
    # 真 DR 產出的 KG 也應走骨架範式（有下載按鈕 = 渲染層接上）
    expect(page.locator("#kgDownloadBtn")).to_be_visible()
