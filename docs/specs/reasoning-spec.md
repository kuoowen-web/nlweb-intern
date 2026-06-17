# M4 Reasoning Module 規格文件

> **最後更新**：2026-06-17（spec ↔ code drift re-sync）。本次補修 10 條：§1 來源分級改 YAML（移除虛構的 `SOURCE_KNOWLEDGE_BASE` / `UNKNOWN_SOURCE_CONFIG` Python 常數）、§2.A temporal 解析歸位至 parent handler、§1.C 移除已砍的 `hallucination_corrected`、§3/§3.B mode 殘留標廢、§9 `mode_configs` 無 consumer 註記、§4 補 subject_entity 歸屬實體機制與 `PARTIALLY_VERIFIED` 狀態、§2.B 補 articleBody fallback、§6 Free Conversation 改為 `/ask` query_param 觸發。

## 概述

M4 Reasoning Module 負責深度研究與推論，採用 Actor-Critic 架構進行多輪迭代，確保回答品質。

### 核心特性

- **Actor-Critic Loop**：Analyst（Actor）+ Critic 迭代改進（最多 3 輪），由 `_phase_actor_critic_loop()` 統一管理
- **多 Agent 協作**：Clarification、Analyst、Critic、Writer 四個專門 Agent
- **Composable Pipeline**：`run_research()` 為 dispatcher，路由至 4 個 phase method
- **來源分層過濾**：依 Tier 過濾，由使用者自選來源範圍（Strict/Discovery/Monitor 模式選擇已於 2026-04 移除）
- **幻覺防護**：Writer sources ⊆ Analyst sources（`_phase_writer()` 內部執行）
- **Free Conversation**：Deep Research 後續 Q&A
- **Phase 2 CoV**：Chain of Verification 事實查核
- **Tier 6 API**：外部知識增強（Stock、Weather、Wikipedia）
- **Non-blocking Architecture**：`asyncio.create_task` + callback + soft interrupt（需 `nonblocking_research=true`）
- **Live 研究（Beta）**：獨立模式，規格見 `docs/specs/live-research-spec.md`

### 檔案結構

```
code/python/reasoning/
├── orchestrator_base.py      # OrchestratorBase（DR / LR 共用 ~200 行 boilerplate）
├── orchestrator.py           # DeepResearchOrchestrator（composable dispatcher + 4 phase methods）
├── research_state.py         # ResearchState dataclass（phase 間資料 bus）
├── live_research/            # Live 研究子目錄（Beta，commit c97d648 起）
│   ├── orchestrator.py      # LiveResearchOrchestrator（6-stage 對話驅動控制器）
│   ├── loop_engine.py       # BABLoopEngine（B → A → B' 可複用迴圈引擎；gap routing 四類）
│   ├── stage_state.py       # LiveResearchStageState dataclass（跨 request 持久化）
│   ├── hallucination_guard.py  # ★ entity_grounding_check / specificity_check / 句子分類（DR-parity sprint Track A，2026-05-28）
│   ├── lr_copy.py           # ★ LR user-facing 文案單一事實源（2026-06-11，AST jargon guard）
│   ├── sse_emit.py          # ★ SSE emit helper（2026-06-10）
│   └── fixtures/
│       └── real_energy_policy_state.json   # mock_bab fixture（flag = live_research_mock_bab，未 rename）
├── agents/
│   ├── base.py              # Agent 基礎類別
│   ├── analyst.py           # 分析 Agent
│   ├── critic.py            # 審查 Agent + CoV
│   ├── clarification.py     # 澄清 Agent
│   ├── writer.py            # 撰寫 Agent
│   └── associator.py        # Associator Agent（Live 研究專用）
├── prompts/
│   ├── analyst.py           # Analyst prompts
│   ├── clarification.py     # Clarification prompts
│   ├── cov.py               # Chain of Verification prompts
│   └── writer.py            # Writer prompts
├── filters/
│   └── source_tier.py       # 來源分層過濾
└── schemas_enhanced.py      # Pydantic schemas

code/python/methods/
└── deep_research.py          # DeepResearchHandler（non-blocking wiring）
```

#### 繼承層級（commit `50d2841` 起）

```
OrchestratorBase（reasoning/orchestrator_base.py）
    ├── DeepResearchOrchestrator（reasoning/orchestrator.py）
    └── LiveResearchOrchestrator（reasoning/live_research/orchestrator.py）
```

`OrchestratorBase`（commit `50d2841`）抽取出 `DeepResearchOrchestrator` 與 `LiveResearchOrchestrator` 共享的 ~200 行 boilerplate，避免 LR 重寫，並讓 phase event 行為一致。

**OrchestratorBase 提供的 shared logic**：

| 方法 | 用途 |
|------|------|
| `_send_progress(message)` | SSE 進度訊息推送（含 `user_friendly_sse` flag 處理 + 斷線偵測） |
| `_emit_phase_event(phase, status)` | Phase boundary event（`message_type: "research_phase"`） |
| `_check_connection()` | Connection alive + soft interrupt 檢查（raise `ResearchCancelledError`） |
| `_setup_research_session(query_id, query, mode, items, ...)` | IterationLogger + ConsoleTracer 初始化 |
| `ProgressConfig` | 11 個 stage 的進度權重與 user-friendly 訊息表 |
| `ResearchCancelledError` | 統一的 cancellation exception |

