// static/js/features/folders.js
//
// D-1 Module Header — Folders Owner (state only — commit 8)
//   Owned state:
//     - _folders (array of {id, name, sessionIds, createdAt, updatedAt} — session folder model)
//     - _sourceFolders (array of {id, name, sites?, collapsed?} — source picker folders)
//     - _fileFolders (array of {id, name, files?, collapsed?} — file picker folders)
//     - _selectedFileIds (Set<string> — selected file source_id set for include-in-search)
//     - _folderModeActive (boolean — UI flag; true while session-folder management view is open)
//
//   Trigger writes:
//     - User-driven CRUD (createFolder / renameFolder / deleteFolder / addSessionToFolder /
//       removeSessionFromFolder / source-folder + file-folder edit handlers)
//     - localStorage hydrate on page init (load from 'taiwanNewsFolders' / SOURCE_FOLDERS_KEY /
//       FILE_FOLDERS_KEY / SELECTED_FILES_KEY)
//     - UserStateSync.clearUserScopedState (IIFE news-search.js) clears all 4 user-scoped arrays.
//       _folderModeActive is NOT user-scoped (it's a transient UI flag) but is wired through
//       getFolderModeActive() so other modules can check current state.
//     - hideFolderPage / showFolderPage toggle _folderModeActive.
//
//   External read:
//     - features/sessions-list.js drag-rebind branch checks getFolderModeActive() to
//       enable sidebar session drag handlers (was `window._folderModeActive` before commit 8).
//     - news-search.js folder CRUD + UI render functions (renderFolderGrid, renderSourceTree,
//       renderFileTree etc.) read via getX() helpers (live ref — supports .forEach / .find /
//       .filter / .findIndex / .splice / .push / .unshift / .length).
//
// D-3 Cross-Module Communication:
//   Static imports only. Pure leaf — no imports from other modules. Per D-V6,
//   features/sessions-list.js may import from this module (sessions-list reads
//   folder→session mapping for drag-rebind). This module does NOT import sessions-list.
//
// D-13 Compliance:
//   No top-level side effects. Only declarations + function definitions + exports.
//   localStorage hydrate is deferred — news-search.js calls the hydrate helpers at
//   the appropriate init time (we expose setSourceFolders / setFileFolders / setSelectedFileIds /
//   pushFolder etc. for the hydrate path to use).
//
// v4.0 Commit 8 (2026-05-24): State migration. Bridge removed: 1 (window._folderModeActive
//   getter at news-search.js:11029-11032). Folder CRUD + drag-rebind handlers stay in
//   news-search.js (Phase 8 sweep moves them to module).
//
// Note on persistFolders: `folders` (session folder model) has dedicated localStorage
// key 'taiwanNewsFolders' that is read by SessionManager + this module on init. Caller
// in news-search.js (saveFolders / hydrate path) uses persistFolders() helper for the
// canonical write, exported here to centralize the write site (per plan §3.8 hazard
// "verify localStorage write happens via persistFolders() helper").

// ============================================================================
// folders — session folder model (id / name / sessionIds / createdAt / updatedAt)
// ============================================================================
let _folders = [];

export function getFolders() {
    return _folders;
}

export function setFolders(arr) {
    _folders = Array.isArray(arr) ? arr : [];
}

export function pushFolder(f) {
    _folders.push(f);
}

export function removeFolder(folderId) {
    _folders = _folders.filter(f => f.id !== folderId);
}

export function clearFolders() {
    // Preserve array reference semantics so any live `.forEach` consumer keeps a valid ref.
    _folders.length = 0;
}

export function persistFolders() {
    try {
        localStorage.setItem('taiwanNewsFolders', JSON.stringify(_folders));
    } catch (e) {
        console.error('[folders] persistFolders failed:', e);
    }
}

// ============================================================================
// sourceFolders — source picker folder model
// ============================================================================
let _sourceFolders = [];

export function getSourceFolders() {
    return _sourceFolders;
}

export function setSourceFolders(arr) {
    _sourceFolders = Array.isArray(arr) ? arr : [];
}

export function clearSourceFolders() {
    _sourceFolders.length = 0;
}

// ============================================================================
// fileFolders — file picker folder model
// ============================================================================
let _fileFolders = [];

export function getFileFolders() {
    return _fileFolders;
}

export function setFileFolders(arr) {
    _fileFolders = Array.isArray(arr) ? arr : [];
}

export function clearFileFolders() {
    _fileFolders.length = 0;
}

