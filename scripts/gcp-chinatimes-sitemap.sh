#!/bin/bash
# GCP Chinatimes Sitemap 雙機協作腳本
#
# 桌機從 sub-sitemap #1 往後跑，GCP 從 #980 往回跑，雙機夾擊加速。
# 自管理 long-running 腳本，不靠 cron。用 nohup 背景跑。
#
# 用法：
#   nohup /home/User/nlweb/scripts/gcp-chinatimes-sitemap.sh \
#     >> /home/User/nlweb/data/crawler/logs/chinatimes-sitemap.log 2>&1 &
#
# 停止：
#   kill $(cat /home/User/nlweb/data/crawler/logs/chinatimes-sitemap.pid)

PYTHON="/home/User/nlweb/venv/bin/python3"
API="http://localhost:8001"
REGISTRY_DB="/home/User/nlweb/data/crawler/crawled_registry.db"
STATE_FILE="/home/User/nlweb/data/crawler/chinatimes_gcp_state.json"
PID_FILE="/home/User/nlweb/data/crawler/logs/chinatimes-sitemap.pid"
POLL_INTERVAL=60       # 秒，輪詢 task 狀態
COOLDOWN=30            # 秒，批次間冷卻
DESKTOP_START_POS=23   # 桌機目前在 sub-sitemap #23
BUFFER=50              # 與桌機保持的安全距離

# 寫入 PID 檔（方便外部 kill）
echo $$ > "$PID_FILE"

echo ""
echo "========================================"
echo "  GCP Chinatimes Sitemap 雙機協作"
echo "  PID: $$"
echo "  Started: $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"
echo ""

# --- Helper Functions ---

load_state() {
    if [ -f "$STATE_FILE" ]; then
        OFFSET=$($PYTHON -c "import json; d=json.load(open('$STATE_FILE')); print(d.get('next_offset', 980))" 2>/dev/null)
        BATCH_SIZE=$($PYTHON -c "import json; d=json.load(open('$STATE_FILE')); print(d.get('batch_size', 20))" 2>/dev/null)
        ROUNDS=$($PYTHON -c "import json; d=json.load(open('$STATE_FILE')); print(d.get('rounds_completed', 0))" 2>/dev/null)
        TOTAL_SUCCESS=$($PYTHON -c "import json; d=json.load(open('$STATE_FILE')); print(d.get('total_success', 0))" 2>/dev/null)
        TOTAL_SKIPPED=$($PYTHON -c "import json; d=json.load(open('$STATE_FILE')); print(d.get('total_skipped', 0))" 2>/dev/null)
        CONSECUTIVE_FAILURES=$($PYTHON -c "import json; d=json.load(open('$STATE_FILE')); print(d.get('consecutive_failures', 0))" 2>/dev/null)
        echo "Restored state: offset=$OFFSET, batch=$BATCH_SIZE, rounds=$ROUNDS, success=$TOTAL_SUCCESS"
    else
        OFFSET=980
        BATCH_SIZE=20
        ROUNDS=0
        TOTAL_SUCCESS=0
        TOTAL_SKIPPED=0
        CONSECUTIVE_FAILURES=0
        echo "No state file found, starting fresh: offset=$OFFSET, batch=$BATCH_SIZE"
    fi
}

save_state() {
    $PYTHON -c "
import json
state = {
    'next_offset': $OFFSET,
    'batch_size': $BATCH_SIZE,
    'rounds_completed': $ROUNDS,
    'total_success': $TOTAL_SUCCESS,
    'total_skipped': $TOTAL_SKIPPED,
    'consecutive_failures': $CONSECUTIVE_FAILURES,
    'updated_at': '$(date -Iseconds)'
}
with open('$STATE_FILE', 'w') as f:
    json.dump(state, f, indent=2)
" 2>/dev/null
}

check_dashboard() {
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" "${API}/api/indexing/crawler/status" --max-time 10)
    if [ "$http_code" != "200" ]; then
        echo "Dashboard 無回應 (HTTP ${http_code})"
        return 1
    fi
    return 0
}