子類別只需呼叫 `super().__init__(handler)` 後，補自己的 domain-specific attributes（如 DR 的 4 個 agent、LR 的 `AssociatorAgent` 與 `BABLoopEngine`）。

#### `reasoning/live_research/` 子目錄（commit `c97d648` 起）

新增子目錄存放 Live 研究專屬模組，與 Deep Research 切分清楚：

| 檔案 | 用途 | 起源 commit |
|------|------|------------|
| `orchestrator.py` | LiveResearchOrchestrator — 6-stage 對話驅動控制器 | `c97d648` |
| `loop_engine.py` | BABLoopEngine — B → A → B' 可複用迴圈引擎（Stage 1 全域、Stage 2 per-section 共用） | `7471619`, `fe4db4c` |
| `stage_state.py` | LiveResearchStageState dataclass — 跨 request 持久化（存於 `search_sessions.live_research_state` JSONB） | `6fd5965` |
| `hallucination_guard.py` | per-section grounding guard — `entity_grounding_check`（三段式）/ `specificity_check`（對稱守門）/ `split_and_filter_ungrounded_sentences`（句子分類）/ `GroundingCheckUnavailable`（R1 fail-closed）。詳見 `live-research-spec.md` §6.9 | DR-parity sprint 2026-05-28 |
| `lr_copy.py` | LR user-facing 文案單一事實源（章節攔阻替換文 / methodology note 模板 / LLM 失敗旁白），禁開發術語（test 掃描契約） | 2026-06-11 |
| `sse_emit.py` | LR SSE emit helper | 2026-06-10 |
| `fixtures/real_energy_policy_state.json` | `live_research_mock_bab` flag 啟用時的 fixture ContextMap，跳過 Stage 1+2 BAB | — |

**動機**：
- 避免 LR 重寫 200 行 boilerplate（透過 OrchestratorBase）
- BAB loop 是可複用的內部引擎（Stage 1 與 Stage 2 都用）
- Live 研究子目錄與 DR 主檔分離，方便獨立演進

完整 Live 研究規格見 `docs/specs/live-research-spec.md`。

---

## 1. 核心資料結構與常數 (Configuration)

### 來源知識庫 (Source Knowledge Base)

用於 Hard Filter（現為純 enrichment，2026-04 起不再做 mode-based hard filtering）與 Enrichment 階段。

> **2026-06-17 re-sync**：先前 spec 列的 `SOURCE_KNOWLEDGE_BASE` / `UNKNOWN_SOURCE_CONFIG` Python 常數**在 code 中不存在**（indexer 全 repo 僅本 spec + proposal 文件命中）。實際來源分級定義在 **`config/config_reasoning.yaml`** 的 `source_tiers:` 區塊（YAML），由 `SourceTierFilter`（`reasoning/filters/source_tier.py`）以 domain（`cna.com.tw` 等）為 key 查表。

來源分級由 `config/config_reasoning.yaml` 的 `source_tiers` 定義（以 site domain 為 key）：

```yaml
source_tiers:
  # Tier 1: Official
  "cna.com.tw":  {tier: 1, type: "official"}      # 中央社
  "moea.gov.tw": {tier: 1, type: "government"}    # 經濟部
  # Tier 2: Mainstream
  "udn.com":         {tier: 2, type: "news"}      # 聯合新聞網
  "news.ltn.com.tw": {tier: 2, type: "news"}      # 自由時報
  "chinatimes.com":  {tier: 2, type: "news"}      # 中時新聞網
  # Tier 3: Digital / Specialty
  "esg.businesstoday.com.tw": {tier: 3, type: "digital"}  # 今周刊 ESG
  "e-info.org.tw":            {tier: 3, type: "digital"}  # 環境資訊中心
```

**未知來源處理（無配置常數，硬編碼於 code）**：未在 `source_tiers` 中的 domain，由 `SourceTierFilter._get_tier_info()`（`reasoning/filters/source_tier.py:94-109`）指派 `tier=999`、`type="unknown"`，enrichment 前綴為 `[Tier Unknown | unknown]`（`_get_tier_prefix():164-181`）。並無「`default_tier: 4` / `include_with_warning`」這類配置；全部來源一律 pass-through enrich（無 hard filter）。

---

## 2. Python 邏輯模組 (Hard Logic)

### A. 時間與意圖解析（屬 parent handler 層，非 reasoning module）

> **2026-06-17 re-sync**：reasoning module **沒有** `HybridTimeParser` class（indexer 全 repo 僅本 spec + audit 文件命中）。時間解析在 **parent handler（NLWebHandler）層**完成；reasoning module 只透過 `methods/deep_research.py:_get_temporal_context()`（`:301-330`）**消費** parent 已備好的 `self.temporal_range`，打包成 dict（含 `start_date` / `end_date` / `is_temporal_query` / `user_selected` / `user_choice_label`）餵給 Analyst prompt 作為 **BINDING constraint**（見 `reasoning/prompts/analyst.py` BINDING 區塊）。reasoning 本身不重複實作三層解析。

下述三層解析邏輯為 **parent 層設計**（DR Stage 0 clarification step 觸發路徑見 `deep_research.py`），reasoning module 不重複實作：

- **Level 1 (Regex/Lib)**: 使用 dateparser 解析明確日期。
- **Level 2 (Keyword)**:
    - type="timeline": 關鍵字 ["歷史", "回顧"]
    - type="fuzzy": 關鍵字 ["最近", "近期", "最新"] -> 需標記為需要 LLM 介入或擴大搜尋。
