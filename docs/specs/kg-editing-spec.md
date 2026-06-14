# KG Edit Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an interactive edit mode to the radial KG mind-map that lets users add/delete/rename nodes and edges, then serialize the edited graph into structured JSON and trigger a selective re-run of the composable research pipeline (phase 2+3 only, reusing existing search results) for AI re-analysis.

**Architecture:** Front-end edit UI + composable pipeline selective re-run. Edit state is maintained in a cloned copy of `currentKGData`. On confirm, the edited graph is serialized to JSON, appended to the original query as structured instructions, and triggers a selective re-run of `_phase_actor_critic_loop` + `_phase_format_result` — reusing the previous search's `formatted_context` (same articles, different analysis framing). One new backend endpoint required: "re-run from phase 2 with modified query + previous context". Critic agent's CoV naturally handles discrepancy warnings when user's KG edits conflict with evidence.

> **2026-04-13 Architecture Update**: Changed from free-conversation injection (`performFreeConversation()`) to composable pipeline selective re-run. Reason: free convo output to chatbox is impractical for report-level re-analysis; composable pipeline's phase isolation enables skipping search and directly reusing existing articles for reframed analysis. See `docs/in progress/plans/major-upgrade-plan.md` §6.8.

**Tech Stack:** D3.js v7 (already loaded), Vanilla JS, CSS3 (news-search.css), HTML (news-search-prototype.html)

---

## Feature Overview

```
[KG 視覺化] → [Edit] 按鈕 → 進入編輯模式
                                ↓
              增刪改 nodes/edges（Popover 表單）
                                ↓
              [Confirm] → 序列化 JSON → 修改 query
                                ↓
              Selective re-run: phase 2 (actor-critic) + phase 3 (format)
              （跳過 phase 1 search，reuse 同批文章）
                                ↓
              新報告輸出到 research panel
```

### Edit Mode Behaviour Rules

1. When edit mode is active, zoom/pan still works but node drag is disabled (nodes stay fixed to radial layout — prevent position drift during text editing in popover).
2. All edits are applied to an in-memory clone (`kgEditData`) — `currentKGData` is never mutated until Confirm.
3. Cancel restores to the last `currentKGData` snapshot and re-renders the original graph.
4. "Connect" tool mode: click node A (gets `kg-connect-source` CSS class + highlight), then click node B to create A→B directed edge. Clicking same node again deselects.

---

## JSON Schema for Serialized Edit

```json
{
  "schema_version": "1.0",
  "edit_timestamp": "2026-04-07T10:00:00.000Z",
  "entities": [
    {
      "entity_id": "e1",
      "name": "台積電",
      "entity_type": "organization",
      "confidence": "high",
      "description": "optional"
    }
  ],
  "relationships": [
    {
      "relationship_id": "r1",
      "source_entity_id": "e1",
      "target_entity_id": "e2",
      "relation_type": "investment",
      "confidence": "medium",
      "description": "optional"
    }
  ],
  "edit_summary": {
    "nodes_added": 1,
    "nodes_deleted": 0,
    "nodes_modified": 2,
    "edges_added": 1,
    "edges_deleted": 0,
    "edges_modified": 0
  }
}
```

---

## Query Modification Template（Selective Re-Run 用）

KG editing 的修改資訊附加到原始 query 後面，作為 analyst 在 selective re-run 時的額外指示：

```
{original_query}

【使用者知識圖譜修改】
使用者根據知識圖譜進行了以下修改，請以此為前提重新分析：
- 新增節點：{nodes_added} 個
- 刪除節點：{nodes_deleted} 個
- 修改節點：{nodes_modified} 個
- 新增關係：{edges_added} 個
- 刪除關係：{edges_deleted} 個

【修改後的知識圖譜（JSON）】
{serialized_json}

請假設使用者的修改為正確前提，據此重新分析。如果你的分析結果與使用者修改有衝突，仍以使用者的修改為前提分析，但標註 ⚠️ 說明 evidence 與使用者判斷的差異。
```

> **2026-04-13 Update**: 原本是獨立 prompt template 注入 `performFreeConversation()`。現改為附加到 query 尾部，透過 composable pipeline selective re-run（phase 2+3）讓 analyst 在同批文章上 reframe 分析。Critic CoV 天然提供 discrepancy warning，不需要 analyst prompt 自行判斷「微調/重寫/回覆」。

---

## File Map

| Action | File | Lines | Responsibility |
|--------|------|-------|----------------|
| Modify | `static/news-search-prototype.html` | 497-509 | Add Edit/Connect/Confirm/Cancel buttons to KG header |
| Modify | `static/news-search-prototype.html` | ~490 | Add popover HTML containers (node popover + edge popover) |
| Modify | `static/news-search.js` | 3828-3829 | Add `kgEditMode`, `kgEditData`, `kgConnectSourceId` state vars |
| Modify | `static/news-search.js` | 3877 (after `displayKnowledgeGraph`) | Add `setupKGEditMode()` call |
| Modify | `static/news-search.js` | after `setupKGViewToggle` (~line 4411) | Add all new KG edit functions (700-800 lines block) |
| Modify | `static/news-search.js` | `renderKGGraphView` node click handler | Route clicks through edit mode dispatcher |
| Modify | `static/news-search.css` | after line 2614 (KG CSS block) | Add edit mode styles, popover styles, connect-source highlight |

**Backend changes needed (2026-04-13 update):**
- New API endpoint: selective re-run from phase 2 with modified query + cached ResearchState
- ResearchState session-level cache: preserve `formatted_context` + `source_map` from previous DR run
- No changes to: orchestrator phases, analyst prompts, critic agent

**Not modified:**
- `renderKGListView` — list view unchanged
- `renderKGLegend` — legend unchanged
- Any non-KG JS/CSS

---

## Day 1 — Edit Mode Toggle + State Infrastructure

### Task 1: Add State Variables and Edit Mode Toggle Button (HTML + JS)

**Files:**
- Modify: `static/news-search-prototype.html:497-509`
- Modify: `static/news-search.js:3828-3829`

- [ ] **Step 1: Add edit button to KG header HTML**

Open `static/news-search-prototype.html`. Find the `<div style="display: flex; gap: 12px; align-items: center;">` block (around line 497). Add the Edit Mode button group between the view toggle and the collapse button:

```html
<!-- KG Edit Controls -->
<div class="kg-edit-controls" id="kgEditControls" style="display: none;">
    <button class="kg-edit-btn kg-edit-connect-btn" id="kgConnectBtn" title="連線模式：點 A → 點 B 建立有向邊">
        連線
    </button>
    <button class="kg-edit-btn kg-edit-add-node-btn" id="kgAddNodeBtn" title="新增節點">
        + 節點
    </button>
</div>
<div class="kg-edit-action-controls" id="kgEditActionControls" style="display: none;">
    <button class="kg-edit-btn kg-edit-confirm-btn" id="kgConfirmEditBtn">確認送出</button>
    <button class="kg-edit-btn kg-edit-cancel-btn" id="kgCancelEditBtn">取消</button>
</div>
<button class="kg-edit-toggle-btn" id="kgEditToggleBtn" title="進入編輯模式">
    編輯
</button>
```

After the edit controls, keep the existing view toggle and collapse/hide buttons exactly as they are. The full block at line 497 should now read:

