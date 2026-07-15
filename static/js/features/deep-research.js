// static/js/features/deep-research.js
//
// D-1 Module Header — Deep Research Owner (NEW module — commit 15, Phase 8)
//   Owned state:
//     - _currentResearchQueryId (string|null — backend query_id captured from DR SSE
//       begin-nlweb-response envelope; used by KG edit rerun to /api/deep_research_rerun
//       endpoint; also serialized into sessionHistory entries by saveCurrentSession
//       and cleared by resetConversation / deleteSavedSession)
//
//   Functions migrated (24):
//     DR pipeline entry:
//       - performDeepResearch (main DR SSE pipeline; was news-search.js:2774)
//     DR display:
//       - displayDeepResearchResults (final report render; was 3939)
//       - renderResearchReportToView (session restore render; was 2519)
//       - showDRError (inline error display; was 2746)
//       - updateReasoningProgress (DR progress log display; was 2558)
//     Citation / collapsible helpers:
//       - addCitationLinks (was 3087)
//       - generateCitationReferenceList (was 3122)
//       - bindCitationReferenceToggles (was 3184)
//       - addCollapsibleSections (was 4065)
//       - bindCollapsibleHandlers (was 4123)
//       - addToggleAllToolbar (was 4147)
//     Clarification UI:
//       - addClarificationMessage (was 6889)
//       - attachClarificationListeners (was 7020)
//       - submitClarification (was 7167)
//     Reasoning chain (9 functions):
//       - displayReasoningChainInContainer (was 4182)
//       - displayReasoningChain (was 5947)
//       - createReasoningChainContainer (was 6019)
//       - createLogicInconsistencyWarning (was 6082)
//       - createCycleWarning (was 6103)
//       - createCriticalNodesAlert (was 6122)
//       - renderArgumentNode (was 6155)
//       - setupHoverInteractions (was 6246)
//       - inferScore (was 6293)
//       - formatReasoningForVerification (was 6301)
//
// D-3 Cross-Module Communication:
//   Static imports:
//     - features/search.js — escapeHTML, setProcessingState, cancelAllActiveRequests,
//       getCurrentSessionId-related (via utils/analytics), inflight DR handle setters,
//       pushConversationHistory, setCurrentConversationId, getCurrentConversationId
//     - features/research.js — setResearchReport, getResearchReport, setArgumentGraph,
//       getArgumentGraph, setChainAnalysis, getChainAnalysis
//     - features/sessions-list.js — getSessionHistory (DR push completion)
//     - features/sharing.js — setShareContentOverride (reasoning chain → share)
//     - features/source-filters.js — getSelectedSitesParam, getIncludePrivateSources
//     - features/session-manager.js — markSessionDirty
//     - features/mode.js — getCurrentMode (LR mode flag for DR URL)
//     - utils/analytics.js — getCurrentSessionId
//
//   Window bridges accessed (KEEP-in-place owners — sweep commits 17/18/19):
//     - window.displayKnowledgeGraph (KG module — commit 17 batch 6'')
//     - window.getCurrentUserId (auth-ui — KEEP-in-place per CEO #5)
//     - window.saveCurrentSession (KEEP-in-place per CEO #5)
//     - window.renderConversationHistory (sessions-list UI — commit 18 batch 6'')
//     - window.__getCurrentKGData (NEW commit 15 bridge — DR completion serializes
//       currentKGData into sessionHistory entry; KG module owns currentKGData until
//       commit 17 migrates it)
//
// D-13 Compliance:
//   No top-level side effects. Pure declarations + exports.

import {
    escapeHTML,
    setProcessingState,
    cancelAllActiveRequests,
    pushConversationHistory,
    setCurrentConversationId, getCurrentConversationId,
    setCurrentDeepResearchAbortController, getCurrentDeepResearchAbortController
} from './search.js?v=20260705c';
import {
    setResearchReport, getResearchReport,
    setArgumentGraph, getArgumentGraph,
    setChainAnalysis, getChainAnalysis
} from './research.js';
import { getSessionHistory, getCurrentLoadedSessionId, getSavedSessions } from './sessions-list.js';
import { setShareContentOverride } from './sharing.js';
import { getSelectedSitesParam, getIncludePrivateSources } from './source-filters.js';
import { markSessionDirty } from './session-manager.js';
import { getCurrentMode } from './mode.js';
import { getCurrentSessionId } from '../utils/analytics.js';
import { getConversationHistory } from './search.js?v=20260705c';

// ============================================================================
// Owned state (was: news-search.js line 1676)
// ============================================================================
let _currentResearchQueryId = null;

export function getCurrentResearchQueryId() { return _currentResearchQueryId; }
export function setCurrentResearchQueryId(id) { _currentResearchQueryId = id; }
export function clearCurrentResearchQueryId() { _currentResearchQueryId = null; }

// ============================================================================
// Citation helpers (Stage 5: URN + private:// + standard URL handling)
// ============================================================================

// Helper function to convert citation numbers [1] to clickable links
export function addCitationLinks(htmlContent, sources) {
    if (!sources || sources.length === 0) {
        return htmlContent;
    }

    // Replace [1], [2], etc. with clickable links
    return htmlContent.replace(/\[(\d+)\]/g, (match, num) => {
        const index = parseInt(num) - 1;
        if (index >= 0 && index < sources.length) {
            const url = sources[index];
            if (url) {
                // Stage 5: URN (LLM Knowledge source)
                if (url.startsWith('urn:llm:knowledge:')) {
                    const topic = url.replace('urn:llm:knowledge:', '');
                    return `<span class="citation-urn" title="讀豹背景知識：${topic}">[${num}]<sup>讀豹</sup></span>`;
                }
                // Bug #13: private:// (user-uploaded documents)
                if (url.startsWith('private://')) {
                    return `<span class="citation-private" title="私人文件來源">[${num}]<sup>\u{1F4C1}</sup></span>`;
                }
                // Normal URL
                return `<a href="${url}" target="_blank" class="citation-link" title="來源 ${num}">[${num}]</a>`;
            }
        }
        // Bug #25 Plan C: Out-of-range citation
        return `<span class="citation-no-link" title="來源暫無連結">[${num}]</span>`;
    });
}

// Generate a citation reference list to append at the end of the report
export function generateCitationReferenceList(sources) {
    if (!sources || sources.length === 0) {
        return '';
    }

    const validSources = sources.map((url, index) => ({
        index: index + 1,
        url: url || ''
    })).filter(item => item.url && item.url.trim() !== '');

    if (validSources.length === 0) {
        return '';
    }

    let html = '<div class="citation-reference-section">';
    html += `<button class="citation-reference-toggle">
        <span class="citation-toggle-icon">▶</span>
        <span>參考資料來源 (${validSources.length})</span>
    </button>`;
    html += '<div class="citation-reference-list">';

    validSources.forEach(item => {
        const url = item.url;
        let sourceType = '新聞';
        let isClickable = true;

        if (url.startsWith('urn:llm:knowledge:')) {
            sourceType = '讀豹背景知識';
            isClickable = false;
        } else if (url.startsWith('private://')) {
            sourceType = '私人文件';
            isClickable = false;
        }

        if (isClickable) {
            html += `<div class="citation-reference-item">
                <span class="citation-reference-number">[${item.index}]</span>
                <a href="${escapeHTML(url)}" target="_blank" class="citation-reference-link">
                    ${escapeHTML(url)}
                </a>
            </div>`;
        } else {
            const displayText = url.startsWith('urn:llm:knowledge:')
                ? url.replace('urn:llm:knowledge:', '')
                : url.replace('private://', '');
            html += `<div class="citation-reference-item">
                <span class="citation-reference-number">[${item.index}]</span>
                <span class="citation-reference-text">
                    <span class="citation-reference-type">${sourceType}</span>
                    ${escapeHTML(displayText)}
                </span>
            </div>`;
        }
    });

    html += '</div></div>';
    return html;
}

// Bind click handlers for citation reference toggles (CSP-safe, no inline onclick)
// Bug fix (P2 E2E finding): sync arrow icon textContent on toggle to mirror
// bindCollapsibleHandlers behavior. CSS transform rotate(90deg) on .expanded is
// present but unreliable across some browsers / zoom levels — explicit ▶↔▼
// textContent update is the canonical approach (matches bindCollapsibleHandlers).
export function bindCitationReferenceToggles(container) {
    if (!container) return;
    container.querySelectorAll('.citation-reference-toggle').forEach(btn => {
        btn.addEventListener('click', function() {
            const section = this.parentElement;
            section.classList.toggle('expanded');
            const icon = this.querySelector('.citation-toggle-icon');
            if (icon) icon.textContent = section.classList.contains('expanded') ? '▼' : '▶';
        });
    });
}