- **Level 3 (Ambiguity Check)**: 若無法解析，回傳 None，觸發 Clarification Agent。

當 parent 偵測到時間模糊並由使用者選定範圍後，`temporal_range.user_selected=True`，`_get_temporal_context()` 會把該選擇打包為 BINDING constraint，限制 Analyst 不得引用範圍外來源。

### B. 過濾與增強 (Hard Filter & Enrich)

函數簽章：`hard_filter_and_enrich(results: List[Result]) -> List[Result]`

> **注意**：`mode` 參數已於 2026-04 移除（模式選擇廢除）。過濾邏輯現在以使用者自選來源為準。

1. **Lookup**: 根據 `r.source` 查表 `SOURCE_KNOWLEDGE_BASE`。
2. **Filter Logic**: 依使用者選擇的來源清單過濾（不再依 strict/discovery/monitor 模式）。
3. **Enrichment**:
    - 修改 `r.content`，在開頭注入標籤：`"[{tier}級來源 | {type}] {content}"`
    - Tier 3-5 來源注入警語標籤。

**Context 提取 snippet fallback（`reasoning/orchestrator.py`）**：擷取 item 內文 snippet 時一律以 `item.get("description") or item.get("articleBody", "")` 做 fallback，防站內語料 `description` 為空時 evidence 內文留白。此 fallback 套用於多處（`_format_context_shared` / budget 估算 / Critic reference sheet / deep-link 等，`orchestrator.py:154/169/188/196/269/276/1692`），commits `789b1ccd` / `3ff05eb9` / `4f4a2cef`。

### C. ResearchState Dataclass

**檔案**：`reasoning/research_state.py`

ResearchState 是 composable pipeline 的資料 bus，取代了原本散落在 `run_research()` 中的 `self.*` instance attributes 和 local variables。每個 phase method 讀取 state 並將結果寫回 state。

```python
state = ResearchState(query=query, mode=mode, items=items, ...)
state = await orchestrator._phase_filter_and_prepare(state)
state = await orchestrator._phase_actor_critic_loop(state)
state = await orchestrator._phase_writer(state)
state = await orchestrator._phase_format_result(state)
```

ResearchState 的完整欄位清單（25 個 fields）定義在 `reasoning/research_state.py`，此處列出各 phase 所屬欄位分組：

| 分組 | 說明 |
|------|------|
| Input（不可變） | `query`, `mode`, `items`, `temporal_context`, `enable_kg`, `enable_web_search`, `query_id` |
| Phase 1 output | `current_context`, `formatted_context`, `source_map` |
| Phase 2 output | `draft`, `review`, `response`, `iteration`, `reject_count`, `seen_citation_ids`, `analyst_citations` |
| Phase 3 output | `final_report`, `plan`（`hallucination_corrected` 已移除：2026-06 commit `4456a0f4` / Task 4 選項A，DR 側無 consumer、零行為變更；LR 側 `live_research/stage_state.py` 的同名欄位為**獨立 class**，仍在用，不受影響） |
| Phase 3.5 output | `chain_analysis` |
| Phase 4 output | `result` |
| Infrastructure | `iteration_logger`, `tracer`, `enable_isolation`, `max_iterations` |
| Error / Early Return | `error`, `early_return` |

### D. 主控流程 (DeepResearchOrchestrator)

#### Composable Pipeline Overview

`run_research()` 現在是 **dispatcher**，根據 `composable_pipeline` feature flag 路由至 composable 或 legacy 路徑（目前 legacy 也路由至 composable，見第 9 節 Feature Flags）。

```
run_research() [dispatcher]
    ↓
_run_research_composable()
    ↓
Phase 1: _phase_filter_and_prepare(state)
Phase 2: _phase_actor_critic_loop(state)
Phase 3: _phase_writer(state)
Phase 4: _phase_format_result(state)
    ↓
return state.result
```

#### Phase 1: `_phase_filter_and_prepare(state)`

**職責**：來源 Tier 過濾 + Citation Context 格式化

**Input**：`state.items`, `state.mode`, `state.tracer`

**Output**：`state.current_context`, `state.formatted_context`, `state.source_map`

**Early Return**：若過濾後無來源 → `state.early_return`（RSN-11：若 `source_map` 為空亦回傳 no-results）

**SSE Events**：`filter_and_prepare started` / `filter_and_prepare completed`

#### Phase 2: `_phase_actor_critic_loop(state)`

**職責**：Analyst + Critic 迭代迴圈，包含 Gap Detection、Gap Resolution（Tier 6）

**Input**：`state.formatted_context`, `state.source_map`, `state.current_context`, `state.query`, `state.mode`, `state.temporal_context`, `state.enable_kg`, `state.enable_web_search`, 其他

**Output**：`state.draft`, `state.review`, `state.response`, `state.iteration`, `state.reject_count`, `state.seen_citation_ids`, `state.analyst_citations`

**Cancellation Checkpoints**：共 7 個（loop start, before analyst.revise/research, before secondary searches, before gap resolutions, before analyst re-run with enriched data, before critic.review）

**SSE Events**：`actor_critic_loop started` / `actor_critic_loop completed`

#### Phase 3: `_phase_writer(state)`

**職責**：Writer 組合最終報告 + 幻覺防護

**Input**：`state.draft`, `state.review`, `state.response`, `state.analyst_citations`, `state.source_map`, `state.query`, `state.mode`

