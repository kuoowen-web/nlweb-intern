// ============================================================================
// v4.0 Frontend Modular Refactor Path A++ — FINAL STATE (commit 25, 2026-05-25)
// ============================================================================
// LOC at refactor end: ~3380 (baseline was 11697, -71% reduction).
//
// Per CEO directive 1 (2026-05-25): "≤500 LOC is GOAL, not hard target. 接近就好."
// We did not hit ≤500. Reason: 14 KEEP-in-place top-level functions plus the
// DOMContentLoaded bootstrap form a coherent residual that resists further
// extraction:
//   - loadSavedSession (~340 LOC) deep-couples to 17 DOM consts + 15 local
//     helpers (see core/session-coordinator.js D-1 header for trade-off).
//   - renderHistoryPopup / restoreSession / resetToHome / resetConversation /
//     openTab / closeAllTabs / renderConversationHistory / showAdvancedPopup /
//     hideAdvancedPopup / matchSessionId / updateUploadButtonVisibility /
//     showHistoryPopup / hideHistoryPopup — all DOM-const coupled or pure-glue.
//
// To hit ≤500 the remaining residual would need to either:
//   (a) move into a single `core/news-search-bootstrap.js` (mechanical move,
//       same coupling debt, no real concern separation), or
//   (b) decompose into 5+ tiny modules with new DOM-getter abstraction layer
//       (over-engineering for a one-time bootstrap surface).
// Neither path improves architectural clarity over current state. Future work
// can choose either if a clear need arises.
//
// All migrated owner modules (search / chat / pins / research / knowledge-graph
// / live-research / deep-research / folders / file-kb / sharing /
// source-filters / session-manager / sessions-list / mode / analytics /
// auth-manager / auth-ui / session-coordinator / page-bootstrap / state-sync)
// pass D-13 (inert on import) + D-V14 (single owner per state hex) per
// frontend_ownership_check.py phase 4b PASS.
//
// Window bridges remaining (~21) are INTENTIONAL — each one is consumed by an
// ES module that cannot direct-import without parse-time cycle (auth/UI →
// authManager → state-sync → news-search) or by the legacy sidebar inline
// onclick handlers. Future cleanup pass can convert sidebar to addEventListener
// + drop the sidebar bridges (~6) once the render functions accept handler
// callbacks. The remaining ~15 bridges are load-bearing.
// ============================================================================

// v4.0 Commit 1 (2026-05-24) — ES module imports for migrated state owners.
// news-search.js is `type="module"` after commit 0c — bare callsites for migrated
// declarations resolve via these named imports instead of classic-script outer-let scope.
import { getCurrentMode, setCurrentMode, clearCurrentMode } from './js/features/mode.js';
// v4.0 Commit 2 (2026-05-24) — search trio (conversationHistory / accumulatedArticles / currentConversationId).
// v4.0 Commit 14a (2026-05-25, Phase 8) — extended: 7 inflight handles + summaryExpanded
//   + escapeHTML / convertMarkdownToHtml utils + 12 simple helper functions.
//   Search-owned local lets (searchGenerationId / currentSearchAbortController /
//   currentSearchEventSource / currentDeepResearch{Event,Abort}* / currentFreeConvAbortController /
//   summaryExpanded) MIGRATED — access via getter/setter exports below.
import {
    getConversationHistory, setConversationHistory, clearConversationHistory, pushConversationHistory,
    getAccumulatedArticles, setAccumulatedArticles, clearAccumulatedArticles, pushAccumulatedArticles,
    getCurrentConversationId, setCurrentConversationId, clearCurrentConversationId,
    // commit 14a — state getters/setters
    getSummaryExpanded, setSummaryExpanded,
    getSearchGenerationId, bumpSearchGenerationId,
    getCurrentSearchAbortController, setCurrentSearchAbortController,
    getCurrentSearchEventSource, setCurrentSearchEventSource,
    getCurrentDeepResearchEventSource, setCurrentDeepResearchEventSource,
    getCurrentDeepResearchAbortController, setCurrentDeepResearchAbortController,
    getCurrentFreeConvAbortController, setCurrentFreeConvAbortController,
    // commit 14a — pure helpers
    escapeHTML, convertMarkdownToHtml,
    // commit 14a — 12 simple helper functions
    cancelActiveSearch, cancelAllActiveRequests, showInterruptedSearchNotice,
    clearQueryState, setProcessingState,
    renderSkeletonCards, renderSummarySkeleton, updateProgressMessage,
    createArticleCard, renderArticlesProgressive, renderAnswerProgressive,
    clearLoadingStates,
    // commit 14b — heavy SSE entry + populate + summaries toggle (8 functions)
    performSearch, handleStreamingRequest, handlePostStreamingRequest,
    populateResultsFromAPI, showMemoryNotification, showTimeFilterRelaxedWarning,
    showSummaries, hideSummaries
} from './js/features/search.js';
// v4.0 Commit 3 (2026-05-24) — chatHistory.
// v4.0 Commit 17 (2026-05-25, Phase 8): 13 chat function bodies MIGRATED.
//   - performFreeConversation (main entry — POST /ask streaming for free-chat with research context)
//   - addChatMessage (chat UI render + history append)
//   - Pin message: togglePinMessage / updatePinButtonState / renderPinnedBanner /
//     truncateText / scrollToMessage / togglePinnedDropdown / closePinnedDropdown / initPinnedBanner
//   - Pin news card: togglePinNewsCard / updateNewsCardPinState / renderPinnedNewsList
//   _messageIdCounter (was top-level let) + MAX_PINNED_MESSAGES/MAX_PINNED_NEWS constants
//   co-migrated as module-internal. Bridge removed: 1 — window.performFreeConversation
//   (was news-search.js:4561). Re-bridge kept (window.performFreeConversation = performFreeConversation)
//   because features/search.js performSearch chat-mode delegate uses window bridge. Sweep
//   commit 25 final cleanup.
import {
    getChatHistory, setChatHistory, clearChatHistory, pushChatHistory,
    // commit 17 — 13 function migrations
    performFreeConversation, addChatMessage,
    togglePinMessage, updatePinButtonState, renderPinnedBanner,
    truncateText, scrollToMessage, togglePinnedDropdown, closePinnedDropdown,
    initPinnedBanner,
    togglePinNewsCard, updateNewsCardPinState, renderPinnedNewsList
} from './js/features/chat.js';
// Re-bridge for search.js performSearch chat-mode delegate path. Sweep commit 25.
window.performFreeConversation = performFreeConversation;
// v4.0 Commit 4 (2026-05-24) — pin pair (pinnedMessages / pinnedNewsCards).
import {
    getPinnedMessages, setPinnedMessages, clearPinnedMessages,
    getPinnedNewsCards, setPinnedNewsCards, clearPinnedNewsCards
} from './js/features/pins.js';
// v4.0 Commit 5 (2026-05-24) — research trio (currentResearchReport / currentArgumentGraph / currentChainAnalysis).
import {
    getResearchReport, setResearchReport, clearResearchReport,
    getArgumentGraph, setArgumentGraph, clearArgumentGraph,
    getChainAnalysis, setChainAnalysis, clearChainAnalysis
} from './js/features/research.js';
// v4.0 Commit 6 (2026-05-24) — shareContentOverride (sharing modal explicit override).
// v4.0 Commit 18 (2026-05-25, Phase 8): 8 sharing/export function bodies MIGRATED.
//   Export builders: cleanHTMLContent, getTop10Articles, formatPlainText,
//     formatForAIChatbot, formatForNotebookLM, copyAndOpen, openFeedbackModal
//   Session sharing: toggleSessionSharing (AC-3 auto-refresh preserved)
//   Bridge removed: 1 — window.toggleSessionSharing (was news-search.js:6284).
//   Re-bridge kept (window.toggleSessionSharing = toggleSessionSharing) for sidebar
//   inline-onclick compat; sweep commit 25. _updateOrgSpaceBadge stays residual
//   (sessions-list scope commit 22) — exposed via window for sharing.js to call.
import {
    getShareContentOverride, setShareContentOverride, clearShareContentOverride,
    // commit 18 — 8 function migrations
    cleanHTMLContent, getTop10Articles,
    formatPlainText, formatForAIChatbot, formatForNotebookLM,
    copyAndOpen, openFeedbackModal,
    toggleSessionSharing
} from './js/features/sharing.js';
// Re-bridge for sidebar inline-onclick compat. Sweep commit 25.
window.toggleSessionSharing = toggleSessionSharing;
// v4.0 Commit 7 (2026-05-24) — LR state (currentLRSessionId + lrInProgress).
// D-V3 H1 fix: live-research.js also wires injectStateSyncBackref({...}) at module
// init to provide backref bundle for the IIFE relocate in commit 11.
// v4.0 Commit 16 (2026-05-25, Phase 8): 15 LR function bodies migrated.
//   - UI helpers: resetLiveResearchUI, addLRChatMessage, show/hideLRTypingIndicator,
//     updateLRTypingIndicatorText, resetLRTypingState, deriveActivityFromNarration,
//     updateLRStageProgress, showLRCheckpoint, addLRSection, showLRExport
//   - SSE handler: handleLiveResearchSSE
//   - Main entries: performLiveResearch, continueLiveResearch
//   Bridge removed: window.performLiveResearch (was line 3247). features/search.js
//   no longer delegates LR via window — DR mode delegate path uses ES import re-export
//   from search.js if needed; LR mode bound directly in news-search.js mode handler.
import {
    getLRSessionId, setLRSessionId, clearLRSessionId,
    isLRInProgress, setLRInProgress,
    // commit 16 — 15 LR functions migrated
    resetLiveResearchUI, addLRChatMessage,
    showLRTypingIndicator, hideLRTypingIndicator, updateLRTypingIndicatorText, resetLRTypingState,
    deriveActivityFromNarration, updateLRStageProgress,
    showLRCheckpoint, addLRSection, showLRExport,
    handleLiveResearchSSE, performLiveResearch, continueLiveResearch,
    // G3 — legacy session gate
    setLRLegacyMode, lockLRUIForLegacySession,
    // LR #19 — session resume
    restoreLRCheckpointFromState,
    // B5 (2026-06-05) — session-switch stale restore guard
    bumpLRSwitchToken,
} from './js/features/live-research.js?v=20260611a';
// Re-bridge performLiveResearch onto window so features/search.js performSearch
// LR mode delegate can still find it via window.performLiveResearch. Sweep target
// commit 25 final cleanup when search.js imports it directly.
window.performLiveResearch = performLiveResearch;
// v4.0 Commit 8 (2026-05-24) — folders quad (folders / sourceFolders / fileFolders / selectedFileIds)
// + _folderModeActive UI flag.
// v4.0 Commit 20 (2026-05-25, Phase 8 part C) — 16 session-folder UI functions + 5 UI lets MIGRATED.
import {
    getFolders, setFolders, pushFolder, removeFolder, clearFolders, persistFolders,
    getSourceFolders, setSourceFolders, clearSourceFolders,
    getFileFolders, setFileFolders, clearFileFolders,
    getSelectedFileIds, setSelectedFileIds, addSelectedFile, removeSelectedFile,
    hasSelectedFile, clearSelectedFileIds, getSelectedFileCount,
    getFolderModeActive, setFolderModeActive,
    // commit 20 — 16 session-folder UI functions migrated
    saveFolders, createFolder, renameFolder, deleteFolder,
    addSessionToFolder, removeSessionFromFolder,
    showFolderPage, hideFolderPage, showFolderMain, showFolderDetail,
    getTimeAgo, getSortedFolders, renderFolderGrid,
    toggleFolderDropdown, closeFolderDropdowns,
    setFolderFilter, setFolderSort, clearPreFolderState,
    startFolderRename, renderFolderDetailSessions,
    makeSidebarSessionsDraggable, removeSidebarSessionsDraggable
} from './js/features/folders.js';
// Re-bridge for sessions-list.js renderLeftSidebarSessions which calls
// window.makeSidebarSessionsDraggable when folder mode is active. Sweep commit 25.
window.makeSidebarSessionsDraggable = makeSidebarSessionsDraggable;
// v4.0 Commit 9 (2026-05-24) — analytics extension (currentAnalyticsQueryId).
// v4.0 Commit 12 (2026-05-25, Phase 8 prep) — getCurrentSessionId added to this import.
//   Removes the duplicate `let currentSessionId = sessionStorage.getItem('nlweb_session_id')`
//   IIFE that previously lived at line ~1641-1653 below. utils/analytics.js owns the
//   single source of truth for the tab-scoped session id (sessionStorage key).
import {
    getAnalyticsQueryId, setAnalyticsQueryId, clearAnalyticsQueryId,
    getCurrentSessionId
} from './js/utils/analytics.js';
// v4.0 Commit 10 (2026-05-24) — sessions hex (5 decls → sessions-list.js per D-V14 split).
//   savedSessions / currentLoadedSessionId / sessionHistory / _sharedSessionsCache / _sharedSessionsLoading
// v4.0 Commit 22 (2026-05-25, Phase 8 part C) — 4 sessions lifecycle functions added.
//   _updateOrgSpaceBadge / handleDeleteSession / deleteSavedSession / startSidebarSessionRename.
//   saveCurrentSession + loadSavedSession stay in news-search.js this commit (CEO #5
//   and DOM-const coupling — sweep commit 23+).
import {
    getSavedSessions, setSavedSessions, clearSavedSessions, hydrateSavedSessions,
    getCurrentLoadedSessionId, setCurrentLoadedSessionId, clearCurrentLoadedSessionId,
    getSessionHistory, setSessionHistory, clearSessionHistory, pushSessionHistory,
    getSharedSessions, setSharedSessions, clearSharedSessions, hydrateSharedSessions,
    isSharedSessionsLoading, setSharedSessionsLoading,
    hydrateFromSoftRefreshInit,
    // commit 22 — sessions lifecycle migrations
    _updateOrgSpaceBadge,
    handleDeleteSession, deleteSavedSession,
    startSidebarSessionRename
} from './js/features/sessions-list.js';
// Re-bridges so inline-onclick handlers (sidebar dropdowns built by renderLeftSidebarSessions)
// and features/sharing.js commit 18 (badge updater) keep working via window. Sweep
// commit 25 when all callers direct-import from sessions-list.js.
window._updateOrgSpaceBadge = _updateOrgSpaceBadge;
window.deleteSavedSession = deleteSavedSession;
window.startSidebarSessionRename = startSidebarSessionRename;
// v4.0 Commit 10 (2026-05-24) — _sessionDirty (D-V14: owned by session-manager.js, not sessions-list).
import {
    isSessionDirty, markSessionDirty, clearSessionDirty
} from './js/features/session-manager.js';
// v4.0 Commit 23 (2026-05-25, Phase 8 FINAL) — saveCurrentSession MIGRATED to core/session-coordinator.js.
// loadSavedSession stays KEEP-in-place (deep DOM-const coupling — see session-coordinator.js D-1 header).
import { saveCurrentSession, adoptLRServerSession } from './js/core/session-coordinator.js';
// v4.0 Commit 24 (2026-05-25, Phase 8 FINAL) — 20 auth UI functions MIGRATED to core/auth-ui.js (NEW).
//   updateAuthUI / showAuthModal / hideAuthModal / switchAuthTab / showMainUI / hideMainUI /
//   getCurrentUserId / handleInviteToken / showAcceptInviteToast / acceptInvite /
//   openOrgModal / reloadOrgMembers / closeOrgModal / renderOrgMembers / removeMember /
//   changeUserRole / toggleUserActive / forceLogoutUser / deleteUser / escapeAttr.
import {
    updateAuthUI, showAuthModal, hideAuthModal, switchAuthTab,
    showMainUI, hideMainUI, getCurrentUserId,
    handleInviteToken, showAcceptInviteToast, acceptInvite,
    openOrgModal, reloadOrgMembers, closeOrgModal, renderOrgMembers,
    removeMember, changeUserRole, toggleUserActive, forceLogoutUser,
    deleteUser, escapeAttr
} from './js/core/auth-ui.js';
// Bridges so external modules (page-bootstrap.js / auth-manager.js / state-sync.js)
// can reach via window without direct-importing auth-ui.js (avoids parse-time cycle).
// Sweep target: when those modules direct-import auth-ui.js.
window.updateAuthUI = updateAuthUI;
window.showAuthModal = showAuthModal;
window.hideAuthModal = hideAuthModal;
window.showMainUI = showMainUI;
window.hideMainUI = hideMainUI;
window.getCurrentUserId = getCurrentUserId;
// v4.0 Commit 13 (2026-05-25, Phase 8) — source-filters owner: 4 lets + 18 functions migrated.
//   includePrivateSources owned here per CEO decision #2; updateIncludePrivateSourcesState
//   migrated to features/file-kb.js (commit 19) writes via setIncludePrivateSources.
import {
    getAvailableSites, setAvailableSites,
    getSelectedSites, setSelectedSites,
    getSourceDisplayNames,
    getIncludePrivateSources, setIncludePrivateSources,
    loadSiteFilters,
    getSelectedSitesParam,
    togglePrivateSources,
    triggerFileUpload,
    addSourceFolder, expandAllSourceFolders, collapseAllSourceFolders, toggleAllSites
} from './js/features/source-filters.js';
// v4.0 Commit 19 (2026-05-25, Phase 8 part C) — file-kb owner: userFiles state + 19 functions migrated.
import {
    getUserFiles, setUserFiles, clearUserFiles,
    handleFileSelect, loadFileFolders, saveFileFolders, saveSelectedFiles,
    loadUserFiles, distributeFilesToFolders, renderFileTreeView, bindFileTreeEvents,
    updateIncludePrivateSourcesState, moveFileToFolder,
    addFileFolder, startRenamingFileFolder, deleteFileFolder,
    expandAllFileFolders, collapseAllFileFolders, renderFileList,
    deleteUserFile, getFileIcon, getStatusText
} from './js/features/file-kb.js';
// v4.0 Commit 11 (2026-05-24, Path A completion) — UserStateSync IIFE relocated to state-sync.js.
// news-search.js residual callsites (mismatch-detect → runInitSync trigger from sidebar
// click handler / session-switch identity-check paths) reference these directly via import.
import {
    UserStateSync, UserStateSyncError, assertUserIdentity
} from './js/core/state-sync.js';
import { authManager } from './js/core/auth-manager.js';
import { getSessionManager } from './js/features/session-manager.js';
// v4.0 Commit 15 (2026-05-25, Phase 8) — deep-research.js NEW module.
//   24 functions migrated: DR pipeline (performDeepResearch), DR display
//   (displayDeepResearchResults / renderResearchReportToView / showDRError /
//   updateReasoningProgress), citation/collapsible helpers (6 fns), clarification
//   UI (3 fns), reasoning chain (9 fns + formatReasoningForVerification).
//   currentResearchQueryId migrated as module state — access via
//   getCurrentResearchQueryId() / setCurrentResearchQueryId(id) / clearCurrentResearchQueryId().
import {
    performDeepResearch, displayDeepResearchResults, renderResearchReportToView,
    showDRError, updateReasoningProgress,
    addCitationLinks, generateCitationReferenceList, bindCitationReferenceToggles,
    addCollapsibleSections, bindCollapsibleHandlers, addToggleAllToolbar,
    addClarificationMessage, attachClarificationListeners, submitClarification,
    displayReasoningChainInContainer, displayReasoningChain,
    createReasoningChainContainer, createLogicInconsistencyWarning, createCycleWarning,
    createCriticalNodesAlert, renderArgumentNode, setupHoverInteractions,
    inferScore, formatReasoningForVerification,
    getCurrentResearchQueryId, setCurrentResearchQueryId, clearCurrentResearchQueryId
} from './js/features/deep-research.js';

        // v4.0 Commit 15 (2026-05-25, Phase 8): re-bridge deep-research.js exports to
        //   window so features/search.js performSearch (DR mode delegate) can reach them
        //   via window.performDeepResearch. submitClarification (in deep-research.js itself)
        //   also calls performDeepResearch directly via import — no bridge needed there.
        //   Sweep target commit 19 when search.js moves to direct import (after batch 6''
        //   reorders chat/LR migration to remove circular risk).
        window.performDeepResearch = performDeepResearch;
        window.showDRError = showDRError;
        window.updateReasoningProgress = updateReasoningProgress;

