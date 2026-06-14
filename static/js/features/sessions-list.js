// static/js/features/sessions-list.js
//
// D-1 Module Header — Sessions List (Path B narrow scope)
//   Owned state ownership (Phase 4a Path B):
//     - Module own RENDERING logic, sidebar tab interaction, sidebar dropdown
//       handlers, shared session click handler. NO state declarations in this
//       module — outer-scope `let` (savedSessions / currentLoadedSessionId /
//       _sessionDirty / _sharedSessionsCache / _sharedSessionsLoading) STAY in
//       news-search.js until UserStateSync IIFE relocates (Phase 7+). This
//       module READS state through window.* bridges only.
//
//   Triggers writing state through this module (forwarded to bridges):
//     - E (session click sidebar item): user clicks own session → calls
//       window.loadSavedSession bridge (Phase 4b moves)
//     - User CRUD (rename / delete / share toggle from sidebar dropdown):
//       calls window.startSidebarSessionRename / deleteSavedSession /
//       toggleSessionSharing bridges (Phase 4b/5 moves)
//     - Shared session click (組織空間): hydrate snake_case → camelCase + tag
//       _isShared=true → calls window.loadSavedSession (Phase 4b moves)
//     - A/B/D/F via applyInit: still owned by news-search.js UserStateSync IIFE
//       (Path B — IIFE in classic script). Renders re-triggered via existing
//       CustomEvent 'session-saved' / 'session-deleted' listeners registered
//       inside initSessionsList()
//
//   External read interface (exported):
//     - renderLeftSidebarSessions()
//     - renderSharedSessions()
//     - initSessionsList()
//
// D-3 Bridge dependencies (read-only / call-only from window):
//   - State reads:
//       window.savedSessions               (array reference shared — mutations sync via ref)
//       window.currentLoadedSessionId      (value via defineProperty getter — see news-search.js)
//       window._sharedSessionsCache        (value via defineProperty getter — see news-search.js)
//       window.sessionHistory              (array reference)
//       window.chatHistory                 (array reference)
//       (v4.0 Commit 5 (2026-05-24): currentResearchReport now read via ES import from features/research.js — bridge removed.)
//       (v4.0 Commit 8 (2026-05-24): _folderModeActive now read via ES import from features/folders.js — bridge removed.)
//   - Function/class calls:
//       window.matchSessionId              (utility from news-search.js — function decl auto-attaches)
//       window.escapeHTML                  (utility from news-search.js — function decl auto-attaches)
//       window.authManager                 (Phase 3 bridge — ES module singleton attached by main.js)
//       window.sessionManager              (Phase 3 bridge — news-search.js classic, Phase 4b moves)
//       window.UserStateSync               (Path B — IIFE in classic script)
//       window.UserStateSyncError          (Path B — IIFE in classic script)
//       window.assertUserIdentity          (Path B — IIFE in classic script)
//       window.loadSavedSession            (Phase 4b moves)
//       window.saveCurrentSession          (Phase 4b moves)
//       window.deleteSavedSession          (Phase 4b/5 moves)
//       window.startSidebarSessionRename   (Phase 4b/5 moves — defined elsewhere in news-search.js)
//       window.toggleSessionSharing        (Phase 4b/5 moves — defined elsewhere in news-search.js)
//       window.makeSidebarSessionsDraggable (Phase 5+ moves — folder system)
//       window._updateOrgSpaceBadge        (Phase 4a — defined elsewhere in news-search.js)
//       window._sessionDirtySetter         (helper — sets news-search.js _sessionDirty)
//
// D-3 No dynamic imports — module uses ONLY static imports. (None needed at Phase 4a;
//   all dependencies are via window bridges.)
//
// D-13 Compliance:
//   This module is INERT on import. NO top-level side effects:
//     - NO addEventListener at module top-level
//     - NO localStorage / sessionStorage writes
//     - NO fetch / XHR
//     - NO DOM mutation
//   All side effects (event listener registration, initial render) only execute
//   inside the exported `initSessionsList()` called by main.js DOMContentLoaded.
//
// Phase 4a Path B (2026-05-21):
//   Real moves (from news-search.js):
//     - function renderLeftSidebarSessions   (was line 10636-10736)
//     - async function renderSharedSessions  (was line 11476-11500)
//     - function _renderSharedSessionsList   (was line 11502-11560 — used by renderSharedSessions)
//     - initSessionTabs IIFE                 (was line 11423-11443 — registers tab click handlers)
//     - session-saved / session-deleted listeners (was line 10855-10856)
//     - initial render call                  (was line 10864-10868 — DOMContentLoaded-gated)
//   Preserved in news-search.js (Phase 7+ migration target):
//     - All 4 outer-let state declarations (savedSessions / currentLoadedSessionId /
//       _sessionDirty / _sharedSessionsCache + _sharedSessionsLoading)
//     - UserStateSync IIFE
//     - saveCurrentSession / scheduleSave (Phase 4b moves)
//     - _isShared early-return guard inside saveCurrentSession (Phase 4b moves)
//     - _sessionDirty 8 mutate points