**Output**：`state.final_report`, `state.plan`, `state.hallucination_corrected`

**Cancellation Checkpoints**：1 個（checkpoint 8：writer phase 前）

**SSE Events**：`writer started` / `writer completed`

#### Phase 4: `_phase_format_result(state)`

**職責**：Session logging + Reasoning Chain Analysis + 格式化 NLWeb 結果

**Input**：`state.response`, `state.review`, `state.final_report`, `state.iteration`, `state.current_context`, `state.query`, `state.mode`, `state.tracer`, `state.iteration_logger`, `state.items`

**Output**：`state.chain_analysis`, `state.result`

**SSE Events**：`format_result started` / `format_result completed`

#### Actor-Critic Loop 偽代碼（Phase 2 內部）

```python
MAX_ITERATIONS = 3

while iteration < MAX_ITERATIONS:
    # 1. Analyst Phase
    if review and review.status == "REJECT":
        response = analyst_agent.revise(draft, review, current_context)
    else:
        response = analyst_agent.research(query, current_context, mode)

    # 2. Gap Detection
    if response.status == "SEARCH_REQUIRED":
        new_results = search_tool.search(response.new_queries)
        current_context += hard_filter(new_results)
        continue

    # 2.5 Gap Resolution via Tier 6 APIs
    if response.gap_resolutions:
        for gap in response.gap_resolutions:
            data = tier6_api.resolve(gap)
            current_context += data
        continue

    draft = response.draft

    # 3. Critic Phase (含 CoV)
    review = critic_agent.review(draft, query, mode)

    if review.status in ["PASS", "WARN"]:
        break

    iteration += 1

# Write results back to state
state.draft = draft
state.review = review
...
```

---

## 3. Agent System Prompts (Soft Logic)

> **mode 參數狀態（2026-06-17 re-sync）**：各 agent 簽章仍保留 `mode` 參數**作向後相容**，但 2026-04 起**無實際邏輯影響**——統一改用 discovery-based 規則。`critic.py:105` 與 `prompts/critic.py:249` docstring 均註「kept for signature compatibility, value ignored since 2026-04」；`prompts/critic.py:_build_mode_compliance_rules`（`:241-258`）docstring 註「strict/discovery/monitor modes have been removed (2026-04). All research now follows unified discovery-based rules.」`source_tier.py:filter_and_enrich` 的 `mode` 參數亦同（value ignored）。

### A. Analyst Agent

**角色**: 首席分析師 (Lead Analyst)
**檔案**: `reasoning/agents/analyst.py`
**Input**: Query, Context（`Search Mode` 參數保留作向後相容，2026-04 起 value ignored）
**Output**:

1. `SEARCH_REQUIRED` JSON (若資料不足)
2. `GAP_RESOLUTION` JSON (若需要外部 API，見 Tier 6 API)
3. Markdown Draft (若資料充足)

**核心邏輯 (Thinking Process)**:

1. **Source Compliance**:
    - 依使用者自選來源範圍進行分析（研究模式 Strict/Discovery/Monitor 已於 2026-04 移除）。
    - 社群來源（Tier 3-5）需標註「未經證實」警語。
2. **Reasoning**: 必須建立推論鏈 (Chain of Reasoning)，嚴禁幻覺。

**Revise Prompt (修改模式)**:
- 輸入包含：Original Draft, Critic Critique, Specific Suggestion。
- 指令：只針對 Critic 的批評進行修改，不要重寫整篇。

### B. Critic Agent

**角色**: 邏輯與品質審查員 (Logic & Quality Controller)
**檔案**: `reasoning/agents/critic.py`
**Input**: Draft, Query（`Mode` 參數保留作向後相容，2026-04 起 value ignored）
**Output**: JSON Only

```json
{
    "status": "PASS | WARN | REJECT",
    "evaluation": {
        "mode_compliance": "已廢棄（2026-04 mode 移除）：欄位仍存在於 structured critique schema（critic.py:244/265 仍 carry），但值不再具語意，勿據此判讀",
        "reasoning_flaws": ["邏輯漏洞1", "來源不合規"],
        "cov_result": {
            "verified_facts": ["事實1", "事實2"],
            "unverified_claims": ["待查核1"],
            "contradictions": []
        }
    },
    "critique": "給 Analyst 的具體批評",
    "suggestion": "具體修改建議 (可執行)"
}
```

**審查標準**:

> **注意**：模式專屬規則（Strict/Discovery/Monitor）已於 2026-04 移除。

- 引用社群來源（Tier 3-5）但未加警語 -> **WARN**。
- 引用來源與使用者選擇範圍不符 -> **REJECT**。

**未來擴充方向**：Consistency Monitor（跨對話一致性查核），見 `docs/specs/live-research-spec.md`。

### C. Clarification Agent

**角色**: 意圖澄清助手
**檔案**: `reasoning/agents/clarification.py`
**Trigger**: 當 TimeParser 失敗或 Query 過於模糊。
**Output**: JSON (提供 2-4 個選項讓用戶選，而非開放式問答)。

```json
{
    "needs_clarification": true,
    "questions": [
        {
            "question": "您想查詢哪個時間範圍的新聞？",
            "options": ["過去一週", "過去一個月", "過去一年", "不限時間"]
        }
    ]
}
```

### D. Writer Agent

