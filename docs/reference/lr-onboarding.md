# LR (Live Research) 新進工程師導讀

> **對象**：第一次接觸 Live Research（LR）模組、要參與其開發的工程師。
> **目標**：用一份文件把你從「完全不懂 LR」帶到「知道每一層在哪、怎麼串、為什麼這樣設計」，從 high-level 架構一路到 code / prompt / dependency 的 file:line。
> **產生方式**：2026-06-19 對 `main` branch code reality 做 6 路平行挖掘（入口路由 / 6-stage 引擎 / prompts / DR-parity 防禦 / 前端 / 持久化依賴），行號為當下 snapshot。
> **權威來源**：本文是「導覽地圖」，遇到衝突以 `docs/specs/live-research-spec.md`（規格）+ code 本身（事實）為準。spec 與 code 有 drift 之處本文會明確標 ⚠️。

---

## 0. 30 秒先有的心智模型

LR 是讀豹的**即時研究追蹤模式**：使用者問一個研究問題，系統不是一次吐答案，而是分成 **6 個 Stage 的對話 loop**，每個 Stage 跑完會停在一個 **checkpoint** 等使用者回覆（確認 / 調整 / 退回），最後在前端渲染完整研究報告。

一句話區分 LR 與既有 Deep Research（DR）：

> **DR = 4-phase 自動串接管線，跑完給結果。LR = 在類似的研究能力之上，包一層「6-stage 對話 loop」，每階段可停、可改、可退。差異在前端互動深度，不在底層研究能力。**

最容易誤會的三件事（先記住，後面會展開）：

1. **路由不是 `generate_mode` 參數**：spec 早期表格寫 `generate_mode=deep_research + enable_live_research=true`，**實際 code 用獨立路由 `/api/live_research`**。以 code 為準。⚠️
2. **LR 不直接復用 DR 的 Composable Pipeline 4-phase**：它**自建 `BABLoopEngine` + 6-stage loop**，只在 Stage 1/2 內部複用同樣的 Associator / Critic / Analyst agent。⚠️
3. **品質防禦有三層**（citation guard → grounding/specificity → publish gate），這是 LR 最複雜、新人最看不懂的部分，第 5 節專門講。

---

## 1. 系統分層全景圖

```
┌──────────────────────────────────────────────────────────────────────┐
│ 前端 (static/js/)                                                       │
│   live-research.js  ← LR 主邏輯（發起 / SSE handler / accordion / 報告）  │
│   news-search.js / main.js  ← 載入 live-research.js（cache-bust importer）│
│   features/text-fragment.js  ← citation highlight 雙錨點共用庫           │
│   features/lr-snapshot.js / lr-resume-classify.js  ← 回顧 / resume       │
└──────────────────────────────────────────────────────────────────────┘
            │ HTTP POST /api/live_research  +  SSE 串流（JWT in cookie/header）
┌──────────────────────────────────────────────────────────────────────┐
│ 後端                                                                    │
│  webserver/middleware/auth.py        ← JWT 驗證（LR 非 public endpoint）  │
│  webserver/routes/api.py             ← 路由註冊 + start/continue handler  │
│  methods/live_research.py            ← LiveResearchHandler（session/state）│
│      ↓ 建立並 await                                                       │
│  reasoning/live_research/orchestrator.py  ← LiveResearchOrchestrator      │
│      │  6-stage 對話 loop 主控 + checkpoint emit + 三層守門呼叫           │
│      ├─ loop_engine.py    ← BABLoopEngine（B→A→B' 迴圈、mini-reasoning）  │
│      ├─ stage_state.py    ← LiveResearchStageState（狀態容器 + 序列化）    │
│      ├─ lr_copy.py        ← user-facing 旁白文案集中常數                  │
│      ├─ hallucination_guard.py ← L1 citation / L2 grounding / specificity│
│      └─ sse_emit.py       ← emit_sse 統一出口（雙路 fallback）            │
│      ↓ 內部複用                                                          │
│  reasoning/agents/{associator,analyst,critic,writer}.py ← LLM 角色 agent │
│  reasoning/prompts/*.py   ← 各角色 prompt builder                        │
│  reasoning/schemas*.py    ← structured output schema                     │
│      ↓ 依賴                                                              │
│  core/retriever.py + postgres_client.py ← 混合檢索（向量 + pg_bigm）      │
│  OpenAI (gpt-5.1 / gpt-4o-mini) · OpenRouter (Qwen3-4B embed) · PG · Web │
└──────────────────────────────────────────────────────────────────────┘
```

**讀這份文件的建議順序**：第 2 節（請求怎麼進來）→ 第 3 節（6-stage 引擎，全文重心）→ 第 4 節（prompt）→ 第 5 節（品質防禦）→ 第 6 節（前端）→ 第 7 節（持久化 + 依賴）。

---

## 2. 入口與路由：一個請求怎麼變成一場研究

### 2.1 完整 call chain（HTTP → Orchestrator）

```
[前端] performLiveResearch(query)                       static/js/features/live-research.js:2481
   → authManager.authenticatedFetch(POST /api/live_research, {query, enable_web_search, enable_gap_enrichment})
        │
[middleware] auth_middleware(request, handler)          code/python/webserver/middleware/auth.py:78
   → jwt.decode(token, secret, HS256)                   auth.py:142
   → request['user'] = {user_id, org_id, authenticated:True}   auth.py:175
        │  (LR 非 PUBLIC_ENDPOINTS，無有效 JWT → 401)
[route] live_research_start_handler(request)            code/python/webserver/routes/api.py:1143
   → body = await request.json()                        api.py:1155
   → query_params['generate_mode'] = 'live_research'    api.py:1166
   → query_params['user_id'] = user['id']               api.py:1172
   → handler = LiveResearchHandler(query_params, wrapper)   api.py:1269
   → await handler.runQuery()                           api.py:1279
        │
[handler] LiveResearchHandler.runQuery()                code/python/methods/live_research.py:129
   → self.lr_session_id = await self._create_lr_session()   live_research.py:135
   → self.query_params['skip_clarification'] = 'true'   live_research.py:~192
   → await self.prepare()   # 繼承 DeepResearchHandler，做 retrieval
   → orchestrator = LiveResearchOrchestrator(handler=self, dry_run=...)   live_research.py:201
   → asyncio.create_task(orchestrator.start(query, initial_items=final_retrieved_items))
        │
[orchestrator] LiveResearchOrchestrator.start(query, initial_items)   orchestrator.py:914
   → state = LiveResearchStageState()
   → state.advance_to_stage(1)
   → state = await self._run_stage_1(state, query, initial_items)
        │  (Stage 1 跑完 emit checkpoint，等前端回覆走 /continue)
```

### 2.2 兩條路由

| 路由 | handler | 用途 |
|---|---|---|
| `POST /api/live_research` | `live_research_start_handler` (api.py:1143) | 開新研究，跑 `orchestrator.start()` |
| `POST /api/live_research/continue` | `live_research_continue_handler` | 從 checkpoint 續跑，跑 `orchestrator.continue_from_checkpoint()` |

