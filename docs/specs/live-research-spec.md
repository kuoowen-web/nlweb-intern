# Live 研究（Beta）技術規格

> **版本**：0.大 + DR-parity sprint 對齊（2026-06-15 code re-sync）
> **狀態**：Beta — Composable Pipeline + 6-Stage Dialog Loop + 前端 UI + DR-parity 三層防禦 land
> **最後更新**：2026-06-17
> **關聯文件**：
> - `docs/in progress/plans/major-upgrade-plan.md`（設計原則 + 架構 framework）
> - `docs/specs/reasoning-spec.md`（既有 M4 Reasoning 規格）
> - `docs/specs/login-spec.md`（Auth 系統規格）
> - `docs/in progress/plans/run-research-refactor-plan.md`（Composable Pipeline refactor）
> - `docs/specs/mock-bab-playbook.md`（mock_bab 自驗 playbook — 取代本 spec 舊 §8.2 mock_retrieval 設計）
>
> **2026-06-15 對齊註記**：本 spec body（§2-§10）原本停在 2026-05-19。2026-05-28~29 DR-parity
> sprint（7 Track）與 2026-06-11~12 的接線 / 硬化批次新增了多個子系統（grounding guard / publish
> gate / gap routing / web search 接線 / evidence sufficiency），原本只在 §11 Changelog 用一兩行帶過、
> body 未整併。本次 re-sync 補上 §6.6-§6.9 + §4.7.7 + §3.3 旗標表更新，並修正兩處 spec-vs-code 矛盾
> （`mock_retrieval` rename 從未實作、Propose-Verify flag 行為）。凡標「假說（待驗）」者為從 code 反推、
> 未實機 E2E 驗證。標「⚠ 待 Zoe 確認」者見 §12。
>
> **2026-06-17 對齊註記（spec drift 補修批次）**：補上 2026-06-04~16 land 的 9 處 code 變更——
> (1) `stage_5_stop_requested` 欄位與停止按鈕機制已移除（placebo），spec §4.7.3 / §4.9.1 同步；
> (2) §6.11 evidence sufficiency 改用「全 evidence_pool 有料量」判（非 `len(analyst_citations)`）；
> (3) §8.2.1 mock_bab cut point 內文校正為「Stage 1+2 BAB 整段跳過」；(4) Stage 5 LLM-done /
> export completeness gate（§4.7.1）；(5) Stage 5 退回 analyst 補搜迴圈（新增 §4.7.8）；
> (6) SEARCH_REQUIRED 二次補搜 + mini-reasoning REJECT→revise 迴圈（§4.3.2 / §6A）；
> (7) `bab_phase3` phase event + SSE 表 entry（§4.3.1 / §6.3）；(8) P-回顧全 stage 回顧 UI +
> legacy 唯讀 modal（§7.4）；(9) §3.3 兩個 NOT IMPLEMENTED flag 補標。code 行號為 2026-06-17
> 在 `main` 上的觀察值。

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
| Mode 參數 | `generate_mode=deep_research` | `generate_mode=deep_research` + `enable_live_research=true` |
| Clarification | 有（gate-style，彈出選項對話） | 取代為 Stage 1 checkpoint（dialogue-style，見下方 4/27 update） |
| Stage 模型 | 4 phase 自動串接 | 6-stage 對話 loop（per-stage checkpoint + user reply） |

**關鍵設計立場**：LR 後端共用 Composable Pipeline (4 phases + ResearchState)，但在其上加 **6-Stage 對話 loop**（Stage 1-6 各自有 checkpoint 等 user reply）。差異在前端互動深度。

> **4/27 update — Clarification 責任歸屬轉移**（commit `7e87fdb` + decisions.md D-2026-04-27）
>
> LR 不複製 DR 的 gate-style clarification。LR 是 dialogue-style：Associator 從任何查詢建出 ContextMap → **Stage 1 checkpoint 取代 clarification**。`code/python/methods/live_research.py:116` 預設設 `query_params["skip_clarification"] = "true"`。

### 1.2 設計對標

**研究員 persona A**（某智庫研究員）做七月專題「台灣綠能發展衝突，如何從國外案例借鏡」。Live 研究讓 研究員 persona A 在等待研究結果時看到「讀豹正在做什麼」——不是技術 log，而是自然語言敘述。

對標來源：`docs/in progress/plans/major-upgrade-plan.md` §0 Executive Summary + §5.4 讀豹 Mental Model。

### 1.3 與既有系統的關係

```
Live 研究 = Deep Research Pipeline (4 phase)
         + 6-Stage Dialog Loop (LiveResearchOrchestrator)
         + Phase SSE + LR SSE Events
         + Auth (真實 JWT path)
         + 前端 Live Research UI
```

---

## 2. 設計原則

10 個原則來自 `major-upgrade-plan.md` §4：

| # | 原則 | 一句話 | LR Beta 體現 |
|---|------|-------|--------------|
| 1 | **北極星** | 一切技術決定服從「能不能 convince 客戶」 | Stage accordion 透明化研究過程 |
| 2 | **Narrow first** | 先在一個領域做到極致 | Beta 先做最小可行 narration + stage tracking |
| 3 | **系統是放大器** | 人類專家做最終價值判斷 | 報告呈現方式不變，研究員仍做判斷 |
| 4 | **不知道就問 user** | Dialogue-Driven Research Loop | Stage 1 checkpoint 取代 gate-style clarification |
| 5 | **高良率要求** | 品質門檻高於一般搜尋 | 共用 Actor-Critic + CoV + Hallucination Guard |
| 6 | **Living document** | 報告能隨新 info 延伸 | ⚠️ Beta 未實現（KG editing + selective re-run） |
| 7 | **Minimize disruption** | 設計不打擾既有工作流 | Narration 在 chat 自然出現，不彈 popup |
| 8 | **Transparent reasoning** | 邊做邊揭露 reasoning chain | Phase SSE + chat narration 即時告知 |
| 9 | **Propose-Verify** | LLM knowledge 是 falsifiable hypothesis | ⚠️ Beta 未實現（仍 reuse CoV backward-looking）。**⚠ 矛盾待 Zoe 確認**：config `live_research_propose_verify: true` 預設 on（§3.3 / §12）—— flag 存在但實際生效程度與本欄「未實現」status 需釐清 |
| 10 | **Dialogue-First UI** | 所有能力走 chat agent 對話 | Narration 透過 chat message，不加新 widget |

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

**檔案**：`config/config_reasoning.yaml`（`reasoning.features` 段，2026-06-15 對齊 code 現況）

> **⚠ 修正（2026-06-15）**：spec 舊版 §3.3 / §8.2 宣稱 flag 已從 `live_research_mock_bab`
> rename 成 `live_research_mock_retrieval` 且 mock_bab「已廢棄」。**這在 code 裡從未發生。**
> code 與 config 實際使用的 flag 仍是 **`live_research_mock_bab`**（`methods/live_research.py:80`
> `_is_mock_bab()`；`config_reasoning.yaml:32`）。`live_research_mock_retrieval` 在 `code/python/`
> **零命中**——rename plan（`docs/in progress/plans/lr-auto-mock-retrieval-rename-plan.md`）仍在
> in-progress、從未實作。下表反映 code 現實；§8.2 同步修正。

下表為 LR 相關 flag 現況（非完整 features 清單）：

| Flag | 預設值 | 說明 |
|------|------|------|
| `composable_pipeline` | `true` | phase SSE 依賴；兩條路徑功能相同，flag 門控未來增強 |
| `nonblocking_research` | `false` | `true` + composable=true → asyncio task；前端未準備 |
| `live_research` | `true` | LR master switch — 開啟所有 LR 行為 |
| `live_research_mock_bab` | `false` | 測試模式：Stage 1+2 用 fixture ContextMap（省 BAB LLM 成本），Stage 3-6 跑真實 LLM。**commit 前必須 false**。見 §8.2 + `mock-bab-playbook.md` |
| `live_research_dry_run` | `false` | 用 mock agents 跑 pipeline，完全不呼叫 LLM |
| `live_research_critic_publish_gate` | `true` | Track F F1：per-section Critic publish gate（claim-level fabrication，見 §6.8）|
| `cov_lite_enabled` | `true` | F3 Chain-of-Verification（DR/LR 共用；LR 可用 `live_research_cov_lite_enabled` 子 flag 覆寫，見 §6.8）|
| `gap_knowledge_enrichment` | `true` | process-wide Analyst prompt builder flag（與 per-request `enable_gap_enrichment` 兩層 toggle，見 §6.7）|
| `live_research_consistency_monitor` | `true` | Critic Consistency Monitor（§4.3.2）|
| `live_research_style_analysis` | `true` | Stage 3 Style Analysis |
| `live_research_per_section_writing` | `true` | ⚠ **NOT IMPLEMENTED as config gate**（2026-06-15，無 .py consumer 讀此 key）。per-section 寫作（VP-7 checkpoint flow，§4.7.1）為 Stage 5 唯一/預設寫作流程，硬編碼啟用，不受此 flag on/off 影響；保留待未來如需真正 gate 時實作 |
| `live_research_max_bab_iterations` | `3` | BAB loop 最大迭代次數 |
| `live_research_narration` | `true` | ⚠ **NOT IMPLEMENTED as config gate**（2026-06-15，無 .py consumer 讀此 key）。narration 一律以 SSE `message_type` 字串 `"live_research_narration"` emit，不受此 flag on/off 影響；保留待未來如需真正 gate 時實作 |
| `live_research_propose_verify` | `true` | ⚠ **NOT IMPLEMENTED**（2026-06-15，無 .py consumer，on/off 不影響 runtime）。forward-looking Propose-Verify pipeline 未落地（仍 reuse CoV backward-looking，§9.3）；保留待未來實作。對應 §2 原則 #9「Beta 未實現」status |

**Per-request toggle（非 config flag，由前端 request body 帶）**：

| Param | 預設 | 提取位置 | 說明 |
|-------|------|---------|------|
| `enable_web_search` | `false` | `methods/deep_research.py`（LR 繼承）| LR Stage 2 per-topic BAB + gap routing 的 web search 開關。見 §6.6 |
| `enable_gap_enrichment` | `false` | `methods/live_research.py:48-49`（LR override）| gap routing 四類路由總開關。見 §6.7 |
| `enable_kg` | `false` | DR `__init__` | Knowledge Graph 生成（LR Track D）|

`max_results_lr: 8`（`config_reasoning.yaml:63`，CEO 決策③ 2026-06-08/09）：LR retrieval num_results 從 3 提到 8，補上游資料不足；DR 維持 `max_results: 5` 不動（split-key，互不 fallback）。

`gap_routing.max_external_calls_per_run: 6`（`config_reasoning.yaml:86`，C3 2026-06-11）：gap routing 單輪外部呼叫上限。

### 3.4 兩層聚焦模型（NEW）

LR 整個流程包含**兩個層級的聚焦**，分別由不同 stage 負責：

| 層級 | 完成標誌 | 對應 Stage | 性質 |
|------|----------|-----------|------|
| **資料蒐集面聚焦** | User 看到「重組後結構（N 章）」並同意 | Stage 1 BAB Loop | 從 abundant evidence 收斂出研究結構（cm.topics / chapters） |
| **文章面聚焦** | 每一章寫好、字數/引用/格式對齊 user_voice | Stage 5 Writer | 每章 prose composition + citation render + format compliance |