```html
<div style="display: flex; gap: 12px; align-items: center;">
    <!-- Edit Controls (shown in edit mode) -->
    <div class="kg-edit-controls" id="kgEditControls" style="display: none;">
        <button class="kg-edit-btn kg-edit-connect-btn" id="kgConnectBtn" title="連線模式：點 A → 點 B 建立有向邊">
            連線
        </button>
        <button class="kg-edit-btn kg-edit-add-node-btn" id="kgAddNodeBtn" title="新增節點">
            + 節點
        </button>
    </div>
    <div class="kg-edit-action-controls" id="kgEditActionControls" style="display: none;">
        <button class="kg-edit-btn kg-edit-confirm-btn" id="kgConfirmEditBtn">確認送出</button>
        <button class="kg-edit-btn kg-edit-cancel-btn" id="kgCancelEditBtn">取消</button>
    </div>
    <button class="kg-edit-toggle-btn" id="kgEditToggleBtn" title="進入編輯模式">
        編輯
    </button>
    <!-- View Toggle (existing) -->
    <div class="kg-view-toggle" id="kgViewToggle">
        <button class="kg-view-btn active" data-view="graph">圖形</button>
        <button class="kg-view-btn" data-view="list">列表</button>
    </div>
    <button class="kg-toggle-button" id="kgToggleButton">
        <span id="kgToggleIcon">▼</span> 收起
    </button>
    <button class="kg-hide-btn" id="kgHideBtn" title="隱藏知識圖譜">
        <img src="/static/images/Icon_cancel.png" alt="" class="inline-icon"> 隱藏
    </button>
</div>
```

- [ ] **Step 2: Add state variables in JS**

Open `static/news-search.js`, find lines 3828-3829:
```javascript
        let currentKGData = null;
        let kgSimulation = null;
```

Replace with:
```javascript
        let currentKGData = null;
        let kgSimulation = null;
        let kgEditMode = false;       // true when edit mode is active
        let kgEditData = null;        // deep-cloned working copy during edit
        let kgConnectMode = false;    // true when "connect" tool is active
        let kgConnectSourceId = null; // entity_id of the first clicked node in connect mode
        let kgEditStats = { nodesAdded: 0, nodesDeleted: 0, nodesModified: 0, edgesAdded: 0, edgesDeleted: 0, edgesModified: 0 };
```

- [ ] **Step 3: Add `setupKGEditMode()` call in `displayKnowledgeGraph`**

In `static/news-search.js`, find line 3873 (the `setupKGViewToggle()` call inside `displayKnowledgeGraph`):
```javascript
            // Setup view toggle
            setupKGViewToggle();
```

Add the setup call directly after:
```javascript
            // Setup view toggle
            setupKGViewToggle();
            // Setup edit mode toggle
            setupKGEditMode();
```

- [ ] **Step 4: Add `setupKGEditMode()` function skeleton**

In `static/news-search.js`, add a new function immediately after the closing brace of `setupKGViewToggle` (after line 4411). This is the skeleton — full implementation comes in later tasks:

```javascript
        // ============================================================
        // KG Edit Mode
        // ============================================================

        function setupKGEditMode() {
            const editToggleBtn = document.getElementById('kgEditToggleBtn');
            const editControls = document.getElementById('kgEditControls');
            const editActionControls = document.getElementById('kgEditActionControls');
            const confirmBtn = document.getElementById('kgConfirmEditBtn');
            const cancelBtn = document.getElementById('kgCancelEditBtn');
            const connectBtn = document.getElementById('kgConnectBtn');
            const addNodeBtn = document.getElementById('kgAddNodeBtn');

            if (!editToggleBtn) return;

            // Remove stale listeners by cloning the buttons
            const newToggle = editToggleBtn.cloneNode(true);
            editToggleBtn.parentNode.replaceChild(newToggle, editToggleBtn);
            const newConfirm = confirmBtn.cloneNode(true);
            confirmBtn.parentNode.replaceChild(newConfirm, confirmBtn);
            const newCancel = cancelBtn.cloneNode(true);
            cancelBtn.parentNode.replaceChild(newCancel, cancelBtn);
            const newConnect = connectBtn.cloneNode(true);
            connectBtn.parentNode.replaceChild(newConnect, connectBtn);
            const newAddNode = addNodeBtn.cloneNode(true);
            addNodeBtn.parentNode.replaceChild(newAddNode, addNodeBtn);

            document.getElementById('kgEditToggleBtn').addEventListener('click', enterKGEditMode);
            document.getElementById('kgConfirmEditBtn').addEventListener('click', confirmKGEdit);
            document.getElementById('kgCancelEditBtn').addEventListener('click', cancelKGEdit);
            document.getElementById('kgConnectBtn').addEventListener('click', toggleKGConnectMode);
            document.getElementById('kgAddNodeBtn').addEventListener('click', showAddNodePopover);
        }

        function enterKGEditMode() {
            if (!currentKGData) return;
            kgEditMode = true;
            kgConnectMode = false;
            kgConnectSourceId = null;
            kgEditStats = { nodesAdded: 0, nodesDeleted: 0, nodesModified: 0, edgesAdded: 0, edgesDeleted: 0, edgesModified: 0 };

            // Deep clone current KG data as the working copy
            kgEditData = JSON.parse(JSON.stringify(currentKGData));

            // Update UI: show edit controls, hide edit toggle button
            document.getElementById('kgEditToggleBtn').style.display = 'none';
            document.getElementById('kgEditControls').style.display = 'flex';
            document.getElementById('kgEditActionControls').style.display = 'flex';

            // Add edit mode indicator to graph container
            const graphContainer = document.getElementById('kgGraphView');
            if (graphContainer) graphContainer.classList.add('kg-edit-active');

            // Re-render with edit mode enabled
            const graphView = document.getElementById('kgGraphView');
            renderKGGraphView(kgEditData, graphView);

            console.log('[KG Edit] Edit mode entered');
        }

        function cancelKGEdit() {
            kgEditMode = false;
            kgConnectMode = false;
            kgConnectSourceId = null;
            kgEditData = null;

            // Hide edit controls, restore edit toggle button
            document.getElementById('kgEditToggleBtn').style.display = '';
            document.getElementById('kgEditControls').style.display = 'none';
            document.getElementById('kgEditActionControls').style.display = 'none';

            const graphContainer = document.getElementById('kgGraphView');
            if (graphContainer) graphContainer.classList.remove('kg-edit-active');

            // Deactivate connect mode button
            document.getElementById('kgConnectBtn').classList.remove('active');

            // Re-render original data
            const graphView = document.getElementById('kgGraphView');
            renderKGGraphView(currentKGData, graphView);
            closeAllKGPopovers();
            console.log('[KG Edit] Edit mode cancelled, original graph restored');
        }
```

- [ ] **Step 5: Add basic CSS for the edit toggle button and mode indicator**

Open `static/news-search.css`. Find the `.kg-hide-btn:hover` block (around line 4293). After that block, add:

```css
        /* ============================================================
           KG Edit Mode
           ============================================================ */

        .kg-edit-toggle-btn {
            background: rgba(255,255,255,0.5);
            border: 1px solid var(--color-border);
            padding: 6px 14px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            color: var(--color-text-tertiary);
            transition: all 0.2s;
        }

        .kg-edit-toggle-btn:hover {
            background: #FFEAA7;
            color: var(--color-text);
        }

        .kg-edit-controls {
            display: flex;
            gap: 6px;
            align-items: center;
        }

        .kg-edit-action-controls {
            display: flex;
            gap: 6px;
            align-items: center;
        }

        .kg-edit-btn {
            padding: 6px 12px;
            border-radius: 6px;
            border: 1px solid var(--color-border);
            cursor: pointer;
            font-size: 13px;
            transition: all 0.2s;
        }

        .kg-edit-connect-btn {
            background: rgba(255,255,255,0.5);
            color: var(--color-text-tertiary);
        }

        .kg-edit-connect-btn.active {
            background: #FFEAA7;
            color: var(--color-text);
            border: 2px solid #2D3436;
        }

        .kg-edit-add-node-btn {
            background: rgba(255,255,255,0.5);
            color: var(--color-text-tertiary);
        }

        .kg-edit-add-node-btn:hover {
            background: #FFEAA7;
            color: var(--color-text);
        }

        .kg-edit-confirm-btn {
            background: #2D3436;
            color: #FFFFFF;
            border-color: #2D3436;
        }

        .kg-edit-confirm-btn:hover {
            background: #555;
        }

        .kg-edit-cancel-btn {
            background: rgba(255,255,255,0.5);
            color: var(--color-text-tertiary);
        }

        .kg-edit-cancel-btn:hover {
            background: #FFEAA7;
            color: var(--color-text);
        }

        /* Edit mode active state on graph container */
        .kg-graph-container.kg-edit-active {
            border: 2px solid #FDCB6E;
        }

        .kg-graph-container.kg-edit-active::before {
            content: '編輯中';
            position: absolute;
            top: 8px;
            left: 8px;
            background: #FDCB6E;
            color: #2D3436;
            font-size: 11px;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 4px;
            z-index: 10;
            pointer-events: none;
        }
```

- [ ] **Step 6: Bump cache-bust version**

Open `static/news-search-prototype.html`. Update both `?v=` parameters:
- Line 8: `href="/static/news-search.css?v=20260407a"`
- Line 658: `src="/static/news-search.js?v=20260407a"`

- [ ] **Step 7: Verify manually**

Open the app in browser. Run a Deep Research query to get a KG. Confirm:
- "編輯" button appears in KG header
- Clicking "編輯" makes the edit controls appear, "編輯" button hides, graph border turns gold, "編輯中" label shows top-left
- Clicking "取消" restores original state
- No console errors

- [ ] **Step 8: Commit**

```bash
git add static/news-search-prototype.html static/news-search.js static/news-search.css
git commit -m "feat(kg-edit): edit mode toggle infrastructure + state variables + CSS"
```

---

## Day 2 — Node Edit: Click Popover + Delete + Add Node

### Task 2: Node Popover HTML + CSS

**Files:**
- Modify: `static/news-search-prototype.html` — add two popover containers inside `kgGraphView`
- Modify: `static/news-search.css` — add popover styles

- [ ] **Step 1: Add popover HTML inside kgGraphView**

Open `static/news-search-prototype.html`. Find the `kgGraphView` div:
```html
<div class="kg-graph-container" id="kgGraphView">
    <div class="kg-tooltip" id="kgTooltip"></div>
</div>
```

Replace with:
```html
<div class="kg-graph-container" id="kgGraphView">
    <div class="kg-tooltip" id="kgTooltip"></div>
    <!-- Node edit popover (shown on node click in edit mode) -->
    <div class="kg-edit-popover" id="kgNodePopover" style="display:none;">
        <div class="kg-popover-header">
            <span class="kg-popover-title">編輯節點</span>
            <button class="kg-popover-close" id="kgNodePopoverClose">&times;</button>
        </div>
        <div class="kg-popover-body">
            <label class="kg-popover-label">名稱</label>
            <input type="text" class="kg-popover-input" id="kgNodeNameInput" placeholder="節點名稱" maxlength="80">
            <label class="kg-popover-label">類型</label>
            <select class="kg-popover-select" id="kgNodeTypeSelect">
                <option value="person">人物</option>
                <option value="organization">組織</option>
                <option value="event">事件</option>
                <option value="location">地點</option>
                <option value="metric">指標</option>
                <option value="technology">技術</option>
                <option value="concept">概念</option>
                <option value="product">產品</option>
            </select>
            <label class="kg-popover-label">描述（選填）</label>
            <input type="text" class="kg-popover-input" id="kgNodeDescInput" placeholder="簡短描述" maxlength="200">
        </div>
        <div class="kg-popover-footer">
            <button class="kg-popover-btn kg-popover-save-btn" id="kgNodeSaveBtn">儲存</button>
            <button class="kg-popover-btn kg-popover-delete-btn" id="kgNodeDeleteBtn">刪除節點</button>
        </div>
        <input type="hidden" id="kgNodeEditingId">
    </div>
    <!-- Edge edit popover (shown on edge click in edit mode) -->
    <div class="kg-edit-popover" id="kgEdgePopover" style="display:none;">
        <div class="kg-popover-header">
            <span class="kg-popover-title">編輯關係</span>
            <button class="kg-popover-close" id="kgEdgePopoverClose">&times;</button>
        </div>
        <div class="kg-popover-body">
            <div class="kg-popover-edge-desc" id="kgEdgeDesc"></div>
            <label class="kg-popover-label">關係類型（選填）</label>
            <input type="text" class="kg-popover-input" id="kgEdgeLabelInput" placeholder="例：投資、隸屬於" maxlength="60">
            <label class="kg-popover-label">描述（選填）</label>
            <input type="text" class="kg-popover-input" id="kgEdgeDescInput" placeholder="簡短描述" maxlength="200">
        </div>
        <div class="kg-popover-footer">
            <button class="kg-popover-btn kg-popover-save-btn" id="kgEdgeSaveBtn">儲存</button>
            <button class="kg-popover-btn kg-popover-delete-btn" id="kgEdgeDeleteBtn">刪除關係</button>
        </div>
        <input type="hidden" id="kgEdgeEditingId">
    </div>
</div>
```

- [ ] **Step 2: Add popover CSS**

Open `static/news-search.css`. At the end of the KG edit mode CSS block added in Task 1, add:

```css
        /* KG Edit Popover */
        .kg-edit-popover {
            position: absolute;
            background: var(--color-bg);
            border: 1px solid var(--color-border);
            border-radius: 10px;
            box-shadow: var(--shadow-md);
            width: 240px;
            z-index: 100;
            padding: 0;
            font-size: 13px;
        }

        .kg-popover-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 14px 8px;
            border-bottom: 1px solid var(--color-border-light);
        }

        .kg-popover-title {
            font-weight: 600;
            color: var(--color-text);
            font-size: 13px;
        }

        .kg-popover-close {
            background: none;
            border: none;
            cursor: pointer;
            font-size: 16px;
            color: var(--color-text-tertiary);
            line-height: 1;
            padding: 0;
        }

        .kg-popover-close:hover {
            color: var(--color-text);
        }

        .kg-popover-body {
            padding: 10px 14px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .kg-popover-label {
            font-size: 11px;
            color: var(--color-text-tertiary);
            margin-bottom: 2px;
            display: block;
        }

        .kg-popover-input,
        .kg-popover-select {
            width: 100%;
            padding: 6px 8px;
            border: 1px solid var(--color-border);
            border-radius: 6px;
            font-size: 13px;
            color: var(--color-text);
            background: var(--color-bg);
            box-sizing: border-box;
        }

        .kg-popover-input:focus,
        .kg-popover-select:focus {
            outline: none;
            border-color: #FDCB6E;
        }

        .kg-popover-footer {
            display: flex;
            gap: 8px;
            padding: 8px 14px 12px;
            border-top: 1px solid var(--color-border-light);
        }

        .kg-popover-btn {
            padding: 6px 12px;
            border-radius: 6px;
            border: none;
            cursor: pointer;
            font-size: 12px;
            font-weight: 500;
            transition: all 0.15s;
        }

        .kg-popover-save-btn {
            background: #2D3436;
            color: #FFFFFF;
            flex: 1;
        }

        .kg-popover-save-btn:hover {
            background: #555;
        }

        .kg-popover-delete-btn {
            background: rgba(220, 38, 38, 0.08);
            color: var(--color-danger);
            border: 1px solid rgba(220, 38, 38, 0.2);
        }

        .kg-popover-delete-btn:hover {
            background: rgba(220, 38, 38, 0.15);
        }

        .kg-popover-edge-desc {
            font-size: 12px;
            color: var(--color-text-secondary);
            padding: 4px 0 2px;
            border-bottom: 1px solid var(--color-border-light);
            margin-bottom: 4px;
        }

        /* Connect mode: source node highlight */
        .kg-node.kg-connect-source circle,
        .kg-node.kg-connect-source rect {
            stroke: #FDCB6E !important;
            stroke-width: 4px !important;
            filter: brightness(1.1);
        }
```