**角色**: 報告編輯
**檔案**: `reasoning/agents/writer.py`
**Task**: 整合 Analyst 草稿與 Critic 意見 (如果是 WARN)，輸出最終 Markdown。由 `_phase_writer()` 呼叫。

**Templates** (定義於 `config/config_reasoning.yaml`):

> **注意**：模式專屬模板（Strict/Discovery/Monitor）已於 2026-04 廢除，改用統一模板。

- **Default**: "研究摘要", "主要發現", "來源分析", "結論"。

**幻覺防護**（在 `_phase_writer()` 內部執行）:
- Writer 只能使用 Analyst 已引用的來源
- 驗證：`writer_sources ⊆ analyst_sources`
- 若違反：自動修正（取交集），降低 confidence_level 為 "Low"，methodology_note 加入 `[自動修正：移除未驗證來源]`

---

## 4. Phase 2 CoV（Chain of Verification）

**檔案**: `reasoning/prompts/cov.py`, `reasoning/agents/critic.py`

### 概述

CoV 是整合於 Critic Agent 的事實查核機制，用於驗證 Analyst 輸出的事實準確性。由 `_phase_actor_critic_loop()` 在 Critic 呼叫時觸發。

### 流程

```
Analyst Draft → Critic (含 CoV)
                    ↓
            1. 提取關鍵事實宣稱
            2. 交叉比對來源
            3. 標記驗證狀態
                    ↓
            CoV Result → 影響 Review Status
```

### 驗證狀態

驗證狀態 enum 定義於 `reasoning/schemas_enhanced.py:VerificationStatus`（`:387-392`），共**四態**：

| 狀態 | 說明 | 影響 |
|------|------|------|
| `verified` | 來源明確支持此宣稱（數字/日期完全匹配或語意等價） | 無 |
| `unverified` | 來源中找不到支持證據（≥3 個 → WARN） | WARN |
| `contradicted` | 來源與宣稱矛盾（含歸屬實體不符，見下） | REJECT |
| `partially_verified` | 宣稱部分內容有來源支持（如「1987 年創立」有據、「首任董事長」未提及） | 比照 unverified（前端 verification banner 同列警示） |

### Prompt 結構

CoV 為**兩階段**（抽取 → 驗證），prompt 由 `reasoning/prompts/cov.py:CoVPromptBuilder` 建構：

| 階段 | 方法 | 作用 |
|------|------|------|
| 抽取 | `build_claim_extraction_prompt(draft)` | 從草稿抽取可驗證宣稱（number / date / person / organization / event / statistic / quote），輸出 `ClaimsList` JSON |
| 驗證 | `build_claim_verification_prompt(claims, formatted_context)` | 逐宣稱比對來源，輸出四態驗證結果 |
| 彙整 | `build_verification_summary_for_critic(...)` | 把驗證結果摘要附到 Critic review prompt |

#### 歸屬實體機制（subject_entity，防張冠李戴）

為防「數字找得到就判 verified」造成的張冠李戴誤判，CoV 在抽取與驗證兩階段攜帶 `subject_entity`（歸屬主詞實體）。相關 schema 欄位定義於 `schemas_enhanced.py:VerifiableClaim.subject_entity`（Optional[str]）。

- **抽取階段**（`build_claim_extraction_prompt`）：每個宣稱把數字/事實/行為歸屬給的**主詞實體**（公司/機構/人/地名）記入 `subject_entity`，原文照抄、不簡化；**無明確主詞 → null**。`subject_entity` 是輔助歸因欄位，**非**可驗證性必要條件——缺主詞不影響該 claim 照常提取與驗證。
- **跨斷層攜帶**：`critic.py:_extract_verifiable_claims` 將 `subject_entity` 放進 claims dict，使主詞穿越「抽取 → 驗證」斷層（`critic.py` 註「A-2: 攜帶主詞穿越抽取→驗證斷層」）。
- **驗證階段**（`build_claim_verification_prompt`）：以 `subject_entity` 為準在來源中找**該主詞**的對應陳述；若數字存在於來源但歸屬於**不同實體 B**（B≠宣稱主詞 A）→ 判 **CONTRADICTED**（不因數字找得到就 VERIFIED）。
- **別名容忍（防誤殺）**：同一實體的全名/簡稱/別名/子公司關係視為同一實體（如「台泥嘉謙綠能」vs「台泥」、「台積電」vs「TSMC」），**不**判 CONTRADICTED；只有指涉明確不同主體（如台鹽綠能 vs 台泥嘉謙綠能）才因歸屬不符判 CONTRADICTED。`subject_entity` 為 null 時不做歸屬比對。

> 相關 commits：`1d55e642` / `87f49a69` / `d56d12c8` / `bf6e034d`（A-2 張冠李戴結構修法，路線 c：抽取標記 + 驗證逐實體比對）。

---

## 5. Tier 6 API 整合（Knowledge Enrichment）

**實作位置**: `reasoning/orchestrator.py`（`_phase_actor_critic_loop()` 內的 Gap Resolution 邏輯）

### 概述

當 Analyst 偵測到資料缺口（Gap）時，可透過 Tier 6 API 取得外部知識補充。

### 可用 API

