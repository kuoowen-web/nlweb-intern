// static/js/features/knowledge-graph.js
//
// D-1 Module Header — Knowledge Graph (Phase 8 commit 21 — NEW module)
//   Owned state (module-private lets, co-migrated from news-search.js 2804-2810):
//     - _currentKGData     (latest rendered KG data — used by saveCurrentSession to
//                            serialize knowledgeGraph into sessionHistory)
//     - _kgSimulation      (D3 simulation handle, retained so resize/cancel can stop it)
//     - _kgEditMode        (true while user is in KG edit mode)
//     - _kgEditData        (deep-cloned working copy during edit)
//     - _kgConnectMode     (true while "connect two nodes" tool is active)
//     - _kgConnectSourceId (entity_id of the first clicked node in connect mode)
//     - _kgEditStats       (counts of adds/deletes/modifications, used by serializeKGEdit)
//
//   Trigger writes:
//     - displayKnowledgeGraph (called by features/deep-research.js when DR completes
//       and by news-search.js restoreSession when an existing session has knowledgeGraph)
//     - enterKGEditMode / cancelKGEdit / confirmKGEdit (user edit flow)
//     - Node/edge CRUD inside edit mode (saveNodeEdit / deleteCurrentNode /
//       createNewNode / saveEdgeEdit / deleteCurrentEdge / handleKGConnectClick)
//     - resetKGState (called by news-search.js resetConversation app-shell reset)
//
//   External read:
//     - features/deep-research.js displayDeepResearchResults reads `displayKnowledgeGraph`
//       (was via window.displayKnowledgeGraph bridge until commit 21; bridge re-attached
//       at news-search.js import site for now — direct import sweep target commit 25).
//     - features/deep-research.js performDeepResearch final_result serializes
//       getCurrentKGData() (was via window.__getCurrentKGData bridge — same sweep plan).
//     - news-search.js saveCurrentSession reads getCurrentKGData() (was bare
//       currentKGData reference); restoreSession reads getKGEditMode() + calls
//       displayKnowledgeGraph(); resetConversation calls resetKGState().
//
// D-3 Cross-Module Communication:
//   Static imports only.
//     - features/research.js     (getResearchReport / getArgumentGraph / getChainAnalysis)
//     - features/search.js       (getConversationHistory) — D-V6 OK (search does NOT
//       import knowledge-graph).
//     - features/deep-research.js (getCurrentResearchQueryId / displayDeepResearchResults
//       / showDRError, setCurrentConversationId) — D-V6 RELAX per CEO #7 (commit 21).
//       deep-research.js currently reads KG via window bridges (no static import back
//       from deep-research → knowledge-graph), so direction is one-way knowledge-graph
//       → deep-research at module-import level. Bridges sweep commit 25.
//     - features/sessions-list.js (getSessionHistory) for confirmKGEdit rerun completion.
//   Bridge calls remaining (sweep targets):
//     - window.renderConversationHistory (defined in news-search.js — Phase 8 KEEP
//       residual; sweep commit 25 if relocated)
//     - window.saveCurrentSession (defined in news-search.js — CEO #5 stays in place
//       until commit 23 session-coordinator.js NEW)
//
// D-13 Compliance:
//   No top-level side effects. Module-load is INERT — only declarations + function
//   bodies + exports. All side effects (DOM mutation, event binding, fetch) only run
//   when an external caller invokes one of the exported functions.
//
//   D-V8 visual-contract: KG render fragment is the only DOM produced here.
//   E2E test path: search → DR mode → KG renders → toggle list/graph → enter edit
//   mode → edit/add/delete node + edge → confirm → /api/research/rerun triggered.
//
// Commit 21 (Phase 8 part C):
//   Migrated from news-search.js lines 2753-4456:
//     - 5 KG constants (KG_ENTITY_COLORS / KG_ENTITY_STYLES / KG_DEFAULT_STYLE /
//       KG_TYPE_LABELS / KG_RELATION_LABELS)
//     - 7 state lets (currentKGData / kgSimulation / kgEditMode / kgEditData /
//       kgConnectMode / kgConnectSourceId / kgEditStats)
//     - 24 functions (displayKnowledgeGraph through confirmKGEdit)
//   Bridges removed FROM news-search.js (re-attached at import site for now):
//     - window.displayKnowledgeGraph (was line 2904 declaration-site attach)
//     - window.__getCurrentKGData (was line 2816 declaration-site attach)
//   Re-bridge kept at news-search.js import site so features/deep-research.js can still
//   reach via window during the transition. Sweep target commit 25.

// v4.0 Commit 21 (2026-05-25, Phase 8 part C) — direct cross-module imports per D-V6 relax.
import { getResearchReport, getArgumentGraph, getChainAnalysis } from './research.js';
import { getConversationHistory } from './search.js?v=20260714a';
import {
    getCurrentResearchQueryId,
    displayDeepResearchResults,
    showDRError
} from './deep-research.js?v=20260715b';
import { setCurrentConversationId } from './search.js?v=20260714a';
import { getSessionHistory } from './sessions-list.js';
import { copyAndOpen } from './sharing.js';
import { markSessionDirty } from './session-manager.js';

// ============================================================================
// KG Constants — entity-type → color/shape/label, relation-type → label
// ============================================================================

// Entity type colors for D3 visualization (brand palette)
const KG_ENTITY_COLORS = {
    'person': '#FDCB6E',
    'organization': '#FDCB6E',
    'event': '#FFEAA7',
    'location': '#FFEAA7',
    'metric': '#FFFFFF',
    'technology': '#FFFFFF',
    'concept': '#B2BEC3',
    'product': '#B2BEC3'
};

const KG_ENTITY_STYLES = {
    'person':       { fill: '#FDCB6E', stroke: '#2D3436', dash: '',      shape: 'circle' },
    'organization': { fill: '#FDCB6E', stroke: '#2D3436', dash: '4,3',   shape: 'diamond' },
    'event':        { fill: '#FFEAA7', stroke: '#2D3436', dash: '',      shape: 'circle' },
    'location':     { fill: '#FFEAA7', stroke: '#2D3436', dash: '',      shape: 'diamond' },
    'metric':       { fill: '#FFFFFF', stroke: '#2D3436', dash: '',      shape: 'circle' },
    'technology':   { fill: '#FFFFFF', stroke: '#2D3436', dash: '',      shape: 'diamond' },
    'concept':      { fill: '#B2BEC3', stroke: '#2D3436', dash: '',      shape: 'circle' },
    'product':      { fill: '#B2BEC3', stroke: '#2D3436', dash: '4,3',   shape: 'circle' }
};

const KG_DEFAULT_STYLE = { fill: '#B2BEC3', stroke: '#2D3436', dash: '', shape: 'circle' };

// Entity type labels
const KG_TYPE_LABELS = {
    'person': '人物',
    'organization': '組織',
    'event': '事件',
    'location': '地點',
    'metric': '指標',
    'technology': '技術',
    'concept': '概念',
    'product': '產品',
    'facility': '設施',
    'service': '服務'
};

// Relation type labels
const KG_RELATION_LABELS = {
    'causes': '導致',
    'enables': '促成',
    'prevents': '阻止',
    'precedes': '先於',
    'concurrent': '同時',
    'part_of': '屬於',
    'owns': '擁有',
    'related_to': '相關'
};

// ============================================================================
// buildKGCopyText — 純函式：KG 資料 → 結構化文字（複製功能用，可單元測試）
// 鏡像 renderKGListView 的欄位讀取與 fallback，但輸出 plain text 而非 HTML。
// 無 DOM / 無 state；空圖回傳 ''（呼叫端據此略過複製）。
// ============================================================================
export function buildKGCopyText(kg) {
    if (!kg || !kg.entities || kg.entities.length === 0) return '';

    const entityCount = kg.entities.length;
    const relCount = (kg.relationships || []).length;

    let out = '知識圖譜\n';
    out += `${entityCount} 個實體 • ${relCount} 個關係\n\n`;

    // 實體區
    out += '【實體】\n';
    kg.entities.forEach((entity, i) => {
        const typeLabel = KG_TYPE_LABELS[entity.entity_type] || entity.entity_type;
        const conf = entity.confidence ? ` [${entity.confidence}]` : '';
        out += `${i + 1}. ${entity.name}（${typeLabel}）${conf}\n`;
        if (entity.description) {
            out += `   ${entity.description}\n`;
        }
    });

    // 關係區
    if (relCount > 0) {
        const entityMap = {};
        kg.entities.forEach(e => { entityMap[e.entity_id] = e.name; });

        out += '\n【關係】\n';
        kg.relationships.forEach((rel, i) => {
            const relationLabel = KG_RELATION_LABELS[rel.relation_type] || rel.relation_type;
            const sourceName = entityMap[rel.source_entity_id] || '未知';
            const targetName = entityMap[rel.target_entity_id] || '未知';
            const conf = rel.confidence ? ` [${rel.confidence}]` : '';
            out += `${i + 1}. ${sourceName} --[${relationLabel}]--> ${targetName}${conf}\n`;
            if (rel.description) {
                out += `   ${rel.description}\n`;
            }
        });
    }

    return out;
}