> ⚠️ **新人坑 #1**：spec §1.1 對照表的「Mode 參數」欄寫 `generate_mode=deep_research + enable_live_research=true`，但實作是**獨立路由**，後端在 handler 內自己設 `query_params['generate_mode'] = 'live_research'`（api.py:1166）。前端打的就是 `/api/live_research`，沒有 `enable_live_research` 這個 flag。

### 2.3 Auth

- LR **不在** `PUBLIC_ENDPOINTS` 清單（auth.py:13-38），所以**一定要登入**。
- token 來源優先序：`Authorization: Bearer` header → cookie `access_token` → query param `auth_token`（auth.py:104-115）。
- 過期 / 無效 → 401（`jwt.ExpiredSignatureError` / `jwt.InvalidTokenError`，auth.py:155-166）。
- 測試帳號見 spec §5.5：`admin@twdubao.com` / `Test1234!`（大寫 T）。**不要找 auth bypass，bypass 已被刪（spec §5.4），測試一律真實登入。**

### 2.4 SSE 機制

所有即時訊息走 SSE，統一出口在 `reasoning/live_research/sse_emit.py:39`：

```python
async def emit_sse(handler, payload) -> bool:
    # 優先 handler.message_sender.send_message
    # fallback handler.http_handler.write_stream
    # 連線已斷（connection_alive_event 未 set）→ 提前 drop 回 False
```

LR 專屬的 SSE event type（`message_type` 欄位）：

| event type | emit 位置 | 前端 handler |
|---|---|---|
| `live_research_session_created` | handler 層 | `setLRSessionId()` |
| `live_research_narration` | orchestrator.py:~6911 / loop_engine.py:1634 | `addLRChatMessage('narration')` |
| `live_research_stage_change` | orchestrator.py:~6919 | `updateLRStageProgress()` |
| `live_research_writer_status` | orchestrator | 更新 typing indicator |
| `live_research_checkpoint` | orchestrator.py:6955 | `showLRCheckpoint()` + 存 snapshot |
| `live_research_section` | orchestrator.py:~6974 | `addLRSection()` |
| `live_research_export` / `final_result` | Stage 6 | `showLRExport()` |
| `research_phase` | loop_engine.py:~1654 | narration 標籤 |

> **設計重點**：`live_research_checkpoint` 與 `live_research_export` 是兩個「終止事件」——前端靠它們區分「正常停在 checkpoint 等回覆」vs「異常斷線」。

### 2.5 Query 前處理與生命週期（容易以為「LR 沒做」其實藏在父類）

新人常問：「我們處理 query 不是有 clarification、enrichment 一大堆機制嗎？怎麼文件上沒看到？」答案是：**LR 全都有，只是繼承自 DR，藏在父類 `DeepResearchHandler.prepare()` 裡**，不是 LR 自己沒做。

LR 的 query 前處理分**兩層**：

**第一層（HTTP request 內，繼承 DR `prepare()`）** —— `runQuery` 設 `skip_clarification=true` 後呼叫 `self.prepare()`，跑完整套父類前處理：

| 步驟 | 檔案 | 職責 | LR 是否有 |
|---|---|---|---|
| Decontextualization | `core/query_analysis/decontextualize.py` | 用對話歷史把查詢脫離脈絡成獨立問題 | ✅ 繼承 |
| QueryUnderstanding | `core/query_analysis/query_understanding.py` | **統一查詢分析**：query rewriting（改寫/擴展）+ time range 抽取 + author 偵測 + domain 偵測 | ✅ 繼承 |
| Tool Selection / Relevance / Guardrails / Memory | `baseHandler.prepare()` | 工具路由 / 相關性 / 安全 / 對話記憶 | ✅ 繼承 |
| Retrieval（向量 + 時間/作者過濾） | `baseHandler.py` | 用 rewritten queries 檢索 + temporal/author filter | ✅ 繼承 |
| **DR 同步 clarification dialog** | `deep_research.py` `_check_clarification_needed` | 彈多選單問歧義 | ❌ **跳過**（`skip_clarification=true`） |

**第二層（Stage 1 checkpoint，LR 專屬）** —— DR 那套「彈多選單問清楚」的同步 clarification 被**跳過**，由 **Stage 1 的 Associator B→A→B' loop + ContextMap 對話取代**。ContextMap 就是「升級版 clarification」：非同步、多輪、可逐個 topic 修改，比 DR 的單向問卷強。

> ⚠️ **新人坑 #5 — 兩個「enrichment」不是同一個東西**（這是最容易混淆的點）：
>
> | | **Query Enrichment** | **Gap (Knowledge) Enrichment** |
> |---|---|---|
> | 在哪 | `query_understanding.py`（prepare 階段） | `loop_engine.py` BAB loop（Stage 1-2） |
> | 做什麼 | 改寫/擴展查詢、抽時間/作者 | 偵測知識缺口 → 補搜四類（LLM/Wiki/Web/Internal） |
> | 何時 | 一次（retrieval 前） | 多次（研究迴圈中） |
> | DR 有嗎 | ✅ 有 | ❌ 無（LR 專屬） |
> | flag | 無 | `enable_gap_enrichment`（per-request）+ `gap_knowledge_enrichment`（process-wide） |
>
> 你在 §5.3 看到的 gap routing 是**後者**；查詢改寫是**前者**。同名不同層。

---

## 3. 6-Stage 引擎（全文重心）

主檔案：`code/python/reasoning/live_research/orchestrator.py`（近 7000 行，**不要整檔讀**，用 indexer 定位後讀必要段落）。

### 3.1 兩個主入口

```python
# 開新研究
async def start(self, query, initial_items=None) -> LiveResearchStageState   # orchestrator.py:914
    state = LiveResearchStageState(); state.advance_to_stage(1)
    return await self._run_stage_1(state, query, initial_items)

# 從任何 checkpoint 續跑（user reply 進來走這裡）
async def continue_from_checkpoint(self, state, user_message="",
                                   auto_continue=False, nav_action="")        # orchestrator.py:936
    # nav_action: ""=正常前進 / "back_one"=退一階 / "restart"=回 Stage 1
```

`continue_from_checkpoint` 是整個對話 loop 的中樞：讀 `state.current_stage` → 決定呼叫哪個 `_handle_stage_N_response` → 判斷是否 `complete_stage()` 進下一階。

### 3.2 六個 Stage：職責 + handler 行號

