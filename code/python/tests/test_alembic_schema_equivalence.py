"""Phase 2 regression test：驗 alembic head 與 legacy `initialize()` schema 等價。

設計目的
--------
Phase 2 main refactor 把 `auth_db.initialize()` 從「啟動時跑 DDL」改成
sanity check + connection pool warm-up，讓 alembic 成為唯一 schema source
of truth。為了確保「移除 initialize DDL 後 server 跑 alembic upgrade head
能得到與舊版完全等價（或 alembic 嚴格優於）的 schema」，本 test 跑兩條
獨立 schema 構建 path 並比對 information_schema dump。

兩條 path
---------
- **Path A**：在乾淨 PG database 跑 `alembic upgrade head` —— 這是 Phase 2
  之後 runtime 的真實路徑。
- **Path B**：在乾淨 PG database 跑 legacy `_get_postgres_schema()` +
  `_get_index_sql()` 的 CREATE statements —— 這是 Phase 2 之前
  `initialize()` 的真實行為。**不**呼叫 `AuthDB.initialize()`，因為它已
  被改為 sanity check（會 raise，不會建表）。

PG fixture / 隔離策略
--------------------
用 `testcontainers[postgresql]` 起一個 throw-away PG container，image 為
`pgvector/pgvector:pg16`（baseline migration `b5e9d3f71a42` 用到
`vector(1024)` type，純 `postgres:16` image 跑不起來）。

**隔離方案：兩個獨立 database**（非 schema namespace）
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
容器啟動後，在 default `test` database 內跑 `CREATE DATABASE test_phase_2_a`
與 `CREATE DATABASE test_phase_2_b`，兩條 path 分別連到自己的 DB。

**為什麼不用 schema namespace + search_path query string？**
alembic Config 底層用 `configparser`，`%` 是 interpolation 特殊字元 —
DSN 內 `?options=-csearch_path%3D...` 的 `%3D` 會被當 interpolation token
觸發 `ValueError: invalid interpolation syntax`。改走兩個獨立 DB 的好處：
  1. DSN 純淨（無 query string、無 `%` 字元），不踩 configparser 雷
  2. alembic env.py 不需動：env.py 自己呼叫 create_engine()，test 無法在
     env.py 外掛 SQLAlchemy event listener 設 search_path
  3. DB-level 隔離比 schema 嚴格，不會有 default schema 污染風險

如何在 testcontainer 內跑 alembic
---------------------------------
透過 monkeypatch 環境變數 `POSTGRES_CONNECTION_STRING` 指向 container 內
**Path A 的專屬 database** DSN，並 override `alembic.ini` 內 `sqlalchemy.url`
（透過 alembic Config API 設）。這樣 `alembic/env.py:_get_database_url()`
會自然讀到 container DSN，不需要改 env.py。

比對策略
--------
`dump_information_schema()` 取以下欄位後做純字串比對：
  - 表名清單（排序）
  - 每張表的欄位：name, data_type, is_nullable, column_default
  - 每張表的 index：indexname, indexdef

兩個 DB 都把 schema dump 限定在 `public` schema（每個 DB 各自 default）。

`compare_schemas()` 接受一個 explicit allow-list (`allow_a_only`)，用來
標記「alembic 比 legacy 多但可接受」的 schema item（例如效能優化的
partial index）。allow-list 必須有 inline comment 說明為什麼可接受 —
絕不 silent 過濾任何 diff（這會違反「不可 silent fail / 不可 reward
hack」原則）。

Skip 規則
---------
若環境沒裝 docker / testcontainers，test 標 skip 但 **import 不能爆**
（為了讓 smoke test + 其他 pytest collection 仍能跑）。docker 可用性
透過 try/except subprocess 偵測，**不**silent fail — 失敗訊息會明確指
出原因。

執行方式
--------
```bash
cd code/python && pytest tests/test_alembic_schema_equivalence.py -v
```

依賴：`testcontainers[postgresql]>=4.0.0`（見 requirements.txt，dev-only）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pytest


# ── Docker / testcontainers 可用性偵測（import 不能爆） ───────────────

def _docker_available() -> bool:
    """偵測 docker daemon 是否可連線。

    不 silent fail：找不到 docker binary 或 daemon 沒起來會被視為「無
    docker」，但理由會反映在 pytest skip message。
    """
    docker_bin = shutil.which("docker")
    if docker_bin is None:
        return False
    try:
        result = subprocess.run(
            [docker_bin, "info"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


_DOCKER_OK = _docker_available()

# import testcontainers 時 wrap try/except —— 缺套件不能 break collection
try:
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-not-found]
    _TESTCONTAINERS_OK = True
    _TESTCONTAINERS_IMPORT_ERROR: Optional[str] = None
except ImportError as e:
    PostgresContainer = None  # type: ignore[assignment,misc]
    _TESTCONTAINERS_OK = False
    _TESTCONTAINERS_IMPORT_ERROR = str(e)

# psycopg 可能也沒裝（不太可能，但保險起見）
try:
    import psycopg  # type: ignore[import-not-found]
    _PSYCOPG_OK = True
except ImportError:
    psycopg = None  # type: ignore[assignment]
    _PSYCOPG_OK = False


_PRECONDITIONS_OK = _DOCKER_OK and _TESTCONTAINERS_OK and _PSYCOPG_OK


def _skip_reason() -> str:
    parts: List[str] = []
    if not _DOCKER_OK:
        parts.append("docker daemon 不可用（找不到 binary 或 daemon 沒起來）")
    if not _TESTCONTAINERS_OK:
        parts.append(
            f"testcontainers 套件未安裝（pip install 'testcontainers[postgresql]>=4.0.0'；"
            f"import error: {_TESTCONTAINERS_IMPORT_ERROR}）"
        )
    if not _PSYCOPG_OK:
        parts.append("psycopg 套件未安裝")
    return "; ".join(parts) if parts else "preconditions ok"


pytestmark = pytest.mark.skipif(
    not _PRECONDITIONS_OK,
    reason=f"Phase 2 schema equivalence test 需要 docker + testcontainers + psycopg: {_skip_reason()}",
)


# ── 路徑 / 常數 ────────────────────────────────────────────────────

# code/python/ 目錄（alembic.ini 所在）
_PYTHON_ROOT = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _PYTHON_ROOT / "alembic.ini"

# 兩條 path 用獨立 PG database（不是 schema namespace —— 見 module docstring）
DB_PATH_A = "test_phase_2_a"  # alembic upgrade head 結果
DB_PATH_B = "test_phase_2_b"  # legacy initialize() 結果

# 兩條 path 都把 schema dump 限定在 public schema（PG default schema）
PUBLIC_SCHEMA = "public"

# Allow-list：alembic 比 legacy 多的 schema items（is 加分項）
# 每個 entry 必須有 inline comment 說明為什麼可接受 — 不可 silent 過濾。
ALLOWED_PATH_A_ONLY_INDEXES: Set[str] = {
    # alembic 從 c1c6deac2013 + 1015e1c40f88 建的 partial indexes，
    # 用來加速 search_sessions visibility / deleted_at 過濾查詢。
    # legacy `auth_db._get_index_sql()` 不建這兩個 —— alembic 是 schema
    # 變嚴格 / 變完整，不是 drift。
    "idx_sessions_visibility",
    "idx_sessions_deleted",
}

# Allow-list：只在 alembic (A) 出現的 tables（is 加分項）。
# 每個 entry 必須有 inline comment 說明為什麼可接受 — 不可 silent 過濾。
# 設計理念：legacy `auth_db._get_postgres_schema()` 只負責 auth / session
# 相關 15 張表；alembic 是**全系統** schema source of truth，會收編
# retrieval / indexing 相關的表（這些表本來就不屬於 auth_db 管轄）。
ALLOWED_PATH_A_ONLY_TABLES: Set[str] = {
    # baseline migration `b5e9d3f71a42` 收編 retrieval / crawler 相關表：
    # - articles: 爬蟲收編的文章 metadata (crawler / indexing pipeline 寫)
    # - chunks: chunking_engine.py 切的 chunk + embedding (vector(1024))
    # - user_document_chunks: private docs (d4a7e1b83c59 migration 加,
    #   user_data_processor.py / user_postgres_provider.py 用)
    # 這三表都**不在** auth_db 管轄，legacy initialize() 不應該也不會建。
    # alembic 收編是 by design — 全系統單一 schema source of truth。
    "articles",
    "chunks",
    "user_document_chunks",
}

# Allow-list：alembic (A) 比 legacy (B) 更嚴格的 column overrides。
# 格式：{(table_name, column_name): (a_tuple, b_tuple, reason)}
# 比對時若 column 差異**精確命中**這份 list，視為加分項 (A 嚴) 而非 drift。
# 每個 entry 必須有 reason — 不可 silent 過濾。
ALLOWED_PATH_A_STRICTER_COLUMNS: Dict[Tuple[str, str], Tuple[Tuple[Any, ...], Tuple[Any, ...], str]] = {
    # alembic migration `e39a746fb916` (align_users_schema_with_initialize)
    # 把 organizations.plan 改為 NOT NULL + default 'free'。legacy
    # `_get_postgres_schema()` 還停在舊版 nullable 無 default，這是
    # legacy schema 落後 alembic 的明證 (Phase 2 alembic 接管 schema 的
    # 動機之一)。A 嚴 B 鬆是加分項，非 drift。
    ("organizations", "plan"): (
        ("plan", "character varying", "NO", "'free'::character varying"),  # A (alembic, 嚴)
        ("plan", "character varying", "YES", None),                          # B (legacy, 鬆)
        "alembic migration e39a746fb916 收緊 NOT NULL DEFAULT 'free'",
    ),
}


# ── PG fixture ────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def pg_container():
    """Module-scoped PostgreSQL container with pgvector extension.

    使用 `pgvector/pgvector:pg16` image，因為 baseline migration
    `b5e9d3f71a42` 建 `user_document_chunks` 時 column type 是
    `vector(1024)`，純 `postgres:16` image 沒有 pgvector extension 跑
    不起來。

    Container 啟動後在 default `test` database 內建立兩個獨立 database
    （`test_phase_2_a` / `test_phase_2_b`），並在**每個**獨立 database
    內各自 `CREATE EXTENSION vector`（extension 是 per-database）。

    `scope="module"` 讓兩條 path test 共用同一個 container，減少啟動成本。
    """
    assert PostgresContainer is not None  # mypy / 防呆，前面 skipif 已擋
    container = PostgresContainer("pgvector/pgvector:pg16")
    with container as pg:
        # 1. 先連 default DB（testcontainers 預設名 `test`），建兩個獨立 DB
        default_dsn = _sqlalchemy_url_to_psycopg(pg.get_connection_url())
        with psycopg.connect(default_dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                # 防呆：DROP IF EXISTS 處理上次 test session 留下的殘留
                for db_name in (DB_PATH_A, DB_PATH_B):
                    cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
                    cur.execute(f'CREATE DATABASE "{db_name}"')

        # 2. 在每個獨立 DB 內各自 CREATE EXTENSION vector
        #    (extension 是 per-database，default DB 裝的不會被新 DB 繼承)
        for db_name in (DB_PATH_A, DB_PATH_B):
            db_dsn = _build_dsn_for_db(default_dsn, db_name)
            with psycopg.connect(db_dsn, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")

        yield pg


@pytest.fixture
def default_dsn(pg_container) -> str:
    """psycopg-style DSN 指向 default `test` database（無 SQLAlchemy prefix）。

    用於需要 admin 操作（CREATE DATABASE 等）的場景；個別 path 的
    DDL / dump 應該用 `_build_dsn_for_db()` 切到專屬 DB。
    """
    return _sqlalchemy_url_to_psycopg(pg_container.get_connection_url())


# ── 主測試 ─────────────────────────────────────────────────────────

def test_alembic_head_equivalent_to_legacy_initialize(pg_container, default_dsn: str) -> None:
    """跑兩條 path 並比對 schema 等價。

    流程：
      1. Path A: 連 `test_phase_2_a` DB → 跑 alembic upgrade head →
         dump public schema → schema A。
      2. Path B: 連 `test_phase_2_b` DB → 直接執行
         `_get_postgres_schema()` 的 CREATE statements + 跑
         `_get_index_sql()` → dump public schema → schema B。
      3. compare_schemas(A, B, allow_a_only=ALLOWED_PATH_A_ONLY_INDEXES)
         應回傳空 list。
    """
    dsn_a = _build_dsn_for_db(default_dsn, DB_PATH_A)
    dsn_b = _build_dsn_for_db(default_dsn, DB_PATH_B)

    # ── Path A: alembic upgrade head ──
    _run_alembic_upgrade_on_db(dsn_a)
    schema_a = dump_information_schema(dsn_a, PUBLIC_SCHEMA)

    # ── Path B: legacy initialize() logic ──
    _run_legacy_initialize_logic(dsn_b)
    schema_b = dump_information_schema(dsn_b, PUBLIC_SCHEMA)

    # ── Compare ──
    diff = compare_schemas(
        schema_a,
        schema_b,
        allow_a_only_indexes=ALLOWED_PATH_A_ONLY_INDEXES,
        allow_a_only_tables=ALLOWED_PATH_A_ONLY_TABLES,
        allow_a_stricter_columns=ALLOWED_PATH_A_STRICTER_COLUMNS,
    )
    assert diff == [], (
        "Schema mismatch between alembic head (path A) and legacy "
        "initialize() (path B):\n" + "\n".join(diff)
    )


# ── Helpers ────────────────────────────────────────────────────────

def _sqlalchemy_url_to_psycopg(url: str) -> str:
    """把 `postgresql+psycopg2://...` 或 `postgresql://...` 轉成 psycopg DSN。

    testcontainers 預設 URL 是 SQLAlchemy 格式（含 `+driver`），psycopg
    直接接會失敗。strip 掉 `+driver` 部分即可。
    """
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql://" + url[len("postgresql+psycopg2://"):]
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://"):]
    return url


def _build_dsn_for_db(default_dsn: str, db_name: str) -> str:
    """把 default DSN 的 database 部分替換成指定的 db_name。

    Default DSN 格式：`postgresql://user:pass@host:port/test`
    替換最後一段 path component。完全不加 query string，避免 alembic
    Config 的 configparser interpolation 踩 `%` 雷。
    """
    # 用 rsplit 切最後一個 `/`，保留 schema://user:pass@host:port 部分
    base, _ = default_dsn.rsplit("/", 1)
    return f"{base}/{db_name}"


def _run_alembic_upgrade_on_db(dsn: str) -> None:
    """在指定的乾淨 PG database 跑 alembic upgrade head。

    用 alembic Config API 而非 subprocess —— 比較好控制 sqlalchemy.url
    override。透過環境變數 + Config.set_main_option 雙重 override，
    確保 env.py 的 `_get_database_url()` 讀到 container DSN。

    DSN 內**不含** query string（已改用獨立 DB 隔離），所以不會踩到
    configparser interpolation 的 `%` 雷。
    """
    from alembic import command
    from alembic.config import Config

    # alembic env.py 用 SQLAlchemy URL 格式（含 +psycopg dialect rewrite）
    sqla_url = "postgresql+psycopg://" + dsn[len("postgresql://"):]

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", sqla_url)
    cfg.set_main_option("script_location", str(_PYTHON_ROOT / "alembic"))

    # 確保 env.py 的 env-var fallback 也指向同一個 DB（雖然 set_main_option
    # 設了 sqlalchemy.url 就會優先讀到，但保險起見）
    old_env = os.environ.get("POSTGRES_CONNECTION_STRING")
    os.environ["POSTGRES_CONNECTION_STRING"] = dsn
    try:
        command.upgrade(cfg, "head")
    finally:
        if old_env is None:
            os.environ.pop("POSTGRES_CONNECTION_STRING", None)
        else:
            os.environ["POSTGRES_CONNECTION_STRING"] = old_env


def _run_legacy_initialize_logic(dsn: str) -> None:
    """在指定的乾淨 PG database 跑 legacy initialize 的 DDL 邏輯。

    **不**呼叫 `AuthDB.initialize()` —— 那已被 Phase 2 main 改為 sanity
    check，會 raise（因為 alembic_version 表還不存在）。改為直接 iterate
    `_get_postgres_schema()` 跟 `_get_index_sql()` 跑 raw CREATE statements。
    """
    from auth.auth_db import AuthDB

    # 建 AuthDB instance 但**不**呼叫 initialize() —— 我們只要它的
    # schema dict / index list（純 string，無 side effect）。
    db = AuthDB(db_path="/tmp/unused_for_path_b.db")  # path 隨便，不會用
    schema_dict = db._get_postgres_schema()
    index_list = db._get_index_sql()

    # 直接連專屬 PG DB，跑 CREATE statements 到 public schema（default）。
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            for table_name, create_sql in schema_dict.items():
                cur.execute(create_sql)
            for index_sql in index_list:
                cur.execute(index_sql)


# ── information_schema dump + compare ─────────────────────────────

def dump_information_schema(dsn: str, schema_name: str) -> Dict[str, Any]:
    """Dump 一個 PG schema 的結構為純 dict（便於字串比對）。

    取下列欄位：
      - tables: 表名清單（排序）
      - columns: 每張表的 (column_name, data_type, is_nullable, column_default)
      - indexes: 每張表的 (indexname, indexdef)

    為了讓比對結果穩定，所有 collection 都排序後存。
    """
    result: Dict[str, Any] = {
        "tables": [],
        "columns": {},  # table_name -> List[Tuple[str, str, str, Optional[str]]]
        "indexes": {},  # table_name -> List[Tuple[str, str]]
    }

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 表名清單
            cur.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = %s AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """,
                (schema_name,),
            )
            tables: List[str] = [r[0] for r in cur.fetchall()]
            result["tables"] = tables

            # 每張表的欄位
            for table_name in tables:
                cur.execute(
                    """
                    SELECT column_name, data_type, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY column_name
                    """,
                    (schema_name, table_name),
                )
                cols: List[Tuple[str, str, str, Optional[str]]] = [
                    (r[0], r[1], r[2], r[3]) for r in cur.fetchall()
                ]
                result["columns"][table_name] = cols

            # 每張表的 index（從 pg_indexes 取）
            for table_name in tables:
                cur.execute(
                    """
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname = %s AND tablename = %s
                    ORDER BY indexname
                    """,
                    (schema_name, table_name),
                )
                idxs: List[Tuple[str, str]] = [
                    (r[0], r[1]) for r in cur.fetchall()
                ]
                result["indexes"][table_name] = idxs

    return result


