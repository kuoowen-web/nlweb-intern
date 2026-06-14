// static/js/features/live-research.js
//
// D-1 Module Header — Live Research Owner (state + functions — commit 16, Phase 8)
//
//   Owned state:
//     - _currentLRSessionId (string|null — server-generated UUID for LR state persistence)
//     - _lrInProgress (boolean — LR run-in-progress flag for Bug 3 guard)
//     - _lrAwaitingCheckpointReply (boolean — UI flag for backend reply-pending state)
//     - _currentLRStage (number — UX-1 D-6 typing indicator stage tracking)
//     - _currentLRActivity (string — UX-1 D-6 dynamic activity text override)
//
//   Functions migrated (15 — commit 16, Phase 8):
//     UI helpers:
//       - resetLiveResearchUI (was news-search.js:2622)
//       - addLRChatMessage (was 2664)
//       - showLRTypingIndicator (was 2716)
//       - hideLRTypingIndicator (was 2733)
//       - updateLRTypingIndicatorText (was 2738)
//       - resetLRTypingState (was 2746)
//       - deriveActivityFromNarration (was 2753)
//       - updateLRStageProgress (was 2816)
//       - showLRCheckpoint (was 2844)
//       - addLRSection (was 2872)
//       - showLRExport (was 2898)
//     SSE handler:
//       - handleLiveResearchSSE (was 2948)
//     Main entries:
//       - performLiveResearch (was 3182)
//       - continueLiveResearch (was 3249)
//
//   Trigger writes:
//     - performLiveResearch / continueLiveResearch SSE handlers (set session_id from server payload)
//     - LR Stage 1-5 entry sets lrInProgress=true
//     - LR Stage 6 export / resetLiveResearchUI / explicit LR end sets lrInProgress=false + clears session id
//     - UserStateSync.clearUserScopedState (IIFE relocated to state-sync.js commit 11) clears
//       via clearLRSessionId with skipIfInflight guard (LR Bug 3 fix — preserves session id
//       when lrInProgress)
//
//   External read:
//     - news-search.js LR control flow (start / stop / continue / SSE handlers via imports)
//     - core/page-bootstrap.js checkAuthOnLoad + visibilitychange guards (preserve authManager._user)
//     - state-sync.js IIFE clearUserScopedState guard (via injected backref)
//
// D-3 Cross-Module Communication:
//   This module IMPORTS injectStateSyncBackref from core/state-sync.js (D-V3 pattern)
//   and registers a small backref bundle at module-init time. state-sync.js does NOT
//   import this module — that direction would create a circular dependency + TDZ risk.
//
//   Additional cross-module imports (D-V6 relax, commit 16):
//     - features/search.js — setProcessingState (LR uses search's spinner state)
//     - features/search.js — pushConversationHistory + setCurrentConversationId
//     - features/source-filters.js — getSelectedSitesParam (LR URL param)
//     - features/session-manager.js — markSessionDirty
//     - features/sessions-list.js — getSavedSessions/setSavedSessions are NOT needed; LR
//       uses window.saveCurrentSession (KEEP-in-place per CEO #5 until commit 25 sweep)
//     - utils/analytics.js — getCurrentSessionId (HTTP session id)
//
// D-13 Compliance:
//   ONE top-level side effect: injectStateSyncBackref({...}) — registers backref bundle.
//   No fetch / no DOM / no localStorage / no addEventListener at top level. The inject
//   call is explicitly permitted as a module-init wiring step (analogous to main.js's
//   injectStateSync({...}) call which wires UserStateSync into auth-manager).
//
// v4.0 Commit 7 (2026-05-24): State migration + D-V3 H1 fix (injectStateSync wire).
// v4.0 Commit 16 (2026-05-25, Phase 8): 15 LR function bodies migrated from news-search.js.
//   Bridge removed: 1 — `window.performLiveResearch` (was news-search.js:3247).
//   continueLiveResearch / resetLiveResearchUI accessed via ES import in
//   news-search.js bindLRCheckpointListeners IIFE + mode-switch handler.
//   saveCurrentSession invoked via window bridge (KEEP-in-place until commit 25 sweep).

import { injectStateSyncBackref } from '../core/state-sync.js';
import { setProcessingState, pushConversationHistory, setCurrentConversationId, escapeHTML } from './search.js';
import { getSelectedSitesParam } from './source-filters.js';
import { markSessionDirty } from './session-manager.js';
import { getCurrentSessionId } from '../utils/analytics.js';
import { classifyLRResumeState } from './lr-resume-classify.js';

// ============================================================================
// currentLRSessionId — server-generated UUID for LR state persistence
// ============================================================================
let _currentLRSessionId = null;

export function getLRSessionId() {
    return _currentLRSessionId;
}

export function setLRSessionId(id) {
    _currentLRSessionId = id;
}

// LR Bug 3 guard (2026-05-19 root fix): when an LR run is inflight, refuse to
// clear the session id from background reset paths (UserStateSync.clearUserScopedState
// / runInitSync / 401-refresh-fail). The next continue POST relies on this id to
// hit the same backend lr_session row; wiping it produces R5「找不到先前的研究 session」.
//
// Callers that legitimately end an LR run (Stage 6 export, resetLiveResearchUI for
// fresh start) call clearLRSessionId() without the skipIfInflight flag → unconditional.
// Background reset paths pass { skipIfInflight: true } → guarded.
export function clearLRSessionId({ skipIfInflight = false } = {}) {
    if (skipIfInflight && _lrInProgress) {
        console.warn('[live-research] LR active — keeping currentLRSessionId across reset (lr_session_id=' + _currentLRSessionId + ')');
        return;
    }
    _currentLRSessionId = null;
}

// ============================================================================
// lrInProgress — LR run-in-progress flag (Bug 3 guard input)
// ============================================================================
let _lrInProgress = false;

export function isLRInProgress() {
    return _lrInProgress;
}

export function setLRInProgress(b) {
    _lrInProgress = !!b;
}

// ============================================================================
// _lrSwitchToken — self-contained session-switch race guard (B5, 2026-06-05)
//   Incremented each time a new session-switch restore is scheduled.
//   restoreLRCheckpointFromState checks if the token it received is still
//   current; if not (a newer switch has been scheduled) it bails out early.
//   Does NOT affect page-load single restore or #19 resume (token null path).
// ============================================================================
let _lrSwitchToken = 0;

export function bumpLRSwitchToken() {
    return ++_lrSwitchToken;
}

export function getLRSwitchToken() {
    return _lrSwitchToken;
}

// ============================================================================
// LR Typing Indicator state (UX-1 D-3 + D-6 stage-aware)
// ============================================================================
let _currentLRStage = 0;          // 0 = not started
let _currentLRActivity = '';      // override text; empty → use stage default
// Bug fix 2026-05-16：追蹤 reply UI 是否該顯示。true = 後端在等 user reply（
// checkpoint emit 後、continueLiveResearch 送出前）。narration handler 若見此旗標
// 仍為 true 而 reply UI 被隱藏，則重新顯示（防 backend 漏 emit checkpoint 卡死）。
let _lrAwaitingCheckpointReply = false;

// Default text per stage (plan D-6 table)
const LR_STAGE_DEFAULT_TEXT = {
    0: '讀豹思考中...',
    1: '正在規劃研究方向...',
    2: '正在蒐集資料...',
    3: '分析文筆風格...',
    4: '整理報告格式...',
    5: '正在撰寫報告...',
    6: '整理匯出內容...'
};

// ============================================================================
// D-V3 backref inject — install live closures into state-sync.js for the
// IIFE relocate (commit 11). Top-level call is the ONE permitted side effect per
// module header rationale above.
// ============================================================================
injectStateSyncBackref({
    isLRInProgress,
    getLRSessionId,
    clearLRSessionId,
});

