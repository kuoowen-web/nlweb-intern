# NLWeb Development Setup

## 系統需求

| 需求              | 說明                                       |
| --------------- | ---------------------------------------- |
| **Python 3.11** | 必須是 3.11，3.12/3.13 會導致 qdrant-client 不相容 |
| **Git**         | 版本管理                                     |
| **C++ 編譯器**     | 部分 Python 套件（chroma-hnswlib）需要從原始碼編譯     |

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

# 2. 一鍵 setup（自動檢查環境 + 建 venv + 裝 dependencies + 建 .env）
bash scripts/setup.sh

# 3. 填入 API keys（找專案負責人拿）
#    編輯 .env，填入 OPENAI_API_KEY, QDRANT_URL, QDRANT_API_KEY

# 4. 啟動 server
source venv/bin/activate        # macOS
source venv/Scripts/activate    # Windows (Git Bash)
cd code/python
python app-file.py

# 5. 打開 http://localhost:8000
```

## 日常開發

每次開新 terminal：

```bash
cd taiwan-news-ai-search
source venv/bin/activate        # macOS
source venv/Scripts/activate    # Windows (Git Bash)
cd code/python
python app-file.py
```

改 code → Ctrl+C 停 server → 重新 `python app-file.py`。

## Environment Variables

把我後來給你們的那個.env版本放進去。

## Troubleshooting

### `pip install` 失敗

通常是 chroma-hnswlib 編譯問題，缺 C++ 編譯器：

- **macOS**: `xcode-select --install`
- **Windows**: 安裝 Visual Studio Build Tools（C++ workload）

### `qdrant-client` 報錯

Python 版本不對。確認：

```bash
python --version  # 必須是 3.11.x
```

### Server 起不來

1. 確認 `.env` 已填入 API keys
2. 確認在 `code/python/` 目錄下執行
3. 確認 venv 已 activate（terminal 前面應該有 `(venv)` 字樣）
