"""
E2E: 前端 XSS 修復真瀏覽器 render 驗證（full-scan P1 批6 FE-2/3/4）。

驗的是 land 前 node --test 驗不到的層——**真瀏覽器實際 render production 函式的輸出、
payload 是否真的不執行**。作法：page.goto 目標環境（本地或 prod）後，在頁面 context
內動態 import 前端 module（其相對 import 依賴由該環境自身 static 供給），對 production
render 函式餵真實 XSS payload，把輸出塞進真 DOM，斷言：
  (a) 全域 XSS 觸發計數器保持 0（payload 未執行）
  (b) 產物內無可執行 sink（javascript: href / 屬性 breakout / 未 escape 的 <img>）
  (c) 良性內容（中文）正常顯示

不需登入（純前端 render 邏輯，不依賴後端回應）。用 page fixture 不用 logged_in_page。
env：E2E_BASE_URL（預設 localhost:8000）。
"""
import json

import pytest
from playwright.sync_api import Page


# 本次修復觸及的前端 module（相對於 base_url 的 static 路徑）
_DR_MODULE = "/static/js/features/deep-research.js"


def _load_dr_module(page: Page, base_url: str):
    """page.goto base_url 後在頁面 context 動態 import deep-research module。

    回傳 module 的 export 函式名 list（驗 import 成功）。import 失敗 → fail-loud。
    """
    page.goto(f"{base_url}/")
    # 等頁面 static 就緒（body 有內容即可，不等 networkidle：analytics beacon 常駐）
    page.wait_for_selector("body", timeout=15000)

    result = page.evaluate(
        """async (modPath) => {
            try {
                const mod = await import(modPath + '?e2e=' + Date.now());
                window.__dr_mod = mod;
                window.__xss_fired = 0;
                return { ok: true, exports: Object.keys(mod).filter(k => typeof mod[k] === 'function') };
            } catch (e) {
                return { ok: false, error: String(e) };
            }
        }""",
        _DR_MODULE,
    )
    if not result.get("ok"):
        pytest.fail(
            f"無法在 {base_url} 載入 {_DR_MODULE}（動態 import 失敗）："
            f"{result.get('error')}。確認 server 起著且 static 供給該檔。",
            pytrace=False,
        )
    return result["exports"]


def test_citation_href_javascript_protocol_blocked(page: Page, base_url: str):
    """FE-4 + 批6 R1 補修：addCitationLinks 對 javascript:/data: url 降級不可點，
    對 urn topic 的 title 屬性做 escapeHtmlAttr——真瀏覽器 render 後 payload 不執行。"""
    exports = _load_dr_module(page, base_url)
    assert "addCitationLinks" in exports, "prod 前端缺 addCitationLinks（修復未上線？）"

    # sources 是 url 字串陣列（親讀 deep-research.js:119 sources[index] 為字串）
    outcome = page.evaluate(
        """() => {
            const mod = window.__dr_mod;
            // [1]=javascript: 協議 / [2]=urn 含屬性 breakout payload / [3]=正常中文標題 url
            const html = '看 [1] 與 [2] 與 [3]。';
            const sources = [
                'javascript:window.__xss_fired++//',
                'urn:llm:knowledge:" onmouseover="window.__xss_fired++" x="惡意主題',
                'https://example.com/正常文章'
            ];
            const out = mod.addCitationLinks(html, sources);
            const div = document.createElement('div');
            div.innerHTML = out;           // 真 render 進 DOM
            document.body.appendChild(div);
            // 觸發 hover 事件（若屬性 breakout 成功，onmouseover 會 fire）
            div.querySelectorAll('span,a').forEach(el => {
                el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
            });
            const anchors = [...div.querySelectorAll('a')];
            const result = {
                xss_fired: window.__xss_fired,
                output: out,
                js_href_anchors: anchors.filter(a => (a.getAttribute('href')||'').toLowerCase().startsWith('javascript:')).length,
                has_no_link_downgrade: out.includes('citation-no-link'),
                // 真 breakout 判定＝有元素真的帶事件屬性（DOM attribute），非 innerHTML 字串比對
                //   （escapeHtmlAttr 把 " 轉 &quot; 後，payload 仍以「屬性值文字」出現在 innerHTML
                //    序列化裡，但那是單一屬性值、未斷開成新屬性——用 hasAttribute 才判得準）
                elements_with_event_attr: [...div.querySelectorAll('*')].filter(el =>
                    el.hasAttribute('onmouseover') || el.hasAttribute('onerror') || el.hasAttribute('onfocus')
                ).length,
                chinese_preserved: div.textContent.includes('看') && div.textContent.includes('。')
            };
            div.remove();
            return result;
        }"""
    )

    assert outcome["xss_fired"] == 0, (
        f"XSS payload 執行了（__xss_fired={outcome['xss_fired']}）！"
        f"output={outcome['output'][:400]}"
    )
    assert outcome["js_href_anchors"] == 0, "存在 javascript: href 的可點連結（isSafeUrl 未擋）"
    assert outcome["has_no_link_downgrade"], "不安全 url 未降級為 citation-no-link"
    assert outcome["elements_with_event_attr"] == 0, (
        "urn topic 的 title 屬性真的 breakout 成事件屬性（escapeHtmlAttr 未生效）"
    )
    assert outcome["chinese_preserved"], "中文內容顯示被破壞"