// ============================================================================
// UI helpers
// ============================================================================

export function resetLiveResearchUI() {
    console.log('[Live Research] Resetting UI');
    // v4.0 Commit 7: unconditional clear (resetLiveResearchUI is a fresh-start path,
    // not a background reset — skipIfInflight not used).
    clearLRSessionId();
    // LR Bug 3 fix (2026-05-19): reset inflight flag on fresh LR UI reset.
    // performLiveResearch() flips it back to true immediately after this call.
    setLRInProgress(false);
    const chat = document.getElementById('lrChat');
    if (chat) chat.innerHTML = '';

    const sections = document.getElementById('lrSections');
    if (sections) { sections.innerHTML = ''; sections.style.display = 'none'; }

    const exportEl = document.getElementById('lrExport');
    if (exportEl) { exportEl.innerHTML = ''; exportEl.style.display = 'none'; }

    const reply = document.getElementById('lrCheckpointReply');
    if (reply) reply.style.display = 'none';
    // Bug fix 2026-05-16: reset awaiting flag on UI reset
    _lrAwaitingCheckpointReply = false;

    // Reset all stage dots
    document.querySelectorAll('.lr-stage-dot').forEach(dot => {
        dot.classList.remove('active', 'completed');
    });
    document.querySelectorAll('.lr-stage-connector').forEach(conn => {
        conn.classList.remove('completed');
    });
    document.querySelectorAll('.lr-stage-labels span').forEach(lbl => {
        lbl.classList.remove('active', 'completed');
    });

    // Track D D2b Step 6 (sprint 2026-05-28, fix-up round 1 C-3 + kg-spec §8.4):
    // cross-session LR KG container reset — 防 user 重啟 LR / 切 session 後 KG 殘留
    // (沿 memory/lessons-frontend.md「KG cross-session 殘留 bug」紀律)
    // 操作 #lrKGDisplayContainer + 移除 SVG (D3 render 殘留)
    const lrKGContainer = document.getElementById('lrKGDisplayContainer');
    if (lrKGContainer) {
        lrKGContainer.style.display = 'none';
        const lrKGGraph = document.getElementById('lrKGGraphView');
        if (lrKGGraph) {
            const oldSvg = lrKGGraph.querySelector('svg');
            if (oldSvg) oldSvg.remove();
        }
        const lrKGContent = document.getElementById('lrKGDisplayContent');
        if (lrKGContent) lrKGContent.innerHTML = '';
        const lrKGMeta = document.getElementById('lrKGMetadata');
        if (lrKGMeta) lrKGMeta.textContent = '';
        const lrKGLegend = document.getElementById('lrKGLegend');
        if (lrKGLegend) lrKGLegend.innerHTML = '';
    }
    // Hide restoreBar (若 user 之前 Hide 過 KG, 新 session 不該顯示舊 restore bar)
    const lrKGRestoreBar = document.getElementById('lrKGRestoreBar');
    if (lrKGRestoreBar) lrKGRestoreBar.style.display = 'none';
}

// G-M1 (2026-05-29): 加 options.dataset 參數，讓呼叫端注入 data-* attributes，
// 消除 SSE live_research_section handler 內 inline 重複的 avatar/wrapper DOM 結構。
// 呼叫端可傳 { dataset: { lrSectionIndex: '0' } }，wrapper.dataset 會被一次性 assign。
// 無 options 或空 dataset 時行為與既有完全相同（backward-compat）。
export function addLRChatMessage(type, text, options = {}) {
    const chat = document.getElementById('lrChat');
    if (!chat) return;

    const { dataset = {} } = options;

    const avatarMap = { narration: '&#x1F43E;', user: '&#x1F464;', system: '&#x2139;&#xFE0F;', error: '&#x26A0;', checkpoint: '&#x1F43E;', section: '&#x1F4DD;' };
    const avatarHTML = avatarMap[type] || '&#x2022;';

    const wrapper = document.createElement('div');
    wrapper.className = `lr-chat-message ${type}`;

    // 注入 dataset attributes（G-M1：讓 section bubble 帶 data-lr-section-index）
    Object.entries(dataset).forEach(([k, v]) => { wrapper.dataset[k] = v; });

    let bubbleInner = '';
    if (type === 'checkpoint') {
        // text is expected to be raw HTML string (built by showLRCheckpoint)
        bubbleInner = text;
    } else {
        bubbleInner = DOMPurify.sanitize(marked.parse(String(text)));
    }

    if (type === 'user') {
        wrapper.innerHTML = `
            <div class="lr-msg-bubble">${DOMPurify.sanitize(String(text))}</div>
            <div class="lr-msg-avatar">${avatarHTML}</div>`;
    } else {
        wrapper.innerHTML = `
            <div class="lr-msg-avatar">${avatarHTML}</div>
            <div class="lr-msg-bubble">${bubbleInner}</div>`;
    }

    chat.appendChild(wrapper);
    chat.scrollTop = chat.scrollHeight;
}

export function showLRTypingIndicator() {
    const chat = document.getElementById('lrChat');
    if (!chat) return;
    hideLRTypingIndicator();
    const ind = document.createElement('div');
    ind.id = 'lrTypingIndicator';
    ind.className = 'lr-typing-indicator active';
    const text = _currentLRActivity || LR_STAGE_DEFAULT_TEXT[_currentLRStage] || LR_STAGE_DEFAULT_TEXT[0];
    ind.innerHTML = `
        <span class="lr-typing-dot"></span>
        <span class="lr-typing-dot"></span>
        <span class="lr-typing-dot"></span>
        <span id="lrTypingText">${DOMPurify.sanitize(String(text))}</span>`;
    chat.appendChild(ind);
    chat.scrollTop = chat.scrollHeight;
}

export function hideLRTypingIndicator() {
    // Remove all (defensive against any leak)
    document.querySelectorAll('#lrTypingIndicator').forEach(el => el.remove());
}

export function updateLRTypingIndicatorText() {
    const textEl = document.getElementById('lrTypingText');
    if (!textEl) return;
    const text = _currentLRActivity || LR_STAGE_DEFAULT_TEXT[_currentLRStage] || LR_STAGE_DEFAULT_TEXT[0];
    textEl.textContent = String(text);
}

// Reset typing-indicator state when starting/finishing a LR run
export function resetLRTypingState() {
    _currentLRStage = 0;
    _currentLRActivity = '';
}

// D-6: Derive activity text from narration string.
// Looks for "開始蒐集 X" / "X 蒐集完成" / writer pattern hints.
export function deriveActivityFromNarration(narrationText) {
    if (!narrationText) return '';
    const s = String(narrationText).trim();

    // Stage 2 — "開始蒐集 X" → "正在蒐集資料：X..."
    let m = s.match(/^開始蒐集\s*(.+?)$/);
    if (m) return `正在蒐集資料：${m[1].trim()}...`;

    // Stage 2 — "X 蒐集完成" → "X 蒐集完成..."
    m = s.match(/^(.+?)\s*蒐集完成$/);
    if (m) return `${m[1].trim()} 蒐集完成...`;

    // Stage 5 — writer hints (fallback; UX-4 writer_status preferred)
    m = s.match(/正在寫.*?第\s*(\d+)\s*\/\s*(\d+)\s*段/);
    if (m) return `正在寫第 ${m[1]}/${m[2]} 段...`;

    return '';
}

