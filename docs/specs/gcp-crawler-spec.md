# GCP e2-micro Crawler 部署規格

## 概述

三機協作分散爬取負載。GCP e2-micro 負責 UDN sitemap backfill + failed URL retry。
Phase 1: 2025-03 → 2025-07（✅ 完成），Phase 2: 2024-01 → 2024-09（✅ cron 自動觸發，運行中）。
Phase 2 完成後自動 retry 所有 failed URLs（cron 接力）。
桌機、筆電、GCP 分割工作範圍，互不重疊。

**重要**：Sitemap 模式從**最新往最舊**處理（newest → oldest）。

---

## 三機工作分配（2026-02-15 更新）

```
桌機（主力）                    筆電（中量）              GCP e2-micro（輕量）
├─ Chinatimes sitemap           ├─ MOEA backfill          ├─ UDN sitemap Phase 1
│  sub-sitemap #1→N（從頭）     │  ID 100K → 122K         │  155K 篇 ✅ 完成
│  目前 #23，進行中             │  進行中 (ID 113K)       ├─ UDN sitemap Phase 2
├─ einfo full_scan              └─ UDN sitemap            │  2024-01 → 2024-09
│  238K → 270K（proxy）            2024-10 → 2025-02      │  ✅ cron 觸發，運行中
├─ LTN ✅ 完成                     gap fill               ├─ Chinatimes sitemap ← NEW
│  693K 篇, watermark 5,342K                              │  sub-sitemap #980→M（從尾）
└─ CNA ✅ 完成                                            │  與桌機夾擊，保持 50 緩衝
   242K 篇, watermark 2026-02-12                          └─ Retry failed URLs
                                                             UDN/LTN/MOEA 共 499 筆
                                                             Phase 2 後 cron 自動接力

                                    完成後 ↓
                                scp articles/ + registry.db
                                    ↓
                                merge_registry.py → 桌機
                                    ↓
                                桌機跑 indexing pipeline → PostgreSQL
```

---

## GCP VM 資訊

| 項目 | 值 |
|------|-----|
| Instance | `nlweb-crawler` |
| Zone | `asia-east1-b` |
| Type | `e2-micro`（免費方案，shared vCPU, 1GB RAM） |
| OS | Debian 12 (bookworm) |
| Disk | 30GB pd-standard |
| Swap | 2GB |
| Python | 3.11 + venv |
| External IP | 動態分配（每次查 `gcloud compute instances list`） |
| GCP Project | `project-ad6eda6e-acac-4f93-97d` |

---

## 記憶體預估

```
Python runtime + modules:  ~60 MB
Crawler (1 source):        ~150 MB
Dashboard server:          ~100 MB
OS + buffer/cache:         ~300 MB
Swap (保險):               2048 MB
────────────────────────────────
合計:                      ~610 MB / 1 GB ✅
```

**重要**：一次只跑一個 source，避免 OOM。

---

## 環境設定

GCP 上透過 `CRAWLER_ENV=gcp` 自動降低併發：

```python
# settings.py 環境感知覆蓋
if CRAWLER_ENV == "gcp":
    concurrent_limit = min(原值, 5)
    delay_range 最小 (0.3, 0.8)
```

Dashboard 啟動時需設定：
```bash
CRAWLER_ENV=gcp python -m indexing.dashboard_server
```

---

## 常用指令

### gcloud 路徑（Windows Git Bash）

```bash
GCLOUD="/c/Users/User/AppData/Local/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd"
# 2026-06-11 修正：實裝在 AppData（per-user install），舊 Program Files (x86) 路徑不存在
# 注意：gcloud.cmd 的參數帶空格會把路徑切爆（cmd 重解析），參數一律不帶空格（如 --display-name=no-spaces）
```

### SSH 連線

```bash
"$GCLOUD" compute ssh nlweb-crawler --zone=asia-east1-b
```

### 開 Dashboard Tunnel

```bash
"$GCLOUD" compute ssh nlweb-crawler --zone=asia-east1-b --ssh-flag="-L 8002:localhost:8001"
# 瀏覽器開 http://localhost:8002（本機 8001 是桌機 Dashboard）
```

