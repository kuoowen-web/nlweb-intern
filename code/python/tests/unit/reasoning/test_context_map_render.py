"""Task 17 — context_map render helper 去重 characterization tests.

抽 `_render_context_map_topics` / `_render_context_map_relations` 共用純格式化邏輯
（已 filter 的 list → markdown 段落），filter / empty-skip 決策留在各 caller。

Characterization gate：抽前抽後 `context_map_to_summary` /
`context_map_extract_for_section` 輸出位元一致（golden 字串在抽取前 capture）。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from reasoning.schemas_live import (  # noqa: E402
    ContextMap,
    ContextMapRelation,
    ContextMapSearchSeed,
    ContextMapTopic,
    _render_context_map_relations,
    _render_context_map_topics,
    context_map_extract_for_section,
    context_map_to_summary,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _full_context_map() -> ContextMap:
    return ContextMap(
        research_question='台灣離岸風電爭議',
        working_hypothesis='漁業與風電存在用地衝突',
        topics=[
            ContextMapTopic(
                topic_id='t1', name='離岸風電', domain='能源政策',
                description='風場開發', relevance='core',
                evidence_ids=[1, 2, 3], confidence='high',
            ),
            ContextMapTopic(
                topic_id='t2', name='漁業權益', domain='漁業',
                relevance='supporting', evidence_ids=[], confidence='medium',
            ),
            ContextMapTopic(
                topic_id='t3', name='周邊經濟', domain='經濟',
                description='', relevance='peripheral',
                evidence_ids=[4], confidence='low',
            ),
        ],
        relations=[
            ContextMapRelation(
                relation_id='r1', source_topic_id='t1', target_topic_id='t2',
                relation_type='contradicts', description='用地重疊',
            ),
        ],
        search_seeds=[
            ContextMapSearchSeed(
                seed_id='s1', query='離岸風場面積', target_topic_id='t1',
                rationale='量化用地', priority='high', status='pending',
            ),
        ],
        followup_questions=['補償機制如何設計？'],
        version=2,
    )


def _empty_segment_context_map() -> ContextMap:
    """單 core topic、無 relation / seed / hypothesis / followup —
    relations / 待查 / 後續問題段全空（caller 各自 skip）。"""
    return ContextMap(
        research_question='極簡問題',
        topics=[
            ContextMapTopic(
                topic_id='x1', name='唯一議題', domain='測試',
                relevance='core', evidence_ids=[],
            ),
        ],
        relations=[],
        search_seeds=[],
        followup_questions=[],
        version=0,
    )


# Golden output captured from the pre-refactor implementation (byte-exact).
_GOLDEN_SUMMARY = (
    "## 研究結構 (v2)\n"
    "研究問題: 台灣離岸風電爭議\n"
    "工作假設: 漁業與風電存在用地衝突\n"
    "\n"
    "### 核心議題\n"
    "- **離岸風電** (能源政策): 風場開發 (confidence: high，3 個來源支持)\n"
    "\n"
    "### 支持議題\n"
    "- **漁業權益** (漁業) (confidence: medium)\n"
    "\n"
    "### 周邊議題\n"
    "- **周邊經濟** (經濟) (confidence: low，1 個來源支持)\n"
    "\n"
    "### 關係\n"
    "- 離岸風電 --contradicts--> 漁業權益: 用地重疊\n"
    "\n"
    "### 待查\n"
    "- [離岸風場面積]: 量化用地 (priority: high)\n"
    "\n"
    "### 後續問題\n"
    "- 補償機制如何設計？\n"
)

_GOLDEN_EXTRACT = (
    "## 研究結構 (v2)\n"
    "研究問題: 台灣離岸風電爭議\n"
    "工作假設: 漁業與風電存在用地衝突\n"
    "\n"
    "### 核心議題\n"
    "- **離岸風電** (能源政策): 風場開發 (confidence: high，3 個來源支持)\n"
    "\n"
    "### 支持議題\n"
    "- **漁業權益** (漁業) (confidence: medium)\n"
    "\n"
    "### 關係\n"
    "- 離岸風電 --contradicts--> 漁業權益: 用地重疊\n"
    "\n"
    "### 待查\n"
    "- [離岸風場面積]: 量化用地 (priority: high)\n"
)

_GOLDEN_SUMMARY_EMPTY = (
    "## 研究結構 (v0)\n"
    "研究問題: 極簡問題\n"
    "\n"
    "### 核心議題\n"
    "- **唯一議題** (測試) (confidence: medium)\n"
)

_GOLDEN_EXTRACT_EMPTY = (
    "## 研究結構 (v0)\n"
    "研究問題: 極簡問題\n"
    "\n"
    "### 核心議題\n"
    "- **唯一議題** (測試) (confidence: medium)\n"
)


# ---------------------------------------------------------------------------
# Helper unit tests — 純格式化已 filter 的 list
# ---------------------------------------------------------------------------
def test_render_topics_groups_by_relevance():
    """給含 core / supporting / peripheral topic 的 list → 依 _RELEVANCE_LABELS
    分組標題 + narrative count bullet（含 evidence → 「，N 個來源支持」）。
    回傳 list of lines，每組以空字串元素分隔（與 caller 逐行 append 一致）。"""
    topics = [
        ContextMapTopic(
            topic_id='t1', name='離岸風電', domain='能源政策',
            description='風場開發', relevance='core',
            evidence_ids=[1, 2, 3], confidence='high',
        ),
        ContextMapTopic(
            topic_id='t2', name='漁業權益', domain='漁業',
            relevance='supporting', evidence_ids=[], confidence='medium',
        ),
    ]
    expected = [
        "### 核心議題",
        "- **離岸風電** (能源政策): 風場開發 (confidence: high，3 個來源支持)",
        "",
        "### 支持議題",
        "- **漁業權益** (漁業) (confidence: medium)",
        "",
    ]
    assert _render_context_map_topics(topics) == expected


def test_render_topics_empty_list_yields_no_lines():
    """空 list → 回空 list（caller 的 skip 決策由 caller 處理，
    helper 對空 list 純粹回空，不吸收 skip）。"""
    assert _render_context_map_topics([]) == []


def test_render_relations_formats_arrows():
    """給 relation list + topic_name_map → 「### 關係」段 + 箭頭 bullet。"""
    relations = [
        ContextMapRelation(
            relation_id='r1', source_topic_id='t1', target_topic_id='t2',
            relation_type='contradicts', description='用地重疊',
        ),
    ]
    name_map = {'t1': '離岸風電', 't2': '漁業權益'}
    expected = [
        "### 關係",
        "- 離岸風電 --contradicts--> 漁業權益: 用地重疊",
        "",
    ]
    assert _render_context_map_relations(relations, name_map) == expected


def test_render_relations_empty_list_yields_no_lines():
    assert _render_context_map_relations([], {}) == []


# ---------------------------------------------------------------------------
# Characterization gate — 抽前抽後位元一致
# ---------------------------------------------------------------------------
def test_summary_byte_identical_to_golden():
    assert context_map_to_summary(_full_context_map()) == _GOLDEN_SUMMARY


def test_extract_byte_identical_to_golden():
    assert context_map_extract_for_section(_full_context_map(), ['t1']) == _GOLDEN_EXTRACT


def test_summary_empty_segments_byte_identical():
    assert context_map_to_summary(_empty_segment_context_map()) == _GOLDEN_SUMMARY_EMPTY


def test_extract_empty_segments_byte_identical():
    assert (
        context_map_extract_for_section(_empty_segment_context_map(), ['x1'])
        == _GOLDEN_EXTRACT_EMPTY
    )
