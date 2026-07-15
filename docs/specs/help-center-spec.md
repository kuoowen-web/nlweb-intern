# Help Center 規格文件

> **Owner**: 讀豹 Team
> **Last Updated**: 2026-03-31
> **Status**: 完成（Feedback POST + FAQ 靜態顯示 已上線）

---

## 目的與範圍

Help Center 是讀豹的使用者支援介面，提供三項功能：

1. **使用說明**：引導使用者操作平台各功能
2. **常見問題（FAQ）**：分類展示預設問題與解答
3. **聯絡客服**：email 連結 + 意見回饋表單（含截圖上傳）

範圍：`static/help.html`（前端頁面）+ `webserver/routes/help.py`（後端 API）+ `static/js/help.js`（互動邏輯）+ `static/js/feedback-utils.js`（共用工具）。

---

## 路由設計

### 已實作端點

| Method | Path | 認證 | 說明 |
|--------|------|------|------|
| `POST` | `/api/help/feedback` | 公開（soft auth） | 提交意見回饋 |

路由在 `webserver/routes/__init__.py` 的 `setup_routes()` 中透過 `setup_help_routes(app)` 統一註冊。

### 認證說明

`/api/help/feedback` 被列入 `auth_middleware` 的 `PUBLIC_ENDPOINTS`，任何未登入使用者皆可提交。

若請求帶有有效 JWT（已登入），middleware 會執行 soft auth：
- `request['user']['authenticated'] = True`
- handler 從 JWT 自動填入 `user_id` 和 `email`（若表單未填 email）

未帶 JWT 的匿名提交也被接受；`user_id` 與 `email` 均為 `NULL`。

### auth middleware 中的 FAQ 相關設定

`PUBLIC_GET_ENDPOINTS` 包含 `/api/faq`（GET 公開；POST/PUT/DELETE 需 admin auth），但目前 `help.py` 並未實作 FAQ CRUD API（見「已知限制」）。

---

## DB Schema

資料表定義在 `auth/auth_db.py` 的 `_get_sqlite_schema()` 與 `_get_postgres_schema()`，AuthDB 在啟動時自動建立。

### `feedbacks` 資料表

| 欄位 | 型別（SQLite/PG） | 說明 |
|------|-----------------|------|
| `id` | INTEGER AUTOINCREMENT / BIGSERIAL | 主鍵 |
| `user_id` | TEXT / UUID | 已登入使用者的 ID（可 NULL） |
| `email` | TEXT | 使用者填寫或從 JWT 自動填入（可 NULL） |
| `category` | TEXT NOT NULL | `bug` / `feature` / `content` / `other` |
| `rating` | INTEGER NOT NULL | 1–5 整數評分 |
| `content` | TEXT NOT NULL | 說明文字（10–500 字） |
| `screenshot_path` | TEXT | 相對路徑，例如 `uploads/feedback/abc123.jpg`（可 NULL） |
| `session_id` | TEXT | 前端傳入的 session 識別符（可 NULL） |
| `created_at` | REAL | Unix timestamp（float） |

### `faqs` 資料表

| 欄位 | 型別 | 說明 |
|------|------|------|
| `id` | INTEGER AUTOINCREMENT | 主鍵 |
| `question` | TEXT NOT NULL | 問題文字 |
| `answer` | TEXT NOT NULL | 解答文字 |
| `category` | TEXT NOT NULL DEFAULT 'general' | `general` / `search` / `account` / `privacy` / `other` |
| `sort_order` | INTEGER NOT NULL DEFAULT 0 | 顯示排序 |
| `is_published` | INTEGER NOT NULL DEFAULT 1 | 是否上架（1=是） |
| `created_at` | REAL | Unix timestamp |
| `updated_at` | REAL | Unix timestamp |

**注意**：`faqs` 資料表已建立，但目前 FAQ 內容由前端 JavaScript 靜態管理（見「FAQ 內容管理方式」），未透過此資料表提供。

---

## 前端架構

`static/help.html` 為單頁，包含三個 Tab：

### Tab 1：使用說明（`panel-help`）

純靜態 HTML，說明六個主題：搜尋功能、閱讀結果、登入與帳號管理、對話紀錄、組織功能（企業版）、進階篩選、快捷鍵。

### Tab 2：常見問題（`panel-faq`）

- FAQ 資料定義在 `static/js/help.js` 的 `FAQ_DATA` 常數（22 個條目，硬編碼）
- 分類篩選按鈕：全部 / 一般 / 搜尋 / 帳號 / 隱私 / 其他
- Accordion 展開：點擊問題展開/收起答案
- 純前端渲染，不呼叫任何 API

### Tab 3：聯絡客服（`panel-contact`）

兩個卡片：
1. 電子郵件客服：`support@twdubao.com`（2 工作天回覆）
2. 意見回饋按鈕：觸發 Feedback Modal（見下節）

---

## Feedback 流程

### 前端送出流程

```
使用者點擊「送出意見回饋」
  → Feedback Modal 開啟
  → 填寫：類別（chip 選擇）+ 評分（星星）+ 說明 + 截圖（選填）+ email（選填）
  → 前端驗證（類別必填、評分必填、說明 ≥ 10 字元）
  → 若有截圖：呼叫 compressAndEncode()（Canvas 壓縮至最長邊 1024px，JPEG 0.8 品質，輸出純 base64）
  → fetch POST /api/help/feedback（JSON）
  → 成功：顯示「感謝您的回饋！」，2 秒後關閉
  → 失敗：顯示 error 文字
```

