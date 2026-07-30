"""Bulk Load 批次寫入測試 —— 逐篇 INSERT → 批次寫入改造的正確性防護。

改造動機（實機實測）：GPU embed VM 在新加坡、prod PostgreSQL 在芬蘭，跨洲 RTT
~150-250ms。逐篇 `INSERT ... RETURNING id` + 逐篇 `conn.commit()` 每篇至少 2 次
跨洲 round-trip → ~28 篇/分鐘。批次寫入把 RTT 從「每篇一次」攤到「每批一次」。

本檔測「批次化引入的新行為」，與 test_bulk_load_harden.py（per-file-pair 契約層
的 5 約束）互補 —— 那 7 個 harden 測試是 regression guard，批次化後仍須全綠。

批次化最微妙處：**批次原子性 vs 單篇壞資料隔離**。一批裡有一篇 DB 層失敗不能
整批 rollback 丟掉好篇 → 設計降級：批次 DB 失敗時該批逐篇重試（好篇照樣 land、
壞篇挑出 errors+1）。同時 BulkLoadError（檔案級致命）必須穿透逐篇 fallback，
不可被降級吞掉。

id 對應策略：不依賴多列 `INSERT ... RETURNING id` 的回傳順序（Postgres 不保證
多列 INSERT 的 RETURNING 順序對應 VALUES 順序）。改用 `RETURNING url, id` 建
url→id map（url 是 UNIQUE NOT NULL，可靠鍵），徹底繞過順序不確定性。

PG fixture 策略同 test_bulk_load_harden.py：testcontainers 起 throw-away
pgvector container，套真 schema。不 mock DB —— 資料正確性修正必須對真 PG 驗證。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

import numpy as np
import pytest


# ── Docker / testcontainers 可用性偵測（import 不能爆） ───────────────

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
    _TESTCONTAINERS_IMPORT_ERROR: Optional[str] = None
except ImportError as e:  # pragma: no cover
    PostgresContainer = None  # type: ignore[assignment,misc]
    _TESTCONTAINERS_OK = False
    _TESTCONTAINERS_IMPORT_ERROR = str(e)

try:
    import psycopg  # type: ignore[import-not-found]
    _PSYCOPG_OK = True
except ImportError:  # pragma: no cover
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
    reason=f"bulk_load batch test 需要 docker + testcontainers + psycopg: {_skip_reason()}",
)


from indexing import bulk_load  # noqa: E402


# ── PG schema (鏡像 baseline migration，與 harden 測試一致) ──────────

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


@pytest.fixture(scope="module")
def pg_dsn():
    assert PostgresContainer is not None
    container = PostgresContainer("pgvector/pgvector:pg16")
    with container as pg:
        dsn = _sqlalchemy_url_to_psycopg(pg.get_connection_url())
        with psycopg.connect(dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA_SQL)
        yield dsn


@pytest.fixture
def clean_db(pg_dsn):
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE articles, chunks RESTART IDENTITY CASCADE")
    return pg_dsn


# ── Fixture builder helpers ────────────────────────────────────────

def _write_pair(
    results_dir: Path,
    name: str,
    articles: List[dict],
    embeddings: np.ndarray,
) -> tuple[Path, Path]:
    jsonl_path = results_dir / f"{name}.jsonl"
    npy_path = results_dir / f"{name}.npy"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for art in articles:
            f.write(json.dumps(art, ensure_ascii=False) + "\n")
    np.save(npy_path, embeddings)
    return jsonl_path, npy_path


def _make_article(url: str, chunk_offsets: List[int]) -> dict:
    """建一篇 article，chunks 用給定的 embedding_offset 清單（chunk_index 從 0 連號）。"""
    return {
        "url": url,
        "title": f"title for {url}",
        "author": "tester",
        "source": "test_source",
        "date_published": "2026-01-01",
        "content": "some content",
        "metadata": {},
        "chunks": [
            {
                "chunk_index": i,
                "chunk_text": f"{url} chunk {i}",
                "embedding_offset": off,
            }
            for i, off in enumerate(chunk_offsets)
        ],
    }


def _embeddings(n: int, dim: int = 1024) -> np.ndarray:
    return (np.arange(n * dim, dtype=np.float32).reshape(n, dim) % 7 + 0.5)


def _count_chunks(dsn: str, url: str) -> int:
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM chunks ch "
            "JOIN articles a ON a.id = ch.article_id WHERE a.url = %s",
            (url,),
        ).fetchone()
    return row["c"]


def _chunk_texts(dsn: str, url: str) -> List[str]:
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        rows = conn.execute(
            "SELECT ch.chunk_text FROM chunks ch "
            "JOIN articles a ON a.id = ch.article_id WHERE a.url = %s "
            "ORDER BY ch.chunk_index",
            (url,),
        ).fetchall()
    return [r["chunk_text"] for r in rows]


def _count_articles(dsn: str) -> int:
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM articles").fetchone()
    return row["c"]


# ── 常數：commit 批次大小可調（200-500） ────────────────────────────

def test_commit_batch_size_constant_exists_and_in_range():
    """攤 commit 需要可調的 commit 批次常數（200-500）。"""
    assert hasattr(bulk_load, "COMMIT_BATCH_SIZE"), (
        "批次化須有可調的 COMMIT_BATCH_SIZE 常數（每 N 篇 commit 一次）"
    )
    assert 200 <= bulk_load.COMMIT_BATCH_SIZE <= 500, (
        f"COMMIT_BATCH_SIZE 應在 200-500，得 {bulk_load.COMMIT_BATCH_SIZE}"
    )


# ── 批次 id 對應：跨批次多篇，每篇 chunks 正確歸位 ───────────────────

def test_batch_many_articles_each_chunk_maps_to_correct_article(clean_db, tmp_path, monkeypatch):
    """多篇文章跨越多個 commit 批次，每篇的 chunk_text 必須歸到正確的 article。

    這是 url→id map 正確性的核心測試：若批次 RETURNING id 順序錯配，chunk 會插
    到別篇文章底下。把 COMMIT_BATCH_SIZE 縮成 3，用 7 篇（跨 3 批）驗跨批邊界。
    每篇 chunk 數不同（1..7），確保 embedding_offset 與 article 對應不能靠巧合。
    """
    dsn = clean_db
    monkeypatch.setattr(bulk_load, "COMMIT_BATCH_SIZE", 3)

    n_articles = 7
    articles = []
    offset = 0
    for a in range(n_articles):
        n_chunks = a + 1  # 1,2,3,4,5,6,7
        offs = list(range(offset, offset + n_chunks))
        offset += n_chunks
        articles.append(_make_article(f"https://ex.com/a{a}", offs))

    j, n = _write_pair(tmp_path, "multi", articles, _embeddings(offset))
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        stats = bulk_load.load_file_pair(j, n, conn)

    assert stats["errors"] == 0
    assert stats["articles"] == n_articles
    assert _count_articles(dsn) == n_articles
    for a in range(n_articles):
        url = f"https://ex.com/a{a}"
        expected = [f"{url} chunk {i}" for i in range(a + 1)]
        assert _chunk_texts(dsn, url) == expected, (
            f"article a{a} 的 chunks 歸位錯誤 → url→id map 對應失敗"
        )


# ── 批次 orphan 防護：一批多篇、其中一篇 shrink 仍原子替換 ───────────

def test_batch_reload_shrink_removes_orphans_multi_article(clean_db, tmp_path, monkeypatch):
    """一批含多篇，重載時其中一篇 chunk 數變少 → 該篇 orphan 被刪，鄰篇不受影響。"""
    dsn = clean_db
    monkeypatch.setattr(bulk_load, "COMMIT_BATCH_SIZE", 3)

    a0 = _make_article("https://ex.com/keep", [0, 1])       # 2 chunks，重載不變
    a1 = _make_article("https://ex.com/shrink", [2, 3, 4, 5, 6])  # 5 chunks
    a2 = _make_article("https://ex.com/grow", [7])          # 1 chunk
    j, n = _write_pair(tmp_path, "b1", [a0, a1, a2], _embeddings(8))
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        bulk_load.load_file_pair(j, n, conn)
    assert _count_chunks(dsn, "https://ex.com/shrink") == 5

    # 重載：shrink 降到 2 chunks，keep 不變，grow 升到 3
    a0b = _make_article("https://ex.com/keep", [0, 1])
    a1b = _make_article("https://ex.com/shrink", [2, 3])
    a2b = _make_article("https://ex.com/grow", [4, 5, 6])
    j2, n2 = _write_pair(tmp_path, "b2", [a0b, a1b, a2b], _embeddings(8))
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        bulk_load.load_file_pair(j2, n2, conn)

    assert _count_chunks(dsn, "https://ex.com/keep") == 2
    assert _count_chunks(dsn, "https://ex.com/shrink") == 2, (
        "shrink 篇的舊 index 2/3/4 chunk 必須被刪（orphan 防護在批次下仍成立）"
    )
    assert _count_chunks(dsn, "https://ex.com/grow") == 3


# ── 批次隔離：一批含一篇 DB 層壞資料，好篇仍 land，壞篇計 error ──────

def test_bad_article_in_batch_isolated_good_ones_land(clean_db, tmp_path, monkeypatch):
    """一批裡一篇文章 DB 層 INSERT 失敗（title=None 違反 NOT NULL），
    批次不能整批 rollback 丟好篇 → 降級逐篇重試，好篇 land、壞篇 errors+1。
    """
    dsn = clean_db
    monkeypatch.setattr(bulk_load, "COMMIT_BATCH_SIZE", 10)

    good1 = _make_article("https://ex.com/g1", [0, 1])
    bad = _make_article("https://ex.com/bad", [2])
    bad["title"] = None  # articles.title NOT NULL → INSERT 這篇會炸
    good2 = _make_article("https://ex.com/g2", [3, 4])

    j, n = _write_pair(tmp_path, "mixed", [good1, bad, good2], _embeddings(5))
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        stats = bulk_load.load_file_pair(j, n, conn)

    assert stats["errors"] == 1, "壞篇應恰好計 1 個 error"
    assert stats["articles"] == 2, "兩篇好文章應成功寫入（不被壞篇連坐 rollback）"
    assert _count_chunks(dsn, "https://ex.com/g1") == 2
    assert _count_chunks(dsn, "https://ex.com/g2") == 2
    assert _count_chunks(dsn, "https://ex.com/bad") == 0, "壞篇不該有 chunk 殘留"


# ── 批次中的 out-of-range offset 仍是檔案級失敗（不被逐篇 fallback 吞） ──

def test_offset_out_of_range_in_batch_is_file_level(clean_db, tmp_path, monkeypatch):
    """一批裡有一篇 embedding_offset 超出 npy 範圍 → BulkLoadError 穿透逐篇
    fallback，整檔失敗（不可被降級成 article-level error 靜默掉）。
    """
    dsn = clean_db
    monkeypatch.setattr(bulk_load, "COMMIT_BATCH_SIZE", 10)

    good = _make_article("https://ex.com/ok", [0, 1])
    oor = _make_article("https://ex.com/oor", [2, 99])  # offset 99 超出 npy
    j, n = _write_pair(tmp_path, "oorbatch", [good, oor], _embeddings(3))

    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        with pytest.raises(bulk_load.BulkLoadError):
            bulk_load.load_file_pair(j, n, conn)


# ── 全篇成功（單批內）：articles/chunks 計數正確 ─────────────────────

def test_batch_stats_accurate(clean_db, tmp_path, monkeypatch):
    """單批內多篇全成功：stats articles/chunks 精確（守 errors gate 的計數基礎）。"""
    dsn = clean_db
    monkeypatch.setattr(bulk_load, "COMMIT_BATCH_SIZE", 500)

    arts = []
    offset = 0
    total_chunks = 0
    for a in range(5):
        nc = 2
        arts.append(_make_article(f"https://ex.com/s{a}", list(range(offset, offset + nc))))
        offset += nc
        total_chunks += nc
    j, n = _write_pair(tmp_path, "stats", arts, _embeddings(offset))
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        stats = bulk_load.load_file_pair(j, n, conn)

    assert stats["articles"] == 5
    assert stats["chunks"] == total_chunks
    assert stats["errors"] == 0


# ── P1：批次內重複 url 去重（快路徑不炸、不降級） ─────────────────────

def test_duplicate_url_in_batch_dedup_no_fallback(clean_db, tmp_path, monkeypatch):
    """同一批含重複 url：去重後快路徑不觸發 CardinalityViolation、不降級。

    反偽設計：monkeypatch 降級路徑成哨兵——若快路徑因重複 url 炸而降級，哨兵
    raise → 測試紅。修復（去重）後快路徑成功、哨兵不被呼叫 → 綠。
    驗證：DB 該 url 恰 1 列、chunks 對應**最後一筆**、stats.articles 計數（去重篇算 1）。
    """
    dsn = clean_db
    monkeypatch.setattr(bulk_load, "COMMIT_BATCH_SIZE", 10)

    def _sentinel(*a, **k):
        raise AssertionError("不該降級逐篇——批次內重複 url 應在快路徑去重解決")

    monkeypatch.setattr(bulk_load, "_flush_batch_per_article", _sentinel)

    url = "https://ex.com/dup"
    v1 = _make_article(url, [0])          # 先出現：1 chunk
    v1["title"] = "版本一"
    v2 = _make_article(url, [1, 2])       # 後出現：2 chunks（應勝出）
    v2["title"] = "版本二"
    other = _make_article("https://ex.com/other", [3])
    j, n = _write_pair(tmp_path, "dup", [v1, v2, other], _embeddings(4))

    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        stats = bulk_load.load_file_pair(j, n, conn)

    assert _count_articles(dsn) == 2, "去重後應只有 2 篇 article（dup 算 1 + other）"
    assert _count_chunks(dsn, url) == 2, "應保留最後一筆（v2）的 2 個 chunks"
    assert _chunk_texts(dsn, url) == [f"{url} chunk 0", f"{url} chunk 1"], (
        "chunks 內容應對應最後一筆 v2"
    )
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        title = conn.execute(
            "SELECT title FROM articles WHERE url = %s", (url,)
        ).fetchone()["title"]
    assert title == "版本二", "article 欄位應為最後一筆版本"
    # stats：dup 去重算 1 篇 + other 1 篇 = 2；errors 0（去重非 error）
    assert stats["articles"] == 2, "重複 url 去重後算 1 篇，不重複計"
    assert stats["errors"] == 0


# ── in-house 缺口 2：chunk INSERT 階段毒化 → 降級（非 article 階段） ──

def test_batch_poisoned_at_chunk_stage_degrades_cleanly(clean_db, tmp_path, monkeypatch):
    """批次在 **chunk INSERT 階段**失敗（非 article 階段）觸發降級。

    壞篇的 chunk_text=None 違反 chunks.chunk_text NOT NULL → 快路徑組完 article
    upsert 後在 _insert_chunk_rows 炸 → 整批 rollback（article upsert 也回滾）→
    降級逐篇。驗證：降級後 conn 乾淨、好篇 land、壞篇隔離（含 article 都不殘留）。
    """
    dsn = clean_db
    monkeypatch.setattr(bulk_load, "COMMIT_BATCH_SIZE", 10)

    good1 = _make_article("https://ex.com/cg1", [0, 1])
    bad = _make_article("https://ex.com/cbad", [2])
    bad["chunks"][0]["chunk_text"] = None  # chunks.chunk_text NOT NULL → chunk INSERT 炸
    good2 = _make_article("https://ex.com/cg2", [3])
    j, n = _write_pair(tmp_path, "chunkpoison", [good1, bad, good2], _embeddings(4))

    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        stats = bulk_load.load_file_pair(j, n, conn)

    assert stats["errors"] == 1, "chunk 階段壞篇應恰好計 1 error"
    assert stats["articles"] == 2, "兩篇好文章仍 land"
    assert _count_chunks(dsn, "https://ex.com/cg1") == 2
    assert _count_chunks(dsn, "https://ex.com/cg2") == 1
    # 壞篇連 article 都不該殘留（降級逐篇時該篇整個 rollback）
    assert _count_articles(dsn) == 2, "壞篇的 article 不該殘留（含 chunk 一起 rollback）"


# ── in-house 缺口 3：降級路徑的 BulkLoadError 仍穿透（檔案級） ────────

def test_bulkloaderror_propagates_through_per_article_fallback(clean_db, tmp_path, monkeypatch):
    """降級逐篇路徑中若遇 out-of-range offset（BulkLoadError）仍往外拋、整檔失敗。

    構造：先讓批次快路徑因一篇 chunk_text=None 在 chunk 階段炸而降級；降級逐篇
    重試時另一篇有 out-of-range offset → 在 _flush_batch_per_article 內 raise
    BulkLoadError，必須穿透（不被該路徑的一般 except 降級吞掉）。
    """
    dsn = clean_db
    monkeypatch.setattr(bulk_load, "COMMIT_BATCH_SIZE", 10)

    # 這篇 chunk_text=None → 快路徑 chunk 階段炸 → 觸發降級
    trigger = _make_article("https://ex.com/trig", [0])
    trigger["chunks"][0]["chunk_text"] = None
    # 這篇 offset 99 超範圍 → 降級逐篇時 raise BulkLoadError（檔案級）
    oor = _make_article("https://ex.com/oor2", [99])
    j, n = _write_pair(tmp_path, "fallback_oor", [trigger, oor], _embeddings(1))

    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        with pytest.raises(bulk_load.BulkLoadError):
            bulk_load.load_file_pair(j, n, conn)


# ── AR R1 #1：降級路徑重複 url 也去重（stats 不雙計） ─────────────────

def test_fallback_path_dedup_no_double_count(clean_db, tmp_path, monkeypatch):
    """一批同時有 (a) 重複 url + (b) 另一篇 chunk 階段毒化觸發整批降級：
    降級逐篇也要對重複 url 去重 → stats.articles 對該 url 只算 1（DB 就是 1 列）。

    修復前：降級處理原始未去重 batch，重複 url 各 commit + articles+1 兩次 → 高報。
    """
    dsn = clean_db
    monkeypatch.setattr(bulk_load, "COMMIT_BATCH_SIZE", 10)

    dup_url = "https://ex.com/dupfb"
    v1 = _make_article(dup_url, [0])
    v1["title"] = "版本一"
    v2 = _make_article(dup_url, [1, 2])  # 後出現應勝出
    v2["title"] = "版本二"
    # 毒化篇：chunk_text=None → 快路徑 chunk 階段炸 → 觸發整批降級
    poison = _make_article("https://ex.com/poison", [3])
    poison["chunks"][0]["chunk_text"] = None

    j, n = _write_pair(tmp_path, "dupfb", [v1, v2, poison], _embeddings(4))
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        stats = bulk_load.load_file_pair(j, n, conn)

    # dup_url 去重算 1 篇（成功）；poison 壞篇 errors+1。articles 應是 1（非 2）。
    assert _count_articles(dsn) == 1, "DB 只該有 dup_url 1 列（poison 壞篇不 land）"
    assert stats["articles"] == 1, "降級路徑重複 url 去重後只算 1 篇（不雙計）"
    assert stats["errors"] == 1, "poison 壞篇計 1 error"
    assert _count_chunks(dsn, dup_url) == 2, "應保留最後一筆 v2 的 2 chunks"
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        title = conn.execute(
            "SELECT title FROM articles WHERE url = %s", (dup_url,)
        ).fetchone()["title"]
    assert title == "版本二"


# ── AR R1 #2：負 embedding_offset raise BulkLoadError（檔案級） ──────

def test_negative_offset_raises_bulkloaderror(clean_db, tmp_path):
    """負 offset 會被 numpy 當從尾端索引 → 靜默拿錯 embedding。必須擋成檔案級失敗。"""
    dsn = clean_db
    art = _make_article("https://ex.com/neg", [0, -1])  # offset -1 = 尾端，非法
    j, n = _write_pair(tmp_path, "neg", [art], _embeddings(3))

    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        with pytest.raises(bulk_load.BulkLoadError) as exc:
            bulk_load.load_file_pair(j, n, conn)
    assert "negative" in str(exc.value).lower(), "錯誤訊息應標明是負值"

    # 檔案級失敗：不進 done
    bulk_load.main_load_dir(str(tmp_path), dsn)
    done_file = tmp_path / ".bulk_load_done"
    written = done_file.read_text(encoding="utf-8").split() if done_file.exists() else []
    assert "neg.jsonl" not in written


# ── AR R1 #3：zero-chunk article 隔離（不誤傷合法 shrink） ────────────

def test_zero_chunk_article_isolated(clean_db, tmp_path, monkeypatch):
    """chunks 完全為空的 article：隔離（errors+1、不進 batch），不 DELETE 既有 chunks。"""
    dsn = clean_db
    monkeypatch.setattr(bulk_load, "COMMIT_BATCH_SIZE", 10)

    good = _make_article("https://ex.com/zc-good", [0, 1])
    empty = _make_article("https://ex.com/zc-empty", [])  # chunks == []
    j, n = _write_pair(tmp_path, "zc", [good, empty], _embeddings(2))

    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        stats = bulk_load.load_file_pair(j, n, conn)

    assert stats["errors"] == 1, "zero-chunk article 應計 1 error"
    assert stats["articles"] == 1, "只有 good 那篇 land"
    assert _count_articles(dsn) == 1
    assert _count_chunks(dsn, "https://ex.com/zc-good") == 2
    # errors>0 → 不寫 done
    bulk_load.main_load_dir(str(tmp_path), dsn)  # 註：good 已 land，此次重跑 empty 仍 error
    done_file = tmp_path / ".bulk_load_done"
    written = done_file.read_text(encoding="utf-8").split() if done_file.exists() else []
    assert "zc.jsonl" not in written, "含 zero-chunk 壞篇的檔不進 done"


def test_zero_chunk_guard_does_not_hit_legit_shrink(clean_db, tmp_path):
    """合法 shrink（舊 5 chunks → 新 3 chunks，非空）不被 zero-chunk guard 誤傷。"""
    dsn = clean_db
    url = "https://ex.com/shrink-ok"

    art5 = _make_article(url, [0, 1, 2, 3, 4])
    j5, n5 = _write_pair(tmp_path, "s5", [art5], _embeddings(5))
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        bulk_load.load_file_pair(j5, n5, conn)
    assert _count_chunks(dsn, url) == 5

    art3 = _make_article(url, [0, 1, 2])  # 變少但非空 → 合法 shrink
    j3, n3 = _write_pair(tmp_path, "s3", [art3], _embeddings(3))
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        stats = bulk_load.load_file_pair(j3, n3, conn)

    assert stats["errors"] == 0, "shrink（非空 chunks）不該被 zero-chunk guard 隔離"
    assert stats["articles"] == 1
    assert _count_chunks(dsn, url) == 3, "orphan 防護正常，舊 chunk 3/4 被刪"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