| Stage | 名稱 | 主函式 (orchestrator.py) | reply handler | 一句話職責 |
|---|---|---|---|---|
| 0 | Retrieval | （無獨立 handler，是 BAB Phase 0 input）`loop_engine.py:405 _execute_search` | — | 站內混合檢索取 raw docs，餵進 BAB |
| 1 | BAB 資料蒐集面聚焦 | `_run_stage_1` :1142 | `_handle_stage_1_response` :1298 | 跑 BABLoopEngine 建研究結構（ContextMap），提研究結構提案 |
| 2 | Per-section BAB | `_run_stage_2` :2021 | `_handle_stage_2_response` :2165 | 對每個 core topic 跑 per-section BAB 蒐章節 detail |
| 3 | Style Analysis | `_run_stage_3` :2249 | `_handle_stage_3_response` :2267 | 問是否給文筆範本，抽 style features |
| 4 | Format Spec | `_run_stage_4` :3153 | `_handle_stage_4_response` :3171 | 收格式偏好（字數 / 引用格式 / 特殊 element），typed-action 解析 |
| 5 | Writer | `_run_stage_5` :3810 | `_handle_stage_5_response` :5399 | **一次只寫一段**，寫完停 checkpoint 等 user reply（VP-7 反轉設計） |
| 6 | Export | `_run_stage_6` :6348 | — | 組全文 + references + KG，emit `final_result` |

> ⚠️ **行號會漂**：orchestrator 一直在改，上表行號是 2026-06-19 snapshot。**用 `python tools/indexer.py --search "_run_stage_5"` 重新定位**，不要硬背行號。

### 3.3 BAB Loop：B→A→B' 三段結構

Stage 1/2 的核心是 `BABLoopEngine`（`loop_engine.py:58`），它的 `run_loop` 跑一個 **Build→Analyze→Build'** 迴圈，預設最多 3 輪（`live_research_max_bab_iterations=3`）：

| Phase | 做什麼 | LLM 角色 / 函式 | model tier |
|---|---|---|---|
| 0 Build initial B | 從 query + 初始資料建 ContextMap | `AssociatorAgent.build_context_map()` | **high** (gpt-5.1) |
| 1 Derive A | 推導新搜尋策略 | `AssociatorAgent.derive_search_plan()` | low (gpt-4o-mini) |
| 2 Execute A | 執行檢索（站內 / 外部） | `BABLoopEngine._execute_search()` :405 | — |
| 3 Mini-reasoning | 分析新資料、出草稿 | `_run_mini_reasoning()`（Analyst high + Critic） | **high** |
| 4 Refine B→B' | 依新證據更新 ContextMap | `AssociatorAgent.refine_context_map()` | **high** |
| — Consistency | 偵測策略漂移，可能 pause | `_run_consistency_check()` | — |

提前終止：`is_stable=true` 或 `paused_by_consistency=true`。

Phase 3 裡藏兩個重要的自我修正迴圈（第 5 節細講）：
- **SEARCH_REQUIRED 二次補搜**：Analyst 說資料不夠 → 補搜一輪 → 重跑 Analyst。
- **REJECT→revise**：Critic 退回 → `analyst.revise()` 重寫（上限 1 輪）。

### 3.4 Checkpoint：emit → 等 reply → resume

```python
# 1. 跑到 stage 邊界，發 checkpoint 給前端
async def _emit_checkpoint(self, stage, proposal, context_map_summary="",
                           evidence_list=None, show_new_sample_button=False)  # :6955
# 2. 落 DB（每個 durable boundary 都存，斷線可復原）
async def _persist_checkpoint_boundary(self, state)                          # :4320
# 3. user reply 進來 → continue_from_checkpoint → _handle_stage_N_response
```

這就是 LR 的「對話」本質：每個 Stage 不是跑完就進下一個，而是**停下來、把提案 emit 給前端、等使用者點「確認 / 調整」才繼續**。

### 3.5 Stage 間 routing：user intent 怎麼決定下一步

`continue_from_checkpoint`（:936-1047）+ 各 `_handle_stage_N_response` 共同決定路由。關鍵分支：

| 觸發 | 行為 |
|---|---|
| Backward nav（`nav_action`） | 最優先攔截：`back_one`→`reset_to_stage(current-1)`；`restart`→`reset_to_stage(1)`；含 `pending_restart_confirmation` 兩段式確認（`meta is None` fail-loud） |
| Stage 1/4 reframe | **confirm round 設計**：不立即 apply，set `pending_reframe_json` → re-emit checkpoint → user 下一輪才真套用 |
| Stage 3/5 dialogue | 可多輪確認**不 advance**（stage_status 維持 "checkpoint"） |
| Stage 5 recollect | 退回 Stage 1 補搜，四段式 confirm（確認 token / abort / 短肯定兜底 / fall through），cap 預設 2，`_dispatch_recollect` :3736 |
| Stage 5 → 6 | **completeness gate**：沒寫完不准匯出，block + checkpoint「還有 N 段沒寫」 |

### 3.6 兩個「清狀態」函式（容易搞混）

| 函式 | 觸發 | 清什麼 / 留什麼 |
|---|---|---|
| `reset_for_recollect` (stage_state.py:~571) | Stage 5 退回補搜 | **清**下游（written_sections / outline / format chapters / KG / critic reviews）；**留** evidence_pool（疊加非清空）/ context_map / style / 設定 / audit log |
| `reset_to_stage(target)` (stage_state.py:~510) | backward nav | 依 target 清不同層；evidence_pool / context_map / user_voice 主欄位永遠保留 |

> **設計哲學**：退回時**證據池（evidence_pool）永遠保留疊加**，不會把已花錢蒐集的資料丟掉重來——這是省錢 + 不破壞 state 的核心紀律。

### 3.7 補資料 / 退回的三個機制（誰能發起，邊界在哪）

LR 有三條「補資料 / 退回」路徑，差別在**誰發起**和**燒錢規模**。規律：**自動補搜只發生在便宜、局部、早期（BAB 階段）；一旦要動「貴、整體、退回重來」，決定權一律交還 user 並要求二次確認。**

| 機制 | 誰發起 | 要 user 同意？ | 動到什麼 | 燒錢 |
|---|---|---|---|---|
| SEARCH_REQUIRED 二次補搜（§5.5） | **Analyst 自動** | 否 | 章內補搜一輪（cap 3 query）再重跑 Analyst | 小 |
| gap routing 四類（§5.3） | **Analyst 自動** | 否 | 補 LLM/Wiki/Web/Internal 進 evidence_pool（cap 6/run） | 小 |
| **Stage 5 退回補搜（recollect）** | **只能 user** | **是**（四段式 consent + cap 2） | `reset_for_recollect` → 重跑整個 Stage 1 BAB | **大** |

> ⚠️ **新人坑 #6 — Writer 無權自己退回**：`_dispatch_recollect`（執行退回補搜）在整個 orchestrator 裡**只有兩個呼叫點，都在 `_handle_stage_5_response`（處理 user reply）內**。Writer 寫到一半發現資料不夠時，做的是「降信心措辭 / 標 `blocked_no_evidence` / 跑守門」，**它不會、也無權自己決定退回 Stage 1 重蒐**。退回補搜的決定權完全在 user。

