// static/js/features/search.js
//
// D-1 Module Header — Search Owner (state only — commit 2)
//   Owned state:
//     - _conversationHistory (array of query strings — multi-turn conversation tracking)
//     - _accumulatedArticles (array of article objects — accumulated across searches in a session)
//     - _currentConversationId (string|null — backend conversation correlation id)
//
//   Trigger writes:
//     - performSearch / performDeepResearch handlers push to conversationHistory + accumulatedArticles
//     - SSE message handlers set currentConversationId from backend
//     - resetConversation / loadSavedSession / deleteSavedSession reset all 3
//     - UserStateSync.clearUserScopedState (IIFE) clears all 3 on logout
//
//   External read:
//     - render hot paths (chat banner / search tab / share modal preview) iterate via getX()
//     - saveCurrentSession serializes via getX()
//
// D-3 Cross-Module Communication:
//   Static imports only. Pure leaf — no imports from other features.
//
// D-13 Compliance:
//   No top-level side effects on import.
//
// v4.0 Commit 2 (2026-05-24): State-only migration. Function bodies (performSearch /
//   performDeepResearch / handleSearchSSE) remain in news-search.js until Phase 8
//   sweep (commit 12 per plan v4 §3.0).

// ============================================================================
// conversationHistory — array of query strings
// ============================================================================
let _conversationHistory = [];

export function getConversationHistory() {
    return _conversationHistory;
}

export function setConversationHistory(arr) {
    _conversationHistory = Array.isArray(arr) ? arr : [];
}

export function clearConversationHistory() {
    // Preserves array reference semantics (same as `_conversationHistory.length = 0`).
    _conversationHistory.length = 0;
}

export function pushConversationHistory(entry) {
    _conversationHistory.push(entry);
}

// ============================================================================
// accumulatedArticles — array of article objects
// ============================================================================
let _accumulatedArticles = [];

export function getAccumulatedArticles() {
    return _accumulatedArticles;
}

export function setAccumulatedArticles(arr) {
    _accumulatedArticles = Array.isArray(arr) ? arr : [];
}

export function clearAccumulatedArticles() {
    _accumulatedArticles.length = 0;
}

export function pushAccumulatedArticles(items) {
    // Accepts spread-style single array (caller passes ...newArticles or single array).
    if (Array.isArray(items)) {
        _accumulatedArticles.push(...items);
    } else {
        _accumulatedArticles.push(items);
    }
}

// ============================================================================
// currentConversationId — backend conversation correlation id
// ============================================================================
let _currentConversationId = null;

export function getCurrentConversationId() {
    return _currentConversationId;
}

export function setCurrentConversationId(id) {
    _currentConversationId = id;
}

export function clearCurrentConversationId() {
    _currentConversationId = null;
}

// ============================================================================
// v4.0 Commit 14a (2026-05-25, Phase 8) — Search inflight handles + UI flag +
//   12 simple helper functions migrated from news-search.js.
//
// State migrated from news-search.js (was lines 1589, 1647-1654):
//   - summaryExpanded (let — AI summary expand/collapse UI flag)
//   - searchGenerationId (let — monotonic counter for stale-result guard)
//   - currentSearchAbortController (let — main /ask SSE abort handle)
//   - currentSearchEventSource (let — legacy EventSource handle, retained for
//     cancelActiveSearch compatibility)
//   - currentDeepResearchEventSource (let — legacy handle kept for cancelAll
//     compatibility; performDeepResearch migration moves to commit 15 deep-research.js)
//   - currentDeepResearchAbortController (let — DR /deep_research_stream abort handle)
//   - currentFreeConvAbortController (let — chat.js /ask free-conv abort handle)
//
// Functions migrated (12 simple helpers — heavy SSE entry / performSearch /
//   performDeepResearch / populateResultsFromAPI deferred to commit 14b / 15):
//   cancelActiveSearch, cancelAllActiveRequests, showInterruptedSearchNotice,
//   clearQueryState, setProcessingState, renderSkeletonCards, renderSummarySkeleton,
//   updateProgressMessage, createArticleCard, renderArticlesProgressive,
//   renderAnswerProgressive, clearLoadingStates.
//
// Pure helpers co-migrated (used by render functions; news-search.js residual
//   callers continue to reach these via this module's imports OR via
//   window.escapeHTML which stays attached at news-search.js line ~3498 for
//   backward compat until commit 19/25 sweep):
//   escapeHTML, convertMarkdownToHtml
//
// Cross-module imports added (D-V6 relax — single-direction read):
//   - clearResearchReport / clearArgumentGraph / clearChainAnalysis from
//     features/research.js (clearQueryState calls them)
//   - getSavedSessions / getCurrentLoadedSessionId from features/sessions-list.js
//     (showInterruptedSearchNotice retry click handler clears interruptedSearch flag)
//   - getSourceDisplayNames from features/source-filters.js (createArticleCard
//     publisher fallback lookup)
//   - getPinnedNewsCards from features/pins.js (createArticleCard isPinned flag)
//
// Window-attach lines removed in news-search.js: window.escapeHTML stays (still
//   referenced by source-filters / live-research / sharing modules' defensive
//   reads — sweep target commit 19).
// ============================================================================

import {
    clearResearchReport, clearArgumentGraph, clearChainAnalysis
} from './research.js';
import {
    getSavedSessions, getCurrentLoadedSessionId,
    // commit 14b — sessionHistory accumulator
    getSessionHistory
} from './sessions-list.js';
import { getSourceDisplayNames, getSelectedSitesParam } from './source-filters.js';
import { getPinnedNewsCards } from './pins.js';
// commit 14b — additional imports for performSearch + SSE handlers
import { getCurrentMode } from './mode.js';
import {
    getCurrentSessionId,
    getAnalyticsQueryId, setAnalyticsQueryId
} from '../utils/analytics.js';
import { markSessionDirty } from './session-manager.js';
import { UserStateSync } from '../core/state-sync.js';
import { buildCitationHref, escapeHtmlAttr } from './text-fragment.js';
import { isCurrentGeneration } from './search-generation.js';

// ----------------------------------------------------------------------------
// UI flag — AI summary expanded state
// ----------------------------------------------------------------------------
let _summaryExpanded = false;
export function getSummaryExpanded() { return _summaryExpanded; }
export function setSummaryExpanded(b) { _summaryExpanded = !!b; }

// ----------------------------------------------------------------------------
// Search-side inflight handles + monotonic generation counter
// ----------------------------------------------------------------------------
let _searchGenerationId = 0;
let _currentSearchAbortController = null;
let _currentSearchEventSource = null;

export function getSearchGenerationId() { return _searchGenerationId; }
export function bumpSearchGenerationId() { _searchGenerationId++; return _searchGenerationId; }
export { isCurrentGeneration };

export function getCurrentSearchAbortController() { return _currentSearchAbortController; }
export function setCurrentSearchAbortController(c) { _currentSearchAbortController = c; }

export function getCurrentSearchEventSource() { return _currentSearchEventSource; }
export function setCurrentSearchEventSource(es) { _currentSearchEventSource = es; }

// ----------------------------------------------------------------------------
// Deep Research inflight handles (commit 15 migrates performDeepResearch body;
//   these handles co-migrate now so cancelAllActiveRequests can null them
//   without cross-module hopping)
// ----------------------------------------------------------------------------
let _currentDeepResearchEventSource = null; // legacy — cancelAll compat
let _currentDeepResearchAbortController = null;

export function getCurrentDeepResearchEventSource() { return _currentDeepResearchEventSource; }
export function setCurrentDeepResearchEventSource(es) { _currentDeepResearchEventSource = es; }

export function getCurrentDeepResearchAbortController() { return _currentDeepResearchAbortController; }
export function setCurrentDeepResearchAbortController(c) { _currentDeepResearchAbortController = c; }

