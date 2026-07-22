# Indexing Module (M0)
#
# 現存兩用途：
#   1. Prod 索引鏈（路徑 B）：cloud_embed.py（GPU VM embed）+ bulk_load.py（VPS PG 批次載入）
#   2. Crawler 監控：dashboard_server.py / dashboard_api.py（port 8001）
#
# 🪦 桌機批次路徑 A（pg_batch → Ingestion/QualityGate/Chunking → PostgreSQLUploader）
# 與 Qdrant 時代模組（pipeline/dual_storage/vault_helpers/source_manager/rollback_manager）
# 已於 2026-07-16 整批刪除（CEO 拍板 D-2026-07-16，刪除不修復）。
# 需要回溯 → git ref 1d150e49。
#
# 本檔刻意不 re-export 任何符號：消費者一律 `from indexing import bulk_load` 等
# submodule 形式（import 機制直接載入子模組，無需 façade）。
