"""LR 無標題 evidence 補標題（low-tier LLM 從 snippet 生成標題）。

接線點：`loop_engine._add_external_evidence`（LR web/wiki 共用入口）。
Google CSE API 無 title 時填字串 "No Title"；空 title 或 "No Title" 進池前
用 low-tier LLM 從 snippet 生成簡潔中文標題。

設計（CEO 拍板）：
1. title 為空字串 OR 等於 "No Title" → 觸發補標題；正常標題不動（省錢）。
2. 每輪 cap=8（per-run 計數器）。超過 cap → 直接用 source_domain，不呼叫 LLM。
3. snippet 也空 → 直接用 source_domain，不呼叫 LLM（沒內文餵 LLM 沒意義）。
4. LLM level="low"；失敗/timeout → 降級用 source_domain + log（不可 silent fail）。

紀律：一律 mock ask_llm，絕不打真實 LLM。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_engine(handler_kwargs=None):
    """沿 test_loop_engine_gap_routing.py 的 _make_engine pattern。"""
    from reasoning.live_research.loop_engine import BABLoopEngine
    handler_kw = {
        "query_params": {},
        "site": "all",
        "enable_web_search": True,
        "enable_gap_enrichment": True,
    }
    if handler_kwargs:
        handler_kw.update(handler_kwargs)
    handler = MagicMock(**handler_kw)
    engine = BABLoopEngine(
        associator=MagicMock(), handler=handler, max_iterations=1,
    )
    engine._current_iteration = 1
    return engine


def _last_entry(engine):
    """取最後一筆入池 evidence。"""
    eid = max(engine.evidence_pool)
    return engine.evidence_pool[eid]


# ──────────────────────────────────────────────────────────────────────────
# 觸發條件：title 空 / "No Title" → 呼叫 LLM 補標題
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_title_triggers_llm_backfill():
    """title 為空字串 → 呼叫 low-tier LLM 從 snippet 生成標題。"""
    engine = _make_engine()
    fake = AsyncMock(return_value={"title": "德國能源轉型政策"})
    with patch("core.llm.ask_llm", fake):
        await engine._add_external_evidence(
            {"url": "https://example.com/a", "title": "",
             "snippet": "德國推動再生能源轉型，逐步淘汰核電與燃煤。"},
            source="web",
        )
    fake.assert_awaited_once()
    assert _last_entry(engine).title == "德國能源轉型政策"


@pytest.mark.asyncio
async def test_no_title_sentinel_triggers_llm_backfill():
    """title 等於 "No Title" → 呼叫 LLM 補標題（Google CSE fallback 字串）。"""
    engine = _make_engine()
    fake = AsyncMock(return_value={"title": "台灣離岸風電進度"})
    with patch("core.llm.ask_llm", fake):
        await engine._add_external_evidence(
            {"url": "https://example.com/b", "title": "No Title",
             "snippet": "彰化外海離岸風場陸續併網發電。"},
            source="web",
        )
    fake.assert_awaited_once()
    assert _last_entry(engine).title == "台灣離岸風電進度"


@pytest.mark.asyncio
async def test_low_tier_level_used_for_backfill():
    """補標題 LLM call 必須用 level="low"（省錢）。"""
    engine = _make_engine()
    seen = {}

    async def fake_ask(prompt, schema, level="high", **kw):
        seen["level"] = level
        return {"title": "標題"}

    with patch("core.llm.ask_llm", fake_ask):
        await engine._add_external_evidence(
            {"url": "https://example.com/c", "title": "",
             "snippet": "內文內容。"},
            source="web",
        )
    assert seen["level"] == "low"


# ──────────────────────────────────────────────────────────────────────────
# 不觸發：正常標題不動（省錢）
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_normal_title_does_not_call_llm():
    """有正常標題 → 不呼叫 LLM，title 原樣入池。"""
    engine = _make_engine()
    fake = AsyncMock(return_value={"title": "不該被用到"})
    with patch("core.llm.ask_llm", fake):
        await engine._add_external_evidence(
            {"url": "https://example.com/d", "title": "正常的文章標題",
             "snippet": "內文。"},
            source="web",
        )
    fake.assert_not_awaited()
    assert _last_entry(engine).title == "正常的文章標題"


# ──────────────────────────────────────────────────────────────────────────
# snippet 空 → 用 source_domain，不呼叫 LLM
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_snippet_uses_domain_no_llm():
    """title 空 + snippet 也空 → 用 source_domain 當標題，不呼叫 LLM。"""
    engine = _make_engine()
    fake = AsyncMock(return_value={"title": "不該被用到"})
    with patch("core.llm.ask_llm", fake):
        await engine._add_external_evidence(
            {"url": "https://www.example.com/e", "title": "No Title",
             "snippet": ""},
            source="web",
        )
    fake.assert_not_awaited()
    assert _last_entry(engine).title == "example.com"


# ──────────────────────────────────────────────────────────────────────────
# cap：每輪最多補 8 筆，超過用 source_domain（不呼叫 LLM）
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cap_exceeded_uses_domain_no_llm():
    """同一 run 內超過 cap=8 的無標題 entry → 用 source_domain，不再呼叫 LLM。"""
    from reasoning.live_research.loop_engine import TITLE_BACKFILL_CAP

    engine = _make_engine()
    call_count = {"n": 0}

    async def fake_ask(prompt, schema, level="low", **kw):
        call_count["n"] += 1
        return {"title": f"生成標題{call_count['n']}"}

    # 餵 cap+2 筆無標題（每筆 domain 不同避免 URL dedup）
    with patch("core.llm.ask_llm", fake_ask):
        for i in range(TITLE_BACKFILL_CAP + 2):
            await engine._add_external_evidence(
                {"url": f"https://site{i}.com/x", "title": "No Title",
                 "snippet": "內文內容。"},
                source="web",
            )

    # LLM 只被叫 cap 次
    assert call_count["n"] == TITLE_BACKFILL_CAP
    # 第 cap+1、cap+2 筆用 domain
    capped_entry = engine.evidence_pool[max(engine.evidence_pool)]
    assert capped_entry.title == f"site{TITLE_BACKFILL_CAP + 1}.com"


@pytest.mark.asyncio
async def test_cap_resets_per_run():
    """cap 計數器 per-run 重置（run_loop 入口 _reset_per_run_dedup_flags）。"""
    engine = _make_engine()
    engine._title_backfill_count = 5
    engine._reset_per_run_dedup_flags()
    assert engine._title_backfill_count == 0


# ──────────────────────────────────────────────────────────────────────────
# LLM 失敗降級：用 source_domain（不可 silent fail，要 log）
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_llm_failure_degrades_to_domain():
    """LLM call 失敗 → 降級用 source_domain 當標題（不 crash、不空標題）。"""
    engine = _make_engine()

    async def boom(prompt, schema, level="low", **kw):
        raise RuntimeError("LLM down")

    with patch("core.llm.ask_llm", boom):
        await engine._add_external_evidence(
            {"url": "https://www.cna.com.tw/news/x", "title": "",
             "snippet": "中央社報導內文。"},
            source="web",
        )
    assert _last_entry(engine).title == "cna.com.tw"


@pytest.mark.asyncio
async def test_llm_failure_logs_degradation(monkeypatch):
    """LLM 失敗降級必須 log（不可 silent fail）。"""
    from reasoning.live_research import loop_engine as le

    captured = []
    original_warning = le.logger.warning

    def spy_warning(msg, *args, **kwargs):
        captured.append(str(msg))
        return original_warning(msg, *args, **kwargs)

    monkeypatch.setattr(le.logger, "warning", spy_warning)

    engine = _make_engine()

    async def boom(prompt, schema, level="low", **kw):
        raise RuntimeError("LLM down")

    with patch("core.llm.ask_llm", boom):
        await engine._add_external_evidence(
            {"url": "https://example.com/f", "title": "No Title",
             "snippet": "內文。"},
            source="web",
        )
    assert any("title" in m.lower() and "example.com" in m for m in captured), \
        f"Expected degradation log mentioning fallback domain, got: {captured}"


@pytest.mark.asyncio
async def test_llm_empty_response_degrades_to_domain():
    """LLM 回空 title（schema 過但內容空）→ 降級用 source_domain，不留空標題。"""
    engine = _make_engine()
    fake = AsyncMock(return_value={"title": "   "})
    with patch("core.llm.ask_llm", fake):
        await engine._add_external_evidence(
            {"url": "https://www.example.com/g", "title": "",
             "snippet": "內文。"},
            source="web",
        )
    assert _last_entry(engine).title == "example.com"
