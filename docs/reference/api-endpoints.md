# API 端點文件

> **2026-07-11 從 `webserver/routes/` 實際註冊重寫**（前版 2026-01-19 含已刪除的 Chat/WebSocket 幻影端點）。
> 來源檔案：`code/python/webserver/routes/__init__.py`（總註冊入口）+ 12 個 route 檔 + `analytics_handler.py` + `ranking_analytics_handler.py`。
> 共 **85 條動態路由註冊**（不含 static mounts）。以 code 為準；文件與 code 不符時以 code 為事實來源。

---

## 認證機制（middleware 層）

**檔案**：`webserver/middleware/auth.py`

- **Token 來源優先順序**：`Authorization: Bearer` header → `access_token` httpOnly cookie → `?auth_token=` query param（僅 GET，供 SSE/EventSource 用）
- **預設全部端點需有效 JWT**（HS256，`JWT_SECRET`）；只有 `PUBLIC_ENDPOINTS` 白名單免驗
- **Public 白名單**：`/`、`/health`、`/ready`、`/who`、`/sites`、`/sites_config`、`/setup`、`/static/*`、`/html/*`、`/favicon.ico`、`/api/auth/{register,login,verify-email,forgot-password,reset-password,refresh,logout,activate}`、`/api/analytics/event`、`/api/analytics/event/batch`、`/api/help/feedback`
- **Soft auth**：public 端點若帶有效 token 仍會解出 `request['user']`（無效 token 靜默略過）
- Token 失效回 401（`type: token_expired` / `invalid_token`）；`JWT_SECRET` 未設定回 500
- 注意：`PUBLIC_GET_ENDPOINTS` 白名單含 `/api/faq`，但目前**無此路由註冊**（FAQ 路由已移除，白名單殘留）
- Dev auth bypass 已於 2026-05-19 刪除，測試一律真實登入

**Rate limit**（`middleware/rate_limit.py`，per IP）：`POST /api/auth/register` 5/hr、`POST /api/auth/forgot-password` 3/hr、`POST /api/auth/login` 10/min、`POST /api/admin/resend-activation` 5/hr。

**併發限制**（`middleware/concurrency_limiter.py`，於 handler 內 enforce）：/ask 一般搜尋 per-session/per-IP 上限；Deep Research 與 Live Research 另有 per-user/per-IP 專屬 slot（超限回 429 `rate_limited`）。`GUARDRAIL_DR_ENABLED=false` 可整體關閉 DR/LR（回 503）。

---

## 查詢與研究（`routes/api.py`）

| Method | Path | 用途 | Auth |
|--------|------|------|------|
| GET/POST | `/ask` | 主查詢端點。`generate_mode`：`none`/`summarize`（list+摘要）、`generate`、`unified`（單一 SSE 流：文章+摘要+AI 回答）、`deep_research`。**預設 SSE streaming**（`streaming=false` 才回同步 JSON；同步 deep_research 被 400 拒絕導向 streaming）。認證後 handler 將 `request['user']` 的 user_id/org_id 注入 query_params 覆蓋偽造。查詢 >500 字回 400 `query_too_long` | 需 JWT |
| GET/POST | `/api/deep_research` | Deep Research 專用 SSE 端點（強制 `generate_mode=deep_research` + streaming）。回 `begin-nlweb-response` → 進度 → `final_result`（含 knowledge_graph / argument_graph / verification_status）→ `complete` | 需 JWT |
| POST | `/api/research/rerun` | KG 編輯後選擇性重跑（跳過 phase 1 search，重用 cached context 跑 phases 2-4）。SSE。需 feature flag `composable_pipeline`，否則 501。前端 caller：`js/features/knowledge-graph.js` | 需 JWT |
| POST | `/api/live_research` | 啟動 Live Research session。SSE。需 feature flag `live_research`，否則 503。支援 `mock` 模式（dev/E2E canned events）。client 斷線**不取消** task（跑到下個 checkpoint 存檔） | 需 JWT |
| POST | `/api/live_research/continue` | LR 從 checkpoint 續跑（`user_message` / `auto_continue` / `nav_action: back_one|restart`）。SSE | 需 JWT |
| POST | `/api/feedback` | 儲存搜尋結果 thumbs up/down + 留言（`rating: positive|negative`）。前端 caller：`js/features/sharing.js` | 需 JWT |
| GET | `/who` | NLWeb 協定「誰」類查詢（WhoHandler，支援 streaming）。**上游 NLWeb 遺產端點，live 前端無 caller** | 公開（soft-auth） |
| GET | `/sites` | 可用網站清單（從 vector DB 取 distinct sources） | 公開（soft-auth） |
| GET | `/sites_config` | 網站清單 + sites.xml metadata overlay（display_name / item_types），5 分鐘 cache；DB 失敗 fallback sites.xml | 公開（soft-auth） |