**研究本質**（CEO framing）：「先蒐集 abundant 資料 → 捨棄一部分 → 聚焦 → 補充新資料 → 再捨棄 → 再聚焦」迴圈。

- BAB Loop 內部 `B → A → B' → re-retrieve → B''` 就是這個迴圈的 in-stage 體現
- Stage 5 revise / Stage 4 reframe 觸發回到 Stage 1/2 重新聚焦時，可能再次補充資料（production）或從同一 pool 重新撈（testing，見 §8.2）

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

**Testing (`mock_retrieval=true`)**：fixture 提供「最後一次蒐集完、即將進入最終聚焦」的 state snapshot（evidence_pool + executed_searches + candidate ContextMap）。詳見 §8.2。

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

**Phase 3 mini-reasoning 進度事件（`bab_phase3`，commits `eccb5b1e` + `3a52a426`）**：mini-reasoning
是 BAB loop 最耗時的 LLM 段（Analyst high model + Critic + 可能 revise / 二次補搜 / gap routing），
為避免前端在最長窗口看到零進度，`run_loop` 在 `_run_mini_reasoning` 前後**對稱 emit** phase event +
narration（`loop_engine.py:255-262`）：
- 有 mini-reasoning input（`_has_mini_reasoning_input`）→ `_emit_phase("bab_phase3", "started")` +
  narration「正在深入分析這批資料、交叉檢驗論點...」；mini 成功（回 True）→ `_emit_phase("bab_phase3", "completed")`。
- early-skip 輪（檢索空手、gate False）**完全不 emit** phase3（不對 user 謊稱正在分析）；mini 失敗輪
  （降級回 False）**不 emit completed**（降級旁白即收尾，緊接 `bab_phase4 started` 標邊界）。
- 前端 `static/js/features/live-research.js` `phaseLabels` 收 `'bab_phase3': '深入分析與交叉檢驗'`（`:1927`）。
- SSE event 表見 §6.3。

#### 4.3.2 收斂條件 + Consistency Monitor

- **is_stable**：`refine_context_map` output 含 `is_stable=true` → break
- **Consistency Monitor**：`recommended_action="pause_confirm"` → set `paused_by_consistency=True` + break
- **Max iterations**：跑滿 `max_iterations=3` → 自然 exit

每輪結束 emit `bab_phase4 completed` 給前端 progress。

##### SEARCH_REQUIRED 二次補搜（DR-parity Task 2，commit `64550230`）

**檔案**：`loop_engine.py:1106-1193`（Phase 3 mini-reasoning 內）。Analyst 回 `status=SEARCH_REQUIRED`
+ 非空 `new_queries` 時 → 即時補搜站內 evidence → 重跑 Analyst 一次。

- **上限 1 輪**（與外層 BAB iteration 隔離，避免疊乘無上限補搜）。
- queries consumer 層硬限：strip → dedup → cap **3 條**（即使 Analyst prompt 要求 1-3，runtime 仍兜底防 LLM 吐超量 / 空 query）。
- 補搜走 BAB 既有 `_execute_search` path（不新建檢索器），side-effect 寫 `self.evidence_pool` → BAB 結束 serialize 進 `state.evidence_pool_json` → outline planner / writer 可引用（CEO 2026-06-12 拍板）。
- **邊界**：補的是 Analyst 頂層 `status=SEARCH_REQUIRED`（即時補救），與 gap_resolutions INTERNAL_SEARCH no-op（交給下一輪 Associator，§6.7）不同層、不重複。
- **失敗降級（不可 silent fail）**：補搜無結果 / re-run 後仍非 `DRAFT_READY` / draft 空 → forensic log + per-run 一次 user-facing 降級旁白 `lr_copy.SEARCH_REQUIRED_DEGRADED_NARRATION`，用原 analyst_output 續跑。
- 測試 `tests/unit/reasoning/test_loop_engine_search_required.py`。

##### mini-reasoning REJECT → revise 迴圈（DR-parity Task 1，commit `fae933c7`）

**檔案**：`loop_engine.py:1217-1288`（Phase 3 mini-reasoning 內，Critic pass 之後）。Critic 回
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
   - 4a 連接詞列舉：「前面 X，然後 Y，結尾 Z」（研究員 persona A R1）
   - 4b 頓號列舉 ≥ 3 章節名 + 收斂語：「A、B、C 這 N 章 / 共 N 章」（研究員 persona A R3）
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

**Fix 方向**：adjust path 應 re-emit reframe checkpoint + 補充 narration「以下是新版結構，是否確認？」— 不 silent advance。

### 4.4 Stage 2 — Per-section BAB（章節 detail）

Stage 1 ContextMap 定案後，Stage 2 對每個 `relevance == "core"` topic 跑 per-section BAB Loop（focus_topic_ids 注入）。Engine `seed_evidence_pool` + `seed_counter` 從 Stage 1 累積過繼（跨 engine 共用 evidence_id space）。

完成後 emit checkpoint「章節 detail，需要調整嗎？」。

**Stage 2 誠實 Narration**（OQ 1 拍板，原 §4.12.4）：
- 繁中 user-friendly
- 不撒謊（「記下來」≠「已記錄」這種 unverified claim）
- 禁用字詞：「retrieval」「session」「state」「已記錄」
- 採用文案：「謝謝你的建議，我已經把它記下來，寫稿階段會盡量採用。」

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
- `target_chapter`: 章節名稱（與 `cm.topics[i].name` / `format_specs["chapters"][i]["name"]` 比對；空字串 = unspecified → 全章注入）
- `description`: user 自然語言描述

**Hard channel vs Soft channel 區分**：

| Channel | block 名稱 | 內容 | 語氣 |
|---------|-----------|------|------|
| Soft | `## 格式要求` | 字數、語氣、引用樣式偏好（free text） | 「以下是用戶偏好」（參考） |
| Hard | `## 必須包含的特殊格式 element` | 表格 / 列表 / 圖 / 程式碼塊（結構化 + filter） | 「**必須**」「沒輸出視為不合格」 |

`_write_section` per-chapter filter：match `target_chapter` 注入；空 → 全章；match 不到 → `logger.warning`（不 silent）。

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

**Stage 5 Completeness Gate（#11 Part B，D-2026-06-11 決策 4，commit `a7b161ca`）**：
`_stage5_remaining_count(state)`（`orchestrator.py:5483`）計算未寫完章節數，匯出意圖統一過此 gate ——
**未寫完不准進 Stage 6**（防匯出半成品）：

| 觸發路徑 | 未寫完（remaining>0）行為 | 全寫完行為 |
|---------|--------------------------|-----------|
| export keyword shortcut（整句＝匯出詞）| block，emit「報告還有 N 段沒寫完，要先寫完才能匯出。要繼續寫嗎？」+ 停 checkpoint（`:5592-5605`）| `complete_stage` → Stage 6 |
| **LLM-done action**（語意等價自然語句如「好了就這樣」走 LLM → `action="done"`）| **block**，emit `lr_copy.stage5_done_unfinished_gate_prompt(remaining)` 釐清 checkpoint（**不硬轉 continue，不違逆 user 結束意圖**）（`:5758-5781`）| `complete_stage` → Stage 6 |
| auto_continue / 空 msg | 繼續寫下一段（不匯出）| `complete_stage` |
| meta-intent ABORT / SKIP | completeness-aware 停原地問「繼續寫完 / 修改某段」| 給「接受 / 繼續編輯」二選一 |

> **背景**：整句「完成」走 export shortcut 已被 block，但語意等價的自然語句（「好了就這樣」）會走 LLM →
> `done` → 舊行為直接 `complete_stage`（Stage 6 publish gate 只是 warn-only）→ 匯出半成品 = #11「中途完全
> 不給匯出」的漏網路徑。LLM-done 分支補同款 gate 堵此洞。

**設計決策（D-D / D-E / D-F）**：
- ~~**D-D**：revise_section target_index 解析失敗 → fallback 最後完成段 K~~ →
  **已反轉（FIX-6，研究員 persona A #14，2026-05-29，`orchestrator.py:5806-5838`）**：target 不明（None）改 emit
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
    author: str = ""  # 缺 → render fallback source_domain
    year: str = ""    # 缺 → render fallback 'n.d.'
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

> **🔴 已移除（2026-06-04，commit `693ac21`）— User stop 路徑整套廢棄（placebo）**
>
> VP-7 single-step flow 之後，每段完成即停在 per-section checkpoint 等 user reply ——
> **per-section checkpoint 本身已是中斷點**，writer 在單段內無 inter-section break 機會，
> 「停止寫作」按鈕在單段執行期間（10-30 秒）按下也無從 break，等於 placebo。2026-06-04
> 整套移除：(a) `LiveResearchStageState.stage_5_stop_requested` 欄位刪除（親驗 `stage_state.py`
> 已無此欄位，`tests/unit/reasoning/test_stage_state.py` 註明「removed 2026-06-04 (placebo)」）；
> (b) `/api/live_research/stop` endpoint + `requestStop()` + `_reload_stop_flag` writer 內檢查全移除；
> (c) 前端停止按鈕移除。下方原雙路徑取消設計**保留作歷史記錄**，但 User stop 路徑已不存在。

**取消路徑現況（2026-06-17）**：只剩 **Disconnect / Cancel（preemptive）** 一條 ——
關 tab / 網路斷 → `AioHttpStreamingWrapper._mark_disconnected()` → `_on_lr_disconnect` callback。
但注意：2026-06-16（commit `a2a9c143`）「斷線不取消」改版後，斷線 server **不再 `.cancel()` task**，
而是把當前 stage 跑到下個 checkpoint 才停存檔（離線跨 checkpoint 計數 + 燒錢上限進 DB state，§7.3 / §4.9）。

> **以下為已移除的雙路徑設計（2026-06-04 前，保留可追溯）**：
>
> | 路徑 | 觸發 | 機制 |
> |------|------|------|
> | ~~**User stop**（cooperative）~~ | ~~按「停止寫作」~~ | ~~POST `/api/live_research/stop` 寫 `state.stage_5_stop_requested=True` → writer loop 每段開頭 `_reload_stop_flag` 看到 break~~（已移除 2026-06-04，placebo） |
> | **Disconnect / Cancel**（preemptive） | 關 tab / 網路斷 | `AioHttpStreamingWrapper._mark_disconnected()` → `_on_lr_disconnect` callback（2026-06-16 後不 `.cancel()`，見上方） |

兩路徑共用 state 持久化：每段成功 `state.written_sections.append(...)` 同步 `last_completed_section_index = i` 並 `_save_state`。

**State Schema（現況，2026-06-17）**：
```python
# stage_5_stop_requested: bool  ← 已移除 2026-06-04（placebo）
stage_5_writer_running: bool = False
last_completed_section_index: int = -1
```

**~~Stop 後行為 (D-5)~~**（隨 User stop 路徑移除而失效）：~~User stop 不直接結束，重 emit checkpoint 三選一~~。
`_parse_revision_intent` enum 的 `continue_writing` action（trigger keywords：繼續/寫完/剩下/continue/往下寫）
**仍存在**，但觸發來源改為 per-section checkpoint 的 user reply（§4.7.1），非 stop 後重 emit。

**dry_run / mock_bab 特殊處理**：fixture writer 立即回沒有 await 點 → CancelledError 來不及 raise。`_run_stage_5` 每段開頭加 `await asyncio.sleep(0.05)` yield point。

**Error 紀律**：
- State load fail → log + return False（writer 繼續，不 silent 殺 loop）
- State save fail → log + `raise`（不 silent fail）
- CancelledError → try/except 必須 re-raise（否則 task wrap 收不到 cancel 完成訊號）

