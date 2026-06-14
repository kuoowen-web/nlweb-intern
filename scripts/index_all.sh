#!/bin/bash
# Full indexing script - runs all sources sequentially
# Usage: QDRANT_PROFILE=offline bash scripts/index_all.sh 2>&1 | tee data/index_all.log

set -e
cd /c/users/user/nlweb/code/python

export QDRANT_PROFILE=offline

ARTICLES="/c/users/user/nlweb/data/crawler/articles"
LOG_PREFIX="[INDEX]"
STARTED=$(date '+%Y-%m-%d %H:%M:%S')

echo "$LOG_PREFIX === Full Indexing Started: $STARTED ==="

# Helper: run pipeline and report
run_pipeline() {
    local file="$1"
    local site="$2"
    local basename=$(basename "$file")
    echo ""
    echo "$LOG_PREFIX --- $site: $basename ($(wc -l < "$file") lines) ---"
    echo "$LOG_PREFIX Start: $(date '+%H:%M:%S')"
    python -m indexing.pipeline "$file" --site "$site" --upload --resume 2>&1 | tail -6
    echo "$LOG_PREFIX End: $(date '+%H:%M:%S')"
}

# Check Qdrant
echo "$LOG_PREFIX Checking Qdrant..."
POINTS=$(curl -s http://localhost:6333/collections/nlweb | python -c "import sys,json; print(json.loads(sys.stdin.read())['result']['points_count'])" 2>/dev/null)
echo "$LOG_PREFIX Qdrant points before: $POINTS"

# ============================================
# 1. MOEA remaining (small files, ~5 min)
# ============================================
echo ""
echo "$LOG_PREFIX ========== MOEA =========="
for f in "$ARTICLES"/moea_*.tsv; do
    run_pipeline "$f" "moea"
done

echo "$LOG_PREFIX MOEA complete. Points: $(curl -s http://localhost:6333/collections/nlweb | python -c "import sys,json; print(json.loads(sys.stdin.read())['result']['points_count'])" 2>/dev/null)"

# ============================================
# 2. Chinatimes (big file first, then recent)
# ============================================
echo ""
echo "$LOG_PREFIX ========== CHINATIMES =========="
# Big file first
run_pipeline "$ARTICLES/chinatimes_2026-02-12_13-30.tsv" "chinatimes"
# Recent files for newer articles
for f in "$ARTICLES"/chinatimes_2026-02-19*.tsv "$ARTICLES"/chinatimes_2026-02-23*.tsv; do
    [ -f "$f" ] && run_pipeline "$f" "chinatimes"
done

echo "$LOG_PREFIX Chinatimes complete. Points: $(curl -s http://localhost:6333/collections/nlweb | python -c "import sys,json; print(json.loads(sys.stdin.read())['result']['points_count'])" 2>/dev/null)"

# ============================================
# 3. CNA (big file first, then recent)
# ============================================
echo ""
echo "$LOG_PREFIX ========== CNA =========="
run_pipeline "$ARTICLES/cna_2026-02-12_13-29.tsv" "cna"
# Recent
for f in "$ARTICLES"/cna_2026-02-13*.tsv "$ARTICLES"/cna_2026-02-19*.tsv "$ARTICLES"/cna_2026-02-23*.tsv; do
    [ -f "$f" ] && run_pipeline "$f" "cna"
done

echo "$LOG_PREFIX CNA complete. Points: $(curl -s http://localhost:6333/collections/nlweb | python -c "import sys,json; print(json.loads(sys.stdin.read())['result']['points_count'])" 2>/dev/null)"

# ============================================
# 4. LTN (big file first, then recent)
# ============================================
echo ""
echo "$LOG_PREFIX ========== LTN =========="
run_pipeline "$ARTICLES/ltn_2026-02-12_13-29.tsv" "ltn"
# Recent
for f in "$ARTICLES"/ltn_2026-02-13*.tsv "$ARTICLES"/ltn_2026-02-19*.tsv "$ARTICLES"/ltn_2026-02-23*.tsv; do
    [ -f "$f" ] && run_pipeline "$f" "ltn"
done

echo "$LOG_PREFIX LTN complete. Points: $(curl -s http://localhost:6333/collections/nlweb | python -c "import sys,json; print(json.loads(sys.stdin.read())['result']['points_count'])" 2>/dev/null)"

# ============================================
# 5. UDN (3 monthly files ~86K articles)
# ============================================
echo ""
echo "$LOG_PREFIX ========== UDN =========="
# Big monthly files first
for f in "$ARTICLES"/udn_2025-11.tsv "$ARTICLES"/udn_2025-12.tsv "$ARTICLES"/udn_2026-01.tsv; do
    [ -f "$f" ] && run_pipeline "$f" "udn"
done
# Incremental files
for f in "$ARTICLES"/udn_2026-02*.tsv; do
    [ -f "$f" ] && run_pipeline "$f" "udn"
done

echo "$LOG_PREFIX UDN complete. Points: $(curl -s http://localhost:6333/collections/nlweb | python -c "import sys,json; print(json.loads(sys.stdin.read())['result']['points_count'])" 2>/dev/null)"

# ============================================
# 6. einfo (small, ~65 articles)
# ============================================
echo ""
echo "$LOG_PREFIX ========== EINFO =========="
for f in "$ARTICLES"/einfo_*.tsv; do
    [ -f "$f" ] && run_pipeline "$f" "einfo"
done

echo "$LOG_PREFIX einfo complete. Points: $(curl -s http://localhost:6333/collections/nlweb | python -c "import sys,json; print(json.loads(sys.stdin.read())['result']['points_count'])" 2>/dev/null)"

# ============================================
# 7. esg_businesstoday (~2K articles)
# ============================================
echo ""
echo "$LOG_PREFIX ========== ESG_BUSINESSTODAY =========="
# Big files first
for f in "$ARTICLES"/esg_businesstoday_2026-02-07_12-12.tsv "$ARTICLES"/esg_businesstoday_2026-02-05_15-25.tsv; do
    [ -f "$f" ] && run_pipeline "$f" "esg_businesstoday"
done
# Recent
for f in "$ARTICLES"/esg_businesstoday_2026-02-09*.tsv "$ARTICLES"/esg_businesstoday_2026-02-10*.tsv "$ARTICLES"/esg_businesstoday_2026-02-23*.tsv; do
    [ -f "$f" ] && run_pipeline "$f" "esg_businesstoday"
done

echo "$LOG_PREFIX esg_businesstoday complete. Points: $(curl -s http://localhost:6333/collections/nlweb | python -c "import sys,json; print(json.loads(sys.stdin.read())['result']['points_count'])" 2>/dev/null)"

# ============================================
# Final reconcile
# ============================================
echo ""
echo "$LOG_PREFIX ========== FINAL RECONCILE =========="
python -m indexing.pipeline --reconcile 2>&1 | tail -3

ENDED=$(date '+%Y-%m-%d %H:%M:%S')
FINAL_POINTS=$(curl -s http://localhost:6333/collections/nlweb | python -c "import sys,json; print(json.loads(sys.stdin.read())['result']['points_count'])" 2>/dev/null)
echo ""
echo "$LOG_PREFIX === Full Indexing Complete ==="
echo "$LOG_PREFIX Started: $STARTED"
echo "$LOG_PREFIX Ended:   $ENDED"
echo "$LOG_PREFIX Final Qdrant points: $FINAL_POINTS"