---

## 認證（`routes/auth.py`）

| Method | Path | 用途 | Auth |
|--------|------|------|------|
| GET | `/setup` | Bootstrap onboarding 頁（`?token=` 一次性 bootstrap token，B2B 客戶 admin 首次建組織+帳號），server-side render HTML | 公開（token 於 handler 內驗證） |
| POST | `/api/auth/register` | 以 bootstrap token 建立 admin + org（B2B 不開放自助註冊）。成功直接 set access/refresh cookie | 公開 |
| GET | `/api/auth/verify-email` | Email 驗證（`?token=`） | 公開 |
| POST | `/api/auth/login` | Email/Password 登入。access_token（15 分）+ refresh_token（7 天）以 httpOnly Secure cookie 下發（refresh cookie path=`/api/auth`） | 公開 |
| POST | `/api/auth/refresh` | 刷新 access token（refresh token 從 cookie 或 body），refresh token rotation | 公開 |
| POST | `/api/auth/logout` | 登出：撤銷 refresh token + 清 cookie | 公開 |
| GET | `/api/auth/me` | 目前使用者資訊（含 org_id / role） | 需 JWT |
| POST | `/api/auth/forgot-password` | 寄密碼重設信（不洩漏 email 是否存在） | 公開 |
| GET | `/api/auth/reset-password` | 重設密碼表單頁（server-side render，`?token=`） | 公開 |
| POST | `/api/auth/reset-password` | 執行密碼重設 | 公開 |
| GET | `/api/auth/activate` | 員工帳號啟用頁（`?token=`；含已啟用/過期/停用三情境友善頁） | 公開 |
| POST | `/api/auth/activate` | 員工設密碼完成啟用。成功直接 set cookie 進站 | 公開 |
| POST | `/api/auth/change-password` | 已登入使用者改密碼 | 需 JWT |
| POST | `/api/auth/logout-all` | 登出全部裝置（撤銷該 user 所有 refresh token） | 需 JWT |

## 組織（`routes/auth.py`）

| Method | Path | 用途 | Auth |
|--------|------|------|------|
| POST | `/api/org` | 建立組織 | 需 JWT |
| GET | `/api/org` | 列出使用者所屬組織 | 需 JWT |
| POST | `/api/org/accept-invite` | 接受組織邀請（token + email match） | 需 JWT |
| POST | `/api/org/{id}/invite` | 邀請成員（admin 於 service 層驗證） | 需 JWT |
| GET | `/api/org/{id}/members` | 列出成員（含 `is_activated`） | 需 JWT（須為成員） |
| DELETE | `/api/org/{id}/members/{user_id}` | 移除成員 | 需 JWT（admin） |

## Admin（`routes/auth.py` + `routes/admin.py`）

| Method | Path | 用途 | Auth |
|--------|------|------|------|
| POST | `/api/admin/create-user` | Admin 建立員工帳號（寄啟用信） | 需 JWT（admin） |
| POST | `/api/admin/logout-user/{user_id}` | Admin 強制登出同 org 成員 | 需 JWT（admin，handler 內驗 role） |
| POST | `/api/admin/resend-activation` | 重寄啟用信給未啟用成員（舊 token 覆蓋失效） | 需 JWT（admin） |
| PATCH | `/api/admin/user/{user_id}/active` | 停用/啟用成員帳號（`is_active`） | 需 JWT（admin） |
| PATCH | `/api/admin/user/{user_id}/role` | 修改成員角色 | 需 JWT（admin） |
| DELETE | `/api/admin/user/{user_id}` | 刪除成員帳號 | 需 JWT（admin） |
| GET | `/api/admin/session-count` | 指定 user 的 session 數（`?user_id=`）。**dev-only：E2E 驗證/診斷用，不接一般 UI**（見 `routes/admin.py` 檔頭） | 需 JWT（admin，handler 內驗 role） |

---

