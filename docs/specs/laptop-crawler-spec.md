# 家用筆電 Crawler 部署規格

## 概述

三機協作分散爬取負載。筆電負責中量任務（UDN sitemap gap fill + MOEA backfill）。
筆電只負責爬取（crawler-only），不跑 indexing pipeline。
TSV 收集後回桌機跑 indexing → PostgreSQL。

---

## 硬體規格

| 零件 | 規格 | 評估 |
|------|------|------|
| CPU | i5-8265U (4C/8T, 3.9GHz boost) | 足夠 |
| RAM | **4GB DDR4** | 吃緊但 crawler-only 可用 |
| 儲存 | **1TB HDD** | SQLite 較慢但可容忍 |
| GPU | MX250 2GB | 用不到 |
| 電池 | 內建 | 天然 UPS（短暫停電不斷） |

---

## 記憶體預算

```
Windows 10/11:           ~2.5 GB
Chrome Remote Desktop:   ~0.1 GB
Dashboard server:        ~0.1 GB
Crawler (1 source):      ~0.2 GB
─────────────────────────────────
合計:                    ~2.9 GB / 4 GB ✅
注意：不要同時開 Chrome 瀏覽器（吃 0.5-1GB）
```

**重要**：一次只跑一個 source，避免 OOM。

---

## 筆電資訊

| 項目 | 值 |
|------|-----|
| Windows 使用者 | `Mounai` |
| 專案路徑 | `C:\Users\Mounai\nlweb\` |
| Python | 3.11.9 |
| Git | 2.53 |
| Dashboard port | 8001 |
| 遠端存取 | Chrome Remote Desktop |

---

## 環境特殊設定

### indexing/__init__.py 已清空

筆電不裝 qdrant_client 等重量級套件，`indexing/__init__.py` 改為：
```python
# dashboard-only
```

**注意**：`git pull` 會覆蓋回原始版本，拉完需重新清空：
```powershell
cd C:\Users\Mounai\nlweb\code\python
python -c "import os;[open(os.path.join('indexing',f),'w').write('# dashboard-only\n') or print('Fixed:',f) for f in os.listdir('indexing') if 'init' in f and f.endswith('.py')]"
```

### PowerShell 注意事項

PowerShell 會吃掉 `__init__` 的雙底線。任何涉及 `__init__.py` 的操作，
都用 Python 來做，不要用 PowerShell 直接操作檔名。

---

## 防休眠設定

已透過 PowerShell（管理員）設定：

```powershell
# 插電不休眠
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 0

# 蓋上螢幕不做任何動作
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT
```

### 驗證方式

1. 蓋上筆電蓋子
2. 等 1 分鐘
3. 用 Chrome Remote Desktop 連線，確認仍可存取
4. 開 `http://localhost:8001` 確認 Dashboard 正常

---

## 常用指令

### 啟動 Dashboard

```powershell
cd C:\Users\Mounai\nlweb\code\python
python -m indexing.dashboard_server
```

### 開機自動啟動

把 `crawler\remote\start-dashboard.bat` 放入啟動資料夾：
```
Win+R → shell:startup → 貼入 start-dashboard.bat 的捷徑
```

或用 Task Scheduler：
- 觸發：使用者登入時
- 動作：啟動程式 → `python`
- 引數：`-m indexing.dashboard_server`
- 工作目錄：`C:\Users\Mounai\nlweb\code\python`

**注意**：`start-dashboard.bat` 裡的路徑寫的是 `C:\Users\User\`，
筆電上要改成 `C:\Users\Mounai\`。

### 啟動任務

```powershell
# Step 1: MOEA backfill — 填補 2024-01 ~ 2025-03 缺口（先跑，2-4 小時）
Invoke-RestMethod -Method POST -Uri "http://localhost:8001/api/indexing/fullscan/start" -ContentType "application/json" -Body '{"sources":["moea"],"start_id":100000,"end_id":122000}'

