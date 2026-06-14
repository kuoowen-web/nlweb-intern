# Knowledge Graph 視覺化規格

> 本文件描述 KG 視覺化功能的實際實作，反映 `static/news-search.js` 與 `static/news-search.css` 的現況（截至 2026-03-31）。
>
> **範圍分界**：本文件涵蓋「KG 資料顯示與互動」。KG 節點/邊的新增、刪除、重命名屬於編輯功能，見 `docs/specs/kg-editing-spec.md`。

---

## 1. 目的與範圍

Knowledge Graph（知識圖譜）以中心放射式心智圖將 Deep Research 報告所萃取的實體與關係視覺化。目標是讓使用者在閱讀分析報告前，先一眼掌握議題的知識架構與核心實體。

| 功能子系統 | 文件 |
|---|---|
| KG 視覺化（本文件）| `docs/specs/kg-spec.md` |
| KG 編輯模式 | `docs/specs/kg-editing-spec.md` |

---

## 2. 資料來源

### 2.1 後端生成路徑

```
Query → Reasoning Orchestrator
  → Analyst Agent（AnalystResearchOutputEnhancedKG）
  → knowledge_graph: KnowledgeGraph（entities[], relationships[]）
  → orchestrator.py 序列化為 JSON
  → SSE done 事件的 metadata 欄位
```

KG 生成由 `config/config_reasoning.yaml` 中 `knowledge_graph_generation: false` 控制，可透過 `enable_kg` request 參數覆蓋。

### 2.2 前端接收路徑

`displayDeepResearchResults()` 從 SSE `done` 事件的 metadata 取出 KG：

```javascript
// 雙路徑向下相容
schemaObj?.knowledge_graph || metadata?.knowledge_graph
```

### 2.3 KG 資料結構（後端 Pydantic Schema）

**KnowledgeGraph**
```
entities: Entity[]
relationships: Relationship[]
```

**Entity**
```
entity_id: str (UUID)
name: str
entity_type: EntityType
description: str | None
evidence_ids: List[int]        // 引用的 citation index
confidence: "high" | "medium" | "low"
attributes: Dict[str, Any]
supporting_claims: List[str]   // 未來連結到 ArgumentNode
```

**Relationship**
```
relationship_id: str (UUID)
source_entity_id: str
target_entity_id: str
relation_type: RelationType
description: str | None
evidence_ids: List[int]
confidence: "high" | "medium" | "low"
temporal_context: Dict | None
```

**EntityType 列舉**：`person`, `organization`, `event`, `location`, `metric`, `technology`, `concept`, `product`, `facility`, `service`

**RelationType 列舉**：`causes`, `enables`, `prevents`（因果）; `precedes`, `concurrent`（時序）; `part_of`, `owns`（層級）; `related_to`（關聯）

**驗證規則**：`KnowledgeGraph.validate_relationships()` 會自動過濾掉引用不存在 entity_id 的關係（log warning，不 raise error）。

### 2.4 前端序列化格式差異

前端取得的 JSON 使用 `model_dump()` 序列化，欄位名稱與 Pydantic model 完全一致（snake_case）。orchestrator 額外附加 metadata：

```json
{
  "entities": [...],
  "relationships": [...],
  "metadata": {
    "generated_at": "ISO8601",
    "entity_count": N,
    "relationship_count": N
  }
}
```

---

## 3. Layout 演算法（中心放射式）

### 3.1 概述

現行 layout 為**靜態極座標計算**，無 D3 force simulation（force simulation 已於 2026-03 重設計時移除）。所有節點位置在渲染前即已確定，不會因物理模擬而移動。

### 3.2 演算法步驟

```
1. 建立 degree map：degree[entity_id] = in-edges + out-edges

2. 選取中心節點：
   - 最高 degree 的 entity 為中心節點
   - Tie-break：取 entities[] 中先出現者

3. 剩餘節點按 entity_type 分組 → typeGroups{}
   - 每個 type 為一個 sector（扇形區域）
   - sectorAngle = 2π / typeCount
   - sector 起始角度從 -π/2（頂部）開始，順時針排列

4. 每個 sector 內均勻分佈節點：
   - step = sectorAngle / (groupSize + 1)
   - node[j] → angle = startAngle + (j + 1) * step

5. 決定環半徑（ring radius）：
   - useDoubleRing = (maxGroupSize > 5)
   - 單環：innerRingR = min(width, height) * 0.32
   - 雙環（交錯排列）：
       innerRingR = min(width, height) * 0.22
       outerRingR = min(width, height) * 0.40
   - 偶數 index 節點放內環，奇數放外環

6. 極座標轉笛卡爾：
   x = centerX + ringR * cos(angle)
   y = centerY + ringR * sin(angle)

7. 中心節點：固定在 (centerX, centerY)
```

