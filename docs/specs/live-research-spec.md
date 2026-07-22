# Live 研究（Beta）技術規格

> **狀態**：Beta — 6-Stage Dialog Loop + 前端 UI + DR-parity 三層防禦 land
> **最後 code re-sync**：2026-07-10（`main`，Batch 2 docs review B4a 校正；先前 2026-06-23 §7.3.1）
> **權威性**：本文定義「系統必須怎麼行為」的契約（state machine / schema / failure 紀律 / 測試 gate）。行號類事實交給 indexer，本文不存行號。新人導覽地圖見 `docs/reference/lr-onboarding.md`。
> **關聯文件**：
> - `docs/reference/lr-onboarding.md`（新人導覽 + 心智模型 + file:line 指路）
> - `docs/specs/reasoning-spec.md`（既有 M4 Reasoning 規格）
> - `docs/specs/login-spec.md`（Auth 系統規格）
> - `docs/specs/mock-bab-playbook.md`（mock_bab 自驗 playbook）
> - `memory/lessons-live-research.md`（LR 踩坑教訓 — debug 前必讀）

---

## 1. 概述

### 1.1 功能定義

Live 研究是 NLWeb 的即時研究追蹤模式。使用者提交研究問題後，系統在 chat 中以「讀豹」的語氣即時敘述研究進度（narration），同時在 Live 研究 tab 的 stage accordion 中顯示四個研究階段的狀態變化，最終在同一 tab 中渲染完整研究報告。

與既有 Deep Research 的差異：

| 面向 | Deep Research | Live 研究（Beta） |
|------|--------------|------------------|
| 進度呈現 | `#reasoning-progress` log 容器 | Chat narration + Stage accordion |
| 結果位置 | `#researchView` tab | `#liveResearchView` tab |
| Phase SSE | 無（只有 intermediate_result stage events） | `research_phase` event（8 個 phase boundary events） + `live_research_narration` / `live_research_checkpoint` / `live_research_writer_status` |
| 入口 / 路由 | `generate_mode=deep_research`（共用 DR 入口） | **獨立路由 `POST /api/live_research`**；handler 內部自設 `generate_mode="live_research"`，前端不帶 `enable_live_research` flag |
| Clarification | 有（gate-style，彈出選項對話） | 取代為 Stage 1 checkpoint（dialogue-style） |
| Stage 模型 | 4 phase 自動串接（`ResearchState`） | 6-stage 對話 loop（`LiveResearchStageState`，per-stage checkpoint + user reply） |

**關鍵設計立場**：LR **不走** DR 的 Composable Pipeline 4-phase，也**不用** `ResearchState`。它自建 `BABLoopEngine` + 6-Stage 對話 loop（`LiveResearchStageState`）。與 DR 真正的共用點**只在 BAB 內部複用同一批 LLM agent**（Associator / Analyst / Critic / Writer），不在 pipeline 路由。

> **Clarification 責任歸屬**：LR 不複製 DR 的 gate-style clarification。LR 是 dialogue-style：Associator 從任何查詢建出 ContextMap → **Stage 1 checkpoint 取代 clarification**。`runQuery` 預設設 `skip_clarification="true"` 後呼叫繼承自 DR 的 `prepare()`（跑完整套 decontextualize / query understanding / retrieval），只跳過 DR 的同步多選單 clarification。

### 1.2 與既有系統的關係

```
Live 研究 = 獨立路由 /api/live_research
         + LiveResearchHandler（繼承 DeepResearchHandler.prepare() 做 retrieval）
         + LiveResearchOrchestrator（6-Stage Dialog Loop + BABLoopEngine）
         + LR SSE Events（narration / checkpoint / stage_change / section / export）
         + Auth（真實 JWT path，非 public endpoint）
         + 前端 Live Research UI
```

> 共用 DR 的：retrieval 前處理（`prepare()`）、BAB 內部的 LLM agent。**不共用** DR 的：4-phase 路由、`ResearchState`、gate-style clarification。

---

## 2. 設計原則

原則 1-10 來自 `docs/archive/plans/major-upgrade-plan.md`（已歸檔）§4；#11 為 2026-07-08 後補（ARC-AGI-3 harness 啟發）：

| # | 原則 | 一句話 | LR Beta 體現 |
|---|------|-------|--------------|
| 1 | **北極星** | 一切技術決定服從「能不能 convince 客戶」 | Stage accordion 透明化研究過程 |
| 2 | **Narrow first** | 先在一個領域做到極致 | Beta 先做最小可行 narration + stage tracking |
| 3 | **系統是放大器** | 人類專家做最終價值判斷 | 報告呈現方式不變，研究員仍做判斷 |
| 4 | **不知道就問 user** | Dialogue-Driven Research Loop | Stage 1 checkpoint 取代 gate-style clarification |
| 5 | **高良率要求** | 品質門檻高於一般搜尋 | 共用 Actor-Critic + CoV + Hallucination Guard |
| 6 | **Living document** | 報告能隨新 info 延伸 | ⚠️ LR 側未實現（KG editing + selective re-run 已在 DR 側 land：`/api/research/rerun`；LR 報告無此路徑） |
| 7 | **Minimize disruption** | 設計不打擾既有工作流 | Narration 在 chat 自然出現，不彈 popup |
| 8 | **Transparent reasoning** | 邊做邊揭露 reasoning chain | Phase SSE + chat narration 即時告知 |
| 9 | **Propose-Verify** | LLM knowledge 是 falsifiable hypothesis | ⚠️ Beta 未實現（仍 reuse CoV backward-looking，§9）|
| 10 | **Dialogue-First UI** | 所有能力走 chat agent 對話 | Narration 透過 chat message，不加新 widget |
| 11 | **客製進 context 層，不進結構層** | 為特定產業/客戶調整餵入的 context（產業背景、關注清單、來源權重），**禁止**為單一客戶 fork pipeline 結構——環境特定 harness 過擬合，在其他場景翻車 | 客製 = 替換 prompt/context 注入；pipeline 路由/stage 機器不分叉 |

---

## 3. 架構總覽

### 3.1 系統架構

```
┌─────────────────────────────────────────────────────────────────┐
│  前端 (news-search.js)                                          │
│  Mode Toggle → performLiveResearch(query) → authenticatedFetch  │
│      ↓                                                          │
│  SSE Event Handler:                                             │
│   research_phase   → updateLiveResearchStage()                  │
│   live_research_*  → showLRCheckpoint / addChatMessage          │
│   final_result     → displayLiveResearchFinalReport()           │
└──────────────────────────────────────────────────────────────────┘
                            │ HTTP POST + SSE (JWT in cookie/header)
┌──────────────────────────────────────────────────────────────────┐
│  後端                                                             │
│  auth_middleware (JWT validate) → routes/api.py                  │
│      ↓                                                            │
│  LiveResearchHandler (methods/live_research.py)                  │
│      ↓                                                            │
│  LiveResearchOrchestrator (6-Stage dialog loop)                  │
│      ↓                                                            │
│  Stage 0: Retrieval                                              │
│  Stage 1: BAB Loop (資料蒐集面聚焦) → checkpoint                  │
│  Stage 2: per-section BAB → checkpoint                           │
│  Stage 3: Style Analysis → checkpoint                            │
│  Stage 4: Format Spec → checkpoint                               │
│  Stage 5: Writer per-section → checkpoint × N                    │
│  Stage 6: Export                                                 │
│      ↓                                                            │
│  PG: live_research_state JSONB (per lr_session_id UUID)          │
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 Data Flow

1. 使用者選 Live 研究 mode + 真實登入（JWT in cookie） → 點搜尋
2. 前端 `performLiveResearch(query)` → `authenticatedFetch` POST `/api/live_research`
3. 後端 `auth_middleware` 驗 JWT → `request['user']` 含 user_id (UUID)
4. `LiveResearchHandler.runQuery()` → `LiveResearchOrchestrator`
5. Orchestrator 跑 Stage 0-6，每 stage 邊界 emit SSE event + persist `live_research_state` JSONB
6. User reply at checkpoint → POST `/api/live_research/continue` → `_load_state` → resume
7. Stage 6 完成 → emit `final_result` → 前端切 tab 渲染報告

### 3.3 Feature Flags

**檔案**：`config/config_reasoning.yaml`。下表為 LR 相關 flag 現況（非完整 features 清單），預設值以 config 為準。

> **位置註記**：多數在 `reasoning.features.*`；但 `lr_recollect_cap` 是 **code default 不在 config**（可被 `features` override）；`offline_max_checkpoint_advances` / `offline_max_wall_seconds` 在 `reasoning:` **頂層扁平 key**（不在 `features:` 段）。

| Flag | 預設值 | 說明 |
|------|------|------|
| `live_research` | `true` | LR master switch — 開啟所有 LR 行為 |
| `live_research_mock_bab` | `false` | 測試模式：Stage 1+2 用 fixture ContextMap（省 BAB LLM 成本），Stage 3-6 跑真實 LLM。**commit 前必須 false**。見 §8.1 + `mock-bab-playbook.md` |
| `live_research_dry_run` | `false` | 用 mock agents 跑 pipeline，完全不呼叫 LLM |
| `live_research_critic_publish_gate` | `true` | Track F F1：per-section Critic publish gate（claim-level fabrication，見 §6.8）|
| `cov_lite_enabled` | `true` | F3 Chain-of-Verification（DR/LR 共用；LR 可用 `live_research_cov_lite_enabled` 子 flag 覆寫，見 §6.8）|
| `gap_knowledge_enrichment` | `true` | process-wide Analyst prompt builder flag（與 per-request `enable_gap_enrichment` 兩層 toggle，見 §6.7）|
| `live_research_consistency_monitor` | `true` | Critic Consistency Monitor（§4.3.2）|
| `live_research_style_analysis` | `true` | Stage 3 Style Analysis |
| `live_research_max_bab_iterations` | `3` | BAB loop 最大迭代次數 |
| `max_results_lr`（`tier_6.web_search`）| `8` | 每個 web search query 撈幾筆（LR 專用；DR 維持 `max_results: 5`，split-key 互不 fallback）|
| `gap_routing.max_external_calls_per_run` | `6` | gap routing 單輪外部呼叫上限 |
| `lr_recollect_cap`（code default）| `2` | Stage 5 退回補搜上限（§4.7.8）|
| `offline_max_checkpoint_advances`（`reasoning` 頂層）| `1` | 離線後最多再跨幾個 checkpoint（防斷線重連循環燒錢）|
| `offline_max_wall_seconds`（`reasoning` 頂層）| `900` | 離線 wall-clock 硬上限（秒）|

> **`composable_pipeline` / `nonblocking_research`**：是 DR Composable Pipeline 的 flag，LR **不走**該路徑（§1），故不列入 LR flag 表。

**Per-request toggle（非 config flag，由前端 request body 帶）**：

| Param | 預設 | 說明 |
|-------|------|------|
| `enable_web_search` | `false` | LR Stage 2 per-topic BAB + gap routing 的 web search 開關。見 §6.6 |
| `enable_gap_enrichment` | `false` | gap routing 四類路由總開關（LR `runQuery` override；前端寫死送 `true`）。見 §6.7 |
| `enable_kg` | `false` | Knowledge Graph 生成（LR Track D）|

### 3.4 兩層聚焦模型

LR 整個流程包含**兩個層級的聚焦**，分別由不同 stage 負責：

| 層級 | 完成標誌 | 對應 Stage | 性質 |
|------|----------|-----------|------|
| **資料蒐集面聚焦** | User 看到「重組後結構（N 章）」並同意 | Stage 1 BAB Loop | 從 abundant evidence 收斂出研究結構（cm.topics / chapters） |
| **文章面聚焦** | 每一章寫好、字數/引用/格式對齊 user_voice | Stage 5 Writer | 每章 prose composition + citation render + format compliance |

**研究本質**（CEO framing）：「先蒐集 abundant 資料 → 捨棄一部分 → 聚焦 → 補充新資料 → 再捨棄 → 再聚焦」迴圈。

- BAB Loop 內部 `B → A → B' → re-retrieve → B''` 就是這個迴圈的 in-stage 體現
- Stage 5 revise / Stage 4 reframe 觸發回到 Stage 1/2 重新聚焦時，可能再次補充資料（production）或從同一 pool 重新撈（testing，見 §8.1）

**為什麼分兩層**：
- 資料面聚焦定下「研究 cover 哪些 topics / chapters」；錯了後面全錯
- 文章面聚焦才寫實際 prose；資料面定案後此層才有意義
- v15 P0-3「writer 不吃 reframe」就是這兩層之間接線斷掉的具象化 — Stage 1 reframe 改了 cm.topics，但 Stage 5 writer 沒讀新值

---

## 4. UX State Machine Contract

本章是 LR 對話流程的 single source — 所有 stage 邊界、SSE event、user reply 處理、reframe / revise 接線、failure 紀律集中於此。

### 4.1 共用 contract

#### 4.1.1 SSE Event Types

| event | 觸發 | 必含欄位 | 用途 |
|-------|------|---------|------|
| `live_research_stage_change` | Stage 推進 | `stage_id`, `stage_name` | 前端切 stage accordion |
| `live_research_narration` | 系統說話 | `text` | 插入 chat 訊息 |
| `research_phase` | DR phase 邊界 (8 個事件，見 §6.3) | `phase`, `status` | 前端 stage progress |
| `live_research_checkpoint` | 系統需要 user input | `checkpoint_type`, `payload`, `reply_ui_spec` | 顯示 reply UI |
| `live_research_writer_status` | Stage 5 writer per-section 狀態 | `status` (started / section_done / stopped / all_done), `total_sections`, `completed`, `section_title?` | typing indicator + 隱藏/顯示 stop button |