**前端按鈕現況（容易和 recollect 搞混）**：LR 前端有兩個 backward-nav 按鈕（`news-search-prototype.html:721-722`）——「← 退回上一階段」（`nav_action=back_one`）和「↺ 重新規劃」（`nav_action=restart`，回 Stage 1）。**但它們走 `reset_to_stage`，不補 evidence**——只是退回去改決定（ContextMap / 格式）。真正「資料不足 → 補 evidence」的 recollect **沒有專屬按鈕**，目前只能 user 在 Stage 5 打字觸發（intent parser 認得「再去找更多資料」「資料不夠去多查」這類，orchestrator.py:6285）。

> 這是個已知 UX 缺口（見 §8 後續票）：有按鈕的 backward-nav 不補資料；會補資料的 recollect 沒按鈕。所以引導文案要明確叫 user 回覆「再去找更多資料」，而不是讓他以為點「退回上一階段」按鈕就會補（那不會）。

---

## 4. Prompts 與旁白（narration）

### 4.1 四個 LLM 角色與 prompt builder

prompt 不在 orchestrator inline，而是集中在 `code/python/reasoning/prompts/*.py`，每個角色一個 builder：

| 角色 | 職責 | prompt builder | 核心方法 |
|---|---|---|---|
| **Builder (Associator)** | 建 / 衍生 / 精化 ContextMap | `prompts/associator.py` | `build_context_map_prompt` / `derive_search_plan_prompt` / `refine_context_map_prompt` |
| **Analyst** | 證據分析、出草稿 | `prompts/analyst.py` | `build_research_prompt` / `build_revision_prompt` |
| **Critic** | 邏輯審查、claim 查核 | `prompts/critic.py` | `build_review_prompt` / `build_section_publish_gate_prompt` |
| **Writer** | 分段寫報告 | `prompts/writer.py` | `build_section_compose_prompt`（LR 專用，:352） |

呼叫端的 agent wrapper 在 `reasoning/agents/{associator,analyst,critic,writer}.py`。

### 4.2 prompt 風格（摘錄，建立直覺）

讀豹的 prompt 是中文、人格化、強制 Actor-Critic 對抗：

```
# analyst.py:354  Analyst
你是新聞情報分析系統中的 **首席分析師**。
⚠️ 重要架構說明：你的輸出將會被另一個 **評論家 Agent (Critic)** 進行嚴格審查。
如果你的推論缺乏證據、違反來源模式設定，或包含邏輯謬誤，你的報告將被退回。

# critic.py:94  Critic
你是無情的 **邏輯審查員**。
你的唯一任務是審核 Analyst 提交的研究報告草稿。
你**不負責**搜尋新資訊，你負責確保報告在邏輯、事實引用與結構上的嚴謹性。

# associator.py:46  Builder
你是研究結構設計師。你的任務是從研究問題出發，建立一個全面的知識地圖（Context Map），
作為後續研究的骨架。

# writer.py:352  Writer（LR 專用，分段）
用於分段撰寫：每次呼叫撰寫報告中的一個章節，
注入格式規格（Stage 4）和文筆特徵（Stage 3 Style Analysis）。
```

### 4.3 prompt 組裝：template + 動態注入

prompt 用 f-string + 條件式區塊組裝（不是 Jinja template）。例如 Analyst（analyst.py:80-114）：

```python
prompt = self._build_base_research_prompt(...)            # 骨架
if enable_argument_graph:    prompt += self._build_argument_graph_instructions()
if enable_knowledge_graph:   prompt += self._build_knowledge_graph_instructions()
if enable_gap_enrichment:    prompt += self._build_gap_enrichment_instructions(enable_web_search)
if previous_draft:           prompt += "...修改任務 + 前草稿注入..."
if enable_live_research and context_map_summary:
    prompt += self._build_context_map_injection(context_map_summary)
```

動態注入來源：ContextMap summary、全 evidence_pool 的 grounding view、user voice / style features、time constraint。
**安全**：用 `generate_boundary_token()` + `wrap_content_with_boundary()` 包外部內容防 prompt injection（SEC-6）。

### 4.4 structured output schema

LLM 一律回 structured output，schema 在 `reasoning/schemas.py` / `reasoning/schemas_live.py`：

| 角色 | schema | 關鍵 enum / field |
|---|---|---|
| Analyst | `AnalystResearchOutput` | `status: "DRAFT_READY" | "SEARCH_REQUIRED"`, `draft`, `citations_used`, `new_queries` |
| Critic | `CriticReviewOutput` | `status: "PASS" | "WARN" | "REJECT"`, `suggestions` |
| Builder | `AssociatorBuildOutput` | ContextMap（topics / relations / search_seeds / narration） |
| Writer | `LiveWriterSectionOutput` | `section_content`, `citations`（`{cite:N}` placeholder，**不准** inline 寫 `[N]` 或 `(Author, Year)`） |
| Publish gate | `CriticSectionReview` | `verdict: "PASS" | "WARN" | "REJECT"`, `claim_issues`, `overall_explanation`, `cov_verification_summary`（schemas_live.py:124） |

> ⚠️ **新人坑 #2**：Writer 只能用 `{cite:N}` placeholder，真正的 APA / 連結是後處理階段才填。直接在 prompt 輸出 `[1]` 會被守門擋。
>
> 註：`f1_review_initial` / `cov_summary` / `final_verdict` 不是 `CriticSectionReview` 的 schema 欄位，而是 publish gate（`_run_publish_gate`）流程裡的 local 變數（`f1_review_final = f1_review_initial.model_copy(update={"cov_verification_summary": cov_summary})`）。在 `schemas_live.py` 找不到這三個名字是正常的。

### 4.5 lr_copy.py：旁白文案（不是 prompt）

`reasoning/live_research/lr_copy.py` 裝的是 **user-facing 旁白文案**（narration），由 orchestrator 透過 SSE emit，**不進 LLM prompt**。集中管理是為了能 AST 掃描禁止內部術語洩漏（test sentinel `test_lr_user_facing_strings_have_no_dev_jargon`）。

代表性常數：

| 常數 | 用途 |
|---|---|
| `chapter_word_overshoot_narration()` | 章節超字數軟提示（不觸發重寫） |
| `GROUNDING_UNAVAILABLE_NARRATION` | 自動查核故障旁白（per-run 只播一次） |
| `KG_MERGE_DEGRADED_NARRATION` | KG merge 失敗降級 |
| `SEARCH_REQUIRED_DEGRADED_NARRATION` | 補搜無結果降級 |
| `MINI_REASONING_REVISE_DEGRADED_NARRATION` | revise 失敗降級 |
| `RECOLLECT_CONSENT_PROMPT` / `RECOLLECT_CAPPED_NARRATION` | Stage 5 退回補搜 consent / 達上限 |
| `NAV_BACK_NOTICE` / `NAV_RESTART_CONFIRM_PROMPT` | backward-nav 旁白 |

> **No Silent Fail 紀律的體現**：每個降級路徑都有一句對應旁白——故障不靜默吞，一定告訴使用者「已降級」。`lr_copy.py` 裡這一票 `*_DEGRADED_NARRATION` 就是 CLAUDE.md「不可 silent fail」在 code 裡的落點。