- [ ] **Step 3: Bump version to `20260407b`**

In `static/news-search-prototype.html`, update both `?v=` parameters to `20260407b`.

- [ ] **Step 4: Commit**

```bash
git add static/news-search-prototype.html static/news-search.css
git commit -m "feat(kg-edit): add node/edge popover HTML + popover CSS"
```

---

### Task 3: Node Click Dispatch + Node Edit Logic

**Files:**
- Modify: `static/news-search.js` — wire node clicks to edit popover, implement save/delete

- [ ] **Step 1: Modify node click handler in `renderKGGraphView`**

Open `static/news-search.js`. Inside `renderKGGraphView`, find the existing node click handler. It currently does tooltip/highlight. Search for a pattern like:
```javascript
nodeGroup.on('click', function(event, d) {
```

The existing handler does highlight/deselect logic. We need to route to edit mode when active. Find the start of the node click handler and add an early-return branch at the very beginning:

```javascript
nodeGroup.on('click', function(event, d) {
    event.stopPropagation();

    // Edit mode: route to popover or connect logic
    if (kgEditMode) {
        if (kgConnectMode) {
            handleKGConnectClick(d.entity_id, d.entity);
        } else {
            showNodeEditPopover(event, d.entity_id, d.entity, d.x, d.y, d.r);
        }
        return; // do not run normal highlight logic in edit mode
    }

    // ... existing highlight/tooltip logic continues unchanged below ...
```

- [ ] **Step 2: Add `showNodeEditPopover` function**

In `static/news-search.js`, inside the KG Edit Mode section (after `cancelKGEdit`), add:

```javascript
        function closeAllKGPopovers() {
            const nodePopover = document.getElementById('kgNodePopover');
            const edgePopover = document.getElementById('kgEdgePopover');
            if (nodePopover) nodePopover.style.display = 'none';
            if (edgePopover) edgePopover.style.display = 'none';
        }

        function positionPopover(popoverEl, nodeX, nodeY, nodeR) {
            // Convert SVG coords to container-relative coords
            // nodeX/nodeY are SVG coordinates; we need to account for zoom/pan transform
            const graphContainer = document.getElementById('kgGraphView');
            const containerRect = graphContainer.getBoundingClientRect();
            const svgEl = graphContainer.querySelector('svg');
            if (!svgEl) return;

            // Get current zoom transform from D3
            const transform = d3.zoomTransform(svgEl);
            const screenX = transform.applyX(nodeX);
            const screenY = transform.applyY(nodeY);

            let left = screenX + nodeR + 8;
            let top = screenY - 40;

            // Clamp within container bounds
            const popoverWidth = 240;
            const popoverHeight = 220; // approximate
            if (left + popoverWidth > graphContainer.clientWidth) {
                left = screenX - nodeR - popoverWidth - 8;
            }
            if (top + popoverHeight > graphContainer.clientHeight) {
                top = graphContainer.clientHeight - popoverHeight - 8;
            }
            if (top < 0) top = 8;
            if (left < 0) left = 8;

            popoverEl.style.left = left + 'px';
            popoverEl.style.top = top + 'px';
        }

        function showNodeEditPopover(event, entityId, entity, nodeX, nodeY, nodeR) {
            closeAllKGPopovers();

            const popover = document.getElementById('kgNodePopover');
            const nameInput = document.getElementById('kgNodeNameInput');
            const typeSelect = document.getElementById('kgNodeTypeSelect');
            const descInput = document.getElementById('kgNodeDescInput');
            const editingId = document.getElementById('kgNodeEditingId');

            // Populate form with current values
            nameInput.value = entity.name || '';
            typeSelect.value = entity.entity_type || 'concept';
            descInput.value = entity.description || '';
            editingId.value = entityId;

            positionPopover(popover, nodeX, nodeY, nodeR);
            popover.style.display = 'block';

            // Wire save button (clone to remove stale listeners)
            const saveBtn = document.getElementById('kgNodeSaveBtn');
            const newSave = saveBtn.cloneNode(true);
            saveBtn.parentNode.replaceChild(newSave, saveBtn);
            document.getElementById('kgNodeSaveBtn').addEventListener('click', saveNodeEdit);

            const deleteBtn = document.getElementById('kgNodeDeleteBtn');
            const newDelete = deleteBtn.cloneNode(true);
            deleteBtn.parentNode.replaceChild(newDelete, deleteBtn);
            document.getElementById('kgNodeDeleteBtn').addEventListener('click', deleteCurrentNode);

            const closeBtn = document.getElementById('kgNodePopoverClose');
            const newClose = closeBtn.cloneNode(true);
            closeBtn.parentNode.replaceChild(newClose, closeBtn);
            document.getElementById('kgNodePopoverClose').addEventListener('click', closeAllKGPopovers);

            nameInput.focus();
            nameInput.select();
        }

        function saveNodeEdit() {
            const entityId = document.getElementById('kgNodeEditingId').value;
            const newName = document.getElementById('kgNodeNameInput').value.trim();
            const newType = document.getElementById('kgNodeTypeSelect').value;
            const newDesc = document.getElementById('kgNodeDescInput').value.trim();

            if (!newName) {
                document.getElementById('kgNodeNameInput').focus();
                return;
            }

            // Find and update entity in kgEditData
            const entity = kgEditData.entities.find(e => e.entity_id === entityId);
            if (!entity) {
                console.error('[KG Edit] Entity not found for save:', entityId);
                return;
            }

            const wasModified = entity.name !== newName || entity.entity_type !== newType || (entity.description || '') !== newDesc;
            if (wasModified) {
                entity.name = newName;
                entity.entity_type = newType;
                entity.description = newDesc || undefined;
                kgEditStats.nodesModified++;
                console.log('[KG Edit] Node modified:', entityId, newName);
            }

            closeAllKGPopovers();

            // Re-render with updated data
            const graphView = document.getElementById('kgGraphView');
            renderKGGraphView(kgEditData, graphView);
        }

        function deleteCurrentNode() {
            const entityId = document.getElementById('kgNodeEditingId').value;

            // Remove entity
            const prevCount = kgEditData.entities.length;
            kgEditData.entities = kgEditData.entities.filter(e => e.entity_id !== entityId);

            if (kgEditData.entities.length < prevCount) {
                // Also remove all relationships involving this entity
                const prevRels = kgEditData.relationships.length;
                kgEditData.relationships = kgEditData.relationships.filter(
                    r => r.source_entity_id !== entityId && r.target_entity_id !== entityId
                );
                const removedRels = prevRels - kgEditData.relationships.length;
                kgEditStats.nodesDeleted++;
                kgEditStats.edgesDeleted += removedRels;
                console.log('[KG Edit] Node deleted:', entityId, '+ removed', removedRels, 'relationships');
            }

            closeAllKGPopovers();

            const graphView = document.getElementById('kgGraphView');
            renderKGGraphView(kgEditData, graphView);
        }
```

- [ ] **Step 3: Add "Add Node" popover logic**