// ============================================================================
// Collapsible section helpers
// ============================================================================

export function addCollapsibleSections(html) {
    const tempDiv = document.createElement('div');
    tempDiv.innerHTML = html;

    const h2Elements = tempDiv.querySelectorAll('h2');
    h2Elements.forEach((h2, index) => {
        const content = document.createElement('div');
        content.className = 'research-section-content';

        let sibling = h2.nextElementSibling;
        while (sibling && sibling.tagName !== 'H2') {
            const next = sibling.nextElementSibling;
            content.appendChild(sibling);
            sibling = next;
        }

        // Skip empty "Finding X" sections with only "完整報告" text
        const titleText = h2.textContent.trim();
        const contentText = content.textContent.trim();
        const isFindingSection = /^Finding\s*\d+/i.test(titleText);
        const isEmptyContent = contentText === '完整報告' || contentText === '' || contentText.length < 20;

        if (isFindingSection && isEmptyContent) {
            h2.remove();
            return;
        }

        const section = document.createElement('div');
        section.className = 'research-section';
        section.setAttribute('data-section-id', `section-${index}`);

        const header = document.createElement('div');
        header.className = 'research-section-header';
        header.innerHTML = `
            <span class="collapse-icon">▼</span>
            <span class="section-title">${h2.innerHTML}</span>
        `;

        section.appendChild(header);
        section.appendChild(content);

        h2.parentNode.replaceChild(section, h2);
    });

    return tempDiv.innerHTML;
}

export function bindCollapsibleHandlers(container) {
    container.querySelectorAll('.research-section-header').forEach(header => {
        header.addEventListener('click', () => {
            const section = header.closest('.research-section');
            const icon = header.querySelector('.collapse-icon');
            const content = section.querySelector('.research-section-content');

            section.classList.toggle('collapsed');
            if (section.classList.contains('collapsed')) {
                icon.textContent = '▶';
                content.style.maxHeight = '0';
                content.style.overflow = 'hidden';
            } else {
                icon.textContent = '▼';
                content.style.maxHeight = '';
                content.style.overflow = '';
            }
        });
    });
}

export function addToggleAllToolbar(reportContainer) {
    const toolbar = document.createElement('div');
    toolbar.className = 'research-toggle-all-toolbar';

    let allCollapsed = false;
    const toggleBtn = document.createElement('button');
    toggleBtn.className = 'btn-toggle-all';
    toggleBtn.textContent = '全部折疊';

    toggleBtn.addEventListener('click', () => {
        allCollapsed = !allCollapsed;
        reportContainer.querySelectorAll('.research-section').forEach(section => {
            const icon = section.querySelector('.collapse-icon');
            const content = section.querySelector('.research-section-content');
            if (allCollapsed) {
                section.classList.add('collapsed');
                if (icon) icon.textContent = '▶';
                if (content) { content.style.maxHeight = '0'; content.style.overflow = 'hidden'; }
            } else {
                section.classList.remove('collapsed');
                if (icon) icon.textContent = '▼';
                if (content) { content.style.maxHeight = ''; content.style.overflow = ''; }
            }
        });
        toggleBtn.textContent = allCollapsed ? '全部展開' : '全部折疊';
        toggleBtn.classList.toggle('active', allCollapsed);
    });

    toolbar.appendChild(toggleBtn);
    reportContainer.insertBefore(toolbar, reportContainer.firstChild);
}

// ============================================================================
// Reasoning chain rendering (9 functions)
// ============================================================================

export function displayReasoningChainInContainer(argumentGraph, chainAnalysis, targetContainer) {
    if (!argumentGraph || argumentGraph.length === 0) {
        console.log('[Reasoning Chain] No argument graph data for container');
        return;
    }

    setArgumentGraph(argumentGraph);
    setChainAnalysis(chainAnalysis);

    console.log('[Reasoning Chain] Rendering', argumentGraph.length, 'nodes in container');

    const nodeMap = {};
    argumentGraph.forEach(node => {
        nodeMap[node.node_id] = node;
    });

    let orderedNodes = argumentGraph;
    if (chainAnalysis?.topological_order && chainAnalysis.topological_order.length > 0) {
        orderedNodes = chainAnalysis.topological_order
            .map(id => nodeMap[id])
            .filter(node => node !== undefined);
    }

    const container = createReasoningChainContainer(orderedNodes, chainAnalysis);

    if (chainAnalysis?.logic_inconsistencies > 0) {
        const warning = createLogicInconsistencyWarning(chainAnalysis.logic_inconsistencies);
        container.querySelector('.reasoning-chain-content').prepend(warning);
    }

    if (chainAnalysis?.has_cycles) {
        const cycleAlert = createCycleWarning(chainAnalysis.cycle_details);
        container.querySelector('.reasoning-chain-content').prepend(cycleAlert);
    }

    if (chainAnalysis?.critical_nodes?.length > 0) {
        const alert = createCriticalNodesAlert(chainAnalysis.critical_nodes, nodeMap);
        container.querySelector('.reasoning-chain-content').prepend(alert);
    }

    orderedNodes.forEach((node, i) => {
        const nodeEl = renderArgumentNode(node, i + 1, nodeMap, chainAnalysis);
        container.querySelector('.reasoning-chain-content').appendChild(nodeEl);
    });

    setupHoverInteractions(container, nodeMap);

    targetContainer.insertBefore(container, targetContainer.firstChild);
}

export function displayReasoningChain(argumentGraph, chainAnalysis) {
    console.log('[Reasoning Chain] Called with:', argumentGraph, chainAnalysis);

    if (!argumentGraph || argumentGraph.length === 0) {
        console.log('[Reasoning Chain] No argument graph data, skipping render');
        return;
    }

    setArgumentGraph(argumentGraph);
    setChainAnalysis(chainAnalysis);

    console.log('[Reasoning Chain] Rendering', argumentGraph.length, 'nodes');

    const nodeMap = {};
    argumentGraph.forEach(node => {
        nodeMap[node.node_id] = node;
    });

    let orderedNodes = argumentGraph;
    if (chainAnalysis?.topological_order && chainAnalysis.topological_order.length > 0) {
        orderedNodes = chainAnalysis.topological_order
            .map(id => nodeMap[id])
            .filter(node => node !== undefined);
        console.log('[Reasoning Chain] Using topological order for rendering');
    }

    const container = createReasoningChainContainer(orderedNodes, chainAnalysis);

    if (chainAnalysis?.logic_inconsistencies > 0) {
        const warning = createLogicInconsistencyWarning(chainAnalysis.logic_inconsistencies);
        container.querySelector('.reasoning-chain-content').prepend(warning);
    }

    if (chainAnalysis?.has_cycles) {
        const cycleAlert = createCycleWarning(chainAnalysis.cycle_details);
        container.querySelector('.reasoning-chain-content').prepend(cycleAlert);
    }

    if (chainAnalysis?.critical_nodes?.length > 0) {
        const alert = createCriticalNodesAlert(chainAnalysis.critical_nodes, nodeMap);
        container.querySelector('.reasoning-chain-content').prepend(alert);
    }

    orderedNodes.forEach((node, i) => {
        const nodeEl = renderArgumentNode(node, i + 1, nodeMap, chainAnalysis);
        container.querySelector('.reasoning-chain-content').appendChild(nodeEl);
    });

    setupHoverInteractions(container, nodeMap);

    const listView = document.getElementById('listView');
    if (!listView) return;
    const reportContainer = listView.querySelector('.deep-research-report');
    if (reportContainer) {
        listView.insertBefore(container, reportContainer);
    } else {
        listView.appendChild(container);
    }
}

