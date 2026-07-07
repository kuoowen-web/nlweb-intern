// static/js/features/file-kb.js
//
// D-1 Module Header — File Knowledge Base Owner (commit 19, Phase 8 part C)
//   Owned state:
//     - _userFiles (array of file metadata objects: {source_id, name, file_type, status, ...} —
//       hydrated from /api/user/sources GET; reset on logout via clearUserFiles)
//
//   Trigger writes:
//     - loadUserFiles() — async fetch from /api/user/sources; writes _userFiles + triggers
//       distributeFilesToFolders + renderFileTreeView
//     - deleteUserFile() — DELETE + refresh via loadUserFiles
//     - handleFileSelect() — upload + SSE progress + final refresh via loadUserFiles
//
//   External read:
//     - file-kb internal — distributeFilesToFolders / renderFileTreeView / bindFileTreeEvents
//       all read _userFiles via getUserFiles() live-ref accessor
//
// D-3 Cross-Module Communication:
//   Static imports only:
//     - features/folders.js — getFileFolders / setFileFolders / getSelectedFileIds /
//       setSelectedFileIds / addSelectedFile / removeSelectedFile / hasSelectedFile /
//       getSelectedFileCount (file-folder state + selected file Set live in folders.js
//       since commit 8)
//     - features/source-filters.js — setIncludePrivateSources (CEO decision #2 owner;
//       file-kb writes via setter when selection count changes)
//   Window bridges still used (until later commits relocate):
//     - window.authManager — auth singleton for authenticatedFetch (still global via main.js bridge)
//     - window.getCurrentUserId — auth UI helper (sweep at commit 24 batch 7'')
//     - window.escapeHTML — HTML escape (still owned in news-search.js)
//
// D-13 Compliance:
//   No top-level side effects. Only declarations + function definitions + exports.
//   The fileInput change listener + btnAddFileFolder / btnExpandAllFiles / btnCollapseAllFiles
//   click listeners stay in news-search.js (they reference these imported functions). The
//   bootstrap loadUserFiles() call also stays in news-search.js DOMContentLoaded handler
//   (after authReady), since it depends on auth readiness.
//
// v4.0 Commit 19 (2026-05-25, Phase 8 part C): NEW module per Phase 8 §16.1 file-kb owner.
//   Functions migrated (15): handleFileSelect, loadFileFolders, saveFileFolders,
//     saveSelectedFiles, loadUserFiles, distributeFilesToFolders, renderFileTreeView,
//     bindFileTreeEvents, updateIncludePrivateSourcesState, moveFileToFolder,
//     addFileFolder, startRenamingFileFolder, deleteFileFolder, expandAllFileFolders,
//     collapseAllFileFolders, renderFileList, deleteUserFile, getFileIcon, getStatusText.
//   State migrated (1 let): userFiles → _userFiles.
//   Bridges removed: 0 (no pre-existing window attaches for file-kb fns; news-search.js
//     wires DOM event listeners using imported function references directly).

import {
    getFileFolders, setFileFolders,
    getSelectedFileIds, setSelectedFileIds,
    addSelectedFile, removeSelectedFile, hasSelectedFile,
    getSelectedFileCount
} from './folders.js';
import { setIncludePrivateSources } from './source-filters.js';

// ============================================================================
// Owned state (was: news-search.js line 1734 `let userFiles = []`)
// ============================================================================
let _userFiles = [];

export function getUserFiles() { return _userFiles; }
export function setUserFiles(arr) { _userFiles = Array.isArray(arr) ? arr : []; }
export function clearUserFiles() { _userFiles.length = 0; }

// ============================================================================
// localStorage keys (was: news-search.js line 5213-5217)
// ============================================================================
const FILE_FOLDERS_KEY = 'nlweb_file_folders';
const UNCATEGORIZED_FILE_FOLDER_ID = '__uncategorized_files__';
const SELECTED_FILES_KEY = 'nlweb_selected_files';