// ============================================================================
// Per-instance KG factory (Track D fix 2026-05-29): 消除 module-global 單例。
// 每個 instance 持有自己的 prefix + 全部 runtime state，DR / LR 同頁互不污染。
//
// 根因: 原本 8 個 module-level 單例 (_kgPrefix + _currentKGData + 6 edit-state
// vars) 跨 DR/LR 共享。LR render 會把 _kgPrefix 留在 'lrKG' (stale)，使後續 DR
// KG edit handler 在 fire time 讀到錯 prefix → 操作錯 DOM + 送出跨污染的
// /api/research/rerun POST。factory 把 prefix 固定成 closure const (instance
// 生命週期內不可變)，並把所有 state 收進 closure，從架構上消除漂移與跨污染。
//
// 原 module-global state 與 24 函式整段搬進此 factory body (邏輯零改、僅 scope
// 改變)。5 個 stateless const (KG_ENTITY_COLORS / KG_ENTITY_STYLES /
// KG_DEFAULT_STYLE / KG_TYPE_LABELS / KG_RELATION_LABELS) 留在 module scope
// 外層 (無 state，跨 instance 共享安全)。
// ============================================================================
function createKGInstance(prefix) {
    // ---- instance-private state（原 module-global，現 closure-scoped）----
    let _currentKGData = null;
    let _kgSimulation = null;
    let _kgEditMode = false;       // true when edit mode is active
    let _kgEditData = null;        // deep-cloned working copy during edit
    let _kgConnectMode = false;    // true when "connect" tool is active
    let _kgConnectSourceId = null; // entity_id of the first clicked node in connect mode
    let _kgEditStats = { nodesAdded: 0, nodesDeleted: 0, nodesModified: 0, edgesAdded: 0, edgesDeleted: 0, edgesModified: 0 };

    // prefix 由 factory 參數固定，instance 生命週期內不可變（原 module-global
    // _kgPrefix 移除，line-242 寫入點刪除）。_kgId 在 closure 捕捉此固定 prefix，
    // 所有 callsite fire 時讀的都是 instance 自己的 prefix，無 timing 依賴。
    const _kgId = (suffix) => `${prefix}${suffix}`;

    // escapeHTML free name（原 module-scope const，搬進 closure 維持可見性）。
    // 每 instance 一份無妨，純 thin wrapper over window.escapeHTML。
    const escapeHTML = (s) => window.escapeHTML(s);

    // ---- External read accessors ----
    function getCurrentKGData() { return _currentKGData; }
    function getKGEditMode() { return _kgEditMode; }

    // ---- 複製 KG 為結構化文字（沿用 sharing.js#copyAndOpen pattern）----
    // 隔離要求：只讀本 instance closure 的 _currentKGData，禁止呼叫 exported getCurrentKGData()
    function copyKGAsText(buttonEl) {
        const text = buildKGCopyText(_currentKGData);
        if (!text) {
            // 不可 silent fail：無資料時給可見訊息（沿用 copyAndOpen 失敗回饋風格）
            const original = buttonEl.textContent;
            buttonEl.textContent = '✗ 無資料';
            setTimeout(() => { buttonEl.textContent = original; }, 2000);
            console.warn('[KG] 複製略過：無 KG 資料');
            return;
        }
        copyAndOpen(text, null, buttonEl);
    }

    function setupKGCopy() {
        const copyBtn = document.getElementById(_kgId('CopyBtn'));
        if (!copyBtn) return;
        // 沿用 setupKGEditMode 的 clone-to-strip-stale-listener pattern
        const fresh = copyBtn.cloneNode(true);
        copyBtn.parentNode.replaceChild(fresh, copyBtn);
        const wired = document.getElementById(_kgId('CopyBtn'));
        // disable 鏡像 edit 鈕：空圖時複製鈕 disabled（鏡像 displayKnowledgeGraph 的 editBtn.disabled = !hasEntities）
        const hasEntities = _currentKGData && _currentKGData.entities && _currentKGData.entities.length > 0;
        wired.disabled = !hasEntities;
        wired.title = hasEntities ? '複製實體與關係文字' : '無資料可複製';
        wired.addEventListener('click', function () {
            copyKGAsText(this);
        });
    }

    // ---- App-shell reset coordinator (called by news-search.js resetConversation) ----
    // Replaces the inline reset block (was news-search.js 2421-2440). Mirrors the
    // same DOM cleanup so behavior is identical.
    //
    // instance prefix 固定，直接用 _kgId（原 resetKGState(prefix) 的本地 _id helper
    // 刪除，等價）。DR 外部 no-arg call 在 module-level thin wrapper route 到 DR
    // instance；LR session 切換清理走 resetLiveResearchUI (live-research.js)，不經此。
    function resetKGState() {
        // Clean up visual edit-mode UI if active
        if (_kgEditMode) {
            const editToggle = document.getElementById(_kgId('EditToggleBtn'));
            const editControls = document.getElementById(_kgId('EditControls'));
            const editActions = document.getElementById(_kgId('EditActionControls'));
            const graphContainer = document.getElementById(_kgId('GraphView'));
            const connectBtn = document.getElementById(_kgId('ConnectBtn'));
            if (editToggle) editToggle.style.display = '';
            if (editControls) editControls.style.display = 'none';
            if (editActions) editActions.style.display = 'none';
            if (graphContainer) graphContainer.classList.remove('kg-edit-active');
            if (connectBtn) connectBtn.classList.remove('active');
        }
        _currentKGData = null;
        _kgEditMode = false;
        _kgEditData = null;
        _kgConnectMode = false;
        _kgConnectSourceId = null;
        if (_kgSimulation) { _kgSimulation.stop(); _kgSimulation = null; }
        const kgContainerReset = document.getElementById(_kgId('DisplayContainer'));
        if (kgContainerReset) kgContainerReset.style.display = 'none';
    }

/**
 * Track D D2b (sprint 2026-05-28): displayKnowledgeGraph 支援 containerPrefix。
 *
 * D-CEO-Q4 LOCKED Option (β): DR 與 LR DOM 完全隔離,共用 module 程式碼。
 * DR caller (deep-research.js displayDeepResearchResults) 不傳 options →
 *   options.containerPrefix === undefined → fallback 'kg' (DR 既有行為,
 *   zero regression)
 * LR caller (live-research.js handleLiveResearchSSE) 傳
 *   { containerPrefix: 'lrKG' } → 全 module id lookup 走 lrKG prefix
 *
 * @param {Object} kg - KnowledgeGraph payload (entities + relationships)
 * @param {Object} [options] - 可選 options
 * @param {string} [options.containerPrefix='kg'] - id prefix; LR 端傳 'lrKG'
 */
function displayKnowledgeGraph(kg) {
    // prefix 已由 createKGInstance 固定，不再從 options 讀（原 line-242
    // `_kgPrefix = options.containerPrefix || 'kg'` 已刪除）。options routing
    // 移到 module-level thin wrapper。instance 版只收 kg。

    // Force exit edit mode if active — new KG data replaces edited state
    if (_kgEditMode) {
        console.log('[KG] Exiting edit mode: new KG data received');
        _kgEditMode = false;
        _kgEditData = null;
        _kgConnectMode = false;
        _kgConnectSourceId = null;
        // Full visual cleanup (mirrors cancelKGEdit)
        const editToggle = document.getElementById(_kgId('EditToggleBtn'));
        const editControls = document.getElementById(_kgId('EditControls'));
        const editActions = document.getElementById(_kgId('EditActionControls'));
        const graphContainer = document.getElementById(_kgId('GraphView'));
        const connectBtn = document.getElementById(_kgId('ConnectBtn'));
        if (editToggle) editToggle.style.display = '';
        if (editControls) editControls.style.display = 'none';
        if (editActions) editActions.style.display = 'none';
        if (graphContainer) graphContainer.classList.remove('kg-edit-active');
        if (connectBtn) connectBtn.classList.remove('active');
        closeAllKGPopovers();
    }
    const container = document.getElementById(_kgId('DisplayContainer'));
    const graphView = document.getElementById(_kgId('GraphView'));
    const listContent = document.getElementById(_kgId('DisplayContent'));
    const empty = document.getElementById(_kgId('DisplayEmpty'));
    const metadata = document.getElementById(_kgId('Metadata'));
    const legend = document.getElementById(_kgId('Legend'));

    // Issue #7: Clear previous KG SVG before rendering new one
    if (graphView) {
        const oldSvg = graphView.querySelector('svg');
        if (oldSvg) oldSvg.remove();
    }

    if (!kg || (!kg.entities || kg.entities.length === 0)) {
        container.style.display = 'none';
        console.log('[KG] No knowledge graph data to display');
        return;
    }

    // Store KG data globally
    _currentKGData = kg;

    // Respect user's KG hidden preference
    if (container.dataset.userHidden === 'true') {
        const restoreBar = document.getElementById(_kgId('RestoreBar'));
        if (restoreBar) restoreBar.style.display = 'block';
        // Still render data so it's ready when user restores
    } else {
        container.style.display = 'block';
    }

    // Update metadata
    const entityCount = kg.entities?.length || 0;
    const relCount = kg.relationships?.length || 0;
    const timestamp = kg.metadata?.generated_at ? new Date(kg.metadata.generated_at).toLocaleTimeString('zh-TW', {hour: '2-digit', minute: '2-digit'}) : '';
    metadata.textContent = `${entityCount} 個實體 • ${relCount} 個關係${timestamp ? ' • 生成於 ' + timestamp : ''}`;

    // Render list view content
    renderKGListView(kg, listContent);

    // Render graph view with D3
    renderKGGraphView(kg, graphView);

    // Render legend
    renderKGLegend(kg, legend);

    // Setup view toggle
    setupKGViewToggle();
    // Setup edit mode toggle
    setupKGEditMode();
    // Setup copy button（沿用 sharing.js copyAndOpen pattern）
    setupKGCopy();

    // Disable edit button if KG is empty
    const editBtn = document.getElementById(_kgId('EditToggleBtn'));
    if (editBtn) {
        const hasEntities = kg && kg.entities && kg.entities.length > 0;
        editBtn.disabled = !hasEntities;
        editBtn.title = hasEntities ? '進入編輯模式' : '無節點可編輯';
    }

    empty.style.display = 'none';
    console.log('[KG] Knowledge graph displayed successfully with D3 visualization');
}

function renderKGListView(kg, container) {
    let html = '';

    // Entities section
    if (kg.entities && kg.entities.length > 0) {
        html += '<div class="kg-section">';
        html += `<div class="kg-section-title">實體 (${kg.entities.length})</div>`;
        kg.entities.forEach(entity => {
            const typeLabel = KG_TYPE_LABELS[entity.entity_type] || entity.entity_type;
            html += '<div class="kg-item">';
            html += `<div><span class="kg-item-name">${escapeHTML(entity.name)}</span>`;
            html += `<span class="kg-item-type">${typeLabel}</span>`;
            html += `<span class="kg-item-confidence ${entity.confidence}">${entity.confidence}</span>`;
            html += '</div>';
            if (entity.description) {
                html += `<div class="kg-item-desc">${escapeHTML(entity.description)}</div>`;
            }
            html += '</div>';
        });
        html += '</div>';
    }

    // Relationships section
    if (kg.relationships && kg.relationships.length > 0) {
        html += '<div class="kg-section">';
        html += `<div class="kg-section-title">關係 (${kg.relationships.length})</div>`;

        const entityMap = {};
        if (kg.entities) {
            kg.entities.forEach(e => entityMap[e.entity_id] = e.name);
        }

        kg.relationships.forEach(rel => {
            const relationLabel = KG_RELATION_LABELS[rel.relation_type] || rel.relation_type;
            const sourceName = entityMap[rel.source_entity_id] || '未知';
            const targetName = entityMap[rel.target_entity_id] || '未知';

            html += '<div class="kg-item">';
            html += `<div>${escapeHTML(sourceName)} <span class="kg-relationship-arrow">→</span> ${escapeHTML(targetName)}`;
            html += `<span class="kg-item-type">${relationLabel}</span>`;
            html += `<span class="kg-item-confidence ${rel.confidence}">${rel.confidence}</span>`;
            html += '</div>';
            if (rel.description) {
                html += `<div class="kg-item-desc">${escapeHTML(rel.description)}</div>`;
            }
            html += '</div>';
        });
        html += '</div>';
    }

    container.innerHTML = html;
}

function renderKGGraphView(kg, container) {
    // Clear previous SVG
    d3.select(container).select('svg').remove();
    if (_kgSimulation) {
        _kgSimulation.stop();
        _kgSimulation = null;
    }

    const tooltip = document.getElementById(_kgId('Tooltip'));
    const width = container.clientWidth || 600;
    const height = container.clientHeight || 400;
    const centerX = width / 2;
    const centerY = height / 2;

    // --- 1. Build adjacency and compute degrees ---
    const degree = {};
    kg.entities.forEach(e => { degree[e.entity_id] = 0; });
    (kg.relationships || []).forEach(r => {
        if (degree.hasOwnProperty(r.source_entity_id)) degree[r.source_entity_id]++;
        if (degree.hasOwnProperty(r.target_entity_id)) degree[r.target_entity_id]++;
    });

    // --- 2. Identify center node (highest degree) ---
    let centerEntity = kg.entities[0];
    let maxDeg = degree[centerEntity.entity_id] || 0;
    kg.entities.forEach(e => {
        const d = degree[e.entity_id] || 0;
        if (d > maxDeg) { maxDeg = d; centerEntity = e; }
    });

    // --- 3. Group remaining nodes by type into sectors ---
    const remaining = kg.entities.filter(e => e.entity_id !== centerEntity.entity_id);
    const typeGroups = {};
    remaining.forEach(e => {
        const t = e.entity_type || 'unknown';
        if (!typeGroups[t]) typeGroups[t] = [];
        typeGroups[t].push(e);
    });
    const typeKeys = Object.keys(typeGroups);
    const numTypes = typeKeys.length || 1;
    const sectorAngle = (2 * Math.PI) / numTypes;

    // --- 4. Compute ring radius ---
    const maxGroupSize = Math.max(...Object.values(typeGroups).map(g => g.length), 1);
    const useDoubleRing = maxGroupSize > 5;
    const innerRingR = Math.min(width, height) * (useDoubleRing ? 0.22 : 0.32);
    const outerRingR = Math.min(width, height) * 0.40;

    // --- 5. Node sizing ---
    const BASE_RADIUS = 14;
    const SCALE_FACTOR = 4;
    const MAX_RADIUS = 40;
    function nodeRadius(entityId) {
        const d = degree[entityId] || 0;
        return Math.min(BASE_RADIUS + d * SCALE_FACTOR, MAX_RADIUS);
    }

    // --- 6. Position all nodes ---
    const nodePositions = {}; // entity_id -> {x, y, r, entity}

    // Center node
    const centerR = Math.max(nodeRadius(centerEntity.entity_id), 24);
    nodePositions[centerEntity.entity_id] = {
        x: centerX, y: centerY, r: centerR, entity: centerEntity, isCenter: true
    };

    // Radial nodes
    typeKeys.forEach((type, typeIdx) => {
        const group = typeGroups[type];
        const startAngle = typeIdx * sectorAngle - Math.PI / 2; // start from top
        const step = sectorAngle / (group.length + 1);

        group.forEach((entity, j) => {
            const angle = startAngle + (j + 1) * step;
            // If double ring, alternate between inner and outer
            const ringR = useDoubleRing ? (j % 2 === 0 ? innerRingR : outerRingR) : innerRingR;
            nodePositions[entity.entity_id] = {
                x: centerX + ringR * Math.cos(angle),
                y: centerY + ringR * Math.sin(angle),
                r: nodeRadius(entity.entity_id),
                entity: entity,
                isCenter: false
            };
        });
    });

    // --- 7. Prepare links (only those with both endpoints present) ---
    const nodeIds = new Set(Object.keys(nodePositions));
    const links = (kg.relationships || [])
        .filter(r => nodeIds.has(r.source_entity_id) && nodeIds.has(r.target_entity_id))
        .map(r => ({
            source: r.source_entity_id,
            target: r.target_entity_id,
            type: r.relation_type,
            description: r.description,
            confidence: r.confidence
        }));

    // --- 8. Create SVG ---
    const svg = d3.select(container)
        .append('svg')
        .attr('width', width)
        .attr('height', height);

    const g = svg.append('g');

    // Zoom + pan
    const zoom = d3.zoom()
        .scaleExtent([0.3, 3])
        .on('zoom', (event) => { g.attr('transform', event.transform); });
    svg.call(zoom);

    // Click on empty space to deselect (or close popovers in edit mode)
    svg.on('click', function(event) {
        if (event.target === this || event.target.tagName === 'svg') {
            if (_kgEditMode) {
                closeAllKGPopovers();
                // If in connect mode and source is selected, just deselect source (don't exit connect mode)
                if (_kgConnectSourceId) {
                    _kgConnectSourceId = null;
                    d3.selectAll('.kg-node').classed('kg-connect-source', false);
                    document.getElementById(_kgId('ConnectBtn')).textContent = '連線中（點節點 A）';
                }
            } else {
                deselectAll();
            }
        }
    });

    // --- 9. Draw arrow markers ---
    svg.append('defs').selectAll('marker')
        .data(['kg-arrow', 'kg-arrow-highlight'])
        .enter().append('marker')
        .attr('id', d => d)
        .attr('viewBox', '0 -5 10 10')
        .attr('refX', 10)
        .attr('refY', 0)
        .attr('markerWidth', 6)
        .attr('markerHeight', 6)
        .attr('orient', 'auto')
        .append('path')
        .attr('fill', d => d === 'kg-arrow-highlight' ? '#FDCB6E' : '#B2BEC3')
        .attr('d', 'M0,-5L10,0L0,5');

    // --- 10. Draw edges: straight lines for center edges, arcs for leaf-leaf ---
    const centerId = centerEntity.entity_id;

    function isCenterEdge(link) {
        return link.source === centerId || link.target === centerId;
    }

    // For center edges: always draw center → leaf (swap if needed)
    function computeStraightPath(link) {
        // Canonical direction: center is always the "from" node
        const fromId = link.source === centerId ? link.source : link.target;
        const toId   = link.source === centerId ? link.target : link.source;
        const s = nodePositions[fromId];
        const t = nodePositions[toId];
        if (!s || !t) return '';

        const dx = t.x - s.x;
        const dy = t.y - s.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;

        const sStyle = KG_ENTITY_STYLES[s.entity.entity_type] || KG_DEFAULT_STYLE;
        const sBoundingR = (sStyle.shape === 'diamond') ? s.r * 1.0 : s.r;
        const startOffset = sBoundingR + 2;

        const tStyle = KG_ENTITY_STYLES[t.entity.entity_type] || KG_DEFAULT_STYLE;
        const tBoundingR = (tStyle.shape === 'diamond') ? t.r * 1.0 : t.r;
        const endOffset = tBoundingR + 8; // extra for arrowhead

        const ux = dx / dist, uy = dy / dist;
        const sx = s.x + ux * startOffset;
        const sy = s.y + uy * startOffset;
        const tx = t.x - ux * endOffset;
        const ty = t.y - uy * endOffset;

        return `M${sx},${sy} L${tx},${ty}`;
    }

    function computeArcPath(link) {
        const s = nodePositions[link.source];
        const t = nodePositions[link.target];
        if (!s || !t) return '';

        // Vector from source to target
        const dx = t.x - s.x;
        const dy = t.y - s.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;

        // Unit normal (perpendicular) for the curve bulge
        const nx = -dy / dist;
        const ny = dx / dist;

        // Bulge amount: proportional to distance, capped
        const bulge = Math.min(dist * 0.2, 40);

        // Control point
        const cx = (s.x + t.x) / 2 + nx * bulge;
        const cy = (s.y + t.y) / 2 + ny * bulge;

        // Offset start/end by node radius so arrow lands on edge
        const sStyle = KG_ENTITY_STYLES[s.entity.entity_type] || KG_DEFAULT_STYLE;
        const sBoundingR = (sStyle.shape === 'diamond') ? s.r * 1.0 : s.r;
        const startOffset = sBoundingR + 2;

        const tStyle = KG_ENTITY_STYLES[t.entity.entity_type] || KG_DEFAULT_STYLE;
        const tBoundingR = (tStyle.shape === 'diamond') ? t.r * 1.0 : t.r;
        const endOffset = tBoundingR + 8; // extra for arrowhead

        // Unit vector from source to control point
        const sdx = cx - s.x, sdy = cy - s.y;
        const sdist = Math.sqrt(sdx * sdx + sdy * sdy) || 1;
        const sx = s.x + (sdx / sdist) * startOffset;
        const sy = s.y + (sdy / sdist) * startOffset;

        // Unit vector from control point to target
        const tdx = t.x - cx, tdy = t.y - cy;
        const tdist = Math.sqrt(tdx * tdx + tdy * tdy) || 1;
        const tx = t.x - (tdx / tdist) * endOffset;
        const ty = t.y - (tdy / tdist) * endOffset;

        return `M${sx},${sy} Q${cx},${cy} ${tx},${ty}`;
    }

    function computeEdgePath(link) {
        return isCenterEdge(link) ? computeStraightPath(link) : computeArcPath(link);
    }

    const edgeGroup = g.append('g').attr('class', 'kg-edges');

    const edge = edgeGroup.selectAll('path')
        .data(links)
        .enter().append('path')
        .attr('class', 'kg-link')
        .attr('d', computeEdgePath)
        .attr('stroke', '#B2BEC3')
        .attr('stroke-width', 1.5)
        .attr('fill', 'none')
        .attr('marker-end', 'url(#kg-arrow)');

    // Invisible wider hitbox paths for easier edge clicking in edit mode
    const edgeHitbox = edgeGroup.selectAll('path.kg-link-hitbox')
        .data(links)
        .enter().append('path')
        .attr('class', 'kg-link-hitbox')
        .attr('d', computeEdgePath)
        .attr('stroke', 'transparent')
        .attr('stroke-width', 14)
        .attr('fill', 'none')
        .attr('pointer-events', 'stroke')
        .style('cursor', 'pointer')
        .on('click', function(event, d) {
            event.stopPropagation();
            if (!_kgEditMode) return;
            showEdgeEditPopover(event, d, this);
        });

    // Edge click handler on visible paths too
    edge.on('click', function(event, d) {
        event.stopPropagation();
        if (!_kgEditMode) return;
        showEdgeEditPopover(event, d, this);
    });

    // Edge labels: midpoint of straight line for center edges, arc midpoint for leaf-leaf
    const edgeLabelGroup = g.append('g').attr('class', 'kg-edge-labels');

    const edgeLabel = edgeLabelGroup.selectAll('text')
        .data(links)
        .enter().append('text')
        .attr('class', 'kg-link-label')
        .each(function(d) {
            let mx, my;
            if (isCenterEdge(d)) {
                // Straight line midpoint
                const fromId = d.source === centerId ? d.source : d.target;
                const toId   = d.source === centerId ? d.target : d.source;
                const s = nodePositions[fromId];
                const t = nodePositions[toId];
                if (!s || !t) return;
                mx = (s.x + t.x) / 2;
                my = (s.y + t.y) / 2;
            } else {
                const s = nodePositions[d.source];
                const t = nodePositions[d.target];
                if (!s || !t) return;
                const dx = t.x - s.x, dy = t.y - s.y;
                const dist = Math.sqrt(dx * dx + dy * dy) || 1;
                const nx = -dy / dist, ny = dx / dist;
                const bulge = Math.min(dist * 0.2, 40);
                mx = (s.x + t.x) / 2 + nx * bulge * 0.6;
                my = (s.y + t.y) / 2 + ny * bulge * 0.6;
            }
            d3.select(this).attr('x', mx).attr('y', my);
        })
        .text(d => KG_RELATION_LABELS[d.type] || d.type);

    // --- 11. Draw nodes ---
    const nodeData = Object.values(nodePositions);

    const nodeGroup = g.append('g').attr('class', 'kg-nodes');

    const node = nodeGroup.selectAll('g')
        .data(nodeData)
        .enter().append('g')
        .attr('class', 'kg-node')
        .attr('transform', d => `translate(${d.x},${d.y})`);

    // Draw shape per node
    node.each(function(d) {
        const el = d3.select(this);
        const style = KG_ENTITY_STYLES[d.entity.entity_type] || KG_DEFAULT_STYLE;
        const strokeW = d.isCenter ? 3 : 2;
        const fillColor = d.isCenter ? '#FDCB6E' : style.fill;
        const strokeColor = d.isCenter ? '#2D3436' : style.stroke;
        const dash = d.isCenter ? '' : style.dash;
        const shape = d.isCenter ? 'circle' : style.shape;

        if (shape === 'diamond') {
            // Diamond = rotated square
            const size = d.r * 1.4; // side length so area is comparable to circle
            el.append('rect')
                .attr('x', -size / 2)
                .attr('y', -size / 2)
                .attr('width', size)
                .attr('height', size)
                .attr('transform', 'rotate(45)')
                .attr('fill', fillColor)
                .attr('stroke', strokeColor)
                .attr('stroke-width', strokeW)
                .attr('stroke-dasharray', dash);
        } else {
            el.append('circle')
                .attr('r', d.r)
                .attr('fill', fillColor)
                .attr('stroke', strokeColor)
                .attr('stroke-width', strokeW)
                .attr('stroke-dasharray', dash);
        }
    });

    // Node labels
    node.append('text')
        .attr('dy', d => d.r + 14)
        .attr('class', 'kg-node-label')
        .text(d => {
            const name = d.entity.name;
            return name.length > 12 ? name.substring(0, 12) + '...' : name;
        });

    // --- 12. Click-to-highlight interaction ---
    let selectedNodeId = null;

    function highlightNode(d) {
        selectedNodeId = d.entity.entity_id;
        const connectedNodeIds = new Set();
        connectedNodeIds.add(d.entity.entity_id);

        // Find connected edges and neighbor nodes
        links.forEach(l => {
            if (l.source === d.entity.entity_id) connectedNodeIds.add(l.target);
            if (l.target === d.entity.entity_id) connectedNodeIds.add(l.source);
        });

        // Dim unrelated nodes
        node.each(function(nd) {
            const el = d3.select(this);
            el.attr('opacity', connectedNodeIds.has(nd.entity.entity_id) ? 1 : 0.2);
        });

        // Highlight connected edges, dim others
        edge.each(function(ld) {
            const isConnected = ld.source === d.entity.entity_id || ld.target === d.entity.entity_id;
            d3.select(this)
                .attr('stroke', isConnected ? '#FDCB6E' : '#B2BEC3')
                .attr('stroke-width', isConnected ? 2.5 : 1.5)
                .attr('opacity', isConnected ? 1 : 0.15)
                .attr('marker-end', isConnected ? 'url(#kg-arrow-highlight)' : 'url(#kg-arrow)');
        });

        // Dim unrelated edge labels
        edgeLabel.each(function(ld) {
            const isConnected = ld.source === d.entity.entity_id || ld.target === d.entity.entity_id;
            d3.select(this).attr('opacity', isConnected ? 1 : 0.15);
        });
    }

    function deselectAll() {
        selectedNodeId = null;
        node.attr('opacity', 1);
        edge.attr('stroke', '#B2BEC3').attr('stroke-width', 1.5).attr('opacity', 1)
            .attr('marker-end', 'url(#kg-arrow)');
        edgeLabel.attr('opacity', 1);
    }

    // Click handler on nodes — moved to after drag setup (Issue #3) in section 14

    // --- 13. Tooltip on hover (Task 6: clamped to container bounds) ---
    node.on('mouseenter', function(event, d) {
        const typeLabel = KG_TYPE_LABELS[d.entity.entity_type] || d.entity.entity_type;
        const deg = degree[d.entity.entity_id] || 0;
        tooltip.innerHTML = `
            <div class="kg-tooltip-title">${escapeHTML(d.entity.name)}</div>
            <div class="kg-tooltip-type">${typeLabel}</div>
            ${d.entity.description ? `<div class="kg-tooltip-desc">${escapeHTML(d.entity.description)}</div>` : ''}
            <div class="kg-tooltip-degree">${deg} 個連結</div>
        `;
        tooltip.classList.add('visible');

        // Position tooltip near the node, clamped to container bounds
        const containerRect = container.getBoundingClientRect();
        const svgPoint = this.getBoundingClientRect();
        let tipX = svgPoint.left - containerRect.left + d.r + 10;
        let tipY = svgPoint.top - containerRect.top - 10;

        // Clamp to prevent overflow
        const tipW = 300; // max-width from CSS
        const tipH = 100; // estimated height
        if (tipX + tipW > containerRect.width) tipX = containerRect.width - tipW - 10;
        if (tipX < 0) tipX = 10;
        if (tipY + tipH > containerRect.height) tipY = containerRect.height - tipH - 10;
        if (tipY < 0) tipY = 10;

        tooltip.style.left = tipX + 'px';
        tooltip.style.top = tipY + 'px';
    })
    .on('mouseleave', function() {
        tooltip.classList.remove('visible');
    });

    // --- 14. Issue #3: Node drag in edit mode ---
    let wasDragged = false;
    const dragBehavior = d3.drag()
        .clickDistance(5)  // Only counts as drag if moved > 5px
        .on('start', function(event, d) {
            wasDragged = false;
            if (!_kgEditMode) return;
            event.sourceEvent.stopPropagation(); // Prevent zoom/pan
        })
        .on('drag', function(event, d) {
            if (!_kgEditMode) return;
            wasDragged = true;
            d.x = event.x;
            d.y = event.y;
            // Update node position in nodePositions map
            nodePositions[d.entity.entity_id].x = d.x;
            nodePositions[d.entity.entity_id].y = d.y;
            // Move the node group
            d3.select(this).attr('transform', `translate(${d.x},${d.y})`);
            // Update connected edges and labels
            edge.attr('d', computeEdgePath);
            edgeHitbox.attr('d', computeEdgePath);
            edgeLabel.each(function(ld) {
                let mx, my;
                if (isCenterEdge(ld)) {
                    const fromId = ld.source === centerId ? ld.source : ld.target;
                    const toId   = ld.source === centerId ? ld.target : ld.source;
                    const s = nodePositions[fromId];
                    const t = nodePositions[toId];
                    if (!s || !t) return;
                    mx = (s.x + t.x) / 2;
                    my = (s.y + t.y) / 2;
                } else {
                    const s = nodePositions[ld.source];
                    const t = nodePositions[ld.target];
                    if (!s || !t) return;
                    const dx2 = t.x - s.x, dy2 = t.y - s.y;
                    const dist2 = Math.sqrt(dx2 * dx2 + dy2 * dy2) || 1;
                    const nx2 = -dy2 / dist2, ny2 = dx2 / dist2;
                    const bulge2 = Math.min(dist2 * 0.2, 40);
                    mx = (s.x + t.x) / 2 + nx2 * bulge2 * 0.6;
                    my = (s.y + t.y) / 2 + ny2 * bulge2 * 0.6;
                }
                d3.select(this).attr('x', mx).attr('y', my);
            });
        })
        .on('end', function(event, d) {
            if (!_kgEditMode) return;
            // wasDragged is checked by click handler via closure
        });

    // Apply drag to all nodes (only active in edit mode due to guards)
    node.call(dragBehavior);

    // Override click handler to ignore drags
    node.on('click', function(event, d) {
        event.stopPropagation();

        // Issue #3: Skip click if it was actually a drag
        if (wasDragged) {
            wasDragged = false;
            return;
        }

        // Edit mode: route to popover or connect logic
        if (_kgEditMode) {
            if (_kgConnectMode) {
                handleKGConnectClick(d.entity.entity_id, d.entity);
            } else {
                showNodeEditPopover(event, d.entity.entity_id, d.entity, d.x, d.y, d.r);
            }
            return;
        }

        if (selectedNodeId === d.entity.entity_id) {
            deselectAll();
        } else {
            highlightNode(d);
        }
    });

    // Add interaction hint overlay (bottom-right, fades out after 3s)
    const existingHint = container.querySelector('.kg-interaction-hint');
    if (existingHint) existingHint.remove();
    const hint = document.createElement('div');
    hint.className = 'kg-interaction-hint';
    hint.textContent = '拖曳移動・滾輪縮放・點擊節點查看';
    container.appendChild(hint);
    setTimeout(() => { hint.classList.add('faded'); }, 3000);

    console.log(`[KG] Radial mind-map rendered: center="${centerEntity.name}", ${nodeData.length} nodes, ${links.length} edges`);
}

function renderKGLegend(kg, container) {
    const types = [...new Set(kg.entities.map(e => e.entity_type))];

    let html = '';
    types.forEach(type => {
        const style = KG_ENTITY_STYLES[type] || KG_DEFAULT_STYLE;
        const label = KG_TYPE_LABELS[type] || type;

        if (style.shape === 'diamond') {
            // Diamond swatch: rotated square via CSS
            html += `
                <div class="kg-legend-item">
                    <div class="kg-legend-diamond" style="background: ${style.fill}; border-color: ${style.stroke};${style.dash ? ' border-style: dashed;' : ''}"></div>
                    <span>${label}</span>
                </div>
            `;
        } else {
            // Circle swatch
            html += `
                <div class="kg-legend-item">
                    <div class="kg-legend-color" style="background: ${style.fill}; border: 2px solid ${style.stroke};${style.dash ? ' border-style: dashed;' : ''}"></div>
                    <span>${label}</span>
                </div>
            `;
        }
    });

    container.innerHTML = html;
}

function setupKGViewToggle() {
    const toggleContainer = document.getElementById(_kgId('ViewToggle'));
    const graphView = document.getElementById(_kgId('GraphView'));
    const listView = document.getElementById(_kgId('DisplayContent'));

    if (!toggleContainer) return;

    toggleContainer.querySelectorAll('.kg-view-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const view = this.getAttribute('data-view');

            // Update button states
            toggleContainer.querySelectorAll('.kg-view-btn').forEach(b => b.classList.remove('active'));
            this.classList.add('active');

            // Toggle views
            if (view === 'graph') {
                graphView.style.display = 'block';
                listView.style.display = 'none';
                // Re-render graph if needed (handles resize) — use kgEditData if in edit mode
                const kgDataToRender = _kgEditMode ? _kgEditData : _currentKGData;
                if (kgDataToRender && graphView.clientWidth > 0) {
                    renderKGGraphView(kgDataToRender, graphView);
                }
            } else {
                graphView.style.display = 'none';
                listView.style.display = 'block';
            }

            console.log('[KG] Switched to', view, 'view');
        });
    });
}