wait_for_no_running_task() {
    # 等待任何 running task 完成
    while true; do
        local running_count
        running_count=$($PYTHON -c "
import json, urllib.request
data = json.load(urllib.request.urlopen('${API}/api/indexing/crawler/status'))
running = [t for t in data.get('tasks', []) if t['status'] == 'running']
print(len(running))
" 2>/dev/null)
        if [ "$running_count" = "0" ] || [ -z "$running_count" ]; then
            return 0
        fi
        echo "  有 task 在跑 ($running_count)，等待 ${POLL_INTERVAL}s..."
        sleep "$POLL_INTERVAL"
    done
}

estimate_desktop_pos() {
    # 桌機位置估計：起始 23 + 經過小時數 * 0.5
    local started_at
    started_at=$($PYTHON -c "
import json, os
if os.path.exists('$STATE_FILE'):
    d = json.load(open('$STATE_FILE'))
    print(d.get('started_at', ''))
" 2>/dev/null)
    if [ -z "$started_at" ]; then
        echo "$DESKTOP_START_POS"
        return
    fi
    $PYTHON -c "
from datetime import datetime
try:
    started = datetime.fromisoformat('${started_at}')
    hours = (datetime.now() - started).total_seconds() / 3600
    pos = $DESKTOP_START_POS + int(hours * 0.5)
    print(pos)
except:
    print($DESKTOP_START_POS)
" 2>/dev/null
}

print_coverage() {
    echo "Coverage (by month):"
    $PYTHON -c "
import sqlite3, os
if not os.path.exists('$REGISTRY_DB'):
    print('  (no registry)')
else:
    c = sqlite3.connect('$REGISTRY_DB')
    rows = c.execute('''
        SELECT substr(date_published,1,7) as m, COUNT(*)
        FROM crawled_articles WHERE source_id='chinatimes'
        GROUP BY m ORDER BY m
    ''').fetchall()
    for r in rows:
        print('  ' + str(r[0]) + ': ' + str(r[1]))
    c.close()
" 2>/dev/null
}

# --- Main Loop ---

load_state

# 首次啟動記錄 started_at
if [ "$ROUNDS" = "0" ]; then
    $PYTHON -c "
import json, os
state = {}
if os.path.exists('$STATE_FILE'):
    state = json.load(open('$STATE_FILE'))
state['started_at'] = '$(date -Iseconds)'
with open('$STATE_FILE', 'w') as f:
    json.dump(state, f, indent=2)
" 2>/dev/null
fi

while true; do
    ROUNDS=$((ROUNDS + 1))

    echo ""
    echo "=== $(date '+%Y-%m-%d %H:%M') | Round $ROUNDS ==="

    # 1. Dashboard 健康檢查
    if ! check_dashboard; then
        echo "Dashboard 不可用，等待 5 分鐘..."
        sleep 300
        continue
    fi

    # 2. 等待任何 running task 完成
    wait_for_no_running_task

    # 3. 停止條件檢查
    desktop_pos=$(estimate_desktop_pos)
    echo "Desktop estimated position: sub-sitemap #${desktop_pos}"
    echo "GCP offset: ${OFFSET}, buffer: ${BUFFER}"

    if [ "$OFFSET" -le 0 ]; then
        echo "OFFSET <= 0, 全部掃完！"
        save_state
        break
    fi

    stop_threshold=$((desktop_pos + BUFFER))
    if [ "$OFFSET" -le "$stop_threshold" ]; then
        echo "OFFSET ($OFFSET) <= desktop_pos ($desktop_pos) + buffer ($BUFFER) = $stop_threshold"
        echo "即將與桌機重疊，停止！"
        save_state
        break
    fi

    if [ "$CONSECUTIVE_FAILURES" -ge 3 ]; then
        echo "連續 $CONSECUTIVE_FAILURES 次 task failed，停止！"
        save_state
        break
    fi

    # 4. 啟動 API
    echo "Offset: $OFFSET, Count: $BATCH_SIZE, Sitemaps: ${OFFSET}~$((OFFSET + BATCH_SIZE - 1))"

    api_result=$(curl -s -X POST "${API}/api/indexing/crawler/start" \
        -H "Content-Type: application/json" \
        -d "{\"source\":\"chinatimes\",\"mode\":\"sitemap\",\"date_from\":\"202401\",\"sitemap_offset\":${OFFSET},\"sitemap_count\":${BATCH_SIZE}}")

    echo "API response: ${api_result}"

    # 檢查是否成功啟動
    task_id=$(echo "$api_result" | $PYTHON -c "import sys,json; print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null)
    if [ -z "$task_id" ]; then
        echo "ERROR: 無法啟動 task"
        CONSECUTIVE_FAILURES=$((CONSECUTIVE_FAILURES + 1))
        save_state
        sleep 60
        continue
    fi

    echo "Task started: ${task_id}"
    start_time=$(date +%s)

    # 5. 輪詢直到 task 完成
    while true; do
        sleep "$POLL_INTERVAL"

        task_info=$($PYTHON -c "
import json, urllib.request
data = json.load(urllib.request.urlopen('${API}/api/indexing/crawler/status'))
for t in data.get('tasks', []):
    if t.get('task_id') == '${task_id}':
        stats = t.get('stats', {})
        status = t.get('status', 'unknown')
        success = stats.get('success', 0)
        skipped = stats.get('skipped', 0)
        not_found = stats.get('not_found', 0)
        failed = stats.get('failed', 0)
        sitemaps = stats.get('sitemaps_processed', 0)
        print(status + '|' + str(success) + '|' + str(skipped) + '|' + str(not_found) + '|' + str(failed) + '|' + str(sitemaps))
        break
else:
    print('not_found|0|0|0|0|0')
" 2>/dev/null)

        task_status=$(echo "$task_info" | cut -d'|' -f1)
        echo "  $(date '+%H:%M:%S') ${task_id}: ${task_status} ($(echo "$task_info" | cut -d'|' -f2-6 | tr '|' ','))"

        if [ "$task_status" != "running" ]; then
            break
        fi
    done

    end_time=$(date +%s)
    elapsed=$(( (end_time - start_time) / 60 ))

    # 6. 評估結果
    batch_success=$(echo "$task_info" | cut -d'|' -f2)
    batch_skipped=$(echo "$task_info" | cut -d'|' -f3)
    batch_not_found=$(echo "$task_info" | cut -d'|' -f4)
    batch_failed=$(echo "$task_info" | cut -d'|' -f5)
    batch_sitemaps=$(echo "$task_info" | cut -d'|' -f6)

    echo "Result: ${task_status} (${elapsed}min)"
    echo "  Success: ${batch_success} | Skipped: ${batch_skipped} | Not Found: ${batch_not_found} | Failed: ${batch_failed}"
    echo "  Sitemaps processed: ${batch_sitemaps}"

    if [ "$task_status" = "failed" ]; then
        CONSECUTIVE_FAILURES=$((CONSECUTIVE_FAILURES + 1))
        echo "Task failed! Consecutive failures: $CONSECUTIVE_FAILURES"
        save_state
        sleep 120
        continue
    fi

    # 重置連續失敗計數
    CONSECUTIVE_FAILURES=0

    # 計算 articles_per_sitemap
    if [ "$batch_sitemaps" -gt 0 ] 2>/dev/null; then
        articles_per_sitemap=$((batch_success / batch_sitemaps))
    else
        articles_per_sitemap=0
    fi
    echo "  Articles/sitemap: ${articles_per_sitemap}"

    # 7. 適應性批次大小調整
    old_batch=$BATCH_SIZE
    if [ "$articles_per_sitemap" -eq 0 ]; then
        BATCH_SIZE=$((BATCH_SIZE * 3))
        [ "$BATCH_SIZE" -gt 200 ] && BATCH_SIZE=200
    elif [ "$articles_per_sitemap" -lt 100 ]; then
        BATCH_SIZE=$((BATCH_SIZE * 2))
        [ "$BATCH_SIZE" -gt 100 ] && BATCH_SIZE=100
    elif [ "$articles_per_sitemap" -gt 5000 ]; then
        BATCH_SIZE=$((BATCH_SIZE / 2))
        [ "$BATCH_SIZE" -lt 5 ] && BATCH_SIZE=5
    fi
    if [ "$BATCH_SIZE" != "$old_batch" ]; then
        echo "  Batch size: ${old_batch} -> ${BATCH_SIZE}"
    fi

    # 8. 更新計數
    TOTAL_SUCCESS=$((TOTAL_SUCCESS + batch_success))
    TOTAL_SKIPPED=$((TOTAL_SKIPPED + batch_skipped))
    OFFSET=$((OFFSET - old_batch))

    echo "Next: offset=${OFFSET}, batch_size=${BATCH_SIZE}"
    echo "Totals: success=${TOTAL_SUCCESS}, skipped=${TOTAL_SKIPPED}"

    # 9. Coverage 報告
    print_coverage

    # 10. 保存 state
    save_state

    # 冷卻
    echo "Cooling down ${COOLDOWN}s..."
    sleep "$COOLDOWN"
done

echo ""
echo "========================================"
echo "  GCP Chinatimes Sitemap 完成"
echo "  Rounds: $ROUNDS"
echo "  Total success: $TOTAL_SUCCESS"
echo "  Total skipped: $TOTAL_SKIPPED"
echo "  Ended: $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

# 清理 PID 檔
rm -f "$PID_FILE"