// ----------------------------------------------------------------------------
// Free-conversation inflight handle (chat.js performFreeConversation body
//   stays in news-search.js until batch 5'' commit 17 chat migration; handle
//   co-migrates now so cancelAllActiveRequests can reach it)
// ----------------------------------------------------------------------------
let _currentFreeConvAbortController = null;

export function getCurrentFreeConvAbortController() { return _currentFreeConvAbortController; }
export function setCurrentFreeConvAbortController(c) { _currentFreeConvAbortController = c; }

// ============================================================================
// Pure helpers (co-migrated for render fn callers; news-search.js keeps a
//   window.escapeHTML bridge for backward compat — commit 19/25 sweeps)
// ============================================================================

export function escapeHTML(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// Convert markdown-style links to HTML and preserve HTML line breaks.
// Converts [來源](url) to clickable <a> tags while keeping <br> tags intact.
export function convertMarkdownToHtml(text) {
    if (!text) return '';

    // First escape any potentially dangerous HTML except <br> tags
    let safe = text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");

    // Restore <br> tags
    safe = safe.replace(/&lt;br&gt;/g, "<br>");

    // Convert markdown links [text](url) to HTML <a> tags
    safe = safe.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function(match, text, url) {
        const decodedUrl = url
            .replace(/&amp;/g, "&")
            .replace(/&lt;/g, "<")
            .replace(/&gt;/g, ">");

        return `<a href="${decodedUrl}" class="source-link" target="_blank" rel="noopener noreferrer">${text}</a>`;
    });

    return safe;
}

// ============================================================================
// 12 simple helper functions (was: news-search.js lines 3017-3045, 3253-3486,
//   3541-3686)
// ============================================================================

// Cancel any in-flight search to prevent stale results from corrupting UI
export function cancelActiveSearch() {
    _searchGenerationId++;
    if (_currentSearchAbortController) {
        _currentSearchAbortController.abort();
        _currentSearchAbortController = null;
    }
    if (_currentSearchEventSource) {
        _currentSearchEventSource.close();
        _currentSearchEventSource = null;
    }
    const loadingState = document.getElementById('loadingState');
    if (loadingState) loadingState.classList.remove('active');
}

// Bug #23: Cancel all active requests across all modes (search, DR, FC)
export function cancelAllActiveRequests() {
    cancelActiveSearch();
    if (_currentDeepResearchAbortController) {
        _currentDeepResearchAbortController.abort();
        _currentDeepResearchAbortController = null;
    }
    // Legacy: close EventSource if somehow still set
    if (_currentDeepResearchEventSource) {
        _currentDeepResearchEventSource.close();
        _currentDeepResearchEventSource = null;
    }
    if (_currentFreeConvAbortController) {
        _currentFreeConvAbortController.abort();
        _currentFreeConvAbortController = null;
    }
    // Reset UI loading states
    const loadingState = document.getElementById('loadingState');
    if (loadingState) loadingState.classList.remove('active');
    const chatLoadingEl = document.getElementById('chatLoading');
    if (chatLoadingEl) chatLoadingEl.classList.remove('active');
}

// Show interrupted search notice with retry button
export function showInterruptedSearchNotice(query, mode) {
    const resultsSection = document.getElementById('resultsSection');
    const initialState = document.getElementById('initialState');
    if (resultsSection) resultsSection.classList.add('active');
    if (initialState) initialState.style.display = 'none';

    const modeLabels = { 'search': '搜尋', 'deep_research': '深度研究', 'chat': '對話' };
    const modeLabel = modeLabels[mode] || '搜尋';

    // Insert notice at top of listView (don't clear existing results)
    const listView = document.getElementById('listView');
    if (listView) {
        const existing = document.getElementById('interrupted-search-notice');
        if (existing) existing.remove();

        const notice = document.createElement('div');
        notice.id = 'interrupted-search-notice';
        notice.style.cssText = 'text-align: center; padding: 24px 20px; margin-bottom: 16px; background: #FFFDF5; border-radius: 8px; border: 1px solid #B2BEC3;';

        const title = document.createElement('div');
        title.style.cssText = 'font-size: 15px; margin-bottom: 8px; color: #2D3436;';
        title.textContent = `${modeLabel}被中斷`;

        const queryDisplay = document.createElement('div');
        queryDisplay.style.cssText = 'font-size: 13px; margin-bottom: 14px; color: #B2BEC3;';
        queryDisplay.textContent = `「${query}」`;

        const retryBtn = document.createElement('button');
        retryBtn.style.cssText = 'padding: 8px 20px; background: #FDCB6E; color: #2D3436; border: none; border-radius: 6px; cursor: pointer; font-size: 14px;';
        retryBtn.textContent = `重新${modeLabel}`;
        retryBtn.addEventListener('click', () => {
            // Clear interrupted state
            const idx = getSavedSessions().findIndex(s => window.matchSessionId(s.id, getCurrentLoadedSessionId()));
            if (idx !== -1) {
                delete getSavedSessions()[idx].interruptedSearch;
                localStorage.setItem('taiwanNewsSavedSessions', JSON.stringify(getSavedSessions()));
            }
            // Remove notice
            notice.remove();
            // Trigger search (mode already restored by loadSavedSession)
            const searchBtn = document.getElementById('btnSearch');
            if (searchBtn) searchBtn.click();
        });

        notice.appendChild(title);
        notice.appendChild(queryDisplay);
        notice.appendChild(retryBtn);
        listView.prepend(notice);
    }
}

// Clear all query-related UI state before starting a new search
export function clearQueryState() {
    // Clear deep research state
    clearResearchReport();
    clearArgumentGraph();
    clearChainAnalysis();

    // Clear research view DOM
    const researchViewEl = document.getElementById('researchView');
    if (researchViewEl) researchViewEl.innerHTML = '';

    // Clear retrieval notice banners (time filter / relevance A / keyword B / empty):
    // they are per-query verdicts inserted at the top of #resultsSection and must not
    // survive into the next query's results (stale "no results" over 60 fresh cards)
    const timeWarning = document.getElementById('timeFilterWarning');
    if (timeWarning) timeWarning.remove();
    for (const staleNoticeId of ['lowRelevanceWarning', 'lowKeywordMatchWarning', 'emptyResultsNotice']) {
        const staleNotice = document.getElementById(staleNoticeId);
        if (staleNotice) staleNotice.remove();
    }

    // Clear time_filter_relaxed notification
    const relaxedNotice = document.getElementById('timeFilterRelaxedNotice');
    if (relaxedNotice) relaxedNotice.remove();

    // Clear AI summary content
    const aiSummaryContent = document.getElementById('aiSummaryContent');
    if (aiSummaryContent) aiSummaryContent.innerHTML = '';

    // Hide AI summary section
    const aiSummarySection = document.getElementById('aiSummarySection');
    if (aiSummarySection) aiSummarySection.style.display = 'none';

    // Reset summary expand/collapse button state
    _summaryExpanded = false;
    const btnToggleSummary = document.getElementById('btnToggleSummary');
    if (btnToggleSummary) {
        btnToggleSummary.textContent = '📝 展開摘要';
        btnToggleSummary.classList.remove('expanded');
    }
}

// Bug #23: UI state machine — toggle between idle and processing states
export function setProcessingState(isProcessing) {
    const searchBtn = document.getElementById('btnSearch');
    const stopBtn = document.getElementById('btnStopGenerate');
    const searchInput = document.getElementById('searchInput');
    if (isProcessing) {
        if (searchBtn) searchBtn.style.display = 'none';
        if (stopBtn) stopBtn.style.display = '';
        // Disable Enter key submission during processing
        if (searchInput) searchInput.dataset.processing = 'true';
    } else {
        if (searchBtn) searchBtn.style.display = '';
        if (stopBtn) stopBtn.style.display = 'none';
        if (searchInput) searchInput.dataset.processing = '';
    }
}

// === Progressive Rendering Functions ===

