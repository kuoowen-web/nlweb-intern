#!/bin/bash
# GCP Chinatimes 接力腳本 — 測試版
# 驗證：API 呼叫 → Chinatimes sitemap 能在 GCP 上正常啟動
# 只跑 1 天 (202401 → 202401) 的量，確認後手動停止
#
# 用法：直接執行
#   bash /home/User/nlweb/scripts/gcp-chinatimes-test.sh

PYTHON="/home/User/nlweb/venv/bin/python3"
REGISTRY_DB="/home/User/nlweb/data/crawler/crawled_registry.db"
API="http://localhost:8001"

# 測試用：只跑 1 個月
TEST_DATE_FROM="202401"
TEST_DATE_TO="202401"

echo ""
echo "=== $(date '+%Y-%m-%d %H:%M') Chinatimes 接力測試 ==="

# Step 1: 確認 Dashboard 活著
echo "[1] 檢查 Dashboard..."
dashboard_check=$(curl -s -o /dev/null -w "%{http_code}" "${API}/api/indexing/crawler/status" --max-time 5)
if [ "$dashboard_check" != "200" ]; then
    echo "FAIL: Dashboard 無回應 (HTTP ${dashboard_check})"
    exit 1
fi
echo "  Dashboard OK"

# Step 2: 確認沒有其他 running task（避免 OOM）
echo "[2] 檢查是否有 running task..."
running_count=$($PYTHON -c "
import json, urllib.request
data = json.load(urllib.request.urlopen('${API}/api/indexing/crawler/status'))
running = [t for t in data.get('tasks', []) if t['status'] == 'running']
print(len(running))
for t in running:
    print(f'  running: {t[\"task_id\"]} ({t[\"source\"]} {t[\"mode\"]})')
" 2>&1)

echo "$running_count"
first_line=$(echo "$running_count" | head -1)
if [ "$first_line" != "0" ]; then
    echo "WARNING: 有 ${first_line} 個 task 在跑，GCP 1GB RAM 可能不夠"
    echo "繼續測試（因為是測試版）..."
fi

# Step 3: 啟動 Chinatimes sitemap（測試範圍）
echo "[3] 啟動 Chinatimes sitemap ${TEST_DATE_FROM} → ${TEST_DATE_TO}..."
api_result=$(curl -s -X POST "${API}/api/indexing/crawler/start" \
    -H "Content-Type: application/json" \
    -d "{\"source\":\"chinatimes\",\"mode\":\"sitemap\",\"date_from\":\"${TEST_DATE_FROM}\",\"date_to\":\"${TEST_DATE_TO}\"}")

echo "API 回應: ${api_result}"

# Step 4: 驗證 task 是否真的啟動
echo "[4] 等待 10 秒後驗證..."
sleep 10

verify=$($PYTHON -c "
import json, urllib.request
data = json.load(urllib.request.urlopen('${API}/api/indexing/crawler/status'))
for t in data.get('tasks', []):
    if t['source'] == 'chinatimes' and t['status'] == 'running':
        s = t.get('stats', {})
        print(f'VERIFIED: {t[\"task_id\"]} is running')
        print(f'  PID: {t.get(\"pid\", \"?\")}, Progress: {s.get(\"progress\", 0)}')
        print(f'  Success: {s.get(\"success\", 0)}, Failed: {s.get(\"failed\", 0)}, Blocked: {s.get(\"blocked\", 0)}')
        exit(0)
print('FAIL: 沒有找到 running 的 chinatimes task')
" 2>&1)

echo "$verify"

if echo "$verify" | grep -q "VERIFIED"; then
    echo ""
    echo "=== 測試通過！Chinatimes sitemap 可在 GCP 正常啟動 ==="
    echo "請手動檢查 blocked 數量，確認 Cloudflare 沒有封鎖 GCP IP"
    echo "測試完成後可用以下指令停止："
    task_id=$(echo "$api_result" | $PYTHON -c "import sys,json; print(json.load(sys.stdin).get('task_id','?'))" 2>/dev/null)
    echo "  curl -s -X POST ${API}/api/indexing/crawler/stop -H 'Content-Type: application/json' -d '{\"task_id\":\"${task_id}\"}'"
else
    echo ""
    echo "=== 測試失敗：task 未成功啟動 ==="
fi