`compressAndEncode()` 和 `getJwtEmail()` 定義在 `static/js/feedback-utils.js`，同時被 `news-search-prototype.html` 引用。

### 後端處理流程（`post_feedback_handler`）

1. 解析 JSON body
2. 驗證欄位：
   - `category` 必須在 `VALID_CATEGORIES = {'bug', 'feature', 'content', 'other'}`
   - `rating` 必須是 1–5 整數
   - `content` 長度 10–500 字元
3. Soft auth：若 JWT 有效，讀取 `user_id` 與 `email`
4. 截圖處理：
   - base64 decode
   - 大小檢查：超過 5 MB（`MAX_SCREENSHOT_BYTES`）拒絕（~~錯誤訊息顯示「1MB」文案不一致~~ → 已修，commit `c9c7a45b` 2026-06-17，前後端統一 5MB）
   - Magic bytes 驗證：僅接受 JPEG（`\xff\xd8\xff`）或 PNG（`\x89PNG...`）
   - 儲存至 `static/uploads/feedback/{uuid}.{ext}`（路徑由 `SCREENSHOT_DIR` 計算，以 `help.py` 所在位置向上 5 層定位專案根目錄）
   - 截圖儲存失敗時記 warning log 並繼續（`screenshot_path = None`）
5. INSERT 至 `feedbacks` 資料表，回傳 `{ success: true, id: <int> }`（HTTP 201）

### 截圖儲存路徑

```
{project_root}/static/uploads/feedback/{uuid4_hex}.{jpg|png}
```

已上傳的截圖可透過 `/static/uploads/feedback/...` 靜態路由存取（由 `setup_static_routes` 負責）。

---

## FAQ 內容管理方式

**目前實作**：FAQ 為靜態前端資料，定義在 `static/js/help.js` 的 `FAQ_DATA` 陣列。

- 所有 22 個條目硬編碼於 JS 檔案中
- 新增/修改 FAQ 需直接編輯 `static/js/help.js`
- 無 admin UI、無 API 呼叫

**資料庫 schema 已就緒**：`faqs` 資料表已在 `auth_db.py` 定義，但目前並未使用。未來可實作 `/api/faq` CRUD API 接替靜態資料。

---

## CSP 相關注意事項

所有回應經過 `csp_middleware`，設定以下 header：

```
Content-Security-Policy:
  default-src 'self';
  script-src 'self' 'nonce-{nonce}' https://*.clarity.ms;
  style-src 'self' 'unsafe-inline';
  img-src 'self' data: blob: ...;
  connect-src 'self' ...;
  form-action 'self';
```

**注意事項**：
- `help.html` 的 `<script>` 標籤為外部 `src` 引用（非 inline），符合 CSP `script-src 'self'` 規則，不需 nonce
- Feedback 截圖使用 `canvas.toDataURL()`（`data:` URL），由 JS 處理後轉為 base64 再 POST，不需 CSP `img-src` 例外
- `blob:` URL 在 `img-src` 中允許，供 `URL.createObjectURL()` 載入圖片時使用（`compressAndEncode` 內部）

---

## 測試覆蓋

測試檔案：`code/python/tests/test_help_routes.py`

| 測試案例 | 說明 |
|----------|------|
| `test_post_feedback_success` | 正常提交，HTTP 201，回傳 `success: true` + `id` |
| `test_post_feedback_missing_required` | 缺少 content，HTTP 400 |
| `test_post_feedback_invalid_rating` | rating=6（超出範圍），HTTP 400 |
| `test_post_feedback_content_too_short` | 內容不足 10 字元，HTTP 400 |
| `test_post_feedback_content_too_long` | 內容超過 500 字元，HTTP 400 |

測試使用真實 SQLite（`tmp_path`），無 DB mock；每個測試透過 `_fresh_db` fixture 取得獨立資料庫。

---

## 已知限制與未來擴充方向

### 已知限制

1. **FAQ 無動態管理**：內容硬編碼於 JS；更新需重新部署前端
2. ~~**截圖大小訊息不一致**：後端限制 5 MB，前端錯誤訊息顯示「1MB」~~ → 已修（commit `c9c7a45b`，2026-06-17）
3. **截圖無存取控制**：上傳後可透過靜態路由公開存取，無需認證
4. **feedbacks 無管理介面**：目前無法在後台瀏覽/處理回饋資料
5. **`/api/faq` GET 端點**：`auth.py` 的 `PUBLIC_GET_ENDPOINTS` 已預留 `/api/faq`，但 `help.py` 尚未實作

### 未來擴充方向

1. **FAQ CRUD API**：實作 `GET /api/faq`（公開）+ `POST/PUT/DELETE /api/faq`（admin only），從 `faqs` 資料表動態讀取
2. **Feedback 後台**：admin dashboard 顯示回饋列表、狀態管理
3. **截圖存取控制**：上傳後僅限 admin 或上傳者存取
4. ~~**截圖大小訊息修正**：前後端統一為 5 MB~~ → 已完成（commit `c9c7a45b`，2026-06-17）
