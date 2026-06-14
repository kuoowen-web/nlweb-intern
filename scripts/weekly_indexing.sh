#!/usr/bin/env bash
#
# Weekly Indexing Pipeline
#
# Orchestrates: GCP Crawler TSV → Spot L4 VM embed → VPS bulk load
# Run from desktop. Tracks metrics in weekly_indexing.log.
#
# Usage:
#   bash scripts/weekly_indexing.sh          # full pipeline
#   bash scripts/weekly_indexing.sh status   # check status only
#
# Lessons learned (2026-04-01 first test):
#   - Spot VM gets preempted frequently; try spot first, fallback on-demand fast
#   - SSH host key changes every new VM; use StrictHostKeyChecking=no
#   - Long-running SSH commands get disconnected; use nohup + poll
#   - gsutil on Windows has permission issues; use gcloud storage instead
#   - Crawler VM needs bucket-level IAM for each new bucket
#   - Pre-split TSVs >20K lines to avoid VRAM OOM

set -euo pipefail

# ─── Config ───
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GCP_ZONE_CRAWLER="asia-east1-b"
GCP_ZONE_GPU=""  # set dynamically
GCP_VM_CRAWLER="nlweb-crawler"
GCP_VM_GPU="nlweb-weekly-embed"
GCP_MACHINE_TYPE="g2-standard-8"
GCP_GPU="nvidia-l4"
GCP_IMAGE_FAMILY="pytorch-2-7-cu128-ubuntu-2204-nvidia-570"
GCP_IMAGE_PROJECT="deeplearning-platform-release"
GCS_BUCKET="gs://YOUR_GCS_BUCKET"
CRAWLER_SA="YOUR_PROJECT_NUMBER-compute@developer.gserviceaccount.com"
VPS_HOST="YOUR_VPS_HOST"
VPS_PORT="2222"
VPS_PG_USER="nlweb"
VPS_PG_DB="nlweb"
SPLIT_THRESHOLD=20000
METRICS_LOG="$PROJECT_DIR/data/weekly_indexing_metrics.log"
EMBED_SCRIPT="$PROJECT_DIR/code/python/indexing/cloud_embed.py"
BULK_LOAD_SCRIPT="$PROJECT_DIR/code/python/indexing/bulk_load.py"

# SSH options to avoid host key issues with ephemeral VMs
GPU_SSH_OPTS="--ssh-flag=-o --ssh-flag=StrictHostKeyChecking=no --ssh-flag=-o --ssh-flag=UserKnownHostsFile=/dev/null"

# ─── Helpers ───
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$METRICS_LOG"; }
die() { log "FATAL: $*"; exit 1; }

vps_ssh() {
    ssh -o ConnectTimeout=10 -p "$VPS_PORT" root@"$VPS_HOST" "$@"
}

gpu_ssh() {
    gcloud compute ssh "$GCP_VM_GPU" --zone="$GCP_ZONE_GPU" $GPU_SSH_OPTS --command="$1" 2>&1
}

gpu_scp() {
    gcloud compute scp $GPU_SSH_OPTS "$1" "$GCP_VM_GPU":"$2" --zone="$GCP_ZONE_GPU" 2>&1
}

get_vps_pg_ip() {
    vps_ssh 'docker inspect nlweb-postgres --format="{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"'
}

get_vps_counts() {
    vps_ssh "docker exec nlweb-postgres psql -U $VPS_PG_USER -d $VPS_PG_DB -t -c \"SELECT count(*) FROM articles\" 2>/dev/null | tr -d ' '"
}

cleanup_vm() {
    if [[ -n "$GCP_ZONE_GPU" ]]; then
        log "Cleaning up GPU VM..."
        gcloud compute instances delete "$GCP_VM_GPU" --zone="$GCP_ZONE_GPU" --quiet 2>/dev/null || true
    fi
}

cleanup_gcs() {
    log "Cleaning up GCS bucket..."
    gcloud storage rm -r "$GCS_BUCKET" 2>/dev/null || true
}