// ============================================================================
// File upload entry (was: news-search.js line 5125-5202)
// ============================================================================
export async function handleFileSelect(event) {
    const file = event.target.files[0];
    if (!file) return;

    console.log('File selected:', file.name, file.size, 'bytes');

    // Show upload modal
    const modal = document.getElementById('uploadModal');
    const progressBar = document.getElementById('progressBarFill');
    const progressText = document.getElementById('progressText');

    modal.classList.add('visible');
    progressBar.style.width = '0%';
    progressText.textContent = '準備上傳...';

    try {
        // Create form data
        const formData = new FormData();
        formData.append('file', file);
        formData.append('user_id', window.getCurrentUserId());

        // Upload file
        progressText.textContent = '正在上傳文件...';
        // P1 E2E fix (2026-05-26): route through authenticatedFetch for 401→refresh→retry.
        // FormData body left intact (authenticatedFetch only adds the Authorization header,
        // it does not set Content-Type, so the multipart boundary is preserved).
        const response = await window.authManager.authenticatedFetch('/api/user/upload', {
            method: 'POST',
            body: formData
        });

        if (response.status === 401) {
            throw new Error('登入已過期，請重新登入後再試。');
        }
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || '上傳失敗');
        }

        const result = await response.json();
        console.log('Upload result:', result);

        const sourceId = result.source_id;

        // Connect to SSE for progress updates
        progressText.textContent = '正在處理文件...';
        const eventSource = new EventSource(`/api/user/upload/${sourceId}/progress?user_id=${window.getCurrentUserId()}`);

        eventSource.onmessage = (event) => {
            const data = JSON.parse(event.data);
            console.log('Progress:', data);

            progressBar.style.width = data.progress + '%';
            progressText.textContent = data.message;

            if (data.status === 'completed') {
                eventSource.close();
                setTimeout(() => {
                    modal.classList.remove('visible');
                    loadUserFiles(); // Refresh file list
                }, 1000);
            } else if (data.status === 'failed') {
                eventSource.close();
                alert('文件處理失敗: ' + data.message);
                modal.classList.remove('visible');
            }
        };

        eventSource.onerror = (error) => {
            console.error('SSE error:', error);
            eventSource.close();
            modal.classList.remove('visible');
            alert('處理過程中斷，請稍後再試');
        };

    } catch (error) {
        console.error('Upload error:', error);
        alert('上傳失敗: ' + error.message);
        modal.classList.remove('visible');
    }

    // Reset file input
    event.target.value = '';
}

// ============================================================================
// File folder persistence (was: news-search.js lines 5220-5277)
// ============================================================================
export function loadFileFolders() {
    try {
        const stored = localStorage.getItem(FILE_FOLDERS_KEY);
        if (stored) {
            setFileFolders(JSON.parse(stored));
            if (!getFileFolders().find(f => f.id === UNCATEGORIZED_FILE_FOLDER_ID)) {
                getFileFolders().unshift({
                    id: UNCATEGORIZED_FILE_FOLDER_ID,
                    name: '未分類',
                    isUncategorized: true,
                    fileIds: [],
                    collapsed: false
                });
            }
        } else {
            setFileFolders([{
                id: UNCATEGORIZED_FILE_FOLDER_ID,
                name: '未分類',
                isUncategorized: true,
                fileIds: [],
                collapsed: false
            }]);
        }

        // Load selected files
        const selectedStored = localStorage.getItem(SELECTED_FILES_KEY);
        if (selectedStored) {
            setSelectedFileIds(new Set(JSON.parse(selectedStored)));
        }
    } catch (e) {
        console.error('Failed to load file folders:', e);
        setFileFolders([{
            id: UNCATEGORIZED_FILE_FOLDER_ID,
            name: '未分類',
            isUncategorized: true,
            fileIds: [],
            collapsed: false
        }]);
    }
}

export function saveFileFolders() {
    try {
        localStorage.setItem(FILE_FOLDERS_KEY, JSON.stringify(getFileFolders()));
    } catch (e) {
        console.error('Failed to save file folders:', e);
    }
}

export function saveSelectedFiles() {
    try {
        localStorage.setItem(SELECTED_FILES_KEY, JSON.stringify([...getSelectedFileIds()]));
    } catch (e) {
        console.error('Failed to save selected files:', e);
    }
}

// ============================================================================
// Load user files from backend (was: news-search.js line 5280-5298)
// ============================================================================
export async function loadUserFiles() {
    loadFileFolders();
    try {
        const response = await window.authManager.authenticatedFetch(`/api/user/sources?user_id=${window.getCurrentUserId()}`);
        if (!response.ok) {
            throw new Error('Failed to load files');
        }

        const result = await response.json();
        _userFiles = result.sources || [];
        console.log('Loaded user files:', _userFiles);

        // Distribute files to folders
        distributeFilesToFolders();
        renderFileTreeView();
    } catch (error) {
        console.error('Error loading files:', error);
    }
}

