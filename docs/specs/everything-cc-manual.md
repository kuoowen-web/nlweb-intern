# Claude Code 配置使用手冊

> 基於 [everything-claude-code](https://github.com/affaan-m/everything-claude-code) 最佳實踐，針對 NLWeb 專案客製化的配置系統

---

## 目錄

1. [概述](#概述)
2. [檔案結構](#檔案結構)
3. [Hooks 自動化](#hooks-自動化)
4. [Memory System](#memory-system)
5. [Planner Agent](#planner-agent)
6. [Commands 快捷指令](#commands-快捷指令)
7. [Rules 規則系統](#rules-規則系統)
8. [維護指南](#維護指南)
9. [常見問題](#常見問題)

---

## 概述

### 設計目標

1. **節省 Token**：只在需要時載入 context，避免浪費
2. **防止迷路**：複雜任務有結構化規劃流程
3. **自動化**：減少重複操作，自動載入專案狀態
4. **一致性**：確保 AI 遵循專案規範

### 核心理念

```
先讀文件 → 再讀程式碼 → 最後修改
     ↓
   索引優先於全文搜尋
     ↓
   漸進式精煉，非一次載入全部
```

---

## 檔案結構

### NLWeb 專案級配置

> 專案狀態 / 進度 / 模組總覽 / 編碼規範等動態內容**不放 `.claude/`**，
> 改放 repo 內的 `docs/status.md`、`docs/reference/systemmap.md`、`CLAUDE.md`、`memory/`（見 [Memory System](#memory-system)）。

```
NLWeb\.claude\
├── settings.json           # Hooks 自動化觸發器（官方 object 格式）+ enabledPlugins
├── settings.local.json     # 本機覆寫（不入 git）
├── agents\
│   └── planner.md          # 高層規劃代理（/high-level-plan 觸發）
├── scripts\
│   ├── py-compile-check.py    # PostToolUse hook：.py 編輯後語法檢查
│   └── intern-sync-reminder.py # PostToolUse hook：commit 動到技術碼時提醒同步 intern repo
├── memory\
│   └── compact-state.json  # 歷史殘留檔（compact 計數機制已退役，僅留檔不再使用）
├── evals\
│   ├── zoe-eval.md         # Zoe skill 評測
│   ├── learn-eval.md       # /learn skill 評測
│   ├── zoe-changelog.md
│   └── learn-changelog.md
├── rules\
│   └── token-optimization.md  # Token 節省規則（NLWeb 專用）
└── commands\               # 13 個 slash command（見 Commands 章節）
    ├── index.md            # /index
    ├── search.md           # /search
    ├── status.md           # /status
    ├── learn.md            # /learn（已併入 /update-docs 職責）
    ├── checkpoint.md       # /checkpoint
    ├── high-level-plan.md  # /high-level-plan（取代舊 /plan）
    ├── review-plan.md      # /review-plan
    ├── zoe.md              # /zoe（技術派工 persona）
    ├── rae.md              # /rae（文書 persona）
    ├── delegate.md         # /delegate（智慧派工）
    ├── chub.md             # /chub（Context Hub，第三方 API 文件查詢）
    ├── optimize-skill.md   # /optimize-skill（Meta-Harness skill 優化）
    └── dubao-b2b-proposal.md # /dubao-b2b-proposal（B2B 提案撰寫）
```

> **跨 session memory 不在 `.claude/`**：lesson / 狀態 / 參考資訊存在 repo 根 `memory/`
> （`MEMORY.md` 索引 + `lessons-*.md` / `project_*.md` / `reference_*.md`）。詳見 [Memory System](#memory-system)。

### 全域配置

```
~\.claude\
└── rules\
    └── performance.md      # 模型選擇與效能指南（通用，跨所有專案）
```

> 全域 skills（design skills、superpowers、systematic-debugging 等）由 Claude Code 本身管理，
> 不屬 NLWeb 專案配置，故不在此列。

---

## Hooks 自動化

### 位置
`NLWeb\.claude\settings.json`（`hooks` 欄位）

> **重要**：Claude Code hooks 必須在 `settings.json` 中以 **object 格式** 定義，
> 以事件名稱（如 `PreToolUse`、`PostToolUse`）作為 key。舊版 `hooks.json` 陣列格式已停用。

### 功能說明

現況 `settings.json` 共掛載 **3 個 hook**：

| Hook 事件 | matcher | 觸發時機 | 功能 |
|-----------|---------|----------|------|
| **PreToolUse** | `Grep` | 嘗試使用 Grep 前 | exit 2 阻擋，強制改用 SQLite 索引搜尋 |
| **PostToolUse** | `Edit\|Write` | 編輯/寫入檔案後 | `.py` 檔自動語法檢查（`py-compile-check.py`） |
| **PostToolUse** | `Bash` | 每次 Bash 後 | `git commit` 動到技術碼時提醒同步 intern repo（`intern-sync-reminder.py`，非阻塞 exit 0） |

另有 `enabledPlugins`：`security-guidance@claude-plugins-official`。

> **無 Stop hook**：Stop hook 會造成無限迴圈（hook 輸出 → Claude 回應 → 再次觸發 Stop → 循環），故不使用。
> `/learn` 改為手動執行或由 rules 提醒。
>
> **歷史**：早期版本曾有 `suggest-compact.js`（工具呼叫計數）+ `PreCompact`（重置計數）+ `PostToolUse:TodoWrite`（里程碑偵測）這套「Smart Compacting」自動化。該機制已整套退役，`suggest-compact.js` 已不存在，相關 hook 全部移除。`.claude/memory/compact-state.json` 為殘留檔。

### 實際效果

**嘗試 Grep 時**（PreToolUse，exit code 2 阻擋）：
```
STOP: Use python tools/indexer.py --search instead of Grep.
SQLite FTS5 is faster and saves tokens.
```

**編輯 Python 後**（PostToolUse Edit|Write，py-compile-check.py 讀取 stdin JSON）：
```
# 如果有語法錯誤會顯示：
[Hook] Python 語法錯誤，請修正: SyntaxError: ...
```

**git commit 動到技術碼後**（PostToolUse Bash，intern-sync-reminder.py，非阻塞）：
```
[intern-sync] 這次 commit 動到了 intern repo 也包含的技術碼：
  - code/python/...
intern repo（nlweb-intern）不會自動同步，現在可能落後。
若這些改動 intern 也需要，走 docs/specs/intern-repo-sync-spec.md 同步流程。
```
> 只在 Bash command 含 `git commit` 且本次 commit 觸及技術碼路徑（`code/`、`config/`、`static/`、`docs/specs/`）時才提醒；純 docs/memory commit 安靜。

### 自訂 Hooks

如需新增 hook，編輯 `.claude/settings.json` 的 `hooks` 欄位：

```json
{
  "hooks": {
    "EventName": [
      {
        "matcher": "ToolPattern",
        "hooks": [
          {
            "type": "command",
            "command": "your-command-here"
          }
        ]
      }
    ]
  }
}
```

**事件名稱**：`PreToolUse`、`PostToolUse`、`Stop`、`SessionStart`、`SessionEnd` 等

**matcher**：工具名稱（正則表達式），如 `"Edit|Write"`、`"Bash"`、`"Grep"`、`""` 匹配所有工具

**hook 類型**：
- `"command"` — 執行 shell 命令，exit code 2 阻擋並回饋 stderr 給 Claude
- `"prompt"` — 透過 Haiku LLM 評估，回傳 `{"ok": true/false}` 決策

**Windows 注意事項**：
- `$CLAUDE_PROJECT_DIR` 在 Windows CMD 不展開。現況 `py-compile-check.py` / `intern-sync-reminder.py` 兩個 hook **直接寫絕對路徑**（`python C:/users/user/nlweb/.claude/scripts/...`）以求穩定；若要可攜，改相對路徑（`.claude/scripts/...`），勿用 `$CLAUDE_PROJECT_DIR`。
- 不可使用 `|| true`（`true` 不是 Windows CMD 指令）
- 不可使用 `2>/dev/null` → 移除或改用 `2>NUL`
- `echo` 不需要單引號（CMD 會把引號當作輸出內容）
- **不可使用 Stop hook** — 任何輸出/error 都會觸發 Claude 回應，形成無限迴圈

> 參考：[Claude Code Hooks 官方文件](https://code.claude.com/docs/en/hooks)

---

## Memory System

### 概述

Memory System 讓 Claude 能跨 session 累積專案知識。當解決非平凡問題時，手動執行 `/learn` 記錄到 repo 根的 `memory/`，供未來參考。

> **路徑**：lesson / 狀態 / 參考資訊存在 **repo 根 `memory/`**（不在 `.claude/memory/`）。
> `memory/MEMORY.md` 是**純索引**，實質內容分散在主題檔。詳見 `CLAUDE.md` 的「Memory 更新規則」。

```
┌─────────────────────────────────────────┐
│  解決非平凡問題後                        │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│  /learn 指令                            │
│  分析對話 → 分類 → 評估信心 → 寫入       │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│  memory/lessons-*.md（依主題分檔）      │
│  + memory/MEMORY.md 加一行索引指標       │
└─────────────────────────────────────────┘
```

### 檔案位置

| 檔案 | 說明 |
|------|------|
| `memory/MEMORY.md` | **純索引**：File Index，每條 lesson/狀態檔一行指標 |
| `memory/lessons-*.md` | 技術教訓（依主題分檔，如 `lessons-frontend.md`、`lessons-db.md`、`lessons-crawler.md`…） |
| `memory/project_*.md` | 專案狀態 |
| `memory/reference_*.md` | 參考資訊 |
| `memory/feedback_*.md` | CEO 協作回饋 |
| `.claude/commands/learn.md` | /learn 指令定義 |

> **禁止**將實質內容（錯誤教訓、開發細節、狀態數字）直接寫進 `MEMORY.md` —— 它只是索引。
> 內容寫到對應主題檔，再於 `MEMORY.md` File Index 加一行指標。

### 內容分類（寫到哪個檔）

| 類型 | 檔名前綴 | 範例 |
|------|----------|------|
| 技術教訓 | `lessons-*` | `lessons-auth.md`、`lessons-infra-deploy.md` |
| 專案狀態 | `project_*` | `project_data_status.md`、`project_infra_vps.md` |
| 參考資訊 | `reference_*` | `reference_cloud_routines.md`、`reference_brand_spec.md` |
| 協作回饋 | `feedback_*` | `feedback_smoke_test.md`、`feedback_e2e_testing.md` |

### 信心等級

| 等級 | 條件 |
|------|------|
| **低** | 第一次遇到，解法可能不完整 |
| **中** | 解決過 2-3 次，或有文件佐證 |
| **高** | 多次驗證，確定有效 |

### 使用方式

**手動**：完成功能開發 / 架構變更後，隨時執行 `/learn` 記錄當前對話的 lesson（無自動提醒 hook）

### 記錄條件

**值得記錄**：
- 解決了非顯而易見的 bug
- 發現了框架/套件的陷阱
- 找到了效能優化方法
- 踩過的坑（避免下次再犯）

**不記錄**：
- 瑣碎修復（typo、格式）
- 一次性問題
- 尚未驗證的假設

---

## Planner Agent

### 位置
`NLWeb\.claude\agents\planner.md`

### 使用時機

- 新功能開發（跨多個檔案）
- 重大架構變更
- 複雜重構
- 不確定從何開始時

### 觸發方式

```
/high-level-plan 實作 XXX 功能
```

或用「規劃」「高層計劃」「先想想」等觸發詞。不適用於已明確知道要改哪些檔案的小修改。

> 舊指令 `/plan` 已淘汰，由 `/high-level-plan` 承接（對應 `.claude/agents/planner.md`）。

### 輸出格式

Planner 會輸出：

```markdown
### 需求摘要
[一句話描述]

### 影響模組
| 模組 | 狀態 | 需要修改的檔案 |
|------|------|----------------|
| M4: Reasoning | 🟢 完成 | `reasoning/orchestrator.py` |

### 實作步驟
1. **[步驟名稱]**
   - 檔案：`具體路徑`
   - 修改：具體內容
   - 驗證：如何確認

### 風險與依賴
- [潛在風險]

### 預估複雜度
- [x] 中等（2-5 個檔案）
```

### 重要規則

- Planner **不寫程式碼**，只輸出計劃
- 必須等使用者確認後才開始實作
- 必須引用具體檔案路徑（不可模糊）

---

## Commands 快捷指令

> 現況 `.claude/commands/` 共 13 個 slash command。以下分核心開發指令與 persona / 派工指令兩組說明。

### /high-level-plan

**用途**：啟動 Planner Agent 做高層架構規劃（不寫程式碼）

**語法**：
```
/high-level-plan 實作用戶上傳功能
/high-level-plan 優化 Ranking 效能
```

**流程**：
1. 讀取 `docs/reference/systemmap.md` 了解模組
2. 讀取 `docs/status.md` 了解目前狀態
3. 輸出結構化計劃
4. 等待確認

> 取代舊 `/plan`。不適用於已明確知道要改哪些檔案的小修改。

---

### /index

**用途**：重建程式碼索引

**語法**：
```
/index
```

**執行**：
```bash
python tools/indexer.py --index
```

**使用時機**：
- 大量檔案修改後
- 新增模組後
- 搜尋結果不準確時

---

### /search

**用途**：使用索引搜尋程式碼

**語法**：
```
/search orchestrator
/search "gap detection"
```

**執行**：
```bash
python tools/indexer.py --search "關鍵字"
```

**優點**：
- SQLite FTS5 全文搜尋
- 結果精準且排序
- 比 Grep 節省大量 Token

---

### /status

**用途**：顯示專案狀態摘要

**語法**：
```
/status
```

**輸出**：
```
=== NLWeb 專案狀態 ===

目前重點：Production 優化

模組狀態：
- M0 Indexing: 🔴 規劃中
- M1 Input: 🟡 部分完成
- M2 Retrieval: 🟡 部分完成
- M3 Ranking: 🟢 完成
- M4 Reasoning: 🟢 完成
- M5 Output: 🟡 部分完成
- M6 Infrastructure: 🟢 完成

下一步：
1. [項目 1]
2. [項目 2]
```

---

### /learn

**用途**：記錄本次對話學到的 lesson（Part A）+ 同步專案文件與程式碼一致性（Part B，原 `/update-docs` 職責，2026-06-15 併入）

**語法**：
```
/learn          # 記錄 lesson + 同步受影響範圍的文件
/learn specs    # 全量掃描所有 spec（**/*.md）並更新
```

**流程**：
1. 分析對話，尋找非平凡問題的解決方案
2. 判斷是否值得記錄
3. 分類到對應領域
4. 評估信心等級
5. 追加到對應的 `memory/lessons-*.md` + 於 `memory/MEMORY.md` 加一行索引
6.（Part B）掃描受影響的 spec/文件，同步至最新程式碼狀態（`/learn specs` 則全量掃 `**/*.md`）

**輸出範例**：
```
=== /learn 執行結果 ===

分析本次對話...

找到 1 個值得記錄的 lesson：

1. **Async Queue Race Condition**
   - 領域：Infrastructure
   - 信心：高
   - 已寫入 memory/lessons-infra-deploy.md + MEMORY.md 索引

本次對話的 lesson 已記錄完成。
```

**觸發時機**：
- 手動：隨時執行
- 自動：Session 結束時 hook 會提醒

---

### /checkpoint

**用途**：建立工作檢查點（git stash + 狀態保存）

**語法**：
```
/checkpoint
```

**執行內容**：
1. 檢查 git 狀態
2. 建立 git stash（如有變更）
3. 更新 `docs/status.md` 的目前狀態

**輸出範例**：
```
=== Checkpoint 建立完成 ===

時間：2026-01-28 15:30
Git Stash：stash@{0} "checkpoint: 實作 M0 - 20260128-1530"

已保存：
- 目前狀態記錄到 docs/status.md
```

**使用時機**：
- 切換任務前
- 完成里程碑後
- 開始實驗性修改前

---

### /update-docs（已合併進 /learn，2026-06-15）

`/update-docs` 已淘汰。其「同步專案文件與程式碼一致性」的職責已併入 `/learn`（spec 掃描+更新為 /learn 的 Part B）：

- 一般「更新文件 / 同步文件」→ 直接用 `/learn`（只處理受影響範圍）
- 全量掃描所有 spec（`**/*.md`）→ 用 `/learn specs`

詳見上方 [`/learn`](#learn) 段落。

---

### /review-plan

**用途**：Review 和驗證 implementation plan 的品質（對照 8 軸向判定能否安全進入 implementation）

**使用時機**：subagent 寫完 plan、自己寫完 plan 要 double check、從 plan 進入 implementation 之前。
不適用於 code review（用 superpowers requesting-code-review）、執行 plan、只讀 plan。

---

### Persona / 派工指令

讀豹採 persona-based 派工體系。以下指令啟動或驅動不同角色：

| 指令 | 角色 / 用途 |
|------|------------|
| `/zoe` | 技術派工 persona（CEO 輸入 `/zoe` 啟動 session；不在 session 中不自動切換） |
| `/rae` | 文書 / 行政 persona（`/rae` 啟動；技術問題改用 `/zoe`） |
| `/delegate` | 智慧派工：分析 CEO 指令 → 收集上下文 → 選正確 skill / agent 執行。非 Zoe session 中使用 |

> **delegate vs zoe**：`/zoe` session 中 Zoe 直接用 Agent tool 派工（不另調 `/delegate`，否則覆蓋人格）。
> `/delegate` 是非 Zoe session 的獨立派工指令。

---

### /chub（Context Hub）

**用途**：透過 context-hub MCP server 查詢第三方 API 文件，避免依賴訓練資料的過時知識

**適用**：openai / anthropic SDK、qdrant-client、redis、stripe、firebase 等 50+ 服務
**不適用**：專案內部程式碼（用 `/search`）、專案架構（讀 `docs/reference/systemmap.md`）

---

### /optimize-skill

**用途**：Meta-Harness proposer — 分析 skill 的執行歷史（traces + evals），提出具體的 skill prompt 修改

**語法**：`/optimize-skill [skill-name]`（預設 `zoe`）

> 評測資料在 `.claude/evals/`（`zoe-eval.md` / `learn-eval.md` + changelog）。

---

### /dubao-b2b-proposal

**用途**：為臺灣讀豹撰寫 B2B 銷售提案（Challenger Sale 框架，三產業版本）

**不適用**：投資人 pitch deck、政府標案、RFP 回應、cold email、定價談判。

---

## Rules 規則系統

### Token 優化規則

**位置**：`NLWeb\.claude\rules\token-optimization.md`

**核心規則**：

| 規則 | 說明 |
|------|------|
| 搜尋用索引 | 禁止 Grep，必須用 `tools/indexer.py`（hook 已 enforce） |
| 先讀文件 | 修改前先讀對應的 spec / `docs/reference/systemmap.md` |
| 漸進式讀取 | 設計文件 → 模組總覽 → 具體程式碼 |
| 限制讀取量 | 單次最多 3 個檔案，大檔分段讀 |

**模組對應表**（現行路徑，舊 `docs/algo/*` 已淘汰）：

| 要修改 | 先讀 |
|--------|------|
| Reasoning | `docs/reference/systemmap.md` Reasoning 章節 |
| Ranking | `docs/specs/bm25-spec.md`、`docs/specs/xgboost-spec.md`、`docs/specs/mmr-spec.md` |
| 查詢分析 | `docs/reference/systemmap.md` Pre-Retrieval 章節 |
| API | `docs/reference/api-endpoints.md` |
| 資料流 | `docs/reference/systemmap.md` Data Flow 章節 |

---

### 效能規則（全域）

**位置**：`~\.claude\rules\performance.md`

**模型選擇**：

| 模型 | 適用場景 |
|------|----------|
| **Haiku** | 單檔案修改、格式化、簡單問答 |
| **Sonnet** | 日常開發、2-5 檔案修改、審查 |
| **Opus** | 架構設計、複雜推論、困難 debug |

**Context 管理**：

| 使用率 | 狀態 | 建議 |
|--------|------|------|
| < 60% | 🟢 | 正常工作 |
| 60-80% | 🟡 | 避免大量讀取 |
| > 80% | 🔴 | 總結後開新對話 |

---

## 驗證測試結果

> 反映現況 `.claude/settings.json` 配置（Smart Compacting 機制已退役）。

### py-compile-check.py

| 測試項目 | 輸入 | 結果 |
|----------|------|------|
| 有效 Python | `{"tool_input":{"file_path":"...pipeline.py"}}` | exit code 0，無輸出 |
| 非 Python 檔案 | `{"tool_input":{"file_path":"...README.md"}}` | exit code 0，跳過不檢查 |
| 語法錯誤 Python | `{"tool_input":{"file_path":"...bad.py"}}` | exit code 2，stderr 輸出錯誤訊息 |
| stdin JSON 解析 | 標準 Claude Code hook 格式 | 正確解析 `tool_input.file_path` |

### settings.json Hooks 結構（現況）

| Event 名稱 | Matcher | 腳本 / 行為 |
|-------------|---------|------------|
| **PreToolUse** | `Grep` | command：echo + exit 2 阻擋 |
| **PostToolUse** | `Edit\|Write` | `py-compile-check.py`（.py 語法檢查） |
| **PostToolUse** | `Bash` | `intern-sync-reminder.py`（commit 動技術碼時提醒，非阻塞） |

另有 `enabledPlugins`：`security-guidance@claude-plugins-official`。

> 無 Stop hook、無 PreCompact、無 TodoWrite milestone（Smart Compacting 已退役）。
> hook 路徑現況為絕對路徑（`C:/users/user/nlweb/.claude/scripts/...`）。

### Slash Commands（現況 13 個）

| 指令 | 檔案 |
|------|------|
| `/index` | `commands/index.md` |
| `/search` | `commands/search.md` |
| `/status` | `commands/status.md` |
| `/learn` | `commands/learn.md` |
| `/checkpoint` | `commands/checkpoint.md` |
| `/high-level-plan` | `commands/high-level-plan.md` |
| `/review-plan` | `commands/review-plan.md` |
| `/zoe` | `commands/zoe.md` |
| `/rae` | `commands/rae.md` |
| `/delegate` | `commands/delegate.md` |
| `/chub` | `commands/chub.md` |
| `/optimize-skill` | `commands/optimize-skill.md` |
| `/dubao-b2b-proposal` | `commands/dubao-b2b-proposal.md` |

> 舊 `/plan`（`commands/plan.md`）已淘汰，由 `/high-level-plan` 承接。

### 即時觸發行為（hooks 生效後）

| 使用者操作 | 自動觸發 | 預期效果 |
|------------|----------|----------|
| 使用 Grep | PreToolUse hook | exit 2 阻擋，強制用 SQLite indexer |
| 編輯 .py 檔案 | py-compile-check.py | 語法錯誤時 exit 2 阻擋 |
| `git commit`（Bash） | intern-sync-reminder.py | 動到技術碼時提醒同步 intern repo（不擋 commit） |

### 踩過的坑（Windows + Claude Code Hooks）

> 以下為實際部署中發現的問題，記錄供未來參考。

| 問題 | 症狀 | 原因 | 修正 |
|------|------|------|------|
| **Stop hook 無限迴圈** | Session 結束後 Claude 不斷回應，無法停止 | Stop hook error → Claude 回應 → 再次觸發 Stop | **移除所有 Stop hooks** |
| **Rate limit 無限迴圈** | 遇到 rate limit 時 hook 持續觸發 | 同上機制在 rate limit 狀態放大 | 移除 Stop hooks 後解決 |
| **`$CLAUDE_PROJECT_DIR` 不展開** | hook 找不到腳本，顯示 path error | Windows CMD 不認識 `$VAR` 語法 | 改用相對路徑（`.claude/scripts/...`） |
| **`\|\| true` 報錯** | `'true' 不是內部或外部命令` | `true` 是 Unix 指令，Windows CMD 無此命令 | 移除，讓腳本自行處理錯誤 |
| **`2>/dev/null` 無效** | stderr 未被抑制 | Windows CMD 使用 `NUL` 非 `/dev/null` | 移除 stderr 重導向 |
| **echo 單引號** | 輸出包含引號字元 | CMD 不解析單引號，原樣輸出 | 移除引號 |
| **prompt hook `ok: false`** | 顯示為 "hook error" | Haiku 回傳 false 時 Claude Code 視為 error | 改用 command hook 或移除 |

---

## 維護指南

### 日常維護

| 檔案 | 頻率 | 內容 |
|------|------|------|
| `docs/status.md` | 每週 | 更新目前重點、最近完成、下一步 |
| `docs/decisions.md` | 重大決策時 | 記錄決策日誌 |
| `memory/lessons-*.md` | 解決非平凡問題後（`/learn`） | 記錄教訓 + `MEMORY.md` 加索引 |

### 定期維護

| 檔案 | 頻率 | 內容 |
|------|------|------|
| `docs/reference/systemmap.md` | 新增/修改模組時 | 更新狀態表、Data Flow |
| `docs/specs/*-spec.md` | 修改核心演算法後 | 同步文件與程式碼（用 `/learn`） |
| 索引 (`/index`) | 大量修改後 | 重建搜尋索引 |

### 月度檢查清單

- [ ] `docs/reference/systemmap.md` 的模組狀態是否正確？
- [ ] `docs/status.md` 的「目前重點」是否過時？
- [ ] `CLAUDE.md` / `.claude/rules/` 是否需要新增規則？
- [ ] `docs/specs/*-spec.md` 是否與程式碼一致？
- [ ] 索引是否包含所有新增的檔案？

---

## 常見問題

### Q: Hooks 沒有生效？

**檢查**：
1. 確認 hooks 定義在 `.claude/settings.json`（非 `hooks.json`）
2. 確認使用 **object 格式**（event name 為 key），非 array 格式
3. 確認 matcher 工具名稱大小寫正確（如 `Grep` 非 `grep`）
4. 確認在 NLWeb 目錄下啟動 Claude Code
5. 重新啟動 Claude Code session（hooks 在啟動時載入快照）
6. 使用 `claude --debug` 查看 hook 執行細節
7. 使用 `/hooks` 選單確認 hook 已註冊

**常見錯誤**：
- `hooks.json` 使用陣列格式 → 改為 settings.json object 格式
- `"type": "event", "event": "PostToolUse"` → PostToolUse 不是 event，是 hook 事件名稱
- `$CLAUDE_FILE_PATH` → 不存在此環境變數，改用 stdin JSON 的 `tool_input.file_path`
- `$CLAUDE_PROJECT_DIR` → Windows CMD 不展開 `$VAR`，改用相對路徑
- `|| true` → `true` 不是 Windows 指令，直接移除
- `2>/dev/null` → Windows 使用 `2>NUL` 或直接移除
- `echo 'text'` → CMD 會輸出引號，改用 `echo text`
- **Stop hook** → 會造成無限迴圈，不可使用（詳見「踩過的坑」）

---

### Q: /high-level-plan 輸出太簡略？

**解決**：
1. 提供更詳細的需求描述
2. 明確指出涉及的模組或功能
3. 說明預期的輸出或行為

---

### Q: 搜尋結果不準確？

**解決**：
1. 執行 `/index` 重建索引
2. 嘗試不同的關鍵字
3. 使用引號包含多字詞：`/search "gap detection"`

---

### Q: Context 使用率太高？

**解決**：
1. 總結目前進度到 `docs/status.md`
2. 用 `/learn` 記錄已產生的 lesson 到 `memory/`
3. 開新對話繼續
4. 避免一次讀取多個大檔案

---

### Q: 如何新增自訂 Command？

在 `NLWeb\.claude\commands\` 新增 `.md` 檔案：

```markdown
---
description: 指令描述
---

# /指令名稱

說明這個指令做什麼。

## 執行步驟
1. 步驟一
2. 步驟二

## 使用時機
- 情況一
- 情況二
```

---

## 參考資源

- [everything-claude-code](https://github.com/affaan-m/everything-claude-code) - 原始配置庫
- `CLAUDE.md` - 專案指引（黃金法則、開發紀律、Memory 更新規則）
- `docs/reference/systemmap.md` - NLWeb 模組總覽
- `docs/status.md` - 專案狀態 / 目前進度
- `.claude/rules/token-optimization.md` - Token 優化規則

---

*建立日期：2026-01-27*
*最後更新：2026-01-29（移除 Stop hook、修正 Windows 相容性、新增踩坑記錄、新增 /update-docs 文件、修正硬編碼路徑）*
*2026-06-15：update-docs 合併進 /learn*
*2026-06-17：大幅追現況 — 砍 Smart Compacting 整章（suggest-compact.js / PreCompact / TodoWrite milestone 已退役）、`/plan`→`/high-level-plan`、補齊 13 個 commands、更正檔案結構表 / Memory 路徑（`.claude/memory/`→repo `memory/`）/ docs 對應表（`docs/algo/*`→`docs/specs/*-spec.md`）、補 intern-sync-reminder hook*
*基於 everything-claude-code 最佳實踐*