Frontend SSE handler 對 unknown `message_type` 預設 merge（避免 SSE 紀律踩坑），新增 case 時要明確 `break`。

#### 4.1.2 Stage 邊界規約

每個 stage 進場 / 退場必含：

| 階段 | 動作 |
|------|------|
| Entry | `await self._emit_stage_change(stage_id)` |
| 進行中 | per-phase `_emit_phase` / per-event `_emit_narration` |
| Wait user | `_emit_checkpoint(checkpoint_type, payload)` + `await _save_state(state)` |
| Exit | `complete_stage()` → `_save_state` → next stage entry |

**Persistence rule**：每個 stage 邊界 + 每次 user reply 處理後**必須** `_save_state`。中途崩潰可由 `_load_state` 恢復。

#### 4.1.3 User Reply Contract

User reply 訊息（POST `/api/live_research/continue` body `user_message`）的處理分支：

| 訊息類型 | 對應 action | 處理 path |
|---------|-----------|----------|
| Auto-continue（empty msg, `auto_continue=true`） | merge default → complete_stage | 進下一 stage |
| Keyword shortcut（≤15 字含「OK/繼續/匯出」等） | 跳過 LLM intent parse | 直接路由 |
| 一般 reply | LLM typed-action parse (TypeAgent) | 嚴格路由（4.6.2 / Stage4Response 範本） |
| Vague / unparseable | `clarifying_question` 路徑 | re-emit checkpoint + narration（見 §4.3.5）|

**禁止 silent advance**：任何 user reply 解析後若不確定該前進，必須 re-emit checkpoint 等 user 再確認，不可 silent 推進下一 stage（v15 P0-2 lesson，見 §4.3.6）。

### 4.2 Stage 0 — Retrieval (資料蒐集)

**位置**：`LiveResearchOrchestrator.execute()` 進入 Stage 1 前。Stage 0 不是獨立 stage 編號，是 BAB Loop 的 Phase 0 input。

**Production**：呼叫 `core.retriever.search()` (pg_bigm + vector) 拿 raw documents → 餵給 BAB Loop Phase 0 build。

**Testing (`mock_bab=true`)**：Stage 1+2 整段 BAB 跳過，直接載入 fixture ContextMap + evidence_pool（見 §8.1 + `mock-bab-playbook.md`）。Stage 3-6 跑真實 LLM。

### 4.3 Stage 1 — BAB 資料蒐集面聚焦

#### 4.3.1 BAB Loop B→A→B' 結構

**檔案**：`code/python/reasoning/live_research/loop_engine.py` — `BABLoopEngine.run_loop()`

```
Phase 0: build initial B (ContextMap)              ← LLM: associator.build_context_map（emit bab_phase0 completed）
Loop ×N (max_iterations=3):
  Phase 1: derive A (search plan) from B           ← LLM: associator.derive_search_plan (low)（emit bab_phase1 started/completed）
  Phase 2: execute A (retrieval / PG / Google)     ← Retrieval call（testing: hit fixture pool）（emit bab_phase2 started/completed）
  Phase 3: mini-reasoning (Analyst + Critic)       ← LLM（non-fatal）（emit bab_phase3 started/completed — 見下方）
           ├ Task 2 SEARCH_REQUIRED 二次補搜（§4.3.2）
           ├ Task 1 Critic REJECT → analyst.revise re-review（§4.3.2 / §6A）
           └ Track D: KG merge（enable_kg=true 時）  ← inner non-fatal；失敗補 per-run 一次降級旁白
  Phase 4: refine B → B' (聚焦)                     ← LLM: associator.refine_context_map (high)（emit bab_phase4 started/completed）
  Consistency check                                ← LLM (low)
  is_stable? / paused_by_consistency? → break
```

返回 final ContextMap → orchestrator emit「研究結構提案」checkpoint。

**Phase 3 mini-reasoning 進度事件（`bab_phase3`）**：mini-reasoning
是 BAB loop 最耗時的 LLM 段（Analyst high model + Critic + 可能 revise / 二次補搜 / gap routing），
為避免前端在最長窗口看到零進度，`run_loop` 在 `_run_mini_reasoning` 前後**對稱 emit** phase event +
narration：
- 有 mini-reasoning input（`_has_mini_reasoning_input`）→ `_emit_phase("bab_phase3", "started")` +
  narration「正在深入分析這批資料、交叉檢驗論點...」；mini 成功（回 True）→ `_emit_phase("bab_phase3", "completed")`。
- early-skip 輪（檢索空手、gate False）**完全不 emit** phase3（不對 user 謊稱正在分析）；mini 失敗輪
  （降級回 False）**不 emit completed**（降級旁白即收尾，緊接 `bab_phase4 started` 標邊界）。
- 前端 `static/js/features/live-research.js` `phaseLabels` 收 `'bab_phase3': '深入分析與交叉檢驗'`。
- SSE event 表見 §6.3。

#### 4.3.2 收斂條件 + Consistency Monitor

- **is_stable**：`refine_context_map` output 含 `is_stable=true` → break
- **Consistency Monitor**：`recommended_action="pause_confirm"` → set `paused_by_consistency=True` + break
- **Max iterations**：跑滿 `max_iterations=3` → 自然 exit

每輪結束 emit `bab_phase4 completed` 給前端 progress。

##### SEARCH_REQUIRED 二次補搜（DR-parity Task 2）

**檔案**：`loop_engine.py`（Phase 3 mini-reasoning 內）。Analyst 回 `status=SEARCH_REQUIRED`
+ 非空 `new_queries` 時 → 即時補搜站內 evidence → 重跑 Analyst 一次。

- **上限 1 輪**（與外層 BAB iteration 隔離，避免疊乘無上限補搜）。
- queries consumer 層硬限：strip → dedup → cap **3 條**（即使 Analyst prompt 要求 1-3，runtime 仍兜底防 LLM 吐超量 / 空 query）。
- 補搜走 BAB 既有 `_execute_search` path（不新建檢索器），side-effect 寫 `self.evidence_pool` → BAB 結束 serialize 進 `state.evidence_pool_json` → outline planner / writer 可引用（CEO 2026-06-12 拍板）。
- **邊界**：補的是 Analyst 頂層 `status=SEARCH_REQUIRED`（即時補救），與 gap_resolutions INTERNAL_SEARCH no-op（交給下一輪 Associator，§6.7）不同層、不重複。
- **失敗降級（不可 silent fail）**：補搜無結果 / re-run 後仍非 `DRAFT_READY` / draft 空 → forensic log + per-run 一次 user-facing 降級旁白 `lr_copy.SEARCH_REQUIRED_DEGRADED_NARRATION`，用原 analyst_output 續跑。
- 測試 `tests/unit/reasoning/test_loop_engine_search_required.py`。

##### mini-reasoning REJECT → revise 迴圈（DR-parity Task 1）

**檔案**：`loop_engine.py`（Phase 3 mini-reasoning 內，Critic pass 之後）。Critic 回
`status=REJECT` → `analyst.revise()` 重寫該批推論 → critic re-review。

- **上限 1 輪**（`MAX_REVISE=1`；非 DR 的 3 輪 —— LR mini-reasoning per-topic 內嵌，外層 BAB `max_iterations` 已疊乘）。
- 退出：revise 後 PASS/WARN → 用 revised output 走正常索引；仍 REJECT / revise 失敗 / re-review 失敗 / revised draft 空 → 維持既有 REJECT **入庫 forensic**（`state.evidence_usage` 標 `critic_status="REJECT"`，render 層過濾不入 writer prompt）+ break。
- **不可 silent fail**：revise / re-review 任一步拋例外 → per-run 一次降級旁白 `lr_copy.MINI_REASONING_REVISE_DEGRADED_NARRATION` + break，讓迴圈外的索引 / KG merge 用原 output 正常跑完（re-review 的 critic.review 也納入內層 try/except，避免冒泡成通用「Mini-reasoning failed」旁白）。
- 測試 `tests/unit/reasoning/test_loop_engine_revise_loop.py`。

對應 failure 降級項見 §4.10。

#### 4.3.3 「研究結構提案」Checkpoint

退出 BAB Loop 後 orchestrator emit `live_research_checkpoint`：

```
checkpoint_type: "stage1_proposal"
payload: {
  context_map_summary: <topics + relations 摘要>,
  proposal_markdown: <D-6 detail-rich format>,
  reply_ui_spec: { type: "free_text", placeholder: "確認結構或提出調整..." }
}
```

User reply 三選一：
- agree（confirm 短訊息 OK/好/確認）→ advance Stage 2
- adjust（structure 訴求）→ typed-action parse → reframe op（§4.3.4）/ incremental op
- reject / clarifying（vague reply）→ §4.3.5 clarification dialog

**`evidence_list` vs `evidence_total`（顯示子集 vs 總量，2026-06-20）**：`_emit_checkpoint` 額外帶 `evidence_list`（per-topic 顯示子集）+ `evidence_total`（evidence_pool 完整筆數）。前端「N 筆資料」標題若只拿 `evidence_list` 長度會讓 user 誤以為只蒐到那幾筆 → 帶 `evidence_total` 讓前端標「節選 vs 總量」。`evidence_total` 與下游 consolidation narration「共蒐集到 N 筆」**同源同一個 `len(pool)`**（Stage 1=`len(final_pool)`、Stage 2 真實=`len(existing_pool)`、Stage 2 mock=`mock_total_evidence`）；不傳時 fallback 為 `len(evidence_list)`（向後兼容 Stage 3 等無 pool 的 checkpoint）。測試：`tests/unit/reasoning/test_emit_checkpoint_evidence.py`。

#### 4.3.4 Reframe op：cm.topics Mutation（原 §4.8）

**Mutation Action 表**（8 個 op_type）：

| op_type | 觸發條件 | 行為 |
|---------|---------|------|
| `merge_topics` | 合併 N 個 topic | N → 1，evidence_ids union |
| `split_topic` | 拆 1 topic | 1 → N，src relations 砍 |
| `add_topic` | 新增 | append cm.topics |
| `remove_topic` | 刪除 | 移除 + relations 涉及移除 |
| `rename_topic` | 改名 | update topic.name |
| `change_relevance` | 改核心程度 | update topic.relevance |
| `change_description` | 改描述 | update topic.description |
| **`reframe_structure`** | **整體重組** | **Replace All — cm.topics + cm.relations 全清，依 op.new_chapters 重建** |

**D-5 Reframe vs Incremental Heuristic**：LLM intent parser 在以下訊號**任一**命中 → `reframe_structure`：

1. user 列出 ≥ 3 個明確 chapter 名稱，且 ≥ 50% 不在現有 topic 清單中
2. user 用整體語氣：「整個 / 整體 / 大方向 / 最後架構 / 重新規劃 / 改成 N 章」
3. user 同時提到 research_question shift + 章節名稱
4. **outline 列舉句型**（任一 sub-pattern 命中即可）：
   - 4a 連接詞列舉：「前面 X，然後 Y，結尾 Z」（Cayenne R1）
   - 4b 頓號列舉 ≥ 3 章節名 + 收斂語：「A、B、C 這 N 章 / 共 N 章」（Cayenne R3）
   - 4c 文體宣告 + 章節列舉：「想寫成 X 類型的，A、B、C」

否則 → incremental ops。

**D-2 Evidence Preservation**：`reframe_structure` 全砍 cm.topics 後：
- evidence_pool 完整保留（在 state level，不在 ContextMap 內）
- 所有舊 topic.evidence_ids union 塞給**第一個新 chapter**（前言）
- Writer 透過 evidence_lookup 看到的 [N] 對應不變，無 phantom citation

**D-3 Relevance Default**：
- 背景 / 文獻 / 延伸 / 附錄 / 回顧 / 歷史 → `supporting`
- 其他（前言 / 結論 / 比較 / 案例…）→ `core`（Stage 2 BAB 只跑 core）
- LLM / user 明確指定 → 採用

**D-1 Confirm Round (Defensive UX)**：CEO 拍板 reframe 採 **confirm round** 而非 immediate apply：

```
Round 1: user 給結構訴求 → LLM parse → reframe_structure op
  → 不立即 apply，存 state.pending_reframe_json
  → emit detail-rich confirm checkpoint (D-6 markdown)

Round 2: user 回覆
  ├─ confirm (OK / 好 / 確認) → apply reframe + clear pending + advance
  ├─ cancel (取消 / 算了) → clear pending + re-emit 原 checkpoint
  └─ 新訴求 → clear pending + recursive call（可能解出新 reframe）
```

**D-6 Detail-Rich Proposal Markdown**（LLM 必填 `proposal_markdown`）：

```markdown
## 我準備重組為 N 章：

### 第 1 章：[chapter_name]
- **預期內容**：[1-2 句]
- **包含資料**：
  - [既有 topic A 的相關面向]
  - [可能補充的新角度]

...

**整體研究問題**：[new_research_question 或 既有]

確認這個結構嗎？或者哪一段要調整？
```

#### 4.3.5 Empty-ops Clarification Dialog（原 §4.9）

當 user reply vague / unparseable 時：

1. `Stage1ParsedIntent` 含 `clarifying_question: str` 欄位
2. `stage1_revision` prompt 三分支：
   - 路徑 A（明確訴求）→ 具體 ops + clarifying_question=""
   - 路徑 B（純 confirm）→ empty ops + clarifying_question=""
   - 路徑 C（無法 mapping）→ empty ops + 繁中問句（含 3 正面例 + 1 反面例）
