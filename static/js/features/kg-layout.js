// static/js/features/kg-layout.js
//
// 增量佈局（純函式，可 node:test 直測；無 DOM、無副作用）。
// 設計點 D（CEO 2026-07-21 改判鎖定）：累積探索需保留空間記憶 → 已放節點座標
// 凍結不動，只有新展開的鄰居找空位擺放。不引入 D3 force（2026-03 刻意移除的
// 相依不重引）；這是「靜態極座標的增量版」。
//
// - layoutSkeleton(subgraph, dims): 骨架初始 / reset 時整體極座標佈局（沿用
//   knowledge-graph.js:474-528 的 sector 演算法思路，此時無「已放不動」約束）。
// - placeNewNodes(existingPositions, newEntities, focusParentId, dims): 增量。
//   既有 positions 原封不動 copy 進結果；新節點以焦點父為錨、沿阿基米德螺線找
//   未占用位置擺放、避讓所有既有節點（含本批先放者）。兜底（畫布放不下）也保證
//   每節點落不同螺線點、不疊同點（🔧 R2 BLOCKER 修訂）。

const BASE_RADIUS = 14;   // 鏡像 knowledge-graph.js:493
const SCALE_FACTOR = 4;
const MAX_RADIUS = 40;
const ANGULAR_SLOTS = 12; // 一環等分角度數
const SPAWN_RADIUS = 90;  // 新節點距父的第一環半徑
// 🔧 R3 N-R3-1：RING_STEP 必須 > 0 —— 兜底不疊點的隱性前提是「半徑隨 step 遞增」
// （RADIUS_PER_STEP = RING_STEP / ANGULAR_SLOTS = 60/12 = 5 > 0）。螺線取樣點座標各異
// 靠 θ 單調遞增 + 半徑 r = SPAWN_RADIUS + step*RADIUS_PER_STEP 遞增；若 RING_STEP 設 0
// 則 RADIUS_PER_STEP=0，同一半徑上繞圈 → toFixed(3) 精度下不同 step 可能落回同座標 →
// 退化回疊點（🔧 R2 BLOCKER 想根治的 regression）。改此值務必保持 RADIUS_PER_STEP > 0
// 且每 step 位移在 toFixed(3) 精度下可辨（現值 5px/step 遠大於 0.001，安全）。
const RING_STEP = 60;     // 一環占滿後加的半徑（必須 > 0，見上 N-R3-1）
const MIN_SEPARATION = 30;// 兩節點中心最小間距（避讓門檻）

export function distance(p, q) {
    const dx = p.x - q.x, dy = p.y - q.y;
    return Math.sqrt(dx * dx + dy * dy);
}

function nodeRadiusFromDegree(deg) {
    return Math.min(BASE_RADIUS + deg * SCALE_FACTOR, MAX_RADIUS);
}

// 骨架 / reset：整體極座標佈局（無凍結約束）。回 { id -> {x,y,r,entity,isCenter} }。
export function layoutSkeleton(subgraph, dims) {
    const entities = subgraph.entities || [];
    const rels = subgraph.relationships || [];
    const width = dims.width || 600, height = dims.height || 400;
    const centerX = width / 2, centerY = height / 2;

    // degree（子圖內）
    const degree = {};
    entities.forEach(e => { degree[e.entity_id] = 0; });
    rels.forEach(r => {
        if (degree[r.source_entity_id] !== undefined) degree[r.source_entity_id]++;
        if (degree[r.target_entity_id] !== undefined) degree[r.target_entity_id]++;
    });

    const positions = {};
    if (entities.length === 0) return positions;

    // 中心 = 最高 degree（tie 取先出現者，鏡像 knowledge-graph.js:466-472）
    let center = entities[0];
    entities.forEach(e => { if ((degree[e.entity_id] || 0) > (degree[center.entity_id] || 0)) center = e; });
    positions[center.entity_id] = {
        x: centerX, y: centerY,
        r: Math.max(nodeRadiusFromDegree(degree[center.entity_id] || 0), 24),
        entity: center, isCenter: true
    };

    // 其餘按 type 分 sector（鏡像 knowledge-graph.js:474-528）
    const remaining = entities.filter(e => e.entity_id !== center.entity_id);
    const typeGroups = {};
    remaining.forEach(e => {
        const t = e.entity_type || 'unknown';
        (typeGroups[t] = typeGroups[t] || []).push(e);
    });
    const typeKeys = Object.keys(typeGroups);
    const numTypes = typeKeys.length || 1;
    const sectorAngle = (2 * Math.PI) / numTypes;
    const maxGroupSize = Math.max(...Object.values(typeGroups).map(g => g.length), 1);
    const useDoubleRing = maxGroupSize > 5;
    const innerR = Math.min(width, height) * (useDoubleRing ? 0.22 : 0.32);
    const outerR = Math.min(width, height) * 0.40;

    typeKeys.forEach((type, typeIdx) => {
        const group = typeGroups[type];
        const startAngle = typeIdx * sectorAngle - Math.PI / 2;
        const step = sectorAngle / (group.length + 1);
        group.forEach((entity, j) => {
            const angle = startAngle + (j + 1) * step;
            const ringR = useDoubleRing ? (j % 2 === 0 ? innerR : outerR) : innerR;
            positions[entity.entity_id] = {
                x: centerX + ringR * Math.cos(angle),
                y: centerY + ringR * Math.sin(angle),
                r: nodeRadiusFromDegree(degree[entity.entity_id] || 0),
                entity, isCenter: false
            };
        });
    });
    return positions;
}

