/**
 * text-fragment.js — 瀏覽器原生 URL Text Fragment（#:~:text=START,END）共用工具。
 * 由 live-research.js（LR citation [N]）與 search.js（搜尋卡片「閱讀全文」）共用。
 * 命中率策略（雙錨點 / 唯一性 heuristic / 降級三態）見各函式註解；
 * 演算法契約由 Python 鏡像 test_lr_textfragment_url.py 鎖定 —— 改演算法須同步改鏡像。
 * 抽自 live-research.js（commit c236d8b9）：逐字搬移，邏輯未變。
 */

/**
 * 屬性安全跳脫（杜絕 HTML attribute injection）。
 * 既有 escapeHTML（search.js）用 textContent→innerHTML 不跳脫 `"` —— 屬性值
 * 內必須跳脫 `"`/`'`，故此處自帶最小版（Decision 6：href/title 不字串拼接）。
 */
export function escapeHtmlAttr(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

// 命中率策略常數（plan 核心風險專章；Python 鏡像 test_lr_textfragment_url.py 鎖契約）
export const ANCHOR_LEN = 12;   // spike 驗 12 字命中 90%；Codex 警告 10 太短易錯位（可微調至 16）
export const MIN_QUOTE = 4;     // quote 短於此 → 不組 fragment
// 唯一性 heuristic：純標點/純數字/常見媒體名/日期格式 → 錨點不唯一，不組 fragment
export const LOW_UNIQUENESS = /^[\s\d\p{P}]+$|^(中央社|聯合報|自由時報|中時|蘋果|ETtoday|CNA|Reuters)$|^\d{4}[-/年.]\d{1,2}([-/月.]\d{1,2})?日?$/u;

// text fragment directive 安全編碼：encodeURIComponent 後再處理 `-`（語法字元）
export function encFrag(s) {
    return encodeURIComponent(s).replace(/-/g, '%2D');
}

/**
 * O2-TF: 用 verbatim 原文 quote 組瀏覽器原生 Text Fragment URL（#:~:text=START,END）。
 * - 雙錨點：START = quote 前 ANCHOR_LEN 字，END = 末 ANCHOR_LEN 字（中間差異不影響命中）。
 * - quote 太短（< MIN_QUOTE）/ 唯一性不足 / START==END → 回 null（caller 降級裸 URL）。
 * - 用 new URL(url) 解析既有 #hash（避免直接拼 `#:~:text=` 造成雙 fragment 非法 URL）。
 * @returns {string|null} fragment URL，或 null（quote 不堪用，caller 降級）
 */
export function buildTextFragmentUrl(url, quote) {
    if (!url || !quote) return null;
    const q = String(quote).trim();
    if (q.length < MIN_QUOTE) return null;
    // 截斷邊界保護：去尾端孤立標點，避免 END 錨點停在不完整字元串。不改內部字元。
    const clean = q.replace(/[\s，。、；：,.;:]+$/u, '') || q;

    let directive;
    if (clean.length <= ANCHOR_LEN * 2) {
        // 太短，整段當單一錨點；唯一性檢查
        if (LOW_UNIQUENESS.test(clean)) return null;
        directive = encFrag(clean);
    } else {
        const start = clean.slice(0, ANCHOR_LEN);
        const end = clean.slice(-ANCHOR_LEN);
        // 唯一性 heuristic：START 不唯一 或 START==END → 降級
        if (start === end || LOW_UNIQUENESS.test(start)) return null;
        directive = encFrag(start) + ',' + encFrag(end);
    }

    // 用 new URL 處理既有 #hash（避免直接拼 `#:~:text=` 造成雙 fragment 非法）
    let u;
    try {
        u = new URL(url);
    } catch (e) {
        return null;  // url 非法 → caller 降級裸 url
    }
    const sep = u.hash ? ':~:text=' : '#:~:text=';
    return u.href + sep + directive;
}

/**
 * O2-TF: 依 citation source 決定最終 href + 可觀測三態標記。回傳 {href, textfrag}。
 * textfrag（Decision 4 三態）：
 *   'generated-unknown' = 組出 fragment（瀏覽器是否真 highlight 未知，JS 偵測不到）
 *   'not-generated'     = 沒組 fragment（quote 空/太短/web source/唯一性不足）→ 裸 URL
 * 只對 http/https 組 fragment（urn:/private:// 由 caller 上游分流，不進此函式）。
 */
export function buildCitationHref(src) {
    const url = (src && src.url) || '';
    const isHttp = /^https?:\/\//i.test(url);
    const fragUrl = isHttp ? buildTextFragmentUrl(url, src && src.quote) : null;
    if (fragUrl) return { href: fragUrl, textfrag: 'generated-unknown' };
    return { href: url, textfrag: 'not-generated' };  // 降級裸 URL（命中失敗不會更糟）
}