### 查看 crawler 狀態

```bash
# 必須用 crawler/status（包含所有 task 類型：fullscan + sitemap + retry）
# fullscan/status 只回傳 fullscan task，會漏掉 sitemap/retry task
"$GCLOUD" compute ssh nlweb-crawler --zone=asia-east1-b --command='curl -s http://localhost:8001/api/indexing/crawler/status'
```

### 啟動 UDN Sitemap

```bash
# UDN sitemap backfill（2025-03 → 2025-07）
"$GCLOUD" compute ssh nlweb-crawler --zone=asia-east1-b \
  --command='curl -s -X POST http://localhost:8001/api/indexing/crawler/start \
  -H "Content-Type: application/json" \
  -d "{\"source\":\"udn\",\"mode\":\"sitemap\",\"date_from\":\"202503\",\"date_to\":\"202507\"}"'
```

### 停止 Crawler

```bash
# 先查 task_id
"$GCLOUD" compute ssh nlweb-crawler --zone=asia-east1-b \
  --command='curl -s http://localhost:8001/api/indexing/fullscan/status'

# 用 task_id 停止
"$GCLOUD" compute ssh nlweb-crawler --zone=asia-east1-b \
  --command='curl -s -X POST http://localhost:8001/api/indexing/crawler/stop \
  -H "Content-Type: application/json" \
  -d "{\"task_id\":\"<TASK_ID>\"}"'
```

### 查看產出

```bash
# TSV 檔案列表
"$GCLOUD" compute ssh nlweb-crawler --zone=asia-east1-b \
  --command='ls -lh ~/nlweb/data/crawler/articles/'

# 記憶體使用
"$GCLOUD" compute ssh nlweb-crawler --zone=asia-east1-b \
  --command='free -m'

# Crawler process
"$GCLOUD" compute ssh nlweb-crawler --zone=asia-east1-b \
  --command='ps aux | grep python'
```

### Dashboard 異常時重啟

```bash
"$GCLOUD" compute ssh nlweb-crawler --zone=asia-east1-b \
  --command='pkill -f dashboard_server; sleep 2; cd ~/nlweb/code/python && source ~/nlweb/venv/bin/activate && CRAWLER_ENV=gcp python -m indexing.dashboard_server > ~/nlweb/data/crawler/logs/dashboard.log 2>&1 &'
```

---

## 分割範圍

### 桌機 Watermark（2026-02-13 快照）

| Source | Type | Watermark | 已爬文章數 | 狀態 |
|--------|------|-----------|-----------|------|
| LTN | sequential | 5,342,046 | 693,273 | **✅ 完成** |
| CNA | date_based | 2026-02-12 | 242,011 | **✅ 完成** |
| UDN | sequential | 8,216,842 | 245,858 | full_scan 完成 + sitemap 三機分工中 |
| Chinatimes | date_based | 2024-02-23 | 35,236 | **桌機 sitemap 進行中** |
| MOEA | sequential | 122,001 | 1,038 | 掃描完成，2024-01~2025-03 需 backfill |
| ESG BT | date_based | 2026-02-13 | 4,167 | **✅ 完成** |
| einfo | sequential | 241,748 | 3,835 | **桌機 full_scan + proxy 進行中** |

**總文章數**：~1,375,000 篇

### ID-to-Date 對照表

**LTN**（月增 ~27K IDs，整體 4,550K → 5,341K）：

| 日期 | ID (P10) | ID (P90) |
|------|----------|----------|
| 2024-01 | 4,552K | 4,567K |
| 2024-06 | 4,694K | 4,719K |
| 2024-12 | 4,883K | 4,907K |
| 2025-06 | 5,064K | 5,089K |
| 2025-09 | 5,163K | 5,170K ← watermark |
| 2026-02 | 5,329K | 5,339K |

**UDN**（月增 ~60K IDs，整體 7,632K → 9,325K）：

> **注意**：Sitemap 模式從**最新往最舊**處理。GCP Phase 1 從 2025-07 往回跑到 2025-03。