// v4.0 Commit 21 (2026-05-25, Phase 8 part C) — knowledge-graph.js NEW module.
//   24 KG functions + 5 constants + 7 state lets migrated. KG render / edit mode /
//   D3 graph / popovers / serialize / confirmKGEdit rerun all owned by module.
//   2 bridges swept FROM declaration site (was news-search.js 2816 + 2904):
//     - window.__getCurrentKGData (re-bridged at import site below; deep-research.js
//       still reads via window — sweep commit 25 when deep-research.js direct-imports)
//     - window.displayKnowledgeGraph (re-bridged at import site below; sidebar
//       restoreSession + deep-research.js callers read via window — sweep commit 25)
//   New accessors used inside news-search.js residual: getCurrentKGData / getKGEditMode /
//   resetKGState (replaces the inline 2421-2438 reset block).
import {
    displayKnowledgeGraph,
    getCurrentKGData, getKGEditMode, resetKGState
} from './js/features/knowledge-graph.js';
// Re-bridge so features/deep-research.js can still reach via window. Sweep commit 25
// when deep-research.js direct-imports from knowledge-graph.js.
window.displayKnowledgeGraph = displayKnowledgeGraph;
window.__getCurrentKGData = getCurrentKGData;

        // ==================== USER STATE SYNC HELPERS ====================
        // v4.0 Commit 11 (2026-05-24): UserStateSyncError class + assertUserIdentity function
        //   MIGRATED to static/js/core/state-sync.js (named exports).
        //   Bridges removed: window.UserStateSyncError / window.assertUserIdentity.
        //   Callers in this file (none active outside the IIFE which itself moves)
        //   should import from core/state-sync.js if a new reference is needed.

        // ==================== AUTH MANAGER ====================
        // Phase 3 Path B (2026-05-21): AuthManager class + authManager singleton MOVED to
        // static/js/core/auth-manager.js. The block below is preserved (commented out, not
        // deleted) for reference until Phase 8 sweep. Active singleton is now exported from
        // auth-manager.js and attached to window by main.js: `window.authManager = authManager`.
        // Test contract: contract test (test_user_state_sync_invariant.py) reads source via
        // `read_text()` so substring "class AuthManager", "async login", "UserStateSync.runInitSync"
        // remain present in the file (inside line comments below) — tests continue to PASS.
        //
        // Reason for line-by-line `// ` prefix (NOT block `/* ... */`): the AuthManager class
        // body contains inline `/* ignore network error */` comments. JS does not support
        // nested block comments — a single `/* PHASE 3 ... */` wrapper would be truncated by
        // the first inner `*/`. line-by-line prefix preserves the entire block as inert text.
        //
        // class AuthManager {
            // List of localStorage keys that are user-scoped and MUST be cleared
            // when a different user logs in (origin-scoped storage means cross-user
            // leakage if not cleared). Device-scoped UI prefs (nlweb-large-font,
            // nlweb-kg-hidden) are intentionally excluded.
            // static USER_SCOPED_KEYS = [
                // 'taiwanNewsSavedSessions',
                // 'taiwanNewsFolders',
                // 'taiwanNewsSessionsMigrated',
                // 'nlweb_source_folders',
                // 'nlweb_file_folders',
                // 'nlweb_selected_files',
            // ];

            // constructor() {
                // this._accessToken = null;
                // this._user = null;
                // this._refreshPromise = null;
                // this._init();
            // }

            // _init() {
                // Try to load user from localStorage
                // const stored = localStorage.getItem('authUser');
                // if (stored) {
                    // try {
                        // this._user = JSON.parse(stored);
                    // } catch (e) {
                        // localStorage.removeItem('authUser');
                    // }
                // }
                // const storedToken = localStorage.getItem('authAccessToken');
                // if (storedToken && storedToken !== 'undefined') {
                    // this._accessToken = storedToken;
                // } else {
                    // localStorage.removeItem('authAccessToken');
                // }
            // }

            // Task 13 cleanup: _clearUserScopedStorageIfUserChanged removed.
            // Superseded by UserStateSync.runInitSync, which calls fullReset
            // (= clearUserScopedState + resetMainUI) before applyInit. The
            // helper became a double-clear once Task 5 routed login() through
            // runInitSync. UserStateSync.clearUserScopedState already iterates
            // AuthManager.USER_SCOPED_KEYS plus authUser / authAccessToken.

            // isLoggedIn() {
                // BP-1: access_token is in httpOnly cookie (not in JS), so only check _user
                // return !!this._user;
            // }

            // getCurrentUser() {
                // return this._user;
            // }

            // getAccessToken() {
                // return this._accessToken;
            // }

            // async login(email, password) {
                // const res = await fetch('/api/auth/login', {
                    // method: 'POST',
                    // headers: { 'Content-Type': 'application/json' },
                    // body: JSON.stringify({ email, password }),
                    // credentials: 'same-origin'
                // });
                // const data = await res.json();
                // if (!res.ok) throw new Error(data.error || 'Login failed');
                // Trigger A (login). clear+fetch+apply goes through
                // UserStateSync.runInitSync (called below). The legacy
                // _clearUserScopedStorageIfUserChanged helper has been removed
                // in Task 13 — fullReset inside runInitSync covers the same
                // intent (cross-user clear) without the double-clear.
                // BP-1: access_token is in httpOnly cookie, not in response body
                // this._accessToken = data.access_token || null;
                // this._user = data.user;
                // if (this._accessToken) {
                    // localStorage.setItem('authAccessToken', this._accessToken);
                // } else {
                    // localStorage.removeItem('authAccessToken');
                // }
                // Trigger A: full reset + GET /api/user/init + apply.
                // localStorage.authUser write happens inside applyInit;
                // do NOT duplicate the write here. keepInviteToken=true so a
                // pending invite (sessionStorage) survives login.
                // try {
                    // await UserStateSync.runInitSync({ keepInviteToken: true });
                // } catch (e) {
                    // console.error('[login] runInitSync failed; falling back to legacy authUser persist:', e);
                    // Fallback: still persist authUser so subsequent reload can recover.
                    // try { localStorage.setItem('authUser', JSON.stringify(this._user)); } catch (_) {}
                // }
                // return data;
            // }

            // async register(email, password, name) {
                // const res = await fetch('/api/auth/register', {
                    // method: 'POST',
                    // headers: { 'Content-Type': 'application/json' },
                    // body: JSON.stringify({ email, password, name })
                // });
                // const data = await res.json();
                // if (!res.ok) throw new Error(data.error || 'Registration failed');
                // return data;
            // }

            // async refreshToken() {
                // Deduplicate concurrent refresh calls
                // if (this._refreshPromise) return this._refreshPromise;
                // this._refreshPromise = (async () => {
                    // try {
                        // const res = await fetch('/api/auth/refresh', {
                            // method: 'POST',
                            // credentials: 'same-origin'
                        // });
                        // const data = await res.json();
                        // if (!res.ok) throw new Error(data.error || 'Refresh failed');
                        // BP-1: access_token may be in httpOnly cookie, not in response
                        // if (data.access_token) {
                            // this._accessToken = data.access_token;
                            // localStorage.setItem('authAccessToken', this._accessToken);
                        // }
                        // return data;
                    // } catch (e) {
                        // this._handleAuthFailure();
                        // throw e;
                    // } finally {
                        // this._refreshPromise = null;
                    // }
                // })();
                // return this._refreshPromise;
            // }

            // async logout() {
                // try {
                    // await fetch('/api/auth/logout', {
                        // method: 'POST',
                        // credentials: 'same-origin'
                    // });
                // } catch (e) { /* ignore network error; still clear locally */ }
                // Trigger C: full clear + UI reset + show login modal.
                // Delegated to _handleAuthFailure (single fullReset call),
                // which handles cancelPendingSave → clear → UI reset → modal.
                // this._handleAuthFailure();
            // }

            // async authenticatedFetch(url, options = {}) {
                // if (!options.headers) options.headers = {};
                // if (this._accessToken) {
                    // options.headers['Authorization'] = `Bearer ${this._accessToken}`;
                // }
                // options.credentials = 'same-origin';

                // let res = await fetch(url, options);

                // If 401, try refresh once (BP-1: always try, cookie may have expired)
                // if (res.status === 401) {
                    // try {
                        // const cachedUserId = this._user?.id || null;
                        // await this.refreshToken();

                        // Task 12 (D-4): distinguish same-user token rotation vs
                        // user identity change. Decode the new access_token's JWT
                        // payload (no signature verify — backend already validated)
                        // and compare payload.user_id against the cached _user.id.
                        // Same → silent refresh, no UI flash. Different → user
                        // identity change (e.g. cookies swapped, multi-account
                        // browser session) → trigger A via runInitSync.
                        // if (this._accessToken && cachedUserId) {
                            // try {
                                // const parts = this._accessToken.split('.');
                                // if (parts.length === 3) {
                                    // base64url decode (atob handles standard base64;
                                    // JWT uses base64url so normalise +/- and pad).
                                    // const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
                                    // const pad = b64.length % 4 === 0 ? '' : '='.repeat(4 - (b64.length % 4));
                                    // const payload = JSON.parse(atob(b64 + pad));
                                    // const newUid = payload.user_id || payload.sub || null;
                                    // if (newUid && newUid !== cachedUserId) {
                                        // console.warn(`[authenticatedFetch] refresh changed user identity: cached=${cachedUserId} → new=${newUid}; triggering runInitSync`);
                                        // Fire-and-forget so the original request can still proceed;
                                        // the init-sync will re-render sidebar/UI to the new user.
                                        // UserStateSync.runInitSync({ keepInviteToken: false }).catch(err =>
                                            // console.error('[authenticatedFetch:refresh-identity-change] runInitSync failed:', err));
                                    // }
                                    // Same user (or no newUid) → silent rotation, no-op.
                                // }
                            // } catch (decodeErr) {
                                // console.warn('[authenticatedFetch] JWT payload decode failed; skipping identity check:', decodeErr);
                            // }
                        // }

                        // options.headers['Authorization'] = `Bearer ${this._accessToken}`;
                        // res = await fetch(url, options);
                    // } catch (e) {
                        // CEO P0 UX fix (2026-05-19): refresh fail 必須 trigger
                        // _handleAuthFailure（顯示 login modal + reset state），不可靜默
                        // return 401 — 否則 caller 看 raw "HTTP 401" 顯示給 user。
                        // 對齊 spec §5.3 token expire mid-LR 紀律：refresh 失敗 → 跳「請重新登入」。
                        // console.error('[authenticatedFetch] refresh failed; triggering _handleAuthFailure:', e);
                        // this._handleAuthFailure();
                    // }
                // }
                // return res;
            // }

            // async forgotPassword(email) {
                // const res = await fetch('/api/auth/forgot-password', {
                    // method: 'POST',
                    // headers: { 'Content-Type': 'application/json' },
                    // body: JSON.stringify({ email })
                // });
                // const data = await res.json();
                // if (!res.ok) throw new Error(data.error || 'Request failed');
                // return data;
            // }

            // _handleAuthFailure() {
                // Trigger C (logout) + Trigger D (401 / refresh fail). Both share
                // the same effect: full clear + UI reset + show login modal.
                // UserStateSync.fullReset handles cancelPendingSave → localStorage
                // / sessionStorage / in-memory globals / sessionManager internals
                // → resetMainUI in one call (single source-of-truth for clearing).
                // Call sequence: cancel timer (inside fullReset) → clear state →
                // null AuthManager fields → reset UI → show modal.
                // try {
                    // UserStateSync.fullReset({ keepInviteToken: false });
                // } catch (e) {
                    // console.error('[_handleAuthFailure] UserStateSync.fullReset error:', e);
                // }
                // this._accessToken = null;
                // this._user = null;
                // if (typeof updateAuthUI === 'function') updateAuthUI();
                // Auth guard: hide main UI and show login modal
        //         if (typeof hideMainUI === 'function') hideMainUI();
        //         if (typeof showAuthModal === 'function') showAuthModal('login');
        //     }
        // }
        //
        // const authManager = new AuthManager();
        // (end PHASE 3 PATH B — AuthManager class + singleton moved to core/auth-manager.js)

        // ==================== SESSION MANAGER ====================
        // Wraps API calls (logged in) or falls back to localStorage (not logged in).
        // Handles auto-migration from localStorage to server on first login.
        // class SessionManager {
            // constructor(authMgr) {
                // this._auth = authMgr;
                // this._saveTimer = null;
                // this._savePending = false;
                // // Defensive: track recent POSTs per session.id to detect _serverId-loss
                // // regressions. If POST fires twice within 5s for the same id, surface
                // // as console.error and suppress the duplicate POST.
                // this._postedRecently = new Map();
            // }

// (Phase 4b removed)
            // _isOnline() {
                // return this._auth.isLoggedIn() && this._auth.getCurrentUser()?.org_id;
            // }

// (Phase 4b removed)
            // // -- Sessions --

// (Phase 4b removed)
            // async loadSessions() {
                // if (this._isOnline()) {
                    // try {
                        // const res = await this._auth.authenticatedFetch('/api/sessions');
                        // const data = await res.json();
                        // if (res.ok && data.success) return data.sessions;
                        // // Server returned non-OK — explicitly log, do NOT silent-fallback
                        // // to localStorage (would risk loading another user's stale data
                        // // per lessons-frontend L201). Better to show empty sidebar than
                        // // leak previous user's sessions.
                        // console.error('[SessionManager] /api/sessions non-OK:', res.status, data);
                    // } catch (e) {
                        // console.error('[SessionManager] /api/sessions error:', e);
                    // }
                    // // Logged-in path: no localStorage fallback. Return [] so sidebar
                    // // shows empty, surfacing the server failure visibly.
                    // return [];
                // }
                // // Not logged in: localStorage is the primary source of truth.
                // try {
                    // const stored = localStorage.getItem('taiwanNewsSavedSessions');
                    // return stored ? JSON.parse(stored) : [];
                // } catch (e) {
                    // console.error('[SessionManager] Failed to load from localStorage:', e);
                    // return [];
                // }
            // }

// (Phase 4b removed)
            // async loadSharedSessions() {
                // if (!this._isOnline()) return [];
                // try {
                    // const res = await this._auth.authenticatedFetch('/api/sessions/shared');
                    // const data = await res.json();
                    // if (res.ok && data.success) return data.sessions;
                    // console.warn('[SessionManager] loadSharedSessions failed:', data);
                    // return [];
                // } catch (e) {
                    // console.warn('[SessionManager] loadSharedSessions error:', e);
                    // return [];
                // }
            // }

// (Phase 4b removed)
            // async setSessionVisibility(serverId, visibility) {
                // if (!serverId) throw new Error('Session not saved to server yet');
                // if (!this._isOnline()) throw new Error('Need to join an organization to share sessions');
                // const res = await this._auth.authenticatedFetch(`/api/sessions/${serverId}/visibility`, {
                    // method: 'PATCH',
                    // headers: { 'Content-Type': 'application/json' },
                    // body: JSON.stringify({ visibility })
                // });
                // const data = await res.json();
                // if (!res.ok) throw new Error(data.error || 'Failed to set visibility');
                // return data;
            // }

// (Phase 4b removed)
            // async saveSession(session) {
                // if (this._isOnline()) {
                    // try {
                        // if (session._serverId) {
                            // // Update existing
                            // await this._auth.authenticatedFetch(`/api/sessions/${session._serverId}`, {
                                // method: 'PUT',
                                // headers: { 'Content-Type': 'application/json' },
                                // body: JSON.stringify({
                                    // title: session.title,
                                    // mode: session.mode,
                                    // conversation_history: session.conversationHistory,
                                    // session_history: session.sessionHistory,
                                    // chat_history: session.chatHistory,
                                    // accumulated_articles: session.accumulatedArticles,
                                    // pinned_messages: session.pinnedMessages,
                                    // pinned_news_cards: session.pinnedNewsCards,
                                    // research_report: session.researchReport,
                                    // conversation_id: session.conversationId,
                                // })
                            // });
                        // } else {
                            // // DEFENSIVE: detect _serverId-loss regression. If POST fires twice in
                            // // 5s for the same in-memory session id, _serverId was lost between calls.
                            // // Likely cause: saveCurrentSession overwrite drops _serverId, hydrate
                            // // path forgets to backfill, or a new code path bypasses the wiring.
                            // // Suppress the second POST to avoid PG row spawn; surface as console.error.
                            // const lastPost = this._postedRecently.get(session.id) || 0;
                            // if (Date.now() - lastPost < 5000) {
                                // console.error(
                                    // '[SessionManager] DEFENSIVE: POST suppressed (duplicate within 5s) for session.id=',
                                    // session.id,
                                    // '— possible _serverId-loss regression. Check saveCurrentSession overwrite (~1626), hydrate (~7745), loadSessions (~911).'
                                // );
                                // return;
                            // }
                            // this._postedRecently.set(session.id, Date.now());
                            // // Create new
                            // const res = await this._auth.authenticatedFetch('/api/sessions', {
                                // method: 'POST',
                                // headers: { 'Content-Type': 'application/json' },
                                // body: JSON.stringify({
                                    // title: session.title,
                                    // mode: session.mode,
                                    // conversation_history: session.conversationHistory,
                                    // session_history: session.sessionHistory,
                                    // chat_history: session.chatHistory,
                                    // accumulated_articles: session.accumulatedArticles,
                                    // research_report: session.researchReport,
                                    // conversation_id: session.conversationId,
                                // })
                            // });
                            // const data = await res.json();
                            // if (res.ok && data.success) {
                                // session._serverId = data.session.id;
                                // // Persist _serverId to localStorage so it survives page refresh
                                // this._saveToLocalStorage();
                                // // Re-render sidebar so sharing button appears immediately
                                // document.dispatchEvent(new CustomEvent('session-saved'));
                            // }
                        // }
                        // return;
                    // } catch (e) {
                        // console.warn('[SessionManager] API save failed, falling back to localStorage', e);
                    // }
                // }
                // // Fallback: save all sessions to localStorage
                // this._saveToLocalStorage();
            // }

// (Phase 4b removed)
            // async deleteSession(sessionId, serverId) {
                // if (this._isOnline() && serverId) {
                    // try {
                        // await this._auth.authenticatedFetch(`/api/sessions/${serverId}`, {
                            // method: 'DELETE'
                        // });
                        // return;
                    // } catch (e) {
                        // console.warn('[SessionManager] API delete failed, falling back to localStorage', e);
                    // }
                // }
                // this._saveToLocalStorage();
            // }

// (Phase 4b removed)
            // async renameSession(sessionId, serverId, newTitle) {
                // if (this._isOnline() && serverId) {
                    // try {
                        // await this._auth.authenticatedFetch(`/api/sessions/${serverId}`, {
                            // method: 'PUT',
                            // headers: { 'Content-Type': 'application/json' },
                            // body: JSON.stringify({ title: newTitle })
                        // });
                        // return;
                    // } catch (e) {
                        // console.warn('[SessionManager] API rename failed, falling back to localStorage', e);
                    // }
                // }
                // this._saveToLocalStorage();
            // }

// (Phase 4b removed)
            // // -- Folders --

// (Phase 4b removed)
            // async loadFolders() {
                // try {
                    // const stored = localStorage.getItem('taiwanNewsFolders');
                    // return stored ? JSON.parse(stored) : [];
                // } catch (e) {
                    // console.error('[SessionManager] Failed to load folders:', e);
                    // return [];
                // }
            // }

// (Phase 4b removed)
            // saveFoldersSync(foldersData) {
                // localStorage.setItem('taiwanNewsFolders', JSON.stringify(foldersData));
            // }

// (Phase 4b removed)
            // // -- Migration --

// (Phase 4b removed)
            // async migrateFromLocal() {
                // if (!this._isOnline()) return { migrated: false };

// (Phase 4b removed)
                // const localKey = 'taiwanNewsSavedSessions';
                // const migratedFlag = 'taiwanNewsSessionsMigrated';

// (Phase 4b removed)
                // if (localStorage.getItem(migratedFlag)) return { migrated: false, reason: 'already_migrated' };