export function updateLRStageProgress(stage) {
    const stageNum = parseInt(stage, 10);
    if (!stageNum || stageNum < 1 || stageNum > 6) return;
    console.log('[Live Research] Stage change:', stageNum);

    document.querySelectorAll('.lr-stage-dot').forEach(dot => {
        const n = parseInt(dot.dataset.stage, 10);
        dot.classList.remove('active', 'completed');
        if (n < stageNum) dot.classList.add('completed');
        else if (n === stageNum) dot.classList.add('active');
    });

    // Connectors: complete connectors before the active stage
    const connectors = document.querySelectorAll('.lr-stage-connector');
    connectors.forEach((conn, idx) => {
        // connector idx 0 is between stage 1 and 2, etc.
        conn.classList.toggle('completed', idx + 1 < stageNum);
    });

    // Labels
    document.querySelectorAll('.lr-stage-labels span').forEach(lbl => {
        const n = parseInt(lbl.dataset.stage, 10);
        lbl.classList.remove('active', 'completed');
        if (n < stageNum) lbl.classList.add('completed');
        else if (n === stageNum) lbl.classList.add('active');
    });
}

// ============================================================================
// G3：Legacy session gate — v1 session 唯讀 UI 鎖定 + Modal CTA
// ============================================================================

// 當前 session 是否為 legacy（v1）唯讀
let _lrSessionIsLegacy = false;
// 最後載入的 LR session 原始 query（供 modal 「用同 query 開新研究」功能使用）
let _lrLegacySessionQuery = '';

export function setLRLegacyMode(isLegacy, query) {
    _lrSessionIsLegacy = !!isLegacy;
    _lrLegacySessionQuery = query || '';
}

export function isLRSessionLegacy() {
    return _lrSessionIsLegacy;
}

/**
 * G3：鎖定舊版 LR session 的 revise/continue UI
 * 呼叫時機：loadSavedSession 中偵測到 schema_version < 2 時
 */
export function lockLRUIForLegacySession() {
    // 鎖定 checkpoint reply 區
    const replyEl = document.getElementById('lrCheckpointReply');
    if (replyEl) {
        const input = document.getElementById('lrReplyInput');
        const replyBtn = document.getElementById('lrBtnReply');
        const autoBtn = document.getElementById('lrBtnAutoContine');
        if (input) {
            input.disabled = true;
            input.placeholder = '此 session 為舊版，已封存唯讀，請匯出後開啟新 session';
        }
        if (replyBtn) {
            replyBtn.disabled = true;
            replyBtn.title = '此 session 為舊版，已封存唯讀，請匯出後開啟新 session';
            replyBtn.style.opacity = '0.4';
            replyBtn.style.cursor = 'not-allowed';
            replyBtn.addEventListener('click', (e) => { e.stopPropagation(); showLRReadonlyModal(); }, { once: false });
        }
        if (autoBtn) {
            autoBtn.disabled = true;
            autoBtn.title = '此 session 為舊版，已封存唯讀，請匯出後開啟新 session';
            autoBtn.style.opacity = '0.4';
            autoBtn.style.cursor = 'not-allowed';
            autoBtn.addEventListener('click', (e) => { e.stopPropagation(); showLRReadonlyModal(); }, { once: false });
        }
    }
}

/**
 * G3：顯示 legacy session 唯讀 modal CTA（CEO 拍板 2026-05-28）
 */
export function showLRReadonlyModal() {
    // 避免重複開 modal
    const existingModal = document.getElementById('lrLegacyReadonlyModal');
    if (existingModal) { existingModal.style.display = 'flex'; return; }

    const modal = document.createElement('div');
    modal.id = 'lrLegacyReadonlyModal';
    modal.className = 'lr-modal-overlay';
    modal.innerHTML = `
        <div class="lr-modal-box">
            <div class="lr-modal-title">此 session 已升級為唯讀</div>
            <div class="lr-modal-body">
                系統已升級研究 pipeline，本 session 為舊版 schema，無法繼續編輯或新增章節。<br>
                建議先匯出當前報告，再用同 query 開啟新研究 session（享受新版 grounding 紀律）。
            </div>
            <div class="lr-modal-actions">
                <button class="lr-btn-primary" id="lrModalBtnExport">匯出當前報告</button>
                <button class="lr-btn-secondary" id="lrModalBtnNewSession">用同 query 開新研究</button>
                <button class="lr-btn-cancel" id="lrModalBtnCancel">取消</button>
            </div>
        </div>`;

    document.body.appendChild(modal);

    // 匯出當前報告：觸發既有 export flow（找 download 按鈕或模擬點擊）
    document.getElementById('lrModalBtnExport')?.addEventListener('click', () => {
        modal.style.display = 'none';
        const dlBtn = document.getElementById('lrBtnDownload');
        if (dlBtn) { dlBtn.click(); }
        else {
            const cpBtn = document.getElementById('lrBtnCopyExport');
            if (cpBtn) cpBtn.click();
        }
    });

    // 用同 query 開新研究：預填 query 到搜尋框並切換到 live_research 模式
    document.getElementById('lrModalBtnNewSession')?.addEventListener('click', () => {
        modal.style.display = 'none';
        const searchInput = document.getElementById('searchInput');
        if (searchInput && _lrLegacySessionQuery) {
            searchInput.value = _lrLegacySessionQuery;
        }
        // 切換到 live_research 模式（觸發 mode button click）
        const lrModeBtn = document.querySelector('.mode-btn-inline[data-mode="live_research"]');
        if (lrModeBtn) lrModeBtn.click();
        // 提示使用者現在可以開始新研究
        console.info('[Live Research] Legacy session — user redirected to new research with query:', _lrLegacySessionQuery);
    });

    // 取消
    document.getElementById('lrModalBtnCancel')?.addEventListener('click', () => {
        modal.style.display = 'none';
    });
}

/**
 * renderEvidenceList — 把 evidence_list payload 渲染為可摺疊 HTML。
 *
 * P0 #5: 設計邊界已 CEO 拍板：
 *   邊界 A (llm_knowledge): Option a — 顯示「AI 背景知識」label，無超連結
 *   邊界 B (year): Option a — published_at 非 null 取前 4 字顯示
 *
 * @param {Array<{id,title,url,source_domain,published_at,source}>} evidenceList
 * @param {string} topicName  — 主題名稱（用於 summary label）
 * @returns {string} HTML string (caller must DOMPurify.sanitize before insert)
 */
function renderEvidenceList(evidenceList, topicName) {
    if (!Array.isArray(evidenceList) || evidenceList.length === 0) {
        return '';
    }

    const itemsHTML = evidenceList.map(e => {
        const isLLM = e.source === 'llm_knowledge';
        const titleSafe = escapeHTML(String(e.title || ''));
        const domainLabel = isLLM
            ? '<span class="lr-evidence-source-label lr-evidence-ai-tag">AI 背景知識</span>'
            : `<span class="lr-evidence-source-label">${escapeHTML(String(e.source_domain || e.source || ''))}</span>`;

        // 邊界 B: 顯示年份
        let yearLabel = '';
        if (e.published_at && typeof e.published_at === 'string' && e.published_at.length >= 4) {
            yearLabel = `<span class="lr-evidence-year">${escapeHTML(e.published_at.slice(0, 4))}</span>`;
        }

        if (isLLM) {
            // 邊界 A: llm_knowledge — 不可點，顯示 AI 背景知識 tag
            return `<li class="lr-evidence-item lr-evidence-ai">
                <span class="lr-evidence-title">${titleSafe}</span>
                ${domainLabel}${yearLabel}
            </li>`;
        } else {
            // internal / web / wiki — 真實 URL，可點
            const urlSafe = escapeHTML(String(e.url || ''));
            return `<li class="lr-evidence-item">
                <a class="lr-evidence-link" href="${urlSafe}" target="_blank" rel="noopener noreferrer">${titleSafe}</a>
                ${domainLabel}${yearLabel}
            </li>`;
        }
    }).join('');

    const countLabel = `${evidenceList.length} 筆資料`;
    const summaryLabel = topicName
        ? escapeHTML(String(topicName)) + ` — ${countLabel}`
        : countLabel;

    return `<details class="lr-evidence-details">
        <summary class="lr-evidence-summary">${summaryLabel}</summary>
        <ul class="lr-evidence-list">${itemsHTML}</ul>
    </details>`;
}