**~~UX-4 Stop Button 語意收窄~~**（VP-7 後曾將 label 改「中斷目前段落」；2026-06-04 整個按鈕移除，本段失效）。

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

#### 4.7.7 Per-section 寫作後守門管線（DR-parity sprint land，2026-06-15 補記）

**檔案**：`reasoning/live_research/orchestrator.py:_write_section`（~L4229-4900）。每寫完一個 section，
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
```

**接線參數（writer 現吃的新 kwargs，spec 舊版未列）**：`evidence_sufficiency`（per-chapter
critical/thin/ok，§6.6 module 5）、`time_constraint`（Track E temporal binding）、`knowledge_graph`
（Track D）、`prior_used_entities` / `all_prior_chapter_summaries`（cross-section 一致性，A/B writer 品質）、
`ungrounded_entities_revision`（rewrite path）。

**紀律**：所有 rewrite path（grounding rewrite / specificity rewrite）寫完後**必須重跑** citation +
fabrication guard——「寫具體」最易誘發編造，rewrite 不可反開防護洞（A/B writer 品質 lesson）。

#### 4.7.8 Stage 5 → Analyst 退回補搜迴圈（P-補搜，commit `385116d6`，2026-06-17 補記）

CEO 根解：Stage 5 寫到一半發現「資料不夠/太薄/要找更多來源」時，能**退回 analyst 重進完整
analyst→critic→writer→critic loop**，疊加新 evidence 重跑 BAB，而非只用現有資料重寫某段。

**觸發**：Stage 5 user reply → `_parse_revision_intent` 新增 `action="recollect"`（intent 契約：「資料
不夠/不足/太薄/去多查/找更多來源/補充蒐集」等要求蒐集**新資料**的訊號 → recollect；「第 N 段重寫/加強」用
現有資料 → revise_section，`orchestrator.py:6048-6066`）。

**四段式 confirm 路由**（`_handle_stage_5_response`，`pending_recollect_confirmation==True` 時，`:5507-5561`）——
補搜會清下游章節（不可逆），故走 informed-consent 兩段式（先 emit consent prompt 設旗標、下一輪 user 回覆才執行）：
1. **段 1**：含確認 token 的 bounded affirmative（「確認」「OK。」「好，開始吧」）→ 直接 `_dispatch_recollect`（不打 LLM）。
2. **段 2 abort（先於段 3）**：`_classify_meta_intent==ABORT`（「算了/取消/不要了」）→ 取消、不刪章節、回常規 checkpoint。abort 優先級最高（誤判代價最高）。
3. **段 3 無 token 短肯定兜底**：`_looks_like_bounded_affirmative_shape`（「好，那就重新蒐集吧」「是的」「行」）→ 確認執行（修 K Round 4「無 token 自然肯定句漏接 → 二次 consent loop」；含修改名詞 marker 者不走此兜底）。
4. **段 4 substantive**：其餘（「改第 3 段」「再多查經濟面」）→ **不吞**，fall through 到既有 `_parse_revision_intent` 正常路由（「不漏使用者任何一句話」鐵律）。

**Cap**：`_recollect_cap()` default **2**（`features["lr_recollect_cap"]` 可 override，`:3559`）。達上限 →
block + 明確告知 `lr_copy.RECOLLECT_CAPPED_NARRATION`（非 silent）。`recollect` action 進 consent 前預檢、
`_dispatch_recollect` 入口二次防護。

**`_dispatch_recollect`（`:3563`）執行序**：
1. cap 二次檢查 → 取研究問題 + 保留的 evidence_pool 當 seed（`seed_counter = max(pool.keys())`）。
2. snapshot 入口 state（`to_dict`，供 rollback）。
3. `recollect_count += 1` → `reset_for_recollect()`（清下游 + 退 Stage 1）。
4. **count+1 + reset 後、await 長跑 `_run_stage_1` 前先強制 `_persist_checkpoint_boundary`** —— 防雙擊/重送/SSE reconnect 並發兩 request 都過 cap → 雙倍燒錢（H，Gemini #4 + Codex #4）。
5. `_run_stage_1(state, query, [], seed_evidence_pool=seed_pool, seed_counter=seed_counter)` —— **seed 雙參數同傳**，engine 從 `counter+1` 起分配新 ID **疊加**既有 pool（B1，防 ID 衝突 / 防 `orchestrator.py:977` 空 pool 覆寫使疊加失效變清空重蒐）。
6. 失敗 → 用 snapshot rollback（還原章節 + count + 所有清掉的欄位）+ emit 明確 error checkpoint（不可 silent fail / 不留半重置 broken state，I，Codex #7）。

**`reset_for_recollect()`（`stage_state.py:501`）清/留窮舉**：
- **清**：current_stage→1、completed_sections / written_sections / book_outline_json / executed_searches、format_specs 的 `chapters`（rebind 新 dict 不 in-place pop，防污染 snapshot 淺引用，C-1）、所有 pending guard（reframe / format confirm / writer running / waiting）、`pending_recollect_confirmation`（G，Codex #6 —— 不清會讓下輪 reply 被誤攔）、推理產物（evidence_usage / knowledge_graph / critic_section_reviews / user_voice.revise_instructions）。
- **留**：evidence_pool_json（疊加非清空）/ context_map / initial_context_map / style / time_constraint / schema_version / offline_* / citation 設定 / `recollect_count`（cap 跨輪累積靠它）/ append-only 稽核 log（rejected_claims_log / consistency_drift_log）。

**State 持久化新欄位**（§4.9.1）：`recollect_count: int = 0` + `pending_recollect_confirmation: bool = False`
（to_dict/from_dict 對稱，舊 session fallback 0 / False，絕不被誤判 capped / 殘留 pending）。
**前端**：recollect 退回時 `clearLRStage5Artifacts` 清 Stage 5 section cards + chat 泡泡（資料仍在 DB，純 DOM 清除避免殘留誤導）。
**測試**：`tests/test_lr_stage_state_recollect.py`、`tests/test_lr_recollect_cap.py`、`tests/test_lr_recollect_dispatch.py`、`tests/test_lr_revision_intent_recollect.py`。

### 4.8 Stage 6 — Export

Final report 渲染 + 提供 download。User 在 Stage 5 final checkpoint 回「匯出」進入（**未寫完章節已被 §4.7.1
completeness gate 擋下，不會以 partial 進此 stage**；極端 partial case 仍能渲染已寫段落）。

完成 emit `final_result` event → 前端切 tab 顯示報告 + citation links + collapsible sections。

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
    # stage_5_stop_requested: bool  ← 已移除 2026-06-04（placebo，停止按鈕機制廢棄，見 §4.7.3）
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
| ~~state load fail in `_reload_stop_flag`~~ | ~~log + return False~~（`_reload_stop_flag` 隨停止按鈕移除 2026-06-04，已不存在，§4.7.3）|
| state save fail | log + `raise`（caller bubble up）|
| Retrieval fail | narration 明示「資料來源蒐集降級」 |
| **SEARCH_REQUIRED 二次補搜無結果 / re-run 仍非 DRAFT_READY**（§4.3.2）| forensic log + per-run 一次降級旁白 `lr_copy.SEARCH_REQUIRED_DEGRADED_NARRATION`，用原 analyst_output 續跑 |
| **mini-reasoning revise / re-review 失敗或 draft 空**（§4.3.2）| forensic log + per-run 一次降級旁白 `lr_copy.MINI_REASONING_REVISE_DEGRADED_NARRATION` + break，原 REJECT 入庫 forensic |
| **recollect `_run_stage_1` 失敗**（§4.7.8）| 用入口 snapshot rollback（章節 / count / 欄位全還原）+ emit 明確 error checkpoint，不留半重置 broken state |
| `_load_state` returns None | emit error narration + frontend 跳「重新開始」（§4.9.3） |
| CancelledError | try/except 必須 re-raise（否則 cancel 訊號鏈斷）|
| `_parse_stage_*_intent` LLM fail | retry / fallback intent + clarifying_question path (§4.3.5) |
| Catch Exception silent pass | **禁止** — 任何 catch 必 log warning 且不吞錯 |

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

Frontend 所有 LR API call 走 `authenticatedFetch`（commit `3c7a447` 將 LR continue + initial fetch 改走此 path）。401 回應觸發 refresh token retry，避免 idle 期間 access_token cookie 過期後 raw fetch 無 Bearer header → middleware 視為 unauthenticated。

對 SSE streaming 兼容：`authenticatedFetch` 不 await body，直接 return Response。

### 5.3 Token Expire Mid-LR

Cookie path `access_token` httpOnly 由 server 設置。Mid-LR token expire 場景：

1. User 進入 LR Stage 5 寫到一半，access_token TTL 到期
2. 瀏覽器自動不送 cookie（過期）
3. 下次 `/api/live_research/continue` request 無 Bearer / cookie → middleware 401
4. Frontend `authenticatedFetch` 攔截 401 → call `/api/auth/refresh` → 拿新 access_token → retry 原 request

**Token expire 而 refresh 也失敗**（refresh_token 也過期）：middleware 401 → frontend 跳「請重新登入」（不靜默繼續）。

### 5.4 ~~Dev Auth Bypass~~（已刪除）

**Spec 明示**：`NLWEB_DEV_AUTH_BYPASS` 不存在於 spec 設計範圍。

**歷史**：曾有 `webserver/middleware/auth.py:117-138` 的 dev bypass 分支，在 `NLWEB_DEV_AUTH_BYPASS=true` 時 set `request['user']={'id':'dev_user', authenticated=True / False}` 並放行。

**問題**：
- silent bypass 違反 no-silent-fail 紀律（v9 R5 narration 撞過）
- `'dev_user'` string id 撞 PG users.id UUID type → v15 P0-1 Server 500
- E2E agent 圖方便撞 bypass 而非真實登入，掩蓋 production-path bug

**處置**：完全刪除 bypass 分支。E2E 一律真實 admin login（§5.5）。Blocker B commit `539e8d3` 對稱 production user 結構（試圖修 bypass）也 revert。

### 5.5 真實 Admin 測試帳號

本地 PG `users` table 含（2026-05-19 驗證）：

```
email:          admin@twdubao.com
password:       test1234!     ← 注意：小寫 t，結尾 !
UUID:           ce024347-6e37-4b56-bd13-820b084d87bf
email_verified: True
is_active:      True
```

E2E agent 必須真實登入此帳號（POST `/api/auth/login` 拿 JWT），不可 bypass。

**Lesson（v9-v15 root cause）**：過去多個 handoff 文件寫錯密碼大小寫（`Test1234!` 大寫 T）→ E2E agent 登不上 → fallback dev bypass → 撞 v15 P0-1。正確密碼是 `test1234!`（小寫 t）。所有 handoff / E2E prompt 必須對齊。

### 5.6 PG `user_id` UUID Contract (v15 P0-1 lesson)

所有 PG operation 的 `user_id` column 是 UUID type（schema strict）。

**禁止**在 auth path 注入 string id（如 `'dev_user'`）— 寫 PG 時必撞 schema → 500。

**正確 path**：所有 user_id 必須來自 `request['user']['id']`，該值由 JWT payload `user_id` 解出，原始來源是 PG `users.id` (UUID)。

**Future-proof**：若需 placeholder user（如系統 task），必須在 `users` table 真實插入 UUID row（如 `00000000-0000-0000-0000-000000000001`），不可在 middleware 編造 string。

---

## 6. 後端規格

### 6.1 Composable Pipeline

#### 6.1.1 ResearchState Dataclass

**檔案**：`code/python/reasoning/research_state.py`

ResearchState 是 Composable Pipeline 的顯式狀態容器。每個 phase method 從 state 讀取輸入、將結果寫回。

**Schema 28 fields**：7 input + 1 phase 1 + 2 phase 1.5 + 7 phase 2 + 3 phase 3 + 1 phase 3.5 + 1 phase 4 + 4 infra + 2 error。詳見 `research_state.py`。

#### 6.1.2 四個 Phase Methods 的 I/O Contract

| Phase | Reads | Writes | Raises |
|-------|-------|--------|--------|
| 1. `_phase_filter_and_prepare` | `items`, `mode`, `tracer` | `current_context`, `formatted_context`, `source_map`, `early_return?` | `NoValidSourcesError` |
| 2. `_phase_actor_critic_loop` | filter output + state | `draft`, `review`, `iteration`, `seen_citation_ids`, `analyst_citations`, gap-merged context | `ResearchCancelledError` (×7 checkpoints) |
| 3. `_phase_writer` | actor-critic output | `final_report`, `plan`, `hallucination_corrected` | `ResearchCancelledError` (checkpoint 8) |
| 4. `_phase_format_result` | writer output | `chain_analysis`, `result` | n/a |

每個 phase 邊界 emit `research_phase: <name> / started|completed` SSE event。

#### 6.1.3 Feature Flag Routing

```python
async def run_research(self, ...):
    use_composable = CONFIG.reasoning_params.get("features", {}).get("composable_pipeline", False)
    if use_composable:
        return await self._run_research_composable(...)
    else:
        return await self._run_research_legacy(...)  # 實際也呼叫 composable
