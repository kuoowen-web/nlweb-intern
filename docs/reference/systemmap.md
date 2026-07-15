# NLWeb 系統總覽

## 概述
NLWeb 是自然語言搜尋系統，提供智慧查詢處理、多源檢索與 AI 驅動的回應生成。系統由 Python 後端透過 HTTP/HTTPS 服務現代 JavaScript 前端。

---

## 模組總覽

系統分為 7 個主要模組（M0-M6）：

### M0: Indexing（索引與數據）🟢 完成
**目標**：高可信資料工廠。自動化擷取、清洗、驗證到分級儲存。

| 元件 | 狀態 | 檔案 | 說明 |
|------|------|------|------|
| **PostgreSQL + pgvector** | ✅ | `retrieval_providers/postgres_client.py` | 語意檢索 + pg_bigm 全文混合檢索 |
| **Crawler Engine** | ✅ | `crawler/core/engine.py` | 爬蟲引擎（async 支援）|
| **Parser Factory** | ✅ | `crawler/parsers/factory.py` | 7 個 Parser（ltn, udn, cna, moea, einfo, esg, chinatimes） |
| **Chunking Engine** | ✅ | `indexing/chunking_engine.py` | 170 字/chunk + Extractive Summary |
| **Quality Gate** | ✅ | `indexing/quality_gate.py` | 長度、HTML、中文比例驗證 |
| **Ingestion Engine** | ✅ | `indexing/ingestion_engine.py` | TSV → CDM 解析 |
| **Source Manager** | ✅ | `indexing/source_manager.py` | 來源分組（Tier 1-4，indexing 層分塊用途，**非**已廢除的 reasoning 權威分級） |
| **Vault Storage** | ✅ | `indexing/dual_storage.py` | SQLite + Zstd 壓縮（線程安全）|
| **Rollback Manager** | ✅ | `indexing/rollback_manager.py` | 遷移記錄、payload 備份 |
| **Indexing Pipeline** | ✅ | `indexing/pipeline.py` | 主流程 + 斷點續傳 |
| **Embedding Module** | ✅ | `indexing/embedding.py` | 向量生成（Qwen3-Embedding-4B, INT8, 1024D）|
| **PostgreSQL Uploader** | ✅ | `indexing/postgresql_uploader.py` | 向量寫入 PostgreSQL（pgvector）|
| **Bulk Load** | ✅ | `indexing/bulk_load.py` | GCS JSONL+NPY → VPS PostgreSQL 批次載入 |
| **Subprocess Runner** | ✅ | `crawler/subprocess_runner.py` | Crawler 子進程入口（GIL 隔離）|
| **Dashboard API** | ✅ | `indexing/dashboard_api.py` | 索引化監控 API（subprocess 管理）|

**部署現況**（2026-06-26 帳單實證）：GCP 常駐**一台 E2 CPU VM**（台灣 asia-east1-b）跑 **daily 爬蟲（crawler）**，實付約 **US$177/月**（該 VM 無 GPU、不跑 embedding）。新文章 embedding 在 **NVIDIA L4 GPU 上跑（比利時 europe-west1，按需開機非常駐**，8 個月累計 ~200 GPU·hr），GPU 名目費用目前由 GCP credit 折抵、**實付趨近 $0**。embedding 模型 Qwen3-Embedding-4B INT8 / 1024D。線上 query-time embedding 另走 OpenRouter API（fallback DeepInfra），兩者為同一模型 qwen3-embedding-4b（1024D）。2026-03 全量首次 indexing 為一次性歷史批次（Free Trial）。

### M1: Input（入口與安全）🟢 完成
**目標**：安全閘道。攔截惡意指令、多模態資料整合、意圖識別。

| 元件 | 狀態 | 檔案 | 說明 |
|------|------|------|------|
| Prompt Guardrails | ✅ | `core/query_analysis/prompt_guardrails.py` | 防 Prompt Injection（Phase 1+2 完成）|
| Guardrail Logger | ✅ | `core/guardrail_logger.py` | 異常記錄與稽核 |
| Upload Gateway | ❌ | `input/upload_gateway.py` | OCR/ETL，PDF/Word 導入 |
| Query Decomposition | ✅ | `core/query_analysis/analyze_query.py` | 複雜問題拆解子查詢 |

### M2: Retrieval（檢索）🟡 部分完成
**目標**：搜尋引擎核心。整合內部索引、Web Search 與多來源資料。