# Cleanup on exit (VM + bucket)
trap 'cleanup_vm' EXIT

# ─── Status check ───
if [[ "${1:-}" == "status" ]]; then
    log "=== Weekly Indexing Status ==="
    CRAWLER_COUNT=$(gcloud compute ssh "$GCP_VM_CRAWLER" --zone="$GCP_ZONE_CRAWLER" \
        --command="ls ~/nlweb/data/crawler/articles/*.tsv | wc -l" 2>/dev/null || echo "N/A")
    CRAWLER_LATEST=$(gcloud compute ssh "$GCP_VM_CRAWLER" --zone="$GCP_ZONE_CRAWLER" \
        --command="ls -t ~/nlweb/data/crawler/articles/*.tsv | head -1 | xargs basename" 2>/dev/null || echo "N/A")
    VPS_ARTICLES=$(get_vps_counts 2>/dev/null || echo "N/A")
    INDEXED=$(vps_ssh 'wc -l < /data/indexed_tsvs.txt 2>/dev/null' 2>/dev/null || echo "N/A")
    log "GCP Crawler TSVs: $CRAWLER_COUNT (latest: $CRAWLER_LATEST)"
    log "VPS Articles: $VPS_ARTICLES"
    log "Indexed TSVs: $INDEXED"
    exit 0
fi

# ─── Main Pipeline ───
START_TIME=$(date +%s)
log "=========================================="
log "=== Weekly Indexing Pipeline Starting ==="
log "=========================================="

# Step 1: Identify new TSVs
log "Step 1: Identifying new TSVs on GCP crawler..."
DONE_FILES=$(vps_ssh 'cat /data/indexed_tsvs.txt 2>/dev/null' || echo "")
CRAWLER_TSVS=$(gcloud compute ssh "$GCP_VM_CRAWLER" --zone="$GCP_ZONE_CRAWLER" \
    --command="ls ~/nlweb/data/crawler/articles/*.tsv | xargs -n1 basename" 2>/dev/null)

NEW_TSVS=""
NEW_COUNT=0
for tsv in $CRAWLER_TSVS; do
    if ! echo "$DONE_FILES" | grep -qF "$tsv" 2>/dev/null; then
        NEW_TSVS="$NEW_TSVS $tsv"
        NEW_COUNT=$((NEW_COUNT + 1))
    fi
done

if [[ $NEW_COUNT -eq 0 ]]; then
    log "No new TSVs to process. Done."
    exit 0
fi
log "Found $NEW_COUNT new TSVs to process"

# Step 2: Create GCS bucket + grant crawler SA access
log "Step 2: Creating GCS bucket..."
gcloud storage buckets create "$GCS_BUCKET" --location=europe-west1 --uniform-bucket-level-access 2>/dev/null || true
gcloud storage buckets add-iam-policy-binding "$GCS_BUCKET" \
    --member="serviceAccount:$CRAWLER_SA" --role=roles/storage.objectAdmin --quiet 2>/dev/null

# Step 3: Upload new TSVs from crawler to GCS
log "Step 3: Uploading $NEW_COUNT TSVs to GCS..."
gcloud compute ssh "$GCP_VM_CRAWLER" --zone="$GCP_ZONE_CRAWLER" --command="
    rm -rf ~/.gsutil 2>/dev/null
    for f in $NEW_TSVS; do
        gsutil cp ~/nlweb/data/crawler/articles/\$f $GCS_BUCKET/tsv/
    done
" 2>&1 | tail -5
log "Upload complete"

# Step 4: Create GPU VM (spot first, fallback on-demand)
log "Step 4: Creating GPU VM..."
ZONES=("europe-west1-c" "europe-west1-b" "us-central1-a" "us-central1-b" "us-central1-c")
VM_CREATED=false