### 3.3 節點大小

```
BASE_RADIUS = 14
SCALE_FACTOR = 4
MAX_RADIUS = 40

nodeRadius(id) = min(BASE_RADIUS + degree(id) * SCALE_FACTOR, MAX_RADIUS)
centerRadius   = max(nodeRadius(centerId), 24)  // 中心節點最小半徑 24
```

---

## 4. 節點設計

### 4.1 品牌色對應表

`KG_ENTITY_STYLES` 常數（`static/news-search.js` line 3794）：

| Entity Type | Fill | Stroke | Stroke Style | Shape |
|---|---|---|---|---|
| person | `#FDCB6E`（金）| `#2D3436` | 2px solid | circle |
| organization | `#FDCB6E`（金）| `#2D3436` | 2px dashed `4,3` | diamond |
| event | `#FFEAA7`（淺金）| `#2D3436` | 2px solid | circle |
| location | `#FFEAA7`（淺金）| `#2D3436` | 2px solid | diamond |
| metric | `#FFFFFF`（白）| `#2D3436` | 2px solid | circle |
| technology | `#FFFFFF`（白）| `#2D3436` | 2px solid | diamond |
| concept | `#B2BEC3`（灰）| `#2D3436` | 2px solid | circle |
| product | `#B2BEC3`（灰）| `#2D3436` | 2px dashed `4,3` | circle |
| 其他（fallback）| `#B2BEC3` | `#2D3436` | solid | circle |

**中心節點**（最高 degree，不論 entity_type）：
- Fill：`#FDCB6E`
- Stroke：`#2D3436`，3px solid
- Shape：強制 circle

### 4.2 Diamond 實作

Diamond 以旋轉 45° 的 `<rect>` 實作：

```javascript
el.append('rect')
  .attr('x', -size/2).attr('y', -size/2)
  .attr('width', size).attr('height', size)
  .attr('transform', 'rotate(45)')
// size = nodeRadius * 1.4（使面積與同半徑 circle 相近）
```

### 4.3 節點 Label

- 位置：節點下方 `dy = nodeRadius + 14`
- 字體：11px，font-weight 500，fill `#2D3436`
- 截斷：超過 12 字元 → 截斷加 `...`
- `pointer-events: none`（不攔截 mouse event）

---

## 5. 邊設計

### 5.1 邊類型

所有邊都是有向邊（帶箭頭），分兩種路徑：

| 情況 | 路徑類型 | 說明 |
|---|---|---|
| 邊的一端為中心節點 | 直線（`M...L...`）| `computeStraightPath()` |
| 兩端皆為非中心節點 | 二次貝茲曲線（`M...Q...`）| `computeArcPath()` |

### 5.2 直線路徑

- 方向：中心節點 → 葉節點（即使原始關係方向相反，arrow 也從中心出發）
- 起點偏移：從節點邊緣出發（不穿透節點）
- 終點偏移：距目標節點邊緣 8px（arrowhead 空間）
- Diamond 節點的 boundingR 使用 `r * 1.0`（同 circle radius）

### 5.3 弧線路徑（Bezier）

- 控制點：線段中點 + 垂直偏移
- bulge = `min(dist * 0.2, 40)`（弧度上限 40px，防過度彎曲）
- 控制點：`midpoint + normal * bulge`

### 5.4 視覺樣式

- 一般邊：stroke `#B2BEC3`，stroke-width 1.5，stroke-opacity 0.7，fill none
- 高亮邊（節點被點選時）：stroke `#FDCB6E`，stroke-width 2.5，opacity 1
- 非連接邊（暗化）：opacity 0.15

### 5.5 Arrow Markers

兩個 SVG defs marker：
- `#kg-arrow`（一般）：fill `#B2BEC3`
- `#kg-arrow-highlight`（高亮）：fill `#FDCB6E`
- `viewBox="0 -5 10 10"`，`markerWidth/Height=6`

### 5.6 邊 Label

- 位置：中心邊 → 直線中點；葉節點邊 → 貝茲中點偏移 60%
- 顯示中文 relation label（`KG_RELATION_LABELS`）
- 字體：10px，fill `#2D3436`，text-anchor middle

---

## 6. D3 Simulation

**現行 layout 無 D3 force simulation。**

節點位置由步驟 3 的靜態極座標計算決定，`d3.select().append()` 僅用於 SVG DOM 操作，不使用 `d3.forceSimulation()`。

全域變數 `kgSimulation` 保留作為清理 hook（在 `renderKGGraphView()` 入口呼叫 `kgSimulation.stop()` 以防舊 simulation 殘留），目前正常情況下永遠為 null。

### Zoom & Pan

```javascript
d3.zoom()
  .scaleExtent([0.3, 3])
  .on('zoom', (event) => { g.attr('transform', event.transform); })
```