// v4.0 Commit 5 (2026-05-24): currentResearchReport bridge removed — direct import.
import { getResearchReport, clearResearchReport } from './research.js';
// v4.0 Commit 8 (2026-05-24): folder mode flag now owned by features/folders.js.
// Replaces `window._folderModeActive` bridge (removed in this commit).
// D-V6 OK: sessions-list.js → folders.js (one-way). folders.js does NOT import sessions-list.
import { getFolderModeActive } from './folders.js';
// v4.0 Commit 22 (2026-05-25, Phase 8 part C): sessions lifecycle migration imports.
//   deleteSavedSession (when the deleted session is the currently-loaded one) needs
//   to clear conversation/chat/pin/article state — pull setters from owner modules.
//   startSidebarSessionRename needs markSessionDirty to mark dirty after rename.
import {
    setConversationHistory,
    setAccumulatedArticles,
    cancelActiveSearch,
    clearCurrentConversationId
} from './search.js';
import { setChatHistory, getChatHistory } from './chat.js';
import { setPinnedMessages, setPinnedNewsCards } from './pins.js';
import { clearCurrentResearchQueryId } from './deep-research.js';
import { markSessionDirty } from './session-manager.js';
// Post-refactor regression fix (2026-05-25): commit 11 removed window.UserStateSync*
// bridges, but this module still referenced them at line 297-301 (session click
// identity self-check + reload-path fallback). Migrated to ES imports.
import { UserStateSync, UserStateSyncError, assertUserIdentity } from '../core/state-sync.js';

// ============================================================================
// v4.0 Commit 10 (2026-05-24): Sessions Hex Migration — declaration owner
//
// 5 declarations migrated from news-search.js to this module (per D-V14 split):
//   - savedSessions (line 1603 in news-search.js — array, 85 callsites)
//   - currentLoadedSessionId (line 1622 — string|null, 31 callsites)
//   - sessionHistory (line 1599 — array, 46 callsites)
//   - _sharedSessionsCache (line 11656 — array|null, 15 callsites)
//   - _sharedSessionsLoading (line 11657 — bool, 8 callsites)
//
// _sessionDirty is OWNED by features/session-manager.js (per D-V14 ownership decision —
// session-manager already owns dirty-driven scheduleSave/flushPendingSave logic).
//
// D-13 Compliance:
//   This module is INERT on import. The 5 declarations below are pure `let` bindings;
//   no side effect runs at module load time. Initial localStorage load of savedSessions
//   happens INSIDE initSessionsList() (called by main.js DOMContentLoaded handler).
//
// Trigger writes:
//   - A (Login) / B (mismatch) / F (warm-start): IIFE clearUserScopedState + applyInit
//     via clearSavedSessions / hydrateSavedSessions helpers
//   - E (session click): loadSavedSession (still in news-search.js until commit 18) →
//     setCurrentLoadedSessionId
//   - D (logout / 401): IIFE clearUserScopedState via clearX helpers
//   - Soft-refresh (visibilitychange): hydrateFromSoftRefreshInit (D-V4 atomic)
//
// External read interface (extends Phase 4a-4b exports):
// ============================================================================

// Owned state declarations
let _savedSessions = [];
let _currentLoadedSessionId = null;
let _sessionHistory = [];
let _sharedSessionsCache = null;
let _sharedSessionsLoading = false;

