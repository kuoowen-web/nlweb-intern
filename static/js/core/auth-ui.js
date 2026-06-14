// static/js/core/auth-ui.js
//
// D-1 Module Header — Auth UI (Phase 8 commit 24)
//   Owned state:
//     - TEMP_USER_ID (module-private const fallback id for unauthenticated users)
//
//   Responsibility:
//     - UI counterpart to core/auth-manager.js. Renders auth modal, user menu,
//       org management modal + admin controls, accept-invite toast, main-UI
//       show/hide. All consumers go through window.authManager (already on window
//       per main.js bootstrap) — no direct ES import from auth-manager.js to
//       avoid coupling the UI module to the manager singleton instance.
//
//   External read interface (exports):
//     - updateAuthUI, showAuthModal, hideAuthModal, switchAuthTab,
//       showMainUI, hideMainUI, getCurrentUserId,
//       handleInviteToken, showAcceptInviteToast, acceptInvite,
//       openOrgModal, reloadOrgMembers, closeOrgModal, renderOrgMembers,
//       removeMember, changeUserRole, toggleUserActive, forceLogoutUser,
//       deleteUser, escapeAttr
//
// CEO directive 2 note (commit 24, 2026-05-25):
//   Split as separate auth-ui.js (not auth-manager extend) because:
//     1. clean separation of concerns (manager = auth logic, ui = DOM rendering)
//     2. auth-manager.js already 341 LOC — adding 20 UI fns would double it
//     3. UI module can stay DOM-coupled without polluting the credential-handling
//        core class
//
// D-13 Compliance: INERT on import. No top-level side effects. All exports
// are pure function declarations; the TEMP_USER_ID const is a module-private
// value, no DOM access at module-eval time.

// Module-private const (was local to news-search.js IIFE).
const TEMP_USER_ID = 'demo_user_001';

// ==================== AUTH UI LOGIC ====================
export function updateAuthUI() {
    const btnShowLogin = document.getElementById('btnShowLogin');
    const userMenu = document.getElementById('userMenu');
    const userDisplayName = document.getElementById('userDisplayName');
    const btnOrgManage = document.getElementById('btnOrgManage');
    const btnCloseAuthModal = document.getElementById('btnCloseAuthModal');
    const settingsUserName = document.getElementById('settingsUserName');

    if (window.authManager.isLoggedIn()) {
        btnShowLogin.style.display = 'none';
        userMenu.style.display = 'flex';
        const user = window.authManager.getCurrentUser();
        const displayName = user.name || user.email;
        userDisplayName.textContent = displayName;
        // Update sidebar settings trigger to show user name + gear icon
        if (settingsUserName) settingsUserName.textContent = displayName;
        if (btnOrgManage) {
            btnOrgManage.style.display = user.role === 'admin' ? '' : 'none';
        }
        // X button is usable when logged in (to close modal after login)
        if (btnCloseAuthModal) btnCloseAuthModal.style.display = '';
        // Show pending invite toast if any
        const pendingToken = sessionStorage.getItem('pendingInviteToken');
        if (pendingToken) showAcceptInviteToast(pendingToken);
    } else {
        btnShowLogin.style.display = '';
        userMenu.style.display = 'none';
        userDisplayName.textContent = '';
        // Reset sidebar settings trigger
        if (settingsUserName) settingsUserName.textContent = '設定';
        if (btnOrgManage) btnOrgManage.style.display = 'none';
        // Hide X button when not logged in — it has no function in that state
        if (btnCloseAuthModal) btnCloseAuthModal.style.display = 'none';
    }
}

// ==================== ORG MANAGEMENT ====================

