// static/js/features/source-filters.js
//
// D-1 Module Header — Source Filters Owner (state + 18 UI functions — commit 13, Phase 8)
//   Owned state:
//     - _availableSites (array of {name, display_name, description?, ...} — from /sites_config)
//     - _selectedSites (array<string> — site names; empty array means "all")
//     - _sourceDisplayNames (object<string,string> — site code → Chinese display name)
//     - _includePrivateSources (boolean — whether private user files / KB are included in search;
//       default true; CEO decision #2 — owned here, written by source-filters togglePrivateSources
//       AND file-kb updateIncludePrivateSourcesState helper via setIncludePrivateSources)
//
//   Trigger writes:
//     - loadSiteFilters() async hydrate from /sites_config (writes availableSites + selectedSites +
//       sourceDisplayNames once on page bootstrap)
//     - User-driven UI: toggleSiteFilter (single site checkbox), toggleAllSites (select-all toggle),
//       moveSiteToFolder (drag-and-drop between folder buckets — writes via getSourceFolders mutations
//       persisted by saveSourceFolders helper; only mutates selectedSites indirectly via folder layout)
//     - togglePrivateSources (checkbox 'includePrivateSourcesCheckbox' change handler)
//     - file-kb updateIncludePrivateSourcesState (stays in news-search.js until commit 16b file-kb
//       split — reads getSelectedFileCount() and writes setIncludePrivateSources(count > 0))
//
//   External read:
//     - news-search.js performSearch / performDeepResearch / performFreeConversation read
//       includePrivateSources via getIncludePrivateSources()
//     - news-search.js createArticleCard + DR rendering read sourceDisplayNames via
//       getSourceDisplayNames() (lookup by article.site)
//     - features/sharing.js format* functions (commit 6) read sourceDisplayNames (via window
//       bridge or direct import from this module — Phase 8 sweep target)
//
// D-3 Cross-Module Communication:
//   Static imports only:
//     - getSourceFolders / setSourceFolders from features/folders.js (commit 8 owner of folder
//       state; this module renders sites grouped by folder via folder.siteNames array)
//   Window bridges used (until later commits relocate):
//     - window.escapeHTML — pure HTML escaping helper (still owned in news-search.js, attached
//       to window at line ~3480; safe to read directly)
//     - window.openTab — right-sidebar tab opener (still owned in news-search.js; togglePrivateSources
//       opens 'files' tab when private sources is enabled). Defensive optional call ?.()
//
// D-13 Compliance:
//   No top-level side effects. Only declarations + function definitions + exports.
//   DOM event listeners (includePrivateSourcesCheckbox change, btnAddSourceFolder click, etc.)
//   stay in news-search.js — they reference imported functions (togglePrivateSources, etc.).
//   loadSiteFilters() bootstrap call stays in news-search.js DOMContentLoaded path.
//
// v4.0 Commit 13 (2026-05-25): NEW module per Phase 8 §16.1 inventory line 195+.
//   Functions migrated (18): loadSourceFolders, saveSourceFolders, loadSiteFilters,
//     distributeToFolders, renderSourceTreeView, bindSourceTreeEvents, moveSiteToFolder,
//     addSourceFolder, startRenamingFolder, deleteSourceFolder, toggleSiteFilter, toggleAllSites,
//     expandAllSourceFolders, collapseAllSourceFolders, renderSiteFilters, getSelectedSitesParam,
//     togglePrivateSources, triggerFileUpload.
//   State migrated (4 lets): availableSites, selectedSites, sourceDisplayNames, includePrivateSources.
//   handleFileSelect (inventory item 19) DEFERRED to commit 16b file-kb split — it writes userFiles
//   which is file-kb scope. Keeping the 'fileInput change' addEventListener wire in news-search.js
//   until file-kb commit can co-migrate cleanly. Source-filters provides triggerFileUpload only
//   (a pure DOM click — safe to migrate; userFiles state untouched).

import { getSourceFolders, setSourceFolders } from './folders.js';

const SOURCE_FOLDERS_KEY = 'nlweb_source_folders';
const UNCATEGORIZED_FOLDER_ID = '__uncategorized__';

// ============================================================================
// Owned state (was: news-search.js line 1608-1614)
// ============================================================================
let _availableSites = [];
let _selectedSites = []; // empty array means "all"
let _sourceDisplayNames = {}; // source code → Chinese display name
let _includePrivateSources = true; // default true (CEO decision #2)