// ---- savedSessions helpers ----
// Live-reference accessor — callers may mutate via .push / .splice / .findIndex / index assign.
export function getSavedSessions() { return _savedSessions; }
// IIFE-style reset: preserve array identity (mutate-in-place) to mirror legacy
// `savedSessions.length = 0` semantic. Critical for cross-module reads that hold
// references (e.g. SessionManager._saveToLocalStorage reads via getSavedSessions()).
export function clearSavedSessions() { _savedSessions.length = 0; }
// Full replace (rebinds the module-private to a new array). Use for cases where
// caller has the new array ready — e.g., login full-load. NOTE: pre-existing
// callers reading via getSavedSessions() at that exact moment see stale ref.
// Most call paths follow clear+hydrate (which preserves identity).
export function setSavedSessions(arr) { _savedSessions = Array.isArray(arr) ? arr : []; }
// applyInit hydrate pattern — backfill _serverId, push, persist + stamp migratedFlag
// (mirror of news-search.js IIFE applyInit lines 2017-2036).
// Per D-V4 + plan §3.10 + lessons-frontend 2026-05-21 lesson 3:
// the migratedFlag stamp prevents migrateFromLocal re-uploading on every cycle.
export function hydrateSavedSessions(sessions) {
    if (!Array.isArray(sessions)) return;
    for (const s of sessions) {
        if (!s._serverId && s.id) s._serverId = s.id;
    }
    _savedSessions.push(...sessions);
    try {
        localStorage.setItem('taiwanNewsSavedSessions', JSON.stringify(_savedSessions));
        // P0 stamp — prevents SessionManager.migrateFromLocal re-POST on next login cycle.
        localStorage.setItem('taiwanNewsSessionsMigrated', Date.now().toString());
    } catch (e) {
        console.error('[sessions-list] hydrateSavedSessions persist failed:', e);
    }
}

// ---- currentLoadedSessionId helpers ----
export function getCurrentLoadedSessionId() { return _currentLoadedSessionId; }
export function setCurrentLoadedSessionId(id) { _currentLoadedSessionId = id; }
export function clearCurrentLoadedSessionId() { _currentLoadedSessionId = null; }

// ---- sessionHistory helpers ----
export function getSessionHistory() { return _sessionHistory; }
// Full replace (used by loadSavedSession + deleteSavedSession reset).
export function setSessionHistory(arr) { _sessionHistory = Array.isArray(arr) ? arr : []; }
// IIFE-style reset preserving identity.
export function clearSessionHistory() { _sessionHistory.length = 0; }
export function pushSessionHistory(entry) { _sessionHistory.push(entry); }

// ---- _sharedSessionsCache helpers ----
export function getSharedSessions() { return _sharedSessionsCache; }
export function clearSharedSessions() { _sharedSessionsCache = null; }
// hydrate stores reference + updates 組織空間 badge (mirror IIFE applyInit lines 2046-2054).
export function hydrateSharedSessions(shared) {
    if (!Array.isArray(shared)) return;
    _sharedSessionsCache = shared;
    try {
        const sharedTab = document.querySelector('.left-sidebar-sessions-tab[data-sessions-tab="shared"]');
        if (sharedTab) {
            const n = shared.length;
            sharedTab.textContent = n > 0 ? `組織空間 (${n})` : '組織空間';
        }
    } catch (e) {
        console.error('[sessions-list] hydrateSharedSessions badge update failed:', e);
    }
}
// Direct setter for non-hydrate writes (e.g., deleteSavedSession invalidates cache to null,
// renderSharedSessions populates cache from fresh fetch).
export function setSharedSessions(v) { _sharedSessionsCache = v; }

// ---- _sharedSessionsLoading helpers ----
export function isSharedSessionsLoading() { return _sharedSessionsLoading; }
export function setSharedSessionsLoading(b) { _sharedSessionsLoading = !!b; }

// ---- D-V4 / H2 atomic hydrate (soft-refresh path) ----
// Mirror of source `_hydrateFromSoftRefreshInit` (news-search.js:11685-11703) with
// Gemini Final Finding 3 hardening: Array.isArray guard per field (skip-on-undefined
// — do NOT default to `|| []` which wipes cache); explicit clearSavedSessions() before
// hydrate (REPLACE not APPEND); single render-trigger debounce via requestAnimationFrame.
export function hydrateFromSoftRefreshInit(init) {
    if (init && Array.isArray(init.sessions)) {
        clearSavedSessions();          // mirror source line 11687 `savedSessions = []`
        hydrateSavedSessions(init.sessions);
    }
    if (init && Array.isArray(init.shared_sessions)) {
        hydrateSharedSessions(init.shared_sessions);
    }
    // Single render trigger — coalesce sidebar render with rAF debounce.
    if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(() => renderLeftSidebarSessions());
    } else {
        renderLeftSidebarSessions();
    }
}
// ============================================================================

/**
 * Render the left-sidebar own-sessions list (latest 15, sorted by updatedAt DESC).
 * Reads state from window.savedSessions / window.currentLoadedSessionId.
 * Re-registers click + menu + dropdown listeners on each call (innerHTML rebuild
 * destroys previous DOM and its listeners — no leak by design).
 */
