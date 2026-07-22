// static/js/core/session-coordinator.js
//
// D-1 Module Header — Session Coordinator (Phase 8 commit 23)
//   Owned state: NONE (pure orchestrator — reads from owner modules, calls owner setters)
//
//   Responsibility:
//     - Cross-module orchestration of "save current session" path:
//       gathers in-memory snapshot from 6+ owner modules
//       (search / sessions-list / chat / pins / research / knowledge-graph)
//       → writes back into sessions-list state via setters
//       → persists to localStorage
//       → schedules debounced server sync (sessionManager.scheduleSave)
//
//   External read interface:
//     - export { saveCurrentSession }
//
//   Trigger callsites (currently inside news-search.js IIFE):
//     - performSearch handler (after user adds a query to conversationHistory)
//     - sidebar session click handler (save current before switching)
//     - resetConversation (no — clears state, no save)
//     - …other in-flow triggers (chat send / DR start / pin actions etc.)
//
// CEO directive 2 note (commit 23, 2026-05-25):
//   Initial slim form. Only saveCurrentSession lives here. loadSavedSession
//   stays KEEP-in-place inside news-search.js IIFE due to deep DOM-const
//   coupling (17 DOM refs + 15 local-only helpers — moving it would require
//   adding ~30 new window bridges, contradicting commit-25 bridge-sweep goal).
//   Future expansion: restoreSession / pending-save scheduler / dirty tracker
//   live elsewhere already (session-manager.js owns SessionManager class +
//   isSessionDirty / markSessionDirty / clearSessionDirty; restoreSession in
//   news-search.js is also DOM-coupled).
//
// D-13 Compliance: INERT on import (no side effects at module-eval time).

import {
    getCurrentLoadedSessionId,
    setCurrentLoadedSessionId,
    getSavedSessions,
    getSessionHistory,
} from '../features/sessions-list.js';
import {
    getConversationHistory,
    getAccumulatedArticles,
    getCurrentConversationId,
} from '../features/search.js?v=20260717a';
import { getChatHistory } from '../features/chat.js?v=20260714a';
import { getPinnedMessages, getPinnedNewsCards } from '../features/pins.js';
import {
    getResearchReport,
    getArgumentGraph,
    getChainAnalysis,
} from '../features/research.js';
import { getCurrentKGData } from '../features/knowledge-graph.js?v=20260721a';
import { getCurrentMode } from '../features/mode.js';
import { getCurrentResearchQueryId } from '../features/deep-research.js?v=20260717a';
import {
    isSessionDirty,
    clearSessionDirty,
} from '../features/session-manager.js';
import { resolveLRSnapshotForSave } from '../features/lr-snapshot.js';