// Render skeleton placeholder cards
export function renderSkeletonCards(count = 5) {
    const listView = document.getElementById('listView');
    if (!listView) return;

    listView.innerHTML = '';
    for (let i = 0; i < count; i++) {
        const skeleton = document.createElement('div');
        skeleton.className = 'skeleton-card';
        skeleton.innerHTML = `
            <div class="skeleton-line skeleton-title"></div>
            <div class="skeleton-line skeleton-meta"></div>
            <div class="skeleton-line skeleton-excerpt"></div>
            <div class="skeleton-line skeleton-excerpt"></div>
        `;
        listView.appendChild(skeleton);
    }
    console.log(`[Progressive] Rendered ${count} skeleton cards`);
}

// Render skeleton + typing indicator for AI summary area
export function renderSummarySkeleton() {
    const aiSummarySection = document.getElementById('aiSummarySection');
    const aiSummaryContent = document.getElementById('aiSummaryContent');
    if (!aiSummarySection || !aiSummaryContent) return;

    aiSummaryContent.innerHTML = `
        <div class="skeleton-summary">
            <div class="skeleton-line skeleton-summary-header"></div>
            <div class="skeleton-line skeleton-summary-line"></div>
            <div class="skeleton-line skeleton-summary-line"></div>
            <div class="skeleton-line skeleton-summary-line"></div>
        </div>
        <div class="ai-typing-indicator" id="progressIndicator">
            <div class="ai-typing-dot"></div>
            <div class="ai-typing-dot"></div>
            <div class="ai-typing-dot"></div>
            <span id="progressMessage" style="margin-left: 8px; color: #2D3436;">正在處理您的查詢...</span>
        </div>
    `;
    aiSummarySection.style.display = 'block';
    console.log('[Progressive] Rendered summary skeleton');
}

// Update progress indicator message
export function updateProgressMessage(message) {
    const progressMsg = document.getElementById('progressMessage');
    if (progressMsg) {
        progressMsg.textContent = message;
        console.log('[Progressive] Updated progress message:', message);
    }
}

// Create a single article card DOM element
export function createArticleCard(article, index) {
    const schema = article.schema_object || article;
    let rawScore = article.score || article.metadata?.score || 0;

    const relevancePercent = rawScore > 1 ? Math.round(rawScore) : Math.round(rawScore * 100);
    const normalizedScore = rawScore > 1 ? rawScore / 100 : rawScore;
    const stars = Math.min(5, Math.max(1, Math.round(normalizedScore * 5)));
    const starsHTML = '★'.repeat(stars) + '☆'.repeat(5 - stars);

    const title = schema.headline || schema.name || '無標題';

    let publisher = '未知來源';
    const _pub2 = schema.publisher;
    if (typeof _pub2 === 'object' && _pub2?.name) {
        publisher = _pub2.name;
    } else if (typeof _pub2 === 'string' && _pub2) {
        publisher = _pub2;
    } else if (article.site && getSourceDisplayNames()[article.site]) {
        publisher = getSourceDisplayNames()[article.site];
    } else if (article.site) {
        publisher = article.site.charAt(0).toUpperCase() + article.site.slice(1);
    } else if (schema.author) {
        if (Array.isArray(schema.author) && schema.author.length > 0) {
            publisher = schema.author[0].name || schema.author[0];
        } else if (typeof schema.author === 'string') {
            publisher = schema.author;
        }
    }

    const datePublished = schema.datePublished || new Date().toISOString();
    const date = new Date(datePublished).toISOString().split('T')[0];
    const description = schema.description || article.description || '';
    const url = schema.url || '#';
    // text fragment verbatim quote（Task 2 後端帶 snake_case matched_text；舊 payload 無此 key → '' → 降級裸 URL）
    const matchedText = schema.matched_text || '';
    const { href: readHref, textfrag } = buildCitationHref({ url, quote: matchedText });
    const isPinned = getPinnedNewsCards().some(p => p.url === url);

    const card = document.createElement('div');
    card.className = 'news-card progressive-fade-in';
    card.setAttribute('data-url', url);
    card.setAttribute('data-title', title);
    card.setAttribute('data-description', description);

    card.innerHTML = `
        <div class="news-title">${escapeHTML(title)}</div>
        <div class="news-meta">
            <span>🏢 ${escapeHTML(publisher)}</span>
            <span>📅 ${date}</span>
            <div class="relevance">
                <span class="stars">${starsHTML}</span>
                <span>相關度 ${relevancePercent}%</span>
            </div>
        </div>
        ${description ? `<div class="news-excerpt">${escapeHTML(description)}</div>` : ''}
        <div class="news-card-footer">
            <a href="${escapeHtmlAttr(readHref)}" class="btn-read-more" target="_blank" rel="noopener noreferrer" data-textfrag="${escapeHtmlAttr(textfrag)}">閱讀全文 →</a>
            <button class="news-card-pin ${isPinned ? 'pinned' : ''}" title="${isPinned ? '取消釘選' : '釘選新聞'}">📌</button>
        </div>
    `;

    return { card, date, title, publisher, description, url, matchedText, starsHTML, relevancePercent, isPinned };
}

// Progressively render articles replacing skeletons
export function renderArticlesProgressive(articles) {
    const listView = document.getElementById('listView');
    const timelineView = document.getElementById('timelineView');
    if (!listView) return;

    // Clear skeleton cards
    listView.innerHTML = '';
    if (timelineView) timelineView.innerHTML = '';

    if (!Array.isArray(articles) || articles.length === 0) {
        listView.innerHTML = '<div class="news-card"><div class="news-title">沒有找到相關文章</div></div>';
        console.warn('[Progressive] No articles to render (articles is not an array or is empty):', typeof articles);
        return;
    }

    // Sort by score
    articles.sort((a, b) => {
        const scoreA = a.score || a.metadata?.score || 0;
        const scoreB = b.score || b.metadata?.score || 0;
        return scoreB - scoreA;
    });

    const articlesByDate = {};

    articles.forEach((article, index) => {
        const { card, date, title, publisher, description, url, matchedText, starsHTML, relevancePercent, isPinned } = createArticleCard(article, index);
        listView.appendChild(card);

        // Group for timeline
        if (!articlesByDate[date]) {
            articlesByDate[date] = [];
        }
        articlesByDate[date].push({ title, publisher, description, url, matchedText, starsHTML, relevancePercent, isPinned });
    });

    // Populate timeline view
    if (timelineView) {
        const sortedDates = Object.keys(articlesByDate).sort().reverse();
        sortedDates.forEach(date => {
            const dateArticles = articlesByDate[date];
            const timelineHTML = `
                <div class="timeline-date">
                    <div class="timeline-dot"></div>
                    <div class="date-label">${date}</div>
                    ${dateArticles.map(art => {
                        const { href: readHref, textfrag } = buildCitationHref({ url: art.url, quote: art.matchedText || '' });
                        return `
                        <div class="news-card progressive-fade-in" data-url="${escapeHTML(art.url)}" data-title="${escapeHTML(art.title)}">
                            <div class="news-title">${escapeHTML(art.title)}</div>
                            <div class="news-meta">
                                <span>🏢 ${escapeHTML(art.publisher)}</span>
                                <div class="relevance">
                                    <span class="stars">${art.starsHTML}</span>
                                    <span>相關度 ${art.relevancePercent}%</span>
                                </div>
                            </div>
                            ${art.description ? `<div class="news-excerpt">${escapeHTML(art.description)}</div>` : ''}
                            <div class="news-card-footer">
                                <a href="${escapeHtmlAttr(readHref)}" class="btn-read-more" target="_blank" rel="noopener noreferrer" data-textfrag="${escapeHtmlAttr(textfrag)}">閱讀全文 →</a>
                                <button class="news-card-pin ${art.isPinned ? 'pinned' : ''}" title="${art.isPinned ? '取消釘選' : '釘選新聞'}">📌</button>
                            </div>
                        </div>
                    `;
                    }).join('')}
                </div>
            `;
            timelineView.innerHTML += timelineHTML;
        });
    }

    console.log(`[Progressive] Rendered ${articles.length} articles`);
}

