# 摰嗥蝑 Crawler ?函蔡閬

## 璁膩

銝?????砍?鞎????餉?鞎砌葉?遙??UDN sitemap gap fill + MOEA backfill嚗?
蝑?芾?鞎祉??crawler-only嚗?銝? indexing pipeline??
TSV ?園?敺?獢?頝?indexing ??PostgreSQL??

---

## 蝖祇?閬

| ?嗡辣 | 閬 | 閰摯 |
|------|------|------|
| CPU | i5-8265U (4C/8T, 3.9GHz boost) | 頞喳? |
| RAM | **4GB DDR4** | ??雿?crawler-only ?舐 |
| ?脣? | **1TB HDD** | SQLite 頛雿摰孵? |
| GPU | MX250 2GB | ?其???|
| ?餅? | ?批遣 | 憭拍 UPS嚗?怠??颱??瘀? |

---

## 閮擃?蝞?

```
Windows 10/11:           ~2.5 GB
Chrome Remote Desktop:   ~0.1 GB
Dashboard server:        ~0.1 GB
Crawler (1 source):      ~0.2 GB
?????????????????????????????????
??:                    ~2.9 GB / 4 GB ??
瘜冽?嚗?閬??? Chrome ?汗?剁???0.5-1GB嚗?
```

**??**嚗?甈∪頝???source嚗??OOM??

---

## 蝑鞈?

| ? | ??|
|------|-----|
| Windows 雿輻??| `YOUR_USER` |
| 撠?頝臬? | `C:\Users\YOUR_USER\nlweb\` |
| Python | 3.11.9 |
| Git | 2.53 |
| Dashboard port | 8001 |
| ?垢摮? | Chrome Remote Desktop |

---

## ?啣??寞?閮剖?

### indexing/__init__.py 撌脫?蝛?

蝑銝? qdrant_client 蝑???憟辣嚗indexing/__init__.py` ?寧嚗?
```python
# dashboard-only
```

**瘜冽?**嚗git pull` ???????嚗?摰??皜征嚗?
```powershell
cd C:\Users\YOUR_USER\nlweb\code\python
python -c "import os;[open(os.path.join('indexing',f),'w').write('# dashboard-only\n') or print('Fixed:',f) for f in os.listdir('indexing') if 'init' in f and f.endswith('.py')]"
```

### PowerShell 瘜冽?鈭?

PowerShell ????`__init__` ??摨??遙雿???`__init__.py` ??雿?
?賜 Python 靘?嚗?閬 PowerShell ?湔??瑼???

---

## ?脖??身摰?

撌脤? PowerShell嚗恣?嚗身摰?

```powershell
# ?銝???
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 0

# ???Ｗ?銝?隞颱???
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT
```

### 撽??孵?

1. ??蝑??
2. 蝑?1 ??
3. ??Chrome Remote Desktop ???嚗Ⅱ隤??臬???
4. ??`http://localhost:8001` 蝣箄? Dashboard 甇?虜

---

## 撣貊?誘

### ?? Dashboard

```powershell
cd C:\Users\YOUR_USER\nlweb\code\python
python -m indexing.dashboard_server
```

### ???芸???

??`crawler\remote\start-dashboard.bat` ?曉??鞈?憭橘?
```
Win+R ??shell:startup ??鞎澆 start-dashboard.bat ?敺?
```

? Task Scheduler嚗?
- 閫貊嚗蝙?刻?交?
- ??嚗???撘???`python`
- 撘嚗-m indexing.dashboard_server`
- 撌乩??桅?嚗C:\Users\YOUR_USER\nlweb\code\python`

**瘜冽?**嚗start-dashboard.bat` 鋆∠?頝臬?撖怎???`C:\Users\User\`嚗?
蝑銝??寞? `C:\Users\YOUR_USER\`??

### ??隞餃?

```powershell
# Step 1: MOEA backfill ??憛怨? 2024-01 ~ 2025-03 蝻箏嚗?頝?2-4 撠?嚗?
Invoke-RestMethod -Method POST -Uri "http://localhost:8001/api/indexing/fullscan/start" -ContentType "application/json" -Body '{"sources":["moea"],"start_id":100000,"end_id":122000}'