| API ID | 名稱 | 用途 | 檔案 |
|--------|------|------|------|
| `llm_knowledge` | LLM 內建知識 | 一般知識問答 | - |
| `google` | Google Custom Search | Web 搜尋 | `retrieval_providers/google_search_client.py` |
| `yfinance` | Yahoo Finance | 股票資訊 | `retrieval_providers/yfinance_client.py` |
| `twse` | 台灣證交所 | 台股資訊 | `retrieval_providers/twse_client.py` |
| `wikipedia` | Wikipedia | 百科知識 | `retrieval_providers/wikipedia_client.py` |
| `wikidata` | Wikidata | 結構化知識 | `retrieval_providers/wikidata_client.py` |
| `cwb_weather` | 中央氣象局 | 台灣天氣 | `retrieval_providers/cwb_weather_client.py` |
| `openweathermap` | OpenWeatherMap | 全球天氣 | `retrieval_providers/global_weather_client.py` |

### Gap Resolution 流程

```python
# Analyst 回傳 Gap Resolution 請求
{
    "status": "GAP_RESOLUTION_NEEDED",
    "gap_resolutions": [
        {"api": "stock_tw", "query": "2330.TW"},
        {"api": "wikipedia", "query": "台積電"}
    ]
}

# Orchestrator 處理（在 _phase_actor_critic_loop() 內）
for gap in gap_resolutions:
    result = tier6_dispatcher.resolve(gap.api, gap.query)
    context.append(format_tier6_result(result))
```

### 結果格式化

```python
def format_tier6_result(api_id: str, data: dict) -> str:
    """格式化 Tier 6 API 結果為 Context 字串"""
    if api_id == "stock_tw":
        return f"[股票資訊] {data['symbol']}: 收盤價 {data['close']}, 漲跌 {data['change']}%"
    elif api_id == "wikipedia":
        return f"[Wikipedia] {data['title']}: {data['summary'][:500]}..."
    # ...
```

---

## 6. Free Conversation Mode

**檔案**: `methods/generate_answer.py`

### 概述

Free Conversation Mode 允許用戶在 Deep Research 完成後進行後續 Q&A，延續研究上下文。

### 觸發條件

```python
if has_previous_deep_research_report(conversation_id):
    mode = "free_conversation"
    context = load_previous_report(conversation_id)
```

### 流程

```
Deep Research 完成 → 用戶後續提問 → Free Conversation
                                        ↓
                          1. 載入之前的研究報告
                          2. 將報告作為 Context 注入
                          3. 使用 LLM 回答後續問題
                                        ↓
                              支援多輪對話
```

### Context 注入

```python
def build_free_conversation_context(report: str, user_question: str) -> str:
    return f"""
以下是之前的研究報告：

{report}

---

用戶後續問題：{user_question}

請根據上述研究報告回答用戶問題。如果報告中沒有相關資訊，請明確告知。
"""
```

### API 端點

> **2026-06-17 re-sync**：**無**獨立 `POST /api/free_conversation` 端點。Free Conversation 整合在 `methods/generate_answer.py:synthesize_free_conversation()`（`:549`），經 **`/ask`（GenerateAnswer handler）** 觸發，不另開路由。

觸發方式（query_params）：

| 參數 | 說明 |
|------|------|
| `free_conversation=true` | 於 `core/baseHandler.py` 讀入並設 `self.free_conversation`；為 true 時 GenerateAnswer 在 `:348` 呼叫 `synthesize_free_conversation()` |
| `research_report` | 前端帶入的先前研究報告字串，於 `generate_answer.py:561` 經 `get_param` 讀取，注入為 `self.injected_research_report` 作 prompt context |

```
POST /ask?generate_mode=...&free_conversation=true&research_report=<先前研究報告字串>
{
    "query": "用戶後續問題"
}
```

> 架構沿革：先前的 free-conversation 注入路徑（`performFreeConversation()`）已於 2026-04 起以 composable pipeline / `/ask` query_param 取代（見 `docs/specs/kg-editing-spec.md` 2026-04-13 architecture update 註記）。

---

## 7. ~~特殊邏輯：Monitor Mode Gap Analysis~~ （已移除）

> **此章節已於 2026-04 廢除**。Monitor Mode 已隨研究模式選擇功能一同移除。官方 vs 民間對比分析現在是 Analyst 的通用邏輯，不再綁定特定模式。

---

## 8. 錯誤處理 (Error Handling)

**類別**: `ResearchError`、`ResearchCancelledError`

| Error Type | 說明 | 處理 |
|------------|------|------|
| `NO_VALID_SOURCES` | 來源過濾後無剩餘資料 | 提示使用者擴大來源選擇範圍 |
| `SEARCH_FAILED` | 搜尋 API 錯誤 | 重試或降級 |
| `LLM_PARSE_ERROR` | JSON 解析失敗 | 重試（最多 3 次）|
| `TIER6_API_ERROR` | 外部 API 錯誤 | 跳過該 Gap，繼續處理 |
| `MAX_ITERATIONS_REACHED` | 達到最大迭代次數 | 使用當前最佳 Draft |
| `ResearchCancelledError` | Client 斷線或 soft interrupt | 立即停止（不再發 LLM 請求），回傳空列表 |

### 優雅降級

```python
try:
    result = orchestrator.run(query)
except ResearchError as e:
    if e.type == "NO_VALID_SOURCES":
        # 提示使用者擴大來源選擇範圍
        return suggest_expand_sources()
    elif e.type == "MAX_ITERATIONS_REACHED":
        # 使用最後的 Draft
        return format_partial_result(e.last_draft)
```

---

## 9. 配置與 Feature Flags

**檔案**: `config/config_reasoning.yaml`

