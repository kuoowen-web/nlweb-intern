# 程式碼規範

## 目錄結構

### Python 後端
```
code/python/
├── core/                  # 核心功能
│   ├── query_analysis/    # 查詢分析模組
│   ├── baseHandler.py     # 基礎 Handler
│   ├── config.py          # 設定管理
│   ├── retriever.py       # 向量資料庫介面
│   └── ...
├── methods/               # 特殊查詢處理器
├── reasoning/             # 推論系統
│   ├── orchestrator.py    # Actor-Critic 協調器
│   └── agents/            # 4 個 Agent
├── llm_providers/         # LLM 提供者包裝
└── webserver/             # HTTP 伺服器
```

### JavaScript 前端
```
static/
├── news-search-prototype.html  # 主 UI
├── managed-event-source.js     # SSE 處理
├── conversation-manager.js     # 對話狀態
└── utils.js                    # 共用工具
```

---

## 命名規範

### Python

| 類型 | 格式 | 範例 |
|------|------|------|
| 檔案 | snake_case | `base_handler.py` |
| 類別 | PascalCase | `NLWebHandler` |
| 函式 | snake_case | `process_query()` |
| 常數 | UPPER_SNAKE | `MAX_RETRIES` |
| 私有 | _prefix | `_internal_method()` |

**類別後綴**：
- `Handler`：請求處理器
- `Client`：外部服務客戶端
- `Manager`：狀態/資源管理器
- `Agent`：推論系統 Agent

### JavaScript

| 類型 | 格式 | 範例 |
|------|------|------|
| 檔案 | kebab-case | `chat-interface.js` |
| 類別 | PascalCase | `ConversationManager` |
| 函式 | camelCase | `handleStreamingData()` |
| 常數 | UPPER_SNAKE | `API_ENDPOINT` |

---

## 錯誤處理

### Python
```python
# 捕捉特定例外
try:
    result = await vector_db.search(query)
except ConnectionError as e:
    logger.error(f"Vector DB 連線失敗: {e}")
    # 回傳部分結果或 fallback
except TimeoutError as e:
    logger.warning(f"Vector DB 超時: {e}")
    # 使用快取結果（如有）
```

### JavaScript
```javascript
try {
    const response = await fetch(url);
    if (!response.ok) {
        throw new Error(`HTTP 錯誤: ${response.status}`);
    }
} catch (error) {
    console.error('API 呼叫失敗:', error);
    this.showErrorMessage('無法處理請求，請重試');
}
```

---

## 邊界情況處理

| 情況 | 處理方式 |
|------|----------|
| 空查詢 | 回傳提示訊息，不處理 |
| JSON 格式錯誤 | 記錄錯誤，回傳錯誤訊息 |
| 缺少參數 | 使用合理預設值 |
| 查詢過長 | 截斷至合理長度（1000 字元） |
| 無效網站 | 預設為 'all' |

---

## 串流回應

1. **連線中斷**：指數退避重試
2. **部分資料**：緩衝並驗證 JSON
3. **超時**：合理時間後關閉（5 分鐘）
4. **記憶體管理**：定期清理舊訊息

---

## 安全規範

| 項目 | 做法 |
|------|------|
| 輸入驗證 | 驗證所有使用者輸入 |
| SQL 注入 | 使用參數化查詢 |
| XSS 防護 | 轉義 HTML 內容 |
| CORS | 正確設定 production 環境 |
| 認證 | 每個請求驗證 token |
| 日誌 | 不記錄密碼、token、PII |

---

## 效能規範

| 項目 | 做法 |
|------|------|
| 快取 | 快取昂貴操作 |
| 分頁 | 限制結果集大小 |
| 延遲載入 | 按需載入資料 |
| 連線池 | 重用資料庫連線 |
| 並行處理 | 使用 asyncio 處理 I/O |

---

## 程式碼品質

1. **DRY**：提取共用功能
2. **SOLID**：單一職責原則
3. **Fail Fast**：早期驗證輸入
4. **明確優於簡短**：清晰變數名

### Python 特定
- 使用型別提示
- 公開方法需有 docstring
- 優先使用 async/await
- 使用 context manager 管理資源

### JavaScript 特定
- 使用 'use strict'
- 預設使用 const
- 箭頭函式用於回呼
- 模板字串用於插值

---

*更新：2026-01-19*
