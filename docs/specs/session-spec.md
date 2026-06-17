# Session System Specification

> **Owner**: NLWeb Team
> **Last Updated**: 2026-05-15
> **Status**: Active（B2B 已上線；2026-05-13 完成 Frontend Init Sync Refactor — `UserStateSync` 取代過去 9 個 case-by-case patch；2026-05-01 完成 cross-user 隔離 + 三層 _serverId hydration + dirty flag 為 defense-in-depth 殘留）
> **Source of Truth**: 本檔。原散落於 `frontend-spec.md` / `login-spec.md` / `docs/in progress/plans/` 的 session 段落以本檔為準。

---

## 目錄

1. [概覽](#1-概覽)
2. [資料模型](#2-資料模型)
3. [後端 API](#3-後端-api)
4. [前端 SessionManager](#4-前端-sessionmanager)
5. [持久化路徑](#5-持久化路徑)
6. [_serverId 三層 Hydration](#6-_serverid-三層-hydration)
7. [Dirty Flag 紀律](#7-dirty-flag-紀律)
8. [Cross-User 隔離](#8-cross-user-隔離)
9. [SSE Handler Black-List](#9-sse-handler-black-list)
10. [Sanitize session_history Defense-in-Depth](#10-sanitize-session_history-defense-in-depth)
11. [組織空間共享](#11-組織空間共享)
12. [Cancel + Retry Interrupt 流程](#12-cancel--retry-interrupt-流程)
13. [Architecture Decisions](#13-architecture-decisions)
14. [檔案清單](#14-檔案清單)
15. [環境變數](#15-環境變數)
16. [Known Issues / Future Work](#16-known-issues--future-work)

---

## 1. 概覽

Session 系統負責**搜尋對話的持久化、跨裝置同步與組織內共享**，是 NLWeb 上線後從 single-user PoC 升級為 B2B SaaS 的核心基礎。一個 session 對應使用者的一段研究脈絡，其中可能包含多輪 query、Deep Research / Live Research 報告、引用文章、釘選新聞、知識圖譜（KG）、自由對話訊息與 LLM-driven 推論鏈（argumentGraph + chainAnalysis）。

> **【架構性前提】Frontend Init Sync Refactor（D-2026-05-13）**
>
> 自 D-2026-05-13 起，**所有 session-related user-scoped state 寫入只透過 `UserStateSync` 7 個 sync trigger 之一**。本 spec 描述的所有「session list / session detail / shared sessions」hydrate、in-memory 更新、localStorage 清理動作，都是這 7 個 trigger 的觀察行為，**禁止任何 ad-hoc 寫入點**。
>
> **Backend 入口**：`GET /api/user/init`（`code/python/webserver/routes/user_init.py`）— 一次 round-trip hydrate `{ user, org, role, sessions, shared_sessions, preferences }`。`user` payload 含 `org_id` + `role`（mirror `/api/auth/login` + `/api/auth/me`，commit `228a93a` 補的 shape contract）。
>
> **Frontend 入口**：`UserStateSync` module（`static/news-search.js`）3 函式：
> - `clearUserScopedState()` — 清光 USER_SCOPED_KEYS + 6 個 user-scoped main-UI globals（取代 §8.5 已 superseded 的舊 helper）
> - `fetchInit()` — 呼叫 `GET /api/user/init`
> - `applyInit()` — hydrate `savedSessions` / `sharedSessions` / `preferences` 進 in-memory + render UI
> - 另含 convenience：`fullReset()`、`runInitSync()`（in-flight de-dupe guard）
>
> **7 個 sync trigger（唯一合法的 session-state 寫入點）**：
>
> | Trigger | 觸發時機 | Session 模組相關性 |
> |---------|---------|------------------|
> | (A) Login / Onboarding 完成 | `AuthManager.login()` 成功、`activate_user()` auto-issue refresh cookie 後跳 `/` | 重新 hydrate `savedSessions` + `sharedSessions` |
> | (B) User identity change | `checkAuthOnLoad` 偵測 `cached.user_id ≠ JWT.user_id` | clearUserScopedState → fullReset 後再 runInitSync |
> | (C) Logout | `AuthManager.logout()` / `btnLogoutAll` | clearUserScopedState（含清 `savedSessions` 陣列 + USER_SCOPED_KEYS） |
> | (D) 401 / refresh fail | `authenticatedFetch` 401 → `refreshToken()` 失敗 catch | 同 (C) |
> | **(E) Session click** ⭐ | Sidebar session click / shared session click | 詳見 §1.4「Trigger E Session-Specific Behavior」 |
> | **(F) Page reload / tab visible** ⭐ | DOMContentLoaded、`visibilitychange` 切回 tab | 詳見 §1.4「Trigger F Session-Specific Behavior」 |
> | (G) SSE envelope | `handleStreamingRequest` / `handlePostStreamingRequest` 每個 onmessage 比對 `data.user_id ≠ authManager._user.id` | mismatch → abort stream + trigger F |
>
> **核心 invariant**：`cache.user_id == JWT.user_id`，mismatch 由 `assertUserIdentity(cached, fresh)` helper 拋 `UserStateSyncError`（codes：`MISMATCH` / `MISSING_CACHED` / `MISSING_FRESH`），caller 必須 `try/catch` 後 trigger A 整套 reset。
>
> **詳細設計**：見 `docs/specs/login-spec.md` 同名段落、`docs/decisions.md` D-2026-05-13、`docs/in progress/plans/frontend-init-sync-refactor-plan.md`。本 spec 後續描述會在 session-specific 細節處反覆引用此架構。

### 1.1 在系統中的角色

```
┌──────────────────────────────────────────────────────────────┐
│  Browser (static/news-search.js)                             │
│  ┌─────────────────────┐     ┌──────────────────────────┐   │
│  │ savedSessions[]     │ ←→  │ localStorage (fallback)  │   │
│  │ in-memory globals   │     │   taiwanNewsSavedSessions│   │
│  │ (_sessionDirty etc) │     └──────────────────────────┘   │
│  └─────────┬───────────┘                                    │
│            │ SessionManager API（debounce 2s）              │
└────────────┼─────────────────────────────────────────────────┘
             │ /api/sessions {GET|POST|PUT|DELETE|PATCH}
             ▼
┌──────────────────────────────────────────────────────────────┐
│  webserver/routes/sessions.py（aiohttp handlers）             │
│           │                                                  │
│           ▼                                                  │
│  core/session_service.py（SessionService）                   │
│           │                                                  │
│           ▼                                                  │
│  PostgreSQL (production) / SQLite (local dev)                │
│  search_sessions + org_folders + session_shares              │
└──────────────────────────────────────────────────────────────┘
```

### 1.2 核心職責

| 職責 | 實作位置 |
|------|----------|
| 自動 debounced PUT（2s）保存 | `SessionManager.scheduleSave` (`news-search.js:481`) |
| LocalStorage fallback（匿名/離線） | `SessionManager._saveToLocalStorage` (`news-search.js:546`) |
| 組織內 session 共享（visibility） | `SessionService.set_visibility` / `get_shared_sessions` |
| Cross-user 隔離 | `AuthManager._clearUserScopedStorageIfUserChanged` + `_handleAuthFailure` |
| Cancel + Retry 中斷恢復 | `interruptedSearch` 標記 + `showInterruptedSearchNotice` |
| Live Research state 持久化 | `live_research_state` JSONB column（D-2026-04-15） |
| SSE 中間訊息防汙染 | 前端 black-list case + `SessionService._sanitize_session_history` |

### 1.3 關鍵設計理念

1. **單一 sync flow（D-2026-05-13）**：所有 session-related user-scoped state 寫入只透過 `UserStateSync` 7 個 sync trigger。**禁止任何 ad-hoc 寫入點**。`cache.user_id == JWT.user_id` 為硬 invariant。取代了 D-2026-05-01 之前的 9 個 case-by-case patch。
2. **localStorage 為主、Server 為輔（D-2026-03-13）**：搜尋結果量大、即時性高，server round-trip 不划算。Server session 用於跨裝置 + 跨瀏覽器恢復。在 D-2026-05-13 之後此原則仍適用於 in-flight 編輯快取，但 user-scoped 寫入仍走 UserStateSync。
3. **Auth boundary 是 user-scoped state 的 hard boundary（D-2026-05-01）**：logout/login/auth-failure 都必須清乾淨，不能假設「同一 origin = 同一 user」。在 D-2026-05-13 之後由 `UserStateSync.clearUserScopedState` 統一執行。
4. **Pre-Navigation Save 必須配 Dirty Flag（D-2026-05-01）**：避免「純瀏覽就 PUT」造成 sort jitter。Defense-in-depth 殘留（見 §1.5）。
5. **SSE black-list（非 white-list）（D-2026-05-01）**：unknown message_type 預設 merge 比 ignore 安全。Defense-in-depth 殘留（見 §1.5）。
6. **Defense-in-Depth**：跨層 boundary（server SSE → client → server PG）每層都過濾，不假設上游乾淨。

### 1.4 Trigger E + F：Session 模組最相關的兩個 Sync Trigger

Trigger E（session click）與 Trigger F（page reload / tab visible）是 7 個 sync trigger 中**最直接影響 session list / session detail / shared sessions 的行為**。session-spec 的後續章節在描述具體流程時應隱含這兩個 trigger 為合法寫入入口。

#### 1.4.1 Trigger E — Session Click

**觸發來源**：
- Sidebar own session click（`renderLeftSidebarSessions` 渲染的 item）
- 「組織空間」shared session click（`renderSharedSessions` 渲染的 item，data-shared-session-id）

**對 session 模組的影響**：
- **不**重新呼叫 `/api/user/init`（避免每次點 session 都 round-trip）
- 點 own session：從 in-memory `savedSessions` 找 entry → 呼叫 `GET /api/sessions/{id}` lazy-load full conversation history → `loadSavedSession(hydrated)`
- 點 shared session：直接 fetch `/api/sessions/{sharedId}` → hydrated 物件帶 `_isShared: true` + `_ownerUserId` → `loadSavedSession`（§11.4）
- **invariant 檢查**：點 session 後若觸發 SSE stream，下一個 envelope 走 trigger G 比對 `data.user_id ≠ authManager._user.id` → mismatch 走 trigger F 整套 reset
- **Y-1 guard 殘留**：shared session click 不 spawn 自己 row（§11.5），由 `_isShared` tag + `saveCurrentSession` early return 守住（defense-in-depth，**不取代** Init Sync 主流）

#### 1.4.2 Trigger F — Page Reload / Tab Visible

**觸發來源**：
- `DOMContentLoaded` page-load（cold start）
- `visibilitychange` event 切回 tab（warm start，偵測背景期間 user 是否在他 tab 切了帳號）

**對 session 模組的影響**：
- 呼叫 `UserStateSync.runInitSync({ keepInviteToken })` → 內部 `fullReset` + `fetchInit` + `applyInit`
- `applyInit` 把 `payload.sessions` 寫入 `savedSessions`（取代過去 `loadSessions()` ad-hoc fetch）
- `applyInit` 把 `payload.shared_sessions` 寫入 `sharedSessions`（取代過去 `loadSharedSessions()` ad-hoc fetch）
- `_serverId` hydration：`savedSessions = payload.sessions.map(s => ({ ...s, _serverId: s._serverId || (UUID-shape ? s.id : null) }))`（§6.1 三層 hydration 的 Layer 3 改由 `applyInit` 統一處理）
- **invariant 檢查**：`assertUserIdentity(cached=authManager._user, fresh=payload.user)` mismatch → 拋 `UserStateSyncError(MISMATCH)` → 觸發 trigger A 整套 reset
- Trigger F 是 cross-tab 切換帳號的主要偵測點（user 在 tab A 切了 admin → tab B visible 後 runInitSync 偵測 mismatch）

### 1.5 Defense-in-Depth：D-2026-05-13 後仍保留的層

D-2026-05-13 Frontend Init Sync Refactor **取代**過去 9 個 case-by-case patch 為 single sync flow（取代清單見 §13 / `docs/decisions.md`），但下列層**刻意保留**作為 defense-in-depth（**不取代** Init Sync 主流，是額外保險）：

| 層 | 位置 | 防護什麼 |
|---|------|---------|
| `_sessionDirty` flag | `news-search.js`（§7） | 防止純瀏覽（loaded session 沒新內容）也送 PUT spawn 出 `updated_at = NOW()` |
| Server-side `_sanitize_session_history` classmethod | `session_service.py:570-616`（§10） | 即使 client SSE black-list 漏一個 case，server filter 仍 catch 污染 entries |
| `list_sessions` `ORDER BY updated_at DESC` | `session_service.py`（§3.1） | UI 排序穩定，避免重新整理 sidebar 順序跳動 |
| Shared session click `_isShared` tag + `saveCurrentSession` early return | `news-search.js:1712-1722`（§11.5 Y-1） | 跨 user 隔離 — shared session click 不會 spawn 自己的 row |

**為何保留**：Init Sync 主流負責 user-scoped state 的**寫入入口統一**；上述四層負責**個別 boundary 的局部正確性**。即使 Init Sync 主流偶有 bug（e.g. runInitSync race），保留層仍守住資料污染。

---

## 2. 資料模型

### 2.1 PostgreSQL `search_sessions` Table

定義位置：`code/python/auth/auth_db.py:589-609`（PG）、`code/python/auth/auth_db.py:392-415`（SQLite mirror）。
Migration：Alembic `c1c6deac2013_add_session_tables`。

```sql
CREATE TABLE search_sessions (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    org_id                UUID NOT NULL REFERENCES organizations(id),
    title                 VARCHAR(500),

    -- JSONB 欄位（內容資料）
    conversation_history  JSONB DEFAULT '[]',
    session_history       JSONB DEFAULT '[]',
    chat_history          JSONB DEFAULT '[]',
    accumulated_articles  JSONB DEFAULT '[]',
    pinned_messages       JSONB DEFAULT '[]',
    pinned_news_cards     JSONB DEFAULT '[]',
    research_report       JSONB DEFAULT '{}',
    team_comments         JSONB DEFAULT '[]',
    live_research_state   JSONB,                   -- 2026-04-15 加入

    -- Metadata
    user_feedback         VARCHAR(20),             -- 'thumbs_up' | 'thumbs_down' | NULL
    admin_note            TEXT,
    visibility            VARCHAR(20) DEFAULT 'private',  -- private | team | org
    is_archived           BOOLEAN DEFAULT FALSE,

    -- Timestamps
    deleted_at            TIMESTAMPTZ,             -- soft delete; NULL = active
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    updated_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_sessions_user_org ON search_sessions(user_id, org_id);
CREATE INDEX idx_sessions_updated  ON search_sessions(updated_at DESC);
CREATE INDEX idx_sessions_visibility ON search_sessions(org_id, visibility)
    WHERE visibility != 'private' AND deleted_at IS NULL;
CREATE INDEX idx_sessions_deleted ON search_sessions(deleted_at)
    WHERE deleted_at IS NOT NULL;
```

> **5 個 History-Related JSONB 欄位**（對齊 PG schema）：
>
> | 欄位 | 用途 | 對應前端 in-memory |
> |------|------|-------------------|
> | `conversation_history` | 該 session 全部 user query 列表（語意層的「我問過什麼」） | `conversationHistory` |
> | `session_history` | 每筆 query + 結果的完整快照（含 `data.content` articles array、`researchReport`、`argumentGraph`、`chainAnalysis`、`knowledgeGraph`），詳細結構見 §2.3 | `sessionHistory` |
> | `chat_history` | Free conversation mode 的訊息列表（role/content） | `chatHistory` |
> | `accumulated_articles` | 跨 query 累積的文章（含 annotation） | `accumulatedArticles` |
> | `research_report` | DR / LR 完整報告物件（最新一份） | `currentResearchReport` |
>
> 這 5 個欄位 + `pinned_messages` / `pinned_news_cards` / `team_comments` / `live_research_state` 共 9 個 JSONB content 欄位，皆受 §3.1 `update_session` allowed_fields 與 §10 sanitize defense-in-depth 管控。

### 2.2 SQLite Mirror（本機開發）

| PG 型別 | SQLite 對應 | 備註 |
|---------|-----------|------|
| `UUID PRIMARY KEY` | `TEXT PRIMARY KEY` | Python 端傳 UUID string |
| `JSONB` | `TEXT` | `json.dumps()` 序列化 |
| `BOOLEAN` | `INTEGER` (0/1) | `is_archived` 在 read 時轉回 bool（`session_service.py:632`） |
| `TIMESTAMPTZ` | `REAL`（Unix epoch float） | `_now()` 依 db_type 切換（`session_service.py:34-42`） |

### 2.3 `session_history` JSONB 結構

每筆 entry 是一次 query + 結果的快照：

```json
{
  "query": "搜尋關鍵字",
  "mode": "search" | "deep_research" | "chat" | "live_research",
  "data": {
    "message_type": "nlws" | "decontextualized_query" | ...,
    "content": [ /* 文章 array, schema.org NewsArticle */ ],
    "answer": "...",
    "summary": "..."
  },
  "isDeepResearch": false,
  "researchReport": { ... },        // 僅 DR entry
  "argumentGraph": [ ... ],         // 僅 DR entry
  "chainAnalysis": { ... },         // 僅 DR entry
  "knowledgeGraph": { ... },        // KG persistence
  "timestamp": 1714521600000
}
```

**重要紀律**：`data.content` 必須是 array（articles）或 omit；**絕不是 string**。詳見 §10「Sanitize Defense」。

### 2.4 `live_research_state` JSONB 結構

Live Research 跨 request 持久化的 state，定義於 `code/python/reasoning/live_research/stage_state.py`：

```python
@dataclass
class LiveResearchStageState:
    current_stage: int = 0           # 0=未開始, 1-6=對應 Stage
    stage_status: str = "pending"    # pending | in_progress | checkpoint | completed
    checkpoint_prompt: str = ""
    context_map_json: str = ""              # ContextMap.model_dump_json()
    initial_context_map_json: str = ""      # Version 0 snapshot
    completed_sections: List[str] = []
    style_features_json: str = ""
    format_specs: Dict[str, str] = {}
    written_sections: List[Dict] = []
    executed_searches: List[str] = []
    created_at: str = ""             # ISO datetime
    last_updated_at: str = ""
```

每次 LR request 開始時從 DB 讀取（`methods/live_research.py:248-254`），結束時寫回（`methods/live_research.py:213-222`）。

Migration：`code/python/tools/migrate_live_research.py`（PG: `JSONB`；SQLite: `TEXT`）。

### 2.5 Junction Tables

| Table | 用途 | Schema |
|-------|------|--------|
| `org_folders` | 組織共用資料夾 | `(id, org_id, name, created_by, ...)` |
| `org_folder_sessions` | folder ↔ session 多對多 | `(folder_id, session_id, added_at)` |
| `session_shares` | per-user 共享（保留，目前未啟用） | `(session_id, shared_with_user_id, shared_at)` |

> 目前 sharing 走 `visibility` 欄位 + org-wide 邏輯，不依賴 `session_shares` table。後者保留供未來 per-user share 擴充。

### 2.6 Size Monitoring

`SessionService._check_jsonb_size()`（`session_service.py:640-648`）對每個 JSONB 欄位寫入時檢查大小，超過 **200 KB** 警告：

```python
JSONB_SIZE_WARNING_THRESHOLD = 200 * 1024
```

超過時 `logger.warning` 提示「Consider migrating to session_messages table」（未來如需拆 table 的 trigger）。

---

## 3. 後端 API

實作位置：`code/python/webserver/routes/sessions.py`（15 endpoints）。所有 handler 用 `_get_user_info()` 從 `request['user']` 取 auth context；缺 `org_id` 回 400；未認證回 401。

### 3.1 CRUD Endpoints

| Method | Path | Handler | 說明 |
|--------|------|---------|------|
| GET | `/api/sessions` | `list_sessions_handler` | List own sessions, ORDER BY `updated_at DESC` |
| POST | `/api/sessions` | `create_session_handler` | 建立 session，回 `{success, session: {id, ...}}` |
| GET | `/api/sessions/{id}` | `get_session_handler` | 走 `get_session_shared`（owner 或 team/org visibility） |
| PUT | `/api/sessions/{id}` | `update_session_handler` | 部分更新，allowed_fields 過濾 |
| DELETE | `/api/sessions/{id}` | `delete_session_handler` | Soft delete（`deleted_at = NOW()`） |
| POST | `/api/sessions/{id}/restore` | `restore_session_handler` | 30 天內復原 |

`list_sessions` query params：`limit` (default 50)、`offset` (0)、`include_archived` (false)。

`update_session` allowed_fields（`session_service.py:127-132`）：

```
{title, conversation_history, session_history, chat_history,
 accumulated_articles, pinned_messages, pinned_news_cards,
 research_report, user_feedback, admin_note, visibility,
 team_comments, is_archived, live_research_state}
```

> 不在清單內的 key 會被靜默 drop。Server 端**不做 diff**：任何 PUT 都會 `updated_at = NOW()`（YAGNI；client 用 dirty flag 阻空轉）。

### 3.2 Sharing Endpoints

| Method | Path | Handler | 說明 |
|--------|------|---------|------|
| PATCH | `/api/sessions/{id}/visibility` | `set_visibility_handler` | 設 visibility=`private\|team\|org`，僅 owner 可改 |
| GET | `/api/sessions/shared` | `shared_sessions_handler` | 同 org 中 visibility ∈ {team, org} 且非自己擁有的 sessions（含 owner_name / owner_email JOIN users） |

詳細邏輯見 §11「組織空間共享」。

### 3.3 Feedback / Note / Annotation

| Method | Path | Handler | 說明 |
|--------|------|---------|------|
| PATCH | `/api/sessions/{id}/feedback` | `feedback_handler` | `feedback ∈ {thumbs_up, thumbs_down, null}` |
| PATCH | `/api/sessions/{id}/note` | `note_handler` | Admin only，admin_note 自由文字 |
| PATCH | `/api/sessions/{id}/articles/annotate` | `annotate_article_handler` | 對單篇 article（in `accumulated_articles`）加 annotation |

### 3.4 Export

| Method | Path | Handler | 說明 |
|--------|------|---------|------|
| GET | `/api/sessions/{id}/export?format=...` | `export_session_handler` | format ∈ `{json, csv, citations, ris}` |

CSV 採 formula injection 防護（`SessionService._csv_safe`，`session_service.py:378-382`），開頭字元 `=+-@\t\r` 加單引號 prefix。

### 3.5 Migration / Preferences

| Method | Path | Handler | 說明 |
|--------|------|---------|------|
| POST | `/api/sessions/migrate` | `migrate_sessions_handler` | 一次性將 localStorage sessions 批次匯入 PG（首次登入觸發） |
| GET | `/api/preferences` | `get_preferences_handler` | 取所有 `(user_id, org_id)` 偏好 |
| PUT | `/api/preferences/{key}` | `set_preference_handler` | Upsert 單一偏好 |

### 3.6 Route 註冊順序紀律

`setup_session_routes`（`sessions.py:491-514`）有**順序敏感性**：literal route 必須在 `{id}` wildcard 之前註冊，否則 `/api/sessions/shared` 會被 wildcard 捕獲：

```python
app.router.add_get('/api/sessions', list_sessions_handler)
app.router.add_post('/api/sessions', create_session_handler)
app.router.add_get('/api/sessions/shared', shared_sessions_handler)    # 必須先
app.router.add_post('/api/sessions/migrate', migrate_sessions_handler) # 必須先
app.router.add_get('/api/sessions/{id}', get_session_handler)
# ... PUT/DELETE/PATCH on {id}
```

### 3.7 PG datetime 序列化處理（D-2026-04-27）

PostgreSQL 回傳 `created_at` / `updated_at` / `deleted_at` 是 Python `datetime` 物件，`aiohttp.json_response` 無法直接序列化。`SessionService` 在 read path 統一轉 ISO string：

- `list_sessions` (`session_service.py:69-74`)：iterate rows，`r[key] = r[key].isoformat()`
- `_deserialize_session` (`session_service.py:635-637`)：對 created_at / updated_at / deleted_at 轉 ISO
- `get_shared_sessions` (`session_service.py:464-468`)：同上
- `create_session` (`session_service.py:105`)：`created_at = now.isoformat() if isinstance(now, datetime) else now`

**通則**：DB abstraction layer 應在最底層統一處理型別轉換，不要讓每個 caller 自己處理。SQLite 端因為存 REAL（float），直接是 JSON-friendly。

---

## 4. 前端 SessionManager

實作位置：`static/js/features/session-manager.js`（ES module；v4.0 Commit 10/30, 2026-05-24/25 從 `news-search.js` 遷出）。

- class 宣告：`export class SessionManager`（grep `export class SessionManager`）。
- 模組對 import **inert**（D-13 compliance）：top-level 只有 `let _sessionManager = null` 純宣告，**不**在 import time `new SessionManager(...)`。
- singleton 建構延遲到 `initSessionManager()`（grep `export function initSessionManager`），由 `main.js` bootstrap 呼叫，並把 instance bridge 到 `window.sessionManager` 供 `news-search.js` 尚未遷出的 callsite（如 `saveCurrentSession` 呼 `window.sessionManager.scheduleSave`）runtime lookup。
- `getSessionManager()`（grep `export function getSessionManager`）回傳已建構的 singleton（未建構回 `null`）。

> **Path B 邊界**（仍留在 `news-search.js`，因需重新賦值 outer-scope `let currentLoadedSessionId` / `savedSessions`，ES module 不能跨檔做）：
> - module-level `saveCurrentSession`（呼叫 `sessionManager.scheduleSave`）
> - `loadSavedSession`
> - D-7 layer #4：`saveCurrentSession` 內 `_isShared` 早退（§11.5 Guard 2）
>
> 這些是 SessionManager 的 **caller**，不是 class method 本身。`_serverId` 三層 hydration（§6）與 dirty flag（§7）的 caller 邏輯也在 `news-search.js`。

### 4.1 Constructor State

`constructor(authMgr)`（`session-manager.js`，grep `constructor(authMgr)`）：

```js
class SessionManager {
    constructor(authMgr) {
        this._auth = authMgr;
        // v4.0 Commit 30 (2026-05-25)：per-session pending-save Map，
        // 取代舊的全局 _saveTimer / _savePending 單對（見 §4.3）。
        // Shape: sid → { session, timer, scheduledAt }
        this._pendingSaves = new Map();
        this._postedRecently = new Map();  // session.id → lastPostTime（5s 重複 POST 偵測）
    }
}
```

> ⚠️ **遷移注意**：舊 spec（及 v4.0 Commit 30 前的 code）constructor 持有單一全局 `this._saveTimer` / `this._savePending`。已被 `this._pendingSaves` Map 取代，下游 method（§4.3）全部改成 per-session 操作。

### 4.2 Public Methods

行號為 `static/js/features/session-manager.js` 的大致位置（refactor 後可能微調，**以 grep marker 為準**）。

| Method | grep marker（~行號） | 用途 |
|--------|---------------------|------|
| `_isOnline()` | `_isOnline()`（~69） | `authManager.isLoggedIn() && currentUser.org_id` 雙重判斷（B2B 必有 org_id） |
| `loadSessions()` | `async loadSessions()`（~75） | logged-in → API；匿名 → localStorage（**logged-in 失敗不 fallback**） |
| `loadSharedSessions()` | `async loadSharedSessions()`（~103） | `GET /api/sessions/shared`，僅 logged-in，失敗回 `[]` |
| `setSessionVisibility(serverId, visibility)` | `async setSessionVisibility`（~117） | `PATCH /api/sessions/{id}/visibility`，需 serverId（非匿名） |
| `saveSession(session)` | `async saveSession(session)`（~130） | 有 `_serverId` → PUT；無 → POST（含 5s 重複 POST detector） |
| `deleteSession(sessionId, serverId)` | `async deleteSession`（~202） | `DELETE /api/sessions/{serverId}`（API），fallback localStorage |
| `renameSession(sessionId, serverId, newTitle)` | `async renameSession`（~216） | PUT `title`；non-OK（含 401）throw（P1 E2E fix 2026-05-26，不 silent swallow） |
| `loadFolders()` / `saveFoldersSync()` | `async loadFolders()` / `saveFoldersSync`（~244 / ~254） | 純 localStorage（folders 仍未走 server） |
| `migrateFromLocal()` | `async migrateFromLocal()`（~260） | 首次登入時把 localStorage 批次 POST 到 `/api/sessions/migrate`，成功後設 `taiwanNewsSessionsMigrated` flag |
| `scheduleSave(session, options)` | `scheduleSave(session, options`（~309） | per-session debounce（2s）寫進 `_pendingSaves` Map（§4.3）；`options.immediate` 同步 flush 不等 2s（DR final_result 用） |
| `flushPendingSave(session)` | `flushPendingSave(session)`（~346） | 有 arg flush 該 session、無 arg flush 全部（`beforeunload`）；回傳 Promise 供 critical path await |
| `_cancelPendingSave(session)` | `_cancelPendingSave(session)`（~378） | **Cancel 不 fire**：有 arg 取消單 session、無 arg 取消全部（logout / auth failure，避免 401 二次 wipe） |
| `loadPreferences()` / `setPreference()` | `async loadPreferences()` / `async setPreference`（~393 / ~406） | 偏好 KV，logged-in 走 API |
| `_saveToLocalStorage()` | `_saveToLocalStorage()`（~422） | 透過 `getSavedSessions()`（owner module `sessions-list.js` getter）寫 localStorage；取代舊 `window.savedSessions` bridge（v4.0 Commit 10） |

### 4.3 Per-Session Debounce 紀律（D-2026-05-01；v4.0 Commit 30 重設計）

**v4.0 Commit 30（2026-05-25, regression fix — clean redesign）**：debounce 從**單一全局** `_saveTimer` / `_savePending` 改為 **per-session pending Map** `_pendingSaves`（`sid → { session, timer, scheduledAt }`，grep `this._pendingSaves`，constructor ~62）。每個 entry 的 `setTimeout` callback 在 fire 時自行 `delete` 自己。`scheduleSave` / `flushPendingSave` / `_cancelPendingSave` 全部支援 single-arg（單 session）或 no-arg（全部）。

**為何改（root cause）**：舊的單一全局 timer 設計下，rapid session switch 會用「新 session 的 scheduleSave」**誤 cancel 上一個 session 還沒 fire 的 pending PUT**。實測 race：CEO 在 DR `final_result` 後 2s 內切走 → 上一 session 的 debounced save 被取消 → **DR 資料從未持久化到 PG**。per-session Map 後，切 session 只動到該 session 自己的 timer，互不干擾。

```js
scheduleSave(session, options = {}) {
    if (!session || session.id == null) { /* warn + return */ }
    const sid = session.id;
    // 只 cancel「同一 session」的 pending（不再跨 session 誤殺）
    const existing = this._pendingSaves.get(sid);
    if (existing) clearTimeout(existing.timer);
    if (options.immediate) {            // DR final_result / 顯式 save：不等 2s
        this._pendingSaves.delete(sid);
        return this.saveSession(session).catch(/* log */);
    }
    const timer = setTimeout(() => {     // debounced 2s
        this._pendingSaves.delete(sid);
        this.saveSession(session).catch(/* log */);
    }, 2000);
    this._pendingSaves.set(sid, { session, timer, scheduledAt: Date.now() });
}

_cancelPendingSave(session) {  // RCA Fix 2: hidden-path
    if (session && session.id != null) {            // 單 session
        const p = this._pendingSaves.get(session.id);
        if (p) { clearTimeout(p.timer); this._pendingSaves.delete(session.id); }
        return;
    }
    for (const [, p] of this._pendingSaves) clearTimeout(p.timer);  // 全部
    this._pendingSaves.clear();
}
```

**陷阱（已修）**：`setTimeout` closure 會抓 declaration-time 的 `session` 物件。如果 user 在 2s 內 logout：
- `_handleAuthFailure` 清 `_user` / token / savedSessions / localStorage
- **timer 沒被 cancel**
- 2s 後 timer fire → `saveSession(staleSession)` → `_isOnline()` 因 `_user = null` 為 false → 走 localStorage fallback（其實 OK）
- 但若 `_user` 已切到下一 user / token expired，會 fetch with 401 → `authenticatedFetch` retry refresh → refresh 也 fail → recursive 進入 `_handleAuthFailure` → **二次 wipe**（console.error + show login modal 兩次）

**修法**：`_handleAuthFailure` 入口（`news-search.js`）第一行呼叫 `sessionManager._cancelPendingSave()`（no-arg = 取消全部；typeof guard 防 module init order）：

```js
_handleAuthFailure() {
    if (typeof sessionManager !== 'undefined' && sessionManager) {
        sessionManager._cancelPendingSave();  // RCA Fix 2 hidden-path（無 arg 取消全部 pending）
    }
    // ... rest of cleanup
}
```

**通則**：**任何 user-scoped state 清理 = 先 cancel 所有 pending timer/promise**。同類陷阱：debounced fetch、queued LLM call、AbortController without abort()、setInterval without clearInterval。

### 4.3a Session-Switch Token Race — Stale Restore 作廢（commit `0218fbda`, 2026-06-17）

per-session debounce 解決「pending **save**」的 cross-session 干擾；另一條獨立 race 在「pending **restore**」：切走的舊 session 可能排了 `setTimeout` 要 restore 自己的畫面狀態，切到新 session 後若那個 stale restore 才 fire，會用舊 session 的狀態覆蓋當前畫面。

**通則修法（session 層）**：`loadSavedSession`（caller，在 `news-search.js`）進入後、**第一個 `await`（hydrate fetch）之前的同步區**就無條件 bump 一個 switch-generation token，作廢「上一個」session 排的 stale `setTimeout` restore。語意對齊既有的 `bumpSearchGenerationId`（同為切換時作廢上一 session 的 pending callback），差別在**必須更前置**——原本的 bump 在 await 之後，對 server-backed session 而言 hydrate await 期間 stale restore 仍會搶先觸發。

> **scope 註記**：本次 commit 的 token 是 LR-專屬的 `bumpLRSwitchToken`，其 restore 排程 / guard 放行語意屬 **LR spec**（live-research）。本 spec 只記通用 session 層原則：**session switch 必須在進入後的 pre-await 同步區作廢上一 session 的 pending restore callback**。任何未來新增的「per-session 排程 restore」都應遵此守則。

### 4.4 `beforeunload` Flush

```js
window.addEventListener('beforeunload', () => {
    if (currentLoadedSessionId !== null) {
        const currentSession = savedSessions.find(s => matchSessionId(s.id, currentLoadedSessionId));
        if (currentSession) sessionManager.flushPendingSave(currentSession);
    }
});
```

`flushPendingSave(currentSession)`（single-arg）對該 session 的 pending entry 立刻 fire（`clearTimeout` + `saveSession`）。差異：`_cancelPendingSave` 取消不執行、`flushPendingSave` 立即執行。no-arg 版（`flushPendingSave()`）flush `_pendingSaves` 內**全部** pending，並回傳 `Promise.all` 供 critical path await。

---

## 5. 持久化路徑

### 5.1 寫入：`saveCurrentSession` → `scheduleSave` → PUT/POST

實作位置：`news-search.js:1690-1809`。

```
[使用者操作 → 設 _sessionDirty = true]
        │
        ▼
[outer guard: sessionHistory.length > 0 etc]
        │
        ▼
saveCurrentSession()  ─ early return if !_sessionDirty
        │
        ├─ Y-1 guard: _isShared session → 跳過 (commit 138ae61)
        ├─ Y-2 guard: currentLoadedSessionId 不在 savedSessions → 跳過
        │
        ▼
[update branch / new branch]
        │ savedSessions[idx] = { id, _serverId, ...全欄位 }  (e43468d 保留 _serverId)
        │ updatedAt = Date.now()
        ▼
localStorage.setItem('taiwanNewsSavedSessions', ...)
        │
        ▼
sessionManager.scheduleSave(persistedSession)  ─ 2s debounce
        │
        ▼ (2 秒後)
sessionManager.saveSession(session)
        │
        ├─ has _serverId → PUT /api/sessions/{_serverId}
        └─ no _serverId  → POST /api/sessions
                           （5s 內重複 POST 同 id → suppress + console.error）
        │
        ▼
_sessionDirty = false  ─ save body 結束後 reset
```

### 5.2 讀取：`loadSessions` 分流

匿名 vs logged-in 走不同 path（D-2026-05-01 收緊）：

```js
async loadSessions() {
    if (this._isOnline()) {
        try {
            const res = await this._auth.authenticatedFetch('/api/sessions');
            const data = await res.json();
            if (res.ok && data.success) return data.sessions;
            console.error('[SessionManager] /api/sessions non-OK:', res.status, data);
        } catch (e) {
            console.error('[SessionManager] /api/sessions error:', e);
        }
        // Logged-in path: NO localStorage fallback. Return [] so sidebar
        // shows empty, surfacing the server failure visibly.
        return [];
    }
    // Not logged in: localStorage is the primary source of truth.
    try {
        const stored = localStorage.getItem('taiwanNewsSavedSessions');
        return stored ? JSON.parse(stored) : [];
    } catch (e) {
        console.error('[SessionManager] Failed to load from localStorage:', e);
        return [];
    }
}
```

**為何 logged-in 不 fallback localStorage**：localStorage 是 origin-scoped 不是 user-scoped。Logged-in 時 server 失敗 → fallback localStorage 可能載入**上一個 user 殘留**的 sessions → cross-user leak silent carrier。寧可空畫面 + 明顯 console.error，也不要 silent 載入錯資料（lessons-frontend L201）。

### 5.3 Page-Load Sync — Trigger F（D-2026-05-13 後）

DOMContentLoaded 是 trigger F cold-start 入口；切回 tab（`visibilitychange`）是 trigger F warm-start 入口。兩者共用同一個 `UserStateSync.runInitSync` 路徑。

```
DOMContentLoaded / visibilitychange (visible)
  │
  ▼
await checkAuthOnLoad()    ─ /api/auth/me 確認 cookie 內 JWT 仍有效
  │                           401 → 走 _handleAuthFailure（trigger D）
  ▼
UserStateSync.runInitSync({ keepInviteToken })
  │
  ├─ assertUserIdentity(cached=authManager._user, fresh=payload.user)
  │    └─ MISMATCH → 拋 UserStateSyncError → trigger A 整套 reset
  │
  ├─ clearUserScopedState()     ─ 清 6 個 globals + USER_SCOPED_KEYS
  │
  ├─ fetchInit()                ─ GET /api/user/init（一次拿全部）
  │
  └─ applyInit(payload)
        ├─ authManager._user = payload.user  ─ 含 org_id + role（commit 228a93a）
        ├─ savedSessions = payload.sessions.map(s => ({
        │     ...s,
        │     _serverId: s._serverId || (UUID-shape ? s.id : null)  ─ 三層 hydration Layer 3（§6）
        │   }))
        ├─ sharedSessions = payload.shared_sessions
        ├─ preferences hydrate
        ├─ renderLeftSidebarSessions()
        └─ update 「組織空間 (N)」 badge
```

**取代過去**：原本 DOMContentLoaded 串接 `loadSessions()` + `loadSharedSessions()` + `loadPreferences()` 三次 round-trip + 多處 ad-hoc render call；D-2026-05-13 後合併為 `runInitSync` 一次 round-trip + 統一 applyInit hydrate。

**in-flight de-dupe**：`runInitSync` 內部會去重複的 concurrent call（同一個 Promise 共用），避免快速連點時 race condition。

`_authReadyPromise` 機制：用 Promise gate 確保多個 `DOMContentLoaded` listener 之間「需 auth 的初始化」必等 `checkAuthOnLoad` 完成。詳見 lessons-frontend「DOMContentLoaded 多 handler race condition」。

### 5.4 Login Hook — Trigger A（D-2026-05-13 後）

Login / Onboarding 完成是 trigger A 入口。`AuthManager.login()` 在 auth success 後直接呼叫 `UserStateSync.runInitSync()`（**契約強制**：見 `code/python/tests/test_user_state_sync_invariant.py` contract test 驗證 `login()` body 必呼叫 `runInitSync`）。

```
authManager.login(email, password)
    │ /api/auth/login → JWT + refresh cookie
    ▼
UserStateSync.runInitSync()
    ├─ clearUserScopedState()  ─ 即使是同 origin 重登也清光（B2B 共用電腦安全）
    ├─ fetchInit()             ─ GET /api/user/init
    └─ applyInit(payload)
        ├─ authManager._user = payload.user  ─ 含 org_id + role
        ├─ savedSessions = payload.sessions
        ├─ sharedSessions = payload.shared_sessions
        ├─ preferences hydrate
        └─ renderLeftSidebarSessions()
    │
    ▼
hideAuthModal() + showMainUI() + updateAuthUI()
    │
    ▼ (首次登入特殊處理)
sessionManager.migrateFromLocal()    ─ localStorage → POST /api/sessions/migrate
    │ 成功後設 'taiwanNewsSessionsMigrated' flag + remove localStorage
    │ migrate 完再次走 UserStateSync.runInitSync() pick up migrated sessions
```

**Onboarding 變形**（`register_user()` / `activate_user()`）：backend 在 activate 後 auto-issue refresh cookie（commit `2ee5508`），前端跳 `/` 後由 trigger F（DOMContentLoaded）的 `checkAuthOnLoad` 拿 JWT → runInitSync。所以 onboarding 不需要顯式呼叫 `runInitSync`，由 trigger F 接手即可。

---

## 6. _serverId 三層 Hydration

### 6.1 為什麼是「三層」（背景）

`SessionManager.scheduleSave()` 從 RG fork merge（commit `9362312`）以來**只有定義沒有 caller** — 一般搜尋 sessions 從未真的寫 PG。VPS 看似正常是 localStorage 假象，PG 內 7 筆 sessions 都來自「分享」(`setSessionVisibility`) 或 LR orchestrator 的 server-initiated path。15/15 active user 中 13/15 是 0 sessions（lessons-frontend「scheduleSave 從合併進來就沒 caller」）。

Commit `3b9f7d8` 在 `saveCurrentSession` 結尾 wire `scheduleSave`，但**必須先補三層 _serverId 漏洞**否則會 spawn 新 row：

| 漏洞層 | Bug | 後果 |
|-------|-----|------|
| 1. `saveCurrentSession` overwrite | object literal `savedSessions[idx] = { id, title, ... }` 沒含 `_serverId` | 每次覆寫抹掉 server ID → 下次 saveSession POST 而非 PUT → spawn new row |
| 2. `loadSavedSession` hydrate path | `hydrated = {...session, ...}`，session 本身沒 `_serverId` | 點 sidebar session 後第一次 mutate → spawn |
| 3. `loadSessions` callback | `savedSessions = sessions`，server `list_sessions` 回 `id`（PG UUID）但無 `_serverId` 欄位 | 頁面載入後第一次 mutate → spawn |

### 6.2 三層補法 + 一個 Detector

| Layer | Commit | 位置 | 修法 |
|-------|--------|------|------|
| 1. saveCurrentSession update branch | `e43468d` | `news-search.js:1744` | `_serverId: savedSessions[existingSessionIndex]._serverId` 顯式保留 |
| 2. loadSavedSession hydrate | `3b9f7d8` | `news-search.js:7928` | `_serverId: session._serverId \|\| serverId`（serverId 來自 UUID-shape detection） |
| 3. loadSessions page-load | `3b9f7d8` | `news-search.js:986-990` | `savedSessions = sessions.map(s => ({ ...s, _serverId: s._serverId \|\| (UUID-shape ? s.id : null) }))` |
| 4. **Detector**（最後防線） | `3b9f7d8` | `news-search.js:343-356` | `_postedRecently` Map：5 秒內同一 `session.id` 第二次 POST → suppress + console.error |

UUID-shape detection：`typeof s.id === 'string' && s.id.includes('-')`。Server-resident sessions 的 `id` 是 PG UUID（含 hyphens），新建 localStorage session 的 `id` 是 `Date.now()` 整數。

### 6.3 Detector（`SessionManager._postedRecently`）

```js
// news-search.js:343-357
const lastPost = this._postedRecently.get(session.id) || 0;
if (Date.now() - lastPost < 5000) {
    console.error(
        '[SessionManager] DEFENSIVE: POST suppressed (duplicate within 5s) for session.id=',
        session.id,
        '— possible _serverId-loss regression. Check saveCurrentSession overwrite (~1626), hydrate (~7745), loadSessions (~911).'
    );
    return;
}
this._postedRecently.set(session.id, Date.now());
```

Regression 偵測線。任何時候 console 看到這條 error 都代表三層中至少一層漏了 `_serverId`，需要回查指引的三個行號。

### 6.4 `id` vs `_serverId` 雙鍵語意

- `session.id`：in-memory 識別。匿名/離線下是 `Date.now()` 整數；server-resident 是 PG UUID string。
- `session._serverId`：對應 PG row 的 UUID。**有值 → PUT；無值 → POST 且 detector 啟動**。

`matchSessionId(a, b)`（`news-search.js:1263`）統一處理 string/integer 比較：`String(a) === String(b)`。**避免 `parseInt(UUID) → NaN`** 的靜默匹配失敗（lessons-frontend「parseInt(UUID) 靜默破壞 session ID 比較」）。

---

## 7. Dirty Flag 紀律

### 7.1 `_sessionDirty` Boolean 早退守則

宣告位置：`news-search.js:1249`（module-level `let`）。

```js
let _sessionDirty = false;
```

`saveCurrentSession()` 入口（`news-search.js:1697-1699`）：

```js
function saveCurrentSession() {
    // RCA Fix 1: pure-browse early return.
    if (!_sessionDirty) {
        return;
    }
    // ... rest of save body
}
```

### 7.2 為何需要（D-2026-05-01）

「三角共謀」（hidden-path RCA Symptom 1）：

1. 4 個 callsite（line 1528 / 1632 / 9691 / 10223）在 user click sidebar / 開新對話 / 切 folder 時呼叫 `saveCurrentSession()` 作為 **pre-navigation save**。outer guard 只檢查 `sessionHistory.length > 0` 等「有沒有 in-memory 內容」，**純瀏覽 loaded session 時 guard 永遠 true**。
2. `saveCurrentSession` update branch 永遠 `updatedAt = Date.now()` + `scheduleSave` → 2s 後 PUT。
3. Server `update_session` 無條件 `set updated_at = NOW()`（沒 diff 邏輯）。
4. Sidebar sort by `updated_at DESC`（commit 138ae61）→ 純瀏覽就跳 top。

時間訊號吻合：`3b9f7d8`（wire scheduleSave）+ `138ae61`（sort by updated_at）合併效應 = 「最近一週才開始」。

**修法**：dirty flag 區分「loaded session 有內容」vs「user 真的產生新內容」。outer 4 個 callsite 的 if-condition **不動**（保留作 outer gate）。

### 7.3 Mutate 點清單（必設 `_sessionDirty = true`）

| 行號 | 場景 | mutate 對象 |
|-----|------|-----------|
| 3321 | Search query submit | `conversationHistory.push(query)` |
| 3509 | DR query submit | 同上 |
| 4259 | LR query submit | 同上 |
| 4391 | Research report 完成 | `currentResearchReport = {...}` |
| 6992 | Chat message push（user 或 assistant） | `chatHistory.push(...)` |
| 7024 | Pin/unpin message | `pinnedMessages.push/splice(...)` |
| 7231 | Pin/unpin news card | `pinnedNewsCards.push/splice(...)` |
| 9885 | Rename session（commitRename） | dirty flag 確保後續 saveCurrentSession 不 early-return |

### 7.4 Reset 點

| 場景 | 說明 |
|------|------|
| `saveCurrentSession` body 結束 | save 完即 reset |
| `UserStateSync.clearUserScopedState()` | auth boundary cleanup（D-2026-05-13 之後由 UserStateSync 統一執行，取代過去的 helper；見 §8.5） |
| `loadSavedSession` 載入完 | 讀取是 read-only |

### 7.5 通則（D-2026-05-01）

> 「Pre-navigation save」pattern **必須**配 dirty flag，否則 outer guard「有 in-memory 內容」永遠 true。`updated_at` 必須對應「使用者真的有新內容」而不是「有東西在記憶體裡」。Server 端不做 diff 是合理的（YAGNI），但 client 必須阻止無內容變化的 PUT。

---

## 8. Cross-User 隔離

### 8.1 背景：localStorage 是 Origin-Scoped 不是 User-Scoped

D-2026-03-13「localStorage 為主」是匿名 / 離線情境的紀律，但延伸到 B2B 共用電腦切帳號就有 cross-user leak（admin 登出後同瀏覽器 member 登入仍看到 admin sessions）。VPS 沒人 complain 是因為大家用各自瀏覽器，但 B2B 共用電腦會撞到（lessons-auth「Logout 也清 user-scoped localStorage」）。

### 8.2 `USER_SCOPED_KEYS` 清單

定義：`AuthManager.USER_SCOPED_KEYS`（`news-search.js:7-14`）：

```js
static USER_SCOPED_KEYS = [
    'taiwanNewsSavedSessions',    // 主要 session 快取
    'taiwanNewsFolders',          // Folder hierarchy
    'taiwanNewsSessionsMigrated', // 首次登入 migrate 完成 flag
    'nlweb_source_folders',       // Source 篩選分組
    'nlweb_file_folders',         // 檔案分組
    'nlweb_selected_files',       // 已選 files
];
// Device-scoped UI prefs (nlweb-large-font, nlweb-kg-hidden) 故意排除
```

### 8.3 `_clearUserScopedStorageIfUserChanged(newUserId)`

實作：`news-search.js:46-67`。

呼叫路徑：
1. **Login**（`AuthManager.login`，line 93-106）：登入成功後、persist new authUser 之前。
2. **checkAuthOnLoad**（line 935-952）：`/api/auth/me` 200 後比對 `data.user.id` vs `localStorage.authUser.id`。

邏輯：
```js
_clearUserScopedStorageIfUserChanged(newUserId) {
    if (!newUserId) return false;
    const prevUserId = JSON.parse(localStorage.getItem('authUser') || 'null')?.id || null;
    if (prevUserId && String(prevUserId) === String(newUserId)) {
        return false;  // 同 user 重登保 cache（D-2026-03-13 仍有效）
    }
    console.warn('[AuthManager] User identity changed (', prevUserId, '→', newUserId, '), clearing user-scoped localStorage');
    for (const key of AuthManager.USER_SCOPED_KEYS) localStorage.removeItem(key);
    return true;
}
```

回傳 `true` 表示有清，呼叫端負責 paired call：
- `savedSessions.length = 0`（reset in-memory array）
- `renderLeftSidebarSessions()`（清 DOM）
- `UserStateSync.clearUserScopedState()`（清主 UI globals + DOM；§8.5 — 取代過去的 helper）

> **D-2026-05-13 之後**：`_clearUserScopedStorageIfUserChanged` 的呼叫路徑本身被 trigger A / B 收編到 `UserStateSync.runInitSync` 內部 — 上述 paired calls 由 `clearUserScopedState` + `applyInit` 統一執行，外部 caller 不應再單獨呼叫此 helper。函式本體保留作為 backward-compat reference / defense-in-depth。

### 8.4 `_handleAuthFailure` 也清（CEO 拍板）

實作：`news-search.js:200-241`。

呼叫路徑：
- `AuthManager.logout()`：使用者主動登出
- `AuthManager.refreshToken()` catch：refresh failed
- `checkAuthOnLoad()` 401 path：commit `138ae61` 改寫
- `btnLogoutAll` click：登出全部裝置

執行步驟（順序敏感）：
```js
_handleAuthFailure() {
    // 1. RCA Fix 2 hidden-path: cancel pending save BEFORE state mutation
    if (typeof sessionManager !== 'undefined' && sessionManager) {
        sessionManager._cancelPendingSave();
    }
    // 2. Clear auth state
    this._accessToken = null;
    this._user = null;
    localStorage.removeItem('authUser');
    localStorage.removeItem('authAccessToken');
    // 3. CEO 拍板：logout 也清 USER_SCOPED_KEYS
    for (const key of AuthManager.USER_SCOPED_KEYS) localStorage.removeItem(key);
    // 4. In-memory + DOM
    if (typeof savedSessions !== 'undefined' && Array.isArray(savedSessions)) {
        savedSessions.length = 0;
    }
    if (typeof renderLeftSidebarSessions === 'function') renderLeftSidebarSessions();
    updateAuthUI();
    // 5. Clear user-scoped main-UI globals + DOM via UserStateSync
    //    (D-2026-05-13 — superseded the removed legacy helper; see §8.5)
    if (typeof UserStateSync !== 'undefined') UserStateSync.clearUserScopedState();
    // 6. Hide UI + show login modal
    if (typeof hideMainUI === 'function') hideMainUI();
    if (typeof showAuthModal === 'function') showAuthModal('login');
}
```

**設計權衡**：CEO 拍板 logout 也清（B2B 共用電腦安全 > 同 user 重登保 cache）。匿名 / 離線狀態 localStorage 持久化是另一情境，不衝突。D-2026-05-13 之後 `_handleAuthFailure` 是 trigger C / D 的內部步驟，由 `UserStateSync` 統一驅動。

### 8.5 主 UI 6 個 Globals 的清理 — 已 superseded by D-2026-05-13 Frontend Init Sync Refactor

> **狀態**：D-2026-05-13 之前用來補清「6 個 resetConversation 沒涵蓋的 user-scoped main-UI globals」的 legacy helper 已**完全從 codebase 移除**（Task 13 cleanup，見 `static/news-search.js` 內 `// Task 13 cleanup` 註解 — 該段註解明確指出超集 API 為 `UserStateSync.clearUserScopedState` + `UserStateSync.resetMainUI`）。本節保留高層歷史脈絡，**不保留舊實作的 code block**。

**歷史問題**（lessons-frontend「resetConversation 不等於 reset all user-scoped state」）：切帳號時 sidebar 清乾淨但**主畫面殘留 user A 的對話 / 結果 / report**。`resetConversation()` 由 `btnNewConversation` 設計，**6 個 user-scoped globals 沒清**：`_sessionDirty` / `currentArgumentGraph` / `currentChainAnalysis` / `shareContentOverride` / `currentLRSessionId` / `currentAnalyticsQueryId`。

**D-2026-05-13 取代**：上述 6 個 globals 的清理由 `UserStateSync.clearUserScopedState()` 統一執行，外加 `UserStateSync.resetMainUI()` safely wraps `resetConversation`。權威 API：

- `UserStateSync.clearUserScopedState()` — 覆蓋 6 個 user-scoped main-UI globals + USER_SCOPED_KEYS（device-scoped UI prefs 不動）
- `UserStateSync.resetMainUI()` — try/catch wrap `resetConversation`，安全處理 resetConversation 拋錯情境
- `UserStateSync.fullReset()` — convenience：`clearUserScopedState` + `resetMainUI` 一次呼叫
- `UserStateSync.runInitSync({ keepInviteToken })` — convenience：`fullReset` + `fetchInit` + `applyInit` 一次呼叫（in-flight de-dupe guard）

**新呼叫點**（取代過去 legacy helper 的兩個呼叫點）：
1. Trigger C（`AuthManager.logout`）/ Trigger D（401 / refresh fail）→ `_handleAuthFailure` 內呼叫 `UserStateSync.clearUserScopedState()`（§8.4 範例已更新）
2. Trigger B（`checkAuthOnLoad` identity-change）→ `assertUserIdentity` 拋 `UserStateSyncError(MISMATCH)` → caller catch 後呼叫 `UserStateSync.runInitSync()` 走 trigger A 整套 reset

**通則保留**（D-2026-05-13 仍適用）：(1) 重用既有 reset/cleanup helper 前必 sweep 同類 globals 檢查 helper coverage（**helper 名稱不一定反映完整 scope**）。(2) 切帳號 / auth failure 是 user-scoped state 的 hard boundary，所有 user-scoped globals 都要清。(3) Sweep 方法：grep 所有非 const、非 DOM ref、非 helper class 的 module-level `let` 宣告，逐一判斷 + 加入 `UserStateSync.clearUserScopedState` 內。

**詳細實作**：見 `static/news-search.js` 內 `UserStateSync` 段、`docs/in progress/plans/frontend-init-sync-refactor-plan.md`、`docs/decisions.md` D-2026-05-13。

### 8.6 `checkAuthOnLoad` 401 走完整 `_handleAuthFailure`（commit 138ae61）

問題：原本 `checkAuthOnLoad` 收 401 後只 `hideMainUI + showAuthModal`，沒清 `_user` / localStorage / render sidebar。結果 `isLoggedIn()` 仍可能 true（_user 殘留）→ 後續 silent fallback 載入舊 user 資料 → cross-user leak。

修法（`news-search.js:918-927`）：
```js
if (res.status === 401) {
    // Y-2/Y-3 fix: 401 path must fully clear stale auth state.
    // Previously only hideMainUI + showAuthModal — but authManager._user
    // remained populated from localStorage cache, so isLoggedIn() returned
    // true. The subsequent loadSessions then silently fell back to
    // localStorage and loaded the *previous* user's sessions (cross-user
    // leak via in-memory savedSessions). _handleAuthFailure clears _user,
    // localStorage, savedSessions, re-renders, hides UI, and shows modal.
    authManager._handleAuthFailure();
    return;
}
```

**通則**：auth failure 是 user-scoped state 的 hard boundary。所有 401 / token expired / refresh fail 路徑都該走同一個 cleanup func，避免「半 logged-out 狀態」（_user 還在但 token 沒了）造成 cross-user leak。

---

## 9. SSE Handler Black-List

### 9.1 為何是 Black-List 而非 White-List（D-2026-05-01）

修 SSE handler 過濾中間訊息時，subagent 把「ignore-list」誤解為 white-list（commit `d910819`），把 `default: Object.assign(accumulatedData, data)` 改成 warn-and-ignore，結果 server 的 `nlws`（最終答案 + items）也被 ignore，UI 顯示「抱歉，我無法回答這個問題」+ session 也是空的（hotfix `1782e76` 改回）。

**通則**：SSE message_type 是**開放集**（server 隨時可加新類型），unknown 預設 merge 比 ignore 安全。要過濾的中間訊息**列名單**（black-list），不要 white-list 鎖死 known set。

### 9.2 前端 Black-List（兩個 Switch）

兩個 SSE handler，**case 清單必須一致**：

#### 9.2.1 GET / EventSource Path（`news-search.js:2199-2210`）

```js
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
    // Unknown / final-result message_type (e.g. nlws,
    // decontextualized_query). Merge so the resolved
    // accumulatedData carries the answer / items / etc.
    console.warn('[SSE] default merge for message_type:', data.message_type, data);
    Object.assign(accumulatedData, data);
    break;
```

#### 9.2.2 POST SSE Path（`news-search.js:2378-2398`）

同上 case 清單。

> Cases already handled explicitly above（`begin-nlweb-response`、`remember`、`time_filter_relaxed`、`author_search_no_results`、`clarification_required`、`intermediate_result`、`complete`、`articles`、`summary`、`answer`）刻意 **NOT 重複**列在 black-list — 它們有自己的 break。

### 9.3 後端 `_BAD_MESSAGE_TYPES`（必須對齊）

實作：`SessionService._BAD_MESSAGE_TYPES`（`session_service.py:561-568`）：

```python
_BAD_MESSAGE_TYPES = frozenset({
    'asking_sites', 'tool_selection', 'decontextualization',
    'pre_check_results', 'site_querying', 'tool_routing',
    'research_phase', 'intermediate_result', 'progress',
    'begin-nlweb-response', 'end-nlweb-response', 'complete',
    'error', 'remember', 'time_filter_relaxed',
    'author_search_no_results', 'clarification_required',
})
```

**前後端對齊紀律**：新增中間 message_type 時，必須**同時**：
1. 前端兩個 SSE switch 加獨立 `case 'X': break;`
2. Server `_BAD_MESSAGE_TYPES` frozenset 加 `'X'`
3. 互相同步避免漏邊。

> Server-side 的清單比前端多一些（`begin-nlweb-response`、`remember`、`time_filter_relaxed`、`author_search_no_results`、`clarification_required`、`intermediate_result`、`complete`），這些是 server defense-in-depth：即使 client 在 default branch 將它們 merge 進 accumulatedData 並 push 到 sessionHistory，server `_sanitize_session_history` 仍會 filter 掉。

---

## 10. Sanitize session_history Defense-in-Depth

### 10.1 問題：SSE 中間訊息汙染 PG

歷史 bug（lessons-frontend「Server 把 SSE 中間訊息（asking_sites）寫進 PG sessionHistory」）：

前端 SSE handler 的 default branch（`Object.assign(accumulatedData, data)`）把 `message_type: asking_sites`、`content: "Asking "`（**string**）也 merge 進 `accumulatedData.content`，覆寫掉原本的 articles array。後續 `combinedData` push 到 sessionHistory → save 到 PG → 重新載入時 `populateResultsFromAPI(lastSession.data)` 把 string 當 array 用 → `articles.sort is not a function` crash → 點該 session 直接白屏。

### 10.2 三層治本（commits `d910819` + `7119da0` + `1ca2cb1`）

| Layer | 位置 | 內容 |
|-------|------|------|
| 1. 前端 black-list | `news-search.js`（兩個 SSE switch） | 獨立 case 跳過中間訊息（§9） |
| 2. Server filter（**Layer 2 defense**） | `SessionService._sanitize_session_history` (`session_service.py:570-616`) | create / update / migrate 時 filter |
| 3. 一次性 cleanup | `code/python/scripts/cleanup_polluted_session_history.py` | 清歷史 PG 污染 |

### 10.3 `_sanitize_session_history` Classmethod

```python
@classmethod
def _sanitize_session_history(cls, history: Any) -> List[Dict]:
    """Drop sessionHistory entries that are SSE intermediate envelopes
    rather than final result snapshots.

    An entry is considered polluted if any of:
      - data.message_type is in _BAD_MESSAGE_TYPES
      - data.content is a string (must be an articles array)

    Returns sanitized list. Logs a warning for each dropped entry.
    Idempotent: clean input passes through unchanged.
    """
    if not isinstance(history, list):
        return []
    clean: List[Dict] = []
    dropped = 0
    for entry in history:
        if not isinstance(entry, dict):
            dropped += 1
            continue
        data = entry.get('data')
        if isinstance(data, dict):
            mt = data.get('message_type')
            if mt in cls._BAD_MESSAGE_TYPES:
                logger.warning(...)
                dropped += 1
                continue
            if isinstance(data.get('content'), str):
                # 必須是 articles array，string 是 envelope leak
                logger.warning(...)
                dropped += 1
                continue
        clean.append(entry)
    if dropped:
        logger.warning(f"_sanitize_session_history: dropped {dropped} polluted entries, kept {len(clean)}")
    return clean
```

### 10.4 呼叫點（write path 全覆蓋）

| Method | 位置 | 呼叫時機 |
|--------|------|---------|
| `create_session` | `session_service.py:96` | INSERT 時對 `session_history` filter |
| `update_session` | `session_service.py:140-143` | PUT 時若 key=`session_history` 且 list → filter |
| `migrate_sessions` | `session_service.py:309` | 一次性 migrate 時 filter |

**Defense-in-depth 原則**：跨層 boundary 的訊息（server SSE → client → server PG）每層都該驗證 / 過濾，不要假設上游送的是乾淨的。即使 client black-list 漏一個 case，server filter 仍 catch。

---

## 11. 組織空間共享

### 11.1 Visibility Model

`search_sessions.visibility` 三值：

| Value | 說明 |
|-------|------|
| `private` | 預設，僅 owner 可見 |
| `team` | （目前等同於 org，未來預留） |
| `org` | 同 org 任一成員可見 |

`SessionService.VALID_VISIBILITY = {'private', 'team', 'org'}`（`session_service.py:419`）。

### 11.2 Sharing API

| Endpoint | 行為 |
|---------|------|
| `PATCH /api/sessions/{id}/visibility` | Owner 改 visibility；非 owner 走 SQL `WHERE user_id = ?` 自動拒絕 |
| `GET /api/sessions/shared` | 同 org 中 visibility ∈ {team, org} 且非自己擁有的 sessions |
| `GET /api/sessions/{id}` | 走 `get_session_shared`，owner 或 visibility ∈ {team, org} 都可讀 |

### 11.3 `get_session_shared` 權限模型

```python
# session_service.py:471-491
async def get_session_shared(self, session_id, user_id, org_id):
    row = await self.db.fetchone(
        "SELECT * FROM search_sessions "
        "WHERE id = ? AND org_id = ? AND deleted_at IS NULL "
        "AND (user_id = ? OR visibility IN ('team', 'org'))",
        (session_id, org_id, user_id)
    )
    # ... fallback logging if not found
```

關鍵 SQL clause：`(user_id = ? OR visibility IN ('team', 'org'))`。同 org 限制由 `org_id = ?` 提供。

### 11.4 Shared Session Click Path（前端）

實作：`news-search.js:10573-10611`。

```
組織空間 tab click → renderSharedSessions(sessions)
        │
        ▼
left-sidebar-session-item with data-shared-session-id
        │
        ▼ (click)
fetch /api/sessions/{sharedId}
        │
        ▼ (200 success)
sharedHydrated = {
    id: s.id,
    _serverId: s.id,           ─ PG UUID
    _isShared: true,           ─ Y-1 tag
    _ownerUserId: s.user_id,   ─ Y-1 tag
    title, visibility,
    conversationHistory, sessionHistory, ... (snake → camel)
}
        │
        ▼
loadSavedSession(sharedHydrated)
```

### 11.5 Y-1 Guard：Shared Session 不 Spawn 自己 Row（commit 138ae61）

問題（lessons-frontend「Shared session click 觸發 spawn 自己的 row」）：

```
1. user click 組織空間的 session（別人的 PG UUID）
2. loadSavedSession(data.session) → currentLoadedSessionId = 別人的 UUID
3. 但該 session 不在我的 savedSessions（沒被 push 進去）
4. 後續任一 mutate（如打字 query）→ saveCurrentSession()
5. findIndex(currentLoadedSessionId) = -1 → 走 new branch
6. POST /api/sessions → 用我的 user_id 建立新 row
   → spawn「未命名搜尋」row（CEO 報的「member 點能源 spawn 重複」）
```

**雙重 guard**：

#### Guard 1：Shared Session Click Tag `_isShared` + `_ownerUserId`

如 §11.4 所示，hydrated 物件帶 `_isShared: true`。

#### Guard 2：`saveCurrentSession` 入口早退（`news-search.js:1712-1722`）

```js
const currentEntry = currentLoadedSessionId !== null
    ? savedSessions.find(s => matchSessionId(s.id, currentLoadedSessionId))
    : null;

// Case 1：currentEntry 存在且 _isShared
if (currentEntry && currentEntry._isShared) {
    console.warn('[saveCurrentSession] skipped: current session is shared (read-only context). currentLoadedSessionId=', currentLoadedSessionId);
    return;
}

// Case 2：currentLoadedSessionId 設了但 savedSessions 找不到
//        — 這是 canonical Y-1 path（shared session click 沒 push 進 savedSessions）
if (currentLoadedSessionId !== null && !currentEntry) {
    console.warn('[saveCurrentSession] skipped: currentLoadedSessionId not in savedSessions (likely shared session click). currentLoadedSessionId=', currentLoadedSessionId);
    return;
}
```

**通則**：用 `currentLoadedSessionId` 作為「current session is owned by me」的隱含假設，遇到 shared session 就破功。明確 tag 物件來源（`_isShared`、`_ownerUserId`）比靠 ID 反推安全。

---

## 12. Cancel + Retry Interrupt 流程

### 12.1 設計理念（D-2026-03-13）

CEO 嘗試「背景 stream 繼續」方案（搜尋中切 session 後 stream 在背景繼續）失敗：6 層 moving parts、3 種 bug、3 輪測試全失敗（lessons-frontend「背景 stream 繼續是假命題」）。

最終採用最簡方案：切 session 時 **cancel 所有 stream**，舊 session 標記 `interruptedSearch`，切回時顯示 retry 按鈕。

### 12.2 切 Session 時 cancelAll + Mark Interrupted

實作：`loadSavedSession`（`news-search.js:7903-8050`）。

```js
// 1. 標記舊 session 為 interrupted（如果正在處理中）
if (searchInput.dataset.processing === 'true' && currentLoadedSessionId !== null) {
    const interruptedQuery = currentMode === 'chat'
        ? (chatHistory.filter(m => m.role === 'user').pop()?.content || '')
        : (conversationHistory.length > 0 ? conversationHistory[conversationHistory.length - 1] : '');
    if (interruptedQuery) {
        const idx = savedSessions.findIndex(s => matchSessionId(s.id, currentLoadedSessionId));
        if (idx !== -1) {
            savedSessions[idx].interruptedSearch = { query: interruptedQuery, mode: currentMode };
            localStorage.setItem('taiwanNewsSavedSessions', JSON.stringify(savedSessions));
        }
    }
}

// 2. Cancel 所有 stream
cancelAllActiveRequests();
setProcessingState(false);

// 3. Increment generation ID（防 stale callback DOM 更新）
searchGenerationId++;

// 4. 載入新 session content
currentLoadedSessionId = session.id;
_sessionDirty = false;  // load 是 read-only
// ...
```

`cancelAllActiveRequests()`（`news-search.js:3122-3142`）覆蓋 4 條 stream：

| Channel | AbortController / EventSource |
|---------|-------------------------------|
| Search | `currentSearchAbortController` + `currentSearchEventSource` |
| Deep Research | `currentDeepResearchAbortController` + `currentDeepResearchEventSource` (legacy) |
| Free Conversation | `currentFreeConvAbortController` |
| Loading UI | `loadingState.classList.remove('active')` + `chatLoading` |

### 12.3 Retry UI

切回 interrupted session 時（`loadSavedSession` line 8029-8038）：

```js
if (session.interruptedSearch) {
    // 顯示舊 results（如果有）
    if (sessionHistory.length > 0) {
        const lastSession = sessionHistory[sessionHistory.length - 1];
        populateResultsFromAPI(lastSession.data, lastSession.query);
    }
    showInterruptedSearchNotice(session.interruptedSearch.query, session.interruptedSearch.mode);
    searchInput.value = session.interruptedSearch.query || '';
    resultsSection.classList.add('active');
    initialState.style.display = 'none';
}
```

`showInterruptedSearchNotice`（`news-search.js:3144-3193`）在 listView 頂端 prepend：

```
┌────────────────────────────────────┐
│      搜尋被中斷                     │
│      「{query}」                    │
│      [ 重新搜尋 ]                   │
└────────────────────────────────────┘
```

點「重新搜尋」按鈕：
1. `delete savedSessions[idx].interruptedSearch`
2. localStorage 同步
3. Remove notice DOM
4. `btnSearch.click()` → 模式已被 loadSavedSession 還原，直接 trigger

### 12.4 Tradeoff

- **代價**：搜尋結果遺失（需重新搜尋）
- **效益**：狀態管理極簡，不會有瘋狂打 API 或跨 session 污染的問題

---

## 13. Architecture Decisions

| ID | Title | Date | 章節對應 |
|----|-------|------|---------|
| D-2026-03-13 | Session 切換：Cancel + Retry Button（非背景 Stream） | 2026-03-13 | §12 |
| D-2026-03-13 | 前端 Session 資料架構：localStorage 為主，Server Session 為輔 | 2026-03-13 | §5 |
| D-2026-04-15 | Live Research 跨 request 持久化：`live_research_state` JSONB column | 2026-04-15 | §2.4 |
| D-2026-04-27 | PG datetime 序列化：DB layer 統一轉 ISO string | 2026-04-27 | §3.7 |
| D-2026-05-01 | 跨用戶 Session 隔離紀律：Logout 也清 + Logged-in 失敗不 silent fallback | 2026-05-01 | §8 |
| D-2026-05-01 | SSE Handler default branch 必須保留 `Object.assign`（black-list approach） | 2026-05-01 | §9 |
| D-2026-05-01 | Pre-Navigation Save 配 Dirty Flag — 防純瀏覽 PUT spawn | 2026-05-01 | §7 |
| D-2026-05-01 | _serverId 三層 Hydration + 5 秒重複 POST Detector | 2026-05-01 | §6 |
| **D-2026-05-13** | **Frontend Init Sync Refactor — `UserStateSync` 7 個 sync trigger + `cache.user_id == JWT.user_id` invariant 取代 9 個 case-by-case patch** | **2026-05-13** | **§1 / §1.4 / §1.5 / §8.5** |

**D-2026-05-13 取代清單**（從 codebase 移除）：
- 過去用來補清 6 個 user-scoped main-UI globals 的 legacy helper（§8.5）— 由 `UserStateSync.clearUserScopedState` + `resetMainUI` 取代
- `_clearUserScopedStorageIfUserChanged()` 函式 + `AuthManager.login()` 內 callsite（§8.3 — 由 `UserStateSync.runInitSync` 取代主要呼叫路徑）
- `loadSavedSession()` 的 metadata-only branch（由 `applyInit` + Trigger E lazy-load 取代）

**D-2026-05-13 保留清單**（defense-in-depth，**不取代** Init Sync 主流，見 §1.5）：
- `_sessionDirty` flag — 防純瀏覽 PUT spawn
- Server-side `_sanitize_session_history` classmethod — 防污染 entries 寫入
- `list_sessions` `ORDER BY updated_at DESC` — UI 排序穩定
- Shared session click 的 `_isShared` tag + `saveCurrentSession` early return（§11.5 Y-1 fix） — 跨 user 隔離

詳見 `docs/decisions.md` D-2026-05-13 + `docs/in progress/plans/frontend-init-sync-refactor-plan.md`。

---

## 14. 檔案清單

### 14.1 後端 Python

| 檔案 | 行數 | 說明 |
|------|------|------|
| `code/python/core/session_service.py` | 649 | SessionService class（CRUD + sharing + sanitize + export） |
| `code/python/webserver/routes/sessions.py` | 515 | aiohttp handlers（15 endpoints） |
| `code/python/webserver/routes/user_init.py` | 116 | **D-2026-05-13 新增**。`GET /api/user/init` composite endpoint — 一次 round-trip 回 `{ user, org, role, sessions, shared_sessions, preferences }`。Frontend `UserStateSync.fetchInit` 唯一後端入口 |
| `code/python/auth/auth_db.py` | 392-415 (SQLite) / 588-609 (PG) | `search_sessions` table schema |
| `code/python/alembic/versions/c1c6deac2013_add_session_tables.py` | — | Initial migration |
| `code/python/tools/migrate_live_research.py` | — | `live_research_state` column migration |
| `code/python/reasoning/live_research/stage_state.py` | — | LiveResearchStageState dataclass |
| `code/python/methods/live_research.py` | 200-258 | LR state save/load via SessionService |
| `code/python/scripts/cleanup_polluted_session_history.py` | — | 一次性清歷史 PG 污染 |
| `code/python/tests/test_user_state_sync_invariant.py` | — | Contract test 驗證 `news-search.js` 的 `UserStateSync` 寫法符合 invariant（D-2026-05-13） |

### 14.2 前端 JavaScript

關鍵檔案與區段（行號為大致位置，refactor 後可能微調，以實際 grep 為準）：

| 檔案 / 區段 | 說明 |
|------|------|
| **`static/js/features/session-manager.js`** | **ES module（v4.0 Commit 10/30 從 `news-search.js` 遷出）。`export class SessionManager`：scheduleSave / flushPendingSave / `_cancelPendingSave`（皆 per-session `_pendingSaves` Map，§4.3）/ saveSession / loadSessions / `_postedRecently` detector / `_saveToLocalStorage`。module-level：`isSessionDirty/markSessionDirty/clearSessionDirty`（§7）、`initSessionManager` / `getSessionManager` singleton（inert on import，D-13）** |
| `static/js/features/sessions-list.js` | 擁有 `savedSessions` array（v4.0 Commit 10）；`getSavedSessions()` getter 供 session-manager.js `_saveToLocalStorage` 讀取（單向 import，§4 D-V6） |
| `static/news-search.js` › `class AuthManager` | 含 `_clearUserScopedStorageIfUserChanged`（D-2026-05-13 後由 UserStateSync 取代主要呼叫路徑）、`_handleAuthFailure`（由 trigger C/D 驅動；入口呼 `sessionManager._cancelPendingSave()`） |
| `static/news-search.js` › `loadSavedSession` | Trigger E session click 入口（own session）；**pre-await 同步區 bump switch token 作廢 stale restore**（§4.3a, commit `0218fbda`）+ hydrate path + interrupt mark |
| `static/news-search.js` › `saveCurrentSession` | SessionManager 的 caller（Path B 仍留 news-search.js）：dirty flag 早退、shared guard（Y-1）、`_serverId` 保留；呼 `window.sessionManager.scheduleSave` |
| `beforeunload` flush | `flushPendingSave(currentSession)` 立即 fire |
| `checkAuthOnLoad` | trigger B / F 入口；401 path 走 `_handleAuthFailure`（trigger D） |
| `DOMContentLoaded` handler | trigger F cold-start 入口；呼叫 `UserStateSync.runInitSync` |
| **`UserStateSync` module** | **D-2026-05-13 新增。`clearUserScopedState` / `fetchInit` / `applyInit` / `fullReset` / `runInitSync`；`assertUserIdentity` helper + `UserStateSyncError` class。所有 session-related user-scoped 寫入唯一入口** |
| `_sessionDirty` 宣告 | **已遷 `session-manager.js`** module-level `let`（v4.0 Commit 10, D-V14；§7 dirty flag 紀律），caller helper 在 news-search.js |
| `resetConversation` | 10 個 globals（btnNewConversation 用）— D-2026-05-13 後由 `UserStateSync.resetMainUI` safely wrap |
| ~~Legacy main-UI globals sweep helper~~ | **已移除**（Task 13 cleanup，superseded by `UserStateSync.clearUserScopedState` + `resetMainUI`，見 §8.5） |
| GET SSE switch | 含 black-list cases（§9） |
| POST SSE switch | 含 black-list cases（§9） |
| `cancelActiveSearch` / `cancelAllActiveRequests` | 切 session 時 abort 所有 stream（§12） |
| `showInterruptedSearchNotice` | Retry UI（§12） |
| `renderLeftSidebarSessions` | 顯式 sort by `updated_at DESC`（§7 / §1.5 defense-in-depth） |
| Shared session click handler | Trigger E session click 入口（shared session）；`_isShared` + `_ownerUserId` tag（§11.4） |

**D-2026-05-13 後新增 backend 路由**：
- `code/python/webserver/routes/user_init.py` — `GET /api/user/init` composite endpoint（一次回 `{ user, org, role, sessions, shared_sessions, preferences }`，user payload 含 `org_id` + `role`，commit `228a93a` shape contract）

### 14.3 相關 Plans / Memory

| 檔案 | 說明 |
|------|------|
| `docs/in progress/plans/session-crud-sharing-plan.md` | 完整 CRUD + Sharing 設計（943 行） |
| `docs/in progress/plans/session-persistence-and-cross-user-leak-fix-plan.md` | Bug A/B/C 修法（780 行） |
| `docs/in progress/plans/hidden-path-rca.md` | RCA 主因 + 三 Fix（417 行） |
| `docs/in progress/plans/cross-user-localstorage-and-sse-pollution-fix-plan.md` | Cross-user + SSE 污染 plan |
| `docs/in progress/plans/sort-order-and-cross-user-spawn-fix-plan.md` | sort 亂跳 + spawn 修法 |
| `docs/in progress/plans/rewire-scheduleSave-without-spawn-plan.md` | wire scheduleSave + 三層 _serverId |
| `docs/in progress/plans/frontend-init-sync-refactor-plan.md` | **D-2026-05-13** — `UserStateSync` + 7 trigger architectural refactor 完整 plan |
| `docs/in progress/plans/frontend-init-sync-state-inventory.md` | D-2026-05-13 — 45 個 localStorage / 22 個 in-memory globals 的 sweep audit |
| `memory/lessons-frontend.md` | Session 持久化 / Cross-User / Save Pattern / SSE Handler 段落 + Init Sync Refactor lessons |
| `memory/lessons-auth.md` | Cross-User 隔離 / Logout 紀律段落 |

### 14.4 相關 Commits（時間序）

| Commit | 內容 |
|--------|------|
| `9362312` | RG fork merge — 帶入 SessionManager（但 `scheduleSave` 無 caller，dead code） |
| `24d39f4` | 加 `_clearUserScopedStorageIfUserChanged` + USER_SCOPED_KEYS 清單 |
| `d910819` | 誤改 SSE white-list（破壞性）→ `1782e76` 緊急 hotfix 改回 black-list |
| `7119da0` | 後端 `_sanitize_session_history` classmethod |
| `1ca2cb1` | `cleanup_polluted_session_history.py` 一次性 cleanup |
| `727db55` | 嘗試直接 wire scheduleSave（漏 _serverId 三層 → spawn 13 筆）→ revert |
| `e43468d` | saveCurrentSession update branch 保留 `_serverId`（Layer 1） |
| `3b9f7d8` | wire scheduleSave + Layer 2 + 3 hydration + `_postedRecently` detector |
| `138ae61` | sort by `updated_at DESC` + 401 走 `_handleAuthFailure` + Y-1 shared session guard |
| `e0b5a41` | RCA 三 Fix：dirty flag + cancelPendingSave + main-UI globals sweep helper（後者於 D-2026-05-13 被 `UserStateSync` 取代並從 codebase 移除）+ favicon 404 |
| `2ee5508` | **D-2026-05-13**：`register_user()` / `activate_user()` auto-issue refresh cookie — 讓 onboarding 完成跳 `/` 後 `checkAuthOnLoad` 拿到 JWT 走 trigger A |
| `228a93a` | **D-2026-05-13**：`/api/user/init` user payload 加 `org_id` + `role` shape contract（mirror `/api/auth/login` + `/api/auth/me`） |
| v4.0 Commit 10 | **2026-05-24**：SessionManager 遷出至 `static/js/features/session-manager.js`（ES module）；`savedSessions` 改由 `sessions-list.js` 擁有、`_sessionDirty` ownership 落 session-manager.js（D-V14） |
| v4.0 Commit 30 | **2026-05-25**：per-session debounce `_pendingSaves` Map 取代全局 `_saveTimer` / `_savePending`（regression fix；rapid switch 不再誤 cancel 上一 session 的 pending PUT；§4.3）|
| `0218fbda` | **2026-06-17**：session-switch token race fix — `loadSavedSession` 進入後 pre-await 同步區無條件 bump switch token，作廢上一 session 的 stale `setTimeout` restore（§4.3a；LR-專屬 token 機制細節在 LR spec）|

---

## 15. 環境變數

| Env Var | 用途 | 預設 / Fallback |
|---------|------|----------------|
| `POSTGRES_CONNECTION_STRING` | PG 連線（與 Auth / Search 共用 `nlweb` database） | — |
| `DATABASE_URL` | Fallback 1 | — |
| `ANALYTICS_DATABASE_URL` | Fallback 2 (legacy) | — |
| （無設定） | Fallback to SQLite at `db/nlweb.db` | 本機開發 |

選擇邏輯見 `auth_db.py:60-63`。

> Session 系統與 Auth / Articles / Chunks 共用同一 PG database（`nlweb`），不需獨立 env var。

---

## 16. Known Issues / Future Work

### 16.1 Active TODOs

| # | 項目 | 嚴重度 | 說明 |
|---|------|-------|------|
| 1 | `update_session` 不檢查 affected rows | 中 | Cross-user PUT（member 對 admin's session）回 200 但 0 rows changed → silent fail anti-pattern。Hidden-path RCA Root Cause 3，留待另開 issue。 |
| 2 | `session_history` 200KB 超過後仍只 warning | 中 | `_check_jsonb_size` 警告但不 enforce。當實際碰到時要 migrate 到 `session_messages` table（拆出 1-N relation）。 |
| 3 | RIS export 標題逸出 / 中文格式 | 低 | `_export_ris` 對中文 + 特殊字元的 RIS 格式合規性未做 unit test。 |
| 4 | folder hierarchy 仍 localStorage-only | 中 | `loadFolders` / `saveFoldersSync` 沒走 server。`org_folders` table 已建好但前端未 wire。跨裝置 folder 不同步。 |
| 5 | `session_shares` table 未啟用 | 低 | Per-user share（精確權限）schema 已建，但前端走 `visibility = 'org'` 簡化模型。 |
| 6 | `team_comments` JSONB 未 wire UI | 低 | Schema 預留 team comments，但前端無 UI。 |

### 16.2 Future Considerations

- **Session size 拆 table**：當單筆 session 持續 > 200KB（多輪 DR + KG），考慮拆 `session_messages` 1-N table，避免 JSONB 全列重寫成本。
- **Real-time collaboration**：目前 session 是 owner-write、others-read。若要多人協作（CEO 提過的「研究室」）需要 CRDT or Operational Transform，目前不在 roadmap。
- **跨 org 引用**：visibility 限同 org。若未來支援跨 org 引用（如顧問交付給客戶），需要新的 `external_share_tokens` table + 唯讀 page。

---

*最後更新：2026-06-17（spec drift audit A3 — §4 SessionManager 重定向至 `static/js/features/session-manager.js`（ES module，v4.0 Commit 10/30 遷出）；§4.2 method 行號改 grep marker + ~行號；§4.3 改寫為 per-session debounce `_pendingSaves` Map（取代全局 timer，v4.0 Commit 30 regression fix）；新增 §4.3a session-switch token race 通用守則（commit `0218fbda`，LR-專屬 token 細節歸 LR spec）；§14.2/§14.4 同步檔案位置與 commits。先前更新 2026-05-15：D-2026-05-13 Frontend Init Sync Refactor — `UserStateSync` 7 trigger 架構納入主流。spec 整合自 frontend-spec / login-spec / 多個 plan 檔；以本檔為 single source of truth）*
