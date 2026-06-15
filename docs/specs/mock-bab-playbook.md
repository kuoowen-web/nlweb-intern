# Mock BAB Playbook — LR Pipeline 工程的可重複自驗（Intern 版）

> **目的**：解耦 BAB（蒐集 evidence，貴）vs Pipeline（Stage 3-6 處理：writer / critic / guard / 組裝 = 工程品質所在）。凍結一份**真實產物**當固定 fixture，反覆跑 pipeline，**零 BAB $、Stage 3-6 真 LLM 小 $**，專測 + 自驗 pipeline 工程。
>
> **核心紀律**：「mock 貴的資料蒐集、永不 mock 推理」。BAB（蒐集）凍成 fixture，pipeline（推理）真跑。
>
> 本 repo 已附**現成 fixture**（`code/python/tests/fixtures/lr_mock_bab_real/`），直接用，**無需自行撈資料庫**。

---

## 為什麼這才是「正確」做法

|            | 舊 mock_bab                                                            | 正確 mock_bab                                          |
| ---------- | --------------------------------------------------------------------- | ----------------------------------------------------- |
| fixture 來源 | orchestrator.py 內 **hardcoded 假料**（3 topics + ~21 筆假 evidence）        | **真語料 fixture**（36 筆真 evidence + 真 outline + 真 context_map） |
| 問題         | evidence 不真實 → pipeline 處理假料 → **放大假象**（抽象詞誤判在小池被放大，誤導判斷）          | pipeline 處理真實 evidence → **真實工程問題無所遁形**            |

**核心**：mock 的是「BAB 怎麼蒐集到這 36 筆」，**不是** mock pipeline 怎麼處理它們。處理永遠真跑。

---

## Fixture 內容（已附在 repo）

fixture 已備在 `code/python/tests/fixtures/lr_mock_bab_real/`，**直接載入即可，無需撈 PG**：

| 產物                                                        | 落地 fixture                                                       |
| --------------------------------------------------------- | ---------------------------------------------------------------- |
| evidence_pool（36 筆真語料）                                    | `evidence_pool.json` |
| book_outline（5 章）                                         | `book_outline.json`                                          |
| context_map                                               | `context_map.json`                                      |
| evidence_usage（grounded claims，35 id / 147 claims，全 PASS） | `evidence_usage.json`                                   |
| style_reference（文筆範本逐字）                                   | `style_reference.md`                                    |

> **🔴 fixture 完整性教訓**：fixture 必須含 **evidence_usage**。原本只列 evidence_pool / context_map / book_outline 三項是**不完整的**——漏了「BAB 也產出 grounded claims（evidence_usage）」這層。chapter-override（5 章）路徑的 writer 硬依賴它，缺則 body 章空轉（詳見下方「兩條 writer 路徑」）。

---

## ⚠️ 兩條 writer 路徑 — 決定 fixture 要不要 evidence_usage

writer 依「章節來源」走兩條路，對 fixture 依賴不同：

- **core_topics 路徑**（`cm.topics` core，通常 = topics 數如 10 章）：writer 直接吃 `evidence_pool` snippet、**不依賴 grounded claims** → body 章寫得出、over-block 測得到，但**結構是 topics 數非 5 章**。
- **chapter-override 路徑**（`format_specs.chapters`，5 章 = 標準結構）：writer **硬依賴 `evidence_usage`（grounded claims）**；evidence_usage 空 → 「[本章資料不足]」跳過 LLM、**body 章空轉、over-block 測不到**。

**結論**：要測 5 章結構 + over-block，fixture **必須含 evidence_usage**（本 repo fixture 已含）。

---

## 固定輸入（標準測試 prompt）

> 以下是標準測試題的 **canonical 輸入腳本，mock 與非-mock 模式共用**。
> - **mock 模式（測 Stage 3-6）**：Stage 1/2 吃 fixture、初始格式抽取被 fixture-as-ground-truth 保護跳過（見「注入機制」段），所以系統**可能不會逐關問**架構/文筆/格式。能照腳本回就回，系統沒問的 checkpoint 不強求。本模式目的是測 Stage 3-6（writer/critic/guard/組裝）。
> - **開場主題在 mock 下是裝飾性的**：mock_bab=true 時 evidence 永遠是 fixture 那 36 筆，開場打什麼主題都不驅動 BAB 蒐集，只影響 Stage 1 顯示文字。