## Session 與偏好（`routes/sessions.py`）

全部需 JWT + org context（無 org_id 回 400）。

| Method | Path | 用途 | Auth |
|--------|------|------|------|
| GET | `/api/sessions` | Session 列表（`limit`/`offset`/`include_archived`，ORDER BY updated_at DESC） | 需 JWT |
| POST | `/api/sessions` | 建立 session（conversation/session/chat history + articles + research report + LR snapshot） | 需 JWT |
| GET | `/api/sessions/shared` | 組織分享給我的 sessions | 需 JWT |
| POST | `/api/sessions/migrate` | localStorage → server 一次性遷移 | 需 JWT |
| GET | `/api/sessions/{id}` | 取完整 session（own 優先，fallback shared 存取） | 需 JWT |
| PUT | `/api/sessions/{id}` | 更新 session | 需 JWT |
| DELETE | `/api/sessions/{id}` | 刪除 session（soft delete，30 天後背景 purge） | 需 JWT |
| POST | `/api/sessions/{id}/restore` | 復原已刪 session | 需 JWT |
| PATCH | `/api/sessions/{id}/feedback` | session 層 thumbs_up/thumbs_down | 需 JWT |
| PATCH | `/api/sessions/{id}/note` | admin 註記 | 需 JWT（admin） |
| PATCH | `/api/sessions/{id}/visibility` | 設定分享範圍（private/team/org） | 需 JWT |
| PATCH | `/api/sessions/{id}/articles/annotate` | 文章標註（URL 走 body 非 path） | 需 JWT |
| GET | `/api/sessions/{id}/export` | 匯出 `format=json|csv|citations|ris`。**live 前端目前無 caller** | 需 JWT |
| GET | `/api/preferences` | 使用者偏好（key-value） | 需 JWT |
| PUT | `/api/preferences/{key}` | 設定偏好 | 需 JWT |

## 前端初始化（`routes/user_init.py`）

| Method | Path | 用途 | Auth |
|--------|------|------|------|
| GET | `/api/user/init` | Composite endpoint：一次回 `{user, org, role, sessions, shared_sessions, preferences}`（D-2026-05-13 Init Sync；取代登入後 5+ 個 round-trip；sessions/shared 各 cap 50） | 需 JWT |

---

## 私有文件（`routes/user_data.py`）

| Method | Path | 用途 | Auth |
|--------|------|------|------|
| POST | `/api/user/upload` | 上傳檔案（multipart；org 儲存配額檢查，超限 413）。上傳後由 SSE 連線觸發處理 | 需 JWT |
| GET | `/api/user/upload/{source_id}/progress` | 處理進度 **SSE** stream（連上才開始處理，防重複） | 需 JWT |
| GET | `/api/user/sources` | 列出使用者資料來源（org 隔離） | 需 JWT |
| DELETE | `/api/user/sources/{source_id}` | 刪除來源 + 關聯 PG chunks | 需 JWT |
| GET | `/api/user/sources/{source_id}/status` | 單一來源處理狀態 | 需 JWT |

## 稽核（`routes/audit.py`）

| Method | Path | 用途 | Auth |
|--------|------|------|------|
| GET | `/api/audit/logs` | 組織稽核日誌（`action`/`user_id`/`since`/`until`/`limit`≤500）。**live 前端目前無 caller** | 需 JWT（admin） |
| GET | `/api/audit/trail` | 個人 research trail。**live 前端目前無 caller** | 需 JWT |

## Help Center（`routes/help.py`）

| Method | Path | 用途 | Auth |
|--------|------|------|------|
| POST | `/api/help/feedback` | 使用者回饋（category/rating 1-5/content 10-500 字/截圖 base64 ≤5MB JPEG/PNG）。前端 caller：`static/js/feedback-modal.js` | 公開（soft-auth 自動補 email） |

---

## Analytics（`webserver/analytics_handler.py`）

| Method | Path | 用途 | Auth |
|--------|------|------|------|
| GET | `/api/analytics/stats` | 整體統計（`?days=7`）。caller：`analytics-dashboard.html` | 需 JWT |
| GET | `/api/analytics/queries` | 近期查詢與指標（`days`/`limit`） | 需 JWT |
| GET | `/api/analytics/top_clicks` | 熱門點擊結果 | 需 JWT |
| GET | `/api/analytics/export_training_data` | 匯出 ML 訓練資料 CSV（4 表 raw logs） | 需 JWT |
| POST | `/api/analytics/event` | 前端單一分析事件（tracker 送） | 公開 |
| POST | `/api/analytics/event/batch` | 前端分析事件批次 | 公開 |