export function getAvailableSites() { return _availableSites; }
export function setAvailableSites(arr) { _availableSites = arr; }

export function getSelectedSites() { return _selectedSites; }
export function setSelectedSites(arr) { _selectedSites = arr; }

export function getSourceDisplayNames() { return _sourceDisplayNames; }
export function setSourceDisplayNames(obj) { _sourceDisplayNames = obj; }

export function getIncludePrivateSources() { return _includePrivateSources; }
export function setIncludePrivateSources(b) { _includePrivateSources = !!b; }

// ============================================================================
// Source folder persistence (was: news-search.js lines 9369-9413)
// ============================================================================

// Load source folders from localStorage
export function loadSourceFolders() {
    try {
        const stored = localStorage.getItem(SOURCE_FOLDERS_KEY);
        if (stored) {
            setSourceFolders(JSON.parse(stored));
            // Ensure uncategorized folder exists
            if (!getSourceFolders().find(f => f.id === UNCATEGORIZED_FOLDER_ID)) {
                getSourceFolders().unshift({
                    id: UNCATEGORIZED_FOLDER_ID,
                    name: '未分類',
                    isUncategorized: true,
                    siteNames: [],
                    collapsed: false
                });
            }
        } else {
            // Initialize with just uncategorized
            setSourceFolders([{
                id: UNCATEGORIZED_FOLDER_ID,
                name: '未分類',
                isUncategorized: true,
                siteNames: [],
                collapsed: false
            }]);
        }
    } catch (e) {
        console.error('Failed to load source folders:', e);
        setSourceFolders([{
            id: UNCATEGORIZED_FOLDER_ID,
            name: '未分類',
            isUncategorized: true,
            siteNames: [],
            collapsed: false
        }]);
    }
}

// Save source folders to localStorage
export function saveSourceFolders() {
    try {
        localStorage.setItem(SOURCE_FOLDERS_KEY, JSON.stringify(getSourceFolders()));
    } catch (e) {
        console.error('Failed to save source folders:', e);
    }
}

// ============================================================================
// Load + render (was: news-search.js lines 9416-9536)
// ============================================================================

// Load available sites from backend
export async function loadSiteFilters() {
    loadSourceFolders();
    try {
        const response = await fetch('/sites_config');
        const data = await response.json();

        if (data.sites && Array.isArray(data.sites)) {
            _availableSites = data.sites;
            // By default, all sites are selected
            _selectedSites = _availableSites.map(s => s.name);
            // Build source code → display name lookup
            _sourceDisplayNames = {};
            _availableSites.forEach(s => {
                _sourceDisplayNames[s.name] = s.display_name || s.name;
            });

            // Distribute sites to folders
            distributeToFolders();
            renderSourceTreeView();
        }
    } catch (error) {
        console.error('Failed to load site filters:', error);
        const container = document.getElementById('sourceTreeView');
        if (container) {
            container.innerHTML = '<div class="tree-view-empty" style="color: #dc2626;">載入失敗</div>';
        }
    }
}

// Distribute sites to folders, putting uncategorized ones in the uncategorized folder
export function distributeToFolders() {
    const categorizedSites = new Set();
    getSourceFolders().forEach(folder => {
        if (!folder.isUncategorized) {
            folder.siteNames.forEach(name => categorizedSites.add(name));
        }
    });

    // Put remaining sites in uncategorized
    const uncategorizedFolder = getSourceFolders().find(f => f.id === UNCATEGORIZED_FOLDER_ID);
    if (uncategorizedFolder) {
        uncategorizedFolder.siteNames = _availableSites
            .map(s => s.name)
            .filter(name => !categorizedSites.has(name));
    }
}

