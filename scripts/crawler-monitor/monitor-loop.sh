#!/bin/bash
# Crawler Monitoring Loop
# Invokes Claude Code every 30 minutes with the monitoring plan.
# Each invocation is a fresh session — no context rot.
#
# Usage:
#   cd C:/users/user/nlweb
#   bash scripts/crawler-monitor/monitor-loop.sh
#
# To stop: Ctrl+C

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="C:/users/user/nlweb"
PLAN_FILE="$SCRIPT_DIR/monitoring-plan.md"
LOG_FILE="$SCRIPT_DIR/monitoring-log.md"
PROMPT_TMP="$SCRIPT_DIR/.prompt-tmp.md"
INTERVAL_SECONDS=1800  # 30 minutes

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

check_count=0

echo -e "${GREEN}=== Crawler Monitor Loop Started ===${NC}"
echo "Plan: $PLAN_FILE"
echo "Log:  $LOG_FILE"
echo "Interval: ${INTERVAL_SECONDS}s ($(($INTERVAL_SECONDS / 60)) min)"
echo "Press Ctrl+C to stop"
echo ""

while true; do
    check_count=$((check_count + 1))
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')

    echo -e "${YELLOW}--- Check #${check_count} at ${timestamp} ---${NC}"

    # Build prompt as a temp file (avoids shell argument length limits)
    cat > "$PROMPT_TMP" <<PROMPT_EOF
You are a crawler monitoring agent. Follow the monitoring plan EXACTLY.

=== MONITORING PLAN ===
$(cat "$PLAN_FILE")

=== RECENT MONITORING LOG ===
$(tail -100 "$LOG_FILE")

=== INSTRUCTIONS ===
Now execute Steps 2-6 of the plan. Be thorough but concise.

IMPORTANT:
- Use the Edit tool to APPEND your log entry to the end of the log file: $LOG_FILE
- Compare current stats against the LAST log entry to detect changes
- If dashboard is down, try to restart it
- You are authorized to investigate, fix code issues, and restart crawlers
- For any code fix: diagnose → plan verification method → implement → verify → record
- After writing the log, EXIT immediately
PROMPT_EOF

    # Invoke Claude Code: pipe the prompt via stdin with -p (print mode)
    # Timeout: 15 minutes max per session (prevents hung sessions from blocking the loop)
    cd "$PROJECT_DIR"
    timeout 900 bash -c 'cat "$1" | claude -p --dangerously-skip-permissions 2>&1 | tee "$2"' \
        _ "$PROMPT_TMP" "$SCRIPT_DIR/last-check-output.txt"

    exit_code=$?

    if [ $exit_code -eq 124 ]; then
        echo -e "${RED}Claude session TIMED OUT after 15 minutes${NC}"
        echo "" >> "$LOG_FILE"
        echo "## Check: $(date '+%Y-%m-%d %H:%M') (TIMEOUT)" >> "$LOG_FILE"
        echo "Session exceeded 15 minute timeout. Likely stuck on a complex fix." >> "$LOG_FILE"
        echo "---" >> "$LOG_FILE"
    elif [ $exit_code -ne 0 ]; then
        echo -e "${RED}Claude exited with code $exit_code${NC}"
        echo "" >> "$LOG_FILE"
        echo "## Check: $(date '+%Y-%m-%d %H:%M') (AGENT ERROR)" >> "$LOG_FILE"
        echo "Claude invocation failed with exit code $exit_code" >> "$LOG_FILE"
        echo "---" >> "$LOG_FILE"
    fi

    echo -e "${GREEN}Check #${check_count} complete. Sleeping ${INTERVAL_SECONDS}s...${NC}"
    echo ""

    sleep $INTERVAL_SECONDS
done
