"""LR O2 / O2-TF: _build_citation_sources 把 evidence_pool 攤成
eid -> {url,title,domain,quote} 供前端 inline citation 點擊回溯 + text fragment
highlight。

from-scratch 分支（依賴 lr-auto-citation-clickback-plan Task 1-4 截至 2026-06-15
未落地 → 本檔含完整契約測試，不假設既有 helper 存在）。
"""

from reasoning.live_research.orchestrator import LiveResearchOrchestrator
from reasoning.live_research.stage_state import LiveResearchStageState
from reasoning.schemas_live import EvidencePoolEntry, serialize_evidence_pool


def _make_state_with_pool():
    """構造一個帶 evidence_pool_json 的最小 state stub。

    - LiveResearchStageState 是 dataclass，**無 `query` 欄位** → no-arg 建構。
    - EvidencePoolEntry `evidence_id: int` 必填，其餘欄位有 default。
    - serialize_evidence_pool / deserialize_evidence_pool 在 reasoning.schemas_live。
    """
    state = LiveResearchStageState()
    pool = {
        3: EvidencePoolEntry(
            evidence_id=3,
            title="丹麥綠能轉型",
            url="https://example.com/dk-green",
            source_domain="example.com",
            author="王立人",
            year="2022",
            snippet="丹麥在 2023 年達成風電佔比超過五成，政府透過社區共有模式分配收益。",
            source="internal",
        ),
        7: EvidencePoolEntry(
            evidence_id=7,
            title="背景知識",
            url="urn:llm:knowledge:能源政策",
            source_domain="",
            author="",
            year="",
            source="llm_knowledge",
        ),
    }
    state.evidence_pool_json = serialize_evidence_pool(pool)
    return state


# ---- 沿用現有 plan Task 1 契約：eid -> {url,title,domain} ----

def test_build_citation_sources_maps_eid_to_metadata():
    state = _make_state_with_pool()
    result = LiveResearchOrchestrator._build_citation_sources(state)
    # eid 為 key（轉成 str 供 JSON 跨層；前端用 String(eid) 查）
    assert result["3"]["url"] == "https://example.com/dk-green"
    assert result["3"]["title"] == "丹麥綠能轉型"
    assert result["3"]["domain"] == "example.com"
    # URN 來源照原樣帶（前端決定渲染方式）
    assert result["7"]["url"] == "urn:llm:knowledge:能源政策"


def test_build_citation_sources_empty_pool_returns_empty_dict():
    state = LiveResearchStageState()
    state.evidence_pool_json = ""
    assert LiveResearchOrchestrator._build_citation_sources(state) == {}


# ---- 本 plan Task A 新增：quote 欄位（text fragment 來源）----

def test_build_citation_sources_includes_verbatim_quote_from_snippet():
    """O2-TF: citation_sources[eid] 必含 quote 欄位，取自 EvidencePoolEntry.snippet
    （verbatim 原文），供前端組 text fragment。"""
    state = LiveResearchStageState()
    pool = {
        3: EvidencePoolEntry(
            evidence_id=3,
            title="丹麥綠能轉型",
            url="https://example.com/dk-green",
            source_domain="example.com",
            snippet="丹麥在 2023 年達成風電佔比超過五成，政府透過社區共有模式分配收益。",
            source="internal",
        ),
    }
    state.evidence_pool_json = serialize_evidence_pool(pool)
    result = LiveResearchOrchestrator._build_citation_sources(state)
    # quote 為 verbatim snippet（trim 後），前端據此組 #:~:text=START,END
    assert "quote" in result["3"]
    assert result["3"]["quote"].startswith("丹麥在 2023 年")
    # url / title / domain 沿用現有 plan 契約不變
    assert result["3"]["url"] == "https://example.com/dk-green"


def test_build_citation_sources_empty_snippet_yields_empty_quote():
    """snippet 空 → quote 空字串（前端降級裸 URL，不組 fragment）。"""
    state = LiveResearchStageState()
    pool = {
        9: EvidencePoolEntry(
            evidence_id=9, title="無摘要來源",
            url="https://example.com/no-snip", source_domain="example.com",
            snippet="", source="internal",
        ),
    }
    state.evidence_pool_json = serialize_evidence_pool(pool)
    result = LiveResearchOrchestrator._build_citation_sources(state)
    assert result["9"]["quote"] == ""
    assert result["9"]["url"] == "https://example.com/no-snip"


def test_build_citation_sources_web_source_suppresses_quote():
    """Decision 2'：web source 的 snippet 是 Google snippet（含省略號），命中率低 →
    後端不交 quote（quote=""），前端據此降級裸 URL。只有 internal 才交 quote。"""
    state = LiveResearchStageState()
    pool = {
        5: EvidencePoolEntry(
            evidence_id=5, title="web 來源",
            url="https://example.com/web", source_domain="example.com",
            snippet="丹麥綠能轉型...政府透過社區共有模式...分配收益",  # Google snippet 含省略號
            source="web",
        ),
        6: EvidencePoolEntry(
            evidence_id=6, title="internal 來源",
            url="https://example.com/internal", source_domain="example.com",
            snippet="丹麥在 2023 年達成風電佔比超過五成。",
            source="internal",
        ),
    }
    state.evidence_pool_json = serialize_evidence_pool(pool)
    result = LiveResearchOrchestrator._build_citation_sources(state)
    assert result["5"]["quote"] == ""               # web → 不交 quote
    assert result["6"]["quote"].startswith("丹麥在 2023 年")  # internal → 交 quote


def test_build_citation_sources_wiki_and_llm_knowledge_suppress_quote():
    """Decision 2'：wiki / llm_knowledge 無對應站外逐字原文 → 不交 quote。"""
    state = LiveResearchStageState()
    pool = {
        1: EvidencePoolEntry(
            evidence_id=1, title="wiki", url="https://wiki/x",
            source_domain="wikipedia.org", snippet="維基摘要內容片段",
            source="wiki",
        ),
        2: EvidencePoolEntry(
            evidence_id=2, title="背景知識", url="urn:llm:knowledge:x",
            source_domain="", snippet="這是 LLM 背景知識虛擬內容",
            source="llm_knowledge",
        ),
    }
    state.evidence_pool_json = serialize_evidence_pool(pool)
    result = LiveResearchOrchestrator._build_citation_sources(state)
    assert result["1"]["quote"] == ""
    assert result["2"]["quote"] == ""


# ---- Task B 契約：payload 四鍵 ----

def test_citation_sources_payload_contract_has_quote_key():
    """契約：_build_citation_sources 輸出的每個 entry 都含 url/title/domain/quote
    四鍵（防 emit 端漏帶 quote / regress 成 None）。"""
    state = LiveResearchStageState()
    state.evidence_pool_json = serialize_evidence_pool({
        1: EvidencePoolEntry(evidence_id=1, url="https://x.com/a",
                             title="t", source_domain="x.com",
                             snippet="原文片段內容", source="internal"),
    })
    cs = LiveResearchOrchestrator._build_citation_sources(state)
    assert set(cs["1"].keys()) == {"url", "title", "domain", "quote"}


def test_section_and_export_payload_contract_includes_citation_sources():
    """契約：空 pool 也回 dict（非 None），前端 graceful no-op。"""
    state = LiveResearchStageState()
    state.evidence_pool_json = ""
    cs = LiveResearchOrchestrator._build_citation_sources(state)
    assert isinstance(cs, dict)