export function createReasoningChainContainer(nodes, chainAnalysis) {
    const container = document.createElement('div');
    container.className = 'reasoning-chain-container';
    container.style.cssText = `
        background: #FFFDF5;
        border-left: 4px solid #FDCB6E;
        border-radius: 8px;
        padding: 20px;
        margin-bottom: 24px;
        width: 100%;
        box-sizing: border-box;
    `;

    const header = document.createElement('div');
    header.style.cssText = 'display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; cursor: pointer;';
    header.innerHTML = `
        <div style="font-size: 18px; font-weight: 700; color: #2D3436;">
            <img src="/static/images/icon-brain.svg" alt="推論" class="inline-icon"> 推論過程
            <span style="color: #636e72; font-size: 14px; font-weight: 400;">
                (${nodes.length} 個推論步驟${chainAnalysis?.max_depth !== undefined ? `, 深度 ${chainAnalysis.max_depth}` : ''})
            </span>
        </div>
        <div style="display: flex; gap: 8px; align-items: center;">
            <button class="btn-share-reasoning" style="background: white; border: 1px solid #B2BEC3; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 13px; transition: all 0.2s;">
                <img src="/static/images/icon-link.svg" alt="驗證連結" class="inline-icon"> 給其他 AI 驗證
            </button>
            <button class="btn-toggle-chain" style="background: white; border: 1px solid #B2BEC3; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 13px;">
                展開
            </button>
        </div>
    `;

    const content = document.createElement('div');
    content.className = 'reasoning-chain-content';
    content.style.display = 'none';

    // Toggle functionality
    const toggleBtn = header.querySelector('.btn-toggle-chain');
    header.addEventListener('click', (e) => {
        if (e.target.closest('.btn-share-reasoning')) return;
        const isHidden = content.style.display === 'none';
        content.style.display = isHidden ? 'block' : 'none';
        toggleBtn.textContent = isHidden ? '收起' : '展開';
    });

    // Share reasoning button
    const shareBtn = header.querySelector('.btn-share-reasoning');
    shareBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        setShareContentOverride(formatReasoningForVerification());
        const modalOverlayEl = document.getElementById('modalOverlay');
        if (modalOverlayEl) modalOverlayEl.classList.add('active');
    });

    container.appendChild(header);
    container.appendChild(content);

    return container;
}

export function createLogicInconsistencyWarning(count) {
    const alert = document.createElement('div');
    alert.style.cssText = `
        background: #FFEAA7;
        border-left: 4px solid #FDCB6E;
        padding: 12px 16px;
        border-radius: 6px;
        margin-bottom: 16px;
    `;
    alert.innerHTML = `
        <div style="font-weight: 700; color: #2D3436; margin-bottom: 4px;"><img src="/static/images/icon-warning.svg" alt="警告" class="inline-icon"> 邏輯一致性問題</div>
        <div style="color: #2D3436; font-size: 13px;">
            偵測到 ${count} 個推論步驟的信心度可能高於其前提（邏輯膨脹）。請檢視帶有 <img src="/static/images/icon-warning.svg" alt="警告" class="inline-icon"> 標記的推論步驟。
        </div>
    `;
    return alert;
}

export function createCycleWarning(cycleDetails) {
    const alert = document.createElement('div');
    alert.style.cssText = `
        background: #fee2e2;
        border-left: 4px solid #dc2626;
        padding: 12px 16px;
        border-radius: 6px;
        margin-bottom: 16px;
    `;
    alert.innerHTML = `
        <div style="font-weight: 700; color: #991b1b; margin-bottom: 4px;"><img src="/static/images/icon-warning.svg" alt="警告" class="inline-icon"> 檢測到循環依賴</div>
        <div style="color: #7f1d1d; font-size: 13px;">${cycleDetails || '推論鏈存在循環引用，可能影響可靠性'}</div>
    `;
    return alert;
}

export function createCriticalNodesAlert(criticalNodes, nodeMap) {
    const alert = document.createElement('div');
    alert.style.cssText = `
        background: #FFEAA7;
        border-left: 4px solid #FDCB6E;
        padding: 12px 16px;
        border-radius: 6px;
        margin-bottom: 16px;
    `;

    const criticalHtml = criticalNodes.map(critical => {
        const node = nodeMap[critical.node_id];
        if (!node) return '';
        return `
            <div style="margin-bottom: 8px; color: #2D3436;">
                <strong>「${node.claim.substring(0, 50)}${node.claim.length > 50 ? '...' : ''}」</strong>
                影響 ${critical.affects_count} 個後續推論
                ${critical.criticality_reason ? `<br><span style="font-size: 13px;">└─ ${critical.criticality_reason}</span>` : ''}
            </div>
        `;
    }).join('');

    alert.innerHTML = `
        <div style="font-weight: 700; color: #2D3436; margin-bottom: 8px;"><img src="/static/images/icon-alert.svg" alt="重要警示" class="inline-icon"> 關鍵薄弱環節</div>
        ${criticalHtml}
    `;

    return alert;
}

export function renderArgumentNode(node, stepNumber, nodeMap, chainAnalysis) {
    const nodeEl = document.createElement('div');
    nodeEl.className = 'argument-node';
    nodeEl.id = `node-${node.node_id}`;
    nodeEl.setAttribute('data-node-id', node.node_id);
    nodeEl.setAttribute('data-depends', JSON.stringify(node.depends_on || []));

    const affectedIds = [];
    Object.values(nodeMap).forEach(n => {
        if (n.depends_on && n.depends_on.includes(node.node_id)) {
            affectedIds.push(n.node_id);
        }
    });
    nodeEl.setAttribute('data-affects', JSON.stringify(affectedIds));

    nodeEl.style.cssText = `
        background: #FFFFFF;
        border: 2px solid #B2BEC3;
        border-radius: 8px;
        padding: 16px;
        margin-bottom: 12px;
        transition: all 0.2s ease;
    `;

    const emoji = {deduction: '<img src="/static/images/icon-deduction.svg" alt="演繹" class="inline-icon">', induction: '<img src="/static/images/icon-induction.svg" alt="歸納" class="inline-icon">', abduction: '<img src="/static/images/icon-abduction.svg" alt="溯因" class="inline-icon">'}[node.reasoning_type] || '<img src="/static/images/icon-interim.svg" alt="中間結論" class="inline-icon">';
    const label = {deduction: '演繹', induction: '歸納', abduction: '溯因'}[node.reasoning_type];
    const score = node.confidence_score ?? inferScore(node.confidence);
    const scoreColor = score >= 7 ? '#16a34a' : score >= 4 ? '#FDCB6E' : '#dc2626';

    let impactInfo = '';
    if (chainAnalysis?.critical_nodes) {
        const critical = chainAnalysis.critical_nodes.find(c => c.node_id === node.node_id);
        if (critical && critical.affects_count > 0) {
            impactInfo = `<div style="color: #dc2626; font-size: 13px; margin-top: 8px;">
                <img src="/static/images/icon-impact.svg" alt="影響力" class="inline-icon"> 影響 ${critical.affects_count} 個後續推論
            </div>`;
        }
    }

    let warningsHtml = '';
    if (node.logic_warnings && node.logic_warnings.length > 0) {
        warningsHtml = node.logic_warnings.map(w => `
            <div style="color: #FDCB6E; font-size: 13px; margin-top: 4px;">
                <img src="/static/images/icon-warning.svg" alt="警告" class="inline-icon"> ${w}
            </div>
        `).join('');
    }

    let depsHtml = '';
    if (node.depends_on && node.depends_on.length > 0) {
        const depLabels = node.depends_on.map(depId => {
            const depIndex = Object.keys(nodeMap).indexOf(depId) + 1;
            return `步驟 ${depIndex}`;
        });
        depsHtml = `<div style="color: #2D3436; font-size: 13px; margin-top: 8px;">
            ↑ 依賴：${depLabels.join(', ')}
        </div>`;
    }

    const evidenceHtml = node.evidence_ids && node.evidence_ids.length > 0
        ? `<div style="color: #666; font-size: 13px; margin-top: 4px;">
               證據來源：${node.evidence_ids.map(id => `<span style="background: #e5e7eb; padding: 2px 6px; border-radius: 3px; margin-right: 4px;">[${id}]</span>`).join('')}
           </div>`
        : '<div style="color: #999; font-size: 13px; margin-top: 4px;">無直接證據引用</div>';

    nodeEl.innerHTML = `
        <div style="font-weight: 700; margin-bottom: 8px; display: flex; align-items: center; gap: 8px;">
            <span style="background: #FFEAA7; padding: 4px 8px; border-radius: 4px; font-size: 14px;">[${stepNumber}]</span>
            <span>${emoji} ${label}</span>
            <span style="color: ${scoreColor}; font-size: 14px; background: ${scoreColor}22; padding: 2px 8px; border-radius: 4px;">
                信心度 ${score.toFixed(1)}/10
            </span>
        </div>
        <div style="color: #2D3436; margin-bottom: 8px; line-height: 1.6;">「${node.claim}」</div>
        ${evidenceHtml}
        ${depsHtml}
        ${impactInfo}
        ${warningsHtml}
    `;

    return nodeEl;
}

