// static/js/features/kg-subgraph.js
//
// 可見集 → 子圖投影（純函式，可 node:test 直測；無 DOM、無副作用）。
// render 層每次拿到的「當前要畫的圖」就是這個投影的輸出 —— 既有極座標佈局器
// （knowledge-graph.js renderKGGraphView）吃它，完全不需知道下鑽存在。
//
// 投影規則：
//   - entities：只保留 visibleIds 內的節點（保留全部原欄位）。
//   - relationships：只保留「兩端皆可見」的邊（鏡像 knowledge-graph.js:530-540
//     的 dangling 過濾，這裡改用可見集而非全 nodeIds）。
//   - 每個可見節點附 hiddenNeighborCount：其鄰居中「仍不可見」的數量
//     （給 render 層畫「+N」badge 提示還有多少可展開）。

export function projectSubgraph(kg, visibleIds) {
    const allEntities = kg.entities || [];
    const allRels = kg.relationships || [];

    // 🔧 R2 SF-4：真實 entity id 集合，用於過濾 dangling ghost。
    // 邊可能指向不存在的 entity（dangling）；若把 ghost 算進鄰居，visible node 會顯示
    // 假的「+N」badge、點展開卻無新節點可見。故建 neighborMap 時只加「真 entity」的另一端。
    const validIds = new Set(allEntities.map(e => e.entity_id));

    // 先建每個節點的鄰居集合（用於算 hiddenNeighborCount，只計真 entity 鄰居）。
    const neighborMap = {};
    allEntities.forEach(e => { neighborMap[e.entity_id] = new Set(); });
    allRels.forEach(r => {
        const s = r.source_entity_id, t = r.target_entity_id;
        // 只在「另一端也是真 entity」時才加入鄰居（排除 dangling ghost）
        if (neighborMap[s] && validIds.has(t)) neighborMap[s].add(t);
        if (neighborMap[t] && validIds.has(s)) neighborMap[t].add(s);
    });

    const entities = allEntities
        .filter(e => visibleIds.has(e.entity_id))
        .map(e => {
            const neigh = neighborMap[e.entity_id] || new Set();
            let hidden = 0;
            neigh.forEach(n => { if (!visibleIds.has(n)) hidden++; });
            return { ...e, hiddenNeighborCount: hidden };
        });

    // 🔧 R3 SF-R3-4：relationship filter 除了「兩端皆可見」，另 guard「兩端皆真 entity」
    // （validIds，已於上面 SF-4 建好，複用）。防禦 bad caller 把 ghost id 塞進 visibleIds
    // 的情況——此時 dangling rel（指向 ghost）的兩端都可能「在 visibleIds 內」卻其一非真
    // entity，只查 visibleIds 會讓 dangling rel 漏進子圖（畫出連到 ghost 的邊）。
    // 加 validIds guard 後，與 badge 計數（neighborMap 只計真 entity）的 dangling 排除一致。
    const relationships = allRels.filter(
        r => visibleIds.has(r.source_entity_id) && visibleIds.has(r.target_entity_id)
            && validIds.has(r.source_entity_id) && validIds.has(r.target_entity_id)
    );

    return { entities, relationships };
}