縮放範圍：0.3x ～ 3x。

---

## 7. 互動行為

### 7.1 Click-to-Highlight（節點點選）

1. 點擊節點 → `highlightNode(d)`：
   - 找出所有鄰接節點（source 或 target 含該節點的邊）
   - 非鄰接節點：opacity 0.2
   - 非連接邊：opacity 0.15，stroke `#B2BEC3`
   - 連接邊：stroke `#FDCB6E`，stroke-width 2.5，marker 換成 `#kg-arrow-highlight`

2. 再次點擊同一節點 → `deselectAll()`（還原所有透明度與邊色）

3. 點擊 SVG 空白處 → `deselectAll()`

### 7.2 Hover Tooltip

`mouseenter` 觸發，顯示：
- 節點名稱、entity_type 中文標籤、description（若有）、連結數（degree）

**Tooltip 定位**：以 container BoundingClientRect 為基準，計算 tipX/tipY，並 clamp 避免溢出容器（max-width 300px，估算高度 100px）。

`mouseleave` 移除 `.visible` class（opacity → 0）。

### 7.3 互動提示 Overlay

每次 `renderKGGraphView()` 後，在容器右下角顯示「拖曳移動・滾輪縮放・點擊節點查看」提示，3 秒後 fade out（`.faded` class）。滑鼠移入 container 時重新顯示（opacity 0.75）。

### 7.4 View Toggle（圖形 / 列表）

`setupKGViewToggle()` 在 `#kgViewToggle` 監聽點擊：

- **圖形模式**（預設）：顯示 `#kgGraphView`，隱藏 `#kgDisplayContent`。切換時若容器寬度 > 0，重新呼叫 `renderKGGraphView()` 以正確計算尺寸。
- **列表模式**：顯示 `#kgDisplayContent`（由 `renderKGListView()` 填充的 HTML 列表），隱藏 `#kgGraphView`。

### 7.5 Collapse / Hide

- **收起（Collapse）**：`#kgToggleButton` 切換 `#kgContentWrapper` 的 display，header 保持可見。
- **隱藏（Hide）**：`#kgHideBtn` 隱藏整個 `#kgDisplayContainer`，顯示 `#kgRestoreBar`，並設定 `dataset.userHidden = 'true'`，同時寫入 `localStorage('nlweb-kg-hidden')`。
- **還原**：點擊 `#kgRestoreBar` 顯示 container，`dataset.userHidden = 'false'`，更新 localStorage。

---

## 8. Session Persistence

### 8.1 全域狀態

```javascript
let currentKGData = null;    // 當前 session 的 KG 資料（由 displayKnowledgeGraph() 設定）
let kgSimulation = null;     // 保留作清理 hook（目前永遠為 null）
```

### 8.2 Session History 內的 KG 存儲

每次新搜尋完成後，`sessionHistory.push({...})` 包含：

```javascript
knowledgeGraph: currentKGData ? JSON.parse(JSON.stringify(currentKGData)) : null
```

使用 `JSON.parse(JSON.stringify(...))` 做深拷貝，避免後續操作污染歷史記錄。

### 8.3 Session 切換還原

`restoreSession(sessionIndex)` 判斷：
- 若 `session.isDeepResearch && session.researchReport`：呼叫 `displayKnowledgeGraph(session.knowledgeGraph)`（如果有）
- 若為普通搜尋：不顯示 KG

`loadSavedSession()` 載入 localStorage 中的 session 時，同樣在 step 末尾呼叫 `displayKnowledgeGraph(session.knowledgeGraph)`。

### 8.4 跨 Session 殘留 Bug（已修復）

**問題**：切換到無 KG 的 session 時，前一 session 的 KG 視覺上仍殘留於畫面。

**根因**：`loadSavedSession()` 的普通搜尋分支（`!session.researchReport`）只清空 researchView，未將 `#kgDisplayContainer` 設回 `display: none`，也未重置 `currentKGData = null`。

**修復位置**：`cancelActiveSearch()` / session 切換初始化流程中：

```javascript
// 清理知識圖譜狀態（在 resetUIForNewSearch() 內）
currentKGData = null;
if (kgSimulation) { kgSimulation.stop(); kgSimulation = null; }
const kgContainerReset = document.getElementById('kgDisplayContainer');
if (kgContainerReset) kgContainerReset.style.display = 'none';
```

此段位於 `static/news-search.js` 第 1589-1593 行。

### 8.5 User Hidden Preference

`initKGVisibilityToggle()` 在 DOMContentLoaded 時讀取 `localStorage('nlweb-kg-hidden')`。若為 `'true'`，設定 `kgContainer.dataset.userHidden = 'true'`。

