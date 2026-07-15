# NLWeb Development Setup

> 與 `scripts/setup.sh` / `scripts/dev-up.sh` 對齊（2026-07-10 更新）。腳本行為變更時請同步本檔。

## 系統需求

| 需求              | 說明                                                  |
| --------------- | --------------------------------------------------- |
| **Python 3.11** | 必須是 3.11，多個依賴套件尚未支援 3.12/3.13                       |
| **Git**         | 版本管理                                                |
| **uv**          | 依賴管理（`setup.sh` 未偵測到會自動安裝）                          |
| **Docker**      | 本機 PostgreSQL（`docker-compose.dev.yml`）             |
| **C++ 編譯器**     | 部分 Python 套件需從原始碼編譯                                 |

### 安裝 Python 3.11

- **macOS**: `brew install python@3.11`
- **Windows**: https://www.python.org/downloads/release/python-3119/
  - 安裝時**務必**勾選 "Add Python to PATH"

### 安裝 C++ 編譯器

- **macOS**: `xcode-select --install`（安裝 Xcode Command Line Tools）
- **Windows**: 安裝 [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
  - 選擇 **Desktop development with C++** workload

## Quick Start

```bash
# 1. Clone
git clone https://github.com/kuoowen-web/taiwan-news-ai-search.git
cd taiwan-news-ai-search

# 2. 一鍵 setup（檢查環境 + uv sync 裝依賴 + 從 .env.example 建 .env）
bash scripts/setup.sh

# 3. 填入 API keys（找專案負責人拿）
#    編輯 .env，至少需要 OPENAI_API_KEY, POSTGRES_CONNECTION_STRING

# 4. 啟動本機 DB（docker compose 起 PostgreSQL + alembic upgrade head）
bash scripts/dev-up.sh

# 5. 啟動 server
cd code/python
uv run python app-aiohttp.py

# 6. 打開 http://localhost:8000
```

## 日常開發

每次開新 terminal：

```bash
cd taiwan-news-ai-search
bash scripts/dev-up.sh          # 確保 PG container 起來 + migration 對齊
cd code/python
uv run python app-aiohttp.py
```

改 code → Ctrl+C 停 server → 重新 `uv run python app-aiohttp.py`。

## Environment Variables

`scripts/setup.sh` 會從 `.env.example` 複製出 `.env`，再填入實際值（找專案負責人拿）。

## Troubleshooting

### `uv sync` 失敗

通常是缺 C++ 編譯器：

- **macOS**: `xcode-select --install`
- **Windows**: 安裝 Visual Studio Build Tools（C++ workload）

### Python 版本不對

多個依賴需要 3.11。確認：

```bash
python --version  # 必須是 3.11.x
```

### Server 起不來

1. 確認 `.env` 已填入 API keys（`OPENAI_API_KEY`、`POSTGRES_CONNECTION_STRING`）
2. 確認 PostgreSQL container 已啟動（先跑 `bash scripts/dev-up.sh`，任一步失敗會直接 exit non-zero）
3. 確認在 `code/python/` 目錄下執行