- **開場輸入主題**：我現在要寫一篇專題研究，主題為能源轉型下的農漁村綠能衝突。內容包含我國綠能進入農漁村所發生的衝突、國外在遇到相關衝突且成功解決的案例以及國外案例中有哪些地方可以讓我國借鏡。
- **詢問研究架構時：** 7000 字。1.前言(500) / 2.國內案例文獻(2500) / 3.國外案例文獻(2500) / 4.結果與討論(1000) / 5.結論(500)
- **詢問文筆範例時：** 見 `code/python/tests/fixtures/lr_mock_bab_real/style_reference.md`（2.1 國內案例段）
- **詢問撰寫格式時：** 第四章要有表格，比較前兩章節對應的案例。請使用 APA 引用。
- **如果再問一次有沒有要用 APA 格式** ：要用 APA 格式。

---

## 注入機制

**現況**：`mock_bab=true` → `_run_stage_1` mock 分支載入 `tests/fixtures/lr_mock_bab_real/` **四檔真語料**：context_map（`_load_mock_bab_fixture`）+ evidence_pool（36 筆）+ evidence_usage（35 id/147 claims，chapter-override writer 必需）+ **book_outline（5 章 — 同步寫 `state.book_outline_json` 與 `format_specs["chapters"]`，writer 走 chapter-override 路徑）**。handler `_is_mock_bab` @ `live_research.py`；config flag @ `config_reasoning.yaml`。守門測試：`test_mock_bab_real_fixture.py`（8 tests，含 outline 5 章與 format_specs 斷言）。

> **⚠️ 歷史教訓**：本段曾宣稱「原本就載 book_outline」— 該 loader 實際從未存在（假✅潛伏到首次完整真跑才現形，E2E 跑出 10 主題章）。**任何「已接線✅」宣稱必須有對應斷言測試或 grep 驗證**，文件與 commit message 的自述都不算數。

**已知刻意不同步**：mock 模式下 Stage 1 的「研究結構提案」顯示仍由 `_context_map_to_outline`（10 topics）生成，與 state 的 5 章 fixture 不同 — 兩者語意不同非 bug。mock 模式不跑初始格式抽取（fixture-as-ground-truth 保護）。

---

## 跑法

1. 本地 server，HEAD 含 grounding + F1，`mock_bab=true`（讀真語料 fixture）。
2. **確認 mock_bab 真生效**（防假綠燈）：`tasklist | findstr python`（Windows）驗 server PID 變 + log 出現 mock_bab branch + 載入 36 筆。
3. 觸發 LR 跑標準 prompt（瀏覽器登入）。**Stage 1+2 用 fixture（零 BAB $）、Stage 3-6 真 LLM**。
4. 跑完撈本地 DB `live_research_state` 對照：`written_sections` / `critic_section_reviews` / `rejected_claims_log` / `hallucination_corrected`。

---

## 成本

- Stage 1+2：**零**（fixture，不打 BAB / 不打 embedding / 不打 Google）。
- Stage 3-6：真 LLM 小 $（writer ×5 章 + per-section entity guard + specificity + critic）。一次估 **~$1-2**（可反覆跑）。

> ⚠️ Stage 3-6 會打真 OpenAI，用**你自己的 dev key**（見 `INTERN_SETUP.md`）。每次跑前確認 key 額度。

---

## 驗收清單（6 模塊，每次跑都對照）

1. **Grounding**：合法 entity（fixture 36 筆內可查的具名實體）**不再被判 ungrounded**、章節不 over-block `[本章內容無法驗證]`
2. **Writer**：無「不是，而是」slop；具名**不被模糊化**（具體公司名 ≠ 某公司）；不退回抽象總結；字數接近各章規格
3. **Critic**：WARN 輸出完整不截斷；Actor-Critic revise 結果**有回流**到 writer（非 fire-and-forget）
4. **韌性**：LLM timeout/empty **不噴 raw `ValueError`**，優雅降級 + 可「重試本段」
5. **Evidence 充分度**：有 evidence-sufficiency narration（說明為何這些 evidence 夠下結論）
6. **Fabrication**：仍擋**真正編造**的 entity（不在 36 筆 fixture 的具體名詞），fail-closed 不破

---

## 自驗紀律

- 每次跑後**撈 DB state 對照**（不只看前端報告 — 前端可能被 render filter 遮蔽）。
- **推測標假說 + 驗偽**：low-model 比對誤判這類，必須真 LLM 跑才驗得到（unit mock LLM 驗不到）。
- **mock_bab 測完還原 `config false`**（**禁 commit `mock_bab=true`**）。
- fixture 載入路徑用真語料，但 `config_reasoning.yaml` 的 flag 預設值不動（false）。