// ============================================================================
// selectedFileIds — Set<string> of selected file source_id
// ============================================================================
let _selectedFileIds = new Set();

export function getSelectedFileIds() {
    return _selectedFileIds;
}

export function setSelectedFileIds(setOrIter) {
    _selectedFileIds = (setOrIter instanceof Set) ? setOrIter : new Set(setOrIter || []);
}

export function addSelectedFile(id) {
    _selectedFileIds.add(id);
}

export function removeSelectedFile(id) {
    _selectedFileIds.delete(id);
}

export function hasSelectedFile(id) {
    return _selectedFileIds.has(id);
}

export function clearSelectedFileIds() {
    _selectedFileIds.clear();
}

export function getSelectedFileCount() {
    return _selectedFileIds.size;
}

// ============================================================================
// _folderModeActive — UI flag (true while session-folder management view open)
// Not user-scoped — preserved across UserStateSync resets. Owned here so consumers
// in features/sessions-list.js can read via getFolderModeActive() instead of the
// removed window._folderModeActive bridge.
// ============================================================================
let _folderModeActive = false;

export function getFolderModeActive() {
    return _folderModeActive;
}

export function setFolderModeActive(b) {
    _folderModeActive = !!b;
}

// ============================================================================
// v4.0 Commit 20 (2026-05-25, Phase 8 part C): session-folder UI extend
//
// 16 functions migrated from news-search.js + 5 local lets co-migrated:
//   - currentFolderSort / currentFolderFilter / currentOpenFolderId /
//     openDropdownFolderId / _preFolderState (UI/transient state)
//   - saveFolders / createFolder / renameFolder / deleteFolder /
//     addSessionToFolder / removeSessionFromFolder
//   - showFolderPage / hideFolderPage / showFolderMain / showFolderDetail
//   - getTimeAgo / getSortedFolders / renderFolderGrid
//   - toggleFolderDropdown / closeFolderDropdowns / startFolderRename
//   - renderFolderDetailSessions
//   - makeSidebarSessionsDraggable / removeSidebarSessionsDraggable
//
// D-V6 direction PRESERVED: folders.js → sessions-list.js (one-way; folders does NOT
//   import sessions-list). Reverse stays prohibited. sessions-list reads
//   getFolderModeActive() to bind drag handlers — that direction stays.
//
// Window bridge removed this commit: window.makeSidebarSessionsDraggable
//   (was attached at news-search.js commit 0a — sessions-list.js renderLeftSidebarSessions
//   reads via window.makeSidebarSessionsDraggable when folder mode is active. We import
//   it via dynamic-style direct call from sessions-list — but sessions-list cannot import
//   from folders.js for THIS function since that would create a cycle... actually D-V6
//   already permits sessions-list → folders. So safe to import directly. Migration plan:
//   keep re-bridge until commit 25 final cleanup since sessions-list already runs
//   `window.makeSidebarSessionsDraggable` via the window path).
//
// Cross-module imports needed:
//   - sessions-list.js: getSavedSessions, getSessionHistory
//   - research.js: getResearchReport
//   - core/state-sync.js: UserStateSync, UserStateSyncError, assertUserIdentity
//   - core/auth-manager.js: authManager (singleton)
// Window bridges still used (until later commits relocate):
//   - window.escapeHTML — pure HTML escape (still owned in news-search.js)
//   - window.matchSessionId — pure id matcher (still owned in news-search.js)
//   - window.saveCurrentSession — KEEP-in-place until commit 23 (per CEO #5)
//   - window.loadSavedSession — sessions-list owner this batch (commit 22) but we
//     access via window to avoid commit-order circular when sessions-list extends.
// ============================================================================

import { getSavedSessions, getSessionHistory } from './sessions-list.js';
import { getResearchReport } from './research.js';
import { UserStateSync, UserStateSyncError, assertUserIdentity } from '../core/state-sync.js';
import { authManager } from '../core/auth-manager.js';

// UI/transient lets (was: news-search.js lines 5385-5388 + 5348 _preFolderState)
let currentFolderSort = 'all';
let currentFolderFilter = '';
let currentOpenFolderId = null;
let openDropdownFolderId = null;
let _preFolderState = null;

// ---- Folder CRUD (was: news-search.js lines 5390-5442) ----

export function saveFolders() {
    persistFolders();
}

export function createFolder(name) {
    const folder = {
        id: Date.now(),
        name: name || '未命名資料夾',
        sessionIds: [],
        createdAt: Date.now(),
        updatedAt: Date.now()
    };
    pushFolder(folder);
    saveFolders();
    renderFolderGrid();
    return folder;
}