// Progressively render AI answer (supports initial + enriched updates)
export function renderAnswerProgressive(answerData, articleCount) {
    const aiSummarySection = document.getElementById('aiSummarySection');
    const aiSummaryContent = document.getElementById('aiSummaryContent');
    if (!aiSummarySection || !aiSummaryContent) return;

    if (!answerData || !answerData.answer) {
        aiSummarySection.style.display = 'none';
        return;
    }

    // Use articleCount from current search's accumulatedData (not DOM which may have stale cards)
    const displayCount = articleCount || 0;

    const formattedAnswer = convertMarkdownToHtml(answerData.answer);
    const isUpdate = aiSummaryContent.querySelector('.summary-content') !== null &&
                     !aiSummaryContent.querySelector('.skeleton-summary');

    const sourceInfoText = displayCount > 0
        ? `讀豹基於 ${displayCount} 則報導生成`
        : `讀豹生成回答（未找到直接相關報導）`;

    aiSummaryContent.innerHTML = `
        <div class="summary-section ${isUpdate ? 'content-updated' : 'progressive-fade-in'}">
            <div class="summary-content">${formattedAnswer}</div>
        </div>
        <div class="summary-footer">
            <div class="source-info">${sourceInfoText}</div>
            <div class="feedback-buttons">
                <button class="btn-feedback" data-rating="positive">👍 有幫助</button>
                <button class="btn-feedback" data-rating="negative">👎 不準確</button>
            </div>
        </div>
    `;
    aiSummarySection.style.display = 'block';

    console.log(`[Progressive] Rendered AI answer (update: ${isUpdate})`);
}

// Clear all loading states
export function clearLoadingStates() {
    const loadingState = document.getElementById('loadingState');
    if (loadingState) loadingState.classList.remove('active');

    // Remove any remaining skeleton elements
    document.querySelectorAll('.skeleton-card, .skeleton-summary, .ai-typing-indicator').forEach(el => el.remove());

    console.log('[Progressive] Cleared all loading states');
}

// ============================================================================
// v4.0 Commit 14b (2026-05-25, Phase 8) — Heavy SSE entry: performSearch +
//   handleStreamingRequest + handlePostStreamingRequest + populateResultsFromAPI
//   + showMemoryNotification + showTimeFilterRelaxedWarning + showSummaries +
//   hideSummaries migration from news-search.js.
//
// R1 SSE precondition: edit-time + gate-time both verified all 5 inflight
//   handles null before migration. Cross-module imports added above
//   (D-V6 relax — all single-direction read).
//
// Window bridges accessed (news-search.js still owns these — sweep commit 19/25):
//   - window.analyticsTracker (AnalyticsTrackerSSE instance — top-level const)
//   - window.authManager (auth-manager.js singleton — main.js attaches)
//   - window.saveCurrentSession (KEEP-in-place per CEO #5 until commit 23)
//   - window.matchSessionId (KEEP-in-place per CEO #5)
//   - window.getAdvancedSearchConfirmed (commit 14b — new bridge for performSearch
//     to read the news-search.js-owned advancedSearchConfirmed let; DR mode
//     gate; cleanest until commit 19 moves to a settings module)
//   - window.performDeepResearch / performLiveResearch / performFreeConversation
//     (commit 14b — new bridges, performSearch delegates to these when mode !=
//     'search'. Removed when commits 15 / batch-5'' migrate those entries.)
//   - window.showAdvancedPopup (KEEP-in-place — performSearch DR-not-confirmed gate)
//   - window.renderConversationHistory (KEEP-in-place until commit 18 sessions-list
//     UI migration)
//   - window.showDRError (KEEP-in-place until commit 15)
// ============================================================================

// Function to show in-page memory notification (5-second auto-fade)
export function showMemoryNotification(itemToRemember) {
    const notification = document.createElement('div');
    notification.className = 'memory-notification';
    notification.innerHTML = `
        <span class="memory-icon"><img src="/static/images/icon-memory.svg" alt="記住" class="inline-icon"></span>
        <span class="memory-text">我會記住：「${escapeHTML(itemToRemember)}」</span>
    `;

    const resultsSection = document.getElementById('resultsSection');
    let notificationArea = document.getElementById('memoryNotificationArea');
    if (!notificationArea) {
        notificationArea = document.createElement('div');
        notificationArea.id = 'memoryNotificationArea';
        notificationArea.style.cssText = 'margin-bottom: 20px;';
        if (resultsSection) resultsSection.insertBefore(notificationArea, resultsSection.firstChild);
    }

    notificationArea.appendChild(notification);

    setTimeout(() => {
        notification.style.opacity = '0';
        setTimeout(() => notification.remove(), 300);
    }, 5000);
}

// Function to show time filter relaxed warning banner
export function showTimeFilterRelaxedWarning(message) {
    const existing = document.getElementById('timeFilterWarning');
    if (existing) existing.remove();

    const warning = document.createElement('div');
    warning.id = 'timeFilterWarning';
    warning.className = 'time-filter-warning';
    warning.innerHTML = `<span class="warning-text">${escapeHTML(message)}</span>`;

    const resultsSection = document.getElementById('resultsSection');
    if (resultsSection) resultsSection.insertBefore(warning, resultsSection.firstChild);
}

export function showLowRelevanceWarning(message) {
    const existing = document.getElementById('lowRelevanceWarning');
    if (existing) existing.remove();

    const warning = document.createElement('div');
    warning.id = 'lowRelevanceWarning';
    warning.className = 'low-relevance-warning';
    warning.innerHTML = `<span class="warning-text">${escapeHTML(message)}</span>`;

    const resultsSection = document.getElementById('resultsSection');
    if (resultsSection) resultsSection.insertBefore(warning, resultsSection.firstChild);
}

export function showLowKeywordMatchWarning(message) {
    const existing = document.getElementById('lowKeywordMatchWarning');
    if (existing) existing.remove();

    const warning = document.createElement('div');
    warning.id = 'lowKeywordMatchWarning';
    warning.className = 'low-keyword-match-warning';
    warning.innerHTML = `<span class="warning-text">${escapeHTML(message)}</span>`;

    const resultsSection = document.getElementById('resultsSection');
    if (resultsSection) resultsSection.insertBefore(warning, resultsSection.firstChild);
}

// Empty-result honest notice (CDE plan §E): neutral info tone, not a warning —
// an empty corpus hit is a fact, not an error. Mutually exclusive server-side
// with the A/B warnings (never fire on empty sets) and author_search_no_results.
export function showEmptyResultsNotice(message) {
    const existing = document.getElementById('emptyResultsNotice');
    if (existing) existing.remove();

    const notice = document.createElement('div');
    notice.id = 'emptyResultsNotice';
    notice.className = 'empty-results-notice';
    notice.innerHTML = `<span class="warning-text">${escapeHTML(message)}</span>`;

    const resultsSection = document.getElementById('resultsSection');
    if (resultsSection) resultsSection.insertBefore(notice, resultsSection.firstChild);
}