def compare_schemas(
    schema_a: Dict[str, Any],
    schema_b: Dict[str, Any],
    *,
    allow_a_only_indexes: Set[str],
    allow_a_only_tables: Set[str],
    allow_a_stricter_columns: Dict[Tuple[str, str], Tuple[Tuple[Any, ...], Tuple[Any, ...], str]],
) -> List[str]:
    """比對兩個 dump 結果，回傳人類可讀的 diff messages（empty = pass）。

    Args:
        schema_a: alembic head 跑出的結果（path A）。
        schema_b: legacy initialize() 跑出的結果（path B）。
        allow_a_only_indexes: 只能在 A 出現的 index 名單（白名單），
            每個必須在 ALLOWED_PATH_A_ONLY_INDEXES 上方有 inline comment
            說明為什麼可接受。alembic_version 內建表也視為 A-only 可接受
            （alembic 自己管理，legacy initialize 不會建）。
        allow_a_only_tables: 只能在 A 出現的 table 名單（白名單）— 用於
            alembic 收編 retrieval/indexing 等不屬於 auth_db 管轄的表。
        allow_a_stricter_columns: A 嚴 B 鬆的欄位差異白名單，key 為
            (table, column)，value 為 (A 的 tuple, B 的 tuple, 原因)。
            欄位差異必須**精確命中**白名單才視為加分項。

    比對嚴格度：
      - 表級：A 跟 B 的表名集合必須相同，**例外**：`alembic_version`
        內建表 + `allow_a_only_tables` 白名單允許只在 A 出現。
      - 欄位級：對每個 table，欄位 tuple list 必須完全相同（含 type、
        nullability、default），**例外** `allow_a_stricter_columns` 列出
        的 (table, column) 差異若精確命中白名單條目，視為加分項。
      - Index 級：A 跟 B 的 index 集合必須相同，**例外**
        `allow_a_only_indexes` 列出的 index 允許只在 A 出現；A 有 B 沒有
        且不在白名單 → diff；B 有 A 沒有 → 永遠 diff。
    """
    diffs: List[str] = []

    tables_a: Set[str] = set(schema_a["tables"])
    tables_b: Set[str] = set(schema_b["tables"])

    # alembic 內建的 schema-tracking 表，允許只在 A 出現
    ALEMBIC_INTERNAL_TABLES: Set[str] = {"alembic_version"}

    a_only_tables = (tables_a - tables_b) - ALEMBIC_INTERNAL_TABLES - allow_a_only_tables
    b_only_tables = tables_b - tables_a

    if a_only_tables:
        diffs.append(
            f"  [tables] alembic-only (A-only, 不在白名單): {sorted(a_only_tables)}"
        )
    if b_only_tables:
        diffs.append(
            f"  [tables] legacy-only (B-only, 表示 alembic 漏收編): {sorted(b_only_tables)}"
        )

    # 對共同存在的表，比對欄位 + index
    common_tables = (tables_a & tables_b)
    for table in sorted(common_tables):
        cols_a = schema_a["columns"].get(table, [])
        cols_b = schema_b["columns"].get(table, [])
        if cols_a != cols_b:
            # 嘗試把已知 A-stricter 的 column 差異從 diff set 移除
            a_set = set(cols_a)
            b_set = set(cols_b)
            unexpected_a_only_cols: List[Tuple[Any, ...]] = []
            unexpected_b_only_cols: List[Tuple[Any, ...]] = []
            for c in sorted(a_set - b_set):
                key = (table, c[0])
                allowed = allow_a_stricter_columns.get(key)
                if allowed is not None and allowed[0] == c:
                    continue  # A 嚴版本，精確命中白名單 → 加分項
                unexpected_a_only_cols.append(c)
            for c in sorted(b_set - a_set):
                key = (table, c[0])
                allowed = allow_a_stricter_columns.get(key)
                if allowed is not None and allowed[1] == c:
                    continue  # B 鬆版本，精確命中白名單 → 加分項
                unexpected_b_only_cols.append(c)
            if unexpected_a_only_cols or unexpected_b_only_cols:
                diffs.append(f"  [columns] table='{table}' differs:")
                for c in unexpected_a_only_cols:
                    diffs.append(f"      A-only (not whitelisted): {c}")
                for c in unexpected_b_only_cols:
                    diffs.append(f"      B-only (not whitelisted): {c}")

        idxs_a = {name: defn for name, defn in schema_a["indexes"].get(table, [])}
        idxs_b = {name: defn for name, defn in schema_b["indexes"].get(table, [])}

        a_only_idx = set(idxs_a) - set(idxs_b)
        b_only_idx = set(idxs_b) - set(idxs_a)
        common_idx = set(idxs_a) & set(idxs_b)

        # A-only index 必須全部在白名單，否則 diff
        unexpected_a_only = a_only_idx - allow_a_only_indexes
        if unexpected_a_only:
            diffs.append(
                f"  [indexes] table='{table}' A-only (not whitelisted): {sorted(unexpected_a_only)}"
            )
        if b_only_idx:
            diffs.append(
                f"  [indexes] table='{table}' B-only (alembic missing): {sorted(b_only_idx)}"
            )
        # 對共同 index，definition 必須完全相同
        for idx_name in sorted(common_idx):
            if idxs_a[idx_name] != idxs_b[idx_name]:
                diffs.append(
                    f"  [indexes] table='{table}' index='{idx_name}' definition differs:\n"
                    f"      A: {idxs_a[idx_name]}\n"
                    f"      B: {idxs_b[idx_name]}"
                )

    return diffs