// ============================================================
// KG Edit Mode
// ============================================================

function setupKGEditMode() {
    const editToggleBtn = document.getElementById(_kgId('EditToggleBtn'));
    const editControls = document.getElementById(_kgId('EditControls'));
    const editActionControls = document.getElementById(_kgId('EditActionControls'));
    const confirmBtn = document.getElementById(_kgId('ConfirmEditBtn'));
    const cancelBtn = document.getElementById(_kgId('CancelEditBtn'));
    const connectBtn = document.getElementById(_kgId('ConnectBtn'));
    const addNodeBtn = document.getElementById(_kgId('AddNodeBtn'));

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

    document.getElementById(_kgId('EditToggleBtn')).addEventListener('click', enterKGEditMode);
    document.getElementById(_kgId('ConfirmEditBtn')).addEventListener('click', confirmKGEdit);
    document.getElementById(_kgId('CancelEditBtn')).addEventListener('click', cancelKGEdit);
    document.getElementById(_kgId('ConnectBtn')).addEventListener('click', toggleKGConnectMode);
    document.getElementById(_kgId('AddNodeBtn')).addEventListener('click', showAddNodePopover);
}

function enterKGEditMode() {
    if (!_currentKGData) return;
    _kgEditMode = true;
    _kgConnectMode = false;
    _kgConnectSourceId = null;
    _kgEditStats = { nodesAdded: 0, nodesDeleted: 0, nodesModified: 0, edgesAdded: 0, edgesDeleted: 0, edgesModified: 0 };

    // Deep clone current KG data as the working copy
    _kgEditData = JSON.parse(JSON.stringify(_currentKGData));

    // Update UI: show edit controls, hide edit toggle button
    document.getElementById(_kgId('EditToggleBtn')).style.display = 'none';
    document.getElementById(_kgId('EditControls')).style.display = 'flex';
    document.getElementById(_kgId('EditActionControls')).style.display = 'flex';

    // Add edit mode indicator to graph container
    const graphContainer = document.getElementById(_kgId('GraphView'));
    if (graphContainer) graphContainer.classList.add('kg-edit-active');

    // Re-render with edit mode enabled
    const graphView = document.getElementById(_kgId('GraphView'));
    renderKGGraphView(_kgEditData, graphView);

    console.log('[KG Edit] Edit mode entered');
}

