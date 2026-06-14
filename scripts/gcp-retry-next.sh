#!/bin/bash
# GCP 接力腳本：UDN Phase 2 完成後 → retry 所有 failed URLs
#
# 完成條件：沒有 running task（代表 UDN Phase 2 已結束）
# 觸發動作：依序 retry 各 source 的 failed URLs (udn 301 + ltn 167 + moea 31)
# 自我清除：觸發後自動從 cron 移除
#
# 部署：
#   crontab -e
#   0 */2 * * * /home/User/nlweb/scripts/gcp-retry-next.sh >> /home/User/nlweb/data/crawler/logs/retry-next.log 2>&1

PYTHON="/home/User/nlweb/venv/bin/python3"
API="http://localhost:8001"
REGISTRY_DB="/home/User/nlweb/data/crawler/crawled_registry.db"

echo ""
echo "=== $(date '+%Y-%m-%d %H:%M') Retry 接力檢查 ==="

# Step 1: Dashboard 健康檢查
dashboard_check=$(curl -s -o /dev/null -w "%{http_code}" "${API}/api/indexing/crawler/status" --max-time 5)
if [ "$dashboard_check" != "200" ]; then
    echo "Dashboard 無回應 (HTTP ${dashboard_check})，下次再檢查。"
    exit 0
fi

# Step 2: 檢查是否還有 running task
running_info=$($PYTHON -c "
import json, urllib.request
data = json.load(urllib.request.urlopen('${API}/api/indexing/crawler/status'))
running = [t for t in data.get('tasks', []) if t['status'] == 'running']
print(len(running))
for t in running:
    src = t.get('source', '?')
    mode = t.get('mode', '?')
    tid = t.get('task_id', '?')
    print('  running: ' + tid + ' (' + src + ' ' + mode + ')')
" 2>&1)

echo "$running_info"
running_count=$(echo "$running_info" | head -1)

if [ "$running_count" != "0" ]; then
    echo "RESULT:FAIL"
    echo "仍有 task 在跑，下次再檢查。"
    exit 0
fi

# Step 3: 確認有 failed URLs 可 retry
failed_info=$($PYTHON -c "
import sqlite3
c = sqlite3.connect('${REGISTRY_DB}')
total = 0
for r in c.execute('SELECT source_id, COUNT(*) FROM failed_urls GROUP BY source_id').fetchall():
    print('  ' + r[0] + ': ' + str(r[1]))
    total += r[1]
print('TOTAL:' + str(total))
c.close()
" 2>&1)

echo "Failed URLs:"
echo "$failed_info"

total_failed=$(echo "$failed_info" | grep "^TOTAL:" | cut -d: -f2)
if [ "$total_failed" = "0" ] || [ -z "$total_failed" ]; then
    echo "沒有 failed URLs 需要 retry。"
    crontab -l 2>/dev/null | grep -v 'gcp-retry-next.sh' | crontab -
    echo "已從 cron 移除。"
    exit 0
fi

# Step 4: 依序 retry 每個 source
echo "RESULT:PASS"
echo ""
echo "沒有 running task！開始 retry failed URLs..."

for source in udn ltn moea; do
    echo ""
    echo "--- Retry ${source} ---"
    api_result=$(curl -s -X POST "${API}/api/indexing/errors/retry" \
        -H "Content-Type: application/json" \
        -d "{\"source\":\"${source}\",\"max_retries\":3,\"limit\":500}")
    echo "API 回應: ${api_result}"

    # 如果有 task 啟動，等它跑完再做下一個
    if echo "$api_result" | grep -q '"task_id"'; then
        task_id=$(echo "$api_result" | $PYTHON -c "import sys,json; print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null)
        echo "等待 ${task_id} 完成..."

        while true; do
            sleep 30
            status=$($PYTHON -c "
import json, urllib.request
data = json.load(urllib.request.urlopen('${API}/api/indexing/crawler/status'))
for t in data.get('tasks', []):
    tid = t.get('task_id', '')
    if tid == '${task_id}':
        print(t.get('status', 'unknown'))
        break
else:
    print('not_found')
" 2>&1)
            echo "  ${task_id}: ${status}"
            if [ "$status" != "running" ]; then
                break
            fi
        done
    fi
done

echo ""
echo "=== 全部 retry 完成 ==="

# 移除自己的 cron job
crontab -l 2>/dev/null | grep -v 'gcp-retry-next.sh' | crontab -
echo "已從 cron 移除。任務完成。"
