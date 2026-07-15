// static/js/features/chat.js
//
// D-1 Module Header — Chat Owner (state + functions — commit 17, Phase 8)
//
//   Owned state:
//     - _chatHistory (array of {role, content, timestamp, msgId} — free-conversation chat)
//     - _messageIdCounter (number — local counter for message IDs in addChatMessage)
//
//   Functions migrated (13 — commit 17, Phase 8):
//     Main entry:
//       - performFreeConversation (was news-search.js:4438)
//     Chat rendering:
//       - addChatMessage (was 4564)
//     Pin message management:
//       - togglePinMessage (was 4618)
//       - updatePinButtonState (was 4655)
//       - renderPinnedBanner (was 4668)
//       - truncateText (was 4731)
//       - scrollToMessage (was 4739)
//       - togglePinnedDropdown (was 4767)
//       - closePinnedDropdown (was 4780)
//       - initPinnedBanner (was 4790)
//     Pin news card management:
//       - togglePinNewsCard (was 4828)
//       - updateNewsCardPinState (was 4865)
//       - renderPinnedNewsList (was 4878)
//
//   Trigger writes (chatHistory):
//     - performFreeConversation handler pushes user + assistant messages
//     - resetConversation / loadSavedSession / deleteSavedSession reset chat
//     - UserStateSync.clearUserScopedState (IIFE) clears on logout
//
//   External read:
//     - chat render loops + sessions-list.js (loadSavedSession restores chatHistory)
//     - saveCurrentSession serializes via getChatHistory()
//
// D-3 Cross-Module Communication:
//   Static imports (commit 17):
//     - features/search.js — handlePostStreamingRequest (SSE POST for free-chat /ask)
//     - features/search.js — escapeHTML (utils — pure)
//     - features/search.js — setProcessingState, cancelAllActiveRequests
//     - features/search.js — getConversationHistory, getAccumulatedArticles, getCurrentConversationId
//     - features/search.js — getCurrentFreeConvAbortController, setCurrentFreeConvAbortController
//     - features/research.js — getResearchReport
//     - features/pins.js — getPinnedMessages, setPinnedMessages, getPinnedNewsCards, setPinnedNewsCards
//     - features/source-filters.js — getIncludePrivateSources
//     - features/sessions-list.js — getCurrentLoadedSessionId
//     - features/session-manager.js — markSessionDirty
//     - utils/analytics.js — getCurrentSessionId
//
//   Window bridges accessed (KEEP-in-place owners — sweep commits 24/25):
//     - window.getCurrentUserId (auth-ui — KEEP-in-place per CEO #5)
//     - window.saveCurrentSession (KEEP-in-place per CEO #5)
//
//   Circular import note: features/search.js does NOT import chat.js, so chat.js → search.js
//   single-direction is safe (D-V6 relax).
//
// D-13 Compliance:
//   No top-level side effects on import. Pure declarations + exports.
//
// v4.0 Commit 3 (2026-05-24): State-only migration.
// v4.0 Commit 17 (2026-05-25, Phase 8): 13 function bodies migrated from news-search.js.
//   Bridge removed: 1 — window.performFreeConversation (was news-search.js:4561).
//   features/search.js performSearch chat-mode delegate still uses window.performFreeConversation,
//   so we keep a re-bridge in news-search.js after import. Sweep at commit 25.

import {
    handlePostStreamingRequest,
    escapeHTML,
    setProcessingState, cancelAllActiveRequests, getSearchGenerationId, isCurrentGeneration,
    getConversationHistory, getAccumulatedArticles, getCurrentConversationId,
    getCurrentFreeConvAbortController, setCurrentFreeConvAbortController
} from './search.js?v=20260714a';
import { getResearchReport } from './research.js';
import {
    getPinnedMessages, setPinnedMessages,
    getPinnedNewsCards, setPinnedNewsCards
} from './pins.js';
import { getIncludePrivateSources } from './source-filters.js';
import { getCurrentLoadedSessionId } from './sessions-list.js';
import { markSessionDirty } from './session-manager.js';
import { getCurrentSessionId } from '../utils/analytics.js';

// ============================================================================
// Chat history state
// ============================================================================
let _chatHistory = [];