# Step 2: UDN sitemap — 填補 2024-10 ~ 2025-02 缺口（MOEA 完成後再跑）
Invoke-RestMethod -Method POST -Uri "http://localhost:8001/api/indexing/crawler/start" -ContentType "application/json" -Body '{"source":"udn","mode":"sitemap","date_from":"202410","date_to":"202502"}'
```

### 停止 Crawler

```powershell
# 查狀態，找 task_id
Invoke-RestMethod -Uri "http://localhost:8001/api/indexing/fullscan/status"

# 停止指定 task
Invoke-RestMethod -Method POST -Uri "http://localhost:8001/api/indexing/crawler/stop" -ContentType "application/json" -Body '{"task_id":"<TASK_ID>"}'
```

### 查看產出

```powershell
# TSV 檔案
dir C:\Users\Mounai\nlweb\data\crawler\articles\

# 記憶體（Task Manager 或）
Get-Process python | Select-Object Name, WorkingSet64
```

---

## 分割範圍（三機協作，2026-02-13 更新）

### 總覽

**重要**：Sitemap 模式從**最新往最舊**處理（newest → oldest）。

```
桌機（主力）               筆電（中量）              GCP（輕量）
─────────────────        ─────────────────        ─────────────────
Chinatimes sitemap       MOEA backfill             UDN sitemap Phase 1
  ~15M 篇                  ID 100K → 122K            2025-03 → 2025-07
einfo full_scan            ≈500 篇                   ~150 篇/分鐘, 97% hit
  238K → 270K（proxy）   UDN sitemap               UDN sitemap Phase 2
LTN ✅ 完成                 2024-10 → 2025-02         2024-01 → 2024-09
  693K 篇                   gap fill                  cron 自動接力
CNA ✅ 完成
  242K 篇
```

### 筆電 Source 排程

| 順序 | Source | 範圍 | 預估量 | 說明 |
|------|--------|------|--------|------|
| 1 | **MOEA backfill** | ID 100,000 → 122,000 | ~500 篇 | 小量，2-4 小時完成 |
| 2 | **UDN sitemap** | 2024-10 → 2025-02 | ~30K 篇 | 100% hit rate，MOEA 完成後再跑 |

一個跑完再切下一個。一次只跑一個 source。

---

## HDD 效能注意事項

SQLite 在 HDD 上隨機讀寫較慢：

- 定期 VACUUM：`python -c "import sqlite3; c=sqlite3.connect(r'C:\Users\Mounai\nlweb\data\crawler\crawled_registry.db'); c.execute('VACUUM'); c.close()"`
- 一次只跑一個 source（避免多個 crawler 同時寫 SQLite）
- 如果 I/O 太慢，考慮用 USB 隨身碟放 registry.db

---

## 斷電 / 重啟復原

1. 筆電內建電池 = 天然 UPS（短暫停電不斷）
2. 長時間斷電 → 重開機 → 自動登入 → Task Scheduler 啟動 Dashboard
3. 用 Chrome Remote Desktop 連進來，手動重啟 crawler（watermark 自動續跑）

### Windows 自動登入

```
Win+R → netplwiz → 取消勾選「必須輸入使用者名稱和密碼」→ 輸入密碼 → 確定
```

### Windows Update 防自動重啟

```
設定 → Windows Update → 進階選項 → 活動時段 → 0:00 ~ 23:59
```

---

## 遠端存取

### SSH（主要監控方式）

桌機已設定 SSH key 免密碼登入，可從 Claude Code 直接監控。

**連線資訊**：
| 項目 | 值 |
|------|-----|
| IP | `192.168.1.109`（區網） |
| 使用者 | `mounai` |
| 認證 | ED25519 key（桌機 `~/.ssh/id_ed25519`） |
| 公鑰位置 | 筆電 `C:\ProgramData\ssh\administrators_authorized_keys` |

**筆電 SSH Server 設定**（已完成）：
```powershell
# 管理員 PowerShell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
```

**注意**：筆電使用者有管理員權限，Windows OpenSSH 會忽略 `~\.ssh\authorized_keys`，
必須用 `C:\ProgramData\ssh\administrators_authorized_keys`。

### SSH 監控指令（從桌機執行）

```bash
# 測試連線
ssh mounai@192.168.1.109 "echo connected && hostname"

