# Claude Code 配置使用手冊

> 基於 [everything-claude-code](https://github.com/affaan-m/everything-claude-code) 最佳實踐，針對 NLWeb 專案客製化的配置系統

---

## 目錄

1. [概述](#概述)
2. [檔案結構](#檔案結構)
3. [Hooks 自動化](#hooks-自動化)
4. [Memory System](#memory-system)
5. [Smart Compacting](#smart-compacting)
6. [Planner Agent](#planner-agent)
7. [Commands 快捷指令](#commands-快捷指令)
8. [Rules 規則系統](#rules-規則系統)
9. [驗證測試結果](#驗證測試結果)
10. [維護指南](#維護指南)
11. [常見問題](#常見問題)

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

```
NLWeb\.claude\
├── CONTEXT.md              # 目前工作狀態（手動維護）
├── PROGRESS.md             # 里程碑記錄（手動維護）
├── NEXT_STEPS.md           # 計劃清單（手動維護）
├── systemmap.md            # M0-M6 模組總覽（手動維護）
├── codingrules.md          # 編碼規範（手動維護）
├── settings.json           # 🆕 Hooks 自動化觸發器（官方 object 格式）
├── hooks.json.bak          # 舊格式備份（已停用）
├── agents\
│   └── planner.md          # 🆕 任務規劃代理
├── memory\
│   ├── lessons-learned.md  # 🆕 累積的專案知識
│   ├── checkpoints.log     # 🆕 檢查點記錄
│   └── compact-state.json  # 🆕 Compact 計數狀態（自動產生）
├── scripts\
│   ├── suggest-compact.js  # 🆕 工具呼叫計數腳本
│   └── py-compile-check.py # 🆕 Python 語法檢查 hook 腳本
├── rules\
│   └── token-optimization.md  # 🆕 Token 節省規則
└── commands\
    ├── plan.md             # 🆕 /plan 指令
    ├── index.md            # 🆕 /index 指令
    ├── search.md           # 🆕 /search 指令
    ├── status.md           # 🆕 /status 指令
    ├── learn.md            # 🆕 /learn 指令
    ├── checkpoint.md       # 🆕 /checkpoint 指令
    └── update-docs\        # 🆕 /update-docs 技能（多檔案指令）
        └── SKILL.md
```

### 全域配置

```
~\.claude\
├── rules\
│   └── performance.md      # 🆕 模型選擇與效能指南
└── skills\
    ├── systematic-debugging\   # 除錯技能（已有）
    └── code-reviewer\          # 審查技能（已有）
```

---

## Hooks 自動化

### 位置
`NLWeb\.claude\settings.json`（`hooks` 欄位）

> **重要**：Claude Code hooks 必須在 `settings.json` 中以 **object 格式** 定義，
> 以事件名稱（如 `PostToolUse`、`Stop`）作為 key。舊版 `hooks.json` 陣列格式已停用。

### 功能說明

| Hook 事件 | 觸發時機 | 功能 |
|-----------|----------|------|
| **PreCompact** | 系統 compact 前 | 重置 compact 計數器 |
| **PreToolUse: Grep** | 嘗試使用 Grep 前 | 阻擋並強制使用 SQLite 索引搜尋 |
| **PostToolUse: (all)** | 每次工具呼叫後 | 執行 suggest-compact.js 計數 |
| **PostToolUse: Edit/Write** | 編輯/寫入檔案後 | .py 檔自動語法檢查 |
| **PostToolUse: TodoWrite** | 更新任務狀態後 | 追蹤里程碑完成數 |

> **⚠ Stop hook 已移除**：Stop hook 會造成無限迴圈（hook 輸出 → Claude 回應 → 再次觸發 Stop → 循環）。
> `/learn` 改為手動執行或由 rules 提醒。

### 實際效果

**嘗試 Grep 時**（PreToolUse，exit code 2 阻擋）：
```
STOP: Use python tools/indexer.py --search instead of Grep.
SQLite FTS5 is faster and saves tokens.
```

**編輯 Python 後**（PostToolUse，py-compile-check.py 讀取 stdin JSON）：
```
# 如果有語法錯誤會顯示：
[Hook] Python 語法錯誤，請修正: SyntaxError: ...
```

**達 50 次工具呼叫時**（PostToolUse wildcard）：
```
╔══════════════════════════════════════════════════════════════╗
║  COMPACT 建議 - 已達 50 次工具呼叫                            ║
║  如果現在是好時機，請執行 /compact                           ║
╚══════════════════════════════════════════════════════════════╝
```

**完成 3 個里程碑時**（PostToolUse: TodoWrite）：
```
╔══════════════════════════════════════════════════════════════╗
║  里程碑完成！COMPACT 建議                                     ║
║  已完成 3 個任務，這是保存進度的好時機                       ║
╚══════════════════════════════════════════════════════════════╝
```

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

**事件名稱**：`PreToolUse`、`PostToolUse`、`Stop`、`PreCompact`、`SessionStart`、`SessionEnd` 等

**matcher**：工具名稱（正則表達式），如 `"Edit|Write"`、`"Bash"`、`""` 匹配所有工具

**hook 類型**：
- `"command"` — 執行 shell 命令，exit code 2 阻擋並回饋 stderr 給 Claude
- `"prompt"` — 透過 Haiku LLM 評估，回傳 `{"ok": true/false}` 決策

**Windows 注意事項**：
- `$CLAUDE_PROJECT_DIR` 在 Windows CMD 不展開 → 使用相對路徑（如 `.claude/scripts/...`）
- 不可使用 `|| true`（`true` 不是 Windows CMD 指令）
- 不可使用 `2>/dev/null` → 移除或改用 `2>NUL`
- `echo` 不需要單引號（CMD 會把引號當作輸出內容）
- **不可使用 Stop hook** — 任何輸出/error 都會觸發 Claude 回應，形成無限迴圈

> 參考：[Claude Code Hooks 官方文件](https://code.claude.com/docs/en/hooks)

---

## Memory System

### 概述

Memory System 讓 Claude 能跨 session 累積專案知識。當解決非平凡問題時，自動或手動記錄到 `lessons-learned.md`，供未來參考。

```
┌─────────────────────────────────────────┐
│  Session 結束                           │
│  Hook 提醒："有 lesson 要記錄嗎？"        │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│  /learn 指令                            │
│  分析對話 → 分類 → 評估信心 → 寫入       │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│  lessons-learned.md                     │
│  累積的專案知識，依領域分類              │
└─────────────────────────────────────────┘
```

### 檔案位置

| 檔案 | 說明 |
|------|------|
| `.claude/memory/lessons-learned.md` | 儲存累積的 lessons |
| `.claude/commands/learn.md` | /learn 指令定義 |

### Lesson 格式

每個 lesson 包含：

```markdown
### [簡短標題]
**問題**：遇到什麼問題
**解決方案**：如何解決
**信心**：低/中/高
**檔案**：`相關檔案路徑`
**日期**：YYYY-MM
```

### 領域分類

| 領域 | 涵蓋內容 |
|------|----------|
| **Reasoning** | orchestrator、agents |
| **Ranking** | ranking、xgboost、mmr |
| **Retrieval** | retriever、bm25、qdrant |
| **API / Frontend** | webserver、static、SSE |
| **Infrastructure** | DB、cache、config |
| **開發環境 / 工具** | Python、套件、開發流程 |

### 信心等級

| 等級 | 條件 |
|------|------|
| **低** | 第一次遇到，解法可能不完整 |
| **中** | 解決過 2-3 次，或有文件佐證 |
| **高** | 多次驗證，確定有效 |

### 使用方式

**自動**：Session 結束時，hook 會提醒執行 `/learn`

**手動**：隨時執行 `/learn` 記錄當前對話的 lesson

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

## Smart Compacting

### 概述

Smart Compacting 解決 Claude Code 自動 compact 的問題：系統會在隨機時間點壓縮 context，可能在任務中途丟失重要資訊。

```
┌─────────────────────────────────────────────────────────────┐
│  問題：自動 compact 發生在任意時間點，丟失關鍵 context       │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  解法：在「邏輯斷點」主動 compact，保留重要狀態             │
└─────────────────────────────────────────────────────────────┘
```

### 運作機制

| 機制 | 觸發時機 | 功能 |
|------|----------|------|
| **suggest-compact.js** | 每次工具呼叫後 | 達 50 次後建議 compact |
| **里程碑偵測** | TodoWrite 呼叫時 | 完成 3 個任務後建議 compact |
| **PreCompact hook** | 系統 compact 前 | 提醒保存狀態 |
| **/checkpoint** | 手動執行 | 建立 git stash + 狀態記錄 |

### 觸發條件

`suggest-compact.js` 追蹤兩種指標：

| 指標 | 門檻 | 說明 |
|------|------|------|
| 工具呼叫次數 | 50 次 | 首次建議，之後每 25 次提醒 |
| 里程碑完成數 | 3 個 | 每完成 3 個 TodoWrite 任務建議 |

**工具呼叫達標時**：
```
╔══════════════════════════════════════════════════════════════╗
║  💡 COMPACT 建議 - 已達 50 次工具呼叫                        ║
║  如果現在是好時機，請執行 /compact                           ║
╚══════════════════════════════════════════════════════════════╝
```

**里程碑完成時**：
```
╔══════════════════════════════════════════════════════════════╗
║  🎯 里程碑完成！COMPACT 建議                                 ║
╠══════════════════════════════════════════════════════════════╣
║  已完成 3 個任務（本次 session）                             ║
║                                                              ║
║  完成里程碑是執行 /compact 的好時機：                        ║
║  • 保存目前進度到 CONTEXT.md                                 ║
║  • 執行 /learn 記錄學到的 lessons                            ║
║  • 執行 /checkpoint 建立檢查點                               ║
╚══════════════════════════════════════════════════════════════╝
```

### PreCompact Hook

當系統準備 compact 時，hook 會提醒：

1. 更新 `.claude/CONTEXT.md`
2. 執行 `/learn` 記錄 lessons
3. 確認 TodoWrite 已更新
4. 執行 `/checkpoint`（可選）

### 建議的 Compact 時機

| 時機 | 說明 |
|------|------|
| **探索 → 執行** | 研究完成，開始實作前 |
| **里程碑完成** | 功能完成，開始下一個前 |
| **除錯完成** | Bug 修復後 |
| **切換領域** | 從 Ranking 切到 Retrieval 等 |

### 檔案位置

| 檔案 | 說明 |
|------|------|
| `.claude/scripts/suggest-compact.js` | 計數腳本 |
| `.claude/memory/compact-state.json` | 計數狀態（自動產生） |
| `.claude/memory/checkpoints.log` | 檢查點記錄 |
| `.claude/commands/checkpoint.md` | /checkpoint 指令 |

### 手動操作

**查看計數狀態**：
```bash
node .claude/scripts/suggest-compact.js --status
```

**重置計數器**：
```bash
node .claude/scripts/suggest-compact.js --reset
```

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
/plan 實作 XXX 功能
```

或直接描述需求，Claude 會自動判斷是否需要規劃。

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

### /plan

**用途**：啟動 Planner Agent 規劃任務

**語法**：
```
/plan 實作用戶上傳功能
/plan 優化 Ranking 效能
```

**流程**：
1. 讀取 systemmap.md 了解模組
2. 讀取 CONTEXT.md 了解目前狀態
3. 輸出結構化計劃
4. 等待確認

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

**用途**：記錄本次對話學到的 lesson

**語法**：
```
/learn
```

**流程**：
1. 分析對話，尋找非平凡問題的解決方案
2. 判斷是否值得記錄
3. 分類到對應領域
4. 評估信心等級
5. 追加到 `lessons-learned.md`

**輸出範例**：
```
=== /learn 執行結果 ===

分析本次對話...

找到 1 個值得記錄的 lesson：

1. **Async Queue Race Condition**
   - 領域：Infrastructure
   - 信心：高
   - 已寫入 lessons-learned.md

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
3. 記錄狀態到 `checkpoints.log`
4. 更新 `CONTEXT.md`
5. 重置 compact 計數器

**輸出範例**：
```
=== Checkpoint 建立完成 ===

時間：2026-01-28 15:30
Git Stash：stash@{0} "checkpoint: 實作 M0 - 20260128-1530"

已保存：
- ✅ 目前狀態記錄到 checkpoints.log
- ✅ CONTEXT.md 已更新
- ✅ Compact 計數器已重置
```

**使用時機**：
- Compact 前保存狀態
- 切換任務前
- 完成里程碑後
- 開始實驗性修改前

---

### /update-docs

**用途**：同步專案文件與程式碼的一致性

**語法**：
```
/update-docs
/update-docs all
/update-docs progress
/update-docs architecture
```

**參數**：

| 參數 | 更新範圍 |
|------|----------|
| `all` 或無參數 | 全部文件 |
| `progress` | CONTEXT.md, PROGRESS.md, COMPLETED_WORK.md, NEXT_STEPS.md |
| `architecture` | systemmap.md, state-machine-diagram.md |

**流程**：
1. 收集資訊（git diff、git log、程式碼結構掃描）
2. 分析變更（模組新增/刪除/重命名、API 變更）
3. 更新對應文件
4. 提醒手動維護 architecture.html
5. 詢問是否 commit

---

## Rules 規則系統

### Token 優化規則

**位置**：`NLWeb\.claude\rules\token-optimization.md`

**核心規則**：

| 規則 | 說明 |
|------|------|
| 搜尋用索引 | 禁止 Grep，必須用 `tools/indexer.py` |
| 先讀文件 | 修改前先讀對應的 `docs/algo/*.md` |
| 漸進式讀取 | 設計文件 → 模組總覽 → 具體程式碼 |
| 限制讀取量 | 單檔案 < 500 行，單次最多 3 個檔案 |

**模組對應表**：

| 要修改 | 先讀 |
|--------|------|
| Reasoning | `docs/algo/reasoning_system.md` |
| Ranking | `docs/algo/ranking_pipeline.md` |
| 查詢分析 | `docs/algo/query_analysis.md` |
| API | `.claude/API_ENDPOINTS.md` |

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

> 2026-01-29 完成全面測試，所有組件運作正常。

### suggest-compact.js

| 測試項目 | 指令 | 結果 |
|----------|------|------|
| 重置計數器 | `--reset` | 所有計數歸零，顯示「計數器已重置」 |
| 查看狀態 | `--status` | 正確顯示 toolCallCount、milestonesCompleted、sessionStart |
| 工具呼叫計數 | （無參數，x5） | 正確累加到 5 |
| 50 次門檻觸發 | （無參數，x50） | 第 50 次顯示 COMPACT 建議框 |
| 里程碑計數 | `--milestone` x3 | 第 3 次觸發里程碑完成建議框 |
| 狀態持久化 | 讀寫 `compact-state.json` | JSON 檔案正確讀寫 |

### py-compile-check.py

| 測試項目 | 輸入 | 結果 |
|----------|------|------|
| 有效 Python | `{"tool_input":{"file_path":"...pipeline.py"}}` | exit code 0，無輸出 |
| 非 Python 檔案 | `{"tool_input":{"file_path":"...README.md"}}` | exit code 0，跳過不檢查 |
| 語法錯誤 Python | `{"tool_input":{"file_path":"...bad.py"}}` | exit code 2，stderr 輸出錯誤訊息 |
| stdin JSON 解析 | 標準 Claude Code hook 格式 | 正確解析 `tool_input.file_path` |

### settings.json Hooks 結構

| Event 名稱 | Hook 數量 | Matcher | 驗證 |
|-------------|-----------|---------|------|
| **PreCompact** | 1 entry, 1 hook | （無 matcher） | command (reset counter) |
| **PreToolUse** | 1 entry, 1 hook | `Grep` | command (exit 2 block) |
| **PostToolUse** | 3 entries | `""` / `Edit\|Write` / `TodoWrite` | 3 個 command hooks |

> Stop hook 已移除（見下方「踩過的坑」）。所有路徑改為相對路徑（不使用 `$CLAUDE_PROJECT_DIR`）。

### Slash Commands

| 指令 | 檔案 | 狀態 |
|------|------|------|
| `/plan` | `commands/plan.md` | 存在且格式正確 |
| `/index` | `commands/index.md` | 存在且格式正確 |
| `/search` | `commands/search.md` | 存在且格式正確 |
| `/status` | `commands/status.md` | 存在且格式正確 |
| `/learn` | `commands/learn.md` | 存在且格式正確 |
| `/checkpoint` | `commands/checkpoint.md` | 存在且格式正確 |
| `/update-docs` | `commands/update-docs/SKILL.md` | 存在且格式正確 |

### 即時觸發行為（hooks 生效後）

| 使用者操作 | 自動觸發 | 預期效果 |
|------------|----------|----------|
| 任何工具呼叫 | suggest-compact.js 計數 | 達 50 次後顯示建議 |
| 編輯 .py 檔案 | py-compile-check.py | 語法錯誤時 exit 2 阻擋 |
| 更新 TodoWrite | suggest-compact.js --milestone | 完成 3 個任務後顯示建議 |
| 使用 Grep | PreToolUse hook | exit 2 阻擋，強制用 SQLite indexer |
| 系統 compact | PreCompact hook | 重置計數器 |

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
| `CONTEXT.md` | 每週 | 更新目前重點、最近完成 |
| `PROGRESS.md` | 完成里程碑時 | 記錄功能、Bug 修復 |
| `NEXT_STEPS.md` | 每週 | 更新計劃清單 |

### 定期維護

| 檔案 | 頻率 | 內容 |
|------|------|------|
| `systemmap.md` | 新增/修改模組時 | 更新狀態表、Data Flow |
| `docs/algo/*.md` | 修改核心演算法後 | 同步文件與程式碼 |
| 索引 (`/index`) | 大量修改後 | 重建搜尋索引 |

### 月度檢查清單

- [ ] `systemmap.md` 的模組狀態是否正確？
- [ ] `CONTEXT.md` 的「目前重點」是否過時？
- [ ] `codingrules.md` 是否需要新增規則？
- [ ] `docs/algo/` 的文件是否與程式碼一致？
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

### Q: /plan 輸出太簡略？

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
1. 總結目前進度到 `PROGRESS.md`
2. 更新 `CONTEXT.md` 的目前狀態
3. 開新對話，讓 hooks 自動載入 context
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
- `.claude/IMPLEMENTATION_PLAN_CLAUDE_CODE_UPGRADE.md` - 實作計劃
- `.claude/systemmap.md` - NLWeb 模組總覽

---

*建立日期：2026-01-27*
*最後更新：2026-01-29（移除 Stop hook、修正 Windows 相容性、新增踩坑記錄、新增 /update-docs 文件、修正硬編碼路徑）*
*基於 everything-claude-code 最佳實踐*