export function getChatHistory() {
    return _chatHistory;
}

export function setChatHistory(arr) {
    _chatHistory = Array.isArray(arr) ? arr : [];
}

export function clearChatHistory() {
    // Preserve array reference semantics (same as `_chatHistory.length = 0`).
    _chatHistory.length = 0;
}

export function pushChatHistory(msg) {
    _chatHistory.push(msg);
}

// ============================================================================
// Local module constants (chat-only — co-migrated)
// ============================================================================
let _messageIdCounter = 0;
const MAX_PINNED_MESSAGES = 5;
const MAX_PINNED_NEWS = 10;

// ============================================================================
// Main entry: free-conversation chat (POST /ask with research report context)
// ============================================================================

export async function performFreeConversation(query) {
    const searchInput = document.getElementById('searchInput');
    const chatMessagesEl = document.getElementById('chatMessages');

    // Add user message to chat
    addChatMessage('user', query);

    // Save session immediately on query submit (before waiting for response)
    if (typeof window.saveCurrentSession === 'function') window.saveCurrentSession();

    // Clear input
    if (searchInput) searchInput.value = '';

    // Show typing indicator in chat flow
    const typingDiv = document.createElement('div');
    typingDiv.className = 'chat-message assistant';
    typingDiv.id = 'chatTypingIndicator';
    typingDiv.innerHTML = `
        <div class="chat-message-header">讀豹</div>
        <div class="chat-message-bubble">
            <div class="chat-typing-indicator">
                <div class="dot"></div>
                <div class="dot"></div>
                <div class="dot"></div>
            </div>
        </div>
    `;
    if (chatMessagesEl) {
        chatMessagesEl.appendChild(typingDiv);
        chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
    }

    // Late-message generation gate token — function scope 宣告，供 try 尾端 caller guard
    // 與 catch guard 共用（catch 讀不到 try 內的 const）。
    let myChatGeneration = null;
    try {
        // Build conversation context
        const searchQueries = getConversationHistory().slice();
        const recentChatHistory = getChatHistory().slice(-4);
        const chatQueries = recentChatHistory.filter(msg => msg.role === 'user').map(msg => msg.content);
        const allPrevQueries = [...searchQueries, ...chatQueries];

        // Reference context for UI display
        let referenceContext = '';
        if (getAccumulatedArticles().length > 0) {
            referenceContext = `參考資料：基於 ${getAccumulatedArticles().length} 則新聞（來自 ${getConversationHistory().length} 次搜尋）`;
        }

        console.log('=== Free Conversation Debug ===');
        console.log('Current query:', query);
        console.log('All prev queries being sent:', allPrevQueries);
        const _rr = getResearchReport();
        if (_rr) {
            console.log('Research report length:', _rr.report?.length || 0);
        }

        // Build POST body - can handle unlimited size
        const requestBody = {
            query: query,
            site: 'all',
            generate_mode: 'generate',
            streaming: true,
            free_conversation: true,
            session_id: getCurrentSessionId(),
            conversation_id: getCurrentConversationId() || '',
            prev: allPrevQueries
        };

        // Add full research report if available (no truncation needed with POST)
        if (_rr && _rr.report) {
            requestBody.research_report = _rr.report;
            console.log('[Free Conversation] Passing full research report:', _rr.report.length, 'chars');
        }

        // Add pinned articles if any
        if (getPinnedNewsCards().length > 0) {
            requestBody.pinned_articles = getPinnedNewsCards().map(p => ({
                url: p.url, title: p.title, description: p.description || ''
            }));
        }

        // Add private sources parameters if enabled
        if (getIncludePrivateSources()) {
            requestBody.include_private_sources = true;
            if (typeof window.getCurrentUserId === 'function') {
                requestBody.user_id = window.getCurrentUserId();
            }
        }

        console.log('[Free Conversation] Using POST request with body size:', JSON.stringify(requestBody).length, 'bytes');

        // Bug #23: Cancel any previous active requests and create abort controller
        cancelAllActiveRequests();
        // Late-message generation gate：在 bump（cancelAllActiveRequests 內）之後賦值，
        // 與 performSearch 的 mySearchGeneration 捕捉時序對稱。chat 遲到 SSE 被後續
        // search/chat supersede 時，inline sink 靠此 token 攔截。
        myChatGeneration = getSearchGenerationId();
        setCurrentFreeConvAbortController(new AbortController());
        setProcessingState(true);

        // Use fetch with POST for streaming (handles large payloads)
        let chatData = await handlePostStreamingRequest('/ask', requestBody, query, getCurrentFreeConvAbortController().signal, {}, myChatGeneration);
        // Late-message generation gate（caller 端）：await 返回後本 chat 已被後續
        // search/chat supersede 時，下面整串 shared-state 寫入（controller / chat DOM /
        // processingState / saveCurrentSession）現在都屬於「接管的新請求」——stale 返回時
        // 一律不得碰。責任劃分：stale 時仍負責清理**自己建立的** typingDiv（否則同 id
        // 孤兒轉圈永久殘留），只跳過 shared-state 寫入。用 isCurrentGeneration 純函式：
        // null/undefined token = opt-out 放行（真錯誤照走既有處理），與 Task 1 語意統一。
        if (!isCurrentGeneration(myChatGeneration, getSearchGenerationId())) { typingDiv.remove(); return; }
        setCurrentFreeConvAbortController(null);

        // Remove typing indicator（清本函式自己建立的節點，避免同 id 重複造成孤兒）
        typingDiv.remove();

        // Add assistant response to chat
        if (chatData.answer) {
            addChatMessage('assistant', chatData.answer, referenceContext);
        } else {
            addChatMessage('assistant', '抱歉，我無法回答這個問題。');
        }
        setProcessingState(false); // Bug #23
        if (chatMessagesEl) chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;

        // 自動建立/更新 session
        if (typeof window.saveCurrentSession === 'function') window.saveCurrentSession();
    } catch (error) {
        // Late-message generation gate（catch 端）：被 supersede 的 stale chat 其 fetch
        // 被 abort → 走這裡。stale 時同樣不得碰 shared state（controller / processingState
        // / chat DOM）——那些屬於接管的新請求。責任劃分：stale 時仍清自己建立的 typingDiv
        // （防同 id 孤兒轉圈），只跳過 shared-state 寫入。用 isCurrentGeneration 純函式：
        // null/undefined token（錯誤發生在 myChatGeneration 賦值前）= opt-out 放行，讓真
        // 錯誤照走既有處理，不誤判 stale 靜默吞錯。
        if (!isCurrentGeneration(myChatGeneration, getSearchGenerationId())) { typingDiv.remove(); return; }
        setCurrentFreeConvAbortController(null);
        setProcessingState(false); // Bug #23
        // Remove typing indicator on error（清本函式自己建立的節點）
        typingDiv.remove();

        if (error.name === 'AbortError') {
            console.log('[Free Conversation] Request aborted');
            return;
        }
        console.error('Chat failed:', error);
        addChatMessage('assistant', error.message || '抱歉，發生錯誤。請稍後再試。');
    }
}

