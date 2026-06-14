# 程式碼索引系統 (Code-in-SQLite)

將專案程式碼索引到 SQLite 資料庫，實現毫秒級全文搜尋與 SQL 分析。

---

## 系統概述

### 為什麼需要這套系統？

> "I just put all my code data into SQLite. Unsurprisingly it can query, filter, aggregate way better than flat files. Agents love databases and a filesystem is just the worst kind of database."

傳統檔案系統搜尋（grep/ripgrep）需要每次掃描所有檔案，而 SQLite + FTS5 索引：

| 比較項目 | Grep/Ripgrep | SQLite + FTS5 |
| ---- | ------------ | ------------- |
| 搜尋速度 | 秒級（需掃描檔案）    | 毫秒級（索引查詢）     |
| 聚合分析 | 需額外處理        | 原生 SQL 支援     |
| 增量更新 | 不支援          | 只更新變動檔案       |
| 複雜查詢 | 困難           | SQL 原生支援      |

### 架構

```
專案檔案 (.py, .yaml, .md)
        │
        ▼
   indexer.py --index
        │
        ▼
┌─────────────────────────────────┐
│     project_index.db            │
├─────────────────────────────────┤
│  files 表（主表）                │
│  - path (主鍵)                  │
│  - extension                    │
│  - content                      │
│  - last_modified                │
│  - size                         │
├─────────────────────────────────┤
│  files_fts 虛擬表 (FTS5)        │
│  - 全文索引 path + content      │
│  - 支援 MATCH 語法              │
└─────────────────────────────────┘
        │
        ▼
   快速搜尋 / SQL 分析
```

---

## 檔案結構

```
tools/
├── indexer.py          # 主程式（索引 + CLI）
├── project_index.db    # SQLite 資料庫（自動生成，已加入 .gitignore）
└── query_examples.sql  # SQL 查詢範例
```

---

## 使用方式

### 基本指令

```bash
# 建立/更新索引（增量更新，只處理變動檔案）
python tools/indexer.py --index

# 全文搜尋
python tools/indexer.py --search "關鍵字"
python tools/indexer.py --search "def process_query"
python tools/indexer.py --search "class Handler"

# 顯示統計資訊
python tools/indexer.py --stats

# 列出特定副檔名的檔案
python tools/indexer.py --list-ext .py
```

### SQL 查詢（Windows）

Windows 沒有內建 sqlite3 CLI，需透過 Python 執行：

```bash
# 統計各副檔名檔案數
python -c "import sqlite3; conn = sqlite3.connect('tools/project_index.db'); cursor = conn.cursor(); cursor.execute('SELECT extension, COUNT(*) FROM files GROUP BY extension'); print(cursor.fetchall())"

# 最大的 5 個 Python 檔案
python -c "import sqlite3; conn = sqlite3.connect('tools/project_index.db'); cursor = conn.cursor(); cursor.execute('SELECT path, size/1024 as kb FROM files WHERE extension=\".py\" ORDER BY size DESC LIMIT 5'); [print(f'{row[1]:.1f} KB  {row[0]}') for row in cursor.fetchall()]"

# 估算 Python 總行數
python -c "import sqlite3; conn = sqlite3.connect('tools/project_index.db'); cursor = conn.cursor(); cursor.execute('SELECT SUM(length(content) - length(replace(content, char(10), \"\"))) FROM files WHERE extension=\".py\"'); print(f'約 {cursor.fetchone()[0]:,} 行')"
```

### SQL 查詢（macOS/Linux）

```bash
# 直接使用 sqlite3 CLI
sqlite3 tools/project_index.db "SELECT extension, COUNT(*) FROM files GROUP BY extension"
```

---

## Agent Best Practices

### 何時使用索引系統

| 場景           | 使用方式                          |
| ------------ | ----------------------------- |
| 搜尋關鍵字/函數/類別  | `--search "關鍵字"`              |
| 找所有相關檔案再修改   | 先搜尋，再用 Read 工具讀取              |
| 統計程式碼（行數、大小） | SQL 聚合查詢                      |
| 找特定目錄下的檔案    | SQL `WHERE path LIKE 'dir/%'` |
| 複雜條件篩選       | SQL 組合查詢                      |

### 使用流程