function cancelKGEdit() {
    _kgEditMode = false;
    _kgConnectMode = false;
    _kgConnectSourceId = null;
    _kgEditData = null;

    // Hide edit controls, restore edit toggle button
    document.getElementById(_kgId('EditToggleBtn')).style.display = '';
    document.getElementById(_kgId('EditControls')).style.display = 'none';
    document.getElementById(_kgId('EditActionControls')).style.display = 'none';

    const graphContainer = document.getElementById(_kgId('GraphView'));
    if (graphContainer) graphContainer.classList.remove('kg-edit-active');

    // Deactivate connect mode button
    document.getElementById(_kgId('ConnectBtn')).classList.remove('active');

    // Re-render original data
    const graphView = document.getElementById(_kgId('GraphView'));
    renderKGGraphView(_currentKGData, graphView);
    closeAllKGPopovers();
    console.log('[KG Edit] Edit mode cancelled, original graph restored');
}

function closeAllKGPopovers() {
    const nodePopover = document.getElementById(_kgId('NodePopover'));
    const edgePopover = document.getElementById(_kgId('EdgePopover'));
    if (nodePopover) nodePopover.style.display = 'none';
    if (edgePopover) edgePopover.style.display = 'none';
}

function positionPopover(popoverEl, nodeX, nodeY, nodeR) {
    // Convert SVG coords to container-relative coords
    // nodeX/nodeY are SVG coordinates; we need to account for zoom/pan transform
    const graphContainer = document.getElementById(_kgId('GraphView'));
    const containerRect = graphContainer.getBoundingClientRect();
    const svgEl = graphContainer.querySelector('svg');
    if (!svgEl) return;

    // Get current zoom transform from D3
    const transform = d3.zoomTransform(svgEl);
    const screenX = transform.applyX(nodeX);
    const screenY = transform.applyY(nodeY);

    let left = screenX + nodeR + 8;
    let top = screenY - 40;

    // Issue #1: Enhanced boundary detection using actual popover dimensions
    // Show briefly off-screen to measure, then position correctly
    popoverEl.style.visibility = 'hidden';
    popoverEl.style.display = 'block';
    const popoverRect = popoverEl.getBoundingClientRect();
    const popoverWidth = popoverRect.width || 240;
    const popoverHeight = popoverRect.height || 220;
    popoverEl.style.visibility = '';
    popoverEl.style.display = 'none';

    const containerWidth = graphContainer.clientWidth;
    const containerHeight = graphContainer.clientHeight;

    // Try right side first, fall back to left
    if (left + popoverWidth > containerWidth) {
        left = screenX - nodeR - popoverWidth - 8;
    }
    // Vertical boundary
    if (top + popoverHeight > containerHeight) {
        top = containerHeight - popoverHeight - 10;
    }
    if (top < 0) top = 10;
    if (left < 0) left = 10;

    // Final clamp
    if (left + popoverWidth > containerWidth) left = containerWidth - popoverWidth - 10;
    if (top + popoverHeight > containerHeight) top = containerHeight - popoverHeight - 10;

    popoverEl.style.left = left + 'px';
    popoverEl.style.top = top + 'px';

    // Issue #1: Setup drag on popover header
    setupPopoverDrag(popoverEl);
}