| 元件 | 狀態 | 檔案 | 說明 |
|------|------|------|------|
| Internal Search | ✅ | `core/retriever.py`, `retrieval_providers/postgres_client.py` | pg_bigm + pgvector 混合檢索（PostgreSQL）|
| Web Search | 🟡 | `retrieval_providers/google_search_client.py` | Reasoning gap resolution 用，非主路徑 |
| Private Docs Search | ✅ | `core/user_data_retriever.py`, `retrieval_providers/user_postgres_provider.py` | 用戶上傳私有文件搜尋 |
| Multi-search Integrator | ❌ | - | 多來源整合（未實作）|

**🔄 Major Upgrade — Pre-Retrieval 升級方向**：

| 升級概念 | 狀態 | 說明 |
|----------|------|------|
| **Association Layer 位置** | 📋 計畫中 | B→A→B' loop 引擎位於 Query Decomposition 之後、Retrieval 之前。先建 Context Map (B)，再衍生 Search Plan (A)，取代目前 reactive 的 Gap Detection 為 proactive retrieval 策略 |
| **Web Search 主動化** | 📋 計畫中 | 現有 Web Search 僅 gap-resolution-triggered，升級為 Propose-Verify 主動 retrieval（Google Search + HTTP Scrape + trafilatura）|

### M3: Ranking（排序）🟢 完成
**目標**：確保 Reasoning 接收最適合結果。結合規則、XGBoost 與 MMR。

Pipeline：Hybrid Retrieval → LLM Ranking → XGBoost (shadow mode) → MMR

| 元件 | 狀態 | 檔案 | 說明 |
|------|------|------|------|
| LLM Ranking | ✅ | `core/ranking.py` | LLM 相關性評分 + Query 類型權重調整 |
| XGBoost Ranking | ✅ | `core/xgboost_ranker.py` | ML 特徵排序（shadow mode）|
| MMR | ✅ | `core/mmr.py` | 多樣性與相關性平衡 |
| Post-Ranking | ✅ | `core/post_ranking.py` | 最終排序調整 |

### M4: Reasoning（推論）🟢 完成 + LR v15 完成 2026-05-19~20（spec v0.大）→ 🔄 Major Upgrade 擴充中
**目標**：核心大腦。Evidence chain、Gap detection、Iterative search、知識圖譜。

| 元件 | 狀態 | 檔案 | 說明 |
|------|------|------|------|
| Orchestrator | ✅ | `reasoning/orchestrator.py` | 核心狀態機，Actor-Critic 循環 |
| Clarification Agent | ✅ | `reasoning/agents/clarification.py` | 歧義解析，選項生成 |
| Time Range Extractor | ✅ | `core/query_analysis/time_range_extractor.py` | 時間範圍解析 |
| Analyst Agent | ✅ | `reasoning/agents/analyst.py` | 知識圖譜、Gap Detection |
| Critic Agent | ✅ | `reasoning/agents/critic.py` | 品質守門員 + CoV 事實查核 |
| Writer Agent | ✅ | `reasoning/agents/writer.py` | 格式化輸出、引用標註 |
| CoV Prompts | ✅ | `reasoning/prompts/cov.py` | Chain of Verification 提示 |
| Free Conversation | ✅ | `methods/generate_answer.py` | Deep Research 後續 Q&A |
| KG & Gap Detection | 🟡 | `reasoning/agents/analyst.py` | 整合在 Analyst 內 |
| **Live Research (LR v15)** | ✅ | `methods/live_research.py`, `reasoning/live_research/orchestrator.py`, `reasoning/live_research/loop_engine.py`, `reasoning/live_research/stages/` | 6-Stage dialog loop + DR citation pipeline port + TypeAgent typed action dispatcher（取代舊 string parser），完成 2026-05-19~20，spec `docs/specs/live-research-spec.md` v0.大 |

**Major Upgrade 落地狀態**（原始計畫已歸檔：`docs/archive/plans/major-upgrade-plan.md`；已落地部分的現行實況見 `reasoning-spec.md` / `live-research-spec.md`）：

