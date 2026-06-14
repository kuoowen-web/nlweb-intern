# CLAUDE.md

本文件為 Claude Code 提供專案指引。

**黃金法則**：僅實作被要求的功能，不要額外新增功能。避免過度複雜化。

**預設直接執行**：指令明確時直接動手，不要事前反問確認。遇到歧義，選最合理預設值繼續，事後在回報中標記所做假設。只有在「選錯方向成本很高且無法事後修正」（動 prod / 燒真實 LLM 錢 / 不可逆操作）時才停下來先給 plan 等點頭。

---

## 專案概述

**臺灣讀豹**（Taiwan Dubao）— 新聞網站自然語言搜尋系統。目標：可信、準確、邏輯嚴謹的搜尋與推論。

詳細架構：`docs/reference/systemmap.md`

---

## 文件 Entry Point

| 主題               | 文件                            |
| ---------------- | ----------------------------- |
| 專案狀態 / 目前進度      | `docs/status.md`              |
| 決策日誌             | `docs/decisions.md`           |
| 系統總覽與模組對應        | `docs/reference/systemmap.md` |
| 跨 session memory | `memory/MEMORY.md`            |
| 演算法 spec         | `docs/specs/*-spec.md`        |

---

## 程式碼搜尋（強制使用 indexer）

**規定**：搜尋程式碼**必須**使用 SQLite FTS5 索引，**禁止** Grep。

```bash
python tools/indexer.py --index      # 開始工作時 / 大量修改後
python tools/indexer.py --search "關鍵字"
```

**為什麼**：毫秒級搜尋 + 減少 token 消耗。Hook 已 enforce。

**例外**：只有索引系統失敗時可改用 Grep。詳見 `docs/specs/code-in-sqlite.md`。

---

## 開發紀律

### Debug 先讀 Memory

被要求 debug 或診斷問題時，必須先讀 `memory/MEMORY.md`（索引）→ 對應模組 `lessons-*.md`。許多 bug 有重複 pattern，先讀可避免重複踩坑。

### **寫Handoff Prompt時直接輸出到Terminal**

寫Handoff給其他Agent/Session時，除非明確被要求，否則不要新產生檔案。

### 不可 Silent Fail

讓錯誤情況自然浮現，不可 silently catch errors/exceptions。可優雅降級，但必須有明確訊息表示已被降級。絕對不可讓錯誤被無視。

### Smoke Test Gate

修改 Python 程式碼後必須 `cd code/python && python tools/smoke_test.py`。FAILED 立即修復。例外：只改 docs/ / memory/ / config / static/。詳見 `memory/delegation-patterns.md`。

### E2E Gate

Unit test + smoke test 通過 ≠ 完成。Pipeline：`Unit → Smoke → Agent E2E (DevTools) → CEO 人工 E2E → Pass = 完成`。例外：只改文件 / config / 無法前端觸發的純後端邏輯。詳見 `memory/delegation-patterns.md`。

### 絕對禁止 Reward Hack

必須尋求全面性解決方案。從系統角度思考：上下游模組如何受影響？依賴關係？命名是否一致？不要在發現第一個問題就停下 — 多數情況需要多處修正，一次修復全部。

### 推測 ≠ 結論

所有推測標為**假說** + 列**驗偽計畫**，驗偽後才升級為結論。常被忽略的驗偽手段：

1. **環境驗證**：CEO 報「我重啟了」必須 `tasklist | grep python` 驗 PID 變了 + CPU time 重置，不可信任口頭確認
2. **對照實驗**：假設 X-specific 前用其他 X' 驗對照
3. **Small test round-trip**：寫長 script 含特殊內容（中文 / unicode / JSON）前先 ~3 行 small test 驗讀寫一致

### 跨日 / 跨環境接手必驗 server alignment

懷疑「環境 vs code 對齊」時，先驗 server start time 比對 latest schema migration timestamp + git log。Server 早於最新 migration → restart 是 first hypothesis，不要先猜密碼 / 帳號狀態。

### 不要估計時間 — AI 不懂時間

軟體工作時間估計常常錯，CEO 偏好結果導向（做完什麼）而非時程導向（要多久）。

- Briefing / alignment 不問「今天能投入幾小時」「估 1-2 天」
- 進度回報用「目前在 X / 下一步 Y」事實描述，不講「再 N 分鐘」
- Plan 文件 `estimated effort` 段是 CEO trade-off 用設計資訊，不是執行時程承諾
- Path comparison / scope estimate 不含「日曆 deadline / 時序壓力」維度
- Work-effort 用 commits 數 / 行數度量，非日曆度量

### 清理臨時檔案

完成任務後刪除所有為了迭代建立的臨時檔案、腳本、輔助檔案。

### Python 3.11

使用 Python 3.11（非 3.13）。多個依賴套件尚未支援 3.13。

### 程式碼風格

- 優先編輯既有檔案而非建立新檔案
- 實作前先檢查鄰近檔案的 pattern
- 除非明確要求，否則不使用 emoji

---

## 元規則

### CLAUDE.md Stability (Prompt Cache)

Claude Code caches CLAUDE.md at org level。每次 edit break cache 整個 prompt 重算費用。Skills 和 memory 在 dynamic 區不影響 cache。

- **每週最多 1 edit** to CLAUDE.md。Batch small changes
- 動態內容（status / progress / current work）放 `docs/status.md`，永不放 CLAUDE.md
- CLAUDE.md 只放穩定 rule + 結構性資訊
- Skills (`.agents/skills/`) 和 memory (`memory/`) 自由編輯（dynamic 區）

### Memory 更新規則

**禁止**將實質內容（錯誤教訓、開發細節、狀態數字）直接寫入 `MEMORY.md`。`MEMORY.md` 是純索引。

- 新技術教訓 → `memory/lessons-*.md`
- 新專案狀態 → `memory/project_*.md`
- 新參考資訊 → `memory/reference_*.md`
- 然後 `MEMORY.md` File Index 加一行指標