// Render source tree view
export function renderSourceTreeView() {
    const container = document.getElementById('sourceTreeView');
    if (!container) return;

    if (_availableSites.length === 0) {
        container.innerHTML = '<div class="tree-view-empty">沒有可用的來源</div>';
        return;
    }

    const escapeHTML = window.escapeHTML || ((s) => String(s ?? ''));
    let html = '';

    // Render each folder
    getSourceFolders().forEach(folder => {
        const sites = folder.siteNames
            .map(name => _availableSites.find(s => s.name === name))
            .filter(Boolean);

        const isCollapsed = folder.collapsed ? 'collapsed' : '';
        const folderIconClass = folder.collapsed ? 'closed' : 'open';

        html += `
        <div class="tree-folder ${isCollapsed}" data-folder-id="${folder.id}">
            <div class="tree-folder-header" data-folder-id="${folder.id}">
                <span class="tree-folder-chevron">
                    <svg viewBox="0 0 16 16" fill="currentColor">
                        <path fill-rule="evenodd" d="M4.646 1.646a.5.5 0 0 1 .708 0l6 6a.5.5 0 0 1 0 .708l-6 6a.5.5 0 0 1-.708-.708L10.293 8 4.646 2.354a.5.5 0 0 1 0-.708z"/>
                    </svg>
                </span>
                <span class="tree-folder-icon ${folderIconClass}">
                    <svg viewBox="0 0 16 16" fill="currentColor">
                        <path d="M.54 3.87.5 3a2 2 0 0 1 2-2h3.672a2 2 0 0 1 1.414.586l.828.828A2 2 0 0 0 9.828 3H13.5a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-11a2 2 0 0 1-2-2V3.87z"/>
                    </svg>
                </span>
                <span class="tree-folder-name ${folder.isUncategorized ? 'uncategorized' : ''}">${escapeHTML(folder.name)}</span>
                <span class="tree-folder-count">(${sites.length})</span>
                ${!folder.isUncategorized ? `
                <div class="tree-folder-actions">
                    <div class="tree-folder-menu">
                        <button class="tree-folder-menu-btn" title="更多選項">⋯</button>
                        <div class="tree-folder-dropdown">
                            <button class="tree-folder-dropdown-item" data-action="rename" data-folder-id="${folder.id}">重新命名</button>
                            <button class="tree-folder-dropdown-item danger" data-action="delete" data-folder-id="${folder.id}">刪除資料夾</button>
                        </div>
                    </div>
                </div>
                ` : ''}
            </div>
            <div class="tree-folder-content">
                ${sites.map(site => {
                    const fullText = site.description || site.name;
                    const dashIndex = fullText.indexOf(' - ');
                    const mainName = dashIndex > -1 ? fullText.substring(0, dashIndex) : fullText;
                    const subInfo = dashIndex > -1 ? fullText.substring(dashIndex + 3) : '';
                    return `
                <div class="tree-item tree-item-two-line" draggable="true" data-site-name="${site.name}" data-folder-id="${folder.id}">
                    <input type="checkbox" class="tree-item-checkbox"
                           ${_selectedSites.includes(site.name) ? 'checked' : ''}
                           data-site-name="${site.name}">
                    <div class="tree-item-text">
                        <span class="tree-item-main" title="${fullText}">${mainName}</span>
                        ${subInfo ? `<span class="tree-item-sub">${subInfo}</span>` : ''}
                    </div>
                </div>
                    `;
                }).join('')}
            </div>
        </div>
        `;
    });

    container.innerHTML = html;
    bindSourceTreeEvents(container);
}