3. Orchestrator dispatch：
   - `intent is None` → 「LLM 死掉」fallback narration + retry checkpoint
   - `empty ops + clarifying_question 非空` → emit narration = clarifying_question + re-emit checkpoint
   - `empty ops + clarifying_question 空` → 「沒問題，目前結構直接用」+ advance

#### 4.3.6 Adjust Path 不可 Silent Advance（v15 P0-2 lesson）

**紀律**：當 user reply 解出 adjust / reframe op 後，**絕對不可** silent advance Stage 2，必須 re-emit checkpoint 讓 user 確認新版。

**v15 P0-2 觀察**（real persona E2E 揭露）：
- R1 → user 提 5 章 reframe → pending checkpoint
- R2「德日韓」→ classifier 判 adjust → narration「先放下你剛才的 5 章重組訴求」+ clear pending + recursive call → **silent advance Stage 2**
- 兩個 sub-bug:
  - (a) narration 引用「5 章」當 user 訴求，但 user 從沒講「5 章」— LLM 自己推測的數字反過來引用 → **narration 絕對不可引用 LLM-generated 數字當 user 訴求**
  - (b) adjust path silent advance → 不重 emit reframe checkpoint 等 user 看 + confirm

**修法（已 land，commit `8025a3f47`）**：adjust path re-emit reframe checkpoint + 補充 narration「以下是新版結構，是否確認？」— 不 silent advance（`orchestrator.py:2092` 契約註解引用本節）。

### 4.4 Stage 2 — Per-section BAB（章節 detail）

Stage 1 ContextMap 定案後，Stage 2 對每個 `relevance == "core"` topic 跑 per-section BAB Loop（focus_topic_ids 注入）。Engine `seed_evidence_pool` + `seed_counter` 從 Stage 1 累積過繼（跨 engine 共用 evidence_id space）。

完成後 emit checkpoint「章節 detail，需要調整嗎？」。

**Stage 2 誠實 Narration**（OQ 1 拍板，原 §4.12.4）：
- 繁中 user-friendly
- 不撒謊（「記下來」≠「已記錄」這種 unverified claim）
- 禁用字詞：「retrieval」「session」「state」「已記錄」
- 採用文案：「謝謝你的建議，我已經把它記下來，寫稿階段會盡量採用。」

**Stage 2 斷線收場契約**（plan: lr-disconnect-midstage-persist，`stopped_early` 語意見三輪 AR D-7）：
- Stage 2 per-topic BAB loop **每個 core topic 落盤**（`state.evidence_pool_json` + `_persist_progress`），不等整個 Stage 2 跑完，evidence 一律不浪費。
- **`completed_sections` 只在 topic 正常收斂完成時 append**（`engine.stopped_early == False`）。若 topic 執行到一半被 offline cooperative break 打斷（`engine.stopped_early == True`，可能發生在 `max_iterations` 內任一輪），該 topic **不**標記 `completed_sections`——半套研究不算完成，evidence 仍落盤但提早 `return`，不繼續跑後續 topic（同時記 `_mark_offline_since` 供 wall-clock 防呆計時）。
- 每個 topic **開始前** 另外檢查離線防呆上限（`_offline_cap_reached`，對齊 Stage 5 per-section 檢查）：達上限 → 標 `offline_capped` + persist + return（bounded burn，不無人看管燒錢）。這與上一點的 `stopped_early` 檢查點是互補、不重疊的兩層防線（前者防「topic 都還沒開始跑就已離線」，後者防「topic 執行中被打斷」）。
- BAB loop 內 `_check_connection()`：**client 純斷線 = cooperative stop（回傳 "offline"，不 raise，並設 `engine.stopped_early = True`）**，讓 loop break 後把已累積 evidence 帶回 caller 落盤；**soft-interrupt（使用者主動打斷）仍 raise**，不影響 `stopped_early`。
- resume：`_run_stage_2` 從第一個不在 `completed_sections` 的 topic 續跑（**不從頭跑 Stage 2**）——若該 topic 是被中途打斷、非全新 topic，`BABLoopEngine` 用上次已落盤的 evidence 當 `seed_evidence_pool` 續跑，不重蒐已收集的部分證據。

### 4.5 Stage 3 — Style Analysis

`_run_style_analysis` 從 user-provided 文本提取文筆特徵（level=low，extraction task）。完成後 emit checkpoint。Intent parsing 用 `_parse_style_confirmation_intent` (low)。

**Round-2 對話回饋只有兩種 action**（2026-06-17 收斂）：`confirm`（往下）/ `adjust`（reconcile 合併微調）。早期的 conversational `redo`（對話輸入當新範本整碗重抽）已**移除**——它把「覆蓋整份風格分析」這個不可逆動作綁在不可靠的 LLM intent 判斷上，易誤判。`_parse_style_confirmation_intent` 的 schema enum 與 guard tuple 都已收緊為 `["confirm", "adjust"]`。

**「重新提供範本」改綁明確按鈕**：覆蓋整份風格分析改由前端按鈕「🔄 重新提供範本」觸發，送 sentinel `STAGE3_NEW_SAMPLE_SENTINEL`（`"__LR_STAGE3_NEW_SAMPLE__"`）。後端 round-2 入口前的 sentinel handler 偵測到即清空 `style_features_json` + 重 emit「請貼新範本」checkpoint；使用者下一則訊息因 `style_features_json` 為空，自然落回第一輪入口走 full `_run_style_analysis` + o7 input-guard（`input_is_writing_sample` / `StyleInputNotASampleError`，貼非範本內容會降級不硬抽）。checkpoint 是否顯示按鈕由 `_emit_checkpoint(show_new_sample_button=...)` 控制（首輪成功與 adjust 兩處帶 `True`）。sentinel 字串在前端被過濾、不顯示於 chat。

### 4.6 Stage 4 — Format Spec Collection

#### 4.6.1 user_voice Container（原 §4.12）

**Schema**：`UserVoice` dataclass（`reasoning/live_research/stage_state.py`）

| Field | Type | Default | Writer | Reader |
|-------|------|---------|--------|--------|
| `citation_style` | `Optional[Literal["author_year","numeric","footnote","none"]]` | `None` | Stage 4 (`Stage4Intent.citation_style_extracted`) | `_write_section` → writer prompt citation_format |
| `stage2_feedback` | `List[Dict[str, str]]` (`{"round", "text"}`) | `[]` | Stage 2 | audit trail + 未來 BAB feedback hook |
| `revise_instructions` | `Dict[int, List[str]]` (key=section_idx, accumulate) | `{}` | Stage 5 revise path | `_write_section` → `writer.compose_section(revise_instruction=...)` |

**Fallback Chain** (`citation_format`)：
```
user_voice.citation_style → style_features.citation_format → "numeric"
```

**Forward-compat fields**（slot 預留，每加須在此 register + roundtrip test）：
`time_constraint` / `style_instruction` / `chapter_role_strategy` / `export_format`

**Backward Compat**：舊 session restore 時 `user_voice` missing / null → `from_dict` 預設空 `UserVoice`；`revise_instructions` value 是 str（舊 schema）→ from_dict 自動包 `[str]`。

#### 4.6.2 Multi-element Typed-action Parse（原 §4.13.2 / .3）

**Stage4Intent schema**（`schemas_live.py`）：

```python
class ChapterSpec(BaseModel):
    type: Literal["narrative_chapter"] = "narrative_chapter"  # 強制 channel
    name: str = Field(..., min_length=1)
    description: str = ""
    relevance: Literal["core", "supporting", "peripheral"] = "core"

class SpecialElementSpec(BaseModel):
    type: Literal["table", "list", "chart", "diagram", "code_block"]
    target_chapter: str = ""
    description: str = ""

class Stage4Intent(BaseModel):
    intent: Stage4Action
    special_elements: List[SpecialElementSpec]
    new_chapters: List[ChapterSpec]
    citation_style_extracted: Optional[Literal[...]]
    target_word_count: Optional[int]  # Blocker A — Phase 3
```

**CEO 紀律**（OQ 拍板）：
- **OQ-1**：完全刪除舊 `_parse_stage_4_intent` 自由 dispatch（+130 行），全 caller migrate 至 `_classify_stage_4_response`。沒並存。
- **OQ-2**：**不**加 keyword validator 兜底。LLM mis-classify chapter vs element → 加 typed few-shot，不靠 heuristic。

**Stage4Response action enum**（10 actions）：

```python
class Stage4ResponseAction(str, Enum):
    confirm_reframe / confirm_format / confirm_both
    cancel_reframe
    adjust_chapters / adjust_format
    add_special_element / new_structure_request
    auto_continue / unclear

class Stage4Response(BaseModel):
    action: Stage4ResponseAction
    confirm_target: Optional[Stage4ConfirmTarget]
    structural_content: Optional[Stage4StructuralPayload]
    format_content: Optional[Stage4FormatPayload]
    clarifying_question: str
    # @model_validator 強制互斥 payload contract (action ↔ payload)
```

Dispatcher (`_handle_stage_4_response`)：

```
auto_continue / 空訊息 → merge default + complete
pending_reframe_json 非空 → _handle_pending_reframe
其他 → _classify_stage_4_response → typed action 嚴格路由
  confirm_format / confirm_both → complete_stage
  adjust_format → 寫 format_specs + advance
  add_special_element → 寫 element + pending=True
  adjust_chapters / new_structure_request → _try_stage_4_reframe_entry_typed
  cancel_reframe / confirm_reframe sans-pending → fallback narration
  unclear → emit clarifying_question
```

**v15 P1-A lesson**：mixed payload「APA + 7000字 + 表格 + 章節字數」一次說 4 件，schema 必須**完整 cover multi-element**。`Stage4FormatPayload` 必含 `citation_style`、`target_word_count`、`section_word_balance`、`special_elements` 並存 — 不可只認 1 個。Few-shot 必含 multi-訴求 example。

#### 4.6.3 special_elements 強制紀律（原 §4.11）

`state.format_specs["special_elements"]: Optional[List[Dict[str, str]]]` 結構化欄位：
- `type`: `table` / `list` / `chart` / `diagram` / `code_block`
- `target_chapter`: 章節指涉（**章名原文**為主；prompt 抽取端章名優先、序數合法）。**空字串 = user 明說「全章/每章」→ 全章注入**（B2：非「未指定」；「未指定/找個地方」填非空原文 → 落澄清）。
- `description`: user 自然語言描述

**Hard channel vs Soft channel 區分**：

| Channel | block 名稱 | 內容 | 語氣 |
|---------|-----------|------|------|
| Soft | `## 格式要求` | 字數、語氣、引用樣式偏好（free text） | 「以下是用戶偏好」（參考） |
| Hard | `## 必須包含的特殊格式 element` | 表格 / 列表 / 圖 / 程式碼塊（結構化 + filter） | 「**必須**」「沒輸出視為不合格」 |

**Stage 4 special_element target 定位（R2 澄清機制，2026-07，雙層 + pending 狀態機）**：
- **第一層 code 短路**（`_resolve_target_chapter_layer1`，不另起語意 call）：target 與某章名 exact 相等 / 唯一
  substring 命中 / 空（明說全章）→ 直接定位（章名原文 or 全章注入）。
- **第二層 LLM 判語意**（復用 Stage 4 classifier call，不另起 call）：第一層非 exact/唯一（語意指涉「講政策
  的那章」/ 序數 / 「找個適合地方」/ 對不到）→ classifier 順帶判 `resolved_chapter_title` + `resolution_confidence`
  （transient，不持久化）。clear → 存完整 pending（`pending_special_element_json`）+ 發**確認式澄清**（合併：「你是指
  『X』『Y』這幾章嗎？」，接住 LLM 語意誤判 confident-wrong）；uncertain / 對不到 → 存 pending + 拋完整 clarification
  列章名問 user。user 下一輪回答走 `_handle_pending_special_element` 從 pending 恢復定位。
- **持久化（OQ-4 命脈）**：所有寫入 special_elements 走**集中 serializer `_serialize_special_element_for_state`**，
  強制排除 transient 欄位 → 持久化仍是 `{type,target_chapter(章名原文),description}`，shape 與今日一致、零 migration。
  `pending_special_element_json` 是新 top-level state 欄位（比照 pending_reframe，缺 key 默認空 = 零 migration）。
- **空 target 語意（B2）**：`target_chapter=""` **只**代表「user 明說全章/每章都要」；「找個適合地方/未指定」由
  classifier 填非空原文 + uncertain → 落第二層問 user（不 silent 塞全章）。
- **pending 狀態機邊界（R7）**：(a) `_handle_stage_4_response` 入口 special pending 短路在 auto/blank
  complete_stage 之前 → 澄清中途送空白/按繼續 → re-emit 同一問句、不 advance、不丟 pending（B-order）；
  (b) 章節尚未定案（無 outline）時 user 若給具體章名 → 直接寫入該 raw target，交 Stage 5 exact filter /
  unmatched 後衛兜底（不無限重問，B-loop）；(c) `pending_reframe` 與 `pending_special_element` 互斥，異常
  同時非空 → 保留 reframe、清 special + **user-facing 提示請結構確認後再說一次表格**（不 silent 丟需求）；
  (d) pending 澄清中收到 reframe 訴求 → reframe 優先（同 (c) 互斥策略），LLM 故障時 sentinel 分流保留 pending 等重試。
- **pending 恢復意圖判定**：user 對澄清問句的回答意圖**交 LLM 判**（`_classify_pending_special_element_reply`：
  confirm/change_chapter/cancel/reframe/unclear），**禁 substring/regex 解析 user 自然語言回答**（中文序數/複合否定規則式必漏）。
  reframe = user 在澄清 round 提出整體結構重組 → 逃生口：清 special pending + 誠實告知表格暫停 + 交 Stage 4 typed
  dispatcher 解結構 payload 走既有 reframe entry（2026-07-15 Cayenne B2 修法）。

