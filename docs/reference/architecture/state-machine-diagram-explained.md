# NLWeb State Machine Diagram - 詳細說明

本文件用白話文詳細說明 state-machine-diagram.md 中的各個狀態圖。

---

## 目錄

1. [系統總覽](#1-系統總覽)
2. [連接層狀態](#2-連接層狀態)
3. [請求處理狀態](#3-請求處理狀態)
4. [排序管道狀態](#4-排序管道狀態)
5. [Reasoning 系統狀態](#5-reasoning-系統狀態)
   - 5.3 [Major Upgrade — 計畫中的 Reasoning 擴充狀態](#53-major-upgrade--計畫中的-reasoning-擴充狀態)
6. [Chat 系統狀態](#6-chat-系統狀態)
7. [SSE 串流狀態](#7-sse-串流狀態)
8. [錯誤處理狀態](#8-錯誤處理狀態)
9. [Handler State](#9-handler-state)
10. [完整生命週期](#10-完整生命週期)
11. [兩種視角的對照：Data Flow vs State Machine](#11-兩種視角的對照data-flow-vs-state-machine)

---

## 1. 系統總覽

### 這張圖在說什麼？

這是整個 NLWeb 系統的「鳥瞰圖」，描述 Server 從啟動到關閉的整個生命週期。

### 狀態說明

| 狀態                  | 白話解釋                                                |
| ------------------- | --------------------------------------------------- |
| **LoadConfig**      | 載入設定檔（config.yaml、環境變數等），決定系統要用哪些 LLM、連接哪個 PostgreSQL   |
| **SetupMiddleware** | 設定「中介軟體」，依序包含：auth（JWT 身份驗證）、rate_limit（速率限制）、concurrency_limiter（並發限制）、csp（Content Security Policy）、cors（跨網域）、logging、correlation、streaming、error_handler 等（共 10 個 middleware） |
| **SetupRoutes**     | 註冊 API 路徑，例如 `/ask` 是搜尋端點、`/chat/ws` 是 WebSocket 端點 |
| **InitChatSystem**  | 啟動 Chat 子系統，包含 WebSocket 管理器、對話管理器                  |
| **ServerRunning**   | 系統正式開始接受請求，這是正常運作的主要狀態                              |
| **ServerShutdown**  | 收到關閉訊號（Ctrl+C 或 kill），優雅地關閉連線、儲存快取                  |

### 生活化比喻

想像 Server 是一家餐廳：

- **LoadConfig** = 早上開店前看今天的菜單和原料清單
- **SetupMiddleware** = 安排門口的接待人員（檢查訂位、防止閒雜人等）
- **SetupRoutes** = 安排各個服務區（外帶區、內用區、VIP 包廂）
- **InitChatSystem** = 啟動對講機系統，讓外場和內場可以溝通
- **ServerRunning** = 營業中！接待客人
- **ServerShutdown** = 打烊，結帳、清潔、關燈

---

## 2. 連接層狀態

### 2.1 HTTP 連接

#### 這張圖在說什麼？

描述一個 HTTP 請求從「進來」到「結束」的完整流程。大部分的搜尋請求走這條路。

#### 狀態說明

| 狀態                   | 白話解釋                                         |
| -------------------- | -------------------------------------------- |
| **HTTPRequest**      | 瀏覽器或 API 客戶端發送了一個請求                          |
| **AuthCheck**        | 檢查這個請求是否有合法的身份憑證（JWT access token 或 httpOnly cookie）；公開端點仍可透過 soft auth 取得使用者資訊 |
| **CORSCheck**        | 檢查請求來源的網域是否被允許（例如只允許來自 localhost 或你的網站）      |
| **RouteMatch**       | 比對請求的路徑，決定要交給哪個處理器                           |
| **SSESetup**         | 如果是 `/ask` 端點，建立 SSE（Server-Sent Events）串流連線 |
| **Streaming**        | 開始傳送搜尋結果，一筆一筆送給前端                            |
| **RESTHandler**      | 其他端點走傳統的 REST 模式（請求→處理→回應）                   |
| **Rejected**         | 請求被拒絕（權限不足、CORS 失敗、找不到路徑）                    |
| **ConnectionClosed** | 連線結束，釋放資源                                    |

#### 為什麼用 SSE？

SSE 的好處是「邊處理邊回傳」。傳統 REST 要等全部處理完才能回傳，但搜尋可能需要 5-10 秒。SSE 可以先回傳「正在搜尋...」，然後一筆一筆送結果，使用者體驗更好。

### 2.2 WebSocket 連接

#### 這張圖在說什麼？

描述 WebSocket 連線的生命週期。WebSocket 用於 Chat 功能，提供雙向即時通訊。

#### 狀態說明

| 狀態                | 白話解釋                           |
| ----------------- | ------------------------------ |
| **Connecting**    | 正在建立 WebSocket 連線（HTTP 升級為 WS） |
| **Connected**     | 連線成功，可以雙向傳訊息                   |
| **Heartbeat**     | 定期發送 Ping/Pong 確認連線還活著         |
| **TimedOut**      | 太久沒收到 Pong，判定連線已斷              |
| **Disconnecting** | 正在關閉連線（清理資源）                   |
| **Disconnected**  | 連線已完全關閉                        |
| **Reconnecting**  | 自動重連機制（帶有指數退避）                 |
| **Failed**        | 連線失敗（網路問題、伺服器錯誤）               |

#### Heartbeat 是什麼？

想像你和朋友講電話，如果對方一直沒聲音，你會喊「喂？還在嗎？」這就是 Heartbeat。Server 定期發送 Ping，客戶端回 Pong，證明連線還活著。如果太久沒回，就判定斷線。

#### 指數退避 (Exponential Backoff)

重連時不會瘋狂嘗試，而是：第一次等 1 秒、第二次等 2 秒、第三次等 4 秒...避免在網路不穩時造成更大負擔。

---

## 3. 請求處理狀態

### 這張圖在說什麼？

這是核心！描述 `NLWebHandler`（負責處理搜尋請求的主角）如何處理一個查詢。

### HandlerInit（初始化階段）

當一個搜尋請求進來，系統會先「準備好舞台」：

| 步驟                   | 白話解釋                                   |
| -------------------- | -------------------------------------- |
| **InitCoreParams**   | 解析請求參數（query=什麼、site=哪個網站、mode=列表還是摘要） |
| **InitQueryContext** | 設定查詢上下文（對話歷史、使用者偏好）                    |
| **InitConversation** | 產生或恢復 conversation_id                  |
| **InitState**        | 建立狀態管理器（用來追蹤 pre-check 進度）             |
| **InitSync**         | 初始化同步機制（Event、Lock）                    |
| **InitMessaging**    | 設定訊息發送器（負責把結果送到前端）                     |

### SendBeginResponse

發送一個「開場訊息」告訴前端：「我開始處理了！」這讓前端可以顯示載入動畫。

### Prepare（準備階段）

這是最複雜的部分！系統會**同時**執行多個預處理任務：

#### ParallelPreChecks（並行預檢查）

| 任務                   | 白話解釋                                             | 實際程式                                     |
| -------------------- | ------------------------------------------------ | ---------------------------------------- |
| **Decontextualize**  | 把「上一題」的指代詞展開。例如「他」→「蔡英文」                         | `query_analysis/decontextualizer.py`     |
| **QueryRewrite**     | 改寫查詢讓搜尋更準確。例如「最近新聞」→「2024年12月新聞」                 | `query_analysis/query_rewriter.py`       |
| **TimeRangeExtract** | 解析時間範圍。「去年的」→ start: 2024-01-01, end: 2024-12-31 | `query_analysis/time_range_extractor.py` |
| **ToolSelection**    | 決定這個查詢該用什麼工具（search / deep_research / chat）      | `query_analysis/tool_selector.py`        |
| **Memory**           | 從對話記憶中找相關資訊                                      | `chat/memory.py`                         |

**為什麼要並行？** 這些任務彼此獨立，同時跑可以省時間。例如「解析時間」和「改寫查詢」沒有依賴關係。

#### Retrieval（檢索階段）

等 pre-checks 完成後，開始從資料庫抓資料：

| 步驟                   | 白話解釋                    |
| -------------------- | ----------------------- |
| **CheckSiteSupport** | 檢查這個網站有沒有被索引過           |
| **VectorSearch**     | 用 PostgreSQL pgvector 做向量相似度搜尋（語意搜尋）+ pg_bigm 關鍵字搜尋（hybrid search） |
| **TemporalFilter**   | 如果是時間相關查詢，過濾掉不符合時間範圍的結果 |
| **SetResults**       | 把檢索結果存起來，供後續使用          |

### RouteQuery（路由階段）

根據 ToolSelection 的結果，決定下一步：

| 條件                           | 下一步                             |
| ---------------------------- | ------------------------------- |
| **Has Handler Class**        | 交給特定處理器（例如 DeepResearchHandler） |
| **No Handler / Search Tool** | 走標準的排序管道（Ranking Pipeline）      |

### PostRanking（後排序階段）

排序完成後的收尾工作：

| 任務                    | 白話解釋                       |
| --------------------- | -------------------------- |
| **CheckMapMessage**   | 如果超過一半的結果有地址，生成地圖訊息        |
| **CheckGenerateMode** | 檢查是否需要生成摘要（mode=summarize） |
| **Summarize**         | 如果需要，呼叫 LLM 生成結果摘要         |

---

## 4. 排序管道狀態

### 這張圖在說什麼？

描述「如何對搜尋結果排序」的完整流程。這是 Ranking 模組的核心。

### 排序流程

```
檢索結果 → LLM 排序（並行）→ 過濾低分 → XGBoost → MMR → 輸出
```

### ParallelRanking（並行 LLM 排序）

對每一筆檢索結果，**同時**呼叫 LLM 評分：

| 步驟                | 白話解釋                         |
| ----------------- | ---------------------------- |
| **CheckAbort**    | 檢查是否該中止（例如使用者關閉頁面）           |
| **PreparePrompt** | 準備 LLM prompt（包含查詢和文章摘要）     |
| **AskLLM**        | 呼叫 LLM 評分（0-100 分）           |
| **ParseScore**    | 解析 LLM 回傳的分數                 |
| **EarlySend**     | 如果分數 > 59，先送給前端（使用者可以先看到好結果） |

**Early Send 機制**：這是讓使用者體驗更好的設計。不用等全部排完，高分結果先送出去。

### XGBoost Shadow Mode

XGBoost 是機器學習排序模型。目前是「影子模式」：

- **計算分數**：根據多種特徵（向量相似度、pg_bigm 關鍵字分數、metadata）預測排序
- **記錄日誌**：把預測結果記下來
- **不改變排序**：實際排序仍用 LLM 分數

**為什麼用影子模式？** 先收集數據，驗證 XGBoost 預測的準確性，等確認比 LLM 更好再正式啟用。

### MMR（Maximal Marginal Relevance）

解決「結果太雷同」的問題：

| 概念       | 白話解釋                               |
| -------- | ---------------------------------- |
| **問題**   | 前 10 名可能都是講同一件事的不同報導               |
| **解法**   | 在選下一筆結果時，考慮它和已選結果的「差異度」            |
| **參數 λ** | 平衡相關性和多樣性（0.7 = 70% 重視相關、30% 重視多樣） |

---

## 5. Reasoning 系統狀態

### 5.1 Deep Research Handler

#### 這張圖在說什麼？

描述「深度研究」模式的處理流程。這是 Reasoning 模組的入口。

#### 處理流程

```
初始化 → 繼承父類 prepare() → 澄清檢查 → 模式偵測 → 執行研究 → 輸出
```

#### 澄清檢查 (Clarification Check)

有些查詢太模糊，需要先問使用者：

| 情況       | 範例         | 系統行為                  |
| -------- | ---------- | --------------------- |
| **時間歧義** | 「蔡英文的政策」   | 問：你要看哪個時期？（任內/卸任後/全部） |
| **範圍歧義** | 「經濟新聞」     | 問：你關心哪個面向？（股市/房市/就業）  |
| **實體歧義** | 「Apple 新聞」 | 問：你要找 Apple 公司還是蘋果日報？ |

如果需要澄清，系統會發送一個 JSON 給前端，讓使用者選擇，然後等待回應。

#### 研究模式偵測

> **注意**：研究模式選擇（Strict / Discovery / Monitor）已於 2026-04 移除。現在由使用者自行選擇來源，系統不再依關鍵字自動偵測模式。此狀態（`DetectMode → SetResearchMode`）已成為空操作（no-op）或已移除。

### 5.2 Actor-Critic Loop（核心推論引擎）

#### 這張圖在說什麼？

這是 Reasoning 的「心臟」！描述 Analyst（分析師）和 Critic（評論家）如何合作產出高品質答案。

#### 整體流程

```
設定 Session → 過濾來源 → Actor-Critic 迴圈 → Writer 撰寫 → 幻覺檢查 → 輸出
```

#### Analyst Phase（分析師階段）

Analyst 是「做研究」的角色：

| 任務                  | 白話解釋                 |
| ------------------- | -------------------- |
| **AnalystResearch** | 第一次分析：閱讀所有來源，整理重點    |
| **AnalystRevise**   | 修訂：根據 Critic 的批評改進草稿 |
| **Gap Detection**   | 找出「資訊缺口」：哪些問題還沒被回答？  |

#### Gap Detection 詳解

這是讓系統「自己知道自己不知道什麼」的機制：

| Gap 類型              | 範例                  | 解決方式                    |
| ------------------- | ------------------- | ----------------------- |
| **SEARCH_REQUIRED** | 找到 A 公司營收，但沒找到 B 公司 | 發起新搜尋                   |
| **llm_knowledge**   | 需要常識性知識補充           | 問 LLM                   |
| **web_search**      | 需要最新資訊              | 呼叫 Bing Search          |
| **stock_tw**        | 需要台股資料              | 呼叫 Yahoo Finance Taiwan |
| **wikipedia**       | 需要百科知識              | 呼叫 Wikipedia API        |

#### Critic Phase（評論家階段）

Critic 是「品質把關」的角色：

| 評審項目     | 白話解釋             |
| -------- | ---------------- |
| **準確性**  | 內容是否正確？有沒有錯誤？    |
| **完整性**  | 是否回答了所有問題？有沒有遺漏？ |
| **清晰度**  | 表達是否清楚？讀者能理解嗎？   |
| **引用品質** | 來源是否可靠？引用是否正確？   |
| **偏見檢測** | 是否有片面觀點？是否平衡報導？  |

Critic 會給出判決：

- **PASS**：通過，可以進入下一階段
- **WARN**：有小問題但可接受，附上警告
- **REJECT**：不合格，必須重做

#### Convergence Check（收斂檢查）

| 情況                    | 行為                |
| --------------------- | ----------------- |
| **PASS 或 WARN**       | 退出迴圈，進入 Writer 階段 |
| **REJECT 且迭代次數 < 上限** | 回到 Analyst 修訂     |
| **REJECT 且迭代次數 ≥ 上限** | 優雅降級，使用最佳草稿繼續     |

#### Writer Phase（寫作階段）

Writer 是「包裝輸出」的角色：

| 任務                      | 白話解釋                        |
| ----------------------- | --------------------------- |
| **CreateOutline**       | 規劃報告結構（如果啟用 plan_and_write） |
| **GenerateFinalReport** | 把草稿格式化成 Markdown            |
| **添加引用**                | 在每個論點後面加上來源連結               |

#### Hallucination Guard（幻覺防護）

最後一道關卡，防止「編造來源」：

| 檢查項目               | 白話解釋                              |
| ------------------ | --------------------------------- |
| **VerifySources**  | Writer 引用的來源，是否都在 Analyst 提供的清單中？ |
| **CorrectSources** | 如果有問題，自動修正（移除無效引用）                |

---

## 5.3 Major Upgrade — 計畫中的 Reasoning 擴充狀態

> 本節對應 state-machine-diagram.md §5.3 的 Mermaid 圖。所有狀態標記為 📋 PLANNED 或 🔄 IN PROGRESS。
> 詳細設計：`docs/in progress/plans/major-upgrade-plan.md`

### 這張圖在說什麼？

描述 Major Upgrade 後 Deep Research 的目標架構。核心改變是：(1) 從 blocking 單體變成 composable non-blocking pipeline，(2) 新增 Association Layer 做 proactive 資料策略，(3) Critic 擴充為 consistency monitor，(4) 輸出從 SSE streaming 改為 event-based 讀豹對話。

### NonBlockingResearch（非阻塞研究）— 📋 計畫中

| 狀態 | 白話解釋 |
|------|----------|
| **CreateResearchTask** | 用 `asyncio.create_task()` 把整個 research 丟到背景執行，HTTP request 不再等 research 完成 |
| **ChatAvailable** | Research 在背景跑的同時，chat 通道維持開放，使用者可以繼續打字 |
| **SoftInterrupt** | 使用者打字 = soft interrupt signal。三層 cancellation：(1) 停派新 API call (2) 中途中斷當前 LLM stream (3) hard HTTP abort |

**為什麼？** 現有架構是 `await orchestrator.run_research()` — 整個 HTTP request 生命週期 coupled 到 research 完成。使用者看到的是「看起來互動但不能打字」，體驗比 loading spinner 更差。真正 decouple 不只解決 blocking 問題，還解鎖 parallel phase execution、streaming partial results、selective re-run 等一整條 UX 優化。

### ComposablePipeline（可組合管線）— 🔄 執行中

| Phase | 狀態 | 白話解釋 |
|-------|------|----------|
| **Phase 1** | **FilterAndPrepare** | 現有的來源過濾 + context 格式化，提取為獨立 function |
| **Phase 1.5** | **AssociationPhase** | 📋 新增。B→A→B' loop（見下方） |
| **Phase 2** | **ActorCriticLoop_v2** | 現有 Actor-Critic，加入 Propose-Verify 和 Consistency Check |
| **Phase 3** | **WriterPhase_v2** | 現有 Writer |
| **Phase 4** | **FormatResult_v2** | 現有 Format |

**關鍵改變**：`run_research()` 850 行單體 method 拆成 ResearchState dataclass + 4 個 composable phases。每個 phase 是獨立的 async function，可以單獨測試、cancel、re-run。

### AssociationPhase（B→A→B' 循環）— 📋 計畫中

| 狀態 | 白話解釋 |
|------|----------|
| **BuildInitialB** | 從查詢 + 使用者 prior knowledge 建立初始 Context Map（master B）。包含：議題、領域、關聯性、影響關係、上下游、追問方向 |
| **DeriveSearchPlan** | 從 Context Map 衍生 Search Plan（A）：要找什麼資料、去哪裡找、為什麼 |
| **ExecuteSearchPlan** | 執行 Search Plan — 內部檢索 + Web Search + HTTP Scrape |
| **RefineContextMap** | 根據 search 結果更新 Context Map → B'。如果不夠 → loop；夠了 → 交付 |

**生活化比喻**：現有系統像是「看到缺什麼才去買」（reactive Gap Detection）。Association Layer 像是「先列購物清單，照清單逛街，邊逛邊更新清單」（proactive）。

**Master B Scope**：整個 session 共享一個 master B（session-wide），不是每步驟一個。每步驟的 agent 收到最新的 B + active focus hint 來聚焦當前任務。

### ProposeVerify（假設-驗證模式）— 📋 計畫中

| 狀態 | 白話解釋 |
|------|----------|
| **LLMPropose** | LLM 用自身知識提出 hypothesis（例如「德國 2019 Energiewende 有社區共有改革」），標註為 hypothesis 而非 fact |
| **SearchVerify** | 用 Google Search + HTTP Scrape 去查驗證 |
| **Confirmed** | 找到可靠來源佐證 → 進入 candidate list |
| **Rejected_PV** | 找不到任何來源 → 視為潛在 hallucination，丟棄 |

**跟現有機制的關係**：Propose-Verify + 現有 Hallucination Guard + CoV 形成三層事實保護。(1) Propose-Verify 在 input 端攔截（LLM 假設未經驗證不採用），(2) CoV 在 Critic 端查核（引用是否正確），(3) Hallucination Guard 在 output 端兜底（移除 phantom sources）。

### ConsistencyCheck（一致性檢查）— 📋 計畫中

| 狀態 | 白話解釋 |
|------|----------|
| **CheckMasterBDrift** | 計算 `diff(current master B, initial master B)`，偵測 research 方向是否偏離初始架構 |
| **DriftDetected** | 偏離超過閾值 — research 發現的 evidence 跟初始假設矛盾 |
| **EmitNarrativeTransition** | 生成「讀豹對話轉折」訊息。不是 popup，是讀豹在對話中自然的一句話：「欸等等，我剛翻的那篇... 好像不是我以為的那樣，我去換一篇。」 |
| **NoDrift** | 方向一致，繼續正常流程 |

**不是新組件**：是現有 `reasoning/agents/critic.py` 的擴充。新增 `review_consistency()` method 跟現有 `review()` 並列，不合併。

### EventBasedOutput（事件式輸出）— 📋 計畫中

| 狀態 | 白話解釋 |
|------|----------|
| **ResearchMilestone** | Research Agent 完成一個 milestone（例如「查完德國案例」「verify 完第一批候選」） |
| **ChatAgentFormat** | Chat Agent 用讀豹的語氣把 milestone 整理成一則 message |
| **PushToConversation** | 用現有 chat push message 機制送到對話。前端零新工作 |

**不做 streaming**：CEO 明確決定不做 character-by-character streaming（前端已夠複雜，那只是小加分）。改用 event-based messages — Research Agent 完成 milestone → 送 event → Chat Agent 整理 → push。

---

## 6. Chat 系統狀態

### 6.1 WebSocket Manager

#### 這張圖在說什麼？

管理所有 WebSocket 連線的「總管」。

#### 主要功能

| 操作                     | 白話解釋                |
| ---------------------- | ------------------- |
| **JoinConversation**   | 新使用者加入對話室           |
| **LeaveConversation**  | 使用者離開對話室            |
| **BroadcastMessage**   | 把訊息廣播給對話室內所有人       |
| **CleanupConnections** | 定期清理已斷線的連線（避免記憶體洩漏） |

#### 生活化比喻

想像 WebSocket Manager 是一個聊天室的管理員：

- 有人進來，登記名字，分配座位
- 有人發言，轉述給其他人聽
- 有人離開，清理座位
- 定期檢查有沒有人已經睡著（斷線）

### 6.2 Conversation Manager

#### 這張圖在說什麼？

管理「對話」本身的邏輯，包括訊息處理和參與者管理。

#### 對話模式

| 模式         | 條件             | 特性                      |
| ---------- | -------------- | ----------------------- |
| **SINGLE** | 1 人類 + 1 AI    | 快速回應（100ms timeout）     |
| **MULTI**  | 2+ 人類 或 3+ 總人數 | 等待其他人輸入（2000ms timeout） |

#### 訊息處理流程

```
收到訊息 → 驗證對話存在 → 檢查佇列限制 → 儲存訊息 → 派送給參與者 → 廣播到 WebSocket
```

#### 參與者類型

| 類型                   | 說明                     |
| -------------------- | ---------------------- |
| **HumanParticipant** | 真人使用者                  |
| **NLWebParticipant** | AI 助手（包裝 NLWebHandler） |

---

## 7. SSE 串流狀態

### 這張圖在說什麼？

描述 SSE（Server-Sent Events）如何傳送搜尋結果給前端。

### 事件類型

| 事件                         | 用途   | 前端行為            |
| -------------------------- | ---- | --------------- |
| **begin-nlweb-response**   | 開始處理 | 顯示載入動畫          |
| **intermediate_result**    | 中間結果 | 顯示進度或臨時結果       |
| **result**                 | 正式結果 | 渲染搜尋結果卡片        |
| **status**                 | 狀態訊息 | 顯示「正在搜尋 XXX...」 |
| **clarification_required** | 需要澄清 | 顯示選項讓使用者選擇      |
| **results_map**            | 地圖資料 | 渲染地圖元件          |
| **end-nlweb-response**     | 處理完成 | 隱藏載入動畫，啟用輸入框    |

### 為什麼用 SSE 而不是 WebSocket？

| SSE                 | WebSocket  |
| ------------------- | ---------- |
| 單向（Server → Client） | 雙向         |
| HTTP 標準，防火牆友好       | 需要 Upgrade |
| 自動重連                | 需要自己實作     |
| 適合「一次性查詢」           | 適合「持續對話」   |

搜尋是「一問一答」的場景，用 SSE 比較簡單。Chat 需要雙向溝通，才用 WebSocket。

---

## 8. 錯誤處理狀態

### 這張圖在說什麼？

描述系統如何處理各種錯誤情況。

### 錯誤分類

| 錯誤類型                | 觸發原因             | 處理方式            |
| ------------------- | ---------------- | --------------- |
| **ConnectionError** | 使用者關閉瀏覽器、網路斷線    | 標記連線已死，清理資源     |
| **TimeoutError**    | LLM 回應太慢、資料庫查詢超時 | 記錄日誌，回傳超時訊息     |
| **ValidationError** | 參數格式錯誤、缺少必要欄位    | 回傳 400 錯誤和說明    |
| **LLMError**        | LLM API 錯誤、額度用盡  | 重試或降級回應         |
| **RetrievalError**  | PostgreSQL 連線失敗、查詢錯誤 | 記錄日誌，回傳空結果      |
| **UnknownError**    | 其他未預期錯誤          | 測試模式拋出，正式模式優雅處理 |

### LLM 錯誤的重試策略

```
第一次失敗 → 等 1 秒 → 重試
第二次失敗 → 等 2 秒 → 重試
第三次失敗 → 放棄，使用降級回應
```

### 測試模式 vs 正式模式

| 模式       | 錯誤處理          | 目的        |
| -------- | ------------- | --------- |
| **測試模式** | 直接拋出例外        | 開發時快速發現問題 |
| **正式模式** | 優雅處理，回傳通用錯誤訊息 | 避免洩漏內部細節  |

---

## 9. Handler State

### 這張圖在說什麼？

描述 `NLWebHandlerState` 如何管理查詢處理的狀態。

### Precheck Steps

每個 precheck 任務都有三個狀態：

```
Initial → Running → Done
```

系統用 `asyncio.Event` 追蹤每個任務的完成狀態。

### 中止條件檢查

在 Fast Track（快速通道）模式下，系統會檢查是否該提早結束：

| 檢查項目                             | 中止原因           |
| -------------------------------- | -------------- |
| **query_done**                   | 查詢已經完成（例如直接回答） |
| **query_is_irrelevant**          | 查詢與網站無關        |
| **required_info_found**          | 已經找到需要的資訊      |
| **requires_decontextualization** | 需要等去脈絡化完成      |
| **connection_alive**             | 使用者已斷線         |
| **top_tool != 'search'**         | 決定不走搜尋流程       |

如果任一條件成立，Fast Track 會被中止，改走正常流程。

---

## 10. 完整生命週期

### 這張圖在說什麼？

用時序圖展示一個完整請求從頭到尾的流程。

### Standard Search 流程

```
1. Client 發送請求
2. Server 驗證（Auth + CORS）
3. 建立 Handler
4. 發送 begin-nlweb-response
5. 並行執行 5 個 pre-check 任務
6. 執行 pgvector 向量搜尋 + pg_bigm 關鍵字搜尋（PostgreSQL hybrid search）
7. 並行執行 LLM 排序（高分先送）
8. 可選：XGBoost 影子預測
9. 可選：MMR 多樣性重排
10. 後處理（地圖、摘要）
11. 發送 end-nlweb-response
```

### Deep Research 流程

```
1-4. 同上
5. 並行 pre-check + 澄清檢查
6. 執行檢索
7. Actor-Critic 迴圈（可能多次）
   - Analyst 分析
   - Gap Detection → 可能觸發二次搜尋
   - Critic 審查
   - 如果 REJECT，回到 Analyst
8. Writer 撰寫報告
9. Hallucination Guard 檢查
10. 後處理
11. 發送 end-nlweb-response
```

### WebSocket Chat 流程

```
1. Client 建立 WebSocket 連線
2. Server 回應 connected
3. 迴圈：
   - Client 發送訊息
   - ConversationManager 處理
   - 廣播給其他參與者
   - 如果有 AI 參與者，呼叫 NLWebHandler
   - 串流回應給 Client
4. Client 斷開連線
5. Server 清理資源
```

---

## 對應關係：State Machine ↔ architecture.html

| State Machine 區域 | architecture.html 模組                              |
| ---------------- | ------------------------------------------------- |
| Server Startup   | Infrastructure                                    |
| Connection Layer | Input (API Gateway)                               |
| Pre-Retrieval    | Input (Query Decomposition)                       |
| Retrieval        | Retrieval (Internal Search, Web Search)           |
| Ranking Pipeline | Ranking (Rule Weight, XGBoost, MMR)               |
| Reasoning System | Reasoning (Orchestrator, Analyst, Critic, Writer) |
| Post-Ranking     | Output (Summarize)                                |
| Chat System      | Infrastructure (Cache) + Output (Frontend UI)     |
| SSE Streaming    | Output (API Gateway)                              |

---

## 總結

這份 State Machine Diagram 完整描述了：

1. **系統生命週期**：從啟動到關閉
2. **連線管理**：HTTP 和 WebSocket 兩種通道
3. **查詢處理**：從接收到回應的完整流程
4. **排序邏輯**：LLM + XGBoost + MMR 三層排序
5. **推論引擎**：Actor-Critic 迴圈的詳細步驟
6. **Major Upgrade 擴充**：Non-blocking Research、Association Layer、Propose-Verify、Consistency Check、Event-Based Output（§5.3）
7. **Chat 系統**：WebSocket 和對話管理
8. **串流機制**：SSE 事件的順序
9. **錯誤處理**：各類錯誤的應對策略

這些圖與 `architecture.html` 的視覺化架構圖是互補的：

- **architecture.html**：展示「模組」和「資料流」（What）
- **state-machine-diagram**：展示「狀態」和「轉換條件」（How）

---

## 11. 兩種視角的對照：Data Flow vs State Machine

### 為什麼有兩份架構文件？

| 文件                                           | 視角          | 主要用途        |
| -------------------------------------------- | ----------- | ----------- |
| **Data Flow Architecture** (Source of Truth) | 模組與組件、資料流向  | 規劃開發藍圖、追蹤進度 |
| **State Machine Diagram**                    | 狀態與轉換、運行時行為 | 理解系統運作、除錯參考 |

兩者是**互補**關係：

- Data Flow 回答「系統**有什麼**」和「資料**怎麼流**」
- State Machine 回答「系統**怎麼運作**」和「狀態**怎麼轉換**」

---

### 模組對照表

| Data Flow 模組                 | State Machine 對應區域                   | 覆蓋狀態                    |
| ---------------------------- | ------------------------------------ | ----------------------- |
| **Module 0: Indexing**       | ❌ 無對應                                | 未實作，State Machine 不涵蓋   |
| **Module 1: Input**          | §2 Connection Layer + §3 HandlerInit | 部分對應                    |
| **Module 2: Retrieval**      | §3 Prepare.Retrieval                 | 部分對應（僅 Internal Search） |
| **Module 3: Ranking**        | §4 Ranking Pipeline                  | ✅ 完整對應                  |
| **Module 4: Reasoning**      | §5 Reasoning System + §5.3 Major Upgrade | ✅ 完整對應 + 📋 升級計畫      |
| **Module 5: Output**         | §7 SSE Streaming                     | 部分對應（僅串流）               |
| **Module 6: Infrastructure** | §1 Server Startup                    | 部分對應                    |

---

### 詳細差異分析

#### Module 0: Indexing（State Machine 無覆蓋）

Data Flow 架構中的 Indexing 模組包含完整的資料工廠設計：

| Data Flow 組件     | 狀態       | State Machine 覆蓋           |
| ---------------- | -------- | -------------------------- |
| PostgreSQL (pgvector + pg_bigm) | Partial  | ⚠️ 僅在 §3 Retrieval 中作為查詢目標 |
| Data Chunking    | Not Done | ❌                          |
| Auto Crawler     | Not Done | ❌                          |
| Format Detector  | Not Done | ❌                          |
| Quality Gate     | Not Done | ❌                          |
| Light NER        | Not Done | ❌                          |
| Source Authority | Not Done | ❌                          |
| Domain Allowlist | Not Done | ❌                          |
| Regex Parser     | Not Done | ❌                          |
| Anomaly Detector | Not Done | ❌                          |

**說明**：Indexing 是離線批次處理，不屬於請求處理的狀態機。未來可考慮新增「Indexing Pipeline State Machine」。

---

#### Module 1: Input（部分覆蓋）

| Data Flow 組件        | 狀態       | State Machine 對應                                          |
| ------------------- | -------- | --------------------------------------------------------- |
| Prompt Guardrails   | Not Done | ❌ 未實作                                                     |
| Upload Gateway      | Not Done | ❌ 未實作                                                     |
| Query Decomposition | Done     | ✅ §3 ParallelPreChecks（但 State Machine 歸類於 Pre-Retrieval） |

**架構差異**：

- Data Flow 將 Query Decomposition 歸類於 Input 模組
- State Machine 將其歸類於 Request Processing 的 Pre-Retrieval 階段
- 這是**視角不同**：Data Flow 看「功能歸屬」，State Machine 看「執行時序」

---

#### Module 2: Retrieval（部分覆蓋）

| Data Flow 組件            | 狀態       | State Machine 對應                              |
| ----------------------- | -------- | --------------------------------------------- |
| Internal Search         | Partial  | ✅ §3 Retrieval（VectorSearch + TemporalFilter） |
| Web Search              | Not Done | ❌ 未實作                                         |
| Custom Source Search    | Not Done | ❌ 未實作                                         |
| Multi-search Integrator | Not Done | ❌ 未實作                                         |

**說明**：State Machine 僅涵蓋已實作的 Internal Search（PostgreSQL pgvector 向量檢索 + pg_bigm 關鍵字檢索 hybrid search）。Data Flow 規劃的 Web Search 與 Custom Source 屬於未來擴展。

---

#### Module 3: Ranking（完整覆蓋）

| Data Flow 組件    | 狀態       | State Machine 對應                    |
| --------------- | -------- | ----------------------------------- |
| MMR             | Done     | ✅ §4 MMRCheck → ApplyMMR            |
| XGBoost Ranking | Done     | ✅ §4 XGBoostShadow → LogPredictions |
| Rule Weight     | Done     | ✅ §4 ParallelRanking（內嵌於 LLM 評分邏輯）  |
| LLM Weight      | Not Done | ⚠️ State Machine 有 LLM 排序，但非動態權重調整  |

**架構一致性**：Ranking 模組的 State Machine 覆蓋率最高，反映該模組已完成度較高。

---

#### Module 4: Reasoning（完整覆蓋）

| Data Flow 組件               | 狀態       | State Machine 對應                        |
| -------------------------- | -------- | --------------------------------------- |
| Deep Research Orchestrator | Partial  | ✅ §5.2 ActorCriticLoop 完整描述             |
| Clarification Agent        | Partial  | ✅ §5.1 ClarificationCheck               |
| Time Range Extractor       | Done     | ✅ §3 TimeRangeExtract（Pre-Retrieval 階段） |
| Analyst Agent              | Partial  | ✅ §5.2 AnalystPhase                     |
| KG & Gap Detection         | Not Done | ⚠️ §5.2 GapSearch 有基本實作，但無完整 KG         |
| Critic Agent               | Partial  | ✅ §5.2 CriticPhase                      |
| Gap Analysis Logic         | Not Done | ⚠️ §5.2 ProcessGapResolutions 有部分實作     |
| Writer Agent               | Partial  | ✅ §5.2 WriterPhase                      |

**架構差異**：

- Data Flow 將 Time Range Extractor 歸類於 Reasoning
- State Machine 將其歸類於 Pre-Retrieval（因為它在 Retrieval 之前執行）
- 這是正確的，因為 Time Range 影響檢索過濾，而非推論邏輯

---

#### Module 5: Output（部分覆蓋）

| Data Flow 組件        | 狀態       | State Machine 對應                 |
| ------------------- | -------- | -------------------------------- |
| API Gateway         | Done     | ✅ §1 SetupRoutes + §2 RouteMatch |
| Frontend UI         | Partial  | ⚠️ 無 State Machine（前端狀態應獨立管理）    |
| LLM Safety Net      | Not Done | ❌ 未實作                            |
| Visualizer Engine   | Not Done | ❌ 未實作                            |
| Graph Editor        | Not Done | ❌ 未實作                            |
| Dashboard UI        | Not Done | ❌ 未實作                            |
| Source & Collab Mgr | Not Done | ❌ 未實作                            |
| Export Service      | Partial  | ❌ State Machine 未涵蓋              |

**說明**：State Machine 主要關注後端串流輸出（§7 SSE Streaming），前端視覺化元件不在 State Machine 範疇。

---

#### Module 6: Infrastructure（部分覆蓋）

| Data Flow 組件      | 狀態       | State Machine 對應                     |
| ----------------- | -------- | ------------------------------------ |
| Postgres DB       | Done     | ⚠️ 作為查詢目標，無專屬狀態                      |
| In-Memory Cache   | Done     | ✅ §3 CacheResults                    |
| SQLite DB         | Done     | ⚠️ 作為查詢目標，無專屬狀態                      |
| User Data Storage | Not Done | ❌ 未實作                                |
| LLM Service       | Done     | ✅ §4 AskLLM、§5 Analyst/Critic/Writer |
| Analytics Engine  | Done     | ⚠️ 作為旁支日誌，無專屬狀態                      |

---

### Data Flow 的核心流程 vs State Machine

#### Data Flow 描述的核心流程

```
Ingestion:
  Domain Allowlist → Auto Crawler → Format Detect → Quality Gate → Light NER → Chunking → PostgreSQL (pgvector + pg_bigm)

Query Processing:
  API Gateway → LLM Safety Net → Prompt Guardrails → Query Decomposition

Retrieval Strategy:
  Query Decomposition → (Internal + Web + Custom) → Multi-search Integrator

Ranking:
  Retrieval → LLM Weight → Rule Weight → XGBoost → MMR

Reasoning Loop:
  Orchestrator → Clarification → Analyst → KG/Gap → Critic → Writer (或 迭代回 Orchestrator)

Output:
  Writer → API → LLM Safety Net → Frontend → Visualizer/Dashboard/Export
```

#### State Machine 描述的相同流程

```
§1 ServerStartup → §2 HTTP/WebSocket Connection →
§3 HandlerInit → SendBeginResponse → ParallelPreChecks → Retrieval →
§4 ParallelRanking → XGBoostShadow → MMR →
§5 (if Deep Research) ActorCriticLoop → WriterPhase → HallucinationGuard →
§3 PostRanking → CacheResults → §7 SendEndResponse
```

**主要差異**：
| 面向 | Data Flow | State Machine |
|------|-----------|---------------|
| Ingestion | 詳細描述 | 不涵蓋 |
| Safety Checks | 入口 + 出口雙重檢查 | 僅 Hallucination Guard |
| Multi-source Retrieval | 整合 3 種來源 | 僅 Internal Search |
| Gap Resolution | 抽象描述 | 詳細狀態（Tier 6 APIs） |
| Output Rendering | 多種視覺化選項 | 僅 SSE 串流 |

---

### 如何使用這兩份文件

| 場景      | 使用文件                                  |
| ------- | ------------------------------------- |
| 規劃新功能   | Data Flow Architecture                |
| 追蹤開發進度  | Data Flow Architecture                |
| 理解系統運作  | State Machine Diagram                 |
| 除錯運行時問題 | State Machine Diagram                 |
| 設計新模組   | 兩者皆用（Data Flow 定位，State Machine 定義行為） |
| 撰寫技術文件  | 兩者皆用                                  |

---

### 建議：讓兩份文件保持同步

1. **Data Flow 新增元件** → 更新 State Machine（如果該元件有運行時狀態）
2. **State Machine 新增狀態** → 確認 Data Flow 有對應元件
3. **元件完成度變更** → 同步更新兩份文件
4. **定期 Review**：每個 Sprint 結束時檢查兩份文件的一致性

---

*文件更新：2026-04-13*
