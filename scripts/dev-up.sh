#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# scripts/dev-up.sh — NLWeb Dev 環境一鍵啟動 helper（Phase 4, 方案 B）
#
# 用途：取代手動 `docker compose up` + `cd code/python && alembic upgrade head`
# 兩步驟，並在 alembic 跑之前確保 PG container 真的 ready。
#
# 流程：
#   1. cd 到 repo root
#   2. 檢查 PG env var（POSTGRES_CONNECTION_STRING / DATABASE_URL /
#      ANALYTICS_DATABASE_URL 至少一個），避免 alembic 偷偷走 SQLite fallback
#   3. docker compose -f docker-compose.dev.yml up -d
#   4. Poll pg_isready + psql SELECT 1，雙重 ready check（避 race）
#   5. 用 venv myenv311 跑 `alembic upgrade head`
#   6. echo 成功 + 提示啟動 server 指令
#
# 後續啟動 server 仍是手動：
#   cd code/python && python app-aiohttp.py
#
# 任何步驟失敗 → set -euo pipefail 自動 exit non-zero，CEO 看 stderr 處理。
# ---------------------------------------------------------------------------

set -euo pipefail

# --- Step 1: cd 到 repo root ------------------------------------------------
# 不論從哪裡呼叫 script，都先進 repo 根目錄
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
echo "[dev-up] repo root: ${REPO_ROOT}"

# --- Step 2: Source .env + verify PG env var --------------------------------
# alembic env.py fallback chain：POSTGRES_CONNECTION_STRING → DATABASE_URL
# → ANALYTICS_DATABASE_URL → SQLite。三個都沒設會偷偷建 data/auth/auth.db，
# 然後 server 起動時 sanity check 仍會擋下（因 PG 是空的），但會浪費時間。
# 在此先 verify，fail fast。
if [[ -f .env ]]; then
  # 用 set -a 把 .env 裡的變數自動 export（不會碰到 set -u 因為 .env 已存在）
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  echo "[dev-up] sourced .env"
else
  echo "[dev-up] WARN: .env 不存在，依賴 shell 環境變數"
fi

if [[ -z "${POSTGRES_CONNECTION_STRING:-}" \
   && -z "${DATABASE_URL:-}" \
   && -z "${ANALYTICS_DATABASE_URL:-}" ]]; then
  echo "[dev-up] ERROR: 找不到任何 PG 連線環境變數" >&2
  echo "[dev-up]   請在 .env 設定 POSTGRES_CONNECTION_STRING（建議值）" >&2
  echo "[dev-up]   範例：POSTGRES_CONNECTION_STRING=postgresql://nlweb:<pw>@localhost:5432/nlweb" >&2
  echo "[dev-up]   否則 alembic 會偷偷走 SQLite fallback、跑到錯的 DB" >&2
  exit 1
fi

# --- Step 3: 啟 PG container -----------------------------------------------
echo "[dev-up] 啟動 PG container..."
docker compose -f docker-compose.dev.yml up -d

# --- Step 4: Wait PG ready（雙重檢查，避 race）-----------------------------
# pg_isready 通過後再用 psql SELECT 1 多檢一層 — init.sql 還在跑時
# pg_isready 可能已回 OK 但 connection 仍 refused。
MAX_ATTEMPTS=30
ATTEMPT=0
echo "[dev-up] 等待 PG ready（最多 ${MAX_ATTEMPTS} 次嘗試，每 2 秒一次）..."
until docker compose -f docker-compose.dev.yml exec -T postgres \
        pg_isready -U nlweb -d nlweb >/dev/null 2>&1 \
     && docker compose -f docker-compose.dev.yml exec -T postgres \
        psql -U nlweb -d nlweb -c "SELECT 1" >/dev/null 2>&1; do
  ATTEMPT=$((ATTEMPT + 1))
  if (( ATTEMPT >= MAX_ATTEMPTS )); then
    echo "[dev-up] ERROR: PG 在 $((MAX_ATTEMPTS * 2)) 秒內未 ready" >&2
    echo "[dev-up]   檢查：docker compose -f docker-compose.dev.yml logs postgres" >&2
    exit 1
  fi
  sleep 2
done
echo "[dev-up] PG ready（${ATTEMPT} 次嘗試）"

# --- Step 5: Alembic upgrade head ------------------------------------------
# 用 CEO 平常的 venv myenv311（含完整 deps + alembic）
VENV_PYTHON="${REPO_ROOT}/myenv311/Scripts/python.exe"
if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "[dev-up] ERROR: 找不到 venv python：${VENV_PYTHON}" >&2
  echo "[dev-up]   請確認 myenv311 venv 已建立並安裝 requirements.txt" >&2
  exit 1
fi

echo "[dev-up] 跑 alembic upgrade head..."
cd code/python
"${VENV_PYTHON}" -m alembic upgrade head
cd "${REPO_ROOT}"

# --- Step 6: 成功 + 提示啟動 server -----------------------------------------
echo ""
echo "[dev-up] OK: dev 環境已就緒"
echo "[dev-up]   PG container：nlweb-dev-postgres（port 5432）"
echo "[dev-up]   Alembic：已 upgrade 到 head"
echo ""
echo "[dev-up] 接下來啟動 server："
echo "  cd code/python && python app-aiohttp.py"
echo ""
