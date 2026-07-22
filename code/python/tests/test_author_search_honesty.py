# -*- coding: utf-8 -*-
"""Author 誠實鏈測試（P1-5 虛構記者幻覺修復，2026-07-08 plan）。

覆蓋：QU regex 抽取精度（Task 1）、_build_filters author 支援（Task 2）、
author-only 兜底鏈 flag 回填（Task 3）、SummarizeResults 空素材 guard（Task 4）。
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.query_analysis.query_understanding import QueryUnderstanding


def _regex_author(query):
    """Bypass __init__（需要 handler）；_regex_author 只用 class attrs。"""
    qu = object.__new__(QueryUnderstanding)
    return qu._regex_author(query)


# (query, 期望 author_name；None = 不得抽出)
# 114 案例 = 基本 15 + 時間修飾中綴 5 + 記者會家族 10 + 堆疊前綴/一下 6 + bare verb 2
#          + bare 職稱/修飾詞族 11 + strip 誤傷防禦 4 + 請問/知道/了解族 5（R3 Codex）
#          + TIME_INFIX 邊界 3（R3 Codex SF/in-house S2）+ possessive scavenger 族 5（R3 in-house B1）
#          + 在/於介詞 4（R3 in-house B2）+ 雙字動詞/疊詞 3（R3 in-house S1）
#          + mid-string 黏連家族 15 + strip 補集誤傷防禦 14（R4 in-house B1）
#          + 多字職稱破洞 8 + 職稱複合詞前綴 4（R5 in-house B1/S1）
# （全表已於 plan 階段以 scratchpad script 對 v9 設計實測 114/114 + ReDoS 1000 位 0.01ms）
# 另 +11 = §7 R6-S1 beat 詞/媒體名 stopwords 採納案例（executor 落地，AR R6 in-house S1）
QU_REGEX_CASES = [
    # --- 基本 15 ---
    ('幫我找記者王不存在寫的文章', '王不存在'),   # P1-5 目標 query（現行誤抽「幫我找」）
    ('幫我找記者林彥良寫的文章', '林彥良'),       # 真實記者同句式（現行誤抽「幫我找」）
    ('記者王不存在寫的文章', '王不存在'),
    ('王不存在的文章', None),                     # 行為變化：無職稱詞 → 交 LLM
    ('記者王小明', None),                         # regex 層不抽（LLM 接手）
    ('王小明記者的報導', '王小明'),
    ('作者：張三豐', '張三豐'),
    ('幫我查作者李四的報導', '李四'),
    ('AI趨勢分析', None),
    ('最新的文章', None),                         # stopword 擋
    ('幫我找最近一週的文章', None),               # 現行 prod 誤抽「最近一週」
    ('編輯陳大文的評論', '陳大文'),
    ('請幫我找王家瑜的報導', None),               # 行為變化：無職稱詞 → 交 LLM
    ('記者王家瑜的報導', '王家瑜'),
    ('幫我找記者王家瑜的報導', '王家瑜'),
    # --- 時間修飾中綴（R2 Codex blocker：帶職稱詞必須抽出名字進 author 鏈）---
    ('記者王家瑜最近寫的文章', '王家瑜'),
    ('幫我找記者林資傑上週的報導', '林資傑'),
    ('幫我找記者王家瑜2020年1月寫的文章', '王家瑜'),   # = E2E Q9
    ('記者王不存在2026年1月寫的文章', '王不存'),   # D3 防線 3 已知 trade-off：名尾「在」+緊接
                                                    # 時間 token 歧義選介詞側；ILIKE %王不存% 超集等價
    ('記者王家瑜關於高端的報導', None),             # 開放主題中綴保守 None（§7 列名）
    # --- in-house B1 記者會家族（台灣新聞高頻主題查詢，strict 下誤抽 = 假空結果紅線）---
    ('總統記者會的新聞', None),
    ('記者會的報導', None),
    ('記者會後的報導', None),
    ('昨天記者會的新聞', None),
    ('關於記者會的報導', None),
    ('幫我找王大明記者寫的文章', '王大明'),       # 前綴 strip 後 name_before_title 正確抽出
    ('編輯部的文章', None),
    ('記者節的新聞', None),
    ('專欄作家寫的文章', None),
    ('記者今天寫的文章', None),
    # --- 堆疊前綴／「一下」填充詞（R2 in-house B1'）---
    ('麻煩幫我找王大明記者寫的文章', '王大明'),
    ('幫我找一下王大明記者的報導', '王大明'),
    ('請幫我找一下王大明記者的報導', '王大明'),
    ('請麻煩幫我找王大明記者寫的文章', '王大明'),
    ('查一下王大明記者的報導', '王大明'),
    ('我想知道記者會的新聞', None),
    # --- bare verb 黏連（R2 Codex SF）---
    ('找王大明記者寫的文章', '王大明'),
    ('查王大明記者的報導', '王大明'),
    # --- bare 職稱族／修飾詞族（R2 in-house S1'：TITLE_WORD-in-name reject 一刀流）---
    ('記者寫的文章', None),
    ('記者的報導', None),
    ('編輯的文章', None),
    ('作者的觀點文章', None),
    ('攝影記者寫的文章', None),
    ('資深記者的報導', None),
    ('實習記者寫的文章', None),
    ('國際記者節的報導', None),
    ('駐美記者的報導', None),
    ('資深記者王大明的報導', '王大明'),           # 具名時 sandwich 救回
    ('攝影記者林彥良寫的文章', '林彥良'),
    # --- 前綴 strip 誤傷防禦 ---
    ('查理布朗的文章', None),                     # 「查」不被 strip；無職稱 → LLM
    ('看見台灣的報導', None),                     # 「看」不被 strip；無職稱 → 交 LLM
    ('我想看王家瑜的報導', None),                 # strip 後無職稱 → 交 LLM
    ('想見你的報導', None),                       # 劇名：「想」剝後無職稱 → 交 LLM（無害）
    # --- 請問/我想知道/想知道/了解 請求族（R3 Codex blocker）---
    ('請問王大明記者的報導', '王大明'),
    ('我想知道王大明記者的報導', '王大明'),
    ('想知道王大明記者寫的文章', '王大明'),
    ('了解王大明記者的報導', '王大明'),
    ('請問記者會的新聞', None),
    # --- TIME_INFIX 邊界（R3 Codex SF + in-house S2）---
    ('記者王家瑜今年寫的文章', '王家瑜'),
    ('記者王家瑜去年寫的文章', '王家瑜'),
    ('記者王大明2的文章', None),                  # 裸數字非時間（必帶單位），交一般檢索
    # --- possessive scavenger 族（R3 in-house B1：pattern 移除後全 None）---
    ('記者王家瑜和林資傑的報導', None),           # 雙作者句式：不誤抽垃圾名（交 LLM/一般檢索）
    ('記者王家瑜昨天在立法院寫的文章', None),     # 「在立法院」非時間 infix → 不硬抽
    ('跑立法院的新聞', None),
    ('2020年專題的報導', None),
    ('寫高端疫苗的文章', None),
    # --- 在/於 介詞前導（R3 in-house B2）---
    ('記者王家瑜在2020年寫的文章', '王家瑜'),
    ('記者林資傑在上週寫的報導', '林資傑'),
    ('記者王家瑜於2020年寫的文章', '王家瑜'),
    ('記者李在明的報導', '李在明'),               # 名含「在」不受 infix 前導影響
    # --- 雙字動詞/疊詞（R3 in-house S1）---
    ('搜尋王大明記者的文章', '王大明'),
    ('尋找王大明記者的報導', '王大明'),
    ('查一查王大明記者的報導', '王大明'),
    # --- R4-B1 mid-string 黏連家族（lookbehind 治本：救回名字或安全 None，不得誤抽）---
    ('我要找王大明記者的報導', '王大明'),        # strip 我要+找
    ('我要看王大明記者的報導', '王大明'),
    ('我在找王大明記者的報導', None),            # 未列舉前導：lookbehind 擋左偏 → 一般檢索
    ('有沒有王大明記者的報導', '王大明'),
    ('有王大明記者的報導嗎', '王大明'),
    ('能不能幫我找王大明記者的報導', None),      # 未列舉前導：lookbehind 擋 → 一般檢索
    ('可以幫我找王大明記者的報導', None),
    ('給我王大明記者的報導', None),              # v7 曾誤抽'我王大明'
    ('列出王大明記者的報導', None),              # v7 曾誤抽'出王大明'
    ('顯示王大明記者的報導', None),
    ('幫查一下王大明記者的報導', None),
    ('請教王大明記者的報導', '王大明'),          # strip 請+教
    ('中國時報王家瑜記者的報導', None),          # 媒體名前導：v7 曾誤抽'報王家瑜'
    ('2026年王大明記者的報導', None),            # 年份前導：v7 曾誤抽'年王大明'（數字必須在 lookbehind 類內）
    ('問問看王大明記者的報導', None),
    # --- R4-B1 修法誤傷防禦（strip 補集不得誤傷正常 query）---
    ('要聞總覽', None),
    ('要不要看王大明記者的報導', None),
    ('有機農業的報導', None),
    ('有線電視新聞', None),
    ('請教育部公布的資料', None),
    ('我要求政府公開的文件', None),
    ('有話好說的報導', None),
    ('教育改革的新聞', None),
    ('教師節的報導', None),
    ('教宗方濟各的新聞', None),
    ('想要看王大明記者的報導', '王大明'),        # fixpoint 兩輪剝淨
    ('問鼎中原的報導', None),
    ('知足常樂的文章', None),
    ('尋人啟事的新聞', None),
    # --- R5-B1 多字職稱破洞（lazy capture + BARE_VERB 職稱補集救回）---
    ('王大明總編輯的文章', '王大明'),            # v8 greedy 曾抽'王大明總'
    ('王丰總編輯的文章', '王丰'),                # 2 字名
    ('陳文茜副總編輯的評論', '陳文茜'),
    ('我要找王大明總編輯的文章', '王大明'),
    ('找王大明主筆的評論', '王大明'),            # v8 曾抽'找王大明'（bare-verb lookahead 漏主筆）
    ('查王健壯主筆的文章', '王健壯'),
    ('歐陽大明總編輯的文章', '歐陽大明'),        # 4 字名回歸
    ('王總編輯的文章', None),                    # 單字姓切面：尾字總/副 reject（R5-N2）
    # --- R5-S1 職稱複合詞前綴 ---
    ('編輯部王大明的文章', None),                # v8 sandwich 曾抽'部王大明'
    ('編輯台王大明的報導', None),
    ('作者群王大明的文章', None),
    ('編輯陳大文的評論二', '陳大文'),            # lookahead 不誤傷正常「編輯+名」
    # --- §7 R6-S1 beat 詞/媒體名 stopwords 批次（executor 落地採納，AR R6 in-house S1）---
    ('體育記者的報導', None),                    # R6 實測：修飾詞首發集外的 beat 詞曾誤抽'體育'
    ('財經記者寫的文章', None),
    ('司法記者的報導', None),
    ('醫藥記者寫的文章', None),
    ('兩岸記者的報導', None),
    ('影劇記者的報導', None),
    ('三立記者的報導', None),                    # 媒體名
    ('華視記者的報導', None),
    ('電視台記者的報導', None),
    ('本報記者的報導', None),
    ('體育記者王大明的報導', '王大明'),          # 具名時 sandwich 救回（與修飾詞族同款）
]


@pytest.mark.parametrize("query,expected", QU_REGEX_CASES)
def test_qu_regex_author_extraction(query, expected):
    r = _regex_author(query)
    got = r['author_name'] if r else None
    assert got == expected, f'{query!r}: got {got!r}, expected {expected!r}'


# --- Task 2: _build_filters author 支援 + fail-loud ---

from retrieval_providers.postgres_client import PgVectorClient


def _client():
    return object.__new__(PgVectorClient)  # _build_filters 無 state 依賴


def test_build_filters_author_contains():
    clauses, params = _client()._build_filters(
        [], None,
        kwargs_filters=[{"field": "author", "operator": "contains", "value": "王家瑜"}])
    assert clauses == ["a.author ILIKE %s"]
    assert params == ["%王家瑜%"]


def test_build_filters_date_unchanged():
    clauses, params = _client()._build_filters(
        [], None,
        kwargs_filters=[{"field": "datePublished", "operator": "gte", "value": "2026-01-01"},
                        {"field": "datePublished", "operator": "lte", "value": "2026-02-01"}])
    assert clauses == ["a.date_published >= %s", "a.date_published <= %s"]
    assert params == ["2026-01-01", "2026-02-01"]


def test_build_filters_unknown_dropped_with_warning(monkeypatch):
    # 注意（AR R1 in-house S2 實測）：postgres_client 的 logger 是 LazyLogger
    # （queue/延遲機制），caplog 捕不到 .warning() —— 必須 monkeypatch logger stub 驗。
    import retrieval_providers.postgres_client as pc
    warnings = []

    class _RecordingLogger:
        def warning(self, msg, *a, **k):
            warnings.append(msg)

        def __getattr__(self, name):          # 其他 level 一律 no-op
            return lambda *a, **k: None

    monkeypatch.setattr(pc, "logger", _RecordingLogger())
    clauses, params = _client()._build_filters(
        [], None,
        kwargs_filters=[{"field": "nonsense", "operator": "eq", "value": "x"},
                        {"field": "author", "operator": "gte", "value": "x"}])  # 不合法組合
    assert clauses == [] and params == []     # drop 行為以回傳值為準
    assert any("NOT be filtered" in w for w in warnings)  # fail-loud：明說後果


# --- Task 3: author-only 兜底鏈 + author_search_no_results flag 回填 ---

def _mk_handler():
    return SimpleNamespace(
        time_filter_relaxed=False,
        author_search_no_results=False,
        low_relevance_warning=False,
        low_keyword_match_warning=False,
    )  # 無 query_id -> analytics 段自動 skip


def _mk_pg(monkeypatch, execute_results):
    """PgVectorClient with mocked embedding + _execute_with_retry.

    execute_results: 依呼叫順序回傳的結果 list（第 1 次 = 主混合查詢，之後 = 兜底）。
    """
    import retrieval_providers.postgres_client as pc

    client = object.__new__(pc.PgVectorClient)

    async def fake_embedding(q, query_params=None):
        return [0.0] * 1024
    monkeypatch.setattr(pc, "get_embedding", fake_embedding)

    calls = {"n": 0}

    async def fake_execute(fn):
        i = min(calls["n"], len(execute_results) - 1)
        calls["n"] += 1
        return execute_results[i]
    client._execute_with_retry = fake_execute
    return client, calls


AUTHOR_FILTER = [{"field": "author", "operator": "contains", "value": "王不存在"}]


def _fake_author_row(name="王家瑜"):
    return {
        "url": "https://example.com/a1", "schema_str": "{}", "title": "t",
        "source": "chinatimes", "author": name, "date_published": "2026-01-01",
        "vector_score": 0.0, "text_score": 0.0, "keyword_hit": True,
    }


def test_author_absent_sets_flag_and_returns_empty(monkeypatch):
    client, calls = _mk_pg(monkeypatch, execute_results=[[]])  # 主查詢空、兜底也空
    h = _mk_handler()
    results = asyncio.run(client.search(
        "幫我找記者王不存在寫的文章", "all",
        handler=h, filters=list(AUTHOR_FILTER)))
    assert h.author_search_no_results is True
    assert results == []
    assert calls["n"] >= 2  # 主查詢 + 至少一次 author-only 兜底


def test_author_exists_fallback_returns_rows_no_flag(monkeypatch):
    client, calls = _mk_pg(monkeypatch, execute_results=[[], [_fake_author_row()]])
    h = _mk_handler()
    results = asyncio.run(client.search(
        "幫我找記者王家瑜寫的文章", "all",
        handler=h,
        filters=[{"field": "author", "operator": "contains", "value": "王家瑜"}]))
    assert h.author_search_no_results is False
    assert len(results) == 1
    # D5：兜底命中不得誤觸發 Signal A/B（分數全 0 不是低關聯證據）
    assert h.low_relevance_warning is False
    assert h.low_keyword_match_warning is False


def test_no_author_filter_behaviour_unchanged(monkeypatch):
    client, calls = _mk_pg(monkeypatch, execute_results=[[]])
    h = _mk_handler()
    results = asyncio.run(client.search("提拉米蘇食譜", "all", handler=h, filters=[]))
    assert h.author_search_no_results is False
    assert results == []
    assert calls["n"] == 1  # 無 author filter：不跑兜底（回歸不變量）


AUTHOR_DATE_FILTERS = [
    {"field": "author", "operator": "contains", "value": "王不存在"},
    {"field": "datePublished", "operator": "gte", "value": "2026-01-01"},
]


def test_author_absent_with_date_no_stale_time_flag(monkeypatch):
    """AR R1 Codex blocker：author+date 全空時，time_filter_relaxed 不得殘留
    （author 場景 date-relax 段被 gate，flag 只能由兜底 step 3 命中時 set）——
    否則 author 文案與「已擴大日期範圍」雙 banner 同發，打破互斥。"""
    client, calls = _mk_pg(monkeypatch, execute_results=[[]])  # 主查詢空、兜底兩步皆空
    h = _mk_handler()
    results = asyncio.run(client.search(
        "幫我找記者王不存在2026年1月寫的文章", "all",
        handler=h, filters=list(AUTHOR_DATE_FILTERS)))
    assert results == []
    assert h.author_search_no_results is True
    assert h.time_filter_relaxed is False   # 關鍵：無 stale flag


def test_author_exists_outside_date_range_sets_time_flag(monkeypatch):
    """author+date：帶 date 的兜底空、去 date 的兜底命中 → time flag 正確 set。"""
    client, calls = _mk_pg(monkeypatch,
                           execute_results=[[], [], [_fake_author_row()]])
    h = _mk_handler()
    results = asyncio.run(client.search(
        "幫我找記者王家瑜2020年1月寫的文章", "all",
        handler=h,
        filters=[{"field": "author", "operator": "contains", "value": "王家瑜"},
                 {"field": "datePublished", "operator": "gte", "value": "2020-01-01"},
                 {"field": "datePublished", "operator": "lte", "value": "2020-01-31"}]))
    assert len(results) == 1
    assert h.time_filter_relaxed is True    # 結果真的來自無 date 查詢
    assert h.author_search_no_results is False


def test_author_only_docs_sql_and_params():
    """AR R1 三家同抓（假綠燈防線）：_author_only_docs 的 SQL 構建邏輯必須被真正
    執行與斷言——fake conn/cursor 捕捉 SQL 與參數。"""
    captured = {}

    class _FakeCursor:
        async def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

        async def fetchall(self):
            import datetime as _dt
            return [{"chunk_id": 1, "article_id": 1, "chunk_text": "內文",
                     "url": "https://example.com/a1", "title": "標題",
                     "author": "王家瑜", "source": "chinatimes",
                     "date_published": _dt.datetime(2026, 1, 1), "metadata": {}}]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _FakeConn:
        def cursor(self, row_factory=None):
            return _FakeCursor()

    client = object.__new__(PgVectorClient)
    rows = asyncio.run(client._author_only_docs(
        _FakeConn(), "王家瑜", [], None,
        [{"field": "author", "operator": "contains", "value": "王家瑜"},
         {"field": "datePublished", "operator": "gte", "value": "2026-01-01"}],
        50, include_vectors=False))
    sql = captured["sql"]
    assert "DISTINCT ON (a.url)" in sql
    assert "a.author ILIKE %s" in sql
    assert "a.date_published >= %s" in sql          # 非 author filter 保留
    assert "date_published DESC" in sql
    assert ", c.embedding" not in sql                # include_vectors=False 不帶
    assert captured["params"] == ["%王家瑜%", "2026-01-01", 50]
    assert len(rows) == 1
    assert rows[0]["author"] == "王家瑜"
    assert rows[0]["keyword_hit"] is True
    assert rows[0]["vector_score"] == 0.0


def test_author_only_docs_include_vectors_sql():
    """include_vectors=True 時 SQL 帶 c.embedding 欄（MMR 相容，D5）。"""
    captured = {}

    class _FakeCursor:
        async def execute(self, sql, params):
            captured["sql"] = sql

        async def fetchall(self):
            return []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _FakeConn:
        def cursor(self, row_factory=None):
            return _FakeCursor()

    client = object.__new__(PgVectorClient)
    asyncio.run(client._author_only_docs(
        _FakeConn(), "王家瑜", [], None, [], 50, include_vectors=True))
    assert ", c.embedding" in captured["sql"]


# --- Task 4: SummarizeResults 空素材 guard ---

def test_summarize_skipped_on_empty_answers():
    from core.post_ranking import SummarizeResults
    sr = object.__new__(SummarizeResults)  # bypass PromptRunner.__init__
    sr.handler = SimpleNamespace(final_ranked_answers=[], generate_mode='unified',
                                 summary=None)
    called = []

    async def fake_run_prompt(*a, **k):
        called.append(1)
        return {"summary": "不該出現的摘要"}
    sr.run_prompt = fake_run_prompt
    asyncio.run(sr.do())
    assert not called, "空素材時不得呼叫 LLM 生成摘要"


def test_summarize_runs_with_answers():
    from core.post_ranking import SummarizeResults
    sr = object.__new__(SummarizeResults)
    # Task 12 (SSE typed pipeline): post_ranking 改走 send_sse(path="full") →
    # handler.message_sender.send_message（真 handler 於 baseHandler:210 必有
    # message_sender；此 fake 原本只給 send_message delegate，under-model 了
    # 真 handler 契約）。補 message_sender 對齊真契約。
    h = SimpleNamespace(final_ranked_answers=[{"title": "x"}], generate_mode='unified',
                        summary=None, send_message=AsyncMock(),
                        message_sender=SimpleNamespace(send_message=AsyncMock()),
                        state=SimpleNamespace(precheck_step_done=AsyncMock()))
    sr.handler = h

    async def fake_run_prompt(*a, **k):
        return {"summary": "正常摘要"}
    sr.run_prompt = fake_run_prompt
    asyncio.run(sr.do())
    assert h.summary == "正常摘要"
    # 送達走 send_sse(path="full") → message_sender.send_message（unified await 分支）
    h.message_sender.send_message.assert_awaited_once()
    sent = h.message_sender.send_message.call_args[0][0]
    assert sent["message_type"] == "summary" and sent["content"] == "正常摘要"
