# Login System Specification

> **Owner**: NLWeb Team (?交??芸???dev)
> **Last Updated**: 2026-03-27
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

蝟餌絞頧???B2B 蝺???嚗?閬?

1. 蝯???Email/Password ?餃嚗?隞?歇?芷??OAuth嚗?
2. Server-side 撠店/?末蝞∠?嚗?隞?localStorage嚗?
3. Email ??嚗esend嚗?湧?霅?隢?蝣潮?閮?
4. WebSocket chat 撌脩宏?歹?B2B 銝?閬?????SSE ??銝脫?嚗?

### ?蔭皜?嚗歇摰?嚗?

- OAuth 蝟餌絞嚗oauth.py`?config_oauth.yaml`嚗歇?芷
- WebSocket chat嚗chat/` ?桅? 9 瑼routes/chat.py`嚗歇?芷
- 靽? `routes/conversation.py`嚗SE 撠店甇瑕 API嚗?

---

## Architecture Decisions

### DB: PostgreSQL嚗絞銝嚗?

??spec ?貊 Neon PostgreSQL嚗撅?analytics DB嚗nfra migration 敺?箄撱?PostgreSQL on Hetzner VPS?uth tables ??articles/chunks tables ?勗??澆?銝 DB??

### Token Strategy

| 憿? | 蝑 | ?? |
|------|------|------|
| Access Token | JWT (HS256), payload: `{user_id, email, name, org_id, role}` | 15 ?? |
| Refresh Token | `secrets.token_urlsafe(64)`, DB 摮?SHA256 hash | 7 憭?|
| Password | bcrypt hash | - |
| Brute Force | ?? email 15 ???批仃??5 甈⊿?摰?| 15 ?? |

### Multi-tenancy

蝯??塚?瘥?user 撅祆 1+ ??organization嚗WT ??`org_id` + `role`?????ａ? `WHERE org_id = $n`??

---

## Implementation Status

> 隞乩?????2026-03-05 撠?RG repo 蝔?蝣潛?撖抵?蝯???
> 璅?閬?嚗歇撽? = 蝔?蝣澆??其??摩甇?Ⅱ嚗撽? = spec 摰?迂摰?雿?撘Ⅳ銝泵??摮??

| Phase | ?批捆 | ???| ?酉 |
|-------|------|------|------|
| 0A | 蝘駁 OAuth | **撌脤?霅?* | 瑼?蝣箄??芷 |
| 0B | 蝘駁 WebSocket Chat | **撌脤?霅?* | 瑼?蝣箄??芷 |
| 1A | DB Schema | **撌脤?霅?* | auth_db.py auto-create, 12 tables |
| 1B | Auth Service | **撌脤?霅?* | 14 public methods, bcrypt+JWT |
| 1B | Email Service | **撌脤?霅?* | 4 send methods (??lockout) |
| 1C | Auth API Routes | **撌脤?霅?* | 8 auth + 6 org endpoints |
| 1D | Auth Middleware | **撌脤?霅?* | JWT 撽?嚗ev bypass 撌?DELETED 2026-05-19 commit `c20f545`嚗?|
| 1E | user_id 璅∪?靽桀儔 | **?典?** | user_data.py OK; **baseHandler.py ?芣**嚗???query param嚗?|
| 1F | ?垢 Login UI | **撌脤?霅?* | AuthManager + modal + TEMP_USER_ID 蝘駁 |
| 2A | Session Schema | **撌脤?霅?* | Alembic migration 摮 |
| 2B | Session API | **撌脤?霅?* | routes/sessions.py, 15 endpoints |
| 2C | Session Service | **撌脤?霅?* | JSONB append + 200KB ?? |
| 2D | ?垢?瑞宏 | **撌脤?霅?* | SessionManager, localStorage 蝘駁 |
| 2E | 蝯?? | **?典?** | ~~user_qdrant_provider~~ ??user_postgres_provider OK嚗?026-03-27 ?瑞宏嚗? **user_data_manager org_id filter ?芣?撖怠瘝??亥岷** |
| 3A | Rate Limiting | **撌脤?霅??潔???** | 撖阡??潭? spec 撖祇? 5-17 ??閬??對? |
| 3B | Audit Logs | **撌脤?霅?* | audit_service.py + routes/audit.py |
| 3C | CORS | **撌脤?霅?* | cors.py, ALLOWED_ORIGINS |
| 4A | Data Migration | **撌脤?霅?* | migrate_to_b2b.py 摮 |
| 5B | Session Sharing | **撌脤?霅?* | visibility + shared_with |
| Tests | 69 tests (31+14+24) | **銝???* | 3 ??test 瑼??券 404 |

---

## Part 1: Auth System

### 1A ??DB Schema

**瑼?**: `auth/auth_db.py` ??AuthDB singleton, SQLite/PostgreSQL ??? ????auto-create??

**Tables嚗?2 撘蛛?頝冽???Sprint嚗?*:

| Table | 隤芣? | Sprint |
|-------|------|--------|
| `organizations` | 蝯? (id, name, slug, plan, max_members, settings) | 1 |
| `users` | 雿輻??(id, email, password_hash, name, email_verified, tokens) | 1 |
| `org_memberships` | 蝯?? (user_id, org_id, role, status) | 1 |
| `invitations` | ?隢?(org_id, email, token, expires_at) | 1 |
| `refresh_tokens` | Refresh Token (token_hash, expires_at, revoked_at) | 1 |
| `login_attempts` | ?餃?岫 (email, ip_address, success, attempted_at) | 1 |
| `search_sessions` | ?? Session (user_id, org_id, history, articles) | 3 |
| `org_folders` | 蝯?鞈?憭?| 3 |
| `org_folder_sessions` | Junction: folder-session | 3 |
| `session_shares` | Junction: session-share | 3 |
| `user_preferences` | 雿輻??憟?(key-value JSONB) | 3 |
| `audit_logs` | 蝔賣?亥? | 5 |