In `static/news-search.js`, after `deleteCurrentNode`, add:

```javascript
        function showAddNodePopover() {
            closeAllKGPopovers();

            const popover = document.getElementById('kgNodePopover');
            const nameInput = document.getElementById('kgNodeNameInput');
            const typeSelect = document.getElementById('kgNodeTypeSelect');
            const descInput = document.getElementById('kgNodeDescInput');
            const editingId = document.getElementById('kgNodeEditingId');

            // Clear form for new node
            nameInput.value = '';
            typeSelect.value = 'concept';
            descInput.value = '';
            editingId.value = '__new__'; // sentinel value

            // Position near top-left of graph container
            const graphContainer = document.getElementById('kgGraphView');
            popover.style.left = '20px';
            popover.style.top = '40px';
            popover.style.display = 'block';

            // Swap Save button handler to createNewNode
            const saveBtn = document.getElementById('kgNodeSaveBtn');
            const newSave = saveBtn.cloneNode(true);
            saveBtn.parentNode.replaceChild(newSave, saveBtn);
            document.getElementById('kgNodeSaveBtn').addEventListener('click', createNewNode);

            // Hide delete button (can't delete a node that doesn't exist yet)
            document.getElementById('kgNodeDeleteBtn').style.display = 'none';

            const closeBtn = document.getElementById('kgNodePopoverClose');
            const newClose = closeBtn.cloneNode(true);
            closeBtn.parentNode.replaceChild(newClose, closeBtn);
            document.getElementById('kgNodePopoverClose').addEventListener('click', () => {
                document.getElementById('kgNodeDeleteBtn').style.display = '';
                closeAllKGPopovers();
            });

            nameInput.focus();
        }

        function createNewNode() {
            const newName = document.getElementById('kgNodeNameInput').value.trim();
            const newType = document.getElementById('kgNodeTypeSelect').value;
            const newDesc = document.getElementById('kgNodeDescInput').value.trim();

            if (!newName) {
                document.getElementById('kgNodeNameInput').focus();
                return;
            }

            // Generate a unique entity_id
            const newId = 'edit_' + Date.now();
            const newEntity = {
                entity_id: newId,
                name: newName,
                entity_type: newType,
                confidence: 'medium',
                description: newDesc || undefined
            };

            kgEditData.entities.push(newEntity);
            kgEditStats.nodesAdded++;

            // Restore delete button visibility for future node edits
            document.getElementById('kgNodeDeleteBtn').style.display = '';

            closeAllKGPopovers();

            const graphView = document.getElementById('kgGraphView');
            renderKGGraphView(kgEditData, graphView);

            console.log('[KG Edit] New node created:', newId, newName);
        }
```

- [ ] **Step 4: Bump version to `20260407c`**

In `static/news-search-prototype.html`, update both `?v=` parameters to `20260407c`.

- [ ] **Step 5: Verify manually**

Run a Deep Research query, enter edit mode. Verify:
- Clicking a node in edit mode opens the node popover with pre-filled values
- Saving changes updates the node label and re-renders
- Deleting a node removes it and all its edges from the graph
- "+ 節點" button opens popover with empty form, saving adds the new node to the graph
- No console errors

- [ ] **Step 6: Commit**

```bash
git add static/news-search.js static/news-search-prototype.html
git commit -m "feat(kg-edit): node click → popover, save/delete/add node"
```

---

## Day 3 — Edge Edit: Delete + Add Edge (Connect Mode)

### Task 4: Edge Click Handler + Edge Delete + Edge Label Edit

**Files:**
- Modify: `static/news-search.js` — edge click dispatch + edge popover logic

- [ ] **Step 1: Modify edge click handler in `renderKGGraphView`**

In `static/news-search.js`, inside `renderKGGraphView`, find the edge (link) click handler. Search for:
```javascript
linkGroup.on('click', function(event, d) {
```
or the link `g` element click. The existing handler shows tooltip on hover. Edges currently have no click handler — add one on the link `g` group. Find where link groups are created (search for `.kg-link`) and add a click handler:

After the existing edge drawing code that creates link `g` elements (they are drawn as `<path class="kg-link">`), find the D3 selection that creates them and add:

```javascript
            // Edge click — edit mode only
            linkGroup.on('click', function(event, d) {
                event.stopPropagation();
                if (!kgEditMode) return;
                showEdgeEditPopover(event, d, this);
            });
```

Where `linkGroup` is the D3 selection of individual edge `<g>` or `<path>` elements. If edges are drawn as plain `<path>` with class `kg-link`, the selection is:
```javascript
            linkPaths.on('click', function(event, d) {
                event.stopPropagation();
                if (!kgEditMode) return;
                showEdgeEditPopover(event, d, this);
            });
```

- [ ] **Step 2: Add `showEdgeEditPopover` and edge save/delete functions**

In `static/news-search.js`, after `createNewNode`, add:

```javascript
        function showEdgeEditPopover(event, linkData, pathEl) {
            closeAllKGPopovers();

            const popover = document.getElementById('kgEdgePopover');
            const edgeDesc = document.getElementById('kgEdgeDesc');
            const labelInput = document.getElementById('kgEdgeLabelInput');
            const descInput = document.getElementById('kgEdgeDescInput');
            const editingId = document.getElementById('kgEdgeEditingId');

            // Build human-readable description
            const entityMap = {};
            kgEditData.entities.forEach(e => { entityMap[e.entity_id] = e.name; });
            const sourceName = entityMap[linkData.source] || linkData.source;
            const targetName = entityMap[linkData.target] || linkData.target;
            edgeDesc.textContent = `${sourceName}  →  ${targetName}`;

            // Find the relationship in kgEditData
            const rel = kgEditData.relationships.find(
                r => r.source_entity_id === linkData.source && r.target_entity_id === linkData.target
            );
            if (!rel) {
                console.error('[KG Edit] Relationship not found for edge click');
                return;
            }

            labelInput.value = rel.relation_type || '';
            descInput.value = rel.description || '';
            editingId.value = rel.relationship_id || `${linkData.source}__${linkData.target}`;

            // Position near clicked edge midpoint — use mouse position
            const graphContainer = document.getElementById('kgGraphView');
            const containerRect = graphContainer.getBoundingClientRect();
            let left = event.clientX - containerRect.left + 10;
            let top = event.clientY - containerRect.top - 20;
            const popoverWidth = 240;
            const popoverHeight = 200;
            if (left + popoverWidth > graphContainer.clientWidth) left = graphContainer.clientWidth - popoverWidth - 8;
            if (top + popoverHeight > graphContainer.clientHeight) top = graphContainer.clientHeight - popoverHeight - 8;
            if (top < 0) top = 8;
            if (left < 0) left = 8;
            popover.style.left = left + 'px';
            popover.style.top = top + 'px';
            popover.style.display = 'block';

            // Wire save/delete buttons
            const saveBtn = document.getElementById('kgEdgeSaveBtn');
            const newSave = saveBtn.cloneNode(true);
            saveBtn.parentNode.replaceChild(newSave, saveBtn);
            document.getElementById('kgEdgeSaveBtn').addEventListener('click', saveEdgeEdit);

            const deleteBtn = document.getElementById('kgEdgeDeleteBtn');
            const newDelete = deleteBtn.cloneNode(true);
            deleteBtn.parentNode.replaceChild(newDelete, deleteBtn);
            document.getElementById('kgEdgeDeleteBtn').addEventListener('click', deleteCurrentEdge);

            const closeBtn = document.getElementById('kgEdgePopoverClose');
            const newClose = closeBtn.cloneNode(true);
            closeBtn.parentNode.replaceChild(newClose, closeBtn);
            document.getElementById('kgEdgePopoverClose').addEventListener('click', closeAllKGPopovers);
        }

        function saveEdgeEdit() {
            const editingId = document.getElementById('kgEdgeEditingId').value;
            const newLabel = document.getElementById('kgEdgeLabelInput').value.trim();
            const newDesc = document.getElementById('kgEdgeDescInput').value.trim();

            // Find rel by relationship_id OR by source__target composite key
            let rel = kgEditData.relationships.find(r => r.relationship_id === editingId);
            if (!rel) {
                const [src, tgt] = editingId.split('__');
                rel = kgEditData.relationships.find(
                    r => r.source_entity_id === src && r.target_entity_id === tgt
                );
            }
            if (!rel) {
                console.error('[KG Edit] Relationship not found for save:', editingId);
                return;
            }

            const wasModified = (rel.relation_type || '') !== newLabel || (rel.description || '') !== newDesc;
            if (wasModified) {
                rel.relation_type = newLabel || rel.relation_type;
                rel.description = newDesc || undefined;
                kgEditStats.edgesModified++;
                console.log('[KG Edit] Edge modified:', editingId);
            }

            closeAllKGPopovers();
            const graphView = document.getElementById('kgGraphView');
            renderKGGraphView(kgEditData, graphView);
        }

        function deleteCurrentEdge() {
            const editingId = document.getElementById('kgEdgeEditingId').value;

            const prevCount = kgEditData.relationships.length;
            kgEditData.relationships = kgEditData.relationships.filter(r => {
                const compositeKey = `${r.source_entity_id}__${r.target_entity_id}`;
                return r.relationship_id !== editingId && compositeKey !== editingId;
            });

            if (kgEditData.relationships.length < prevCount) {
                kgEditStats.edgesDeleted++;
                console.log('[KG Edit] Edge deleted:', editingId);
            }

            closeAllKGPopovers();
            const graphView = document.getElementById('kgGraphView');
            renderKGGraphView(kgEditData, graphView);
        }
```

