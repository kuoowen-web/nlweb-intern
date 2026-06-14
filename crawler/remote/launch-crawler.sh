#!/bin/bash
# 直接啟動 crawler（不經 Dashboard）
# Usage: ./launch-crawler.sh ltn|cna|udn|moea [--dry-run]
set -e

SOURCE="${1:?Usage: $0 <source> [--dry-run]}"
DRY_RUN="${2:-}"

cd ~/nlweb
source venv/bin/activate
cd code/python

case "$SOURCE" in
    udn)
        PARAMS='{"source":"udn","mode":"sitemap","date_from":"202401"}'
        ;;
    ltn)
        PARAMS='{"source":"ltn","mode":"full_scan"}'
        ;;
    cna)
        PARAMS='{"source":"cna","mode":"full_scan","start_date":"2024-01-01"}'
        ;;
    moea)
        PARAMS='{"source":"moea","mode":"full_scan"}'
        ;;
    *)
        echo "Unknown source: $SOURCE"
        echo "Available: ltn, cna, udn, moea"
        exit 1
        ;;
esac

TASK_ID="gcp_${SOURCE}_$(date +%s)"
SIGNAL_DIR=~/nlweb/data/crawler/signals
LOG_FILE=~/nlweb/data/crawler/logs/${SOURCE}.log

echo "$(date) Starting $SOURCE as $TASK_ID"
echo "Params: $PARAMS"
echo "Log: $LOG_FILE"

if [ "$DRY_RUN" = "--dry-run" ]; then
    echo "[DRY RUN] Would run:"
    echo "  python -m crawler.subprocess_runner --params '$PARAMS' --task-id $TASK_ID --signal-dir $SIGNAL_DIR"
    exit 0
fi

mkdir -p "$SIGNAL_DIR" "$(dirname "$LOG_FILE")"

python -m crawler.subprocess_runner \
    --params "$PARAMS" \
    --task-id "$TASK_ID" \
    --signal-dir "$SIGNAL_DIR" \
    2>> "$LOG_FILE"