# Try spot
for zone in "${ZONES[@]}"; do
    if gcloud compute instances create "$GCP_VM_GPU" \
        --zone="$zone" \
        --machine-type="$GCP_MACHINE_TYPE" \
        --accelerator="type=$GCP_GPU,count=1" \
        --boot-disk-size=100GB \
        --boot-disk-type=pd-balanced \
        --image-family="$GCP_IMAGE_FAMILY" \
        --image-project="$GCP_IMAGE_PROJECT" \
        --maintenance-policy=TERMINATE \
        --provisioning-model=SPOT \
        --scopes=storage-full \
        --metadata="install-nvidia-driver=True" 2>/dev/null; then
        GCP_ZONE_GPU="$zone"
        VM_CREATED=true
        log "VM created in $zone (Spot)"
        break
    fi
done

# Fallback on-demand
if [[ "$VM_CREATED" != "true" ]]; then
    log "Spot unavailable, trying on-demand..."
    for zone in "${ZONES[@]}"; do
        if gcloud compute instances create "$GCP_VM_GPU" \
            --zone="$zone" \
            --machine-type="$GCP_MACHINE_TYPE" \
            --accelerator="type=$GCP_GPU,count=1" \
            --boot-disk-size=100GB \
            --boot-disk-type=pd-balanced \
            --image-family="$GCP_IMAGE_FAMILY" \
            --image-project="$GCP_IMAGE_PROJECT" \
            --maintenance-policy=TERMINATE \
            --scopes=storage-full \
            --metadata="install-nvidia-driver=True" 2>/dev/null; then
            GCP_ZONE_GPU="$zone"
            VM_CREATED=true
            log "VM created in $zone (on-demand)"
            break
        fi
    done
fi

[[ "$VM_CREATED" == "true" ]] || die "Could not create GPU VM in any zone"

# Wait for SSH (with host key auto-accept)
log "Waiting for SSH..."
sleep 30
for i in $(seq 1 10); do
    gpu_ssh "echo ready" >/dev/null 2>&1 && break
    sleep 15
done

# Step 5: Setup VM + download TSVs + pre-split
log "Step 5: Setting up VM environment..."
gpu_ssh "pip install sentence-transformers bitsandbytes 'accelerate>=1.1.0' numpy 2>&1 | tail -1" | tail -1

gpu_scp "$EMBED_SCRIPT" "/tmp/cloud_embed.py" | tail -1