export function setupHoverInteractions(container, nodeMap) {
    const nodes = container.querySelectorAll('.argument-node');

    nodes.forEach(nodeEl => {
        nodeEl.addEventListener('mouseenter', () => {
            const dependsOn = JSON.parse(nodeEl.getAttribute('data-depends') || '[]');
            const affects = JSON.parse(nodeEl.getAttribute('data-affects') || '[]');

            nodeEl.style.borderColor = '#FDCB6E';
            nodeEl.style.boxShadow = '0 4px 12px rgba(253, 203, 110, 0.3)';

            dependsOn.forEach(depId => {
                const depEl = document.getElementById(`node-${depId}`);
                if (depEl) {
                    depEl.style.backgroundColor = '#FFEAA7';
                    depEl.style.borderColor = '#FDCB6E';
                }
            });

            affects.forEach(affectedId => {
                const affectedEl = document.getElementById(`node-${affectedId}`);
                if (affectedEl) {
                    affectedEl.style.borderColor = '#ef4444';
                    affectedEl.style.borderWidth = '2px';
                }
            });
        });

        nodeEl.addEventListener('mouseleave', () => {
            nodes.forEach(n => {
                n.style.backgroundColor = '#FFFFFF';
                n.style.borderColor = '#B2BEC3';
                n.style.borderWidth = '2px';
                n.style.boxShadow = 'none';
            });
        });
    });
}

export function inferScore(confidence) {
    const mapping = { 'high': 8.0, 'medium': 5.0, 'low': 2.0 };
    return mapping[confidence] || 5.0;
}

export function formatReasoningForVerification() {
    const _ag = getArgumentGraph();
    if (!_ag || _ag.length === 0) {
        return '無推論鏈資料可供驗證。';
    }

    const query = getResearchReport()?.query || getConversationHistory()[0] || '(未知查詢)';
    let content = '';

    content += `我請其他 Agent 做「${query}」的研究，他的推論過程如下，請幫我檢查是否合理：\n\n`;
    content += `${'='.repeat(50)}\n\n`;

    content += `【推論步驟】（共 ${_ag.length} 步）\n\n`;

    _ag.forEach((node, index) => {
        const typeLabel = {deduction: '演繹', induction: '歸納', abduction: '溯因'}[node.reasoning_type] || node.reasoning_type;
        const score = node.confidence_score ?? inferScore(node.confidence);

        content += `步驟 ${index + 1}：${typeLabel}\n`;
        content += `主張：「${node.claim}」\n`;
        content += `信心度：${score.toFixed(1)}/10\n`;

        if (node.evidence_ids && node.evidence_ids.length > 0) {
            content += `證據來源：[${node.evidence_ids.join('], [')}]\n`;
        } else {
            content += `證據來源：無直接引用\n`;
        }

        if (node.depends_on && node.depends_on.length > 0) {
            const depLabels = node.depends_on.map(depId => {
                const depIndex = _ag.findIndex(n => n.node_id === depId);
                return depIndex >= 0 ? `步驟 ${depIndex + 1}` : depId;
            });
            content += `依賴：${depLabels.join(', ')}\n`;
        }

        if (node.logic_warnings && node.logic_warnings.length > 0) {
            content += `警告：${node.logic_warnings.join('; ')}\n`;
        }

        content += `\n`;
    });

    const _ca = getChainAnalysis();
    if (_ca) {
        content += `${'='.repeat(50)}\n\n`;
        content += `【分析摘要】\n`;
        content += `- 推論步驟數：${_ag.length}\n`;
        if (_ca.max_depth !== undefined) {
            content += `- 推論深度：${_ca.max_depth}\n`;
        }
        if (_ca.logic_inconsistencies > 0) {
            content += `- 邏輯不一致數：${_ca.logic_inconsistencies}\n`;
        }
        if (_ca.has_cycles) {
            content += `- 存在循環依賴\n`;
        }
        if (_ca.critical_nodes?.length > 0) {
            content += `- 關鍵薄弱環節：${_ca.critical_nodes.length} 個\n`;
        }
    }

    content += `\n${'='.repeat(50)}\n`;
    content += `\n請檢查上述推論鏈的邏輯是否正確、證據是否充分、結論是否合理。`;

    return content;
}

// ============================================================================
// DR display + error + progress
// ============================================================================

// Session-restore path: render a previously-saved DR report into research view
export function renderResearchReportToView(report, argGraph, chainAnalysis) {
    const researchViewEl = document.getElementById('researchView');
    if (!researchViewEl || !report || !report.report) return;

    console.log('[Session] Rendering research report to research view');
    researchViewEl.innerHTML = '';

    const reportContainer = document.createElement('div');
    reportContainer.className = 'deep-research-report';

    let reportHTML = DOMPurify.sanitize(marked.parse(report.report));

    if (report.sources && report.sources.length > 0) {
        reportHTML = addCitationLinks(reportHTML, report.sources);
    }

    reportHTML = addCollapsibleSections(reportHTML);

    if (report.sources && report.sources.length > 0) {
        reportHTML += generateCitationReferenceList(report.sources);
    }

    reportContainer.innerHTML = reportHTML;
    researchViewEl.appendChild(reportContainer);

    bindCollapsibleHandlers(researchViewEl);
    bindCitationReferenceToggles(reportContainer);
    addToggleAllToolbar(reportContainer);

    if (argGraph && argGraph.length > 0) {
        displayReasoningChainInContainer(argGraph, chainAnalysis, researchViewEl);
    }
}

// Low-relevance / low-keyword warning banner for DR, emitted during prepare()
// (BEFORE the research orchestrator starts). Inserted at the TOP of #resultsSection
// — the stable wrapper that holds every view (#researchView, #listView, ...). This
// survives the #researchView.innerHTML='' clear at final_result because the banner
// is a SIBLING above #researchView, not inside it (mirrors search.js Task 3 target).
export function showResearchRelevanceWarning(message, kind) {
    // kind: 'relevance' | 'keyword' — distinct DOM ids so both can show at once.
    const id = kind === 'keyword' ? 'drLowKeywordWarning' : 'drLowRelevanceWarning';
    const existing = document.getElementById(id);
    if (existing) existing.remove();

    const warning = document.createElement('div');
    warning.id = id;
    warning.className = kind === 'keyword' ? 'low-keyword-match-warning' : 'low-relevance-warning';
    warning.innerHTML = `<span class="warning-text">${escapeHTML(message)}</span>`;

    const container = document.getElementById('resultsSection');
    if (container) container.insertBefore(warning, container.firstChild);
}

// Inline error display for DR (replaces alert() calls)
export function showDRError(message) {
    const loadingStateEl = document.getElementById('loadingState');
    if (loadingStateEl) loadingStateEl.classList.remove('active');

    const resultsSection = document.getElementById('resultsSection');
    if (resultsSection) resultsSection.classList.add('active');

    const researchViewEl = document.getElementById('researchView');
    const listViewEl = document.getElementById('listView');
    const targetEl = researchViewEl || listViewEl;
    if (targetEl) {
        targetEl.innerHTML = `<div class="news-card" style="border-left: 3px solid var(--brand-dark);">
            <div class="news-title">無法進行 Deep Research</div>
            <div class="news-excerpt visible">${escapeHTML(message)}</div>
        </div>`;
    }

    const researchTab = document.querySelector('.tab[data-view="research"]');
    if (researchTab) {
        researchTab.click();
    }
}