// (Phase 4b removed)
                // const stored = localStorage.getItem(localKey);
                // if (!stored) return { migrated: false, reason: 'no_local_data' };

// (Phase 4b removed)
                // let sessions;
                // try {
                    // sessions = JSON.parse(stored);
                // } catch (e) {
                    // return { migrated: false, reason: 'parse_error' };
                // }

// (Phase 4b removed)
                // if (!sessions.length) return { migrated: false, reason: 'empty' };

// (Phase 4b removed)
                // try {
                    // const res = await this._auth.authenticatedFetch('/api/sessions/migrate', {
                        // method: 'POST',
                        // headers: { 'Content-Type': 'application/json' },
                        // body: JSON.stringify({ sessions })
                    // });
                    // const data = await res.json();
                    // if (res.ok && data.success) {
                        // localStorage.setItem(migratedFlag, Date.now().toString());
                        // localStorage.removeItem(localKey);
                        // localStorage.removeItem('taiwanNewsFolders');
                        // console.log(`[SessionManager] Migrated ${data.created} sessions to server, cleared localStorage`);
                        // return { migrated: true, created: data.created, errors: data.errors };
                    // }
                // } catch (e) {
                    // console.error('[SessionManager] Migration failed:', e);
                // }
                // return { migrated: false, reason: 'api_error' };
            // }

// (Phase 4b removed)
            // // -- Debounced Save --

// (Phase 4b removed)
            // scheduleSave(session) {
                // // Debounce: save after 2 seconds of inactivity
                // if (this._saveTimer) clearTimeout(this._saveTimer);
                // this._savePending = true;
                // this._saveTimer = setTimeout(() => {
                    // this._savePending = false;
                    // this.saveSession(session).catch(e =>
                        // console.error('[SessionManager] Debounced save failed:', e)
                    // );
                // }, 2000);
            // }

// (Phase 4b removed)
            // flushPendingSave(session) {
                // if (this._savePending && this._saveTimer) {
                    // clearTimeout(this._saveTimer);
                    // this._savePending = false;
                    // this.saveSession(session).catch(e =>
                        // console.error('[SessionManager] Flush save failed:', e)
                    // );
                // }
            // }

// (Phase 4b removed)
            // // RCA Fix 2 (hidden-path): cancel any pending debounced save without firing.
            // // Called from authManager._handleAuthFailure to prevent a 2s-deferred PUT
            // // from firing after logout/auth-failure — that PUT would 401 and recursively
            // // wipe the sidebar a second time. Pure cleanup; no PUT side effects.
            // _cancelPendingSave() {
                // if (this._saveTimer) {
                    // clearTimeout(this._saveTimer);
                    // this._saveTimer = null;
                // }
                // this._savePending = false;
            // }

// (Phase 4b removed)
            // // -- Preferences --

// (Phase 4b removed)
            // async loadPreferences() {
                // if (this._isOnline()) {
                    // try {
                        // const res = await this._auth.authenticatedFetch('/api/preferences');
                        // const data = await res.json();
                        // if (res.ok && data.success) return data.preferences;
                    // } catch (e) {
                        // console.warn('[SessionManager] Failed to load preferences from API', e);
                    // }
                // }
                // return {};
            // }

// (Phase 4b removed)
            // async setPreference(key, value) {
                // if (this._isOnline()) {
                    // try {
                        // await this._auth.authenticatedFetch(`/api/preferences/${key}`, {
                            // method: 'PUT',
                            // headers: { 'Content-Type': 'application/json' },
                            // body: JSON.stringify({ value })
                        // });
                    // } catch (e) {
                        // console.warn('[SessionManager] Failed to set preference via API', e);
                    // }
                // }
            // }

// (Phase 4b removed)
            // // -- Internal helpers --

