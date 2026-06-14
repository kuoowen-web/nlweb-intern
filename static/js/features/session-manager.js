// static/js/features/session-manager.js
//
// D-1 Module Header — Session Manager (Path B narrow scope)
//   Owned state:
//     - _sessionManager module-private singleton (lazy init pattern)
//     - SessionManager class instance state (this._auth, this._saveTimer,
//       this._savePending, this._postedRecently)
//
//   Triggers writing this state (D-2026-05-13):
//     - A (Login): authManager.login() -> sessionManager.migrateFromLocal()/loadSessions()
//     - D (401/refresh): sessionManager.saveSession path falls back to localStorage
//     - User CRUD via news-search.js saveCurrentSession (Path B preserved):
//       saveCurrentSession -> sessionManager.scheduleSave -> debounced saveSession
//
//   External read interface:
//     - export { SessionManager, initSessionManager, getSessionManager }
//
// Path B (Phase 4b narrow):
//   This module owns SessionManager class declaration + singleton init.
//   - saveCurrentSession (module-level function) STAYS in news-search.js
//     until UserStateSync IIFE relocates (Phase 7+) -- it reassigns outer-scope
//     `let currentLoadedSessionId` / `_sessionDirty` which ES module cannot do.
//   - loadSavedSession STAYS in news-search.js for the same reason.
//   - D-7 layer #4 (_isShared early return inside saveCurrentSession) lives
//     in news-search.js.
//
// D-3 Bridge dependencies (read-only from window):
//   - window.authManager (Phase 3 set)
//   - window.savedSessions (Phase 4a maintained array bridge) -- read by
//     SessionManager._saveToLocalStorage() for the localStorage fallback path.
//     `let savedSessions = []` declaration still lives in news-search.js
//     (Phase 7+ migrates). The reference is updated by news-search.js at every
//     reassign callsite (Phase 4a added `window.savedSessions = savedSessions;`
//     after each reassignment).
//
// D-13 Compliance:
//   This module is INERT on import. SessionManager class declaration is pure
//   (no side effects at class-eval time). Singleton instance construction
//   happens inside exported initSessionManager() called by main.js bootstrap.
//   The module-private `let _sessionManager = null` is a pure binding; no
//   `new SessionManager(...)` runs until initSessionManager() executes.

// v4.0 Commit 10 (2026-05-24): savedSessions now owned by features/sessions-list.js.
// _saveToLocalStorage reads via owner-module getter (replaces window.savedSessions bridge).
// D-V6 OK: session-manager.js → sessions-list.js (one-way). sessions-list.js does NOT
// import session-manager.js (uses window.sessionManager runtime lookup).
import { getSavedSessions } from './sessions-list.js';

export class SessionManager {
    constructor(authMgr) {
        this._auth = authMgr;
        // v4.0 Commit 30 (2026-05-25, regression fix — clean redesign):
        // Per-session pending-save Map. Replaces single global _saveTimer /
        // _savePending pair that allowed rapid session switches to cancel a
        // previous session's pending PUT (race observed when CEO clicked away
        // within 2s of DR final_result — DR data never persisted to PG).
        //
        // Shape: sid -> { session, timer, scheduledAt }
        // Each entry's setTimeout callback removes its own entry on fire.
        // scheduleSave / flushPendingSave / _cancelPendingSave all operate
        // per-session (single-arg) or globally (no-arg).
        this._pendingSaves = new Map();
        // Defensive: track recent POSTs per session.id to detect _serverId-loss
        // regressions. If POST fires twice within 5s for the same id, surface
        // as console.error and suppress the duplicate POST.
        this._postedRecently = new Map();
    }

    _isOnline() {
        return this._auth.isLoggedIn() && this._auth.getCurrentUser()?.org_id;
    }

    // -- Sessions --

    async loadSessions() {
        if (this._isOnline()) {
            try {
                const res = await this._auth.authenticatedFetch('/api/sessions');
                const data = await res.json();
                if (res.ok && data.success) return data.sessions;
                // Server returned non-OK -- explicitly log, do NOT silent-fallback
                // to localStorage (would risk loading another user's stale data
                // per lessons-frontend L201). Better to show empty sidebar than
                // leak previous user's sessions.
                console.error('[SessionManager] /api/sessions non-OK:', res.status, data);
            } catch (e) {
                console.error('[SessionManager] /api/sessions error:', e);
            }
            // Logged-in path: no localStorage fallback. Return [] so sidebar
            // shows empty, surfacing the server failure visibly.
            return [];
        }
        // Not logged in: localStorage is the primary source of truth.
        try {
            const stored = localStorage.getItem('taiwanNewsSavedSessions');
            return stored ? JSON.parse(stored) : [];
        } catch (e) {
            console.error('[SessionManager] Failed to load from localStorage:', e);
            return [];
        }
    }