// Function to update Deep Research progress display - Log Style
export function updateReasoningProgress(data) {
    console.log('[Progress] updateReasoningProgress called with stage:', data.stage);
    let container = document.getElementById('reasoning-progress');

    if (!container) {
        console.log('[Progress] Creating new log-style progress container');
        container = document.createElement('div');
        container.id = 'reasoning-progress';
        container.className = 'reasoning-progress-container';
        container.innerHTML = `
            <div class="progress-header">深度研究進行中</div>
            <div class="progress-log" id="progress-log"></div>
        `;

        const loadingState = document.getElementById('loadingState');
        if (loadingState) {
            loadingState.appendChild(container);
        } else {
            const resultsSection = document.getElementById('results');
            if (resultsSection) {
                resultsSection.insertBefore(container, resultsSection.firstChild);
            }
        }
    }

    const logContainer = document.getElementById('progress-log');
    if (!logContainer) return;

    const stage = data.stage;

    function addLogEntry(icon, text, cssClass = '') {
        const existingActive = logContainer.querySelector('.log-entry.active');
        if (existingActive && cssClass === 'complete') {
            existingActive.classList.remove('active');
            existingActive.classList.add('complete');
            existingActive.querySelector('.log-icon').textContent = icon;
            return;
        }

        const entry = document.createElement('div');
        entry.className = `log-entry ${cssClass}`;
        entry.innerHTML = `
            <span class="log-icon">${icon}</span>
            <span class="log-text">${text}</span>
        `;
        logContainer.appendChild(entry);

        container.scrollTop = container.scrollHeight;
    }

    function completeLastActive(icon = '✓') {
        const lastActive = logContainer.querySelector('.log-entry.active:last-of-type');
        if (lastActive) {
            lastActive.classList.remove('active');
            lastActive.classList.add('complete');
            lastActive.querySelector('.log-icon').textContent = icon;
        }
    }

    switch (stage) {
        case 'analyst_analyzing':
            const iterInfo = data.iteration && data.total_iterations
                ? ` (${data.iteration}/${data.total_iterations})`
                : '';
            addLogEntry('○', `正在深度分析資料來源${iterInfo}...`, 'active');
            break;

        case 'analyst_complete':
            completeLastActive('✓');
            addLogEntry('✓', `分析完成`, 'complete');
            break;

        case 'gap_search_started':
            addLogEntry('↻', `偵測到資訊缺口，正在補充搜尋...`, 'active gap-search');
            break;

        case 'analyst_integrating_new_data':
            completeLastActive('✓');
            addLogEntry('○', '整合新資料中，重新分析...', 'active');
            break;

        case 'cov_verifying':
            addLogEntry('○', '正在驗證事實宣稱...', 'active cov');
            break;

        case 'cov_complete':
            completeLastActive('✓');
            addLogEntry('✓', '事實查核完成', 'complete cov');
            break;

        case 'critic_reviewing':
            addLogEntry('○', '正在檢查邏輯與來源可信度...', 'active');
            break;

        case 'critic_complete':
            completeLastActive();
            const status = data.status || 'PASS';
            const statusIcon = status === 'PASS' ? '✓' : status === 'WARN' ? '⚠' : '✗';
            const statusClass = status === 'PASS' ? 'complete' : status === 'WARN' ? 'warning' : 'error';
            const statusText = status === 'PASS' ? '審查通過' : status === 'WARN' ? '審查通過（有警告）' : '需要修改';
            addLogEntry(statusIcon, statusText, statusClass);
            break;

        case 'writer_planning':
            addLogEntry('○', '正在規劃報告結構...', 'active');
            break;

        case 'writer_composing':
            completeLastActive('✓');
            addLogEntry('○', '正在撰寫最終報告...', 'active');
            break;

        case 'writer_complete':
            completeLastActive('✓');
            addLogEntry('✓', '報告生成完成', 'complete');
            const header = container.querySelector('.progress-header');
            if (header) {
                header.style.setProperty('--blink-color', '#22c55e');
            }
            break;

        default:
            console.log('[Progress] Unknown stage:', stage);
            break;
    }
}

// Display the final DR report — fired by performDeepResearch on final_result
export function displayDeepResearchResults(report, metadata, savedQuery) {
    console.log('[Deep Research] Displaying results');
    console.log('[Deep Research] Metadata received:', metadata);
    console.log('[Deep Research] Sources array:', metadata?.sources);
    console.log('[Deep Research] Sources count:', metadata?.sources?.length);

    setResearchReport({
        report: report || '',
        sources: metadata?.sources || [],
        query: savedQuery || '',
        timestamp: Date.now()
    });
    markSessionDirty();
    console.log('[Deep Research] Stored report for follow-up:', getResearchReport().report.substring(0, 100) + '...');

    let schemaObj = null;
    if (metadata?.content && Array.isArray(metadata.content) && metadata.content.length > 0) {
        schemaObj = metadata.content[0].schema_object;
    } else {
        schemaObj = metadata?.schema_object;
    }

    const resultsSection = document.getElementById('resultsSection');
    if (resultsSection) resultsSection.classList.add('active');

    // Display Knowledge Graph if available (Phase KG) — KG module still in news-search.js
    //   until commit 17 batch 6''; reach via window.displayKnowledgeGraph bridge.
    if (typeof window.displayKnowledgeGraph === 'function') {
        window.displayKnowledgeGraph(schemaObj?.knowledge_graph || metadata?.knowledge_graph);
    }

    const researchViewEl = document.getElementById('researchView');
    if (!researchViewEl) {
        console.error('[Deep Research] researchView element not found!');
        return;
    }

    researchViewEl.innerHTML = '';

    // RSN-4: Show verification warning banner if CoV found unverified/partially_verified claims
    const verificationStatus = metadata?.verification_status;
    if (verificationStatus === 'unverified' || verificationStatus === 'partially_verified') {
        const verificationMessage = metadata?.verification_message || '本報告未經完整事實驗證';
        const warningBanner = document.createElement('div');
        warningBanner.className = 'verification-warning';
        warningBanner.innerHTML = `<span class="verification-warning-icon">⚠</span> ${DOMPurify.sanitize(verificationMessage)}`;
        researchViewEl.appendChild(warningBanner);
    }

    const reportContainer = document.createElement('div');
    reportContainer.className = 'deep-research-report';

    let reportHTML = DOMPurify.sanitize(marked.parse(report || '無結果'));

    if (metadata && metadata.sources && metadata.sources.length > 0) {
        console.log('[Deep Research] Adding citation links with', metadata.sources.length, 'sources');
        reportHTML = addCitationLinks(reportHTML, metadata.sources);
    } else {
        console.warn('[Deep Research] No sources available for citation links');
    }

    reportHTML = addCollapsibleSections(reportHTML);

    if (metadata && metadata.sources && metadata.sources.length > 0) {
        reportHTML += generateCitationReferenceList(metadata.sources);
    }

    reportContainer.innerHTML = reportHTML;
    researchViewEl.appendChild(reportContainer);

    bindCollapsibleHandlers(researchViewEl);
    bindCitationReferenceToggles(reportContainer);
    addToggleAllToolbar(reportContainer);

    const argGraph = schemaObj?.argument_graph || metadata?.argument_graph;
    const chainAnalysis = schemaObj?.reasoning_chain_analysis || metadata?.reasoning_chain_analysis;
    displayReasoningChainInContainer(argGraph, chainAnalysis, researchViewEl);

    const progressContainer = document.getElementById('reasoning-progress');
    if (progressContainer) {
        progressContainer.remove();
    }

    const researchTab = document.querySelector('.tab[data-view="research"]');
    if (researchTab) {
        researchTab.click();
    }

    // Move search input to bottom of chat area (follow-up mode)
    const chatInputContainer = document.getElementById('chatInputContainer');
    const searchContainer = document.getElementById('searchContainer');
    if (chatInputContainer && searchContainer) {
        chatInputContainer.appendChild(searchContainer);
        chatInputContainer.style.display = 'block';
        const btnSearch = document.getElementById('btnSearch');
        const searchInput = document.getElementById('searchInput');
        if (btnSearch) btnSearch.textContent = '發送';
        if (searchInput) searchInput.placeholder = '基於報告繼續提問...';
        console.log('[Deep Research] Search input moved to bottom for follow-up questions');
    }

    console.log('[Deep Research] Results displayed successfully in research view');

    // Immediately save to prevent loss on close/refresh
    window.saveCurrentSession?.();
}

// ============================================================================
// Clarification UI (3 functions)
// ============================================================================