`_write_section` per-chapter filter（R2 改 exact）：target_chapter 定位成章名原文後 → **exact 命中 section_title 即注入**；空 →
全章注入；對不到 → 不注入。**no silent fail 後衛**：outline 定案後 `_diagnose_unmatched_special_element_targets`
跑一次（per-run dedup），對不到任何章的 target → emit `lr_copy.special_element_target_unmatched_narration`
誠實告知 user（接「對不到任何章」；LLM 語意誤判「對到錯的存在章」由第二層確認式澄清接）。**禁 hardcode 序數↔章名
映射；語意對應交 LLM 判、非 code 硬猜**（CEO 拍板「llm 很聰明，不可能這種簡單的事都做不到」）。

#### 4.6.3.1 通用 clarification 機制（2026-07，R2 抽象）

LR 有三條底層同型的 clarification 管線（Stage 1 empty-ops / Stage 4 unclear / R2 表格章節指涉），共用同一 spine：
`emit 問句 as narration → re-emit checkpoint → return state（不 advance）`。R2 把此 spine 抽成通用抽象：

- **`ClarificationRequest`（typed Pydantic model）**：`question: str`（validator 保證非空，對齊 Stage4Response
  action='unclear' 藍本）+ `stage: int`（決定 re-emit 哪個 checkpoint）。
- **`_emit_clarification(req, state)` helper**：emit `req.question` narration + re-emit `stage=req.stage` 的
  checkpoint（恢復前端 reply UI，前端 continueLiveResearch 已把 reply UI 隱藏）+ return state（不 advance）。
- **收編既有兩條 spine**：Stage 1 empty-ops + Stage 4 unclear 兩條 inline dispatch 收編到共用 helper，**行為零漂移**
  （既有 `test_stage4_response_dispatch.py` / `test_stage1_dialog_loop.py` 全綠為 gate）。

**明列 backlog 不接的歧義點**（本輪 scope 護欄）：檢索歧義 / 實體混淆消歧 / gap 分類歧義 / 字數格式抽取歧義 —— 這些
未成熟或 UX 不同，不套本機制（避免輕量抽象拖成無底洞）。

#### 4.6.4 D-7 Stage 4 Reframe Entry（原 §4.8.8）

User 在 Stage 4 表達結構訴求時**不退回 Stage 1**，改在 Stage 4 直接 trigger reframe entry：
- `_try_stage_4_reframe_entry` reuse `_parse_stage_1_intent` 解 user_message
- emit detail-rich confirm proposal（§4.3.4 D-6 helper）
- `state.current_stage` 保持 4
- Confirm 後保持 Stage 4 等格式 reply

**兩個 pending flag 並存**：
- `state.pending_format_confirmation`：format spec 記下後等 OK
- `state.pending_reframe_json`：reframe 等 OK

User reply 先走 reframe 短路（confirm/cancel/adjust 三分支），reframe 解決後 format pending 還在等下一輪 OK。

### 4.7 Stage 5 — Writer

#### 4.7.1 Per-section Checkpoint Flow（VP-7，原 §4.10）

**設計反轉**：`_run_stage_5` 從 for-loop 改 **single-step**。每次只寫**一段**，完成後立即 emit per-section checkpoint 並 return。User 必須主動回覆「繼續 / 修改 / 匯出」才能往下推。

```
flow (n 段):
  Stage 5 進場 → outline planner → narration「規劃完成」
  → _run_stage_5 寫第 1 段 → emit checkpoint「第 1/n 段完成，三選一」
  → user reply「繼續」→ _handle_stage_5_response → _run_stage_5 寫第 2 段 → checkpoint
  ...
  → 寫到第 n 段 → emit all_done + final checkpoint「進入匯出？」
  → user reply「匯出」→ complete_stage → _run_stage_6
```

**State 追蹤**（`LiveResearchStageState`）：
- `last_completed_section_index: int = -1`（既有）
- `stage5_waiting_for_user: bool = False`（VP-7 新增）

**`_run_stage_5` Single-Step 語義**：
```
進場 → outline planner (idempotent) → next_i = last_completed + 1
if next_i >= total: emit all_done + final checkpoint; waiting=True; return
if connection_alive == False: return early
emit started → write_section(next_i) → append → last_completed = next_i
emit section_done
if next_i == total-1: emit all_done + final checkpoint
else: emit per-section checkpoint「第 K/N 段完成。要 (1) 繼續 (2) 修改某段 (3) 匯出？」
waiting=True → return state
```

CancelledError 仍 re-raise；`stage_5_writer_running` 在 finally clear。

**`_handle_stage_5_response` Dispatch**（含 completeness gate，#11 Part B / D-2026-06-11 決策 4）：
```
pending_recollect_confirmation==True + 非空 msg → 四段式 confirm 路由（§4.7.8）
auto_continue / empty msg → completeness gate：未寫完→繼續寫下一段；全寫完→complete_stage
export keyword shortcut (整句完全等於匯出詞) → completeness gate（見下）
continue keyword shortcut (整句完全等於 continue 動詞) → _run_stage_5
meta-intent ABORT/SKIP → completeness-aware 停 checkpoint 問釐清（絕不靜默匯出）
LLM intent parse → action:
  structure_change → friendly redirect narration + 保持 checkpoint
    ⚠ 判讀邊界（`orchestrator.py` prompt）：structure_change
    僅限「整章 / 章與章之間」的結構操作（合併整章 / 拆分整章 / 刪整章 / 改章數 / 章節間重排）。
    **段內順序操作**（對調 / 重排 / 調順序 / 換順序，作用在一段之內）一律 revise_section，
    **即使錨點不是段號**（「這段」「這部分」「最後一段」也算段內）。動詞本身不決定 action，
    看作用層級。修法 = 純 prompt 工程；真 LLM 矩陣驗證 16 case ×3 全綠（段內 5 BUG 修好、章節級
    對照組不被矯枉過正）。target_index 仍依下方 null 規則（無段號錨點 → null，系統反問改哪段）。
  done → ★ completeness gate（見下，LLM-done 分支）
  recollect → consent checkpoint（§4.7.8）
  continue_writing → _run_stage_5
  revise_section:
    target_index = parsed（FIX-6：缺失→clarifying question 列出已寫章節，不再靜默 fallback）
    clamp [0, total)
    emit「正在修改第 K 段...」→ write_section(target) → 取代 written_sections[target]
    emit per-section checkpoint「修改完成。三選一」
  parse fail → 保持 checkpoint + 「沒看懂」narration
```

**Stage 5 Completeness Gate（#11 Part B，D-2026-06-11 決策 4）**：
`_stage5_remaining_count(state)`（`orchestrator.py`）計算未寫完章節數，匯出意圖統一過此 gate ——
**未寫完不准進 Stage 6**（防匯出半成品）：

| 觸發路徑 | 未寫完（remaining>0）行為 | 全寫完行為 |
|---------|--------------------------|-----------|
| export keyword shortcut（整句＝匯出詞）| block，emit「報告還有 N 段沒寫完，要先寫完才能匯出。要繼續寫嗎？」+ 停 checkpoint | `complete_stage` → Stage 6 |
| **LLM-done action**（語意等價自然語句如「好了就這樣」走 LLM → `action="done"`）| **block**，emit `lr_copy.stage5_done_unfinished_gate_prompt(remaining)` 釐清 checkpoint（**不硬轉 continue，不違逆 user 結束意圖**）| `complete_stage` → Stage 6 |
| auto_continue / 空 msg | 繼續寫下一段（不匯出）| `complete_stage` |
| meta-intent ABORT / SKIP | completeness-aware 停原地問「繼續寫完 / 修改某段」| 給「接受 / 繼續編輯」二選一 |

> **背景**：整句「完成」走 export shortcut 已被 block，但語意等價的自然語句（「好了就這樣」）會走 LLM →
> `done` → 舊行為直接 `complete_stage`（Stage 6 publish gate 只是 warn-only）→ 匯出半成品 = #11「中途完全
> 不給匯出」的漏網路徑。LLM-done 分支補同款 gate 堵此洞。

**設計決策（D-D / D-E / D-F）**：
- ~~**D-D**：revise_section target_index 解析失敗 → fallback 最後完成段 K~~ →
  **已反轉（FIX-6，Cayenne #14）**：target 不明（None）改 emit
  clarifying question 列出已寫章節、停在 checkpoint 等 user 回，**不 mutate 任何 section**（靜默改錯段比多問一句嚴重）。
  只攔 None；target 有給但 out of range 仍走 clamp。
- ~~**D-E**：多段未寫完直接 export → 直接進 Stage 6，不問確認~~ →
  **已被 completeness gate 取代（2026-06-11，見上表）**：未寫完一律 block，不再直接進 Stage 6。
- **D-F**：Frontend progress bar 暫不加。typing indicator「第 K/N 段完成」已足夠。

#### 4.7.2 Writer Typed Citations + APA（原 §4.13.4）

```python
class CitationInline(BaseModel):
    evidence_id: int  # 必須 ∈ analyst_citations 白名單

class LiveWriterSectionOutput(BaseModel):
    section_content: str  # 含 {cite:N} placeholder
    citations: List[CitationInline]

class EvidencePoolEntry(BaseModel):
    author: str = ""  # 缺 → render fallback 文章標題前N字加引號（APA 標準；不再 fallback source_domain）
    year: str = ""    # 缺 → published_at derive 年份 / 都缺 'n.d.'（year 缺不再連坐 author 落來源不明）
```

**OQ-5 CEO 拍板**：**立刻 strict** — Writer LLM 只 output `{cite:N}` placeholder + structured `citations` list；舊式 inline `(Author, Year)` 字串禁止。沒 dual mode 過渡期。

**OQ-3 CEO 拍板**：APA mode 中文 author 整名 render「(王立人, 2022)」，不區分 surname。

**Renderer**（`_render_section_citations` staticmethod）：

| citation_format | `{cite:1}` → | author/year 缺時 |
|-----------------|--------------|------------------|
| `author_year` | `(王立人, 2022)` | `(source_domain, n.d.)` + methodology_note 明示 fallback |
| `numeric` | `[1]` | n/a |
| `footnote` | `¹` (unicode superscript) | n/a |
| `none` | `''`（移除） | n/a |

Wired 進 `_write_section` 在 `apply_hallucination_guard` 後跑（guard 已過濾 phantom citations）。

**Hallucination Guard Check 3**：`section.citations[i].evidence_id ⊆ valid_evidence_ids`。違反 → 過濾 phantom + `confidence_level="Low"` + methodology_note append。

> **⚠ 重要補充（2026-06-15）**：上述「Check 3 = citation-id 白名單」只是 LR per-section 防護的
> **第一道**（`apply_hallucination_guard`，純 deterministic）。2026-05-28 DR-parity sprint 之後，
> `_write_section` 還串接了**內容層 entity grounding + auto-rewrite + partial block** + **publish
> gate（critic + CoV-lite）**，整套住在 `reasoning/live_research/hallucination_guard.py`（spec 舊版
> 完全沒提此檔）。完整三層防禦見新增的 **§6.9（grounding guard）+ §6.8（publish gate）**。

**v15 P1-B lesson**：user 明示 APA，writer 仍出全 `[N]` 138 個 — citation_style enum 接線從 user_voice 到 writer prompt 必須完整，不可在 v15 流程斷掉。每次 release 必須驗 APA path（real persona E2E）。

#### 4.7.3 Writer Cancellation UX-4（原 §4.7）

**契約：User stop 路徑整套已移除（placebo），不得復活。**

VP-7 single-step flow 之後，每段完成即停在 per-section checkpoint 等 user reply ——
**per-section checkpoint 本身已是中斷點**，writer 在單段內無 inter-section break 機會，
「停止寫作」按鈕在單段執行期間（10-30 秒）按下也無從 break，等於 placebo。整套移除三處：
- (a) `LiveResearchStageState.stage_5_stop_requested` 欄位刪除；
- (b) `/api/live_research/stop` endpoint + `requestStop()` + `_reload_stop_flag` writer 內檢查全移除；
- (c) 前端停止按鈕移除。

**不得以任何形式復活此停止路徑**（placebo，且 single-step flow 下無單段內 break 機會）。

**取消路徑現況**：只剩 **Disconnect / Cancel（preemptive）** 一條 ——
關 tab / 網路斷 → `AioHttpStreamingWrapper._mark_disconnected()` → `_on_lr_disconnect` callback。
但注意：「斷線不取消」改版後，斷線 server **不再 `.cancel()` task**，
而是把當前 stage 跑到下個 checkpoint 才停存檔（離線跨 checkpoint 計數 + 燒錢上限進 DB state，§7.3 / §4.9）。

state 持久化：每段成功 `state.written_sections.append(...)` 同步 `last_completed_section_index = i` 並 `_save_state`。

**State Schema（現況）**：
```python
# stage_5_stop_requested: bool  ← 已移除（placebo）
stage_5_writer_running: bool = False
last_completed_section_index: int = -1
```

**`continue_writing` action 現況**：`_parse_revision_intent` enum 的 `continue_writing` action（trigger keywords：繼續/寫完/剩下/continue/往下寫）
**仍存在**，但觸發來源是 per-section checkpoint 的 user reply（§4.7.1），非 stop 後重 emit。

**dry_run / mock_bab 特殊處理**：fixture writer 立即回沒有 await 點 → CancelledError 來不及 raise。`_run_stage_5` 每段開頭加 `await asyncio.sleep(0.05)` yield point。

**Error 紀律**：
- State load fail → log + return False（writer 繼續，不 silent 殺 loop）
- State save fail → log + `raise`（不 silent fail）
- CancelledError → try/except 必須 re-raise（否則 task wrap 收不到 cancel 完成訊號）

