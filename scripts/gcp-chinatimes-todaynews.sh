#!/bin/bash
# GCP Chinatimes todaynews.xml 每日收集腳本
# 每天自動抓取 todaynews.xml 中的文章，防止覆蓋缺口繼續擴大
#
# 使用方式（crontab）：
#   0 22 * * * /home/User/nlweb/scripts/gcp-chinatimes-todaynews.sh >> /home/User/nlweb/data/crawler/logs/todaynews.log 2>&1
#
# todaynews.xml 涵蓋最近 2-3 天、約 1000 篇文章
# retry_urls mode 會自動 skip 已爬過的，所以重複執行安全

set -euo pipefail

DASHBOARD_URL="http://localhost:8001"
SITEMAP_URL="https://www.chinatimes.com/sitemaps/sitemap_todaynews.xml"
PYTHON="/home/User/nlweb/venv/bin/python"
LOG_PREFIX="[todaynews]"
TMPDIR="/tmp"

echo ""
echo "=== $(date '+%Y-%m-%d %H:%M:%S') | Chinatimes todaynews collection ==="

# 1. 健康檢查
echo "$LOG_PREFIX Checking dashboard..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${DASHBOARD_URL}/api/indexing/crawler/status" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" != "200" ]; then
    echo "$LOG_PREFIX ERROR: Dashboard not responding (HTTP $HTTP_CODE). Aborting."
    exit 1
fi
echo "$LOG_PREFIX Dashboard OK"

# 2. 下載 todaynews.xml 並提取 URL，生成 API request body
SITEMAP_FILE="${TMPDIR}/todaynews_$$.xml"
BODY_FILE="${TMPDIR}/todaynews_body_$$.json"

curl -s -o "$SITEMAP_FILE" "$SITEMAP_URL"

SITEMAP_FILE="$SITEMAP_FILE" BODY_FILE="$BODY_FILE" $PYTHON << 'PYEOF'
import re, json, os, sys

sitemap_file = os.environ["SITEMAP_FILE"]
body_file = os.environ["BODY_FILE"]

with open(sitemap_file) as f:
    content = f.read()

urls = re.findall(r'<loc>(https://www\.chinatimes\.com/[^<]+)</loc>', content)
print(f"[todaynews] Found {len(urls)} URLs in sitemap")

if not urls:
    print("[todaynews] ERROR: No URLs found")
    sys.exit(1)

body = {
    "source": "chinatimes",
    "mode": "retry_urls",
    "urls": urls
}
with open(body_file, "w") as f:
    json.dump(body, f)

print(f"[todaynews] Request body written to {body_file} ({len(urls)} URLs)")
PYEOF

rm -f "$SITEMAP_FILE"

if [ ! -f "$BODY_FILE" ]; then
    echo "$LOG_PREFIX ERROR: Failed to create request body"
    exit 1
fi

# 3. 啟動爬蟲
echo "$LOG_PREFIX Starting crawler..."
RESPONSE=$(curl -s -X POST "${DASHBOARD_URL}/api/indexing/crawler/start" \
    -H "Content-Type: application/json" \
    -d @"$BODY_FILE")
rm -f "$BODY_FILE"

TASK_ID=$(echo "$RESPONSE" | $PYTHON -c "import sys,json; print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null || echo "")

if [ -z "$TASK_ID" ]; then
    echo "$LOG_PREFIX ERROR: Failed to start crawler. Response: $RESPONSE"
    exit 1
fi
echo "$LOG_PREFIX Task started: $TASK_ID"

# 4. 等待完成（最多 30 分鐘）
MAX_WAIT=1800
ELAPSED=0
POLL_INTERVAL=30

while [ $ELAPSED -lt $MAX_WAIT ]; do
    sleep $POLL_INTERVAL
    ELAPSED=$((ELAPSED + POLL_INTERVAL))

    TASK_STATUS=$(curl -s "${DASHBOARD_URL}/api/indexing/crawler/status" | \
        $PYTHON -c "
import sys, json
data = json.load(sys.stdin)
for t in data.get('tasks', []):
    if t['task_id'] == '${TASK_ID}':
        s = t.get('stats', {})
        print(f\"{t['status']} success={s.get('success',0)} skip={s.get('skipped',0)} fail={s.get('failed',0)} blocked={s.get('blocked',0)}\")
        break
else:
    print('not_found')
" 2>/dev/null || echo "error")

    MINUTES=$((ELAPSED / 60))
    echo "$LOG_PREFIX ${MINUTES}min: $TASK_STATUS"

    case "$TASK_STATUS" in
        completed*|failed*|early_stopped*|not_found)
            echo "$LOG_PREFIX Task finished."
            break
            ;;
    esac
done

if [ $ELAPSED -ge $MAX_WAIT ]; then
    echo "$LOG_PREFIX WARNING: Task still running after 30 min. Will complete in background."
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S') | Done ==="
