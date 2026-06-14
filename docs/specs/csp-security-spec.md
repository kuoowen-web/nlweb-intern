# CSP Security 規格文件

## 概述

NLWeb 使用 nonce-based Content Security Policy（CSP）作為全站安全基礎，搭配 CDN 本地化策略，防止 XSS 與 data injection 攻擊。

**主要檔案**：
- `code/python/webserver/middleware/csp.py` — CSP middleware
- `code/python/webserver/middleware/auth.py` — Auth middleware（含 PUBLIC_ENDPOINTS）
- `code/python/webserver/middleware/__init__.py` — Middleware chain 組裝

---

## CSP Policy 定義

### 目前 Policy（csp.py 現行版本）

```
default-src 'self';
script-src 'self' 'nonce-{nonce}' https://*.clarity.ms https://static.cloudflareinsights.com;
style-src 'self' 'unsafe-inline';
img-src 'self' data: blob: https://*.clarity.ms https://c.bing.com;
connect-src 'self' https://*.clarity.ms https://cloudflareinsights.com;
font-src 'self';
frame-src 'self';
form-action 'self';
base-uri 'self';
object-src 'none'
```

### 各 Directive 說明

| Directive | 值 | 理由 |
|-----------|-----|------|
| `default-src` | `'self'` | 基礎白名單，未明確指定的資源類型 fallback 到此 |
| `script-src` | `'self' 'nonce-{nonce}' https://*.clarity.ms https://static.cloudflareinsights.com` | 只允許同源腳本、有 nonce 的 inline 腳本、Microsoft Clarity analytics、Cloudflare Insights beacon |
| `style-src` | `'self' 'unsafe-inline'` | 同源 CSS 及 inline style（UI 框架需要，接受此風險） |
| `img-src` | `'self' data: blob: https://*.clarity.ms https://c.bing.com` | 同源圖片 + data URI（icon）+ blob（使用者上傳預覽）+ Clarity + Bing（Clarity 追蹤像素） |
| `connect-src` | `'self' https://*.clarity.ms https://cloudflareinsights.com` | XHR/fetch 限制同源 + Clarity 資料回傳 + Cloudflare Insights beacon 回傳 |
| `font-src` | `'self'` | 字型只允許同源（已本地化，不依賴 Google Fonts） |
| `frame-src` | `'self'` | iframe 只允許同源 |
| `form-action` | `'self'` | 防止 form 被劫持送往外部 |
| `base-uri` | `'self'` | 防止 `<base>` 標籤被注入以改變相對 URL 解析 |
| `object-src` | `'none'` | 完全禁止 Flash/plugin（已無使用場景） |

**注意**：`frame-ancestors` directive 未在 CSP header 中定義，由獨立的 `X-Frame-Options: DENY` header 覆蓋此功能（見 Security Headers 段落）。

---

## Nonce 生成與注入流程

### 生成

```python
nonce = secrets.token_urlsafe(16)  # 約 128-bit entropy，URL-safe Base64
request['csp_nonce'] = nonce
```

每個請求產生一個獨立的 nonce，儲存在 `request['csp_nonce']`，供下游 handler 存取。

### Policy 組裝

```python
csp_policy = _CSP_TEMPLATE.format(nonce=nonce)
```

`_CSP_TEMPLATE` 為模組層級常數，只有 `{nonce}` 佔位符。

### Response 注入

```python
_apply_security_headers(response.headers, csp_policy)
```

所有 responses（含 HTTP exceptions）都注入 CSP header：

```python
try:
    response = await handler(request)
except web.HTTPException as ex:
    _apply_security_headers(ex.headers, csp_policy)
    raise
_apply_security_headers(response.headers, csp_policy)
```

### Handler 端使用 Nonce

Handler 可透過 `request['csp_nonce']` 取得 nonce，將其注入 HTML 的 `<script nonce="...">` 標籤，讓 inline script 通過 CSP 檢查。

---

## PUBLIC_ENDPOINTS 白名單機制