// Issue #1: Make popover draggable by its header
function setupPopoverDrag(popoverEl) {
    const header = popoverEl.querySelector('.kg-popover-header');
    if (!header || header._dragSetup) return;
    header._dragSetup = true;
    header.style.cursor = 'move';

    let isDragging = false;
    let startX, startY, origLeft, origTop;

    header.addEventListener('mousedown', function(e) {
        if (e.target.classList.contains('kg-popover-close')) return;
        isDragging = true;
        startX = e.clientX;
        startY = e.clientY;
        origLeft = parseInt(popoverEl.style.left, 10) || 0;
        origTop = parseInt(popoverEl.style.top, 10) || 0;
        e.preventDefault();
        e.stopPropagation();
    });

    document.addEventListener('mousemove', function(e) {
        if (!isDragging) return;
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        popoverEl.style.left = (origLeft + dx) + 'px';
        popoverEl.style.top = (origTop + dy) + 'px';
    });

    document.addEventListener('mouseup', function() {
        isDragging = false;
    });
}

function showNodeEditPopover(event, entityId, entity, nodeX, nodeY, nodeR) {
    closeAllKGPopovers();

    const popover = document.getElementById(_kgId('NodePopover'));
    const nameInput = document.getElementById(_kgId('NodeNameInput'));
    const typeSelect = document.getElementById(_kgId('NodeTypeSelect'));
    const descInput = document.getElementById(_kgId('NodeDescInput'));
    const editingId = document.getElementById(_kgId('NodeEditingId'));

    // Populate form with current values
    nameInput.value = entity.name || '';
    typeSelect.value = entity.entity_type || 'concept';
    descInput.value = entity.description || '';
    editingId.value = entityId;

    // Restore delete button visibility (may be hidden from add-node flow)
    document.getElementById(_kgId('NodeDeleteBtn')).style.display = '';

    positionPopover(popover, nodeX, nodeY, nodeR);
    popover.style.display = 'block';

    // Wire save button (clone to remove stale listeners)
    const saveBtn = document.getElementById(_kgId('NodeSaveBtn'));
    const newSave = saveBtn.cloneNode(true);
    saveBtn.parentNode.replaceChild(newSave, saveBtn);
    document.getElementById(_kgId('NodeSaveBtn')).addEventListener('click', saveNodeEdit);

    const deleteBtn = document.getElementById(_kgId('NodeDeleteBtn'));
    const newDelete = deleteBtn.cloneNode(true);
    deleteBtn.parentNode.replaceChild(newDelete, deleteBtn);
    document.getElementById(_kgId('NodeDeleteBtn')).addEventListener('click', deleteCurrentNode);

    const closeBtn = document.getElementById(_kgId('NodePopoverClose'));
    const newClose = closeBtn.cloneNode(true);
    closeBtn.parentNode.replaceChild(newClose, closeBtn);
    document.getElementById(_kgId('NodePopoverClose')).addEventListener('click', closeAllKGPopovers);

    nameInput.focus();
    nameInput.select();
}