export async function openOrgModal() {
    const overlay = document.getElementById('orgModalOverlay');
    overlay.style.display = 'flex';
    document.getElementById('orgMembersList').innerHTML = '<div class="org-loading">Loading...</div>';
    document.getElementById('orgInviteFeedback').style.display = 'none';
    document.getElementById('orgInviteSection').style.display = 'none';

    const user = window.authManager.getCurrentUser();
    const org_id = user?.org_id;
    if (!org_id) {
        document.getElementById('orgMembersList').innerHTML = '<div class="org-empty">You are not part of any organization.</div>';
        return;
    }

    try {
        // Fetch org name
        const orgsRes = await window.authManager.authenticatedFetch('/api/org');
        const orgsData = await orgsRes.json();
        const org = orgsData.organizations?.find(o => o.id === org_id);
        if (org) {
            document.getElementById('orgModalTitle').textContent = `${org.name} - Member Management`;
        }

        // Fetch members
        const membersRes = await window.authManager.authenticatedFetch(`/api/org/${org_id}/members`);
        const membersData = await membersRes.json();
        if (!membersRes.ok) throw new Error(membersData.error || 'Failed to load members');

        renderOrgMembers(membersData.members, org_id, user);

        if (user.role === 'admin') {
            document.getElementById('orgInviteSection').style.display = 'block';
        }
    } catch (e) {
        document.getElementById('orgMembersList').innerHTML = `<div class="org-error">Load failed: ${e.message}</div>`;
    }
}

export async function reloadOrgMembers() {
    const user = window.authManager.getCurrentUser();
    const org_id = user?.org_id;
    if (!org_id) return;
    try {
        const membersRes = await window.authManager.authenticatedFetch(`/api/org/${org_id}/members`);
        const membersData = await membersRes.json();
        if (!membersRes.ok) throw new Error(membersData.error || 'Failed to load members');
        renderOrgMembers(membersData.members, org_id, user);
    } catch (e) {
        document.getElementById('orgMembersList').innerHTML = `<div class="org-error">Reload failed: ${e.message}</div>`;
    }
}