// 增量：既有座標不動，新節點以父為錨找空位。回新 map（含既有 + 新）。
//
// 🔧 R2 BLOCKER 修訂：新節點找空位改用**阿基米德螺線**（r = a + b·θ）逐點掃描，
// 而非「固定環數 + 固定單點兜底」。螺線的每個取樣點座標天生各異（θ 單調遞增），
// 因此即使畫布極端擁擠、找不到滿足 MIN_SEPARATION 的 free slot，退最後手段時
// 取「螺線上距既有節點最遠的取樣點」，也保證**每個新節點落在不同螺線點、不疊同點**。
// 消費端（applyExpandAndRerender）只讀回傳的 {x,y,r,entity,isCenter}，不依賴內部
// 演算法，故本次改動不影響 layoutSkeleton / renderKGGraphView 對本函式輸出的消費。
export function placeNewNodes(existingPositions, newEntities, focusParentId, dims) {
    const width = dims.width || 600, height = dims.height || 400;
    // 既有原封不動 copy（凍結 — 這是 D 的核心）
    const result = {};
    Object.keys(existingPositions).forEach(id => { result[id] = existingPositions[id]; });

    // 錨點 = 焦點父；父不存在則錨到畫布中心
    const anchor = existingPositions[focusParentId] || { x: width / 2, y: height / 2 };

    // 距 result 中所有節點的最小距離（越大越空曠）
    function minDistToPlaced(cand) {
        let m = Infinity;
        Object.values(result).forEach(p => { const d = distance(cand, p); if (d < m) m = d; });
        return m === Infinity ? Number.MAX_VALUE : m;
    }

    // 阿基米德螺線取樣：以 anchor 為心，SPAWN_RADIUS 起始半徑，θ 每步 +ANGLE_STEP，
    // 半徑隨 θ 線性增長（每繞一圈 ANGULAR_SLOTS 步、半徑增 RING_STEP）。
    // MAX_SPIRAL_STEPS 上限夠大（畫布通常掃不完就找到 free slot）。
    const ANGLE_STEP = (2 * Math.PI) / ANGULAR_SLOTS;
    const RADIUS_PER_STEP = RING_STEP / ANGULAR_SLOTS;
    const MAX_SPIRAL_STEPS = 2000;

    (newEntities || []).forEach((entity, idx) => {
        let placed = null;
        // fallback 候選：螺線上「距既有節點最遠」的取樣點（保證各 idx 落不同點：
        // 起始 θ 依 idx 遞增偏移，兩個新節點的螺線起點不同 → 掃到的點集不同）。
        let bestCand = null, bestClearance = -1;
        const thetaStart = idx * ANGLE_STEP * 0.5; // idx 偏移，避免不同新節點螺線完全重合

        for (let step = 0; step < MAX_SPIRAL_STEPS; step++) {
            const theta = thetaStart + step * ANGLE_STEP;
            const radius = SPAWN_RADIUS + step * RADIUS_PER_STEP;
            const cand = { x: anchor.x + radius * Math.cos(theta), y: anchor.y + radius * Math.sin(theta) };
            const clearance = minDistToPlaced(cand);
            if (clearance >= MIN_SEPARATION) { placed = cand; break; } // 找到 free slot，停
            // 記錄目前最空曠的候選，供極端擁擠時的最後手段
            if (clearance > bestClearance) { bestClearance = clearance; bestCand = cand; }
        }
        // 極端：MAX_SPIRAL_STEPS 內都找不到 ≥ MIN_SEPARATION（畫布物理放不下）→
        // 用最空曠候選。因 thetaStart 隨 idx 不同 + 螺線半徑隨 step 遞增，各新節點的
        // bestCand 座標各不相同（不疊同點）。
        if (!placed) placed = bestCand || { x: anchor.x + SPAWN_RADIUS, y: anchor.y };

        result[entity.entity_id] = {
            x: placed.x, y: placed.y,
            r: BASE_RADIUS, // 新展開節點用基準半徑（degree 於重繪時由邊數自然體現）
            entity, isCenter: false
        };
        // 已放入 result → 後續同批新節點的 minDistToPlaced 會避開它（同批不重疊）
    });
    return result;
}