---

## 5. DR-parity 品質防禦層（最複雜、最該懂）

這一層是 LR 「為什麼這麼複雜」的答案。讀豹賣的是**可信**，所以 Writer 寫出來的每段話在進入報告前都要過層層守門。**三層守門**全部串在 `_write_section`（orchestrator.py:4229）裡：

```
_write_section(chapter_idx, ...)
  ├─ L1  apply_hallucination_guard            # citation-id 白名單（deterministic）
  ├─ L2  entity_grounding_check + specificity_check   # 內容層 LLM grounding + auto-rewrite
  └─ L3  _run_publish_gate                     # F1 critic + F3 CoV-lite
```

### 5.1 L2 — Grounding Guard（`hallucination_guard.py`）

防「寫了 evidence 裡沒有的具體 entity」（幻覺）。

| 元件 | 函式 | 行 | 作用 |
|---|---|---|---|
| Entity grounding | `entity_grounding_check(section, chapter_evidence_text, handler, grounding_level="low")` | :373 | 列出 prose 裡字面 + 語意都對不到 evidence 的 entity |
| 三段組成 | 抽 entity → 字面命中過濾 → LLM 語意判定 | :373-412 | `_extract_entities_for_grounding` → `_deterministic_grounded_filter` → `_semantic_grounding_check` |
| 對稱守門 | `specificity_check(...)` | :415 | 反向偵測「evidence 有料但 prose 太抽象」（under-specification），回 True = 需 auto-rewrite |
| 失敗策略 (R1) | `GroundingCheckUnavailable` exception | :395 | 語意判定失敗 → raise → orchestrator 捕捉 → DR 式降級（保留正文 + 降信心 + methodology note），**不靜默回 `[]`** |

**過度封鎖（over-block）修法的四個關鍵決策**（hallucination_guard.py:1188 一帶）：
1. tier=low（資料好，誰判都行，省錢）；
2. evidence 看**全 pool** + 12000 字 budget cap + 4 級優先序（不只看本章）；
3. **partial block = 只刪未驗證句、保留全文**（不整章替換）；
4. 句子分類（:1193）：候選刪除句若含「已驗證 entity」或「citation 標記」或「上下文依賴連接詞」→ 保留；純未驗證句才直刪。

> ⚠️ **新人坑 #3**：grounding guard 走 **fail-open + 降級旁白**（不是 fail-closed）。理由：grounding LLM 抖動時若 fail-closed 會整段擋掉、重演 over-block 災難。代價是 LLM 爆窗時會放行 + 旁白，屬 silent-fail 邊界案例。改這塊前先讀 `lessons-live-research.md`。

### 5.2 L3 — Publish Gate（orchestrator.py:4534 `_run_publish_gate`）

每段寫完的最後一道審查，Track F（2026-05-28）。

- **F1 critic**（flag `live_research_critic_publish_gate`，預設 on）：claim-level fabrication 審查，六類 claim（numeric / temporal / causal / comparative / predictive / evaluative）。
- **F3 CoV-lite**（Chain of Verification，flag `cov_lite_enabled`，預設 on；LR 可用 `live_research_cov_lite_enabled` 子 flag 覆寫）。
- **Auto-escalate**：`contradicted_count > 0` → REJECT；`unverified_count >= 3` → WARN。
- **Verdict 三分支**：REJECT = 整章替換 + 列最多 5 處問題句；WARN = 降信心 + amber strip；PASS = 照常。
- **Fail 紀律 (E1)**：F1/F3 拋例外 → forensic log + 旁白（degrade-and-narrate，**非 fail-open**）。
- WARN explanation 自 2026-06-19 起**完整輸出不截斷**（舊 100 字 cap 已移除，那是某次修 bug 順手夾帶的副產物）。

### 5.3 Gap Routing 四類（loop_engine.py:`_process_gap_resolutions_lr`）

Analyst 偵測到知識缺口後，分四類補：

| 類型 | 行為 | gate |
|---|---|---|
| LLM_KNOWLEDGE | 把 LLM 知識建成 virtual doc 入 pool（source=llm_knowledge） | 無（一律跑） |
| WIKIPEDIA | 打 Wikipedia API；計入 cap | `enable_gap_enrichment` |
| WEB_SEARCH | 打 Google Custom Search；計入 cap | `enable_gap_enrichment` **且** `enable_web_search` |
| INTERNAL_SEARCH | no-op（已由 BAB main loop 處理，交下一輪 Associator） | — |

**外部呼叫 cap**：`gap_routing.max_external_calls_per_run=6`，達上限 log skip + `_narrate_gap_cap_once`（per-run dedup）。

> ⚠️ **「全開」其實是前端寫死、不是後端預設開**：後端 `live_research.py:48` 讀 `enable_gap_enrichment` 的 fallback 是 `'false'`（沒帶就關）。是**前端 `live-research.js` 寫死送 `true`**（2026-06-11 CEO 拍板 default-on）才讓四類全開。所以前端沒有「開關 UI」≠ 後端強制開——後端解析邏輯**已支援關閉**，哪天想做成讓 user 自選，改的是前端那兩行 + 加 UI，後端不用動。

### 5.4 Evidence Sufficiency（C-1 gate，orchestrator.py:609 `_compute_chapter_sufficiency`）

判「這章資料夠不夠」決定 writer 措辭（夠→逼具體；不夠→保守）：

```python
def _compute_chapter_sufficiency(analyst_citations, evidence_pool):
    if len(evidence_pool) == 0:  return "critical"
    if len(evidence_pool) <= 2:  return "thin"
    return "ok"
```

> ⚠️ **關鍵：sufficiency 不是「補資料機制」，它只分類 + 校準措辭**。它回傳 `critical/thin/ok` 後，唯一作用是塞進 writer kwargs（orchestrator.py:4938→4949）告訴 Writer「這章該用什麼語氣寫」（夠→逼具體、不夠→保守、空→更保守甚至標 `blocked_no_evidence` 不寫）。**它不會去呼叫 Analyst / Builder、也不觸發任何補搜。** 真正「補資料」是 §3.7 那三個機制（Analyst 自動補 / user 退回 recollect）。讀豹的立場：**到了寫作階段就不再自動補資料，只負責讓報告誠實**——避免 Writer 階段無限自動補搜燒錢失控。

> ⚠️ **2026-06-17 重要校正**：舊版用 `len(analyst_citations)` 判，但全局 evidence 模型下 writer 讀**全 pool**，analyst_citations 空 ≠ 沒 evidence → 會誤判「資料不足」。改用 `len(evidence_pool)`。這個 bug 是 LR 早期一個典型「配額判定 vs 實際用量不一致」的坑。

### 5.5 兩個自我修正迴圈（在 BAB Phase 3 內）