// 儲存當前對話
export function saveCurrentSession(opts = {}) {
    // RCA Fix 1: pure-browse early return.
    // Outer callsite guards only check "is there in-memory content", which is
    // TRUE for any loaded session even when nothing changed. The dirty flag
    // distinguishes "loaded session has content" from "user produced new
    // content since load". Without this, sidebar click → bumps updatedAt →
    // sort jumps top.
    if (!isSessionDirty()) {
        return;
    }
    // Y-1 fix: detect shared (read-only) session context and skip early.
    // Clicking a shared session in 組織空間 sets currentLoadedSessionId to
    // another user's PG row UUID. That session is NOT in savedSessions, so
    // findIndex returns -1 and the else-branch would push a brand-new entry
    // → POST /api/sessions → spawn current user's own row.
    //
    // Two cases handled:
    //  1. currentEntry exists and is tagged _isShared (defensive: future
    //     callers may add shared entries to savedSessions).
    //  2. currentLoadedSessionId is set but no matching entry in savedSessions
    //     — this is the canonical Y-1 path (shared session click did not push
    //     into savedSessions on purpose).
    const currentEntry = getCurrentLoadedSessionId() !== null
        ? getSavedSessions().find(s => window.matchSessionId(s.id, getCurrentLoadedSessionId()))
        : null;
    if (currentEntry && currentEntry._isShared) {
        console.warn('[saveCurrentSession] skipped: current session is shared (read-only context). currentLoadedSessionId=', getCurrentLoadedSessionId());
        return;
    }
    if (getCurrentLoadedSessionId() !== null && !currentEntry) {
        console.warn('[saveCurrentSession] skipped: currentLoadedSessionId not in savedSessions (likely shared session click). currentLoadedSessionId=', getCurrentLoadedSessionId());
        return;
    }
    const existingSessionIndex = getCurrentLoadedSessionId() !== null
        ? getSavedSessions().findIndex(s => window.matchSessionId(s.id, getCurrentLoadedSessionId()))
        : -1;

    // Prepare research report data with reasoning chain
    const researchReportData = getResearchReport() ? {
        ...getResearchReport(),
        argumentGraph: getArgumentGraph() ? [...getArgumentGraph()] : null,
        chainAnalysis: getChainAnalysis() ? { ...getChainAnalysis() } : null
    } : null;

    // v3 LR dialog snapshot：只在 LR mode 才 serialize 當前 #lrChat DOM（存的就是 user 此刻所見）。
    // 用 window bridge（window.serializeLRChatDOM，live-research.js 暴露）避免 core→features import 循環。
    const lrDialogSnapshot = (getCurrentMode() === 'live_research'
        && typeof window !== 'undefined' && typeof window.serializeLRChatDOM === 'function')
        ? window.serializeLRChatDOM()
        : [];

    if (existingSessionIndex !== -1) {
        // 更新現有 session
        // Title 優先保留 user-edited 名稱（rename 過的），避免 saveCurrentSession 把使用者命名覆蓋掉。
        // 僅在現有 title 為空或為預設值「未命名搜尋」時，才用 conversationHistory[0] 升級。
        const existingTitle = getSavedSessions()[existingSessionIndex].title;
        const preservedTitle = (existingTitle && existingTitle !== '未命名搜尋')
            ? existingTitle
            : (getConversationHistory()[0] || '未命名搜尋');
        // TRAP 2 guard (D-4): never let an empty serialize clobber a good snapshot.
        const priorSnapshot = getSavedSessions()[existingSessionIndex].lrDialogSnapshot;
        const lrSnapResolved = resolveLRSnapshotForSave(lrDialogSnapshot, priorSnapshot);
        if (lrSnapResolved.preserved) {
            console.warn('[saveCurrentSession] empty LR snapshot serialize skipped — preserving existing non-empty snapshot. currentLoadedSessionId=', getCurrentLoadedSessionId());
        }
        getSavedSessions()[existingSessionIndex] = {
            id: getCurrentLoadedSessionId(),
            _serverId: getSavedSessions()[existingSessionIndex]._serverId,
            title: preservedTitle,
            mode: getCurrentMode(),
            conversationHistory: [...getConversationHistory()],
            sessionHistory: [...getSessionHistory()],
            chatHistory: [...getChatHistory()],
            accumulatedArticles: [...getAccumulatedArticles()],
            pinnedMessages: [...getPinnedMessages()],
            pinnedNewsCards: [...getPinnedNewsCards()],
            researchReport: researchReportData,
            knowledgeGraph: getCurrentKGData() ? JSON.parse(JSON.stringify(getCurrentKGData())) : null,
            conversationId: getCurrentConversationId(),
            researchQueryId: getCurrentResearchQueryId(),
            lrDialogSnapshot: lrSnapResolved.snapshot,
            createdAt: getSavedSessions()[existingSessionIndex].createdAt,
            updatedAt: Date.now()
        };
    } else {
        // 新增 session
        const newSession = {
            id: Date.now(),
            title: getConversationHistory()[0] || '未命名搜尋',
            mode: getCurrentMode(),
            conversationHistory: [...getConversationHistory()],
            sessionHistory: [...getSessionHistory()],
            chatHistory: [...getChatHistory()],
            accumulatedArticles: [...getAccumulatedArticles()],
            pinnedMessages: [...getPinnedMessages()],
            pinnedNewsCards: [...getPinnedNewsCards()],
            researchReport: researchReportData,
            knowledgeGraph: getCurrentKGData() ? JSON.parse(JSON.stringify(getCurrentKGData())) : null,
            conversationId: getCurrentConversationId(),
            researchQueryId: getCurrentResearchQueryId(),
            lrDialogSnapshot: lrDialogSnapshot,
            createdAt: Date.now()
        };
        getSavedSessions().push(newSession);
        // v4.0 Commit 10 (2026-05-24): currentLoadedSessionId owned by features/sessions-list.js.
        setCurrentLoadedSessionId(newSession.id);
    }

    // 儲存到 localStorage
    localStorage.setItem('taiwanNewsSavedSessions', JSON.stringify(getSavedSessions()));
    console.log('Session saved');

    // Persist to server (debounced 2s, requires login).
    // Without this wiring sessions only live in localStorage.
    //
    // Spawn defense (after revert of 727db55):
    //   1. Update path: _serverId preserved across overwrites (e43468d)
    //   2. Hydrate path: _serverId backfilled
    //   3. Page-load loadSessions: _serverId backfilled
    //   4. Duplicate POST detector in SessionManager
    // All four together ensure scheduleSave → PUT for existing PG-resident sessions
    // and at most one POST per genuinely-new session.
    const persistedSession = existingSessionIndex !== -1
        ? getSavedSessions()[existingSessionIndex]
        : getSavedSessions()[getSavedSessions().length - 1];
    // D-6: capture the scheduleSave promise so the export caller can await dispatch.
    // {immediate} forwarded from opts; debounced (default) callers ignore the return.
    let saveResult;
    if (persistedSession && window.authManager.isLoggedIn()) {
        saveResult = window.sessionManager.scheduleSave(persistedSession, opts);
    }

    // RCA Fix 1: clear dirty after the save body has run. scheduleSave debounces
    // the actual PUT 2s later, but the in-memory + localStorage write is done.
    // Subsequent pure-browse clicks will early-return until next mutation.
    clearSessionDirty();

    document.dispatchEvent(new CustomEvent('session-saved'));
    return saveResult;  // D-6: undefined for debounced path; the saveSession().catch() promise for immediate
}