export function renderLeftSidebarSessions() {
    const container = document.getElementById('leftSidebarSessions');
    if (!container) return;

    // v4.0 Commit 10 (2026-05-24): read from owner module (was window.savedSessions bridge).
    const savedSessions = _savedSessions;

    if (savedSessions.length === 0) {
        container.innerHTML = '';
        return;
    }

    // 最新的在最上面，最多顯示 15 條
    // Bug X fix: explicit sort by updated_at DESC instead of .reverse()
    // Server path returns array sorted DESC (newest at index 0); localStorage push puts newest at end.
    // .reverse() only worked for the localStorage path — sort works for both.
    const recent = savedSessions
        .slice()
        .sort((a, b) => {
            const ta = new Date(a.updatedAt || a.updated_at || a.createdAt || a.created_at || 0).getTime();
            const tb = new Date(b.updatedAt || b.updated_at || b.createdAt || b.created_at || 0).getTime();
            return tb - ta; // DESC: newest at top
        })
        .slice(0, 15);

    // v4.0 Commit 10 (2026-05-24): read from owner module (was window.currentLoadedSessionId bridge).
    const currentLoadedSessionId = _currentLoadedSessionId;
    const matchSessionId = window.matchSessionId;
    const escapeHTML = window.escapeHTML;
    const authManager = window.authManager;

    container.innerHTML = recent.map(session => {
        const isActive = matchSessionId(currentLoadedSessionId, session.id);
        const isOnline = authManager.isLoggedIn() && authManager.getCurrentUser()?.org_id;
        const isShared = session.visibility && session.visibility !== 'private';
        const shareLabel = isShared ? '取消共享' : '共享到組織';
        return `<div class="left-sidebar-session-item${isActive ? ' active' : ''}" data-sidebar-session-id="${session.id}">
            <span class="left-sidebar-session-title">${escapeHTML(session.title)}</span>
            <button class="left-sidebar-session-menu-btn" data-menu-session-id="${session.id}">&#8943;</button>
            <div class="left-sidebar-session-dropdown" data-dropdown-session-id="${session.id}">
                <button class="left-sidebar-session-dropdown-item" data-action="rename" data-session-id="${session.id}">重新命名</button>
                ${isOnline ? `<button class="left-sidebar-session-dropdown-item" data-action="share" data-session-id="${session.id}">${shareLabel}</button>` : ''}
                <button class="left-sidebar-session-dropdown-item danger" data-action="delete" data-session-id="${session.id}">刪除</button>
            </div>
        </div>`;
    }).join('');

    // Click on session item to load (ignore menu/dropdown clicks)
    container.querySelectorAll('.left-sidebar-session-item').forEach(item => {
        item.addEventListener('click', async (e) => {
            if (e.target.closest('.left-sidebar-session-menu-btn') || e.target.closest('.left-sidebar-session-dropdown')) return;
            const sessionId = item.dataset.sidebarSessionId;
            // v4.0 Commit 10 (2026-05-24): read from owner module (was window.savedSessions bridge).
            const session = _savedSessions.find(s => matchSessionId(s.id, sessionId));
            if (session) {
                // Trigger E: session click (sidebar). Identity self-check
                // before navigating. Mismatch path: fall back to reload-path
                // (Trigger F via runInitSync) — defensive against silent logout.
                try {
                    assertUserIdentity(authManager._user, authManager._user);
                } catch (err) {
                    if (err instanceof UserStateSyncError && err.code !== 'MISSING_FRESH' && authManager.isLoggedIn()) {
                        console.warn('[session-click:sidebar] identity self-check failed, triggering reload-path:', err);
                        await UserStateSync.runInitSync({ keepInviteToken: false }).catch(err2 =>
                            console.error('[session-click:sidebar] runInitSync failed:', err2));
                        return;
                    }
                }
                // 切換前先保存當前對話（防止深度報告等狀態丟失）
                // v4.0 Commit 10 (2026-05-24): sessionHistory owned by this module.
                // Post-refactor regression fix (2026-05-25): chatHistory was migrated
                // to chat.js (commit 3) and window.chatHistory bridge removed in commit 11
                // — switched from window.chatHistory fallback to ES import getChatHistory().
                // Prior `|| []` fallback silently lost chat-only sessions on switch.
                const sessionHistory = _sessionHistory;
                const chatHistory = getChatHistory();
                if (sessionHistory.length > 0 || getResearchReport() || chatHistory.length > 0) {
                    window.saveCurrentSession();
                }
                window.loadSavedSession(session);
            }
        });
    });

    // "..." menu button toggle
    container.querySelectorAll('.left-sidebar-session-menu-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const sid = btn.dataset.menuSessionId;
            const dropdown = container.querySelector(`.left-sidebar-session-dropdown[data-dropdown-session-id="${sid}"]`);
            // Close all other dropdowns first
            container.querySelectorAll('.left-sidebar-session-dropdown.visible').forEach(d => {
                if (d !== dropdown) d.classList.remove('visible');
            });
            dropdown.classList.toggle('visible');
        });
    });

    // Dropdown actions (rename / delete / share)
    container.querySelectorAll('.left-sidebar-session-dropdown-item').forEach(actionBtn => {
        actionBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const action = actionBtn.dataset.action;
            const sessionId = actionBtn.dataset.sessionId;
            if (action === 'delete') {
                window.deleteSavedSession(sessionId);
            } else if (action === 'rename') {
                window.startSidebarSessionRename(sessionId);
            } else if (action === 'share') {
                window.toggleSessionSharing(sessionId);
            }
        });
    });

    // 若處於資料夾管理模式，重新綁定拖曳
    // v4.0 Commit 8 (2026-05-24): was window._folderModeActive bridge — now reads owner module.
    if (getFolderModeActive()) {
        if (typeof window.makeSidebarSessionsDraggable === 'function') {
            window.makeSidebarSessionsDraggable();
        }
    }
}

