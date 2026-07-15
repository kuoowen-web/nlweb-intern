# 正確 Mock BAB Playbook — Pipeline 工程的可重複自驗

> **目的**：解耦 BAB（蒐集 evidence，貴）vs Pipeline（Stage 3-6 處理：writer / critic / guard / 組裝 = 工程品質所在）。凍結一次 prod BAB 的**真實產物**當固定 fixture，反覆跑 pipeline，**零 BAB $、Stage 3-6 真 LLM 小 $**，專測 + 自驗 pipeline 工程。
> 
> **依據 CEO 紀律**：「mock 貴的資料蒐集、永不 mock 推理」。BAB（蒐集）凍成 fixture，pipeline（推理）真跑。
> 
> **兩種執行路徑（依 infra 狀態選，互補）**：
> 
> - **方法 A（code `mock_bab`，本檔主體）**：改 orchestrator 載真語料 fixture → 起 server → Stage 3-6 真 OpenAI（~$1-2）。驗 **code 實作**正確性。前提：pipeline code 跑得動 + 注入機制已建。
> - **方法 B（Claude Code 純人工，見檔末「方法 B」段）**：Zoe 扮讀豹 + subagent 餵 code 真實 prompt，**零 server / 零改 code / 零 OpenAI**（Claude 額度）。驗 **機制設計**本身。前提：pipeline infra 卡死連 code 都跑不動、或要快速解耦「方法論 vs infra」時首選（2026-06-08 實證得 8.5/10 零 fabrication）。

---

## 為什麼這才是「正確」做法

|            | 舊 mock_bab                                                            | 正確 mock_bab                                                           |
| ---------- | --------------------------------------------------------------------- | --------------------------------------------------------------------- |
| fixture 來源 | orchestrator.py 內 **hardcoded 假料**（3 topics + ~21 筆假 evidence）        | **prod 真語料**（`5767ae4a` 的 36 筆真 evidence + 真 outline + 真 context_map） |
| 問題         | evidence 不真實 → pipeline 處理假料 → **放大假象**（如 lr-7 抽象詞誤判在 21 筆小池被放大，誤導判斷） | pipeline 處理真實 evidence → **真實工程問題無所遁形**                               |
| 證據         | —                                                                     | 今天的 over-block / slop / critic 不完整全是用真語料才抓到的                          |

**核心**：mock 的是「BAB 怎麼蒐集到這 36 筆」，**不是** mock pipeline 怎麼處理它們。處理永遠真跑。

---

## Fixture 來源（prod session 5767ae4a）

來源 session：`5767ae4a-cbf7-474d-974c-0021ddd5e634`（題「台灣綠能衝突借鏡國外」，2026-06-08 prod 真跑，含 F1 web search 撈到的德/日/智利國際 evidence）。

| 產物                                                        | 撈法                                                                       | 落地 fixture                                                       |
| --------------------------------------------------------- | ------------------------------------------------------------------------ | ---------------------------------------------------------------- |
| evidence_pool（36 筆真語料）                                    | `live_research_state->>'evidence_pool_json'`（已撈 `_tmp_ev_5767.json`）     | `code/python/tests/fixtures/lr_mock_bab_real/evidence_pool.json` |
| book_outline（5 章）                                         | `live_research_state->>'book_outline_json'`（已撈 `_tmp_outline_5767.json`） | `.../book_outline.json`                                          |
| context_map                                               | `live_research_state->>'context_map_json'`                               | `.../context_map.json` ✅ 已撈                                      |
| evidence_usage（grounded claims，35 id / 147 claims，全 PASS） | `live_research_state->'evidence_usage'`（jsonb，非 _json string）            | `.../evidence_usage.json` ✅ 已撈                                   |
| style_reference（CEO 文筆範本逐字）                               | CEO mock session 提供                                                      | `.../style_reference.md` ✅ 已存                                    |

> **🔴 fixture 曾漏 evidence_usage（2026-06-08 方法 A e2e 揪出）**：原清單只列 evidence_pool / context_map / book_outline 三項是**不完整的**——漏了「BAB 也產出 grounded claims（evidence_usage）」這層。chapter-override（5 章）路徑的 writer 硬依賴它，缺則 body 章空轉（詳見下方「兩條 writer 路徑」）。
> **資料已撈補**（`evidence_usage.json` 35 id / 147 claims，全 PASS）；**但 code `mock_bab` 載入邏輯仍須加載 evidence_usage**（見下方「注入機制」，原本只載 3 檔）——這步是 code 改動，方法 A agent 復工時做。

PG 撈法：`ssh -i C:\Users\User\.ssh\YOUR_SSH_KEY -p 2222 root@YOUR_VPS_HOST` → `docker exec -i nlweb-postgres psql -U nlweb -d nlweb -tA`（中文輸出正常；中文 LIKE 輸入會被 cp950 壞，本 fixture 撈不需中文 WHERE）。

