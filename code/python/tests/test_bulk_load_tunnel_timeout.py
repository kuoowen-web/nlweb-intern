"""Bulk Load tunnel 逾時防護測試 —— 跨洲 SSH tunnel「半開」連線的止血機制。

改造動機（實機實測）：GPU embed VM 經跨洲 SSH tunnel 連 prod PostgreSQL。tunnel
若「半開」（TCP 連線斷了但雙方狀態未偵測），psycopg 送出 SQL 後**無限等 DB 回應**
—— 實機遇一台 GPU VM 卡 11 分鐘 0% CPU 沒被打斷。

三道防護（皆在連線層，不動任何寫入邏輯）：
1. connect_timeout=15：建連上限（tunnel 建連階段就卡死時止血）。
2. TCP keepalive（keepalives=1, keepalives_idle=30, keepalives_interval=10,
   keepalives_count=3）：治半開連線的**主力**。~60s 內偵測對端不回應 → 連線判死、
   psycopg 拋錯，而非無限等。
3. statement_timeout（值 STATEMENT_TIMEOUT）：**用連線參數 options="-c statement_timeout=..."
   而非連上後 SQL SET**——psycopg3 非 autocommit 下 `SET`（非 SET LOCAL）綁隱式 transaction，
   降級路徑必經的 rollback 會把它回滾成 0（無限）→ 防護失效。options 是 libpq 連線層、
   不綁 transaction，rollback 不影響。單條 SQL 超時 → PG 拋 QueryCanceled，走現有 flush()
   降級 → 該檔進 error 不進 done（下次重跑）。

測試分兩層：
- **mock 層（本檔多數）**：不需 docker，驗 psycopg.connect 帶了正確 kwargs（含 options
  設 statement_timeout）+ **不再用連上後 SQL SET**（回歸鎖）。mock connect 即可驗。
- **真 PG 層（testcontainers）**：驗 statement_timeout 短值時 pg_sleep 被中斷拋
  QueryCanceled、**rollback 後 statement_timeout 仍生效**（options 修法核心）、且在 flush
  降級路徑下該檔進 error 不進 done。需 docker，無 docker 時 skip。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional
from unittest import mock

import numpy as np
import pytest

from indexing import bulk_load


# ── 常數：STATEMENT_TIMEOUT 存在且可調 ──────────────────────────────

def test_statement_timeout_constant_exists():
    """statement_timeout 須為檔頭可調常數（跟 COMMIT_BATCH_SIZE 同區）。"""
    assert hasattr(bulk_load, "STATEMENT_TIMEOUT"), (
        "須有可調的 STATEMENT_TIMEOUT 常數（session 級 SET statement_timeout 的值）"
    )
    assert bulk_load.STATEMENT_TIMEOUT == "300s", (
        f"STATEMENT_TIMEOUT 應為 '300s'（正常批次 <10s，300s 有充足餘裕），"
        f"得 {bulk_load.STATEMENT_TIMEOUT!r}"
    )


# ── mock 層：連線帶正確的 keepalive / connect_timeout kwargs ──────────

class _FakeConn:
    """psycopg.connect() 的 context-manager 替身，記錄 execute 呼叫。"""

    def __init__(self):
        self.executed: List[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self

    def rollback(self):
        pass


def _run_main_load_dir_with_mock_connect(tmp_path: Path):
    """在無檔案對的空目錄跑 main_load_dir，攔截 psycopg.connect 拿到 kwargs。

    空目錄 → 沒有 file pair → main_load_dir 在連線前就 return（見實作：
    `if not pairs: return`）。故本 helper 需至少一對 dummy 檔讓流程進到 connect。
    """
    # 造一對空 pair 讓 main_load_dir 進到 psycopg.connect（內容不重要，
    # 我們只驗 connect kwargs，不驗寫入）。
    (tmp_path / "d.jsonl").write_text("", encoding="utf-8")
    np.save(tmp_path / "d.npy", np.zeros((1, 1024), dtype=np.float32))

    fake = _FakeConn()
    with mock.patch.object(bulk_load.psycopg, "connect", return_value=fake) as mconn:
        bulk_load.main_load_dir(str(tmp_path), "postgresql://x@localhost/y")
    return mconn, fake


def test_connect_has_connect_timeout(tmp_path):
    """psycopg.connect 帶 connect_timeout=15（建連上限）。"""
    mconn, _ = _run_main_load_dir_with_mock_connect(tmp_path)
    _args, kwargs = mconn.call_args
    assert kwargs.get("connect_timeout") == 15, (
        f"connect 應帶 connect_timeout=15，得 kwargs={kwargs}"
    )


def test_connect_has_tcp_keepalive_kwargs(tmp_path):
    """psycopg.connect 帶完整 TCP keepalive kwargs（治半開連線主力）。"""
    mconn, _ = _run_main_load_dir_with_mock_connect(tmp_path)
    _args, kwargs = mconn.call_args
    assert kwargs.get("keepalives") == 1, f"缺 keepalives=1，得 {kwargs}"
    assert kwargs.get("keepalives_idle") == 30, f"缺 keepalives_idle=30，得 {kwargs}"
    assert kwargs.get("keepalives_interval") == 10, (
        f"缺 keepalives_interval=10，得 {kwargs}"
    )
    assert kwargs.get("keepalives_count") == 3, f"缺 keepalives_count=3，得 {kwargs}"


def test_connect_preserves_row_factory(tmp_path):
    """三道防護不得破壞既有的 row_factory=dict_row（下游 r["url"]/r["id"] 依賴）。"""
    mconn, _ = _run_main_load_dir_with_mock_connect(tmp_path)
    _args, kwargs = mconn.call_args
    assert kwargs.get("row_factory") is bulk_load.dict_row, (
        f"row_factory 必須仍是 dict_row，得 {kwargs}"
    )


def test_statement_timeout_set_via_connect_options_not_sql_set(tmp_path):
    """statement_timeout **必須**經連線參數 options 傳（`-c statement_timeout=...`），
    **不可**用連上後 `SET statement_timeout`。

    真因（真 PG 驗證）：psycopg3 預設非 autocommit，連上後的 `SET`（非 SET LOCAL）綁在
    隱式 transaction 上，降級路徑必經的 conn.rollback() 會把 statement_timeout 一併
    回滾成 0（無限）→ 降級逐篇重試時防護失效、半開連線下又無限等。options 是 libpq
    連線層設定，不綁 transaction，rollback 不影響。

    本測試同時鎖兩件事：
    (a) connect kwargs 帶 options 且含 STATEMENT_TIMEOUT 常數值；
    (b) code **不再**用連上後 SQL `SET statement_timeout`（防退回舊的會被 rollback 回滾的做法）。
    """
    mconn, fake = _run_main_load_dir_with_mock_connect(tmp_path)
    _args, kwargs = mconn.call_args

    # (a) options 連線參數帶 statement_timeout（不綁 transaction，rollback 不回滾）
    options = kwargs.get("options", "")
    assert "statement_timeout" in options, (
        f"connect 應帶 options='-c statement_timeout=...'，得 kwargs={kwargs}"
    )
    assert bulk_load.STATEMENT_TIMEOUT in options, (
        f"options 應含 STATEMENT_TIMEOUT 常數值 {bulk_load.STATEMENT_TIMEOUT!r}，"
        f"得 options={options!r}"
    )

    # (b) 回歸鎖：不得再用連上後 SQL SET statement_timeout（會被 rollback 回滾）
    sql_set_calls = [
        sql for (sql, _p) in fake.executed
        if isinstance(sql, str)
        and "set" in sql.lower()
        and "statement_timeout" in sql.lower()
    ]
    assert not sql_set_calls, (
        "不可用連上後 SQL `SET statement_timeout`（非 autocommit 下綁 transaction，"
        f"降級 rollback 會回滾防護）；改用連線參數 options。誤用的 SQL：{sql_set_calls}"
    )


# ── 真 PG 層（testcontainers）：statement_timeout 中斷 → 降級 → error 不進 done ──

def _docker_available() -> bool:
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

try:
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-not-found]
    _TESTCONTAINERS_OK = True
    _TC_IMPORT_ERROR: Optional[str] = None
except ImportError as e:  # pragma: no cover
    PostgresContainer = None  # type: ignore[assignment,misc]
    _TESTCONTAINERS_OK = False
    _TC_IMPORT_ERROR = str(e)

try:
    import psycopg as _psycopg  # noqa: F401
    _PSYCOPG_OK = True
except ImportError:  # pragma: no cover
    _psycopg = None  # type: ignore[assignment]
    _PSYCOPG_OK = False


_PG_PRECONDITIONS_OK = _DOCKER_OK and _TESTCONTAINERS_OK and _PSYCOPG_OK


_SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS articles (
    id              BIGSERIAL PRIMARY KEY,
    url             TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL,
    author          TEXT,
    source          TEXT NOT NULL,
    date_published  TIMESTAMPTZ,
    content         TEXT,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chunks (
    id              BIGSERIAL PRIMARY KEY,
    article_id      BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    chunk_text      TEXT NOT NULL,
    embedding       vector(1024),
    tsv             TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (article_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_article_id ON chunks (article_id);
"""


