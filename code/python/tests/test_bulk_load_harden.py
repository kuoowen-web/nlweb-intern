"""Bulk Load harden 測試 — 5 項資料完整性修正（4 輪 adversarial review 收斂）。

對應 plan `indexing-incremental-backfill-plan.md` 的「AR R3 blocker + bulk_load.py
harden」段。增量補 indexing 架構會反覆重載同一檔（續跑/重試），所以「重載的正確性」
是資料完整性關鍵。

5 項修正：
1. 🔴 orphan chunks（資料完整性 blocker）：文章重載若新 chunk 數 < 舊 chunk 數，
   舊的高 index chunk 殘留 → 搜尋返回過期內容。修法：同 transaction 內先
   DELETE FROM chunks WHERE article_id=X 再 INSERT（原子替換）。
2. `.bulk_load_done` errors gate：errors>0 不該寫進 done（下次不重跑會漏資料）。
3. out-of-range offset 當檔案級失敗：offset >= len(embeddings) 現在靜默 continue
   → 少 chunk 無錯。修法：raise（檔案級失敗，不進 done）。
4. 1-D npy 防呆：embeddings.shape[1] 對 1-D array crash（IndexError）→ 先驗 ndim==2。
5. URL ON CONFLICT 冪等：重載同 URL 更新不重複（確認既有行為正確）。

PG fixture 策略：與 test_alembic_schema_equivalence.py 相同，用 testcontainers 起
throw-away `pgvector/pgvector:pg16` container，套 baseline migration 建 articles +
chunks 表（真 schema，含 FK / index / vector(1024)）。不 mock DB —— 資料正確性
修正必須對真 PG 驗證。
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
    reason=f"bulk_load harden test 需要 docker + testcontainers + psycopg: {_skip_reason()}",
)


# bulk_load 是被測目標（import 不受 skipif 影響，供 collection）
from indexing import bulk_load  # noqa: E402


# ── PG schema (articles + chunks，鏡像 baseline migration b5e9d3f71a42) ──

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
    """Module-scoped pgvector container；建 articles + chunks 表後 yield DSN。"""
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
    """每個 test 前清空 articles + chunks（TRUNCATE CASCADE 連 chunks 一起清）。"""
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
    """寫一對 {name}.jsonl + {name}.npy。articles 每筆是 jsonl 一行的 dict。"""
    jsonl_path = results_dir / f"{name}.jsonl"
    npy_path = results_dir / f"{name}.npy"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for art in articles:
            f.write(json.dumps(art, ensure_ascii=False) + "\n")
    np.save(npy_path, embeddings)
    return jsonl_path, npy_path


def _make_article(url: str, n_chunks: int, offset_base: int = 0) -> dict:
    """建一篇 n_chunks 個 chunk 的 article dict（embedding_offset 連號）。"""
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
                "chunk_text": f"chunk {i} text",
                "embedding_offset": offset_base + i,
            }
            for i in range(n_chunks)
        ],
    }


def _embeddings(n: int, dim: int = 1024) -> np.ndarray:
    """n x dim float32 embeddings（非零，避免 pgvector 邊界問題）。"""
    return (np.arange(n * dim, dtype=np.float32).reshape(n, dim) % 7 + 0.5)


def _count_chunks(dsn: str, url: str) -> int:
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM chunks ch "
            "JOIN articles a ON a.id = ch.article_id WHERE a.url = %s",
            (url,),
        ).fetchone()
    return row["c"]


def _count_articles(dsn: str, url: str) -> int:
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM articles WHERE url = %s", (url,)
        ).fetchone()
    return row["c"]


# ── 修正 1：orphan chunks（原子替換） ──────────────────────────────

def test_reload_with_fewer_chunks_removes_orphans(clean_db, tmp_path):
    """載入 5 chunks 的文章 → 重載成 3 chunks 版本 → DB 只剩 3 chunks（非 5）。"""
    dsn = clean_db
    url = "https://example.com/reload-shrink"

    # 第一次：5 chunks
    art5 = _make_article(url, n_chunks=5)
    j5, n5 = _write_pair(tmp_path, "batch5", [art5], _embeddings(5))
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        bulk_load.load_file_pair(j5, n5, conn)
    assert _count_chunks(dsn, url) == 5, "首次載入應有 5 chunks"

    # 第二次：同 URL 但只有 3 chunks（重載 shrink）
    art3 = _make_article(url, n_chunks=3)
    j3, n3 = _write_pair(tmp_path, "batch3", [art3], _embeddings(3))
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        bulk_load.load_file_pair(j3, n3, conn)

    assert _count_chunks(dsn, url) == 3, (
        "重載成 3 chunks 後應只剩 3，舊的 index 3/4 chunk 必須被刪除（orphan）"
    )


def test_first_load_delete_empty_is_noop(clean_db, tmp_path):
    """首次載入（無舊 chunks）：DELETE 空集合要正常，不報錯。"""
    dsn = clean_db
    url = "https://example.com/first-load"
    art = _make_article(url, n_chunks=4)
    j, n = _write_pair(tmp_path, "first", [art], _embeddings(4))
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        stats = bulk_load.load_file_pair(j, n, conn)
    assert stats["errors"] == 0
    assert _count_chunks(dsn, url) == 4


# ── 修正 2：.bulk_load_done errors gate ──────────────────────────

def test_done_file_not_written_when_errors(clean_db, tmp_path):
    """一對檔含一篇會失敗的文章（errors>0）→ 該檔名不進 .bulk_load_done。"""
    dsn = clean_db
    good = _make_article("https://example.com/good", n_chunks=2)
    # 壞文章：缺 url key → load_file_pair 內 data["url"] KeyError → article-level error
    bad = {"title": "no url", "chunks": []}
    # good 用 offset 0-1，需要 >=2 embeddings
    j, n = _write_pair(tmp_path, "mixed", [good, bad], _embeddings(2))

    bulk_load.main_load_dir(str(tmp_path), dsn)  # driver（見實作）

    done_file = tmp_path / ".bulk_load_done"
    written = done_file.read_text(encoding="utf-8").split() if done_file.exists() else []
    assert "mixed.jsonl" not in written, (
        "errors>0 的檔案不該寫進 .bulk_load_done（否則下次不重跑會漏資料）"
    )


def test_done_file_written_when_no_errors(clean_db, tmp_path):
    """全部成功（errors==0）→ 該檔名寫進 .bulk_load_done。"""
    dsn = clean_db
    good = _make_article("https://example.com/all-good", n_chunks=2)
    j, n = _write_pair(tmp_path, "clean", [good], _embeddings(2))

    bulk_load.main_load_dir(str(tmp_path), dsn)

    done_file = tmp_path / ".bulk_load_done"
    written = done_file.read_text(encoding="utf-8").split() if done_file.exists() else []
    assert "clean.jsonl" in written


# ── 修正 3：out-of-range offset 當檔案級失敗 ──────────────────────

def test_offset_out_of_range_fails_file(clean_db, tmp_path):
    """jsonl 的 embedding_offset 指到 npy 範圍外 → 該檔失敗、不進 done。"""
    dsn = clean_db
    # article 宣稱 chunk offset 0,1,2 但 npy 只有 2 列 → offset 2 out of range
    art = _make_article("https://example.com/oor", n_chunks=3)
    j, n = _write_pair(tmp_path, "oor", [art], _embeddings(2))  # 只 2 embeddings

    # 檔案級失敗：load_file_pair 應 raise（或 driver 記為 fatal，檔名不進 done）
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        with pytest.raises(Exception):
            bulk_load.load_file_pair(j, n, conn)

    # 透過 driver 驗不進 done
    bulk_load.main_load_dir(str(tmp_path), dsn)
    done_file = tmp_path / ".bulk_load_done"
    written = done_file.read_text(encoding="utf-8").split() if done_file.exists() else []
    assert "oor.jsonl" not in written, "offset 超範圍的檔不該進 done（會少 chunk 無錯）"


# ── 修正 4：1-D npy 防呆 ──────────────────────────────────────────

def test_1d_npy_clean_error(clean_db, tmp_path):
    """餵 1-D npy → 乾淨的檔案級錯誤（非 IndexError crash）。"""
    dsn = clean_db
    art = _make_article("https://example.com/1d", n_chunks=1)
    jsonl_path = tmp_path / "oned.jsonl"
    npy_path = tmp_path / "oned.npy"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(art, ensure_ascii=False) + "\n")
    np.save(npy_path, np.arange(1024, dtype=np.float32))  # 1-D array

    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        with pytest.raises(ValueError) as exc:
            bulk_load.load_file_pair(jsonl_path, npy_path, conn)
    # 乾淨錯誤：訊息提到維度 / 2D / ndim，不是裸 IndexError
    msg = str(exc.value).lower()
    assert ("2" in msg or "dim" in msg or "shape" in msg), (
        f"1-D npy 應給乾淨的維度錯誤訊息，得到：{exc.value!r}"
    )


# ── 修正 5：URL ON CONFLICT 冪等（確認既有行為正確） ──────────────

def test_url_reload_idempotent_no_duplicate(clean_db, tmp_path):
    """重載同 URL：articles 不重複（ON CONFLICT (url) DO UPDATE），欄位更新。"""
    dsn = clean_db
    url = "https://example.com/idempotent"

    art_v1 = _make_article(url, n_chunks=2)
    art_v1["title"] = "版本一標題"
    j1, n1 = _write_pair(tmp_path, "v1", [art_v1], _embeddings(2))
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        bulk_load.load_file_pair(j1, n1, conn)

    art_v2 = _make_article(url, n_chunks=2)
    art_v2["title"] = "版本二標題"
    j2, n2 = _write_pair(tmp_path, "v2", [art_v2], _embeddings(2))
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        bulk_load.load_file_pair(j2, n2, conn)

    assert _count_articles(dsn, url) == 1, "同 URL 重載不該產生重複 article row"
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        row = conn.execute(
            "SELECT title FROM articles WHERE url = %s", (url,)
        ).fetchone()
    assert row["title"] == "版本二標題", "重載應更新欄位到最新版本"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
