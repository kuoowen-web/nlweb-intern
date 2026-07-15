# Organization & Multi-Tenant Specification

> **Owner**: NLWeb Team
> **Last Updated**: 2026-07-10（spec drift reconcile — §8.2/§8.3 標 `_clearUserScopedStorageIfUserChanged` / `_resetMainUIState` 已 Task 13 移除，現走 `UserStateSync.fullReset`（`core/state-sync.js`）+ `_handleAuthFailure`（`core/auth-manager.js`）；§7.6 前端 render 定位 `news-search.js`→`static/js/features/sessions-list.js` + `sharing.js`）
> **狀態**: active（核心已實作，部分 gaps 列於 §10）
> **相關 spec**: `docs/specs/login-spec.md`（Auth 系統，本 spec 的上游）

---

## Table of Contents

- [1. 概覽](#1-概覽)
- [2. 資料模型](#2-資料模型)
- [3. User-Org 關係](#3-user-org-關係)
- [4. Bootstrap Token Onboarding](#4-bootstrap-token-onboarding)
- [5. Member CRUD（Admin-Only）](#5-member-crudadmin-only)
- [6. Org Admin Role](#6-org-admin-role)
- [7. Shared Session（組織空間共享）](#7-shared-session組織空間共享)
- [8. Cross-Org / Cross-User 隔離](#8-cross-org--cross-user-隔離)
- [9. Rate Limiting in B2B Context](#9-rate-limiting-in-b2b-context)
- [10. Known Gaps / Future Work](#10-known-gaps--future-work)

---

## 1. 概覽

### 1.1 設計動機

NLWeb 是 B2B 線上服務（D-2026-03 商業模式：純 B2B，不做個人版，`docs/decisions.md:381-384`），系統採用「組織制」（Organization-bound）資料模型：

- **每個 user 必屬於恰好 1 個 organization**（無個人版、無跨 org 共享帳號）
- **資料隔離以 `org_id` 為主軸**（user_id 是次級隔離）
- **不開放自助註冊**，由 admin 透過 bootstrap token 引導建帳號

此設計動機由兩個核心 decision 決定：

| Decision | Date | 來源 | 影響 |
|----------|------|------|------|
| B2B Onboarding：Bootstrap Token（非自助註冊） | 2026-03-17 | `docs/decisions.md:273-277` | 移除 register tab、Admin-only user creation、Setup page 走 token |
| B2B 純 org-bound model — 沒有個人用戶、跨 org 不歸戶 | 2026-03-17 | `memory/lessons-auth.md:49-52` | 移除 invite_member 前端、刪帳號 hard delete、user 不可跨 org |

D-2026-03-17 Bootstrap Token 的核心理由：

> B2B 服務需要控管誰能進入系統。開放自助註冊會引入未授權用戶，增加管理成本和安全風險。Bootstrap token 一次性設計確保每個 token 只能建立一個帳號，admin 全程掌控用戶引導。
> — `docs/decisions.md:276`

### 1.2 系統角色（Personas）

| 角色 | 來源 | 權限 |
|------|------|------|
| **Platform Admin** | NLWeb 公司內部（CLI 操作者） | 透過 `bootstrap_cli` 產生 bootstrap token；不存在 DB 中 |
| **Org Admin** | 客戶組織第一個註冊用戶（透過 bootstrap token）或被 Org Admin 升級的 member | `org_memberships.role = 'admin'`；可建/停用/刪除 member、可分享 session 給整個 org |
| **Org Member** | 由 Org Admin 透過 `admin_create_user` 建立 | `org_memberships.role = 'member'`；只能管理自己的 session、可讀組織共享 session |

### 1.3 資料隔離原則

```
Tenant Boundary：org_id
  ├─ Hard boundary：跨 org 完全隔離（user 換公司 = 新帳號）
  ├─ User-scoped：每個 user 在 org 內擁有自己的 sessions / private docs / preferences
  └─ Org-shared：admin / member 可主動將 session visibility 設為 'org' 共享給組織成員
```

**Cross-User Storage 紀律**：localStorage 是 origin-scoped 不是 user-scoped，登入 / 登出時必須清 user-scoped keys（D-2026-05-01，詳見 §8.2）。

---

## 2. 資料模型

### 2.1 organizations Table

**檔案**：`code/python/auth/auth_db.py:314-327`（SQLite）、`code/python/auth/auth_db.py:515-528`（PostgreSQL）

| 欄位 | 型別 | 說明 |
|------|------|------|
| `id` | TEXT/UUID | PK，`uuid.uuid4()` 生成 |
| `name` | TEXT/VARCHAR(255) | 組織顯示名 |
| `slug` | TEXT/VARCHAR(255) UNIQUE | URL-safe 縮寫，由 name lowercase 派生 |
| `plan` | TEXT/VARCHAR(50) | 訂閱方案標記（目前未使用） |
| `max_members` | INTEGER DEFAULT 5 | 成員上限（admin_create_user / invite_member 都會檢查） |
| `settings` | TEXT/JSON DEFAULT '{}' | 預留設定 JSON |
| `storage_quota_gb` | INTEGER DEFAULT 5 | 私文件儲存配額（目前未強制執行） |
| `monthly_search_limit` | INTEGER DEFAULT 1000 | 月度搜尋上限（目前未強制執行） |
| `created_at` | REAL/DOUBLE PRECISION | Unix epoch |
| `is_active` | INTEGER/BOOLEAN DEFAULT 1/TRUE | Soft-disable flag（目前未使用） |

**索引**：未直接 index `organizations`（FK 反向 lookup 走 `org_memberships`）。

### 2.2 users Table

**檔案**：`code/python/auth/auth_db.py:328-342`、`code/python/auth/auth_db.py:529-543`

| 欄位 | 型別 | 說明 |
|------|------|------|
| `id` | TEXT/UUID | PK |
| `email` | TEXT/VARCHAR(255) UNIQUE | 登入用 |
| `password_hash` | TEXT/VARCHAR(255) NULL | bcrypt；NULL 代表帳號未啟用（admin_create_user 後等使用者透過 activation token 設密碼） |
| `name` | TEXT/VARCHAR(255) | 顯示名 |
| `email_verified` | INTEGER/BOOLEAN | Bootstrap admin auto-verified；admin_create_user 建立的用戶在 `activate_account` 時 verified |
| `email_verification_token` | TEXT/VARCHAR(255) | Verification / activation token（複用同一欄位） |
| `email_verification_expires` | REAL/DOUBLE PRECISION | 48 小時 |
| `is_active` | INTEGER/BOOLEAN DEFAULT 1/TRUE | Admin 可 toggle（停用 / 啟用） |

**注意**：`users` 表本身**不含** `org_id`。User 與 org 的關聯透過 `org_memberships` 表表達（為了將來支援 user 跨 org 留彈性，但 D-2026-03-17 純 org-bound 決策後實際只允許 1 user 1 active membership）。

### 2.3 org_memberships Table（Junction）

**檔案**：`code/python/auth/auth_db.py:344-355`、`code/python/auth/auth_db.py:545-554`

| 欄位 | 型別 | 說明 |
|------|------|------|
| `id` | TEXT/UUID | PK |
| `user_id` | TEXT/UUID | FK → `users.id` ON DELETE CASCADE |
| `org_id` | TEXT/UUID | FK → `organizations.id` ON DELETE CASCADE |
| `role` | TEXT/VARCHAR(50) DEFAULT 'member' | `'admin'` 或 `'member'` |
| `invited_by` | TEXT/UUID NULL | 建立此 membership 的 admin user_id |
| `status` | TEXT/VARCHAR(50) DEFAULT 'active' | `'active'` / `'removed'`（remove_member soft delete 用） |
| `accepted_at` | REAL/DOUBLE PRECISION | 接受邀請或 admin_create_user 建立時間 |

**索引**（`auth_db.py:704-705`）：
```sql
CREATE INDEX idx_org_memberships_user ON org_memberships(user_id);
CREATE INDEX idx_org_memberships_org ON org_memberships(org_id);
```

### 2.4 JWT Payload Schema

**檔案**：`code/python/auth/auth_service.py:806-823`

JWT access token 攜帶 org context，所有 protected endpoint 透過 `auth_middleware` 注入到 `request['user']`：

```python
payload = {
    'user_id': user_id,
    'email': email,
    'name': name,
    'org_id': org_id,        # ← 由 list-membership-LIMIT-1 取得
    'role': role,            # ← 'admin' / 'member'
    'iat': int(now),
    'exp': int(now + 15*60), # 15 分鐘
}
```

`AuthService.login()` 取 active membership 的方式（`auth_service.py:311-316`）：

```python
membership = await self.db.fetchone(
    "SELECT org_id, role FROM org_memberships WHERE user_id = ? AND status = 'active' LIMIT 1",
    (user['id'],)
)
org_id = membership['org_id'] if membership else None
role = membership['role'] if membership else None
```

`LIMIT 1` 是純 org-bound model 下的物理保證 — 一個 user 只會有一個 active membership（D-2026-03-17）。

**Middleware 注入**（`code/python/webserver/middleware/auth.py:65-73`）：

```python
request['user'] = {
    'id': user_id,
    'name': payload.get('name'),
    'email': payload.get('email'),
    'org_id': payload.get('org_id'),
    'role': payload.get('role'),
    'authenticated': True,
    'token': token,
}
```

> ⚠️ **Token 撤銷延遲**：org_id 寫入 JWT payload，admin 改 user role / 強制 logout 後最長需等 15 分鐘 access token 過期才生效。延遲處理見 D4 (`docs/specs/login-spec.md:443`)，目前接受此 tradeoff。

---

## 3. User-Org 關係

### 3.1 純 Org-Bound Model

**Source of truth**：`memory/lessons-auth.md:49-52`（D-2026-03-17）

> 原始 login 系統有 `invite_member`（邀請已有帳號加入 org），假設 user 可以跨 org。B2B 場景不適用：user 永遠屬於 org，換公司 = 新帳號，不帶走資料。

實作上，`org_memberships` schema 雖然支援 user 對應多 org（無 UNIQUE 約束於 `user_id`），但 D-2026-03-17 決策下：

| 規則 | 強制方式 |
|------|---------|
| 一個 user 在系統中只能有一筆 active membership | `register_user` / `admin_create_user` 都會建立一筆 + status='active'；前端 invite flow 已移除 |
| `JWT.org_id` 必為 user 唯一 active membership | `auth_service.py:312` `LIMIT 1` |
| 換公司 = 新帳號 | 沒有 transfer membership API；也沒有 self-service「離開 org 加入新 org」流程 |
| 帳號刪除不歸戶 | `delete_user` hard delete，session 透過 `user_id = NULL` 保留資料但斷開歸屬（`auth_service.py:582-589`） |

### 3.2 帳號刪除（Hard Delete）

**檔案**：`code/python/auth/auth_service.py:542-598`（`delete_user`）

`admin_delete_user` 流程（5 步 hard delete）：

1. `DELETE FROM login_attempts WHERE email = ?`（`auth_service.py:563-567`）
2. `DELETE FROM refresh_tokens WHERE user_id = ?`（`auth_service.py:570-573`）
3. `DELETE FROM org_memberships WHERE user_id = ? AND org_id = ?`（`auth_service.py:576-579`）
4. `UPDATE search_sessions SET user_id = NULL WHERE user_id = ?`（`auth_service.py:582-589`）— 保留 session 資料但斷開 user 歸屬
5. `DELETE FROM users WHERE id = ?`（`auth_service.py:592-595`）

**設計理由**：
- **Hard delete user record**：B2B 場景下被刪除的 user 不應留下「殘骸 row」，避免後續 admin 看到 ghost member
- **Session user_id NULL**：保留組織歷史搜尋資料（org_id 仍在 search_sessions），但失去個人歸屬。Step 4 失敗會 log warning + capture sentry，不阻擋整體刪除（`auth_service.py:586-589`）

### 3.3 帳號停用（Soft Disable）

**檔案**：`code/python/auth/auth_service.py:504-538`（`set_user_active`）

與 hard delete 並存的較輕量操作：

- `users.is_active = false` → `login()` 拒絕並回 "Account is deactivated"（`auth_service.py:294-297`）
- 同時 `revoke_all_user_tokens()` 撤銷所有 refresh token（`auth_service.py:534-535`）
- 不刪除任何資料；可用 `set_user_active(is_active=true)` 恢復

| 操作 | API | 影響 |
|------|-----|------|
| Disable | `PATCH /api/admin/user/{user_id}/active` body `{is_active: false}` | 立即無法登入；JWT 仍有效到 15 分鐘過期 |
| Enable | `PATCH /api/admin/user/{user_id}/active` body `{is_active: true}` | 可再次登入 |
| Hard delete | `DELETE /api/admin/user/{user_id}` | 不可逆，連帶清 tokens / membership / session 歸屬 |

**設計權衡**：CEO 場景下「離職員工」用 disable，「誤建帳號 / 退費客戶」用 hard delete。

---

## 4. Bootstrap Token Onboarding（D-2026-03-17）

### 4.1 流程概覽

```
Platform Admin                 Customer Admin
─────────────                  ────────────────
$ bootstrap_cli                                  
  --org "Acme Co" --expires 72                   
        │                                        
        ▼                                        
  url=https://app/setup?token=<token>            
        │                                        
        └─── 透過 email / 私訊發送 ───────►       
                                          │      
                                          ▼      
                          GET /setup?token=xxx   
                            ├─ validate_bootstrap_token()
                            ├─ 顯示組織設定表單   
                            └─ POST /api/auth/register
                                ├─ register_user(... bootstrap_token)
                                ├─ create_organization(name, user_id)
                                ├─ admin = role='admin'（auto-verified）
                                └─ 標記 token used_at + used_by_email
```

### 4.2 bootstrap_tokens Table

**檔案**：`code/python/auth/auth_db.py:475-484`（SQLite）、`code/python/auth/auth_db.py:661-670`（PostgreSQL）

| 欄位 | 型別 | 說明 |
|------|------|------|
| `id` | TEXT | UUID |
| `token` | TEXT UNIQUE | `secrets.token_urlsafe(32)` |
| `org_name_hint` | TEXT DEFAULT '' | 預設組織名（CEO 在 CLI 時可選提供，setup form 預填） |
| `created_at` | REAL/DOUBLE PRECISION | |
| `expires_at` | REAL/DOUBLE PRECISION | 預設 72 小時後 |
| `used_at` | REAL/DOUBLE PRECISION NULL | 一次性：!= NULL 代表已使用 |
| `used_by_email` | TEXT NULL | 使用此 token 註冊的 admin email |

> 📌 **Spec vs Code Drift**（`login-spec.md:467` I1 待調查）：原 spec 有 `org_id` / `created_by` / `used_by`(user_id)，CLI 有 `--list/--revoke`。Code 是簡化版（`org_name_hint` / `used_by_email`，CLI 只 `--org/--expires`）。功能正常但完整度未達 spec 水準。本 spec 描述 code 實際狀態。

### 4.3 CLI 工具

**檔案**：`code/python/auth/bootstrap_cli.py`

```bash
python -m auth.bootstrap_cli --org "Company Name" --expires 72
```

輸出範例：

```
Bootstrap token created.
URL: https://app.twdubao.com/setup?token=<32 chars>
Expires in: 72 hours
```

### 4.4 Setup Page（`/setup?token=xxx`）

**檔案**：`code/python/webserver/routes/auth.py:943-1160`（`setup_page_handler`）

| 步驟 | 說明 | 來源 |
|------|------|------|
| 1. URL 進入 | `/setup` 在 `PUBLIC_ENDPOINTS`（`webserver/middleware/auth.py:23-24`） | |
| 2. Token 驗證 | `validate_bootstrap_token()` 查 `bootstrap_tokens` 並檢查 `used_at IS NULL` + `expires_at > now` | `auth_service.py:72-85` |
| 3. UI 顯示 | 獨立品牌化頁面（讀豹 logo + 深藍金色），與 login modal 分離；form 欄位：org_name / admin_name / email / password / password2 | `routes/auth.py:1067-1100` |
| 4. POST `/api/auth/register` | body 含 `bootstrap_token`、`org_name`、`name`、`email`、`password` | `routes/auth.py:1126-1137` |
| 5. `register_user()` 完成 | `password_hash` + 建 org + admin membership + 標記 token used | `auth_service.py:89-135` |
| 6. 跳轉 `/` 登入頁 | 2 秒後 `window.location.href = '/'` | `routes/auth.py:1145` |

### 4.5 Login Modal — 移除 Register Tab

D-2026-03-17 規定 B2B 不開放自助註冊，因此 login modal 不應有「註冊」tab：

> Login modal 移除「註冊」tab。 — `docs/decisions.md:275`

新用戶必須走 bootstrap token URL 或 admin 建帳號 + activation email 兩種路徑，不能透過 modal 自行註冊。

### 4.6 register_user() 實作要點

**檔案**：`code/python/auth/auth_service.py:89-135`

```python
async def register_user(self, email: str, password: str, name: str,
                        org_name: str = '', bootstrap_token: str = '') -> Dict[str, Any]:
    if not bootstrap_token:
        raise ValueError("Bootstrap token is required")
    token_row = await self.validate_bootstrap_token(bootstrap_token)
    # ... bcrypt + email validate ...
    await self.create_organization(org_name or token_row.get('org_name_hint', ''), user_id)
    # 標記 token 為 used（一次性）
    await self.db.execute(
        "UPDATE bootstrap_tokens SET used_at = ?, used_by_email = ? WHERE token = ?",
        (time.time(), email, bootstrap_token)
    )
    # Bootstrap admin auto-verified — 不寄 verification email
    return {..., 'email_verified': True}
```

特殊行為：
- Bootstrap admin **不寄驗證信**（auto-verified），E2 issue 修復後（`login-spec.md:455`）已確認此設計
- Bootstrap-created admin role 由 `create_organization()` 寫入（`auth_service.py:651-655`，`role='admin'`）

---

## 5. Member CRUD（Admin-Only）

### 5.1 Admin Create User（取代 invite_member 前端）

**檔案**：`code/python/auth/auth_service.py:139-203`（`admin_create_user`）

D-2026-03-17 純 org-bound model 下，前端 invite flow 移除（無「邀請已有帳號加入 org」概念）。改為 admin 直接建立 member 帳號，系統發 activation email 讓 member 自設密碼。

| 步驟 | 動作 | 程式碼 |
|------|------|-------|
| 1 | 驗證 admin role | `auth_service.py:147-152` |
| 2 | 檢查 org `max_members` 限制 | `auth_service.py:155-164` |
| 3 | 檢查 email 未被註冊 | `auth_service.py:166-168` |
| 4 | INSERT users（`password_hash=NULL`，等 activation） | `auth_service.py:175-180` |
| 5 | INSERT org_memberships（role 預設 member） | `auth_service.py:183-190` |
| 6 | 發送 activation email | `auth_service.py:193-194` |

API endpoint：`POST /api/admin/create-user`（`webserver/routes/auth.py:337-373`）。

### 5.2 List Org Members

**檔案**：`code/python/auth/auth_service.py:749-763`（`list_org_members`）

```sql
SELECT u.id, u.email, u.name, u.is_active, m.role, m.accepted_at
FROM org_memberships m JOIN users u ON m.user_id = u.id
WHERE m.org_id = ? AND m.status = 'active'
```

關鍵欄位 `u.is_active`（lessons-auth.md:44-47 教訓 — 首次實作漏了此欄位導致前端「已停用 badge」無法顯示，2026-03-17 修復）。

API endpoint：`GET /api/org/{id}/members`（`webserver/routes/auth.py:1290-1305`）。

### 5.3 Disable / Enable Member

**檔案**：`code/python/auth/auth_service.py:504-538`（`set_user_active`）

| 約束 | 程式碼 |
|------|-------|
| 不能 disable 自己 | `auth_service.py:507-508` raises PermissionError |
| 必須是 same-org admin | `auth_service.py:510-515` |
| 必須是 same-org 的 active member | `auth_service.py:518-523` |
| Disable 後立即撤銷所有 refresh token | `auth_service.py:534-535` |

API endpoint：`PATCH /api/admin/user/{user_id}/active`（`webserver/routes/auth.py:420-458`）。

### 5.4 Hard Delete Member

**檔案**：`code/python/auth/auth_service.py:542-598`（`delete_user`）

詳見 §3.2 流程。API endpoint：`DELETE /api/admin/user/{user_id}`（`webserver/routes/auth.py:461-488`）。

### 5.5 Change Member Role

**檔案**：`code/python/auth/auth_service.py:602-631`（`change_member_role`）

允許值：`'admin'` / `'member'`。約束：

- 不能改自己的 role（`auth_service.py:605-606`）
- 必須由 admin 操作（`auth_service.py:611-616`）
- 目標 user 必須是同 org active member（`auth_service.py:618-623`）

API endpoint：`PATCH /api/admin/user/{user_id}/role`（`webserver/routes/auth.py:491-527`）。

> ⚠️ JWT 中的 role 在改動後最多需 15 分鐘才生效（access token 過期）。如需即時生效應呼叫 `POST /api/admin/logout-user/{user_id}` 強制 logout 該用戶（`webserver/routes/auth.py:376-417`）。

### 5.6 Soft Remove vs Hard Delete

| 操作 | 結果 | API | 何時用 |
|------|------|-----|-------|
| `remove_member` | `org_memberships.status = 'removed'`，user 仍在 users 表 | `DELETE /api/org/{id}/members/{user_id}` | 早期設計，B2B 純 org-bound 後**不建議使用** |
| `set_user_active(false)` | `users.is_active = false`，撤銷 tokens | `PATCH /api/admin/user/{user_id}/active` | 暫時停權 |
| `delete_user` | Hard delete + session user_id NULL | `DELETE /api/admin/user/{user_id}` | 永久移除 |

`remove_member` 在 schema 上保留，但 B2B 場景下基本上應使用 `delete_user`（保證 user 完全離開系統，符合 D-2026-03-17）。

---

## 6. Org Admin Role

### 6.1 權限矩陣

| 操作 | Admin | Member | 端點 / 程式碼 |
|------|:-----:|:------:|---------|
| List org members | ✅ | ✅ | `GET /api/org/{id}/members` |
| Create user (member / admin) | ✅ | ❌ | `POST /api/admin/create-user` |
| Disable / enable member | ✅ | ❌ | `PATCH /api/admin/user/{user_id}/active` |
| Hard delete member | ✅ | ❌ | `DELETE /api/admin/user/{user_id}` |
| Change member role | ✅ | ❌ | `PATCH /api/admin/user/{user_id}/role` |
| Force logout member | ✅ | ❌ | `POST /api/admin/logout-user/{user_id}` |
| 自己的 session：CRUD / share | ✅ | ✅ | `routes/sessions.py` |
| 看其他 user 的 private session | ❌ | ❌ | — |
| 看其他 user 的 org-shared session | ✅ | ✅ | `GET /api/sessions/shared` |
| Share session 給 org | ✅ | ✅ | `PATCH /api/sessions/{id}/visibility` |

### 6.2 Admin 驗證 Pattern

每個 admin 操作的入口 handler 先檢查 JWT 中的 role：

```python
# 範例：admin_logout_user_handler (routes/auth.py:376-417)
membership = await db.fetchone(
    "SELECT role FROM org_memberships WHERE user_id = ? AND org_id = ? AND status = 'active'",
    (user_info['id'], org_id)
)
if not membership or membership['role'] != 'admin':
    return web.json_response({'error': 'Only admins can ...'}, status=403)
```

對於需要操作 cross-user 資料的 endpoint（admin_set_user_active / admin_delete_user / admin_change_role），service 層也會再查一次 admin role（doubly-validated）：例如 `auth_service.py:510-515`、`auth_service.py:547-552`。這是 defense-in-depth 而非冗餘 — JWT 可能因 race condition 過期後被使用，service 層查 DB 是 source of truth。

### 6.3 Admin 與 Member 權限差異

```
                    ┌────────────────────────────────────┐
                    │     Admin 與 Member 共同擁有：       │
                    │  - Login / logout / refresh         │
                    │  - 查 / 改 / 刪自己的 session         │
                    │  - 分享自己的 session 給 org         │
                    │  - 讀 org-shared session             │
                    │  - 私文件 CRUD（私人空間）           │
                    └────────────────────────────────────┘
                                      ▲
                                      │ extends
                                      │
                    ┌────────────────────────────────────┐
                    │           Admin 額外擁有：          │
                    │  - admin_create_user                │
                    │  - set_user_active / delete_user    │
                    │  - change_member_role               │
                    │  - logout_user / list_org_members   │
                    └────────────────────────────────────┘
```

---

## 7. Shared Session（組織空間共享）

### 7.1 設計目的

讓組織內的研究成果（搜尋對話 + accumulated articles + research report）可在團隊成員間共享，避免重複工作。共享為**主動分享 opt-in**，不是預設可見。

### 7.2 Visibility 模型

**檔案**：`code/python/core/session_service.py:419`（`VALID_VISIBILITY = {'private', 'team', 'org'}`）

| Visibility | 可見範圍 |
|-----------|---------|
| `'private'`（預設） | 只有 owner 自己可看 |
| `'team'` | 預留（目前等同於 'org'，未來可能引入 sub-team 概念） |
| `'org'` | 整個 org 的所有 member 都可看 |

> 📌 目前前端只實作 `private` ⇄ `org` 切換（`static/news-search.js:9925-9932`），`team` 為 schema-level 預留。

### 7.3 set_visibility API

**檔案**：`code/python/core/session_service.py:421-442`、`code/python/webserver/routes/sessions.py:297-334`

```http
PATCH /api/sessions/{id}/visibility
Content-Type: application/json

{ "visibility": "org" }
```

Service 層約束：

- 必須是 session owner（`WHERE id = ? AND user_id = ? AND org_id = ?`，`session_service.py:428-432`）
- visibility 值必須在 `VALID_VISIBILITY` 集合中（`session_service.py:424-425`）
- 設成 `'team'` 或 `'org'` 時 fire-and-forget audit log `session.share`（`routes/sessions.py:320-328`）

前端 `setSessionVisibility` 實作（`static/news-search.js:308-319`）：

```javascript
async setSessionVisibility(serverId, visibility) {
    if (!serverId) throw new Error('Session not saved to server yet');
    if (!this._isOnline()) throw new Error('Need to join an organization to share sessions');
    const res = await this._auth.authenticatedFetch(`/api/sessions/${serverId}/visibility`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ visibility })
    });
    // ...
}
```

### 7.4 GET /api/sessions/shared

**檔案**：`code/python/core/session_service.py:444-469`、`code/python/webserver/routes/sessions.py:337-360`

回傳同 org 內 visibility 為 'team' / 'org' 的所有 session（包含自己分享的）：

```sql
SELECT s.id, s.user_id, s.title, s.visibility, s.user_feedback,
       s.created_at, s.updated_at,
       u.name AS owner_name, u.email AS owner_email
FROM search_sessions s
LEFT JOIN users u ON u.id = s.user_id
WHERE s.org_id = ?
  AND s.visibility IN ('team', 'org')
  AND s.deleted_at IS NULL
ORDER BY s.updated_at DESC LIMIT ? OFFSET ?
```

關鍵設計：

- **JOIN users**：補上 owner 資訊（name / email）給前端顯示
- **org_id WHERE 過濾**：硬隔離 — 不可能讀到其他 org 的共享 session
- **不過濾 owner**：自己分享的 session 也會出現（讓 owner 確認分享狀態）
- **deleted_at IS NULL**：soft-deleted 的 session 不顯示

### 7.5 GET 單一 Shared Session

**檔案**：`code/python/core/session_service.py:471-491`（`get_session_shared`）

```sql
SELECT * FROM search_sessions
WHERE id = ? AND org_id = ? AND deleted_at IS NULL
  AND (user_id = ? OR visibility IN ('team', 'org'))
```

讀取邏輯：是 owner **或** session 已分享 → 可讀。`org_id` filter 是 hard boundary。

### 7.6 前端 UI：左 Sidebar 「組織空間」Tab

**檔案**：`static/news-search-prototype.html`（HTML tabs）、`static/js/features/sessions-list.js`（`renderLeftSidebarSessions` / `renderSharedSessions` render；v4.0 從 `news-search.js` 遷出，main.js 以 `window.*` bridge legacy callsite）。visibility toggle 在 `static/js/features/sharing.js`（`toggleSessionSharing`）。下方各 `news-search.js:NNNN` 行號為歷史定位，勿當現況——以 grep marker（函式名）為準。

#### Tab 結構

```
┌────────────────────────┐
│  我的對話 │ 組織空間 (3) │  ← left-sidebar-sessions-tab
├────────────────────────┤
│  ▸ 自己的 sessions      │  ← #leftSidebarSessions
│  ▸ ...                 │
├────────────────────────┤
│  ▸ 共享 session         │  ← #leftSidebarSessionsShared
│    Owner Name / 日期    │     （切到 tab 才 lazy load）
│  ▸ ...                 │
└────────────────────────┘
```

`組織空間 (N)` badge 在 `DOMContentLoaded` 時 pre-fetch 並設定（`static/news-search.js:995-1003`）：

```javascript
sessionManager.loadSharedSessions().then(sessions => {
    if (sessions && sessions.length > 0) {
        const sharedTab = document.querySelector('.left-sidebar-sessions-tab[data-sessions-tab="shared"]');
        if (sharedTab) {
            sharedTab.textContent = `組織空間 (${sessions.length})`;
            _sharedSessionsCache = sessions;
        }
    }
});
```

#### Optimistic UI

`toggleSessionSharing` 收到後端成功回應後，立即更新 in-memory + localStorage + 重新 render（`static/news-search.js:9920-9938`）：

```javascript
async function toggleSessionSharing(sessionId) {
    const session = savedSessions.find(s => matchSessionId(s.id, sessionId));
    const serverId = session._serverId || session.id;
    const isCurrentlyShared = session.visibility && session.visibility !== 'private';
    const newVisibility = isCurrentlyShared ? 'private' : 'org';
    try {
        await sessionManager.setSessionVisibility(serverId, newVisibility);
        session.visibility = newVisibility;
        localStorage.setItem('taiwanNewsSavedSessions', JSON.stringify(savedSessions));
        renderLeftSidebarSessions();
    } catch (err) { ... }
}
```

### 7.7 Shared Session Click 不 Spawn（Commit `138ae61`）

**問題**（D-2026-05-01）：點擊組織空間 tab 中的 shared session 時，`currentLoadedSessionId` 設成另一個 user 的 PG row UUID。如果該 session 不在 `savedSessions` array 中（because shared），原 `saveCurrentSession()` 流程的 `findIndex` 會回 -1，落入 push-new-entry 分支 → POST `/api/sessions` → spawn 一筆當前 user 自己的 row（cross-user spawn）。

**修法**（commit `138ae61`，`static/news-search.js:1700-1722`）：

雙重 guard：

1. **Shared session click handler** 把 server payload map snake_case → camelCase 並 tag `_isShared: true` + `_ownerUserId: s.user_id`（`static/news-search.js:10581-10589`）

```javascript
const sharedHydrated = {
    id: s.id,
    _serverId: s.id,
    _isShared: true,
    _ownerUserId: s.user_id,
    title: s.title,
    visibility: s.visibility,
    conversationHistory: s.conversation_history ?? [],
    // ...
};
```

2. **`saveCurrentSession()` 函數頭加雙層 early return**：

```javascript
const currentEntry = currentLoadedSessionId !== null
    ? savedSessions.find(s => matchSessionId(s.id, currentLoadedSessionId))
    : null;
if (currentEntry && currentEntry._isShared) {
    console.warn('[saveCurrentSession] skipped: current session is shared (read-only context)');
    return;
}
if (currentLoadedSessionId !== null && !currentEntry) {
    console.warn('[saveCurrentSession] skipped: currentLoadedSessionId not in savedSessions');
    return;
}
```

通則來自 `memory/lessons-frontend.md`：

> 用 `currentLoadedSessionId` 作為「current session is owned by me」的隱含假設，遇到 shared session 就破功。明確 tag 物件來源（`_isShared`、`_ownerUserId`）比靠 ID shape 推測更安全。

### 7.8 Permission Model 概覽

```
┌──────────────────────────────────────────────────────────────┐
│  PATCH /api/sessions/{id}/visibility（set sharing）          │
│    ├─ Auth required                                          │
│    ├─ JWT.org_id required                                    │
│    └─ 必須是 session owner（user_id 相符）                    │
├──────────────────────────────────────────────────────────────┤
│  GET /api/sessions/shared（list shared in org）              │
│    ├─ Auth required                                          │
│    ├─ JWT.org_id required                                    │
│    └─ 只回 same org 的 visibility != 'private' session        │
├──────────────────────────────────────────────────────────────┤
│  GET /api/sessions/{id}（single session）                    │
│    ├─ Auth required                                          │
│    ├─ JWT.org_id required                                    │
│    └─ Owner OR (same org AND visibility != 'private')        │
└──────────────────────────────────────────────────────────────┘
```

---

## 8. Cross-Org / Cross-User 隔離

### 8.1 Org_id WHERE Filter（D-2026-03-17）

**原則**：所有 org-scoped 資料 query 必須在 WHERE 子句中過濾 `org_id = JWT.org_id`。

| Module | Status | 程式碼 |
|--------|:------:|-------|
| `core/session_service.py` list/get/update/delete sessions | ✅ | `session_service.py:50-65`、`session_service.py:428-440` 等多處 |
| `core/session_service.py` shared sessions | ✅ | `session_service.py:457-461`（`s.org_id = ?`） |
| `retrieval_providers/user_postgres_provider.py` private docs | ✅ | `user_postgres_provider.py:171-173`（org_id filter clause） |
| `auth/auth_service.py` admin operations | ✅ | 每個 admin 方法都查 `org_memberships WHERE org_id = ?` |
| Web search articles / chunks | N/A | 文章是公開資料，無 org boundary |
| `core/user_data_processor.py` private doc indexing | ✅ | `user_data_processor.py:40,93` 寫入時帶 org_id |

> ⚠️ `login-spec.md:88` 標註 user_data_manager `org_id filter 只有寫入沒有查詢`，但 `user_postgres_provider.py:171-173` 已實作 query filter（2026-03-27 PG 遷移時補上）。Spec 落差待清理（見 §10）。

### 8.2 Frontend localStorage 不能跨 User（D-2026-05-01）

**問題**：localStorage 是 origin-scoped 不是 user-scoped。B2B 共用電腦切帳號（admin logout → member login）時，未清理會看到上一個 user 的 sessions / folders。

**修法摘要**（原始 patch commit `24d39f4`、`e43468d`、`3b9f7d8`、`138ae61`；後於 D-2026-05-13 收斂為 `UserStateSync`）：

> ⚠️ **已 superseded by D-2026-05-13 Frontend Init Sync Refactor + v4.0 模組化**。下方流程描述的 `_clearUserScopedStorageIfUserChanged()` / `_resetMainUIState()` 兩個 helper **已完全從 codebase 移除**（Task 13 cleanup）。現況：cross-user 清理由 `UserStateSync.runInitSync`（`static/js/core/state-sync.js`）內部 `fullReset()`（= `clearUserScopedState` + `resetMainUI`）在 trigger A/B/C/D 統一執行，**無條件清**（不再做「同 user 保 cache」的 conditional）。清理範圍語義不變，見 `docs/specs/session-spec.md` §8.3/§8.5、`docs/specs/login-spec.md` §1F-C。

```
┌─────────────────────────────────────────────────────────────────┐
│  現況（D-2026-05-13 後，7-trigger 統一寫入）：                    │
│                                                                 │
│  (1) Login（trigger A）/ identity change（trigger B）           │
│        → UserStateSync.runInitSync()                           │
│          ├─ fullReset()（無條件清 USER_SCOPED_KEYS 6 keys +      │
│          │    6 個 main-UI globals；device-scoped prefs 不動）   │
│          └─ fetchInit() + applyInit() 重新 hydrate             │
│                                                                 │
│  (2) Logout（trigger C）/ 401·refresh fail（trigger D）          │
│        → _handleAuthFailure() → UserStateSync.fullReset()      │
│          （CEO 拍板：B2B 安全 > 同 user 重登保 cache）           │
│                                                                 │
│  (3) checkAuthOnLoad 401 path → 完整 _handleAuthFailure()       │
│        清 _user + localStorage + render 空 sidebar              │
│                                                                 │
│  (4) loadSessions logged-in 失敗 → [] + console.error          │
│        不 fallback localStorage（避免載入舊 user 資料）         │
└─────────────────────────────────────────────────────────────────┘
```

詳細見 `memory/lessons-auth.md`「Cross-User 隔離 / Logout 紀律」段 + `docs/specs/session-spec.md` §8。

### 8.3 Logout 紀律完整清單

**檔案**：`static/js/core/auth-manager.js`（`_handleAuthFailure`，:328-352）+ `static/js/core/state-sync.js`（`UserStateSync`），`docs/decisions.md` D-2026-05-13。

任何 user-scoped state 清理都必須先 cancel pending timer / promise（`memory/lessons-auth.md`「Debounce timer 跨 auth 邊界」段）：

```
_handleAuthFailure() 入口（現況，D-2026-05-13 後）：
  └─ UserStateSync.fullReset({ keepInviteToken: false })
        ├─ _cancelPendingSave()              ← 取消 debounced PUT（state mutation 前先 cancel）
        ├─ clearUserScopedState()            ← 清 USER_SCOPED_KEYS + 6 個 main-UI globals
        └─ resetMainUI()                     ← try/catch wrap resetConversation
  ↳ 收尾：null _accessToken/_user + updateAuthUI() + hideMainUI() + showAuthModal('login')
```

> ⚠️ 舊流程列的 `_resetMainUIState()` / `_clearUserScopedStorageIfUserChanged(null)` 兩個 helper **已移除**（Task 13 cleanup），收斂進 `UserStateSync.fullReset`。

通則：

> 任何 user-scoped state 清理 = 先 cancel 所有 pending timer/promise。setTimeout closure 抓住的是 declaration-time 變數，cancellation 必須在 state mutation 之前。
> — `memory/lessons-auth.md:144`

### 8.4 Cross-User Spawn Defense（commit `138ae61`）

`SessionManager._postedRecently` 5 秒重複 POST 偵測 + console.error 警示 `_serverId` 漏洞 regression（D-2026-05-01，`docs/decisions.md:633`）。配合 §7.7 的 `_isShared` 雙層 guard 形成 cross-user spawn 防線。

---

## 9. Rate Limiting in B2B Context

### 9.1 設計原則（D-2026-02-15）

**檔案**：`docs/decisions.md:164-167`

> B2B 模型下所有使用者已認證，以 **user_id 為主要限制鍵**（比 IP 更精確，不受共享 IP 影響）。**IP 限制只套用在未認證請求**（防爬蟲/DDoS），已認證用戶完全走 user_id/session 級限制。

### 9.2 兩層限速機制

| 層級 | 鍵 | 適用場景 | 程式碼 |
|------|-----|---------|-------|
| **L1 Endpoint Rate Limit** | IP | 未認證 endpoint（register / login / forgot-password） | `webserver/middleware/rate_limit.py:55-78` |
| **L2 User-Scoped Concurrency** | user_id / session | 已認證查詢（`/ask` / DR / LR） | `core/concurrency.py`（pending tracking issue） |

### 9.3 L1 Endpoint Rate Limit（IP-based）

**檔案**：`code/python/webserver/middleware/rate_limit.py`

| Endpoint | Limit | 用途 |
|----------|-------|------|
| `/api/auth/register` | 5/hour | 防大量 bootstrap token brute force |
| `/api/auth/forgot-password` | 3/hour | 防 email 騷擾 |
| `/api/auth/login` | 10/min | 防 brute force（與 BRUTE_FORCE_MAX_ATTEMPTS=5 / 15min 並行） |

實作機制：sliding window in-memory dict，per-IP 累計。Window 過期自動 evict（`rate_limit.py:50-51`）。

**IP 取得**（`webserver/middleware/ip_utils.py`）：trusted-proxy 驗證，只信 loopback / Docker 網路的 `X-Forwarded-For`，否則用 `request.remote`（D-2026-03-27 修正後，`login-spec.md:275`）。

### 9.4 L2 User-Scoped Concurrency（user_id-based）

| Resource | Limit | 鍵 |
|----------|-------|-----|
| Deep Research | 1 同時執行 | session_id |
| 一般查詢 | 5 同時執行 | session_id |
| LR | TBD | session_id |

理由：B2B 同 IP 多 user（共用辦公網路）不應互相影響。session-level 限制比 user-level 更精細（同 user 多 tab）。

### 9.5 Public Endpoints 認證關係

`PUBLIC_ENDPOINTS`（`webserver/middleware/auth.py:13-39`）內的 endpoint 不要求 JWT，但仍有 IP rate limit：

```
登入流程：login (IP rate limit) → JWT issued → 後續 /ask 等 (auth + user_id rate limit)
```

`/setup`、`/api/auth/register`、`/api/analytics/event` 等公開 endpoint 透過 IP rate limit 防 abuse；認證後 endpoint 走 user_id-based 限制。

### 9.6 共享 IP 不誤擋已認證用戶

D-2026-02-15 的核心 tradeoff：

> 同 IP 的已認證用戶互不影響，但未認證請求的 IP 限制（DR 3/IP）可能誤擋共享 IP 的未登入用戶
> — `docs/decisions.md:167`

對 B2B 大客戶（如同公司 NAT 網段共用一 IP）此設計確保 admin / member 不互相影響。

---

## 10. Known Gaps / Future Work

### 10.1 Spec vs Code 落差

| # | 項目 | 狀態 | 來源 |
|---|------|------|------|
| G1 | `bootstrap_tokens` schema 簡化版（無 `org_id` / `created_by` / `used_by` user_id；CLI 無 `--list/--revoke`） | open（功能正常但完整度未達原 spec） | `login-spec.md:467` I1 |
| G2 | Soft `remove_member` API 仍存在但 D-2026-03-17 後不建議使用 | 應評估是否移除 / 改名 | `auth_service.py:765-783` |
| G3 | `team` visibility 為 schema-level 預留，目前前端只切 private ⇄ org | 未使用，待確認是否實作 sub-team | `session_service.py:419` |
| G4 | `organizations.max_members / storage_quota_gb / monthly_search_limit` 部分未強制執行 | `max_members` 已執行，配額 column 預留 | `auth_db.py:320-323` |

### 10.2 Architectural Limitations

| # | 限制 | 影響 | Mitigation |
|---|------|------|----------|
| L1 | JWT 中 org_id / role 撤銷延遲（access token 15 分鐘有效） | Admin 改 role / 強制 logout 後最長 15 分鐘 stale | `revoke_all_user_tokens` 撤銷 refresh token，強制下次 refresh；`/api/admin/logout-user/{user_id}` 主動撤銷 |
| L2 | `org_memberships` schema 支援 cross-org，但業務規則禁止（D-2026-03-17） | 未來若放寬需重新設計 | LIMIT 1 取首個 active membership 是純 org-bound 的物理保證 |
| L3 | localStorage 是 origin-scoped 不是 user-scoped | B2B 共用電腦切帳號需 active 清理 | §8.2 全套清理紀律（D-2026-05-01） |
| L4 | Rate Limit 為 single-instance in-memory | 多 instance 部署需 Redis | 目前單 VPS 部署無此問題（D6 in `login-spec.md:445`） |

### 10.3 Future Work

| # | 項目 | 優先度 | 備註 |
|---|------|:------:|-----|
| F1 | Org-level audit dashboard（admin 可看 org 內所有 audit log） | Medium | `audit_logs` 表已 index `org_id`（`auth_db.py:717`） |
| F2 | Bootstrap Token CLI 補 `--list / --revoke` + 完整 schema（`org_id`, `created_by`, `used_by`） | Low | 完整度提升，目前可運作 |
| F3 | `team` visibility 實作 sub-team 概念 | Low | 視 B2B 客戶實際需求 |
| F4 | Org-scoped storage quota 強制執行 | Low | 需先有 monitoring + 計費邏輯 |
| F5 | Cross-org transfer member 流程 | Future | D-2026-03-17 純 org-bound 下不需要；若有併購等場景再評估 |

---

## 附錄：相關 commit / 決策索引

| Commit | 主題 | Files |
|--------|------|-------|
| `138ae61` | sort order + cross-user spawn paths | `static/news-search.js`、`static/news-search-prototype.html` |
| `24d39f4` | _clearUserScopedStorageIfUserChanged | `static/news-search.js` |
| `e43468d` | logout user-scoped storage sweep | `static/news-search.js` |
| `3b9f7d8` | _handleAuthFailure 完整 cleanup | `static/news-search.js` |

| Decision | Date | 影響章節 |
|----------|------|---------|
| D-2026-03-17 B2B Onboarding：Bootstrap Token | 2026-03-17 | §1, §4, §5 |
| D-2026-03-17 B2B 純 org-bound model | 2026-03-17 | §1, §3 |
| D-2026-03-13 前端 Session 資料架構：localStorage 為主 | 2026-03-13 | §8.2 |
| D-2026-05-01 跨用戶 Session 隔離紀律 | 2026-05-01 | §7.7, §8.2-8.4 |
| D-2026-02-15 Rate Limiter：user_id 為主、IP 為輔 | 2026-02-15 | §9 |
| D-2026-03-05 Login 系統接手：Surgical Merge | 2026-03-05 | 全文（系統來源 context） |
