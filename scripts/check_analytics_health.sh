#!/bin/bash
# Analytics Infrastructure Health Check
# Run weekly to monitor database growth and identify when Phase 2 migration is needed

echo "=== NLWeb Analytics Health Check ==="
echo "Date: $(date)"
echo

# Database file path
DB_PATH="code/python/data/analytics/query_logs.db"

# Check if database exists
if [ ! -f "$DB_PATH" ]; then
    echo "ERROR: Database not found at $DB_PATH"
    echo "Has the logging system been initialized?"
    exit 1
fi

echo "=== Database Metrics ==="
echo

# Database file size
DB_SIZE=$(ls -lh "$DB_PATH" 2>/dev/null | awk '{print $5}')
DB_SIZE_BYTES=$(stat -f%z "$DB_PATH" 2>/dev/null || stat -c%s "$DB_PATH" 2>/dev/null)
echo "Database size: ${DB_SIZE}"

# Query count
QUERY_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM queries" 2>/dev/null)
echo "Total queries logged: ${QUERY_COUNT:-0}"

# Recent queries (last 7 days)
CUTOFF_7D=$(date -d '7 days ago' +%s 2>/dev/null || date -v-7d +%s 2>/dev/null)
RECENT_7D=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM queries WHERE timestamp > $CUTOFF_7D" 2>/dev/null)
echo "Queries (last 7 days): ${RECENT_7D:-0}"

# Recent queries (last 30 days)
CUTOFF_30D=$(date -d '30 days ago' +%s 2>/dev/null || date -v-30d +%s 2>/dev/null)
RECENT_30D=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM queries WHERE timestamp > $CUTOFF_30D" 2>/dev/null)
echo "Queries (last 30 days): ${RECENT_30D:-0}"

# Retrieved documents
RETRIEVED_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM retrieved_documents" 2>/dev/null)
echo "Retrieved documents: ${RETRIEVED_COUNT:-0}"

# Ranking scores
RANKING_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM ranking_scores" 2>/dev/null)
echo "Ranking scores: ${RANKING_COUNT:-0}"

# User interactions
INTERACTION_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM user_interactions" 2>/dev/null)
echo "User interactions: ${INTERACTION_COUNT:-0}"

# Clicks
CLICKS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM user_interactions WHERE clicked = 1" 2>/dev/null)
echo "Total clicks: ${CLICKS:-0}"

# CTR (if queries > 0)
if [ "${QUERY_COUNT:-0}" -gt 0 ]; then
    CTR=$(echo "scale=2; ${CLICKS:-0} * 100 / ${QUERY_COUNT}" | bc 2>/dev/null)
    echo "Click-through rate: ${CTR}%"
fi

# Errors
ERRORS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM queries WHERE error_occurred = 1" 2>/dev/null)
echo "Errors: ${ERRORS:-0}"

echo
echo "=== Table Sizes ==="
echo

# Individual table row counts
QUERIES_ROWS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM queries" 2>/dev/null)
RD_ROWS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM retrieved_documents" 2>/dev/null)
RS_ROWS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM ranking_scores" 2>/dev/null)
UI_ROWS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM user_interactions" 2>/dev/null)

echo "queries: ${QUERIES_ROWS:-0} rows"
echo "retrieved_documents: ${RD_ROWS:-0} rows"
echo "ranking_scores: ${RS_ROWS:-0} rows"
echo "user_interactions: ${UI_ROWS:-0} rows"

echo
echo "=== Phase 2 Migration Triggers ==="
echo

# Check upgrade triggers
WARNINGS=0

# Trigger 1: Database size > 500 MB
if [ "${DB_SIZE_BYTES:-0}" -gt 524288000 ]; then
    echo "🔴 CRITICAL: Database > 500 MB - MIGRATE TO PHASE 2 NOW"
    WARNINGS=$((WARNINGS + 1))
elif [ "${DB_SIZE_BYTES:-0}" -gt 262144000 ]; then
    echo "⚠️  WARNING: Database > 250 MB - Plan Phase 2 migration soon"
    WARNINGS=$((WARNINGS + 1))
else
    echo "✅ Database size OK (< 250 MB)"
fi

# Trigger 2: Query count > 10,000
if [ "${QUERY_COUNT:-0}" -gt 10000 ]; then
    echo "🔴 CRITICAL: Query count > 10,000 - MIGRATE TO PHASE 2 NOW"
    WARNINGS=$((WARNINGS + 1))