// Bind events for source tree view
export function bindSourceTreeEvents(container) {
    // Folder toggle (collapse/expand)
    container.querySelectorAll('.tree-folder-header').forEach(header => {
        header.addEventListener('click', (e) => {
            // Don't toggle if clicking on actions or checkbox
            if (e.target.closest('.tree-folder-actions') || e.target.closest('.tree-folder-menu')) return;

            const folderId = header.dataset.folderId;
            const folder = getSourceFolders().find(f => f.id === folderId);
            if (folder) {
                folder.collapsed = !folder.collapsed;
                saveSourceFolders();
                renderSourceTreeView();
            }
        });

        // Drag over folder header
        header.addEventListener('dragover', (e) => {
            e.preventDefault();
            header.classList.add('drag-over');
        });

        header.addEventListener('dragleave', () => {
            header.classList.remove('drag-over');
        });

        header.addEventListener('drop', (e) => {
            e.preventDefault();
            header.classList.remove('drag-over');
            const siteName = e.dataTransfer.getData('text/site-name');
            const targetFolderId = header.dataset.folderId;
            if (siteName && targetFolderId) {
                moveSiteToFolder(siteName, targetFolderId);
            }
        });
    });

    // Checkbox toggle
    container.querySelectorAll('.tree-item-checkbox').forEach(checkbox => {
        checkbox.addEventListener('change', (e) => {
            e.stopPropagation();
            const siteName = checkbox.dataset.siteName;
            toggleSiteFilter(siteName);
        });

        checkbox.addEventListener('click', (e) => {
            e.stopPropagation();
        });
    });

    // Item drag
    container.querySelectorAll('.tree-item[draggable="true"]').forEach(item => {
        item.addEventListener('dragstart', (e) => {
            e.dataTransfer.setData('text/site-name', item.dataset.siteName);
            e.dataTransfer.effectAllowed = 'move';
            item.classList.add('dragging');

            // Create custom drag preview
            const preview = document.createElement('div');
            preview.className = 'tree-drag-preview';
            preview.textContent = item.dataset.siteName;
            document.body.appendChild(preview);
            e.dataTransfer.setDragImage(preview, 0, 0);
            setTimeout(() => preview.remove(), 0);
        });

        item.addEventListener('dragend', () => {
            item.classList.remove('dragging');
        });
    });

    // Folder menu dropdown
    container.querySelectorAll('.tree-folder-menu-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const dropdown = btn.nextElementSibling;

            // Close other dropdowns
            container.querySelectorAll('.tree-folder-dropdown.visible').forEach(d => {
                if (d !== dropdown) d.classList.remove('visible');
            });

            dropdown.classList.toggle('visible');
        });
    });

    // Folder dropdown actions
    container.querySelectorAll('.tree-folder-dropdown-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.stopPropagation();
            const action = item.dataset.action;
            const folderId = item.dataset.folderId;

            // Close dropdown
            item.closest('.tree-folder-dropdown').classList.remove('visible');

            if (action === 'rename') {
                startRenamingFolder(folderId);
            } else if (action === 'delete') {
                deleteSourceFolder(folderId);
            }
        });
    });

    // Close dropdowns when clicking outside
    document.addEventListener('click', () => {
        container.querySelectorAll('.tree-folder-dropdown.visible').forEach(d => {
            d.classList.remove('visible');
        });
    });
}

// ============================================================================
// Folder CRUD (was: news-search.js lines 9652-9788)
// ============================================================================

// Move a site to a different folder
export function moveSiteToFolder(siteName, targetFolderId) {
    // Remove from current folder
    getSourceFolders().forEach(folder => {
        const index = folder.siteNames.indexOf(siteName);
        if (index > -1) {
            folder.siteNames.splice(index, 1);
        }
    });

    // Add to target folder
    const targetFolder = getSourceFolders().find(f => f.id === targetFolderId);
    if (targetFolder && !targetFolder.siteNames.includes(siteName)) {
        targetFolder.siteNames.push(siteName);
    }

    saveSourceFolders();
    renderSourceTreeView();
    console.log(`[Tree] Moved "${siteName}" to folder "${targetFolder?.name}"`);
}

// Add new source folder
export function addSourceFolder() {
    const container = document.getElementById('sourceTreeView');
    if (!container) return;

    // Check if already adding
    if (container.querySelector('.tree-new-folder-row')) return;

    const row = document.createElement('div');
    row.className = 'tree-new-folder-row';
    row.innerHTML = `
        <input type="text" class="tree-new-folder-input" placeholder="資料夾名稱" autofocus>
        <button class="tree-new-folder-btn confirm">確定</button>
        <button class="tree-new-folder-btn cancel">取消</button>
    `;

    container.insertBefore(row, container.firstChild);

    const input = row.querySelector('.tree-new-folder-input');
    input.focus();

    const confirmAdd = () => {
        const name = input.value.trim();
        if (name) {
            const newFolder = {
                id: 'folder_' + Date.now(),
                name: name,
                isUncategorized: false,
                siteNames: [],
                collapsed: false
            };
            // Insert before uncategorized
            const uncatIndex = getSourceFolders().findIndex(f => f.id === UNCATEGORIZED_FOLDER_ID);
            if (uncatIndex > -1) {
                getSourceFolders().splice(uncatIndex, 0, newFolder);
            } else {
                getSourceFolders().push(newFolder);
            }
            saveSourceFolders();
            console.log(`[Tree] Created new folder: "${name}"`);
        }
        row.remove();
        renderSourceTreeView();
    };

    row.querySelector('.tree-new-folder-btn.confirm').addEventListener('click', confirmAdd);
    row.querySelector('.tree-new-folder-btn.cancel').addEventListener('click', () => row.remove());
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') confirmAdd();
        if (e.key === 'Escape') row.remove();
    });
}