```

Tasks 0-4 refactor 是 zero behavior change。

### 6.2 Non-blocking Architecture

當 `composable_pipeline=true` 且 `nonblocking_research=true`：

```python
self._research_task = asyncio.create_task(orchestrator.run_research(...), name=...)
self._research_task.add_done_callback(self._on_research_complete)
```

HTTP connection 保持 open，但 task 可被 `cancel()` 從 disconnect handler 或 soft interrupt 中斷。

**soft_interrupt_event**：`asyncio.Event` 在 handler 初始化；orchestrator `_check_connection()` 每 checkpoint 檢查 → set 後下一個 checkpoint 拋 `ResearchCancelledError`。

**8 個 checkpoint** 分布於 Phase 2 迴圈（Analyst 前 / Gap 前後 / Tier 6 前 / Critic 前後 / 收斂前）+ Phase 3 開頭（Writer 前）。LLM call **中途**無法中斷。

### 6.3 Phase SSE Events

**4 個高層 phase boundary（Composable Pipeline）**：

| # | phase | status | 位置 |
|---|-------|--------|-----|
| 1-2 | `filter_and_prepare` | started / completed | `_phase_filter_and_prepare` 開頭 / 結尾 |
| 3-4 | `actor_critic_loop` | started / completed | `_phase_actor_critic_loop` 開頭 / 結尾 |
| 5-6 | `writer` | started / completed | `_phase_writer` 開頭 / 結尾 |
| 7-8 | `format_result` | started / completed | `_phase_format_result` 開頭 / 結尾 |

`_emit_phase_event(phase_name, status)` helper → `_send_progress({"message_type": "research_phase", ...})`.

**BAB Loop 內 fine-grained phase events（`loop_engine.py` `run_loop`，前端 `phaseLabels` 對應中文標籤）**：

| phase | status | 位置 | 前端標籤（`live-research.js:1924-1928`）|
|-------|--------|-----|--------------------------------------|
| `bab_phase0` | completed | `loop_engine.py:218`（build initial B 後）| 建立初始研究結構 |
| `bab_phase1` | started / completed | `:228` / `:233`（derive search plan）| 推導搜尋計畫 |
| `bab_phase2` | started / completed | `:236` / `:240`（execute retrieval）| 執行資料蒐集 |
| **`bab_phase3`** | **started / completed** | **`:255` / `:259`（mini-reasoning 前後對稱 emit；early-skip / 失敗輪不 emit completed，見 §4.3.1）** | **深入分析與交叉檢驗** |
| `bab_phase4` | started / completed | `:265` / `:276`（refine B→B'）| 本輪結構調整（completed 顯示「本輪更新完成」）|

`_emit_phase(phase, status)` helper（commits `eccb5b1e` + `3a52a426`）。

### 6.4 System Prompt（GAP）

⚠️ **最大 GAP**：LR Beta 完全 reuse DR prompts。讀豹 persona、Association 指引、Propose-Verify 紀律、Transparent reasoning、Stage awareness、Dialogue-Driven 指引均未實現。

未來 prompt 設計方向兩選一：(A) 改現有 prompts + flag 切換 LR 段落（reuse 多 / 但 flag 組合爆炸） vs (B) 新建 LR 專用 prompts（乾淨 / 但維護兩套）。決定未做。

### 6.5 LLM Cost Optimization

**Level 分配**（commit `7e87fdb` 後）：

| 函式 | 任務性質 | Level |
|------|---------|-------|
| `AssociatorAgent.build_context_map()` | 設定研究方向（generative）| `high` (gpt-5.1) |
| `AssociatorAgent.derive_search_plan()` | 機械性提取 | `low` (gpt-4o-mini) |
| `AssociatorAgent.refine_context_map()` | 整合搜尋結果（generative）| `high` |
| `_run_style_analysis()` | extraction | `low` |
| `_parse_*_intent()` | 簡單分類 | `low` |
| `WriterAgent.compose_section()` | 最終使用者產出 | `high` |

**判準**：generative + 深度推理 + 最終使用者產出 → high；mechanical extraction + intent classification → low。

**不可妥協項**（必須 high）：`build_context_map` / `refine_context_map` / `compose_section`。

**成本對比**：4/27 前 ~$0.92 / query → 4/27 後 ~$0.67（-27%）。

---

## 6A. DR-Parity Sprint 子系統（2026-05-28~29 land，2026-06-15 補記）

> 本章補記 2026-05-28~29 DR-parity sprint（7 Track：A Grounding / B Citation / C External APIs /
> D Knowledge Graph / E Temporal / F Critic / G Frontend）+ 2026-06-11~12 接線批次的子系統。spec 舊版
> body 完全沒有這些；舊版只在 §11 Changelog 用一兩行帶過。檔案行號為 2026-06-15 在 `main` 上的觀察值，
> 後續重構可能漂移。Sprint commit 群與 lesson 詳見 `memory/lessons-live-research.md` 2026-05-29 段。

### 6.6 Web Search 接線（Track C C1 + F1 接線批次）

**Per-request toggle**：`enable_web_search`（預設 `false`）。LR `LiveResearchHandler` 繼承 DR 的
`enable_web_search` 提取。**接線兩條 request path**（兩條都要帶，否則 Stage 2 web search 實質關閉）：

| Request | 帶 flag 的位置 | 狀態 |
|---------|---------------|------|
| 初始 `/api/live_research` | `performLiveResearch` → POST body 帶 `enable_web_search=true`（default-on） | ✅ |
| `continueLiveResearch` `/api/live_research/continue` | ~~body 不帶+不持久化 → Stage 2 per-topic BAB 的 web search 在 prod 實質關閉~~ → **已完成（2026-06）：D1 前端 flag 接線補完，continue body 補帶兩 flag（commit `5af0fed1`..`1fdd9b3c`）** |

**接線深度（CEO 決策③ 2026-06-08/09）**：LR web search `num_results = max_results_lr = 8`（DR 維持 5，
split-key 不 fallback，`config_reasoning.yaml:63`）。

**真機驗證**（status.md 2026-06-12）：prod 真機 network log 親驗 `live-research.js?v=20260611a` loaded
source 含 `enable_web_search×3`，import specifier cache-bust 真機穿透。撈到德/日/智利國際 evidence（F1
web search 接線生效）。

**⚠ 假說（待驗）**：Stage 2 continue path 的 web search 真實生效（撈到站外 evidence）這條，目前 mock_bab
E2E **零判別力**（mock_bab 下 Stage 2 直接用 Stage 1 ContextMap、不進 BABLoopEngine，見 §8.2），真驗收
= 真 BAB E2E（~$5）或 prod manual gate。status.md 記為「🔴 待 CEO prod 真機 BAB re-gate」。

### 6.7 Gap Routing 四類（Track C C4）

**檔案**：`reasoning/live_research/loop_engine.py:_process_gap_resolutions_lr`（~L666）。port 自 DR
`orchestrator.py:_process_gap_resolutions`，但 LR **只 handle 4 類**（DR 的 stock / weather / company API
三類在 LR 明示砍，`log skip 不 raise`）。

| GapResolutionType | LR 行為 |
|-------------------|---------|
| `LLM_KNOWLEDGE` | `_add_llm_knowledge_evidence` 建 virtual doc 進 evidence_pool（標 source=llm_knowledge）|
| `WIKIPEDIA` | `_execute_wikipedia_searches_lr`（外部呼叫，計入 cap）|
| `WEB_SEARCH` | `enable_web_search=true` 且 search_query 非空才打；`_execute_web_search` → `_normalize_item` → `_add_external_evidence(source="web")`（外部呼叫，計入 cap）|
| `INTERNAL_SEARCH` | no-op pass-through（已由 BAB main loop `_execute_search` 處理，交下一輪 Associator）|

**Toggle gate**：
- `enable_gap_enrichment=false`（per-request，預設 false）→ 整個 method early return，**所有 gap 跳過**
- `enable_web_search=false` → 只 `WEB_SEARCH` 類 log skip，其餘三類仍跑
- 兩層 toggle：per-request `enable_gap_enrichment` + process-wide `gap_knowledge_enrichment`（Analyst prompt builder flag）各司其職

**Per-run 外部呼叫 cap**（C3 2026-06-11）：`gap_routing.max_external_calls_per_run`（預設 `6`）。WIKIPEDIA +
WEB_SEARCH 真打外部前計數，達上限後跳過並 emit 一次 user-facing 旁白（`_narrate_gap_cap_once`，per-run
dedup 防轟炸）。被 gate / 空 query / cap 跳過的 gap 不消耗額度。

**接線狀態**：~~`enable_gap_enrichment` 前端 static/ 0 命中 → 後端 4 類 gap routing prod 永不執行~~ →
**已完成（2026-06）：D1 接線補完，gap_enrichment default-on + continue body 帶 flag**（status.md 2026-06-12）。

### 6.8 Publish Gate（Track F：F1 critic + F3 CoV-lite）

**檔案**：`reasoning/live_research/orchestrator.py:_run_publish_gate`（~L3975）。三層防禦的**第三層**
（L1 = BAB Critic verdict / L2 = per-section entity guard §6.9 / L3 = 本 publish gate）。

**Config flag**：`live_research_critic_publish_gate`（F1，預設 `true`）+ `cov_lite_enabled`（F3，預設 `true`，
LR 可用 `live_research_cov_lite_enabled` 子 flag 覆寫——**⚠ 此子 flag 未落 config 鍵，fallback 到
`cov_lite_enabled`，fallback 行為正確**，status.md 2026-06-11）。

**流程**：
1. `status != "drafted"` → short-circuit pass-through（F-AMB-7）
2. `chapter_evidence_text` 空 → 短路：標「查無可審來源」+ 降 Low（不 silent PASS，不燒 high-tier call）
3. F1 critic call（`critic_agent.review_section_publish_gate`）→ `f1_review_initial`
4. F3 CoV-lite call（若 F1 verdict ≠ REJECT）→ `cov_summary`（F3 fail → degraded `verification_status="unverified"`，不 silent）
5. F3 auto-escalate：`contradicted_count > 0` → 升級 REJECT；`unverified_count >= 3` → WARN
6. 依 final verdict 一次性 mutate：**REJECT** → 整章替換查核失敗文（`lr_copy.critic_rejected_content`，列最多 5 處問題句）/ **WARN** → 降信心 + amber strip（methodology_note ⚠ 提示）/ **PASS** → 照常
7. 寫進 `state.critic_section_reviews`（含 `cov_verification_summary`）

**抓 6 類 claim-level fabrication**：numeric / temporal / causal / comparative / predictive / evaluative。

**Fail 紀律**（status.md 2026-06-12 E1 hardening）：~~F1/F3 最外層 except 吞錯 → section 未經 gate 原文
通過（fail-open）~~ → **已完成（2026-06）：E1 publish-gate 硬化，degrade-and-narrate，故障明確 log + 旁白**
（commit `2653dbee` 等）。

**F3 detection 量測**（status.md 2026-06-12）：`tools/verify_cov_lr.py` harness 首跑 8/9、0 誤殺、0 降級，
唯一 MISS = A-2 張冠李戴（跨實體數字移植）→ 路線 c（抽取階段標記實體歸屬）A2c 已 merge main 關閉缺口
（run3 全綠 10/10）。

### 6.9 Grounding Guard（Track A T5：per-section entity grounding）

**檔案**：`reasoning/live_research/hallucination_guard.py`（**spec 舊版完全沒提此檔**）。**這是 LR 自造的
（DR 根本沒有 entity grounding——DR 只做 citation-id 白名單）**，也是 prod 真機抓到的 over-block 兇手來源。

**`entity_grounding_check(section, chapter_evidence_text, ...)` 三段式**（CEO 方向「良好資料來源 → low model
判讀」）：
1. `_extract_entities_for_grounding`：LLM（low）只**列出** prose 中具體 entity（國家/城市/機構/風場/法規/人名/數字），不判 grounded。抽取 fail → 回 `[]`（fail-open 安全方向：抽不出 = 沒東西要查）+ 通知 caller 補旁白
2. `_deterministic_grounded_filter`：字面命中 evidence（NFKC + casefold + 去空白正規化）→ 直接視為 grounded（零成本捷徑，省 trivial LLM call）
3. `_semantic_grounding_check`：殘餘字面不命中者 → LLM（low）語意判定（同義/全名/改寫/上位詞涵蓋，例：evidence「台灣電力公司」支撐 prose「台電」）

**R1 fail-closed 鐵律**（`GroundingCheckUnavailable` exception）：**語意判定階段** LLM exception / 爆 low-model
context window / 回傳無法解析 → raise `GroundingCheckUnavailable`，**絕不回 `[]` 當全 grounded**（fail-open
會在 evidence 變多爆窗時悄悄放行所有幻覺）。caller（orchestrator）捕捉 → 走 DR 式退化路徑 (a)：保留正文 +
降 Low + methodology note 標「grounding 系統驗證失敗，本章未經完整查證」。（注意：**抽取階段** fail 仍 fail-open
回 `[]`，方向安全。）

**Over-block 修法（CEO 決策①②④，status.md 2026-06-05~12）**：
- ① grounding 判讀 tier = **low**（資料好誰都能判，`ModelConfig` 僅 low/high 無 medium）
- ② evidence 範圍 = **全 pool**（`render_grounding_evidence_view` 餵全 evidence_pool，非本章 subset；R2 內建 12000 字 budget cap + 4 級優先序：本章引用 > 有 claim > prior overlap > 其餘，防爆窗）
- ④ partial block = **(b) 刪未驗證句為主 + (a) DR 式退化**（保留全文 + 降 Low + methodology 標哪些 entity 未驗證），丟掉 (c) 整章替換

**`split_and_filter_ungrounded_sentences`（R3 句子分類）**：候選刪除句（含 ungrounded entity）若**任一**成立則
**不硬刪**（保留 + 回報 unsafe_count，caller 走退化 (a)）：(1) 同句含已驗證 entity；(2) 含 citation 標記
`[N]`；(3) 被上下文依賴連接詞綁定（但是/因此/然而…/代名詞指代）。只有「純未驗證句」（三條都不成立）才 regex
直接刪。**不引入 LLM 改寫**（CEO 否決，會引回模糊化）。

**`specificity_check`（A 對稱守門）**：與 grounding 反向——偵測「evidence 有具體資訊但 prose 全抽象」
（under-specification）。drafted body chapter + evidence 有具體資訊 + prose 抽不到任何具體 entity → flag
→ specificity auto-rewrite。intro/conclusion 章排除（`_is_intro_or_conclusion`）。重用 T7 entity 抽取結果，
零額外 LLM call。

**根因脈絡（status.md 2026-06-05）**：prod over-block keystone = evidence snippet 全空——source
`postgres_client._build_schema_json` 寫 `articleBody`、consumer `loop_engine._normalize_item` 讀
`description`，鍵名不一致 → 內部語料 evidence 失去內文 → grounding 只在標題上空轉 → 合法核心 entity 被
over-block。修法 C1（`get('description') or get('articleBody','')` fallback，commit `f172d9b`）已上 prod。

### 6.10 其他 Track 子系統（簡記，待 §body 詳化）

| Track | 子系統 | 檔案 / 狀態 |
|-------|--------|-----------|
| B Citation | critic verdict 回流（`GroundedClaim` → render filter / WARN 降信心 / writer findings）；per-section publish gate 層**無** DR 式 revise loop（注意：BAB **mini-reasoning** 層另有 REJECT→revise 迴圈，見 §4.3.2 DR-parity Task 1）| `_run_publish_gate` + critic |
| C External | source enum + Tier 6 writer + `gap_resolutions_lr`（§6.7）| loop_engine |
| D KG | BAB build → Stage 6 merge → 前端 render（writer 仍看不到）；`createKGInstance(prefix)` per-instance closure（DR/LR 隔離，2026-05-29 `_kgPrefix` hazard 修法）| `static/knowledge-graph.js` |
| E Temporal | `TimeRange` + Stage 1 intent + `datePublished` filter + evidence_pool `published_at` + writer BINDING block；`state.time_constraint` plumb 到 F1 + writer | Track E land |
| F Critic | F1 publish gate（§6.8）+ F2 Consistency Monitor 持久化 + F3 CoV port | critic + orchestrator |
| 接線批次 | `lr_copy.py`（user-facing 文案單一事實源，AST jargon guard）/ `sse_emit.py`（SSE emit helper）| 2026-06-10~11 |

> **⚠ 待 Zoe 確認（§12）**：上表多為 status.md / changelog 反推，body 尚未逐項詳化。是否要在這次範圍內把
> 每個 Track 展開成完整章節，需 Zoe 拍板（見 §12）。

### 6.11 Evidence Sufficiency（module 5，2026-06-11）

**檔案**：`reasoning/live_research/orchestrator.py:_compute_chapter_sufficiency`（`:596`，module-level
函式）→ `_write_section` 注入鏈 → `writer.compose_section` → `build_section_compose_prompt`。

> **🔴 校正（2026-06-17）**：spec 舊版寫「per-chapter 用 `len(analyst_citations)` 算充分度」——
> 這在「全 pool 轉向」（P2 W9 / SF1）之後**已不成立**。全局 evidence 模型下 writer 讀**全
> evidence_pool**，`analyst_citations` 空 ≠ 沒 evidence。

per-chapter 用**全 evidence_pool 有料量**判充分度（`_compute_chapter_sufficiency(analyst_citations, evidence_pool)`，
親驗 `:603-608`）：
- **`critical`** — pool 完全空（`len(evidence_pool)==0`）
- **`thin`** — pool 量 `<= EVIDENCE_THIN_CHAPTER_CITATIONS`（`:593` 常數 = 2，即 ≤2）
- **`ok`** — pool 量 > 2

（intro/conclusion 章的 `ok` 覆寫由 caller 在 `_is_intro_or_conclusion` 處理，保留既有。）

**條件式 writer calibration**：evidence 足章維持逼具體（`specificity_check` 照常）、薄弱章才放行保守措辭。
防打架靠互斥（specificity rewrite 只在 ok 章跑、calibration 保守只在 thin/critical 章）。

**⚠ 假說（待驗）**：`thin` 閾值 ≤2（`EVIDENCE_THIN_CHAPTER_CITATIONS`）為初值，待真實 BAB 分佈優化
（status.md 標「待 mock bab 真實分佈微調」）。

---

## 7. 前端規格

> **⚠ 路徑漂移（2026-06-15）**：本章多處引用 `static/news-search.js:<行號>`。LR 前端邏輯已**抽離成獨立
> 模組** `static/js/features/live-research.js`（prod 載入 `live-research.js?v=20260611a`，cache-bust
> 穿透真機驗證 status.md 2026-06-12）。本章行號為 2026-05-19 monolithic `news-search.js` 的位置，多已
> stale。**⚠ 待 Zoe 確認**：是否要在這次範圍把 §7 改成對齊 `static/js/features/live-research.js`
> 的新模組結構（需獨立 frontend 對照，見 §12）。本次先標 stale，未逐行重寫。

### 7.1 Mode Toggle

**HTML**：`static/news-search-prototype.html:408-413`

```html
<div class="mode-toggle-inline" id="modeToggleInline">
    <button class="mode-btn-inline active" data-mode="search">新聞搜尋</button>
    <button class="mode-btn-inline" data-mode="deep_research">進階搜尋</button>
    <button class="mode-btn-inline" data-mode="live_research">Live 研究<span class="mode-beta-badge">Beta</span></button>
    <button class="mode-btn-inline" data-mode="chat">自由對話</button>