// I-3: Full attribute escaping for inline onclick handlers
export function escapeAttr(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/'/g, '&#39;')
        .replace(/"/g, '&quot;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/\\/g, '\\\\');
}

export function closeOrgModal() {
    document.getElementById('orgModalOverlay').style.display = 'none';
}

export function renderOrgMembers(members, orgId, currentUser) {
    const list = document.getElementById('orgMembersList');
    if (!members || members.length === 0) {
        list.innerHTML = '<div class="org-empty">No members.</div>';
        return;
    }
    const isAdmin = currentUser.role === 'admin';
    list.innerHTML = members.map(m => {
        const isSelf = m.id === currentUser.id;
        const roleLabel = m.role === 'admin' ? '管理員' : '成員';
        const joinDate = (m.accepted_at && m.accepted_at > 0) ? new Date(m.accepted_at * 1000).toLocaleDateString('zh-TW') : '-';
        const selfTag = isSelf ? ' <span class="org-self-tag">(我)</span>' : '';

        let adminControls = '';
        if (isAdmin && !isSelf) {
            const activeLabel = m.is_active === false ? '啟用' : '停用';
            const activeClass = m.is_active === false ? 'btn-activate-member' : 'btn-deactivate-member';
            const inactiveBadge = m.is_active === false ? '<span class="org-inactive-badge">已停用</span>' : '';
            const safeId = escapeAttr(m.id);
            const safeName = escapeAttr(m.name || m.email);
            // 未啟用成員顯示「重寄啟用信」按鈕
            const resendBtn = (m.is_activated === false)
                ? `<button class="btn-resend-activation btn-admin-action" data-user-id="${safeId}">重寄啟用信</button>`
                : '';
            adminControls = `
                ${inactiveBadge}
                <select class="org-role-select" data-user-id="${safeId}">
                    <option value="member"${m.role === 'member' ? ' selected' : ''}>成員</option>
                    <option value="admin"${m.role === 'admin' ? ' selected' : ''}>管理員</option>
                </select>
                <button class="${activeClass} btn-admin-action btn-toggle-active" data-user-id="${safeId}" data-is-active="${m.is_active !== false}">${activeLabel}</button>
                ${resendBtn}
                <button class="btn-force-logout btn-admin-action" data-user-id="${safeId}">強制登出</button>
                <button class="btn-delete-member btn-admin-action" data-user-id="${safeId}" data-user-name="${safeName}">刪除</button>`;
        } else if (!isAdmin) {
            adminControls = `<span class="org-role-badge org-role-${m.role}">${roleLabel}</span>`;
        }

        return `<div class="org-member-row">
            <div class="org-member-info">
                <span class="org-member-name">${m.name || ''}${selfTag}</span>
                <span class="org-member-email">${m.email}</span>
            </div>
            <div class="org-member-meta">
                ${isAdmin && !isSelf ? '' : `<span class="org-role-badge org-role-${m.role}">${roleLabel}</span>`}
                <span class="org-join-date">${joinDate}</span>
                ${adminControls}
            </div>
        </div>`;
    }).join('');

    // Bind admin action handlers (CSP-safe, no inline handlers)
    list.querySelectorAll('.org-role-select').forEach(sel => {
        sel.addEventListener('change', function() {
            changeUserRole(this.dataset.userId, this.value, this);
        });
    });
    list.querySelectorAll('.btn-toggle-active').forEach(btn => {
        btn.addEventListener('click', function() {
            toggleUserActive(this.dataset.userId, this.dataset.isActive === 'true');
        });
    });
    list.querySelectorAll('.btn-force-logout').forEach(btn => {
        btn.addEventListener('click', function() {
            forceLogoutUser(this.dataset.userId);
        });
    });
    list.querySelectorAll('.btn-delete-member').forEach(btn => {
        btn.addEventListener('click', function() {
            deleteUser(this.dataset.userId, this.dataset.userName);
        });
    });
    list.querySelectorAll('.btn-resend-activation').forEach(btn => {
        btn.addEventListener('click', async function() {
            const userId = this.dataset.userId;
            const feedback = document.getElementById('orgInviteFeedback');
            this.disabled = true;
            this.textContent = '寄送中...';
            try {
                const res = await window.authManager.authenticatedFetch('/api/admin/resend-activation', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ user_id: userId }),
                });
                const data = await res.json();
                feedback.style.display = 'block';
                if (res.ok && data.success) {
                    feedback.textContent = '啟用信已重新寄出';
                    feedback.style.color = '#059669';
                } else if (res.status === 429) {
                    feedback.textContent = '請稍後再試（已超過重寄次數限制）';
                    feedback.style.color = '#dc2626';
                } else {
                    feedback.textContent = data.error || '寄送失敗';
                    feedback.style.color = '#dc2626';
                }
                setTimeout(() => { feedback.style.display = 'none'; }, 3000);
            } catch (e) {
                const fb = document.getElementById('orgInviteFeedback');
                fb.textContent = '網路錯誤，請稍後再試';
                fb.style.color = '#dc2626';
                fb.style.display = 'block';
                setTimeout(() => { fb.style.display = 'none'; }, 3000);
            } finally {
                this.disabled = false;
                this.textContent = '重寄啟用信';
            }
        });
    });
}

export async function removeMember(orgId, userId, memberName) {
    if (!confirm(`Are you sure you want to remove "${memberName}"?`)) return;
    try {
        const res = await window.authManager.authenticatedFetch(`/api/org/${orgId}/members/${userId}`, { method: 'DELETE' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Remove failed');
        await openOrgModal();
    } catch (e) {
        alert(`Remove failed: ${e.message}`);
    }
}

// ==================== FEATURE 4: ADMIN USER CONTROLS ====================

export async function changeUserRole(userId, newRole, selectEl) {
    try {
        const res = await window.authManager.authenticatedFetch(`/api/admin/user/${userId}/role`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ role: newRole })
        });
        const data = await res.json();
        if (!res.ok) {
            alert(`角色變更失敗: ${data.error || '未知錯誤'}`);
            // Revert select to previous value
            selectEl.value = newRole === 'admin' ? 'member' : 'admin';
            return;
        }
        await openOrgModal();
    } catch (e) {
        alert(`角色變更失敗: ${e.message}`);
    }
}

export async function toggleUserActive(userId, currentlyActive) {
    const newActive = !currentlyActive;
    const label = newActive ? '啟用' : '停用';
    try {
        const res = await window.authManager.authenticatedFetch(`/api/admin/user/${userId}/active`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_active: newActive })
        });
        const data = await res.json();
        if (!res.ok) {
            alert(`${label}失敗: ${data.error || '未知錯誤'}`);
            return;
        }
        alert(`已${label}該使用者`);
        await openOrgModal();
    } catch (e) {
        alert(`${label}失敗: ${e.message}`);
    }
}

