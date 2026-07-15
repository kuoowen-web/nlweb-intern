# 使用者流程與 UX

> **2026-07-11 對 code 現況重寫**（前版 2026-01-19 描述 OAuth 四 provider 與 list/summarize/generate 三模式，均已過時）。
> 權威細節：登入/session 流程見 `docs/specs/login-spec.md`；前端模組與 UI 細節見 `docs/specs/frontend-spec.md`；端點清單見 `docs/reference/api-endpoints.md`。

---

## 主要使用路徑

### 1. 首次使用者（B2B onboarding，不開放自助註冊）

**客戶 admin**：
```
收到 /setup?token=xxx 連結 → 填組織名稱 + 管理員帳密 → 建立組織 → 自動發 cookie 直接進站
```

**組織員工**：
```
admin 建帳號 → 收啟用信 → /api/auth/activate?token=xxx 設密碼 → 自動發 cookie 直接進站
```

匿名使用者無法搜尋（`/ask` 需 JWT）；未登入進站會看到 login modal。

### 2. 登入使用者（Email/Password + JWT）

```
登入 → access(15 分)/refresh(7 天) httpOnly cookie → Init Sync → 進入工作區
```

**步驟**：
1. Login modal 輸入 email/password（同 email 15 分鐘內錯 5 次鎖定）
2. 登入成功觸發 **UserStateSync trigger A**：清空前一使用者殘留 state → `GET /api/user/init` 一次拉回 user/org/role/sessions/shared_sessions/preferences → hydrate UI
3. 左側邊欄載入個人 sessions + 組織空間分享 sessions
4. 之後 session 內容自動存 server（PostgreSQL），跨裝置同步

### 3. 查詢流程

```
選模式 → 輸入自然語言查詢 → Enter 送出 → SSE 串流漸進渲染 → 追問或開新對話
```

---

## 搜尋模式（三模式 + Live Research）

前端 `currentMode` 對應（`frontend-spec.md` §4.1）：

### 新聞搜尋（search）
```
查詢 → SSE /ask → 文章卡片 + AI 摘要漸進渲染 → 點卡片開啟來源
```
- 結果依 ranking pipeline 排序（LLM + XGBoost shadow + MMR）
- unified 模式單一 SSE 流回傳文章 + 摘要 + AI 回答

### 進階搜尋（deep_research）
```
查詢 → SSE /api/deep_research →（必要時澄清問題）→ 研究進度 → 報告 + 知識圖譜 + 推論鏈
```
- 報告含信心度、參考資料來源；知識圖譜可編輯後經 `/api/research/rerun` 選擇性重跑
- 同時只能進行一個 DR（併發限制，超限 429）

### 自由對話（chat）
```
訊息 → POST /ask（帶 research context）→ 多輪對話
```
- Deep Research 完成後可就報告內容追問

### Live Research（feature flag `live_research`）
```
查詢 → SSE /api/live_research → 6 階段對話式研究（結構提案 → 資料蒐集 → 風格 → 格式 → 撰寫 → 匯出）
     → 每階段 checkpoint 停下等使用者確認 → /api/live_research/continue 續跑（可回上一步/重來）
```

---

## 介面狀態

### 載入與串流
| 狀態 | 顯示 |
|------|------|
| 查詢處理中 | 處理中狀態（`setProcessingState`） |
| SSE 串流中 | 結果漸進出現（骨架屏 + 逐批渲染） |
| DR 進行中 | 「深度研究進行中」+ 階段名稱（不露技術細節） |
| LR 進行中 | 讀豹敘事訊息（narration）逐則推入對話 |

### 錯誤狀態（對齊 server 實際回應）
| 情況 | Server 回應 | 前端行為 |
|------|------------|---------|
| 未登入 / token 失效 | 401（`token_expired` / `invalid_token`） | 先 refresh；失敗走 trigger D：清 state + 回 login modal |
| 查詢過長 | 400「查詢過長，請縮短至 500 字元以內」 | 顯示訊息 |
| 併發超限 | 429「目前查詢量過大」/「Deep Research 同時只能進行一個」 | 顯示訊息 + retry_after 30s |
| DR/LR 功能關閉 | 503（kill switch / feature flag） | 顯示「功能暫時關閉/尚未啟用」 |
| auth rate limit | 429（login 10/min 等） | 顯示訊息 |

---

## 失敗復原

### SSE 串流中斷
- 當前 SSE 走 **POST fetch reader**（`handlePostStreamingRequest`；舊 EventSource GET 路徑已 deprecated 無 caller）
- **Deep Research**：client 斷線 → server 取消背景 research task
- **Live Research**：client 斷線**不取消** — server 把當前 stage 跑到下個 checkpoint 存檔（防呆燒錢上限由 orchestrator enforce），重連後可續跑（lr-sse-reconnect-resume）

### 認證失敗
```
401 → refresh token → 成功則重試原請求；失敗 → UserStateSync fullReset → login modal
```
- 所有 auth failure path 走同一 cleanup（trigger C/D），不分支實作
- 快速連搜的主動取消（AbortError）不觸發 auth failure（是正常取消，見 login-spec §1F-C trigger D）

### 跨使用者身分保護（B2B 共用電腦）
- 核心 invariant：`cache.user_id == JWT.user_id`，由 7 個 sync trigger 維護（login-spec §1F-C）
- SSE envelope 帶 `user_id` stamp，mismatch 即 abort stream 並重新同步（trigger G）
- logout 無條件清 6 個 user-scoped localStorage keys；device-scoped UI 偏好（大字體等）保留

---

## 可及性與操作

### 鍵盤
| 快捷鍵 | 功能 |
|--------|------|
| `Enter` | 送出搜尋 |
| `Shift + Enter` | 換行 |
| `Escape` | 關閉 popup / 取消搜尋 |

### 顯示
- 大字體模式（`body.large-font`，搜尋框右上切換，device-scoped 偏好跨登入保留）
- 響應式斷點：>1200px 三欄；768-1200px 隱藏左側邊欄；<768px 單欄

### 效能
- 漸進式渲染（`requestAnimationFrame` 逐批渲染文章卡片）
- 搜尋取消機制（`searchGenerationId` + AbortController；新查詢自動取消前一請求）
- `/sites_config` server 端 5 分鐘 cache

---

## 隱私與安全

1. Token 存放：httpOnly + Secure + SameSite=Lax cookie（不放 localStorage，BP-1）
2. 資料隔離：所有 session/文件/偏好查詢帶 `org_id` filter（multi-tenant）
3. 上傳配額：org 儲存空間上限（超限 413），檔案處理狀態可追蹤
4. CSP / CORS / rate limit / prompt guardrails middleware 全鏈啟用
5. 稽核：登入、session 操作、admin 操作寫 `audit_logs`（fire-and-forget）

---

*更新：2026-07-11（對齊 login-spec / frontend-spec / webserver routes 現況；移除 OAuth 與 list/summarize/generate 過時描述）*
