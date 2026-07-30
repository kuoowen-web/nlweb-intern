// static/js/features/dr-restore-source.js
// [R9/R10] reload research report 來源選擇——純函式、無副作用（不 import browser globals、
//   不碰 module state/DOM），供 node:test import。照 search-generation.js 範本（無副作用 helper module）。
// [R10] 一次回全 restored surfaces：report/argumentGraph/chainAnalysis/knowledgeGraph 四者從同一
//   origin 決策選來源——server 有效報告 → 四者全走 server；nested fallback → 四者全走 nested。
//   杜絕「report 走 server 但 KG 走 nested」的分源不一致（R7 KG 分源踩過的坑，一次回全從根本防止）。
//   Step 2b if-else 改成從 helper 一次拿四者、不再各自分支選 graph/chain/KG。
// origin: 'server'（有意義 server report）| 'nested'（有意義 nested report）| 'none'（都無有意義報告）。
// [R10 收尾 should-fix1] server **與 nested 分支皆用 `.report` validity**（有 report 字串內容才算有效）——
//   對稱：server `{}` 與 nested `{researchReport:{}}` 空物件都不當有效來源、落 origin:'none'（clear）。
// ⚠️ [R10 收尾 should-fix2] **`origin` 只描述 report/argumentGraph/chainAnalysis 的來源決策**（三者嚴格跟隨 origin）。
//   **KG（knowledgeGraph）有自己的三層 fallback（server → nested → legacy `session.knowledgeGraph`），
//   不嚴格跟隨 origin**：即使 `origin === 'server'`，若 `serverReport.knowledgeGraph` 為 null，KG 會退到
//   legacy `session.knowledgeGraph`（`?? session?.knowledgeGraph`）→ 此時 KG 其實來自 legacy session、**非 server**。
//   故 `origin:'server'` **不保證** KG 來自 server。此為刻意設計（[R8 should-fix1] 保留 legacy KG 尾巴，避免
//   舊 session 的 sibling KG 消失）；消費端（Step 2b-KG）只需 `restored.knowledgeGraph` 這個值本身、不需知其來源，
//   故**不另加 `knowledgeGraphOrigin` 欄位**（避免未經要求的功能 + 增消費端複雜度），以此註解澄清語意即可。
export function pickResearchReportSource(session, fallbackDREntry) {
    // [R7 BLOCKER2] .report predicate（有意義報告），非 truthiness——空物件 {} 無 .report → serverReport 為 null → 落 nested。
    const serverReport = session?.researchReport?.report ? session.researchReport : null;
    if (serverReport) {
        // server 有效 → report/graph/chain/KG 四者全走 server（同源）。
        return {
            origin: 'server',
            report: serverReport,                                    // 整個 researchReport dict（setResearchReport 吃）
            argumentGraph: serverReport.argumentGraph ?? null,       // [R6] camelCase list
            chainAnalysis: serverReport.chainAnalysis ?? null,       // [R6] camelCase dict
            // [R8 should-fix1] KG 三層 fallback 的第一層（server），legacy 尾巴在 nested/none 分支保留。
            knowledgeGraph: serverReport.knowledgeGraph ?? session?.knowledgeGraph ?? null,
        };
    }
    // [R10 收尾 should-fix1] nested 分支也套 .report validity（與 server 分支 serverReport predicate 對稱）——
    //   nested entry 的 researchReport 要**有 .report 字串內容**才算有效 fallback；空物件 {}（無 .report）
    //   **不當有效 fallback** → 落 origin:'none'（clear）。防「nested 是 {researchReport:{}} 空物件 → 回 origin:'nested'
    //   + report:{} → setResearchReport({}) 後 restore-to-view `if(_rrRestore && _rrRestore.report)` 為 false →
    //   報告區顯示空」的對稱漏洞（R7-B2 只修 server 半邊的 {} truthiness，nested 半邊此處補齊）。
    //   （前端 nested find `e.isDeepResearch && e.researchReport` 用 truthiness、空物件 truthy → {researchReport:{}}
    //   entry 會被命中、fallbackDREntry 非 null，故 truthiness 判斷不足、須判 .report。）
    if (fallbackDREntry?.researchReport?.report) {
        // nested fallback（匿名/未登入/舊 session，且 nested report 有意義內容）→ 四者全走 nested（同源）。
        return {
            origin: 'nested',
            report: fallbackDREntry.researchReport,                  // nested entry 的 researchReport（已驗 .report 有內容）
            argumentGraph: fallbackDREntry.argumentGraph ?? null,    // nested entry top-level camelCase
            chainAnalysis: fallbackDREntry.chainAnalysis ?? null,
            // [R8 should-fix1] KG 三層 fallback：nested entry 的 knowledgeGraph → legacy session.knowledgeGraph。
            knowledgeGraph: fallbackDREntry.knowledgeGraph ?? session?.knowledgeGraph ?? null,
        };
    }
    // 都無有意義報告（server 無 .report + nested 無 .report/無 nested entry）→ clear（但 KG 仍保留 legacy
    //   session.knowledgeGraph 尾巴——should-fix1：「無 server 報告、無帶有效 report 的 nested entry、但
    //   session.knowledgeGraph sibling 有值」的舊 session KG 不消失）。空物件 nested `{researchReport:{}}` 也落此支
    //   （[R10 收尾 should-fix1] .report validity 對稱，nested 空物件視同無效 fallback）。
    return { origin: 'none', report: null, argumentGraph: null, chainAnalysis: null, knowledgeGraph: session?.knowledgeGraph ?? null };
}