```
1. 開始工作前
   └─> python tools/indexer.py --index  （確保索引是最新的）

2. 需要搜尋時
   └─> python tools/indexer.py --search "關鍵字"
   └─> 或執行 SQL 查詢

3. 修改大量檔案後
   └─> python tools/indexer.py --index  （增量更新）
```

### 實用查詢範例

**場景 A：修改所有跟 Database Connection 有關的設定**

```bash
python tools/indexer.py --search "database connection"
```

**場景 B：統計 Python 總行數**

```bash
python -c "import sqlite3; conn = sqlite3.connect('tools/project_index.db'); cursor = conn.cursor(); cursor.execute('SELECT SUM(length(content) - length(replace(content, char(10), \"\"))) FROM files WHERE extension=\".py\"'); print(cursor.fetchone()[0])"
```

**場景 C：找函數定義位置**

```bash
python tools/indexer.py --search "def handle_callback"
```

**場景 D：找所有 YAML 中的 timeout 設定**

```bash
python -c "import sqlite3; conn = sqlite3.connect('tools/project_index.db'); cursor = conn.cursor(); cursor.execute('SELECT path FROM files_fts JOIN files f ON files_fts.rowid=f.rowid WHERE files_fts MATCH \"timeout\" AND f.extension IN (\".yaml\", \".yml\")'); [print(row[0]) for row in cursor.fetchall()]"
```

### FTS5 搜尋語法

```sql
-- 基本搜尋
WHERE files_fts MATCH 'keyword'

-- AND 搜尋（兩個詞都要出現）
WHERE files_fts MATCH 'database AND connection'

-- OR 搜尋（任一詞出現）
WHERE files_fts MATCH 'timeout OR retry'

-- 片語搜尋（精確詞組）
WHERE files_fts MATCH '"def process_query"'

-- 前綴搜尋
WHERE files_fts MATCH 'process*'
```

---

## Human in the Loop

### 人類需要做的維護

**基本上不需要手動維護**。以下是各情況的處理方式：

| 情況       | 處理方式                    | 人類動作  |
| -------- | ----------------------- | ----- |
| 日常使用     | Agent 自動執行 `--index`    | 無需動作  |
| 大量檔案變動   | 增量更新自動處理                | 無需動作  |
| 資料庫損壞/刪除 | 重新執行 `--index` 重建       | 無需動作  |
| 想加入新副檔名  | 編輯 `INCLUDE_EXTENSIONS` | 需手動編輯 |
| 想排除新目錄   | 編輯 `EXCLUDE_DIRS`       | 需手動編輯 |

### 可選的自動化設定

如果希望索引自動保持最新，可以設定：

**方法 1：Git pre-commit hook**

```bash
# .git/hooks/pre-commit
#!/bin/sh
python tools/indexer.py --index
```

**方法 2：VS Code Task**

```json
// .vscode/tasks.json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Update Code Index",
      "type": "shell",
      "command": "python tools/indexer.py --index",
      "problemMatcher": []
    }
  ]
}
```

**方法 3：檔案監控（watchdog）**

```python
# 需安裝 watchdog: pip install watchdog
# 監控檔案變動並自動更新索引
```

> 注意：這些自動化都是可選的。正常使用時，讓 Agent 在需要時執行 `--index` 即可。

---

## 設定調整

### 索引範圍

編輯 `tools/indexer.py` 中的常數：

```python
# 要索引的副檔名
INCLUDE_EXTENSIONS = {".py", ".yaml", ".yml", ".md"}

# 要排除的目錄
EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    "venv",
    "myenv",
    "myenv311",
    ".venv",
    "node_modules",
    # ... 其他
}
```

### 資料庫位置

預設在 `tools/project_index.db`，已加入 `.gitignore` 不會上傳。

---

## 疑難排解

### Q: 搜尋結果過時？

執行 `python tools/indexer.py --index` 更新索引。

### Q: 資料庫檔案太大？

刪除 `tools/project_index.db` 後重新執行 `--index`。

### Q: Windows 顯示亂碼？

已在 indexer.py 中修復 UTF-8 編碼問題。如仍有問題，確認終端機支援 UTF-8。

### Q: 想重建完整索引？

```bash
del tools\project_index.db   # Windows
rm tools/project_index.db    # macOS/Linux
python tools/indexer.py --index
```

---

## 相關檔案

- `tools/indexer.py` - 主程式
- `tools/query_examples.sql` - SQL 查詢範例
- `.gitignore` - 已排除 `project_index.db`