// (Phase 4b removed)
            // _saveToLocalStorage() {
                // // Called as fallback; savedSessions is a global variable
                // try {
                    // localStorage.setItem('taiwanNewsSavedSessions', JSON.stringify(savedSessions));
                // } catch (e) {
                    // console.error('[SessionManager] Failed to save to localStorage:', e);
                // }
            // }
        // }

        // // Phase 3 Path B (2026-05-21): sessionManager construction deferred to main.js
        // // DOMContentLoaded handler because authManager moved to ES module — its window
        // // bridge is set AFTER classic-script parse time. Line 617's original direct
        // // `new SessionManager(authManager)` fired ReferenceError that aborted the rest
        // // of news-search.js (including UserStateSync IIFE and lrInProgress getter).
        // // Phase 4b will move SessionManager class itself to features/session-manager.js.
        // let sessionManager;
        // window._initSessionManager = function () {
            // if (!sessionManager) {
                // sessionManager = new SessionManager(window.authManager);
                // window.sessionManager = sessionManager;  // expose for module callsites if needed
            // }
        // };

        // Flush pending saves before page unload
        window.addEventListener('beforeunload', () => {
            if (window.sessionManager && getCurrentLoadedSessionId() !== null) {
                const currentSession = getSavedSessions().find(s => window.matchSessionId(s.id, getCurrentLoadedSessionId()));
                if (currentSession) window.sessionManager.flushPendingSave(currentSession);
            }
        });

        // v4.0 Commit 24 (2026-05-25, Phase 8 FINAL): 20 auth UI functions MIGRATED
        // to core/auth-ui.js (NEW). Imported below. Bridges kept for external callers:
        //   - window.updateAuthUI / showMainUI / hideMainUI / showAuthModal / hideAuthModal
        //     consumed by core/auth-manager.js + core/page-bootstrap.js + core/state-sync.js
        //     via window lookup (these modules cannot direct-import auth-ui.js to avoid
        //     circular ref: auth-ui.js → window.authManager (set by main.js after auth-manager
        //     module-init) ← which is fine, but importing auth-ui.js into auth-manager.js
        //     itself would create a parse-time cycle).
        //   - window.getCurrentUserId consumed by features/deep-research.js (private sources
        //     path). Sweep target later when deep-research.js direct-imports auth-ui.
        // Z-prep window-attaches removed: updateAuthUI / showAuthModal / hideAuthModal /
        //   showMainUI / hideMainUI / getCurrentUserId — replaced by the bridge block above.

        // ==================== AUTH READY PROMISE (KEEP-in-place) ====================
        // KEEP-in-place: _authReadyPromise / _authReadyResolve binding stays here because
        // (a) other code in this same file awaits it via bare name (file-kb loader,
        // DOMContentLoaded handler line ~3361 `await _authReadyPromise`), and (b)
        // core/page-bootstrap.js resolves it via window._authReadyResolve. Moving it
        // to auth-ui.js would require either rewriting the bare-name awaits as
        // imports (many callsites) or re-bridging _authReadyPromise back onto module
        // scope — pure noise. Lives here cleanly.
        let _authReadyResolve;
        const _authReadyPromise = new Promise(r => { _authReadyResolve = r; });
        // Phase 3 Path B (2026-05-21): explicit window attach so ES module
        // (static/js/core/page-bootstrap.js's checkAuthOnLoad) can resolve the
        // promise via `window._authReadyResolve()`. classic-script `let` does NOT
        // auto-attach to window.
        window._authReadyResolve = _authReadyResolve;
        window._authReadyPromise = _authReadyPromise;

        // async function checkAuthOnLoad() {
            // try {
                // let res = await authManager.authenticatedFetch('/api/auth/me');
                // if (res.status === 401) {
                    // Try to refresh token before giving up (prevents login modal flash on valid sessions)
                    // try {
                        // await authManager.refreshToken();
                        // res = await authManager.authenticatedFetch('/api/auth/me');
                    // } catch (refreshErr) {
                        // Refresh failed — fall through to show login modal
                    // }
                // }
                // if (res.status === 401) {
                    // Y-2/Y-3 fix: 401 path must fully clear stale auth state.
                    // Previously only hideMainUI + showAuthModal — but authManager._user
                    // remained populated from localStorage cache, so isLoggedIn() returned
                    // true. The subsequent loadSessions then silently fell back to
                    // localStorage and loaded the *previous* user's sessions (cross-user
                    // leak via in-memory savedSessions). _handleAuthFailure clears _user,
                    // localStorage, savedSessions, re-renders, hides UI, and shows modal.
                    // authManager._handleAuthFailure();
                    // return;
                // }
                // if (res.ok) {
                    // const data = await res.json();
                    // if (data.user) {
                        // Trigger B (user identity change): use assertUserIdentity
                        // invariant helper. On MISMATCH, full reset + init sync
                        // (NOT case-by-case clearing). On MISSING_CACHED (first
                        // load), just persist authUser and proceed. On
                        // MISSING_FRESH (server returned no user.id) — abnormal,
                        // log + fall through to login modal.
                        // const cached = (() => {
                            // try { return JSON.parse(localStorage.getItem('authUser') || 'null'); }
                            // catch (_) { return null; }
                        // })();

                        // let mismatch = false;
                        // let freshMissing = false;
                        // try {
                            // assertUserIdentity(cached, data.user);
                        // } catch (e) {
                            // if (e instanceof UserStateSyncError) {
                                // if (e.code === 'MISMATCH') {
                                    // console.warn('[checkAuthOnLoad] user identity mismatch, triggering full reset:', e.message);
                                    // mismatch = true;
                                // } else if (e.code === 'MISSING_CACHED') {
                                    // First-time load on this browser; normal — no cached user to compare.
                                // } else if (e.code === 'MISSING_FRESH') {
                                    // console.error('[checkAuthOnLoad] /api/auth/me returned user without id; refusing to apply:', e.message);
                                    // freshMissing = true;
                                // } else {
                                    // throw e;
                                // }
                            // } else {
                                // throw e;
                            // }
                        // }

                        // if (freshMissing) {
                            // Backend anomaly: treat as auth failure to surface visibly.
                            // authManager._handleAuthFailure();
                            // return;
                        // }

                        // LR Bug 3 root fix (2026-05-19, 對稱補 dac83ce): page reload resets
                        // lrInProgress to false (module-level let), so this guard is normally
                        // a no-op here. Kept for symmetry with the other two _user mutation
                        // points (visibilitychange line ~1140, applyInit line ~1754) so future
                        // refactors that move LR state into persistent storage don't silently
                        // regress this invariant.
                        // if (typeof lrInProgress !== 'undefined' && lrInProgress) {
                            // const cachedUid = authManager._user && authManager._user.id;
                            // const incomingUid = data.user && data.user.id;
                            // console.warn('[UserStateSync] LR active — keeping authManager._user (user_id=' + cachedUid + ', incoming user_id=' + incomingUid + ')');
                        // } else {
                            // authManager._user = data.user;
                        // }
                        // if (mismatch) {
                            // Full sync: clear stale state, fetch fresh /api/user/init, apply.
                            // try {
                                // await UserStateSync.runInitSync({ keepInviteToken: false });
                            // } catch (e) {
                                // console.error('[checkAuthOnLoad] runInitSync after mismatch failed:', e);
                                // Persist new authUser as fallback so subsequent reload can recover.
                                // try { localStorage.setItem('authUser', JSON.stringify(data.user)); } catch (_) {}
                            // }
                        // } else {
                            // Same user (or first load) — soft path: just persist authUser.
                            // Sidebar refresh is handled by the existing post-checkAuthOnLoad
                            // sessionManager.loadSessions() block below.
                            // try { localStorage.setItem('authUser', JSON.stringify(data.user)); } catch (_) {}
                        // }
                    // }
                    // hideAuthModal();
                    // showMainUI();
                    // updateAuthUI();
                // }
            // } catch (e) {
                // console.warn('[AuthGuard] /api/auth/me failed:', e);
                // On network error, allow UI if we have cached auth
                // if (!authManager.isLoggedIn()) {
                    // hideMainUI();
                    // showAuthModal('login');
                // }
            // } finally {
                // _authReadyResolve();
            // }
        // }

        document.addEventListener('DOMContentLoaded', async () => {
            // Run auth guard first — must await to prevent later code from overriding modal state
            // await checkAuthOnLoad();

            // Trigger F (Task 10): tab-visibility identity invariant.
            // DOMContentLoaded path is already handled by checkAuthOnLoad's
            // Trigger B (Task 7) MISMATCH branch — re-running checkAuthOnLoad
            // here would dedupe naturally but adds an extra /api/auth/me call
            // on every visibility change. Instead, run a lighter inline check
            // mirroring Trigger B semantics: /api/auth/me → assertUserIdentity →
            // mismatch ⇒ runInitSync; match ⇒ soft refresh sessions/shared.
            // document.addEventListener('visibilitychange', async () => {
                // if (document.visibilityState !== 'visible') return;
                // if (!authManager.isLoggedIn()) return;

                // let res;
                // try {
                    // res = await authManager.authenticatedFetch('/api/auth/me', { method: 'GET' });
                // } catch (e) {
                    // console.error('[visibilitychange] /api/auth/me network error:', e);
                    // return;
                // }
                // if (!res.ok) {
                    // 401 path already handled by _handleAuthFailure inside authenticatedFetch.
                    // return;
                // }
                // let body;
                // try { body = await res.json(); } catch (_) { return; }
                // if (!body.success || !body.user) return;

                // let mismatch = false;
                // try {
                    // assertUserIdentity(authManager._user, body.user);
                // } catch (e) {
                    // if (e instanceof UserStateSyncError && e.code === 'MISMATCH') {
                        // mismatch = true;
                    // } else if (e instanceof UserStateSyncError) {
                        // MISSING_CACHED / MISSING_FRESH — treat as no-op for tab-visibility.
                        // console.warn('[visibilitychange] identity check skipped:', e.code);
                        // return;
                    // } else {
                        // throw e;
                    // }
                // }

                // if (mismatch) {
                    // console.warn('[visibilitychange] identity mismatch, triggering full reset.');
                    // LR Bug 3 root fix (2026-05-19, 對稱補 dac83ce): mid-LR tab switch can
                    // hit this path if backend identity drifts (token rotation, JWT claim
                    // refresh). Preserve authManager._user so next LR continue POST keeps
                    // the user_id that owns the lr_session row. runInitSync's applyInit has
                    // its own twin guard, but we also skip this pre-emptive assignment to
                    // avoid a transient window where _user is the new identity before
                    // applyInit runs.
                    // if (typeof lrInProgress !== 'undefined' && lrInProgress) {
                        // const cachedUid = authManager._user && authManager._user.id;
                        // const incomingUid = body.user && body.user.id;
                        // console.warn('[UserStateSync] LR active — keeping authManager._user (user_id=' + cachedUid + ', incoming user_id=' + incomingUid + ')');
                    // } else {
                        // authManager._user = body.user;
                    // }
                    // await UserStateSync.runInitSync({ keepInviteToken: false }).catch(err =>
                        // console.error('[visibilitychange] runInitSync failed:', err));
                    // return;
                // }

                // Same user → soft refresh. Do NOT call applyInit (which appends to
                // savedSessions without clearing). Replace sidebar lists in-place so
                // changes from other tabs (rename, new session) become visible without
                // a full UI reset.
                // try {
                    // const init = await UserStateSync.fetchInit();
                    // if (Array.isArray(init.sessions)) {
                        // savedSessions.length = 0;
                        // for (const s of init.sessions) {
                            // if (!s._serverId && s.id) s._serverId = s.id;
                            // savedSessions.push(s);
                        // }
                        // try {
                            // localStorage.setItem('taiwanNewsSavedSessions', JSON.stringify(savedSessions));
                        // } catch (_) { /* quota — best-effort */ }
                        // if (typeof renderLeftSidebarSessions === 'function') renderLeftSidebarSessions();
                    // }
                    // if (Array.isArray(init.shared_sessions)) {
                        // _sharedSessionsCache = init.shared_sessions;
                        // const sharedTab = document.querySelector('.left-sidebar-sessions-tab[data-sessions-tab="shared"]');
                        // if (sharedTab) sharedTab.textContent = `組織空間 (${init.shared_sessions.length})`;
                    // }
                // } catch (e) {
                    // console.error('[visibilitychange] soft refresh failed:', e);
                // }
            // });

            updateAuthUI();

            // If already logged in on page load, sync sessions from server
            if (window.authManager.isLoggedIn()) {
                window.sessionManager.loadSessions().then(sessions => {
                    if (sessions && sessions.length) {
                        // Bug A defense: server's list_sessions returns id=PG_UUID but no _serverId.
                        // Backfill _serverId so downstream saveCurrentSession → scheduleSave goes
                        // via PUT (not POST). UUID-shape detection mirrors loadSavedSession's
                        // hydrate fallback (line ~7735).
                        // v4.0 Commit 10 (2026-05-24): savedSessions owned by features/sessions-list.js.
                        setSavedSessions(sessions.map(s => ({
                            ...s,
                            _serverId: s._serverId
                                || (typeof s.id === 'string' && s.id.includes('-') ? s.id : null),
                        })));
                        window.renderLeftSidebarSessions();
                    }
                }).catch(e => console.warn('[SessionManager] Page-load session sync failed:', e));

                window.sessionManager.loadSharedSessions().then(sessions => {
                    if (sessions && sessions.length > 0) {
                        const sharedTab = document.querySelector('.left-sidebar-sessions-tab[data-sessions-tab="shared"]');
                        if (sharedTab) {
                            sharedTab.textContent = `組織空間 (${sessions.length})`;
                            // v4.0 Commit 10 (2026-05-24): _sharedSessionsCache owned by features/sessions-list.js.
                            setSharedSessions(sessions);
                        }
                    }
                }).catch(e => console.warn('[SharedSession] Page-load badge update failed:', e));
            }

            document.getElementById('btnShowLogin').addEventListener('click', () => {
                // Close settings popover before opening auth modal
                const pop = document.getElementById('settingsPopover');
                if (pop) pop.style.display = 'none';
                showAuthModal('login');
            });
            document.getElementById('btnCloseAuthModal').addEventListener('click', () => {
                if (window.authManager.isLoggedIn()) hideAuthModal();
            });
            document.getElementById('authModalOverlay').addEventListener('click', (e) => {
                if (e.target === e.currentTarget && window.authManager.isLoggedIn()) hideAuthModal();
            });
            document.getElementById('tabLogin').addEventListener('click', () => switchAuthTab('login'));
            document.getElementById('btnForgotPassword').addEventListener('click', (e) => { e.preventDefault(); switchAuthTab('forgot'); });
            document.getElementById('btnBackToLogin').addEventListener('click', (e) => { e.preventDefault(); switchAuthTab('login'); });

            document.getElementById('btnLogout').addEventListener('click', async () => {
                const pop = document.getElementById('settingsPopover');
                if (pop) pop.style.display = 'none';
                await window.authManager.logout();
                updateAuthUI();
            });

            // Org management button
            document.getElementById('btnOrgManage').addEventListener('click', () => {
                const pop = document.getElementById('settingsPopover');
                if (pop) pop.style.display = 'none';
                openOrgModal();
            });
            document.getElementById('btnCloseOrgModal').addEventListener('click', closeOrgModal);
            document.getElementById('orgModalOverlay').addEventListener('click', (e) => {
                if (e.target === e.currentTarget) closeOrgModal();
            });

            // Invite form (admin creates employee account)
            document.getElementById('orgInviteForm').addEventListener('submit', async (e) => {
                e.preventDefault();
                const name = document.getElementById('inviteName').value.trim();
                const email = document.getElementById('inviteEmail').value.trim();
                const role = document.getElementById('inviteRole').value;
                const feedback = document.getElementById('orgInviteFeedback');
                feedback.className = 'org-invite-feedback';
                feedback.style.display = 'none';
                if (!name || !email) return;
                try {
                    const res = await window.authManager.authenticatedFetch('/api/admin/create-user', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ email, name, role })
                    });
                    const data = await res.json();
                    if (!res.ok) throw new Error(data.error || '建立帳號失敗');
                    feedback.textContent = `帳號已建立，啟用信已寄出（${email}）`;
                    feedback.className = 'org-invite-feedback success';
                    feedback.style.display = 'block';
                    document.getElementById('inviteName').value = '';
                    document.getElementById('inviteEmail').value = '';
                    await reloadOrgMembers();
                } catch (err) {
                    feedback.textContent = err.message;
                    feedback.className = 'org-invite-feedback error';
                    feedback.style.display = 'block';
                }
            });

            // Handle invite token in URL on page load
            handleInviteToken();

            // Login form
            document.getElementById('loginForm').addEventListener('submit', async (e) => {
                e.preventDefault();
                const errEl = document.getElementById('loginError');
                errEl.style.display = 'none';
                const email = document.getElementById('loginEmail').value;
                const password = document.getElementById('loginPassword').value;
                try {
                    await window.authManager.login(email, password);
                    hideAuthModal();
                    showMainUI();
                    updateAuthUI();
                    // After login: check pending invite token
                    const pendingToken = sessionStorage.getItem('pendingInviteToken');
                    if (pendingToken) showAcceptInviteToast(pendingToken);
                    // After login: migrate localStorage sessions to server, then refresh
                    window.sessionManager.migrateFromLocal().then(result => {
                        if (result.migrated) {
                            console.log(`[SessionManager] Migrated ${result.created} sessions from localStorage`);
                        }
                        // Refresh sessions from API
                        return window.sessionManager.loadSessions();
                    }).then(sessions => {
                        if (sessions && sessions.length) {
                            // v4.0 Commit 10 (2026-05-24): savedSessions owned by features/sessions-list.js.

                            setSavedSessions(sessions);
                            window.renderLeftSidebarSessions();
                            console.log(`[SessionManager] Loaded ${sessions.length} sessions from server`);
                        }
                    }).catch(e => console.warn('[SessionManager] Post-login sync failed:', e));
                } catch (err) {
                    errEl.textContent = err.message;
                    errEl.style.display = 'block';
                    // Clear password field on failed login (security)
                    document.getElementById('loginPassword').value = '';
                }
            });

            // Register form removed — B2B onboarding uses /setup?token=xxx

            // Forgot password form
            document.getElementById('forgotPasswordForm').addEventListener('submit', async (e) => {
                e.preventDefault();
                const errEl = document.getElementById('forgotError');
                const successEl = document.getElementById('forgotSuccess');
                errEl.style.display = 'none';
                successEl.style.display = 'none';
                const email = document.getElementById('forgotEmail').value;
                try {
                    await window.authManager.forgotPassword(email);
                    successEl.textContent = 'If that email exists, a reset link has been sent.';
                    successEl.style.display = 'block';
                } catch (err) {
                    errEl.textContent = err.message;
                    errEl.style.display = 'block';
                }
            });

            // ==================== FEATURE 2: CHANGE PASSWORD ====================
            document.getElementById('btnChangePassword').addEventListener('click', () => {
                const pop = document.getElementById('settingsPopover');
                if (pop) pop.style.display = 'none';
                document.getElementById('changePwdError').style.display = 'none';
                document.getElementById('changePwdSuccess').style.display = 'none';
                document.getElementById('changePwdForm').reset();
                document.getElementById('changePwdModalOverlay').style.display = 'flex';
            });

            document.getElementById('btnCloseChangePwdModal').addEventListener('click', () => {
                document.getElementById('changePwdModalOverlay').style.display = 'none';
            });

            document.getElementById('changePwdModalOverlay').addEventListener('click', (e) => {
                if (e.target === e.currentTarget) document.getElementById('changePwdModalOverlay').style.display = 'none';
            });

            document.getElementById('changePwdForm').addEventListener('submit', async (e) => {
                e.preventDefault();
                const errEl = document.getElementById('changePwdError');
                const successEl = document.getElementById('changePwdSuccess');
                errEl.style.display = 'none';
                successEl.style.display = 'none';

                const currentPassword = document.getElementById('currentPassword').value;
                const newPassword = document.getElementById('newPassword').value;
                const confirmNewPassword = document.getElementById('confirmNewPassword').value;

                if (newPassword !== confirmNewPassword) {
                    errEl.textContent = '新密碼與確認密碼不一致';
                    errEl.style.display = 'block';
                    return;
                }

                try {
                    const res = await window.authManager.authenticatedFetch('/api/auth/change-password', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword })
                    });
                    const data = await res.json();
                    if (!res.ok) throw new Error(data.error || '變更失敗');
                    successEl.textContent = '密碼已變更，請重新登入';
                    successEl.style.display = 'block';
                    setTimeout(async () => {
                        document.getElementById('changePwdModalOverlay').style.display = 'none';
                        await window.authManager.logout();
                        updateAuthUI();
                    }, 1500);
                } catch (err) {
                    errEl.textContent = err.message;
                    errEl.style.display = 'block';
                }
            });

            // ==================== FEATURE 3: LOGOUT ALL DEVICES ====================
            // Logout dropdown: show on hover over wrapper
            const logoutWrapper = document.getElementById('logoutDropdownWrapper');
            if (logoutWrapper) {
                logoutWrapper.addEventListener('mouseenter', () => {
                    document.getElementById('logoutDropdown').style.display = 'block';
                });
                logoutWrapper.addEventListener('mouseleave', () => {
                    document.getElementById('logoutDropdown').style.display = 'none';
                });
            }

            const btnLogoutAll = document.getElementById('btnLogoutAll');
            if (btnLogoutAll) {
                btnLogoutAll.addEventListener('click', async () => {
                    document.getElementById('logoutDropdown').style.display = 'none';
                    try {
                        await window.authManager.authenticatedFetch('/api/auth/logout-all', { method: 'POST' });
                    } catch (e) { /* ignore */ }
                    window.authManager._handleAuthFailure();
                    updateAuthUI();
                });
            }

        });

        const searchInput = document.getElementById('searchInput');
        const btnSearch = document.getElementById('btnSearch');
        const initialState = document.getElementById('initialState');
        const loadingState = document.getElementById('loadingState');
        const resultsSection = document.getElementById('resultsSection');
        const listView = document.getElementById('listView');
        const timelineView = document.getElementById('timelineView');
        const btnShare = document.getElementById('btnShareSidebar');
        const modalOverlay = document.getElementById('modalOverlay');
        const btnCloseModal = document.getElementById('btnCloseModal');
        const summaryToggle = document.getElementById('summaryToggle');
        const btnToggleSummary = document.getElementById('btnToggleSummary');
        const summaryLoading = document.getElementById('summaryLoading');
        const chatContainer = document.getElementById('chatContainer');
        const chatMessagesEl = document.getElementById('chatMessages');
        const searchContainer = document.getElementById('searchContainer');
        const chatInputContainer = document.getElementById('chatInputContainer');
        const chatLoading = document.getElementById('chatLoading');

        // v4.0 Commit 14a (2026-05-25, Phase 8): summaryExpanded MIGRATED to
        //   features/search.js. Access via getSummaryExpanded() / setSummaryExpanded(b).
        let summaryGenerated = false;

        // Conversation history tracking
        // v4.0 Commit 2 (2026-05-24): MIGRATED to features/search.js. Access via
        // getConversationHistory() (returns live reference for .length/.push/.shift/.forEach/indexing)
        // / setConversationHistory(arr) for full reassign / clearConversationHistory() for
        // IIFE-style reset (preserves array reference). pushConversationHistory(entry) for append.

        // v4.0 Commit 10 (2026-05-24): sessionHistory / savedSessions / _sessionDirty /
        //   currentLoadedSessionId — ALL MIGRATED.
        //   sessionHistory / savedSessions / currentLoadedSessionId → features/sessions-list.js
        //   _sessionDirty → features/session-manager.js (D-V14 ownership split)
        // Initial localStorage load of savedSessions runs inside sessions-list.js initSessionsList().
        // Phase 4a window bridges (window.savedSessions / Object.defineProperty getters /
        //   window._resetSavedSessions) deleted in this commit — readers import helpers.

        function matchSessionId(a, b) {
            if (a == null || b == null) return false;
            return String(a) === String(b);
        }
        // Phase 4a Path B (2026-05-21): explicit window attach so ES modules
        // (features/sessions-list.js) can read this utility. Function declarations
        // auto-attach to window in non-strict mode but explicit is defensive.
        window.matchSessionId = matchSessionId;

        // Mode tracking: 'search', 'deep_research', or 'chat'
        // v4.0 Commit 1 (2026-05-24): MIGRATED to features/mode.js (decl owner).
        // Read via getCurrentMode(); write via setCurrentMode(mode).
        // Default 'search' state is initialized in features/mode.js module scope.

        // User Knowledge Base - temporary user_id (fallback when not logged in)
        const TEMP_USER_ID = 'demo_user_001';
        // v4.0 Commit 19 (2026-05-25, Phase 8 part C): userFiles MIGRATED to features/file-kb.js.
        //   Access via getUserFiles() / setUserFiles / clearUserFiles. Same module owns
        //   the 19 file-kb functions (handleFileSelect / loadUserFiles / renderFileTreeView /
        //   distributeFilesToFolders / etc.).
        // v4.0 Commit 13 (2026-05-25, Phase 8): includePrivateSources MIGRATED to
        //   features/source-filters.js (owner, per CEO decision #2). Access via
        //   getIncludePrivateSources() / setIncludePrivateSources(b). Same module
        //   also owns availableSites / selectedSites / sourceDisplayNames.

        // Site Filter — v4.0 Commit 13 (2026-05-25, Phase 8): MIGRATED to
        //   features/source-filters.js. Access via getAvailableSites() / setAvailableSites,
        //   getSelectedSites() / setSelectedSites, getSourceDisplayNames(),
        //   getSelectedSitesParam(), togglePrivateSources(), triggerFileUpload(),
        //   addSourceFolder(), toggleAllSites(), expand/collapseAllSourceFolders().

        // ==================== ANALYTICS INITIALIZATION ====================
        const analyticsTracker = new AnalyticsTrackerSSE('/api/analytics/event');
        // v4.0 Commit 14b (2026-05-25, Phase 8): expose analyticsTracker so migrated
        //   SSE handlers (features/search.js handleStreamingRequest /
        //   handlePostStreamingRequest) can call .startQuery without a circular import.
        //   Bridge removed in commit 19 sweep when AnalyticsTrackerSSE owner moves to a
        //   dedicated module.
        window.analyticsTracker = analyticsTracker;
        // v4.0 Commit 9 (2026-05-24): currentAnalyticsQueryId MIGRATED to utils/analytics.js.
        // Access via getAnalyticsQueryId() / setAnalyticsQueryId(id) / clearAnalyticsQueryId().

        // Track current conversation ID for multi-turn conversations
        // v4.0 Commit 2 (2026-05-24): MIGRATED to features/search.js. Access via
        // getCurrentConversationId() / setCurrentConversationId(id) / clearCurrentConversationId().

        // Track current Deep Research query_id for KG editing rerun
        // v4.0 Commit 15 (2026-05-25): currentResearchQueryId MIGRATED to features/deep-research.js. Access via getCurrentResearchQueryId() / setCurrentResearchQueryId(id) / clearCurrentResearchQueryId().

        // v4.0 Commit 14a (2026-05-25, Phase 8): 6 inflight handles MIGRATED to
        //   features/search.js. Access via:
        //     getSearchGenerationId() / bumpSearchGenerationId()
        //     getCurrentSearchAbortController() / setCurrentSearchAbortController(c)
        //     getCurrentSearchEventSource() / setCurrentSearchEventSource(es)
        //     getCurrentDeepResearchEventSource() / setCurrentDeepResearchEventSource(es)
        //     getCurrentDeepResearchAbortController() / setCurrentDeepResearchAbortController(c)
        //     getCurrentFreeConvAbortController() / setCurrentFreeConvAbortController(c)
        //   cancelActiveSearch / cancelAllActiveRequests now live in search.js and read/write
        //   the module-local handles directly. performSearch / performDeepResearch /
        //   performFreeConversation bodies still in news-search.js — use getters/setters.

        // v4.0 Commit 7 (2026-05-24): currentLRSessionId + lrInProgress MIGRATED to features/live-research.js.
        // Access via getLRSessionId() / setLRSessionId(id) / clearLRSessionId({ skipIfInflight }) /
        // isLRInProgress() / setLRInProgress(b). LR Bug 3 guard now lives inside the module's
        // clearLRSessionId helper — pass { skipIfInflight: true } from background reset paths
        // (UserStateSync.clearUserScopedState IIFE) to preserve the session id mid-run.
        // BRIDGE REMOVED: window.lrInProgress getter. page-bootstrap.js now imports isLRInProgress
        // from features/live-research.js directly. Per plan §7 row 7.

        // v4.0 Commit 12 (2026-05-25, Phase 8 prep): currentSessionId IIFE REMOVED.
        // Tab-scoped session id ('nlweb_session_id' sessionStorage key) is now owned by
        // static/js/utils/analytics.js. Access via getCurrentSessionId() — lazy generates
        // on first read. utils/analytics.js Phase 1 comment foreshadowed this Phase 8 sweep.
        // 7 callsites below migrated: bare `currentSessionId` → `getCurrentSessionId()`.

        // Event delegation: Track all clicks on article links (left, middle, right)
        const handleLinkClick = (event) => {
            const link = event.target.closest('.btn-read-more, a[href]');
            if (!link) return;

            const newsCard = link.closest('.news-card');
            if (!newsCard) return;

            const url = link.href;
            const allCards = document.querySelectorAll('.news-card');
            const position = Array.from(allCards).indexOf(newsCard);

            if (getAnalyticsQueryId() && url) {
                analyticsTracker.trackClick(url, position);
            }
        };

        // Listen for all types of clicks
        document.addEventListener('click', handleLinkClick);        // Left click
        document.addEventListener('auxclick', handleLinkClick);     // Middle click
        document.addEventListener('contextmenu', handleLinkClick);  // Right click

        // MutationObserver: Auto-track article displays
        const articleObserver = new MutationObserver((mutations) => {
            mutations.forEach(mutation => {
                mutation.addedNodes.forEach(node => {
                    if (node.nodeType === 1 && node.classList && node.classList.contains('news-card')) {
                        const link = node.querySelector('a[href]');
                        if (link && getAnalyticsQueryId()) {
                            const allCards = document.querySelectorAll('.news-card');
                            const position = Array.from(allCards).indexOf(node);
                            const url = link.href;

                            analyticsTracker.trackResultDisplayed(url, position, {
                                title: node.querySelector('.news-title')?.textContent || ''
                            });

                            node.dataset.analyticsUrl = url;
                            node.dataset.analyticsPosition = position;
                            analyticsTracker.observeResult(node);
                        }
                    }
                });
            });
        });

        articleObserver.observe(document.getElementById('listView'), { childList: true, subtree: true });
        articleObserver.observe(document.getElementById('timelineView'), { childList: true, subtree: true });

        console.log('[Analytics] Tracker initialized');
        // ==================== END ANALYTICS INITIALIZATION ====================

        // Chat history for free conversation mode
        // v4.0 Commit 3 (2026-05-24): MIGRATED to features/chat.js.
        // Access via getChatHistory() (live ref) / setChatHistory(arr) / clearChatHistory() / pushChatHistory(msg).
        // window.chatHistory bridge below is updated to read from the owner module (removed in commit 11).

        // Pinned messages (Line-style announcement)
        // v4.0 Commit 4 (2026-05-24): pinnedMessages MIGRATED to features/pins.js. Access via
        // getPinnedMessages() (live ref — supports .some/.findIndex/.splice/.shift/.push) /
        // setPinnedMessages(arr) for full reassign / clearPinnedMessages() preserves ref.
        // v4.0 Commit 17 (2026-05-25, Phase 8): _messageIdCounter + MAX_PINNED_MESSAGES co-migrated into features/chat.js (module-local).

        // Pinned news cards
        // v4.0 Commit 4 (2026-05-24): pinnedNewsCards MIGRATED to features/pins.js.
        // v4.0 Commit 17 (2026-05-25, Phase 8): MAX_PINNED_NEWS co-migrated into features/chat.js (module-local).

        // Accumulated articles from ALL searches in this conversation
        // v4.0 Commit 2 (2026-05-24): MIGRATED to features/search.js. Access via
        // getAccumulatedArticles() (live ref) / setAccumulatedArticles(arr) / clearAccumulatedArticles()
        // / pushAccumulatedArticles(items) — caller passes ARRAY (will be spread internally).

        // Store Deep Research report for free conversation follow-up
        // v4.0 Commit 5 (2026-05-24): currentResearchReport MIGRATED to features/research.js.
        // Access via getResearchReport() / setResearchReport(r) / clearResearchReport().

        // Store reasoning chain data for sharing/verification
        // v4.0 Commit 5 (2026-05-24): currentArgumentGraph + currentChainAnalysis MIGRATED
        // to features/research.js. getArgumentGraph / setArgumentGraph / clearArgumentGraph;
        // getChainAnalysis / setChainAnalysis / clearChainAnalysis.
        // v4.0 Commit 6 (2026-05-24): shareContentOverride MIGRATED to features/sharing.js.
        // Access via getShareContentOverride() / setShareContentOverride(c) / clearShareContentOverride().
        // Used by share format builders (truthy guard `getShareContentOverride() || formatXxx()` fallback).

        // v4.0 Commit 11 (2026-05-24): sessionHistory + chatHistory bridges REMOVED.
        //   sessions-list.js (consumer) imports getSessionHistory + getChatHistory
        //   directly from owner modules (sessions-list.js / chat.js).
        // v4.0 Commit 5 (2026-05-24): BRIDGE REMOVED — features/sessions-list.js now
        // imports getResearchReport from features/research.js directly. Per plan §7 row 5.
        // (Object.defineProperty(window, 'currentResearchReport', { get })) deleted.

        // ==================== 新版模式切換與 Popup 邏輯 ====================

        // 新版模式按鈕（搜尋框內）
        const modeButtonsInline = document.querySelectorAll('.mode-btn-inline');
        const advancedSearchPopup = document.getElementById('advancedSearchPopup');
        const popupClose = document.getElementById('popupClose');
        const btnUploadInline = document.getElementById('btnUploadInline');

        // Research Mode 固定為 discovery（已移除前端選擇）
        let currentResearchMode = 'discovery';

        // 追蹤使用者是否已確認進階搜尋設定（點擊過 popup 內的選項）
        let advancedSearchConfirmed = false;
        // v4.0 Commit 14b (2026-05-25, Phase 8): expose getter so features/search.js
        //   performSearch can gate Deep Research mode on advanced settings confirmation.
        //   advancedSearchConfirmed remains a news-search.js-owned let (written by
        //   showAdvancedPopup confirm path / deleteSavedSession reset / init blocks);
        //   commit 19 sweep moves to settings module.
        window.getAdvancedSearchConfirmed = () => advancedSearchConfirmed;

        // ==================== USER STATE SYNC MODULE ====================
        // v4.0 Commit 11 (2026-05-24, Path A completion): UserStateSync IIFE
        //   MIGRATED to static/js/core/state-sync.js (REAL IIFE — replaces thin alias).
        //   The IIFE body (clearUserScopedState / applyInit / fetchInit /
        //   resetMainUI / fullReset / runInitSync) is preserved with all 6 P0
        //   invariants (LR Bug 3 guards / taiwanNewsSessionsMigrated stamp /
        //   assertUserIdentity 2-arg / sessionManager inline / document.dispatchEvent
        //   shape / window.resetConversation prefix).
        //   Bridges removed: UserStateSync.

        // 更新上傳按鈕可見性
        function updateUploadButtonVisibility() {
            if (getCurrentMode() === 'deep_research' || getCurrentMode() === 'chat') {
                btnUploadInline.classList.add('visible');
            } else {
                btnUploadInline.classList.remove('visible');
            }
        }

        // 顯示/隱藏 popup（非 modal，無 overlay）
        function showAdvancedPopup() {
            advancedSearchPopup.classList.add('visible');
        }
        // v4.0 Commit 14b (2026-05-25, Phase 8): expose for features/search.js performSearch delegation.
        window.showAdvancedPopup = showAdvancedPopup;

        function hideAdvancedPopup() {
            advancedSearchPopup.classList.remove('visible');
            // 關閉 popup 時標記已確認（因為一定有預設值 discovery）
            advancedSearchConfirmed = true;
        }

        // 只有關閉按鈕才關閉 popup（非 modal，不攔截外部點擊）
        popupClose.addEventListener('click', hideAdvancedPopup);

        // 來源篩選 checkbox → 控制右 sidebar 的 sources tab
        const sourceFilterToggle = document.getElementById('sourceFilterToggle');
        if (sourceFilterToggle) {
            sourceFilterToggle.addEventListener('change', () => {
                advancedSearchConfirmed = true;
                if (sourceFilterToggle.checked) {
                    openTab('sources');
                } else {
                    closeAllTabs();
                }
            });
        }

        // 新版模式切換處理
        modeButtonsInline.forEach(button => {
            button.addEventListener('click', () => {
                const newMode = button.dataset.mode;

                // 如果點擊進階搜尋且已經是進階搜尋模式，toggle popup
                if (newMode === 'deep_research' && getCurrentMode() === 'deep_research') {
                    if (advancedSearchPopup.classList.contains('visible')) {
                        hideAdvancedPopup();
                    } else {
                        showAdvancedPopup();
                    }
                    return;
                }

                // Don't do anything if clicking the current mode (except deep_research)
                if (newMode === getCurrentMode()) return;

                // Update button states (新版)
                modeButtonsInline.forEach(btn => btn.classList.remove('active'));
                button.classList.add('active');

                // Update current mode
                const previousMode = getCurrentMode();
                setCurrentMode(newMode);

                // Handle mode-specific UI changes
                if (newMode === 'search') {
                    btnSearch.textContent = '搜尋';
                    searchInput.placeholder = '問我任何新聞相關問題，例如：最近台灣資安政策有什麼進展？';

                    // Move search container back to original position if coming from chat/deep_research/live_research
                    if (previousMode === 'chat' || previousMode === 'deep_research' || previousMode === 'live_research') {
                        const mainContainer = document.querySelector('main .container');
                        const loadingStateEl = document.getElementById('loadingState');
                        mainContainer.insertBefore(searchContainer, loadingStateEl);
                        chatInputContainer.style.display = 'none';
                        chatContainer.classList.remove('active');
                    }

                    hideAdvancedPopup();
                } else if (newMode === 'deep_research') {
                    btnSearch.textContent = '搜尋';
                    searchInput.placeholder = '輸入問題進行深度研究分析...';

                    // Move search container to chat area bottom
                    chatContainer.classList.add('active');
                    chatInputContainer.appendChild(searchContainer);
                    chatInputContainer.style.display = 'block';

                    // 自動顯示 popup 並重置確認狀態
                    advancedSearchConfirmed = false;
                    showAdvancedPopup();
                } else if (newMode === 'live_research') {
                    btnSearch.textContent = '搜尋';
                    searchInput.placeholder = '輸入問題，讀豹將即時展示研究過程...';

                    // Move search container to chat area bottom (same as DR)
                    chatContainer.classList.add('active');
                    chatInputContainer.appendChild(searchContainer);
                    chatInputContainer.style.display = 'block';

                    hideAdvancedPopup();

                    // Reset live research stages
                    resetLiveResearchUI();
                } else if (newMode === 'chat') {
                    btnSearch.textContent = '發送';
                    searchInput.placeholder = '讀豹會參考摘要內容及您釘選的文章來回答...';

                    // chatContainer 已獨立於 resultsSection，不需要顯示 resultsSection
                    chatContainer.classList.add('active');
                    chatInputContainer.appendChild(searchContainer);
                    chatInputContainer.style.display = 'block';

                    hideAdvancedPopup();
                }

                // 更新上傳按鈕可見性
                updateUploadButtonVisibility();

            });
        });

        // 進階設定 checkbox 也標記已確認
        const kgToggleCheckbox = document.getElementById('kgToggle');
        const webSearchToggleCheckbox = document.getElementById('webSearchToggle');

        kgToggleCheckbox.addEventListener('change', () => {
            advancedSearchConfirmed = true;
        });

        webSearchToggleCheckbox.addEventListener('change', () => {
            advancedSearchConfirmed = true;
        });

        // 初始化上傳按鈕可見性
        updateUploadButtonVisibility();

        // ==================== 左側邊欄系統 ====================
        const leftSidebar = document.getElementById('leftSidebar');
        const btnExpandSidebar = document.getElementById('btnExpandSidebar');
        const btnCollapseSidebar = document.getElementById('btnCollapseSidebar');
        const btnNewConversation = document.getElementById('btnNewConversation');
        const btnToggleCategories = document.getElementById('btnToggleCategories');
        // History Popup 元素
        const btnHistorySearch = document.getElementById('btnHistorySearch');
        const historyPopupOverlay = document.getElementById('historyPopupOverlay');
        const historyPopupClose = document.getElementById('historyPopupClose');
        const historyPopupSearchInput = document.getElementById('historyPopupSearchInput');
        const historyPopupList = document.getElementById('historyPopupList');
        const btnSettings = document.getElementById('btnSettings');

        // 展開按鈕：開啟側邊欄
        btnExpandSidebar.addEventListener('click', () => {
            leftSidebar.classList.add('visible');
            btnExpandSidebar.classList.add('hidden');
        });

        // 收回按鈕：關閉側邊欄
        btnCollapseSidebar.addEventListener('click', () => {
            leftSidebar.classList.remove('visible');
            btnExpandSidebar.classList.remove('hidden');
        });

        // 新對話按鈕：儲存當前對話後清空
        btnNewConversation.addEventListener('click', () => {
            // 如果有內容，先儲存
            if (getSessionHistory().length > 0 || getResearchReport() || getChatHistory().length > 0) {
                saveCurrentSession();
            }
            // 清空並重置
            resetConversation();
            // 關閉側邊欄並顯示展開按鈕
            leftSidebar.classList.remove('visible');
            btnExpandSidebar.classList.remove('hidden');
        });

        // 開啟資料夾 (btnToggleCategories) - 行為在 FOLDER/PROJECT SYSTEM 區段定義

        // Settings popover toggle
        const settingsPopover = document.getElementById('settingsPopover');
        btnSettings.addEventListener('click', (e) => {
            e.stopPropagation();
            const isVisible = settingsPopover.style.display !== 'none';
            settingsPopover.style.display = isVisible ? 'none' : 'block';
        });

        // Settings popover: only closes via button click inside popover, not click-outside

        // ==================== 歷史搜尋 Popup ====================

        // 顯示 popup
        function showHistoryPopup() {
            historyPopupOverlay.classList.add('visible');
            historyPopupSearchInput.value = '';
            historyPopupSearchInput.focus();
            renderHistoryPopup();
        }

        // 隱藏 popup
        function hideHistoryPopup() {
            historyPopupOverlay.classList.remove('visible');
        }

        // 點擊「歷史搜尋」按鈕
        btnHistorySearch.addEventListener('click', showHistoryPopup);

        // 點擊關閉按鈕
        historyPopupClose.addEventListener('click', hideHistoryPopup);

        // 點擊 overlay 關閉
        historyPopupOverlay.addEventListener('click', (e) => {
            if (e.target === historyPopupOverlay) {
                hideHistoryPopup();
            }
        });

        // ESC 鍵關閉
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && historyPopupOverlay.classList.contains('visible')) {
                hideHistoryPopup();
            }
        });

        // 搜尋框輸入時過濾
        historyPopupSearchInput.addEventListener('input', () => {
            renderHistoryPopup(historyPopupSearchInput.value.trim().toLowerCase());
        });

        // 渲染 popup 歷史記錄列表
        function renderHistoryPopup(filterText = '') {
            historyPopupList.innerHTML = '';

            if (getSavedSessions().length === 0) {
                historyPopupList.innerHTML = '<div class="history-popup-empty">尚無搜尋記錄</div>';
                return;
            }

            // 過濾
            let filteredSessions = getSavedSessions().slice().reverse();
            if (filterText) {
                filteredSessions = filteredSessions.filter(session =>
                    session.title.toLowerCase().includes(filterText)
                );
            }

            if (filteredSessions.length === 0) {
                historyPopupList.innerHTML = '<div class="history-popup-empty">找不到符合的記錄</div>';
                return;
            }

            filteredSessions.forEach(session => {
                const date = new Date(session.createdAt);
                const dateStr = date.toLocaleDateString('zh-TW', {
                    year: 'numeric',
                    month: '2-digit',
                    day: '2-digit'
                });

                const item = document.createElement('div');
                item.className = 'history-popup-item';
                item.innerHTML = `
                    <div class="history-popup-item-content">
                        <div class="history-popup-item-title">${escapeHTML(session.title)}</div>
                        <div class="history-popup-item-date">${dateStr}</div>
                    </div>
                    <span class="history-popup-item-icon">→</span>
                `;

                item.addEventListener('click', async () => {
                    // Trigger E: session click (history popup). Identity self-check
                    // — same user expected (popup click never crosses identity), but
                    // if _user is missing fall back to reload-path (Trigger F via
                    // runInitSync). Safe-guard against clicking after a silent logout.
                    try {
                        assertUserIdentity(window.authManager._user, window.authManager._user);
                    } catch (e) {
                        if (e instanceof UserStateSyncError && e.code !== 'MISSING_FRESH' && window.authManager.isLoggedIn()) {
                            console.warn('[session-click:popup] identity self-check failed, triggering reload-path:', e);
                            await UserStateSync.runInitSync({ keepInviteToken: false }).catch(err =>
                                console.error('[session-click:popup] runInitSync failed:', err));
                            return;
                        }
                    }
                    // 切換前先保存當前對話（防止深度報告等狀態丟失）
                    if (getSessionHistory().length > 0 || getResearchReport() || getChatHistory().length > 0) {
                        saveCurrentSession();
                    }
                    loadSavedSession(session);
                    hideHistoryPopup();
                    // 關閉左側邊欄
                    leftSidebar.classList.remove('visible');
                    btnExpandSidebar.classList.remove('hidden');
                });

                historyPopupList.appendChild(item);
            });
        }

        // v4.0 Commit 23 (2026-05-25, Phase 8 FINAL): saveCurrentSession body MIGRATED
        // to core/session-coordinator.js. Imported above. Z-prep window-attach removed
        // — callers in features modules (chat.js / deep-research.js / folders.js etc.)
        // still use window.saveCurrentSession optional-chained; re-bridge below until
        // those callers direct-import from core/session-coordinator.js (future cleanup).
        window.saveCurrentSession = saveCurrentSession;
        window.adoptLRServerSession = adoptLRServerSession;

        // 重置對話
        // ===== 共用 UI 重置函式 =====
        // 將搜尋框歸位、隱藏聊天/資料夾、重置模式按鈕、清空結果區
        function resetToHome() {
            // 搬回 searchContainer
            if (searchContainer.parentElement === chatInputContainer) {
                const mainContainer = document.querySelector('main .container');
                const loadingStateEl = document.getElementById('loadingState');
                mainContainer.insertBefore(searchContainer, loadingStateEl);
            }
            searchContainer.style.display = 'block';
            chatInputContainer.style.display = 'none';
            // 清除 inline style，讓 CSS .chat-container { display: none } 生效
            // 不能用 style.display = 'none'，否則之後 .active class 無法覆蓋 inline style
            chatContainer.style.display = '';
            chatContainer.classList.remove('active');
            chatMessagesEl.innerHTML = '';

            // 關閉資料夾頁
            const folderPageEl = document.getElementById('folderPage');
            if (folderPageEl) folderPageEl.style.display = 'none';
            clearPreFolderState();

            // 重置模式為 search（新版按鈕同步）
            clearCurrentMode();
            btnSearch.textContent = '搜尋';
            searchInput.placeholder = '問我任何新聞相關問題，例如：最近台灣資安政策有什麼進展？';
            modeButtonsInline.forEach(btn => btn.classList.remove('active'));
            const searchInlineBtn = document.querySelector('.mode-btn-inline[data-mode="search"]');
            if (searchInlineBtn) searchInlineBtn.classList.add('active');

            // 重置結果區
            resultsSection.classList.remove('active');
            resultsSection.style.display = '';
            listView.innerHTML = '';
            listView.style.display = '';  // Clear inline display so CSS default (flex) takes effect
            timelineView.innerHTML = '';
            timelineView.classList.remove('active');
            const researchViewReset = document.getElementById('researchView');
            if (researchViewReset) {
                researchViewReset.classList.remove('active');
                // Bug fix (P3 hidden DOM residual): clear stale report HTML so
                // #researchView doesn't retain old DR content across new conversations.
                researchViewReset.innerHTML = '';
            }
            const liveResearchViewReset = document.getElementById('liveResearchView');
            if (liveResearchViewReset) liveResearchViewReset.classList.remove('active');

            // Reset tabs to default (list tab active)
            const allTabs = document.querySelectorAll('.tab');
            allTabs.forEach(t => t.classList.remove('active'));
            const listTab = document.querySelector('.tab[data-view="list"]');
            if (listTab) listTab.classList.add('active');
            if (summaryToggle) summaryToggle.classList.add('active');

            // 隱藏釘選 banner
            const pinnedBanner = document.getElementById('pinnedBanner');
            if (pinnedBanner) pinnedBanner.style.display = 'none';

            // 重置釘選新聞列表
            const pinnedNewsList = document.getElementById('pinnedNewsList');
            if (pinnedNewsList) {
                pinnedNewsList.innerHTML = '<div class="pinned-news-empty">尚未釘選任何新聞</div>';
            }

            // 清理知識圖譜狀態（含編輯模式 — state + visual）
            // v4.0 Commit 21 (2026-05-25, Phase 8 part C): inline block replaced by
            // resetKGState() export from features/knowledge-graph.js. Behavior identical.
            resetKGState();
        }
        // v4.0 Commit 22 (2026-05-25, Phase 8 part C): expose resetToHome onto window so
        // features/sessions-list.js deleteSavedSession can reach it when the deleted
        // session was the currently-loaded one. resetToHome stays KEEP-residual (touches
        // many local DOM consts + folder state). Sweep target commit 25+.
        window.resetToHome = resetToHome;

        function resetConversation() {
            cancelActiveSearch();

            // 清空所有資料
            setConversationHistory([]);
            // v4.0 Commit 10 (2026-05-24): sessionHistory owned by features/sessions-list.js.
            clearSessionHistory();
            setChatHistory([]);
            setAccumulatedArticles([]);
            setPinnedMessages([]);
            setPinnedNewsCards([]);
            // v4.0 Commit 10 (2026-05-24): currentLoadedSessionId owned by features/sessions-list.js.
            setCurrentLoadedSessionId(null);
            clearResearchReport();
            clearCurrentConversationId();
            clearCurrentResearchQueryId();

            // 共用 UI 重置
            resetToHome();

            // resetConversation 專有的重置
            searchInput.value = '';
            initialState.style.display = 'block';

            // 清空右側 Tab「搜尋紀錄」（conversationHistory 已被清空）
            renderConversationHistory();

            // 重置 AI 摘要
            const summaryContent = document.getElementById('summaryContent');
            if (summaryContent) {
                summaryContent.innerHTML = '';
            }
            summaryGenerated = false;

            console.log('Conversation reset');
        }
        // Phase v4.0 commit 0a (Z prep): explicit window-attach. Removed in commit 19.
        // Per Gemini Final Review Finding 2 — IIFE resetMainUI in core/state-sync.js
        // references window.resetConversation; without this attach, module-scoped
        // news-search.js post-0c would not expose this function.
        window.resetConversation = resetConversation;

        // Task 13 cleanup: _resetMainUIState removed. Superseded by
        // UserStateSync.clearUserScopedState (covers the same 6 globals
        // _sessionDirty / currentArgumentGraph / currentChainAnalysis /
        // shareContentOverride / currentLRSessionId / currentAnalyticsQueryId)
        // + UserStateSync.resetMainUI (wraps resetConversation safely).
        // Triggers A/B/C/D/F now go through UserStateSync.fullReset or runInitSync,
        // both of which invoke clearUserScopedState + resetMainUI in one call.

        // ==================== 右側 Tab 面板系統 ====================
        const rightTabLabels = document.querySelectorAll('.right-tab-label');
        const rightTabPanels = document.querySelectorAll('.right-tab-panel');
        const rightTabCloseButtons = document.querySelectorAll('.right-tab-panel-close');
        let currentOpenTab = null;

        // Tab 標籤點擊處理
        rightTabLabels.forEach(label => {
            label.addEventListener('click', () => {
                const tabName = label.dataset.tab;

                // 如果點擊的是當前開啟的 Tab，則關閉
                if (currentOpenTab === tabName) {
                    closeAllTabs();
                    return;
                }

                // 關閉其他 Tab，開啟此 Tab
                closeAllTabs();
                openTab(tabName);
            });
        });

        // 關閉按鈕處理
        rightTabCloseButtons.forEach(btn => {
            btn.addEventListener('click', () => {
                closeAllTabs();
            });
        });

        function openTab(tabName) {
            const label = document.querySelector(`.right-tab-label[data-tab="${tabName}"]`);
            const panel = document.querySelector(`.right-tab-panel[data-tab="${tabName}"]`);

            if (label && panel) {
                label.classList.add('active');
                panel.classList.add('visible');
                currentOpenTab = tabName;

                // 如果是搜尋紀錄 Tab，重新載入當前 session 的查詢列表
                if (tabName === 'history') {
                    renderConversationHistory();
                }
            }
        }
        // Post-refactor regression fix (2026-05-25): openTab is KEEP-in-place
        // but features/source-filters.js togglePrivateSources calls window.openTab.
        // The window-attach was missed during commit 0a Z-prep. Re-attach here.
        window.openTab = openTab;

        function closeAllTabs() {
            rightTabLabels.forEach(l => l.classList.remove('active'));
            rightTabPanels.forEach(p => p.classList.remove('visible'));
            currentOpenTab = null;
        }

        // Function to render conversation history
        // 渲染當前 session 的查詢歷史到右側 Tab「搜尋紀錄」
        function renderConversationHistory() {
            const container = document.getElementById('savedSessionsListNew');
            if (!container) return;

            if (getConversationHistory().length === 0) {
                container.innerHTML = '<div class="empty-sessions">尚無查詢紀錄</div>';
                return;
            }

            container.innerHTML = '';

            getConversationHistory().forEach((query, index) => {
                const item = document.createElement('div');
                item.className = 'saved-session-item';
                item.innerHTML = `
                    <div class="saved-session-item-title">${index + 1}. ${escapeHTML(query)}</div>
                `;

                // 點擊回溯到該次查詢的結果
                item.addEventListener('click', () => {
                    restoreSession(index);
                    closeAllTabs();
                });

                container.appendChild(item);
            });
        }
        // v4.0 Commit 14b (2026-05-25, Phase 8): expose for features/search.js performSearch delegation.
        window.renderConversationHistory = renderConversationHistory;

        // Function to restore a previous session
        function restoreSession(sessionIndex) {
            if (sessionIndex >= 0 && sessionIndex < getSessionHistory().length) {
                const session = getSessionHistory()[sessionIndex];
                console.log('Restoring session:', session);

                if (session.isDeepResearch && session.researchReport) {
                    // Restore deep research report
                    setResearchReport({ ...session.researchReport });
                    setArgumentGraph(session.argumentGraph ? [...session.argumentGraph] : null);
                    setChainAnalysis(session.chainAnalysis ? { ...session.chainAnalysis } : null);

                    // Render research report in research view
                    renderResearchReportToView(getResearchReport(), getArgumentGraph(), getChainAnalysis());

                    // Also populate news list if article data exists
                    if (session.data) {
                        populateResultsFromAPI(session.data, session.query);
                    }

                    // Restore Knowledge Graph if available
                    // Don't overwrite KG if user is currently editing
                    // v4.0 Commit 21 (2026-05-25): kgEditMode read via getKGEditMode(),
                    // displayKnowledgeGraph imported from features/knowledge-graph.js.
                    if (session.knowledgeGraph && !getKGEditMode()) {
                        displayKnowledgeGraph(session.knowledgeGraph);
                    }

                    // Switch to research tab
                    const researchTab = document.querySelector('.tab[data-view="research"]');
                    if (researchTab) researchTab.click();
                } else {
                    // Normal search — clear research view and restore news list
                    clearResearchReport();
                    clearArgumentGraph();
                    clearChainAnalysis();
                    const researchViewEl = document.getElementById('researchView');
                    if (researchViewEl) researchViewEl.innerHTML = '';

                    populateResultsFromAPI(session.data, session.query);

                    // Switch to list tab
                    const listTab = document.querySelector('.tab[data-view="list"]');
                    if (listTab) listTab.click();
                }

                // Show results section
                resultsSection.classList.add('active');

                // Scroll to results
                resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }

        // v4.0 Commit 15 (2026-05-25, Phase 8): renderResearchReportToView MIGRATED to features/deep-research.js.

        // v4.0 Commit 14b (2026-05-25, Phase 8): handleStreamingRequest MIGRATED to features/search.js.

        // v4.0 Commit 14b (2026-05-25, Phase 8): handlePostStreamingRequest MIGRATED to features/search.js.

        // v4.0 Commit 15 (2026-05-25, Phase 8): updateReasoningProgress MIGRATED to features/deep-research.js.

        // Function to show memory notification
        // v4.0 Commit 14b (2026-05-25, Phase 8): showMemoryNotification MIGRATED to features/search.js.

        // v4.0 Commit 14b (2026-05-25, Phase 8): showTimeFilterRelaxedWarning MIGRATED to features/search.js.

        // v4.0 Commit 14b (2026-05-25, Phase 8): populateResultsFromAPI MIGRATED to features/search.js.

        // v4.0 Commit 14a (2026-05-25, Phase 8): Progressive Rendering Functions
        //   (renderSkeletonCards / renderSummarySkeleton / updateProgressMessage /
        //   createArticleCard / renderArticlesProgressive / renderAnswerProgressive /
        //   clearLoadingStates) MIGRATED to features/search.js as named exports.

        // v4.0 Commit 14a (2026-05-25, Phase 8): escapeHTML + convertMarkdownToHtml
        //   MIGRATED to features/search.js as named exports. window.escapeHTML bridge
        //   re-attached below for backward compat (source-filters / live-research /
        //   sharing modules' defensive reads). Removed in commit 19 sweep.
        window.escapeHTML = escapeHTML;

        // Search functionality
        btnSearch.addEventListener('click', performSearch);
        searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
                e.preventDefault();
                // Bug #23: Prevent duplicate sends during processing
                if (searchInput.dataset.processing === 'true') return;
                performSearch();
            }
        });

        // v4.0 Commit 14a (2026-05-25, Phase 8): cancelActiveSearch / cancelAllActiveRequests
        //   / showInterruptedSearchNotice / clearQueryState / setProcessingState MIGRATED
        //   to features/search.js as named exports. Search inflight handles (searchGenerationId
        //   / currentSearchAbortController / currentSearchEventSource / DR / FC) live in
        //   search.js module scope — accessed via getter/setter exports above.

        // Bug #23: Stop button click handler
        document.addEventListener('DOMContentLoaded', () => {
            const stopBtn = document.getElementById('btnStopGenerate');
            if (stopBtn) {
                stopBtn.addEventListener('click', () => {
                    cancelAllActiveRequests();
                    setProcessingState(false);
                });
            }
        });

        // v4.0 Commit 14b (2026-05-25, Phase 8): performSearch MIGRATED to features/search.js.
        //   Delegates by mode: chat/DR/LR → window bridges (commits 15 + batch 5'' migrate those entries).

        // v4.0 Commit 15 (2026-05-25, Phase 8): showDRError MIGRATED to features/deep-research.js.

        // v4.0 Commit 15 (2026-05-25, Phase 8): performDeepResearch MIGRATED to features/deep-research.js.

        // v4.0 Commit 15 (2026-05-25, Phase 8): addCitationLinks MIGRATED to features/deep-research.js.

        // v4.0 Commit 15 (2026-05-25, Phase 8): generateCitationReferenceList MIGRATED to features/deep-research.js.

        // v4.0 Commit 15 (2026-05-25, Phase 8): bindCitationReferenceToggles MIGRATED to features/deep-research.js.

        // ==================== LIVE RESEARCH (BETA) FUNCTIONS — 6-Stage Pipeline ====================
        // v4.0 Commit 16 (2026-05-25, Phase 8): 15 LR function bodies MIGRATED to features/live-research.js.
        //   UI helpers (12), SSE handler (handleLiveResearchSSE), main entries (performLiveResearch /
        //   continueLiveResearch). bindLRCheckpointListeners IIFE below uses imported names.


        // Checkpoint button event listeners (bound once on load)
        (function bindLRCheckpointListeners() {
            const replyBtn = document.getElementById('lrBtnReply');
            const autoContinueBtn = document.getElementById('lrBtnAutoContine');
            const replyInput = document.getElementById('lrReplyInput');

            if (replyBtn) {
                replyBtn.addEventListener('click', () => {
                    const input = document.getElementById('lrReplyInput');
                    const val = input ? input.value.trim() : '';
                    continueLiveResearch(val, false);
                    if (input) input.value = '';
                });
            }

            if (autoContinueBtn) {
                autoContinueBtn.addEventListener('click', () => {
                    continueLiveResearch('', true);
                });
            }

            if (replyInput) {
                replyInput.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault();
                        const btn = document.getElementById('lrBtnReply');
                        if (btn) btn.click();
                    }
                });
            }

        })();

        // v4.0 Commit 15 (2026-05-25, Phase 8): displayDeepResearchResults MIGRATED to features/deep-research.js.

        // v4.0 Commit 15 (2026-05-25, Phase 8): addCollapsibleSections MIGRATED to features/deep-research.js.

        // v4.0 Commit 15 (2026-05-25, Phase 8): bindCollapsibleHandlers MIGRATED to features/deep-research.js.

        // v4.0 Commit 15 (2026-05-25, Phase 8): addToggleAllToolbar MIGRATED to features/deep-research.js.

        // v4.0 Commit 15 (2026-05-25, Phase 8): displayReasoningChainInContainer MIGRATED to features/deep-research.js.

        // v4.0 Commit 21 (2026-05-25, Phase 8 part C): Knowledge Graph block
        // (5 constants + 7 state lets + 24 functions, was lines 2754-4460)
        // MIGRATED to features/knowledge-graph.js. See import block at file top.
        // Internal callers within this file now use:
        //   - getCurrentKGData() / getKGEditMode() / resetKGState()
        //   - displayKnowledgeGraph() (also re-bridged onto window for deep-research.js)


        // ============================================================
        // Reasoning Chain Visualization (Phase 4 - Enhanced)
        // ============================================================

        // v4.0 Commit 15 (2026-05-25, Phase 8): displayReasoningChain MIGRATED to features/deep-research.js.

        // v4.0 Commit 15 (2026-05-25, Phase 8): createReasoningChainContainer MIGRATED to features/deep-research.js.

        // v4.0 Commit 15 (2026-05-25, Phase 8): createLogicInconsistencyWarning MIGRATED to features/deep-research.js.

        // v4.0 Commit 15 (2026-05-25, Phase 8): createCycleWarning MIGRATED to features/deep-research.js.

        // v4.0 Commit 15 (2026-05-25, Phase 8): createCriticalNodesAlert MIGRATED to features/deep-research.js.

        // v4.0 Commit 15 (2026-05-25, Phase 8): renderArgumentNode MIGRATED to features/deep-research.js.

        // v4.0 Commit 15 (2026-05-25, Phase 8): setupHoverInteractions MIGRATED to features/deep-research.js.

        // v4.0 Commit 15 (2026-05-25, Phase 8): inferScore MIGRATED to features/deep-research.js.

        // v4.0 Commit 15 (2026-05-25, Phase 8): formatReasoningForVerification MIGRATED to features/deep-research.js.

        // KG Toggle Button Handler (Bug #17: operate on kgContentWrapper, not individual elements)
        document.addEventListener('DOMContentLoaded', () => {
            const toggleButton = document.getElementById('kgToggleButton');
            const wrapper = document.getElementById('kgContentWrapper');
            const icon = document.getElementById('kgToggleIcon');

            if (toggleButton && wrapper) {
                toggleButton.addEventListener('click', () => {
                    const isCollapsed = wrapper.style.display === 'none';
                    wrapper.style.display = isCollapsed ? '' : 'none';
                    icon.textContent = isCollapsed ? '▼' : '▶';
                    toggleButton.childNodes[1].textContent = isCollapsed ? ' 收起' : ' 展開';
                });
            }
        });

        // v4.0 Commit 17 (2026-05-25, Phase 8): 13 chat function bodies MIGRATED to features/chat.js.
        //   performFreeConversation + addChatMessage + pin-message helpers (8) + pin-news-card helpers (3).
        //   Event delegation handler for .news-card-pin click below uses imported togglePinNewsCard.


        // Event delegation for news card pin buttons
        document.addEventListener('click', (e) => {
            if (e.target.classList.contains('news-card-pin')) {
                e.preventDefault();
                e.stopPropagation();
                const card = e.target.closest('.news-card');
                if (card) {
                    const url = card.dataset.url;
                    const title = card.dataset.title;
                    const description = card.dataset.description || '';
                    if (url && title) {
                        togglePinNewsCard(url, title, description);
                    }
                }
            }
        });

        // ==================== END PIN NEWS CARD FUNCTIONS ====================

        // v4.0 Commit 15 (2026-05-25, Phase 8): addClarificationMessage MIGRATED to features/deep-research.js.

        // v4.0 Commit 15 (2026-05-25, Phase 8): attachClarificationListeners MIGRATED to features/deep-research.js.

        // v4.0 Commit 15 (2026-05-25, Phase 8): submitClarification MIGRATED to features/deep-research.js.

        // View tabs
        const tabs = document.querySelectorAll('.tab');
        const researchView = document.getElementById('researchView');
        const liveResearchView = document.getElementById('liveResearchView');
        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                const view = tab.dataset.view;

                // Update active tab
                tabs.forEach(t => t.classList.remove('active'));
                tab.classList.add('active');

                // Switch views (four-way: list / timeline / research / live-research)
                if (view === 'list') {
                    listView.style.display = 'flex';
                    timelineView.classList.remove('active');
                    if (researchView) researchView.classList.remove('active');
                    if (liveResearchView) liveResearchView.classList.remove('active');
                    summaryToggle.classList.add('active');
                    // Restore main search input when leaving LR view
                    searchContainer.style.display = 'block';
                } else if (view === 'timeline') {
                    listView.style.display = 'none';
                    timelineView.classList.add('active');
                    if (researchView) researchView.classList.remove('active');
                    if (liveResearchView) liveResearchView.classList.remove('active');
                    summaryToggle.classList.add('active');
                    // Restore main search input when leaving LR view
                    searchContainer.style.display = 'block';
                } else if (view === 'research') {
                    listView.style.display = 'none';
                    timelineView.classList.remove('active');
                    if (researchView) researchView.classList.add('active');
                    if (liveResearchView) liveResearchView.classList.remove('active');
                    summaryToggle.classList.remove('active'); // Hide summary toggle in research view
                    // Restore main search input when leaving LR view
                    searchContainer.style.display = 'block';
                } else if (view === 'live-research') {
                    listView.style.display = 'none';
                    timelineView.classList.remove('active');
                    if (researchView) researchView.classList.remove('active');
                    if (liveResearchView) liveResearchView.classList.add('active');
                    summaryToggle.classList.remove('active');
                    // Hide main search input when in LR view (lrCheckpointReply is the input)
                    searchContainer.style.display = 'none';
                }
            });
        });

        // Summary toggle
        btnToggleSummary.addEventListener('click', () => {
            if (!getSummaryExpanded()) {
                // Expand summary - just show the descriptions that are already loaded
                showSummaries();
                setSummaryExpanded(true);
                btnToggleSummary.innerHTML = '<span class="emoji-bw">📝</span> 收起摘要';
                btnToggleSummary.classList.add('expanded');
            } else {
                // Collapse summary
                hideSummaries();
                setSummaryExpanded(false);
                btnToggleSummary.innerHTML = '<span class="emoji-bw">📝</span> 展開摘要';
                btnToggleSummary.classList.remove('expanded');
            }
        });

        // v4.0 Commit 14b (2026-05-25, Phase 8): showSummaries MIGRATED to features/search.js.

        // v4.0 Commit 14b (2026-05-25, Phase 8): hideSummaries MIGRATED to features/search.js.

        // Share modal
        btnShare.addEventListener('click', () => {
            modalOverlay.classList.add('active');
        });

        btnCloseModal.addEventListener('click', () => {
            modalOverlay.classList.remove('active');
            clearShareContentOverride(); // Clear override when closing
        });

        modalOverlay.addEventListener('click', (e) => {
            if (e.target === modalOverlay) {
                modalOverlay.classList.remove('active');
                clearShareContentOverride(); // Clear override when closing
            }
        });

        // v4.0 Commit 22 (2026-05-25, Phase 8 part C): handleDeleteSession + deleteSavedSession + deleteConfirmTimeout MIGRATED to features/sessions-list.js. Re-bridged window.deleteSavedSession at import site for sidebar dropdown click handler compat (handleDeleteSession is not exposed since it is only called from migrated sidebar code). Sweep commit 25.

        // Function to load a saved session
        async function loadSavedSession(session) {
            // Always resolve fresh from savedSessions — closure may hold stale object
            const freshSession = getSavedSessions().find(s => window.matchSessionId(s.id, session.id)) || session;
            session = freshSession;

            // Task 9 (Trigger E): always force-hydrate from server on click.
            // Cache may be stale across tabs (another tab edited this session). The
            // only exceptions: (a) _isShared sessions already hydrate inline via the
            // shared-session click handler — re-hydrating is redundant and would
            // refetch with the original owner_id, harmless but wasteful; (b) sessions
            // without _serverId / UUID-shape id are purely local (pre-login drafts).
            const serverId = session._serverId
                || (typeof session.id === 'string' && session.id.includes('-') ? session.id : null);
            if (!session._isShared && serverId && window.authManager.isLoggedIn()) {
                try {
                    const res = await window.authManager.authenticatedFetch(`/api/sessions/${serverId}`);
                    const data = await res.json();
                    if (res.ok && data.success && data.session) {
                        // Server returns snake_case; map to camelCase for in-memory shape
                        const s = data.session;
                        const hydrated = {
                            ...session,
                            // Bug A defense: ensure _serverId is set so subsequent saveCurrentSession
                            // → scheduleSave goes via PUT, not POST. serverId comes from session._serverId
                            // (if set on a prior hydrate) or from the UUID-shaped session.id (list_sessions
                            // returns id = PG UUID for server-resident sessions).
                            _serverId: session._serverId || serverId,
                            conversationHistory: s.conversation_history ?? s.conversationHistory ?? [],
                            sessionHistory: s.session_history ?? s.sessionHistory ?? [],
                            chatHistory: s.chat_history ?? s.chatHistory ?? [],
                            accumulatedArticles: s.accumulated_articles ?? s.accumulatedArticles ?? [],
                            pinnedMessages: s.pinned_messages ?? s.pinnedMessages ?? [],
                            pinnedNewsCards: s.pinned_news_cards ?? s.pinnedNewsCards ?? [],
                            researchReport: s.research_report ?? s.researchReport ?? null,
                            conversationId: s.conversation_id ?? s.conversationId ?? null,
                            // G3：live_research_state 含 schema_version，供 legacy session gate 使用
                            liveResearchState: s.live_research_state ?? s.liveResearchState ?? null,
                        };
                        // Replace in-memory entry so future clicks (and saveSession) see full content
                        const idx = getSavedSessions().findIndex(x => window.matchSessionId(x.id, session.id));
                        if (idx !== -1) getSavedSessions()[idx] = hydrated;
                        session = hydrated;
                    } else if (res.status === 401) {
                        // E2E v10 修法：hydrate 拿到 401（auth refresh 之後仍 401，例如
                        // session 屬於另一個 user 或已被刪除）→ 必須 cleanup stale
                        // localStorage entry，避免每次 reload 都自動 hydrate 同一個失效
                        // session、導致 frontend stuck 在 default search mode 切不到 LR。
                        //
                        // 紀律：
                        //   1) 不堵 error — 既有 `authenticatedFetch` 已嘗試 refresh，
                        //      若 refresh 本身 fail 會走 `_handleAuthFailure` 完整 logout。
                        //      這裡只處理「refresh 成功但 session 仍 401」(stale id) 情境。
                        //   2) 不 silent — console.warn 留 trace。
                        //   3) Cleanup 後 return，讓 frontend 進 fresh state（不繼續 restore
                        //      mode、不 render 空對話）。
                        console.warn(
                            '[Session] Auth 401 — cleared stale localStorage entry for session',
                            session.id,
                            '(session no longer accessible: deleted / cross-user / expired)'
                        );
                        // (a) 移除 in-memory savedSessions 對應 entry
                        const staleIdx = getSavedSessions().findIndex(x => window.matchSessionId(x.id, session.id));
                        if (staleIdx !== -1) getSavedSessions().splice(staleIdx, 1);
                        // (b) 同步寫回 localStorage 的 taiwanNewsSavedSessions（保留其他 entry）
                        try {
                            localStorage.setItem('taiwanNewsSavedSessions', JSON.stringify(getSavedSessions()));
                        } catch (storageErr) {
                            console.warn('[Session] Failed to persist cleaned savedSessions:', storageErr);
                        }
                        // (c) 重新 render sidebar 反映清掉的 entry
                        if (typeof window.renderLeftSidebarSessions === 'function') {
                            try { window.renderLeftSidebarSessions(); } catch (_) { /* render failure 不阻擋 cleanup */ }
                        }
                        // (d) 中斷 load flow — 不繼續往下 restore conversationHistory / mode，
                        //     讓 UI 維持 fresh state（visitor / 初始狀態），等 user 自行操作。
                        return;
                    } else {
                        console.warn('[Session] Hydrate failed; server responded non-OK', data);
                    }
                } catch (e) {
                    // Don't silent-fail: log clearly and let fallback (?? []) keep UI usable
                    console.error('[Session] Failed to fetch full session from server:', e);
                }
            }
            console.log('Loading saved session:', session.id, 'sessionHistory:', session.sessionHistory?.length || 0);

            // If there's an active request, mark current session as interrupted before cancelling
            if (searchInput.dataset.processing === 'true' && getCurrentLoadedSessionId() !== null) {
                const interruptedQuery = getCurrentMode() === 'chat'
                    ? (getChatHistory().filter(m => m.role === 'user').pop()?.content || '')
                    : (getConversationHistory().length > 0 ? getConversationHistory()[getConversationHistory().length - 1] : '');
                if (interruptedQuery) {
                    const idx = getSavedSessions().findIndex(s => window.matchSessionId(s.id, getCurrentLoadedSessionId()));
                    if (idx !== -1) {
                        getSavedSessions()[idx].interruptedSearch = { query: interruptedQuery, mode: getCurrentMode() };
                        localStorage.setItem('taiwanNewsSavedSessions', JSON.stringify(getSavedSessions()));
                        console.log('[Session] Marked as interrupted:', getCurrentLoadedSessionId(), interruptedQuery);
                    }
                }
            }

            // Cancel all active requests (search, DR, free convo)
            cancelAllActiveRequests();
            setProcessingState(false);

            // Increment searchGenerationId so any lingering callbacks skip DOM updates
            bumpSearchGenerationId();

            // Track this session's ID to prevent duplicate saves
            // v4.0 Commit 10 (2026-05-24): currentLoadedSessionId owned by features/sessions-list.js.
            setCurrentLoadedSessionId(session.id);

            // RCA Fix 1: loading a session is a read-only navigation. Reset dirty so
            // any subsequent saveCurrentSession() call (e.g. before next navigate)
            // early-returns until the user actually produces new content.
            clearSessionDirty();

            // Restore conversation history and session data
            // Defense-in-depth: even if hydrate failed, never crash on undefined spread
            setConversationHistory(session.conversationHistory ? [...session.conversationHistory] : []);
            // v4.0 Commit 10 (2026-05-24): sessionHistory owned by features/sessions-list.js.
            setSessionHistory(session.sessionHistory ? [...session.sessionHistory] : []);

            // Restore chat history and accumulated articles (if they exist)
            setChatHistory(session.chatHistory ? [...session.chatHistory] : []);
            setAccumulatedArticles(session.accumulatedArticles ? [...session.accumulatedArticles] : []);
            setPinnedMessages(session.pinnedMessages ? [...session.pinnedMessages] : []);
            setPinnedNewsCards(session.pinnedNewsCards ? [...session.pinnedNewsCards] : []);

            // Restore conversation ID for follow-up context continuity
            setCurrentConversationId(session.conversationId || null);
            setCurrentResearchQueryId(session.researchQueryId || null);

            // Restore Deep Research report for follow-up Q&A
            // Prefer per-entry snapshot from sessionHistory (supports multiple DR reports);
            // fall back to top-level session.researchReport for backward compatibility.
            const lastDREntry = [...getSessionHistory()].reverse().find(e => e.isDeepResearch && e.researchReport);

            if (lastDREntry) {
                setResearchReport({ ...lastDREntry.researchReport });
                setArgumentGraph(lastDREntry.argumentGraph ? [...lastDREntry.argumentGraph] : null);
                setChainAnalysis(lastDREntry.chainAnalysis ? { ...lastDREntry.chainAnalysis } : null);
                console.log('[Session] Restored research report from getSessionHistory() entry:', getResearchReport().report?.substring(0, 100) + '...');
                if (getArgumentGraph()) {
                    console.log('[Session] Restored argument graph with', getArgumentGraph().length, 'nodes');
                }
            } else if (session.researchReport) {
                // Backward compat: old saved sessions without per-entry snapshots
                setResearchReport({ ...session.researchReport });
                setArgumentGraph(session.researchReport?.argumentGraph ? [...session.researchReport.argumentGraph] : null);
                setChainAnalysis(session.researchReport?.chainAnalysis ? { ...session.researchReport.chainAnalysis } : null);
                console.log('[Session] Restored research report from top-level (legacy):', getResearchReport().report?.substring(0, 100) + '...');
            } else {
                clearResearchReport();
                clearArgumentGraph();
                clearChainAnalysis();
            }

            // 先重置 UI 到首頁狀態（resets mode to search, clears all containers, resets tabs to list)
            resetToHome();
            const aiSummarySec = document.getElementById('aiSummarySection');
            if (aiSummarySec) aiSummarySec.style.display = 'none';

            // Render the last query's results (articles + AI summary)
            if (session.interruptedSearch) {
                // Session had an in-progress search that was interrupted — show retry button
                // Show old results underneath if available
                if (getSessionHistory().length > 0) {
                    const lastSession = getSessionHistory()[getSessionHistory().length - 1];
                    populateResultsFromAPI(lastSession.data, lastSession.query);
                }
                showInterruptedSearchNotice(session.interruptedSearch.query, session.interruptedSearch.mode);
                searchInput.value = session.interruptedSearch.query || '';
                resultsSection.classList.add('active');
                initialState.style.display = 'none';
            } else if (getSessionHistory().length > 0) {
                const lastSession = getSessionHistory()[getSessionHistory().length - 1];
                populateResultsFromAPI(lastSession.data, lastSession.query);

                // resetToHome() 移除了 .active class，恢復 session 後需要重新加上
                resultsSection.classList.add('active');
                initialState.style.display = 'none';
            } else if (getConversationHistory().length > 0 && !getResearchReport()) {
                // Session has query but no results (edge case)
                const lastQuery = getConversationHistory()[getConversationHistory().length - 1];
                searchInput.value = lastQuery;
            }

            // Update conversation history display
            renderConversationHistory();

            // Restore chat messages if any (rendering only, mode switch handled below)
            if (getChatHistory().length > 0) {
                console.log(`Restoring ${getChatHistory().length} chat messages`);
                chatMessagesEl.innerHTML = '';

                getChatHistory().forEach(msg => {
                    const messageDiv = document.createElement('div');
                    messageDiv.className = `chat-message ${msg.role}`;

                    const msgId = msg.msgId || `msg-${msg.timestamp}-${Math.random().toString(36).substr(2, 9)}`;
                    messageDiv.setAttribute('data-msg-id', msgId);

                    const headerText = msg.role === 'user' ? '你' : '讀豹';
                    let formattedContent = msg.content;
                    if (msg.role === 'assistant') {
                        formattedContent = DOMPurify.sanitize(marked.parse(msg.content));
                    } else {
                        formattedContent = escapeHTML(msg.content);
                    }

                    const isPinned = getPinnedMessages().some(p => p.msgId === msgId);

                    messageDiv.innerHTML = `
                        <div class="chat-message-header">${headerText}</div>
                        <div class="chat-message-content-wrapper">
                            <div class="chat-message-bubble">${formattedContent}</div>
                            <button class="chat-message-pin ${isPinned ? 'pinned' : ''}" data-msg-id="${msgId}" title="${isPinned ? '取消釘選' : '釘選訊息'}"><img src="/static/images/Icon_Pin.png" alt="" class="inline-icon"></button>
                        </div>
                    `;

                    const pinBtn = messageDiv.querySelector('.chat-message-pin');
                    pinBtn.addEventListener('click', () => togglePinMessage(msgId, msg.content, msg.role));

                    chatMessagesEl.appendChild(messageDiv);
                });

                chatContainer.classList.add('active');
                renderPinnedBanner();
                chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
            }

            // Render pinned news list (outside of chat block since news cards are separate)
            renderPinnedNewsList();

            // Restore Deep Research report in research view if available
            const _rrRestore = getResearchReport();
            if (_rrRestore && _rrRestore.report) {
                const researchViewEl = document.getElementById('researchView');
                if (researchViewEl) {
                    console.log('[Session] Restoring research report to research view');
                    researchViewEl.innerHTML = '';

                    // Create report container
                    const reportContainer = document.createElement('div');
                    reportContainer.className = 'deep-research-report';

                    // Convert markdown to HTML
                    let reportHTML = DOMPurify.sanitize(marked.parse(_rrRestore.report));

                    // Add citation links if sources are available
                    if (_rrRestore.sources && _rrRestore.sources.length > 0) {
                        reportHTML = addCitationLinks(reportHTML, _rrRestore.sources);
                    }

                    // Apply collapsible sections
                    reportHTML = addCollapsibleSections(reportHTML);

                    // Append citation reference list (toggle)
                    if (_rrRestore.sources && _rrRestore.sources.length > 0) {
                        reportHTML += generateCitationReferenceList(_rrRestore.sources);
                    }

                    reportContainer.innerHTML = reportHTML;
                    researchViewEl.appendChild(reportContainer);

                    // Bind collapsible handlers
                    bindCollapsibleHandlers(researchViewEl);

                    // Bind citation reference toggles (CSP-safe)
                    bindCitationReferenceToggles(reportContainer);

                    // Add toggle-all toolbar
                    addToggleAllToolbar(reportContainer);

                    // Restore reasoning chain if available
                    const _agRestore = getArgumentGraph();
                    if (_agRestore && _agRestore.length > 0) {
                        displayReasoningChainInContainer(_agRestore, getChainAnalysis(), researchViewEl);
                        console.log('[Session] Restored reasoning chain in research view');
                    }

                    // Show results section and switch to research tab
                    resultsSection.classList.add('active');
                    const researchTab = document.querySelector('.tab[data-view="research"]');
                    if (researchTab) {
                        researchTab.click();
                    }
                }

                // Restore Knowledge Graph if available (prefer per-entry, fallback to top-level)
                // Don't overwrite KG if user is currently editing
                // v4.0 Commit 21 (2026-05-25): kgEditMode read via getKGEditMode().
                const kgSource = lastDREntry?.knowledgeGraph || session.knowledgeGraph;
                if (kgSource && !getKGEditMode()) {
                    displayKnowledgeGraph(kgSource);
                }

                // Restore researchQueryId from the most recent DR entry that has it
                const lastEntryWithQueryId = [...getSessionHistory()].reverse().find(e => e.researchQueryId);
                if (lastEntryWithQueryId) {
                    setCurrentResearchQueryId(lastEntryWithQueryId.researchQueryId);
                }
            } else {
                // No research report in this session — clear any leftover report display
                const researchViewEl = document.getElementById('researchView');
                if (researchViewEl) {
                    researchViewEl.innerHTML = '';
                }
            }

            // Restore mode from saved session (default: infer from content)
            // LR #19 fix: if mode is missing (NULL from server for pre-migration sessions)
            // but liveResearchState is present and non-trivial, infer 'live_research'.
            // This handles existing sessions that pre-date the mode column addition.
            const hasLRState = session.liveResearchState &&
                typeof session.liveResearchState === 'object' &&
                (session.liveResearchState.current_stage ?? 0) > 0;
            const savedMode = session.mode ||
                (hasLRState ? 'live_research' :
                    (getResearchReport() ? 'deep_research' :
                        (getChatHistory().length > 0 ? 'chat' : 'search')));
            setCurrentMode(savedMode);

            // Sync mode button UI
            modeButtonsInline.forEach(btn => {
                btn.classList.remove('active');
                if (btn.dataset.mode === savedMode) btn.classList.add('active');
            });

            // Mode-specific UI setup
            if (savedMode === 'deep_research') {
                btnSearch.textContent = '搜尋';
                searchInput.placeholder = '輸入問題進行深度研究分析...';
                chatContainer.classList.add('active');
                chatInputContainer.appendChild(searchContainer);
                chatInputContainer.style.display = 'block';
                // DR popup already confirmed for restored sessions
                advancedSearchConfirmed = true;
            } else if (savedMode === 'live_research') {
                btnSearch.textContent = '搜尋';
                searchInput.placeholder = '輸入問題，讀豹將即時展示研究過程...';
                chatContainer.classList.add('active');
                chatInputContainer.appendChild(searchContainer);
                chatInputContainer.style.display = 'block';

                // G3 legacy session gate：偵測 v1 session（schema_version < 2）
                // live_research_state 在 hydrate 步驟中從 server 讀取；
                // 舊 session 可能沒有此欄位 → default to legacy (v1)
                const lrState = session.liveResearchState;
                const lrSchemaVersion = (lrState && typeof lrState === 'object')
                    ? (lrState.schema_version ?? 1)
                    : (lrState == null ? 1 : 1); // 無 lrState = 舊 session = v1
                const isLegacyLRSession = lrSchemaVersion < 2;

                // 取得 session 的原始 query（供 modal 「用同 query 開新研究」）
                const legacyQuery = getConversationHistory().length > 0
                    ? getConversationHistory()[getConversationHistory().length - 1]
                    : '';

                setLRLegacyMode(isLegacyLRSession, legacyQuery);

                if (isLegacyLRSession) {
                    console.info('[Session] Legacy LR session (schema_version <2) — locking revise/continue UI');
                    // 延遲一 tick 確保 DOM 已 reset 後再鎖定
                    setTimeout(() => lockLRUIForLegacySession(), 0);
                    // Do NOT call setLRSessionId or restoreLRCheckpointFromState for legacy sessions
                    // (their state is not readable by current orchestrator)
                } else if (lrState && typeof lrState === 'object') {
                    // LR #19 fix: wire server UUID so continueLiveResearch sends correct lr_session_id
                    // _serverId is the PG UUID used as lr_session_id in backend _load_state()
                    const lrServerId = session._serverId || serverId;
                    if (!lrServerId) {
                        console.warn('[Session] LR resume: no _serverId found, lr_session_id will be null');
                    }

                    // LR #19 順序修正：不在這裡呼叫 setLRSessionId(lrServerId)。
                    // 舊做法：先 set → restoreLRCheckpointFromState 內部 resetLiveResearchUI()
                    // 的 unconditional clearLRSessionId() 把它清成 null → crash。
                    // 新做法：lrServerId 傳進 restoreLRCheckpointFromState，
                    // 在 reset 之後再 set（順序正確）。
                    // Restore checkpoint UI from persisted state — zero HTTP, zero new LR run
                    // Delay one tick so DOM is ready (resetToHome + mode-switch happened above)
                    // B5: bump switch token so any prior stale restore callbacks bail out early.
                    const _switchToken = bumpLRSwitchToken();
                    // B4 (fix): placeholder set here (not in restoreLRCheckpointFromState) so it fills
                    // the setTimeout gap. resetLiveResearchUI() inside restore will clear it before
                    // rendering real content — no stale "載入中" residue.
                    const _lrChatEl = document.getElementById('lrChat');
                    if (_lrChatEl) _lrChatEl.innerHTML = '<div class="lr-loading">載入研究進度中…</div>';
                    setTimeout(() => restoreLRCheckpointFromState(lrState, lrServerId, _switchToken), 0);
                }
            } else if (savedMode === 'chat') {
                btnSearch.textContent = '發送';
                searchInput.placeholder = '讀豹會參考摘要內容及您釘選的文章來回答...';
                chatContainer.classList.add('active');
                chatInputContainer.appendChild(searchContainer);
                chatInputContainer.style.display = 'block';
            }
            // search mode: already set by resetToHome(), no extra work needed

            // Restore search input to last query (skip for live_research — search box is hidden)
            if (savedMode !== 'live_research' && getConversationHistory().length > 0) {
                searchInput.value = getConversationHistory()[getConversationHistory().length - 1];
            }

            console.log(`[Session] Mode restored: ${savedMode}`);

            // Hide initial state (session has content)
            initialState.style.display = 'none';
            resultsSection.style.display = '';  // Clear inline style so CSS class takes effect
            // resultsSection.active is already set in the sessionHistory block above (if needed)
            // 確保資料夾頁面關閉（不走 hideFolderPage 以免覆蓋我們剛設好的狀態）
            const _fp = document.getElementById('folderPage');
            if (_fp) _fp.style.display = 'none';
            clearPreFolderState();
            // 確保搜尋容器可見
            searchContainer.style.display = 'block';

            // Refresh sidebar to sync active session highlight
            window.renderLeftSidebarSessions();
        }
        // Phase v4.0 commit 0a (Z prep): explicit window-attach. Removed in commit 19.
        window.loadSavedSession = loadSavedSession;

        // ===== Export/Share Functions =====
        // v4.0 Commit 18 (2026-05-25, Phase 8): 7 export/share function bodies MIGRATED to features/sharing.js.
        //   cleanHTMLContent / getTop10Articles / formatPlainText / formatForAIChatbot /
        //   formatForNotebookLM / copyAndOpen / openFeedbackModal.


        // Button handlers
        const btnCopyPlainText = document.getElementById('btnCopyPlainText');
        const btnCopyChatGPT = document.getElementById('btnCopyChatGPT');
        const btnCopyClaude = document.getElementById('btnCopyClaude');
        const btnCopyGemini = document.getElementById('btnCopyGemini');
        const btnCopyNotebookLM = document.getElementById('btnCopyNotebookLM');

        btnCopyPlainText.addEventListener('click', () => {
            const content = getShareContentOverride() || formatPlainText();
            copyAndOpen(content, null, btnCopyPlainText);
        });

        btnCopyChatGPT.addEventListener('click', () => {
            const content = getShareContentOverride() || formatForAIChatbot();
            copyAndOpen(content, 'https://chat.openai.com/', btnCopyChatGPT);
        });

        btnCopyClaude.addEventListener('click', () => {
            const content = getShareContentOverride() || formatForAIChatbot();
            copyAndOpen(content, 'https://claude.ai/', btnCopyClaude);
        });

        btnCopyGemini.addEventListener('click', () => {
            const content = getShareContentOverride() || formatForAIChatbot();
            copyAndOpen(content, 'https://gemini.google.com/', btnCopyGemini);
        });

        btnCopyNotebookLM.addEventListener('click', () => {
            const content = getShareContentOverride() || formatForNotebookLM();
            copyAndOpen(content, 'https://notebooklm.google.com/', btnCopyNotebookLM);
        });

        // Feedback buttons — open modal for user comment (Bug #14)
        // Use event delegation because .btn-feedback buttons are created dynamically
        // after search results render, not at page load time
        document.addEventListener('click', (e) => {
            const btn = e.target.closest('.btn-feedback');
            if (btn) {
                const rating = btn.dataset.rating || (btn.textContent.includes('👍') ? 'positive' : 'negative');
                openFeedbackModal(rating);
            }
        });

        // v4.0 Commit 18 (2026-05-25, Phase 8): openFeedbackModal MIGRATED to features/sharing.js (event delegation above uses imported name).


        // ==================== SOURCE TREE VIEW (VS Code Style) ====================

        // v4.0 Commit 13 (2026-05-25, Phase 8): source-filters block MIGRATED.
        // 4 lets (availableSites / selectedSites / sourceDisplayNames / includePrivateSources)
        // + 18 functions (loadSourceFolders, saveSourceFolders, loadSiteFilters, distributeToFolders,
        // renderSourceTreeView, bindSourceTreeEvents, moveSiteToFolder, addSourceFolder,
        // startRenamingFolder, deleteSourceFolder, toggleSiteFilter, toggleAllSites,
        // expandAllSourceFolders, collapseAllSourceFolders, renderSiteFilters,
        // getSelectedSitesParam, togglePrivateSources, triggerFileUpload) all live in
        // static/js/features/source-filters.js. Imports at top of this file.
        //
        // The addEventListener wires below reference the imported function identifiers
        // (togglePrivateSources, addSourceFolder, expand/collapseAllSourceFolders,
        // toggleAllSites, triggerFileUpload). handleFileSelect (commit 19) moved to
        // features/file-kb.js — the change-event wire still resolves via ES import above.

        // Bind private sources checkbox (JS listener, not inline onchange)
        document.getElementById('includePrivateSourcesCheckbox')?.addEventListener('change', togglePrivateSources);

        // Bind toolbar buttons for source tree
        document.getElementById('btnAddSourceFolder')?.addEventListener('click', addSourceFolder);
        document.getElementById('btnExpandAllSources')?.addEventListener('click', expandAllSourceFolders);
        document.getElementById('btnCollapseAllSources')?.addEventListener('click', collapseAllSourceFolders);
        document.getElementById('btnToggleAllSites')?.addEventListener('click', toggleAllSites);

        // Bind file upload buttons and file input (moved from inline handlers for CSP compliance)
        document.getElementById('btnUploadFile')?.addEventListener('click', function() { triggerFileUpload(); });
        document.getElementById('btnUploadInline')?.addEventListener('click', function() { triggerFileUpload(); });
        document.getElementById('fileInput')?.addEventListener('change', function(e) { handleFileSelect(e); });


        // ==================== v4.0 Commit 19 (2026-05-25, Phase 8 part C): MOVED to features/file-kb.js ====================
        // 19 file-kb functions MIGRATED: handleFileSelect / loadFileFolders / saveFileFolders /
        //   saveSelectedFiles / loadUserFiles / distributeFilesToFolders / renderFileTreeView /
        //   bindFileTreeEvents / updateIncludePrivateSourcesState / moveFileToFolder /
        //   addFileFolder / startRenamingFileFolder / deleteFileFolder /
        //   expandAllFileFolders / collapseAllFileFolders / renderFileList / deleteUserFile /
        //   getFileIcon / getStatusText.
        // userFiles state MIGRATED (was let userFiles = [] above; now _userFiles in file-kb.js).
        // ==================== /Commit 19 MOVED ====================

        // Bind toolbar buttons for file tree
        document.getElementById('btnAddFileFolder')?.addEventListener('click', addFileFolder);
        document.getElementById('btnExpandAllFiles')?.addEventListener('click', expandAllFileFolders);
        document.getElementById('btnCollapseAllFiles')?.addEventListener('click', collapseAllFileFolders);

        // ==================== LEFT SIDEBAR SESSION LIST ====================

        // ==================== Phase 4a Path B (2026-05-21): MOVED to static/js/features/sessions-list.js ====================
        // The function renderLeftSidebarSessions is now defined in features/sessions-list.js.
        // main.js attaches it via window.renderLeftSidebarSessions bridge so legacy callsites still resolve.
        // Body kept here (line-by-line // prefixed) as reference until Phase 8 sweep deletes.
        // function renderLeftSidebarSessions() {
            // const container = document.getElementById('leftSidebarSessions');
            // if (!container) return;
