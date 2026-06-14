# Database Schema Management Spec

> **Status**: active
> **Established**: 2026-05-13（方案 B 完成）
> **Owner**: M6 Infrastructure
> **Supersedes**: 舊版 database-spec.md（2026-03-12，反映「alembic 是 documentation」狀態，已被本版取代）

---

## 目的

定義 NLWeb 整套 PostgreSQL schema 的**單一管理機制**：所有 schema 變動透過 alembic migration，由 deploy.yml 在 production 自動執行，本機 dev 顯式跑。解決 2026-05-07 揭發的「alembic 在 VPS 從沒跑過、schema 完全靠 `auth_db.initialize()` 自動建表維護」根因問題。

本 spec 對應的決策日誌：`docs/decisions.md`「Alembic 接成 schema source of truth（方案 B）」（2026-05-07 拍板、2026-05-13 完成）。

---

## 架構

### Schema Source of Truth：Alembic

| 元件 | 角色 |
|------|------|
| `code/python/alembic/versions/*.py` | **schema source of truth**。所有表（auth / session / audit / infra / user_document_chunks / feedbacks / faqs / bootstrap_tokens）的 CREATE / ALTER 都在這裡 |
| `code/python/alembic/env.py` | alembic 連線設定。fallback chain：`POSTGRES_CONNECTION_STRING` → `DATABASE_URL` → `ANALYTICS_DATABASE_URL` → SQLite |
| `code/python/alembic.ini` | alembic 設定檔（`script_location = %(here)s/alembic`） |
| `code/python/auth/auth_db.py:initialize()` | **不再**跑 DDL，只做 (1) sanity check：查 `alembic_version` 表是否存在；(2) connection pool warm-up |
| `.github/workflows/deploy.yml` | CI 分階段 up：先 `up -d postgres` + wait healthy → `docker compose run --rm --no-deps -w /app/python app alembic upgrade head`（one-shot）→ 再 `up -d app nginx`。詳見「Operational > Production」段 |
| `infra/init.sql` | **僅**負責 PG container 第一次啟動時建 `pgvector` / `pg_bigm` extension。**不**管 schema |

### 為什麼 extensions 不走 alembic

`CREATE EXTENSION pgvector` / `pg_bigm` 需要 superuser 權限、且 alembic offline mode 與 extension upgrade 不便。container entrypoint（`/docker-entrypoint-initdb.d/init.sql`）跑剛好 — 只在 volume 第一次 init 時跑一次。Extension 版本管理屬基礎建設層，與業務 schema 解耦。

### Migration 鏈（截至 2026-05-13）

```
9df501ad9a13  baseline_auth_tables                    （Sprint 1 auth tables × 6）
    │
c1c6deac2013  add_session_tables                      （Sprint 3 session × 5）
    │
a3f8c2e51d07  add_audit_logs                          （Sprint 5 audit_logs）
    │
b5e9d3f71a42  add_infra_tables                        （articles + chunks + pgvector 1024D）
    │
d4a7e1b83c59  add_user_document_chunks                （user upload 預備）
    │
e39a746fb916  align_users_schema_with_initialize      （users 補 email_verification_expires + password_hash nullable，2026-05-07）
    │
1015e1c40f88  phase_b_align_vps_schema                （Phase 1.5：bootstrap_tokens 收編 + audit_logs uuid/jsonb + org.plan NOT NULL/DEFAULT + session partial index）
    │
7c2f4ae6b1d3  phase_b_collect_feedbacks_faqs          （Phase 2.5：feedbacks + faqs 收編，HEAD）
```

`alembic current` 預期回 `7c2f4ae6b1d3 (head)`。

---

## Migration Lifecycle

### 新增 schema 變動

1. `cd code/python && alembic revision -m "<slug>"` 產生新檔
2. 在新檔的 `upgrade()` / `downgrade()` 用 idempotent guard（見下節「Idempotent guard 規範」）
3. 本機 dev PG 跑 `alembic upgrade head` 驗證
4. 跑 `pytest tests/test_alembic_schema_equivalence.py -v` 驗 path A（alembic head）vs path B（legacy `_get_postgres_schema()` dict）等價 — 如有 drift，回頭補 migration
5. 跑 `cd code/python && python tools/smoke_test.py`
6. 必要時跑 agent E2E（修動 auth/session/admin 流程時必跑）
7. commit + push → deploy.yml 自動執行 `alembic upgrade head`，新 migration 在 VPS 跑

### Idempotent guard 規範

**所有** migration 必須對 dirty PG（既有 schema、可能 partial drift）安全。具體規範：