#### 4.7.4 Revise Dialog

User 在 per-section checkpoint reply「第 K 段太短」/「補 2050 淨零脈絡」→ revise_section path（§4.7.1）→ `revise_instruction` 串接 (`user_voice.revise_instructions[section_idx]` accumulate List) → `writer.compose_section(revise_instruction=...)` → writer prompt builder 條件式注入 `## 段落修改指示` block。

#### 4.7.5 Outline Planner 兩階段

Stage 5 進場前先跑 outline planner — 從 ContextMap chapter_source（reframe 後的 cm.topics 或 format_specs.chapters override）衍生 `BookOutline` (title + brief per chapter)。

**Skeleton Fallback 紀律**：LLM call 失敗時用 chapter_source 衍生 default BookOutline，避免 hard fail 卡 Stage 5。**呼叫端必須 emit narration 明示「outline planner 降級」**，不可 silent fail。

**Blocker A**：outline planner prompt 接收 `target_word_count` budget（user_voice 來），per-chapter 字數均勻分配。

#### 4.7.6 Reframe → Writer 接線（v15 P0-3 lesson）

**紀律**：Stage 1 / Stage 4 reframe 改了 `cm.topics`，**Stage 5 writer 必須讀新值**。

**v15 P0-3 觀察**：即便 reframe commit OK，writer 用原 ContextMap 9 個 core topic（不對齊 user 5 章）。Plan 2 `_resolve_chapter_source` 在 reframe path 沒接到，或 reframe op 沒真的改 cm.topics。

**接線 contract**：
1. `_apply_context_map_revisions` 真實 mutate `state.context_map_json` 中的 cm.topics
2. `_run_stage_5` 進場時 reload state.context_map → 重新 `_resolve_chapter_source`
3. Outline planner 吃 resolved chapter_source，不吃 cached ContextMap
4. Writer 吃 outline_planner output，不吃 cached chapter list

每個 reframe path 必加 verification test：reframe 後立刻 dump cm.topics，confirm 真的改了，writer 真的讀新值。

#### 4.7.7 Per-section 寫作後守門管線（DR-parity sprint land）

**檔案**：`reasoning/live_research/orchestrator.py` 的 `_write_section`。每寫完一個 section，
依序跑下列守門（全部在同一 `_write_section` 內，三層防禦）：

```
writer.compose_section() 寫出 section
  ↓
1. apply_hallucination_guard(section, valid_ids)        ← deterministic（§4.7.2 Check 1-3）
  ↓
2. entity_grounding_check(section, chapter_evidence_text) ← 內容層 LLM grounding（§6.9）
   ├─ ungrounded 非空 → auto-rewrite 1 次 → 再 apply_hallucination_guard + entity_grounding_check
   │     └─ 仍 ungrounded → partial block（刪純未驗證句 / DR 式退化，CEO 決策④，§6.9）
   └─ GroundingCheckUnavailable → DR 式退化（保留正文 + 降 Low + methodology note，§6.9 R1）
  ↓
3. specificity_check(section, ...)                        ← 對稱守門（evidence 有具體但 prose 抽象，§6.9）
   └─ flag → specificity auto-rewrite → 重跑 guard + grounding（rewrite 不可反開洞）
  ↓
4. _run_publish_gate(section, ...)                        ← F1 critic + F3 CoV-lite（§6.8）
   └─ REJECT → 整章替換查核失敗文 / WARN → 降信心 + amber strip / PASS → 照常
  ↓
5. _maybe_narrate_word_overshoot(section, target, user_specified) ← 字數偏長透明化旁白（§4.7.9）
   └─ user 明確要求字數 + 實際 > target × _WORD_OVERSHOOT_RATIO → 只發「本章比預期長、內容保留」旁白
      （content 一字不動）；user 沒指定字數 → 完全不處理（不切不旁白）
  ↓
（以上全部跑完、status=="drafted" 才 citation render — truncate 在 render 之前）
```

**接線參數（writer 現吃的新 kwargs，spec 舊版未列）**：`evidence_sufficiency`（per-chapter
critical/thin/ok，§6.6 module 5）、`time_constraint`（Track E temporal binding）、`knowledge_graph`
（Track D）、`prior_used_entities` / `all_prior_chapter_summaries`（cross-section 一致性，A/B writer 品質）、
`ungrounded_entities_revision`（rewrite path）。

**紀律**：所有 rewrite path（grounding rewrite / specificity rewrite）寫完後**必須重跑** citation +
fabrication guard——「寫具體」最易誘發編造，rewrite 不可反開防護洞（A/B writer 品質 lesson）。

#### 4.7.8 Stage 5 → Analyst 退回補搜迴圈（P-補搜）

CEO 根解：Stage 5 寫到一半發現「資料不夠/太薄/要找更多來源」時，能**退回 analyst 重進完整
analyst→critic→writer→critic loop**，疊加新 evidence 重跑 BAB，而非只用現有資料重寫某段。

**觸發**：Stage 5 user reply → `_parse_revision_intent` 新增 `action="recollect"`（intent 契約：「資料
不夠/不足/太薄/去多查/找更多來源/補充蒐集」等要求蒐集**新資料**的訊號 → recollect；「第 N 段重寫/加強」用
現有資料 → revise_section）。

> **資料不足判定點的引導文案（契約）**：資料不足判定點（critic F1 REJECT / WARN、C-1 `blocked_no_evidence`）的 `lr_copy` 文案除陳述「資料不足」外，**必須**補上明確出路引導「可回覆『再去找更多資料』讓我退回補搜」，對齊上述 recollect intent 契約的觸發詞，user 不必自己猜關鍵字。

**四段式 confirm 路由**（`_handle_stage_5_response`，`pending_recollect_confirmation==True` 時）——
補搜會清下游章節（不可逆），故走 informed-consent 兩段式（先 emit consent prompt 設旗標、下一輪 user 回覆才執行）：
1. **段 1**：含確認 token 的 bounded affirmative（「確認」「OK。」「好，開始吧」）→ 直接 `_dispatch_recollect`（不打 LLM）。
2. **段 2 abort（先於段 3）**：`_classify_meta_intent==ABORT`（「算了/取消/不要了」）→ 取消、不刪章節、回常規 checkpoint。abort 優先級最高（誤判代價最高）。
3. **段 3 無 token 短肯定兜底**：`_looks_like_bounded_affirmative_shape`（「好，那就重新蒐集吧」「是的」「行」）→ 確認執行（修 K Round 4「無 token 自然肯定句漏接 → 二次 consent loop」；含修改名詞 marker 者不走此兜底）。
4. **段 4 substantive**：其餘（「改第 3 段」「再多查經濟面」）→ **不吞**，fall through 到既有 `_parse_revision_intent` 正常路由（「不漏使用者任何一句話」鐵律）。

**Cap**：`_recollect_cap()` default **2**（`features["lr_recollect_cap"]` 可 override）。達上限 →
block + 明確告知 `lr_copy.RECOLLECT_CAPPED_NARRATION`（非 silent）。`recollect` action 進 consent 前預檢、
`_dispatch_recollect` 入口二次防護。

**`_dispatch_recollect` 執行序**：
1. cap 二次檢查 → 取研究問題 + 保留的 evidence_pool 當 seed（`seed_counter = max(pool.keys())`）。
2. snapshot 入口 state（`to_dict`，供 rollback）。
3. `recollect_count += 1` → `reset_for_recollect()`（清下游 + 退 Stage 1）。
4. **count+1 + reset 後、await 長跑 `_run_stage_1` 前先強制 `_persist_checkpoint_boundary`** —— 防雙擊/重送/SSE reconnect 並發兩 request 都過 cap → 雙倍燒錢（H，Gemini #4 + Codex #4）。
5. `_run_stage_1(state, query, [], seed_evidence_pool=seed_pool, seed_counter=seed_counter)` —— **seed 雙參數同傳**，engine 從 `counter+1` 起分配新 ID **疊加**既有 pool（B1，防 ID 衝突 / 防空 pool 覆寫使疊加失效變清空重蒐）。
6. 失敗 → 用 snapshot rollback（還原章節 + count + 所有清掉的欄位）+ emit 明確 error checkpoint（不可 silent fail / 不留半重置 broken state，I，Codex #7）。

**`reset_for_recollect()`（`stage_state.py`）清/留窮舉**：
- **清**：current_stage→1、completed_sections / written_sections / book_outline_json / executed_searches、format_specs 的 `chapters`（rebind 新 dict 不 in-place pop，防污染 snapshot 淺引用，C-1）、所有 pending guard（reframe / format confirm / writer running / waiting）、`pending_recollect_confirmation`（G，Codex #6 —— 不清會讓下輪 reply 被誤攔）、推理產物（evidence_usage / knowledge_graph / critic_section_reviews / user_voice.revise_instructions）。
- **留**：evidence_pool_json（疊加非清空）/ context_map / initial_context_map / style / time_constraint / schema_version / offline_* / citation 設定 / `recollect_count`（cap 跨輪累積靠它）/ append-only 稽核 log（rejected_claims_log / consistency_drift_log）。

**State 持久化新欄位**（§4.9.1）：`recollect_count: int = 0` + `pending_recollect_confirmation: bool = False`
（to_dict/from_dict 對稱，舊 session fallback 0 / False，絕不被誤判 capped / 殘留 pending）。
**前端**：recollect 退回時 `clearLRStage5Artifacts` 清 Stage 5 section cards + chat 泡泡（資料仍在 DB，純 DOM 清除避免殘留誤導）。
**測試**：`tests/test_lr_stage_state_recollect.py`、`tests/test_lr_recollect_cap.py`、`tests/test_lr_recollect_dispatch.py`、`tests/test_lr_revision_intent_recollect.py`。

#### 4.7.9 章節字數 overshoot 透明化旁白（軟約束，2026-07 regression 修復）

**背景**：字數對齊走軟約束（outline planner 分配 target budget §4.7.5 + writer prompt ±15%）。
prod 證實軟約束壓不住字數（target=800 actual=2258），曾一度改 post-process **硬切**（commit
9371337b），但硬切**截斷正文** → 報告出現「完整句子。…」斷尾，CEO「切掉就不能用了」。**改回軟約束**：
`_maybe_narrate_word_overshoot` 在 §4.7.7 守門管線**第 5 步**只發透明化旁白，**絕不砍 content**。

**契約**：
- **content 一字不動**：post-process 只做**不破壞使用者可見產出**的透明化（旁白提示），禁止砍/改
  content（砍掉的正文使用者要不回；lessons-live-research.md「壓超標的正解是透明化旁白，不是砍使用者內容」）。
- **僅 user 明確要求字數才發旁白**：`user_specified_word_count`（來自 `user_voice.target_word_count`
  非 None，或 `format_specs.chapters[i].word_target` > 0）為 True 才發。**user 沒指定字數 = 完全不管
  字數**（不切也不旁白，不對系統自塞的 default 動刀）。
- **閾值**：`_WORD_OVERSHOOT_RATIO`（超標 30% 才發，正常波動不洗訊息）。
- **status gate**：僅 `status=="drafted"` 才判（被 block / critic_rejected / guard_failed 的替換文字數無意義）。
- **target 度量**：`_count_chapter_words`（剝 `{cite:N}` 後字元數）；target≤0 → no-op。
- **no silent fail**：發 `lr_copy.chapter_word_overshoot_narration`（誠實告知「本章偏長、內容完整保留」）。
- **不觸發 auto-rewrite / 不砍 content**：只 emit 旁白，不回呼 writer.compose_section、不 mutate section_content。

> **與 outline planner 的接線（(b) 根解）**：outline planner prompt 對「user 未指定字數」的章一律回
> `target_word_count=0`（`prompts/outline_planner.py`），不自動塞 800-1500 default —— 避免系統自塞的
> 字數觸發任何字數處理。

**測試**：`tests/unit/reasoning/test_lr_chapter_truncate.py`（旁白發放 / content 不動 / user 未指定
不發 / 閾值 / status gate）。

### 4.8 Stage 6 — Export

Final report 渲染 + 提供 download。User 在 Stage 5 final checkpoint 回「匯出」進入（**未寫完章節已被 §4.7.1
completeness gate 擋下，不會以 partial 進此 stage**；極端 partial case 仍能渲染已寫段落）。

完成 emit `final_result` event → 前端切 tab 顯示報告 + citation links + collapsible sections。

Stage 6 組 H1 前呼叫 `_generate_report_title`（low-tier LLM，input=research_question + 各章標題/摘要）生成有質感報告標題；失敗降級 research_question（不 silent fail）。詳見 §7.4.1 後端配套。

### 4.9 State Persistence Contract

#### 4.9.1 PG `live_research_state` JSONB Schema

```python
@dataclass
class LiveResearchStageState:
    current_stage: int
    context_map_json: str
    initial_context_map_json: str
    evidence_pool_json: str  # Dict[int, EvidencePoolEntry]
    executed_searches: List[str]
    completed_sections: List[str]
    last_completed_section_index: int
    written_sections: List[Dict]
    # stage_5_stop_requested: bool  ← 已移除（placebo，停止按鈕機制廢棄，見 §4.7.3）
    stage_5_writer_running: bool
    stage5_waiting_for_user: bool
    pending_reframe_json: Optional[str]
    pending_format_confirmation: bool
    format_specs: Dict[str, Any]
    user_voice: UserVoice
    style_features: Optional[Dict]
    book_outline_json: Optional[str]
    final_report_markdown: str        # 路 3 P-回顧：Stage 6 後端組好的整份 full_report markdown（§7.4）
    recollect_count: int              # Stage 5 退回補搜累計次數，cap 計數（§4.7.8）
    pending_recollect_confirmation: bool  # recollect informed-consent 兩段式 confirm 旗標（§4.7.8）
    # offline_since / offline_capped / offline_cap_reason / offline_checkpoint_advances
    # （既有離線防呆欄位，stage_state.py 實際定義，本精簡版 schema 表省略未列——
    # plan: lr-disconnect-midstage-persist 未新增欄位，僅擴張既有欄位的寫入時機，見 D-4）
    ...
```