---

## ⚠️ 兩條 writer 路徑 — 決定 fixture 要不要 evidence_usage（2026-06-08 方法 A e2e 揪出）

writer 依「章節來源」走兩條路，對 fixture 依賴不同：

- **core_topics 路徑**（`cm.topics` core，通常 = topics 數如 10 章）：writer 直接吃 `evidence_pool` snippet、**不依賴 grounded claims** → body 章寫得出、over-block 測得到，但**結構是 topics 數非 5 章**。
- **chapter-override 路徑**（`format_specs.chapters`，5 章 = CEO 標準結構）：writer **硬依賴 `evidence_usage`（grounded claims）**；evidence_usage 空 → 「[本章資料不足]」跳過 LLM、**body 章空轉、over-block 測不到**。

**結論**：要測 CEO 的 5 章結構 + over-block，fixture **必須含 evidence_usage**（從 5767ae4a PG `evidence_usage` 撈）。
**對照方法 B**：方法 B 是人工派 Analyst subagent 產 grounded claims 餵 writer（= 人工 mock 了 evidence_usage），所以 body 章寫得出——這正是「人工接線補了 code fixture 缺的層」的實例，呼應方法 B 紀律①。

---

## 固定輸入（CEO 標準測試 prompt）

> **定位（CEO 拍板 2026-06-13）**：以下是讀豹標準測試題的 **canonical 輸入腳本，mock 與非-mock 模式共用**，不是 mock 專屬。兩模式用法差別：
> - **mock 模式（測 Stage 3-6）**：Stage 1/2 吃 fixture、初始格式抽取被 fixture-as-ground-truth 保護跳過（見「注入機制」段），所以系統**可能不會逐關問**架構/文筆/格式。**能照腳本回就回，系統沒問的 checkpoint 不強求、不為觸發而硬湊** — 本模式目的是測 Stage 3-6（writer/critic/guard/組裝），前段 checkpoint 問不問不影響。
> - **非-mock 模式（H3 merge 後真機 E2E）**：初始格式抽取會真的逐關問架構/文筆/格式 — 此時**嚴格按下方腳本逐關回**。
> - **開場主題在 mock 下是裝飾性的**：mock_bab=true 時 evidence 永遠是 fixture 那 36 筆，開場打什麼主題都不驅動 BAB 蒐集，只影響 Stage 1 顯示文字。非-mock 模式開場主題才真正驅動蒐集。

- **開場輸入主題**：我現在要寫一篇專題研究，主題為能源轉型下的農漁村綠能衝突。內容包含我國綠能進入農漁村所發生的衝突、國外在遇到相關衝突且成功解決的案例以及國外案例中有哪些地方可以讓我國借鏡。    
- **詢問研究架構時：** 7000 字。1.前言(500) / 2.國內案例文獻(2500) / 3.國外案例文獻(2500) / 4.結果與討論(1000) / 5.結論(500)
- **詢問文筆範例時：** 2.1 國內案例
    新北市政府交通局自2021年7月起積極推動太陽能候車亭的建設，並附設智慧站牌於捷運頭前庄站、捷運幸福站等重要站點。這些候車亭上方裝設的太陽能板每天能提供約200瓦
    特小時的電力，足以供應候車亭及其智慧站牌的運行需求。站牌採用電子紙技術，具有抗紫外線、防反射與防污特性，夜間照明消耗的電量約為63瓦時。由於裝設過程中無需挖
    掘或埋設電線，整個設置僅需一天，實現了節能與環保的雙重效果(新北市政府交通局，2022)。
    新北市的太陽能板選用小型且模組化的設計(如圖1所示)，這不僅降低了初期建設成本，也有助於保持候車亭結構的穩定性。此外，低耗能的電子紙顯示技術確保即使是小容量
    太陽能板也能有效發揮作用。根據電子紙的特性，其操作溫度範圍在0至50攝氏度之間(Good Display, 2024; Pervasive Displays, 2023)，符合臺灣的氣候條件，因此無論寒
    暑，電子紙都能穩定運作，顯示效果不受影響。候車亭可分為連網式和離網式兩種類型，前者如捷運幸福站(圖2)，在發電量不足時可以通過電網補充電力，確保穩定供電；後
    者如山光社區站(圖3)，適合設置在難以連接電網或連接成本較高的地區，依靠足夠的太陽能板和儲能設施，即使在無電網覆蓋的情況下也能正常運行。`code/python/tests/fixtures/lr_mock_bab_real/style_reference.md`（簡元璽、謝依芸論文 2.1 段）