```yaml
reasoning:
  enabled: true
  max_iterations: 3
  analyst_timeout: 300
  critic_timeout: 120
  writer_timeout: 300

  features:
    composable_pipeline: true
    nonblocking_research: false
    user_friendly_sse: true
    plan_and_write: true
    argument_graphs: true
    structured_critique: true
    knowledge_graph_generation: false
    gap_knowledge_enrichment: true
    cov_lite_enabled: true
    agent_isolation: false

source_tiers:
  # Tier 過濾由使用者自選來源決定（研究模式 strict/discovery/monitor 已於 2026-04 移除）
  social_tier_warning_threshold: 3  # Tier >= 3 的來源加警語標籤
```

> **`mode_configs` 殘留註記（2026-06-17 re-sync）**：`config/config_reasoning.yaml`（`:189-195`）仍存在 `mode_configs:` 區塊（`strict.max_tier` / `discovery.max_tier` / `monitor.compare_tiers`），但 2026-04 mode 移除後**已無任何 .py consumer**（`source_tier.py` docstring 明示 mode-based hard filtering 已移除、`mode` 參數 value ignored）。屬遺留配置，**保留待清理**。（本 spec 任務不修改 config，僅在此註明狀態。）

### Feature Flags 詳細說明

#### `composable_pipeline`

| 值 | 行為 |
|----|------|
| `true` | `run_research()` 路由至 `_run_research_composable()`（4 phase methods + ResearchState） |
| `false` | `run_research()` 路由至 `_run_research_legacy()`，但 legacy 實作目前也路由至 composable（零行為差異，flag 主要用來 gate Task 6+ 功能） |

> **注意**：Tasks 0-4 的重構是零行為變更（zero behavior change）。`composable_pipeline=false` 並不會還原到舊的單體 method，因為該 method 已被提取為 4 個 phase methods。

#### `nonblocking_research`

| 值 | 行為 |
|----|------|
| `true` | 需同時搭配 `composable_pipeline=true`；`run_research()` 透過 `asyncio.create_task` 包裝為具名 Task，支援 `.cancel()` 和 soft interrupt |
| `false` | 預設；`await orchestrator.run_research(...)` 阻塞式執行（legacy blocking path） |

> **注意**：`nonblocking_research=true` 需要 `composable_pipeline=true`。若只有 `nonblocking_research=true` 而 `composable_pipeline=false`，仍使用阻塞式路徑。

#### 其他 Feature Flags

| Flag | 說明 |
|------|------|
| `user_friendly_sse` | SSE 進度訊息改為繁中用戶友善文字 |
| `plan_and_write` | Writer 先生成報告大綱再撰寫（適合 2000+ 字長報告） |
| `argument_graphs` | Analyst 產生結構化論證圖 |
| `structured_critique` | Critic 使用結構化弱點偵測 |
| `knowledge_graph_generation` | 生成 Entity-Relationship KG（可被 per-request `enable_kg` 參數覆蓋） |
| `gap_knowledge_enrichment` | Stage 5：Gap Detection + LLM Knowledge + Web Search |
| `cov_lite_enabled` | Phase 2 CoV 事實查核 |
| `agent_isolation` | SEC-6：Critic 只看 Analyst 引用的來源（Context Routing 隔離） |

---

## 10. Non-blocking Architecture

> **完整規格**（Task schedule、Live 研究整合）見 `docs/specs/live-research-spec.md`。

### 基本 Wiring（`methods/deep_research.py`）

```python
# 需要 composable_pipeline=true AND nonblocking_research=true
if enable_composable and enable_nonblocking:
    self._research_task = asyncio.create_task(
        orchestrator.run_research(...),
        name=f"research_{self.conversation_id}"
    )
    # Callback: catch exceptions silently swallowed by asyncio
    self._research_task.add_done_callback(self._on_research_complete)
    try:
        results = await self._research_task
    except asyncio.CancelledError:
        results = []
    finally:
        self._research_task = None
else:
    # Legacy blocking path（預設）
    results = await orchestrator.run_research(...)
```

### Soft Interrupt Event

`_soft_interrupt_event`（`asyncio.Event`）在 `DeepResearchHandler.__init__()` 初始化。Orchestrator 的 `_check_connection()` 在每個 cancellation checkpoint 檢查此 event：

```python
soft_interrupt = getattr(self.handler, '_soft_interrupt_event', None)
if soft_interrupt and soft_interrupt.is_set():
    raise ResearchCancelledError("User interrupted (soft)")
```

設定 `_soft_interrupt_event` 可在 phase boundary 安全停止 research（不強制 cancel task）。

### `_on_research_complete` Callback

```python
def _on_research_complete(self, task: asyncio.Task):
    """W2 fix：確保背景 task 的 exception 被記錄，不會被 asyncio 靜默吞掉。"""
    try:
        exc = task.exception()
        if exc:
            logger.error(f"[DEEP RESEARCH] Background research task failed: {exc}")
            asyncio.create_task(self._send_research_error(exc))
    except asyncio.CancelledError:
        pass  # 正常 cancellation（client disconnect 或 soft interrupt）
```

---

## 11. Phase SSE Events

Orchestrator 在每個 phase 的開始和結束發送 SSE event（`message_type: "research_phase"`）：

```json
{
    "message_type": "research_phase",
    "phase": "<phase_name>",
    "status": "started | completed"
}
```

**8 個 Phase Events 清單**：