</div>
```

點擊「Live 研究」→ `currentMode='live_research'` → placeholder 更新 → 送出進入 `performLiveResearch(query)` → `performDeepResearch(query, skipClarification=true)` → POST 帶 `enable_live_research=true`。

### 7.2 Tab + Stage Accordion

**HTML**：`news-search-prototype.html:558-636`

四個 `<details>` 對應 4 phases：
| Stage ID | data-phase | Display |
|----------|-----------|---------|
| `lrStageFilterAndPrepare` | `filter_and_prepare` | 階段 1：資料準備與篩選 |
| `lrStageActorCriticLoop` | `actor_critic_loop` | 階段 2：深度分析與查證 |
| `lrStageWriter` | `writer` | 階段 3：撰寫與查核 |
| `lrStageFormatResult` | `format_result` | 階段 4：結論與格式化 |

狀態 icon：⏳（等待）/ 🔄（進行中）/ ✅（完成）。

### 7.3 SSE Handler

**檔案**：`static/news-search.js:3339-3349`

```javascript
} else if (data.message_type === 'research_phase') {
    if (currentMode === 'live_research') {
        const narration = generateLiveResearchNarration(data.phase, data.status);
        if (narration) addChatMessage('assistant', narration);
        updateLiveResearchStage(data.phase, data.status, data);
    }
}
```

**Narration 8 句靜態文字**（`news-search.js:3545-3568`）：4 phases × 2 statuses。⚠️ 沒有動態數字（「找到 N 筆」），phase event payload 只有 phase + status。

**LR 對話事件**（`live_research_narration` / `live_research_checkpoint` / `live_research_writer_status`）由各 stage handler 處理 — `showLRCheckpoint` 顯示 reply UI，`addChatMessage` 插入 narration。

**斷線「不取消」恢復（2026-06-16，commit `a2a9c143`）**：SSE client 斷線時 server **不 `.cancel()` task**，繼續把當前 stage 跑到下個 checkpoint 才停存檔（離線跨 checkpoint 計數 + 燒錢上限進 DB state，達上限才停）。前端偵測斷線顯示 `showLRConnectionInterrupted`（非 error）；`online` / `visibilitychange` 醒來後 debounced **read-only 重連**（`_doLRReconnect` 只 GET state + render，**INVARIANT：絕不送 /continue**）；三狀態分流 render（in_progress / checkpoint / offline_capped）。state 欄位見 §4.9（`offline_since` / `offline_capped` / `offline_cap_reason` / `offline_checkpoint_advances`）。

### 7.4 Final Report Rendering

`displayLiveResearchFinalReport()` (`news-search.js:3644-3673`) reuse DR 函式：`marked.parse` / `DOMPurify.sanitize` / `addCitationLinks` / `addCollapsibleSections` / `generateCitationReferenceList`。

Render 到 `#liveResearchFinalReport`（在 `#liveResearchView` 內），收 `final_result` 自動切 tab。