// ============================================================================
// Add message to chat UI
// ============================================================================

export function addChatMessage(role, content, referenceInfo = null, existingMsgId = null) {
    const chatMessagesEl = document.getElementById('chatMessages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `chat-message ${role}`;

    // Assign unique ID to message
    const msgId = existingMsgId || `msg-${Date.now()}-${_messageIdCounter++}`;
    messageDiv.setAttribute('data-msg-id', msgId);

    const roleIcon = role === 'user'
        ? '<img src="/static/images/icon-role-user.svg" alt="你" class="inline-icon">'
        : '<img src="/static/images/icon-role-dubao.svg" alt="讀豹" class="inline-icon">';
    const headerText = role === 'user' ? '你' : '讀豹';

    // For assistant messages, use marked.js for full Markdown rendering
    // For user messages, escape HTML for safety
    let formattedContent = content;
    if (role === 'assistant') {
        formattedContent = DOMPurify.sanitize(marked.parse(content));
    } else {
        formattedContent = escapeHTML(content);
    }

    // Check if this message is already pinned
    const isPinned = getPinnedMessages().some(p => p.msgId === msgId);

    let messageHTML = `
        <div class="chat-message-header">${roleIcon} ${headerText}</div>
        <div class="chat-message-content-wrapper">
            <div class="chat-message-bubble">${formattedContent}</div>
            <button class="chat-message-pin ${isPinned ? 'pinned' : ''}" data-msg-id="${msgId}" title="${isPinned ? '取消釘選' : '釘選訊息'}"><img src="/static/images/Icon_Pin.png" alt="" class="inline-icon"></button>
        </div>
    `;

    if (referenceInfo && role === 'assistant') {
        messageHTML += `<div class="chat-reference-info"><img src="/static/images/icon-citation.svg" alt="參考資訊" class="inline-icon"> ${referenceInfo}</div>`;
    }

    messageDiv.innerHTML = messageHTML;
    if (chatMessagesEl) chatMessagesEl.appendChild(messageDiv);

    // Add click handler for pin button
    const pinBtn = messageDiv.querySelector('.chat-message-pin');
    if (pinBtn) pinBtn.addEventListener('click', () => togglePinMessage(msgId, content, role));

    // Store in chat history with ID
    pushChatHistory({ role, content, timestamp: Date.now(), msgId });
    markSessionDirty();  // RCA Fix 1: any new chat message (user or assistant) is new content

    // Scroll to bottom
    if (chatMessagesEl) chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;

    return msgId;
}

// ============================================================================
// Pin message functions
// ============================================================================

export function togglePinMessage(msgId, content, role) {
    const _pins = getPinnedMessages();
    const existingIndex = _pins.findIndex(p => p.msgId === msgId);

    if (existingIndex !== -1) {
        // Unpin
        _pins.splice(existingIndex, 1);
        console.log('[Pin] Unpinned message:', msgId);
    } else {
        // Pin - enforce max limit
        if (_pins.length >= MAX_PINNED_MESSAGES) {
            // Remove oldest pinned message
            _pins.shift();
        }
        _pins.push({
            msgId,
            content,
            role,
            pinnedAt: Date.now()
        });
        console.log('[Pin] Pinned message:', msgId);
    }
    markSessionDirty();  // RCA Fix 1: pin/unpin mutates pinnedMessages = new content

    // Update pin button state
    updatePinButtonState(msgId);

    // Render the banner
    renderPinnedBanner();

    // 只在 session 已存在時才存檔（釘選不應建立新 session）
    if (getCurrentLoadedSessionId() !== null) {
        if (typeof window.saveCurrentSession === 'function') window.saveCurrentSession();
    }
}

// Update the visual state of a pin button
// Bug fix (P2 E2E finding): use scoped selector to avoid matching stale
// .pinned-dropdown-unpin elements in the pinned banner dropdown that share
// the same data-msg-id attribute. The banner dropdown is rendered AFTER
// togglePinMessage calls this function, so the broader selector can match
// the wrong (stale) element — querySelector returns null for .chat-message-pin
// inside a .pinned-dropdown-unpin, leaving the pin button visually stale
// until reload. Fix: scope to .chat-message to exclude banner elements.
export function updatePinButtonState(msgId) {
    const isPinned = getPinnedMessages().some(p => p.msgId === msgId);
    const messageEl = document.querySelector(`.chat-message[data-msg-id="${msgId}"]`);
    if (messageEl) {
        const pinBtn = messageEl.querySelector('.chat-message-pin');
        if (pinBtn) {
            pinBtn.classList.toggle('pinned', isPinned);
            pinBtn.title = isPinned ? '取消釘選' : '釘選訊息';
        }
    }
}

// Render the pinned messages banner
export function renderPinnedBanner() {
    const banner = document.getElementById('pinnedBanner');
    const bannerText = document.getElementById('pinnedBannerText');
    const bannerCount = document.getElementById('pinnedBannerCount');
    const bannerToggle = document.getElementById('pinnedBannerToggle');
    const bannerDropdown = document.getElementById('pinnedBannerDropdown');

    if (!banner) return;

    const _pinsForRender = getPinnedMessages();
    if (_pinsForRender.length === 0) {
        banner.style.display = 'none';
        return;
    }

    banner.style.display = 'block';

    // Show the latest pinned message
    const latestPinned = _pinsForRender[_pinsForRender.length - 1];
    const truncatedText = truncateText(latestPinned.content, 50);
    bannerText.textContent = truncatedText;

    // Update count
    bannerCount.textContent = _pinsForRender.length;
    bannerToggle.style.display = _pinsForRender.length > 1 ? 'flex' : 'none';

    // Render dropdown items
    bannerDropdown.innerHTML = '';
    _pinsForRender.slice().reverse().forEach((pinned, idx) => {
        const item = document.createElement('div');
        item.className = 'pinned-dropdown-item';

        const roleLabel = pinned.role === 'user' ? '你' : '讀豹';
        const truncated = truncateText(pinned.content, 40);

        item.innerHTML = `
            <span class="pinned-dropdown-role">${roleLabel}：</span>
            <span class="pinned-dropdown-text">${escapeHTML(truncated)}</span>
            <button class="pinned-dropdown-unpin" data-msg-id="${pinned.msgId}" title="取消釘選"><img src="/static/images/Icon_cancel.png" alt="" class="inline-icon"></button>
        `;

        // Click to scroll to message (dropdown stays open)
        item.addEventListener('click', (e) => {
            e.stopPropagation();
            if (!e.target.classList.contains('pinned-dropdown-unpin')) {
                console.log('[Pin] Scrolling to message:', pinned.msgId);
                scrollToMessage(pinned.msgId);
                // Don't close dropdown - user can close manually
            }
        });

        // Unpin button
        const unpinBtn = item.querySelector('.pinned-dropdown-unpin');
        unpinBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            togglePinMessage(pinned.msgId, pinned.content, pinned.role);
        });

        bannerDropdown.appendChild(item);
    });
}

