# Docker 部署最佳實踐

## Python 版本相容性

### 關鍵教訓

**問題**：Dockerfile 使用 Python 3.13，導致 production 失敗。

**根本原因**：
- Dockerfile 使用 Python 3.13
- Python 3.13 太新 → qdrant-client 安裝不完整版本
- `AsyncQdrantClient` 類別存在但**缺少 `search()` 方法**
- 本地開發正常是因為使用 Python 3.11

### 解決方案

1. **使用 Python 3.11**
   ```dockerfile
   FROM python:3.11-slim AS builder       # Line 2
   FROM python:3.11-slim                  # Line 20
   COPY --from=builder /usr/local/lib/python3.11/site-packages ...
   ```

2. **釘選關鍵依賴**
   ```
   qdrant-client==1.11.3
   ```

3. **加入執行時診斷**
   ```python
   logger.critical(f"PYTHON VERSION: {sys.version}")
   logger.critical(f"MODULE HAS method: {'method' in dir(Module)}")
   ```

4. **變更 base image 時清除 Docker build cache**
   - Render："Manual Deploy" → "Clear build cache & deploy"

---

## 除錯 Docker 部署失敗

### 當 production 失敗但本地正常時：

1. **先檢查 Python 版本** - 最常見的「缺少方法」錯誤原因
2. **檢查 Docker build 日誌** - 驗證正確 base image
3. **加入診斷日誌** - 啟動時記錄版本和可用方法
4. **清除 build cache** - 強制完整重建
5. **檢查多個進程** - 舊進程可能仍在執行

### 警示訊號

- 錯誤：`'ClassName' object has no attribute 'method_name'`
- Library import 成功但類別不完整
- 本地正常但 Docker 失敗
- → 可能是 Python 版本不相容

---

## nginx + aiohttp 代理設定

### 關鍵教訓

**問題**：nginx 預設用 HTTP/1.0 做 reverse proxy。aiohttp 的 `web.json_response()` 使用 chunked transfer encoding，但 HTTP/1.0 不支援 chunked → `RuntimeError` → 502。

**解決方案**：每個 nginx location block（包括 `/health`）都必須設：
```nginx
proxy_http_version 1.1;
proxy_set_header Connection "";
proxy_set_header Host $host;
```

對於需要 HTTP/1.0 相容的 endpoint（如 health check），改用 `web.Response(body=json_bytes)` 設 Content-Length 取代 chunked。

---

## Docker COPY 層快取

### 關鍵教訓

**問題**：`docker compose up -d --build` 的 COPY 層快取可能不因檔案變更失效，導致舊程式碼留在 image 裡。

**解決方案**：Dockerfile 在 `COPY code/` 前加 `ARG CACHE_BUST`，CI/CD 傳 `--build-arg CACHE_BUST=$(date +%s)`：
```dockerfile
ARG CACHE_BUST
COPY code/ /app/
```

---

## CI/CD Health Check

### 關鍵教訓

**問題**：`curl -sf http://localhost/health` 打到 nginx 拿 301 redirect 就算成功，永遠不會失敗。

**解決方案**：直接在 container 內測 app：
```yaml
docker exec nlweb-app python -c "import urllib.request; ..."
```

---

## volume-mount 單檔 config 生效（inode trap）

### 關鍵教訓

**問題**：`docker-compose.production.yml` 用單檔掛載 `./nginx.conf:/etc/nginx/conf.d/default.conf:ro`。Linux/Docker 單檔 bind-mount 綁的是 **inode 非路徑**。deploy 的 `git reset --hard` 會 unlink 舊檔寫新檔 → 換新 inode → 運行中的 nginx 容器仍抓**舊 inode** → `nginx -s reload` / `docker exec nginx -t` 全讀**舊 config**，nginx.conf 改了卻 silent 不生效（deploy 顯示成功）。

**解決方案**：deploy.yml 用 `docker compose up -d --force-recreate nginx`（重綁當前 inode = 新 config 生效，~1s downtime），**不要靠 `nginx -s reload`**。syntax 預驗用 ephemeral 容器掛 Host 最新檔 + `--network <現役 nginx 的 net>`（否則 `nginx -t` 解析不到 `proxy_pass` 的 upstream hostname）。驗生效：`docker exec nlweb-nginx grep <新 directive> /etc/nginx/conf.d/default.conf`。零停機根治可改整個目錄掛載（dir mount 追路徑 → reload 即生效）。

---

## 關鍵教訓

1. **Docker 部署失敗時先檢查 Python 版本**
2. **變更 base image 時務必清除 build cache**
3. **釘選依賴版本**避免相容性問題
4. **在模組載入時加入診斷日誌**驗證執行環境
5. **謹慎測試最新 Python** - library 可能尚未準備好
6. **nginx 代理到 aiohttp 必須設 `proxy_http_version 1.1`** — 否則 chunked response crash
7. **Docker COPY 層用 ARG CACHE_BUST 強制更新** — 確保新程式碼進入 image
8. **CI/CD health check 必須直接測 app** — 不要經過 reverse proxy
9. **新增 import 必須同步更新 requirements.txt** — 本地 global 安裝會遮蔽遺漏
10. **路徑解析用環境變數優先** — `.parent` chain 在 Docker 裡目錄層數不同會爆。用 `NLWEB_CONFIG_DIR`、`NLWEB_DATA_DIR` env var，fallback 才用 `.parent` 計算
11. **try/except import 的套件也要列入 requirements.txt** — graceful degradation 掩蓋了缺套件問題
12. **volume-mount 單檔 config 改了要 `--force-recreate`** — 單檔 bind-mount 綁 inode，git reset 換 inode 後 reload 讀舊 config（inode trap）

---

## 部署檢查清單

- [ ] 驗證 Dockerfile 使用 Python 3.11（非 3.13）
- [ ] 釘選關鍵依賴（qdrant-client 等）
- [ ] 加入關鍵 library 執行時診斷
- [ ] 部署前清除 Docker build cache（或使用 CACHE_BUST）
- [ ] nginx 每個 location block 設 `proxy_http_version 1.1`
- [ ] volume-mount 單檔 config（nginx.conf）改動 → deploy 用 `--force-recreate`，不靠 reload（inode trap）
- [ ] CI/CD health check 直接測 app（不經 nginx）
- [ ] 新增 Python import 時確認 requirements.txt 已更新（含 try/except import）
- [ ] 路徑解析使用環境變數（`NLWEB_CONFIG_DIR`、`NLWEB_DATA_DIR`），非 `.parent` 硬算
- [ ] 先在 staging 環境測試
- [ ] 監控日誌中的版本/方法可用性錯誤

---

*更新：2026-04-27*
