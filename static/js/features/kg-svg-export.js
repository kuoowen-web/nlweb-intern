// static/js/features/kg-svg-export.js
//
// SVG「所見即所得」匯出（CEO 拍板 #3）：序列化畫面當下的焦點子圖 SVG DOM，
// lazy（點按鈕才做），前端零後端成本。純字串處理部分（buildStandaloneSVG /
// buildExportFilename）可 node:test 直測；serializeGraphSVG / triggerSVGDownload
// 觸碰 DOM，由 Agent E2E 覆蓋。
//
// 關鍵：node/edge label 樣式在 CSS class，序列化 DOM 不自動帶入 → 把關鍵樣式
// 以 <style> 注入 standalone SVG（EXPORT_CSS）。字型不 embed，離線 fall back
// 到系統中文字型（Microsoft JhengHei 等），中文仍可見（見 kg-spec §7 匯出取捨）。

// 匯出時注入的關鍵樣式（鏡像 news-search.css 的 .kg-* SVG 規則，只取離線必需者）。
// 🔧 R3 SF-R3-5：node label 只用 `.kg-node-label`（**移除 `.kg-node text`**）。
// 原因：badge 的 `<text>` append 在 `.kg-node` group 內 → 同時匹配 `.kg-node text`。
// `.kg-node text`（class+type，specificity 0,1,1）比 `.kg-hidden-badge`（class，0,1,0）高，
// 會覆蓋 badge 的 font-size:10px/font-weight:700（強制成 11px/500）→ 匯出 SVG 裡 badge 字級/字重跑掉。
// node label 本身有 `.kg-node-label` class（renderKGGraphView L790 verify），移除 `.kg-node text`
// 後 label 仍由 `.kg-node-label` 命中不掉樣式；badge 由 `.kg-hidden-badge` 命中，兩者 specificity
// 皆 0,1,0 且 selector 互斥，不再相互覆蓋。
export const EXPORT_CSS = [
    '.kg-node-label{font-size:11px;fill:#2D3436;font-weight:500;text-anchor:middle;}',
    '.kg-link{fill:none;stroke-opacity:0.7;}',
    '.kg-link-label{font-size:10px;fill:#2D3436;text-anchor:middle;dominant-baseline:central;}',
    '.kg-hidden-badge{font-size:10px;font-weight:700;fill:#2D3436;text-anchor:middle;}',
    'text{font-family:"Noto Sans TC","Microsoft JhengHei",sans-serif;}'
].join('');

const XMLNS = 'http://www.w3.org/2000/svg';

// 純字串：SVG 內容 → standalone 可離線開啟字串。
export function buildStandaloneSVG(innerSVG, opts = {}) {
    let svg = innerSVG;
    // 確保 root svg 有 xmlns（只在缺時加）。
    if (!svg.includes(`xmlns="${XMLNS}"`)) {
        svg = svg.replace(/^<svg/, `<svg xmlns="${XMLNS}"`);
    }
    // 注入 <style>（緊接在 <svg ...> 後）。
    const css = opts.css || EXPORT_CSS;
    svg = svg.replace(/(<svg[^>]*>)/, `$1<style>${css}</style>`);
    return `<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n${svg}`;
}

// 純字串：焦點名稱 → 安全檔名。
export function buildExportFilename(focusName) {
    const base = (focusName || '').trim();
    if (!base) return 'kg-knowledge-graph.svg';
    // 移除檔名不安全字元（/ \ : * ? " < > |），空白轉 -。
    const safe = base.replace(/[\/\\:*?"<>|]/g, '').replace(/\s+/g, '-');
    return `kg-${safe}.svg`;
}

// DOM：序列化 graph container 內的 svg → standalone 字串。
// clone 後注入 style，不污染畫面上的 live SVG。
export function serializeGraphSVG(svgEl) {
    if (!svgEl) return null;
    const clone = svgEl.cloneNode(true);
    const raw = new XMLSerializer().serializeToString(clone);
    return buildStandaloneSVG(raw, {});
}

// DOM：把文字內容下載為檔案（沿用 live-research.js:1071-1076 blob download pattern）。
export function downloadTextAsFile(text, filename, mimeType) {
    const blob = new Blob([text], { type: mimeType });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
}