- **詢問撰寫格式時：** 第四章要有表格，比較前兩章節對應的案例。請使用APA引用。
- **如果再問一次有沒有要用APA格式** ：要用APA格式。

---

## 注入機制（✅ 已完成 2026-06-12 — 四檔全接線，宣稱經斷言測試釘死）

**現況**：`mock_bab=true` → `_run_stage_1` mock 分支載入 `tests/fixtures/lr_mock_bab_real/` **四檔真語料**：context_map（`_load_mock_bab_fixture`）+ evidence_pool（36 筆）+ evidence_usage（35 id/147 claims，`54d993ac`，chapter-override writer 必需）+ **book_outline（5 章，`0f02fd16` — 同步寫 `state.book_outline_json` 與 `format_specs["chapters"]`，writer 走 chapter-override 路徑）**。handler `_is_mock_bab` @ `live_research.py:71-74`；config flag @ `config_reasoning.yaml:32`。守門測試：`test_mock_bab_real_fixture.py`（8 tests，含 outline 5 章與 format_specs 斷言）。

> **⚠️ 歷史教訓（2026-06-12）**：本段曾宣稱「原本就載 book_outline」— 該 loader 實際從未存在（假✅潛伏到首次完整真跑才現形，E2E 跑出 10 主題章）。**任何「已接線✅」宣稱必須有對應斷言測試或 30 秒 grep 驗證**，文件與 commit message 的自述都不算數。

**已知刻意不同步**：mock 模式下 Stage 1 的「研究結構提案」顯示仍由 `_context_map_to_outline`（10 topics）生成，與 state 的 5 章 fixture 不同 — 兩者語意不同非 bug（見 `lr-initial-format-spec-extraction-plan.md` E2E-3 設計鎖定）。mock 模式不跑初始格式抽取（fixture-as-ground-truth 保護）。

---

## 跑法

1. 本地 server，HEAD 含 grounding + F1（`db7f8f60` 或之後），`mock_bab=true`（讀真語料 fixture）。
2. **確認 mock_bab 真生效**（防假綠燈）：`tasklist | grep python` 驗 server PID 變 + log 出現 mock_bab branch + 載入 36 筆。
3. 觸發 LR 跑 CEO 標準 prompt（瀏覽器登入 or 腳本）。**Stage 1+2 用 fixture（零 BAB $）、Stage 3-6 真 LLM**。
4. 跑完撈 PG `live_research_state` 對照：`written_sections` / `critic_section_reviews` / `rejected_claims_log` / `hallucination_corrected`。

---

## 成本

- Stage 1+2：**零**（fixture，不打 BAB / 不打 embedding / 不打 Google）。
- Stage 3-6：真 LLM 小 $（writer ×5 章 + per-section entity guard + specificity + critic）。一次估 **~$1-2**（比 ~$5 BAB run 省，且可反覆跑）。

---

## 驗收清單（6 模塊，每次跑都對照）

1. **Grounding**：合法 entity（台鹽綠能 / 台泥 / 台南地方法院 / 泰國蝦 / 嘉義縣，36 筆 fixture 內可查）**不再被判 ungrounded**、章節不 over-block `[本章內容無法驗證]`
2. **Writer**：無「不是，而是」slop；具名**不被模糊化**（台泥 ≠ 某水泥公司）；不退回抽象總結；字數接近各章規格
3. **Critic**：WARN 輸出完整不截斷；Actor-Critic revise 結果**有回流**到 writer（非 fire-and-forget）
4. **韌性**：LLM timeout/empty **不噴 raw `ValueError`**，優雅降級 + 可「重試本段」
5. **Evidence 充分度**：有 evidence-sufficiency narration（說明為何這些 evidence 夠下結論）
6. **Fabrication**：仍擋**真正編造**的 entity（不在 36 筆 fixture 的具體名詞），fail-closed 不破

---

## 自驗紀律

- 每次跑後**撈 PG state 對照**（不只看前端報告 — 前端可能被 render filter 遮蔽）。
- **推測標假說 + 驗偽**：low-model 比對誤判這類，必須真 LLM 跑才驗得到（unit mock LLM 驗不到）。
- **mock_bab 測完還原 `config false`**（demo-killer 紀律，**禁 commit `mock_bab=true`**）。
- fixture 載入路徑用真語料，但 `config_reasoning.yaml` 的 flag 預設值不動（false）。

---

## 方法 B：Claude Code UI 純人工 Mock（2026-06-08 實證，pipeline 卡死 / 快速驗設計時用）

