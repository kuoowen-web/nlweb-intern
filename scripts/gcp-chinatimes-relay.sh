#!/bin/bash
# GCP Chinatimes 自動接力腳本
# 等待 index_1 sitemap 任務完成後，自動啟動 full_scan 補 2025-07~2026-02 缺口
#
# 使用方式：
#   nohup /home/User/nlweb/scripts/gcp-chinatimes-relay.sh \
#     >> /home/User/nlweb/data/crawler/logs/chinatimes-relay.log 2>&1 &
#
# 設計：
#   Phase 1: 監控 index_1 任務直到完成
#   Phase 2: 啟動 full_scan (2025-07-01 ~ 2026-02-15)
#   Phase 3: 監控 full_scan 進度

set -euo pipefail

DASHBOARD_URL="http://localhost:8001"
PYTHON="/home/User/nlweb/venv/bin/python"
PIDFILE="/home/User/nlweb/data/crawler/chinatimes-relay.pid"
LOG_PREFIX="[relay]"

# 寫入 PID
echo $$ > "$PIDFILE"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_PREFIX $1"
}

check_dashboard() {
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" "${DASHBOARD_URL}/api/indexing/crawler/status" 2>/dev/null || echo "000")
    [ "$code" = "200" ]
}

# 取得特定 task 的狀態
get_task_status() {
    local task_id="$1"
    curl -s "${DASHBOARD_URL}/api/indexing/crawler/status" | \
        $PYTHON -c "
import sys, json
data = json.load(sys.stdin)
for t in data.get('tasks', []):
    if t['task_id'] == '${task_id}':
        s = t.get('stats', {})
        status = t['status']
        success = s.get('success', 0)
        failed = s.get('failed', 0)
        skipped = s.get('skipped', 0)
        blocked = s.get('blocked', 0)
        sitemaps = s.get('sitemaps_processed', '')
        dur = t.get('duration_seconds', 0)
        print(f'{status}|{success}|{failed}|{skipped}|{blocked}|{sitemaps}|{dur:.0f}')
        break
else:
    print('not_found|0|0|0|0||0')
" 2>/dev/null || echo "error|0|0|0|0||0"
}

# 找到最新的 chinatimes running 任務
find_running_chinatimes() {
    curl -s "${DASHBOARD_URL}/api/indexing/crawler/status" | \
        $PYTHON -c "
import sys, json
data = json.load(sys.stdin)
for t in reversed(data.get('tasks', [])):
    if t.get('source') == 'chinatimes' and t['status'] == 'running':
        print(t['task_id'])
        break
else:
    print('')
" 2>/dev/null || echo ""
}

log "=== Chinatimes Relay Script Started ==="
log "PID: $$"

# ============================================================
# Phase 1: 等待 index_1 完成
# ============================================================
log "Phase 1: Waiting for index_1 sitemap task to complete..."

POLL_INTERVAL=300  # 5 min

while true; do
    if ! check_dashboard; then
        log "Dashboard not responding. Waiting 60s..."
        sleep 60
        continue
    fi

    TASK_ID=$(find_running_chinatimes)

    if [ -z "$TASK_ID" ]; then
        log "No running chinatimes task found. Proceeding to Phase 2."
        break
    fi

    STATUS_LINE=$(get_task_status "$TASK_ID")
    IFS='|' read -r STATUS SUCCESS FAILED SKIPPED BLOCKED SITEMAPS DUR <<< "$STATUS_LINE"

    HOURS=$((DUR / 3600))
    MINS=$(( (DUR % 3600) / 60 ))

    if [ "$STATUS" = "running" ]; then
        log "index_1 running: success=$SUCCESS skip=$SKIPPED sitemaps=$SITEMAPS (${HOURS}h${MINS}m)"
        sleep $POLL_INTERVAL
    else
        log "Task $TASK_ID finished with status=$STATUS success=$SUCCESS"
        break
    fi
done

log "Phase 1 complete."
sleep 10

# ============================================================
# Phase 2: 啟動 full_scan
# ============================================================
log "Phase 2: Starting full_scan for gap period (2025-07-01 ~ 2026-02-15)..."

if ! check_dashboard; then
    log "ERROR: Dashboard not responding before full_scan start. Aborting."
    rm -f "$PIDFILE"
    exit 1
fi

RESPONSE=$(curl -s -X POST "${DASHBOARD_URL}/api/indexing/fullscan/start" \
    -H "Content-Type: application/json" \
    -d '{"sources":["chinatimes"],"start_date":"2025-07-01","end_date":"2026-02-15"}')