function saveNodeEdit() {
    const entityId = document.getElementById(_kgId('NodeEditingId')).value;
    const newName = document.getElementById(_kgId('NodeNameInput')).value.trim();
    const newType = document.getElementById(_kgId('NodeTypeSelect')).value;
    const newDesc = document.getElementById(_kgId('NodeDescInput')).value.trim();

    if (!newName) {
        document.getElementById(_kgId('NodeNameInput')).focus();
        return;
    }

    // Find and update entity in kgEditData
    const entity = _kgEditData.entities.find(e => e.entity_id === entityId);
    if (!entity) {
        console.error('[KG Edit] Entity not found for save:', entityId);
        return;
    }

    const wasModified = entity.name !== newName || entity.entity_type !== newType || (entity.description || '') !== newDesc;
    if (wasModified) {
        entity.name = newName;
        entity.entity_type = newType;
        entity.description = newDesc || undefined;
        _kgEditStats.nodesModified++;
        console.log('[KG Edit] Node modified:', entityId, newName);
    }

    closeAllKGPopovers();

    // Re-render with updated data
    const graphView = document.getElementById(_kgId('GraphView'));
    renderKGGraphView(_kgEditData, graphView);
}

function deleteCurrentNode() {
    const entityId = document.getElementById(_kgId('NodeEditingId')).value;

    // Remove entity
    const prevCount = _kgEditData.entities.length;
    _kgEditData.entities = _kgEditData.entities.filter(e => e.entity_id !== entityId);

    if (_kgEditData.entities.length < prevCount) {
        // Also remove all relationships involving this entity
        const prevRels = _kgEditData.relationships.length;
        _kgEditData.relationships = _kgEditData.relationships.filter(
            r => r.source_entity_id !== entityId && r.target_entity_id !== entityId
        );
        const removedRels = prevRels - _kgEditData.relationships.length;
        _kgEditStats.nodesDeleted++;
        _kgEditStats.edgesDeleted += removedRels;
        console.log('[KG Edit] Node deleted:', entityId, '+ removed', removedRels, 'relationships');
    }

    closeAllKGPopovers();

    const graphView = document.getElementById(_kgId('GraphView'));
    renderKGGraphView(_kgEditData, graphView);
}

function showAddNodePopover() {
    closeAllKGPopovers();

    const popover = document.getElementById(_kgId('NodePopover'));
    const nameInput = document.getElementById(_kgId('NodeNameInput'));
    const typeSelect = document.getElementById(_kgId('NodeTypeSelect'));
    const descInput = document.getElementById(_kgId('NodeDescInput'));
    const editingId = document.getElementById(_kgId('NodeEditingId'));

    // Clear form for new node
    nameInput.value = '';
    typeSelect.value = 'concept';
    descInput.value = '';
    editingId.value = '__new__'; // sentinel value

    // Position near top-left of graph container
    popover.style.left = '20px';
    popover.style.top = '40px';
    popover.style.display = 'block';

    // Swap Save button handler to createNewNode
    const saveBtn = document.getElementById(_kgId('NodeSaveBtn'));
    const newSave = saveBtn.cloneNode(true);
    saveBtn.parentNode.replaceChild(newSave, saveBtn);
    document.getElementById(_kgId('NodeSaveBtn')).addEventListener('click', createNewNode);

    // Hide delete button (can't delete a node that doesn't exist yet)
    document.getElementById(_kgId('NodeDeleteBtn')).style.display = 'none';

    const closeBtn = document.getElementById(_kgId('NodePopoverClose'));
    const newClose = closeBtn.cloneNode(true);
    closeBtn.parentNode.replaceChild(newClose, closeBtn);
    document.getElementById(_kgId('NodePopoverClose')).addEventListener('click', () => {
        document.getElementById(_kgId('NodeDeleteBtn')).style.display = '';
        closeAllKGPopovers();
    });

    nameInput.focus();
}