| 升級概念 | 狀態 | 說明 |
|----------|------|------|
| **Composable Pipeline Refactor** | ✅ 已落地 | `run_research()` 為 dispatcher，路由至 4 個 phase methods（filter_and_prepare → actor_critic_loop → writer → format_result）+ ResearchState dataclass；`composable_pipeline: true` 現行 prod。見 `reasoning-spec.md` §C/§9 |
| **Association Layer（B→A→B' Loop）** | ✅ 已落地（LR 形式） | 以 LR `BABLoopEngine`（`reasoning/live_research/loop_engine.py`）落地——Stage 1 全域 ContextMap + Stage 2 per-section 共用 B→A→B' 迴圈。見 `live-research-spec.md` |
| **Critic Agent 擴充** | 📋 計畫中 | 新增 `review_consistency()`（code 零命中，仍未實作）— 檢查 research 方向是否 align 初始架構（master B drift 偵測）。新 output channel：讀豹對話轉折訊息。不新建組件 |
| **Propose-Verify Pattern** | 📋 計畫中 | LLM knowledge = falsifiable hypothesis → verify 後才進 candidate list。仍未實現（LR spec §9：現僅 prompt 層 reminder，reuse CoV backward-looking） |
| **Mini-Reasoning per Step** | 📋 計畫中 | 每步驟是一個 mini reasoning module（internal Actor-Critic）。Hierarchical: MetaOrchestrator → StepOrchestrator → 現有 DeepResearchOrchestrator（reuse）。未實作 |
| **Association Agent** | ✅ 已落地 | `reasoning/agents/associator.py`（Live 研究專用 AssociatorAgent） |

### M5: Output（輸出與介面）🟡 部分完成 + Frontend Modular Refactor v4.0 Path A++ 完成 2026-05-25（D-2026-05-25）→ 🔄 Major Upgrade 擴充中
**目標**：推論可視化、儀表板與協作管理。

| 元件 | 狀態 | 檔案 | 說明 |
|------|------|------|------|
| API Gateway | ✅ | `webserver/aiohttp_server.py` | 路由、驗證、流控 |
| Frontend UI | ✅ | `static/news-search-prototype.html`, `static/news-search.js` (3420 LOC stub), `static/news-search.css` | 對話、引用、模式切換。原生 HTML/JS/CSS 分離（D-2025-12）。Modular Refactor v4.0 Path A++ 完成 2026-05-25：21 module surfaces，ES module 架構（`type="module"`），UserStateSync IIFE 搬到 state-sync.js，24 user-scoped let 分散至 8 owner modules，3420 LOC stub（-71%）|
| LLM Safety Net | ❌ | `output/llm_safety_net.py` | 輸出過濾 PII/有害內容 |
| Visualizer Engine | ❌ | `output/visualizer_engine.py` | 推論鏈 Tree View |
| Graph Editor | ❌ | `output/graph_editor.py` | 知識圖譜編輯 |
| Dashboard UI | ❌ | `output/dashboard_ui.py` | 數據看板 |
| Export Service | 🟡 | - | Word/PPT/Excel 匯出 |

#### Frontend 模組架構（v4.0 Path A++ — 2026-05-25）

HTML 載入方式：`<script type="module" src="static/js/main.js">` — ES module（defer semantics，取代 classic script）。

##### 21 Module Surfaces 總覽

**core/ (5 modules)**

| 模組 | 檔案 | 主要 Exports | 說明 |
|------|------|-------------|------|
| auth-manager | `static/js/core/auth-manager.js` | `authManager`, `AuthManager` | JWT auth, login/logout, token refresh |
| auth-ui (NEW) | `static/js/core/auth-ui.js` | `showAuthModal`, `hideAuthModal`, `updateAuthUI` | Auth 相關 DOM 操作 |
| session-coordinator (NEW) | `static/js/core/session-coordinator.js` | `initSessionCoordinator` | 跨模組 session 協調 |
| state-sync | `static/js/core/state-sync.js` | `UserStateSync`, `UserStateSyncError`, `assertUserIdentity`, `hydrateAuthUser`, `injectStateSyncBackref` | UserStateSync IIFE（228 行），init sync 7 個 trigger |
| page-bootstrap | `static/js/core/page-bootstrap.js` | `bootstrapPage` | DOMContentLoaded 初始化序列 |

**features/ (14 modules)**