FULLSCAN_TASK_ID=$(echo "$RESPONSE" | $PYTHON -c "
import sys, json
data = json.load(sys.stdin)
tasks = data.get('tasks', [])
if tasks:
    print(tasks[0].get('task_id', ''))
else:
    print(data.get('task_id', ''))
" 2>/dev/null || echo "")

if [ -z "$FULLSCAN_TASK_ID" ]; then
    log "ERROR: Failed to start full_scan. Response: $RESPONSE"
    rm -f "$PIDFILE"
    exit 1
fi

log "Full scan started: $FULLSCAN_TASK_ID"

# ============================================================
# Phase 3: 監控 full_scan 進度
# ============================================================
log "Phase 3: Monitoring full_scan progress..."

MONITOR_INTERVAL=600  # 10 min
CONSECUTIVE_ERRORS=0
MAX_ERRORS=10

while true; do
    sleep $MONITOR_INTERVAL

    if ! check_dashboard; then
        CONSECUTIVE_ERRORS=$((CONSECUTIVE_ERRORS + 1))
        log "Dashboard not responding ($CONSECUTIVE_ERRORS/$MAX_ERRORS)"
        if [ $CONSECUTIVE_ERRORS -ge $MAX_ERRORS ]; then
            log "ERROR: Dashboard unreachable for too long. Exiting (task continues in background)."
            break
        fi
        continue
    fi
    CONSECUTIVE_ERRORS=0

    STATUS_LINE=$(get_task_status "$FULLSCAN_TASK_ID")
    IFS='|' read -r STATUS SUCCESS FAILED SKIPPED BLOCKED SITEMAPS DUR <<< "$STATUS_LINE"

    HOURS=$((DUR / 3600))
    MINS=$(( (DUR % 3600) / 60 ))

    case "$STATUS" in
        running)
            log "full_scan: success=$SUCCESS fail=$FAILED skip=$SKIPPED blocked=$BLOCKED (${HOURS}h${MINS}m)"
            ;;
        completed)
            log "FULL SCAN COMPLETED! success=$SUCCESS fail=$FAILED skip=$SKIPPED (${HOURS}h${MINS}m)"
            break
            ;;
        failed|early_stopped)
            log "Full scan $STATUS: success=$SUCCESS fail=$FAILED blocked=$BLOCKED"
            log "Attempting restart (watermark auto-resume)..."
            sleep 30
            RESPONSE=$(curl -s -X POST "${DASHBOARD_URL}/api/indexing/fullscan/start" \
                -H "Content-Type: application/json" \
                -d '{"sources":["chinatimes"],"start_date":"2025-07-01","end_date":"2026-02-15"}')
            NEW_ID=$(echo "$RESPONSE" | $PYTHON -c "
import sys, json
data = json.load(sys.stdin)
tasks = data.get('tasks', [])
if tasks:
    print(tasks[0].get('task_id', ''))
else:
    print(data.get('task_id', ''))
" 2>/dev/null || echo "")
            if [ -n "$NEW_ID" ]; then
                FULLSCAN_TASK_ID="$NEW_ID"
                log "Restarted as: $FULLSCAN_TASK_ID"
            else
                log "ERROR: Restart failed. Response: $RESPONSE"
                log "Manual intervention needed."
                break
            fi
            ;;
        not_found|error)
            log "WARNING: Task status unknown ($STATUS). Checking for new tasks..."
            NEW_TASK=$(find_running_chinatimes)
            if [ -n "$NEW_TASK" ]; then
                FULLSCAN_TASK_ID="$NEW_TASK"
                log "Found running task: $FULLSCAN_TASK_ID"
            else
                log "No running tasks. Exiting."
                break
            fi
            ;;
    esac
done

# Coverage 報告
log "=== Final Coverage Report ==="
REGISTRY_DB="/home/User/nlweb/data/crawler/crawled_registry.db"
if [ -f "$REGISTRY_DB" ]; then
    $PYTHON -c "
import sqlite3
c = sqlite3.connect('$REGISTRY_DB')
rows = c.execute('''
    SELECT substr(date_published,1,7) as m, COUNT(*)
    FROM crawled_articles WHERE source_id='chinatimes'
    AND date_published >= '2025-07'
    GROUP BY m ORDER BY m
''').fetchall()
total = 0
for r in rows:
    print(f'  {r[0]}: {r[1]:,}')
    total += r[1]
print(f'  TOTAL (2025-07+): {total:,}')
c.close()
"
fi

log "=== Relay Script Finished ==="
rm -f "$PIDFILE"