- [ ] **Step 3: Bump version to `20260407d`**

In `static/news-search-prototype.html`, update both `?v=` parameters to `20260407d`.

- [ ] **Step 4: Verify manually**

In edit mode:
- Click an edge → edge popover shows with source/target label and current relation type
- Saving updates the edge label in the re-rendered graph
- Deleting removes the edge

- [ ] **Step 5: Commit**

```bash
git add static/news-search.js static/news-search-prototype.html
git commit -m "feat(kg-edit): edge click → popover, save/delete edge"
```

---

### Task 5: Connect Mode — Add New Directed Edge

**Files:**
- Modify: `static/news-search.js` — connect mode state machine

- [ ] **Step 1: Add `toggleKGConnectMode` and `handleKGConnectClick`**

In `static/news-search.js`, after `deleteCurrentEdge`, add:

```javascript
        function toggleKGConnectMode() {
            kgConnectMode = !kgConnectMode;
            kgConnectSourceId = null;

            const connectBtn = document.getElementById('kgConnectBtn');
            if (kgConnectMode) {
                connectBtn.classList.add('active');
                connectBtn.textContent = '連線中（點節點 A）';
                console.log('[KG Edit] Connect mode ON');
            } else {
                connectBtn.classList.remove('active');
                connectBtn.textContent = '連線';
                // Remove connect-source highlight from any node
                d3.selectAll('.kg-node').classed('kg-connect-source', false);
                console.log('[KG Edit] Connect mode OFF');
            }
            closeAllKGPopovers();
        }

        function handleKGConnectClick(entityId, entity) {
            if (!kgConnectSourceId) {
                // First click: select source node
                kgConnectSourceId = entityId;

                // Highlight source node
                d3.selectAll('.kg-node').classed('kg-connect-source', function(d) {
                    return d && d.entity_id === entityId;
                });

                const connectBtn = document.getElementById('kgConnectBtn');
                connectBtn.textContent = `連線中（${entity.name} → 點目標）`;
                console.log('[KG Edit] Connect source selected:', entityId, entity.name);
            } else {
                // Second click: create edge from source to this node
                const targetId = entityId;

                if (targetId === kgConnectSourceId) {
                    // Clicked same node — deselect
                    kgConnectSourceId = null;
                    d3.selectAll('.kg-node').classed('kg-connect-source', false);
                    document.getElementById('kgConnectBtn').textContent = '連線中（點節點 A）';
                    return;
                }

                // Check if this edge already exists
                const exists = kgEditData.relationships.some(
                    r => r.source_entity_id === kgConnectSourceId && r.target_entity_id === targetId
                );

                if (exists) {
                    console.warn('[KG Edit] Edge already exists:', kgConnectSourceId, '->', targetId);
                    // Still reset connect state
                } else {
                    // Create new relationship
                    const newRel = {
                        relationship_id: 'edit_rel_' + Date.now(),
                        source_entity_id: kgConnectSourceId,
                        target_entity_id: targetId,
                        relation_type: '',       // user can edit label later
                        confidence: 'medium',
                        description: undefined
                    };
                    kgEditData.relationships.push(newRel);
                    kgEditStats.edgesAdded++;
                    console.log('[KG Edit] New edge created:', kgConnectSourceId, '->', targetId);
                }

                // Reset connect state
                kgConnectSourceId = null;
                d3.selectAll('.kg-node').classed('kg-connect-source', false);
                document.getElementById('kgConnectBtn').textContent = '連線中（點節點 A）';

                // Re-render
                const graphView = document.getElementById('kgGraphView');
                renderKGGraphView(kgEditData, graphView);
            }
        }
```

- [ ] **Step 2: Bump version to `20260407e`**

In `static/news-search-prototype.html`, update both `?v=` parameters to `20260407e`.

- [ ] **Step 3: Verify manually**

In edit mode:
- Click "連線" → button turns active gold, prompt changes
- Click node A → node glows gold, prompt shows "A → 點目標"
- Click node B → new edge A→B appears in re-rendered graph
- Clicking the same node A again deselects it
- Attempting to create a duplicate edge logs a warning and resets without adding

- [ ] **Step 4: Commit**

```bash
git add static/news-search.js static/news-search-prototype.html
git commit -m "feat(kg-edit): connect mode — click node A then B to create directed edge"
```

---

## Day 4 — Serialization + Prompt Injection + Smoke Test

### Task 6: Serialize Edited KG and Build Prompt Template

**Files:**
- Modify: `static/news-search.js` — add `serializeKGEdit` and `buildKGEditPrompt`

- [ ] **Step 1: Add `serializeKGEdit` function**

In `static/news-search.js`, after `handleKGConnectClick`, add:

```javascript
        function serializeKGEdit() {
            if (!kgEditData) return null;

            const payload = {
                schema_version: '1.0',
                edit_timestamp: new Date().toISOString(),
                entities: kgEditData.entities.map(e => ({
                    entity_id: e.entity_id,
                    name: e.name,
                    entity_type: e.entity_type,
                    confidence: e.confidence || 'medium',
                    description: e.description || undefined
                })),
                relationships: kgEditData.relationships.map(r => ({
                    relationship_id: r.relationship_id,
                    source_entity_id: r.source_entity_id,
                    target_entity_id: r.target_entity_id,
                    relation_type: r.relation_type || '',
                    confidence: r.confidence || 'medium',
                    description: r.description || undefined
                })),
                edit_summary: {
                    nodes_added: kgEditStats.nodesAdded,
                    nodes_deleted: kgEditStats.nodesDeleted,
                    nodes_modified: kgEditStats.nodesModified,
                    edges_added: kgEditStats.edgesAdded,
                    edges_deleted: kgEditStats.edgesDeleted,
                    edges_modified: kgEditStats.edgesModified
                }
            };

            // Strip undefined fields
            payload.entities = payload.entities.map(e => {
                if (!e.description) delete e.description;
                return e;
            });
            payload.relationships = payload.relationships.map(r => {
                if (!r.description) delete r.description;
                return r;
            });

            return payload;
        }

        function buildKGEditPrompt(serialized) {
            const s = serialized.edit_summary;
            const totalChanges = s.nodes_added + s.nodes_deleted + s.nodes_modified + s.edges_added + s.edges_deleted + s.edges_modified;

            const changeSummaryLines = [];
            if (s.nodes_added > 0)   changeSummaryLines.push(`- 新增節點：${s.nodes_added} 個`);
            if (s.nodes_deleted > 0) changeSummaryLines.push(`- 刪除節點：${s.nodes_deleted} 個`);
            if (s.nodes_modified > 0) changeSummaryLines.push(`- 修改節點：${s.nodes_modified} 個`);
            if (s.edges_added > 0)   changeSummaryLines.push(`- 新增關係：${s.edges_added} 個`);
            if (s.edges_deleted > 0) changeSummaryLines.push(`- 刪除關係：${s.edges_deleted} 個`);
            if (s.edges_modified > 0) changeSummaryLines.push(`- 修改關係：${s.edges_modified} 個`);

            const changeSummary = changeSummaryLines.length > 0
                ? changeSummaryLines.join('\n')
                : '- 無明確修改（圖譜已確認）';

            const jsonStr = JSON.stringify(serialized, null, 2);

            return `以下是使用者根據知識圖譜提出的修改意見，請根據這些修改重新分析報告：

【知識圖譜修改（共 ${totalChanges} 項）】
${changeSummary}

【修改後的知識圖譜（JSON）】
\`\`\`json
${jsonStr}
\`\`\`

請根據以上修改後的知識圖譜，判斷需要：
1. 微調現有報告（gap resolution — 補充缺漏資訊）
2. 重寫報告（若結構變動幅度大）
3. 僅回覆說明（若修改屬輕微補充）

請給出完整的更新分析。`;
        }
```

- [ ] **Step 2: Verify serialization output in console**

Open browser DevTools. After entering edit mode and making a change, run in console:
```javascript
const s = serializeKGEdit();
console.log(JSON.stringify(s, null, 2));
console.log(buildKGEditPrompt(s));
```
Confirm: JSON output matches the schema in this spec. Prompt text is readable and complete.

- [ ] **Step 3: Commit**

```bash
git add static/news-search.js
git commit -m "feat(kg-edit): serializeKGEdit + buildKGEditPrompt"
```

---

### Task 7: Confirm Flow — Inject into Free Conversation

**Files:**
- Modify: `static/news-search.js` — implement `confirmKGEdit` function

- [ ] **Step 1: Add `confirmKGEdit` function**

In `static/news-search.js`, replace the empty `confirmKGEdit` stub (currently not defined — add it after `buildKGEditPrompt`):

```javascript
        async function confirmKGEdit() {
            if (!kgEditData) {
                console.error('[KG Edit] No edit data to confirm');
                return;
            }

            // Serialize and build prompt
            const serialized = serializeKGEdit();
            if (!serialized) {
                console.error('[KG Edit] Serialization failed');
                return;
            }

            const prompt = buildKGEditPrompt(serialized);
            console.log('[KG Edit] Confirm: injecting prompt into conversation, length:', prompt.length);

            // Exit edit mode (keep kgEditData as the new currentKGData)
            kgEditMode = false;
            kgConnectMode = false;
            kgConnectSourceId = null;

            // Update currentKGData to reflect confirmed edits
            currentKGData = JSON.parse(JSON.stringify(kgEditData));
            kgEditData = null;

            // Update UI: exit edit mode appearance
            document.getElementById('kgEditToggleBtn').style.display = '';
            document.getElementById('kgEditControls').style.display = 'none';
            document.getElementById('kgEditActionControls').style.display = 'none';

            const graphContainer = document.getElementById('kgGraphView');
            if (graphContainer) graphContainer.classList.remove('kg-edit-active');
            document.getElementById('kgConnectBtn').classList.remove('active');

            closeAllKGPopovers();

            // Re-render updated graph (now with confirmed edits as the live graph)
            const graphView = document.getElementById('kgGraphView');
            renderKGGraphView(currentKGData, graphView);

            // Switch to chat mode: set currentMode and show chatContainer
            // (No dedicated enterChatMode function exists — mode is set by variable assignment)
            currentMode = 'chat';
            const chatContainer = document.getElementById('chatContainer');
            if (chatContainer) chatContainer.style.display = 'flex';

            // Hide results section if shown (prevent overlap with chat)
            const resultsSection = document.getElementById('resultsSection');
            if (resultsSection) resultsSection.style.display = 'none';

            // Move search input to chat area bottom (same pattern as deep_research → chat transition)
            const searchContainerEl = document.getElementById('searchContainer');
            const chatInputArea = document.getElementById('chatInputArea');
            if (searchContainerEl && chatInputArea) {
                chatInputArea.appendChild(searchContainerEl);
            }

            // Directly call performFreeConversation with the full prompt
            await performFreeConversation(prompt);
        }
```

- [ ] **Step 2: Find `chatInputArea` element name**

Before implementing Step 1, verify the ID used for the chat input area in the HTML:

```bash
grep -n "chatInputArea\|chat-input-area\|chatBottom" /c/users/user/nlweb/static/news-search-prototype.html | head -10
```

If the ID is different, update the `searchContainerEl` / `chatInputArea` lines in `confirmKGEdit` accordingly.

- [ ] **Step 3: Bump version to `20260407f`**

In `static/news-search-prototype.html`, update both `?v=` parameters to `20260407f`.

- [ ] **Step 4: Verify E2E flow manually**

Run a Deep Research query. Enter KG edit mode. Make at least one change (rename a node, delete an edge). Click "確認送出". Verify:
- Edit mode UI exits cleanly (edit controls hidden, "編輯" button restored, graph border removed)
- The graph re-renders with the confirmed changes
- A chat message appears in the chat area with the full prompt text visible
- The AI responds (it may take 10-30 seconds)
- No console errors

- [ ] **Step 5: Run smoke test**

```bash
cd /c/users/user/nlweb/code/python && python tools/smoke_test.py
```

Expected: All tests PASS. If any FAIL, fix before committing.

- [ ] **Step 6: Commit**

```bash
git add static/news-search.js static/news-search-prototype.html
git commit -m "feat(kg-edit): confirm → serialize + prompt injection → performFreeConversation"
```

---

### Task 8: Edge Cases, Polish, and Final Verification

**Files:**
- Modify: `static/news-search.js` — edge cases
- Modify: `static/news-search.css` — final polish

- [ ] **Step 1: Handle popover close on SVG background click in edit mode**

In `static/news-search.js`, inside `renderKGGraphView`, find the SVG background click handler:
```javascript
            svg.on('click', function(event) {
                if (event.target === this || event.target.tagName === 'svg') {
                    deselectAll();
                }
            });
```

Update to also close popovers and cancel connect mode source selection:
```javascript
            svg.on('click', function(event) {
                if (event.target === this || event.target.tagName === 'svg') {
                    if (kgEditMode) {
                        closeAllKGPopovers();
                        // If in connect mode and source is selected, just deselect source (don't exit connect mode)
                        if (kgConnectSourceId) {
                            kgConnectSourceId = null;
                            d3.selectAll('.kg-node').classed('kg-connect-source', false);
                            document.getElementById('kgConnectBtn').textContent = '連線中（點節點 A）';
                        }
                    } else {
                        deselectAll();
                    }
                }
            });