// Truncate text to specified length
export function truncateText(text, maxLength) {
    // Get first line only
    const firstLine = text.split('\n')[0];
    if (firstLine.length <= maxLength) return firstLine;
    return firstLine.substring(0, maxLength) + '...';
}

// Scroll to a specific message (only scroll chat container, not the page)
export function scrollToMessage(msgId) {
    console.log('[Pin] Looking for message:', msgId);
    // Use specific selector to find chat-message div, not dropdown buttons
    const messageEl = document.querySelector(`.chat-message[data-msg-id="${msgId}"]`);
    const chatContainer = document.getElementById('chatMessages');
    console.log('[Pin] Found element:', messageEl);

    if (messageEl && chatContainer) {
        // Calculate scroll position within the chat container
        const containerRect = chatContainer.getBoundingClientRect();
        const messageRect = messageEl.getBoundingClientRect();
        const scrollOffset = messageRect.top - containerRect.top + chatContainer.scrollTop;

        // Smooth scroll only the chat container
        chatContainer.scrollTo({
            top: scrollOffset,
            behavior: 'smooth'
        });

        // Highlight briefly
        messageEl.classList.add('highlight');
        setTimeout(() => messageEl.classList.remove('highlight'), 2000);
    } else {
        console.warn('[Pin] Message element not found for id:', msgId);
    }
}