| 迴圈 | 觸發 | 行為 | 上限 |
|---|---|---|---|
| SEARCH_REQUIRED 二次補搜 | Analyst `status="SEARCH_REQUIRED"` + 非空 `new_queries` | dedup+strip+cap 3 條 → `_execute_search` 補 evidence → 重跑 Analyst | 1 輪 |
| REJECT→revise | Critic `status="REJECT"` + `revise_count < 1` | `analyst.revise()` 重寫 → re-review | 1 輪（vs DR 3 輪） |

兩者失敗都 forensic log + 旁白（`SEARCH_REQUIRED_DEGRADED_NARRATION` / `MINI_REASONING_REVISE_DEGRADED_NARRATION`），**不可冒泡成 silent fail**。

### 5.6 Feature Flags 速查（`config_reasoning.yaml`）

> **位置欄解讀**：多數在 `reasoning.features.*`；但有三個例外——`lr_recollect_cap` **不在 config**（是 code default，可被 `features` override）；`offline_max_*` 兩個在 `reasoning:` **頂層扁平 key**（不在 `features:` 段，刻意對齊 `analyst_timeout` 慣例）。別在 `features:` 段裡找這三個。

| flag | 預設 | 位置 | 說明 |
|---|---|---|---|
| `live_research` | true | `features` | LR 總開關 |
| `live_research_mock_bab` | **false** | `features` | Stage 1+2 用 fixture（省 BAB 錢），Stage 3-6 真實 LLM。**commit 前必須 false** |
| `live_research_dry_run` | false | `features` | mock agents 不打 LLM（純測試） |
| `live_research_critic_publish_gate` | true | `features` | F1 |
| `cov_lite_enabled` | true | `features` | F3 CoV 開關（**見下方 ⚠️**） |
| `gap_knowledge_enrichment` | true | `features` | Analyst prompt gap routing flag |
| `live_research_consistency_monitor` | true | `features` | BAB consistency monitor |
| `live_research_style_analysis` | true | `features` | Stage 3 |
| `live_research_max_bab_iterations` | 3 | `features` | BAB 最大輪數 |
| `max_results_lr` | 8 | `tier_6.web_search` | **每個 web search query 撈幾筆**（**見下方 ⚠️**） |
| `gap_routing.max_external_calls_per_run` | 6 | `tier_6.gap_routing` | gap 外部呼叫上限 |
| `lr_recollect_cap` | 2 | **code default**（不在 config） | Stage 5 退回補搜上限（`orchestrator.py:3734`，可被 `features` override） |
| `offline_max_checkpoint_advances` | 1 | `reasoning` 頂層 | 離線最多前進 checkpoint 數（防斷線重連循環燒錢） |
| `offline_max_wall_seconds` | 900 | `reasoning` 頂層 | 離線 wall-clock 硬上限 15 分鐘 |

> ⚠️ **`cov_lite_enabled` 名字會誤導**：它**不是「lite vs full」切換**（full 不存在）——on = 跑 CoV（critic.py:127 / orchestrator.py:4557）、off = **完全不跑 CoV**（直接 skip）。「lite」只是這個 CoV 實作的固定名字。**off 不是降級成 full，是沒有 CoV。** 另外 LR 有獨立子 flag `live_research_cov_lite_enabled`（fallback 到 `cov_lite_enabled`）——可以**只關 LR 的 CoV 而不動 DR**（逃生艙：若量到 LR per-section CoV 比 DR 貴可單獨關）。所以兩者不是「綁死共用」，是「共用預設 + LR 可獨立 override」。注意 code 預設 `.get(..., False)`，config 設 `true`——config 載入失敗會**靜默變沒 CoV**。

> ⚠️ **`max_results_lr=8` 只管 web search，且是 per-query**：它是「LR 每打**一個** Google web search query 最多撈 8 筆」的上限（loop_engine.py:613），**不管站內檢索**（站內走 `core/retriever.search()` 自己的 num_results）。BAB 每輪 3 query → web evidence 理論上限 `8 × 3 = 24 筆/輪`。與 DR 的 `max_results=5` **完全解耦**（兩鍵互不 fallback，避免調 DR 連帶改 LR）。**注意**：這 24 只是單輪 web 數字，全流程（多輪 + Stage 2 per-topic + 站內檢索）的 evidence 總量遠大於此（見 §8 B1）。

**已移除的 dead flag**（2026-06-19）：`live_research_propose_verify`、`live_research_per_section_writing` 作為 config flag 已徹底移除（零 `.py` consumer，config/spec 墓誌銘也已清掉，當它不存在）。
> ⚠️ **`live_research_narration` 是一名兩用**：作為 **config flag** 已移除；但**同名的 SSE event `live_research_narration`（§2.4 表）是活的、到處在用**（前端 narration 命脈）。看到這個名字先分清是在講 flag（死）還是 SSE event（活），別混淆。同理 Propose-Verify（§2 原則#9）是**設計概念（Beta 未實現）**不是 flag。

---

## 6. 前端層（`static/js/`）

主檔案 `static/js/features/live-research.js`。

### 6.1 發起 + SSE handler

```javascript
// 發起：static/js/features/live-research.js:2481
export async function performLiveResearch(query)
//   → authManager.authenticatedFetch(POST /api/live_research,
//       { query, session_id, site, enable_web_search:true, enable_gap_enrichment:true })

// SSE 分發：live-research.js:2190
export async function handleLiveResearchSSE(response, triggeringLRSid=null)
```

SSE event → handler 對照（live-research.js）：

| event | handler | 行 |
|---|---|---|
| `live_research_session_created` | `setLRSessionId()` | 2227 |
| `live_research_narration` | `addLRChatMessage('narration')` | 2238 |
| `live_research_stage_change` | `updateLRStageProgress()` | 2260 |
| `live_research_checkpoint` | `showLRCheckpoint()` + 存 snapshot | 2307 |
| `live_research_section` | `addLRSection()` | 2320 |
| `live_research_export` | `showLRExport()` → Stage 6 | 2346 |
| `research_phase` | narration 標籤 | 2370 |

### 6.2 Stage Accordion（6 個 dot）

`updateLRStageProgress(stage)`（:681）：6 個 `.lr-stage-dot[data-stage="1..6"]`，依當前階段加 `completed`/`active` class。
**點擊回顧**（完成後）：`wireLRStageNavigation(lrState)`（:1365）綁 click → `renderLRStageDialog(stageNum, snapshot)`（:1113）lazy render 該階段對話快照到 `#lrStageReview` 容器。

### 6.3 報告渲染：主路徑 vs fallback（`showLRExportFromState` :1548）

| 路徑 | 條件 | 行為 |
|---|---|---|
| **主路徑**（新 session） | `lrState.final_report_markdown` 非空 | `showLRExport(persisted, ...)`：**零重組，逐字一致**（後端組好整份字串） |
| **fallback**（舊 session） | `final_report_markdown` 空 | `renderLRReviewReport(lrState)`：從 `written_sections` 重組 + 顯眼 banner「回顧重建版，可能與下載檔略有差異」 |

KG 是 D3 圖不在字串裡，另走 `displayKGForReview(lrState)`（:1531）。