/**
 * Render the organisation-shared sessions tab (組織空間).
 * Uses prefetched cache (window._sharedSessionsCache) if present, otherwise
 * loads via window.sessionManager.loadSharedSessions().
 */
export async function renderSharedSessions() {
    const container = document.getElementById('leftSidebarSessionsShared');
    if (!container) return;
    // v4.0 Commit 10 (2026-05-24): read from owner module (was window._sharedSessionsLoading bridge).
    if (_sharedSessionsLoading) return;

    // Use cache if available (pre-fetched on page load)
    // v4.0 Commit 10 (2026-05-24): read from owner module (was window._sharedSessionsCache bridge).
    const cached = _sharedSessionsCache;
    if (cached) {
        _renderSharedSessionsList(container, cached);
        // Consume cache (one-shot read) — owner module sets to null.
        clearSharedSessions();
        return;
    }

    container.innerHTML = '<div style="padding:12px 16px;color:#888;font-size:13px;text-align:center;">載入中...</div>';
    setSharedSessionsLoading(true);

    try {
        const sessions = await window.sessionManager.loadSharedSessions();
        setSharedSessionsLoading(false);
        _renderSharedSessionsList(container, sessions);
    } catch (err) {
        setSharedSessionsLoading(false);
        console.error('[SharedSession] renderSharedSessions error:', err);
        container.innerHTML = '<div style="padding:12px 16px;color:#888;font-size:13px;text-align:center;">載入共享對話失敗</div>';
    }
}

/**
 * Internal helper — renders the list of shared sessions and attaches click
 * handlers that hydrate via snake_case → camelCase + tag _isShared=true.
 * The _isShared tag is consumed by saveCurrentSession (in news-search.js until
 * Phase 4b) to short-circuit Y-1 spawn prevention.
 */