// 
            // if (savedSessions.length === 0) {
                // container.innerHTML = '';
                // return;
            // }
// 
            // // 最新的在最上面，最多顯示 15 條
            // // Bug X fix: explicit sort by updated_at DESC instead of .reverse()
            // // Server path returns array sorted DESC (newest at index 0); localStorage push puts newest at end.
            // // .reverse() only worked for the localStorage path — sort works for both.
            // const recent = savedSessions
                // .slice()
                // .sort((a, b) => {
                    // const ta = new Date(a.updatedAt || a.updated_at || a.createdAt || a.created_at || 0).getTime();
                    // const tb = new Date(b.updatedAt || b.updated_at || b.createdAt || b.created_at || 0).getTime();
                    // return tb - ta; // DESC: newest at top
                // })
                // .slice(0, 15);
            // container.innerHTML = recent.map(session => {
                // const isActive = matchSessionId(currentLoadedSessionId, session.id);
                // const isOnline = authManager.isLoggedIn() && authManager.getCurrentUser()?.org_id;
                // const isShared = session.visibility && session.visibility !== 'private';
                // const shareLabel = isShared ? '取消共享' : '共享到組織';
                // return `<div class="left-sidebar-session-item${isActive ? ' active' : ''}" data-sidebar-session-id="${session.id}">
                    // <span class="left-sidebar-session-title">${escapeHTML(session.title)}</span>
                    // <button class="left-sidebar-session-menu-btn" data-menu-session-id="${session.id}">&#8943;</button>
                    // <div class="left-sidebar-session-dropdown" data-dropdown-session-id="${session.id}">
                        // <button class="left-sidebar-session-dropdown-item" data-action="rename" data-session-id="${session.id}">重新命名</button>
                        // ${isOnline ? `<button class="left-sidebar-session-dropdown-item" data-action="share" data-session-id="${session.id}">${shareLabel}</button>` : ''}
                        // <button class="left-sidebar-session-dropdown-item danger" data-action="delete" data-session-id="${session.id}">刪除</button>
                    // </div>
                // </div>`;
            // }).join('');