| 日期 | 覆蓋狀態 | 負責機台 |
|------|---------|---------|
| 2024-01~09 | GCP Phase 2（**✅ cron 觸發，運行中**） | GCP |
| 2024-10~2025-02 | 筆電 sitemap（待部署） | 筆電 |
| 2025-03~2025-07 | GCP Phase 1（**✅ 完成**，155K 篇） | GCP |
| 2025-08~2026-02 | **✅ 已完成**（桌機 sitemap） | 桌機 |

**MOEA**（ID 110,050 → 121,891，月產 ~30-50 篇）：

| 日期 | 覆蓋狀態 |
|------|---------|
| 2023-06~2025-03 | **缺口**（ID ~110,000~119,000） |
| 2025-04~2026-02 | 完整 |

### GCP 範圍

| Source | 工作 | 範圍 | 狀態 | 備註 |
|--------|------|------|------|------|
| **UDN Phase 1** | sitemap backfill | 2025-03 → 2025-07 | **✅ 完成** | 155K 篇 |
| **UDN Phase 2** | sitemap backfill | 2024-01 → 2024-09 | **運行中** | cron 於 2026-02-14 08:00 自動觸發 |
| **Chinatimes** | sitemap backfill | sub-sitemap #980→0 | **待啟動** | 從尾端往回跑，與桌機夾擊 |
| **Retry** | failed URL retry | UDN/LTN/MOEA | **待觸發** | Phase 2 完成後 cron 自動接力，共 499 筆 |

### GCP 自動接力機制

#### Phase 2 接力（✅ 已觸發）

Phase 1 完成後，`gcp-udn-next.sh` cron 腳本自動啟動 Phase 2：

- **檢查條件**：2025-03~07 每月 UDN 文章數都 > 27,000 篇
- **觸發動作**：呼叫 API 啟動 UDN sitemap 202401→202409
- **自我清除**：觸發後自動從 cron 移除
- **實際觸發**：2026-02-14 08:00（04:00/06:00 檢查未達標，08:00 通過）
- **部署腳本**：`scripts/gcp-udn-next.sh`（已從 cron 移除）

#### Retry 接力（待觸發）

Phase 2 完成後，`gcp-retry-next.sh` cron 腳本自動 retry 所有 failed URLs：

- **檢查條件**：沒有 running task（代表 Phase 2 已結束）
- **觸發動作**：依序 retry UDN (301) → LTN (167) → MOEA (31) 的 failed URLs
- **每個 source 跑完再跑下一個**（避免 OOM）
- **自我清除**：全部完成後自動從 cron 移除
- **排程**：每 2 小時檢查一次
- **部署腳本**：`scripts/gcp-retry-next.sh`

```bash
# cron 設定（已部署在 GCP）
0 */2 * * * /home/User/nlweb/scripts/gcp-retry-next.sh >> /home/User/nlweb/data/crawler/logs/retry-next.log 2>&1
```

#### Chinatimes 雙機協作（待啟動）

UDN Phase 2 + Retry 完成後，手動啟動 Chinatimes sitemap 從尾端往回跑：

- **方式**：`nohup` 背景長跑腳本（非 cron）
- **範圍**：sub-sitemap #980 往 #0 方向（桌機從 #1 往 #1000）
- **停止條件**：距離桌機 50 個 sub-sitemap 時自動停止
- **適應性批次**：根據 hit rate 自動調整（空區間加速、密集區減速）
- **State file**：`chinatimes_gcp_state.json`（crash 後自動恢復）
- **部署腳本**：`scripts/gcp-chinatimes-sitemap.sh`

```bash
# 啟動
nohup /home/User/nlweb/scripts/gcp-chinatimes-sitemap.sh \
  >> /home/User/nlweb/data/crawler/logs/chinatimes-sitemap.log 2>&1 &

# 查看進度
tail -50 /home/User/nlweb/data/crawler/logs/chinatimes-sitemap.log

# 停止
kill $(cat /home/User/nlweb/data/crawler/logs/chinatimes-sitemap.pid)
```