export function showLRCheckpoint(stage, proposal, autoOption, evidenceList) {
    console.log('[Live Research] Checkpoint at stage', stage, '— proposal:', proposal);
    // Ensure proposal is always rendered as string (guards against [object Object])
    const proposalText = typeof proposal === 'object' && proposal !== null
        ? (proposal.proposal || proposal.text || JSON.stringify(proposal, null, 2))
        : String(proposal || '');
    const proposalHTML = DOMPurify.sanitize(marked.parse(proposalText));
    const autoLabel = autoOption || '讀豹自動決定';

    // P0 #5: render evidence list if present (stage 1 / stage 2 checkpoint)
    let evidenceHTML = '';
    if (Array.isArray(evidenceList) && evidenceList.length > 0) {
        // Group by topic: stage 1 sends a flat list, render as single collapsible
        const rawEvidenceHTML = renderEvidenceList(evidenceList, '');
        evidenceHTML = DOMPurify.sanitize(rawEvidenceHTML);
    }

    const bubbleHTML = `
        <div class="lr-checkpoint-label">Checkpoint — 階段 ${stage}</div>
        <div class="lr-checkpoint-proposal">${proposalHTML}</div>
        ${evidenceHTML}`;

    addLRChatMessage('checkpoint', bubbleHTML);

    // Show reply UI
    const replyEl = document.getElementById('lrCheckpointReply');
    if (replyEl) {
        replyEl.style.display = '';
        const input = document.getElementById('lrReplyInput');
        if (input) { input.value = ''; input.focus(); }
        const autoBtn = document.getElementById('lrBtnAutoContine');
        if (autoBtn) autoBtn.textContent = (typeof autoLabel === 'string' && autoLabel) ? autoLabel : '讀豹決定';
    }
    // Bug fix 2026-05-16: mark we are awaiting reply
    _lrAwaitingCheckpointReply = true;
}

