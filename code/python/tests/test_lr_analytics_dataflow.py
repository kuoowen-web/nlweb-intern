"""LR analytics data-flow 驗證（票 2026-07-28-k Task 6）：真 QueryLogger + 真 SQLite。

不 mock DB —— 斷言「資料真的落表」（lessons-live-research：control flow vs data flow
兩層，LR 有『物件搬過來但輸出丟掉』前科）。模擬完整打點序列：
  route: log_query_start(mode='live_research')     [同步 → queries row]
  handler: update_query_conversation_id            [同步 UPDATE]
  client: log_tier_6_enrichment(query_id=...)      [queue → tier_6_enrichment row]
  handler: log_query_complete                      [同步 UPDATE]

Run: cd code/python && uv run pytest tests/test_lr_analytics_dataflow.py -v
"""
import sqlite3

import pytest


@pytest.fixture()
def real_sqlite_logger(tmp_path, monkeypatch):
    """真 QueryLogger + tmp SQLite。清 PG env → AnalyticsDB 走 sqlite；
    替換 singleton 避免污染其他測試 / 真 DB。

    設空字串（非 delenv）：主 repo .env 帶 PG 連線字串，而 core.config 的 import-time
    load_dotenv(override=False) 會在 fixture import AnalyticsDB 時把 PG url 重新注入
    （override=False 只在 var「不存在」時注入，delenv 正好讓它不存在）。設空字串 →
    var「已存在（空）」→ load_dotenv 不覆寫 → AnalyticsDB 的 or 鏈遇空字串 falsy → sqlite。"""
    for var in ("POSTGRES_CONNECTION_STRING", "DATABASE_URL", "ANALYTICS_DATABASE_URL"):
        monkeypatch.setenv(var, "")

    from core.analytics_db import AnalyticsDB

    db_path = str(tmp_path / "query_logs.db")
    test_db = AnalyticsDB(db_path=db_path)
    monkeypatch.setattr(AnalyticsDB, "_instance", test_db)

    from core.query_logger import QueryLogger

    ql = QueryLogger()  # 直建 instance（不經 get_query_logger singleton）
    yield ql, db_path
    ql.shutdown()


def _fetchone(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def test_lr_query_start_row_lands_in_queries_table(real_sqlite_logger):
    ql, db_path = real_sqlite_logger
    ql.log_query_start(
        query_id="query_df_1",
        user_id="user-uuid-1",
        query_text="台灣綠能發展衝突",
        site="all",
        mode="live_research",
        session_id="sess_frontend",
        org_id="org-uuid-1",
    )
    row = _fetchone(db_path, "SELECT * FROM queries WHERE query_id = ?", ("query_df_1",))
    assert row is not None, "log_query_start 同步寫必須立即可查（FK parent 保證）"
    assert row["mode"] == "live_research"
    assert row["user_id"] == "user-uuid-1"
    assert row["org_id"] == "org-uuid-1"
    assert row["session_id"] == "sess_frontend"
    assert row["conversation_id"] in (None, "")  # initial 打點時未回填


def test_conversation_id_backfill_updates_row(real_sqlite_logger):
    ql, db_path = real_sqlite_logger
    ql.log_query_start(
        query_id="query_df_2", user_id="u", query_text="Q", site="all",
        mode="live_research",
    )
    ql.update_query_conversation_id("query_df_2", "lr-uuid-B")
    row = _fetchone(
        db_path, "SELECT conversation_id FROM queries WHERE query_id = ?", ("query_df_2",)
    )
    assert row["conversation_id"] == "lr-uuid-B"


def test_tier6_row_lands_and_joins_parent(real_sqlite_logger):
    """queue 路徑：log_tier_6_enrichment → worker 消化 → tier_6_enrichment row
    落表且 query_id 可 JOIN 回 queries（LR CSE 用量歸因的核心查詢形狀）。"""
    ql, db_path = real_sqlite_logger
    ql.log_query_start(
        query_id="query_df_3", user_id="u", query_text="Q", site="all",
        mode="live_research",
    )
    ql.log_tier_6_enrichment(
        query_id="query_df_3",
        source_type="google_search",
        cache_hit=False,
        latency_ms=320,
        timeout_occurred=False,
        result_count=0,
        metadata={"query": "德國風電", "error_type": "http_429"},
    )
    ql.log_queue.join()  # 等 worker thread 消化 queue

    row = _fetchone(
        db_path,
        "SELECT t.source_type, t.result_count, t.metadata, q.mode "
        "FROM tier_6_enrichment t JOIN queries q ON q.query_id = t.query_id "
        "WHERE t.query_id = ?",
        ("query_df_3",),
    )
    assert row is not None, "tier6 row 必須落表且 JOIN 得回 LR 的 queries row"
    assert row["source_type"] == "google_search"
    assert row["mode"] == "live_research"
    assert "http_429" in (row["metadata"] or "")


def test_query_complete_updates_terminal_fields(real_sqlite_logger):
    ql, db_path = real_sqlite_logger
    ql.log_query_start(
        query_id="query_df_4", user_id="u", query_text="Q", site="all",
        mode="live_research",
    )
    ql.log_query_complete(
        query_id="query_df_4",
        latency_total_ms=1234.5,
        num_results_retrieved=7,
        error_occurred=True,
        error_message="state_not_found",
    )
    row = _fetchone(db_path, "SELECT * FROM queries WHERE query_id = ?", ("query_df_4",))
    assert row["latency_total_ms"] == pytest.approx(1234.5)
    assert row["num_results_retrieved"] == 7
    assert row["error_occurred"] == 1
    assert row["error_message"] == "state_not_found"