// ============================================================================
// File distribution to folders (was: news-search.js line 5301-5321)
// ============================================================================
export function distributeFilesToFolders() {
    const categorizedFileIds = new Set();
    getFileFolders().forEach(folder => {
        if (!folder.isUncategorized) {
            folder.fileIds.forEach(id => categorizedFileIds.add(id));
        }
    });

    // Put remaining files in uncategorized
    const uncategorizedFolder = getFileFolders().find(f => f.id === UNCATEGORIZED_FILE_FOLDER_ID);
    if (uncategorizedFolder) {
        uncategorizedFolder.fileIds = _userFiles
            .map(f => f.source_id)
            .filter(id => !categorizedFileIds.has(id));
    }

    // Clean up selected files that no longer exist
    const existingIds = new Set(_userFiles.map(f => f.source_id));
    setSelectedFileIds(new Set([...getSelectedFileIds()].filter(id => existingIds.has(id))));
    saveSelectedFiles();
}

// ============================================================================
// File tree render (was: news-search.js line 5324-5405)
// ============================================================================
export function renderFileTreeView() {
    const container = document.getElementById('fileTreeView');
    if (!container) return;

    const escapeHTML = window.escapeHTML;

    // Show empty state only if no folders (except uncategorized) AND no files
    const hasCustomFolders = getFileFolders().some(f => !f.isUncategorized);
    if (_userFiles.length === 0 && !hasCustomFolders) {
        container.innerHTML = '<div class="tree-view-empty">尚未上傳文件</div>';
        return;
    }

    let html = '';

    // Render each folder
    getFileFolders().forEach(folder => {
        const files = folder.fileIds
            .map(id => _userFiles.find(f => f.source_id === id))
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
                <span class="tree-folder-count">(${files.length})</span>
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
                ${files.map(file => {
                    const icon = getFileIcon(file.file_type);
                    const statusText = getStatusText(file.status);
                    const isSelected = hasSelectedFile(file.source_id);
                    const canDelete = file.status !== 'processing';

                    return `
                    <div class="tree-item" draggable="true" data-file-id="${file.source_id}" data-folder-id="${folder.id}">
                        <input type="checkbox" class="tree-item-checkbox"
                               ${isSelected ? 'checked' : ''}
                               ${file.status !== 'ready' ? 'disabled' : ''}
                               data-file-id="${file.source_id}"
                               title="${file.status === 'ready' ? '勾選以包含在搜尋中' : '處理中，無法選取'}">
                        <span class="tree-item-icon">${icon}</span>
                        <span class="tree-item-name" title="${file.name}">${file.name}</span>
                        <span class="tree-item-status ${file.status}">${statusText}</span>
                        ${canDelete ? `
                        <div class="tree-item-actions">
                            <button class="tree-item-action-btn delete" data-file-id="${file.source_id}" data-file-name="${file.name}" title="刪除檔案"><img src="/static/images/icon-delete.svg" alt="刪除" class="inline-icon"></button>
                        </div>
                        ` : ''}
                    </div>
                    `;
                }).join('')}
            </div>
        </div>
        `;
    });

    container.innerHTML = html;
    bindFileTreeEvents(container);
}

// ============================================================================
// Bind file tree events (was: news-search.js line 5408-5529)
// ============================================================================
export function bindFileTreeEvents(container) {
    // Folder toggle
    container.querySelectorAll('.tree-folder-header').forEach(header => {
        header.addEventListener('click', (e) => {
            if (e.target.closest('.tree-folder-actions') || e.target.closest('.tree-folder-menu')) return;

            const folderId = header.dataset.folderId;
            const folder = getFileFolders().find(f => f.id === folderId);
            if (folder) {
                folder.collapsed = !folder.collapsed;
                saveFileFolders();
                renderFileTreeView();
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
            const fileId = e.dataTransfer.getData('text/file-id');
            const targetFolderId = header.dataset.folderId;
            if (fileId && targetFolderId) {
                moveFileToFolder(fileId, targetFolderId);
            }
        });
    });

    // Checkbox toggle (select file for context)
    container.querySelectorAll('.tree-item-checkbox').forEach(checkbox => {
        checkbox.addEventListener('change', (e) => {
            e.stopPropagation();
            const fileId = checkbox.dataset.fileId;
            if (checkbox.checked) {
                addSelectedFile(fileId);
            } else {
                removeSelectedFile(fileId);
            }
            saveSelectedFiles();
            updateIncludePrivateSourcesState();
            console.log('[FileTree] Selected files:', [...getSelectedFileIds()]);
        });

        checkbox.addEventListener('click', (e) => {
            e.stopPropagation();
        });
    });

    // Item drag
    container.querySelectorAll('.tree-item[draggable="true"]').forEach(item => {
        item.addEventListener('dragstart', (e) => {
            e.dataTransfer.setData('text/file-id', item.dataset.fileId);
            e.dataTransfer.effectAllowed = 'move';
            item.classList.add('dragging');

            const preview = document.createElement('div');
            preview.className = 'tree-drag-preview';
            const file = _userFiles.find(f => f.source_id === item.dataset.fileId);
            preview.textContent = file?.name || item.dataset.fileId;
            document.body.appendChild(preview);
            e.dataTransfer.setDragImage(preview, 0, 0);
            setTimeout(() => preview.remove(), 0);
        });

        item.addEventListener('dragend', () => {
            item.classList.remove('dragging');
        });
    });

    // Delete button
    container.querySelectorAll('.tree-item-action-btn.delete').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const fileId = btn.dataset.fileId;
            const fileName = btn.dataset.fileName;
            deleteUserFile(fileId, fileName);
        });
    });

    // Folder menu dropdown
    container.querySelectorAll('.tree-folder-menu-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const dropdown = btn.nextElementSibling;
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
            item.closest('.tree-folder-dropdown').classList.remove('visible');

            if (action === 'rename') {
                startRenamingFileFolder(folderId);
            } else if (action === 'delete') {
                deleteFileFolder(folderId);
            }
        });
    });

    // Close dropdowns
    document.addEventListener('click', () => {
        container.querySelectorAll('.tree-folder-dropdown.visible').forEach(d => {
            d.classList.remove('visible');
        });
    });
}

// ============================================================================
// Update includePrivateSources state — writes source-filters owner via setter
// (CEO decision #2: source-filters owns the state, file-kb writes via setIncludePrivateSources)
// (was: news-search.js line 5533-5535)
// ============================================================================
export function updateIncludePrivateSourcesState() {
    setIncludePrivateSources(getSelectedFileCount() > 0);
}

// ============================================================================
// Move file between folders (was: news-search.js line 5543-5560)
// ============================================================================
export function moveFileToFolder(fileId, targetFolderId) {
    getFileFolders().forEach(folder => {
        const index = folder.fileIds.indexOf(fileId);
        if (index > -1) {
            folder.fileIds.splice(index, 1);
        }
    });

    const targetFolder = getFileFolders().find(f => f.id === targetFolderId);
    if (targetFolder && !targetFolder.fileIds.includes(fileId)) {
        targetFolder.fileIds.push(fileId);
    }

    saveFileFolders();
    renderFileTreeView();
    const file = _userFiles.find(f => f.source_id === fileId);
    console.log(`[FileTree] Moved "${file?.name}" to folder "${targetFolder?.name}"`);
}

// ============================================================================
// Add file folder (was: news-search.js line 5563-5610)
// ============================================================================
export function addFileFolder() {
    const container = document.getElementById('fileTreeView');
    if (!container) return;
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
                id: 'file_folder_' + Date.now(),
                name: name,
                isUncategorized: false,
                fileIds: [],
                collapsed: false
            };
            const uncatIndex = getFileFolders().findIndex(f => f.id === UNCATEGORIZED_FILE_FOLDER_ID);
            if (uncatIndex > -1) {
                getFileFolders().splice(uncatIndex, 0, newFolder);
            } else {
                getFileFolders().push(newFolder);
            }
            saveFileFolders();
            console.log(`[FileTree] Created new folder: "${name}"`);
        }
        row.remove();
        renderFileTreeView();
    };

    row.querySelector('.tree-new-folder-btn.confirm').addEventListener('click', confirmAdd);
    row.querySelector('.tree-new-folder-btn.cancel').addEventListener('click', () => row.remove());
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') confirmAdd();
        if (e.key === 'Escape') row.remove();
    });
}

// ============================================================================
// Rename file folder (was: news-search.js line 5613-5653)
// ============================================================================
export function startRenamingFileFolder(folderId) {
    const folder = getFileFolders().find(f => f.id === folderId);
    if (!folder || folder.isUncategorized) return;

    const header = document.querySelector(`#fileTreeView .tree-folder-header[data-folder-id="${folderId}"]`);
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
            saveFileFolders();
            console.log(`[FileTree] Renamed folder to: "${newName}"`);
        }
        renderFileTreeView();
    };

    input.addEventListener('blur', finishRename);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            finishRename();
        }
        if (e.key === 'Escape') {
            e.preventDefault();
            renderFileTreeView();
        }
    });
}

