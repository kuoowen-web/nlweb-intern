# Frontend Specification

> 台灣新聞 AI 搜尋引擎前端功能規格書

---

## 目錄

1. [架構概覽](#1-架構概覽)
2. [檔案結構](#2-檔案結構)
3. [頁面結構](#3-頁面結構)
4. [核心功能模組](#4-核心功能模組)
5. [狀態管理](#5-狀態管理)
6. [API 整合](#6-api-整合)
7. [串流處理](#7-串流處理)
8. [樣式系統](#8-樣式系統)
9. [事件處理](#9-事件處理)
10. [LocalStorage 資料結構](#10-localstorage-資料結構)

---

## 1. 架構概覽

> **核心架構**：兩個 architectural invariant 同時成立。
>
> **D-2026-05-13 Frontend Init Sync**：所有 user-scoped 前端 state 寫入**只透過 `UserStateSync` module 7 個 sync trigger** 進行（Login/Onboarding、Identity change、Logout、401、Session click、Page reload、SSE envelope）。核心 invariant：`cache.user_id == JWT.user_id`，mismatch 由 `assertUserIdentity()` 拋 `UserStateSyncError` 觸發 fullReset。詳見 §10.3。
>
> **D-2026-05-25 Frontend Modular Refactor v4.0 Path A++**：23 ES module surfaces（5 core + 16 features + 2 utils）取代 11,697 LOC 單體 script。`UserStateSync` IIFE 搬至 `core/state-sync.js`（~335 行，真實 owner）。24 user-scoped `let` 分散至 8 owner modules。news-search.js 精簡至 ~3,517 行 stub（-70%）。詳見 §2。

### 1.1 技術棧

| 技術 | 用途 |
|------|------|
| Vanilla JavaScript | 主要邏輯（無框架） |
| CSS3 + CSS Variables | 樣式系統 |
| D3.js v7 | 知識圖譜視覺化 |
| Server-Sent Events (SSE) | 串流回應 |
| LocalStorage | 客戶端資料持久化 |

### 1.2 設計原則

- **漸進式渲染**：搜尋結果、摘要、推論鏈皆採用串流式漸進渲染
- **非阻塞 UI**：所有 API 呼叫皆為非同步，支援中斷機制
- **響應式設計**：支援桌面與平板裝置
- **無障礙支援**：大字體模式、語義化 HTML
- **User State Sync**：cross-user / cross-tab 一致性透過 `UserStateSync` 七個 trigger 統一管理（§10.3），禁止繞過直接寫入 user-scoped state
- **ES Module 架構**：`<script type="module">` + 23 module surfaces（v4.0）。State ownership 按 module 邊界明確分離，不集中在單一 script
- **Import Direction 規則（D-V6）**：基本方向 `core/ → features/ → utils/`（不反向）。D-V6 放寬：cross-feature read-only 或 function-call import 允許（search↔chat / KG→search / deep-research↔search / folders→sessions-list）。禁止：features/ → core/auth-manager；circular imports

---

## 2. 檔案結構

> **2026-05-25 — Frontend Modular Refactor v4.0 Path A++ 完成**（D-2026-05-25）：29 commits（0a–25）+ 6 post-E2E regression/UX patches = 35 total。`news-search.js` 從 11,697 LOC 精簡至 ~3,517 行（-70%）。23 module surfaces 建立（5 core + 16 features + 2 utils）。`UserStateSync` IIFE（~335 行）從 news-search.js 搬至 `core/state-sync.js`，不再是 thin alias。24 user-scoped `let` declarations 分散至 8 owner modules。`<script type="module">` 載入方式啟用（commit 0c）。
> **LOC 說明**：本 spec 的行數均標「~行」概數（避免隨提交漂移），實際數字以 `wc -l` 自驗為準。後續持續演進，數百行內的差異屬正常。
> **Line refs 說明**：v4.0 完成後 news-search.js 為 ~3,517 行 stub。本 spec 內行號 refs 均為邏輯參考。需找實際位置時 grep 函數名或 D-1 module header 標記。

```
static/
├── news-search-prototype.html   # 主頁面 HTML 結構（type="module" script 載入）
├── news-search.js               # ~3,517 行 stub — 14 類 KEEP-in-place 函數 + 21 intentional window bridges（見 §2.3）
├── news-search.css              # 主樣式（barrel @import manifest + 縮減 legacy inline rules）
├── analytics-tracker-sse.js     # 分析追蹤器
├── analytics-dashboard.html     # 分析儀表板
├── indexing-dashboard.html      # 索引儀表板
├── index.html                   # 入口重導向
├── js/                          # ES Module 架構（v4.0 完成）
│   ├── main.js                  # Bootstrap entrypoint — import chain / injectStateSync / 21 window bridges / DOMContentLoaded init
│   ├── phase-gate-probe.js      # D-11 programmatic probe（gated by ?phaseProbe=1）
│   ├── utils/
│   │   ├── dom.js               # matchSessionId, escapeHtml
│   │   └── analytics.js         # getAnalyticsTracker, getCurrentAnalyticsQueryId, setCurrentAnalyticsQueryId
│   ├── core/
│   │   ├── auth-manager.js      # AuthManager class + authManager singleton + injectStateSync
│   │   ├── auth-ui.js           # showAuthModal, hideAuthModal, updateAuthUI（NEW v4.0）
│   │   ├── session-coordinator.js  # initSessionCoordinator — 跨模組 session 協調（NEW v4.0）
│   │   ├── state-sync.js        # UserStateSync IIFE（~335 行，v4.0 commit 11 搬入）— 真實 owner，不再是 alias
│   │   └── page-bootstrap.js    # checkAuthOnLoad + visibilitychange listener + bootstrapPage()
│   └── features/
│       ├── mode.js              # getCurrentMode, setCurrentMode — search/chat/LR mode state
│       ├── search.js            # performSearch, getAccumulatedArticles — 搜尋流程 + 文章 state
│       ├── chat.js              # getChatHistory, pushChatHistory — 對話 history state
│       ├── pins.js              # getPinnedMessages, getPinnedNewsCards — 釘選功能
│       ├── research.js          # getCurrentResearchReport — Deep Research report state
│       ├── sharing.js           # getShareContentOverride — 分享功能
│       ├── live-research.js     # isLRInProgress, getLRSessionId, performLiveResearch — LR 功能 + state
│       ├── folders.js           # getFolders, getSourceFolders, clearPreFolderState — Folder CRUD + state
│       ├── source-filters.js    # getSelectedFileIds — 私有檔案來源篩選（NEW v4.0）
│       ├── deep-research.js     # performDeepResearch — Deep Research 執行器（NEW v4.0）
│       ├── knowledge-graph.js   # KG render + edit ops（NEW v4.0）
│       ├── file-kb.js           # loadUserFiles — 私有知識庫檔案管理（NEW v4.0）
│       ├── sessions-list.js     # renderLeftSidebarSessions, renderSharedSessions, hydrateFromSoftRefreshInit
│       ├── session-manager.js   # sessionManager singleton, markSessionDirty, clearSessionDirty, isSessionDirty（D-V14 _sessionDirty owner）
│       ├── lr-resume-classify.js # LR resume 分類 — 中斷後續跑判定（LR-specific）
│       └── text-fragment.js     # Text Fragment 深連結 — 引用片段定位/捲動高亮
└── css/                         # CSS 模組（v3.3 Phase 1-2 建立）
    ├── tokens.css               # :root variables / @font-face
    ├── base.css                 # body reset / .container / .emoji-bw
    └── components/
        ├── sidebar.css          # .left-sidebar*
        ├── popover.css          # .settings-popover
        ├── modal.css            # .modal-overlay / .auth-modal etc.
        └── tabs.css             # .right-tabs-container
```

### 2.1 ES Module 載入方式

HTML 載入：`<script type="module" src="static/js/main.js">` — defer semantics，取代 classic script。

`main.js` 職責：
1. Import 23 module surfaces（部分由其他 module 轉導入，非全部在 main.js 直接 import）
2. 呼叫 `injectStateSync({ UserStateSync, UserStateSyncError, assertUserIdentity })` — wire auth-manager
3. 呼叫 `injectStateSyncBackref({ isLRInProgress, getLRSessionId, clearLRSessionId })` — wire live-research D-V3 backref
4. 設定 21 intentional `window.X = X` bridges（見 §2.3）
5. DOMContentLoaded init sequence

### 2.2 news-search.js Stub — KEEP-in-place 14 類函數

以下函數刻意保留在 news-search.js，不搬出：

| 類別 | 代表函數 | 保留原因 |
|------|--------|---------|
| DOM init（頁面啟動）| DOMContentLoaded handlers | 直接操作 HTML DOM，與 module 載入同時執行 |
| Bootstrap 序列 | window.resetConversation prefix | 相依 window global，搬出需完整 bridge 重構 |
| Session hydration | loadSavedSession | 直接 reassign 多個 outer-scope `let`，classic-script only |
| DOM-coupled residuals | setProcessingState, cancelAllActiveRequests | 操作複雜 DOM + 相依多個 outer state |
| 21 window-attach bridges | window.openTab = openTab 等 | Sidebar inline-onclick callsite，無法移除 |

**Root cause**：classic-script `let` binding 不能被 ES module reassign（D-V3 / lessons-frontend 2026-05-21）。真實搬出需一次性大改 declaration → getter/setter/event-based pattern。AC-V6（≤500 LOC）為 GOAL not hard target，CEO directive 1（2026-05-25）接受 ~3,400 行 stub 現況（refactor 完成時；後續演進至 ~3,517 行）。

### 2.3 21 Intentional Window Bridges（Load-Bearing）

v4.0 完成後仍保留 21 個 `window.X = ...`，原因分兩類：

| 原因 | 說明 |
|------|------|
| Sidebar inline-onclick | HTML sidebar 用 `onclick="window.funcName()"` 呼叫，無法改成 ES import |
| ES module parse-time cycle avoidance | 跨模組呼叫若改 import 會形成循環依賴；window bridge 避免 |

完整清單記錄在 `static/js/news-search.js` top-of-file commit 25 comment block（v4.0 最終狀態）。清零路徑需 HTML sidebar 重構（sidebar inline-onclick 替換為 ES event listener）+ 解決 parse-time circular dependency，為後續 Phase 7+ scope。

### 2.4 設計合約（v4.0 Design Contracts）

這些合約在 v4.0 執行過程中由 CEO 拍板確立，為後續 refactor 的設計邊界：

**D-V3 Backref Pattern（state-sync ↔ live-research 循環依賴迴避）**

`core/state-sync.js` 不可 direct import `features/live-research.js`（TDZ + circular dependency 風險）。改用 inject pattern：

```
live-research.js 啟動時呼叫：
  injectStateSyncBackref({ isLRInProgress, getLRSessionId, clearLRSessionId })
→ state-sync.js 內部儲存 backref function pointers
→ clearUserScopedState() 透過 backref pointers 呼叫（不 direct import）
```

**D-V6 Import Direction 規則 + 放寬**（CEO 拍板）

基本方向：`core/ → features/ → utils/`（不反向）。

放寬允許的 cross-feature import：
- `search ↔ chat`（互相讀取 history 做 session state）
- `knowledge-graph → search`（KG 讀取搜尋結果 per CEO #7）
- `deep-research ↔ search`（DR 執行後更新 search state）
- `folders → sessions-list`（commit 8 先例，folder 更新後 re-render sidebar）

禁止：`features/ → core/auth-manager`（auth 單向 core）；circular imports。

**D-V14 _sessionDirty Owner**

`_sessionDirty` flag 由 `features/session-manager.js` 唯一 own（`markSessionDirty`, `clearSessionDirty`, `isSessionDirty` exports）。news-search.js 內其他函數透過 `markSessionDirty()` 呼叫，不直接操作 boolean。save dirty flag 的架構意圖不變（見 §4.2.5），owner 從 news-search.js outer scope 移至 session-manager module。

**AC-V1–V7 驗收合約最終狀態**

| AC | 描述 | 最終狀態 |
|----|------|------|
| AC-V1 | window bridges grep 0 | Partial — 21 intentional residuals（documented commit 25）|
| AC-V2 | outer user-scoped let === 0 | Pass（commit 11）|
| AC-V3 | UserStateSync IIFE in news-search.js === 0 | Pass（commit 11）|
| AC-V4 | ~441 real ops migrated | Pass（cumulative）|
| AC-V5 | hazard map H1-H6 + R0-R4 + R8.1-R8.3 resolved | Pass |
| AC-V6 | ≤500 LOC OR delete | Stub path — ~3,400 行 at refactor end（CEO directive 1 接受；現 ~3,517 行）|
| AC-V7 | HTML type="module" + zero console error | Pass（commit 0c + final E2E）|

**Phase 0 Source-of-Truth + β Inventory Path（v4.0 新標準）**

大型 function-migration refactor 前必須先派 inventory subagent grep source 驗 function 存在性，產出 source-verified list 再 dispatch executor（「β path」）。不可信任 plan 文字中的 function name list。

**Post-Refactor Regression Sweep Standard（v4.0 新標準）**

每個 refactor batch 後強制 grep 驗：
- Pattern 1：bare migrated identifier 殘留在 news-search.js
- Pattern 2：window.X bridge ref 殘留在 features/core（含 silent defensive fallback variant）

---

## 3. 頁面結構

### 3.1 主頁面佈局

```
┌──────────┬──────────────────────────────────┬───────────────────┐
│          │                        [Aa]      │                   │
│  左側邊欄  │          主內容區                 │   右側 Tab 面板    │
│  (可收合)  │                                  │   (可展開/收合)    │
│          │  ┌────────────────────────────┐  │                   │
│ - 分享結果 │  │ 初始狀態 / 搜尋框 / 結果區  │  │ - 來源篩選        │
│ - 新對話   │  │                            │  │ - 我的檔案        │
│ - 歷史搜尋 │  └────────────────────────────┘  │ - 搜尋紀錄        │
│ - 資料夾   │                                  │ - 釘選新聞        │
│ - Sessions │                                  │                   │
│          │                                  │                   │
│ ─────── │                                  │                   │
│ 🔔 ⚙設定 │                                  │                   │
└──────────┴──────────────────────────────────┴───────────────────┘
```

> Header 已移除（2026-03-29）。字體大小按鈕移到搜尋框右上，通知+Auth 移到左 sidebar 底部 settings popover。

### 3.2 HTML 元素 ID 對照表

| 元素 ID | 說明 | 所在區域 |
|---------|------|----------|
| `leftSidebar` | 左側邊欄容器 | 左側 |
| `rightTabsContainer` | 右側 Tab 面板容器 | 右側 |
| `initialState` | 初始歡迎畫面 | 主內容 |
| `searchContainer` | 搜尋框容器 | 主內容 |
| `loadingState` | 載入中狀態 | 主內容 |
| `resultsSection` | 搜尋結果區 | 主內容 |
| `chatContainer` | 聊天容器 (自由對話模式) | 主內容 |
| `folderPage` | 資料夾頁面 | 主內容 (覆蓋) |
| `btnFontSize` | 字體大小切換 | 搜尋框右上 |
| `btnNotification` | 通知按鈕 | 左 sidebar 底部 |
| `btnSettings` | Settings popover 觸發 | 左 sidebar 底部 |
| `settingsPopover` | Settings 彈出選單 | 左 sidebar 底部 |
| `authArea` | Auth 區域（登入/使用者選單） | Settings popover 內 |

---

## 4. 核心功能模組

### 4.1 搜尋模式系統

系統支援三種搜尋模式，透過 `currentMode` 變數追蹤：

| 模式 | 變數值 | 說明 | API 端點 |
|------|--------|------|----------|
| 新聞搜尋 | `search` | 快速搜尋，回傳文章列表 + 摘要 | SSE `/ask` |
| 進階搜尋 | `deep_research` | Deep Research，含推論鏈、知識圖譜 | SSE `/api/deep_research` |
| 自由對話 | `chat` | 多輪對話，支援上下文 | POST `/api/free_conversation` |

#### 4.1.1 新聞搜尋 (Search Mode)

```javascript
async function performSearch() {
    // 1. 建立 SSE 連線到 /ask
    // 2. 串流接收：articles, answer, reasoning
    // 3. 漸進式渲染文章卡片和摘要
}
```

**串流事件類型**：
- `articles` - 文章資料
- `answer_chunk` - 摘要片段
- `reasoning_chunk` - 推論片段
- `done` - 完成

#### 4.1.2 進階搜尋 (Deep Research Mode)

```javascript
async function performDeepResearch(query, skipClarification, comprehensiveSearch, userTimeRange, userTimeLabel) {
    // 1. 建立 SSE 連線到 /api/deep_research
    // 2. 處理澄清問題 (clarification)
    // 3. 串流接收研究報告
    // 4. 渲染知識圖譜、推論鏈
}
```

**輸入框位置**：進入 Deep Research 模式時，搜尋框移動到聊天區底部（與 Chat 模式相同行為），回到 Search 模式時搜尋框回歸主內容區頂部。

**進階選項**：研究模式已移除（2026-03-29 shelf），前端固定使用 discovery。進階搜尋 popup 改為非 modal，包含來源篩選 checkbox（連動右 sidebar）和進階設定（KG toggle + Web Search toggle）。

#### 4.1.3 自由對話 (Chat Mode)

```javascript
async function performFreeConversation(userMessage) {
    // 1. POST 到 /api/free_conversation
    // 2. 維護 conversationHistory
    // 3. 支援多輪對話上下文
}
```

### 4.2 左側邊欄系統

#### 4.2.1 功能按鈕

| 按鈕 ID | 功能 | 處理函數 |
|---------|------|----------|
| `btnShareSidebar` | 分享搜尋結果 | 開啟分享 Modal |
| `btnNewConversation` | 開啟新對話 | `resetConversation()` |
| `btnHistorySearch` | 歷史搜尋 | `showHistoryPopup()` |
| `btnToggleCategories` | 資料夾系統 | `showFolderPage()` |
| `btnCollapseSidebar` | 收合側邊欄 | 隱藏側邊欄 |

#### 4.2.2 Session 列表

```javascript
// 渲染左側邊欄的 session 列表（最多 15 條）
function renderLeftSidebarSessions() {
    // 顯示最近 15 個 sessions（依 updatedAt DESC 排序）
    // 支援拖曳到資料夾
    // 支援重新命名、刪除、分享
}
```

**排序規則（5/01 update）**：使用顯式 `sort by updatedAt DESC` 取代既有 `.slice().reverse()`。原因：兩條 data path 順序語意可能反向 —
- localStorage in-memory `push()` pattern：最新在 array 末尾，`.reverse()` 後最新在前。
- Server `loadSessions()` 走 `/api/sessions`：回傳已是 `ORDER BY updated_at DESC`（首位最新），再 `.reverse()` 反而把**最舊推到最上**。

`renderLeftSidebarSessions` 永遠不假設 array order，顯式 sort 才能涵蓋兩條 path：

```javascript
const recent = savedSessions
    .slice()
    .sort((a, b) => {
        const ta = new Date(a.updatedAt || a.updated_at || a.createdAt || a.created_at || 0).getTime();
        const tb = new Date(b.updatedAt || b.updated_at || b.createdAt || b.created_at || 0).getTime();
        return tb - ta; // DESC: newest at top
    })
    .slice(0, 15);
```

**通則**：render 時不該假設 array order；任何 source 不單一的 list 都該顯式 sort。
**引用**：commit `138ae61`（sort order fix）、`memory/lessons-frontend.md`「sidebar 排序：localStorage push order ≠ server ORDER BY」、`static/news-search.js` 約 line 9749-9769。

#### 4.2.3 Session 持久化路徑（5/01 update）

**背景**：`SessionManager.scheduleSave` 從 RG fork merge（commit `9362312`）就只有定義沒 caller，sessions 看似持久化是 localStorage 的假象。Commit `3b9f7d8` 在 `saveCurrentSession` 結尾 wire `scheduleSave`，但同步必須補完 `_serverId` 三層 hydration 否則 PUT 走錯成 POST → spawn 重複 row。

**`_serverId` 三層 hydration**：每一層都要保留 `_serverId` 才能讓後續 `scheduleSave` 走 PUT（更新既有 PG row）而非 POST（新建 row）。漏掉任一層都會回退到 POST：

| 層 | 位置 | 修法 | Commit |
|----|------|------|--------|
| 1. **Update path**（`saveCurrentSession` overwrite） | `static/news-search.js:1742-1759`（`savedSessions[idx] = {...}`）| object literal 顯式包含 `_serverId: savedSessions[existingSessionIndex]._serverId` | `e43468d` |
| 2. **Hydrate path**（`loadSavedSession` 從 server fetch full session） | `static/news-search.js:7903-7950`（`hydrated = {...session, ...}`）| 顯式 `_serverId: session._serverId || serverId`（fallback 用 UUID-shaped `session.id`）| `3b9f7d8` |
| 3. **Page-load loadSessions callback**（server `list_sessions` 只回 `id`，不回 `_serverId`）| `static/news-search.js:980-991`（`savedSessions = sessions.map(...)`）| 把 server `id`（PG UUID）map 成 `_serverId`：`_serverId: s._serverId \|\| (typeof s.id === 'string' && s.id.includes('-') ? s.id : null)` | `3b9f7d8` |

**最後防線：Duplicate POST detector**（`SessionManager._postedRecently`）：
```javascript
// static/news-search.js:254-257（constructor）+ ~348-357（POST 前 5 秒重複偵測）
this._postedRecently = new Map();
// ...
const lastPost = this._postedRecently.get(session.id) || 0;
if (Date.now() - lastPost < 5000) {
    console.error('[SessionManager] DEFENSIVE: POST suppressed (duplicate within 5s) ...');
    return;
}
this._postedRecently.set(session.id, Date.now());
```

5 秒內同一個 `session.id` 第二次 POST 直接 console.error + 抑制 — 用來偵測「`_serverId` 三層」之外有第四層漏洞的 regression。

**通則**：物件 replace pattern 要全 grep 該物件所有「再生產點」（save / hydrate / load / refresh），任一處漏掉 marker 都會走錯分支。Defense-in-depth 用 detector 把 regression 變 loud failure，不靠 silent fallback。

**引用**：D-2026-05-01「跨用戶 Session 隔離紀律」、`memory/lessons-frontend.md`「saveCurrentSession 物件 replace 容易丟附加屬性 — _serverId 三層漏洞」、commits `e43468d` + `3b9f7d8`。

#### 4.2.4 Shared Session 隔離（5/01 update）

**問題**：點「組織空間」(shared) tab 裡別人的 session（`static/news-search.js:10573-10610` shared session click handler）→ `currentLoadedSessionId` 設成**別人的 PG UUID**，但該 session 不在我的 `savedSessions`。後續 `saveCurrentSession` `findIndex = -1` → push 新 entry（`id = Date.now()`）→ POST → spawn 自己「未命名搜尋」row。

**修法**：兩層 guard（commit `138ae61`）：

1. **Shared session click handler** 把 server payload map snake_case → camelCase 並 tag `_isShared = true`、`_ownerUserId`：
   ```javascript
   // static/news-search.js:10585-10602
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
   loadSavedSession(sharedHydrated);
   ```

2. **`saveCurrentSession` 函數頭加早退**（`static/news-search.js:1700-1722`）：
   ```javascript
   const currentEntry = currentLoadedSessionId !== null
       ? savedSessions.find(s => matchSessionId(s.id, currentLoadedSessionId))
       : null;
   if (currentEntry && currentEntry._isShared) {
       console.warn('[saveCurrentSession] skipped: current session is shared (read-only context). ...');
       return;
   }
   if (currentLoadedSessionId !== null && !currentEntry) {
       console.warn('[saveCurrentSession] skipped: currentLoadedSessionId not in savedSessions ...');
       return;
   }
   ```

兩個條件涵蓋：(1) 未來把 shared entry push 進 savedSessions 也安全 (2) 目前不 push 的情境（findIndex = -1）也擋住。

**通則**：用 `currentLoadedSessionId` 作為「current session is owned by me」的隱含假設，遇到 shared session 就破功。明確 tag 物件來源（`_isShared`、`_ownerUserId`）比靠 ID 反推安全。

**引用**：commit `138ae61`、`memory/lessons-frontend.md`「Shared session click 觸發 spawn 自己的 row」、`static/news-search.js`（`saveCurrentSession`、shared session click）。

#### 4.2.5 Pre-Navigation Save 配 Dirty Flag（5/01 update）

**背景**：4 個 callsite（`static/news-search.js:1572`、`1676`、`9795`、`10331`）在 user click sidebar / 開新對話 / 開歷史 popup / 切資料夾時呼叫 `saveCurrentSession()` 作為「pre-navigation save」防護，避免 deep research 報告等狀態切換前丟失。但這 4 個 callsite 的 outer guard 只檢查 `sessionHistory.length > 0 || currentResearchReport || chatHistory.length > 0`（「**有沒有 in-memory 內容**」），**純瀏覽 loaded session 時 guard 永遠 true** → save body 永遠執行 → `updatedAt = Date.now()` + `scheduleSave` PUT → server 端 `update_session` 無條件 `set updated_at = NOW()` → 加上 4.2.2 sort by `updated_at` DESC，**純瀏覽就跳 top**（CEO 報「最近一週 sidebar 排序亂跳」）。

**三角共謀**：(1) pre-navigation save 4 callsites + (2) `scheduleSave` wired live（`3b9f7d8`）+ (3) sort by `updated_at` DESC（`138ae61`）= 純瀏覽就 visible 重排。時間訊號完全吻合「最近一週才開始」。

**修法**：`saveCurrentSession()` 函數入口加 `_sessionDirty` boolean early return（`static/news-search.js:1244-1249` 宣告 + `1690-1699` 入口檢查）：

```javascript
// 宣告（line 1244-1249）
let _sessionDirty = false;

// 入口（line 1690-1699）
function saveCurrentSession() {
    if (!_sessionDirty) {
        return;
    }
    // ... rest of save body
}
```

**Mutate 點清單**（只有「真的產生新內容」才設 `_sessionDirty = true`）：

| 行號 | 位置 | 觸發 |
|------|------|------|
| `3321` / `3509` | search query submit（兩個分支）| 新查詢 = 新內容 |
| `4259` | live research query 送出 | LR 新搜尋 |
| `4391` | research report 完成 | DR 報告完成 |
| `6992` | chat message push | user / assistant 新訊息 |
| `7024` | pin/unpin message | `pinnedMessages` mutate |
| `7231` | pin/unpin news card | `pinnedNewsCards` mutate |
| `9885` | rename session | rename PATCH 之後 |

**Reset 點**：
- `loadSavedSession` 載入時（`static/news-search.js:7981`）：reset 為 `false`（讀已存 session 不算 mutate）。
- `saveCurrentSession` body 結束（`static/news-search.js:1806`）：reset 為 `false`（PUT 已排程，後續純瀏覽不該再觸發）。

**不動 outer 4 callsite 的 if-condition**（保留作 outer gate；inner dirty flag 是 second gate）。Server 端不做 diff 是合理的（YAGNI），client 必須阻止無內容變化的 PUT。

**通則**：「Pre-navigation save」pattern 必須配 dirty flag，否則 outer guard「有 in-memory 內容」永遠 true。`updated_at` 必須對應「使用者真的有新內容」而不是「有東西在記憶體裡」。

**引用**：D-2026-05-01「Pre-Navigation Save 配 Dirty Flag」、`memory/lessons-frontend.md`「三角共謀（sort 亂跳）」、`static/news-search.js`（_sessionDirty 宣告 + saveCurrentSession 入口 + 8 處 mutate 點）。

#### 4.2.6 Cross-User 隔離

**背景**：`localStorage` 是 origin-scoped 不是 user-scoped。同瀏覽器 admin → 登出 → member 登入 → member 看到 admin 的 sessions。VPS 沒人 complain 是因為大家用各自瀏覽器，但 B2B 共用電腦會撞到，是嚴重資料隔離問題。

**已 superseded by D-2026-05-13 Frontend Init Sync Refactor**。早期 9 個 case-by-case patch（local storage clear helper、main-UI reset wrapper、`_handleAuthFailure` 內部清理流程等）已從 codebase 移除，改走 `UserStateSync` 模組統一處理。

**新架構**：cross-user 隔離由 `UserStateSync.clearUserScopedState()` + `assertUserIdentity()` + `UserStateSync.runInitSync()` 三段式處理 — `clearUserScopedState()` 統一清 user-scoped localStorage keys 與 in-memory globals（含 D-2026-05-13 前由 legacy main-UI reset wrapper 涵蓋的 6 個 user-scoped main-UI globals：`_sessionDirty` / `currentArgumentGraph` / `currentChainAnalysis` / `shareContentOverride` / `currentLRSessionId` / `currentAnalyticsQueryId`），`assertUserIdentity()` 在 `cache.user_id !== fresh.user_id` 時拋 `UserStateSyncError` → trigger A 整套 reset，`runInitSync()` 整合 fullReset + `fetchInit()` + `applyInit()` 一次走完。詳見 §10.3。

**引用**：D-2026-05-13「Frontend Init Sync Refactor」、`memory/lessons-frontend.md`「Frontend Init Sync — Architectural Refactor（2026-05-13）」、`docs/in progress/plans/frontend-init-sync-refactor-plan.md`。

### 4.3 右側 Tab 面板系統

#### 4.3.1 Tab 結構

| Tab ID | 面板 ID | 功能 |
|--------|---------|------|
| `sources` | `tabPanelSources` | 來源篩選 (Tree View) |
| `files` | `tabPanelFiles` | 我的檔案 (Tree View) |
| `history` | `tabPanelHistory` | 搜尋紀錄 |
| `pinned-news` | `tabPanelPinnedNews` | 釘選新聞 |

#### 4.3.2 來源篩選 (Source Filter)

採用 VS Code Explorer 風格的 Tree View：

```javascript
function renderSourceTreeView() {
    // 渲染資料夾結構
    // 每個來源顯示：checkbox + 主名稱 + 副資訊 (兩行)
    // 支援拖曳分類、全選/全不選
}
```

**資料結構**：
```javascript
const sourceFolders = [
    {
        id: 'folder-uuid',
        name: '資料夾名稱',
        siteNames: ['site1', 'site2'],
        collapsed: false,
        isUncategorized: false
    }
];
```

#### 4.3.3 我的檔案 (User Files)

```javascript
function renderFileTreeView() {
    // 渲染使用者上傳的檔案
    // 支援 PDF, DOCX, TXT, MD 格式
    // 顯示處理狀態：ready, processing, failed
}
```

**自動開啟**：當使用者勾選「包含文件」checkbox 時，自動呼叫 `openTab('files')` 開啟右側「我的檔案」面板。

### 4.4 搜尋結果區

#### 4.4.1 文章卡片

```javascript
function createArticleCard(article, index) {
    // 建立文章卡片 HTML
    // 包含：標題、來源、日期、摘要、相關性分數
    // 支援：釘選、複製連結
}
```

**文章資料結構**：
```javascript
{
    name: "文章標題",
    url: "https://...",
    description: "文章摘要",
    source: "來源名稱",
    date_published: "2024-01-01",
    relevance_score: 0.95,
    snippet: "AI 生成的相關片段"
}
```

#### 4.4.2 Deep Research 報告 UI 元件

**展開/折疊 Toggle**：報告頂部有單一「全部折疊/全部展開」toggle 按鈕（`addToggleAllToolbar()`），點擊切換所有章節的折疊狀態。

**進度顯示**：Deep Research 進度面板標題為「深度研究進行中」，僅顯示階段名稱（如「搜尋中...」「分析中...」），不顯示技術細節。

**參考資料**：報告末尾的引用來源列表包在可折疊 toggle 中（預設折疊），按鈕顯示「參考資料來源 (N)」，展開後顯示每條來源的完整 Title + URL。

**報告語言**：報告中所有標籤均為中文：
- 模式標籤：`discovery` → 「廣泛探索」、`strict` → 「嚴謹查核」、`monitor` → 「情報監測」（研究模式前端已移除，固定 discovery）
- 欄位標籤：「~~研究模式~~（已移除 2026-03-29）」「分析來源數」「時間範圍」等（「研究發現 N」section headers 已移除）
- 信心度：「High」→「高」、「Medium」→「中」、「Low」→「低」

#### 4.4.3 摘要區

```javascript
function renderAnswerProgressive(answerData, articleCount) {
    // 漸進式渲染 AI 摘要
    // 支援 Markdown 轉 HTML
    // 支援引用連結 [1] → 點擊跳轉
}
```

#### 4.4.3 知識圖譜

```javascript
function displayKnowledgeGraph(kg) {
    // 使用 D3.js 渲染放射式心智圖（radial mind-map）
    // 中心節點 = degree 最高的 entity
    // 其餘按 type 分扇區，極座標排列
    // 支援：圖形視圖 / 列表視圖
    // 支援：展開/收合、隱藏/顯示
    // 支援：click-to-highlight、zoom/pan
}
```

**節點樣式**（品牌色 + 形狀區分）：
- 填色：金 `#FDCB6E` / 淡金 `#FFEAA7` / 白 `#FFFFFF` / 灰 `#B2BEC3`
- 形狀：circle / diamond
- Stroke：統一炭色 `#2D3436`
- 大小：`14 + degree × 4`（上限 40px）

**邊樣式**：
- 中心連接邊：直線放射（箭頭朝外）
- 葉節點間邊：quadratic Bezier 弧線

**節點類型**：
- `person` - 人物
- `organization` - 組織
- `event` - 事件
- `location` - 地點
- `metric` - 指標
- `technology` - 技術
- `concept` - 概念
- `product` - 產品

#### 4.4.4 推論鏈

```javascript
function displayReasoningChain(argumentGraph, chainAnalysis) {
    // 渲染論證圖
    // 顯示：前提 → 推論 → 結論
    // 標示：支持/反對/中立
}
```

### 4.5 資料夾系統

```javascript
// 資料夾頁面管理
function showFolderPage() { ... }
function hideFolderPage() { ... }
function createFolder(name) { ... }
function renameFolder(folderId, newName) { ... }
function deleteFolder(folderId) { ... }
function addSessionToFolder(folderId, sessionId) { ... }
```

**資料結構**：
```javascript
const folders = [
    {
        id: 'folder-uuid',
        name: '專案名稱',
        sessionIds: ['session-1', 'session-2'],
        createdAt: 1704067200000,
        updatedAt: 1704153600000
    }
];
```

### 4.6 使用者回饋系統

```javascript
function openFeedbackModal(rating) {
    // 開啟回饋 Modal
    // rating: 'positive' | 'negative'
    // 支援選擇原因 + 文字留言
}
```

---

## 5. 狀態管理

> **跨章節提示**：所有 user-scoped state 寫入皆透過 `UserStateSync` 7 trigger 為唯一合法入口（D-2026-05-13）。完整 trigger 表 + invariant + 函式定義見 §10.3 Init Sync Architecture。

### 5.1 全域狀態變數

> **v4.0 ownership 更新**：v4.0 完成後 state 不再集中在 news-search.js outer scope，而是分散至各 owner module。以下列出邏輯 state 名稱 + 新 owner module（若已搬移）。存取方式改為 ES module export 函數（getter/setter），不直接讀 outer `let`。

| 狀態 / 變數名 | 類型 | Owner Module | 存取方式 |
|--------|------|------|------|
| `currentMode` | string | `features/mode.js` | `getCurrentMode()` / `setCurrentMode()` |
| `accumulatedArticles` | array | `features/search.js` | `getAccumulatedArticles()` |
| `conversationHistory` | array | news-search.js stub | window global（待 Phase 7+）|
| `sessionHistory` | array | news-search.js stub | `getSessionHistory()`（sessions-list）|
| `chatHistory` | array | `features/chat.js` | `getChatHistory()` / `pushChatHistory()` |
| `savedSessions` | array | news-search.js stub | `window.savedSessions`（每筆含 `_serverId` marker，見 §4.2.3）|
| `_sessionDirty` | boolean | `features/session-manager.js` | `markSessionDirty()` / `isSessionDirty()`（D-V14）|
| `currentLoadedSessionId` | string\|null | news-search.js stub | `window.currentLoadedSessionId` getter |
| `currentConversationId` | string | news-search.js stub | window global |
| `currentResearchReport` | object\|null | `features/research.js` | `getCurrentResearchReport()` |
| `currentArgumentGraph` | array\|null | `features/research.js`（user-scoped）| module getter |
| `currentChainAnalysis` | object\|null | `features/research.js`（user-scoped）| module getter |
| `pinnedMessages` | array | `features/pins.js` | `getPinnedMessages()` |
| `pinnedNewsCards` | array | `features/pins.js` | `getPinnedNewsCards()` |
| `shareContentOverride` | object\|null | `features/sharing.js`（user-scoped）| `getShareContentOverride()` |
| `currentLRSessionId` | string\|null | `features/live-research.js`（user-scoped）| `getLRSessionId()` / `clearLRSessionId()` |
| `lrInProgress` | boolean | `features/live-research.js` | `isLRInProgress()` |
| `folders` | array | `features/folders.js` | `getFolders()` |
| `sourceFolders` | array | `features/folders.js` | `getSourceFolders()` |
| `selectedFileIds` | array | `features/source-filters.js` | `getSelectedFileIds()` |
| `currentAnalyticsQueryId` | string\|null | `utils/analytics.js`（user-scoped）| `getCurrentAnalyticsQueryId()` / `setCurrentAnalyticsQueryId()` |
| `searchGenerationId` | number | news-search.js stub | window global（取消機制用）|
| `availableSites` | array | news-search.js stub | window global |

**User-scoped globals 清除規範**：所有標記 user-scoped 的 state，cross-user 切換時必須**全部**清除。實際做法見 §10.3 — 統一透過 `UserStateSync.clearUserScopedState()` 處理，由 7 個 sync trigger（§10.3 表）作為唯一合法寫入入口。`clearUserScopedState()` 透過 D-V3 backref pattern 清除 live-research state，透過各 module setter 清除其餘 module-owned state。

### 5.2 UI 狀態

| 狀態 | 控制元素 | 說明 |
|------|----------|------|
| 初始狀態 | `#initialState` | 顯示歡迎訊息 |
| 載入中 | `#loadingState` | 顯示 spinner |
| 結果區 | `#resultsSection` | 顯示搜尋結果 |
| 聊天模式 | `#chatContainer` | 顯示聊天介面 |
| 資料夾頁 | `#folderPage` | 顯示資料夾系統 |

---

## 6. API 整合

> **跨章節提示**：user identity 相關狀態 hydration 走 `GET /api/user/init` composite endpoint（D-2026-05-13），由 `UserStateSync.fetchInit()` + `applyInit()` 統一處理。詳見 §10.3。

### 6.1 API 端點列表

| 端點 | 方法 | 說明 | 回應類型 |
|------|------|------|----------|
| `/ask` | GET (SSE) | 新聞搜尋 | SSE Stream |
| `/api/deep_research` | GET (SSE) | Deep Research | SSE Stream |
| `/api/free_conversation` | POST | 自由對話 | SSE Stream |
| `/sites_config` | GET | 取得網站設定 | JSON |
| `/api/user/init` | GET | 一次 round-trip 取回 `{ user, org, role, sessions, shared_sessions, preferences }`（D-2026-05-13，見 §10.3）| JSON |
| `/api/user/upload` | POST | 上傳檔案 | JSON |
| `/api/user/upload/{id}/progress` | GET (SSE) | 上傳進度 | SSE Stream |
| `/api/user/sources` | GET | 取得使用者資料來源 | JSON |
| `/api/user/sources/{id}` | DELETE | 刪除使用者資料來源 | JSON |
| `/api/feedback` | POST | 提交使用者回饋 | JSON |
| `/api/analytics/event` | POST | 分析事件 | JSON |

### 6.2 SSE 串流處理

> **@deprecated（2026-07-05）**：下方 `handleStreamingRequest` 這個 EventSource GET 路徑
> 已**無 live caller**（全 repo 僅自身定義 + docs/comment 引用）。當前 SSE 走
> `handlePostStreamingRequest`（POST fetch reader，見 §6.2 之後段落）。此範例僅存為
> legacy 參考；若未來重新啟用，必須比照 `handlePostStreamingRequest` 補上
> late-message generation gate，否則同一 SSE race 會在此路徑復現。

```javascript
async function handleStreamingRequest(url, query) {
    const eventSource = new EventSource(url);

    eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
        // 根據 data.type 分發處理
    };

    eventSource.onerror = () => {
        eventSource.close();
    };
}
```

### 6.3 搜尋取消機制

```javascript
let searchGenerationId = 0;
let currentSearchAbortController = null;
let currentSearchEventSource = null;

function cancelActiveSearch() {
    searchGenerationId++;
    if (currentSearchEventSource) {
        currentSearchEventSource.close();
    }
    if (currentSearchAbortController) {
        currentSearchAbortController.abort();
    }
}
```

---

## 7. 串流處理

### 7.1 SSE 事件類型

#### 新聞搜尋 (`/ask`)

| 事件類型 | 說明 | 資料結構 |
|----------|------|----------|
| `articles` | 文章列表 | `{ articles: [...] }` |
| `answer_chunk` | 摘要片段 | `{ chunk: "..." }` |
| `reasoning_chunk` | 推論片段 | `{ chunk: "..." }` |
| `done` | 完成 | `{}` |

#### Deep Research (`/api/deep_research`)

| 事件類型 | 說明 | 資料結構 |
|----------|------|----------|
| `clarification` | 澄清問題 | `{ questions: [...] }` |
| `status` | 狀態更新 | `{ message: "..." }` |
| `progress` | 進度更新 | `{ phase: "...", progress: 0.5 }` |
| `report_chunk` | 報告片段 | `{ chunk: "..." }` |
| `knowledge_graph` | 知識圖譜 | `{ nodes: [...], edges: [...] }` |
| `reasoning_chain` | 推論鏈 | `{ argument_graph: {...} }` |
| `sources` | 引用來源 | `{ sources: [...] }` |
| `done` | 完成 | `{ metadata: {...} }` |

### 7.2 漸進式渲染

```javascript
function renderArticlesProgressive(articles) {
    // 1. 先顯示骨架屏 (skeleton)
    // 2. 逐批渲染文章卡片
    // 3. 使用 requestAnimationFrame 優化
}

function renderAnswerProgressive(answerData) {
    // 1. 即時顯示串流文字
    // 2. 完成後轉換 Markdown → HTML
    // 3. 加入引用連結
}
```

### 7.3 SSE Handler — Black-List Approach（5/01 update）

**規則**：`handleStreamingRequest` / `handlePostStreamingRequest` 兩個 SSE switch 的 `default:` branch **必須**保留 `Object.assign(accumulatedData, data)`，**不可改成 white-list 思維**。

**為什麼**：SSE `message_type` 是**開放集**（server 隨時可加新類型）。
- 如果 `default:` 改成 warn-and-ignore（white-list），任何新加的 message_type（包含送 final answer 的 `nlws`）會被當 unknown 丟掉 → `accumulatedData = {}` → UI 顯示空白（commit `d910819` 把 default 改成 ignore，line `nlws` 被 ignore → CEO 看到「抱歉，我無法回答這個問題」+ session 也是空的；hotfix `1782e76` 改回）。
- Unknown 預設 **merge** 比 ignore 安全。新加的 final-result 類型不需改前端就 work。

**過濾中間 envelope 的正確做法**：用獨立 `case 'X': break;` 跳過，不要動 `default:`。

```javascript
// static/js/features/search.js（handleStreamingRequest GET switch + handlePostStreamingRequest POST switch；原 news-search.js 已於 v4.0 Commit 14b 遷移）
// Server-side intermediate envelopes — explicit skip so they
// do NOT fall through to the default Object.assign below.
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
    // Loud log so we never silent-fail.
    console.warn('[SSE] default merge for message_type:', data.message_type, data);
    Object.assign(accumulatedData, data);
    break;
```

**前後端黑名單字串對齊**：前端兩個 switch 的 case 清單必須跟 server 端 `_BAD_MESSAGE_TYPES` 一致（兩邊不同步會造成 server 過濾掉前端沒過濾的、或反之）。

| 端 | 位置 | 集合 |
|----|------|------|
| 前端 | `static/js/features/search.js`（`handleStreamingRequest` GET SSE + `handlePostStreamingRequest` POST SSE 兩個 switch）| `case 'X':` 清單 |
| Server | `code/python/core/session_service.py:600-610`（`_BAD_MESSAGE_TYPES` frozenset）| 用於 `_sanitize_session_history` 在 create / update / migrate 時過濾 |

**Server `_BAD_MESSAGE_TYPES`（7/05 完整清單；`low_relevance_warning` / `low_keyword_match_warning` / `empty_results` 在前端是 render case 非 skip case，列入 server 端屬 defense-in-depth）**：
```python
{
    'asking_sites', 'tool_selection', 'decontextualization',
    'pre_check_results', 'site_querying', 'tool_routing',
    'research_phase', 'intermediate_result', 'progress',
    'begin-nlweb-response', 'end-nlweb-response', 'complete',
    'error', 'remember', 'time_filter_relaxed',
    'author_search_no_results', 'clarification_required',
    'low_relevance_warning', 'low_keyword_match_warning',
    'empty_results',
}
```

**Defense-in-depth（跨層 boundary）**：
1. **前端 SSE handler** 黑名單 case 跳過（不污染 `accumulatedData`）。
2. **Server `_sanitize_session_history`**：create / update / migrate 時 filter（防 regression 把 envelope 寫進 PG）。
3. **一次性 cleanup script**：`code/python/scripts/cleanup_polluted_session_history.py` 清歷史污染。

**通則**：
- SSE `message_type` 開放集，unknown 預設 merge 比 ignore 安全 — black-list 思維才正確。
- 寫 plan / 跟 subagent 溝通時若用「ignore-list」字眼，**必須明寫是 black-list 還是 white-list**，避免 subagent 解讀錯方向。
- 跨層 boundary 的訊息（server SSE → client → server PG）每層都該驗證 / 過濾，不要假設上游送的是乾淨的。

**引用**：D-2026-05-01「SSE Handler default branch 必須保留 `Object.assign`（black-list approach）」、`memory/lessons-frontend.md`「SSE handler default branch 必須保留 Object.assign」「Server 把 SSE 中間訊息（asking_sites）寫進 PG sessionHistory」、commits `d910819`（white-list 錯誤）→ `1782e76`（hotfix 改回）+ `7119da0`（server sanitize）+ `1ca2cb1`（cleanup script）。

---

## 8. 樣式系統

> **2026-06-16 — Icon emoji→SVG（commit 173aad89）**：UI icon 已由 emoji 全面替換為 Rika 設計師 SVG icon，並新增聊天角色頭像。本節若有 emoji icon 相關描述均以 SVG 實作為準。

### 8.1 CSS 變數

```css
:root {
    /* 品牌色（讀豹金炭主題） */
    --color-primary: #FDCB6E;
    --color-primary-hover: #d4a84b;
    --color-primary-light: #FFEAA7;
    --color-primary-bg: #FFEAA7;

    /* 文字 */
    --color-text: #2D3436;
    --color-text-secondary: #2D3436;
    --color-text-tertiary: #636e72;
    --color-text-muted: #B2BEC3;

    /* 背景 */
    --color-bg: #FFFFFF;
    --color-bg-hover: #FFFBF0;
    --color-bg-card: #FFFDF5;
    --color-bg-section: #FFF8E1;

    /* 邊框 */
    --color-border: #B2BEC3;
    --color-border-light: #dfe6e9;

    /* 狀態 */
    --color-success: #059669;
    --color-danger: #dc2626;

    /* 陰影 */
    --shadow-sm: 0 1px 3px rgba(0,0,0,0.1);
    --shadow-md: 0 4px 12px rgba(0,0,0,0.12);
}
```

### 8.2 按鈕與 Toggle 狀態規範（2026-03-29 制定）

所有可切換狀態的按鈕/tab/toggle 遵循以下通用規則。設計師（小龜）確認此規則適用全站，後續 spec 不會重複標註。

#### 規則

| 所在區域背景 | Default 狀態 | Active/Selected 狀態 |
|------------|-------------|---------------------|
| **白色背景** | `rgba(255,255,255,0.5)` 半透明白 | `#FFEAA7` 淺黃 |
| **淺黃背景（#FFEAA7）** | `#FFEAA7` 淺黃 | `#FDCB6E` 深黃 + `2px solid #2D3436` 黑框 |

#### 例外

| 元素 | 原因 | Default | Active |
|------|------|---------|--------|
| 搜尋結果 tabs（新聞列表/時間軸/深度研究報告） | 主要導航 tab，需要高辨識度 | 半透明白 | `#FDCB6E` 深黃 + 黑框 |
| 展開摘要按鈕 | 與搜尋結果 tabs 太近但大小不同，需區隔 | 半透明白 | `#FFEAA7` 淺黃 |
| 搜尋按鈕 `.btn-search` | 主要行動按鈕（CTA） | `#2D3436` 黑底白字 | 不變 |
| 深色背景上的按鈕（settings popover 等） | 背景已是深色，不適用白/黃規則 | 維持現行設計 | 維持現行設計 |

#### 適用元素清單

**已符合規範（不需改）**：
- 模式切換 `.mode-btn-inline`（淺黃底）
- 右 sidebar tabs `.right-tab-label`（淺黃底）
- 左 sidebar session tabs `.left-sidebar-sessions-tab`（淺黃底）
- 資料夾排序 tabs `.folder-sort-tab`（白底→已套用）
- 新增資料夾按鈕 `.folder-add-btn`
- 搜尋按鈕 `.btn-search`

**需要調整（白底區域 → 半透明白 default + 淺黃 active）**：
- 搜尋結果 tabs `.tab`（default 改半透明白，active 維持深黃 — 例外）
- 展開摘要 `.btn-toggle-summary`（active 用淺黃 — 例外）
- 分享 Modal `.btn-copy`
- 大字體 `.btn-font-size`
- 上傳檔案 `.btn-upload-inline`
- 回饋 `.btn-feedback`
- Clarification `.option-chip`
- 來源篩選工具列 `.tree-toolbar-btn`
- KG 控制 `.kg-toggle-button` `.kg-hide-btn`
- KG 檢視切換 `.kg-view-btn`
- 文章釘選按鈕（JS 動態生成）
- DR 報告折疊 toggle（JS 動態生成）

**維持現行設計（CEO 確認 2026-03-29）**：
- DR popup checkbox `.advanced-option-row` — default 白底 + checked 深黃（按 Figma spec）
- 資料夾卡片 `.folder-card` — default 白底 + selected 淺黃
- 包含文件 checkbox `.user-files-toggle` — 維持 checkbox 樣式
- Settings popover `.popover-menu-item` — 深色背景，維持現行設計

### 8.3 響應式斷點

| 斷點 | 寬度 | 說明 |
|------|------|------|
| Desktop | > 1200px | 完整三欄佈局 |
| Tablet | 768px - 1200px | 隱藏左側邊欄 |
| Mobile | < 768px | 單欄佈局 |

### 8.3 大字體模式

```css
body.large-font {
    font-size: 18px;
}

body.large-font .search-input {
    font-size: 18px;
}

body.large-font .news-card-title {
    font-size: 18px;
}

/* Deep Research 報告也受大字體影響 */
body.large-font .research-section-header .section-title {
    font-size: 20px;
}

body.large-font .research-section-content {
    font-size: 17px;
}
```

---

## 9. 事件處理

### 9.1 事件委派

```javascript
// 文章連結點擊追蹤
document.addEventListener('click', handleLinkClick);
document.addEventListener('auxclick', handleLinkClick);
document.addEventListener('contextmenu', handleLinkClick);

// Tab 面板切換
document.querySelectorAll('.right-tab-label').forEach(tab => {
    tab.addEventListener('click', () => openTab(tab.dataset.tab));
});
```

### 9.2 鍵盤快捷鍵

| 快捷鍵 | 功能 |
|--------|------|
| `Enter` | 送出搜尋 (搜尋框) |
| `Shift + Enter` | 換行 (搜尋框) |
| `Escape` | 關閉 Popup / 取消搜尋 |

### 9.3 拖曳功能

```javascript
// 來源篩選拖曳分類
container.querySelectorAll('.tree-item[draggable="true"]').forEach(item => {
    item.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('text/site-name', item.dataset.siteName);
    });
});

// Session 拖曳到資料夾
function initSidebarDragDelegation() {
    // 支援拖曳 session 到資料夾
}
```

---

## 10. LocalStorage 資料結構

### 10.1 儲存的 Key

| Key | 說明 | 資料類型 |
|-----|------|----------|
| `taiwanNewsSavedSessions` | 已儲存的 sessions | JSON Array |
| `taiwanNewsFolders` | 資料夾列表 | JSON Array |
| `taiwanNewsSourceFolders` | 來源分類資料夾 | JSON Array |
| `taiwanNewsFileFolders` | 檔案分類資料夾 | JSON Array |
| `taiwanNewsSelectedFiles` | 已選取的檔案 | JSON Array |
| `taiwanNewsSessionsMigrated` | localStorage → server 遷移完成 flag | boolean |
| `lastUserId` | 最近一次登入的 user_id（供 cross-user 偵測比對）| string |
| `nlweb_large_font` | 大字體模式（device-scoped）| boolean |
| `nlweb_kg_hidden` | 知識圖譜隱藏（device-scoped）| boolean |
| `nlweb_session_id` | Session ID | string |
| `authUser` | 登入使用者基本資料 | JSON |
| `authAccessToken` | 存取 token | string |

**User-scoped vs Device-scoped（5/01 update）**：

`AuthManager.USER_SCOPED_KEYS` 列出 6 個須在 cross-user 切換 / logout / auth-failure 時清除的 key（含 `taiwanNewsSavedSessions`、`taiwanNewsFolders`、`taiwanNewsSourceFolders`、`taiwanNewsFileFolders`、`taiwanNewsSelectedFiles`、`taiwanNewsSessionsMigrated`）。`nlweb_large_font`、`nlweb_kg_hidden` 等 **device-scoped UI 偏好刻意不在清除清單**，跨用戶共用裝置仍保留偏好。詳見 §4.2.6 Cross-User 隔離。

### 10.2 Session 資料結構

```javascript
{
    id: 'session-uuid',
    title: '搜尋標題',
    timestamp: 1704067200000,
    mode: 'search',
    queries: [
        {
            query: '使用者問題',
            answer: 'AI 回答',
            articles: [...],
            knowledgeGraph: {...},
            reasoningChain: {...}
        }
    ]
}
```

### 10.3 Init Sync Architecture（2026-05-13 update）

> **背景**：取代 2026-04-29 ~ 05-08 之間 9 個 case-by-case cross-user leak patch（commits `5ff8947` → `e0b5a41`），改為 single sync flow。詳見 D-2026-05-13 + `docs/in progress/plans/frontend-init-sync-refactor-plan.md` + `memory/lessons-frontend.md`「Frontend Init Sync — Architectural Refactor（2026-05-13）」段。

**核心 invariant**：`cache.user_id == JWT.user_id`。前端 user-scoped state 只允許在以下 7 個 sync trigger 透過 `UserStateSync` module 寫入，其他寫入點視為 bug：

| Trigger | 偵測時機 | 行為 |
|---|---|---|
| **A. Login / Onboarding** | `login()` 收 200 + 新 JWT、`completeOnboarding()` 成功 | fullReset → `fetchInit()` → `applyInit()` |
| **B. User identity change** | `checkAuthOnLoad()` 收 `/api/auth/me` 200 且 `data.user.id !== cached.id` | 同 A |
| **C. Logout** | `logout()` / admin force logout | fullReset → show login modal（不 fetch） |
| **D. 401 / refresh fail** | `authenticatedFetch` 收 401 且 refresh 失敗 | 同 C |
| **E. Session click** | sidebar / popup / folder detail `.left-sidebar-session-item` click | `GET /api/sessions/{id}` 拉完整內容並 hydrate，不從 cache 讀；不清 user-scoped state（同 user） |
| **F. Page reload / tab visible** | `DOMContentLoaded` checkAuthOnLoad、`document.visibilitychange === 'visible'` | mismatch → 走 A；match → `fetchInit()` soft refresh sessions / shared |
| **G. SSE envelope** | `handleStreamingRequest` / `handlePostStreamingRequest` 每個 onmessage | envelope `data.user_id` ≠ `authManager._user.id` → abort stream + trigger F |

**`UserStateSync` module 三函式**（`static/js/core/state-sync.js`，v4.0 commit 11 搬入，~335 行）：
- `clearUserScopedState({ keepInviteToken })` — 統一清光 A+B+C+D+E+F 範圍的 user-scoped state（device-scoped UI prefs 不動）
- `fetchInit()` — 呼叫 `GET /api/user/init`，一次拿回 `{ user, org, role, sessions, shared_sessions, preferences }`
- `applyInit(initPayload)` — 把 init payload hydrate 進 in-memory caches + render UI

**Convenience helpers**：`UserStateSync.fullReset()`（= `clearUserScopedState` + reset main UI）+ `UserStateSync.runInitSync({ keepInviteToken })`（= `fullReset` + `fetchInit` + `applyInit`，含 in-flight guard，dedupe 並行呼叫共用同一 Promise）。

**`assertUserIdentity(cached, fresh)` helper** — mismatch 拋 `UserStateSyncError`（code=`MISMATCH` / `MISSING_CACHED` / `MISSING_FRESH`），caller 必須 `try/catch` 後 trigger A 整套 reset。

**Backend composite endpoint**：`GET /api/user/init`（`code/python/webserver/routes/user_init.py`，含完整 `setup_user_init_routes` 註冊）— 必要層（user/org/role/sessions/shared_sessions）+ lazy 層（preferences），避免 5 個 round-trip。**228a93a 補的 user shape contract**：user payload 含 `org_id` + `role`，mirror `/api/auth/login` + `/api/auth/me`。Backend variant：`register_user()` / `activate_user()` 成功後 auto-issue refresh cookie（commit `2ee5508`），onboarding 完成跳 `/` 後 `checkAuthOnLoad` 才能拿到 JWT 走 trigger A。

**Defense-in-depth（不取代 Init Sync 主流，僅作補強層）**：以下機制保留作為 regression 防護，但**正常 user-scoped state 寫入永遠走上述 7 trigger，不應依賴 defense-in-depth 處理**：

| 機制 | 角色 |
|------|------|
| `_sessionDirty` flag | save dirty flag，阻止「純瀏覽」觸發無內容 PUT（見 §4.2.5） |
| Server-side `_sanitize_session_history` classmethod | create / update / migrate 時過濾 intermediate envelope，防 SSE black-list regression 寫進 PG |
| `list_sessions` `ORDER BY updated_at DESC` | server 統一排序，render 端不假設 array order（見 §4.2.2） |
| Shared session `_isShared` tag + `saveCurrentSession` 早退（Y-1 fix） | shared session click 不 spawn 自己的「未命名搜尋」row（見 §4.2.4） |

詳見 D-2026-05-13「保留清單」段。

---

## 附錄 A：函數索引

> **v4.0 注意**：函數所在位置已從單體 news-search.js 分散至 21 個 ES module。標注模組位置；news-search.js 殘留的保留函數以「stub」標記。

### 搜尋相關
- `performSearch()` - 執行新聞搜尋（`features/search.js`）
- `performDeepResearch()` - 執行 Deep Research（`features/deep-research.js`）
- `performFreeConversation()` - 執行自由對話（news-search.js stub）
- `cancelActiveSearch()` - 取消搜尋（news-search.js stub）
- `handleStreamingRequest()` - **@deprecated** legacy EventSource GET SSE，無 live caller（當前 SSE 走 `handlePostStreamingRequest`）
- `performLiveResearch()` - 執行 Live Research（`features/live-research.js`）
- `continueLiveResearch()` - 繼續 Live Research（`features/live-research.js`）

### 渲染相關
- `createArticleCard()` - 建立文章卡片（news-search.js stub）
- `renderArticlesProgressive()` - 漸進式渲染文章（news-search.js stub）
- `renderAnswerProgressive()` - 漸進式渲染摘要（news-search.js stub）
- `displayKnowledgeGraph()` - 顯示知識圖譜（`features/knowledge-graph.js`）
- `displayReasoningChain()` - 顯示推論鏈（news-search.js stub）
- `renderSourceTreeView()` - 渲染來源 Tree View（news-search.js stub）
- `renderFileTreeView()` - 渲染檔案 Tree View（`features/file-kb.js`）
- `addToggleAllToolbar()` - Deep Research 報告全部展開/折疊 toggle（news-search.js stub）
- `generateCitationReferenceList()` - 報告末尾可折疊引用來源列表（news-search.js stub）
- `updateReasoningProgress()` - Deep Research 進度顯示（news-search.js stub）
- `togglePrivateSources()` - 切換包含文件（自動開啟檔案面板）（`features/source-filters.js`）
- `renderLeftSidebarSessions()` - 渲染左 sidebar session 列表（`features/sessions-list.js`）
- `renderSharedSessions()` - 渲染組織空間 sessions（`features/sessions-list.js`）

### Session 管理
- `saveCurrentSession()` - 儲存目前 session（news-search.js stub，入口檢查 `isSessionDirty()` via session-manager）
- `loadSavedSession()` - 載入 session（news-search.js stub，hydrate 補 `_serverId`，reset dirty flag）
- `deleteSavedSession()` - 刪除 session（news-search.js stub）
- `resetConversation()` - 重置對話（news-search.js stub）
- `markSessionDirty()` / `clearSessionDirty()` / `isSessionDirty()` - dirty flag API（`features/session-manager.js`，D-V14 owner）
- `UserStateSync.clearUserScopedState()` - 清 user-scoped localStorage keys + in-memory globals（`core/state-sync.js`，D-2026-05-13）
- `UserStateSync.fetchInit()` - 呼叫 `GET /api/user/init` composite endpoint（`core/state-sync.js`，D-2026-05-13）
- `UserStateSync.applyInit()` - hydrate init payload 進 in-memory caches + render UI（`core/state-sync.js`，D-2026-05-13）
- `UserStateSync.fullReset()` - clearUserScopedState + reset main UI 便利函式（`core/state-sync.js`）
- `UserStateSync.runInitSync()` - fullReset + fetchInit + applyInit，含 in-flight guard（`core/state-sync.js`）
- `hydrateAuthUser()` - auth user hydration helper（`core/state-sync.js`，exported v4.0 commit 11）
- `SessionManager.scheduleSave()` - debounced 2s PUT/POST（`features/session-manager.js`）
- `SessionManager._cancelPendingSave()` - cancel pending PUT timer（`features/session-manager.js`）
- `SessionManager._postedRecently` - 5 秒重複 POST detector，最後防線（`features/session-manager.js`）
- `AuthManager._handleAuthFailure()` - logout / 401 / refresh fail 入口（`core/auth-manager.js`，轉呼叫 UserStateSync trigger C/D）
- `showAuthModal()` / `hideAuthModal()` / `updateAuthUI()` - Auth DOM 操作（`core/auth-ui.js`，v4.0 NEW）
- `initSessionCoordinator()` - 跨模組 session 協調初始化（`core/session-coordinator.js`，v4.0 NEW）

### 資料夾系統
- `getFolders()` / `getSourceFolders()` / `clearPreFolderState()` - Folder state 存取（`features/folders.js`）
- `showFolderPage()` - 顯示資料夾頁面（news-search.js stub）
- `createFolder()` / `renameFolder()` / `deleteFolder()` - Folder CRUD（`features/folders.js`）

### UI 控制
- `openTab()` - 開啟 Tab（news-search.js stub，window bridge via main.js）
- `closeAllTabs()` - 關閉所有 Tab（news-search.js stub）
- `showHistoryPopup()` - 顯示歷史搜尋（news-search.js stub）
- `setProcessingState()` - 設定處理中狀態（news-search.js stub，DOM-coupled）
- `bootstrapPage()` - 頁面初始化序列（`core/page-bootstrap.js`）

---

## 附錄 B：事件類型常數

```javascript
// SSE 事件類型
const SSE_EVENT_TYPES = {
    ARTICLES: 'articles',
    ANSWER_CHUNK: 'answer_chunk',
    REASONING_CHUNK: 'reasoning_chunk',
    CLARIFICATION: 'clarification',
    STATUS: 'status',
    PROGRESS: 'progress',
    REPORT_CHUNK: 'report_chunk',
    KNOWLEDGE_GRAPH: 'knowledge_graph',
    REASONING_CHAIN: 'reasoning_chain',
    SOURCES: 'sources',
    DONE: 'done',
    ERROR: 'error'
};

// 搜尋模式
const SEARCH_MODES = {
    SEARCH: 'search',
    DEEP_RESEARCH: 'deep_research',
    CHAT: 'chat'
};

// 研究模式
// Shelved 2026-03-29 — frontend no longer exposes mode selection
const RESEARCH_MODES = {
    DISCOVERY: 'discovery',
    STRICT: 'strict',
    MONITOR: 'monitor'
};
```

---

*最後更新：2026-05-25（Frontend Modular Refactor v4.0 Path A++ 完成：§2 檔案結構全面重寫 + §2.4 D-V3/D-V6/D-V14/AC contracts 新增 + §1 架構概覽更新 + §10.3 UserStateSync 位置更新 + 附錄 A 函數模組位置標注）*