### 6.4 ⚠️ 新人坑 #4 — ESM cache-bust 三 importer 同步（這個 repo 反覆踩的坑）

`live-research.js` 被三個檔案 import，三處的 `?v=` **必須完全一致**：

| importer | 檔案 | 行 |
|---|---|---|
| HTML script tag | `static/news-search-prototype.html` | ~955 |
| main.js import | `static/js/main.js` | ~75 |
| news-search.js import | `static/news-search.js` | ~157 |

**為什麼**：瀏覽器把 `live-research.js?v=A` 和 `?v=B` 當**兩份獨立 module instance**，各自跑 module 頂層 code → shared state（如 `_currentLRSessionId`）分裂成多份 → `performLiveResearch` 在 instance A 設了 sessionId，`handleLiveResearchSSE` 在 instance B 讀不到 → 「找不到先前的研究 session」。

**規律**：版本號 `YYYYMMDD` + 字母尾綴（a/b/c…）。改 `live-research.js` 前先三處 grep 驗證一致，改完三處一起 bump。**目前版本：`20260619e`**（會持續往後 bump，以實際檔案為準）。

### 6.5 SSE 重連（`_doLRReconnect` :231，commit `a2a9c143`）

- **READ-ONLY 鐵律**：醒來重連只 GET 後端 state（`/api/sessions/{sid}`），**絕不送 POST `/continue`**（避免重複燒錢）。
- 401 處理：先檢查有無前台 refresh 在跑（避免並發 token 輪換），否則一次性裸 `fetch('/api/auth/refresh')`，失敗也**不彈 login modal**只溫和提示。
- 觸發：`online` / `visibilitychange`（切回分頁時），debounce 600ms。

> ⚠️ **新人坑 #7 — 切分頁 / 看 YouTube 不會中斷研究**：`visibilitychange` 會在你切回分頁時觸發 reconnect 流程，但 `_scheduleLRWakeReconnect`（:208）**第一行就是 `if (!_lrConnectionLost) return`**——沒「真的斷線過」就什麼都不做。而 `_lrConnectionLost` **只在 SSE 串流真的掛掉時才設 true**（`reader.read()` 拋非 AbortError，或串流結束卻沒收到 checkpoint/export 終止事件）。**切分頁、視窗失焦、最小化都不算斷線**——SSE 串流在背景繼續收資料。所以「丟著讓它跑、去回 LINE / 看 YouTube」完全 OK，研究跑在後端、前端只是觀眾，回來時串流還活著就自動更新、真斷了才 read-only 補一次 state。這套架構**就是為了**支援「切出去做別的事」而設計的。

### 6.6 Citation Text-Fragment Highlight（`features/text-fragment.js`）

點內文引用 → 跳到原文並 highlight。`buildTextFragmentUrl(url, quote)`（:41）用**雙錨點**：前 12 字 START + 後 12 字 END（`ANCHOR_LEN=12`），組 `#:~:text=START,END`。過短（<4 字）或不夠獨特（純標點 / 純數字 / 媒體名）→ 降級裸 URL。Python 端有鏡像契約 `tests/unit/reasoning/test_lr_textfragment_url.py` 確保前後端輸出一致。

---

## 7. 持久化與外部依賴

### 7.1 PG 持久化

| 項目 | 位置 | 說明 |
|---|---|---|
| 存哪 | `search_sessions` 表的 `live_research_state` JSONB 欄位 | — |
| state schema | `stage_state.py:122+` `LiveResearchStageState`（28+ fields） | — |
| `_save_state` | `methods/live_research.py:432` | `SessionService().update_session(..., {"live_research_state": state.to_dict()})` |
| `_load_state` | `methods/live_research.py:461` | 讀 `session["live_research_state"]` → `from_dict` |
| `lr_dialog_snapshot` | **獨立 top-level 欄位**（非 nested 在 live_research_state） | 前端對話 snapshot，獨立以避免後端 `_save_state` 整欄覆蓋；存取時機 bug 曾踩坑（見 status.md 2026-06-19） |

**schema_version legacy gate**：`schema_version < 2` 的舊 session → 後端 409 `legacy_schema_session` 禁止 continue（live_research.py:~319），前端顯示唯讀 export modal。新 session default `schema_version=2`（DR-parity sprint 後）。

**lr_session_id**：server 生成 UUID（非前端 session_id），`_create_lr_session()`（live_research.py:87）產生，貫穿整個 6-stage session 跨多次 HTTP request。

### 7.2 ⚠️ 與 DR 的關係（澄清一個常見誤解）

spec 早期說「LR 共用 Composable Pipeline 4-phase」。**實際 code**：

- LR **不用** DR 的 `ResearchState`，用自己的 `LiveResearchStageState`。
- LR **不直接走** Composable Pipeline 的 4-phase 路由，而是**自建 `BABLoopEngine` + 6-stage 對話 loop**。
- **真正的共用點**：BAB Loop 內部用的 LLM agent（Associator / Analyst / Critic / Writer）和 DR 是同一批。

```
DR: ResearchState → 4 phase（filter / actor-critic / writer / format）自動串接
LR: LiveResearchStageState → 6 stage 對話 loop
      Stage 1 → BABLoopEngine.run_loop (B→A→B')
      Stage 2 → BABLoopEngine per-topic
      Stage 3-6 → 對話管理（非 pipeline）
```

### 7.3 外部依賴

| 依賴 | 用途 | 設定 |
|---|---|---|
| **OpenAI** | LLM | high=`gpt-5.1`（build/refine/compose），low=`gpt-4o-mini`（derive/style/intent）；`config_llm.yaml` |
| **OpenRouter** | embedding | `Qwen/Qwen3-Embedding-4B`；`config_embedding.yaml` |
| **PostgreSQL** | 檢索 + 狀態 | 混合檢索（向量 + pg_bigm BM25），`core/retriever.py` → `postgres_client.py:445 search()` |
| **Google Custom Search** | web search | gap routing WEB_SEARCH（free tier 100/day） |
| **Wikipedia API** | knowledge | gap routing WIKIPEDIA |

### 7.4 成本（為什麼測試要小心燒錢）

| 階段 | 成本估算 | 備註 |
|---|---|---|
| Stage 1 BAB | ~$0.30-0.50 | **最貴**：B→A→B' ×3 high-tier |
| Stage 2 per-section BAB | ~$0.30-0.50 | 每 core topic 一輪 |
| Stage 3/4 | ~$0.02 each | low-tier |
| Stage 5 Writer | ~$0.08-0.12 | 每章一次 high-tier |
| Publish gate | ~$0.05 | grounding + CoV |
| **整輪（無 mock）** | **~$0.67-1.20** | — |
| mock_bab 模式 | ~$0.20-0.30 | 跳 Stage 1+2 |

