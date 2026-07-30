// static/js/features/kg-rerun-gate.js
//
// KG 編輯 rerun no-op gate 的純判準（可 node:test 直測，無副作用、無 import）。
// 拖曳節點只改座標、不進 _kgEditStats（knowledge-graph.js:883-919 drag handler）→
// 純拖曳送出時 6 個 edit_summary 計數皆 0 → 無資料變更、不該觸發 rerun 白燒 LLM。
// 每個真實編輯路徑（save/delete/create node/edge）都 ++ 對應 stat，故 totalChanges===0
// 是「無資料變更」的安全充要判準（缺欄位以 0 計，防 undefined 誤放行）。
export function shouldBlockRerun(editSummary) {
    const s = editSummary || {};
    const total =
        (s.nodes_added || 0) + (s.nodes_deleted || 0) + (s.nodes_modified || 0) +
        (s.edges_added || 0) + (s.edges_deleted || 0) + (s.edges_modified || 0);
    return total === 0;
}