// ============================================================================
// Delete file folder (was: news-search.js line 5656-5673)
// ============================================================================
export function deleteFileFolder(folderId) {
    const folder = getFileFolders().find(f => f.id === folderId);
    if (!folder || folder.isUncategorized) return;

    const uncategorized = getFileFolders().find(f => f.id === UNCATEGORIZED_FILE_FOLDER_ID);
    if (uncategorized) {
        folder.fileIds.forEach(fileId => {
            if (!uncategorized.fileIds.includes(fileId)) {
                uncategorized.fileIds.push(fileId);
            }
        });
    }

    setFileFolders(getFileFolders().filter(f => f.id !== folderId));
    saveFileFolders();
    renderFileTreeView();
    console.log(`[FileTree] Deleted folder: "${folder.name}"`);
}

// ============================================================================
// Expand / collapse all file folders (was: news-search.js line 5676-5687)
// ============================================================================
export function expandAllFileFolders() {
    getFileFolders().forEach(f => f.collapsed = false);
    saveFileFolders();
    renderFileTreeView();
}

export function collapseAllFileFolders() {
    getFileFolders().forEach(f => f.collapsed = true);
    saveFileFolders();
    renderFileTreeView();
}

// ============================================================================
// Legacy alias (was: news-search.js line 5690-5692)
// ============================================================================
export function renderFileList() {
    renderFileTreeView();
}