白名單定義在 `code/python/webserver/middleware/auth.py`，供 auth_middleware 判斷是否跳過 JWT 驗證。CSP middleware 本身不區分 public/private endpoint，**所有 endpoint 都注入 CSP header**。

### 完整白名單（PUBLIC_ENDPOINTS）

```python
PUBLIC_ENDPOINTS = {
    '/', '/health', '/ready', '/who', '/sites', '/sites_config',
    '/static', '/html', '/setup',
    '/api/auth/register', '/api/auth/login', '/api/auth/verify-email',
    '/api/auth/forgot-password', '/api/auth/reset-password',
    '/api/auth/refresh', '/api/auth/logout', '/api/auth/activate',
    '/api/analytics/event', '/api/analytics/event/batch',
    '/api/help/feedback',
}
```

### PUBLIC_GET_ENDPOINTS（僅 GET 方法免驗證）

```python
PUBLIC_GET_ENDPOINTS = {
    '/api/faq',  # FAQ 列表公開讀取；POST/PUT/DELETE 需 admin auth
}
```

### Path Prefix 規則

以下 prefix 自動免驗證（`path.startswith(...)`）：
- `/static/` — 所有靜態資源
- `/html/` — HTML 頁面

`/favicon.ico` 也在免驗證清單內。

### Soft Auth（公開 endpoint 的可選 JWT）

公開 endpoint 若請求帶有 JWT（Bearer header 或 `access_token` cookie），auth_middleware 仍會嘗試解碼（`_try_soft_auth`），成功則設定 `request['user']`，但失敗不返回 401。

---

## CDN 本地化策略

### 背景

原版 CSP 允許外部 CDN（jsdelivr、unpkg 等）。為收緊 CSP，第三方 JS 庫已下載到 `static/` 目錄本地部署。

### 已本地化的資源

| 資源 | 來源 CDN | 本地路徑 |
|------|----------|----------|
| Microsoft Clarity 初始化腳本 | `https://www.clarity.ms/tag/...` | `/static/clarity-init.js` |
| D3.js v7 | jsdelivr | `/static/d3.v7.min.js` |
| DOMPurify | jsdelivr | `/static/dompurify.min.js` |
| marked.js | jsdelivr | `/static/marked.min.js` |

### 未本地化的資源

| 資源 | 原因 |
|------|------|
| `https://*.clarity.ms`（runtime beacon） | Clarity 運行時需要向 clarity.ms 回傳 telemetry data，無法本地化 |
| `https://c.bing.com`（Clarity 追蹤像素） | Bing/Clarity 整合機制，無法迴避 |
| `https://static.cloudflareinsights.com`（beacon script） | Cloudflare CDN 注入的分析腳本，由 Cloudflare proxy 動態加入 |
| `https://cloudflareinsights.com`（beacon 回傳） | Cloudflare Insights 資料收集端點 |

### 效果

本地化後，`script-src` 可移除大多數外部 CDN domain，只保留 `*.clarity.ms`（analytics 必需）。`font-src 'self'` 表示字型也已本地化（不依賴 Google Fonts）。

---

## Middleware Chain 順序與依賴

`code/python/webserver/middleware/__init__.py` 定義完整順序（aiohttp outermost-first）：

```
1. correlation_middleware   — 為每個請求注入 correlation ID（所有後續 middleware 可用）
2. error_middleware         — 全域錯誤攔截
3. csp_middleware           — 生成 nonce，設定 CSP + security headers（在 auth 之前執行）
4. logging_middleware       — 請求/回應記錄
5. cors_middleware          — CORS headers
6. rate_limit_middleware    — 速率限制
7. auth_middleware          — JWT 驗證（最後防線，在 CSP 之後）
8. streaming_middleware     — SSE streaming 支援
```

**CSP 在 auth 之前執行**的原因：即使請求被 auth 拒絕（401），回應仍需包含 CSP header，防止 401 錯誤頁面被 XSS 利用。

---

## Security Headers

`_apply_security_headers` 為每個 response 設定以下 headers：