// Function to populate UI from API response (used by loadSavedSession to render
//   restored search results; performSearch uses progressive rendering callbacks
//   instead but this entry remains for the session-restore path)
export function populateResultsFromAPI(data, query) {
    // Get articles from response - prioritize content/results for summarize mode.
    // Defensive type check: hydrated PG sessions may contain SSE message envelopes
    // where `data.content` is a string (e.g. "Asking ") rather than an array.
    // Pre-Array.isArray fallback prevents `articles.sort is not a function` crashes
    // when clicking into legacy/incomplete sessions.
    const safeData = data || {};
    let articles = null;
    if (Array.isArray(safeData.content)) {
        articles = safeData.content;
    } else if (Array.isArray(safeData.results)) {
        articles = safeData.results;
    } else if (safeData.nlws && Array.isArray(safeData.nlws.items)) {
        articles = safeData.nlws.items;
    } else {
        if (safeData.content !== undefined || safeData.results !== undefined) {
            console.warn('[populateResultsFromAPI] data fields are not arrays; falling back to []. Likely a hydrated SSE envelope. shape=',
                { contentType: typeof safeData.content, resultsType: typeof safeData.results, message_type: safeData.message_type });
        }
        articles = [];
    }

    const aiSummarySection = document.getElementById('aiSummarySection');
    const aiSummaryContent = document.getElementById('aiSummaryContent');
    const listView = document.getElementById('listView');
    const timelineView = document.getElementById('timelineView');

    if (!aiSummarySection || !aiSummaryContent) {
        console.warn('[populateResultsFromAPI] aiSummarySection or aiSummaryContent not found in DOM');
    } else if (data.nlws && data.nlws.answer) {
        const formattedAnswer = convertMarkdownToHtml(data.nlws.answer);
        aiSummaryContent.innerHTML = `
            <div class="summary-section">
                <div class="summary-content">${formattedAnswer}</div>
            </div>
            <div class="summary-footer">
                <div class="source-info">讀豹基於 ${articles.length} 則報導生成</div>
                <div class="feedback-buttons">
                    <button class="btn-feedback" data-rating="positive"><img src="/static/images/icon-good.svg" alt="有幫助" class="inline-icon"> 有幫助</button>
                    <button class="btn-feedback" data-rating="negative"><img src="/static/images/icon-bad.svg" alt="不準確" class="inline-icon"> 不準確</button>
                </div>
            </div>
        `;
        aiSummarySection.style.display = 'block';
    } else if (data.summary && data.summary.message) {
        aiSummaryContent.innerHTML = `
            <div class="summary-section">
                <div class="summary-content">${escapeHTML(data.summary.message)}</div>
            </div>
            <div class="summary-footer">
                <div class="source-info">讀豹基於 ${articles.length} 則報導生成</div>
                <div class="feedback-buttons">
                    <button class="btn-feedback" data-rating="positive"><img src="/static/images/icon-good.svg" alt="有幫助" class="inline-icon"> 有幫助</button>
                    <button class="btn-feedback" data-rating="negative"><img src="/static/images/icon-bad.svg" alt="不準確" class="inline-icon"> 不準確</button>
                </div>
            </div>
        `;
        aiSummarySection.style.display = 'block';
    } else {
        if (aiSummarySection) aiSummarySection.style.display = 'none';
    }

    if (!listView) {
        console.warn('[populateResultsFromAPI] listView not found in DOM');
        return;
    }

    listView.innerHTML = '';
    if (timelineView) timelineView.innerHTML = '';

    if (articles.length === 0) {
        listView.innerHTML = '<div class="news-card"><div class="news-title">沒有找到相關文章</div></div>';
        console.warn('No articles found in API response');
        return;
    }

    const articlesByDate = {};

    articles.sort((a, b) => {
        const scoreA = a.score || a.metadata?.score || 0;
        const scoreB = b.score || b.metadata?.score || 0;
        return scoreB - scoreA;
    });

    articles.forEach((article, index) => {
        const schema = article.schema_object || article;
        let rawScore = article.score || article.metadata?.score || 0;
        const relevancePercent = rawScore > 1 ? Math.round(rawScore) : Math.round(rawScore * 100);
        const normalizedScore = rawScore > 1 ? rawScore / 100 : rawScore;
        const stars = Math.min(5, Math.max(1, Math.round(normalizedScore * 5)));
        const starsHTML = '★'.repeat(stars) + '☆'.repeat(5 - stars);

        const title = schema.headline || schema.name || '無標題';
        let publisher = '未知來源';
        const _pub = schema.publisher;
        if (typeof _pub === 'object' && _pub?.name) {
            publisher = _pub.name;
        } else if (typeof _pub === 'string' && _pub) {
            publisher = _pub;
        } else if (article.site && getSourceDisplayNames()[article.site]) {
            publisher = getSourceDisplayNames()[article.site];
        } else if (article.site) {
            publisher = article.site.charAt(0).toUpperCase() + article.site.slice(1);
        } else if (schema.author) {
            if (Array.isArray(schema.author) && schema.author.length > 0) {
                publisher = schema.author[0].name || schema.author[0];
            } else if (typeof schema.author === 'string') {
                publisher = schema.author;
            }
        }

        const datePublished = schema.datePublished || new Date().toISOString();
        const date = new Date(datePublished).toISOString().split('T')[0];
        const description = schema.description || article.description || '';
        const url = schema.url || '#';
        const matchedText = schema.matched_text || '';
        const { href: readHref, textfrag } = buildCitationHref({ url, quote: matchedText });
        const isPinned = getPinnedNewsCards().some(p => p.url === url);

        const cardHTML = `
            <div class="news-card" data-url="${escapeHTML(url)}" data-title="${escapeHTML(title)}" data-description="${escapeHTML(description)}">
                <div class="news-title">${escapeHTML(title)}</div>
                <div class="news-meta">
                    <span><img src="/static/images/icon-source.svg" alt="來源" class="inline-icon"> ${escapeHTML(publisher)}</span>
                    <span><img src="/static/images/icon-date.svg" alt="日期" class="inline-icon"> ${date}</span>
                    <div class="relevance">
                        <span class="stars">${starsHTML}</span>
                        <span>相關度 ${relevancePercent}%</span>
                    </div>
                </div>
                ${description ? `<div class="news-excerpt">${escapeHTML(description)}</div>` : ''}
                <div class="news-card-footer">
                    <a href="${escapeHtmlAttr(readHref)}" class="btn-read-more" target="_blank" rel="noopener noreferrer" data-textfrag="${escapeHtmlAttr(textfrag)}">閱讀全文 →</a>
                    <button class="news-card-pin ${isPinned ? 'pinned' : ''}" title="${isPinned ? '取消釘選' : '釘選新聞'}"><img src="/static/images/Icon_Pin.png" alt="" class="inline-icon"></button>
                </div>
            </div>
        `;

        listView.innerHTML += cardHTML;

        if (!articlesByDate[date]) {
            articlesByDate[date] = [];
        }
        articlesByDate[date].push({
            title, publisher, description, url, matchedText, starsHTML, relevancePercent, isPinned
        });
    });

    if (timelineView) {
        const sortedDates = Object.keys(articlesByDate).sort().reverse();
        sortedDates.forEach(date => {
            const dateArticles = articlesByDate[date];
            const timelineHTML = `
                <div class="timeline-date">
                    <div class="timeline-dot"></div>
                    <div class="date-label">${date}</div>
                    ${dateArticles.map(art => {
                        const { href: readHref, textfrag } = buildCitationHref({ url: art.url, quote: art.matchedText || '' });
                        return `
                        <div class="news-card" data-url="${escapeHTML(art.url)}" data-title="${escapeHTML(art.title)}">
                            <div class="news-title">${escapeHTML(art.title)}</div>
                            <div class="news-meta">
                                <span><img src="/static/images/icon-source.svg" alt="來源" class="inline-icon"> ${escapeHTML(art.publisher)}</span>
                                <div class="relevance">
                                    <span class="stars">${art.starsHTML}</span>
                                    <span>相關度 ${art.relevancePercent}%</span>
                                </div>
                            </div>
                            ${art.description ? `<div class="news-excerpt">${escapeHTML(art.description)}</div>` : ''}
                            <div class="news-card-footer">
                                <a href="${escapeHtmlAttr(readHref)}" class="btn-read-more" target="_blank" rel="noopener noreferrer" data-textfrag="${escapeHtmlAttr(textfrag)}">閱讀全文 →</a>
                                <button class="news-card-pin ${art.isPinned ? 'pinned' : ''}" title="${art.isPinned ? '取消釘選' : '釘選新聞'}"><img src="/static/images/Icon_Pin.png" alt="" class="inline-icon"></button>
                            </div>
                        </div>
                    `;
                    }).join('')}
                </div>
            `;
            timelineView.innerHTML += timelineHTML;
        });
    }
}