# Step 2: UDN sitemap ??憛怨? 2024-10 ~ 2025-02 蝻箏嚗OEA 摰?敺?頝?
Invoke-RestMethod -Method POST -Uri "http://localhost:8001/api/indexing/crawler/start" -ContentType "application/json" -Body '{"source":"udn","mode":"sitemap","date_from":"202410","date_to":"202502"}'
```

### ?迫 Crawler

```powershell
# ?亦?????task_id
Invoke-RestMethod -Uri "http://localhost:8001/api/indexing/fullscan/status"

# ?迫?? task
Invoke-RestMethod -Method POST -Uri "http://localhost:8001/api/indexing/crawler/stop" -ContentType "application/json" -Body '{"task_id":"<TASK_ID>"}'
```

### ?亦??Ｗ

```powershell
# TSV 瑼?
dir C:\Users\YOUR_USER\nlweb\data\crawler\articles\

# 閮擃?Task Manager ??
Get-Process python | Select-Object Name, WorkingSet64
```

---

## ?蝭?嚗?璈?雿?2026-02-13 ?湔嚗?

### 蝮質汗

**??**嚗itemap 璅∪?敺?*??啣????*??嚗ewest ??oldest嚗?

```
獢?嚗蜓??               蝑嚗葉??              GCP嚗???
?????????????????        ?????????????????        ?????????????????
Chinatimes sitemap       MOEA backfill             UDN sitemap Phase 1
  ~15M 蝭?                 ID 100K ??122K            2025-03 ??2025-07
einfo full_scan            ??00 蝭?                  ~150 蝭???, 97% hit
  238K ??270K嚗roxy嚗?  UDN sitemap               UDN sitemap Phase 2
LTN ??摰?                 2024-10 ??2025-02         2024-01 ??2024-09
  693K 蝭?                  gap fill                  cron ?芸??亙?
CNA ??摰?
  242K 蝭?
```

### 蝑 Source ??

| ?? | Source | 蝭? | ?摯??| 隤芣? |
|------|--------|------|--------|------|
| 1 | **MOEA backfill** | ID 100,000 ??122,000 | ~500 蝭?| 撠?嚗?-4 撠?摰? |
| 2 | **UDN sitemap** | 2024-10 ??2025-02 | ~30K 蝭?| 100% hit rate嚗OEA 摰?敺?頝?|

銝??摰???銝??甈∪頝???source??

---

## HDD ?瘜冽?鈭?

SQLite ??HDD 銝璈?撖怨??ｇ?

- 摰? VACUUM嚗python -c "import sqlite3; c=sqlite3.connect(r'C:\Users\YOUR_USER\nlweb\data\crawler\crawled_registry.db'); c.execute('VACUUM'); c.close()"`
- 銝甈∪頝???source嚗????crawler ??撖?SQLite嚗?
- 憒? I/O 憭芣嚗??USB ?刻澈蝣 registry.db

---

## ?琿 / ??敺拙?

1. 蝑?批遣?餅? = 憭拍 UPS嚗?怠??颱??瘀?
2. ?瑟????????璈????芸??餃 ??Task Scheduler ?? Dashboard
3. ??Chrome Remote Desktop ??脖?嚗?????crawler嚗atermark ?芸?蝥?嚗?

### Windows ?芸??餃

```
Win+R ??netplwiz ?????暸???撓?乩蝙?刻?蝔勗?撖Ⅳ?? 頛詨撖Ⅳ ??蝣箏?
```

### Windows Update ?脰????

```
閮剖? ??Windows Update ???脤??賊? ??瘣餃??挾 ??0:00 ~ 23:59
```

---

## ?垢摮?

### SSH嚗蜓閬?扳撘?

獢?撌脰身摰?SSH key ??蝣潛?伐??臬? Claude Code ?湔????

**???鞈?**嚗?
| ? | ??|
|------|-----|
| IP | `YOUR_LAPTOP_LAN_IP`嚗?蝬莎? |
| 雿輻??| `YOUR_USER` |
| 隤? | ED25519 key嚗?璈?`~/.ssh/id_ed25519`嚗?|
| ?祇雿蔭 | 蝑 `C:\ProgramData\ssh\administrators_authorized_keys` |

**蝑 SSH Server 閮剖?**嚗歇摰?嚗?
```powershell
# 蝞∠???PowerShell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
```

**瘜冽?**嚗??颱蝙?刻?蝞∠??⊥???Windows OpenSSH ?蕭??`~\.ssh\authorized_keys`嚗?
敹???`C:\ProgramData\ssh\administrators_authorized_keys`??