export function addClarificationMessage(clarificationData, originalQuery, eventSource, savedQuery) {
    console.log('[Clarification] Adding multi-dimensional clarification:', clarificationData);

    const loadingState = document.getElementById('loadingState');
    if (loadingState) {
        loadingState.classList.remove('active');
    }

    const chatMessagesEl = document.getElementById('chatMessages');
    if (!chatMessagesEl) {
        console.error('[Clarification] Chat messages element not found');
        return;
    }

    const iconMap = {
        'time': '<img src="/static/images/icon-clarify-time.svg" alt="時間" class="inline-icon">',
        'scope': '<img src="/static/images/icon-clarify-scope.svg" alt="範圍" class="inline-icon">',
        'entity': '<img src="/static/images/icon-clarify-entity.svg" alt="實體" class="inline-icon">'
    };

    const messageDiv = document.createElement('div');
    messageDiv.className = 'chat-message assistant clarification';

    let contentHTML = '<div class="chat-message-header"><img src="/static/images/icon-role-dubao.svg" alt="讀豹" class="inline-icon"> 讀豹</div>';
    contentHTML += '<div class="chat-message-bubble">';
    contentHTML += '<div class="clarification-card">';

    contentHTML += `
        <div class="clarification-header">
            ${clarificationData.instruction || '為了精準搜尋'}「${escapeHTML(originalQuery)}」，請選擇以下條件
        </div>
    `;

    clarificationData.questions.forEach(question => {
        const icon = iconMap[question.clarification_type] || '<img src="/static/images/icon-clarify-other.svg" alt="其他" class="inline-icon">';
        const requiredMark = question.required ? '<span class="required">*</span>' : '';

        contentHTML += `
            <div class="question-block" data-question-id="${question.question_id}">
                <div class="question-label">
                    <span class="question-icon">${icon}</span>
                    <span class="question-text">${escapeHTML(question.question)}${requiredMark}</span>
                    <span class="multi-select-hint">(可多選)</span>
                </div>
                <div class="options-group">
        `;

        question.options.forEach(opt => {
            const queryModifier = opt.query_modifier || '';
            const isComprehensive = opt.is_comprehensive || false;
            const timeRangeJson = opt.time_range ? JSON.stringify(opt.time_range) : '';

            contentHTML += `
                <button class="option-chip"
                        data-option-id="${opt.id}"
                        data-label="${escapeHTML(opt.label)}"
                        data-query-modifier="${escapeHTML(queryModifier)}"
                        data-is-comprehensive="${isComprehensive}"
                        data-time-range="${escapeHTML(timeRangeJson)}">
                    ${escapeHTML(opt.label)}
                </button>
            `;
        });

        contentHTML += `
            <div class="custom-input-group" style="margin-top: 8px; display: flex; gap: 6px; align-items: center;">
                <input type="text" class="custom-option-input"
                       placeholder="或自行輸入..."
                       data-question-id="${question.question_id}"
                       style="flex: 1; padding: 6px 10px; border: 1px solid #B2BEC3; border-radius: 16px; font-size: 0.9em;">
                <button class="option-chip custom-input-confirm"
                        data-question-id="${question.question_id}"
                        style="padding: 6px 12px; background: #2D3436; color: #FFFFFF;">
                    確定
                </button>
            </div>
        `;

        contentHTML += '</div></div>';
    });

    // Bug #4: 自由聚焦選項
    contentHTML += `
        <div class="clarification-extra-section" style="margin-top: 16px; padding-top: 12px; border-top: 1px solid #B2BEC3;">
            <div style="font-size: 0.9em; color: #2D3436; margin-bottom: 6px;">
                或直接輸入您的研究重點：
            </div>
            <div class="custom-input-group" style="display: flex; gap: 6px; align-items: center;">
                <input type="text" class="clarification-extra-focus"
                       placeholder="例如：特定事件、人物、時間段..."
                       style="flex: 1; padding: 6px 10px; border: 1px solid #B2BEC3; border-radius: 16px; font-size: 0.9em;">
                <button class="option-chip free-start-confirm"
                        style="padding: 6px 12px; background: #FDCB6E; color: #2D3436;">
                    開始研究
                </button>
            </div>
        </div>
    `;

    contentHTML += `
        <div class="clarification-actions" style="margin-top: 12px;">
            <button class="submit-clarification" disabled style="width: 100%;">
                ${clarificationData.submit_label || '開始搜尋'}
            </button>
        </div>
    `;

    contentHTML += '</div></div>';

    messageDiv.innerHTML = contentHTML;
    chatMessagesEl.appendChild(messageDiv);
    chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;

    console.log('[Clarification] Multi-question card rendered');

    attachClarificationListeners(messageDiv, clarificationData, originalQuery, eventSource);
}

export function attachClarificationListeners(container, clarificationData, originalQuery, eventSource) {
    const questions = clarificationData.questions;
    const selectedAnswers = {};

    function updateSubmitButton() {
        const submitBtn = container.querySelector('.submit-clarification');
        const allAnswered = questions.every(q => selectedAnswers[q.question_id] && selectedAnswers[q.question_id].length > 0);
        submitBtn.disabled = !allAnswered;
        if (allAnswered) {
            console.log('[Clarification] All questions answered, submit enabled');
        }
    }

    container.querySelectorAll('.option-chip:not(.custom-input-confirm)').forEach(chip => {
        chip.addEventListener('click', function() {
            const questionBlock = this.closest('.question-block');
            const questionId = questionBlock.dataset.questionId;

            if (!selectedAnswers[questionId]) {
                selectedAnswers[questionId] = [];
            }

            const optionId = this.dataset.optionId;
            const isCurrentlySelected = this.classList.contains('selected');

            if (isCurrentlySelected) {
                this.classList.remove('selected');
                selectedAnswers[questionId] = selectedAnswers[questionId].filter(a => a.option_id !== optionId);
            } else {
                this.classList.add('selected');

                let timeRange = null;
                const timeRangeJson = this.dataset.timeRange;
                if (timeRangeJson) {
                    try {
                        timeRange = JSON.parse(timeRangeJson);
                    } catch (e) {
                        console.warn('[Clarification] Failed to parse time_range:', e);
                    }
                }

                selectedAnswers[questionId].push({
                    option_id: optionId,
                    label: this.dataset.label,
                    query_modifier: this.dataset.queryModifier,
                    is_comprehensive: this.dataset.isComprehensive === 'true',
                    time_range: timeRange
                });

                // Mutual exclusion: comprehensive option deselects all others
                if (this.dataset.isComprehensive === 'true') {
                    questionBlock.querySelectorAll('.option-chip:not(.custom-input-confirm)').forEach(otherChip => {
                        if (otherChip === this) return;
                        otherChip.classList.remove('selected');
                    });
                    selectedAnswers[questionId] = [{
                        option_id: optionId,
                        label: this.dataset.label,
                        query_modifier: this.dataset.queryModifier,
                        is_comprehensive: true,
                        time_range: timeRange
                    }];
                }
            }

            const customInput = questionBlock.querySelector('.custom-option-input');
            if (customInput) customInput.value = '';
            const confirmBtn = questionBlock.querySelector('.custom-input-confirm');
            if (confirmBtn) confirmBtn.classList.remove('selected');

            console.log('[Clarification] Selected:', questionId, selectedAnswers[questionId]);
            updateSubmitButton();
        });
    });

    container.querySelectorAll('.custom-input-confirm').forEach(btn => {
        btn.addEventListener('click', function() {
            const questionId = this.dataset.questionId;
            const questionBlock = container.querySelector(`.question-block[data-question-id="${questionId}"]`);
            const customInput = questionBlock.querySelector('.custom-option-input');
            const customValue = customInput.value.trim();

            if (!customValue) {
                alert('請輸入內容');
                return;
            }

            questionBlock.querySelectorAll('.option-chip:not(.custom-input-confirm)').forEach(c => c.classList.remove('selected'));

            this.classList.add('selected');

            selectedAnswers[questionId] = [{
                option_id: '_custom',
                label: customValue,
                query_modifier: customValue,
                is_comprehensive: false
            }];

            console.log('[Clarification] Custom input:', questionId, selectedAnswers[questionId]);
            updateSubmitButton();
        });
    });

    container.querySelectorAll('.custom-option-input').forEach(input => {
        input.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                const questionId = this.dataset.questionId;
                const confirmBtn = container.querySelector(`.custom-input-confirm[data-question-id="${questionId}"]`);
                if (confirmBtn) confirmBtn.click();
            }
        });
    });

    const freeStartBtn = container.querySelector('.free-start-confirm');
    if (freeStartBtn) {
        freeStartBtn.addEventListener('click', () => {
            const extraInput = container.querySelector('.clarification-extra-focus');
            const extraFocus = extraInput ? extraInput.value.trim() : '';
            submitClarification(selectedAnswers, originalQuery, eventSource, questions, extraFocus, true);
        });
    }

    container.querySelector('.submit-clarification').addEventListener('click', () => {
        const extraFocusInput = container.querySelector('.clarification-extra-focus');
        const extraFocus = extraFocusInput ? extraFocusInput.value.trim() : '';
        submitClarification(selectedAnswers, originalQuery, eventSource, questions, extraFocus);
    });
}