| 模組 | 檔案 | 主要 Exports | 說明 |
|------|------|-------------|------|
| mode | `static/js/features/mode.js` | `getCurrentMode`, `setCurrentMode` | search/chat/LR mode state |
| search | `static/js/features/search.js` | `performSearch`, `getAccumulatedArticles`, ... | 搜尋流程 + 文章 state |
| chat | `static/js/features/chat.js` | `getChatHistory`, `pushChatHistory`, ... | 對話 history state |
| pins | `static/js/features/pins.js` | `getPinnedMessages`, `getPinnedNewsCards`, ... | 釘選功能 |
| research | `static/js/features/research.js` | `getCurrentResearchReport`, ... | Deep Research report state |
| sharing | `static/js/features/sharing.js` | `getShareContentOverride`, ... | 分享功能 |
| live-research | `static/js/features/live-research.js` | `isLRInProgress`, `getLRSessionId`, `clearLRSessionId`, `performLiveResearch`, `continueLiveResearch` | LR 功能 + 其 state pair |
| folders | `static/js/features/folders.js` | `getFolders`, `getSourceFolders`, `clearPreFolderState`, ... | Folder CRUD + state |
| source-filters (NEW) | `static/js/features/source-filters.js` | `getSelectedFileIds`, ... | 私有檔案來源篩選 |
| deep-research (NEW) | `static/js/features/deep-research.js` | `performDeepResearch`, ... | Deep Research 執行器 |
| knowledge-graph (NEW) | `static/js/features/knowledge-graph.js` | KG render + edit ops | 知識圖譜渲染與編輯 |
| file-kb (NEW) | `static/js/features/file-kb.js` | `loadUserFiles`, ... | 私有知識庫檔案管理 |
| sessions-list | `static/js/features/sessions-list.js` | `renderLeftSidebarSessions`, `renderSharedSessions`, `hydrateFromSoftRefreshInit` | Session sidebar 渲染 |
| session-manager | `static/js/features/session-manager.js` | `sessionManager`, `markSessionDirty`, `clearSessionDirty`, `isSessionDirty` | Session CRUD + `_sessionDirty` owner（D-V14）|

**utils/ (2 modules)**

| 模組 | 檔案 | 主要 Exports | 說明 |
|------|------|-------------|------|
| analytics | `static/js/utils/analytics.js` | `getAnalyticsTracker`, `getCurrentAnalyticsQueryId`, `setCurrentAnalyticsQueryId` | 查詢 analytics tracking |
| dom | `static/js/utils/dom.js` | `matchSessionId`, `escapeHtml` | DOM 工具函數 |

##### Import Graph 設計規則

**D-V3 Backref Pattern（state-sync ↔ live-research）**

`core/state-sync.js` 不可 direct import `features/live-research.js`（TDZ + circular dependency 風險）。改用 inject pattern：

```
live-research.js 啟動時呼叫：
  injectStateSyncBackref({ isLRInProgress, getLRSessionId, clearLRSessionId })
→ state-sync.js 內部儲存 backref function pointers
→ clearUserScopedState() 透過 backref pointers 呼叫（不 direct import）
```

**D-V6 Import Direction 規則 + 放寬**

基本方向：`core/ → features/ → utils/`（不反向）。

D-V6 放寬（CEO 拍板）：跨 feature read-only 或 function-call import 允許：
- `search ↔ chat`（互相讀取 history 做 session state）
- `knowledge-graph → search`（KG 讀取搜尋結果 per CEO #7）
- `deep-research ↔ search`（DR 執行後更新 search state）
- `folders → sessions-list`（commit 8 先例，folder 更新後 re-render sidebar）

**禁止**：features/ → core/auth-manager（auth 單向 core）；circular imports。

##### 21 Intentional Window Bridges（Load-Bearing）

Refactor 結束後仍有 21 個 `window.X = ...` 刻意保留，原因分兩類：

| 原因 | 數量 | 說明 |
|------|------|------|
| Sidebar inline-onclick | 多數 | HTML sidebar 用 `onclick="window.funcName()"` 呼叫，無法改成 ES import |
| ES module parse-time cycle avoidance | 少數 | 跨模組呼叫若改 import 會形成循環依賴；window bridge 避免 |

完整清單記錄在 `static/js/news-search.js` top-of-file commit 25 comment block（29 commits 最終狀態）。

##### news-search.js Stub（KEEP-in-place 14 類函數）

`static/news-search.js` 從 11697 行精簡至 3420 行（-71%）。以下函數刻意保留不搬出：