```

- [ ] **Step 2: Guard `confirmKGEdit` when no changes were made**

In `static/news-search.js`, in `confirmKGEdit`, after serialization and before the prompt injection, add a guard:

```javascript
            const totalChanges = serialized.edit_summary.nodes_added +
                serialized.edit_summary.nodes_deleted +
                serialized.edit_summary.nodes_modified +
                serialized.edit_summary.edges_added +
                serialized.edit_summary.edges_deleted +
                serialized.edit_summary.edges_modified;

            if (totalChanges === 0) {
                // No changes — still allow confirm (user may want AI to re-analyze as-is)
                console.log('[KG Edit] Confirm with 0 changes — sending current KG for re-analysis');
            }
```

This ensures confirm always works even with zero edits (user may want AI to just re-analyze the current graph).

- [ ] **Step 3: Disable "Edit" button when KG has no entities**

In `static/news-search.js`, inside `displayKnowledgeGraph`, after the call to `setupKGEditMode()`, add:

```javascript
            // Disable edit button if KG is empty
            const editBtn = document.getElementById('kgEditToggleBtn');
            if (editBtn) {
                const hasEntities = kg && kg.entities && kg.entities.length > 0;
                editBtn.disabled = !hasEntities;
                editBtn.title = hasEntities ? '進入編輯模式' : '無節點可編輯';
            }
```

- [ ] **Step 4: Add disabled state CSS for edit toggle button**

In `static/news-search.css`, find `.kg-edit-toggle-btn:hover` and add after:
```css
        .kg-edit-toggle-btn:disabled {
            opacity: 0.4;
            cursor: not-allowed;
        }

        .kg-edit-toggle-btn:disabled:hover {
            background: rgba(255,255,255,0.5);
            color: var(--color-text-tertiary);
        }
```

- [ ] **Step 5: Bump version to `20260407g`**

In `static/news-search-prototype.html`, update both `?v=` parameters to `20260407g`.

- [ ] **Step 6: Run full smoke test**

```bash
cd /c/users/user/nlweb/code/python && python tools/smoke_test.py
```

Expected: All tests PASS.

- [ ] **Step 7: Final E2E checklist — verify all scope items**

Work through each item in the scope table:

| Item | Test |
|------|------|
| Enter edit mode | Click "編輯" → UI switches to edit mode |
| Exit edit mode (cancel) | Click "取消" → original graph restored |
| Edit node label | Click node → popover → change name → save → graph re-renders |
| Delete node | Click node → delete → node + its edges removed |
| Add node | Click "+ 節點" → fill form → node appears in graph |
| Delete edge | Click edge → delete → edge removed |
| Add edge (connect mode) | Click "連線" → click A → click B → edge A→B created |
| Confirm submit | Click "確認送出" → chat opens → prompt injected → AI responds |
| Cancel | Restores original, no corruption of `currentKGData` |
| Edge label edit | Click edge → change relation_type → save → re-renders |
| Node type select | Popover type dropdown → change type → save → node style updates |

- [ ] **Step 8: Final commit**

```bash
git add static/news-search.js static/news-search.css static/news-search-prototype.html
git commit -m "feat(kg-edit): edge case polish — background click, disabled state, zero-change confirm"
```

---

## Testing Strategy

### Manual Verification Points (per task)

Each task includes a manual verification step. Run after implementing each task before committing.

### Smoke Test Gate

Run `cd code/python && python tools/smoke_test.py` after Task 7 and after Task 8. This catches any Python-side regressions. Since this feature is pure frontend, smoke test should always pass unless an unrelated file was accidentally touched.

#### E11: smoke_test.py 加入 ruff F821 static analysis（commit `92a8dda`）

Commit `92a8dda` 在 `tools/smoke_test.py` 加入 `ruff check --select F821`（undefined name）static analysis，作為 smoke test 的第二道關卡。動機來自本次 KG editing 開發中的真實事故：

- **觸發案例**：`webserver/routes/api.py` 的 import 段使用 `import time as time_mod`（避免與既有 `time` 變數衝突），但本次新增的 `log_query_start` pre-register 程式碼手動寫成了 `time.time()` 而非 `time_mod.time()`。
- **為何 import smoke test 抓不到**：函數體內的未定義變數（`NameError: name 'time' is not defined`）只在執行該函數時才會報錯，import 模組本身不會觸發。原本只有 `__import__()` 的 smoke test 全部通過，但 runtime 一呼叫該 endpoint 就爆。
- **解決方案**：用 ruff F821 做 static analysis，可在不執行程式碼的情況下抓出函數體內的 undefined name。詳見 `memory/lessons-frontend.md`「Import alias 造成 runtime error — smoke test 改用 ruff F821 偵測」段。
- **通則**：改檔案前先看 import section 的 alias。有 alias 的 module（`time as time_mod`, `json as json_mod` 等）是高危區，手動補的 code 容易誤用 bare name。
- **強制性**：此檢查現為 smoke test 的標準輸出之一（`Static analysis (ruff F821): OK / FAILED`），FAILED 視同 smoke test 失敗，必須立即修復後才算通過 gate。

### E2E Checklist (Day 4, Task 8 Step 7)

The full E2E checklist in Task 8 Step 7 is the acceptance gate. Every scope item must pass.

### Browser Console Zero-Error Rule

After every manual verification step, confirm the browser DevTools console shows no JS errors. KG edit adds significant DOM manipulation — errors indicate a broken listener or null reference.

### Regression Check: Existing KG Functions

After Day 2, verify these existing KG functions still work correctly (not broken by edit mode additions):
- Display KG from new Deep Research query (SSE flow)
- Toggle between graph/list view
- Collapse/expand KG section
- Hide/restore KG
- Session persistence: save and reload a session — KG restores correctly

---

## Risks and Fallbacks

| Risk | Likelihood | Fallback |
|------|------------|---------- |
| `enterChatMode` function name mismatch | Medium | Task 7 Step 2 explicitly requires verifying the function name before committing. If no single `enterChatMode`, manually set `currentMode = 'chat'` and show/hide containers as done elsewhere in the codebase. |
| D3 zoom transform makes popover positioning wrong | Medium | `positionPopover` uses `d3.zoomTransform(svgEl)` to account for zoom. If zoom is not applied to `svg` element but to an inner `g`, adjust the selector in `positionPopover`. |
| Edge click not firing (edges are thin lines) | Medium | Increase edge hit area using a wider invisible stroke. Add: `.kg-link-hitbox { stroke: transparent; stroke-width: 12px; pointer-events: stroke; }` and add a parallel hitbox path behind each visible edge. |
| `performFreeConversation` not in scope when called from `confirmKGEdit` | Low | Both are inside the same IIFE/closure in `news-search.js`. If hoisting issues arise, move `confirmKGEdit` further down the file, after `performFreeConversation`. |
| Prompt too long for LLM context window | Low | The serialized KG JSON is bounded by the number of entities (typically <50) and relationships (<100). Even at 100 entities + 200 relationships, the JSON is <10KB, well within LLM context. No truncation needed. |
| `kgEditData` not re-initialized after resize re-render | Low | `renderKGGraphView` is called with `kgEditData` during edit mode. The re-render reads from `kgEditData`, not `currentKGData`, so subsequent edits are additive. Confirm by: enter edit mode → make change → resize window → re-render triggers → make another change → both changes preserved. |
| CSS `position: absolute` popover leaks outside container bounds | Low | `positionPopover` clamps to container bounds. The container has `overflow: hidden`. Popover `z-index: 100` keeps it above SVG. |