/**
 * LR 雙 row 收斂：採納後端 _create_lr_session 建立的 row A UUID 為當前 session 的 _serverId。
 *
 * 由 live-research.js 的 live_research_session_created handler 呼叫。
 * 解決「後端 _create_lr_session 與前端 saveCurrentSession 各自 POST 獨立 PG row」根因：
 * 收到 row A UUID 後，取消前端尚未發出的冗餘 POST（防 row B 生成），把 row A 設為
 * 當前 session 的 _serverId，後續 persist 一律走 PUT 到 row A（含 live_research_state）。
 *
 * INVARIANT: 純資料協調 — 絕不呼叫 performLiveResearch / 發任何 /api/live_research 請求。
 *
 * @param {string} serverId - 後端 row A 的 PG UUID（= lr_session_id）
 */
export function adoptLRServerSession(serverId) {
    if (!serverId || typeof serverId !== 'string') {
        console.warn('[adoptLRServerSession] 無效 serverId，跳過收斂:', serverId);
        return;
    }

    const loadedId = getCurrentLoadedSessionId();
    if (loadedId === null) {
        // 理論上 performLiveResearch 的 saveCurrentSession 已設過 currentLoadedSessionId；
        // 若為 null 表示沒有當前 session entry 可收斂（不應發生）。明示 warn，不 silent。
        console.warn('[adoptLRServerSession] currentLoadedSessionId 為 null，無 session 可採納 row A；跳過。serverId=', serverId);
        return;
    }

    const sessions = getSavedSessions();
    const idx = sessions.findIndex(s => window.matchSessionId(s.id, loadedId));
    if (idx === -1) {
        console.warn('[adoptLRServerSession] 當前 loaded session 不在 savedSessions（可能 shared context）；跳過收斂。loadedId=', loadedId);
        return;
    }

    const entry = sessions[idx];
    const priorServerId = entry._serverId || null;

    // (a) 取消該 session 尚未發出的 debounced POST，防止 row B 生成。
    if (window.sessionManager && typeof window.sessionManager._cancelPendingSave === 'function') {
        window.sessionManager._cancelPendingSave(entry);
    }

    // (b) 若前端 POST 已搶先發出並建立 row B（_serverId 已是「不同於 row A」的 UUID），刪除 row B 防垃圾遺留。
    if (priorServerId && priorServerId !== serverId) {
        console.warn('[adoptLRServerSession] 前端 POST 已搶先建立 row B（' + priorServerId + '），刪除以收斂到後端 row A（' + serverId + '）');
        if (window.sessionManager && typeof window.sessionManager.deleteSession === 'function') {
            // deleteSession(sessionId, serverId) — 第一參數僅供 localStorage fallback；此處只需 serverId 觸發 DELETE。
            window.sessionManager.deleteSession(entry.id, priorServerId).catch(e =>
                console.error('[adoptLRServerSession] 刪除 row B 失敗（不阻斷收斂）:', e)
            );
        }
    }

    // (c) 採納 row A 為 _serverId，並把 in-memory id + currentLoadedSessionId 對齊 row A UUID。
    //     對齊 id 讓後續 saveCurrentSession 的 findIndex/matchSessionId 命中同一 entry，走 PUT。
    entry._serverId = serverId;
    entry.id = serverId;
    setCurrentLoadedSessionId(serverId);

    // (d) persist localStorage（讓 _serverId 跨 reload 存活）。
    localStorage.setItem('taiwanNewsSavedSessions', JSON.stringify(getSavedSessions()));
    console.log('[adoptLRServerSession] 已採納後端 row A 為 _serverId:', serverId, '（priorServerId=', priorServerId, '）');

    document.dispatchEvent(new CustomEvent('session-saved'));
}