| 類別 | 函數例子 | 保留原因 |
|------|--------|---------|
| DOM init（頁面啟動）| `DOMContentLoaded` handlers | 直接操作 HTML DOM，與 module 載入同時執行 |
| Bootstrap 序列 | `window.resetConversation` prefix | 相依 window global，搬出需完整 bridge |
| Session hydration | `loadSavedSession` | 直接 reassign 多個 outer-scope `let`，classic-script only |
| DOM-coupled residuals | `setProcessingState`, `cancelAllActiveRequests` | 操作複雜 DOM + 相依多個 outer state |
| 21 window-attach bridges | `window.openTab = openTab`, etc. | Sidebar inline-onclick callsite，無法移除 |

**Root cause**: classic-script `let` binding 不能被 ES module reassign（D-V3 / lessons-frontend 2026-05-21）。真實搬出需一次性大改 declaration → getter/setter/event-based pattern。AC-V6（≤500 LOC）為 GOAL not hard target，CEO directive 1（2026-05-25）接受 3420 LOC 現況。

**Major Upgrade 升級方向**（原始計畫已歸檔：`docs/archive/plans/major-upgrade-plan.md` §5.4, §6.6；LR narration 已部分落地對話驅動精神——narration 走 chat message 不加新 widget，見 `live-research-spec.md` 原則 7/10——下表三項的完整形態仍為計畫）：

| 升級概念 | 狀態 | 說明 |
|----------|------|------|
| **讀豹 Single Voice** | 📋 計畫中 | 底層 Chat Agent + Research Subagent two-layer，UI 呈現單一 voice（讀豹）。類比 Claude Code 主 agent + background subagent |
| **Event-Based Narration** | 📋 計畫中 | 不做 character-by-character streaming，改用 event-based messages。Research Agent 完成 milestone → 送 event → Chat Agent 整理成一則 message push 到對話。現有 chat push 機制完全 reuse |
| **Dialogue-First UI** | 📋 計畫中 | 所有原本的 UI widget（progress、warning、decision points）改成讀豹對話內容。不加新 UI widgets、sidebar alerts、popup（原則 #10） |

### M6: Infrastructure（基礎設施）🟢 完成

| 元件 | 狀態 | 檔案 | 說明 |
|------|------|------|------|
| PostgreSQL（一體化）| ✅ | `retrieval_providers/postgres_client.py` | vectors + metadata + auth + analytics + sessions 統一 DB |
| Auth System | ✅ | `auth/auth_db.py`, `auth/auth_service.py`, `webserver/routes/auth.py` | Email/Password + JWT access+refresh token + B2B 支援 |
| Session Management | ✅ | `core/session_service.py`, `webserver/routes/sessions.py` | 會話管理與持久化 |
| Middleware Layer | ✅ | `webserver/middleware/` | auth, rate_limit, concurrency_limiter, csp, cors, correlation, error_handler, logging_middleware, streaming, ip_utils |
| In-Memory Cache | ✅ | `chat/cache.py` | 活躍對話記憶體快取 |
| LLM Service | ✅ | `core/llm.py` | 統一 LLM API 封裝 |
| Analytics Engine | ✅ | `core/query_logger.py`, `core/analytics_db.py` | 檢索品質與行為追蹤（SQLite 本地 / PostgreSQL 生產）|
| Audit Service | ✅ | `core/audit_service.py`, `webserver/routes/audit.py` | 操作稽核日誌 |

---

## 核心 Data Flow

### Ingestion（離線）
```
Domain Allowlist → Auto Crawler → Format Detect → Quality Gate → Light NER → Data Chunking → PostgreSQL (pgvector + pg_bigm)
```

### Query Processing（線上）
```
API Gateway → Middleware Chain (auth, rate_limit, csp, cors) → Prompt Guardrails → Query Decomposition
```

### Retrieval Strategy
```
Query Decomposition → Internal Search (pg_bigm + pgvector) → [+ Private Docs] → [+ Web Search (gap resolution)]
```

### Ranking Pipeline
```
Retrieval Results → LLM Ranking → XGBoost (shadow) → MMR → Post-Ranking
```

### Reasoning Loop（Deep Research）— 現有架構
```
Orchestrator → Clarification (if ambiguous) → Time Range Extractor
           ↓
    Analyst Agent → KG & Gap Detection
           ↓
    Critic Agent → PASS/REJECT
           ↓
    Writer Agent → 格式化輸出
           ↓
    (Back to Orchestrator if REJECT)
```