export async function forceLogoutUser(userId) {
    try {
        const res = await window.authManager.authenticatedFetch(`/api/admin/logout-user/${userId}`, { method: 'POST' });
        const data = await res.json();
        if (!res.ok) {
            alert(`強制登出失敗: ${data.error || '未知錯誤'}`);
            return;
        }
        alert('已強制登出該使用者');
    } catch (e) {
        alert(`強制登出失敗: ${e.message}`);
    }
}

export async function deleteUser(userId, userName) {
    if (!confirm(`確定要刪除帳號「${userName}」？此操作無法還原。`)) return;
    try {
        const res = await window.authManager.authenticatedFetch(`/api/admin/user/${userId}`, { method: 'DELETE' });
        const data = await res.json();
        if (!res.ok) {
            alert(`刪除失敗: ${data.error || '未知錯誤'}`);
            return;
        }
        await openOrgModal();
    } catch (e) {
        alert(`刪除失敗: ${e.message}`);
    }
}

export function handleInviteToken() {
    const params = new URLSearchParams(window.location.search);
    const token = params.get('invite');
    if (!token) return;
    // Clean URL
    const url = new URL(window.location.href);
    url.searchParams.delete('invite');
    window.history.replaceState({}, '', url);
    // Store for post-login if not yet authenticated
    sessionStorage.setItem('pendingInviteToken', token);
    if (window.authManager.isLoggedIn()) {
        showAcceptInviteToast(token);
    }
}

export function showAcceptInviteToast(token) {
    const toast = document.getElementById('acceptInviteToast');
    toast.style.display = 'flex';
    document.getElementById('btnAcceptInvite').onclick = () => acceptInvite(token);
    document.getElementById('btnDeclineInvite').onclick = () => {
        sessionStorage.removeItem('pendingInviteToken');
        toast.style.display = 'none';
    };
}

export async function acceptInvite(token) {
    try {
        const res = await window.authManager.authenticatedFetch('/api/org/accept-invite', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Accept failed');
        sessionStorage.removeItem('pendingInviteToken');
        document.getElementById('acceptInviteToast').style.display = 'none';
        alert('Successfully joined organization! Please refresh the page.');
        window.location.reload();
    } catch (e) {
        alert(`Accept invite failed: ${e.message}`);
    }
}

export function showAuthModal(tab = 'login') {
    const overlay = document.getElementById('authModalOverlay');
    overlay.style.display = 'flex';
    // Clear credential fields for security — prevents old values lingering after logout/failure
    const emailEl = document.getElementById('loginEmail');
    const passwordEl = document.getElementById('loginPassword');
    if (emailEl) emailEl.value = '';
    if (passwordEl) passwordEl.value = '';
    switchAuthTab(tab);
}

export function hideAuthModal() {
    document.getElementById('authModalOverlay').style.display = 'none';
    // Clear errors/success
    document.querySelectorAll('.auth-error, .auth-success').forEach(el => el.style.display = 'none');
}

export function switchAuthTab(tab) {
    document.getElementById('loginForm').style.display = tab === 'login' ? 'block' : 'none';
    document.getElementById('forgotPasswordForm').style.display = tab === 'forgot' ? 'block' : 'none';

    document.getElementById('tabLogin').classList.toggle('active', tab === 'login');

    // Clear messages
    document.querySelectorAll('.auth-error, .auth-success').forEach(el => el.style.display = 'none');
}

// Helper: get current user id (falls back to TEMP_USER_ID for unauthenticated users)
export function getCurrentUserId() {
    if (window.authManager.isLoggedIn()) {
        return window.authManager.getCurrentUser().id;
    }
    return TEMP_USER_ID;
}

export function showMainUI() {
    document.getElementById('leftSidebar').style.display = '';
    document.getElementById('searchContainer').style.display = '';
    document.getElementById('initialState').style.display = '';
}

export function hideMainUI() {
    document.getElementById('leftSidebar').style.display = 'none';
    document.getElementById('searchContainer').style.display = 'none';
    document.getElementById('initialState').style.display = 'none';
}
