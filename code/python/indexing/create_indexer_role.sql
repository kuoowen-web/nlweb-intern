-- =============================================================================
-- create_indexer_role.sql — VPS 專用 indexing DB role（Plan B v8.1 G3-1）
-- =============================================================================
--
-- 用途：為 weekly indexing pipeline（GPU VM 上的 orchestrator → bulk_load.py）
--       建一個「最小權限」的專用 DB role `indexer_import`，取代目前直接用
--       `nlweb` app 帳號寫入 prod 的做法。
--
-- 為什麼要獨立 role（安全殘項收斂）：
--   現況 pipeline 用 `nlweb` app 帳號（等同前端服務帳號）寫 prod。GPU VM 是
--   on-demand、拋棄式、被入侵風險高於後端服務。用 app 帳號 = 一旦 GPU VM 洩漏
--   密碼，攻擊者拿到的是能碰 users/sessions/auth 全表的帳號。專用 role 把權限
--   收斂到「只能 INSERT/UPDATE articles + chunks」——洩漏面 = 只能改新聞資料，
--   碰不到用戶資料、碰不到 DDL、碰不到 SUPERUSER。
--
-- 執行方式（人工在 VPS 上跑，密碼走 psql 變數不 hardcode 進檔）：
--   在 VPS 上（PG 在 docker 容器 nlweb-postgres 內，DB=nlweb）：
--     docker exec -i nlweb-postgres \
--       psql -U nlweb -d nlweb \
--       -v pw="$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)" \
--       -f - < create_indexer_role.sql
--   或先產密碼再手動帶入：
--     PW='<強密碼>'
--     docker exec -i nlweb-postgres psql -U nlweb -d nlweb -v pw="$PW" -f - < create_indexer_role.sql
--   ⚠ 記下產生的密碼 —— 之後要換裝進 launcher.env（見檔末「換裝步驟」）。
--
-- 冪等性：role 若已存在，CREATE ROLE 會報錯中止（fail-loud，避免無意間覆蓋既有
--   role 的密碼/屬性）。重跑前需先 DROP 或改用下方「重設密碼」段。GRANT 冪等可重跑。
-- =============================================================================

-- ── 1. 建 role（LOGIN + 強密碼；密碼走 :'pw' 變數，不 hardcode）─────────────
-- NOSUPERUSER / NOCREATEDB / NOCREATEROLE 顯式寫出（即使是預設）——安全意圖白紙黑字。
CREATE ROLE indexer_import
    LOGIN
    PASSWORD :'pw'
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOREPLICATION;

-- ── 2. DATABASE 級：顯式 CONNECT + TEMPORARY ────────────────────────────────
-- 為什麼顯式給（不靠 PUBLIC 預設）：URL gate（bulk_load 後的 COUNT(DISTINCT) 驗證）
-- 會 CREATE TEMP TABLE + \copy 灌 URL 清單——TEMP 需要 DATABASE 的 TEMPORARY 權限。
-- 現況靠 PUBLIC 預設權限；日後 DB hardening「REVOKE ... FROM PUBLIC」會讓它無聲斷。
-- 顯式 GRANT 給 indexer_import = 不依賴 PUBLIC，hardening 後仍成立。
GRANT CONNECT, TEMPORARY ON DATABASE nlweb TO indexer_import;

-- ── 3. SCHEMA 級：public 的 USAGE ───────────────────────────────────────────
-- CREATE TEMP + 存取 public schema 內物件需要 schema USAGE（同上，不靠 PUBLIC 預設）。
-- 注意：只給 USAGE，不給 CREATE —— indexer 不該在 public schema 建永久物件（DDL 收斂）。
GRANT USAGE ON SCHEMA public TO indexer_import;

-- ── 4. TABLE 級：articles / chunks 的 DML（最小集）────────────────────────────
-- articles：bulk_load ON CONFLICT (url) DO UPDATE ... RETURNING id
--   → 需要 INSERT（新文章）+ SELECT（RETURNING / 讀回 id）+ UPDATE（既有文章更新）。
GRANT INSERT, SELECT, UPDATE ON articles TO indexer_import;

-- chunks：bulk_load 對每篇文章「DELETE FROM chunks WHERE article_id=... 再 INSERT」原子替換
--   （chunk 縮短殘留根治）→ 需要 DELETE（原子替換的刪舊）+ INSERT + SELECT +
--     UPDATE（ON CONFLICT (article_id, chunk_index) DO UPDATE 分支）。
--   ⚠ DELETE 是 v5 相對 v4 清單新增的——原子替換必需，缺了會讓 chunk 縮短殘留復發。
GRANT INSERT, SELECT, UPDATE, DELETE ON chunks TO indexer_import;

-- ── 5. SEQUENCE 級：id 產生器的 USAGE ───────────────────────────────────────
-- articles / chunks 是 BIGSERIAL PK → INSERT 時 nextval('<table>_id_seq')。
-- USAGE 涵蓋 nextval + currval（現行流程不用 setval，故不給 UPDATE）。
GRANT USAGE ON SEQUENCE articles_id_seq TO indexer_import;
GRANT USAGE ON SEQUENCE chunks_id_seq   TO indexer_import;

-- =============================================================================
-- 明示「不 GRANT」清單（安全邊界——這些是刻意不給，非遺漏）
-- =============================================================================
--   ✗ users / sessions / 任何 auth・用戶資料表：indexer 只碰新聞資料，零用戶資料存取。
--       （fresh role 對未 GRANT 的表天生無權，此處不做任何 GRANT = 天然拒絕；
--         若 prod 曾對 PUBLIC 開過這些表的權限，另需 REVOKE，但那是 DB hardening 範疇。）
--   ✗ user_document_chunks（私有知識庫）：非 indexing pipeline 對象。
--   ✗ DDL（CREATE / ALTER / DROP TABLE、schema CREATE）：indexer 不改結構。
--   ✗ SUPERUSER / CREATEDB / CREATEROLE / REPLICATION：見第 1 段顯式 NO*。
--   ✗ setIamPolicy 等級的 DB 管理權：不適用（PG role 無此概念，列此僅為對齊 IAM 收斂精神）。
--
-- =============================================================================
-- 驗收（人工在 VPS 上跑，對照 plan G3 驗收）
-- =============================================================================
--   負向（應被拒）：
--     docker exec -i nlweb-postgres psql -U indexer_import -d nlweb \
--       -c "SELECT * FROM users LIMIT 1;"        -- 預期：permission denied for table users
--     docker exec -i nlweb-postgres psql -U indexer_import -d nlweb \
--       -c "CREATE TABLE _x(i int);"             -- 預期：permission denied for schema public
--   正向（應通過）：
--     docker exec -i nlweb-postgres psql -U indexer_import -d nlweb \
--       -c "CREATE TEMP TABLE _t(url text); SELECT 1;"   -- 預期：CREATE TABLE / 1
--     docker exec -i nlweb-postgres psql -U indexer_import -d nlweb \
--       -c "SELECT current_user;"                -- 預期：indexer_import（驗本輪真用專用 role）
--
-- =============================================================================
-- 重設密碼（role 已存在時輪替用，非首次建立）
-- =============================================================================
--   ALTER ROLE indexer_import PASSWORD :'pw';    -- 帶 -v pw=... 執行
-- =============================================================================