#### 7.4.1 P-回顧模式（completed session 全 stage 回顧，commit `ab06f4a0`，2026-06-17 補記）

**檔案**：`static/js/features/live-research.js`。載入已完成 session（`classifyLRResumeState` 回
`'completed'`，`:1665-1678`）時**不重跑 pipeline**（維持 restore read-only invariant），從已存 state
重建全 stage 內容供 user 點選回顧：

- `markAllStagesCompleted()`（`:1565`）—— 全 stage dot 標 completed。
- `wireLRStageNavigation(lrState)`（`:1365`）—— 綁 stage dot click（每次覆寫 `_lrReviewState`）；點 5/6 → `showLRExportFromState`。
- `showLRExportFromState(lrState)`（`:1249`）—— **主路徑**：`lrState.final_report_markdown` 非空 → 直接 `showLRExport`（後端原字串，逐字一致，**零重組**）；**fallback**：欄位上線前的舊 session（`final_report_markdown` 空）→ 前端重組（可見差異）+ banner + KG 視覺重建（KG 是 D3 圖、非 markdown，不在字串裡，須另呼）。
- `appendLRCriticReviewEntry(lrState)` —— Track F critic review 折疊入口。
- emit narration「此 Live 研究已完成。點上方任一階段可回顧該階段研究內容；下方為完整報告。」

**後端配套**：Stage 6 `orchestrator._run_stage_6` 在 emit `final_result` 前 `state.final_report_markdown = full_report`
（含 H1 研究問題標題 + sections + references + KG markdown），隨 `_persist_checkpoint_boundary` 落
`live_research_state` JSONB（`stage_state.py:173` 欄位，前端回顧主路徑直讀，與 export 逐字一致）。

#### 7.4.2 Legacy session（schema_version < 2）唯讀 modal（commits `cccfdb8b` / `d853a1e1` / `fc490c23` / `52e95af1`）

DR-parity sprint 前的舊 session（`schema_version < 2`，§4.9.1 addendum C-3）不可被新 orchestrator 繼續跑
（後端 revise/continue API gate 回 409 `legacy_schema_session`），前端對應鎖定 + 唯讀 export：

- `setLRLegacyMode(isLegacy, query, state)`（`:493`）—— 偵測 `schema_version < 2` 時設旗標 + 只在 legacy 存 state（v2 不殘留）。
- `lockLRUIForLegacySession()`（`:509`）—— 鎖 checkpoint reply 區：input disabled + placeholder「此 session 為舊版，已封存唯讀」；reply / auto 按鈕**不設 disabled**（disabled button 不 dispatch click → modal 開不出，S5-7 第二層死端修法），改 opacity/cursor/tooltip 視覺鎖定 + click → `showLRReadonlyModal()`；reply bar 以「可見」呈現避免 dead-end。
- `showLRReadonlyModal()`（`:579`）—— modal CTA「此 session 已升級為唯讀」+ 三按鈕（匯出當前報告 / 用同 query 開新研究 / 取消）。
- `buildLegacyReportMarkdown(state)`（`:554`）—— 從 `state.written_sections`（filter 有內容章節）重組可下載 markdown，檔頭 note 揭露「由封存舊版 session 重建，未含新版完整參考清單」（直接 Blob 下載，**絕不 fallback 去點 `#lrBtnDownload`**，避免匯出殘留別的 session 報告）。

### 7.5 Citation Text-Fragment Highlight（2026-06-16 land，commits `f2643935`/`c236d8b9`/`7fc774d4`）

點 citation link `[N]` 到原文時，用 URL **text-fragment**（`#:~:text=START,END`）讓瀏覽器自動 highlight 被引用的段落。前後端分工：

**後端 — `citation_sources`（`f2643935`）**：
- `_build_citation_sources` 產 `eid -> {url, title, domain, quote}`，於 `_emit_section`（per-section SSE）+ export SSE 兩處 emit（兩 call site 都傳 local state）。
- `_extract_quote` 對 quote **trim-only normalize**（不 collapse 空白），保留逐字原文供前端做精確比對。
- **Source split（Decision 2'）**：只有 **internal source（站內新聞）** 帶逐字 `quote`；**web / wiki / llm_knowledge** 來源 `quote=""`（站外頁面 DOM 不可控、逐字命中率無保證，不嘗試 highlight）。

**前端 — text-fragment 組裝（`c236d8b9`，helper 於 `7fc774d4` 抽到 `static/js/features/text-fragment.js` 共用）**：
- `addLRCitationLinks`：numeric `[N]`（=eid 有 citation_source）→ `<a>` 帶 text-fragment href；urn / private / 無 source → `<span>`（不可點）。
- `buildTextFragmentUrl`：**START,END 雙錨點**（各取 `ANCHOR_LEN`=12 字），`new URL()` 組 hash-safe，`-` 編碼為 `%2D`；quote 短於 `MIN_QUOTE` 或唯一性低於 `LOW_UNIQUENESS` heuristic 時 **degrade → null**（寧可退回普通連結無 highlight，也不強塞會錯標的 fragment）。
- `buildCitationHref`：`data-textfrag` 三態（generated / unknown / not-generated）。
- 安全：`escapeHtmlAttr` 防 href/title attribute-injection（escape 引號）；外連 `rel="noopener noreferrer"`（防 reverse-tabnabbing + Referer 隱私）。
- **演算法契約鎖定**：Python mirror test 鎖 URL 演算法，JS 實作 node 驗 == mirror。

**搜尋卡片同款（④，`db177b1a`/`14b8a552`）**：search schema 帶 `description` + `matched_text`，4 個卡片 render path 全部接同一 text-fragment helper（一致性要求：不容許部分路徑接、部分不接）。`articleBody` 未動。

> 真實 highlight 效果（瀏覽器是否真標到段落）= CEO 真機 E2E；text-fragment 字串正確性 = Python/JS mirror test 自動驗。

---

## 8. 測試 Contract

本章是 LR 測試 single source — 取代舊 §4.5 mock_bab + 舊 §7。

> **🔴 修正（2026-06-15）— §8.1 / §8.2 的 `mock_retrieval` 設計從未實作**
>
> 本章（§8.1 工具欄、整個 §8.2）描述的 `live_research_mock_retrieval` flag + 「pre-focus state cut
> point」+「Fixture Replay 不跳過 BAB Loop」是 **2026-05-19 寫的目標設計（aspirational）**，
> rename plan（`docs/in progress/plans/lr-auto-mock-retrieval-rename-plan.md`）**至今未實作**。
>
> **code 現實（2026-06-15 main）**：
> - 實際 flag 仍是 **`live_research_mock_bab`**（`_is_mock_bab()`，`config_reasoning.yaml:32`），**未** rename。
> - 實際 cut point **與本章描述相反**：`mock_bab=true` 時 **Stage 1+2 用 fixture ContextMap、Stage 2
>   直接跳過 BABLoopEngine**（log `[LIVE RESEARCH] mock_bab: skipping Stage 2 BAB loops`）；Stage 3-6 跑
>   真實 LLM。即「mock 掉 BAB」而非「mock 掉 retrieval、BAB 真跑」。
> - 真實使用法見 **`docs/specs/mock-bab-playbook.md`**（凍結一次 prod BAB 真實產物當固定 fixture，反覆跑
>   Stage 3-6 pipeline）。fixture 檔 = `reasoning/live_research/fixtures/real_energy_policy_state.json`
>   + `tests/fixtures/lr_mock_bab_real/`（session 5767ae4a 36 筆真語料）。
> - **後果**：mock_bab 對 Stage 2 web search / gap routing 接線**零判別力**（BAB 被跳過），這些只能用真
>   BAB E2E（~$5）或 prod manual gate 驗（§6.6 假說）。
>
> 下方 §8.1 表格與 §8.2 內文保留作「目標設計參考」，但**勿照字面當 code 現況**。三層金字塔的**精神**
> （Unit → 低成本 replay → real persona）仍有效，只是 flag 名稱與 cut point 與 code 不符。

### 8.1 三層測試金字塔

> **⚠ 下表 `mock_retrieval` 欄請讀作 `mock_bab`（見上方修正 banner）。**

| 層 | 工具 | 目的 | Cost | Release Gate? |
|----|------|------|------|--------------|
| **Unit** | pytest + fixture | 演算法 / schema / parser / typed action 正確性 | 0 | ✅ 必過 |
| **Fixture Replay** | ~~`mock_retrieval=true`~~ → **`mock_bab=true`** + 真 admin login + 真 PG + Stage 3-6 全跑（**Stage 1+2 BAB 被 fixture 跳過**） | 「給定凍結的 BAB 產物，Stage 3-6 pipeline（writer/critic/guard/組裝）工程品質對不對？」 | 低（省 BAB token） | ✅ 必過 |
| **Real Persona E2E** | `mock_bab=false` + 真實 retrieval + 真實 BAB + 真實 persona reply | 研究員 persona A / 記者 persona B persona 全程真實 | 高（全程真實，~$5） | ✅ release 前 ≥ 1 次 |

**關鍵差異（修正版）**：
- Fixture Replay（`mock_bab`）**跳過** Stage 1+2 BAB Loop（用 fixture ContextMap）—— 與舊 §8.2「不跳過」描述相反
- Fixture Replay 真實登入 admin@twdubao.com，不用 dev bypass
- Fixture Replay 真實 PG write，不繞過 PG schema

### 8.2 ~~mock_retrieval Mode~~ → mock_bab Mode（見上方修正 banner）

> **⚠ 以下為 2026-05-19 目標設計（未實作）。code 現況見上方 🔴 banner + `mock-bab-playbook.md`。**

~~**取代**舊 §4.5 mock_bab。Flag rename `live_research_mock_bab` → `live_research_mock_retrieval`。~~
→ rename 未發生，code 仍用 `live_research_mock_bab`（2026-06-15）。

#### 8.2.1 Cut Point — ~~最後一次蒐集後、最後聚焦前~~（目標設計，未實作）

