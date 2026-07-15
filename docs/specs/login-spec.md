# Login System Specification

> **Owner**: NLWeb Team (接手自外部 dev)
> **Last Updated**: 2026-07-11（兩條收斂：① 1E baseHandler「部分 vs Done」矛盾統一 — baseHandler.py:124 本身未改但上游注入完成，Implementation Status 與 §1E 表述一致化；② I1 bootstrap_tokens 結案 — Part 6 Table Schema/CLI 段改記 code 現況，原規劃增強標「待 CEO」）。前版 2026-07-10（spec drift reconcile — §1F-A/B 標 superseded：AuthManager/`_handleAuthFailure` 已遷 `static/js/core/auth-manager.js`，現走 `UserStateSync.fullReset()`；`_clearUserScopedStorageIfUserChanged` 已 Task 13 移除；行號改 grep-marker；§1F-C trigger D 補 AbortError UX 契約 commit `6c60faf7`）
> **Source repo**: `c0925028920-cpu/taiwan-news-ai-search-RG`

---

## Table of Contents

- [Context](#context)
- [Architecture Decisions](#architecture-decisions)
- [Implementation Status](#implementation-status)
- [Part 1: Auth System](#part-1-auth-system)
- [Part 2: Session Management](#part-2-session-management)
- [Part 3: Security Hardening](#part-3-security-hardening)
- [Part 4: Data Migration](#part-4-data-migration)
- [Part 5+: Research Collaboration](#part-5-research-collaboration)
- [Infra Adaptation](#infra-adaptation)
- [Part 6: Bootstrap Token Onboarding](#part-6-bootstrap-token-onboarding)
- [Known Gaps](#known-gaps)
- [File Inventory](#file-inventory)
- [Environment Variables](#environment-variables)
- [Dependencies](#dependencies)
- [Cost Analysis](#cost-analysis)

---

## Context

系統轉型為 B2B 線上服務，需要：

1. 組織制 Email/Password 登入（取代已刪除的 OAuth）
2. Server-side 對話/偏好管理（取代 localStorage）
3. Email 服務（Resend）支援驗證、邀請、密碼重設
4. WebSocket chat 已移除（B2B 不需要，僅保留 SSE 搜尋串流）

### 前置清理（已完成）

- OAuth 系統（`oauth.py`、`config_oauth.yaml`）已刪除
- WebSocket chat（`chat/` 目錄 9 檔、`routes/chat.py`）已刪除
- 保留 `routes/conversation.py`（SSE 對話歷史 API）

---

## Architecture Decisions

### DB: PostgreSQL（統一）

原 spec 選用 Neon PostgreSQL（擴展 analytics DB）。Infra migration 後改為自建 PostgreSQL on Hetzner VPS。Auth tables 與 articles/chunks tables 共存於同一 DB。

### Token Strategy

| 類型 | 策略 | 時效 |
|------|------|------|
| Access Token | JWT (HS256), payload: `{user_id, email, name, org_id, role}` | 15 分鐘 |
| Refresh Token | `secrets.token_urlsafe(64)`, DB 存 SHA256 hash | 7 天 |
| Password | bcrypt hash | - |
| Brute Force | 同一 email 15 分鐘內失敗 5 次鎖定 | 15 分鐘 |

### Multi-tenancy

組織制：每個 user 屬於 1+ 個 organization，JWT 含 `org_id` + `role`。資料隔離靠 `WHERE org_id = $n`。

---

## Implementation Status

> 以下狀態基於 2026-03-05 對 RG repo 程式碼的審計結果。
> 標記規則：已驗證 = 程式碼存在且邏輯正確；未驗證 = spec 宣稱完成但程式碼不符或不存在。

| Phase | 內容 | 狀態 | 備註 |
|-------|------|------|------|
| 0A | 移除 OAuth | **已驗證** | 檔案確認刪除 |
| 0B | 移除 WebSocket Chat | **已驗證** | 檔案確認刪除 |
| 1A | DB Schema | **已驗證** | auth_db.py auto-create, 12 tables |
| 1B | Auth Service | **已驗證** | 14 public methods, bcrypt+JWT |
| 1B | Email Service | **已驗證** | 4 send methods (含 lockout) |
| 1C | Auth API Routes | **已驗證** | 8 auth + 6 org endpoints |
| 1D | Auth Middleware | **已驗證** | JWT 驗證（dev bypass 已 DELETED 2026-05-19 commit `c20f545`） |
| 1E | user_id 模式修復 | **已驗證（上游注入）** | user_data.py OK；baseHandler.py 檔案本身未改（仍讀 query_params），但 `/ask` 強制 auth + api.py handler 注入使 user_id 可信 — 兩層合起來等同完成（統一表述見 §1E，2026-07-11 收斂） |
| 1F | 前端 Login UI | **已驗證** | AuthManager + modal + TEMP_USER_ID 移除 |
| 2A | Session Schema | **已驗證** | Alembic migration 存在 |
| 2B | Session API | **已驗證** | routes/sessions.py, 15 endpoints |
| 2C | Session Service | **已驗證** | JSONB append + 200KB 監控 |
| 2D | 前端遷移 | **已驗證** | SessionManager, localStorage 移除 |
| 2E | 組織隔離 | **部分** | ~~user_qdrant_provider~~ → user_postgres_provider OK（2026-03-27 遷移）; **user_data_manager org_id filter 只有寫入沒有查詢** |
| 3A | Rate Limiting | **已驗證（值不同）** | 實際值比 spec 寬鬆 5-17 倍（見下方） |
| 3B | Audit Logs | **已驗證** | audit_service.py + routes/audit.py |
| 3C | CORS | **已驗證** | cors.py, ALLOWED_ORIGINS |
| 4A | Data Migration | **已驗證** | migrate_to_b2b.py 存在 |
| 5B | Session Sharing | **已驗證** | visibility + shared_with |
| Tests | 69 tests (31+14+24) | **不存在** | 3 個 test 檔案全部 404 |

---

## Part 1: Auth System

### 1A — DB Schema

**檔案**: `auth/auth_db.py` — AuthDB singleton, SQLite/PostgreSQL 雙支援, 啟動時 auto-create。

**Tables（12 張，跨所有 Sprint）**:

| Table | 說明 | Sprint |
|-------|------|--------|
| `organizations` | 組織 (id, name, slug, plan, max_members, settings) | 1 |
| `users` | 使用者 (id, email, password_hash, name, email_verified, tokens) | 1 |
| `org_memberships` | 組織成員 (user_id, org_id, role, status) | 1 |
| `invitations` | 邀請 (org_id, email, token, expires_at) | 1 |
| `refresh_tokens` | Refresh Token (token_hash, expires_at, revoked_at) | 1 |
| `login_attempts` | 登入嘗試 (email, ip_address, success, attempted_at) | 1 |
| `search_sessions` | 搜尋 Session (user_id, org_id, history, articles) | 3 |
| `org_folders` | 組織資料夾 | 3 |
| `org_folder_sessions` | Junction: folder-session | 3 |
| `session_shares` | Junction: session-share | 3 |
| `user_preferences` | 使用者偏好 (key-value JSONB) | 3 |
| `audit_logs` | 稽核日誌 | 5 |

**Alembic Migrations**:
- `9df501ad9a13` — baseline: 6 auth tables
- `c1c6deac2013` — session tables (5 tables)
- `a3f8c2e51d07` — audit_logs
- `b5e9d3f71a42` — infra tables (articles + chunks, 適配新 infra)

### 1B — Auth Service

**檔案**: `auth/auth_service.py` — 14 public methods

| 方法 | 已驗證 | 說明 |
|------|--------|------|
| `register_user(email, password, name)` | Yes | bcrypt hash + 驗證 email |
| `verify_email(token)` | Yes | email_verified = true |
| `login(email, password, ip)` | Yes | brute force check + JWT + refresh |
| `refresh_token(token)` | Yes | SHA256 比對 + 新 access token |
| `logout(refresh_token)` | Yes | 撤銷 refresh token |
| `forgot_password(email)` | Yes | 不洩漏 email 是否存在 |
| `reset_password(token, new_pw)` | Yes | 更新密碼 + 撤銷所有 refresh |
| `create_organization(name, admin_user_id)` | Yes | 建 org + admin membership |
| `invite_member(org_id, email, role, invited_by)` | Yes | 驗證 admin + 人數上限 |
| `accept_invitation(token, user_id)` | Yes | token + email match |
| `list_user_orgs(user_id)` | Yes | |
| `list_org_members(org_id, requester)` | Yes | 驗證是成員 |
| `remove_member(org_id, target, requester)` | Yes | admin only, 不可移除自己 |
| `get_user_by_id(user_id)` | Yes | 不含 password_hash |

### 1B — Email Service

**檔案**: `auth/email_service.py` — Resend (production) / console log (dev)

| 方法 | 說明 |
|------|------|
| `send_verification_email(email, token, name)` | 註冊驗證 |
| `send_password_reset_email(email, token, name)` | 密碼重設（1 小時） |
| `send_invitation_email(email, org_name, inviter_name, token)` | 組織邀請（7 天） |
| `send_lockout_notification(email, name)` | 帳號鎖定通知 |

### 1C — Auth API Routes

**檔案**: `webserver/routes/auth.py`

**Auth**:

| Method | Endpoint | 說明 |
|--------|----------|------|
| POST | `/api/auth/register` | 註冊 |
| GET | `/api/auth/verify-email?token=xxx` | 驗證 email |
| POST | `/api/auth/login` | 登入 (access_token + HttpOnly refresh cookie) |
| POST | `/api/auth/refresh` | 刷新 (cookie or body) |
| POST | `/api/auth/logout` | 登出 |
| GET | `/api/auth/me` | 目前使用者 |
| POST | `/api/auth/forgot-password` | 忘記密碼 |
| POST | `/api/auth/reset-password` | 重設密碼 |

**Organization**:

| Method | Endpoint | 說明 |
|--------|----------|------|
| POST | `/api/org` | 建立組織 |
| GET | `/api/org` | 列出組織 |
| POST | `/api/org/{id}/invite` | 邀請 |
| GET | `/api/org/{id}/members` | 列出成員 |
| DELETE | `/api/org/{id}/members/{user_id}` | 移除成員 |
| POST | `/api/org/accept-invite` | 接受邀請 |

**新增 Auth（2026-03-16）**:

| Method | Endpoint | 說明 |
|--------|----------|------|
| POST | `/api/auth/change-password` | 已登入改密碼 |
| POST | `/api/auth/logout-all` | 登出全部裝置（撤銷所有 refresh token） |

**新增 Admin（2026-03-16）**:

| Method | Endpoint | 說明 |
|--------|----------|------|
| POST | `/api/admin/logout-user/{user_id}` | Admin 強制登出指定用戶 |
| PATCH | `/api/admin/user/{user_id}/active` | 停用/啟用帳號 |
| DELETE | `/api/admin/user/{user_id}` | 刪除帳號 |
| PATCH | `/api/admin/user/{user_id}/role` | 修改角色 |

**Cookie 設定**: `Set-Cookie: refresh_token` (HttpOnly, Secure=request.secure, SameSite=Lax, path=/api/auth)

### 1D — Auth Middleware

**檔案**: `webserver/middleware/auth.py` — 完整重寫

- Token 來源優先順序: Bearer header > cookie > query param (GET only)
- JWT 解碼失敗 -> 401（非靜默放行）
- JWT_SECRET 未設定 -> 500
- `request['user']` 含 user_id, org_id, role, authenticated
- **Dev bypass: 已 DELETED 2026-05-19（commit `c20f545`）**。E2E 強制真實 `admin@twdubao.com / test1234!`（小寫 t）登入。Auth bypass 在 v9-v15 LR E2E 造成兩次 P0，且 dev/prod auth path 不對稱、handoff 密碼大小寫錯誤皆是 root cause。詳見 `memory/lessons-auth.md` 2026-05-19 段。
- `/ask` 已從 PUBLIC_ENDPOINTS 移除（需 auth）
- `/sites_config` 在 PUBLIC_ENDPOINTS（唯讀站台配置，無敏感資料）

### 1E — user_id 修復

| 檔案 | 狀態 | 說明 |
|------|------|------|
| `webserver/routes/user_data.py` | Done | 從 `request['user']['id']` 取值 |
| `core/baseHandler.py` | Done（上游注入；檔案本身未改） | `baseHandler.py:124` 仍 `get_param(query_params, "user_id")` — 設計上不動 handler，信任鏈由上游保證：(1) `/ask` 不在 PUBLIC_ENDPOINTS（`middleware/auth.py`），必有有效 JWT；(2) api.py handler 把 `request['user']` 的 user_id/org_id **注入 query_params 覆蓋偽造**（`/ask` `api.py:107-112`、rerun `:971-976`、LR start `:1180-1184`、LR continue `:1378-1382`）。2026-07-11 收斂：本表與 Implementation Status 表原先一「Done」一「部分」是同一事實的兩半，已統一為此表述。**注**：直連 `POST/GET /api/deep_research` 的 handler 原**未做注入** = P0 跨 user/org 私文讀取洩漏（前端另有走 `/api/deep_research` 直連的 caller `deep-research.js:1469`，非只走 `/ask`，故真實可觸發）。**2026-07-14 CEO 拍板補**：修法 AR R1→R2 三家 APPROVE 定稿、code 完成（7 commit in worktree，**未 merge**，backlog 票 `2026-07-14-d`）。三層根解：DR handler 補注入（抽 `inject_auth_user_into_params` helper）+ baseHandler `_resolve_trusted_identity` L2 收斂只認 JWT + provider org 強制隔離。**⚠️ 未 merge 前本注不改「Done」——merge+push+prod E2E 後再回來把 baseHandler 那行的信任鏈更新為「L2 已收斂」。** |
| ~~`storage_providers/qdrant_storage.py`~~ | 🪦 檔案已刪（Qdrant 2026-06-22 徹底廢除） | user_id filter 職責由 `retrieval_providers/user_postgres_provider.py` 承接（mandatory user_id + optional org_id） |

### 1F — 前端 Login UI

**已完成**:
- AuthManager class (login, register, refreshToken, logout, authenticatedFetch)
- Request Queue 機制 (多個 401 同時觸發時只 refresh 一次)
- Login/Register modal
- TEMP_USER_ID 全部移除
- SSE 斷線重連 + token refresh

#### 1F-A — Cross-User Storage Isolation（2026-05-01 update）

> ⚠️ **已 superseded by v4.0 前端模組化（2026-05-25）+ D-2026-05-13 Frontend Init Sync Refactor**。
> AuthManager 已從單體 `static/news-search.js` 遷至 `static/js/core/auth-manager.js`（grep `export class AuthManager`）。本節 `_clearUserScopedStorageIfUserChanged` 相關描述屬歷史 —— 該 helper 已於 **Task 13 cleanup 完全移除**（`auth-manager.js` 內 `// Task 13 cleanup` 註解為證），cross-user 清理改由 `UserStateSync.runInitSync`（內部 `fullReset` = `clearUserScopedState` + `resetMainUI`）在 trigger A/B/C/D 統一執行。**`USER_SCOPED_KEYS` 清單本身仍 active**（權威定義 `AuthManager.USER_SCOPED_KEYS` static array，grep `USER_SCOPED_KEYS` in `auth-manager.js`）；下方 `news-search.js` 行號為歷史定位，勿當現況。權威現況見 §1F-C + `docs/specs/session-spec.md` §1/§8.5。

**`AuthManager.USER_SCOPED_KEYS`**（grep `static USER_SCOPED_KEYS` in `static/js/core/auth-manager.js`；歷史定位 `static/news-search.js:7-14`）— 6 個 user-scoped localStorage keys：

| Key | 說明 |
|-----|------|
| `taiwanNewsSavedSessions` | 對話 session 列表 |
| `taiwanNewsFolders` | 使用者資料夾 |
| `taiwanNewsSessionsMigrated` | 一次性 migration flag |
| `nlweb_source_folders` | 來源資料夾 |
| `nlweb_file_folders` | 檔案資料夾 |
| `nlweb_selected_files` | 已選檔案 |

> 註：device-scoped UI prefs（`nlweb-large-font`、`nlweb-kg-hidden`）刻意不在此清單，跨 user 保留。

**~~`_clearUserScopedStorageIfUserChanged(newUserId)`~~**（⚠️ 已移除，Task 13 cleanup）— 舊實作偵測 user 變化才清。現由 `UserStateSync.runInitSync` 內部 `fullReset` 無條件清（B2B 共用電腦安全 > 同 user 重登保 cache，CEO 拍板 D-2026-05-01）；匿名 / 離線的 localStorage 持久化（D-2026-03-13）走另一情境不衝突。

**`AuthManager.logout()`**（grep `async logout()` in `static/js/core/auth-manager.js`；:223-234）— 呼叫後端 `POST /api/auth/logout` 撤銷 refresh token，再呼叫 `_handleAuthFailure()` 統一清理。

**`_handleAuthFailure()` 中 USER_SCOPED_KEYS 清除** — logout 場景**無條件清** 6 個 keys，不檢查 user_id 是否變化。現況：由 `UserStateSync.fullReset()` → `clearUserScopedState()` 統一 iterate `AuthManager.USER_SCOPED_KEYS`（見 §1F-C）。

**CEO 拍板**：B2B 共用電腦切帳號的安全 > 同 user 重登保 cache（D-2026-05-01）。logout 場景必須清，避免下一個人在同瀏覽器開 F12 / 登入前讀到上一個 user 的資料。匿名 / 離線情境的 localStorage 持久化（D-2026-03-13）是另一個獨立紀律，不衝突。

#### 1F-B — Auth Failure 統一清理流程（2026-05-01 update）

> ⚠️ **下方 7 步驟表為歷史實作（superseded by D-2026-05-13 + v4.0 模組化）**。現況 `_handleAuthFailure()` 已遷 `static/js/core/auth-manager.js`（grep `_handleAuthFailure()`，:328-352），且**不再逐步展開 7 個清理動作**，而是委派給單一 `UserStateSync.fullReset({ keepInviteToken: false })`（`static/js/core/state-sync.js`）—— 該 convenience 內部一次完成 cancelPendingSave → clearUserScopedState（USER_SCOPED_KEYS + 6 個 main-UI globals）→ resetMainUI，再由 `_handleAuthFailure` 收尾 null 掉 `_accessToken` / `_user` + `updateAuthUI()` + `hideMainUI()` + `showAuthModal('login')`。下方步驟的**語義仍成立**（清理範圍一致），但已收斂為單一 `fullReset` 呼叫，不再是 7 個散落 callsite。權威現況見 `docs/specs/session-spec.md` §8.5。

> **背景**：原本 `checkAuthOnLoad` 收 `/api/auth/me` 401 後只 hide main UI / show login modal，沒清 `_user` / localStorage / 重新 render sidebar，導致 `isLoggedIn()` 仍 true → 後續 silent fallback 載入舊 user 資料 → cross-user leak。修法見 commit `138ae61` + commit `e0b5a41` + `memory/lessons-auth.md`「checkAuthOnLoad 401 path 改呼叫完整 _handleAuthFailure」「Debounce timer 跨 auth 邊界」「resetConversation 不等於 reset all user-scoped state」三段。

**通則**：任何 auth failure path（logout / token expired / refresh fail / 401）都必須走同一個 cleanup func，不要分支實作。「半 logged-out 狀態」（`_user` 還在但 token 沒了）是 cross-user leak 的隱性載體。

**~~`AuthManager._handleAuthFailure()` 完整流程~~**（⚠️ 歷史 7-step 實作，`static/news-search.js:200-241`；現為單一 `UserStateSync.fullReset()`，見上方 callout）：

| # | 步驟 | 程式碼位置 | 為什麼 |
|---|------|-----------|--------|
| 1 | **第一行**：cancel 所有 pending debounce timer（`sessionManager._cancelPendingSave()`） | `news-search.js:206-208` + `_cancelPendingSave` 定義在 `news-search.js:507-513` | 防 `setTimeout(..., 2000)` closure 抓 stale session 在 token 失效後 fire → 401 → 遞迴回到本 func → 二次 wipe sidebar。**state mutation 之前必須先 cancel**。 |
| 2 | 清 `_accessToken` + `_user` | `news-search.js:209-210` | in-memory auth state |
| 3 | 清 token / user 的 localStorage（`authUser`、`authAccessToken`） | `news-search.js:211-212` | 持久化 auth cache |
| 4 | 清 6 個 USER_SCOPED_KEYS（無條件） | `news-search.js:218-220` | 見 §1F-A |
| 5 | 清 in-memory `savedSessions` 陣列 + `renderLeftSidebarSessions()` | `news-search.js:221-228` | 否則 DOM 殘留前一 user 的 sidebar entries 直到下次 reload |
| 6 | **`_resetMainUIState()` 清 main-UI globals** | `news-search.js:235-237`，helper 定義在 `news-search.js:1937-1949` | 切帳號時主畫面殘留 user A 對話 / 結果 / report。helper 內 wrap 既有 `resetConversation()`（已清 10 個 globals 含 `conversationHistory`、`sessionHistory`、`chatHistory`、`accumulatedArticles`、`pinnedMessages`、`pinnedNewsCards`、`currentLoadedSessionId`、`currentResearchReport`、`currentConversationId`、`currentResearchQueryId`），**再補 6 個 helper 沒涵蓋的**：`_sessionDirty`、`currentArgumentGraph`、`currentChainAnalysis`、`shareContentOverride`、`currentLRSessionId`、`currentAnalyticsQueryId`。**重用 > 抽象 > 重寫** — 不重構 `resetConversation`，只 wrap。 |
| 7 | `hideMainUI()` + `showAuthModal('login')` | `news-search.js:239-240` | UI 切回登入畫面 |

> 步驟 1 的 `typeof` guard（`typeof sessionManager !== 'undefined'`）是為了 module-init order 防護 — `sessionManager` 在 `authManager` 之後宣告，若初始化期間就觸發 auth failure 不會炸。runtime invocations 一律滿足 typeof check。步驟 6 同樣有 `typeof _resetMainUIState === 'function'` guard，因 helper 定義在 class 之後。

**入口列表**：以下三條 path 都呼叫 `_handleAuthFailure()`，避免分支實作：

| 入口 | 程式碼位置 | 觸發時機 |
|------|-----------|---------|
| `AuthManager.logout()` | `news-search.js:164` | 使用者主動登出 |
| `AuthManager.refreshToken()` catch block | `news-search.js:148` | refresh token 失敗（過期 / 撤銷） |
| `checkAuthOnLoad()` 401 path | `news-search.js:926` | 頁面載入時 `/api/auth/me` 回 401（commit `138ae61` 改：原本只 hideMainUI + showAuthModal） |
| `authenticatedFetch` 內呼叫的 `refreshToken()` 失敗時 | `news-search.js:1205`（其他 callsite） | API 呼叫遇 401 又 refresh 失敗 |

**配套：`SessionManager._cancelPendingSave()`**（`news-search.js:507-513`）— 純清理 `_saveTimer` + `_savePending`，不 fire PUT。

**配套：`loadSessions` logged-in 失敗不 fallback localStorage**（`news-search.js:266-292`）— logged-in 狀態 server 失敗時回 `[]` + `console.error`，**不**回退讀 `taiwanNewsSavedSessions`（避免載入上一個 user 殘留）。匿名 / 離線狀態仍走 localStorage（D-2026-03-13 不變）。詳見 D-2026-05-01 + `memory/lessons-auth.md`「Logged-in 狀態 server 失敗 → [] + console.error」段落。

#### 1F-C — Init Sync Architecture（2026-05-13 update）

> **背景**：2026-04-29 ~ 05-08 連修 9 個 cross-user leak patch（commits `5ff8947` → `e0b5a41`），CEO 2026-05-08 拍板「每個人登入之後就要重新同步資料庫狀態，不可以個案式找清理點」。本段是 architectural refactor 取代 1F-A / 1F-B 的 case-by-case cleanup 為 single sync flow。詳見 D-2026-05-13 + `docs/in progress/plans/frontend-init-sync-refactor-plan.md` + `memory/lessons-frontend.md`「Frontend Init Sync — Architectural Refactor」段。

**核心 invariant**：`cache.user_id == JWT.user_id`。前端 user-scoped state 只允許在 7 個 sync trigger 透過 `UserStateSync` module 寫入，其他寫入點視為 bug。

**7 個 sync trigger**：

| Trigger | 偵測時機 | 行為 |
|---|---|---|
| **A. Login / Onboarding** | `login()` 收 200 + 新 JWT、`completeOnboarding()` 成功跳 `/` | fullReset → `fetchInit()` → `applyInit()` |
| **B. User identity change** | `checkAuthOnLoad()` 收 `/api/auth/me` 200 且 `data.user.id !== cached.id` | 同 A |
| **C. Logout** | `logout()` / admin force logout | fullReset → show login modal（不 fetch） |
| **D. 401 / refresh fail** | `authenticatedFetch` 收 401 且 refresh 失敗 | 同 C。**特例（AbortError UX 契約，2026-07-10 commit `6c60faf7`）**：若 401-refresh-retry 窗內 caller 主動 `AbortController.abort()`（快速連搜取消前一請求），`fetch` 拋 `AbortError` —— 這是「非錯誤的正常取消」，`authenticatedFetch` catch 內以 `e.name === 'AbortError'` re-throw（`auth-manager.js:303-305`）交回 caller 既有 AbortError 分支，**絕不觸發 `_handleAuthFailure`**（否則誤彈 login modal + reset state）。已驗所有帶 `signal` 的 caller（search / chat / deep-research）都有靜默 return 分支；不帶 signal 的 caller 走不到此路徑 |
| **E. Session click** | sidebar / popup / folder detail click | `GET /api/sessions/{id}` 拉完整內容並 hydrate，不從 cache 讀 |
| **F. Page reload / tab visible** | `DOMContentLoaded` checkAuthOnLoad、`document.visibilitychange === 'visible'` | mismatch → 走 A；match → soft refresh |
| **G. SSE envelope** | `handleStreamingRequest` / `handlePostStreamingRequest` 每個 onmessage | envelope `data.user_id` ≠ `authManager._user.id` → abort stream + trigger F |

**`UserStateSync` module 三函式**（`static/js/core/state-sync.js`，v4.0 Commit 11 搬入；Path B 期間仍由 news-search.js classic-script IIFE 透過 `window.UserStateSync` 供給，見 `auth-manager.js` header D-3 註）：
- `clearUserScopedState()` — 統一清光 A/B/C/D 範圍的 user-scoped state（device-scoped UI prefs 不動）
- `fetchInit()` — 呼叫 `GET /api/user/init` composite endpoint
- `applyInit()` — hydrate in-memory caches + render UI（含組織空間 shared_sessions，Phase 4b.5 Fix 2 補強）
- convenience：`fullReset()`（= clearUserScopedState + resetMainUI）、`runInitSync()`（= fullReset + fetchInit + applyInit，含 in-flight de-dupe）

**`assertUserIdentity(cached, fresh)` helper**（`static/js/core/state-sync.js`）— mismatch 拋 `UserStateSyncError`，caller 必須 `try/catch` 後 trigger A 整套 reset。

**Backend composite endpoint `GET /api/user/init`**（`code/python/webserver/routes/user_init.py`）— 一次 round-trip 回 `{ user, org, role, sessions, shared_sessions, preferences }`，避免 5 個獨立呼叫。

**Backend variant：onboarding 完成 auto-issue cookie**（commit `2ee5508`）— `register_user()` / `activate_user()` 成功後 backend 直接 `Set-Cookie: refresh_token`，前端跳 `/` 後 `checkAuthOnLoad()` 走 cookie 拿到新 JWT → `assertUserIdentity` 偵測到 MISMATCH（cached=admin / fresh=新 user）→ trigger A 整套 reset。

**SSE envelope `user_id` stamping**（Phase 4b.5 Fix 1，commit `c413465`）— backend 每個 SSE emitter path 顯式 stamp `user_id` 欄位，**不靠單一 hook 攔截**（codebase 有 ad-hoc emitter path）。Helper `_stamp_user_id_on_envelope(envelope, user_id)` 在 `code/python/core/utils/message_senders.py` + `code/python/core/state.py` 顯式呼叫。

**取代 / 保留**：
- **取代**（已從 codebase 移除）：1F-B 的 `_resetMainUIState()` helper、`_clearUserScopedStorageIfUserChanged()` + `login()` callsite、`loadSavedSession()` metadata-only branch
- **保留**（defense-in-depth）：`_sessionDirty` dirty gate、server-side `_sanitize_session_history`、`list_sessions` ORDER BY `updated_at DESC`、Shared session `_isShared` 早退

**驗收**：9/9 E2E scenario PASS（含 incognito vs 殘留瀏覽器、跨 user 快速切換、onboarding 完成跳搜尋、F5 reload、SSE mismatch abort）。Sweep audit 0 bug。Zoe + CEO review PASS（2026-05-13）。

---

## Part 2: Session Management

### 2A — Schema

`search_sessions` 表（見 1A 的完整 table list）。Junction tables 取代原設計的 UUID[]。

### 2B-2C — Session API + Service

**API**: `webserver/routes/sessions.py` — 15 endpoints (CRUD + migrate + feedback + export + sharing)
**Service**: `core/session_service.py` — JSONB append pattern, 200KB size 監控

### 2D — 前端遷移

SessionManager class 取代 localStorage。首次登入觸發 `POST /api/sessions/migrate`。

### 2E — 組織隔離

| 模組 | 狀態 | 說明 |
|------|------|------|
| JWT org_id 注入 | Done | middleware 層 |
| ~~user_qdrant_provider.py~~ → user_postgres_provider.py | Done | 已遷移至 PG（2026-03-27），org_id filter 支援 |
| user_data_manager.py | Done | create/list/delete 全部支援 org_id |
| query_logger.py | Done | queries schema + log_query_start 已加 org_id |

---

## Part 3: Security Hardening

### 3A — Rate Limiting

**檔案**: `webserver/middleware/rate_limit.py`

| Endpoint | 目前實際值 | 說明 |
|----------|-----------|------|
| `/api/auth/register` | 5/hr | ✅ 已調緊（原 50/hr） |
| `/api/auth/forgot-password` | 3/hr | ✅ 已調緊（原 50/hr） |
| `/api/auth/login` | 10/min | ✅ 已調緊（原 60/min） |

Rate limit 已於 2026-03-05 infra adaptation 時調整至 production 值。

**IP 取得**（2026-03-27 修正）：`webserver/middleware/ip_utils.py` 集中管理 trusted-proxy 驗證。只有來自 loopback/Docker 網路的 request 才信任 `X-Forwarded-For`，否則用 `request.remote`。`rate_limit.py` 和 `auth.py` 共用 `get_client_ip()`。

### 3B — Audit Logs

**已完成**: `core/audit_service.py` + `webserver/routes/audit.py`
- Alembic migration `a3f8c2e51d07`
- fire-and-forget (`asyncio.create_task`)

### 3C — CORS

**已完成**: `webserver/middleware/cors.py`
- `ALLOWED_ORIGINS` env var
- Dev mode 允許 localhost
- 已修復 wildcard + credentials bug

---

## Part 4: Data Migration

**檔案**: `scripts/migrate_to_b2b.py` (idempotent)

原設計遷移 Qdrant conversations + user_data + analytics。Infra migration 後 Qdrant 移除（🪦 2026-06-22 徹底廢除），Qdrant conversation 遷移部分已不適用；腳本仍存在（idempotent）。

---

## Part 5+: Research Collaboration

| 功能 | 狀態 |
|------|------|
| Session Export (JSON/CSV) | 已在 session_service.py |
| RIS export + citation | TODO |
| Session Sharing (visibility) | Done |
| 組織管理 UI + 邀請流程 | Done |

---

## Infra Adaptation

> Login system 實作於 infra migration 前。以下列出需要適配的項目。

### 高衝突：Qdrant 移除（🪦 已全數收帳，2026-07-10 核驗）

Infra migration 將 Qdrant 替換為 PostgreSQL pgvector（Qdrant 已於 2026-06-22 徹底廢除）。以下 login 修改作廢後的重寫**均已完成**：

| 檔案 | Login 做了什麼 | 收帳結果 |
|------|---------------|-------------|
| ~~`storage_providers/qdrant_storage.py`~~ | ~~加 user_id filter~~ → 檔案已刪 | `user_postgres_provider.py` 已實作 user_id filter（mandatory，`user_id = %s` 子句）|
| ~~`retrieval_providers/user_qdrant_provider.py`~~ | ~~加 org_id filter~~ → 已完成（2026-03-27） | `user_postgres_provider.py` 已支援 org_id filter |

### 中衝突：DB 統一

| 項目 | Login 假設 | 新 Infra 實際 | 適配 | 狀態 |
|------|-----------|--------------|------|------|
| DB 連線 | `ANALYTICS_DATABASE_URL` (Neon) | `DATABASE_URL` (自建 PostgreSQL) | env var 改為 `DATABASE_URL`，保留 fallback | ✅ Done |
| Connection pool | 每次 query 新連線 | `psycopg_pool.AsyncConnectionPool` | auth_db.py 改用 pool (min=1, max=5) | ✅ Done |
| Schema 管理 | Alembic (auth + session + audit) | init.sql (articles + chunks) | 新增 Alembic migration `b5e9d3f71a42` 統一管理 | ✅ Done |
| Table 共存 | 12 張 auth/session 表 | articles + chunks 表 | 確認無 naming conflict | ✅ OK |

### 低衝突：部署環境

| 項目 | Login 假設 | 新 Infra | 適配 |
|------|-----------|---------|------|
| SSL | Render 自帶 HTTPS | Hetzner VPS | 自建 Let's Encrypt |
| Cookie Secure | `Secure=request.secure` | 需確保 HTTPS | 部署時處理 |
| CORS origin | Render domain | Hetzner domain | 改 `ALLOWED_ORIGINS` |
| BASE_URL | Render URL | 新 domain | 改 env var |
| Middleware | aiohttp middleware | 不變 | 直接合併 |
| 前端 | aiohttp static files | 不變 | 直接合併 |

---

## Part 6: Bootstrap Token Onboarding

> 完成於 2026-03-17。B2B 用戶不自助註冊，由 admin 透過 bootstrap token 引導。

### 設計決策

B2B 模型下，不開放任何人自行註冊。Admin 事先產生一次性 bootstrap token，發給目標用戶，用戶在 `/setup?token=xxx` 頁面完成首次設定帳號。

### Table Schema（2026-07-11 改記 code 現況）

實際 schema（`auth/auth_db.py:599-608` SQLite / `:800-809` PostgreSQL；PG 已由 alembic `1015e1c40f88_phase_b_align_vps_schema.py` 收編）：

```sql
CREATE TABLE bootstrap_tokens (
    id             TEXT PRIMARY KEY,        -- UUID
    token          TEXT UNIQUE NOT NULL,    -- secrets.token_urlsafe(32)
    org_name_hint  TEXT DEFAULT '',         -- 預填 /setup 表單用，非正式 org 關聯
    created_at     REAL NOT NULL,           -- PG: DOUBLE PRECISION（epoch 秒）
    expires_at     REAL NOT NULL,           -- PG: DOUBLE PRECISION
    used_at        REAL,                    -- NULL = unused
    used_by_email  TEXT                     -- 使用者 email（非 user_id）
);
```

> **原規劃設計（未實作）**：spec 最初設計含 `org_id NOT NULL`、`org_name NOT NULL`、`created_by`（admin user_id）、`used_by`（user_id）。實作為簡化版 — org 在 token 建立時尚不存在（由 /setup 流程建立），故只留 `org_name_hint`；追溯用 `used_by_email`。一次性 onboarding 用途下功能完整可用。**是否補做原規劃欄位（org_id/created_by/used_by user_id）待 CEO 確認**。

### Setup 頁面

- **路由**: `GET /setup?token=xxx`（已加入 PUBLIC_ENDPOINTS）
- **UI**: 獨立品牌化頁面（讀豹 logo + 深藍金色風格），與 login modal 分離
- **流程**: token 驗證 → 填寫 email/password/name → 帳號建立 → 成功訊息

### CLI 工具（2026-07-11 改記 code 現況）

實際 CLI（`auth/bootstrap_cli.py`）只支援產生 token：

```bash
# 產生 bootstrap token（輸出 token + /setup URL）
cd code/python
python -m auth.bootstrap_cli --org "Company Name" --expires 72
```

- `--org`：org name hint（預填 /setup 表單，可留空）
- `--expires`：有效小時數，預設 72

> **原規劃的 `--list` / `--revoke` 未實作**（token 管理目前直接查/改 DB）。是否補做待 CEO 確認。

### register_user 修改

`auth_service.py` 的 `register_user()` 新增必填參數 `bootstrap_token: str`。收到請求時：
1. 驗證 token 存在且未使用、未過期
2. 建立 user + org_membership（admin 角色，auto-verified，不寄 verification email）
3. 標記 token `used_at = now(), used_by_email = email`（一次性；`auth_service.py:124-125`）

### 測試覆蓋

117/117 tests pass（含 bootstrap token 流程的 test cases）。

---

## Known Gaps

> 程式碼審計發現的問題。合併前需逐一處理。

### Must Fix

| # | 問題 | 嚴重度 | 說明 |
|---|------|--------|------|
| ~~1~~ | ~~**Tests 不存在**~~ | ~~High~~ | ✅ 已完成（2026-03-16）：113/113 tests pass，適配 B2B bootstrap model |
| 2 | ~~baseHandler.py 未改~~ | ~~High~~ | auth middleware soft-auth + api.py 注入 user_id/org_id（2026-03-05） |
| 3 | ~~Rate limit 過寬~~ | ~~Medium~~ | ✅ 已調緊至 production 值（2026-03-05） |
| 4 | ~~org_id 查詢 filter 缺失~~ | ~~Medium~~ | list/delete 已加 org_id filter（2026-03-05） |
| 5 | ~~query_logger org_id~~ | ~~Medium~~ | queries schema + log_query_start 已加 org_id（2026-03-05） |

### Completed (Code Review 2026-03-05)

| # | 修復項目 | 類型 |
|---|----------|------|
| M1 | rate_limit_middleware 未註冊 → 加入 middleware __init__ | MUST FIX |
| M2 | /api/org/accept-invite 不應為 public endpoint → 移除 | MUST FIX |
| M3 | email HTML template injection → html.escape | MUST FIX |
| M4 | _pg_execute autocommit=True 破壞 transaction → 改 conn.commit() | MUST FIX |
| S5 | Boolean `= 1` 在 PG 不相容 → 全面參數化 `= ?` + True/False | SHOULD FIX |
| S6 | JWT_SECRET 長度檢查 → startup warning if < 32 chars | SHOULD FIX |
| S7 | _adapt_query_pg JSONB `?` 衝突 → 加 TODO 註解 | SHOULD FIX |
| S8 | CSV formula injection → _csv_safe() sanitizer | SHOULD FIX |
| S9 | parseInt 無 try/except → sessions.py + audit.py 加 400 回傳 | SHOULD FIX |
| S10 | PG JSONB append 無 size check → append_message/articles 加檢查 | SHOULD FIX |

### Deferred (Code Review 2026-03-05)

> 以下為 code review 發現但目前不急、或需要較大重構才能解決的項目。

| # | 問題 | 理由 | 優先度 |
|---|------|------|--------|
| D1 | 雙 DB pool（auth_db + analytics_db）連線浪費 | 需統一 DB layer 重構，Infra Migration 時一起處理 | Low |
| ~~D2~~ | ~~雙 schema 管理（Alembic + initialize() 手動 DDL）~~ → **升級**（2026-05-07）：實際是 alembic 在 VPS 從沒跑過（deploy.yml 沒 alembic step + alembic_version 表不存在）。CEO 拍板方案 B：alembic 接成 schema source of truth。**Resolved（2026-05-13）— 方案 B 完成**：alembic 變唯一 schema source of truth，`auth_db.initialize()` 改 sanity check（無 DDL），deploy.yml 加 `alembic upgrade head` step，所有 migrations idempotent。詳見 `docs/specs/database-spec.md` + `docs/decisions.md`「Alembic 接成 schema source of truth（方案 B）」。 | ~~Approved（時程待定）~~ → **Resolved** |
| ~~D3~~ | ~~localStorage 存 JWT token（XSS 風險）~~ | ✅ 已完成（2026-03-11 BP-1）：後端 `set_cookie(httponly=True)`，前端 `authenticatedFetch()` 用 `credentials: 'same-origin'` | ~~Medium~~ |
| D4 | org_id 寫入 JWT，revoke 有延遲 | JWT 天生限制，需 token blacklist 機制（PostgreSQL table），複雜度高 | Low |
| D5 | login_attempts 表無 cleanup 機制 | 資料增長慢，可加 scheduled SQL DELETE | Low |
| ~~D6~~ | ~~_windows dict 記憶體洩漏（rate_limit）~~ | ✅ 非問題：已有 sliding window eviction，key 數量有限（3 條規則 × IP），restart 清空。Single-instance 部署無需 Redis | ~~Low~~ |
| ~~D7~~ | ~~email_service 每次 import time 讀 env var~~ | ✅ 已確認非問題 | ~~Very Low~~ |

### E2E 第一輪發現（2026-03-17）

> 8 個問題，來自 E2E 第一輪測試。**全部修復 + 第二輪驗證通過（2026-03-17）。**

| # | 問題 | 嚴重度 | 狀態 |
|---|------|--------|:----:|
| ~~E1~~ | Setup 成功後 auto redirect | Medium | ✅ |
| ~~E2~~ | Bootstrap 不寄 verification email | High | ✅ |
| ~~E3~~ | 未登入 login modal X 按鈕隱藏 | Low | ✅ |
| ~~E4~~ | 停用反饋 + 啟用按鈕 + 已停用 badge | Medium | ✅ |
| ~~E5~~ | 被停用帳號登入顯示「帳號已被停用」 | High | ✅ |
| ~~E6~~ | 刪除帳號 hard delete + 清理關聯資料 | High | ✅ |
| ~~E7~~ | Login modal 密碼欄位清空 | Medium | ✅ |
| ~~E8~~ | 忘記密碼 reset password 品牌化頁面 | High | ✅ |

### Admin Resend Activation（2026-05-07）

> 完成 admin 重寄啟用信機制 + activate page 友善 UI（commits `269aa7a`→`eb7661a`，8 個 commits，Plan: `docs/in progress/plans/admin-resend-activation-plan.md`）。

**新增 endpoint**：`POST /api/admin/resend-activation`（rate limit 5/hr per-IP）— admin 可對未啟用 member 重新產生 activation token + 寄信。舊 token 自動 invalidate（覆蓋同欄位）。拒絕條件：非同 org admin → 403、不存在 → 404、已啟用 / 已停用 → 400。

**`list_org_members` SQL 加 `is_activated` 欄位**（`(u.password_hash IS NOT NULL) as is_activated` + Python `bool()` 正規化）— 防 2026-03-17「SELECT 漏欄位 → 前端功能壞掉但 unit test 全過」重蹈。

**前端 admin org modal**：未啟用成員顯示「重寄啟用信」按鈕（`is_activated === false` 條件渲染），既有 `btn-force-logout` 同 pattern（`querySelectorAll().forEach()` 而非 event delegation，與既有風格一致）。建立成員後 `await reloadOrgMembers()` 自動刷新（修 pre-existing bug：原本要關閉重開 modal）。

**Activate page GET 三情境友善 server-side render**（`activate_page_handler`）：
- **正常**：token 存在 + `password_hash IS NULL` + 未過期 → 既有密碼設定表單
- **過期**：`expires < now` → 「啟用連結已過期，請聯絡管理員」
- **token 不存在 / 已啟用**：合併文案「此啟用連結已失效。如果您之前已設定過密碼，請從首頁登入；若忘記密碼，請聯絡管理員。」+ 前往登入按鈕（CEO 拍板 2026-05-07，避免 user enumeration）

**Schema migration**：`e39a746fb916_align_users_schema_with_initialize`（補 `email_verification_expires DOUBLE PRECISION` + `password_hash` 改 nullable，PG 用 inspector + IF NOT EXISTS guard，VPS 跑 = no-op）— **但目前 VPS 不會執行此 migration**（D2 升級議題）。詳見 `alembic-architecture-fix-plan.md`。

### Cross-User 隔離 / Logout 紀律（2026-05-01）

> 2 個問題，來自 B2B 共用電腦切帳號的 cross-user leak 調查。**全部修復**。詳細實作見 §1F-A、§1F-B + D-2026-05-01 + `memory/lessons-auth.md`「Cross-User 隔離 / Logout 紀律（2026-05-01）」段落。

| # | 問題 | 嚴重度 | 狀態 | 說明 |
|---|------|--------|:----:|------|
| ~~E9~~ | `logout()` 沒清 user-scoped localStorage（6 個 keys） | High | ✅ | commit `24d39f4`：`_handleAuthFailure` 清 `USER_SCOPED_KEYS` 6 個 keys（`taiwanNewsSavedSessions`、`taiwanNewsFolders`、`taiwanNewsSessionsMigrated`、`nlweb_source_folders`、`nlweb_file_folders`、`nlweb_selected_files`）。CEO 拍板：B2B 共用電腦安全 > 同 user 重登保 cache。詳見 §1F-A。 |
| ~~E10~~ | `checkAuthOnLoad` 401 path 沒清 `_user` / 沒 render → silent fallback 載入舊 user 資料 | High | ✅ | commit `138ae61` + commit `e0b5a41`：401 path 改呼叫完整 `_handleAuthFailure()`（cancel pending timer + 清 _user + 清 token + 清 user-scoped localStorage + `_resetMainUIState` 清 main-UI globals + render 空 sidebar + show login modal）。任何 auth failure path 都走同一個 cleanup func。詳見 §1F-B。 |

### ~~待調查 (2026-03-18)~~ → 已收斂（2026-07-11）

| # | 問題 | 狀態 |
|---|------|------|
| I1 | `bootstrap_tokens` schema spec vs code 落差 — spec 原記載的 `org_id`、`created_by`、`used_by`（user_id）、CLI `--list/--revoke` 是**原規劃設計**，實作為簡化版（`org_name_hint` / `used_by_email` / CLI 只有 `--org/--expires`）。**已裁決（2026-07-11）**：以 code 現況為準改寫 Part 6 的 Table Schema 與 CLI 段（含實際 DDL 出處 `auth_db.py:599-608`/`:800-809` 與 alembic 收編紀錄）；簡化版在一次性 onboarding 用途下功能完整（org 於 token 建立時尚不存在，`org_id`/`created_by` 無從填起是結構性原因，非漏做）。原規劃增強（正式 org/user 關聯欄位 + CLI 管理指令）**是否補做待 CEO** | 已收斂（增強項待 CEO） |

### Will Be Invalidated by Infra Migration（🪦 已發生 — Qdrant 於 2026-06-22 徹底廢除）

| # | 問題 | 說明 |
|---|------|------|
| 6 | qdrant_storage.py 修改 | 已作廢，檔案已刪（職責由 user_postgres_provider 承接） |
| 7 | user_qdrant_provider.py 修改 | 已作廢，檔案已刪（2026-03-27 遷移至 user_postgres_provider） |
| 8 | migrate_to_b2b.py 範圍 | Qdrant conversation 遷移不再適用（腳本仍在） |

---

## File Inventory

### 新增的檔案（從 RG repo 合併）

| 檔案 | 已驗證 |
|------|--------|
| `auth/__init__.py` | Yes |
| `auth/auth_db.py` | Yes |
| `auth/auth_service.py` | Yes |
| `auth/email_service.py` | Yes |
| `webserver/routes/auth.py` | Yes |
| `webserver/routes/sessions.py` | Yes |
| `webserver/routes/audit.py` | Yes |
| `webserver/middleware/rate_limit.py` | Yes (已調緊) |
| `webserver/middleware/cors.py` | Yes |
| `core/session_service.py` | Yes |
| `core/audit_service.py` | Yes |
| `alembic/` + `alembic.ini` | Yes |
| `scripts/migrate_to_b2b.py` | Yes |

### 已修改的檔案（需 merge 進主 repo）

| 檔案 | 已驗證 | 注意事項 |
|------|--------|---------|
| `webserver/routes/__init__.py` | Yes | 加入 auth/sessions/audit routes |
| `webserver/middleware/auth.py` | Yes | 完整重寫 |
| `webserver/aiohttp_server.py` | Yes | 移除 chat 初始化 |
| `core/config.py` | Yes | 移除 OAuth config |
| `webserver/routes/user_data.py` | Yes | user_id 從 request['user'] |
| `static/news-search-prototype.html` | Yes | Login modal + org modal |
| `static/news-search.js` | Yes | AuthManager + SessionManager |
| `static/news-search.css` | Yes | Modal styles |

### 測試檔案（2026-03-16 新增）

| 檔案 | 說明 |
|------|------|
| `tests/test_auth_service.py` | 已建立，含 B2B bootstrap model |
| `tests/test_auth_middleware.py` | 已建立 |
| `tests/test_session_service.py` | 已建立 |

### 刪除的檔案（已確認）

| 檔案 |
|------|
| `chat/` (9 files) |
| `webserver/routes/chat.py` |
| `webserver/routes/oauth.py` |
| `config/config_oauth.yaml` |

---

## Environment Variables

| 變數 | 說明 | 狀態 | Infra 適配 |
|------|------|------|-----------|
| `DATABASE_URL` | 統一 DB URL | 新增 | 取代 ANALYTICS_DATABASE_URL |
| `JWT_SECRET` | JWT 簽名密鑰 | 已使用 | 不變 |
| `RESEND_API_KEY` | Resend API key | 已使用（可選） | 不變 |
| `RESEND_FROM_EMAIL` | 發信地址 | 已使用 | 不變 |
| `BASE_URL` | 系統 base URL | 已使用 | 改為新 domain |
| ~~`NLWEB_DEV_AUTH_BYPASS`~~ | ~~開發模式跳過認證~~ | **DELETED 2026-05-19**（commit `c20f545`） | 不再使用（E2E 強制真實 login） |
| `ALLOWED_ORIGINS` | CORS 允許 origin | 已使用 | 改為新 domain |

---

## Dependencies

| 套件 | 用途 | 狀態 |
|------|------|------|
| `bcrypt` | 密碼 hash | 已使用 |
| `PyJWT` | JWT token | 已使用 |
| `resend` | Email 發送 | 已使用（可選） |
| `alembic` | DB migration | 已使用 |
| `psycopg` | Async PostgreSQL | 已使用 |

---

## Cost Analysis

| 階段 | 月費 | 說明 |
|------|------|------|
| 開發期 | $0 | Free Tier + dev console email |
| Early B2B (<50 users) | ~$1/月 | 只有 domain 成本（DB 已含在 Hetzner VPS） |
| Growth (50-500 users) | ~$20/月 | Resend Pro $20（DB 已含在 VPS） |
| Scale (500+ users) | ~$100+/月 | Resend Business + 可能需升級 VPS |

注：原 spec 包含 Neon PostgreSQL 費用。Infra migration 後 DB 已含在 Hetzner VPS 月費中，不另計。
