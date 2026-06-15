# INTERN_SETUP — 臺灣讀豹（NLWeb）剝離版 repo

這是臺灣讀豹（Taiwan Dubao / NLWeb，新聞自然語言搜尋系統）的**剝離版程式碼 repo**，
專供 intern 做 E2E 開發。

## 這個 repo 是什麼 / 不是什麼

**有**：可跑起來、可做 E2E 的純程式碼 + 演算法 spec + 部署設定範本。

**沒有**（已從原始 repo 剝離）：

- 任何法律 / 商業 / 戰略文檔（`docs/gtm.md`、`docs/decisions.md`、`docs/status.md`、
  `docs/legal/`、`docs/proposal/`、`memory/` 等一律未複製）
- 任何真實憑證（LLM API key、DB 帳密、Qdrant 金鑰、admin 密碼、OAuth secret）—
  全部換成 `YOUR_..._HERE` 形式的 placeholder
- 任何真實使用資料（query log、真實新聞語料 TSV / JSON / embeddings、analytics DB）
- 真實 prod 基礎設施座標（VPS IP、GCP service account、GCS bucket）—
  已 placeholder 化
- 爬蟲（crawler）模組 — E2E 不需要，已整組移除

> 程式碼**不依賴**任何被剝離的敏感文檔（已驗證無 import）。E2E 照跑。

## 環境需求

- **Python 3.11**（**不要**用 3.13，多個依賴尚未支援）
- Windows 上需要 Visual Studio Build Tools（Desktop development with C++）—
  詳見 `SETUP.md`
- （選用）PostgreSQL / Qdrant；本地 dev 可用 SQLite fallback

## 快速開始

```bash
# 1. 建 venv（Python 3.11）+ 裝依賴
bash scripts/setup.sh
#   或手動：python3.11 -m venv myenv311 && 啟用 && pip install -r requirements.txt
#   後端依賴另見 code/python/requirements.txt

# 2. 設定環境變數
#   .env 檔案不在 repo 內（不會 commit）。請向負責人私下索取一份 .env，
#   放到 repo 根目錄。裡面已含可用的 dev 憑證（API key / DB / Qdrant）。
#   .env 永遠不要 commit（.gitignore 已排除）。

# 3. config 檢查
#   config/ 下的 yaml 多數用「環境變數名稱」引用憑證（api_key_env: OPENAI_API_KEY），
#   真實值都從 .env 讀。一般不需改 config，只要 .env 到位即可。
```

## 憑證說明

- **`.env`** → 向負責人私下索取，放 repo 根目錄。**絕不 commit。**
- `config/*.yaml` → 用 `*_env` 引用 .env，通常不用動。
- `scripts/weekly_indexing.sh` → 內含 `YOUR_VPS_HOST` / `YOUR_GCS_BUCKET` /
  `YOUR_PROJECT_NUMBER` placeholder（只有要跑 weekly indexing pipeline 才需要，E2E 用不到）。
- E2E / spec 文件中的 admin 帳密為 `admin@example.com` / `YOUR_ADMIN_PASSWORD` placeholder，
  本地測試請用你自己建立的測試帳號（或向負責人索取測試帳號）。

## 跑 E2E

> 你負責 **E2E 測試**。完整 E2E checklist 與測試題組見 **`docs/e2etest.md`**。

- **本地起 server**：見 `code/python/app-aiohttp.py`（預設 port 8000）/
  `docs/reference/api-endpoints.md`
- **架構總覽**：`docs/reference/systemmap.md`
- **演算法 spec**：`docs/specs/*-spec.md`（bm25 / xgboost / mmr / reasoning / indexing 等）
- **LR（Live Research）E2E**：`docs/specs/mock-bab-playbook.md` 有「用現成 fixture 跑 LR
  而不燒蒐集成本」的操作手冊 + 6 模塊驗收清單。fixture 已附在
  `code/python/tests/fixtures/lr_mock_bab_real/`。

E2E 要搜得到東西，需要少量種子資料 → 見下。

## 種子資料

本 repo **不含**真實新聞語料。E2E 要搜得到結果，需要少量種子資料：

- 向負責人索取一份**去敏感化的小型種子資料**（少量公開新聞），
  再經 indexing pipeline 寫入本地 DB。
- 取得與寫入方式見 `data/seed/README.md`。

> 任何真實語料**不要** commit 進 repo（`.gitignore` 已排除 `data/`、`*.tsv`）。
> 版權 / 來源有疑問 → 先問負責人。

## 程式碼搜尋（建議）

repo 內含 SQLite FTS5 程式碼索引工具（毫秒級搜尋、省 token）：

```bash
python tools/indexer.py --index            # 先建索引（剝離版未附 .db，需自建一次）
python tools/indexer.py --search "關鍵字"
```

## 注意事項

- 不要把任何真實憑證 / 真實語料 commit 進這個 repo。
- `CLAUDE.md` / `AGENTS.md` 內提到的 `memory/`、`docs/status.md` 等內部協作流程文件
  在本剝離版**不存在**，與 E2E 開發無關，可忽略相關段落。