- **`CREATE TABLE`**：一律寫 `CREATE TABLE IF NOT EXISTS`（透過 `op.execute("CREATE TABLE IF NOT EXISTS ...")`，不用 `op.create_table`，因為後者不支援 IF NOT EXISTS）
- **`CREATE INDEX`**：一律寫 `CREATE INDEX IF NOT EXISTS`
- **`ALTER TABLE ADD COLUMN`**：
  - PG 9.6+ 可用 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
  - 跨 dialect（PG + SQLite）時用 SQLAlchemy `inspect()` 預檢欄位是否存在，再決定要不要 add
- **`ALTER TABLE ALTER COLUMN TYPE` / `SET NOT NULL` / `SET DEFAULT`**：
  - PG 用 `information_schema.columns` 預檢，避免重複 ALTER 失敗
  - 範例：`1015e1c40f88` 對 `audit_logs.user_id` 從 `String(36)` 改 `uuid`、對 `organizations.plan` 補 NOT NULL DEFAULT 'free'
- **PG-only 物件**（pgvector type、partial index、GIN index）：用 `bind.dialect.name == 'postgresql'` 分流，SQLite 路徑跳過或退化為純 column

**驗證**：所有 8 個 migrations 已通過 idempotent 檢查（commit `c7c1d45` 對 5 個 legacy migrations 補 IF NOT EXISTS / inspector pre-check / information_schema 預檢）。VPS first deploy 跑 alembic upgrade head 全 no-op，安全。

### Schema 等價驗證

`code/python/tests/test_alembic_schema_equivalence.py`：

- **目的**：驗證 alembic head 跑出的 schema 與舊版 `auth_db._get_postgres_schema()` dict 跑出的 schema 等價
- **設計**：建兩個獨立 schema（`test_phase_2_a` / `test_phase_2_b`），跑 path A（alembic）vs path B（legacy initialize），dump `information_schema.columns` + `pg_indexes`，逐欄字串比對
- **允許差異 allow-list**：alembic-only 的 `idx_sessions_visibility` / `idx_sessions_deleted` partial index（auth_db legacy 不建）— 這是 alembic 比 legacy **多**的優化 index，不算 drift
- **觸發頻率**：alembic 變動 / `auth_db.py` schema-related code 變動時跑。不進 smoke test（testcontainer 啟動成本高），改在 PR / pre-merge 階段跑

`auth_db._get_postgres_schema()` / `_get_sqlite_schema()` / `_get_index_sql()` 三個函式**保留**但不再供 runtime 使用，僅供本 test 做 regression 對照。Docstring 已標註「runtime schema 來源是 alembic，請勿擴充本 dict」。

---

## Operational

### Production（VPS）

`.github/workflows/deploy.yml` SSH script 採分階段 up：

```yaml
docker compose -f docker-compose.production.yml build app
docker compose -f docker-compose.production.yml up -d postgres
# wait postgres healthy（docker inspect health status loop, 60s window）
docker compose -f docker-compose.production.yml run --rm --no-deps \
    -w /app/python app alembic upgrade head
docker compose -f docker-compose.production.yml up -d app nginx
```

關鍵設計：

- **分階段 up（2026-05-20 修 race）**：原本 `up -d` 一次起所有 services → `sleep 10` → `docker exec alembic`，但 app container 在 `docker exec` 之前就啟動完了，`auth_db._verify_schema_async` 找不到 `alembic_version` 表 → raise RuntimeError → Sentry noise（每次 deploy 一筆 false-positive event）。分階段 up 保證 alembic 跑完才啟 app，schema sanity check 一次 PASS
- **`docker compose run --rm`**：one-shot migration container，跑完即 remove，不殘留
- **`--no-deps`**：postgres 已 up，避免 run command 重新 spawn
- **`-w /app/python`**：Dockerfile `WORKDIR /app`，但 `alembic.ini` 在 `/app/python/`，必須 override working dir
- **環境變數**：`docker compose run` 繼承同 service 的 `env_file`（line 60 `.env`）+ `environment`（line 62 `POSTGRES_CONNECTION_STRING`），與 `up -d app` 一致
- **錯誤處理**：SSH script `set -e` + 顯式 `|| { ... exit 1 }`，alembic 失敗 → SSH exit code 非 0 → `LINE Notify - Failure` step 自動觸發
- **Rollback 安全**：alembic 失敗時 app/nginx 尚未 up（仍跑舊 container）→ production 不中斷
- **不加自動 rollback / retry**：違反「不可 silent fail」原則。失敗時人工診斷，下一次 push 才修復

### Local Development

> 本節目前 placeholder — Phase 4「本機 dev 流程更新」尚未拍板細節。當前推薦做法見 `docs/rented-computer-modifications.md` 與 `docs/in progress/plans/local-dev-pg-setup-plan.md`。Phase 4 完成後本節將補完整流程（含 `docker-compose.dev.yml` 入口腳本是否自動跑 alembic、`data/e2e_test.sql` fixture 是否重 export 等決策）。

當前最低要求：