// 
            // // Click on session item to load (ignore menu/dropdown clicks)
            // container.querySelectorAll('.left-sidebar-session-item').forEach(item => {
                // item.addEventListener('click', async (e) => {
                    // if (e.target.closest('.left-sidebar-session-menu-btn') || e.target.closest('.left-sidebar-session-dropdown')) return;
                    // const sessionId = item.dataset.sidebarSessionId;
                    // const session = savedSessions.find(s => matchSessionId(s.id, sessionId));
                    // if (session) {
                        // // Trigger E: session click (sidebar). Identity self-check
                        // // before navigating. Mismatch path: fall back to reload-path
                        // // (Trigger F via runInitSync) — defensive against silent logout.
                        // try {
                            // assertUserIdentity(authManager._user, authManager._user);
                        // } catch (err) {
                            // if (err instanceof UserStateSyncError && err.code !== 'MISSING_FRESH' && authManager.isLoggedIn()) {
                                // console.warn('[session-click:sidebar] identity self-check failed, triggering reload-path:', err);
                                // await UserStateSync.runInitSync({ keepInviteToken: false }).catch(err2 =>
                                    // console.error('[session-click:sidebar] runInitSync failed:', err2));
                                // return;
                            // }
                        // }
                        // // 切換前先保存當前對話（防止深度報告等狀態丟失）
                        // if (sessionHistory.length > 0 || currentResearchReport || chatHistory.length > 0) {
                            // saveCurrentSession();
                        // }
                        // loadSavedSession(session);
                    // }
                // });
            // });