> **🔴 校正（2026-06-17）— code 現實的 cut point 與下方舊設計相反**
>
> code 現實（親驗 `orchestrator.py:1761` log `[LIVE RESEARCH] mock_bab: skipping Stage 2 BAB loops,
> using existing ContextMap`）：`mock_bab=true` 時 cut point 是 **「Stage 1+2 BAB **整段跳過**，直接
> 載入 fixture ContextMap」**，**不是**下方舊設計的「Phase 4 refine 前餵入、BAB 真跑」。即「mock 掉
> 整個 BAB」而非「mock 掉 retrieval、BAB 各 phase 真跑、只在 Phase 4 切入」。與 `mock-bab-playbook.md`
> 一致。下方 CEO framing + BAB Loop 切點圖為 2026-05-19 舊設計（aspirational，**未實作**），保留可追溯。

**code 現實 cut point（2026-06-17）**：
```
Stage 1+2 整段 BAB Loop（build initial B → 多輪 derive/retrieve/mini-reasoning/refine）  ← ★ 整段跳過 ★
  └ 直接載入 fixture ContextMap（real_energy_policy_state.json）+ fixture evidence_pool
Stage 3-6（style analysis / outline planner / writer / critic / guard / publish gate / 組裝 / PG write）  ← 全真實 LLM 跑
```

---

**↓↓↓ 以下為 2026-05-19 舊設計（未實作），勿照字面當 code 現況 ↓↓↓**

~~CEO framing：「假設我們已經通過了最後一次蒐集，抵達了最後一次聚焦的時候。自此的所有機制，都要測試。」~~

~~對應 BAB Loop（§4.3.1）位置（**舊設計，code 未走此切點**）~~：

```
Phase 0: build initial B                         ↓ 不測（fixture 提供 initial state）
Loop ×N:
  Phase 1: derive A                              ↓ 不測（fixture 提供累積 executed_searches）
  Phase 2: execute A (retrieval)                 ↓ 不測（fixture 提供 evidence_pool）
  Phase 3: mini-reasoning                        ↓ 不測（fixture 提供 ContextMap 累積）
  Phase 4: refine B → B'                         ← ★（舊設計）Cut point：fixture 餵入這裡 ★（未實作）
  Consistency check                              ← 真實跑
  emit「研究結構提案」checkpoint                 ← 真實跑
  → user reply → reframe / advance → ...        ← 全真實跑
```

~~**Cut point 之前**：fixture 替代（省 retrieval + 多輪 build/derive token）~~
~~**Cut point 之後**：全真實跑（含 final refine + consistency + Stage 2-6 + PG write + user dialog）~~

#### 8.2.2 Fixture Schema

`code/python/reasoning/live_research/fixtures/<persona>_pre_focus_state.json`：

```json
{
  "research_question": "...",
  "evidence_pool": [<EvidencePoolEntry × 22+>],
  "executed_searches": ["..."],
  "context_map_pre_focus": {
    "topics": [<candidate topics × N，待 final refine>],
    "relations": [<candidate relations>],
    "version": <N-1>,
    "revision_history": [<v0..vN-1>]
  },
  "initial_context_map_json": "<v0 snapshot>"
}
```

**設計理由**：
- `evidence_pool` 是「最後一次蒐集」的累積結果（abundant raw data）
- `context_map_pre_focus` 是「進入最終聚焦前」的中間 state — final refine 真實跑
- `initial_context_map_json` 用於 drift detection 真實跑

#### 8.2.3 `_execute_search` Substitution

CEO framing：「我們在 testing 階段，先假設沒有必要重新去蒐集新資料。」

實作：在 `mock_retrieval=true` 時 override `BABLoopEngine._execute_search`：

```python
async def mock_execute_search(seeds):
    # 對 fixture pool 做 in-memory match
    relevant = match_pool_by_seeds(self.evidence_pool, seeds, top_n=5)
    return format_fixture_results(relevant), build_source_map(relevant)
```

**Matching 演算法**（單純可預測，避免 fixture replay 不穩定）：

| 階段 | 做法 |
|------|------|
| 1. Tokenize seed.query | 中文字 unigram + bigram + 英文 token（lowercase） |
| 2. Score each pool entry | sum of token overlap with `(title + snippet)` ／ length-normalized |
| 3. Filter | score > 0 的進候選 |
| 4. Top-N | 依 score 降序取前 `top_n=5`（同 production `retriever_search` 的 num_results=5） |
| 5. 空結果 | return `("（fixture 中未找到相關結果）", {})` — 真實 path 也是這 string |

**選擇理由**：
- **不用 embedding score**：fixture mode 重點是「pipeline 接線」，不是「retrieval 品質」；embedding 引入 stochastic 元素，違反 fixture 可預測性
- **不用 pg_bigm 演算法**：那要連 PG，違反「不打 PG」紀律
- **不用純 keyword exact match**：太脆，seed query「2050 淨零」抓不到 snippet「淨零碳排 2050 目標」
- Token overlap 是 deterministic、可預測、足以區分相關 vs 無關 evidence

**Production fidelity loss**（要意識到）：fixture replay 抓不到 retrieval 演算法本身的 bug（pg_bigm threshold 設定錯 / vector embedding 漂移）— 這些只有 real persona E2E (§8.4) 能抓。

**行為**：
- 任何 retrieval call 永遠 hit 同一 fixture pool，不打 PG / 不打 Google
- LLM 的 search plan / refine / reframe 真實跑
- UX 第 10 步 revise「補 2050 淨零脈絡」回到 Stage 1/2 時也 hit 同 pool（不真實補新資料）

**Production fidelity 對比**：
| 行為 | mock_retrieval=true | false (production) |
|------|--------------------|--------------------|
| Stage 0 retrieval | fixture pool | 真實 pg_bigm + vector |
| BAB Phase 0 build | 真實 LLM | 真實 LLM |
| BAB Phase 1 derive | 真實 LLM | 真實 LLM |
| BAB Phase 2 retrieval | fixture pool | 真實 PG / Google |
| BAB Phase 3-4 mini-reasoning + refine | 真實 LLM | 真實 LLM |
| Consistency check | 真實 LLM | 真實 LLM |
| Stage 2-6 (含 reframe / writer / revise) | 真實 LLM | 真實 LLM |
| Auth path | 真實 JWT (admin login) | 真實 JWT |
| PG write | 真實 | 真實 |

#### 8.2.4 行為對照表

| 測試什麼 | Unit | Fixture Replay | Real Persona |
|---------|------|---------------|--------------|
| TypeAgent schema / parser | ✅ | ✅（順帶） | ✅（順帶） |
| BAB Loop convergence 邏輯 | ❌ | ✅ | ✅ |
| Final refine LLM 行為 | ❌ | ✅ | ✅ |
| Consistency Monitor | ❌ | ✅ | ✅ |
| Stage 1 reframe → cm.topics mutate → writer 接線 | ❌ | ✅ | ✅ |
| Stage 4 multi-element typed action | ❌ | ✅ | ✅ |
| Stage 5 per-section checkpoint flow | ❌ | ✅ | ✅ |
| Writer typed citations + APA | ❌ | ✅ | ✅ |
| PG schema (UUID, JSONB write) | ❌ | ✅ | ✅ |
| JWT path / authenticatedFetch refresh | ❌ | ✅ | ✅ |
| 真實 retrieval quality (pg_bigm 結果是否相關) | ❌ | ❌ | ✅ |
| 真實 LLM 從新 raw data 推導出對的研究結構 | ❌ | ❌ | ✅ |

#### 8.2.5 Commit 紀律

- **commit 前必須** `live_research_mock_retrieval=false` in `config/config_reasoning.yaml`
- Fixture Replay E2E **PASS ≠ Real Persona PASS** — 兩個獨立 gate（§8.3）
- Fixture 反映真實 persona 的研究領域（研究員 persona A fixture 內容必須是學術論文場景，不是「能源政策」這種泛例題）

### 8.3 Release Gate 標準

| Gate | 標準 |
|------|------|
| **Commit gate** | Unit test 全 PASS + smoke test PASS |
| **PR merge gate** | + Fixture Replay E2E PASS (主要 persona) |
| **Release gate** | + Real Persona E2E PASS ≥ 1 次（最近 7 天內） |

**禁止**以 mock fixture E2E PASS 宣稱 release-ready。

### 8.4 Persona Fixtures

#### 8.4.1 研究員 persona A — 學術論文 5 章 7000 字 APA

Persona：某智庫研究員，七月專題「台灣綠能發展衝突，如何從國外案例借鏡」。

Fixture：`fixtures/cayenne_pre_focus_state.json`

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

預留 記者 persona B（某媒體記者） / 其他 vendor 訪談 persona / B2B 客戶 persona。

每 persona fixture 必須含：(a) 研究領域 raw evidence pool (b) 完整 user reply 序列 (c) acceptance criteria 含至少 1 個歷史 P0 防回歸點。

### 8.5 Auth 測試紀律

- E2E 一律真實 admin login（`admin@twdubao.com / test1234!`）
- **禁止** `NLWEB_DEV_AUTH_BYPASS=true`（spec §5.4 明示刪除）
- 登入失敗即 stop + 報 CEO，不繼續走 anonymous path
- Token expire mid-test → authenticatedFetch refresh-then-retry 自動處理；refresh 也失敗 → frontend 跳「請重新登入」

### 8.6 E2E Agent Prompt Template

派 E2E agent 跑 LR test 時，prompt 必含：

```
帳號：admin@twdubao.com / test1234!
登入路徑：HTTPS POST /api/auth/login 拿 JWT cookie，或前端 UI 真實 click
禁止：(1) NLWEB_DEV_AUTH_BYPASS 或任何 auth bypass
      (2) 在 mock_retrieval=true 時跳過 BAB Loop（fixture 餵的是 pre-focus state，BAB 必須跑）
      (3) silent fail 容忍（任何降級必須 emit narration）
登入失敗處理：stop + LINE CEO，不繼續 anonymous

Chrome MCP tab 紀律：
- 禁碰 CEO 工作 tab（id 由 CEO 提供）
- E2E agent 用新 tab，screenshot 存 `docs/e2e-screenshots/<test-id>/`

Mode：
- mock_retrieval=true (PR merge gate)
- mock_retrieval=false (release gate, real persona)
```

---

## 9. 未來規劃

### 9.1 Association Layer（B→A→B' Loop）

Master B Scope：**Session-wide**（不是 step-local 或 hierarchical）。整個 session 共享一個 master B。

新增 `reasoning/association/`：`context_map.py` / `associator.py` / `loop_engine.py`（已實作於 `reasoning/live_research/`）。

### 9.2 Critic Extension（Consistency Monitor）

`review_consistency(diff)` method on `critic.py` → 輸出 `ConsistencyReview {drift_detected, drift_summary, narrative_transition, severity}`。

**讀豹對話轉折** output channel：不是 popup，是 chat 中自然的一句話：

> 「欸等等，我剛翻的那篇德國 2019 改革... 仔細看好像不是我以為的社區共有模式，是 utility-scale 的政策。我去換一篇。」

### 9.3 Propose-Verify Pipeline

LLM propose → 標 hypothesis → search 驗證 → 只有 confirmed 進 candidate list。與 Hallucination Guard（backward-looking）+ CoV（backward-looking）形成三層事實保護。⚠️ 未實現。

### 9.4 User Checkpoint between Phases

未來在 Composable Pipeline phase boundary 加 user checkpoint。Composable Pipeline refactor 已讓此 trivial。

### 9.5 Non-blocking UX

