# Skill 開發指南

> 基於 Anthropic 官方 "Lessons from Building Claude Code: How We Use Skills" 分析，
> 結合我們自身 skills 現況整理的優化方針。

---

## 一、Skill 的 9 大類型

Anthropic 內部將數百個 skills 歸納為 9 大類型。好的 skill 應該清楚歸屬其中一類；跨多類的 skill 往往令人困惑。

| # | 類型 | 說明 | 範例 |
|---|------|------|------|
| 1 | **Library & API Reference** | 說明如何正確使用某個 library/CLI/SDK，含 gotchas 和 code snippets | billing-lib, internal-platform-cli |
| 2 | **Product Verification** | 描述如何測試/驗證產出是否正確，常搭配 playwright、tmux 等外部工具 | signup-flow-driver, checkout-verifier |
| 3 | **Data Fetching & Analysis** | 連接資料/監控系統，含 credentials、dashboard ID、常見查詢 workflow | funnel-query, grafana |
| 4 | **Business Process & Team Automation** | 自動化重複性團隊流程，通常依賴其他 skill 或 MCP | standup-post, weekly-recap |
| 5 | **Code Scaffolding & Templates** | 為 codebase 中特定功能產生 boilerplate | new-migration, create-app |
| 6 | **Code Quality & Review** | 強制程式碼品質，可搭配 hooks 或 GitHub Action 自動執行 | adversarial-review, testing-practices |
| 7 | **CI/CD & Deployment** | 幫助 fetch、push、deploy 程式碼 | babysit-pr, deploy-service |
| 8 | **Runbooks** | 從症狀出發，走過多工具調查流程，產出結構化報告 | service-debugging, oncall-runner |
| 9 | **Infrastructure Operations** | 執行例行維護與營運程序，含破壞性操作的 guardrails | orphan-cleanup, cost-investigation |

### 我們的 Skills 覆蓋現況

| 類型 | 我們有的 | 缺口 |
|------|----------|------|
| Library/API Reference | — | NLWeb API、schema 沒有專屬 skill |
| Product Verification | — | 沒有驗證搜尋結果品質的 skill |
| Data Fetching & Analysis | crawler-monitor（部分） | 不完整 |
| Business Process | daily-recap, notion-monthly | 還行 |
| Code Scaffolding | — | 不急需 |
| Code Quality & Review | superpowers 已覆蓋 | OK |
| CI/CD & Deployment | — | 未來需要 |
| Runbooks | crawler-monitor（部分） | 不完整 |
| Infra Operations | catchup-scan, newest-scan | 還行 |

---

## 二、核心原則

### 1. Skill 是資料夾，不是 markdown 檔案

> "The most interesting part of skills is that they're not just text files. They're folders that can include scripts, assets, data, etc."

**善用檔案系統做 progressive disclosure：**
- 主 `SKILL.md` 只放核心指令和流程
- `references/` — 詳細的 API 簽名、格式說明、查詢範例
- `scripts/` — 可執行的腳本，讓 Claude 做 composition 而非重建 boilerplate
- `assets/` — 模板檔案，供 Claude 複製使用

**我們的現況：** 14 個 skill 中只有 3 個用了子資料夾。`newest-scan` 是 14KB 的巨大單檔，應拆分。

### 2. Gotchas 段落是最高價值內容

> "The highest-signal content in any skill is the Gotchas section."

每個 skill 都應維護一個 `## Gotchas` 段落，記錄 Claude 在使用此 skill 時常犯的錯誤。這個段落應隨時間持續更新。

**範例：**
```markdown
## Gotchas
- GCP VM SSH 有時會 timeout，先 ping 確認再連
- desktop crawler 的 log 路徑在 D: 不在 C:
- 不要同時在兩台機器對同一個 source 跑 scan，會產生重複文章
- newest_scan 模式必須用 --mode newest，不能用 full_scan
```

### 3. Description 是給模型看的觸發器

> "The description field is not a summary — it's a description of when to trigger."

模型在 session 開始時掃描所有 skill 的 description，以此判斷「要不要觸發」。Description 應該：
- 列出具體的觸發詞（中英文）
- 描述「什麼情境下用」而非「這個 skill 做什麼」
- 包含否定條件（什麼時候不要觸發）

### 4. 不要寫顯而易見的東西

