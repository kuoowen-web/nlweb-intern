#!/bin/bash
# 監控腳本 — 用 cron 每 30 分鐘跑一次
# crontab -e → */30 * * * * ~/nlweb/crawler/remote/monitor-gcp.sh

LOG=~/nlweb/data/crawler/logs/monitor.log

echo "=== $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"

# Crawler process check
if pgrep -f subprocess_runner > /dev/null; then
    echo "Crawler: RUNNING (PID: $(pgrep -f subprocess_runner | head -1))" >> "$LOG"
else
    echo "Crawler: STOPPED" >> "$LOG"
fi

# Dashboard process check
if pgrep -f dashboard_server > /dev/null; then
    echo "Dashboard: RUNNING" >> "$LOG"
else
    echo "Dashboard: STOPPED" >> "$LOG"
fi

# Memory usage
free -m | head -3 >> "$LOG"

# Disk usage
df -h / | tail -1 >> "$LOG"

# Article counts
echo "Articles:" >> "$LOG"
for f in ~/nlweb/data/crawler/articles/*.tsv; do
    if [ -f "$f" ]; then
        lines=$(wc -l < "$f")
        name=$(basename "$f")
        echo "  $name: $lines lines" >> "$LOG"
    fi
done

echo "" >> "$LOG"