function createNewNode() {
    const newName = document.getElementById(_kgId('NodeNameInput')).value.trim();
    const newType = document.getElementById(_kgId('NodeTypeSelect')).value;
    const newDesc = document.getElementById(_kgId('NodeDescInput')).value.trim();

    if (!newName) {
        document.getElementById(_kgId('NodeNameInput')).focus();
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

    _kgEditData.entities.push(newEntity);
    _kgEditStats.nodesAdded++;

    // Restore delete button visibility for future node edits
    document.getElementById(_kgId('NodeDeleteBtn')).style.display = '';

    closeAllKGPopovers();

    const graphView = document.getElementById(_kgId('GraphView'));
    renderKGGraphView(_kgEditData, graphView);

    console.log('[KG Edit] New node created:', newId, newName);
}

function showEdgeEditPopover(event, linkData, pathEl) {
    closeAllKGPopovers();

    const popover = document.getElementById(_kgId('EdgePopover'));
    const edgeDesc = document.getElementById(_kgId('EdgeDesc'));
    const relationSelect = document.getElementById(_kgId('EdgeRelationType'));
    const customInput = document.getElementById(_kgId('EdgeRelationCustom'));
    const descInput = document.getElementById(_kgId('EdgeDescInput'));
    const editingId = document.getElementById(_kgId('EdgeEditingId'));

    // Build human-readable description
    const entityMap = {};
    _kgEditData.entities.forEach(e => { entityMap[e.entity_id] = e.name; });
    const sourceName = entityMap[linkData.source] || linkData.source;
    const targetName = entityMap[linkData.target] || linkData.target;
    edgeDesc.textContent = `${sourceName}  →  ${targetName}`;

    // Find the relationship in kgEditData
    const rel = _kgEditData.relationships.find(
        r => r.source_entity_id === linkData.source && r.target_entity_id === linkData.target
    );
    if (!rel) {
        console.error('[KG Edit] Relationship not found for edge click');
        return;
    }

    // Issue #2: Set select value or switch to custom
    const knownTypes = Object.keys(KG_RELATION_LABELS);
    const currentType = rel.relation_type || '';
    if (knownTypes.includes(currentType)) {
        relationSelect.value = currentType;
        customInput.style.display = 'none';
        customInput.value = '';
    } else {
        relationSelect.value = 'custom';
        customInput.style.display = '';
        customInput.value = currentType;
    }

    // Wire select change to toggle custom input
    const newSelect = relationSelect.cloneNode(true);
    relationSelect.parentNode.replaceChild(newSelect, relationSelect);
    document.getElementById(_kgId('EdgeRelationType')).addEventListener('change', function() {
        const ci = document.getElementById(_kgId('EdgeRelationCustom'));
        if (this.value === 'custom') {
            ci.style.display = '';
            ci.focus();
        } else {
            ci.style.display = 'none';
            ci.value = '';
        }
    });

    descInput.value = rel.description || '';
    editingId.value = rel.relationship_id || `${linkData.source}__${linkData.target}`;

    // Position near clicked edge midpoint — use mouse position with boundary checks
    const graphContainer = document.getElementById(_kgId('GraphView'));
    const containerRect = graphContainer.getBoundingClientRect();

    // Measure popover
    popover.style.visibility = 'hidden';
    popover.style.display = 'block';
    const popoverRect = popover.getBoundingClientRect();
    const popoverWidth = popoverRect.width || 240;
    const popoverHeight = popoverRect.height || 200;
    popover.style.visibility = '';
    popover.style.display = 'none';

    let left = event.clientX - containerRect.left + 10;
    let top = event.clientY - containerRect.top - 20;
    if (left + popoverWidth > graphContainer.clientWidth) left = graphContainer.clientWidth - popoverWidth - 10;
    if (top + popoverHeight > graphContainer.clientHeight) top = graphContainer.clientHeight - popoverHeight - 10;
    if (top < 0) top = 10;
    if (left < 0) left = 10;
    popover.style.left = left + 'px';
    popover.style.top = top + 'px';
    popover.style.display = 'block';

    // Setup drag on edge popover header
    setupPopoverDrag(popover);

    // Wire save/delete buttons
    const saveBtn = document.getElementById(_kgId('EdgeSaveBtn'));
    const newSave = saveBtn.cloneNode(true);
    saveBtn.parentNode.replaceChild(newSave, saveBtn);
    document.getElementById(_kgId('EdgeSaveBtn')).addEventListener('click', saveEdgeEdit);

    const deleteBtn = document.getElementById(_kgId('EdgeDeleteBtn'));
    const newDelete = deleteBtn.cloneNode(true);
    deleteBtn.parentNode.replaceChild(newDelete, deleteBtn);
    document.getElementById(_kgId('EdgeDeleteBtn')).addEventListener('click', deleteCurrentEdge);

    const closeBtn = document.getElementById(_kgId('EdgePopoverClose'));
    const newClose = closeBtn.cloneNode(true);
    closeBtn.parentNode.replaceChild(newClose, closeBtn);
    document.getElementById(_kgId('EdgePopoverClose')).addEventListener('click', closeAllKGPopovers);
}

function saveEdgeEdit() {
    const editingId = document.getElementById(_kgId('EdgeEditingId')).value;
    // Issue #2: Read from select or custom input
    const selectEl = document.getElementById(_kgId('EdgeRelationType'));
    const customEl = document.getElementById(_kgId('EdgeRelationCustom'));
    const newLabel = selectEl.value === 'custom' ? customEl.value.trim() : selectEl.value;
    const newDesc = document.getElementById(_kgId('EdgeDescInput')).value.trim();

    // Find rel by relationship_id OR by source__target composite key
    let rel = _kgEditData.relationships.find(r => r.relationship_id === editingId);
    if (!rel) {
        const [src, tgt] = editingId.split('__');
        rel = _kgEditData.relationships.find(
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
        _kgEditStats.edgesModified++;
        console.log('[KG Edit] Edge modified:', editingId);
    }

    closeAllKGPopovers();
    const graphView = document.getElementById(_kgId('GraphView'));
    renderKGGraphView(_kgEditData, graphView);
}

function deleteCurrentEdge() {
    const editingId = document.getElementById(_kgId('EdgeEditingId')).value;

    const prevCount = _kgEditData.relationships.length;
    _kgEditData.relationships = _kgEditData.relationships.filter(r => {
        const compositeKey = `${r.source_entity_id}__${r.target_entity_id}`;
        return r.relationship_id !== editingId && compositeKey !== editingId;
    });

    if (_kgEditData.relationships.length < prevCount) {
        _kgEditStats.edgesDeleted++;
        console.log('[KG Edit] Edge deleted:', editingId);
    }

    closeAllKGPopovers();
    const graphView = document.getElementById(_kgId('GraphView'));
    renderKGGraphView(_kgEditData, graphView);
}

function toggleKGConnectMode() {
    _kgConnectMode = !_kgConnectMode;
    _kgConnectSourceId = null;

    const connectBtn = document.getElementById(_kgId('ConnectBtn'));
    if (_kgConnectMode) {
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
    if (!_kgConnectSourceId) {
        // First click: select source node
        _kgConnectSourceId = entityId;

        // Highlight source node
        d3.selectAll('.kg-node').classed('kg-connect-source', function(d) {
            return d && d.entity && d.entity.entity_id === entityId;
        });

        const connectBtn = document.getElementById(_kgId('ConnectBtn'));
        connectBtn.textContent = `連線中（${entity.name} → 點目標）`;
        console.log('[KG Edit] Connect source selected:', entityId, entity.name);
    } else {
        // Second click: create edge from source to this node
        const targetId = entityId;

        if (targetId === _kgConnectSourceId) {
            // Clicked same node — deselect
            _kgConnectSourceId = null;
            d3.selectAll('.kg-node').classed('kg-connect-source', false);
            document.getElementById(_kgId('ConnectBtn')).textContent = '連線中（點節點 A）';
            return;
        }

        // Check if this edge already exists
        const exists = _kgEditData.relationships.some(
            r => r.source_entity_id === _kgConnectSourceId && r.target_entity_id === targetId
        );

        if (exists) {
            console.warn('[KG Edit] Edge already exists:', _kgConnectSourceId, '->', targetId);
            // Still reset connect state
        } else {
            // Create new relationship
            const newRel = {
                relationship_id: 'edit_rel_' + Date.now(),
                source_entity_id: _kgConnectSourceId,
                target_entity_id: targetId,
                relation_type: '',       // user can edit label later
                confidence: 'medium',
                description: undefined
            };
            _kgEditData.relationships.push(newRel);
            _kgEditStats.edgesAdded++;
            console.log('[KG Edit] New edge created:', _kgConnectSourceId, '->', targetId);
        }

        // Reset connect state
        _kgConnectSourceId = null;
        d3.selectAll('.kg-node').classed('kg-connect-source', false);
        document.getElementById(_kgId('ConnectBtn')).textContent = '連線中（點節點 A）';

        // Re-render
        const graphView = document.getElementById(_kgId('GraphView'));
        renderKGGraphView(_kgEditData, graphView);
    }
}

function serializeKGEdit() {
    if (!_kgEditData) return null;

    const payload = {
        schema_version: '1.0',
        edit_timestamp: new Date().toISOString(),
        entities: _kgEditData.entities.map(e => ({
            entity_id: e.entity_id,
            name: e.name,
            entity_type: e.entity_type,
            confidence: e.confidence || 'medium',
            description: e.description || undefined
        })),
        relationships: _kgEditData.relationships.map(r => ({
            relationship_id: r.relationship_id,
            source_entity_id: r.source_entity_id,
            target_entity_id: r.target_entity_id,
            relation_type: r.relation_type || '',
            confidence: r.confidence || 'medium',
            description: r.description || undefined
        })),
        edit_summary: {
            nodes_added: _kgEditStats.nodesAdded,
            nodes_deleted: _kgEditStats.nodesDeleted,
            nodes_modified: _kgEditStats.nodesModified,
            edges_added: _kgEditStats.edgesAdded,
            edges_deleted: _kgEditStats.edgesDeleted,
            edges_modified: _kgEditStats.edgesModified
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

async function confirmKGEdit() {
    if (!_kgEditData) {
        console.error('[KG Edit] No edit data to confirm');
        return;
    }

    // Serialize and build prompt
    const serialized = serializeKGEdit();
    if (!serialized) {
        console.error('[KG Edit] Serialization failed');
        return;
    }

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

    const editPayload = serialized;
    const kgEditsJson = JSON.stringify(editPayload);
    console.log('[KG Edit] Serialized edits:', JSON.stringify(editPayload, null, 2));

    // Validate we have the required query_id
    const queryId = getCurrentResearchQueryId();
    const originalQuery = getResearchReport()?.query || getConversationHistory()[getConversationHistory().length - 1] || '';

    if (!queryId) {
        console.error('[KG Edit] No getCurrentResearchQueryId() available');
        alert('找不到原始研究的 query_id，無法重新分析。請重新進行深度研究後再試。');
        return;
    }

    if (!originalQuery) {
        console.error('[KG Edit] No original query text available');
        alert('找不到原始查詢文字，無法重新分析。');
        return;
    }

    console.log('[KG Edit] Confirm rerun: query_id=%s, query=%s, changes=%d', queryId, originalQuery, totalChanges);

    // Exit edit mode immediately (don't wait for API)
    _kgEditMode = false;
    _kgConnectMode = false;
    _kgConnectSourceId = null;

    // Update currentKGData to reflect confirmed edits
    _currentKGData = JSON.parse(JSON.stringify(_kgEditData));
    _kgEditData = null;

    // Update UI: exit edit mode appearance
    document.getElementById(_kgId('EditToggleBtn')).style.display = '';
    document.getElementById(_kgId('EditControls')).style.display = 'none';
    document.getElementById(_kgId('EditActionControls')).style.display = 'none';

    const graphContainer = document.getElementById(_kgId('GraphView'));
    if (graphContainer) graphContainer.classList.remove('kg-edit-active');
    document.getElementById(_kgId('ConnectBtn')).classList.remove('active');

    closeAllKGPopovers();

    // Re-render updated graph (now with confirmed edits as the live graph)
    const graphView = document.getElementById(_kgId('GraphView'));
    renderKGGraphView(_currentKGData, graphView);

    // Issue #4: Show loading indicator with spinner in research panel
    const researchViewEl = document.getElementById('researchView');
    if (researchViewEl) {
        const loadingIndicator = document.createElement('div');
        loadingIndicator.id = 'kgRerunLoading';
        loadingIndicator.className = 'kg-rerun-loading';
        loadingIndicator.innerHTML = `
            <div class="kg-rerun-spinner"></div>
            <div class="kg-rerun-status">
                <strong>已套用 ${totalChanges} 項知識圖譜修改</strong>
                <span id="kgRerunStatusText">正在重新生成研究報告...</span>
            </div>
        `;
        researchViewEl.prepend(loadingIndicator);
        loadingIndicator.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    // Switch to research tab so user sees progress
    const researchTab = document.querySelector('.tab[data-view="research"]');
    if (researchTab) researchTab.click();

    // Call rerun API with fetch + SSE streaming
    try {
        // P1 E2E fix (2026-05-26): route through authenticatedFetch for 401→refresh→retry.
        const rerunResponse = await window.authManager.authenticatedFetch('/api/research/rerun', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'text/event-stream'
            },
            body: JSON.stringify({
                query_id: queryId,
                kg_edits: kgEditsJson,
                query: originalQuery
            })
        });

        // Handle error responses (JSON, not SSE)
        if (!rerunResponse.ok) {
            const loadingEl = document.getElementById(_kgId('RerunLoading'));
            if (loadingEl) loadingEl.remove();

            if (rerunResponse.status === 401) {
                // Token expired + refresh failed (login modal already shown). Friendly message.
                showDRError('登入已過期，請重新登入後再試。');
                console.error('[KG Edit Rerun] 401 after refresh — auth expired');
                return;
            }

            let errorMsg = '重新分析失敗';
            try {
                const errorData = await rerunResponse.json();
                if (errorData.message) errorMsg = errorData.message;
            } catch (e) { /* ignore parse errors */ }

            if (rerunResponse.status === 400) {
                // cache_miss or validation error
                showDRError('原始研究結果已過期，請重新搜尋後再編輯知識圖譜。');
            } else if (rerunResponse.status === 429) {
                showDRError('系統忙碌中，請稍後再試。');
            } else if (rerunResponse.status === 501) {
                showDRError('此功能尚未啟用。');
            } else if (rerunResponse.status === 503) {
                showDRError('深度研究功能暫時關閉。');
            } else {
                showDRError(errorMsg);
            }
            console.error('[KG Edit Rerun] API error:', rerunResponse.status, errorMsg);
            return;
        }

        // Stream is OK — read SSE from ReadableStream (same pattern as DR handler)
        const rerunReader = rerunResponse.body.getReader();
        const rerunDecoder = new TextDecoder();
        let rerunBuffer = '';
        let rerunFullReport = '';

        while (true) {
            const { done, value } = await rerunReader.read();
            if (done) break;

            rerunBuffer += rerunDecoder.decode(value, { stream: true });

            // Process complete SSE messages (separated by double newlines)
            const messages = rerunBuffer.split('\n\n');
            rerunBuffer = messages.pop(); // Keep incomplete message in buffer

            for (const message of messages) {
                if (!message.trim()) continue;

                const lines = message.split('\n');
                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    let data;
                    try {
                        data = JSON.parse(line.slice(6));
                    } catch (e) {
                        console.error('[KG Edit Rerun] Failed to parse SSE data:', e);
                        continue;
                    }
                    console.log('[KG Edit Rerun] SSE:', data);

                    if (data.message_type === 'begin-nlweb-response') {
                        // Rerun started
                        if (data.conversation_id) {
                            setCurrentConversationId(data.conversation_id);
                        }
                        console.log('[KG Edit Rerun] Stream started, is_rerun:', data.is_rerun);
                    } else if (data.message_type === 'intermediate_result') {
                        // Issue #5: Update loading indicator with SSE progress text
                        const statusTextEl = document.getElementById(_kgId('RerunStatusText'));
                        if (statusTextEl && data.user_message) {
                            statusTextEl.textContent = data.user_message;
                        } else if (statusTextEl && data.stage) {
                            // Fallback: use stage name
                            statusTextEl.textContent = data.stage;
                        }
                    } else if (data.message_type === 'research_phase') {
                        console.log('[KG Edit Rerun] Phase:', data.phase, data.status);
                    } else if (data.message_type === 'final_result') {
                        // Rerun complete — update research panel
                        rerunFullReport = data.final_report || '';

                        // Remove loading indicator
                        const loadingEl = document.getElementById(_kgId('RerunLoading'));
                        if (loadingEl) loadingEl.remove();

                        // Display updated results using existing function
                        displayDeepResearchResults(rerunFullReport, data, originalQuery);

                        // Issue #6: Add rerun version badge at top of research report
                        const rerunResearchView = document.getElementById('researchView');
                        if (rerunResearchView) {
                            const badge = document.createElement('div');
                            badge.className = 'kg-rerun-version-badge';
                            badge.innerHTML = '<span class="emoji-bw">&#x1f504;</span> 此為根據知識圖譜修改重新生成的版本';
                            rerunResearchView.prepend(badge);
                        }

                        // Update session history
                        getSessionHistory().push({
                            query: originalQuery,
                            data: data,
                            timestamp: Date.now(),
                            isDeepResearch: true,
                            isRerun: true,
                            researchReport: getResearchReport() ? { ...getResearchReport() } : null,
                            argumentGraph: getArgumentGraph() ? [...getArgumentGraph()] : null,
                            chainAnalysis: getChainAnalysis() ? { ...getChainAnalysis() } : null,
                            knowledgeGraph: _currentKGData ? JSON.parse(JSON.stringify(_currentKGData)) : null,
                            researchQueryId: getCurrentResearchQueryId()
                        });

                        // renderConversationHistory + saveCurrentSession still live in
                        // news-search.js (KEEP-in-place per CEO #5 until commit 23+).
                        // Reach via window bridge — sweep target commit 25.
                        if (typeof window.renderConversationHistory === 'function') {
                            window.renderConversationHistory();
                        }
                        if (typeof window.saveCurrentSession === 'function') {
                            // 2026-07-13 regression fix: displayDeepResearchResults (:1889) saved
                            // and cleared the dirty flag BEFORE the rerun sessionHistory entry above
                            // was pushed — re-mark or the dirty-gate (session-coordinator.js:70)
                            // silently swallows this save and the rerun entry never persists.
                            markSessionDirty();
                            window.saveCurrentSession();
                        }

                        console.log('[KG Edit Rerun] Complete. Report length:', rerunFullReport.length);
                        return;
                    } else if (data.message_type === 'complete') {
                        console.log('[KG Edit Rerun] Stream complete');
                        const loadingEl = document.getElementById(_kgId('RerunLoading'));
                        if (loadingEl) loadingEl.remove();
                        return;
                    } else if (data.message_type === 'error') {
                        console.error('[KG Edit Rerun] SSE error:', data.error);
                        const loadingEl = document.getElementById(_kgId('RerunLoading'));
                        if (loadingEl) loadingEl.remove();
                        showDRError(data.error || 'KG 重新分析發生錯誤');
                        return;
                    }
                }
            }
        }

        // Stream ended without final_result
        const loadingEl = document.getElementById(_kgId('RerunLoading'));
        if (loadingEl) loadingEl.remove();

    } catch (error) {
        console.error('[KG Edit Rerun] Error:', error);
        const loadingEl = document.getElementById(_kgId('RerunLoading'));
        if (loadingEl) loadingEl.remove();
        showDRError('KG 重新分析發生錯誤：' + (error.message || '未知錯誤'));
    }
}

    // ---- instance public API ----
    return {
        displayKnowledgeGraph,
        getCurrentKGData,
        getKGEditMode,
        resetKGState,
    };
}

// ============================================================================
// Two module-load singletons: DR ('kg') + LR ('lrKG'). 各自獨立 state。
// 在 module load time 建立，各自 idempotent；prefix 在 bind time 即固定。
// ============================================================================
const kgDR = createKGInstance('kg');
const kgLR = createKGInstance('lrKG');

// ============================================================================
// Public API (same names as before; DR is the default instance).
// LR caller 傳 { containerPrefix: 'lrKG' } → route 到 LR instance。
// DR caller（無 options / 'kg'）→ DR instance（zero regression）。
// 選完 instance 即呼叫該 instance 自己的方法，其 handler 永遠操作該 instance
// 固定 prefix 與隔離 state —— 不存在 global 可被污染。
// ============================================================================
export function displayKnowledgeGraph(kg, options = {}) {
    const inst = options.containerPrefix === 'lrKG' ? kgLR : kgDR;
    return inst.displayKnowledgeGraph(kg);
}
// session save / restore / reset 等外部消費者一律對 DR instance（verified: 皆 DR-specific）
export function getCurrentKGData() { return kgDR.getCurrentKGData(); }
export function getKGEditMode() { return kgDR.getKGEditMode(); }
export function resetKGState(prefix) {
    // DR caller (news-search.js resetConversation) no-arg → DR。保留 prefix 形參相容。
    const inst = prefix === 'lrKG' ? kgLR : kgDR;
    return inst.resetKGState();
}