def _sqlalchemy_url_to_psycopg(url: str) -> str:
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql://" + url[len("postgresql+psycopg2://"):]
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url[len("postgresql+psycopg://"):]
    return url


@pytest.mark.skipif(
    not _PG_PRECONDITIONS_OK,
    reason="statement_timeout 真 PG 驗證需 docker + testcontainers + psycopg",
)
def test_statement_timeout_interrupts_and_raises_query_canceled():
    """真 PG：SET statement_timeout 短值 → pg_sleep 超時被中斷拋 QueryCanceled。

    這是「方案可行性」的真機對照（實機已驗過，此為 CI/docker 回歸鎖定）：
    2s statement_timeout 下 pg_sleep(10) 必被中斷，psycopg 拋
    psycopg.errors.QueryCanceled。這是 statement_timeout 防護生效的直接證據。
    """
    assert PostgresContainer is not None
    import psycopg
    from psycopg.rows import dict_row

    container = PostgresContainer("pgvector/pgvector:pg16")
    with container as pg:
        dsn = _sqlalchemy_url_to_psycopg(pg.get_connection_url())
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.execute("SET statement_timeout = '2s'")
            with pytest.raises(psycopg.errors.QueryCanceled):
                conn.execute("SELECT pg_sleep(10)")


@pytest.mark.skipif(
    not _PG_PRECONDITIONS_OK,
    reason="statement_timeout rollback 存活性真 PG 驗證需 docker + testcontainers + psycopg",
)
def test_statement_timeout_survives_rollback_via_connect_options():
    """真 PG（真因鎖）：用 code 的連線建法（options 連線參數）建連後，
    conn.rollback() **不得**把 statement_timeout 回滾成 0。

    這直接鎖住本輪真因：舊做法「連上後 SQL SET statement_timeout」在 psycopg3
    非 autocommit 下綁隱式 transaction，rollback（降級路徑必經）會把它回滾成 0
    → 降級防護失效。改用 options 連線參數（libpq 層，不綁 transaction）後，
    rollback 後 statement_timeout 仍在 → pg_sleep 仍被中斷。

    對照：若有人改回 SQL SET 做法，rollback 後 SHOW 會是 '0'、pg_sleep 不被中斷
    → 本測試紅。
    """
    assert PostgresContainer is not None
    import psycopg
    from psycopg.rows import dict_row

    container = PostgresContainer("pgvector/pgvector:pg16")
    with container as pg:
        dsn = _sqlalchemy_url_to_psycopg(pg.get_connection_url())
        # 用與 code 相同的建連法（options 連線參數）+ 短 timeout 便於測試
        with psycopg.connect(
            dsn, row_factory=dict_row, options="-c statement_timeout=2s"
        ) as conn:
            # 觸發一次 QueryCanceled + rollback（模擬降級路徑）
            with pytest.raises(psycopg.errors.QueryCanceled):
                conn.execute("SELECT pg_sleep(10)")
            conn.rollback()

            # 真因鎖：rollback 後 statement_timeout 仍生效（非 '0'）
            shown = conn.execute("SHOW statement_timeout").fetchone()["statement_timeout"]
            assert shown != "0", (
                f"rollback 後 statement_timeout 不該被回滾成 0（防護失效），得 {shown!r}"
            )
            # 且 rollback 後 pg_sleep 仍被中斷（降級路徑防護仍在）
            with pytest.raises(psycopg.errors.QueryCanceled):
                conn.execute("SELECT pg_sleep(10)")