// Submit clarification response with natural language query building
export function submitClarification(selectedAnswers, originalQuery, eventSource, questions, extraFocus = '', forceAllComprehensive = false) {
    console.log('[Clarification] Submitting answers:', selectedAnswers);
    console.log('[Clarification] Original query:', originalQuery);
    console.log('[Clarification] Extra focus:', extraFocus, 'Force comprehensive:', forceAllComprehensive);

    const clarificationCards = document.querySelectorAll('.clarification-card');
    clarificationCards.forEach(card => {
        card.querySelectorAll('button').forEach(btn => {
            btn.disabled = true;
            btn.style.opacity = '0.5';
            btn.style.pointerEvents = 'none';
        });
        card.querySelectorAll('input').forEach(inp => {
            inp.disabled = true;
            inp.style.opacity = '0.5';
        });
    });

    // Abort the DR stream
    if (eventSource) {
        if (typeof eventSource.abort === 'function') {
            eventSource.abort();
        } else if (typeof eventSource.close === 'function') {
            eventSource.close();
        }
    }

    let clarifiedQuery = originalQuery;
    let allComprehensive = true;

    let timeModifier = '';
    let scopeModifier = '';
    let entityModifier = '';
    let userTimeRange = null;
    let userTimeLabel = null;

    questions.forEach(q => {
        const answers = selectedAnswers[q.question_id];
        if (!answers || answers.length === 0) return;

        answers.forEach(answer => {
            if (!answer.is_comprehensive) {
                allComprehensive = false;
            }
        });

        const modifiers = answers.map(a => a.query_modifier).filter(Boolean);
        const mergedModifier = modifiers.join('、');

        if (mergedModifier) {
            if (q.clarification_type === 'time') {
                timeModifier = mergedModifier;
                const lastWithTimeRange = [...answers].reverse().find(a => a.time_range);
                if (lastWithTimeRange) {
                    userTimeRange = lastWithTimeRange.time_range;
                    userTimeLabel = lastWithTimeRange.label;
                    console.log('[Clarification] User selected time range:', userTimeRange, 'label:', userTimeLabel);
                }
            } else if (q.clarification_type === 'scope') {
                scopeModifier = mergedModifier;
            } else if (q.clarification_type === 'entity') {
                entityModifier = mergedModifier;
            }
        }
    });

    if (timeModifier && scopeModifier) {
        clarifiedQuery = `${originalQuery}(${timeModifier}，${scopeModifier})`;
    } else if (timeModifier) {
        clarifiedQuery = `${originalQuery}(${timeModifier})`;
    } else if (scopeModifier) {
        clarifiedQuery = `${originalQuery}(${scopeModifier})`;
    } else if (entityModifier) {
        clarifiedQuery = `${entityModifier}${originalQuery}`;
    }

    if (extraFocus) {
        clarifiedQuery += `，${extraFocus}`;
    }

    if (forceAllComprehensive) {
        allComprehensive = true;
    }

    console.log('[Clarification] Clarified query:', clarifiedQuery);
    console.log('[Clarification] All comprehensive:', allComprehensive);
    console.log('[Clarification] User time range:', userTimeRange);

    const chatMessagesEl = document.getElementById('chatMessages');
    const userMessageDiv = document.createElement('div');
    userMessageDiv.className = 'chat-message user';

    const selections = Object.values(selectedAnswers).flatMap(arr => arr.map(a => a.label));
    if (extraFocus) selections.push(extraFocus);
    if (forceAllComprehensive && selections.length === 0) selections.push('直接開始研究');
    const selectionText = selections.join(' + ');
    userMessageDiv.innerHTML = `
        <div class="chat-message-header"><img src="/static/images/icon-role-user.svg" alt="你" class="inline-icon"> 你</div>
        <div class="chat-message-bubble">${escapeHTML(selectionText)}</div>
    `;
    chatMessagesEl.appendChild(userMessageDiv);
    chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;

    console.log('[Clarification] Re-submitting with skip_clarification=true');
    performDeepResearch(clarifiedQuery, true, allComprehensive, userTimeRange, userTimeLabel);
}

// ============================================================================
// performDeepResearch — main DR SSE pipeline entry
// ============================================================================