### 🔄 Reasoning Loop — Major Upgrade 目標架構（Composable Pipeline）
```
ResearchState dataclass 驅動：

Phase 1: filter_and_prepare()
    ↓
Phase 1.5 (📋): Association Layer — B→A→B' Loop
    Context Map (B) → Search Plan (A) → Execute → Refine B → B'
    ↓
Phase 2: actor_critic_loop()
    Analyst → [Propose-Verify (📋)] → Critic [+ review_consistency() (📋)] → PASS/REJECT
    ↓
Phase 3: writer()
    ↓
Phase 4: format_result()

Non-blocking: asyncio.create_task() — chat 不等 research
三層 Cancellation: soft interrupt → mid-stream LLM abort → hard HTTP abort
```

### Output
```
Writer → API → (LLM Safety Net) → Frontend UI → Visualizer/Dashboard/Export
```

### 🔄 Output — Major Upgrade 目標架構
```
Research Agent (milestone event) → Chat Agent (讀豹 voice) → Event-Based Message Push → Frontend
                                                           ↑
                               Critic narrative_transition ─┘ (讀豹對話轉折)
```

---

## 關鍵檔案對應（運行時狀態）

| 狀態區域 | 主要檔案 |
|----------|----------|
| Server Startup | `webserver/aiohttp_server.py` |
| Connection Layer | `webserver/middleware/` |
| **Auth** | `auth/auth_db.py`, `auth/auth_service.py`, `webserver/routes/auth.py` |
| Request Processing | `core/baseHandler.py`, `core/state.py` |
| Pre-Retrieval | `core/query_analysis/*.py` |
| Retrieval | `core/retriever.py`, `retrieval_providers/postgres_client.py` |
| Private Docs | `core/user_data_processor.py`, `retrieval_providers/user_postgres_provider.py`, `core/user_data_retriever.py` |
| Session Management | `core/session_service.py`, `webserver/routes/sessions.py` |
| Ranking | `core/ranking.py`, `core/xgboost_ranker.py`, `core/mmr.py` |
| Reasoning | `reasoning/orchestrator.py`, `reasoning/agents/*.py` |
| Post-Ranking | `core/post_ranking.py` |
| SSE Streaming | `core/utils/message_senders.py`, `core/schemas.py` |
| **Help Center** | `webserver/routes/help.py`, `static/help.html`, `static/js/help.js` |
| **Frontend Module** | `static/news-search.js`（3420 LOC stub，-71%）, `static/news-search.css`, `static/news-search-prototype.html`, `static/js/core/` (5 modules), `static/js/features/` (14 modules), `static/js/utils/` (2 modules)（ES module 架構，Modular Refactor v4.0 Path A++ 完成 2026-05-25，21 module surfaces，詳見 M5 Frontend 模組架構段）|
| **Init Sync (Frontend)** | `webserver/routes/user_init.py`（composite endpoint）, `static/js/core/state-sync.js` UserStateSync IIFE（228 行，`clearUserScopedState` / `fetchInit` / `applyInit` + `assertUserIdentity` helper + 7 個 sync trigger A-G；D-2026-05-13 invariant `cache.user_id == JWT.user_id`，commit 228a93a user payload `org_id`+`role` contract）|
| **Live Research (LR)** | `methods/live_research.py`, `reasoning/live_research/orchestrator.py`, `reasoning/live_research/loop_engine.py`, `reasoning/live_research/stages/`, `docs/specs/live-research-spec.md`（LR v15 完成 2026-05-19~20：6-Stage dialog loop + DR citation pipeline + TypeAgent typed action dispatcher）|
| **Onboarding** | `auth/auth_service.py:register_user / activate_account`, `webserver/routes/auth.py`（D-2026-05-13 Backend variant：register/activate 成功後 auto-issue access + refresh cookie，commit `2ee5508`；完成跳 `/` 後 checkAuthOnLoad 走 trigger A）|

詳細狀態流程參見：`docs/reference/architecture/state-machine-diagram.md`

---

## 主要 API

### HTTP 端點

#### 查詢處理
- **`GET/POST /ask`** - 主要查詢端點
  - 參數：`query`、`site`、`generate_mode`、`streaming`、`prev`、`model`、`thread_id`

#### 資訊端點
- **`GET /sites`** - 可用網站清單
- **`GET /who`** - 「誰」類查詢
- **`GET /health`** - 健康檢查