每次 stage transition / user reply 處理後 `_save_state`。

#### 4.9.2 `lr_session_id` UUID Lifecycle

- Server-generated UUID（首次 `/api/live_research` 進入時）
- Frontend echo 回（`continueResearch` body 帶 `lr_session_id`）
- Backend `_load_state(lr_session_id, user_id)` lookup

#### 4.9.3 `_load_state` Failure — No Silent Re-run (R5 fix)

**舊行為（已移除）**：state 找不到時 silent fallback `runQuery()` → mock path 重 emit Stage 1 初始 fixture checkpoint → user 在 Stage 5 reply 但被退回 Stage 1，完全不知後端發生什麼事。

**新行為**：emit 明示 narration「找不到先前的研究 session（可能已過期、被重置、或 SSE 連線中斷後未能恢復）。請點「重新開始研究」重新進入新的研究流程。」+ 回 error response (`status="error", error="state_not_found"`)。**不重 emit Stage 1，不靜默 re-run。**

### 4.10 Failure / Silent-Fail 紀律

對齊 CLAUDE.md「不可 silent fail」紀律：

| 場景 | 紀律 |
|------|------|
| LLM TypeAgent retry × N 仍失敗 | skeleton fallback + emit narration 明示「降級為 X」 |
| outline planner LLM call fail | skeleton fallback (chapter_source 衍生) + narration 明示降級 |
| ~~state load fail in `_reload_stop_flag`~~ | ~~log + return False~~（`_reload_stop_flag` 隨停止按鈕移除，已不存在，§4.7.3）|
| state save fail | log + `raise`（caller bubble up）|
| Retrieval fail | narration 明示「資料來源蒐集降級」 |
| **`_execute_search` per-seed 檢索例外**（如 embedding 雙 provider 同失敗，2026-07-05 `84288461`）| log + per-run 一次降級旁白 `lr_copy.RETRIEVAL_ERROR_DEGRADED_NARRATION`（與 SEARCH_REQUIRED「補搜**無結果**」語義分離：這裡是查詢**出錯**），跳過該 seed 續跑 |
| **SEARCH_REQUIRED 二次補搜無結果 / re-run 仍非 DRAFT_READY**（§4.3.2）| forensic log + per-run 一次降級旁白 `lr_copy.SEARCH_REQUIRED_DEGRADED_NARRATION`，用原 analyst_output 續跑 |
| **mini-reasoning revise / re-review 失敗或 draft 空**（§4.3.2）| forensic log + per-run 一次降級旁白 `lr_copy.MINI_REASONING_REVISE_DEGRADED_NARRATION` + break，原 REJECT 入庫 forensic |
| **recollect `_run_stage_1` 失敗**（§4.7.8）| 用入口 snapshot rollback（章節 / count / 欄位全還原）+ emit 明確 error checkpoint，不留半重置 broken state |
| `_load_state` returns None | emit error narration + frontend 跳「重新開始」（§4.9.3） |
| CancelledError | try/except 必須 re-raise（否則 cancel 訊號鏈斷）|
| `_parse_stage_*_intent` LLM fail | retry / fallback intent + clarifying_question path (§4.3.5) |
| Catch Exception silent pass | **禁止** — 任何 catch 必 log warning 且不吞錯 |
| **Stage 2 BAB loop client 斷線**（plan: lr-disconnect-midstage-persist，D-7）| cooperative stop（`_check_connection` 回 "offline" 不 raise，設 `engine.stopped_early=True`）+ 每 topic 落盤；**`completed_sections` 只在 `stopped_early=False`（正常收斂）時 append**，中途被打斷的 topic 不標記完成、evidence 仍留供 resume 續跑；**禁** raise ResearchCancelledError 走 error 路徑蒸發進度 |

---

## 5. Auth Contract

### 5.1 真實 JWT Path

**檔案**：`code/python/webserver/middleware/auth.py`

```
Request → auth_middleware
  → 抽 Bearer header / cookie / query param `auth_token`
  → jwt.decode(token, JWT_SECRET, ['HS256'])
  → 取 payload.user_id (UUID)
  → request['user'] = {id, name, email, org_id, role, authenticated=True, token}
  → handler
```

所有 LR endpoint (`/api/live_research`, `/api/live_research/continue`；~~`/api/live_research/stop`~~ 已隨停止按鈕移除 2026-06-04，見 §4.7.3) 走此 path。`request['user']['id']` 是 UUID，流入所有 PG operation。

### 5.2 authenticatedFetch Refresh-then-retry

**檔案**：`static/news-search.js`

Frontend 所有 LR API call 走 `authenticatedFetch`（LR continue + initial fetch 都走此 path）。401 回應觸發 refresh token retry，避免 idle 期間 access_token cookie 過期後 raw fetch 無 Bearer header → middleware 視為 unauthenticated。

對 SSE streaming 兼容：`authenticatedFetch` 不 await body，直接 return Response。

### 5.3 Token Expire Mid-LR

Cookie path `access_token` httpOnly 由 server 設置。Mid-LR token expire 場景：

1. User 進入 LR Stage 5 寫到一半，access_token TTL 到期
2. 瀏覽器自動不送 cookie（過期）
3. 下次 `/api/live_research/continue` request 無 Bearer / cookie → middleware 401
4. Frontend `authenticatedFetch` 攔截 401 → call `/api/auth/refresh` → 拿新 access_token → retry 原 request

**Token expire 而 refresh 也失敗**（refresh_token 也過期）：middleware 401 → frontend 跳「請重新登入」（不靜默繼續）。

### 5.4 Dev Auth Bypass — 已移除（契約：不得復活）

`NLWEB_DEV_AUTH_BYPASS` 不在設計範圍。曾有的 dev bypass 分支已完全刪除（撞過 v9 silent-fail、v15 P0-1 `'dev_user'` string id vs UUID 500，且掩蓋 production-path bug）。

**契約**：testing 一律真實登入（§5.5），不得以任何形式重新引入 auth bypass。

### 5.5 E2E 測試帳號（契約：真實登入）

E2E agent 必須真實登入（POST `/api/auth/login` 拿 JWT），不可 bypass。

- **帳號 / 密碼**：以**當前執行環境的 PG `users` 實際值為準**——本地 credential 每台機器不同（可任意 reset），線上各使用者有自己的帳密。spec 不凍結具體密碼值。派 E2E agent 前，先確認當下這台機器的實際帳密再寫進 prompt。
- **歷史教訓**：過去多個 handoff 把密碼大小寫寫錯 → E2E 登不上 → fallback dev bypass → 撞 P0。**派工時對齊當前環境實際值**是這條紀律的本體（不是凍結某個固定密碼）。

### 5.6 PG `user_id` UUID Contract (v15 P0-1 lesson)

所有 PG operation 的 `user_id` column 是 UUID type（schema strict）。

**禁止**在 auth path 注入 string id（如 `'dev_user'`）— 寫 PG 時必撞 schema → 500。

**正確 path**：所有 user_id 必須來自 `request['user']['id']`，該值由 JWT payload `user_id` 解出，原始來源是 PG `users.id` (UUID)。

**Future-proof**：若需 placeholder user（如系統 task），必須在 `users` table 真實插入 UUID row（如 `00000000-0000-0000-0000-000000000001`），不可在 middleware 編造 string。

---

## 6. 後端規格

### 6.1 與 DR Composable Pipeline 的關係

LR **不走** DR 的 Composable Pipeline（`ResearchState` + `_phase_filter_and_prepare` / `_phase_actor_critic_loop` / `_phase_writer` / `_phase_format_result` 四 phase），改用自建的 `BABLoopEngine` + 6-Stage 對話 loop（state machine 見 §4）。DR Composable Pipeline 的規格在 `reasoning-spec.md`，不在本文範圍。

LR 與 DR 真正的共用點：(1) retrieval 前處理（繼承 `DeepResearchHandler.prepare()`）；(2) BAB 內部複用同一批 LLM agent。

### 6.3 BAB Loop Phase SSE Events

BAB Loop（`loop_engine.py` `run_loop`）每個 phase 邊界 emit `research_phase` event，前端 `phaseLabels` 對應中文標籤：

| phase | status | 含義 | 前端標籤 |
|-------|--------|------|---------|
| `bab_phase0` | completed | build initial B（建初始 ContextMap）| 建立初始研究結構 |
| `bab_phase1` | started / completed | derive search plan | 推導搜尋計畫 |
| `bab_phase2` | started / completed | execute retrieval | 執行資料蒐集 |
| `bab_phase3` | started / completed | mini-reasoning（前後對稱 emit；early-skip / 失敗輪不 emit completed，見 §4.3.1）| 深入分析與交叉檢驗 |
| `bab_phase4` | started / completed | refine B→B' | 本輪結構調整 |

> DR 的 4 個高層 phase event（`filter_and_prepare` / `actor_critic_loop` / `writer` / `format_result`）屬 Composable Pipeline，LR 不 emit。

### 6.4 System Prompt — LR 專屬 prompt（已實現）

LR 已有自造 / 衍生的專屬 prompt path（非「完全 reuse DR」）：Associator 整套（`build_context_map` / `derive_search_plan` / `refine_context_map`）、Writer 分段 compose（`build_section_compose_prompt`）、Critic publish gate（`build_section_publish_gate_prompt`，code 標「新發明」）。prompt 風格為中文、人格化、強制 Actor-Critic 對抗。各角色 prompt builder 與摘錄見 `lr-onboarding.md` §4。

### 6.5 LLM Cost Optimization

**Level 分配判準**：generative + 深度推理 + 最終使用者產出 → `high`（gpt-5.1）；mechanical extraction + intent classification → `low`（gpt-4o-mini）。

| 函式 | 任務性質 | Level |
|------|---------|-------|
| `build_context_map()` | 設定研究方向（generative）| `high` |
| `derive_search_plan()` | 機械性提取 | `low` |
| `refine_context_map()` | 整合搜尋結果（generative）| `high` |
| style analysis / intent parse | extraction / 分類 | `low` |
| `compose_section()` | 最終使用者產出 | `high` |

**不可妥協項**（必須 high）：`build_context_map` / `refine_context_map` / `compose_section`。整輪成本估算見 `lr-onboarding.md` §7.4。

---

## 6A. DR-Parity 三層品質防禦子系統

> LR 賣的是「可信」，Writer 寫出的每段話進報告前都要過三層守門（L1 citation 白名單 → L2 grounding/specificity → L3 publish gate），全串在 `_write_section` 內。本章定義三層的契約。子系統由 2026-05-28~29 DR-parity sprint（7 Track）+ 後續接線批次 land；commit 群與踩坑 lesson 見 `memory/lessons-live-research.md`。

### 6.6 Web Search 接線

**契約**：`enable_web_search`（per-request，預設 `false`）必須在**兩條 request path 都帶**，否則 Stage 2 web search 實質關閉：

| Request | 帶 flag 的位置 |
|---------|---------------|
| 初始 `/api/live_research` | `performLiveResearch` POST body 帶 `enable_web_search=true`（前端 default-on）|
| `/api/live_research/continue` | continue body 同樣補帶（否則 continue path 的 Stage 2 per-topic BAB web search 關閉）|

**接線深度**：LR web search `num_results = max_results_lr = 8`（DR 維持 5，split-key 不 fallback）。

> **驗收限制**：「Stage 2 continue path web search 真實撈到站外 evidence」這條，mock_bab E2E **零判別力**（mock_bab 下 Stage 2 直接用 Stage 1 ContextMap、不進 BABLoopEngine，見 §8.1）。真驗收 = 真 BAB E2E（~$5）或 prod manual gate。

### 6.7 Gap Routing 四類

`loop_engine.py` 的 `_process_gap_resolutions_lr`。LR **只 handle 4 類**（DR 的 stock / weather / company API 三類在 LR 明示砍，log skip 不 raise）：

| GapResolutionType | LR 行為 | gate |
|-------------------|---------|------|
| `LLM_KNOWLEDGE` | 建 virtual doc 進 evidence_pool（source=llm_knowledge）| 無（一律跑）|
| `WIKIPEDIA` | 打 Wikipedia API（計入 cap）| `enable_gap_enrichment` |
| `WEB_SEARCH` | 打 Google Custom Search → `_add_external_evidence(source="web")`（計入 cap）| `enable_gap_enrichment` **且** `enable_web_search` |
| `INTERNAL_SEARCH` | no-op pass-through（已由 BAB main loop 處理，交下一輪 Associator）| — |

**Toggle gate（兩層）**：
- `enable_gap_enrichment=false`（per-request，預設 false）→ method early return，**所有 gap 跳過**
- `enable_web_search=false` → 只 `WEB_SEARCH` 類 log skip，其餘三類仍跑
- per-request `enable_gap_enrichment` + process-wide `gap_knowledge_enrichment`（Analyst prompt builder flag）各司其職

**Per-run 外部呼叫 cap**：`gap_routing.max_external_calls_per_run`（預設 `6`）。WIKIPEDIA + WEB_SEARCH 真打外部前計數，達上限跳過並 emit 一次 user-facing 旁白（`_narrate_gap_cap_once`，per-run dedup）。被 gate / 空 query / cap 跳過的 gap 不消耗額度。