log "Step 6: Downloading TSVs and pre-splitting..."
gpu_ssh "
    mkdir -p /tmp/tsv_raw /tmp/tsv_input /tmp/embed_output
    gsutil -m cp '$GCS_BUCKET/tsv/*.tsv' /tmp/tsv_raw/ 2>&1 | tail -1
    for f in /tmp/tsv_raw/*.tsv; do
        bn=\$(basename \$f)
        lines=\$(wc -l < \$f)
        if [ \$lines -gt $SPLIT_THRESHOLD ]; then
            prefix=\$(echo \$bn | sed 's/.tsv$//')
            split -l $SPLIT_THRESHOLD \$f /tmp/tsv_input/\${prefix}_part_
            for part in /tmp/tsv_input/\${prefix}_part_*; do mv \$part \${part}.tsv; done
            echo \"Split \$bn (\$lines lines)\"
        else
            cp \$f /tmp/tsv_input/
        fi
    done
    echo 'Files ready:' \$(ls /tmp/tsv_input/*.tsv | wc -l)
" | tail -5

# Step 7: Run embedding (nohup to survive SSH disconnect)
log "Step 7: Running embedding..."
EMBED_START=$(date +%s)
gpu_ssh "PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True nohup python3 /tmp/cloud_embed.py /tmp/tsv_input/ /tmp/embed_output/ > /tmp/embed.log 2>&1 &
echo PID=\$!" | grep PID

# Poll for completion
while true; do
    sleep 30
    STATUS=$(gpu_ssh "ps aux | grep cloud_embed | grep -v grep | wc -l" 2>/dev/null | tr -d '[:space:]' || echo "0")
    if [[ "$STATUS" == "0" ]]; then
        break
    fi
done
EMBED_END=$(date +%s)
EMBED_DURATION=$(( (EMBED_END - EMBED_START) / 60 ))

# Check results
EMBED_SUMMARY=$(gpu_ssh "grep -E 'Complete|ERROR' /tmp/embed.log" 2>/dev/null || echo "unknown")
EMBED_ERRORS=$(gpu_ssh "grep -c ERROR /tmp/embed.log 2>/dev/null || echo 0" 2>/dev/null | tr -d '[:space:]' || echo "unknown")
log "Embedding completed in ${EMBED_DURATION}m (errors: $EMBED_ERRORS)"
log "  $EMBED_SUMMARY"

# Step 8: Upload results to GCS
log "Step 8: Uploading results to GCS..."
gpu_ssh "gsutil -m cp /tmp/embed_output/*.jsonl /tmp/embed_output/*.npy '$GCS_BUCKET/results/' 2>&1 | tail -1" | tail -1

# Step 9: Delete GPU VM
log "Step 9: Deleting GPU VM..."
cleanup_vm
GCP_ZONE_GPU=""  # prevent double-delete in trap

# Step 10: Download results to VPS + bulk load
log "Step 10: Downloading results to VPS and running bulk load..."

# Make bucket readable for VPS
gcloud storage buckets add-iam-policy-binding "$GCS_BUCKET" \
    --member=allUsers --role=roles/storage.objectViewer --quiet 2>/dev/null

LOAD_START=$(date +%s)
VPS_PG_IP=$(get_vps_pg_ip)
ARTICLES_BEFORE=$(get_vps_counts)

vps_ssh "
    mkdir -p /data/embed_weekly
    gsutil -m cp '$GCS_BUCKET/results/*.jsonl' '$GCS_BUCKET/results/*.npy' /data/embed_weekly/ 2>&1 | tail -1
"

# Upload bulk_load.py
scp -P "$VPS_PORT" "$BULK_LOAD_SCRIPT" root@"$VPS_HOST":/data/bulk_load.py

# Fix sequence + run bulk load
vps_ssh "
    docker exec nlweb-postgres psql -U $VPS_PG_USER -d $VPS_PG_DB -c \
        \"SELECT setval('articles_id_seq', (SELECT COALESCE(MAX(id),0) FROM articles));\"
    python3 /data/bulk_load.py /data/embed_weekly/ \
        --pg-dsn postgresql://$VPS_PG_USER:nlweb_dev@$VPS_PG_IP:5432/$VPS_PG_DB
    rm -rf /data/embed_weekly
"

# Update master indexed list
log "Updating master indexed TSV list..."
for tsv in $NEW_TSVS; do
    echo "$tsv"
done | vps_ssh 'cat >> /data/indexed_tsvs.txt && sort -u /data/indexed_tsvs.txt -o /data/indexed_tsvs.txt'

LOAD_END=$(date +%s)
LOAD_DURATION=$(( (LOAD_END - LOAD_START) / 60 ))

ARTICLES_AFTER=$(get_vps_counts)
NEW_ARTICLES=$((ARTICLES_AFTER - ARTICLES_BEFORE))

# Step 11: Cleanup GCS
log "Step 11: Cleaning up GCS..."
cleanup_gcs

# ─── Metrics ───
END_TIME=$(date +%s)
TOTAL_DURATION=$(( (END_TIME - START_TIME) / 60 ))

log "=========================================="
log "=== Weekly Indexing Pipeline Complete ==="
log "=========================================="
log "TSVs processed: $NEW_COUNT"
log "New articles: $NEW_ARTICLES"
log "VPS total: $ARTICLES_AFTER articles"
log "Embed time: ${EMBED_DURATION}m"
log "Load time: ${LOAD_DURATION}m"
log "Total time: ${TOTAL_DURATION}m"
log "Errors: $EMBED_ERRORS"
log "=========================================="