## Ranking Analytics（`webserver/ranking_analytics_handler.py`）

| Method | Path | 用途 | Auth |
|--------|------|------|------|
| GET | `/api/ranking/config` | 目前 ranking 系統設定（LLM prompt/model、BM25/XGBoost/MMR 參數）。caller：`analytics-dashboard.html` | 需 JWT |
| GET | `/api/ranking/pipeline/{query_id}` | 單一查詢的 pipeline trace（retrieved/ranked counts + top K 分數） | 需 JWT |

---

## 健康檢查（`routes/health.py`）

| Method | Path | 用途 | Auth |
|--------|------|------|------|
| GET | `/health` | 健康檢查（含 PostgreSQL `SELECT 1`，3s timeout；HTTP/1.0 相容回應供 Cloudflare 健檢） | 公開 |
| GET | `/ready` | Readiness（static files + client session） | 公開 |

## 協定端點：MCP / A2A（`routes/mcp.py`、`routes/a2a.py`）— **已卸載（拍板 3，2026-07-14）**

> **route 未掛載**：對外零 caller，`setup_routes()` 已移除 `setup_mcp_routes(app)`+`setup_a2a_routes(app)` 兩行呼叫以關閉攻擊面。以下路徑**目前皆不對外開放**（框架遺產）。
> code 保留於 `routes/mcp.py`/`routes/a2a.py`（檔頭 import 亦保留）；未來 agent 整合時在 `setup_routes` 加回上述兩行即復活。

供外部 agent 以協定介接；站內前端不呼叫。（復活後）**注意：不在 middleware public 白名單，實際需 JWT**（含各自的 `/health`）。

| Method | Path | 用途 |
|--------|------|------|
| GET | `/mcp/health`、`/mcp/healthz` | MCP 健康檢查 |
| GET/POST | `/mcp`、`/mcp/{path:.*}` | Model Context Protocol JSON-RPC 端點 |
| GET | `/a2a/health`、`/a2a/healthz` | A2A 健康檢查 |
| POST | `/a2a`、`/a2a/{path:.*}` | Agent-to-Agent 協定端點（capabilities: ask, list_sites） |
| GET | `/a2a` | A2A agent 資訊卡 |

## 靜態（`routes/static.py`）

| Method | Path | 用途 | Auth |
|--------|------|------|------|
| GET | `/` | 主頁面（serve `static/news-search-prototype.html`，no-cache） | 公開 |
| GET | `/favicon.ico` | favicon（serve favicon.png） | 公開 |
| GET | `/static/*` | 靜態檔案 mount | 公開 |
| GET | `/.well-known/*` | `.well-known` mount（目前含 security.txt） | 註冊了但**不在 public 白名單**（middleware 會要求 JWT） |
| GET | `/html/*` | 條件 mount：僅 `static/html/` 目錄存在時註冊（目前 repo 無此目錄 → 未掛載） | 公開（白名單有列） |

---

## SSE 端點總覽

| 端點 | 觸發方式 |
|------|---------|
| `GET/POST /ask` | 預設 streaming（`streaming=false` 關閉；前端現走 POST fetch-reader，GET EventSource 路徑已 deprecated 無 live caller） |
| `GET/POST /api/deep_research` | 強制 streaming |
| `POST /api/research/rerun` | 強制 streaming |
| `POST /api/live_research`、`POST /api/live_research/continue` | 強制 streaming |
| `GET /api/user/upload/{source_id}/progress` | 上傳處理進度 |

SSE 回應 headers：`text/event-stream` + `X-Accel-Buffering: no`。SSE envelope 帶 `user_id` stamp（前端 Trigger G 身分核對，Phase 4b.5）。

---

## 前版幻影端點（已確認不存在，勿再引用）

- `GET /health/chat`、`GET /chat/my-conversations`、`POST /chat/create`、`WebSocket /chat/ws/{conv_id}` — WebSocket chat 系統已於 B2B 轉型時整組刪除（login-spec「前置清理」）
- OAuth 相關端點 — OAuth 已刪除，改 Email/Password + JWT

---

*更新：2026-07-11（從 route 檔實際註冊全面重寫）*