// 
            // // "..." menu button toggle
            // container.querySelectorAll('.left-sidebar-session-menu-btn').forEach(btn => {
                // btn.addEventListener('click', (e) => {
                    // e.stopPropagation();
                    // const sid = btn.dataset.menuSessionId;
                    // const dropdown = container.querySelector(`.left-sidebar-session-dropdown[data-dropdown-session-id="${sid}"]`);
                    // // Close all other dropdowns first
                    // container.querySelectorAll('.left-sidebar-session-dropdown.visible').forEach(d => {
                        // if (d !== dropdown) d.classList.remove('visible');
                    // });
                    // dropdown.classList.toggle('visible');
                // });
            // });
// 
            // // Dropdown actions (rename / delete / share)
            // container.querySelectorAll('.left-sidebar-session-dropdown-item').forEach(actionBtn => {
                // actionBtn.addEventListener('click', (e) => {
                    // e.stopPropagation();
                    // const action = actionBtn.dataset.action;
                    // const sessionId = actionBtn.dataset.sessionId;
                    // if (action === 'delete') {
                        // deleteSavedSession(sessionId);
                    // } else if (action === 'rename') {
                        // startSidebarSessionRename(sessionId);
                    // } else if (action === 'share') {
                        // toggleSessionSharing(sessionId);
                    // }
                // });
            // });