export function renameFolder(folderId, newName) {
    const folder = getFolders().find(f => f.id === folderId);
    if (!folder) return;
    folder.name = newName;
    folder.updatedAt = Date.now();
    saveFolders();
    renderFolderGrid();
}

export function deleteFolder(folderId) {
    removeFolder(folderId);
    saveFolders();
    if (currentOpenFolderId === folderId) {
        currentOpenFolderId = null;
        showFolderMain();
    }
    renderFolderGrid();
}

export function addSessionToFolder(folderId, sessionId) {
    const folder = getFolders().find(f => f.id === folderId);
    if (!folder) return;
    if (folder.sessionIds.some(id => window.matchSessionId(id, sessionId))) return; // already in folder
    folder.sessionIds.push(sessionId);
    folder.updatedAt = Date.now();
    saveFolders();
}

export function removeSessionFromFolder(folderId, sessionId) {
    const folder = getFolders().find(f => f.id === folderId);
    if (!folder) return;
    folder.sessionIds = folder.sessionIds.filter(id => !window.matchSessionId(id, sessionId));
    folder.updatedAt = Date.now();
    saveFolders();
}

// ---- View switching (was: news-search.js lines 5446-5518) ----

export function showFolderPage() {
    const ids = ['initialState', 'searchContainer', 'resultsSection', 'loadingState'];
    const chatContainer = document.getElementById('chatContainer');
    const chatInputContainer = document.getElementById('chatInputContainer');
    // 快照目前每個元素的 display 值（含 chat 相關元素）
    _preFolderState = {};
    ids.forEach(id => {
        const el = document.getElementById(id);
        _preFolderState[id] = el ? el.style.display : '';
    });
    _preFolderState._chatContainerActive = chatContainer ? chatContainer.classList.contains('active') : false;
    _preFolderState._chatInputDisplay = chatInputContainer ? chatInputContainer.style.display : '';

    // 隱藏主要內容，顯示資料夾頁
    ids.forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    });
    if (chatContainer) chatContainer.classList.remove('active');
    if (chatInputContainer) chatInputContainer.style.display = 'none';
    document.getElementById('folderPage').style.display = 'block';

    showFolderMain();
    renderFolderGrid();

    // 進入資料夾管理模式：啟用 sidebar session 拖曳
    setFolderModeActive(true);
    makeSidebarSessionsDraggable();
}

export function hideFolderPage() {
    const chatContainer = document.getElementById('chatContainer');
    const chatInputContainer = document.getElementById('chatInputContainer');
    // 離開資料夾管理模式：關閉 sidebar session 拖曳
    setFolderModeActive(false);
    removeSidebarSessionsDraggable();

    document.getElementById('folderPage').style.display = 'none';
    currentOpenFolderId = null;

    // 還原進入前的 UI 狀態
    if (_preFolderState) {
        Object.keys(_preFolderState).forEach(id => {
            if (id.startsWith('_')) return; // skip special keys
            const el = document.getElementById(id);
            if (el) el.style.display = _preFolderState[id];
        });
        // 還原 chat 相關元素
        if (_preFolderState._chatContainerActive && chatContainer) {
            chatContainer.classList.add('active');
        }
        if (chatInputContainer) chatInputContainer.style.display = _preFolderState._chatInputDisplay || '';
        _preFolderState = null;
    } else {
        // fallback：顯示首頁
        document.getElementById('initialState').style.display = 'block';
        document.getElementById('searchContainer').style.display = 'block';
    }
}

export function showFolderMain() {
    document.getElementById('folderMain').style.display = 'block';
    document.getElementById('folderDetail').style.display = 'none';
    currentOpenFolderId = null;
}

export function showFolderDetail(folderId) {
    const folder = getFolders().find(f => f.id === folderId);
    if (!folder) return;

    currentOpenFolderId = folderId;
    document.getElementById('folderMain').style.display = 'none';
    document.getElementById('folderDetail').style.display = 'block';
    document.getElementById('folderDetailTitle').textContent = folder.name;

    renderFolderDetailSessions(folder);
}

// ---- Rendering (was: news-search.js lines 5522-5775) ----