// Show/hide article summary excerpts in list + timeline views
export function showSummaries() {
    const listView = document.getElementById('listView');
    const timelineView = document.getElementById('timelineView');
    if (listView) {
        listView.querySelectorAll('.news-excerpt').forEach(excerpt => excerpt.classList.add('visible'));
    }
    if (timelineView) {
        timelineView.querySelectorAll('.news-excerpt').forEach(excerpt => excerpt.classList.add('visible'));
    }
}

export function hideSummaries() {
    const listView = document.getElementById('listView');
    const timelineView = document.getElementById('timelineView');
    if (listView) {
        listView.querySelectorAll('.news-excerpt').forEach(excerpt => excerpt.classList.remove('visible'));
    }
    if (timelineView) {
        timelineView.querySelectorAll('.news-excerpt').forEach(excerpt => excerpt.classList.remove('visible'));
    }
}

// ============================================================================
// SSE pipeline — handleStreamingRequest (legacy EventSource GET) +
//   handlePostStreamingRequest (POST fetch reader)
// ============================================================================

// @deprecated — legacy EventSource GET path. 無 live caller（2026-07-05 重驗：
//   全 repo 僅自身定義 + docs/comment 引用）。整支零世代 gate；若未來重新啟用，
//   必須比照 handlePostStreamingRequest 的 late-message generation gate 補上，
//   否則同一 SSE race 會在此路徑復現。Sweep 目標 commit 19/25。
export async function handleStreamingRequest(url, query) {
    return new Promise((resolve, reject) => {
        const eventSource = new EventSource(url);
        setCurrentSearchEventSource(eventSource); // Store for cancellation
        let accumulatedData = {};
        let memoryNotifications = [];

        eventSource.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                console.log('SSE message received:', data);

                // Trigger G (Task 11): deterministic envelope-level identity check.
                const currentUid = window.authManager?._user?.id || null;
                if (data && data.user_id) {
                    if (currentUid && data.user_id !== currentUid) {
                        console.warn(`[SSE:handleStreamingRequest] envelope discarded: data.user_id=${data.user_id} !== current=${currentUid}; aborting stream`);
                        try { eventSource.close(); } catch (_) {}
                        setCurrentSearchEventSource(null);
                        UserStateSync.runInitSync({ keepInviteToken: false }).catch(err =>
                            console.error('[SSE-abort] runInitSync failed:', err));
                        resolve(accumulatedData);
                        return;
                    }
                } else {
                    console.warn('[SSE:handleStreamingRequest] envelope missing user_id (expected after Task 2A); passing through without identity check.');
                }

                switch(data.message_type) {
                    case 'begin-nlweb-response':
                        if (data.query_id) {
                            setAnalyticsQueryId(data.query_id);
                            window.analyticsTracker?.startQuery(getAnalyticsQueryId(), data.query);
                            console.log('[Analytics] Using backend query_id:', getAnalyticsQueryId());
                        }
                        if (data.conversation_id) {
                            setCurrentConversationId(data.conversation_id);
                            console.log('[Conversation] Using backend conversation_id:', getCurrentConversationId());
                        }
                        break;

                    case 'remember':
                        if (data.item_to_remember) {
                            showMemoryNotification(data.item_to_remember);
                            memoryNotifications.push(data.item_to_remember);
                        }
                        break;

                    case 'intermediate_result':
                        // DR progress update — handler still in news-search.js until commit 15
                        if (typeof window.updateReasoningProgress === 'function') {
                            window.updateReasoningProgress(data);
                        }
                        break;

                    case 'clarification_required':
                        console.warn('[Clarification] Received clarification_required in regular search — not handled');
                        break;

                    case 'time_filter_relaxed':
                        console.warn('[Temporal] Time filter relaxed:', data.content);
                        showTimeFilterRelaxedWarning(data.content);
                        break;

                    case 'low_relevance_warning':
                        console.warn('[Relevance] Low relevance:', data.content);
                        showLowRelevanceWarning(data.content);
                        break;

                    case 'low_keyword_match_warning':
                        console.warn('[Relevance] Low keyword match:', data.content);
                        showLowKeywordMatchWarning(data.content);
                        break;

                    case 'author_search_no_results':
                        console.warn('[Author] No results for author:', data.content);
                        showTimeFilterRelaxedWarning(data.content);
                        break;

                    case 'empty_results':
                        console.warn('[Results] Empty result set:', data.content);
                        showEmptyResultsNotice(data.content);
                        break;

                    case 'complete':
                        console.log('Stream complete. Accumulated data:', accumulatedData);
                        eventSource.close();
                        setCurrentSearchEventSource(null);
                        resolve(accumulatedData);
                        break;

                    // Server-side intermediate envelopes — explicit skip so they do NOT
                    // fall through to the default Object.assign below. Black-list MUST
                    // stay in sync with server-side `_BAD_MESSAGE_TYPES` in
                    // code/python/core/session_service.py.
                    case 'asking_sites':
                    case 'tool_selection':
                    case 'decontextualization':
                    case 'pre_check_results':
                    case 'site_querying':
                    case 'tool_routing':
                    case 'research_phase':
                    case 'progress':
                    case 'end-nlweb-response':
                    case 'error':
                        console.debug('[SSE] ignoring intermediate envelope:', data.message_type);
                        break;

                    default:
                        console.warn('[SSE] default merge for message_type:', data.message_type, data);
                        Object.assign(accumulatedData, data);
                        break;
                }
            } catch (e) {
                console.error('Error parsing SSE message:', e);
            }
        };

        eventSource.onerror = (error) => {
            console.error('SSE error:', error);
            eventSource.close();
            setCurrentSearchEventSource(null);
            resolve(accumulatedData);
        };
    });
}