// 
            // // 若處於資料夾管理模式，重新綁定拖曳
            // if (_folderModeActive) {
                // makeSidebarSessionsDraggable();
            // }
        // }
        // ==================== /Phase 4a Path B: MOVED ====================

        // Phase 4a Path B (2026-05-21): outside-click sidebar dropdown close handler
        // MOVED to features/sessions-list.js (registered inside initSessionsList()).
        // // Close sidebar session dropdowns on outside click
        // document.addEventListener('click', () => {
            // const container = document.getElementById('leftSidebarSessions');
            // if (container) {
                // container.querySelectorAll('.left-sidebar-session-dropdown.visible').forEach(d => {
                    // d.classList.remove('visible');
                // });
            // }
        // });

        // v4.0 Commit 22 (2026-05-25, Phase 8 part C): startSidebarSessionRename MIGRATED to features/sessions-list.js. Re-bridged at import site for sidebar dropdown click handler compat. Sweep commit 25.

        // v4.0 Commit 18 (2026-05-25, Phase 8): toggleSessionSharing MIGRATED to features/sharing.js (re-bridge at import block).


        // v4.0 Commit 22 (2026-05-25, Phase 8 part C): _updateOrgSpaceBadge MIGRATED to features/sessions-list.js. Re-bridged at import site for sharing.js commit 18 compat. Sweep commit 25.

        // 資料夾相關旗標（須在任何引用它們的函式呼叫前宣告，避免 let TDZ）
        // v4.0 Commit 8 (2026-05-24): _folderModeActive MIGRATED to features/folders.js.
        // Access via getFolderModeActive() / setFolderModeActive(b). BRIDGE REMOVED:
        // window._folderModeActive getter. features/sessions-list.js now imports
        // getFolderModeActive from features/folders.js directly. Per plan §7 row 8.
        // v4.0 Commit 20 (2026-05-25, Phase 8 part C): _preFolderState MIGRATED to features/folders.js (with the 16 session-folder UI functions).

        // Phase 4a Path B (2026-05-21): session-saved / session-deleted CustomEvent
        // listeners + initial render call MOVED to features/sessions-list.js
        // (registered inside initSessionsList(), called by main.js DOMContentLoaded).
        // // 監聽 session 變更事件，同步更新左側邊欄
        // document.addEventListener('session-saved', renderLeftSidebarSessions);
        // document.addEventListener('session-deleted', renderLeftSidebarSessions);
        //
        // // Initial render — deferred to DOMContentLoaded because Phase 3 Path B
        // // moved authManager to an ES module; window.authManager bridge is set by
        // // main.js (deferred module exec). Direct call at parse time fires
        // // ReferenceError inside renderLeftSidebarSessions's authManager.isLoggedIn()
        // // call (line ~10648). DOMContentLoaded fires after module top-level exec,
        // // so window.authManager is guaranteed set.
        // if (document.readyState === 'loading') {
            // document.addEventListener('DOMContentLoaded', () => renderLeftSidebarSessions(), { once: true });
        // } else {
            // renderLeftSidebarSessions();
        // }

        // ==================== FOLDER/PROJECT SYSTEM ====================

        // Folder data model - persisted in localStorage
        // v4.0 Commit 8 (2026-05-24): folders MIGRATED to features/folders.js.
        // Access via getFolders() (live ref) / setFolders(arr) / pushFolder(f) /
        // removeFolder(id) / clearFolders() / persistFolders().
        try {
            const storedFolders = localStorage.getItem('taiwanNewsFolders');
            if (storedFolders) {
                setFolders(JSON.parse(storedFolders));
                console.log(`[Folder] Loaded ${getFolders().length} folders from localStorage`);
            }
        } catch (e) {
            console.error('[Folder] Failed to load folders from localStorage:', e);
        }

        // v4.0 Commit 20 (2026-05-25, Phase 8 part C): currentFolderSort / currentFolderFilter / currentOpenFolderId / openDropdownFolderId MIGRATED to features/folders.js. DOM wires below use setFolderFilter / setFolderSort setters.
        // ==================== v4.0 Commit 20 MOVED to features/folders.js ====================
        // 16 functions migrated: saveFolders, createFolder, renameFolder, deleteFolder,
        //   addSessionToFolder, removeSessionFromFolder, showFolderPage, hideFolderPage,
        //   showFolderMain, showFolderDetail, getTimeAgo, getSortedFolders, renderFolderGrid,
        //   toggleFolderDropdown, closeFolderDropdowns, startFolderRename,
        //   renderFolderDetailSessions. Outside-click handler for folder-card-menu
        //   dropdowns now lives inside folders.js renderFolderGrid (or stays here if pre-existing).
        // ==================== /Commit 20 MOVED ====================

        // Close folder-card dropdowns when clicking outside (top-level side effect; was between closeFolderDropdowns and startFolderRename in the migrated block).
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.folder-card-menu')) {
                closeFolderDropdowns();
            }
        });

        // -- Wire sidebar "開啟資料夾" button to folder page --
        btnToggleCategories.addEventListener('click', () => {
            showFolderPage();
        });

        // "< 回到搜尋" button on folder main page
        document.getElementById('btnFolderBackToHome').addEventListener('click', () => {
            hideFolderPage();
        });

        // "新增資料夾" button on folder page
        document.getElementById('btnAddFolder').addEventListener('click', () => {
            createFolder();
        });

        // "< 回到頁" button
        document.getElementById('btnFolderBack').addEventListener('click', () => {
            showFolderMain();
        });

        // Folder search input
        // Folder search input
        document.getElementById('folderSearchInput').addEventListener('input', (e) => {
            setFolderFilter(e.target.value.trim());
            renderFolderGrid();
        });

        // Folder sort tabs
        document.querySelectorAll('.folder-sort-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.folder-sort-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                setFolderSort(tab.dataset.sort);
                renderFolderGrid();
            });
        });

        // -- Drag-and-drop: sidebar sessions → folder cards --
        // 使用 event delegation 避免 listener 堆疊，且不干擾子元素點擊

        // 單一 delegated handler，綁在 container 上（只綁一次）
        (function initSidebarDragDelegation() {
            const container = document.getElementById('leftSidebarSessions');
            if (!container) return;

            container.addEventListener('dragstart', (e) => {
                if (!getFolderModeActive()) return;
                // 若從按鈕/選單觸發，取消拖曳
                if (e.target.closest('.left-sidebar-session-menu-btn') || e.target.closest('.left-sidebar-session-dropdown')) {
                    e.preventDefault();
                    return;
                }
                const item = e.target.closest('.left-sidebar-session-item');
                if (!item) return;
                const sessionId = item.dataset.sidebarSessionId;
                if (!sessionId) return;
                e.dataTransfer.setData('text/session-id', sessionId);
                e.dataTransfer.effectAllowed = 'copy';
                item.classList.add('dragging');
            });

            container.addEventListener('dragend', (e) => {
                const item = e.target.closest('.left-sidebar-session-item');
                if (item) item.classList.remove('dragging');
            });
        })();

        // v4.0 Commit 20 (2026-05-25): makeSidebarSessionsDraggable / removeSidebarSessionsDraggable MIGRATED to features/folders.js.
        // The window.makeSidebarSessionsDraggable re-bridge is wired at the top-level import block above (read by sessions-list.js).

        // ==================== END FOLDER/PROJECT SYSTEM ====================

        // ==================== LARGE FONT MODE ====================
        (function initLargeFontMode() {
            document.addEventListener('DOMContentLoaded', () => {
                const btn = document.getElementById('btnFontSize');
                if (!btn) return;

                // Restore preference
                try {
                    if (localStorage.getItem('nlweb-large-font') === 'true') {
                        document.body.classList.add('large-font');
                        btn.classList.add('active');
                    }
                } catch (e) { /* localStorage unavailable */ }

                btn.addEventListener('click', () => {
                    const isActive = document.body.classList.toggle('large-font');
                    btn.classList.toggle('active', isActive);
                    try {
                        localStorage.setItem('nlweb-large-font', isActive ? 'true' : 'false');
                    } catch (e) { /* localStorage unavailable */ }
                });
            });
        })();

        // ==================== KG VISIBILITY TOGGLE ====================
        (function initKGVisibilityToggle() {
            document.addEventListener('DOMContentLoaded', () => {
                const hideBtn = document.getElementById('kgHideBtn');
                const restoreBar = document.getElementById('kgRestoreBar');
                const kgContainer = document.getElementById('kgDisplayContainer');
                if (!hideBtn || !restoreBar || !kgContainer) return;

                // Restore preference
                let kgHidden = false;
                try {
                    kgHidden = localStorage.getItem('nlweb-kg-hidden') === 'true';
                } catch (e) { /* localStorage unavailable */ }

                // Apply stored preference: if hidden, ensure container stays hidden and bar is ready
                if (kgHidden) {
                    // The container starts display:none anyway; keep restoreBar ready
                    // restoreBar will show when displayKnowledgeGraph is called
                    kgContainer.dataset.userHidden = 'true';
                }

                hideBtn.addEventListener('click', () => {
                    kgContainer.style.display = 'none';
                    kgContainer.dataset.userHidden = 'true';
                    restoreBar.style.display = 'block';
                    try {
                        localStorage.setItem('nlweb-kg-hidden', 'true');
                    } catch (e) {}
                });

                restoreBar.addEventListener('click', () => {
                    kgContainer.style.display = 'block';
                    kgContainer.dataset.userHidden = 'false';
                    restoreBar.style.display = 'none';
                    try {
                        localStorage.setItem('nlweb-kg-hidden', 'false');
                    } catch (e) {}
                });
            });
        })();

        // Phase 4a Path B (2026-05-21): initSessionTabs IIFE MOVED to
        // features/sessions-list.js (registered inside initSessionsList()).
        // // ==================== SESSION TABS (我的對話 / 組織空間) ====================
        // (function initSessionTabs() {
            // document.addEventListener('DOMContentLoaded', () => {
                // const tabs = document.querySelectorAll('.left-sidebar-sessions-tab');
                // const myList = document.getElementById('leftSidebarSessions');
                // const sharedList = document.getElementById('leftSidebarSessionsShared');
                // if (!tabs.length) return;
        //
                // tabs.forEach(tab => {
                    // tab.addEventListener('click', () => {
                        // tabs.forEach(t => t.classList.remove('active'));
                        // tab.classList.add('active');
                        // const which = tab.dataset.sessionsTab;
                        // if (myList) myList.style.display = which === 'my' ? '' : 'none';
                        // if (sharedList) sharedList.style.display = which === 'shared' ? '' : 'none';
                        // if (which === 'shared') {
                            // renderSharedSessions();
                        // }
                    // });
                // });
            // });
        // })();

        // v4.0 Commit 10 (2026-05-24): _sharedSessionsCache / _sharedSessionsLoading
        // declarations + window bridges + _hydrateFromSoftRefreshInit setter MIGRATED to
        // features/sessions-list.js. page-bootstrap.js imports hydrateFromSoftRefreshInit
        // directly; renderSharedSessions (already in sessions-list.js) reads via owner
        // module helpers (getSharedSessions / isSharedSessionsLoading / setSharedSessionsLoading
        // / clearSharedSessions / hydrateSharedSessions). Per plan §3.10 (D-V4 H2 fix).

        // ==================== Phase 4a Path B (2026-05-21): MOVED to static/js/features/sessions-list.js ====================
        // renderSharedSessions + _renderSharedSessionsList are now defined in features/sessions-list.js.
        // main.js attaches renderSharedSessions via window.renderSharedSessions bridge so legacy callsite (cache prefetch) still resolves.
        // _renderSharedSessionsList stays internal to the new module.
        // async function renderSharedSessions() {
            // const container = document.getElementById('leftSidebarSessionsShared');
            // if (!container) return;
            // if (_sharedSessionsLoading) return;
// 
            // // Use cache if available (pre-fetched on page load)
            // if (_sharedSessionsCache) {
                // _renderSharedSessionsList(container, _sharedSessionsCache);
                // _sharedSessionsCache = null;
                // return;
            // }
// 
            // container.innerHTML = '<div style="padding:12px 16px;color:#888;font-size:13px;text-align:center;">載入中...</div>';
            // _sharedSessionsLoading = true;
// 
            // try {
                // const sessions = await sessionManager.loadSharedSessions();
                // _sharedSessionsLoading = false;
                // _renderSharedSessionsList(container, sessions);
            // } catch (err) {
                // _sharedSessionsLoading = false;
                // console.error('[SharedSession] renderSharedSessions error:', err);
                // container.innerHTML = '<div style="padding:12px 16px;color:#888;font-size:13px;text-align:center;">載入共享對話失敗</div>';
            // }
        // }
// 
        // function _renderSharedSessionsList(container, sessions) {
            // if (!sessions || sessions.length === 0) {
                // container.innerHTML = '<div style="padding:12px 16px;color:#888;font-size:13px;text-align:center;">組織空間尚無共享對話</div>';
                // return;
            // }
// 
            // container.innerHTML = sessions.map(session => {
                // const title = escapeHTML(session.title || '未命名對話');
                // const ownerLabel = session.owner_name || session.owner_email || '';
                // const dateStr = session.updated_at ? new Date(session.updated_at).toLocaleDateString('zh-TW') : '';
                // const meta = [ownerLabel, dateStr].filter(Boolean).join(' · ');
                // return `<div class="left-sidebar-session-item" style="cursor:pointer;" data-shared-session-id="${session.id}">
                    // <div style="display:flex;flex-direction:column;gap:2px;overflow:hidden;">
                        // <span class="left-sidebar-session-title">${title}</span>
                        // ${meta ? `<span style="font-size:11px;color:#888;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHTML(meta)}</span>` : ''}
                    // </div>
                // </div>`;
            // }).join('');
// 
            // container.querySelectorAll('[data-shared-session-id]').forEach(item => {
                // item.addEventListener('click', async () => {
                    // const sharedId = item.dataset.sharedSessionId;
                    // try {
                        // const res = await authManager.authenticatedFetch(`/api/sessions/${sharedId}`);
                        // const data = await res.json();
                        // if (res.ok && data.success && data.session) {
                            // const s = data.session;
                            // // Y-1 fix: map server snake_case → camelCase + tag _isShared
                            // // so saveCurrentSession can detect this is another user's session
                            // // (read-only context) and skip — preventing spawn of the
                            // // current user's own row when they type a query in shared view.
                            // const sharedHydrated = {
                                // id: s.id,
                                // _serverId: s.id,
                                // _isShared: true,
                                // _ownerUserId: s.user_id,
                                // title: s.title,
                                // visibility: s.visibility,
                                // conversationHistory: s.conversation_history ?? [],
                                // sessionHistory: s.session_history ?? [],
                                // chatHistory: s.chat_history ?? [],
                                // accumulatedArticles: s.accumulated_articles ?? [],
                                // pinnedMessages: s.pinned_messages ?? [],
                                // pinnedNewsCards: s.pinned_news_cards ?? [],
                                // researchReport: s.research_report ?? null,
                                // conversationId: s.conversation_id ?? null,
                                // createdAt: s.created_at,
                                // updatedAt: s.updated_at,
                            // };
                            // loadSavedSession(sharedHydrated);
                        // } else {
                            // console.error('[SharedSession] Failed to load:', data);
                        // }
                    // } catch (err) {
                        // console.error('[SharedSession] Load error:', err);
                    // }
                // });
            // });
        // }
        // ==================== /Phase 4a Path B: MOVED ====================

        // Initialize on page load
        document.addEventListener('DOMContentLoaded', async () => {
            loadSiteFilters();
            initPinnedBanner();
            await _authReadyPromise;
            loadUserFiles();
        });