### 6.8 Publish Gate（Track F：F1 critic + F3 CoV-lite）

`orchestrator.py` 的 `_run_publish_gate`。三層防禦的**第三層**（L1 = citation-id 白名單 / L2 = per-section entity grounding §6.9 / L3 = 本 publish gate）。

**Config flag**：`live_research_critic_publish_gate`（F1，預設 `true`）+ `cov_lite_enabled`（F3，預設 `true`）。LR 可用子 flag `live_research_cov_lite_enabled` 覆寫——此子 flag 未落 config 鍵，fallback 到 `cov_lite_enabled`（行為正確）；可只關 LR CoV 不動 DR。

**流程契約**：
1. `status != "drafted"` → short-circuit pass-through（F-AMB-7）
2. `chapter_evidence_text` 空 → 短路：標「查無可審來源」+ 降 Low（**不 silent PASS，不燒 high-tier call**）
3. F1 critic call → 抓 6 類 claim-level fabrication：numeric / temporal / causal / comparative / predictive / evaluative
4. F3 CoV-lite call（若 F1 verdict ≠ REJECT）→ F3 fail → degraded `verification_status="unverified"`（不 silent）
5. **Auto-escalate**：`contradicted_count > 0` → 升級 REJECT；`unverified_count >= 3` → WARN
6. 依 final verdict 一次性 mutate：**REJECT** → 整章替換查核失敗文（列最多 5 處問題句）/ **WARN** → 降信心 + amber strip（「[查核提醒：…]」append 進 methodology_note）/ **PASS** → 照常
7. 寫進 `state.critic_section_reviews`（含 `cov_verification_summary`）

**Fail 紀律（E1）**：F1/F3 拋例外 → forensic log + 旁白（degrade-and-narrate），**非 fail-open**（不可讓 section 未經 gate 原文通過）。

> **WARN explanation 完整輸出（契約）**：`warn_marker` 的 explanation **不截斷、完整輸出**（只做 `_sanitize_warn_explanation` 把內部 `[ ]` 換全形防破壞 dedup regex）。**不得重新引入字數上限**——critic 查核說明是使用者信任的關鍵訊息，攔腰砍斷會給出半句 warning。（早期 `_WARN_EXPLANATION_MAX=100` 是某次修 bug 順手夾帶的副產物，已移除。）

### 6.9 Grounding Guard（per-section entity grounding）

`hallucination_guard.py`。LR 自造（DR 只做 citation-id 白名單，無 entity grounding）。防「寫了 evidence 裡沒有的具體 entity」（幻覺）。

**`entity_grounding_check` 三段式**（方向：良好資料來源 → low model 判讀）：
1. `_extract_entities_for_grounding`：LLM（low）只**列出** prose 中具體 entity（國家/城市/機構/風場/法規/人名/數字），不判 grounded。抽取 fail → 回 `[]`（**fail-open**，安全方向：抽不出 = 沒東西要查）+ 通知 caller 補旁白
2. `_deterministic_grounded_filter`：字面命中 evidence（NFKC + casefold + 去空白正規化）→ 直接視為 grounded（零成本捷徑）
3. `_semantic_grounding_check`：殘餘不命中者 → LLM（low）語意判定（同義/全名/改寫/上位詞涵蓋，例：evidence「台灣電力公司」支撐 prose「台電」）

**R1 fail-closed 鐵律**（`GroundingCheckUnavailable` exception）：**語意判定階段** LLM exception / 爆 low-model context window / 回傳無法解析 → raise，**絕不回 `[]` 當全 grounded**（fail-open 會在 evidence 變多爆窗時悄悄放行所有幻覺）。caller 捕捉 → 退化路徑 (a)：保留正文 + 降 Low + methodology note 標「grounding 系統驗證失敗，本章未經完整查證」。

> **兩階段非對稱**（易誤判）：**抽取階段 fail-open**（回 `[]`）/ **語意階段 fail-closed**（raise）。端到端結果是 degrade-and-narrate（pipeline 不硬停、降級放行 + 旁白），但 grounding 決策本身是 fail-closed（絕不靜默回全 grounded）。

**Over-block 修法（四個關鍵決策）**：
- ① grounding 判讀 tier = **low**（資料好誰都能判，省錢）
- ② evidence 範圍 = **全 pool**（非本章 subset；內建 12000 字 budget cap + 4 級優先序：本章引用 > 有 claim > prior overlap > 其餘，防爆窗）
- ③ partial block = **刪未驗證句為主 + DR 式退化**（保留全文 + 降 Low + methodology 標哪些 entity 未驗證），不整章替換
- ④ 句子分類（`split_and_filter_ungrounded_sentences`）：候選刪除句若**任一**成立則**不硬刪**（保留 + 回報 unsafe_count）：(1) 同句含已驗證 entity；(2) 含 citation 標記 `[N]`；(3) 被上下文依賴連接詞綁定（但是/因此/然而/代名詞指代）。只有「純未驗證句」才 regex 直接刪。**不引入 LLM 改寫**（會引回模糊化）。

**`specificity_check`（對稱守門）**：與 grounding 反向——偵測「evidence 有具體資訊但 prose 全抽象」（under-specification）。drafted body chapter + evidence 有料 + prose 抽不到具體 entity → flag → auto-rewrite。intro/conclusion 章排除。重用 entity 抽取結果，零額外 LLM call。

### 6.10 其他 Track 子系統

B Citation / C External / D KG / E Temporal / F2 Consistency Monitor 等其餘 sprint Track 的子系統摘要與 lesson 見 `memory/lessons-live-research.md`。本文只就構成契約的 §6.6-6.9 + §6.11 詳述。

### 6.11 Evidence Sufficiency

`orchestrator.py` 的 `_compute_chapter_sufficiency(analyst_citations, evidence_pool)`（module-level）→ `_write_section` 注入鏈 → `compose_section`。

**契約**：用**全 evidence_pool 有料量**判充分度（**不是** `len(analyst_citations)`——全局 evidence 模型下 writer 讀全 pool，`analyst_citations` 空 ≠ 沒 evidence）：
- **`critical`** — pool 完全空（`len(evidence_pool)==0`）
- **`thin`** — pool 量 ≤ `EVIDENCE_THIN_CHAPTER_CITATIONS`（= 2）
- **`ok`** — pool 量 > 2

（intro/conclusion 章的 `ok` 覆寫由 caller 在 `_is_intro_or_conclusion` 處理。）

> **關鍵**：sufficiency **不是補資料機制**——它只分類 + 校準 writer 措辭（足章維持逼具體 / 薄弱章放行保守），**不呼叫 Analyst/Builder、不觸發補搜**。到了寫作階段就不再自動補資料，只負責讓報告誠實（避免 Writer 階段無限自動補搜燒錢）。防打架靠互斥：specificity rewrite 只在 ok 章跑、保守 calibration 只在 thin/critical 章。`thin` 閾值 ≤2 為初值，待真實 BAB 分佈微調。

---

## 7. 前端規格

> LR 前端主邏輯在獨立模組 `static/js/features/live-research.js`（被 `main.js` + `news-search.js` import，三處 `?v=` cache-bust 須一致——改任一處務必三處同步 bump，否則 module instance 分裂、shared session state 讀不到）。函式名與行號見 `lr-onboarding.md` §6；本文只定行為契約。

### 7.1 Mode Toggle + 發起

Mode toggle 四鈕（新聞搜尋 / 進階搜尋 / Live 研究[Beta] / 自由對話）。點「Live 研究」→ `currentMode='live_research'` → `performLiveResearch(query)` → POST `/api/live_research`，body `{ query, session_id, site, enable_web_search:true, enable_gap_enrichment:true }`（**無 `enable_live_research` flag——路由本身就是 LR 入口，見 §1**）。

### 7.2 Stage Accordion（6 dot）

6 個 stage dot（`data-stage="1..6"`）對應 6-stage loop（**不是** DR 的 4 phase）。依當前階段加 `completed` / `active` class。完成後點 dot 可 lazy render 該階段對話快照（§7.4.1）。

### 7.3 SSE Handler

`handleLiveResearchSSE` 分發 LR SSE event（event type → handler 對照見 §4.1 + `lr-onboarding.md` §6.1）：narration → 插 chat message；checkpoint → `showLRCheckpoint` 顯示 reply UI + 存 snapshot；section → `addLRSection`；export / final_result → 切 tab 渲染報告。

**斷線「不取消」恢復（契約）**：SSE client 斷線時 server **不 `.cancel()` task**，繼續把當前 stage 跑到下個 checkpoint 才停存檔（離線跨 checkpoint 計數 + wall-clock 上限進 DB state，達上限才停，見 §4.9 `offline_*` 欄位）。前端偵測斷線顯示中斷提示（非 error）；`online` / `visibilitychange` 醒來後 debounced **read-only 重連**（`_doLRReconnect` 只 GET state + render，**INVARIANT：絕不送 `/continue`**，避免重複燒錢）。三狀態分流 render（in_progress / checkpoint / offline_capped）。

> **切分頁 / 失焦不算斷線**：`visibilitychange` 只在「真的斷過線」（`_lrConnectionLost=true`，SSE 串流真的掛掉）後才觸發重連。切分頁、視窗失焦、最小化**不算斷線**——研究跑在後端、前端只是觀眾，串流在背景繼續收。

#### 7.3.1 連線層 fd 釋放（detach + slot 綁 task，2026-06-23 治本）

> **解決的 prod 故障**：斷線後 SSE handler 卡 `await self._lr_research_task` 不 return → TCP 連線 fd 不關 → 單 event loop（fd soft limit 1024）殭屍長連線累積 → Cloudflare edge 522（TCP handshake 超時）。「斷線不取消」（§7.3）保留背景 task，但連線層必須與 task 生命週期**解耦**。

- **Detach-aware await**（`methods/live_research.py` runQuery / continueResearch）：原 `await self._lr_research_task` 改成 `asyncio.wait({task, _lr_detach_event.wait()}, FIRST_COMPLETED)`。client 離線（`_lr_mark_client_disconnected` set `_lr_detach_event`，與 `connection_alive_event.clear()` **成對**）→ 提早 return route handler（`status="detached"`），**不 cancel** 背景 task。detach 路徑刻意**保留** `_lr_research_task` ref 非 None（route 掛 slot-release callback 的前提；C2 外層 finally `if not _detached` 才清 ref）。
- **Route fd 收尾**（`webserver/routes/api.py` start/continue handler）：成功 / detach / CancelledError 路徑 return SSE response 前 `await wrapper.finish_response()`（write_eof + heartbeat cancel）→ handler return → aiohttp transport teardown 釋放 fd。斷線時 `connection_alive=False` → guard 跳過 write_eof（transport 已死，送 EOF 無意義；fd 釋放靠 handler return 非 write_eof）。
- **路 A：slot release 綁 task 終態**（修 Gemini C1 同 session 並行雙寫）：並行 slot release 從 HTTP handler finally 移到背景 task 生命週期——detach 終態由 route closure done-callback release（捕獲 limiter 區域變數），非 detach 終態 route finally release，靠 `ConcurrencyLimiter.release()` idempotent 兜底（雙釋放點安全網）。**INVARIANT（I-A1）**：detach return → `add_done_callback` 之間禁任何 `await`（破了則 C1 並行雙寫復活、且 test 可能仍全綠）。效果：背景 task 未完成期間 slot 仍佔住 → 同 user 第二請求被擋 429，不會啟動第二個並行 task 競寫同 session row。`acquire` 仍在 route 層不動。
- **持久化單一責任**：route 層 trailing `_save_state` 已移除（detach 後與 task 內 `_persist_checkpoint_boundary → _persist_progress → _save_state` 雙寫、可能舊 snapshot 覆寫新）。持久化單一歸背景 task 內部，每 boundary idempotent 寫。

### 7.4 Final Report Rendering

收 `final_result` / export → 渲染到 `#liveResearchView` 並切 tab（reuse DR 的 `marked.parse` / `DOMPurify.sanitize` / citation link / collapsible section / reference list）。

#### 7.4.1 報告渲染：主路徑 vs fallback（契約）

| 路徑 | 條件 | 行為 |
|------|------|------|
| **主路徑**（新 session）| `lrState.final_report_markdown` 非空 | 直接渲染後端組好的整份字串：**零重組、逐字一致** |
| **fallback**（舊 session）| `final_report_markdown` 空 | 從 `written_sections` 前端重組 + 顯眼 banner「回顧重建版，可能與下載檔略有差異」|

KG 是 D3 圖、不在 markdown 字串裡，另走 KG 視覺重建。

**全 stage 回顧**：載入 completed session（`classifyLRResumeState` 回 `'completed'`）時**不重跑 pipeline**（restore read-only invariant），把既有 6 stage dot 改為 toggle、點到才 lazy render 該 stage 對話原貌。對話快照存獨立 top-level 欄位 `lr_dialog_snapshot`（**非** nested 在 `live_research_state`，避開後端 `_save_state` 整欄覆蓋 + 自由對話 `chat_history` restore loop 污染，見 §4.9）。

**後端配套**：Stage 6 在 emit `final_result` 前把 `state.final_report_markdown` 設為整份報告（**H1 = low-tier LLM 生成的報告標題 + 原始查詢 blockquote 副標** + sections + references + KG markdown），隨 checkpoint boundary 落 `live_research_state` JSONB，前端主路徑直讀，與 export 逐字一致。生標題失敗 / timeout / 空回應 → H1 降級退回 `research_question` 且 `logger.warning`（不 silent fail，plan: lr-report-title-generation）。純標題值另存 `generated_report_title` 欄位供前端 fallback / debug。