# 查 Full Scan 狀態
ssh mounai@192.168.1.109 "curl -s http://localhost:8001/api/indexing/fullscan/status"

# 查 Crawler 狀態
ssh mounai@192.168.1.109 "curl -s http://localhost:8001/api/indexing/crawler/status"

# 查 Python 程序
ssh mounai@192.168.1.109 "tasklist | findstr python"

# 查記憶體使用
ssh mounai@192.168.1.109 "powershell -c \"Get-Process python | Select-Object Name, WorkingSet64\""

# 查 TSV 產出
ssh mounai@192.168.1.109 "dir C:\Users\Mounai\nlweb\data\crawler\articles\"
```

**IP 變動時**：筆電 IP 由 DHCP 分配，可能會變。重新確認：
```powershell
# 在筆電上
ipconfig
```

### Chrome Remote Desktop（備用 GUI 存取）

1. 筆電安裝 Chrome + Chrome Remote Desktop（remotedesktop.google.com）
2. 設定遠端存取（設定 PIN）
3. 從任何裝置的 Chrome 瀏覽器連線

### 日常巡檢（每天 2 分鐘）

**方式 A：SSH（推薦，從桌機 Claude Code 直接查）**
```bash
ssh mounai@192.168.1.109 "curl -s http://localhost:8001/api/indexing/fullscan/status"
```
確認 running 任務的 progress 持續增長、success 數字正常。

**方式 B：Chrome Remote Desktop（需要 GUI 操作時）**
1. Chrome Remote Desktop 連進筆電
2. 開 `http://localhost:8001` 看 Dashboard
3. 確認 Found 數字持續增長
4. Task Manager 確認 RAM < 3.5GB

---

## 春節結束：資料收回

```powershell
# 1. 停止 crawler
Invoke-RestMethod -Method POST -Uri "http://localhost:8001/api/indexing/crawler/stop" -ContentType "application/json" -Body '{"task_id":"<TASK_ID>"}'

# 2. 複製 articles 和 registry 到隨身碟或網路共享
# 　 articles: C:\Users\Mounai\nlweb\data\crawler\articles\
# 　 registry: C:\Users\Mounai\nlweb\data\crawler\crawled_registry.db

# 3. 在桌機合併 registry
python crawler/remote/merge_registry.py laptop-registry.db data/crawler/crawled_registry.db

# 4. 桌機跑 indexing
cd code/python
python -m indexing.pipeline --tsv-dir <laptop-articles-dir>
```

---

## 異常處理

### Dashboard 掛了

關掉 PowerShell 視窗，重新開一個：
```powershell
cd C:\Users\Mounai\nlweb\code\python
python -m indexing.dashboard_server
```

### Crawler 卡住（progress 不動超過 10 分鐘）

1. Dashboard 上按 Stop
2. 等 30 秒
3. 重新啟動同一個 source（watermark 自動續跑）

### 記憶體不足（RAM > 3.5GB）

1. 關掉 Chrome 瀏覽器（用 Chrome Remote Desktop 不需要開本機 Chrome）
2. Task Manager 關掉其他不需要的程式
3. 如果還不夠，停止 crawler，VACUUM registry.db，重啟

### git pull 後 Dashboard 壞掉

`__init__.py` 被覆蓋回原版。重新清空：
```powershell
cd C:\Users\Mounai\nlweb\code\python
python -c "import os;[open(os.path.join('indexing',f),'w').write('# dashboard-only\n') or print('Fixed:',f) for f in os.listdir('indexing') if 'init' in f and f.endswith('.py')]"
```