**Alembic Migrations**:
- `9df501ad9a13` ??baseline: 6 auth tables
- `c1c6deac2013` ??session tables (5 tables)
- `a3f8c2e51d07` ??audit_logs
- `b5e9d3f71a42` ??infra tables (articles + chunks, ?拚???infra)

### 1B ??Auth Service

**瑼?**: `auth/auth_service.py` ??14 public methods

| ?寞? | 撌脤?霅?| 隤芣? |
|------|--------|------|
| `register_user(email, password, name)` | Yes | bcrypt hash + 撽? email |
| `verify_email(token)` | Yes | email_verified = true |
| `login(email, password, ip)` | Yes | brute force check + JWT + refresh |
| `refresh_token(token)` | Yes | SHA256 瘥? + ??access token |
| `logout(refresh_token)` | Yes | ?日 refresh token |
| `forgot_password(email)` | Yes | 銝援瞍?email ?臬摮 |
| `reset_password(token, new_pw)` | Yes | ?湔撖Ⅳ + ?日???refresh |
| `create_organization(name, admin_user_id)` | Yes | 撱?org + admin membership |
| `invite_member(org_id, email, role, invited_by)` | Yes | 撽? admin + 鈭箸銝? |
| `accept_invitation(token, user_id)` | Yes | token + email match |
| `list_user_orgs(user_id)` | Yes | |
| `list_org_members(org_id, requester)` | Yes | 撽??舀???|
| `remove_member(org_id, target, requester)` | Yes | admin only, 銝蝘駁?芸楛 |
| `get_user_by_id(user_id)` | Yes | 銝 password_hash |

### 1B ??Email Service

**瑼?**: `auth/email_service.py` ??Resend (production) / console log (dev)

| ?寞? | 隤芣? |
|------|------|
| `send_verification_email(email, token, name)` | 閮餃?撽? |
| `send_password_reset_email(email, token, name)` | 撖Ⅳ?身嚗? 撠?嚗?|
| `send_invitation_email(email, org_name, inviter_name, token)` | 蝯??隢?7 憭抬? |
| `send_lockout_notification(email, name)` | 撣唾???? |

### 1C ??Auth API Routes

**瑼?**: `webserver/routes/auth.py`

**Auth**:

| Method | Endpoint | 隤芣? |
|--------|----------|------|
| POST | `/api/auth/register` | 閮餃? |
| GET | `/api/auth/verify-email?token=xxx` | 撽? email |
| POST | `/api/auth/login` | ?餃 (access_token + HttpOnly refresh cookie) |
| POST | `/api/auth/refresh` | ?瑟 (cookie or body) |
| POST | `/api/auth/logout` | ?餃 |
| GET | `/api/auth/me` | ?桀?雿輻??|
| POST | `/api/auth/forgot-password` | 敹?撖Ⅳ |
| POST | `/api/auth/reset-password` | ?身撖Ⅳ |

**Organization**:

| Method | Endpoint | 隤芣? |
|--------|----------|------|
| POST | `/api/org` | 撱箇?蝯? |
| GET | `/api/org` | ?蝯? |
| POST | `/api/org/{id}/invite` | ?隢?|
| GET | `/api/org/{id}/members` | ?? |
| DELETE | `/api/org/{id}/members/{user_id}` | 蝘駁? |
| POST | `/api/org/accept-invite` | ?亙??隢?|

**?啣? Auth嚗?026-03-16嚗?*:

| Method | Endpoint | 隤芣? |
|--------|----------|------|
| POST | `/api/auth/change-password` | 撌脩?交撖Ⅳ |
| POST | `/api/auth/logout-all` | ?餃?券鋆蔭嚗?瑟???refresh token嚗?|

**?啣? Admin嚗?026-03-16嚗?*:

| Method | Endpoint | 隤芣? |
|--------|----------|------|
| POST | `/api/admin/logout-user/{user_id}` | Admin 撘瑕?餃???冽 |
| PATCH | `/api/admin/user/{user_id}/active` | ?/?撣唾? |
| DELETE | `/api/admin/user/{user_id}` | ?芷撣唾? |
| PATCH | `/api/admin/user/{user_id}/role` | 靽格閫 |

**Cookie 閮剖?**: `Set-Cookie: refresh_token` (HttpOnly, Secure=request.secure, SameSite=Lax, path=/api/auth)

### 1D ??Auth Middleware

**瑼?**: `webserver/middleware/auth.py` ??摰?神

- Token 靘??芸???: Bearer header > cookie > query param (GET only)
- JWT 閫?Ⅳ憭望? -> 401嚗????曇?嚗?
- JWT_SECRET ?芾身摰?-> 500
- `request['user']` ??user_id, org_id, role, authenticated
- **Dev bypass: 撌?DELETED 2026-05-19嚗ommit `c20f545`嚗?*?2E 撘瑕?祕 `admin@example.com / YOUR_ADMIN_PASSWORD`嚗?撖?t嚗?乓uth bypass ??v9-v15 LR E2E ???拇活 P0嚗? dev/prod auth path 銝?蝔晞andoff 撖Ⅳ憭批?撖恍隤斤???root cause?底閬?`memory/lessons-auth.md` 2026-05-19 畾萸?
- `/ask` 撌脣? PUBLIC_ENDPOINTS 蝘駁嚗? auth嚗?
- `/sites_config` ??PUBLIC_ENDPOINTS嚗霈蝡?蔭嚗??鞈?嚗?

### 1E ??user_id 靽桀儔