### GCP 資源使用量（2026-02-13 快照）

```
RAM:  969 MB total, 52% used（Crawler + Dashboard ~250MB）
CPU:  Mostly idle (shared vCPU)
I/O:  76.7% iowait (network-bound, NOT CPU/RAM-bound)
```

**結論**：GCP e2-micro 網路 I/O 是瓶頸，RAM/CPU 有餘裕。一次只跑一個 source 即可。

---

## 執行計畫

### 前置條件

1. Dashboard 啟動並正常運行（`CRAWLER_ENV=gcp`）
2. `crawled_registry.db` 已從桌機同步過來
3. `merge_registry.py` 包含 `failed_urls` 合併邏輯

### 啟動後巡檢

每天花 2 分鐘：

```bash
# 查狀態（用 crawler/status 才能看到所有 task 類型）
"$GCLOUD" compute ssh nlweb-crawler --zone=asia-east1-b \
  --command='curl -s http://localhost:8001/api/indexing/crawler/status' | python -m json.tool

# 查記憶體
"$GCLOUD" compute ssh nlweb-crawler --zone=asia-east1-b \
  --command='free -m'
```

### 完成後資料收回

```bash
# 1. 下載 articles
"$GCLOUD" compute scp --recurse nlweb-crawler:/home/User/nlweb/data/crawler/articles/ ./articles-gcp/ --zone=asia-east1-b

# 2. 下載 registry
"$GCLOUD" compute scp nlweb-crawler:/home/User/nlweb/data/crawler/crawled_registry.db ./registry-gcp.db --zone=asia-east1-b

# 3. 合併 registry
python crawler/remote/merge_registry.py registry-gcp.db data/crawler/crawled_registry.db

# 4. 桌機跑 indexing
cd code/python
python -m indexing.pipeline --tsv-dir ../../articles-gcp/
```

---

## 檔案清單

```
crawler/remote/
├── setup-gcp.sh              # GCP VM 初始化腳本
├── launch-crawler.sh          # 直接啟動 crawler（不經 Dashboard）
├── nlweb-crawler.service      # Crawler systemd 服務
├── nlweb-dashboard.service    # Dashboard systemd 服務
├── monitor-gcp.sh             # cron 監控腳本
├── merge_registry.py          # Registry DB 合併
├── setup-laptop-windows.ps1   # Windows 筆電防休眠設定
└── start-dashboard.bat        # Windows 開機自動啟動 Dashboard

scripts/
├── gcp-udn-next.sh           # GCP Phase 2 自動接力 cron 腳本（✅ 已觸發並移除）
├── gcp-retry-next.sh          # GCP Retry 自動接力 cron 腳本（待觸發）
├── gcp-chinatimes-sitemap.sh  # GCP Chinatimes sitemap 雙機協作腳本（待啟動）
└── gcp-chinatimes-test.sh     # Chinatimes 測試腳本（已驗證 GCP 可跑 Chinatimes）
```

---

## 異常處理

### Dashboard 掛了

```bash
"$GCLOUD" compute ssh nlweb-crawler --zone=asia-east1-b \
  --command='pkill -f dashboard_server; sleep 2; cd ~/nlweb/code/python && source ~/nlweb/venv/bin/activate && CRAWLER_ENV=gcp python -m indexing.dashboard_server > ~/nlweb/data/crawler/logs/dashboard.log 2>&1 &'
```

### Crawler 卡住（progress 不動）

1. 查 log：`tail -50 ~/nlweb/data/crawler/logs/*.log`
2. 停掉重啟（watermark 會自動續跑）
3. 如果被 rate limit，等 10 分鐘再啟動

### OOM（記憶體不足）

1. `free -m` 確認
2. 確保只跑一個 source
3. 如果 swap 用超過 1GB，考慮降低 `concurrent_limit`

### VM 意外重啟

Dashboard 和 crawler 都不會自動重啟（除非設了 systemd）。
手動重啟 Dashboard，crawler 從 watermark 自動續跑。

---

*更新：2026-02-15*