#### 7.4.2 Legacy session（schema_version < 2）唯讀 modal（契約）

DR-parity sprint 前的舊 session（`schema_version < 2`，§4.9.1）不可被新 orchestrator 繼續跑（後端 revise/continue API gate 回 **409 `legacy_schema_session`**），前端對應鎖定 + 唯讀 export：

- 偵測 `schema_version < 2` → 設 legacy 旗標（v2 session 不殘留此旗標）。
- 鎖 checkpoint reply 區：input disabled + placeholder「此 session 為舊版，已封存唯讀」；reply / auto 按鈕**不設 disabled**（disabled button 不 dispatch click → modal 開不出，死端修法），改 opacity/cursor/tooltip 視覺鎖定 + click → 唯讀 modal。
- 唯讀 modal CTA「此 session 已升級為唯讀」+ 三按鈕（匯出當前報告 / 用同 query 開新研究 / 取消）。
- 從 `written_sections`（filter 有內容章節）重組可下載 markdown，檔頭 note 揭露「由封存舊版 session 重建，未含新版完整參考清單」（直接 Blob 下載，**絕不 fallback 去點下載按鈕**，避免匯出殘留別的 session 報告）。

### 7.5 Citation Text-Fragment Highlight

點 citation link `[N]` 到原文時，用 URL **text-fragment**（`#:~:text=START,END`）讓瀏覽器自動 highlight 被引用的段落。前後端分工：

**後端 — `citation_sources`**：
- `_build_citation_sources` 產 `eid -> {url, title, domain, quote}`，於 per-section SSE + export SSE 兩處 emit。
- `_extract_quote` 對 quote **trim-only normalize**（不 collapse 空白），保留逐字原文供前端精確比對。
- **Source split（契約）**：只有 **internal source（站內新聞）** 帶逐字 `quote`；**web / wiki / llm_knowledge** 來源 `quote=""`（站外頁面 DOM 不可控、逐字命中率無保證，不嘗試 highlight）。

**前端 — text-fragment 組裝**（helper 在 `static/js/features/text-fragment.js`）：
- numeric `[N]`（eid 有 citation_source）→ `<a>` 帶 text-fragment href；urn / private / 無 source → `<span>`（不可點）。
- `buildTextFragmentUrl`：**START,END 雙錨點**（各取 `ANCHOR_LEN`=12 字），hash-safe，`-` 編碼為 `%2D`；quote 短於 `MIN_QUOTE` 或唯一性過低（純標點/數字/媒體名）→ **degrade → 裸 URL**（寧可無 highlight，也不強塞會錯標的 fragment）。
- 安全：`escapeHtmlAttr` 防 attribute-injection；外連 `rel="noopener noreferrer"`。

**前後端 mirror 契約**：前端 fallback 重組路徑有 `buildLRCitationSources`（後端 `_build_citation_sources` 的 client port），須對齊後端三點（source 欄位名 / 缺失預設 `"internal"` / `_extract_quote` = `snippet.strip()`）。**這條 mirror 易漂移——改任一端務必同步另一端**（曾漂移導致舊 session 回顧 internal highlight 失效；`test_lr_citation_sources.py` + URL 演算法的 Python/JS mirror test 守契約）。

**搜尋卡片同款**：search schema 帶 `description` + `matched_text`，4 個卡片 render path 全部接同一 text-fragment helper（一致性要求：不容許部分路徑接、部分不接）。

> 真實 highlight 效果（瀏覽器是否真標到段落）= 真機 E2E；text-fragment 字串正確性 = Python/JS mirror test 自動驗。

---

## 8. 測試 Contract

本章是 LR 測試 single source。

### 8.1 三層測試金字塔

| 層 | 工具 | 目的 | Cost | Release Gate? |
|----|------|------|------|--------------|
| **Unit** | pytest + fixture | 演算法 / schema / parser / typed action 正確性 | 0 | ✅ 必過 |
| **Fixture Replay** | `mock_bab=true` + 真 admin login + 真 PG + Stage 3-6 全跑（**Stage 1+2 BAB 被 fixture 跳過**）| 「給定凍結的 BAB 產物，Stage 3-6 pipeline（writer/critic/guard/組裝）工程品質對不對？」 | 低（省 BAB token） | ✅ 必過 |
| **Real Persona E2E** | `mock_bab=false` + 真實 retrieval + 真實 BAB + 真實 persona reply | Cayenne persona 全程真實 | 高（~$5） | ✅ release 前 ≥ 1 次 |

**mock_bab 契約**：`mock_bab=true` 時 **Stage 1+2 整段 BAB Loop 跳過**，直接載入 fixture ContextMap + evidence_pool；Stage 3-6 跑真實 LLM（real admin login + 真 PG write，不用 dev bypass、不繞 PG schema）。用法見 `mock-bab-playbook.md`。

> **mock_bab 的判別力盲區**：BAB 被整段跳過，故 mock_bab 對「Stage 2 web search / gap routing 接線是否生效」**零判別力**——那種驗收需真 BAB E2E（~$5）或 prod manual gate。**commit 前 `live_research_mock_bab` 必須 false。**

### 8.3 Release Gate 標準

| Gate | 標準 |
|------|------|
| **Commit gate** | Unit test 全 PASS + smoke test PASS |
| **PR merge gate** | + Fixture Replay E2E PASS (主要 persona) |
| **Release gate** | + Real Persona E2E PASS ≥ 1 次（最近 7 天內） |

**禁止**以 mock fixture E2E PASS 宣稱 release-ready。

### 8.4 Persona Fixtures

#### 8.4.1 Cayenne — 學術論文 5 章 7000 字 APA

Persona：台綜院研究員，七月專題「台灣綠能發展衝突，如何從國外案例借鏡」。

Fixture：`code/python/reasoning/live_research/fixtures/real_energy_policy_state.json`（+ `code/python/tests/fixtures/lr_mock_bab_real/`）——原規劃檔名 `cayenne_pre_focus_state.json` 未落地（repo 與 git 歷史均無此檔，2026-07-10 校正）

**user reply 序列**（fixture-mutation E2E）：
1. Stage 1: reframe 為「前言 / 國內案例 / 國外案例 / 結果與討論 / 結論」5 章
2. Stage 4: mixed payload「APA + 7000字 + 章節字數均勻 + 含表格」
3. Stage 5: 第 1 段 revise「補 2050 淨零脈絡」
4. Stage 5: 接續寫第 2-5 段
5. Stage 6: export

**Acceptance**：
- 5 章 cm.topics 真實對齊 user reply（P0-2/P0-3 防回歸）
- 4 個 format spec 全 ack（P1-A 防回歸）
- 引用全 APA `(Author, Year)` 格式（P1-B 防回歸）
- PG write user_id 是 UUID 不撞 schema（P0-1 防回歸）
- 第 1 段 revise 後真實含「2050 淨零」context

#### 8.4.2 後續 Persona Slot

預留 vendor 訪談 persona / B2B 客戶 persona。

每 persona fixture 必須含：(a) 研究領域 raw evidence pool (b) 完整 user reply 序列 (c) acceptance criteria 含至少 1 個歷史 P0 防回歸點。

### 8.5 Auth 測試紀律

- E2E 一律真實 admin login（帳密以當前環境 PG 實際值為準，見 §5.5）
- **禁止** `NLWEB_DEV_AUTH_BYPASS=true` 或任何 auth bypass（§5.4）
- 登入失敗即 stop + 報 CEO，不繼續走 anonymous path
- Token expire mid-test → authenticatedFetch refresh-then-retry 自動處理；refresh 也失敗 → frontend 跳「請重新登入」

### 8.6 E2E Agent Prompt Template

派 E2E agent 跑 LR test 時，prompt 必含：

```
帳號：<當前環境 PG 實際 admin 帳密，派工前確認>（見 §5.5）
登入路徑：HTTPS POST /api/auth/login 拿 JWT cookie，或前端 UI 真實 click
禁止：(1) NLWEB_DEV_AUTH_BYPASS 或任何 auth bypass
      (2) silent fail 容忍（任何降級必須 emit narration）
登入失敗處理：stop + LINE CEO，不繼續 anonymous
Mode：mock_bab=true (PR merge gate) / mock_bab=false (release gate, real persona)

Chrome MCP tab 紀律：
- 禁碰 CEO 工作 tab（id 由 CEO 提供）
- E2E agent 用新 tab，screenshot 存 `docs/e2e-screenshots/<test-id>/`
```

---

## 9. 未來規劃（未實現）

以下為設計方向，尚未實現：

- **Propose-Verify Pipeline**：LLM propose → 標 hypothesis → search 驗證 → 只有 confirmed 進 candidate list（與 Hallucination Guard + CoV 的 backward-looking 形成三層事實保護）。對應設計原則 #9，目前仍 reuse CoV。
- **Non-blocking UX**：`nonblocking_research=true` 啟用 + 前端 `setProcessingState` 解除 + 打字即 interrupt trigger。三層 cancellation 中 **mid-stream LLM abort**（LLM stream 中途斷、省 output token）未實作；soft interrupt（停派新 API call）+ hard HTTP abort（斷連線）已有。

> **LR Stage 5 stop 已移除**（非未來規劃）：Cooperative flag + per-section break 的停止按鈕機制 2026-06-04 整套移除（placebo，見 §4.7.3），`stage_5_stop_requested` 欄位 + `/api/live_research/stop` endpoint 已刪。LR 的「不取消、跑到 checkpoint 才停」由 §7.3 斷線恢復機制處理。

---

## 10. 已知限制 & Known Gaps

| # | 項目 | 嚴重度 | 說明 |
|---|------|-------|------|
| 1 | Propose-Verify 未實現 | 中 | 設計原則 #9 的 forward-looking 驗證未做，仍 reuse CoV（§9）。註：grounding guard（§6.9）+ publish gate（§6.8）已 land，是獨立 guard 機制，非此項。 |
| 2 | Stage 狀態不 persist on frontend | 低 | Stage accordion 純前端 DOM state，重載頁面回初始（backend `live_research_state` JSONB 仍 persist）|
| 3 | Non-blocking 未啟用 + mid-stream LLM abort 未實作 | 中 | 前端未準備「研究背景跑 + user 可繼續互動」；LLM stream 中途無法 cancel，只能 checkpoint 才 break（§9）|
| 4 | ~~recollect 缺專屬按鈕~~ → 已完成（2026-06-19 commit `9e910fb2`） | ~~中~~ | 前端按鈕 `lrBtnRecollect`「補充更多資料再寫」已 land（`news-search-prototype.html:723`，僅 Stage 5 顯示，送固定文字觸發既有 consent、後端零改）。|
| 5 | Cayenne 以外 persona fixture 未建 | 中 | §8.4.2 slot 留空 |
| 6 | Real Persona E2E 自動化未建 | 中 | real persona E2E 需手動跑，CI 整合未做 |

---

## 11. Changelog（里程碑）

| 日期 | 里程碑 |
|------|------|
| 2026-04 | LR Beta 初版 land：獨立 API routes + 6-stage loop + 前端 UI + BABLoopEngine |
| 2026-05-19 | Spec v0.大 大重寫：§3.4 兩層聚焦模型 / §4 UX State Machine Contract（七個 fix doc 整併）/ §5 Auth Contract / §8 測試 Contract |
| 2026-05-28~29 | **DR-parity sprint 全 7 Track land**：三層品質防禦（citation 白名單 / entity grounding / publish gate F1+F3 CoV）+ Citation / External APIs / KG / Temporal / Critic 擴充 / Frontend |
| 2026-06-11~12 | 接線批次：web search / gap routing default-on、evidence sufficiency 改全 pool 判、`lr_copy.py` + `sse_emit.py` |
| 2026-06-16 | Citation text-fragment highlight（§7.5）+ SSE 斷線不取消 read-only 重連（§7.3）|
| 2026-06-19 | cruft 體檢：移除 dead flag、WARN explanation 不截斷、citation 前後端 mirror 修正 |
| 2026-06-23 | **SSE 522 連線釋放治本（§7.3.1）**：detach-aware await（斷線釋 fd、task 不 cancel）+ route finish_response + 路 A slot 綁 task 終態（修 Gemini C1 並行雙寫）+ 移除冗餘 trailing save。已 deploy；行為層 2026-07-06 prod 驗收 PASS（slot 佔用生效、fd 29/conn 1 無洩漏），嚴格版（429 顯式 + fd 鋸齒 + 落 DB）待下次真機批 |

> 完整 commit 群與踩坑 lesson 見 `memory/lessons-live-research.md`。

---

## 12. Spec↔Code 待確認清單

> 不急的工程債。跟下次 LR 工程一起做，不需單獨開 sprint。

| # | 待確認項目 | 風險 | 備註 |
|---|-----------|------|------|
| 1 | **LR 六 stage 間 payload 審視** | Token 成本 + 品質稀釋 | 待驗證：六個 stage 之間傳遞的是蒸餾後的 structured findings 還是累積的原始 transcript？若有 stage 往下游傳全量原始內容，是 token 成本點 + 品質稀釋點（context 被 noise 填滿，downstream agent 推理品質下降）。**不急，跟下次 LR 工程一起做。** |

---

*最後 code re-sync：2026-07-10（`main`，Batch 2 docs review：§4.3.6 修法收帳 `8025a3f47`、§10#4 recollect 按鈕收帳 `9e910fb2`、§8.4.1 fixture 路徑校正、§2 原則出處校正）。先前 re-sync：2026-06-23（新增 §7.3.1 連線層 fd 釋放）。本文已從歷史/未實作設計脂肪瘦身為純契約文件；新人導覽見 `docs/reference/lr-onboarding.md`。*