function _renderSharedSessionsList(container, sessions) {
    const escapeHTML = window.escapeHTML;
    const authManager = window.authManager;

    if (!sessions || sessions.length === 0) {
        container.innerHTML = '<div style="padding:12px 16px;color:#888;font-size:13px;text-align:center;">組織空間尚無共享對話</div>';
        return;
    }

    container.innerHTML = sessions.map(session => {
        const title = escapeHTML(session.title || '未命名對話');
        const ownerLabel = session.owner_name || session.owner_email || '';
        const dateStr = session.updated_at ? new Date(session.updated_at).toLocaleDateString('zh-TW') : '';
        const meta = [ownerLabel, dateStr].filter(Boolean).join(' · ');
        return `<div class="left-sidebar-session-item" style="cursor:pointer;" data-shared-session-id="${session.id}">
            <div style="display:flex;flex-direction:column;gap:2px;overflow:hidden;">
                <span class="left-sidebar-session-title">${title}</span>
                ${meta ? `<span style="font-size:11px;color:#888;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHTML(meta)}</span>` : ''}
            </div>
        </div>`;
    }).join('');

    container.querySelectorAll('[data-shared-session-id]').forEach(item => {
        item.addEventListener('click', async () => {
            const sharedId = item.dataset.sharedSessionId;
            try {
                const res = await authManager.authenticatedFetch(`/api/sessions/${sharedId}`);
                const data = await res.json();
                if (res.ok && data.success && data.session) {
                    const s = data.session;
                    // Y-1 fix: map server snake_case → camelCase + tag _isShared
                    // so saveCurrentSession can detect this is another user's session
                    // (read-only context) and skip — preventing spawn of the
                    // current user's own row when they type a query in shared view.
                    const sharedHydrated = {
                        id: s.id,
                        _serverId: s.id,
                        _isShared: true,
                        _ownerUserId: s.user_id,
                        title: s.title,
                        visibility: s.visibility,
                        conversationHistory: s.conversation_history ?? [],
                        sessionHistory: s.session_history ?? [],
                        chatHistory: s.chat_history ?? [],
                        accumulatedArticles: s.accumulated_articles ?? [],
                        pinnedMessages: s.pinned_messages ?? [],
                        pinnedNewsCards: s.pinned_news_cards ?? [],
                        researchReport: s.research_report ?? null,
                        conversationId: s.conversation_id ?? null,
                        createdAt: s.created_at,
                        updatedAt: s.updated_at,
                    };
                    window.loadSavedSession(sharedHydrated);
                } else {
                    console.error('[SharedSession] Failed to load:', data);
                }
            } catch (err) {
                console.error('[SharedSession] Load error:', err);
            }
        });
    });
}

/**
 * D-13 Side-effect entrypoint — called by main.js DOMContentLoaded handler.
 *
 * Registers:
 *   - Sidebar tab click handlers (我的對話 / 組織空間 switching)
 *   - 'session-saved' / 'session-deleted' CustomEvent listeners
 *     (re-render sidebar after CRUD operations elsewhere)
 *   - Outside-click handler to close visible dropdowns
 *   - Initial render of left-sidebar own sessions
 */
export function initSessionsList() {
    // v4.0 Commit 10 (2026-05-24): initial localStorage hydrate moved here from
    // news-search.js (lines 1611-1619). Runs once at startup before sidebar render.
    // Mirror semantics: parse 'taiwanNewsSavedSessions' string; populate _savedSessions
    // in-place via .push (preserves array reference for downstream consumers).
    try {
        const stored = localStorage.getItem('taiwanNewsSavedSessions');
        if (stored) {
            const parsed = JSON.parse(stored);
            if (Array.isArray(parsed)) {
                _savedSessions.push(...parsed);
                console.log(`[SessionManager] Loaded ${_savedSessions.length} sessions from localStorage (initial)`);
            }
        }
    } catch (e) {
        console.error('[SessionManager] Failed to load sessions from localStorage:', e);
    }

    // ==================== SESSION TABS (我的對話 / 組織空間) ====================
    const tabs = document.querySelectorAll('.left-sidebar-sessions-tab');
    const myList = document.getElementById('leftSidebarSessions');
    const sharedList = document.getElementById('leftSidebarSessionsShared');
    if (tabs.length) {
        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                tabs.forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                const which = tab.dataset.sessionsTab;
                if (myList) myList.style.display = which === 'my' ? '' : 'none';
                if (sharedList) sharedList.style.display = which === 'shared' ? '' : 'none';
                if (which === 'shared') {
                    renderSharedSessions();
                }
            });
        });
    }

    // Close sidebar session dropdowns on outside click
    document.addEventListener('click', () => {
        const container = document.getElementById('leftSidebarSessions');
        if (container) {
            container.querySelectorAll('.left-sidebar-session-dropdown.visible').forEach(d => {
                d.classList.remove('visible');
            });
        }
    });

    // 監聽 session 變更事件，同步更新左側邊欄
    document.addEventListener('session-saved', renderLeftSidebarSessions);
    document.addEventListener('session-deleted', renderLeftSidebarSessions);

    // Initial render — by the time initSessionsList is called (main.js
    // DOMContentLoaded), classic-script news-search.js has finished evaluating
    // and window.authManager / window.savedSessions are available.
    renderLeftSidebarSessions();

    // D-11 Probe recordInit is centralized in main.js DOMContentLoaded handler
    // (consistent with core modules). Do NOT call recordInit here — duplicate
    // would inflate Sentinel 4 count (verified caught: initCounts === 2).
}

// ============================================================================
// v4.0 Commit 22 (2026-05-25, Phase 8 part C) — Sessions lifecycle extend
//
// Migrated from news-search.js (per inventory Owner: sessions-list):
//   - _updateOrgSpaceBadge (was line 3649) — 組織空間 tab badge delta updater.
//     Bridge swept: window._updateOrgSpaceBadge (re-bridged in news-search.js
//     import site only; sharing.js commit 18 still calls via window — sweep
//     commit 25 when sharing.js direct-imports).
//   - handleDeleteSession (was line 2921) — two-click delete confirmation UX.
//   - deleteSavedSession (was line 2944) — full delete pipeline: localStorage +
//     server PATCH + state cleanup + sidebar refresh.
//   - startSidebarSessionRename (was line 3592) — sidebar inline rename input.
//
// NOT migrated this commit (per CEO #5 / hazard analysis):
//   - saveCurrentSession — stays in news-search.js until commit 23
//     (session-coordinator.js NEW). It owns the full save-session shape with
//     deep cross-module reads + KG snapshot + DR snapshot. Co-location with
//     scheduleSave/dirty logic awaits commit 23.
//   - loadSavedSession — defers to a later commit because it depends on many
//     local DOM consts (searchInput / initialState / resultsSection / chatContainer /
//     chatInputContainer / searchContainer / chatMessagesEl / btnSearch /
//     modeButtonsInline) + the `_preFolderState` outer let + `advancedSearchConfirmed`
//     module-internal state in news-search.js. Migration needs either a
//     DOM-const accessor pattern or co-migration of those references. Out of
//     scope for batch 6b''; dispatcher decides commit 23+ approach.
//   - flushPendingSaveOnBeforeUnload — inventory listed it but it is actually
//     wired inline (news-search.js line 861 beforeunload listener calling
//     window.sessionManager.flushPendingSave). No standalone function exists.
//
// External re-bridges remaining for now (sweep commit 25):
//   - window.deleteSavedSession (re-bridge needed because sessions-list.js
//     renderLeftSidebarSessions dropdown handler already calls via window;
//     sweep when we remove that indirection).
//   - window.startSidebarSessionRename / window._updateOrgSpaceBadge (same
//     reason — sidebar dropdown + sharing.js still reach via window).
//
// D-13 Compliance preserved — module remains inert on import; all new
// functions are pure exports (no top-level side effects).
// ============================================================================

// Two-click delete confirmation timeout (was news-search.js line 2918 local let)
let _deleteConfirmTimeout = null;

/**
 * Update 組織空間 tab badge by delta. Pure DOM. Used by deleteSavedSession (this
 * module) AND features/sharing.js toggleSessionSharing (commit 18) — sharing.js
 * still calls via window._updateOrgSpaceBadge until commit 25 direct-import sweep.
 */
export function _updateOrgSpaceBadge(delta) {
    const sharedTab = document.querySelector('.left-sidebar-sessions-tab[data-sessions-tab="shared"]');
    if (!sharedTab) return;
    const match = sharedTab.textContent.match(/\((\d+)\)/);
    const current = match ? parseInt(match[1]) : 0;
    const next = Math.max(0, current + delta);
    sharedTab.textContent = next > 0 ? `組織空間 (${next})` : '組織空間';
}

/**
 * Two-click delete confirmation handler — first click arms the button (shows
 * '確定刪除' label for 3 seconds), second click within the window calls
 * deleteSavedSession. Bound from the sidebar dropdown's "delete" action.
 */
export function handleDeleteSession(sessionId, deleteBtn) {
    if (deleteBtn.classList.contains('confirming')) {
        // Second click - actually delete
        deleteSavedSession(sessionId);
    } else {
        // First click - show confirmation
        deleteBtn.classList.add('confirming');
        deleteBtn.textContent = '確定刪除';

        // Clear any existing timeout
        if (_deleteConfirmTimeout) {
            clearTimeout(_deleteConfirmTimeout);
        }

        // Reset after 3 seconds if not confirmed
        _deleteConfirmTimeout = setTimeout(() => {
            deleteBtn.classList.remove('confirming');
            deleteBtn.innerHTML = '<img src="/static/images/Icon_cancel.png" alt="" class="inline-icon">';
        }, 3000);
    }
}

/**
 * Delete a saved session (local + server + state cleanup if current).
 * Migrated from news-search.js line 2944. `resetToHome` and
 * `renderConversationHistory` stay in news-search.js for now (KEEP-residual)
 * — reached via window bridges (already exist).
 */
export function deleteSavedSession(sessionId) {
    console.log('Deleting session:', sessionId);
    cancelActiveSearch();

    // Find the session before removing (need _serverId for API call)
    const session = _savedSessions.find(s => window.matchSessionId(s.id, sessionId));

    // Capture shared state BEFORE removing (mirror toggleSessionSharing pattern):
    // shared sessions need org-space badge decrement + cache invalidation.
    const wasShared = session && session.visibility && session.visibility !== 'private';

    // Remove from savedSessions (owner module's private array — splice-style mutate)
    setSavedSessions(_savedSessions.filter(s => !window.matchSessionId(s.id, sessionId)));

    // Update localStorage
    localStorage.setItem('taiwanNewsSavedSessions', JSON.stringify(_savedSessions));

    // Mirror toggleSessionSharing: keep org-space badge in sync, and invalidate
    // the prefetched shared cache so next visit to the shared tab refetches
    // (prevents stale prefetch from re-rendering the deleted shared session).
    if (wasShared) {
        _updateOrgSpaceBadge(-1);
        clearSharedSessions();
    }

    // Call backend API to delete server-side
    if (session) {
        window.sessionManager.deleteSession(session.id, session._serverId || session.id);
    }

    // If the deleted session is currently loaded, reset the interface
    if (window.matchSessionId(_currentLoadedSessionId, sessionId)) {
        setCurrentLoadedSessionId(null);
        setConversationHistory([]);
        clearSessionHistory();
        setChatHistory([]);
        setAccumulatedArticles([]);
        setPinnedMessages([]);
        setPinnedNewsCards([]);
        clearResearchReport();
        clearCurrentConversationId();
        clearCurrentResearchQueryId();

        // resetToHome + renderConversationHistory + DOM consts (searchInput /
        // initialState) live in news-search.js KEEP-residual. Reach via window
        // bridges (renderConversationHistory bridge already exists at
        // news-search.js line 2577; resetToHome bridge added by commit 22 in
        // news-search.js).
        if (typeof window.resetToHome === 'function') {
            window.resetToHome();
        }
        const searchInputEl = document.getElementById('searchInput');
        if (searchInputEl) searchInputEl.value = '';
        const initialStateEl = document.getElementById('initialState');
        if (initialStateEl) initialStateEl.style.display = 'block';

        if (typeof window.renderConversationHistory === 'function') {
            window.renderConversationHistory();
        }
    }

    document.dispatchEvent(new CustomEvent('session-deleted'));
}

/**
 * Sidebar inline rename for a session (click the menu → "rename"). Replaces
 * the title span with an input, commits on blur or Enter, restores on Escape.
 * Migrated from news-search.js line 3592.
 */
export function startSidebarSessionRename(sessionId) {
    const container = document.getElementById('leftSidebarSessions');
    if (!container) return;
    const item = container.querySelector(`.left-sidebar-session-item[data-sidebar-session-id="${sessionId}"]`);
    if (!item) return;

    const session = _savedSessions.find(s => window.matchSessionId(s.id, sessionId));
    if (!session) return;

    // Close dropdown
    const dropdown = item.querySelector('.left-sidebar-session-dropdown');
    if (dropdown) dropdown.classList.remove('visible');

    // Replace title span with input
    const titleSpan = item.querySelector('.left-sidebar-session-title');
    const menuBtn = item.querySelector('.left-sidebar-session-menu-btn');
    if (menuBtn) menuBtn.style.display = 'none';

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'left-sidebar-session-rename';
    input.value = session.title;
    titleSpan.replaceWith(input);
    input.focus();
    input.select();

    function commitRename() {
        const newName = input.value.trim();
        if (newName && newName !== session.title) {
            // P1 E2E fix (2026-05-26): keep the old title so we can revert the optimistic
            // update if the server rename is rejected (e.g. token expired mid-rename and
            // refresh failed). Previously the rename fired unawaited + errors were swallowed,
            // so a rejected PATCH left a stale localStorage/UI title that reverted on reload
            // (silent data loss).
            const previousTitle = session.title;
            session.title = newName;
            session.updatedAt = Date.now();
            localStorage.setItem('taiwanNewsSavedSessions', JSON.stringify(_savedSessions));
            // RCA Fix 1: rename happens via dedicated PATCH (renameSession), but if the
            // user later navigates we still want saveCurrentSession() to push the latest
            // body — mark dirty so the outer guard does not early-return.
            markSessionDirty();
            Promise.resolve(
                window.sessionManager.renameSession(session.id, session._serverId || session.id, newName)
            ).catch((e) => {
                // Server rejected the rename — revert optimistic local state and notify.
                console.error('[Sessions] rename rejected; reverting optimistic update:', e);
                session.title = previousTitle;
                localStorage.setItem('taiwanNewsSavedSessions', JSON.stringify(_savedSessions));
                renderLeftSidebarSessions();
                if (typeof window.showToast === 'function') {
                    window.showToast(e.message || '重新命名失敗，請稍後再試。');
                } else {
                    alert(e.message || '重新命名失敗，請稍後再試。');
                }
            });
        }
        renderLeftSidebarSessions();
    }

    input.addEventListener('blur', commitRename);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { input.blur(); }
        if (e.key === 'Escape') {
            input.removeEventListener('blur', commitRename);
            renderLeftSidebarSessions();
        }
    });
}