1. 本機 dev PG 第一次啟動後，**必須**手動跑一次 `cd code/python && alembic upgrade head`
2. 之後 server 啟動時 `auth_db.initialize()` 的 sanity check 才會通過（否則會 raise `RuntimeError: alembic_version table not found. Run 'alembic upgrade head' before starting server.`）

### Sanity Check 行為

`auth_db.py:initialize()` 改寫後（commit `5904b49`）：

```python
async def initialize(self):
    if self._initialized:
        return
    if self.db_type == 'postgres':
        await self._verify_schema_async()  # 查 alembic_version 表
        await self._get_pool()              # warm-up
    else:
        self._verify_schema_sync()
    self._initialized = True
```

- PG：查 `SELECT 1 FROM information_schema.tables WHERE table_name = 'alembic_version'`；0 rows → raise（error message 含解法指令）
- SQLite：對 `auth.db` 查 `sqlite_master`；0 rows → 同樣 raise
- **不可 silent fail**：raise 顯式錯誤，包含 `cd code/python && alembic upgrade head` 解法

### Schema Audit

| 工具 | 路徑 | 用途 |
|------|------|------|
| Schema equivalence test | `code/python/tests/test_alembic_schema_equivalence.py` | alembic head vs legacy initialize schema 字面對比 |
| Phase 1 VPS audit report | `docs/scratch/alembic-vps-schema-audit.md` | 2026-05-08 首次 VPS pg_dump 對 alembic head 的 drift 清單（14 drift） |
| Phase 3b feedbacks/faqs audit | `docs/scratch/alembic-phase-3b-vps-audit-report.md` | 2026-05-12 VPS feedbacks/faqs 對 Phase 2.5 migration 對比（0 drift） |

---

## 已知限制

### Extensions 不在 alembic scope

- `pgvector` / `pg_bigm` 由 `infra/init.sql` 在 PG container 第一次 init 時建
- 升級 extension 版本需手動 SSH VPS 跑 `ALTER EXTENSION ... UPDATE`，不透過 alembic

### Analytics 表暫不在 alembic scope

下列表目前由各自模組的 `CREATE TABLE IF NOT EXISTS` 在 runtime 自動建，**不**在 alembic 管：

- `queries` / `retrieved_documents` / `ranking_scores` / `user_interactions` / `tier_6_enrichment` / `feature_vectors` / `user_feedback`（管理者：`core/analytics_db.py`、`core/query_logger.py`）

這是方案 B 完成後**仍待清理**的 silent backstop。未來追加 cleanup phase 把這些表也收編進 alembic（與本 spec「Idempotent guard 規範」一致即可），但本期不做。完整 analytics schema 定義見 `docs/specs/analytics-spec.md`。

### 雙 dialect（PG + SQLite）支援

- 本機 dev 預設用 PG（`docker-compose.dev.yml`），SQLite 路徑保留供無 docker 環境臨時測試
- PG-only 物件（pgvector / pg_bigm / partial index / JSONB）在 SQLite 路徑跳過（用 `bind.dialect.name` 分流）
- 因此 SQLite 上跑出的 schema 與 PG 不完全等價（差 partial index / JSONB type），這是 known limitation，非 bug

---

## 連線管理

| 用途 | 連線方式 | 環境變數 |
|------|----------|----------|
| Search retrieval | async psycopg pool | `POSTGRES_CONNECTION_STRING` |
| Auth | async psycopg `AsyncConnectionPool` | `POSTGRES_CONNECTION_STRING` → `DATABASE_URL` → `ANALYTICS_DATABASE_URL` |
| Analytics | async psycopg `AsyncConnectionPool` | 同上 |
| Alembic | SQLAlchemy + psycopg3（env.py 內部 rewrite `postgresql://` → `postgresql+psycopg://`） | 同上 |

四個模組 fallback chain 對齊一致（commit `7dce100` 把 env.py 從只讀 `ANALYTICS_DATABASE_URL` 改為三順位）。

---

## 表分類總覽

| 分類 | 表名 | 管理方式 | 狀態 |
|------|------|----------|------|
| **Search** | articles, chunks | alembic `b5e9d3f71a42` | 生產中 |
| **Auth** | organizations, users, org_memberships, invitations, refresh_tokens, login_attempts | alembic `9df501ad9a13` + `e39a746fb916` + `1015e1c40f88`（org.plan） | 生產中 |
| **Session** | search_sessions, org_folders, org_folder_sessions, session_shares, user_preferences | alembic `c1c6deac2013` + `1015e1c40f88`（partial index） | 生產中 |
| **Audit** | audit_logs | alembic `a3f8c2e51d07` + `1015e1c40f88`（uuid/jsonb） | 生產中 |
| **Bootstrap onboarding** | bootstrap_tokens | alembic `1015e1c40f88` 收編 | 生產中 |
| **Feedback / FAQ** | feedbacks, faqs | alembic `7c2f4ae6b1d3` 收編 | 生產中 |
| **User Uploads（預備）** | user_document_chunks | alembic `d4a7e1b83c59` | 預備 |
| **Analytics** | queries / retrieved_documents / ranking_scores / user_interactions / tier_6_enrichment / feature_vectors / user_feedback | `core/analytics_db.py` runtime CREATE | 暫不在 alembic scope（見「已知限制」） |