// Function to handle POST streaming requests (for large payloads like research reports)
// Bug #23: Added abortSignal parameter for cancellation support
// Progressive rendering: Added callbacks parameter for real-time UI updates
export async function handlePostStreamingRequest(url, body, query, abortSignal = null, callbacks = {}, generationToken = null) {
    const { onArticles, onSummary, onAnswer, onComplete, onProgress } = callbacks;
    const fetchOptions = {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream'
        },
        body: JSON.stringify(body)
    };
    if (abortSignal) fetchOptions.signal = abortSignal;
    // P1 E2E fix (2026-05-26): route through authenticatedFetch for 401→refresh→retry.
    // The JSON body is re-readable so the internal retry-after-refresh re-fetch works.
    const response = await window.authManager.authenticatedFetch(url, fetchOptions);

    if (response.status === 401) {
        // Token expired + refresh failed (authenticatedFetch already showed login modal).
        const err = new Error('登入已過期，請重新登入後再試。');
        err.httpStatus = 401;
        throw err;
    }

    if (response.status === 429 || response.status === 400 || response.status === 503) {
        let errorMsg = '請稍後再試';
        try {
            const errorData = await response.json();
            if (errorData.message) errorMsg = errorData.message;
        } catch (e) { /* ignore parse errors */ }
        const err = new Error(errorMsg);
        err.httpStatus = response.status;
        throw err;
    }

    if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let accumulatedData = {};
    let buffer = '';

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        // Late-message generation gate（層一）：本 stream 已被後續搜尋/切換 supersede
        // 時，停止消化剩餘 chunk。abort 後 reader loop 要等下一次 read() 才 throw，
        // 同一 chunk buffer 內多條 message 會在同步迴圈全部處理完——這道 gate 讓遲到
        // stream 在下一輪 read 前就退場，避免整批 stale message 灌進當前畫面。
        if (!isCurrentGeneration(generationToken, getSearchGenerationId())) {
            console.log('[POST SSE] stale generation, stopping reader loop (token', generationToken, '!= current', getSearchGenerationId(), ')');
            try { reader.cancel(); } catch (_) {}
            return accumulatedData;
        }

        buffer += decoder.decode(value, { stream: true });

        const messages = buffer.split('\n\n');
        buffer = messages.pop();

        for (const message of messages) {
            if (!message.trim()) continue;

            const lines = message.split('\n');
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        console.log('POST SSE message received:', data);

                        const currentUid = window.authManager?._user?.id || null;
                        if (data && data.user_id) {
                            if (currentUid && data.user_id !== currentUid) {
                                console.warn(`[SSE:handlePostStreamingRequest] envelope discarded: data.user_id=${data.user_id} !== current=${currentUid}; aborting stream`);
                                try { reader.cancel(); } catch (_) {}
                                UserStateSync.runInitSync({ keepInviteToken: false }).catch(err =>
                                    console.error('[SSE-abort] runInitSync failed:', err));
                                return accumulatedData;
                            }
                        } else {
                            console.warn('[SSE:handlePostStreamingRequest] envelope missing user_id (expected after Task 2A); passing through without identity check.');
                        }

                        // Late-message generation gate（層二）：即使同一 chunk buffer 內
                        // 多條 message 在層一 gate 通過後才被後續搜尋 supersede，這道逐
                        // message gate 確保 stale message 不會進 switch 觸發任何 inline
                        // sink（remember / *_warning / begin-nlweb-response shared-state /
                        // injection_blocked cancel）。這是「驗世代」不是「過濾 message
                        // type」——放行的 message 仍走原 switch，default Object.assign 黑名單
                        // 行為完全不變。
                        if (!isCurrentGeneration(generationToken, getSearchGenerationId())) {
                            console.log('[POST SSE] stale generation, skipping message_type:', data.message_type);
                            try { reader.cancel(); } catch (_) {}
                            return accumulatedData;
                        }

                        switch(data.message_type) {
                            case 'begin-nlweb-response':
                                if (data.query_id) {
                                    setAnalyticsQueryId(data.query_id);
                                    window.analyticsTracker?.startQuery(getAnalyticsQueryId(), data.query || query);
                                    console.log('[Analytics] Using backend query_id:', getAnalyticsQueryId());
                                }
                                if (data.conversation_id) {
                                    setCurrentConversationId(data.conversation_id);
                                    console.log('[Conversation] Using backend conversation_id:', getCurrentConversationId());
                                }
                                break;

                            case 'remember':
                                if (data.item_to_remember) {
                                    showMemoryNotification(data.item_to_remember);
                                }
                                break;

                            case 'time_filter_relaxed':
                                console.warn('[Temporal] Time filter relaxed:', data.content);
                                showTimeFilterRelaxedWarning(data.content);
                                break;

                            case 'low_relevance_warning':
                                console.warn('[Relevance] Low relevance:', data.content);
                                showLowRelevanceWarning(data.content);
                                break;

                            case 'low_keyword_match_warning':
                                console.warn('[Relevance] Low keyword match:', data.content);
                                showLowKeywordMatchWarning(data.content);
                                break;

                            case 'author_search_no_results':
                                console.warn('[Author] No results for author:', data.content);
                                showTimeFilterRelaxedWarning(data.content);
                                break;

                            case 'empty_results':
                                console.warn('[Results] Empty result set:', data.content);
                                showEmptyResultsNotice(data.content);
                                break;

                            case 'injection_blocked': {
                                // Guardrail P2-1: query blocked by injection detection
                                const blockMsg = data.message || '此查詢無法處理，請嘗試其他查詢方式。';
                                reader.cancel();
                                return Promise.reject(new Error(blockMsg));
                            }

                            case 'progress':
                                console.log('[Progress]', data.stage, data.message);
                                if (onProgress) onProgress(data.stage, data.message, data.percent);
                                break;

                            // Unified mode message types
                            case 'articles': {
                                let content = data.content;
                                if (typeof content === 'string') {
                                    try { content = JSON.parse(content); } catch(e) { content = []; }
                                }
                                if (!Array.isArray(content)) content = [];
                                accumulatedData.content = content;
                                if (onArticles) onArticles(accumulatedData.content);
                                break;
                            }
                            case 'summary':
                                accumulatedData.summary = { message: data.content };
                                if (onSummary) onSummary(accumulatedData.summary, accumulatedData.content?.length || 0);
                                break;
                            case 'answer':
                                accumulatedData.nlws = data.answer ? { answer: data.answer } : null;
                                if (onAnswer) onAnswer(accumulatedData.nlws, accumulatedData.content?.length || 0);
                                break;
                            case 'end-nlweb-response':
                                break;

                            case 'complete':
                                console.log('POST Stream complete. Accumulated data:', accumulatedData);
                                if (onComplete) onComplete(accumulatedData);
                                return accumulatedData;

                            // Server-side intermediate envelopes — explicit skip.
                            case 'asking_sites':
                            case 'tool_selection':
                            case 'decontextualization':
                            case 'pre_check_results':
                            case 'site_querying':
                            case 'tool_routing':
                            case 'research_phase':
                            case 'intermediate_result':
                            case 'clarification_required':
                            case 'error':
                                console.debug('[POST SSE] ignoring intermediate envelope:', data.message_type);
                                break;

                            default:
                                console.warn('[POST SSE] default merge for message_type:', data.message_type, data);
                                Object.assign(accumulatedData, data);
                                break;
                        }
                    } catch (e) {
                        console.error('Error parsing POST SSE message:', e, line);
                    }
                }
            }
        }
    }

    // Process any remaining content in buffer after stream ends.
    // Late-message generation gate：stream 已 done 但若本 stream 已被後續搜尋
    // supersede，這段重播（begin-nlweb-response 寫 conversation_id / analytics
    // query_id 等 shared state）必須跳過，否則 stale stream 的尾 buffer 會覆蓋當前
    // 對話 ID，導致後續追問接錯對話串。
    if (buffer.trim() && isCurrentGeneration(generationToken, getSearchGenerationId())) {
        const lines = buffer.split('\n');
        for (const line of lines) {
            if (line.startsWith('data: ')) {
                try {
                    const data = JSON.parse(line.slice(6));
                    console.log('POST SSE final buffer message:', data);
                    if (data.message_type === 'begin-nlweb-response' && data.query_id) {
                        setAnalyticsQueryId(data.query_id);
                        window.analyticsTracker?.startQuery(getAnalyticsQueryId(), data.query || query);
                        console.log('[Analytics] Using backend query_id (from final buffer):', getAnalyticsQueryId());
                    }
                    if (data.message_type === 'begin-nlweb-response' && data.conversation_id) {
                        setCurrentConversationId(data.conversation_id);
                    }
                    if (data.message_type === 'complete') {
                        if (onComplete) onComplete(accumulatedData);
                    }
                    if (data.message_type === 'articles') {
                        accumulatedData.content = data.content || [];
                        if (onArticles) onArticles(accumulatedData.content);
                    }
                    if (data.message_type === 'answer') {
                        accumulatedData.nlws = data.answer ? { answer: data.answer } : null;
                        if (onAnswer) onAnswer(accumulatedData.nlws, accumulatedData.content?.length || 0);
                    }
                    if (data.message_type === 'summary') {
                        accumulatedData.summary = { message: data.content };
                        if (onSummary) onSummary(accumulatedData.summary, accumulatedData.content?.length || 0);
                    }
                } catch (e) {
                    console.error('Error parsing final buffer SSE message:', e, line);
                }
            }
        }
    }

    return accumulatedData;
}

// ============================================================================
// performSearch — main search pipeline entry. Delegates to performFreeConversation
//   / performDeepResearch / performLiveResearch by mode (those entries still in
//   news-search.js — commit 14b uses window bridges; commits 15 + batch 5''
//   migrate the delegates).
// ============================================================================

