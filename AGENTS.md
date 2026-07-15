# AGENTS.md

本文件為 Codex 提供專案指引。

> 行為規則與 `CLAUDE.md` 同步維護，**CLAUDE.md 為 master**（2026-07-11 收斂拍板）；兩檔規則若有出入，以 CLAUDE.md 為準。動態狀態一律不放本檔。

**黃金法則**：僅實作被要求的功能，不要額外新增功能。避免過度複雜化。

---

## 專案概述

新聞網站自然語言搜尋系統。目標：可信、準確、邏輯嚴謹的搜尋與推論。

**專案狀態**：見 `docs/status.md`（動態內容不放本檔，避免 stale）。

---

## 架構概述

**核心流程**：Query → Pre-retrieval 分析 → Tool 選擇 → Retrieval (pg_bigm + Vector) → Ranking (LLM → XGBoost → MMR) → Response

### 關鍵檔案對應

| 狀態區域               | 主要檔案                                                       |
| ------------------ | ---------------------------------------------------------- |
| **Crawler**        | `crawler/core/engine.py`, `crawler/subprocess_runner.py`, `crawler/parsers/*.py` |
| **Indexing**       | `indexing/pipeline.py`, `indexing/chunking_engine.py`      |
| Server Startup     | `webserver/aiohttp_server.py`                              |
| Connection Layer   | `webserver/middleware/`                                     |
| **Auth**           | `auth/auth_db.py`, `auth/auth_service.py`, `webserver/routes/auth.py` |
| Request Processing | `core/baseHandler.py`, `core/state.py`                     |
| Pre-Retrieval      | `core/query_analysis/*.py`                                 |
| Retrieval          | `core/retriever.py`, `retrieval_providers/postgres_client.py`  |
| Private Docs       | `core/user_data_processor.py`, `retrieval_providers/user_postgres_provider.py`, `core/user_data_retriever.py` |
| Session Management | `core/session_service.py`, `webserver/routes/sessions.py`  |
| Ranking            | `core/ranking.py`, `core/xgboost_ranker.py`, `core/mmr.py` |
| Reasoning          | `reasoning/orchestrator.py`, `reasoning/agents/*.py`       |
| Post-Ranking       | `core/post_ranking.py`                                     |
| SSE Streaming      | `core/utils/message_senders.py`, `core/schemas.py`         |
| **Help Center**    | `webserver/routes/help.py`, `static/help.html`, `static/js/help.js` |

### 關鍵設計模式

1. **Streaming**：使用 SSE 即時回應
2. **平行處理**：Pre-retrieval 檢查同時執行
3. **Wrapper Pattern**：NLWebParticipant 包裝 handler，不修改原始碼
4. **Cache-First**：活躍對話使用記憶體快取

### 程式碼索引工具（強制使用）

**規定**：搜尋程式碼時，**必須**使用 SQLite 索引系統，**禁止**使用 Grep 工具。

**工作流程**：
1. **開始工作時**：`python tools/indexer.py --index`
2. **搜尋時**：`python tools/indexer.py --search "關鍵字"`
3. **大量修改檔案後**：`python tools/indexer.py --index`

**為什麼**：
- FTS5 搜尋是毫秒級，Grep 需掃描所有檔案
- 減少 token 消耗，提升效率
- 支援 SQL 聚合分析

**例外情況**：只有當索引系統失敗時，才可向使用者報錯並改用 Grep。

**詳細文件**：`docs/specs/code-in-sqlite.md`

---

## 文件查詢指令

**重要**：當被詢問特定模組或檔案時，必須先閱讀對應文件了解上下游模組關係：

| 詢問主題       | 需閱讀的文件                                                       |
| ---------- | ------------------------------------------------------------ |
| 系統狀態機、運作流程 | `docs/reference/architecture/state-machine-diagram.md`       |
| 系統總覽與 API  | `docs/reference/systemmap.md`                                |
| 程式碼規範      | `docs/reference/codingrules.md`                              |
| UX 流程      | `docs/reference/userworkflow.md`                             |
| 專案狀態       | `docs/status.md`                                             |
| 已完成工作      | `docs/archive/completed-work.md`                             |
| 決策日誌       | `docs/decisions.md`                                          |
| 演算法規格      | `docs/specs/*-spec.md` (bm25, mmr, xgboost 等)               |
| Login 系統   | `docs/specs/login-spec.md`                                   |
| Docker 部署  | `docs/reference/docker-deployment.md`                        |

---

## 模組開發狀態

模組狀態為動態資訊，不放本檔（曾因 stale 表格誤導）。查詢順序：
`docs/reference/systemmap.md`（模組架構與現況）→ `docs/status.md`（目前重點與 backlog）→ `docs/archive/completed-work.md`（已完成工作史）。

---

## 重要開發規則

### Debug 與問題診斷：先讀 Memory

**關鍵**：被要求 debug 或診斷問題時，**必須**先讀取 memory 相關檔案，再開始調查。