// Start renaming a folder
export function startRenamingFolder(folderId) {
    const folder = getSourceFolders().find(f => f.id === folderId);
    if (!folder || folder.isUncategorized) return;

    const header = document.querySelector(`.tree-folder-header[data-folder-id="${folderId}"]`);
    if (!header) return;

    const nameEl = header.querySelector('.tree-folder-name');
    const originalName = folder.name;

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'tree-folder-rename-input';
    input.value = originalName;

    nameEl.replaceWith(input);
    input.focus();
    input.select();

    const finishRename = () => {
        const newName = input.value.trim();
        if (newName && newName !== originalName) {
            folder.name = newName;
            saveSourceFolders();
            console.log(`[Tree] Renamed folder to: "${newName}"`);
        }
        renderSourceTreeView();
    };

    input.addEventListener('blur', finishRename);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            finishRename();
        }
        if (e.key === 'Escape') {
            e.preventDefault();
            renderSourceTreeView();
        }
    });
}

// Delete a source folder (move contents to uncategorized)
export function deleteSourceFolder(folderId) {
    const folder = getSourceFolders().find(f => f.id === folderId);
    if (!folder || folder.isUncategorized) return;

    // Move all sites to uncategorized
    const uncategorized = getSourceFolders().find(f => f.id === UNCATEGORIZED_FOLDER_ID);
    if (uncategorized) {
        folder.siteNames.forEach(siteName => {
            if (!uncategorized.siteNames.includes(siteName)) {
                uncategorized.siteNames.push(siteName);
            }
        });
    }

    // Remove folder
    setSourceFolders(getSourceFolders().filter(f => f.id !== folderId));
    saveSourceFolders();
    renderSourceTreeView();
    console.log(`[Tree] Deleted folder: "${folder.name}", moved ${folder.siteNames.length} sites to uncategorized`);
}

// ============================================================================
// Filter toggles (was: news-search.js lines 9791-9827)
// ============================================================================

// Toggle individual site filter
export function toggleSiteFilter(siteName) {
    const index = _selectedSites.indexOf(siteName);
    if (index > -1) {
        _selectedSites.splice(index, 1);
    } else {
        _selectedSites.push(siteName);
    }
}

// Toggle all sites
export function toggleAllSites() {
    const allSelected = _selectedSites.length === _availableSites.length;
    if (allSelected) {
        _selectedSites = [];
    } else {
        _selectedSites = _availableSites.map(s => s.name);
    }
    renderSourceTreeView();
}

// Expand all source folders
export function expandAllSourceFolders() {
    getSourceFolders().forEach(f => f.collapsed = false);
    saveSourceFolders();
    renderSourceTreeView();
}

// Collapse all source folders
export function collapseAllSourceFolders() {
    getSourceFolders().forEach(f => f.collapsed = true);
    saveSourceFolders();
    renderSourceTreeView();
}

// Legacy function for compatibility
export function renderSiteFilters() {
    renderSourceTreeView();
}

// ============================================================================
// Public read API for cross-module consumers (was: news-search.js 9831-9849)
// ============================================================================

// Get selected sites as parameter value
// Returns 'all' when all (or none) selected, else comma-joined site name list.
// Called from search.js performSearch / performDeepResearch / performLiveResearch (commit 14+).
export function getSelectedSitesParam() {
    // If all sites are selected or none selected, return 'all'
    if (_selectedSites.length === 0 || _selectedSites.length === _availableSites.length) {
        return 'all';
    }
    return _selectedSites.join(',');
}

// Toggle private sources checkbox handler.
// Wired to 'includePrivateSourcesCheckbox' change event in news-search.js bootstrap.
export function togglePrivateSources() {
    const checkbox = document.getElementById('includePrivateSourcesCheckbox');
    _includePrivateSources = checkbox.checked;
    console.log('Include private sources:', _includePrivateSources);

    // 勾選時自動開啟右側「我的檔案」面板
    if (_includePrivateSources && typeof window.openTab === 'function') {
        window.openTab('files');
    }
}

// Trigger file input click (just clicks the hidden <input type="file">).
// The actual change handler (handleFileSelect) stays in news-search.js until commit 16b
// (file-kb split) since it writes userFiles state which is file-kb scope.
export function triggerFileUpload() {
    document.getElementById('fileInput').click();
}