#### 認證（Email/Password + JWT）
- **`POST /api/auth/login`** - 登入，取得 access + refresh token
- **`POST /api/auth/logout`** - 登出
- **`POST /api/auth/refresh`** - 刷新 access token
- **`POST /api/auth/register`** - 用戶註冊

#### Session 管理
- **`GET /api/sessions`** - Session 列表
- **`POST /api/sessions`** - 建立 Session
- **`DELETE /api/sessions/{id}`** - 刪除 Session

#### 對話管理
- **`GET /api/conversations`** - 對話列表
- **`POST /api/conversations`** - 建立/更新對話
- **`DELETE /api/conversations/{id}`** - 刪除對話

#### Private Docs
- **`POST /api/user-data/upload`** - 上傳私有文件
- **`GET /api/user-data`** - 文件列表
- **`DELETE /api/user-data/{id}`** - 刪除文件

#### Help Center
- **`POST /api/feedback`** - 提交用戶回饋

#### 稽核
- **`GET /api/audit`** - 操作稽核日誌

### SSE 訊息類型
| 類型 | 說明 |
|------|------|
| `begin-nlweb-response` | 開始處理 |
| `result` | 搜尋結果 |
| `intermediate_result` | Reasoning 進度 |
| `summary` | 摘要回應 |
| `clarification_required` | 需要澄清 |
| `results_map` | 地圖資料 |
| `end-nlweb-response` | 處理完成 |
| `error` | 錯誤訊息 |

---

## 系統架構圖

### 現有架構
```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Browser   │────▶│  WebServer   │────▶│  Middleware  │
│ (JS Client) │◀────│   (HTTP)     │◀────│(auth/csp/...) │
└─────────────┘     └──────────────┘     └─────────────┘
                            │                     │
                            ▼                     ▼
                    ┌──────────────┐     ┌─────────────┐
                    │ NLWebHandler │────▶│  Auth / JWT  │
                    │    (Base)    │     │  Sessions   │
                    └──────────────┘     └─────────────┘
                            │
            ┌───────────────┼───────────────┐
            ▼               ▼               ▼
    ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
    │  Retriever  │ │   Ranking   │ │  Reasoning  │
    │(pg_bigm+vec)│ │(LLM+XGB+MMR)│ │(Actor-Critic)│
    └─────────────┘ └─────────────┘ └─────────────┘
```

### 🔄 Major Upgrade 目標架構（Research Helper Platform）
```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Browser   │────▶│  WebServer   │────▶│  Middleware  │
│ (JS Client) │◀────│   (HTTP)     │◀────│(auth/csp/...) │
└─────────────┘     └──────────────┘     └─────────────┘
       ▲                    │                     │
       │                    ▼                     ▼
       │            ┌──────────────┐     ┌─────────────┐
       │            │ Chat Agent   │────▶│  Auth / JWT  │
       │            │ (讀豹 voice) │     │  Sessions   │
       │            └──────────────┘     └─────────────┘
       │                    │
       │     ┌──────────────┼──────────────┐
       │     ▼              ▼              ▼
       │ ┌─────────┐ ┌──────────┐ ┌───────────────────┐
       │ │Retriever│ │ Ranking  │ │Research Subagent   │
       │ │(hybrid) │ │(LLM+XGB │ │ ┌─Association─────┐│
       │ └─────────┘ │ +MMR)    │ │ │B→A→B' Loop     ││
       │             └──────────┘ │ └─────────────────┘│
       │                          │ ┌─Actor-Critic────┐│
       │  event-based messages    │ │Analyst+Critic   ││
       └──────────────────────────│ │+Propose-Verify  ││
                                  │ └─────────────────┘│
                                  │ ┌─Writer──────────┐│
                                  │ │Format + Guard   ││
                                  │ └─────────────────┘│
                                  └───────────────────┘
              asyncio.create_task() ← non-blocking
```

---

## Major Upgrade — Research Helper Platform

> 詳細計畫：`docs/archive/plans/major-upgrade-plan.md`（已歸檔——核心項目 Composable Pipeline / LR 6-stage / BAB loop 已落地，本章保留為策略脈絡快照，現行實況見 `reasoning-spec.md` / `live-research-spec.md`）
> 決策日誌：`docs/decisions.md`（2026-04-10~11 區段）

NLWeb 正從「新聞搜尋引擎」升級為「可控的專業研究助理平台」。對標 IDC / Gartner / 台綜院 / Palantir 戰略 AI，差異化是「可控性 + 繁體中文垂直專業 + Research as Living Entity」。