export function addLRSection(index, title, content, sources, methodologyNote) {
    console.log('[Live Research] Section', index, ':', title);
    const sectionsEl = document.getElementById('lrSections');
    if (!sectionsEl) return;
    sectionsEl.style.display = '';

    const bodyHTML = DOMPurify.sanitize(marked.parse(String(content || '')));
    const sectionNumDisplay = parseInt(index, 10) + 1 || '?';
    const titleSafe = DOMPurify.sanitize(String(title || ''));

    // L3 WARN marker: methodology_note (若非空) 顯示成 ⚠ 提示列
    const warnHTML = methodologyNote
        ? `<div class="lr-section-warn"><span class="lr-section-warn-icon">⚠</span><span class="lr-section-warn-text">${escapeHTML(String(methodologyNote))}</span></div>`
        : '';

    // G1 upsert：先查同 index 既有卡片，找到則 update-in-place，找不到才 append
    // 防止 writer revise / Critic REJECT 重跑同 section_index 時疊加噪音卡片
    const existing = sectionsEl.querySelector(`[data-lr-section-index="${index}"]`);
    if (existing) {
        // Update-in-place：只更新 body（保留折疊狀態）
        const bodyEl = existing.querySelector('.lr-section-body');
        if (bodyEl) bodyEl.innerHTML = bodyHTML;
        // 同步更新 title（revise 可能改題目）
        const titleEl = existing.querySelector('.lr-section-title');
        if (titleEl) titleEl.textContent = title || '';
        // 同步更新 WARN marker（revise 後 critic 可能通過或持續警告）
        const existingWarn = existing.querySelector('.lr-section-warn');
        if (warnHTML) {
            if (existingWarn) {
                existingWarn.outerHTML = warnHTML;
            } else {
                // 插在 body 上方
                const bodyEl2 = existing.querySelector('.lr-section-body');
                if (bodyEl2) bodyEl2.insertAdjacentHTML('beforebegin', warnHTML);
            }
        } else if (existingWarn) {
            existingWarn.remove();
        }
        existing.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        return;
    }

    const card = document.createElement('div');
    card.className = 'lr-section-card';
    // G1：加 data attribute 供後續 upsert 查詢 + G2 collapse 使用
    card.dataset.lrSectionIndex = index;

    // Bug 1 root fix (2026-05-18)：移除段末「來源：[1] [2]」force-append div。
    // Inline [N] citation（renderLRCitations）已 clickable，Stage 6 export
    // 末尾有 references master list。Per-section card 再列一次 = 冗餘 + 視覺
    // 突兀（CEO 觀察「placeholder 樣、跟內文無關」）。
    card.innerHTML = `
        <div class="lr-section-header" title="點擊折疊／展開">
            <div class="lr-section-num">${sectionNumDisplay}</div>
            <div class="lr-section-title">${titleSafe}</div>
            <span class="lr-collapse-icon">▼</span>
        </div>
        ${warnHTML}<div class="lr-section-body">${bodyHTML}</div>`;

    // G2：掛 click handler 到 header 實現折疊
    const header = card.querySelector('.lr-section-header');
    const icon = card.querySelector('.lr-collapse-icon');
    const body = card.querySelector('.lr-section-body');
    header.addEventListener('click', () => {
        const isCollapsed = card.classList.toggle('collapsed');
        icon.textContent = isCollapsed ? '▶' : '▼';
        if (isCollapsed) {
            body.style.maxHeight = '0';
            body.style.overflow = 'hidden';
        } else {
            body.style.maxHeight = '';
            body.style.overflow = '';
        }
    });

    sectionsEl.appendChild(card);

    // G2：第一段 section 出現時初始化 toolbar（toolbar 只建一次）
    if (!sectionsEl.querySelector('.lr-toggle-all-toolbar')) {
        _addLRToggleAllToolbar(sectionsEl);
    }

    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

/**
 * G2：「全部折疊 / 全部展開」toolbar（DR parity）。
 * 插入在 sectionsEl 頂部，第一個 section 出現時呼叫一次。
 */
function _addLRToggleAllToolbar(sectionsEl) {
    const toolbar = document.createElement('div');
    toolbar.className = 'lr-toggle-all-toolbar';
    let allCollapsed = false;
    const btn = document.createElement('button');
    btn.className = 'lr-btn-toggle-all';
    btn.textContent = '全部折疊';
    btn.addEventListener('click', () => {
        allCollapsed = !allCollapsed;
        sectionsEl.querySelectorAll('.lr-section-card').forEach(c => {
            const icon = c.querySelector('.lr-collapse-icon');
            const body = c.querySelector('.lr-section-body');
            if (allCollapsed) {
                c.classList.add('collapsed');
                if (icon) icon.textContent = '▶';
                if (body) { body.style.maxHeight = '0'; body.style.overflow = 'hidden'; }
            } else {
                c.classList.remove('collapsed');
                if (icon) icon.textContent = '▼';
                if (body) { body.style.maxHeight = ''; body.style.overflow = ''; }
            }
        });
        btn.textContent = allCollapsed ? '全部展開' : '全部折疊';
    });
    toolbar.appendChild(btn);
    sectionsEl.insertBefore(toolbar, sectionsEl.firstChild);
}

// Track D D2b (sprint 2026-05-28): LR KG full D3 graph render — reuse DR module
// D-CEO-Q1 LOCKED Option (a): 複用 DR displayKnowledgeGraph + prefix 化
// D-CEO-Q4 LOCKED Option (β): LR 獨立 #lrKGDisplayContainer + DR module 參數化
// (D2a placeholder DOM list 已刪除, displayLRKnowledgeGraph 被取代)
import { displayKnowledgeGraph } from './knowledge-graph.js';

// Track D D2b Step 1b (sprint 2026-05-28, fix-up round 1 C-2 / R2-I2):
// 移植 DR initKGVisibilityToggle IIFE 到 LR (來源 static/news-search.js:3285-3323)
// D-CEO-Q3 LOCKED: LR ≥ DR 互動 — (e) Hide/Restore + localStorage 持久化必收
// localStorage key 區別: DR 用 'nlweb-kg-hidden', LR 用 'nlweb-lr-kg-hidden'
// — 兩 tab 獨立 hidden state, user 在 DR 收起不影響 LR; 反之亦然
(function initLRKGVisibilityToggle() {
    document.addEventListener('DOMContentLoaded', () => {
        const hideBtn = document.getElementById('lrKGHideBtn');
        const restoreBar = document.getElementById('lrKGRestoreBar');
        const kgContainer = document.getElementById('lrKGDisplayContainer');
        if (!hideBtn || !restoreBar || !kgContainer) return;

        // Restore preference
        let kgHidden = false;
        try {
            kgHidden = localStorage.getItem('nlweb-lr-kg-hidden') === 'true';
        } catch (e) { /* localStorage unavailable */ }

        // Apply stored preference: if hidden, mark container userHidden so restoreBar
        // shows when displayKnowledgeGraph(kg, {containerPrefix:'lrKG'}) is called
        if (kgHidden) {
            kgContainer.dataset.userHidden = 'true';
        }

        hideBtn.addEventListener('click', () => {
            kgContainer.style.display = 'none';
            kgContainer.dataset.userHidden = 'true';
            restoreBar.style.display = 'block';
            try {
                localStorage.setItem('nlweb-lr-kg-hidden', 'true');
            } catch (e) {}
        });

        // DR pattern: restoreBar 整個 div 是 clickable (parallel news-search.js:3314)
        restoreBar.addEventListener('click', () => {
            kgContainer.style.display = 'block';
            kgContainer.dataset.userHidden = 'false';
            restoreBar.style.display = 'none';
            try {
                localStorage.setItem('nlweb-lr-kg-hidden', 'false');
            } catch (e) {}
        });
    });
})();

// Track D D2b Step 1c (sprint 2026-05-28, fix-up round 2 R2-C1):
// 移植 DR kgToggleButton collapse handler 到 LR (來源 static/news-search.js:2376-2390)
// D-CEO-Q3 LOCKED: LR ≥ DR 互動 — (m) Collapse with header retained 必收
// Collapse 與 Hide/Restore 是兩個不同 UX:
//   - Collapse: 保留 header bar 可見, 收合 #lrKGContentWrapper (graph + popovers)
//   - Hide: 整個 #lrKGDisplayContainer 隱藏 + #lrKGRestoreBar 顯示
// localStorage key 'nlweb-lr-kg-collapsed' — LR > DR 擴張 (DR 原版未持久化)
// 沿 D-CEO-Q3 LOCKED「更多」= LR 允許優於 DR (R3 NF#3 紀律驗 DR 無 'nlweb-kg-collapsed' key)
(function initLRKGCollapseToggle() {
    document.addEventListener('DOMContentLoaded', () => {
        const toggleButton = document.getElementById('lrKGToggleButton');
        const wrapper = document.getElementById('lrKGContentWrapper');
        const icon = document.getElementById('lrKGToggleIcon');
        if (!toggleButton || !wrapper) return;

        // Reload 復原 collapsed 狀態
        let isCollapsed = false;
        try {
            isCollapsed = localStorage.getItem('nlweb-lr-kg-collapsed') === 'true';
        } catch (e) { /* localStorage 不可用降級 */ }
        if (isCollapsed) {
            wrapper.style.display = 'none';
            if (icon) icon.textContent = '▶';
            if (toggleButton.childNodes[1]) toggleButton.childNodes[1].textContent = ' 展開';
        }

        toggleButton.addEventListener('click', () => {
            const collapsedNow = wrapper.style.display === 'none';
            wrapper.style.display = collapsedNow ? '' : 'none';
            if (icon) icon.textContent = collapsedNow ? '▼' : '▶';
            if (toggleButton.childNodes[1]) {
                toggleButton.childNodes[1].textContent = collapsedNow ? ' 收起' : ' 展開';
            }
            try {
                localStorage.setItem('nlweb-lr-kg-collapsed', collapsedNow ? 'false' : 'true');
            } catch (e) {}
        });
    });
})();

export function showLRExport(content, format) {
    console.log('[Live Research] Export ready, format:', format);
    const exportEl = document.getElementById('lrExport');
    if (!exportEl) return;
    exportEl.style.display = '';

    const fmt = (format || 'markdown').toLowerCase();
    let bodyHTML;
    if (fmt === 'markdown' || fmt === 'md') {
        bodyHTML = DOMPurify.sanitize(marked.parse(String(content || '')));
    } else {
        bodyHTML = `<pre style="white-space:pre-wrap;word-break:break-word;">${DOMPurify.sanitize(String(content || ''))}</pre>`;
    }

    exportEl.innerHTML = `
        <div class="lr-export-title">研究報告匯出</div>
        <div class="lr-export-content">${bodyHTML}</div>
        <div class="lr-export-actions">
            <button class="lr-btn-primary" id="lrBtnDownload">下載報告</button>
            <button class="lr-btn-secondary" id="lrBtnCopyExport">複製文字</button>
        </div>`;

    // Bind download/copy
    const rawContent = String(content || '');
    const dl = document.getElementById('lrBtnDownload');
    if (dl) {
        dl.addEventListener('click', () => {
            const blob = new Blob([rawContent], { type: 'text/markdown;charset=utf-8' });
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'live-research-report.md';
            a.click();
            URL.revokeObjectURL(a.href);
        });
    }
    const cp = document.getElementById('lrBtnCopyExport');
    if (cp) {
        cp.addEventListener('click', () => {
            navigator.clipboard.writeText(rawContent).then(() => {
                cp.textContent = '已複製';
                setTimeout(() => { cp.textContent = '複製文字'; }, 2000);
            }).catch(err => console.error('[Live Research] Copy failed:', err));
        });
    }

    exportEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ============================================================================
// Session resume — pure DOM restore (LR #19)
// ============================================================================

/**
 * Restore the LR UI from a persisted liveResearchState (resume path).
 *
 * INVARIANT: This function NEVER calls performLiveResearch / continueLiveResearch
 * / makes any HTTP request. It is a pure DOM restore used by loadSavedSession.
 *
 * @param {Object} lrState - Deserialized liveResearchState from server
 *   (snake_case keys, as returned by GET /api/sessions/{id})
 */
export function restoreLRCheckpointFromState(lrState, lrServerId = null, switchToken = null) {
    // B5: stale restore guard — if a newer session-switch has been scheduled,
    // this call is outdated; bail out silently. Only applies when switchToken is
    // supplied (session-switch path). Single-restore paths (page load, #19 resume)
    // call without switchToken (null) and are unaffected.
    if (switchToken !== null && switchToken !== getLRSwitchToken()) {
        console.log('[LR] stale restore skipped (token', switchToken, '!= current', getLRSwitchToken(), ')');
        return;
    }

    if (!lrState || typeof lrState !== 'object') {
        console.warn('[Live Research] restoreLRCheckpointFromState: no valid state, skipping restore');
        return;
    }

    const stage = lrState.current_stage ?? 0;
    const status = lrState.stage_status ?? '';

    console.log('[Live Research] restoreLRCheckpointFromState: stage=', stage, 'status=', status);

    // Reset LR UI containers to clean state (no SSE stream start)
    resetLiveResearchUI();

    // LR #19 順序修正：setLRSessionId 必須在 resetLiveResearchUI() 之後，
    // 否則 reset 的 unconditional clearLRSessionId() 會把它清成 null，
    // 導致 continue 送 lr_session_id=null → 後端 fallback analytics id → crash。
    if (lrServerId) {
        setLRSessionId(lrServerId);
        console.log('[Live Research] resume: setLRSessionId (after reset) =', lrServerId);
    }

    // Show LR tab and make results section active (mirrors performLiveResearch setup)
    const resultsSection = document.getElementById('resultsSection');
    if (resultsSection) resultsSection.classList.add('active');
    const lrTab = document.querySelector('.tab[data-view="live-research"]');
    if (lrTab) lrTab.click();

    // Hide main search input (LR uses lrCheckpointReply, same as during active LR)
    const searchContainer = document.getElementById('searchContainer');
    if (searchContainer) searchContainer.style.display = 'none';

    const resumeClass = classifyLRResumeState(stage, status);
    if (resumeClass === 'completed' || resumeClass === 'not_started') {
        // No reply box for terminal states. Completed = export already done;
        // not_started = empty session that should not resume mid-flow.
        const doneText = resumeClass === 'completed'
            ? '此 Live 研究已完成匯出。如需重新研究，請開新對話。'
            : '此 Live 研究 session 尚未開始。請開新對話。';
        addLRChatMessage('assistant', `<em>${escapeHTML(doneText)}</em>`);
        return;
    }
    // resumeClass === 'in_progress' falls through to the resume-notice path below.

    // Build stage-appropriate resume notice
    const stageLabels = {
        1: '階段 1 — 研究架構提案',
        2: '階段 2 — 資料蒐集與分析',
        3: '階段 3 — 文筆風格設定',
        4: '階段 4 — 章節大綱',
        5: '階段 5 — 章節撰寫',
        6: '階段 6 — 匯出',
    };
    const stageLabel = stageLabels[stage] || `階段 ${stage}`;
    const resumeNotice = `**（從中斷處繼續）${stageLabel}**\n\n` +
        `你的研究進度已保存。你可以：\n` +
        `- 直接輸入訊息繼續這個階段\n` +
        `- 按「讀豹決定」讓讀豹自動繼續`;

    // Show checkpoint bubble with resume notice + reply UI
    showLRCheckpoint(stage, resumeNotice, '讀豹決定', null);

    // If Stage 5 and there are written sections, re-render them
    if (stage === 5) {
        const writtenSections = lrState.written_sections ?? [];
        writtenSections.forEach((section, idx) => {
            if (section && section.content) {
                addLRSection(
                    idx,
                    section.title || `第 ${idx + 1} 段`,
                    section.content,
                    section.sources ?? [],
                    section.methodology_note ?? null
                );
            }
        });
    }
}

// ============================================================================
// SSE handler
// ============================================================================

export async function handleLiveResearchSSE(response) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    const loadingState = document.getElementById('loadingState');

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });

            const messages = buffer.split('\n\n');
            buffer = messages.pop(); // keep incomplete message

            for (const message of messages) {
                if (!message.trim()) continue;

                const lines = message.split('\n');
                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue;
                    let data;
                    try {
                        data = JSON.parse(line.slice(6));
                    } catch (e) {
                        console.error('[Live Research] Failed to parse SSE:', e, line);
                        continue;
                    }

                    console.log('[Live Research] SSE event:', data.message_type || data.type, data);

                    const type = data.message_type || data.type || '';

                    if (type === 'live_research_session_created') {
                        setLRSessionId(data.session_id);
                        console.log('[Live Research] Server session created:', getLRSessionId());
                        // LR 雙 row 收斂：把後端 row A UUID 採納為當前 session 的 _serverId，
                        // 取消前端冗餘 POST（防 row B），使 live_research_state 與 session payload 同 row。
                        if (typeof window.adoptLRServerSession === 'function') {
                            window.adoptLRServerSession(data.session_id);
                        } else {
                            console.warn('[Live Research] window.adoptLRServerSession 未掛載，雙 row 收斂跳過（resume 可能失效）');
                        }

                    } else if (type === 'live_research_narration') {
                        // UX-1 D-6: derive dynamic activity text from narration content
                        const narrText = data.text || '';
                        const derivedActivity = deriveActivityFromNarration(narrText);
                        if (derivedActivity) _currentLRActivity = derivedActivity;
                        hideLRTypingIndicator();
                        addLRChatMessage('narration', narrText);
                        showLRTypingIndicator();
                        // Bug fix 2026-05-16 (defensive): if we are still awaiting a
                        // checkpoint reply but the reply UI got hidden by
                        // continueLiveResearch and the backend forgot to re-emit a
                        // checkpoint, restore the reply UI so user is not stuck.
                        if (_lrAwaitingCheckpointReply) {
                            const replyEl = document.getElementById('lrCheckpointReply');
                            if (replyEl && replyEl.style.display === 'none') {
                                console.warn('[Live Research] narration received while awaiting reply — restoring reply UI defensively');
                                replyEl.style.display = '';
                                const input = document.getElementById('lrReplyInput');
                                if (input) { input.value = ''; input.focus(); }
                            }
                        }

                    } else if (type === 'live_research_stage_change') {
                        // UX-1 D-6: update stage-aware indicator text
                        const stageNum = parseInt(data.stage, 10);
                        if (stageNum >= 1 && stageNum <= 6) {
                            _currentLRStage = stageNum;
                            _currentLRActivity = '';  // reset activity when stage changes
                            updateLRTypingIndicatorText();
                        }
                        updateLRStageProgress(data.stage);

                    } else if (type === 'live_research_writer_status') {
                        // Stage 5 per-section writer status — typing indicator updates.
                        // Schema (backend orchestrator._emit_writer_status):
                        //   started:      { status, total_sections, completed }
                        //   section_done: { status, total_sections, completed, section_title }
                        //   all_done:     { status, total_sections, completed }
                        // Note: stop button removed (2026-06-04). VP-7 per-section
                        // checkpoint is the sole interruption path.
                        const wStatus = data.status;
                        const wTotal = data.total_sections;
                        const wDone = data.completed;
                        const wTitle = data.section_title;

                        if (wStatus === 'started') {
                            if (wTotal) {
                                _currentLRActivity = `正在寫第 ${wDone || 0}/${wTotal} 段...`;
                                updateLRTypingIndicatorText();
                            }
                        } else if (wStatus === 'section_done') {
                            // VP-7：每段完成後 writer 進 paused 狀態等 user reply。
                            // mini-checkpoint 緊接著會 emit，showLRCheckpoint() 處理 reply UI。
                            if (wTotal) {
                                _currentLRActivity = wTitle
                                    ? `第 ${wDone}/${wTotal} 段「${wTitle}」完成，等你回覆...`
                                    : `第 ${wDone}/${wTotal} 段完成，等你回覆...`;
                                updateLRTypingIndicatorText();
                            }
                        }
                        // all_done: typing indicator hide handled by subsequent checkpoint/export event

                    } else if (type === 'live_research_checkpoint') {
                        if (loadingState) loadingState.classList.remove('active');
                        hideLRTypingIndicator();
                        showLRCheckpoint(
                            data.stage,
                            data.proposal,
                            data.auto_continue_option,
                            data.evidence_list || []  // P0 #5: pass evidence_list ([] on old sessions)
                        );

                    } else if (type === 'live_research_section') {
                        addLRSection(data.section_index, data.title, data.content, data.sources, data.methodology_note);
                        // CEO P0 UX fix (2026-05-19): inline render prose in chat so user
                        // can review without switching tabs. Without this, user only sees
                        // "第 K/N 段完成" narration + checkpoint reply UI — cannot give
                        // revise feedback on prose they never saw (Stage 5 spec §4.7.1).
                        // G1 upsert：chat bubble 同理，同 section_index 已存在則 update-in-place
                        // G-M1 refactor (2026-05-29): 改用 addLRChatMessage(options.dataset)
                        // 消除 inline 重複的 avatar/wrapper DOM 結構；若 section_index 已在 DOM
                        // 中（revise 重跑），直接 update-in-place bubble 內容，不走 addLRChatMessage。
                        try {
                            const sectionMd = `**第 ${(parseInt(data.section_index, 10) + 1) || '?'} 段：${data.title || ''}**\n\n${data.content || ''}`;
                            const chat = document.getElementById('lrChat');
                            const existingBubble = chat && chat.querySelector(`[data-lr-section-index="${data.section_index}"]`);
                            if (existingBubble) {
                                // Update-in-place：更新 bubble 內容（revise 重跑場景）
                                const bubbleEl = existingBubble.querySelector('.lr-msg-bubble');
                                if (bubbleEl) bubbleEl.innerHTML = DOMPurify.sanitize(marked.parse(sectionMd));
                            } else {
                                // 新 bubble：走 addLRChatMessage，由 options.dataset 注入 data-lr-section-index
                                addLRChatMessage('section', sectionMd, { dataset: { lrSectionIndex: data.section_index } });
                            }
                        } catch (e) {
                            console.warn('[Live Research] inline section render failed:', e);
                        }

                    } else if (type === 'live_research_export') {
                        if (loadingState) loadingState.classList.remove('active');
                        hideLRTypingIndicator();
                        setProcessingState(false);
                        showLRExport(data.content, data.format);
                        // Track D D2b (sprint 2026-05-28): KG full D3 graph render
                        // D-AMB-3 LOCKED Option (d) 雙路: SSE event 帶 knowledge_graph
                        // payload (None / dict)。
                        // D-CEO-Q4 LOCKED Option (β): containerPrefix='lrKG' →
                        // displayKnowledgeGraph 操作 #lrKGDisplayContainer 等 LR id
                        if (data.knowledge_graph) {
                            displayKnowledgeGraph(data.knowledge_graph, { containerPrefix: 'lrKG' });
                        }
                        // LR Bug 3 fix (2026-05-19): Stage 6 export emitted → LR run
                        // officially complete. Release the guard so subsequent
                        // UserStateSync resets (logout / user switch) can clear
                        // currentLRSessionId normally.
                        setLRInProgress(false);

                    } else if (type === 'research_phase') {
                        // FIX-7a (2026-05-29): bab_phase4 是每 iteration 都會觸發的中間事件，
                        // 不是整個研究的終止訊號。用 per-phase 的非終止字眼，避免 user 誤以為
                        // 研究已結束。「完成」→「本輪更新完成」（僅 bab_phase4）。
                        const phaseLabels = {
                            'bab_phase0': '建立初始研究結構',
                            'bab_phase1': '推導搜尋計畫',
                            'bab_phase2': '執行資料蒐集',
                            'bab_phase4': '本輪結構調整',
                        };
                        // bab_phase4 用非終止狀態字眼（本輪更新）；其他 phase 維持「完成」。
                        const statusLabels = {
                            'started': '開始中',
                            'completed': '完成',
                        };
                        const phase4StatusLabels = {
                            'started': '開始中',
                            'completed': '本輪更新完成',
                        };
                        const isBabPhase4 = data.phase === 'bab_phase4';
                        const phaseLabel = phaseLabels[data.phase] || data.phase || '';
                        const statusLabel = (isBabPhase4 ? phase4StatusLabels : statusLabels)[data.status] || data.status || '';
                        const txt = phaseLabel + (statusLabel ? '⋯' + statusLabel : '');
                        if (txt) addLRChatMessage('narration', txt);

                    } else if (type === 'error') {
                        console.error('[Live Research] Server error:', data.error || data.message);
                        hideLRTypingIndicator();
                        addLRChatMessage('error', data.error || data.message || '發生未知錯誤');

                    } else if (type === 'begin-nlweb-response' || type === 'begin_nlweb_response') {
                        if (data.conversation_id) setCurrentConversationId(data.conversation_id);

                    } else if (type === 'intermediate_result') {
                        // Ignore or show as narration
                        if (data.text) addLRChatMessage('narration', data.text);

                    } else if (type === 'clarification_required') {
                        console.log('[Live Research] Clarification required:', data.clarification);
                        // Build a user-friendly clarification message in Traditional Chinese
                        const clarObj = data.clarification;
                        let clarHTML;
                        if (typeof clarObj === 'object' && clarObj !== null && clarObj.questions) {
                            // Full structured clarification — render questions as list
                            const instruction = clarObj.instruction || '為了更精準地進行研究，請提供更多資訊：';
                            let questionsHTML = '';
                            clarObj.questions.forEach(q => {
                                questionsHTML += `<p><strong>${DOMPurify.sanitize(q.question || q.text || '')}</strong></p>`;
                                if (q.options && q.options.length) {
                                    questionsHTML += '<ul>';
                                    q.options.forEach(opt => {
                                        const optLabel = typeof opt === 'object' ? (opt.label || opt.text || String(opt)) : String(opt);
                                        questionsHTML += `<li>${DOMPurify.sanitize(optLabel)}</li>`;
                                    });
                                    questionsHTML += '</ul>';
                                }
                            });
                            clarHTML = `<div class="lr-clarification-block"><p>${DOMPurify.sanitize(instruction)}</p>${questionsHTML}<p class="lr-clarification-hint">請在下方輸入你的回答，讀豹會重新開始研究。</p></div>`;
                        } else {
                            // Simple text clarification
                            const clarText = typeof clarObj === 'object' && clarObj !== null
                                ? (clarObj.instruction || clarObj.text || clarObj.question || '讀豹需要更多資訊才能繼續研究。')
                                : String(clarObj || '讀豹需要更多資訊才能繼續研究。');
                            clarHTML = DOMPurify.sanitize(marked.parse(clarText));
                        }
                        // Display clarification in LR chat area
                        addLRChatMessage('checkpoint', clarHTML);
                        // Show reply UI so user can respond via continueLiveResearch
                        if (loadingState) loadingState.classList.remove('active');
                        hideLRTypingIndicator();
                        setProcessingState(false);
                        const replyEl = document.getElementById('lrCheckpointReply');
                        if (replyEl) {
                            replyEl.style.display = '';
                            const input = document.getElementById('lrReplyInput');
                            if (input) { input.value = ''; input.focus(); }
                        }
                    }
                }
            }
        }
    } catch (e) {
        if (e.name !== 'AbortError') {
            console.error('[Live Research] Stream error:', e);
            addLRChatMessage('error', 'SSE 串流中斷：' + e.message);
        }
    } finally {
        try { reader.cancel(); } catch (_) {}
        if (loadingState) loadingState.classList.remove('active');
        hideLRTypingIndicator();
        setProcessingState(false);
    }
}