> "If you're publishing a skill that is primarily about knowledge, try to focus on information that pushes Claude out of its normal way of thinking."

Claude 已經知道很多，skill 應該聚焦在：
- Claude 預設行為會出錯的地方
- 我們專案特有的慣例
- 非直覺的操作順序

### 5. 避免過度指導 (Railroading)

> "Give Claude the information it needs, but give it the flexibility to adapt to the situation."

Skill 應該提供必要資訊，但不要把每一步都寫死。給 Claude 彈性去適應不同情境。

---

## 三、進階技巧

### Config 機制

用 `config.json` 分離設定與邏輯。首次使用時引導使用者設定。

```
skill-folder/
├── SKILL.md
├── config.json        ← SSH 連線資訊、路徑、project ID 等
├── references/
└── scripts/
```

好處：換機器或分享 skill 時不需要改 SKILL.md 本身。

**適合的 skill：** crawler-monitor（機器連線資訊）、catchup-scan/newest-scan（registry 路徑）、notion-monthly（database ID）。

### Memory / Log 機制

Skill 可以用 log 檔存歷史執行紀錄，下次執行時參考。

```markdown
每次執行完畢，將結果 append 到 `${CLAUDE_PLUGIN_DATA}/scan-history.jsonl`。
下次執行時先讀取 history，比較差異。
```

**適合的 skill：** daily-recap（趨勢分析）、crawler-monitor（健檢 diff）、catchup-scan（避免重複掃描）。

### On-Demand Hooks

Skill 被呼叫時才生效的 hooks，session 結束後自動消失。適合高風險操作的 guardrails。

```markdown
hooks:
  PreToolUse:
    - matcher: Bash
      hook: "python scripts/guard.py --block-dangerous"
```

**應用場景：**
- 操作 production 資料時阻擋 `rm -rf`、`DROP TABLE`、`force-push`
- crawler 操作時檢查是否會影響正在跑的 process

### 腳本組合 (Store Scripts & Generate Code)

把常用操作封裝成可組合的腳本，讓 Claude 專注在 composition 而非重建 boilerplate。

```
scripts/
├── check_registry.py    ← 查 registry 狀態
├── ssh_gcp.sh           ← 標準化 SSH 連線
└── parse_crawler_log.py ← 解析 crawler log
```

Claude 可以在執行時組合這些腳本，而不用每次重寫同樣的邏輯。

---

## 四、缺口分析與新 Skill 提案

### P0: nlweb-verify（Product Verification）

搜尋品質驗證 skill，填補我們最大的空白。

```
nlweb-verify/
├── SKILL.md             ← 驗證流程與判斷標準
├── scripts/
│   ├── query_test.py    ← 對指定 query 執行搜尋
│   └── report.py        ← 產出結構化報告
└── references/
    └── quality-criteria.md  ← 品質指標定義
```

功能：
- 對指定 query 執行搜尋，檢查結果數量、相關性、時效性
- 比對不同 source 的覆蓋率
- 輸出結構化品質報告

### P2: crawler-runbook（Runbook）

標準化爬蟲問題排查流程。

```
crawler-runbook/
├── SKILL.md             ← 排查流程總覽
└── references/
    ├── symptoms.md      ← 症狀 → 可能原因 mapping
    └── fix-recipes.md   ← 常見修復步驟
```

常見場景：文章數異常、連線失敗、重複文章、encoding 問題。

---

## 五、優化行動優先順序

| 優先級 | 行動 | 原因 |
|--------|------|------|
| **P0** | 每個 skill 加 Gotchas 段落 | 最低成本、最高回報 |
| **P0** | newest-scan 拆檔（14KB → 模組化） | 太大的單檔浪費 context window |
| **P1** | crawler-monitor 加 scripts + log | 最常用的 skill，值得投資 |
| **P1** | 建立 nlweb-verify skill | 填補 product verification 缺口 |
| **P2** | 各 skill 加 config.json 機制 | 分離設定與邏輯，利於分享 |
| **P2** | 建立 crawler-runbook skill | 標準化排查流程 |
| **P3** | 研究 on-demand hooks 應用 | 進階安全機制 |

---

## 六、Autoresearch：用實驗迴圈自動優化 Skill