**流程**：
1. 先讀 `memory/MEMORY.md`（專案根目錄下）——它是純索引，模組 → lessons 檔的完整對應表以它為準（單一來源；`.claude/commands/learn.md` A2 有同表）
2. 依索引讀對應 `memory/lessons-*.md`（如 crawler / infra-deploy / auth / frontend / general）
3. 確認是否為已知問題或類似 pattern
4. 若為新問題，才開始從程式碼調查

**為什麼**：過去許多 bug 有重複 pattern（如 Windows pipe buffer、watermark skip、curl_cffi fallback）。先讀 memory 可避免重複踩坑，大幅加速 debug。

### 以盡速debug為前提，不可以Silent Fail

 **關鍵**：讓錯誤情況自然浮現，不可以silently catch errors/exceptions

- 如果程式或LLM表現不如預期，我們要能第一時間catch，並且debug

- 可以優雅降級，但必須要有明確訊息表示已被降級。

- 絕對不可以讓錯誤被無視。

### Smoke Test：修改程式碼後必跑

**關鍵**：任何修改 Python 程式碼的操作完成後必須執行 `cd code/python && python tools/smoke_test.py`。FAILED 則立即修復，不可跳過。

**例外**：只修改 docs/、memory/、config YAML/JSON、static/ 前端檔案時不需要跑。

**詳細規則與派工指引**：見 `memory/delegation-patterns.md`「Smoke Test Gate」段落

### E2E Gate：程式碼改動在 E2E 測試通過前不算完成

**關鍵**：Unit test + smoke test 通過 ≠ 完成。Pipeline：`Unit Test → Smoke Test → Agent E2E (DevTools) → CEO 人工 E2E → Pass = 完成`

**例外**：只修改文件、config、或無法透過前端觸發的純後端邏輯時不需要跑。

**詳細流程、環境驗證、prompt 模板**：見 `memory/delegation-patterns.md`「E2E Gate」段落

### 絕對禁止 Reward Hack

**關鍵**：必須尋求全面性解決方案。

- 從系統角度思考：上下游模組如何受影響？依賴關係如何？命名是否與既有程式碼一致？
- 不要在發現第一個問題就停下：多數情況需要多處修正，目標是一次修復全部。

### 清理臨時檔案

完成任務後，務必刪除任何為了迭代而建立的臨時檔案、腳本或輔助檔案。

### 演算法變更

**關鍵**：修改搜尋/排序演算法時，**必須**更新 `docs/specs/` 目錄文件。

- 建立/更新 `docs/specs/{algorithm}-spec.md`
- 內容包含：目的、公式、參數、實作細節、測試策略
- 範例：`docs/specs/xgboost-spec.md`、`docs/specs/mmr-spec.md`

### Python 版本

**使用 Python 3.11**（非 3.13）。多個依賴套件尚未支援 3.13。

### Analytics 資料庫

**雙資料庫支援**：系統透過 `POSTGRES_CONNECTION_STRING` 環境變數自動偵測（fallback: `DATABASE_URL` → `ANALYTICS_DATABASE_URL`）。

- **本地開發**：SQLite（預設，免設定）
- **Production**：VPS PostgreSQL（與 Auth / Search 共用 `nlweb` database）

### 程式碼風格

- 優先編輯既有檔案而非建立新檔案
- 實作前先檢查鄰近檔案的 pattern
- 設定變更需重啟 server
- 除非明確要求，否則不使用 emoji
- Code review 後若有 simplification 類建議，可用 `simplify` skill 自動處理

### AGENTS.md Stability (Prompt Cache)

**Background**: Codex caches AGENTS.md content at org level (`cacheScope: 'org'`). Every edit breaks the cache and causes the full prompt to be re-billed. Skills and memory files are in the dynamic section and do not affect cache.

**Rules**:
- **Maximum 1 edit per week** to AGENTS.md. Batch small changes.
- Dynamic content (status, progress numbers, current work) stays in `docs/status.md`, never in AGENTS.md.
- AGENTS.md contains only stable rules and structural information that change infrequently.
- Skills (`.Codex/commands/`, `.Codex/skills/`) and memory files (`memory/`) can be edited freely -- they are loaded in the dynamic section.

### Docker 部署

**關鍵**：變更 base image 時務必清除 Docker build cache。

**詳細資訊**：見 `docs/reference/docker-deployment.md`（僅在 Docker 部署時需要）

### Memory 更新規則

**禁止**將實質內容（錯誤教訓、開發細節、狀態數字）直接寫入 `MEMORY.md`。`MEMORY.md` 是純索引，只放檔案指標。

- 新的技術教訓 → 寫入對應的 `memory/lessons-*.md`
- 新的專案狀態 → 寫入對應的 `memory/project_*.md`
- 新的參考資訊 → 寫入對應的 `memory/reference_*.md`
- 然後在 `MEMORY.md` 的 File Index 加一行指標