// ============================================================================
// Main entry points
// ============================================================================

export async function performLiveResearch(query) {
    console.log('=== Live Research Mode (6-Stage) ===');

    // Reset UI
    resetLiveResearchUI();
    // LR Bug 3 fix (2026-05-19): mark LR inflight so UserStateSync.clearUserScopedState
    // (triggered by background 401 → refresh-fail → _handleAuthFailure) does NOT wipe
    // currentLRSessionId mid-run. Cleared on Stage 6 export, or fresh resetLiveResearchUI.
    setLRInProgress(true);
    const resultsSection = document.getElementById('resultsSection');
    if (resultsSection) resultsSection.classList.add('active');
    const lrTab = document.querySelector('.tab[data-view="live-research"]');
    if (lrTab) lrTab.click();
    // Hide main search input while LR is active — user interacts via lrCheckpointReply
    const searchContainer = document.getElementById('searchContainer');
    if (searchContainer) searchContainer.style.display = 'none';
    // LR uses in-chat typing indicator instead of top spinner (UX-1 D-3 + D-6)
    resetLRTypingState();
    showLRTypingIndicator();
    setProcessingState(true);

    // Save to history
    pushConversationHistory(query);
    markSessionDirty();  // RCA Fix 1: new live research query is new content
    if (typeof window.saveCurrentSession === 'function') window.saveCurrentSession();

    // Call new /api/live_research endpoint
    const searchParams = new URLSearchParams(window.location.search);
    const isMockLR = searchParams.get('mock_lr') === 'true';
    const isDryRunLR = searchParams.get('dry_run_lr') === 'true';
    const url = new URL('/api/live_research', window.location.origin);
    const body = JSON.stringify({
        query: query,
        session_id: getCurrentSessionId(),
        site: getSelectedSitesParam(),
        // F1 (2026-06-08): CEO 拍板 LR web search default-on（無 UI toggle）。
        // 解鎖 loop_engine 已 land 的 web 能力；內部仍由 analyst source_strategy
        // + Track C C3 國際 keyword 四閘 gate 智能觸發，純台灣題不會打 Google。
        enable_web_search: true,
        // F2 (2026-06-11): CEO 拍板 enable_gap_enrichment default-on（全 4 類一起開）。
        // 後端 loop_engine._process_gap_resolutions_lr 4 類（llm_knowledge / wikipedia /
        // web_search / internal）已 ready（commit 2427d515），前端補接線解鎖。
        // Default-on 紀律：gate/guard 類 default-off（安全閾值），enrichment 類 default-on
        // （符合 CEO 期望 prod 行為）。
        enable_gap_enrichment: true,
        ...(isMockLR ? { mock: true } : {}),
        ...(isDryRunLR ? { dry_run: 'true' } : {})
    });

    try {
        // RCA fix 2026-05-19: 改走 authenticatedFetch 啟用 401 refresh-then-retry，
        // 避免 idle 期間 access_token cookie 過期後 raw fetch 無 Bearer header
        // → middleware dev-bypass 靜默放行 → user_id='' → R5 narration。
        // authenticatedFetch 不會 await body（line 196/236 直接 return Response），
        // 對 SSE streaming 兼容。
        console.log('[Live Research] Initial via authenticatedFetch');
        const response = await window.authManager.authenticatedFetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: body
        });

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.message || `HTTP ${response.status}`);
        }

        await handleLiveResearchSSE(response);
    } catch (e) {
        console.error('[Live Research] Error:', e);
        const loadingState = document.getElementById('loadingState');
        if (loadingState) loadingState.classList.remove('active');
        hideLRTypingIndicator();
        setProcessingState(false);
        addLRChatMessage('error', '研究啟動失敗：' + e.message);
    }
}

