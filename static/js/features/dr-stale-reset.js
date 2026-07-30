// static/js/features/dr-stale-reset.js
// [R9/R10] Step 0c「本次 final_result 有無有效 graph/KG」判準——純函式、無副作用（不 import browser
//   globals、不碰 module state/DOM），供 node:test import + Step 0c code 消費（同一實作、不 drift）。
//   照 search-generation.js 範本（無副作用 helper module）。data = final_result SSE envelope（top-level snake_case）。

// graph 有效 = argument_graph 是非空 list。[R9 verified] 對齊 graph/chain renderer early-return
//   （displayReasoningChainInContainer :317 / displayReasoningChain :369 皆 `!argumentGraph || length===0`）。
//   空 list `[]` 是 truthy，故判 `.length === 0`（純 truthiness 會漏）。
export function hasValidArgGraph(data) {
    const g = data?.argument_graph;
    return Array.isArray(g) && g.length > 0;
}

// KG 有效 = knowledge_graph.entities 是非空 list。[R9 verified] 對齊 KG renderer early-return
//   （displayKnowledgeGraph knowledge-graph.js:337 `!kg || !kg.entities || kg.entities.length===0`）。
//   空 KG 物件 `{entities:[]}` / `{metadata:{...}}`（無 entities）是 truthy → 純 truthiness 會漏 → 必須判 entities。
//   用 Array.isArray(kg.entities) 比 renderer 的 `!kg.entities` 略嚴（多防 entities 非陣列 truthy 值如 {}）。
export function hasValidKG(data) {
    const kg = data?.knowledge_graph;
    return !!kg && Array.isArray(kg.entities) && kg.entities.length > 0;
}