> 基於 Karpathy 的 autoresearch 方法論，將自主實驗迴圈應用於 skill prompt 優化。
> 參考實作：`C:\Users\User\autoresearch_on_skills\`

### 核心概念

大多數 skill 只有約 70% 的成功率。剩下 30% 不是靠重寫解決的，而是靠 **反覆執行 → 評分 → 微調 → 保留改進** 的迴圈來收斂。

```
讀取 skill → 定義 binary eval → 跑 baseline → 改一個東西 → 重跑 → 分數進步就保留，否則 revert → 重複
```

### 方法論重點

#### 1. Binary Eval — 只用「是/否」，不用量表

量表（1-7 分）會放大變異，讓結果不可靠。所有品質檢查都必須是 binary：

```
EVAL 1: 文章時效性
Question: 搜尋結果的第一篇文章是否在 7 天內發佈？
Pass: 日期在 7 天內
Fail: 日期超過 7 天或無日期
```

**好 eval 的三項測試：**
1. 兩個不同的 agent 對同一個 output 打分會一致嗎？（不一致 = 太主觀）
2. Skill 能否不實際改善就通過這個 eval？（能 = 太窄）
3. 這個 eval 測的是使用者真正在意的嗎？（不是 = 刪掉）

#### 2. 一次只改一個東西

好的 mutation：
- 針對最常失敗的 eval 加一條具體指令
- 把模糊的措辭改明確
- 加 anti-pattern（「不要做 X」）
- 把重要指令往上移（位置 = 優先級）
- 加一個正確行為的範例
- 刪除導致過度最佳化的指令

壞的 mutation：
- 一次改 5 個東西（不知道哪個有效）
- 整個重寫
- 加模糊指令如「做得更好」

#### 3. 保留完整實驗紀錄

每次 mutation 都記錄：改了什麼、為什麼、結果如何、哪些 eval 進步/退步。

這個 changelog 是最有價值的產出 — 未來的 agent 或更強的模型可以接續優化。

#### 4. 停止條件

- 連續 3 次 ≥95% pass rate（邊際遞減）
- 達到預設的 budget cap
- 使用者手動停止

### 我們可以怎麼用

#### 適合 autoresearch 的 skill

| Skill | 為什麼適合 | 可能的 eval |
|-------|-----------|-------------|
| **crawler-monitor** | 輸出格式和診斷品質不穩定 | 是否列出所有機器狀態？是否包含 error count？是否有可執行的建議？ |
| **daily-recap** | 摘要品質波動大 | 是否按類別分組？是否包含具體數字？是否遺漏重要 session？ |
| **catchup-scan** | 啟動指令偶爾出錯 | 指令語法是否正確？registry 路徑是否存在？是否避免重複掃描？ |
| **notion-monthly** | 分類有時不準確 | 5 個分類是否都有內容？是否有文章被分錯類？格式是否正確？ |

#### 不適合 autoresearch 的 skill

- **plan-discuss** — 對話式 skill，輸出高度依賴上下文，難以定義 binary eval
- **ux-discuss** — 同上，引導式討論無法標準化評分
- **skill-creator** — 元層級 skill，每次輸出都不同

### 實務建議

1. **先從最痛的 skill 開始** — 那個你最常手動修正 Claude 輸出的 skill
2. **3-5 個測試輸入就夠了** — 但要涵蓋不同 use case，避免 overfit
3. **3-6 個 eval 是甜蜜點** — 太多 eval 會讓 skill 開始背答案
4. **baseline 很重要** — 改之前先量化現況，否則無法知道是否真的改善了
5. **Dashboard 很有用** — autoresearch skill 會產生即時 HTML dashboard，可以看到分數趨勢
6. **改善的 SKILL.md 不會覆蓋原檔** — 產出在 `autoresearch-[name]/` 目錄，手動 review 後再決定是否採用

---

## 七、Skill 發佈與維護

### 分享方式
- **Repo 內 `.claude/skills/`** — 小團隊、少 repo 時適合
- **Plugin Marketplace** — 規模化時讓使用者自選安裝

### 品質控管
- 新 skill 先放 sandbox，有人用了再正式發佈
- 用 PreToolUse hook 追蹤 skill 使用率，找出 undertrigger 的 skill
- 定期清理不再使用或已被取代的 skill

### 持續改善
- 每次 Claude 使用 skill 出錯時，更新 Gotchas
- 定期檢視 skill 的 description 是否準確觸發
- 隨專案演進，更新 references 中的資料