elif [ "${QUERY_COUNT:-0}" -gt 5000 ]; then
    echo "⚠️  WARNING: Query count > 5,000 - Plan Phase 2 migration"
    WARNINGS=$((WARNINGS + 1))
else
    echo "✅ Query count OK (< 5,000)"
fi

# Trigger 3: Error rate > 1%
if [ "${QUERY_COUNT:-0}" -gt 0 ]; then
    ERROR_RATE=$(echo "scale=2; ${ERRORS:-0} * 100 / ${QUERY_COUNT}" | bc 2>/dev/null)
    ERROR_RATE_INT=$(echo "$ERROR_RATE / 1" | bc 2>/dev/null)

    if [ "${ERROR_RATE_INT:-0}" -gt 1 ]; then
        echo "⚠️  WARNING: Error rate > 1% ($ERROR_RATE%) - Investigate errors"
        WARNINGS=$((WARNINGS + 1))
    else
        echo "✅ Error rate OK (${ERROR_RATE}%)"
    fi
fi

echo
echo "=== Recommendations ==="
echo

if [ "$WARNINGS" -eq 0 ]; then
    echo "✅ System healthy - continue with Phase 1"
    echo "   Next check: $(date -d '+7 days' 2>/dev/null || date -v+7d 2>/dev/null)"
elif [ "$WARNINGS" -le 2 ]; then
    echo "⚠️  $WARNINGS warning(s) detected"
    echo "   Action: Plan Phase 2 migration in 1-2 weeks"
    echo "   Review: docs/UPGRADE_GUIDE.md (Phase 1 → Phase 2)"
else
    echo "🔴 $WARNINGS critical issue(s) detected"
    echo "   Action: Migrate to Phase 2 immediately"
    echo "   Steps:"
    echo "   1. Backup database: cp $DB_PATH ${DB_PATH}.backup"
    echo "   2. Install PostgreSQL: See docs/UPGRADE_GUIDE.md"
    echo "   3. Run migration script: python scripts/migrate_sqlite_to_postgres.py"
fi

echo
echo "=== Export Test ==="
echo

# Test CSV export performance (optional, comment out if too slow)
# EXPORT_START=$(date +%s)
# curl -s "http://localhost:8000/api/analytics/export_training_data?days=7" -o /tmp/test_export.csv 2>/dev/null
# EXPORT_END=$(date +%s)
# EXPORT_TIME=$((EXPORT_END - EXPORT_START))
#
# if [ -f /tmp/test_export.csv ]; then
#     EXPORT_ROWS=$(wc -l < /tmp/test_export.csv)
#     echo "CSV export: ${EXPORT_ROWS} rows in ${EXPORT_TIME}s"
#     rm /tmp/test_export.csv
#
#     if [ "$EXPORT_TIME" -gt 30 ]; then
#         echo "⚠️  WARNING: Export took > 30s - Consider Phase 2 migration"
#     fi
# else
#     echo "⚠️  CSV export failed - Is server running?"
# fi

echo
echo "=== Backup Status ==="
echo

# Check for backups
BACKUP_DIR="data/analytics/backups"
if [ -d "$BACKUP_DIR" ]; then
    BACKUP_COUNT=$(ls -1 "$BACKUP_DIR"/*.db 2>/dev/null | wc -l)
    if [ "$BACKUP_COUNT" -gt 0 ]; then
        LATEST_BACKUP=$(ls -t "$BACKUP_DIR"/*.db 2>/dev/null | head -1)
        LATEST_DATE=$(stat -f%Sm -t "%Y-%m-%d" "$LATEST_BACKUP" 2>/dev/null || stat -c%y "$LATEST_BACKUP" 2>/dev/null | cut -d' ' -f1)
        echo "✅ Backups found: $BACKUP_COUNT files"
        echo "   Latest backup: $LATEST_DATE"
    else
        echo "⚠️  No backups found in $BACKUP_DIR"
        echo "   Action: Set up daily backup cron job"
    fi
else
    echo "⚠️  Backup directory not found: $BACKUP_DIR"
    echo "   Action: Create directory and set up backups"
    echo "   mkdir -p $BACKUP_DIR"
fi

echo
echo "=== End of Report ==="
echo
echo "Run this script weekly: ./scripts/check_analytics_health.sh"
echo "For automated checks, add to crontab:"
echo "  0 9 * * 1 cd /path/to/NLWeb && ./scripts/check_analytics_health.sh | mail -s 'Analytics Weekly Report' admin@example.com"