export function getTimeAgo(timestamp) {
    const diff = Date.now() - timestamp;
    const minutes = Math.floor(diff / 60000);
    if (minutes < 1) return '剛剛';
    if (minutes < 60) return `${minutes} 分鐘前`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} 小時前`;
    const days = Math.floor(hours / 24);
    return `${days} 天前`;
}

export function getSortedFolders() {
    let list = [...getFolders()];

    // Apply search filter
    if (currentFolderFilter) {
        list = list.filter(f => f.name.toLowerCase().includes(currentFolderFilter.toLowerCase()));
    }

    // Apply sort
    if (currentFolderSort === 'created') {
        list.sort((a, b) => b.createdAt - a.createdAt);
    } else if (currentFolderSort === 'updated') {
        list.sort((a, b) => b.updatedAt - a.updatedAt);
    }
    // 'all' = original order (newest last, which is push order)

    return list;
}

export function renderFolderGrid() {
    const grid = document.getElementById('folderGrid');
    if (!grid) return;

    const escapeHTML = window.escapeHTML;
    const sortedFolders = getSortedFolders();

    if (sortedFolders.length === 0) {
        grid.innerHTML = '<div class="folder-empty">尚未建立資料夾</div>';
        return;
    }

    grid.innerHTML = sortedFolders.map(folder => `
        <div class="folder-card" data-folder-id="${folder.id}">
            <div class="folder-card-menu">
                <button class="folder-card-menu-btn" data-menu-folder-id="${folder.id}">&#8942;</button>
                <div class="folder-card-dropdown" id="folderDropdown_${folder.id}">
                    <button class="folder-card-dropdown-item" data-action="rename" data-folder-id="${folder.id}">重新命名</button>
                    <button class="folder-card-dropdown-item danger" data-action="delete" data-folder-id="${folder.id}">刪除</button>
                </div>
            </div>
            <div class="folder-card-name" data-name-folder-id="${folder.id}">${escapeHTML(folder.name)}</div>
            <div class="folder-card-meta">更新時間 ${getTimeAgo(folder.updatedAt)}</div>
        </div>
    `).join('');

    // Bind events
    grid.querySelectorAll('.folder-card').forEach(card => {
        const folderId = parseInt(card.dataset.folderId);

        // Click card → open detail (but not if clicking menu)
        card.addEventListener('click', (e) => {
            if (e.target.closest('.folder-card-menu')) return;
            showFolderDetail(folderId);
        });

        // Drag-and-drop: folders accept session drops
        card.addEventListener('dragover', (e) => {
            e.preventDefault();
            card.classList.add('drag-over');
        });
        card.addEventListener('dragleave', () => {
            card.classList.remove('drag-over');
        });
        card.addEventListener('drop', (e) => {
            e.preventDefault();
            card.classList.remove('drag-over');
            const sessionId = e.dataTransfer.getData('text/session-id');
            if (sessionId) {
                addSessionToFolder(folderId, sessionId);
                // 成功閃爍回饋
                card.classList.add('drop-success');
                setTimeout(() => card.classList.remove('drop-success'), 600);
                console.log(`[Folder] Session ${sessionId} added to folder ${folderId}`);
            }
        });
    });

    // Context menu buttons
    grid.querySelectorAll('.folder-card-menu-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const folderId = parseInt(btn.dataset.menuFolderId);
            toggleFolderDropdown(folderId);
        });
    });

    // Dropdown actions
    grid.querySelectorAll('.folder-card-dropdown-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.stopPropagation();
            const folderId = parseInt(item.dataset.folderId);
            const action = item.dataset.action;

            closeFolderDropdowns();

            if (action === 'rename') {
                startFolderRename(folderId);
            } else if (action === 'delete') {
                deleteFolder(folderId);
            }
        });
    });
}

export function toggleFolderDropdown(folderId) {
    const dropdown = document.getElementById(`folderDropdown_${folderId}`);
    if (!dropdown) return;

    const isVisible = dropdown.classList.contains('visible');
    closeFolderDropdowns();
    if (!isVisible) {
        dropdown.classList.add('visible');
        openDropdownFolderId = folderId;
    }
}

export function closeFolderDropdowns() {
    document.querySelectorAll('.folder-card-dropdown.visible').forEach(d => {
        d.classList.remove('visible');
    });
    openDropdownFolderId = null;
}

// Post-refactor regression fix (2026-05-25 commit 26): expose _preFolderState reset
// so news-search.js resetToHome / restoreSession callsites can clear it via ES import
// (was bare `_preFolderState = null` after commit 20 migration).
export function clearPreFolderState() {
    _preFolderState = null;
}

// Filter / sort setters used by news-search.js DOM event wires (input / tab click)
export function setFolderFilter(s) {
    currentFolderFilter = (typeof s === 'string') ? s : '';
}

export function setFolderSort(s) {
    currentFolderSort = s || 'all';
}

export function startFolderRename(folderId) {
    const nameEl = document.querySelector(`[data-name-folder-id="${folderId}"]`);
    if (!nameEl) return;

    const folder = getFolders().find(f => f.id === folderId);
    if (!folder) return;

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'folder-rename-input';
    input.value = folder.name;

    nameEl.innerHTML = '';
    nameEl.appendChild(input);
    input.focus();
    input.select();

    function commit() {
        const newName = input.value.trim();
        if (newName && newName !== folder.name) {
            renameFolder(folderId, newName);
        } else {
            renderFolderGrid(); // restore original
        }
    }

    input.addEventListener('blur', commit);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            input.blur();
        } else if (e.key === 'Escape') {
            input.value = folder.name; // cancel
            input.blur();
        }
    });
}

export function renderFolderDetailSessions(folder) {
    const container = document.getElementById('folderDetailSessions');
    if (!container) return;

    const escapeHTML = window.escapeHTML;

    if (folder.sessionIds.length === 0) {
        container.innerHTML = '<div class="folder-detail-empty">此資料夾尚無搜尋記錄</div>';
        return;
    }

    // Match sessionIds to savedSessions
    const sessions = folder.sessionIds
        .map(id => getSavedSessions().find(s => window.matchSessionId(s.id, id)))
        .filter(Boolean);

    if (sessions.length === 0) {
        container.innerHTML = '<div class="folder-detail-empty">此資料夾尚無搜尋記錄</div>';
        return;
    }

    container.innerHTML = sessions.map(session => {
        const dateStr = getTimeAgo(session.updatedAt || session.createdAt);
        return `
            <div class="folder-session-item" data-session-id="${session.id}">
                <div class="folder-session-info">
                    <div class="folder-session-title">${escapeHTML(session.title)}</div>
                    <div class="folder-session-meta">更新時間 ${dateStr}</div>
                </div>
                <button class="folder-session-remove-btn" data-remove-session-id="${session.id}" title="從資料夾移除">&times;</button>
            </div>
        `;
    }).join('');

    // Click session → load it (ignore remove button clicks)
    container.querySelectorAll('.folder-session-item').forEach(item => {
        item.addEventListener('click', async (e) => {
            if (e.target.closest('.folder-session-remove-btn')) return;
            const sessionId = item.dataset.sessionId;
            const session = getSavedSessions().find(s => window.matchSessionId(s.id, sessionId));
            if (session) {
                // Trigger E: session click (folder detail). Identity self-check
                // before navigating. Mismatch path: fall back to reload-path.
                try {
                    assertUserIdentity(authManager._user, authManager._user);
                } catch (err) {
                    if (err instanceof UserStateSyncError && err.code !== 'MISSING_FRESH' && authManager.isLoggedIn()) {
                        console.warn('[session-click:folder] identity self-check failed, triggering reload-path:', err);
                        await UserStateSync.runInitSync({ keepInviteToken: false }).catch(err2 =>
                            console.error('[session-click:folder] runInitSync failed:', err2));
                        return;
                    }
                }
                // 切換前先保存當前對話（防止深度報告等狀態丟失）
                if (getSessionHistory().length > 0 || getResearchReport()) {
                    if (typeof window.saveCurrentSession === 'function') window.saveCurrentSession();
                }
                hideFolderPage();
                if (typeof window.loadSavedSession === 'function') window.loadSavedSession(session);
            }
        });
    });

    // Remove session from folder
    container.querySelectorAll('.folder-session-remove-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const sessionId = btn.dataset.removeSessionId;
            removeSessionFromFolder(folder.id, sessionId);
            // Re-render with updated folder data
            const updatedFolder = getFolders().find(f => f.id === folder.id);
            if (updatedFolder) {
                renderFolderDetailSessions(updatedFolder);
            }
            console.log(`[Folder] Session ${sessionId} removed from folder ${folder.id}`);
        });
    });
}

// ---- Sidebar draggable toggles (was: news-search.js lines 5843-5858) ----

export function makeSidebarSessionsDraggable() {
    document.querySelectorAll('.left-sidebar-session-item').forEach(item => {
        item.setAttribute('draggable', 'true');
    });
}

export function removeSidebarSessionsDraggable() {
    document.querySelectorAll('.left-sidebar-session-item').forEach(item => {
        item.removeAttribute('draggable');
        item.classList.remove('dragging');
    });
}