// Toggle pinned dropdown visibility
export function togglePinnedDropdown() {
    console.log('[Pin] Toggling dropdown');
    const dropdown = document.getElementById('pinnedBannerDropdown');
    const arrow = document.querySelector('.pinned-banner-arrow');
    if (dropdown) {
        const isVisible = dropdown.classList.toggle('visible');
        if (arrow) {
            arrow.textContent = isVisible ? '▲' : '▼';
        }
    }
}

// Close pinned dropdown
export function closePinnedDropdown() {
    const dropdown = document.getElementById('pinnedBannerDropdown');
    const arrow = document.querySelector('.pinned-banner-arrow');
    if (dropdown) {
        dropdown.classList.remove('visible');
        if (arrow) arrow.textContent = '▼';
    }
}

// Initialize pinned banner event listeners
export function initPinnedBanner() {
    console.log('[Pin] Initializing pinned banner');
    const bannerToggle = document.getElementById('pinnedBannerToggle');
    const bannerCurrent = document.getElementById('pinnedBannerCurrent');
    console.log('[Pin] bannerToggle:', bannerToggle);
    console.log('[Pin] bannerCurrent:', bannerCurrent);

    if (bannerToggle) {
        bannerToggle.addEventListener('click', (e) => {
            e.stopPropagation();
            togglePinnedDropdown();
        });
    }

    // Click on banner text to scroll to latest pinned
    if (bannerCurrent) {
        bannerCurrent.addEventListener('click', (e) => {
            console.log('[Pin] Banner clicked, target:', e.target);
            if (!e.target.closest('.pinned-banner-toggle')) {
                const _pins = getPinnedMessages();
                if (_pins.length > 0) {
                    const latestPinned = _pins[_pins.length - 1];
                    console.log('[Pin] Scrolling to latest pinned:', latestPinned.msgId);
                    scrollToMessage(latestPinned.msgId);
                }
            }
        });
    }

    // Dropdown only closes when toggle button is clicked manually
    // (removed auto-close on outside click)
}