export async function performDeepResearch(query, skipClarification = false, comprehensiveSearch = false, userTimeRange = null, userTimeLabel = null) {
    console.log('=== Deep Research Mode ===');
    console.log('Query:', query);
    console.log('Skip clarification:', skipClarification);
    console.log('Comprehensive search:', comprehensiveSearch);
    console.log('User time range:', userTimeRange);
    console.log('User time label:', userTimeLabel);

    const savedQuery = query;

    const searchInput = document.getElementById('searchInput');
    if (searchInput) searchInput.value = '';
    setProcessingState(true);

    if (!skipClarification) {
        pushConversationHistory(query);
        markSessionDirty();
        window.saveCurrentSession?.();
    }

    const chatContainer = document.getElementById('chatContainer');
    const chatMessagesEl = document.getElementById('chatMessages');
    if (chatContainer) {
        chatContainer.classList.add('active');
        console.log('[Deep Research] Chat container activated');

        if (chatMessagesEl) {
            const userMessageDiv = document.createElement('div');
            userMessageDiv.className = 'chat-message user';
            userMessageDiv.innerHTML = `
                <div class="chat-message-header"><img src="/static/images/icon-role-user.svg" alt="你" class="inline-icon"> 你</div>
                <div class="chat-message-bubble">${escapeHTML(query)}</div>
            `;
            chatMessagesEl.appendChild(userMessageDiv);
            chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
            console.log('[Deep Research] User message added to chat');
        }
    }

    try {
        const base = window.location.origin;

        const deepResearchUrl = new URL('/api/deep_research', base);
        deepResearchUrl.searchParams.append('query', query);
        deepResearchUrl.searchParams.append('site', getSelectedSitesParam());
        // research_mode fixed to 'discovery' (was currentResearchMode local let — value never
        //   changed in production; hardcoding here removes the need for cross-module access)
        deepResearchUrl.searchParams.append('research_mode', 'discovery');
        deepResearchUrl.searchParams.append('max_iterations', '3');

        if (skipClarification) {
            deepResearchUrl.searchParams.append('skip_clarification', 'true');
            console.log('[Deep Research] Skip clarification enabled');
        }

        if (comprehensiveSearch) {
            deepResearchUrl.searchParams.append('comprehensive_search', 'true');
            console.log('[Deep Research] Comprehensive search enabled (high diversity)');
        }

        if (userTimeRange && userTimeRange.start && userTimeRange.end) {
            deepResearchUrl.searchParams.append('time_range_start', userTimeRange.start);
            deepResearchUrl.searchParams.append('time_range_end', userTimeRange.end);
            deepResearchUrl.searchParams.append('user_selected_time', 'true');
            if (userTimeLabel) {
                deepResearchUrl.searchParams.append('user_time_label', userTimeLabel);
            }
            console.log('[Deep Research] User-selected time range:', userTimeRange.start, 'to', userTimeRange.end);
        }

        const kgToggle = document.getElementById('kgToggle');
        if (kgToggle && kgToggle.checked) {
            deepResearchUrl.searchParams.append('enable_kg', 'true');
            console.log('[Deep Research] Knowledge Graph generation enabled');
        }

        const webSearchToggle = document.getElementById('webSearchToggle');
        if (webSearchToggle && webSearchToggle.checked) {
            deepResearchUrl.searchParams.append('enable_web_search', 'true');
            console.log('[Deep Research] Web Search enabled');
        }

        if (getCurrentMode() === 'live_research') {
            deepResearchUrl.searchParams.append('enable_live_research', 'true');
            console.log('[Live Research] Live Research mode enabled');
        }

        deepResearchUrl.searchParams.append('session_id', getCurrentSessionId());

        const _convId = getCurrentConversationId();
        if (_convId) {
            deepResearchUrl.searchParams.append('conversation_id', _convId);
            console.log('[Deep Research] Using existing conversation_id:', _convId);
        }

        if (getIncludePrivateSources()) {
            deepResearchUrl.searchParams.append('include_private_sources', 'true');
            // getCurrentUserId still in news-search.js auth-ui residual; reach via window bridge
            //   added by news-search.js (commit 15 — see KEEP-in-place CEO #5)
            const userId = typeof window.getCurrentUserId === 'function' ? window.getCurrentUserId() : null;
            if (userId) {
                deepResearchUrl.searchParams.append('user_id', userId);
                console.log('[Deep Research] Private sources enabled for user:', userId);
            }
        }

        console.log('Deep Research URL:', deepResearchUrl.toString());

        // Bug #23: Cancel any previous active requests before starting DR
        cancelAllActiveRequests();

        // Show loading AFTER cancelAllActiveRequests (which removes .active)
        const loadingState = document.getElementById('loadingState');
        if (loadingState) loadingState.classList.add('active');

        const oldProgress = document.getElementById('reasoning-progress');
        if (oldProgress) oldProgress.remove();

        // Use fetch + ReadableStream for SSE (allows reading 429/400/503 response body)
        const drAbortController = new AbortController();
        setCurrentDeepResearchAbortController(drAbortController);

        let drResponse;
        try {
            // P1 E2E fix (2026-05-26): route through authenticatedFetch so an
            // access-token expiry mid-request → 401 triggers refresh + retry once
            // (and on refresh-fail _handleAuthFailure shows login modal) instead of
            // silently falling into the `!drResponse.ok` raw "HTTP 401" branch.
            drResponse = await window.authManager.authenticatedFetch(deepResearchUrl.toString(), {
                signal: drAbortController.signal
            });
        } catch (fetchError) {
            setCurrentDeepResearchAbortController(null);
            if (loadingState) loadingState.classList.remove('active');
            setProcessingState(false);
            if (fetchError.name === 'AbortError') {
                console.log('[Deep Research] Request aborted');
                return;
            }
            console.error('[Deep Research] Fetch error:', fetchError);
            showDRError('Deep Research 連線錯誤：' + fetchError.message);
            return;
        }

        if (drResponse.status === 429 || drResponse.status === 400 || drResponse.status === 503) {
            setCurrentDeepResearchAbortController(null);
            if (loadingState) loadingState.classList.remove('active');
            setProcessingState(false);
            let errorMsg = '請稍後再試';
            try {
                const errorData = await drResponse.json();
                if (errorData.message) errorMsg = errorData.message;
            } catch (e) { /* ignore parse errors */ }
            console.error('[Deep Research] Guardrail/server error:', drResponse.status, errorMsg);
            showDRError(errorMsg);
            return;
        }

        if (drResponse.status === 401) {
            // Token expired and refresh failed (authenticatedFetch already fired
            // _handleAuthFailure → login modal). Surface a friendly message, not raw 401.
            setCurrentDeepResearchAbortController(null);
            if (loadingState) loadingState.classList.remove('active');
            setProcessingState(false);
            showDRError('登入已過期，請重新登入後再試。');
            return;
        }

        if (!drResponse.ok) {
            setCurrentDeepResearchAbortController(null);
            if (loadingState) loadingState.classList.remove('active');
            setProcessingState(false);
            showDRError('Deep Research 連線錯誤：HTTP ' + drResponse.status);
            return;
        }

        const drReader = drResponse.body.getReader();
        const drDecoder = new TextDecoder();
        let drBuffer = '';
        let fullReport = '';

        function closeDRStream() {
            try { drReader.cancel(); } catch (e) {}
            setCurrentDeepResearchAbortController(null);
        }

        try {
            while (true) {
                const { done, value } = await drReader.read();
                if (done) break;

                drBuffer += drDecoder.decode(value, { stream: true });

                const messages = drBuffer.split('\n\n');
                drBuffer = messages.pop();

                for (const message of messages) {
                    if (!message.trim()) continue;

                    const lines = message.split('\n');
                    for (const line of lines) {
                        if (!line.startsWith('data: ')) continue;
                        let data;
                        try {
                            data = JSON.parse(line.slice(6));
                        } catch (e) {
                            console.error('[Deep Research] Failed to parse SSE data:', e);
                            continue;
                        }
                        console.log('Deep Research SSE:', data);

                        if (data.message_type === 'begin-nlweb-response') {
                            if (data.conversation_id) {
                                setCurrentConversationId(data.conversation_id);
                                console.log('[Deep Research] Using backend conversation_id:', getCurrentConversationId());
                            }
                            if (data.query_id) {
                                _currentResearchQueryId = data.query_id;
                                console.log('[Deep Research] Captured query_id for rerun:', _currentResearchQueryId);
                            }
                        } else if (data.message_type === 'low_relevance_warning') {
                            console.warn('[Relevance] Low relevance (DR):', data.content);
                            showResearchRelevanceWarning(data.content, 'relevance');
                        } else if (data.message_type === 'low_keyword_match_warning') {
                            console.warn('[Relevance] Low keyword match (DR):', data.content);
                            showResearchRelevanceWarning(data.content, 'keyword');
                        } else if (data.message_type === 'clarification_required') {
                            console.log('[Clarification] Request received:', data.clarification);
                            addClarificationMessage(data.clarification, data.query, drAbortController, savedQuery);
                        } else if (data.message_type === 'intermediate_result') {
                            updateReasoningProgress(data);
                        } else if (data.message_type === 'research_phase') {
                            console.log('[Deep Research] research_phase event (ignored in DR):', data.phase, data.status);
                        } else if (data.message_type === 'final_result') {
                            fullReport = data.final_report || '';

                            closeDRStream();

                            if (loadingState) loadingState.classList.remove('active');
                            setProcessingState(false);

                            displayDeepResearchResults(fullReport, data, savedQuery);

                            // currentKGData still in news-search.js until commit 17 batch 6''
                            //   — reach via window.__getCurrentKGData bridge to serialize into
                            //   sessionHistory entry alongside research outputs
                            const kgSnapshot = typeof window.__getCurrentKGData === 'function'
                                ? window.__getCurrentKGData()
                                : null;

                            getSessionHistory().push({
                                query: savedQuery,
                                data: data,
                                timestamp: Date.now(),
                                isDeepResearch: true,
                                isLiveResearch: getCurrentMode() === 'live_research',
                                researchReport: getResearchReport() ? { ...getResearchReport() } : null,
                                argumentGraph: getArgumentGraph() ? [...getArgumentGraph()] : null,
                                chainAnalysis: getChainAnalysis() ? { ...getChainAnalysis() } : null,
                                knowledgeGraph: kgSnapshot ? JSON.parse(JSON.stringify(kgSnapshot)) : null,
                                researchQueryId: _currentResearchQueryId
                            });

                            window.renderConversationHistory?.();

                            window.saveCurrentSession?.();
                            // v4.0 Commit 30 (2026-05-25, regression fix):
                            // DR final_result carries the heaviest payload
                            // (writer report + KG + reasoning chain) and CEO
                            // often clicks away within 2s. saveCurrentSession
                            // arms a 2s debounce — flush this session right
                            // away so a session-switch click cannot drop it.
                            try {
                                const mgr = window.sessionManager;
                                if (mgr && typeof mgr.flushPendingSave === 'function'
                                    && typeof window.matchSessionId === 'function') {
                                    const curId = getCurrentLoadedSessionId();
                                    if (curId != null) {
                                        const cur = getSavedSessions().find(s => window.matchSessionId(s.id, curId));
                                        if (cur) {
                                            const p = mgr.flushPendingSave(cur);
                                            if (p && typeof p.then === 'function') {
                                                p.catch(e => console.error('[Deep Research] flushPendingSave failed:', e));
                                            }
                                        }
                                    }
                                }
                            } catch (e) {
                                console.error('[Deep Research] DR-final flushPendingSave path threw:', e);
                            }
                            return;
                        } else if (data.message_type === 'complete') {
                            closeDRStream();
                            setProcessingState(false);
                            console.log('Deep Research stream complete');
                            return;
                        } else if (data.message_type === 'error' || data.message_type === 'research_error') {
                            // W2: 後端背景研究失敗會發 research_error（deep_research.py
                            // _send_research_error），但前端原本只認 'error'，導致使用者
                            // 完全看不到後端研究錯誤。一併接住，錯誤才會浮現給使用者。
                            console.error('[Deep Research] SSE error event:', data.message_type, data.error);
                            closeDRStream();
                            if (loadingState) loadingState.classList.remove('active');
                            setProcessingState(false);
                            showDRError(data.error || 'Deep Research 發生錯誤');
                            return;
                        }
                    }
                }
            }
        } catch (streamError) {
            closeDRStream();
            if (loadingState) loadingState.classList.remove('active');
            setProcessingState(false);
            if (streamError.name === 'AbortError') {
                console.log('[Deep Research] Stream aborted');
                return;
            }
            console.error('[Deep Research] Stream read error:', streamError);
            showDRError('Deep Research 串流錯誤：' + streamError.message);
        }

    } catch (error) {
        setCurrentDeepResearchAbortController(null);
        const loadingState = document.getElementById('loadingState');
        if (loadingState) loadingState.classList.remove('active');
        setProcessingState(false);
        if (error.name === 'AbortError') {
            console.log('[Deep Research] Aborted');
            return;
        }
        console.error('Deep Research error:', error);
        showDRError('Deep Research 發生錯誤：' + (error.message || '未知錯誤'));
    }
}