export async function performSearch() {
    const searchInput = document.getElementById('searchInput');
    const query = searchInput?.value?.trim() || '';
    if (!query) return;

    // Bug #23: Cancel all active requests (search, DR, FC) before starting new search
    cancelAllActiveRequests();

    // Clear interrupted state since user is re-searching
    const currentIdx = getSavedSessions().findIndex(s => window.matchSessionId?.(s.id, getCurrentLoadedSessionId()));
    if (currentIdx !== -1 && getSavedSessions()[currentIdx].interruptedSearch) {
        delete getSavedSessions()[currentIdx].interruptedSearch;
    }

    clearQueryState();
    setProcessingState(true);
    const mySearchGeneration = getSearchGenerationId();
    setCurrentSearchAbortController(new AbortController());

    // Hide initial state and folder page
    const initialState = document.getElementById('initialState');
    if (initialState) initialState.style.display = 'none';
    const folderPageSearch = document.getElementById('folderPage');
    if (folderPageSearch) folderPageSearch.style.display = 'none';

    // Check current mode — delegate to chat / DR / LR entries (still in news-search.js
    //   via window bridges until commits 15 + batch 5'' migrate them)
    if (getCurrentMode() === 'chat') {
        if (typeof window.performFreeConversation === 'function') {
            await window.performFreeConversation(query);
        } else {
            console.error('[performSearch] chat mode: window.performFreeConversation missing');
            setProcessingState(false);
        }
        return;
    }

    if (getCurrentMode() === 'deep_research') {
        // 如果未確認進階搜尋設定，先彈出 popup
        const advConfirmed = window.getAdvancedSearchConfirmed?.() ?? true;
        if (!advConfirmed) {
            setProcessingState(false);
            window.showAdvancedPopup?.();
            return;
        }
        if (typeof window.performDeepResearch === 'function') {
            await window.performDeepResearch(query);
        } else {
            console.error('[performSearch] deep_research mode: window.performDeepResearch missing');
            setProcessingState(false);
        }
        return;
    }

    if (getCurrentMode() === 'live_research') {
        if (typeof window.performLiveResearch === 'function') {
            await window.performLiveResearch(query);
        } else {
            console.error('[performSearch] live_research mode: window.performLiveResearch missing');
            setProcessingState(false);
        }
        return;
    }

    // Search mode — unified SSE flow with progressive rendering
    const loadingState = document.getElementById('loadingState');
    const resultsSection = document.getElementById('resultsSection');
    if (loadingState) loadingState.classList.add('active');
    if (resultsSection) resultsSection.classList.add('active');
    renderSkeletonCards(5);
    renderSummarySkeleton();
    if (resultsSection) resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

    let userMessage; // declared outer-scope so catch handler can persist it

    try {
        const prevQueriesForThisTurn = [...getConversationHistory()];

        // Save session immediately on query submit (before waiting for results)
        pushConversationHistory(query);
        markSessionDirty();  // RCA Fix 1: new query is real new content
        window.saveCurrentSession?.();

        const body = {
            query: query,
            site: getSelectedSitesParam(),
            generate_mode: 'unified',
            streaming: 'true',
            session_id: getCurrentSessionId()
        };
        if (prevQueriesForThisTurn.length > 0) {
            body.prev = JSON.stringify(prevQueriesForThisTurn);
        }

        // Phase 4: Progressive rendering callbacks
        let lastReceivedArticles = [];
        const callbacks = {
            onProgress: (stage, message, percent) => {
                if (mySearchGeneration !== getSearchGenerationId()) return;
                updateProgressMessage(message);
            },
            onArticles: (articles) => {
                const safeArticles = Array.isArray(articles) ? articles : [];
                if (mySearchGeneration !== getSearchGenerationId()) return;
                lastReceivedArticles = safeArticles;
                console.log('[Progressive] Articles received:', safeArticles.length);
                renderArticlesProgressive(safeArticles);
                if (loadingState) loadingState.classList.remove('active');
            },
            onAnswer: (answerData, articleCount) => {
                if (mySearchGeneration !== getSearchGenerationId()) return;
                const listViewEl = document.getElementById('listView');
                const hasCards = listViewEl && listViewEl.querySelectorAll('.news-card:not(.skeleton-card)').length > 0;
                if (!hasCards && lastReceivedArticles.length > 0) {
                    console.log('[Progressive] Fallback: rendering articles missed by onArticles guard');
                    renderArticlesProgressive(lastReceivedArticles);
                }
                if (answerData?.answer) {
                    console.log('[Progressive] Answer received');
                    renderAnswerProgressive(answerData, articleCount);
                }
            },
            onComplete: () => {
                if (mySearchGeneration !== getSearchGenerationId()) return;
                console.log('[Progressive] Stream complete');
                clearLoadingStates();

                const listViewFinal = document.getElementById('listView');
                const actualCount = listViewFinal
                    ? listViewFinal.querySelectorAll('.news-card:not(.skeleton-card)').length
                    : 0;
                const sourceInfoEl = document.querySelector('#aiSummaryContent .source-info');
                if (sourceInfoEl) {
                    sourceInfoEl.textContent = actualCount > 0
                        ? `讀豹基於 ${actualCount} 則報導生成`
                        : `讀豹生成回答（未找到直接相關報導）`;
                    console.log(`[Progressive] source-info updated to ${actualCount} articles`);
                }
            }
        };

        const combinedData = await handlePostStreamingRequest(
            '/ask', body, query, getCurrentSearchAbortController().signal, callbacks, mySearchGeneration
        );

        // Stale check: user switched away during search
        if (mySearchGeneration !== getSearchGenerationId()) {
            console.log('[Search] Stale search discarded');
            return;
        }

        console.log('Unified Combined Data:', combinedData);

        // Store complete session data for this query
        getSessionHistory().push({
            query: query,
            data: combinedData,
            timestamp: Date.now()
        });

        // Accumulate articles from this search for chat mode
        if (Array.isArray(combinedData.content) && combinedData.content.length > 0) {
            const existingUrls = new Set(getAccumulatedArticles().map(art => art.url || art.schema_object?.url));
            const newArticles = combinedData.content.filter(art => {
                const url = art.url || art.schema_object?.url;
                return url && !existingUrls.has(url);
            });
            pushAccumulatedArticles(newArticles);
            console.log(`Accumulated ${newArticles.length} new articles, total: ${getAccumulatedArticles().length}`);
        }

        // Trim history if too long
        if (getConversationHistory().length > 10) {
            getConversationHistory().shift();
            getSessionHistory().shift();
        }

        window.renderConversationHistory?.();
        window.saveCurrentSession?.();

        setProcessingState(false);
    } catch (error) {
        setProcessingState(false);
        clearLoadingStates();
        if (error.name === 'AbortError' || mySearchGeneration !== getSearchGenerationId()) {
            console.log('[Search] Search cancelled or superseded');
            return;
        }
        console.error('Search failed:', error);
        const listViewErr = document.getElementById('listView');
        if (listViewErr) {
            if (error.name === 'TypeError' && error.message.includes('fetch')) {
                userMessage = '網路連線中斷，請檢查網路後重試';
            } else if (error.message.includes('timeout') || error.message.includes('Timeout')) {
                userMessage = '搜尋逾時，請稍後再試';
            } else if (error.message.includes('429')) {
                userMessage = '搜尋過於頻繁，請稍後再試';
            } else if (error.message.includes('503') || error.message.includes('500')) {
                userMessage = '系統暫時無法處理，請稍後再試';
            } else {
                userMessage = '搜尋時發生錯誤，請稍後再試';
            }
            listViewErr.innerHTML = `<div class="news-card"><div class="news-title">${escapeHTML(userMessage)}</div><div class="news-excerpt visible" style="font-size: 0.85em; color: var(--text-secondary);">${escapeHTML(error.message)}</div></div>`;
        }
        // Save failed search to sessionHistory so clicking into it shows "no results" + error message
        getSessionHistory().push({
            query: query,
            data: { content: [], nlws: { answer: userMessage } },
            timestamp: Date.now()
        });
        window.saveCurrentSession?.();
    }
}