> **省錢測試紀律**：開發期用 `mock_bab=true`（Stage 1+2 用 fixture）跑 Stage 3-6，省一半錢。但 mock_bab 對「Stage 2 web search 是否生效」**零判別力**（mock 直接用 Stage 1 ContextMap），那種驗收需要真 BAB E2E（~$5）或 prod manual gate。詳見 `docs/specs/mock-bab-playbook.md`。

---

## 8. 上手指南（怎麼開始改 LR）

### 8.1 開工前必讀（按順序）

1. 本文件（地圖）
2. `docs/specs/live-research-spec.md`（規格，尤其你要動的 §）
3. `memory/lessons-live-research.md`（LR 專屬踩坑教訓 —— **debug 前一定先讀**）
4. 對應子系統：動 grounding 讀 §5.1、動 prompt 讀 §4、動前端讀 §6

### 8.2 搜尋 code（強制用 indexer，禁用 Grep）

```bash
# 從 repo 根執行；indexer.py 在 repo 根 tools/，不在 code/python/
python tools/indexer.py --search "_run_stage_5"
python tools/indexer.py --index      # 大量修改後重建
```

### 8.3 改完必跑的 gate

```bash
# Smoke（改 Python 必跑；只改 docs/config/static 可免）
cd code/python && python tools/smoke_test.py      # 用 venv myenv311

# E2E pipeline：Unit → Smoke → Agent E2E (DevTools) → CEO 人工 E2E
```

> Unit + smoke 過 ≠ 完成。LR 很多東西要真機 E2E（登入 `admin@twdubao.com`/`Test1234!`）才算驗到。燒真實 LLM 錢 / push prod 前一定先給 plan 等 CEO 點頭。

### 8.4 各層「我想改 X，去哪」速查

| 想改 | 去 |
|---|---|
| 某個 Stage 的流程 | `orchestrator.py` 的 `_run_stage_N` / `_handle_stage_N_response`（§3.2） |
| BAB 迴圈 / 補搜 / revise | `loop_engine.py`（§3.3, §5.5） |
| LLM 怎麼被問（prompt） | `reasoning/prompts/{角色}.py`（§4.1） |
| 給使用者看的文案 | `reasoning/live_research/lr_copy.py`（§4.5） |
| 幻覺 / grounding 守門 | `hallucination_guard.py`（§5.1） |
| 發布前審查 | `orchestrator.py:_run_publish_gate`（§5.2） |
| 狀態欄位 / 序列化 / 退回清狀態 | `stage_state.py`（§3.6, §7.1） |
| 前端 UI / SSE / 報告渲染 | `static/js/features/live-research.js`（§6） |
| 開關 / 成本 / model / timeout | `config_reasoning.yaml` / `config_llm.yaml`（§5.6, §7.3） |

### 8.5 三個最容易踩的坑（再強調）

1. **改 `live-research.js` → 三處 cache-bust 一起 bump**（§6.4），否則 state 分裂。
2. **守門相關改動先讀 lessons** —— over-block / fail-open 邊界有血淚史（§5.1）。
3. **不要 silent fail** —— 每個降級都要有對應旁白（§4.5）；CLAUDE.md 紅線。

### 8.6 已知缺口 / 後續優化方向（未動手，待拍板）

這些是已盤點出、但需要進一步驗證或設計決策才動手的方向，列在這裡讓你接手時知道「哪些是已知的、不是你漏看的」：

**B1 — 補資料機制：瓶頸大概率在「消費端」而非「查詢量」（假說，待 prod 驗證）**
直覺上會擔心「3 query × 8 = 24 筆太少」，但那 24 只是**單輪 web search**——全流程（多輪 BAB + Stage 2 per-topic + 站內檢索）理論可達數百筆 evidence。所以「報告資料不足」更可能的真因**不是查得不夠，而是查到了 writer 用不到**：evidence_pool 蒐了幾百筆，但 writer 只看得到被 Analyst cite + Critic 標記成 grounded claim 的那些（`render_grounded_narrative` 用 `evidence_usage` 過濾 + 12000 字 char budget）。**`evidence_pool 大 ≠ evidence_usage 充分`**。
- **驗證方式**（動手前先做）：prod 跑難題，量 `evidence_pool.size vs evidence_usage.size` 比值；<20% 就證實瓶頸在消費端（Analyst cite 率 / grounding 轉換率），而非搜尋量。
- ⚠️ 這決定優化方向**完全相反**：瓶頸在消費端 → 改 evidence 利用率；在查詢量 → 加 query 數 / 放寬 cap。**別在沒驗證前瞎加查詢量。**
- 次要候選瓶頸：gap routing cap 6、SEARCH_REQUIRED 只 1 輪 cap 3、writer char budget 12000 截斷、Associator query 數不隨難度自適應（簡單題難題都 ~3 query）。

**B2 — recollect 缺專屬按鈕（UX 缺口）**
「資料不足 → 補搜（recollect）」目前**只能 user 打字觸發**，沒有像 backward-nav 那樣的按鈕（§3.7）。已在後端文案補了精準引導（叫 user 回覆「再去找更多資料」），但**前端加一個「補搜」按鈕**會更直覺。動前端 + cache-bust + E2E，列後續票。

---

## 附錄：關鍵檔案速查表

```
code/python/
├── webserver/
│   ├── middleware/auth.py            ← JWT 驗證
│   └── routes/api.py                 ← /api/live_research(/continue) 路由 + handler
├── methods/
│   └── live_research.py              ← LiveResearchHandler：session / state / runQuery
├── reasoning/
│   ├── live_research/
│   │   ├── orchestrator.py           ← 【核心】6-stage loop 主控 + 三層守門呼叫
│   │   ├── loop_engine.py            ← BABLoopEngine（B→A→B'、mini-reasoning、gap routing）
│   │   ├── stage_state.py            ← LiveResearchStageState（狀態 + 序列化 + reset）
│   │   ├── lr_copy.py                ← user-facing 旁白文案常數
│   │   ├── hallucination_guard.py    ← L1 citation / L2 grounding / specificity
│   │   └── sse_emit.py               ← emit_sse 統一出口
│   ├── agents/{associator,analyst,critic,writer}.py  ← LLM 角色 wrapper
│   ├── prompts/*.py                  ← 各角色 prompt builder
│   └── schemas.py / schemas_live.py  ← structured output schema
└── core/retriever.py + postgres_client.py  ← 混合檢索

static/js/
├── features/live-research.js         ← 【前端核心】發起 / SSE / accordion / 報告
├── features/text-fragment.js         ← citation highlight 雙錨點
├── features/lr-snapshot.js / lr-resume-classify.js  ← 回顧 / resume
├── main.js · news-search.js          ← import live-research.js（cache-bust）
config/
├── config_reasoning.yaml             ← LR flags / cap / timeout / max_results_lr
├── config_llm.yaml                   ← high/low model tier
└── config_embedding.yaml             ← Qwen3-4B
```

> **行號免責**：本文 file:line 是 2026-06-19 `main` snapshot，orchestrator 等熱檔案行號會漂。一律 `python tools/indexer.py --search "函式名"` 重新定位，別硬背。