| Header | 值 | 說明 |
|--------|-----|------|
| `Content-Security-Policy` | 見上方 policy | XSS 防護主體 |
| `X-Frame-Options` | `DENY` | 防止 clickjacking（補充 CSP `frame-ancestors`） |
| `X-Content-Type-Options` | `nosniff` | 防止 MIME sniffing |
| `Server` | `讀豹` | 隱藏實際 server 軟體資訊（混淆） |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=(), payment=()` | 禁用不需要的瀏覽器 API |

---

## 已知例外與 Workarounds

### style-src 'unsafe-inline'

**現況**：保留 `'unsafe-inline'`。

**原因**：前端 UI 組件使用大量 inline style，遷移成本高。

**風險**：允許攻擊者注入 CSS（用於 CSS injection 攻擊），但 JS 仍受 nonce 保護。

**改善路徑**：需將 inline style 遷移為 CSS class，或改用 nonce-based style（工程量較大）。

### frame-ancestors 未在 CSP 定義

**現況**：`frame-ancestors` 不在 CSP policy 中，改由 `X-Frame-Options: DENY` 負責。

**原因**：ZAP scan（2026-03-26）回報舊版 CSP 未定義 `frame-ancestors`，後續改版新增了 `form-action` 和 `base-uri`，`X-Frame-Options` 同步補上作為 clickjacking 防護。現行 CSP 已含 `form-action` 和 `base-uri`，但 `frame-ancestors` 仍未加入。

### News Search Prototype 頁面（SRI 缺失）

**現況**：`/static/news-search-prototype.html` 在 ZAP scan 時仍有 jsdelivr CDN 引用（dompurify, marked），被 ZAP 回報 Sub Resource Integrity（SRI）missing。

**後續**：這些庫已下載為本地檔案（`/static/dompurify.min.js`、`/static/marked.min.js`），但 HTML 中的 `<script src>` 是否已更新為本地路徑需驗證。

---

## ZAP Scan 結果摘要（2026-03-26）

掃描對象：`http://host.docker.internal:8000`（Docker 容器內，HTTP 模式）

| Alert | Risk | 狀態 |
|-------|------|------|
| CSP: Failure to Define Directive with No Fallback（frame-ancestors, form-action） | Medium | 已部分修復（form-action, base-uri 已加入；frame-ancestors 由 X-Frame-Options 補） |
| CSP: style-src unsafe-inline | Medium | 已知，暫時保留 |
| Missing Anti-clickjacking Header | Medium | 已修復（X-Frame-Options: DENY 已加入） |
| Sub Resource Integrity Attribute Missing | Medium | CDN 資源已本地化，待確認 HTML 已更新 |
| Bypassing 403（x-original-url header） | Medium | 非 CSP 問題，屬 reverse proxy 設定議題 |
| HTTP Only Site | Medium | 非 CSP 問題，VPS 部署時需加 HTTPS/TLS termination |

**注意**：ZAP scan 中的 CSP evidence 顯示舊版 policy（無 nonce、無 form-action、無 base-uri）。現行 `csp.py` 為更新後版本，已加入這些改善。

---

## 測試策略

### 本地驗證

1. 啟動 server 後，curl 任意 endpoint，確認回應 headers 包含：
   - `Content-Security-Policy`（含 nonce）
   - `X-Frame-Options: DENY`
   - `X-Content-Type-Options: nosniff`
   - `Server: 讀豹`
   - `Permissions-Policy`

2. 驗證每次請求的 nonce 不同：
   ```bash
   curl -si http://localhost:8000/ | grep -i "content-security-policy"
   curl -si http://localhost:8000/ | grep -i "content-security-policy"
   ```

3. 驗證 HTTPException（如 404）也帶有 security headers。

### ZAP 全掃

```bash
# Docker 模式（參考 docs/e2etest.md）
docker run -t ghcr.io/zaproxy/zaproxy:stable zap-baseline.py \
  -t http://host.docker.internal:8000 -J zap-report.json
```

掃描結果存入 `zap-reports/zap-report.json`。目標為消除所有 Medium 以上風險的 CSP 相關 alerts。