**詳細欄位定義** 見：
- Auth / Session / Audit：`docs/specs/login-spec.md`
- Analytics：`docs/specs/analytics-spec.md`
- Search（articles/chunks/HNSW）：`docs/specs/indexing-spec.md` + `docs/specs/bm25-spec.md`

---

## History

### 2026-05-20 Phase 5 follow-up：deploy.yml schema-init race 修

修 alembic 方案 B 完成後遺留的 deploy race：原本 `docker compose up -d` 一次起所有 services
後再 `docker exec alembic upgrade head`，但 app container 啟動時 `alembic_version` 表還沒建
→ `auth_db._verify_schema_async` raise RuntimeError → Sentry 每次 deploy 一筆 false-positive
event。改為分階段 up（postgres → wait healthy → `docker compose run --rm` 跑 alembic →
up app + nginx）。

詳細 plan：`docs/in progress/plans/deploy-yml-schema-init-race-fix-plan.md`。

### 2026-05-13 方案 B 完成

alembic 從「死的 documentation」變成「活的 schema gate」。9 個 commits 從 `a3d73ce` 到 `c7c1d45`：

| Phase | Commit | 內容 |
|-------|--------|------|
| Preflight (前置) | `c39cc48` | `e39a746fb916` align users schema with auth_db.initialize（admin resend activation 時順帶完成） |
| Phase 1.5 | `a3d73ce` | catch-up migration `1015e1c40f88` 對齊 VPS 真實 schema（bootstrap_tokens 收編 + audit_logs uuid/jsonb + org.plan NOT NULL/DEFAULT + 兩個 session partial index） |
| Phase 2 E2E | `b6d768a` | Phase 1.5 catch-up migration 的 agent E2E PASS 紀錄 |
| Phase 2.5 | `68fd851` | catch-up migration `7c2f4ae6b1d3` 收編 feedbacks + faqs |
| Phase 2 Main | `5904b49` | `auth_db.initialize()` 移除 DDL，改 sanity check + pool warm-up |
| Phase 2 env.py | `7dce100` | env.py fallback chain 對齊 CLAUDE.md 規範 |
| Phase 2 test | `08546b3` | unit test `test_alembic_schema_equivalence.py` |
| Phase 3a | `73d989c` | deploy.yml 加 `alembic upgrade head` step |
| Migrations idempotent fix | `c7c1d45` | 5 個 legacy migrations 補 idempotent guard（IF NOT EXISTS / inspector pre-check / information_schema 預檢）— VPS first deploy 全 no-op |

CEO 拍板：2026-05-07（決策日誌）。完成驗證：2026-05-13（VPS deploy 後 `alembic current` 回 `7c2f4ae6b1d3`、health check pass）。

Phase 3b VPS audit confirm feedbacks/faqs 0 drift（`docs/scratch/alembic-phase-3b-vps-audit-report.md`）→ 因此**不需要** Phase 3.5 ALTER catch-up migration。

### 2026-03-12 舊版

舊版 database-spec.md（同檔名）反映「alembic + auth_db.initialize 雙系統並存」狀態，是 D2 deferred 議題的描述版本。已被本版 supersede。

---

## References

| 類別 | 文件 |
|------|------|
| **Strategy plan** | `docs/in progress/plans/alembic-architecture-fix-plan.md` |
| **Phase 2 plan** | `docs/in progress/plans/alembic-phase-2-remove-initialize-ddl-plan.md` |
| **Phase 3a plan** | `docs/in progress/plans/alembic-phase-3a-deploy-yml-plan.md` |
| **Phase 1 audit** | `docs/scratch/alembic-vps-schema-audit.md` |
| **Phase 3b audit** | `docs/scratch/alembic-phase-3b-vps-audit-report.md` |
| **決策日誌** | `docs/decisions.md`「Alembic 接成 schema source of truth（方案 B）」 |
| **Lesson** | `memory/lessons-infra-deploy.md`「Alembic 在 VPS production 從沒跑過 — 死的 documentation」 |
| **Login spec** | `docs/specs/login-spec.md`（D2 deferred 已 mark resolved） |
| **Analytics spec** | `docs/specs/analytics-spec.md`（analytics 表 schema 細節） |
| **Indexing spec** | `docs/specs/indexing-spec.md`（articles / chunks / HNSW 細節） |