### SSH ???誘嚗?獢??瑁?嚗?

```bash
# 皜祈岫???
ssh YOUR_USER@YOUR_LAPTOP_LAN_IP "echo connected && hostname"

# ??Full Scan ???
ssh YOUR_USER@YOUR_LAPTOP_LAN_IP "curl -s http://localhost:8001/api/indexing/fullscan/status"

# ??Crawler ???
ssh YOUR_USER@YOUR_LAPTOP_LAN_IP "curl -s http://localhost:8001/api/indexing/crawler/status"

# ??Python 蝔?
ssh YOUR_USER@YOUR_LAPTOP_LAN_IP "tasklist | findstr python"

# ?亥??園?雿輻
ssh YOUR_USER@YOUR_LAPTOP_LAN_IP "powershell -c \"Get-Process python | Select-Object Name, WorkingSet64\""

# ??TSV ?Ｗ
ssh YOUR_USER@YOUR_LAPTOP_LAN_IP "dir C:\Users\YOUR_USER\nlweb\data\crawler\articles\"
```

**IP 霈???*嚗???IP ??DHCP ??嚗?賣?霈??啁Ⅱ隤?
```powershell
# ?函??颱?
ipconfig
```

### Chrome Remote Desktop嚗???GUI 摮?嚗?

1. 蝑摰? Chrome + Chrome Remote Desktop嚗emotedesktop.google.com嚗?
2. 閮剖??垢摮?嚗身摰?PIN嚗?
3. 敺遙雿?蝵桃? Chrome ?汗?券??

### ?亙虜撌⊥炎嚗?憭?2 ??嚗?

**?孵? A嚗SH嚗?佗?敺?璈?Claude Code ?湔?伐?**
```bash
ssh YOUR_USER@YOUR_LAPTOP_LAN_IP "curl -s http://localhost:8001/api/indexing/fullscan/status"
```
蝣箄? running 隞餃???progress ??憓?uccess ?詨?甇?虜??

**?孵? B嚗hrome Remote Desktop嚗?閬?GUI ????**
1. Chrome Remote Desktop ??脩???
2. ??`http://localhost:8001` ??Dashboard
3. 蝣箄? Found ?詨???憓
4. Task Manager 蝣箄? RAM < 3.5GB

---

## ?亦?蝯?嚗????

```powershell
# 1. ?迫 crawler
Invoke-RestMethod -Method POST -Uri "http://localhost:8001/api/indexing/crawler/stop" -ContentType "application/json" -Body '{"task_id":"<TASK_ID>"}'

# 2. 銴ˊ articles ??registry ?圈頨怎??雯頝臬鈭?
# ? articles: C:\Users\YOUR_USER\nlweb\data\crawler\articles\
# ? registry: C:\Users\YOUR_USER\nlweb\data\crawler\crawled_registry.db

# 3. ?冽?璈?雿?registry
python crawler/remote/merge_registry.py laptop-registry.db data/crawler/crawled_registry.db

# 4. 獢?頝?indexing
cd code/python
python -m indexing.pipeline --tsv-dir <laptop-articles-dir>
```

---

## ?啣虜??

### Dashboard ??

?? PowerShell 閬?嚗??圈?銝??
```powershell
cd C:\Users\YOUR_USER\nlweb\code\python
python -m indexing.dashboard_server
```

### Crawler ?∩?嚗rogress 銝?頞? 10 ??嚗?

1. Dashboard 銝? Stop
2. 蝑?30 蝘?
3. ???????source嚗atermark ?芸?蝥?嚗?

### 閮擃?頞喉?RAM > 3.5GB嚗?

1. ?? Chrome ?汗?剁???Chrome Remote Desktop 銝?閬??祆? Chrome嚗?
2. Task Manager ???嗡?銝?閬?蝔?
3. 憒???憭??迫 crawler嚗ACUUM registry.db嚗???

### git pull 敺?Dashboard 憯?

`__init__.py` 鋡怨????????唳?蝛綽?
```powershell
cd C:\Users\YOUR_USER\nlweb\code\python
python -c "import os;[open(os.path.join('indexing',f),'w').write('# dashboard-only\n') or print('Fixed:',f) for f in os.listdir('indexing') if 'init' in f and f.endswith('.py')]"
```