    async loadSharedSessions() {
        if (!this._isOnline()) return [];
        try {
            const res = await this._auth.authenticatedFetch('/api/sessions/shared');
            const data = await res.json();
            if (res.ok && data.success) return data.sessions;
            console.warn('[SessionManager] loadSharedSessions failed:', data);
            return [];
        } catch (e) {
            console.warn('[SessionManager] loadSharedSessions error:', e);
            return [];
        }
    }

    async setSessionVisibility(serverId, visibility) {
        if (!serverId) throw new Error('Session not saved to server yet');
        if (!this._isOnline()) throw new Error('Need to join an organization to share sessions');
        const res = await this._auth.authenticatedFetch(`/api/sessions/${serverId}/visibility`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ visibility })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Failed to set visibility');
        return data;
    }

    async saveSession(session) {
        if (this._isOnline()) {
            try {
                if (session._serverId) {
                    // Update existing
                    await this._auth.authenticatedFetch(`/api/sessions/${session._serverId}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            title: session.title,
                            mode: session.mode,
                            conversation_history: session.conversationHistory,
                            session_history: session.sessionHistory,
                            chat_history: session.chatHistory,
                            accumulated_articles: session.accumulatedArticles,
                            pinned_messages: session.pinnedMessages,
                            pinned_news_cards: session.pinnedNewsCards,
                            research_report: session.researchReport,
                            conversation_id: session.conversationId,
                        })
                    });
                } else {
                    // DEFENSIVE: detect _serverId-loss regression. If POST fires twice in
                    // 5s for the same in-memory session id, _serverId was lost between calls.
                    // Likely cause: saveCurrentSession overwrite drops _serverId, hydrate
                    // path forgets to backfill, or a new code path bypasses the wiring.
                    // Suppress the second POST to avoid PG row spawn; surface as console.error.
                    const lastPost = this._postedRecently.get(session.id) || 0;
                    if (Date.now() - lastPost < 5000) {
                        console.error(
                            '[SessionManager] DEFENSIVE: POST suppressed (duplicate within 5s) for session.id=',
                            session.id,
                            '-- possible _serverId-loss regression. Check saveCurrentSession overwrite (~1626), hydrate (~7745), loadSessions (~911).'
                        );
                        return;
                    }
                    this._postedRecently.set(session.id, Date.now());
                    // Create new
                    const res = await this._auth.authenticatedFetch('/api/sessions', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            title: session.title,
                            mode: session.mode,
                            conversation_history: session.conversationHistory,
                            session_history: session.sessionHistory,
                            chat_history: session.chatHistory,
                            accumulated_articles: session.accumulatedArticles,
                            pinned_messages: session.pinnedMessages,
                            pinned_news_cards: session.pinnedNewsCards,
                            research_report: session.researchReport,
                            conversation_id: session.conversationId,
                        })
                    });
                    const data = await res.json();
                    if (res.ok && data.success) {
                        session._serverId = data.session.id;
                        // Persist _serverId to localStorage so it survives page refresh
                        this._saveToLocalStorage();
                        // Re-render sidebar so sharing button appears immediately
                        document.dispatchEvent(new CustomEvent('session-saved'));
                    }
                }
                return;
            } catch (e) {
                console.warn('[SessionManager] API save failed, falling back to localStorage', e);
            }
        }
        // Fallback: save all sessions to localStorage
        this._saveToLocalStorage();
    }

    async deleteSession(sessionId, serverId) {
        if (this._isOnline() && serverId) {
            try {
                await this._auth.authenticatedFetch(`/api/sessions/${serverId}`, {
                    method: 'DELETE'
                });
                return;
            } catch (e) {
                console.warn('[SessionManager] API delete failed, falling back to localStorage', e);
            }
        }
        this._saveToLocalStorage();
    }

    async renameSession(sessionId, serverId, newTitle) {
        if (this._isOnline() && serverId) {
            // P1 E2E fix (2026-05-26): do NOT silently swallow a non-OK response.
            // authenticatedFetch returns the 401 response (after a failed token refresh)
            // rather than throwing, so the previous `await ...; return;` treated auth
            // rejection as success → optimistic UI/localStorage rename persisted but the
            // server kept the old title → reload reverted (silent data loss). Now we
            // check res.ok and throw so the caller can revert + notify.
            const res = await this._auth.authenticatedFetch(`/api/sessions/${serverId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title: newTitle })
            });
            if (!res.ok) {
                const msg = res.status === 401
                    ? '登入已過期，請重新登入後再試。'
                    : `重新命名失敗（HTTP ${res.status}）`;
                console.error('[SessionManager] renameSession non-OK:', res.status);
                throw new Error(msg);
            }
            return;
        }
        // Anonymous / offline: localStorage is the source of truth (D-2026-03-13).
        this._saveToLocalStorage();
    }

    // -- Folders --

    async loadFolders() {
        try {
            const stored = localStorage.getItem('taiwanNewsFolders');
            return stored ? JSON.parse(stored) : [];
        } catch (e) {
            console.error('[SessionManager] Failed to load folders:', e);
            return [];
        }
    }

    saveFoldersSync(foldersData) {
        localStorage.setItem('taiwanNewsFolders', JSON.stringify(foldersData));
    }

    // -- Migration --

    async migrateFromLocal() {
        if (!this._isOnline()) return { migrated: false };

        const localKey = 'taiwanNewsSavedSessions';
        const migratedFlag = 'taiwanNewsSessionsMigrated';

        if (localStorage.getItem(migratedFlag)) return { migrated: false, reason: 'already_migrated' };

        const stored = localStorage.getItem(localKey);
        if (!stored) return { migrated: false, reason: 'no_local_data' };

        let sessions;
        try {
            sessions = JSON.parse(stored);
        } catch (e) {
            return { migrated: false, reason: 'parse_error' };
        }

        if (!sessions.length) return { migrated: false, reason: 'empty' };

        try {
            const res = await this._auth.authenticatedFetch('/api/sessions/migrate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sessions })
            });
            const data = await res.json();
            if (res.ok && data.success) {
                localStorage.setItem(migratedFlag, Date.now().toString());
                localStorage.removeItem(localKey);
                localStorage.removeItem('taiwanNewsFolders');
                console.log(`[SessionManager] Migrated ${data.created} sessions to server, cleared localStorage`);
                return { migrated: true, created: data.created, errors: data.errors };
            }
        } catch (e) {
            console.error('[SessionManager] Migration failed:', e);
        }
        return { migrated: false, reason: 'api_error' };
    }

    // -- Debounced Save (per-session pending Map) --
    //
    // v4.0 Commit 30 (2026-05-25, regression fix — clean redesign):
    //   scheduleSave / flushPendingSave / _cancelPendingSave operate on
    //   per-session pending state (_pendingSaves Map keyed by session.id).
    //   Switching sessions never cancels another session's pending save.
    //   options.immediate flushes synchronously instead of waiting 2s
    //   (used by DR final_result — heavy payload, frequently-switched).

    scheduleSave(session, options = {}) {
        if (!session || session.id == null) {
            console.warn('[SessionManager] scheduleSave: missing session.id; ignored');
            return;
        }
        const sid = session.id;

        // Cancel any pending save FOR THIS SAME SESSION ONLY. Other sessions'
        // timers are untouched — this is the fix vs the previous single
        // global timer that cancelled cross-session.
        const existing = this._pendingSaves.get(sid);
        if (existing) clearTimeout(existing.timer);

        // Immediate flush path — caller knows the data is critical (DR done,
        // explicit save). No 2s wait.
        if (options.immediate) {
            this._pendingSaves.delete(sid);
            return this.saveSession(session).catch(e =>
                console.error(`[SessionManager] Immediate save failed for sid=${sid}:`, e)
            );
        }

        // Debounced path — 2s.
        const timer = setTimeout(() => {
            this._pendingSaves.delete(sid);
            this.saveSession(session).catch(e =>
                console.error(`[SessionManager] Debounced save failed for sid=${sid}:`, e)
            );
        }, 2000);

        this._pendingSaves.set(sid, { session, timer, scheduledAt: Date.now() });
    }

    // flushPendingSave(session) — flush THIS session's pending save (fire now).
    // flushPendingSave()         — flush ALL pending saves (used by beforeunload
    //                              and any future "save everything now" caller).
    // Returns a Promise (so callers can await on critical paths).
    flushPendingSave(session) {
        if (session && session.id != null) {
            const pending = this._pendingSaves.get(session.id);
            if (!pending) return Promise.resolve();
            clearTimeout(pending.timer);
            this._pendingSaves.delete(session.id);
            return this.saveSession(pending.session).catch(e =>
                console.error(`[SessionManager] Flush save failed for sid=${session.id}:`, e)
            );
        }
        // No arg — flush all pending saves.
        const promises = [];
        for (const [, p] of this._pendingSaves) {
            clearTimeout(p.timer);
            promises.push(
                this.saveSession(p.session).catch(e =>
                    console.error('[SessionManager] Flush-all save failed:', e)
                )
            );
        }
        this._pendingSaves.clear();
        return Promise.all(promises);
    }

    // RCA Fix 2 (hidden-path): cancel pending debounced save(s) without firing.
    //
    // _cancelPendingSave()         — cancel ALL pending saves
    //                                (logout / auth-failure path; prevents stale
    //                                PUTs after token invalidation).
    // _cancelPendingSave(session)  — cancel one session's pending save.
    //
    // Pure cleanup; no PUT side effects.
    _cancelPendingSave(session) {
        if (session && session.id != null) {
            const pending = this._pendingSaves.get(session.id);
            if (pending) {
                clearTimeout(pending.timer);
                this._pendingSaves.delete(session.id);
            }
            return;
        }
        for (const [, p] of this._pendingSaves) clearTimeout(p.timer);
        this._pendingSaves.clear();
    }

    // -- Preferences --

    async loadPreferences() {
        if (this._isOnline()) {
            try {
                const res = await this._auth.authenticatedFetch('/api/preferences');
                const data = await res.json();
                if (res.ok && data.success) return data.preferences;
            } catch (e) {
                console.warn('[SessionManager] Failed to load preferences from API', e);
            }
        }
        return {};
    }

    async setPreference(key, value) {
        if (this._isOnline()) {
            try {
                await this._auth.authenticatedFetch(`/api/preferences/${key}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ value })
                });
            } catch (e) {
                console.warn('[SessionManager] Failed to set preference via API', e);
            }
        }
    }

    // -- Internal helpers --

    _saveToLocalStorage() {
        // v4.0 Commit 10 (2026-05-24): savedSessions owned by features/sessions-list.js.
        // Read via getSavedSessions() (live array reference, not snapshot).
        // Replaces Phase 4a `window.savedSessions || []` bridge read.
        try {
            localStorage.setItem(
                'taiwanNewsSavedSessions',
                JSON.stringify(getSavedSessions())
            );
        } catch (e) {
            console.error('[SessionManager] Failed to save to localStorage:', e);
        }
    }
}

// ============================================================================
// v4.0 Commit 10 (2026-05-24): _sessionDirty ownership (D-V14)
//
// Per D-V14 ownership split: _sessionDirty lives in session-manager.js (NOT
// sessions-list.js) because session-manager owns the dirty-driven scheduleSave /
// flushPendingSave logic. Caller (news-search.js saveCurrentSession + handlers)
// uses helpers below.
//
// Trigger writes (mirror of source lines 1610 / 1928 / 2515 / 4066 / 4254 /
// 5238 / 5410 / 8014 / 8047 / 8257 / 9044 / 10977):
//   - false on init (default)
//   - false on UserStateSync.clearUserScopedState (IIFE)
//   - false on saveCurrentSession completion
//   - false on loadSavedSession completion (fresh load == clean state)
//   - true on new query / chat / pin/unpin / DR completion / new content events
// ============================================================================
let _sessionDirty = false;
export function isSessionDirty() { return _sessionDirty; }
export function markSessionDirty() { _sessionDirty = true; }
export function clearSessionDirty() { _sessionDirty = false; }

// Module-private singleton. Construction is deferred to initSessionManager()
// per D-13 (no top-level side effect on import). `let _sessionManager = null`
// is a pure declaration -- no `new SessionManager(...)` runs at module eval time.
let _sessionManager = null;

export function initSessionManager() {
    if (_sessionManager) return _sessionManager;
    if (typeof window === 'undefined' || !window.authManager) {
        console.error('[session-manager] initSessionManager called before window.authManager bridge set; sessionManager not constructed.');
        return null;
    }
    _sessionManager = new SessionManager(window.authManager);
    // Bridge for not-yet-migrated callsites in news-search.js
    // (saveCurrentSession calls window.sessionManager.scheduleSave at line ~2344;
    // loadSessions at line ~1248; etc.). Phase 7+ migrates these callsites to
    // the owner module.
    window.sessionManager = _sessionManager;
    return _sessionManager;
}

export function getSessionManager() {
    return _sessionManager;
}