export async function continueLiveResearch(userMessage, autoContinue) {
    console.log('[Live Research] Continue — autoContinue:', autoContinue, 'message:', userMessage);

    // LR Bug 3 fix (2026-05-19): re-assert inflight flag — continueLiveResearch is
    // called per mini-checkpoint reply throughout the LR run, all before Stage 6 export.
    setLRInProgress(true);
    // Hide reply UI
    const replyEl = document.getElementById('lrCheckpointReply');
    if (replyEl) replyEl.style.display = 'none';
    // Bug fix 2026-05-16: clear awaiting flag — server will re-emit checkpoint if still needed
    _lrAwaitingCheckpointReply = false;
    // LR uses in-chat typing indicator instead of top spinner (UX-1 D-3 + D-6)
    // Note: keep currentLRStage from prior stage_change; reset activity only
    _currentLRActivity = '';
    showLRTypingIndicator();

    // Show user's reply in chat
    if (!autoContinue && userMessage) {
        addLRChatMessage('user', userMessage);
    } else {
        addLRChatMessage('system', '（讀豹決定）');
    }

    const searchParamsContinue = new URLSearchParams(window.location.search);
    const isMockLR = searchParamsContinue.get('mock_lr') === 'true';
    const isDryRunLRContinue = searchParamsContinue.get('dry_run_lr') === 'true';
    const url = new URL('/api/live_research/continue', window.location.origin);
    const body = JSON.stringify({
        session_id: getCurrentSessionId(),     // frontend session ID (auth/analytics)
        lr_session_id: getLRSessionId(),       // server-generated UUID for state persistence
        user_message: userMessage || '',
        auto_continue: autoContinue || false,
        // F2 (2026-06-11): Stage 2 per-topic BAB 跑在 continue request（orchestrator.py:835）。
        // 修前 continue body 不帶這兩個 flag → handler.enable_web_search/enable_gap_enrichment
        // = False → Stage 2 BAB 永不跑 web/gap（re-audit B6）。修後補帶，與 performLiveResearch
        // 對齊——前端為 source of truth，不依賴 stage_state 持久化（形態 a 決策）。
        enable_web_search: true,
        enable_gap_enrichment: true,
        ...(isMockLR ? { mock: true } : {}),
        ...(isDryRunLRContinue ? { dry_run: 'true' } : {})
    });

    try {
        // RCA fix 2026-05-19: 改走 authenticatedFetch 啟用 401 refresh-then-retry。
        // 真因：idle 11+ 分鐘 cookie expire → raw fetch 無 Bearer header
        // → middleware dev-bypass 靜默放行 → user_id='' → 「找不到研究 session」。
        // 改 authenticatedFetch 後：401 自動 trigger refreshToken → 新 access_token
        // → retry continue → user_id 正確。Refresh fail 走 _handleAuthFailure
        // （Option C lrInProgress guard 保護 _user mutation）。
        // SSE 兼容性：authenticatedFetch 不 await body（line 196/236 直接 return
        // Response object），response.body.getReader() stream 可正常讀。
        console.log('[Live Research] Continue via authenticatedFetch');
        const response = await window.authManager.authenticatedFetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: body
        });

        if (response.status === 401) {
            // CEO P0 UX fix (2026-05-19): authenticatedFetch refresh-then-retry 失敗
            // → _handleAuthFailure 已 trigger（login modal shown + state reset）。
            // 顯示 friendly narration 而非 raw "HTTP 401" — 對齊 spec §5.3。
            const loadingState = document.getElementById('loadingState');
            if (loadingState) loadingState.classList.remove('active');
            hideLRTypingIndicator();
            setProcessingState(false);
            addLRChatMessage('error', '登入已過期，請重新登入後再繼續研究。');
            return;
        }

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.message || `HTTP ${response.status}`);
        }

        await handleLiveResearchSSE(response);
    } catch (e) {
        console.error('[Live Research] Continue error:', e);
        const loadingState = document.getElementById('loadingState');
        if (loadingState) loadingState.classList.remove('active');
        hideLRTypingIndicator();
        setProcessingState(false);
        addLRChatMessage('error', '繼續研究失敗：' + e.message);
    }
}