// ============================================================================
// Pin news card functions
// ============================================================================

export function togglePinNewsCard(url, title, description) {
    const _pins = getPinnedNewsCards();
    const existingIndex = _pins.findIndex(p => p.url === url);

    if (existingIndex !== -1) {
        // Unpin
        _pins.splice(existingIndex, 1);
        console.log('[PinNews] Unpinned news:', url);
    } else {
        // Pin - enforce max limit
        if (_pins.length >= MAX_PINNED_NEWS) {
            // Remove oldest pinned news
            _pins.shift();
        }
        _pins.push({
            url,
            title,
            description: description || '',
            pinnedAt: Date.now()
        });
        console.log('[PinNews] Pinned news:', url);
    }
    markSessionDirty();  // RCA Fix 1: pin/unpin mutates pinnedNewsCards = new content

    // Update all pin button states for this URL
    updateNewsCardPinState(url);

    // Render the pinned news list
    renderPinnedNewsList();

    // 只在 session 已存在時才存檔（釘選不應建立新 session）
    if (getCurrentLoadedSessionId() !== null) {
        if (typeof window.saveCurrentSession === 'function') window.saveCurrentSession();
    }
}

// Update the visual state of pin buttons for a specific URL
export function updateNewsCardPinState(url) {
    const isPinned = getPinnedNewsCards().some(p => p.url === url);
    const cards = document.querySelectorAll(`.news-card[data-url="${CSS.escape(url)}"]`);
    cards.forEach(card => {
        const pinBtn = card.querySelector('.news-card-pin');
        if (pinBtn) {
            pinBtn.classList.toggle('pinned', isPinned);
            pinBtn.title = isPinned ? '取消釘選' : '釘選新聞';
        }
    });
}

// Render the pinned news list in the right tab panel
export function renderPinnedNewsList() {
    const listEl = document.getElementById('pinnedNewsList');
    if (!listEl) return;

    if (getPinnedNewsCards().length === 0) {
        listEl.innerHTML = '<div class="pinned-news-empty">尚未釘選任何新聞</div>';
        return;
    }

    listEl.innerHTML = getPinnedNewsCards().map(news => `
        <div class="pinned-news-item" data-url="${escapeHTML(news.url)}">
            <img src="/static/images/Icon_Pin.png" alt="" class="pinned-news-item-icon inline-icon">
            <span class="pinned-news-item-title">${escapeHTML(news.title)}</span>
            <button class="pinned-news-item-unpin" title="取消釘選"><img src="/static/images/Icon_cancel.png" alt="" class="inline-icon"></button>
        </div>
    `).join('');

    // Add event listeners
    listEl.querySelectorAll('.pinned-news-item').forEach(item => {
        const url = item.dataset.url;
        const news = getPinnedNewsCards().find(n => n.url === url);

        // Click to open link
        item.addEventListener('click', (e) => {
            if (!e.target.classList.contains('pinned-news-item-unpin')) {
                window.open(url, '_blank');
            }
        });

        // Unpin button
        const unpinBtn = item.querySelector('.pinned-news-item-unpin');
        if (unpinBtn && news) {
            unpinBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                togglePinNewsCard(news.url, news.title, news.description);
            });
        }
    });
}
