// static/js/features/kg-visible-set.js
//
// 可見集狀態機（純函式，可 node:test 直測；無 DOM、無副作用）。
// KG 下鑽的核心：任一時刻只有「可見集」內的節點被渲染。所有動作回傳「新 Set」
// （不 mutate 傳入的 Set），便於 render 層做 immutable 比較與復原。
//
// 開放點 A（累積）: expandNode 把新鄰居加入既有可見集（不清空）。
// 開放點 C（一次一層）: expandNode 只加直接鄰居，不遞迴多層。
// focusNode 是「聚焦」動作：把可見集收斂回「焦點 + 直接鄰居」單鄰域。
// 🔧 R2 SF-3：收合動作 = 「回骨架」reset（Task 6 的 ResetSkeletonBtn / 雙擊已聚焦節點
// 回骨架）。CEO A 拍板講的是「累積探索 + 回骨架 reset」，未要求 per-node collapse；
// 故不提供 collapseNode（避免死 code：實作了卻無 UI 入口）。

// 無向鄰居：所有 source==id 或 target==id 的邊的另一端。
export function neighborsOf(kg, id) {
    const out = new Set();
    (kg.relationships || []).forEach(r => {
        if (r.source_entity_id === id) out.add(r.target_entity_id);
        if (r.target_entity_id === id) out.add(r.source_entity_id);
    });
    // 只保留真實存在於 entities 的鄰居（過濾 dangling）
    const valid = new Set((kg.entities || []).map(e => e.entity_id));
    return new Set([...out].filter(n => valid.has(n)));
}

// 展開：可見集 ∪ {id 的直接鄰居}。回新 Set。
export function expandNode(kg, visible, id) {
    const next = new Set(visible);
    neighborsOf(kg, id).forEach(n => next.add(n));
    next.add(id); // 焦點本身確保可見
    return next;
}

// 🔧 R2 SF-3：collapseNode 已移除（per-node collapse 不做，收合 = 回骨架 reset）。

// 聚焦：可見集 = {id} ∪ id 的直接鄰居（丟棄其餘）。回新 Set。
export function focusNode(kg, id) {
    const next = neighborsOf(kg, id);
    next.add(id);
    return next;
}

// 點節點動作決策（純函式）。
// 有隱藏鄰居 → expand（下鑽優先）；無隱藏鄰居 → 退回既有 highlight/deselect 切換。
export function decideNodeClickAction({ hiddenNeighborCount, isSelected }) {
    if (hiddenNeighborCount > 0) return 'expand';
    return isSelected ? 'deselect' : 'highlight';
}