> 不起 server、不改 code、不打 OpenAI。Zoe（orchestrator）扮讀豹，派 Claude subagent 當各 reasoning agent，**餵 code 撈出的真實 prompt**。用於「pipeline infra 一直卡，但想驗機制設計能不能產出好報告」。本次跑 Cayenne 題（台灣綠能衝突借鏡國外）得 **8.5/10、零 fabrication**（opus 獨立評估），證明 LR 方法論設計成立、fabrication 根因在 evidence 蒐集層（F1 web search regression）非推理層。

### 前置：撈真實 prompt blueprint（保真度命脈）

派 read-only subagent 從 code 撈各 stage 真實 prompt + 編排，落地 blueprint（本次在 `docs/scratch/`）：

- `lr-mock-blueprint-orchestration.md`（6-stage 編排 + state 結構 + **data flow 斷線審查**：stage2_feedback 無 consumer / KG 不進 writer）
- `lr-mock-blueprint-prompts.md`（Associator / Analyst / Critic / Writer / Style 各 prompt **逐字**）
- `lr-mock-blueprint-guards.md`（L1 / L2 / L3 + specificity + citation 判準）
- `lr-mock-blueprint-websearch-status.md`（外部 API 觸發現況）
  **鐵律**：subagent 必須餵這些真實 prompt（grounding block / critic 6 類判準逐字），不可即興自寫——否則驗到的是「你重新設計的 LR」非「現有機制」。

### Stage 流程（每 stage 派對應 subagent + 真實 prompt）

- **Stage 0 Retrieval**（唯一該 mock 的「貴蒐集」）：SSH 撈 prod 真語料 + 派 web search subagent 補國際 evidence。**先驗 evidence 來源**：SSH 查近期 LR session 的 `evidence_pool_json` 的 `source_domain` 分布——F1 web search regression（前端不帶 `enable_web_search`，6/5 起 evidence 只剩站內 7 source）就是這樣查到的，不可假設 web search 有在跑。
- **Stage 1 BAB**：Associator subagent 建 ContextMap → Analyst subagent 抽 grounded claims（每筆附 [E#]）→ **獨立** L1 Critic subagent 查核（propose-verify；本次抓到 Analyst 漂移把「10,000」寫成「8650」）。
- **Stage 2-4**：per-section 補蒐集 / Style 分析（餵 CEO 文筆範本）/ Format（APA + 各章字數）。
- **Stage 5 Writer ×N**：每章 writer subagent（餵真實 grounding block）→ **獨立** critic subagent 跑 L2 entity guard + L3 publish gate（6 類 fabrication）+ synthesis 章驗不冒新 entity。**絕不自審**。
- **Stage 6**：Zoe 組裝 5 章 + `_build_references_block`（文末 APA 參考文獻，別漏）。
- **評估**：**fresh-context 獨立 subagent**（沒參與寫作），逐一核對全報告 entity vs evidence pool，對照 baseline（Cayenne 4 編造地名 弗萊堡/千葉/北萊茵/智利 是否消除或 grounded）。

### 🔴 方法 B 專屬紀律（confidently-wrong 防線，最關鍵）

1. **人工接線 ≠ code 現實**：人工編排時 orchestrator 會**手動補上 code 沒接的線**（本次 Zoe 把 L1 critic 抓到的 ungrounded 人工修回，但真實 code 的 BAB critic 是 **fire-and-forget**、根本不回流）。→ mock 成功要嚴格區分「方法論有效」vs「我手動接的線」。**報結論前必派 code audit（DR/LR 對照）驗哪些是 code 真接、哪些是人工補的**，別把人工接線效果當 code 現實報給 CEO。
2. **獨立評估不自審**：寫作 subagent 與評估 subagent 必須 fresh context 分離。
3. **真實 prompt 餵 subagent**（見前置）。
4. **對照歷史 baseline 判定**（fabrication 4 地名是否消除/變 grounded）。
5. mock 完**派 fix-plan 覆蓋度盤點**：audit 列的結構問題哪些有 plan、哪些「做了 90% 卡最後接線」（如 Track C/D 後端做了前端沒接）、哪些 plan 是 severity 灌水該砍。

### 何時用 A vs B

- pipeline code 跑得動、要驗 code 實作 → **A**
- pipeline infra 卡死 / 要快速驗「機制設計值不值得修」/ 要零成本 → **B**
- B 驗設計（便宜快）、A 驗實作（真 code path）；**B 發現的「人工補的接線」正是 A/code 該補的 gap**（本次 B 揭露 LR 缺 DR 五大能力 + F1 web search regression）。

### 重跑材料

- CEO 標準 prompt 序列（12 段，整理在 mock session 對話）+ 上述 4 份 blueprint + evidence pool。

- 本次完整產物：`docs/scratch/lr-mock-*`（report-final / ch1-5 / evidence-web / contextmap / stage1-analyst / run-tracking）+ `dr-capability-audit.md` + `lr-capability-reality.md`。