| Phase | Events |
|-------|--------|
| `filter_and_prepare` | `filter_and_prepare started` / `filter_and_prepare completed` |
| `actor_critic_loop` | `actor_critic_loop started` / `actor_critic_loop completed` |
| `writer` | `writer started` / `writer completed` |
| `format_result` | `format_result started` / `format_result completed` |

> Phase events 之外，loop 內部還有 `intermediate_result` 類型的進度訊息（`analyst_analyzing`、`critic_reviewing`、`writer_composing` 等），由 `_send_progress()` 發送。

---

## 12. 除錯工具

### ConsoleTracer

即時事件視覺化，用於開發除錯。

```python
from reasoning.debug import ConsoleTracer

tracer = ConsoleTracer()
orchestrator = DeepResearchOrchestrator(tracer=tracer)
```

### IterationLogger

JSON 事件流日誌，用於事後分析。

```python
from reasoning.debug import IterationLogger

logger = IterationLogger(log_dir="logs/reasoning")
orchestrator = DeepResearchOrchestrator(logger=logger)
```

---

## 13. Changelog

### 2026-03-19 — RSN-11 Guard Fix + Verification Status SSE

**RSN-11 Guard Fix（P0 Bug）**：
- `orchestrator.py` L604 的空結果 guard 從 `if not self.formatted_context` 改為 `if not self.source_map`
- 原因：`_get_current_time_header()` 永遠回傳非空字串，讓 formatted_context 永不為空，即使 retrieval 回傳 0 結果
- `source_map` 只有真正的 retrieval 結果才會填入，不受 header 影響
- 測試：`tests/unit/test_zero_results_guard.py`（8 tests）

**RSN-4 Verification Status SSE Propagation**：
- Critic agent 的 `verification_status` / `verification_message`（CoV 查核結果）現在傳到前端
- 資料流：`critic.py __dict__` → `orchestrator._format_result` → `api.py final_result SSE` → `news-search.js warning banner`
- 前端在 unverified / partially_verified 時顯示黃色 warning banner
- 測試：`tests/unit/test_verification_status_sse.py`（6 tests）

### 2026-03-27 — R2 source_map ID collision fix + orchestrator consolidation

**source_map ID collision fix (Critical)**:
- 9x `len(self.source_map)+1` 改為 `max(self.source_map.keys(), default=0)+1`
- 防止 gap search 新增文件時 ID 碰撞覆蓋既有 source entry
- merge 前加 invariant check（overlap detection + error log）

**orchestrator 方法合併 (-130 lines)**:
- 7 個 duplicate `_execute_*_searches()` 方法（stock_tw, yfinance, google, wikipedia, wikidata, cwb_weather, openweathermap）合併為 generic `_execute_api_searches()`
- `schemas_enhanced.py` circular import 修復

### 2026-04 — 移除研究模式選擇

- Strict / Discovery / Monitor 三種研究模式已移除，改為使用者自選 source
- `hard_filter_and_enrich()` 的 `mode` 參數廢除
- Agent prompt 中的模式分支邏輯移除，改為通用邏輯
- Writer 模板統一，不再依模式切換
- Monitor Mode Gap Analysis（第 7 節）廢除

### 2026-04 — Composable Pipeline Refactor

- `run_research()` 拆為 4 composable phases（`_phase_filter_and_prepare`, `_phase_actor_critic_loop`, `_phase_writer`, `_phase_format_result`）
- 新增 `ResearchState` dataclass（25 fields，`reasoning/research_state.py`）
- 新增 `composable_pipeline` + `nonblocking_research` feature flags
- 新增 Phase SSE events（8 events：4 phases × started/completed）
- 新增 `soft_interrupt_event`（`asyncio.Event`）於 `DeepResearchHandler`
- 新增 `_on_research_complete` done callback（W2 fix：防止 asyncio 靜默吞掉 exception）
- 新增 `ResearchCancelledError`（統一的 cancellation exception）
- 移除 Strict/Discovery/Monitor 研究模式
- 新功能 Live 研究（Beta）獨立為 `docs/specs/live-research-spec.md`

### 2026-04 後續 — OrchestratorBase 抽取 + `reasoning/live_research/` 子目錄

**E13：基底類別抽取與 LR 子目錄建立**

- commit `50d2841`：抽取 `OrchestratorBase`（`reasoning/orchestrator_base.py`，~200 行），shared logic 包含 phase event emission、SSE 推送、connection check、IterationLogger / ConsoleTracer 初始化
- commit `c97d648`：建立 `reasoning/live_research/` 子目錄，新增 `LiveResearchOrchestrator`（6-stage 對話驅動控制器），繼承 `OrchestratorBase`
- commits `7471619` / `fe4db4c`：新增 `BABLoopEngine`（`reasoning/live_research/loop_engine.py`），B → A → B' 可複用迴圈引擎，Stage 1（全域）與 Stage 2（per-section 聚焦）共用
- commit `6fd5965`：新增 `LiveResearchStageState` dataclass（`reasoning/live_research/stage_state.py`），跨 request 持久化於 `search_sessions.live_research_state` JSONB 欄位
- 新增 `fixtures/real_energy_policy_state.json`：`live_research_mock_bab` flag 啟用時的 fixture ContextMap
- 繼承層級：`OrchestratorBase ← {DeepResearchOrchestrator, LiveResearchOrchestrator}`
- 動機：避免 LR 重寫 ~200 行 boilerplate；BAB loop 是可複用的內部引擎

---

*更新：2026-05-04*