### 升級概念總覽

| # | 概念 | 狀態 | 影響模組 | 簡述 |
|---|------|------|----------|------|
| 1 | **產品升級** | ✅ 已拍板 | 全系統 | 從新聞搜尋引擎 → 可控的專業研究助理平台 |
| 2 | **Composable Pipeline Refactor** | 🔄 執行中 | M4 | `run_research()` 單體 → ResearchState + 4 composable phases |
| 3 | **Non-blocking Deep Research** | 🔄 執行中 | M4, M5 | `asyncio.create_task()` 背景執行，chat 不等 research。三層 cancellation |
| 4 | **B→A→B' Association Loop** | 📋 計畫中 | M2, M4 | Context Map 驅動 Search Plan，session-wide master B |
| 5 | **Critic Agent 擴充** | 📋 計畫中 | M4 | `review_consistency()` + 讀豹對話轉折 output channel |
| 6 | **Propose-Verify Pattern** | 📋 計畫中 | M2, M4 | LLM hypothesis → Search verify → 三層事實保護 |
| 7 | **讀豹 Single Voice + Event-Based Narration** | 📋 計畫中 | M5 | Chat Agent + Research Subagent，UI 單一 voice |
| 8 | **Dialogue-First UI** | 📋 計畫中 | M5 | UI widget → 讀豹對話內容（原則 #10） |
| 9 | **Mini-Reasoning per Step** | 📋 計畫中 | M4 | Hierarchical: MetaOrchestrator → StepOrchestrator → 現有 DeepResearchOrchestrator |
| 10 | **Research State Serialization** | 📋 未來 | M4, M6 | 研究會話 lossless 存檔 + 跨 session load |
| 11 | **Trigger-Aware Conclusions** | 📋 未來 | M4 | 結論帶失效條件 + trigger 動作 |
| 12 | **Association Agent** | 📋 未來 | M4 | B→A→B' loop 引擎，新 agent |

### 設計原則（10 條，按優先順序）

1. **北極星**：一切技術決定服從「能不能 convince 客戶」
2. **Narrow first, generalize later**：先在一個領域做到極致
3. **系統是放大器不是取代者**：人類專家做最終價值判斷
4. **不知道就問 user，不要猜**：Dialogue-Driven Research Loop
5. **高良率要求**：寧可慢也不能漏/錯
6. **Living document**：報告能隨新 info 延伸、lossless 存檔、可重啟
7. **Minimize workflow disruption**：不打擾使用者既有工作流
8. **Transparent reasoning**：邊做邊告知 reasoning chain
9. **Propose-Verify Pattern**：LLM knowledge = falsifiable hypothesis
10. **Dialogue-First UI**：所有能力改成讀豹對話內容

### 核心 Frameworks（5 個）

| Framework | 說明 |
|-----------|------|
| **Dialogue-Driven Research Loop** | AI propose → 解釋推理 → 問人類 → 人類 feedback → 迭代收斂 |
| **Async Pair-Work** | AI 背景執行 + 系統問使用者引導式問題，wall-clock = max(AI, human) |
| **Consistency Monitor** | Critic Agent 擴充 — 監控 master B drift，偏離就讀豹對話提醒 |
| **讀豹 Mental Model** | 讀豹邊翻書邊聊，single voice，event-based narration |
| **B→A→B' Iterative Loop** | Context Map → Search Plan → Execute → Refine。Abductive reasoning loop |

---

## 設定檔

| 檔案 | 用途 |
|------|------|
| `config/config_nlweb.yaml` | 主設定 |
| `config/config_retrieval.yaml` | 檢索端點 |
| `config/config_llm.yaml` | LLM 提供者 |
| `config/config_embedding.yaml` | Embedding 提供者（Qwen3 / OpenAI 等）|
| `config/config_reasoning.yaml` | Reasoning 參數 |
| `config/config_webserver.yaml` | WebServer 設定 |
| `config/config_logging.yaml` | 日誌設定 |
| `config/prompts.xml` | Prompt 模板 |

---

*更新：內文含至 2026-06-26 增量更新（Qdrant / WebSocket 廢除、pgvector + pg_bigm 已反映；source tier 廢除亦已反映）。footer 原標「2026-05-25」係 Frontend Modular Refactor v4.0 段更新日，非全文最新——2026-07-10 稽核更正此日期誤導。*