`displayKnowledgeGraph()` 尊重此偏好：若 `dataset.userHidden === 'true'`，仍執行完整渲染（資料準備好），但 container 保持 `display: none`，並顯示 restore bar。

---

## 9. HTML 結構

```html
<!-- KG Restore Bar -->
<div class="kg-restore-bar" id="kgRestoreBar">...</div>

<!-- KG 主容器 -->
<div class="kg-display-container" id="kgDisplayContainer" style="display: none;">
  <div class="kg-display-header">
    <div class="kg-display-title">
      <span>知識圖譜</span>
      <span class="kg-display-metadata" id="kgMetadata"></span>
    </div>
    <div class="kg-view-toggle" id="kgViewToggle">
      <button class="kg-view-btn active" data-view="graph">圖形</button>
      <button class="kg-view-btn" data-view="list">列表</button>
    </div>
    <button id="kgToggleButton">收起</button>
    <button class="kg-hide-btn" id="kgHideBtn">隱藏</button>
  </div>
  <div id="kgContentWrapper">
    <div class="kg-graph-container" id="kgGraphView">
      <div class="kg-tooltip" id="kgTooltip"></div>
      <!-- SVG 由 D3 動態注入 -->
    </div>
    <div class="kg-display-content" id="kgDisplayContent" style="display: none;">
      <!-- 列表 HTML 由 renderKGListView() 填充 -->
    </div>
    <div class="kg-legend" id="kgLegend"></div>
    <div class="kg-display-empty" id="kgDisplayEmpty" style="display: none;"></div>
  </div>
</div>
```

---

## 10. CSS 關鍵樣式

| 選擇器 | 說明 |
|---|---|
| `.kg-display-container` | 白底，圓角 12px，box-shadow，border 2px `var(--color-primary-bg)` |
| `.kg-graph-container` | 高度 400px，`overflow: hidden`，`position: relative`（tooltip 定位基準）|
| `.kg-node` | `cursor: pointer`，`transition: opacity 0.3s` |
| `.kg-node:hover circle/rect` | `filter: brightness(1.08)`，stroke-width 3px |
| `.kg-link` | `stroke-opacity: 0.7`，transition stroke/opacity 0.3s |
| `.kg-tooltip` | `position: absolute`，`pointer-events: none`，max-width 300px，opacity 0 → 1（`.visible`）|
| `.kg-interaction-hint` | 右下角提示，3s fade，hover 時重顯（opacity 0.75）|
| `.kg-view-btn.active` | background `#FFEAA7` |
| `.kg-restore-bar` | dashed border，display none，cursor pointer |

---

## 11. 已知限制

1. **EntityType 不完整支援**：後端 schema 包含 `facility` 和 `service` 兩個 EntityType，但前端 `KG_ENTITY_STYLES` 和 `KG_TYPE_LABELS` 未涵蓋（fallback 為灰色 circle，無中文標籤）。

2. **節點 label 截斷**：超過 12 字元強制截斷，長名稱（如機構全名）會顯示不完整。Tooltip hover 仍顯示完整名稱。

3. **無拖曳**：靜態 layout 不支援拖曳重排節點。節點位置由演算法決定，使用者無法手動調整。（KG 編輯模式同樣不啟用拖曳，見 `kg-editing-spec.md`）

4. **Resize 不自動重繪**：切換回圖形 view 時觸發重繪，但視窗 resize 不自動觸發。

5. **大量節點效能**：節點數超過 50 時，sector 角度過密，label 容易重疊。無自動 label 避讓機制。

6. **KG 生成預設關閉**：`config_reasoning.yaml` 中 `knowledge_graph_generation: false`，需 `enable_kg=true` 參數才生成。

---

## 12. 未來擴充方向

- 補齊 `facility`、`service` entity type 的樣式與中文標籤
- Resize Observer 自動重繪（目前切換 view 時才重繪）
- Label 避讓（SVG collision detection 或 `getBBox()`）
- KG 編輯模式（見 `docs/specs/kg-editing-spec.md`）
- KG 匯出（SVG / JSON 下載）
- 跨議題 KG 合併與比較

---

## 13. 相關檔案

| 檔案 | 說明 |
|---|---|
| `static/news-search.js` L3794-4415 | KG 前端全部邏輯 |
| `static/news-search.css` L528-670, L2474-2662, L4324-4358 | KG CSS |
| `static/news-search-prototype.html` L498-544 | KG HTML 結構 |
| `code/python/reasoning/schemas_enhanced.py` L242-334 | 後端 KG schema |
| `code/python/reasoning/orchestrator.py` | KG 序列化與 SSE 傳送 |
| `docs/specs/kg-editing-spec.md` | KG 編輯模式規格 |
| `docs/superpowers/plans/2026-03-27-kg-radial-mindmap.md` | 中心放射式重設計計畫（歷史參考）|