@pytest.mark.skipif(
    not _PG_PRECONDITIONS_OK,
    reason="QueryCanceled 降級路徑真 PG 驗證需 docker + testcontainers + psycopg",
)
def test_query_canceled_in_flush_degrades_to_error_not_done(tmp_path):
    """真 PG（端到端，走 main_load_dir 真實 code path）：statement_timeout 中斷一批
    → QueryCanceled 走 flush 降級 → 逐篇也超時 → 該檔進 error、不進 .bulk_load_done。

    **關鍵：走 main_load_dir**（吃 code 的 options 連線參數建連），不自己手動建連 +
    SQL SET —— 唯有走真實 code path 才會抓到「rollback 回滾 statement_timeout」真因
    （舊做法此測試紅：降級路徑 pg_sleep 不再被中斷、正常返回 → commit 成功 →
    errors=0 → 誤寫 done）。

    構造：monkeypatch STATEMENT_TIMEOUT 成 '1s'（讓 main_load_dir 用短 timeout 建連）
    + mock _insert_chunk_rows 成超時 pg_sleep（模擬半開連線「送 SQL 後 DB 不回」）。
    驗證：該檔不進 done + grand errors>0（半開連線下該檔止血、下次重跑，不誤當成功）。
    """
    assert PostgresContainer is not None
    import json

    import psycopg

    art = {
        "url": "https://ex.com/qc",
        "title": "qc",
        "author": "t",
        "source": "s",
        "date_published": "2026-01-01",
        "content": "c",
        "metadata": {},
        "chunks": [
            {"chunk_index": 0, "chunk_text": "x", "embedding_offset": 0},
        ],
    }
    (tmp_path / "qc.jsonl").write_text(
        json.dumps(art, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    np.save(tmp_path / "qc.npy", (np.arange(1024, dtype=np.float32) % 7 + 0.5).reshape(1, 1024))

    container = PostgresContainer("pgvector/pgvector:pg16")
    with container as pg:
        dsn = _sqlalchemy_url_to_psycopg(pg.get_connection_url())
        with psycopg.connect(dsn, autocommit=True) as c0:
            with c0.cursor() as cur:
                cur.execute(_SCHEMA_SQL)

        # 讓 chunk INSERT 階段變成「超時的 pg_sleep」→ statement_timeout 中斷拋
        # QueryCanceled。monkeypatch STATEMENT_TIMEOUT 讓 main_load_dir 用 1s 建連。
        def _sleeping_insert(conn, chunk_rows):
            conn.execute("SELECT pg_sleep(10)")

        with mock.patch.object(bulk_load, "STATEMENT_TIMEOUT", "1s"), \
                mock.patch.object(bulk_load, "_insert_chunk_rows", _sleeping_insert):
            grand = bulk_load.main_load_dir(str(tmp_path), dsn)

        # 快路徑 chunk 階段 statement_timeout 中斷 → 降級逐篇 → 降級也超時（因 options
        # 建連的 timeout 不被 rollback 回滾）→ 該篇 errors+1 → 該檔 errors>0 不進 done。
        assert grand["errors"] >= 1, (
            "statement_timeout 中斷（QueryCanceled）在降級路徑應計 error，不可誤當成功。"
            "errors=0 表示降級路徑 statement_timeout 失效（真因未治）。"
        )
        assert grand["articles"] == 0, "被 statement_timeout 打斷的篇不該算成功寫入"

        done_file = tmp_path / ".bulk_load_done"
        written = done_file.read_text(encoding="utf-8").split() if done_file.exists() else []
        assert "qc.jsonl" not in written, (
            "statement_timeout 中斷的檔不該進 .bulk_load_done（半開連線止血、下次重跑）"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