`nonblocking_research=true` 啟用條件 + 前端 `setProcessingState` 解除 + 打字即 interrupt trigger。

**三層 cancellation**：
| Layer | 能做 | 省錢 | 狀態 |
|-------|-----|------|------|
| Soft interrupt | Subagent 停派新 API call | 省未來 API call | ✅ |
| Mid-stream LLM abort | 當前 LLM stream 中途斷 | 省 output token | ⚠️ 未實作 |
| Hard HTTP abort | 當前 request 斷連線 | 錢扣 | ✅ |
| LR Stage 5 stop | Cooperative flag + per-section break | 省剩餘 writer LLM | ✅（§4.7.3） |

---

## 10. 已知限制 & Known Gaps

| # | 項目 | 嚴重度 | 說明 |
|---|------|-------|------|
| 1 | ⚠️ System prompt 未設計 | **最大 GAP** | LR Beta 大量 reuse DR prompts。讀豹 persona、Transparent reasoning、Stage awareness、Dialogue-Driven 均未實現。見 §6.4。**註（2026-06-15）**：Grounding guard（§6.9）與 publish gate（§6.8）**已實作並 land**，不再屬「未實現」——但屬獨立 guard 機制，非 system prompt 層。Propose-Verify 仍見 §9.3 + §12 矛盾項。 |
| 2 | ⚠️ Stage 狀態不 persist on frontend | 低 | Stage accordion 純前端 DOM state，重新載入頁面回初始狀態（backend `live_research_state` JSONB 仍 persist） |
| 3 | ⚠️ Non-blocking flag 未啟用 | 中 | 前端未準備好接受「研究背景跑 + 使用者可繼續互動」 |
| 4 | ⚠️ Phase 之間沒有 user checkpoint | 中 | 4 phases 自動串接（DR phase 層，不是 LR stage 層）；LR stage 層已有 checkpoint |
| 5 | ⚠️ Mid-stream LLM abort 未實作 | 低 | Stream 中途無法 cancel；只能 checkpoint 才 break |
| 6 | ⚠️ 研究員 persona A 以外 persona fixture 未建 | 中 | §8.4.2 slot 留空 |
| 7 | ⚠️ Real Persona E2E 自動化未建 | 中 | 目前 real persona E2E 需手動跑；CI 整合未做 |

---

## 11. Changelog

| 日期 | 事件 |
|------|------|
| 2026-04-10 | CEO + Zoe brainstorming session：產品升級願景、10 個設計原則、研究員 persona A persona 對標 |
| 2026-04-11 | Refactor plan + 開始 execution。讀豹 mental model（「邊翻書邊聊」）。Consistency Monitor = Critic 擴充決策 |
| 2026-04-12 | run_research() composable pipeline Tasks 0-5 完成 |
| 2026-04-13 | Composable Pipeline 完成 + LR Beta UI + E2E 5/5 PASS |
| 2026-04-15 | Clarification flow 修復 + i18n + LR 獨立 API routes |
| 2026-04-27 | BAB loop crash fix + UX 修復 |
| 2026-04-27 | Spec §1.1 Clarification 責任歸屬轉移（DR gate-style → Stage 1 checkpoint dialogue-style）（commit `7e87fdb`） |
| 2026-04-27 | Spec mock_bab fixture 測試模式（`live_research_mock_bab` flag，省 76% E2E 成本）（commit `7e87fdb`） |
| 2026-04-27 | Spec LLM Cost Optimization（intent parsers + style analysis 改 low，省 27%） |
| 2026-05-15 | UX-4 Stage 5 Writer Loop Cancellation（hybrid stop button + cooperative flag） |
| 2026-05-15 | UX-9 ContextMap reframe_structure mutation（第 8 個 op_type，Replace All semantics） |
| 2026-05-16 | Stage 1 Empty-ops Clarification Dialog（clarifying_question 欄位 + 三分支紀律） |
| 2026-05-16 | VP-7 Writer Per-Section Checkpoint Flow（for-loop → single-step，每段 emit checkpoint） |
| 2026-05-16 | Stage 4 special_elements 強制紀律（hard channel vs soft channel） |
| 2026-05-19 | user_voice container（4 fix: D/B/I-1/I-2 統一接線）+ Stage 2 誠實 narration |
| 2026-05-19 | TypeAgent refactor（Stage4Intent / Stage4Response / Writer typed citations，立刻 strict 無 dual mode） |
| 2026-05-19 | Blocker A/B/C fix（target_word_count budget / dev_user 對稱 production user / clarifying_question null coerce） |
| 2026-05-19 | **Spec v0.大 大重寫 (a)** — §3.4 加「兩層聚焦模型」：資料蒐集面聚焦 (Stage 1 BAB) vs 文章面聚焦 (Stage 5 Writer)，明示 v15 P0-3 的接線斷點屬於兩層之間 |
| 2026-05-19 | **Spec v0.大 大重寫 (b)** — §4.7-4.13 七個 fix doc 整併進新 §4 UX State Machine Contract，per-stage single source；散落紀律（SSE event types / stage 邊界 / user reply contract / persistence / failure 紀律）集中 |
| 2026-05-19 | **Spec v0.大 大重寫 (c)** — §4.5 mock_bab 廢棄 → §8.2 mock_retrieval：cut point 改成「最後一次蒐集後、最後聚焦前」；BAB Loop 真實跑；retrieval call hit fixture pool (token-overlap top-N 演算法)；fixture schema 改為「pre-focus state」 |
| 2026-05-19 | **Spec v0.大 大重寫 (d)** — 新 §5 Auth Contract：dev bypass 刪除（spec 明示不存在）；真實 admin login (admin@twdubao.com / test1234!) 強制；PG user_id UUID contract（v15 P0-1 lesson） |
| 2026-05-19 | **Spec v0.大 大重寫 (e)** — 新 §8 測試 Contract 取代舊 §4.5 + §7：三層測試金字塔 (Unit / Fixture Replay / Real Persona) + release gate 標準 + persona fixtures (研究員 persona A) + E2E agent prompt template |
| 2026-05-19 | **Spec v0.大 大重寫 (f)** — v15 研究員 persona A real persona E2E P0 lessons 嵌入 §4.3.6 (adjust path silent advance, P0-2) / §4.7.6 (reframe→writer 接線, P0-3) / §5.6 (PG UUID, P0-1) — 防回歸 |
| 2026-05-19 | **Sub-RCA finding** — admin 登入失敗根因：handoff 文件寫錯密碼大小寫 (`Test1234!` vs 正確 `test1234!`)；spec §5.5 補正並嵌入 lesson |
| 2026-05-28~29 | **LR DR-parity sprint 全 7 Track land** — A Grounding（三層防禦 L1 BAB Critic / L2 entity guard / L3 per-section publish gate，抓 6 類 claim-level fabrication）/ B Citation / C External APIs / D KG / E Temporal BINDING / F Critic 擴充 / G Frontend。詳見 `lessons-live-research.md` 2026-05-29 段 |
| 2026-05-29 | **Sprint adversarial 驗收**（opus 4.8）— C/D/F 補 independent review + L3 real-LLM detection harness（`tools/verify_l3_critic.py`，撈到並修 precision-inflation gap）。F latent NameError / D `_kgPrefix` HIGH hazard / F-AMB-6 誤導文件 修復 |
| 2026-05-29 | **研究員 persona A 17 題痛點全補修**（FIX-1 sprint / FIX-2 completeness gate / FIX-3 author-year / FIX-4 reframe 約束保留+per-chapter edit / FIX-5 confirm shortcut / FIX-6 未指明改段問清楚 / FIX-7 narration+consolidation / FIX-8 章節編號）。研究員 persona A-path replay 驗收 |
| 2026-05-29 | **Writer 品質 A/B** — A：grounding block 加正向強制具體化 + 對稱 `specificity_check` 守門（防空泛，與 fabrication guard 對稱）；B：synthesis 章注入所有前章摘要 + post-write 新-entity 兜底（防跨段冒新資訊）。⚠️ spec body（§writer/grounding/§8）尚未詳記新 guard 行為 — follow-up |
| 2026-06-15 | **Spec code re-sync（落後對齊）** — diagnose 為「(a) spec 存在但 body 嚴重落後於 sprint code」。補 §6.6（web search 接線）/ §6.7（gap routing 四類 + cap）/ §6.8（publish gate F1+F3）/ §6.9（grounding guard `hallucination_guard.py`，spec 舊版無此檔）/ §6.10-6.11（其他 Track 簡記 + evidence sufficiency）/ §4.7.7（per-section 守門管線）。修兩矛盾：(1) `mock_retrieval` rename 從未實作（§3.3 + §8.2 修正，code 仍用 `mock_bab`）；(2) Propose-Verify flag on vs §2 #9「未實現」status（標 §12 待 Zoe 確認）。§7 前端標路徑漂移 `news-search.js` → `static/js/features/live-research.js`。新增 §12 待 Zoe 確認清單。reasoning-spec.md 同步補 3 個新檔（hallucination_guard / lr_copy / sse_emit）|

---

## 12. 待 Zoe 確認清單（2026-06-15 re-sync 產出）

本次 code re-sync 拿不準、或超出「純對齊」範圍、需 Zoe 拍板的項目：

1. **Propose-Verify flag 矛盾**：config `live_research_propose_verify: true` 預設 on，但 §2 原則 #9 + §9.3
   寫「未實現（仍 reuse CoV backward-looking）」。flag 存在不等於 forward-looking propose-verify pipeline
   真生效。需 Zoe 確認：flag on 實際觸發了什麼（Analyst prompt 段落？）vs §9.3 描述的完整 pipeline 落差。
   spec 暫保留「未實現」status + 標矛盾，未自行改寫成「已實現」。

2. **§7 前端章節是否本次重寫**：LR 前端已從 `static/news-search.js`（monolithic）抽離成
   `static/js/features/live-research.js`。§7 全部行號（3339/3545/3644 等）stale。本次只標 stale banner，
   未逐行對齊新模組——需獨立 frontend 對照。Zoe 決定是否納入本次範圍。

3. **§6.10 其他 Track 是否展開成完整章節**：B Citation / D KG / E Temporal 目前是簡記表（從 status.md /
   changelog 反推），body 未詳化。是否要在這次把每個 Track 展開（需各自讀 code 驗證），或維持簡記指向
   lessons-live-research.md，待 Zoe 拍板。

4. **`InitialFormatSpec`（H3 initial format spec extraction）刻意未納入**：此功能在獨立未 merge branch
   `feature/lr-initial-format-spec`（status.md 2026-06-12），**`main` 上 code 零命中**。本次 spec **未**
   記載此機制（避免 spec 跑在 code 前——這正是 §8.2 `mock_retrieval` 踩過的坑）。merge 後需補 spec。

5. **§8.2 整段是否重寫 vs 保留為目標設計**：`mock_retrieval` cut-point 設計（pre-focus state / BAB 真跑）
   是 aspirational、未實作。本次用 banner + 刪除線標明，但**保留原內文**作未來設計參考。Zoe 決定要整段砍掉
   改寫成 `mock_bab` 現況，還是維持現在的「banner + 標註」雙軌。

6. **CHANGELOG 2026-05-29 那條的 follow-up 已部分還清**：原 changelog 自承「spec body 尚未詳記新 guard
   行為」。本次 §6.8/§6.9 已補。確認是否還有遺漏的 sprint 機制未進 body。

---

*更新：2026-06-15（DR-parity sprint + 接線批次 code re-sync；前一版 2026-05-19）*