// ============================================================================
// Delete user file (was: news-search.js line 5695-5720)
// ============================================================================
export async function deleteUserFile(sourceId, fileName) {
    if (!confirm(`確定要刪除「${fileName}」嗎？此操作無法復原。`)) {
        return;
    }

    try {
        // P1 E2E fix (2026-05-26): route through authenticatedFetch for 401→refresh→retry.
        const response = await window.authManager.authenticatedFetch(`/api/user/sources/${sourceId}?user_id=${window.getCurrentUserId()}`, {
            method: 'DELETE'
        });

        if (response.status === 401) {
            throw new Error('登入已過期，請重新登入後再試。');
        }
        if (!response.ok) {
            const result = await response.json();
            throw new Error(result.error || 'Failed to delete file');
        }

        // Remove from selected files
        removeSelectedFile(sourceId);
        saveSelectedFiles();

        console.log(`File deleted: ${fileName} (source_id=${sourceId})`);
        loadUserFiles();
    } catch (error) {
        console.error('Error deleting file:', error);
        alert('刪除失敗: ' + error.message);
    }
}

// ============================================================================
// Pure helpers — file icon + status text (was: news-search.js line 5723-5742)
// ============================================================================
export function getFileIcon(fileType) {
    const icons = {
        '.pdf': '<img src="/static/images/icon-pdf.svg" alt="PDF" class="inline-icon">',
        '.docx': '<img src="/static/images/icon-doc.svg" alt="DOC" class="inline-icon">',
        '.txt': '<img src="/static/images/icon-txt.svg" alt="TXT" class="inline-icon">',
        '.md': '<img src="/static/images/icon-md.svg" alt="MD" class="inline-icon">'
    };
    return icons[fileType] || '<img src="/static/images/icon-pdf.svg" alt="檔案" class="inline-icon">';
}

export function getStatusText(status) {
    const texts = {
        'uploading': '上傳中',
        'processing': '處理中',
        'ready': '就緒',
        'failed': '失敗'
    };
    return texts[status] || status;
}