| 瑼? | ???| 隤芣? |
|------|------|------|
| `webserver/routes/user_data.py` | Done | 敺?`request['user']['id']` ??|
| `core/baseHandler.py` | Done | auth middleware soft-auth + api.py 瘜典??query_params |
| `storage_providers/qdrant_storage.py` | Done (撠?撱? | user_id filter ??Qdrant ??infra migration 敺宏??|

### 1F ???垢 Login UI

**撌脣???*:
- AuthManager class (login, register, refreshToken, logout, authenticatedFetch)
- Request Queue 璈 (憭?401 ??閫貊? refresh 銝甈?
- Login/Register modal
- TEMP_USER_ID ?券蝘駁
- SSE ?瑞???+ token refresh

#### 1F-A ??Cross-User Storage Isolation嚗?026-05-01 update嚗?

> **?**嚗2B ?梁?餉?董??嚗localStorage` ??origin-scoped 銝 user-scoped ??admin ?餃敺??汗??member ?餃隞???admin sessions?耨瘜? commit `24d39f4` + D-2026-05-01 + `memory/lessons-auth.md`?ogout 銋? user-scoped localStorage?挾?賬?

**`AuthManager.USER_SCOPED_KEYS`**嚗static/news-search.js:7-14`嚗?6 ??user-scoped localStorage keys嚗?

| Key | 隤芣? |
|-----|------|
| `taiwanNewsSavedSessions` | 撠店 session ?” |
| `taiwanNewsFolders` | 雿輻???冗 |
| `taiwanNewsSessionsMigrated` | 銝甈⊥?migration flag |
| `nlweb_source_folders` | 靘?鞈?憭?|
| `nlweb_file_folders` | 瑼?鞈?憭?|
| `nlweb_selected_files` | 撌脤瑼? |

> 閮鳴?device-scoped UI prefs嚗nlweb-large-font`?nlweb-kg-hidden`嚗???冽迨皜嚗楊 user 靽???

**`_clearUserScopedStorageIfUserChanged(newUserId)`**嚗static/news-search.js:46-67`嚗??菜葫 user 霈????? user ?靽? cache嚗-2026-03-13?ocalStorage ?箔蜓???/ ?Ｙ?????敺???嚗?

**`AuthManager.logout()`**嚗static/news-search.js:157-165`嚗??澆敺垢 `POST /api/auth/logout` ?日 refresh token嚗??澆 `_handleAuthFailure()` 蝯曹?皜???

**`_handleAuthFailure()` 銝?USER_SCOPED_KEYS 皜**嚗static/news-search.js:213-220`嚗?logout ?湔**?⊥?隞嗆?** 6 ??keys嚗?瑼Ｘ user_id ?臬霈???

**CEO ?**嚗2B ?梁?餉?董??摰 > ??user ?靽?cache嚗-2026-05-01嚗ogout ?湔敹?皜??踹?銝??犖?典??汗?券? F12 / ?餃???唬?銝??user ?????/ ?Ｙ?????localStorage ????D-2026-03-13嚗?虫??蝡?敺?銝?蝒?

#### 1F-B ??Auth Failure 蝯曹?皜?瘚?嚗?026-05-01 update嚗?

> **?**嚗???`checkAuthOnLoad` ??`/api/auth/me` 401 敺 hide main UI / show login modal嚗?皜?`_user` / localStorage / ? render sidebar嚗???`isLoggedIn()` 隞?true ??敺? silent fallback 頛??user 鞈? ??cross-user leak?耨瘜? commit `138ae61` + commit `e0b5a41` + `memory/lessons-auth.md`?heckAuthOnLoad 401 path ?孵?怠???_handleAuthFailure?ebounce timer 頝?auth ???esetConversation 銝???reset all user-scoped state??畾萸?

**??**嚗遙雿?auth failure path嚗ogout / token expired / refresh fail / 401嚗敹?韏啣?銝??cleanup func嚗?閬??臬祕雿? logged-out ???`_user` ?雿?token 瘝?嚗 cross-user leak ??扯?擃?

**`AuthManager._handleAuthFailure()` 摰瘚?**嚗static/news-search.js:200-241`嚗?

| # | 甇仿? | 蝔?蝣潔?蝵?| ?箔?暻?|
|---|------|-----------|--------|
| 1 | **蝚砌?銵?*嚗ancel ???pending debounce timer嚗sessionManager._cancelPendingSave()`嚗?| `news-search.js:206-208` + `_cancelPendingSave` 摰儔??`news-search.js:507-513` | ??`setTimeout(..., 2000)` closure ??stale session ??token 憭望?敺?fire ??401 ???艘???func ??鈭活 wipe sidebar??*state mutation 銋?敹???cancel**??|
| 2 | 皜?`_accessToken` + `_user` | `news-search.js:209-210` | in-memory auth state |
| 3 | 皜?token / user ??localStorage嚗authUser`?authAccessToken`嚗?| `news-search.js:211-212` | ????auth cache |
| 4 | 皜?6 ??USER_SCOPED_KEYS嚗璇辣嚗?| `news-search.js:218-220` | 閬?禮1F-A |
| 5 | 皜?in-memory `savedSessions` ??? + `renderLeftSidebarSessions()` | `news-search.js:221-228` | ?血? DOM 畾??? user ??sidebar entries ?游銝活 reload |
| 6 | **`_resetMainUIState()` 皜?main-UI globals** | `news-search.js:235-237`嚗elper 摰儔??`news-search.js:1937-1949` | ?董??銝餌?Ｘ???user A 撠店 / 蝯? / report?elper ??wrap ?Ｘ? `resetConversation()`嚗歇皜?10 ??globals ??`conversationHistory`?sessionHistory`?chatHistory`?accumulatedArticles`?pinnedMessages`?pinnedNewsCards`?currentLoadedSessionId`?currentResearchReport`?currentConversationId`?currentResearchQueryId`嚗?**?? 6 ??helper 瘝項??**嚗_sessionDirty`?currentArgumentGraph`?currentChainAnalysis`?shareContentOverride`?currentLRSessionId`?currentAnalyticsQueryId`??*? > ?質情 > ?神** ??銝?瑽?`resetConversation`嚗 wrap??|
| 7 | `hideMainUI()` + `showAuthModal('login')` | `news-search.js:239-240` | UI ???餃?恍 |

> 甇仿? 1 ??`typeof` guard嚗typeof sessionManager !== 'undefined'`嚗?箔? module-init order ?脰風 ??`sessionManager` ??`authManager` 銋?摰??嚗?????停閫貊 auth failure 銝??詻untime invocations 銝敺遛頞?typeof check?郊撽?6 ?見??`typeof _resetMainUIState === 'function'` guard嚗? helper 摰儔??class 銋???

**?亙?”**嚗誑銝?璇?path ?賢??`_handleAuthFailure()`嚗???臬祕雿?

| ?亙 | 蝔?蝣潔?蝵?| 閫貊?? |
|------|-----------|---------|
| `AuthManager.logout()` | `news-search.js:164` | 雿輻?蜓???|
| `AuthManager.refreshToken()` catch block | `news-search.js:148` | refresh token 憭望?嚗???/ ?日嚗?|
| `checkAuthOnLoad()` 401 path | `news-search.js:926` | ?頛??`/api/auth/me` ??401嚗ommit `138ae61` ?對????hideMainUI + showAuthModal嚗?|
| `authenticatedFetch` ?批?怎? `refreshToken()` 憭望???| `news-search.js:1205`嚗隞?callsite嚗?| API ?澆??401 ??refresh 憭望? |

**??嚗SessionManager._cancelPendingSave()`**嚗news-search.js:507-513`嚗?蝝???`_saveTimer` + `_savePending`嚗? fire PUT??

**??嚗loadSessions` logged-in 憭望?銝?fallback localStorage**嚗news-search.js:266-292`嚗?logged-in ???server 憭望??? `[]` + `console.error`嚗?*銝?*?霈 `taiwanNewsSavedSessions`嚗???乩?銝??user 畾?嚗??/ ?Ｙ????韏?localStorage嚗-2026-03-13 銝?嚗底閬?D-2026-05-01 + `memory/lessons-auth.md`?ogged-in ???server 憭望? ??[] + console.error?挾?賬?

#### 1F-C ??Init Sync Architecture嚗?026-05-13 update嚗?

> **?**嚗?026-04-29 ~ 05-08 ??耨 9 ??cross-user leak patch嚗ommits `5ff8947` ??`e0b5a41`嚗?CEO 2026-05-08 ????犖?餃銋?撠梯???郊鞈?摨怎???銝隞亙?撘皜?暺畾菜 architectural refactor ?誨 1F-A / 1F-B ??case-by-case cleanup ??single sync flow?底閬?D-2026-05-13 + `docs/in progress/plans/frontend-init-sync-refactor-plan.md` + `memory/lessons-frontend.md`?rontend Init Sync ??Architectural Refactor?挾??

**?詨? invariant**嚗cache.user_id == JWT.user_id`??蝡?user-scoped state ?芸?閮勗 7 ??sync trigger ?? `UserStateSync` module 撖怠嚗隞神?仿?閬 bug??

**7 ??sync trigger**嚗?

| Trigger | ?菜葫?? | 銵 |
|---|---|---|
| **A. Login / Onboarding** | `login()` ??200 + ??JWT?completeOnboarding()` ??頝?`/` | fullReset ??`fetchInit()` ??`applyInit()` |
| **B. User identity change** | `checkAuthOnLoad()` ??`/api/auth/me` 200 銝?`data.user.id !== cached.id` | ??A |
| **C. Logout** | `logout()` / admin force logout | fullReset ??show login modal嚗? fetch嚗?|
| **D. 401 / refresh fail** | `authenticatedFetch` ??401 銝?refresh 憭望? | ??C |
| **E. Session click** | sidebar / popup / folder detail click | `GET /api/sessions/{id}` ???游摰嫣蒂 hydrate嚗?敺?cache 霈 |
| **F. Page reload / tab visible** | `DOMContentLoaded` checkAuthOnLoad?document.visibilitychange === 'visible'` | mismatch ??韏?A嚗atch ??soft refresh |
| **G. SSE envelope** | `handleStreamingRequest` / `handlePostStreamingRequest` 瘥?onmessage | envelope `data.user_id` ??`authManager._user.id` ??abort stream + trigger F |

**`UserStateSync` module 銝撘?*嚗static/news-search.js`嚗?
- `clearUserScopedState()` ??蝯曹?皜? A/B/C/D 蝭???user-scoped state嚗evice-scoped UI prefs 銝?嚗?
- `fetchInit()` ???澆 `GET /api/user/init` composite endpoint
- `applyInit()` ??hydrate in-memory caches + render UI嚗蝯?蝛粹? shared_sessions嚗hase 4b.5 Fix 2 鋆撥嚗?

**`assertUserIdentity(cached, fresh)` helper**嚗static/news-search.js`嚗?mismatch ??`UserStateSyncError`嚗aller 敹? `try/catch` 敺?trigger A ?游? reset??

**Backend composite endpoint `GET /api/user/init`**嚗code/python/webserver/routes/user_init.py`嚗?銝甈?round-trip ??`{ user, org, role, sessions, shared_sessions, preferences }`嚗??5 ?蝡?怒?

**Backend variant嚗nboarding 摰? auto-issue cookie**嚗ommit `2ee5508`嚗?`register_user()` / `activate_user()` ??敺?backend ?湔 `Set-Cookie: refresh_token`嚗?蝡航歲 `/` 敺?`checkAuthOnLoad()` 韏?cookie ?踹??JWT ??`assertUserIdentity` ?菜葫??MISMATCH嚗ached=admin / fresh=??user嚗? trigger A ?游? reset??

**SSE envelope `user_id` stamping**嚗hase 4b.5 Fix 1嚗ommit `c413465`嚗?backend 瘥?SSE emitter path 憿臬? stamp `user_id` 甈?嚗?*銝??桐? hook ?**嚗odebase ??ad-hoc emitter path嚗elper `_stamp_user_id_on_envelope(envelope, user_id)` ??`code/python/core/utils/message_senders.py` + `code/python/core/state.py` 憿臬??澆??

**?誨 / 靽?**嚗?
- **?誨**嚗歇敺?codebase 蝘駁嚗?1F-B ??`_resetMainUIState()` helper?_clearUserScopedStorageIfUserChanged()` + `login()` callsite?loadSavedSession()` metadata-only branch
- **靽?**嚗efense-in-depth嚗?`_sessionDirty` dirty gate?erver-side `_sanitize_session_history`?list_sessions` ORDER BY `updated_at DESC`?hared session `_isShared` ?拚

**撽**嚗?/9 E2E scenario PASS嚗 incognito vs 畾??汗?具楊 user 敹恍??nboarding 摰?頝單?撠5 reload?SE mismatch abort嚗weep audit 0 bug?oe + CEO review PASS嚗?026-05-13嚗?

---

## Part 2: Session Management

### 2A ??Schema

`search_sessions` 銵剁?閬?1A ????table list嚗unction tables ?誨?身閮? UUID[]??

### 2B-2C ??Session API + Service

**API**: `webserver/routes/sessions.py` ??15 endpoints (CRUD + migrate + feedback + export + sharing)
**Service**: `core/session_service.py` ??JSONB append pattern, 200KB size ??

### 2D ???垢?瑞宏

SessionManager class ?誨 localStorage??甈∠?亥孛??`POST /api/sessions/migrate`??

### 2E ??蝯??

| 璅∠? | ???| 隤芣? |
|------|------|------|
| JWT org_id 瘜典 | Done | middleware 撅?|
| ~~user_qdrant_provider.py~~ ??user_postgres_provider.py | Done | 撌脤蝘餉 PG嚗?026-03-27嚗?org_id filter ?舀 |
| user_data_manager.py | Done | create/list/delete ?券?舀 org_id |
| query_logger.py | Done | queries schema + log_query_start 撌脣? org_id |

---

## Part 3: Security Hardening

### 3A ??Rate Limiting

**瑼?**: `webserver/middleware/rate_limit.py`

| Endpoint | ?桀?撖阡???| 隤芣? |
|----------|-----------|------|
| `/api/auth/register` | 5/hr | ??撌脰矽蝺???50/hr嚗?|
| `/api/auth/forgot-password` | 3/hr | ??撌脰矽蝺???50/hr嚗?|
| `/api/auth/login` | 10/min | ??撌脰矽蝺???60/min嚗?|

Rate limit 撌脫 2026-03-05 infra adaptation ?矽?渲 production ?潦?

**IP ??**嚗?026-03-27 靽格迤嚗?`webserver/middleware/ip_utils.py` ?葉蝞∠? trusted-proxy 撽??????loopback/Docker 蝬脰楝??request ?縑隞?`X-Forwarded-For`嚗? `request.remote`?rate_limit.py` ??`auth.py` ?梁 `get_client_ip()`??

### 3B ??Audit Logs

**撌脣???*: `core/audit_service.py` + `webserver/routes/audit.py`
- Alembic migration `a3f8c2e51d07`
- fire-and-forget (`asyncio.create_task`)

### 3C ??CORS

**撌脣???*: `webserver/middleware/cors.py`
- `ALLOWED_ORIGINS` env var
- Dev mode ?迂 localhost
- 撌脖耨敺?wildcard + credentials bug

---

## Part 4: Data Migration

**瑼?**: `scripts/migrate_to_b2b.py` (idempotent)

?身閮蝘?Qdrant conversations + user_data + analytics?nfra migration 敺?Qdrant 蝘駁嚗蝘餌????閰摯??

---

## Part 5+: Research Collaboration

| ? | ???|
|------|------|
| Session Export (JSON/CSV) | 撌脣 session_service.py |
| RIS export + citation | TODO |
| Session Sharing (visibility) | Done |
| 蝯?蝞∠? UI + ?隢?蝔?| Done |

---

## Infra Adaptation

> Login system 撖虫???infra migration ?誑銝??粹?閬?????

### 擃?蝒?Qdrant 蝘駁

Infra migration 撠?Qdrant ?踵???PostgreSQL pgvector?誑銝?login 靽格**?券雿誥**嚗??神嚗?

| 瑼? | Login ??隞暻?| ?閬??暻?|
|------|---------------|-------------|
| `storage_providers/qdrant_storage.py` | ??user_id filter | ?冽??PostgreSQL retriever 銝剖祕雿?user_id filter |
| ~~`retrieval_providers/user_qdrant_provider.py`~~ | ~~??org_id filter~~ ??撌脣???2026-03-27嚗?| `user_postgres_provider.py` 撌脫??org_id filter |

### 銝剛?蝒?DB 蝯曹?

| ? | Login ?身 | ??Infra 撖阡? | ?拚? | ???|
|------|-----------|--------------|------|------|
| DB ??? | `ANALYTICS_DATABASE_URL` (Neon) | `DATABASE_URL` (?芸遣 PostgreSQL) | env var ?寧 `DATABASE_URL`嚗???fallback | ??Done |
| Connection pool | 瘥活 query ?圈?? | `psycopg_pool.AsyncConnectionPool` | auth_db.py ?寧 pool (min=1, max=5) | ??Done |
| Schema 蝞∠? | Alembic (auth + session + audit) | init.sql (articles + chunks) | ?啣? Alembic migration `b5e9d3f71a42` 蝯曹?蝞∠? | ??Done |
| Table ?勗? | 12 撘?auth/session 銵?| articles + chunks 銵?| 蝣箄???naming conflict | ??OK |

### 雿?蝒??函蔡?啣?

| ? | Login ?身 | ??Infra | ?拚? |
|------|-----------|---------|------|
| SSL | Render ?芸葆 HTTPS | Hetzner VPS | ?芸遣 Let's Encrypt |
| Cookie Secure | `Secure=request.secure` | ?蝣箔? HTTPS | ?函蔡????|
| CORS origin | Render domain | Hetzner domain | ??`ALLOWED_ORIGINS` |
| BASE_URL | Render URL | ??domain | ??env var |
| Middleware | aiohttp middleware | 銝? | ?湔?蔥 |
| ?垢 | aiohttp static files | 銝? | ?湔?蔥 |

---

## Part 6: Bootstrap Token Onboarding

> 摰???2026-03-17?2B ?冽銝?抵酉????admin ?? bootstrap token 撘???

### 閮剛?瘙箇?

B2B 璅∪?銝?銝??曆遙雿犖?芾?閮餃??dmin 鈭??Ｙ?銝甈⊥?bootstrap token嚗蝯衣璅?塚??冽??`/setup?token=xxx` ?摰?擐活閮剖?撣唾???

### Table Schema

```sql
CREATE TABLE bootstrap_tokens (
    id          TEXT PRIMARY KEY,           -- UUID
    token       TEXT UNIQUE NOT NULL,       -- secrets.token_urlsafe(32)
    org_id      TEXT NOT NULL,
    org_name    TEXT NOT NULL,
    created_by  TEXT NOT NULL,              -- admin user_id
    expires_at  TIMESTAMP NOT NULL,
    used_at     TIMESTAMP,                  -- NULL = unused
    used_by     TEXT                        -- user_id after use
);
```

SQLite ??PostgreSQL ?梁?詨? schema嚗auth_db.py` auto-create嚗?

### Setup ?

- **頝舐**: `GET /setup?token=xxx`嚗歇? PUBLIC_ENDPOINTS嚗?
- **UI**: ?函??????ｇ?霈鞊?logo + 瘛梯??憸冽嚗???login modal ?
- **瘚?**: token 撽? ??憛怠神 email/password/name ??撣唾?撱箇? ????閮

### CLI 撌亙

```bash
# ?Ｙ? bootstrap token
python -m auth.bootstrap_cli --org "Company Name" --expires 72

# ????token
python -m auth.bootstrap_cli --list

# ?日 token
python -m auth.bootstrap_cli --revoke <token_id>
```

`--expires` ?桐??箏????身 72 撠???

### register_user 靽格

`auth_service.py` ??`register_user()` ?啣?敹‵? `bootstrap_token: str`??啗?瘙?嚗?
1. 撽? token 摮銝雿輻???
2. 撱箇? user + org_membership嚗dmin 閫嚗uto-verified嚗?撖?verification email嚗?
3. 璅? token `used_at = now(), used_by = user_id`嚗?甈⊥改?

### 皜祈岫閬?

117/117 tests pass嚗 bootstrap token 瘚???test cases嚗?

---

## Known Gaps

> 蝔?蝣澆祟閮?曄?????雿萄????????

### Must Fix

| # | ?? | ?湧?摨?| 隤芣? |
|---|------|--------|------|
| ~~1~~ | ~~**Tests 銝???*~~ | ~~High~~ | ??撌脣???2026-03-16嚗?113/113 tests pass嚗??B2B bootstrap model |
| 2 | ~~baseHandler.py ?芣~~ | ~~High~~ | auth middleware soft-auth + api.py 瘜典 user_id/org_id嚗?026-03-05嚗?|
| 3 | ~~Rate limit ?祝~~ | ~~Medium~~ | ??撌脰矽蝺 production ?潘?2026-03-05嚗?|
| 4 | ~~org_id ?亥岷 filter 蝻箏仃~~ | ~~Medium~~ | list/delete 撌脣? org_id filter嚗?026-03-05嚗?|
| 5 | ~~query_logger org_id~~ | ~~Medium~~ | queries schema + log_query_start 撌脣? org_id嚗?026-03-05嚗?|

### Completed (Code Review 2026-03-05)

| # | 靽桀儔? | 憿? |
|---|----------|------|
| M1 | rate_limit_middleware ?芾酉????? middleware __init__ | MUST FIX |
| M2 | /api/org/accept-invite 銝???public endpoint ??蝘駁 | MUST FIX |
| M3 | email HTML template injection ??html.escape | MUST FIX |
| M4 | _pg_execute autocommit=True ?游? transaction ????conn.commit() | MUST FIX |
| S5 | Boolean `= 1` ??PG 銝摰????券???`= ?` + True/False | SHOULD FIX |
| S6 | JWT_SECRET ?瑕漲瑼Ｘ ??startup warning if < 32 chars | SHOULD FIX |
| S7 | _adapt_query_pg JSONB `?` 銵? ????TODO 閮餉圾 | SHOULD FIX |
| S8 | CSV formula injection ??_csv_safe() sanitizer | SHOULD FIX |
| S9 | parseInt ??try/except ??sessions.py + audit.py ??400 ? | SHOULD FIX |
| S10 | PG JSONB append ??size check ??append_message/articles ?炎??| SHOULD FIX |

### Deferred (Code Review 2026-03-05)

> 隞乩???code review ?潛雿???乓??閬?憭折?瑽??質圾瘙箇????

| # | ?? | ? | ?芸?摨?|
|---|------|------|--------|
| D1 | ??DB pool嚗uth_db + analytics_db嚗??瘚芾祥 | ?蝯曹? DB layer ??嚗nfra Migration ??韏瑁???| Low |
| ~~D2~~ | ~~??schema 蝞∠?嚗lembic + initialize() ?? DDL嚗~ ??**??**嚗?026-05-07嚗?撖阡???alembic ??VPS 敺?頝?嚗eploy.yml 瘝?alembic step + alembic_version 銵其?摮嚗EO ??寞? B嚗lembic ?交? schema source of truth??*Resolved嚗?026-05-13嚗??寞? B 摰?**嚗lembic 霈銝 schema source of truth嚗auth_db.initialize()` ??sanity check嚗 DDL嚗?deploy.yml ??`alembic upgrade head` step嚗???migrations idempotent?底閬?`docs/specs/database-spec.md` + `docs/decisions.md`?lembic ?交? schema source of truth嚗獢?B嚗?| ~~Approved嚗?蝔?摰?~~ ??**Resolved** |
| ~~D3~~ | ~~localStorage 摮?JWT token嚗SS 憸券嚗~ | ??撌脣???2026-03-11 BP-1嚗?敺垢 `set_cookie(httponly=True)`嚗?蝡?`authenticatedFetch()` ??`credentials: 'same-origin'` | ~~Medium~~ |
| D4 | org_id 撖怠 JWT嚗evoke ?辣??| JWT 憭拍??嚗? token blacklist 璈嚗ostgreSQL table嚗?銴?摨阡? | Low |
| D5 | login_attempts 銵函 cleanup 璈 | 鞈?憓?ｇ??臬? scheduled SQL DELETE | Low |
| ~~D6~~ | ~~_windows dict 閮擃援瞍?rate_limit嚗~ | ????憿?撌脫? sliding window eviction嚗ey ?賊???嚗? 璇???? IP嚗?restart 皜征?ingle-instance ?函蔡?⊿? Redis | ~~Low~~ |
| ~~D7~~ | ~~email_service 瘥活 import time 霈 env var~~ | ??撌脩Ⅱ隤??? | ~~Very Low~~ |

### E2E 蝚砌?頛芰?橘?2026-03-17嚗?

> 8 ??憿?靘 E2E 蝚砌?頛芣葫閰艾?*?券靽桀儔 + 蝚砌?頛芷?霅?嚗?026-03-17嚗?*

| # | ?? | ?湧?摨?| ???|
|---|------|--------|:----:|
| ~~E1~~ | Setup ??敺?auto redirect | Medium | ??|
| ~~E2~~ | Bootstrap 銝? verification email | High | ??|
| ~~E3~~ | ?芰??login modal X ???梯? | Low | ??|
| ~~E4~~ | ??? + ??? + 撌脣???badge | Medium | ??|
| ~~E5~~ | 鋡怠??典董??仿＊蝷箝董?歇鋡怠??具?| High | ??|
| ~~E6~~ | ?芷撣唾? hard delete + 皜??鞈? | High | ??|
| ~~E7~~ | Login modal 撖Ⅳ甈?皜征 | Medium | ??|
| ~~E8~~ | 敹?撖Ⅳ reset password ??????| High | ??|

### Admin Resend Activation嚗?026-05-07嚗?

> 摰? admin ???靽⊥???+ activate page ?? UI嚗ommits `269aa7a`?eb7661a`嚗? ??commits嚗lan: `docs/in progress/plans/admin-resend-activation-plan.md`嚗?

**?啣? endpoint**嚗POST /api/admin/resend-activation`嚗ate limit 5/hr per-IP嚗?admin ?臬??芸???member ??Ｙ? activation token + 撖縑?? token ?芸? invalidate嚗???甈?嚗?蝯?隞塚??? org admin ??403??摮 ??404?歇? / 撌脣?????400??

**`list_org_members` SQL ??`is_activated` 甈?**嚗(u.password_hash IS NOT NULL) as is_activated` + Python `bool()` 甇????????2026-03-17?ELECT 瞍?雿????垢?憯?雿?unit test ?券???頩?

**?垢 admin org modal**嚗??憿舐內??撖??其縑????`is_activated === false` 璇辣皜脫?嚗??Ｘ? `btn-force-logout` ??pattern嚗querySelectorAll().forEach()` ?? event delegation嚗??Ｘ?憸冽銝?湛??遣蝡??∪? `await reloadOrgMembers()` ?芸??瑟嚗耨 pre-existing bug嚗??祈????? modal嚗?

**Activate page GET 銝?憓???server-side render**嚗activate_page_handler`嚗?
- **甇?虜**嚗oken 摮 + `password_hash IS NULL` + ?芷??????Ｘ?撖Ⅳ閮剖?銵典
- **??**嚗expires < now` ?????券??撌脤???隢蝯∠恣???
- **token 銝???/ 撌脣???*嚗?雿菜?獢迨????撌脣仃???銋?撌脰身摰?撖Ⅳ嚗?敺???伐??亙?閮?蝣潘?隢蝯∠恣??? ???餃??嚗EO ? 2026-05-07嚗??user enumeration嚗?

**Schema migration**嚗e39a746fb916_align_users_schema_with_initialize`嚗? `email_verification_expires DOUBLE PRECISION` + `password_hash` ??nullable嚗G ??inspector + IF NOT EXISTS guard嚗PS 頝?= no-op嚗?**雿??VPS 銝??瑁?甇?migration**嚗2 ??霅圈?嚗底閬?`alembic-architecture-fix-plan.md`??

### Cross-User ? / Logout 蝝敺?2026-05-01嚗?

> 2 ??憿?靘 B2B ?梁?餉?董?? cross-user leak 隤踵??*?券靽桀儔**?底蝝啣祕雿? 禮1F-A??F-B + D-2026-05-01 + `memory/lessons-auth.md`?ross-User ? / Logout 蝝敺?2026-05-01嚗挾?賬?

| # | ?? | ?湧?摨?| ???| 隤芣? |
|---|------|--------|:----:|------|
| ~~E9~~ | `logout()` 瘝? user-scoped localStorage嚗? ??keys嚗?| High | ??| commit `24d39f4`嚗_handleAuthFailure` 皜?`USER_SCOPED_KEYS` 6 ??keys嚗taiwanNewsSavedSessions`?taiwanNewsFolders`?taiwanNewsSessionsMigrated`?nlweb_source_folders`?nlweb_file_folders`?nlweb_selected_files`嚗EO ?嚗2B ?梁?餉摰 > ??user ?靽?cache?底閬?禮1F-A??|
| ~~E10~~ | `checkAuthOnLoad` 401 path 瘝? `_user` / 瘝?render ??silent fallback 頛??user 鞈? | High | ??| commit `138ae61` + commit `e0b5a41`嚗?01 path ?孵?怠???`_handleAuthFailure()`嚗ancel pending timer + 皜?_user + 皜?token + 皜?user-scoped localStorage + `_resetMainUIState` 皜?main-UI globals + render 蝛?sidebar + show login modal嚗遙雿?auth failure path ?質粥????cleanup func?底閬?禮1F-B??|

### 敺矽??(2026-03-18)

| # | ?? | ???|
|---|------|------|
| I1 | `bootstrap_tokens` schema spec vs code ?賢榆 ??spec 閮剛???`org_id`?created_by`嚗ser_id嚗used_by`嚗ser_id嚗LI ?舀 `--list/--revoke`嚗ode ?舐陛??嚗org_name_hint`? `created_by`?used_by_email`?LI ?芣? `--org/--expires`???code ?甇?虜?舐嚗?閮剛?摰摨行??spec 瘞湔???隤踵嚗撌格 code review ??嚗ebug 蝪∪?嚗gent ?芸??游祕雿? | 敺矽??|

### Will Be Invalidated by Infra Migration

| # | ?? | 隤芣? |
|---|------|------|
| 6 | qdrant_storage.py 靽格 | Qdrant 蝘駁敺?撱?|
| 7 | user_qdrant_provider.py 靽格 | ?? |
| 8 | migrate_to_b2b.py 蝭? | Qdrant conversation ?瑞宏銝??拍 |

---

## File Inventory

### ?啣???獢?敺?RG repo ?蔥嚗?

| 瑼? | 撌脤?霅?|
|------|--------|
| `auth/__init__.py` | Yes |
| `auth/auth_db.py` | Yes |
| `auth/auth_service.py` | Yes |
| `auth/email_service.py` | Yes |
| `webserver/routes/auth.py` | Yes |
| `webserver/routes/sessions.py` | Yes |
| `webserver/routes/audit.py` | Yes |
| `webserver/middleware/rate_limit.py` | Yes (撌脰矽蝺? |
| `webserver/middleware/cors.py` | Yes |
| `core/session_service.py` | Yes |
| `core/audit_service.py` | Yes |
| `alembic/` + `alembic.ini` | Yes |
| `scripts/migrate_to_b2b.py` | Yes |

### 撌脖耨?寧?瑼?嚗? merge ?脖蜓 repo嚗?

| 瑼? | 撌脤?霅?| 瘜冽?鈭? |
|------|--------|---------|
| `webserver/routes/__init__.py` | Yes | ? auth/sessions/audit routes |
| `webserver/middleware/auth.py` | Yes | 摰?神 |
| `webserver/aiohttp_server.py` | Yes | 蝘駁 chat ????|
| `core/config.py` | Yes | 蝘駁 OAuth config |
| `webserver/routes/user_data.py` | Yes | user_id 敺?request['user'] |
| `static/news-search-prototype.html` | Yes | Login modal + org modal |
| `static/news-search.js` | Yes | AuthManager + SessionManager |
| `static/news-search.css` | Yes | Modal styles |

### 皜祈岫瑼?嚗?026-03-16 ?啣?嚗?

| 瑼? | 隤芣? |
|------|------|
| `tests/test_auth_service.py` | 撌脣遣蝡???B2B bootstrap model |
| `tests/test_auth_middleware.py` | 撌脣遣蝡?|
| `tests/test_session_service.py` | 撌脣遣蝡?|

### ?芷??獢?撌脩Ⅱ隤?

| 瑼? |
|------|
| `chat/` (9 files) |
| `webserver/routes/chat.py` |
| `webserver/routes/oauth.py` |
| `config/config_oauth.yaml` |

---

## Environment Variables

| 霈 | 隤芣? | ???| Infra ?拚? |
|------|------|------|-----------|
| `DATABASE_URL` | 蝯曹? DB URL | ?啣? | ?誨 ANALYTICS_DATABASE_URL |
| `JWT_SECRET` | JWT 蝪賢?撖 | 撌脖蝙??| 銝? |
| `RESEND_API_KEY` | Resend API key | 撌脖蝙?剁??舫嚗?| 銝? |
| `RESEND_FROM_EMAIL` | ?潔縑?啣? | 撌脖蝙??| 銝? |
| `BASE_URL` | 蝟餌絞 base URL | 撌脖蝙??| ?寧??domain |
| ~~`NLWEB_DEV_AUTH_BYPASS`~~ | ~~?璅∪?頝喲?隤?~~ | **DELETED 2026-05-19**嚗ommit `c20f545`嚗?| 銝?雿輻嚗2E 撘瑕?祕 login嚗?|
| `ALLOWED_ORIGINS` | CORS ?迂 origin | 撌脖蝙??| ?寧??domain |

---

## Dependencies

| 憟辣 | ?券?| ???|
|------|------|------|
| `bcrypt` | 撖Ⅳ hash | 撌脖蝙??|
| `PyJWT` | JWT token | 撌脖蝙??|
| `resend` | Email ?潮?| 撌脖蝙?剁??舫嚗?|
| `alembic` | DB migration | 撌脖蝙??|
| `psycopg` | Async PostgreSQL | 撌脖蝙??|

---

## Cost Analysis

| ?挾 | ?祥 | 隤芣? |
|------|------|------|
| ???| $0 | Free Tier + dev console email |
| Early B2B (<50 users) | ~$1/??| ?芣? domain ?嚗B 撌脣??Hetzner VPS嚗?|
| Growth (50-500 users) | ~$20/??| Resend Pro $20嚗B 撌脣??VPS嚗?|
| Scale (500+ users) | ~$100+/??| Resend Business + ?航??? VPS |

瘜剁???spec ? Neon PostgreSQL 鞎餌?nfra migration 敺?DB 撌脣??Hetzner VPS ?祥銝哨?銝閮?
