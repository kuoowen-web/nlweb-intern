// static/js/features/kg-skeleton.js
//
// Top-N 骨架選擇（純函式，可 node:test 直測；無 DOM、無 import、無副作用）。
// 鏡像 kg-rerun-gate.js 的可測純函式先例。
//
// 骨架 = KG overhaul（互動下鑽）的初始可見集：一打開只顯示 top-N 高 degree
// 核心實體 + 它們「之間」的邊，長尾隱藏。N 自適應（開放點 B）：
//   N = clamp(round(totalEntities * 0.3), 8, 15)
// 且第 N 名 degree 的所有 tie 節點全收（避免任意砍同分節點）。

// degree[entity_id] = in-edges + out-edges。🔧 R5 N-R5-1：per-endpoint guard——
// 逐端點檢查是否在 entities 內（hasOwnProperty），在的端 +1、不在的端（dangling ghost）
// 不進 map。**不是整條 dangling edge 丟棄**：一端真一端 ghost 的邊，真的那端仍 +1。
// 鏡像現行 knowledge-graph.js:459-464 的 per-endpoint hasOwnProperty guard（行為一致，不改）。
export function computeDegreeMap(kg) {
    const degree = {};
    (kg.entities || []).forEach(e => { degree[e.entity_id] = 0; });
    (kg.relationships || []).forEach(r => {
        if (Object.prototype.hasOwnProperty.call(degree, r.source_entity_id)) degree[r.source_entity_id]++;
        if (Object.prototype.hasOwnProperty.call(degree, r.target_entity_id)) degree[r.target_entity_id]++;
    });
    return degree;
}

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

// 回傳 { skeletonEntityIds: Set<string>, hiddenCount: number }。
export function selectSkeleton(kg) {
    const entities = kg.entities || [];
    const total = entities.length;
    if (total === 0) return { skeletonEntityIds: new Set(), hiddenCount: 0 };

    const degree = computeDegreeMap(kg);

    // 目標骨架大小 N（自適應）。若 total <= N，全收。
    const N = clamp(Math.round(total * 0.3), 8, 15);
    if (total <= N) {
        return { skeletonEntityIds: new Set(entities.map(e => e.entity_id)), hiddenCount: 0 };
    }

    // 依 degree 降序排序（tie-break：entities 原始順序穩定，鏡像現行「先出現者」）。
    const sorted = entities
        .map((e, idx) => ({ id: e.entity_id, deg: degree[e.entity_id] || 0, idx }))
        .sort((a, b) => (b.deg - a.deg) || (a.idx - b.idx));

    // 取前 N 名，但第 N 名的 degree 若與後面 tie，全收（不任意砍同分）。
    const cutoffDeg = sorted[N - 1].deg;
    const skeleton = sorted.filter(s => s.deg >= cutoffDeg);
    const skeletonEntityIds = new Set(skeleton.map(s => s.id));

    return { skeletonEntityIds, hiddenCount: total - skeletonEntityIds.size };
}

// 渲染層初始化 helper：完整 kg → 初始下鑽 state。
// visibleIds 初始 == skeletonIds（一打開只顯示骨架）。
export function initVisibleState(kg) {
    const { skeletonEntityIds, hiddenCount } = selectSkeleton(kg);
    return {
        visibleIds: new Set(skeletonEntityIds),
        skeletonIds: new Set(skeletonEntityIds),
        hiddenCount
    };
}