def test_clarification_option_attribute_breakout_blocked(page: Page, base_url: str):
    """批6 R1+R2：addClarificationMessage 的 data-* 屬性用 escapeHtmlAttr、
    is_comprehensive normalize 成 boolean、submit_label 用 escapeHTML——
    LLM clarification payload 不 attribute breakout、不 HTML 注入。"""
    exports = _load_dr_module(page, base_url)
    assert "addClarificationMessage" in exports, "prod 前端缺 addClarificationMessage"

    outcome = page.evaluate(
        """() => {
            const mod = window.__dr_mod;
            window.__xss_fired = 0;
            // clarificationData 結構照 addClarificationMessage(clarificationData, originalQuery, ...)
            const clarificationData = {
                questions: [{
                    question_id: 'q1',
                    question: '請選擇範圍',
                    options: [{
                        id: 'o1" onmouseover="window.__xss_fired++" x="',
                        label: '選項<img src=x onerror="window.__xss_fired++">',
                        query_modifier: 'mod',
                        is_comprehensive: '" onfocus="window.__xss_fired++" x="',  // 非 boolean payload
                        time_range: null
                    }]
                }],
                submit_label: '</button><img src=x onerror="window.__xss_fired++">'
            };
            let threw = null, addedEl = null;
            const beforeCount = document.querySelectorAll('.clarification-message, [class*=clarification]').length;
            try {
                // 需要 chatMessages 容器——建一個
                if (!document.getElementById('chatMessages')) {
                    const c = document.createElement('div');
                    c.id = 'chatMessages';
                    document.body.appendChild(c);
                    addedEl = c;
                }
                mod.addClarificationMessage(clarificationData, '原始查詢', null, null);
            } catch(e) { threw = String(e); }
            // 觸發所有新元素的 hover/focus
            const container = document.getElementById('chatMessages');
            let domHTML = container ? container.innerHTML : '';
            if (container) {
                container.querySelectorAll('*').forEach(el => {
                    el.dispatchEvent(new MouseEvent('mouseover', {bubbles:true}));
                    if (el.focus) try { el.focus(); } catch(e){}
                });
            }
            const result = {
                xss_fired: window.__xss_fired,
                threw: threw,
                // 真 breakout＝有元素真的帶事件屬性（DOM attribute），非字串比對
                elements_with_event_attr: container ? [...container.querySelectorAll('*')].filter(el =>
                    el.hasAttribute('onmouseover') || el.hasAttribute('onerror') || el.hasAttribute('onfocus')
                ).length : 0,
                // 真注入＝有真的 <img> 元素被建出來（非文字），且非既有頁面元素
                injected_imgs: container ? [...container.querySelectorAll('img')].filter(im =>
                    (im.getAttribute('src')||'') === 'x' || im.hasAttribute('onerror')
                ).length : 0,
                domHTML_snippet: domHTML.slice(0, 500)
            };
            if (addedEl) addedEl.remove();
            return result;
        }"""
    )

    assert outcome["threw"] is None, f"addClarificationMessage 拋錯：{outcome['threw']}"
    assert outcome["xss_fired"] == 0, (
        f"clarification XSS payload 執行了（__xss_fired={outcome['xss_fired']}）！"
        f"dom={outcome['domHTML_snippet']}"
    )
    assert outcome["elements_with_event_attr"] == 0, (
        f"clarification 屬性 breakout（真的事件屬性進 DOM）：{outcome['domHTML_snippet']}"
    )
    assert outcome["injected_imgs"] == 0, "submit_label/label 的 <img> HTML 注入成功（escapeHTML 未生效）"


def test_argument_node_evidence_ids_escaped(page: Page, base_url: str):
    """批6 R1 補修：renderArgumentNode 的 evidence_ids 進 innerHTML text 用 escapeHTML——
    含 HTML 的 evidence id 不注入。"""
    exports = _load_dr_module(page, base_url)
    assert "renderArgumentNode" in exports, "prod 前端缺 renderArgumentNode"

    outcome = page.evaluate(
        """() => {
            const mod = window.__dr_mod;
            window.__xss_fired = 0;
            const node = {
                node_id: 'n1',
                claim: '正常主張<img src=x onerror="window.__xss_fired++">',
                node_type: 'claim',
                evidence_ids: ['</span><img src=x onerror="window.__xss_fired++">', 'E2'],
                logic_warnings: ['警告<img src=x onerror="window.__xss_fired++">'],
                depends_on: [],
                confidence: 0.8
            };
            let threw = null, el = null;
            try {
                // renderArgumentNode 回傳 DOM element（:634 nodeEl.innerHTML=...）；nodeMap 是物件（Object.keys）
                el = mod.renderArgumentNode(node, 1, {}, null);
            } catch(e) { threw = String(e); }
            let injected = 0;
            if (el && el.nodeType === 1) {
                document.body.appendChild(el);
                el.querySelectorAll('*').forEach(x => x.dispatchEvent(new MouseEvent('mouseover',{bubbles:true})));
                // 真注入＝真的 <img src=x / onerror> 元素被建出來（非文字）
                injected = el.querySelectorAll('img[src="x"], img[onerror]').length;
                var snippet = el.innerHTML.slice(0, 400);
                el.remove();
            }
            const result = {
                xss_fired: window.__xss_fired,
                threw: threw,
                returned_element: !!(el && el.nodeType === 1),
                injected_imgs: injected,
                snippet: typeof snippet !== 'undefined' ? snippet : ''
            };
            return result;
        }"""
    )

    assert outcome["threw"] is None, f"renderArgumentNode 拋錯：{outcome['threw']}"
    assert outcome["returned_element"], "renderArgumentNode 未回傳 DOM element（簽名假設錯或修復破壞）"
    assert outcome["xss_fired"] == 0, (
        f"evidence_ids/claim/warnings XSS 執行了（__xss_fired={outcome['xss_fired']}）！snippet={outcome['snippet']}"
    )
    assert outcome["injected_imgs"] == 0, "claim/evidence_ids/warnings 的 <img onerror> 注入成功（escapeHTML 未生效）"
