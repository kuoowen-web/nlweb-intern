"""搜尋卡片 text fragment：_build_schema_json 須在 schema 多帶
description（卡片摘要顯示）+ matched_text（前端組 #:~:text= 的 verbatim quote），
且不得改動既有 articleBody 語意（CEO 保守版）。"""
import json
import os
import sys
import datetime

# Add code/python to sys.path（tests/unit/retrieval/ → 往上三層）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from retrieval_providers.postgres_client import PgVectorClient


def _make_row(chunk_text="彰化外海離岸風電第三階段區塊開發，預計新增三百萬瓩容量。"):
    return {
        "title": "離岸風電第三階段",
        "url": "https://example.com/wind-3",
        "chunk_text": chunk_text,
        "source": "中央社",
        "date_published": datetime.datetime(2026, 1, 1),
        "author": "",
    }


def _schema(row):
    # _build_schema_json 只讀 row 參數，不依賴 instance 狀態 → __new__ stub 不連 DB
    s = object.__new__(PgVectorClient)
    return json.loads(s._build_schema_json(row))


def test_schema_has_description_equal_to_chunk_text():
    row = _make_row()
    schema = _schema(row)
    assert schema["description"] == row["chunk_text"]


def test_schema_has_matched_text_verbatim_quote():
    """matched_text = chunk_text 逐字（trim 後），供前端組 text fragment 雙錨點。"""
    row = _make_row()
    schema = _schema(row)
    assert schema["matched_text"].startswith("彰化外海離岸風電")


def test_schema_articlebody_unchanged_conservative():
    """CEO 保守版鐵律：articleBody 語意不變，仍 = chunk_text。"""
    row = _make_row()
    schema = _schema(row)
    assert schema["articleBody"] == row["chunk_text"]


def test_schema_empty_chunk_yields_empty_matched_text():
    row = _make_row(chunk_text="")
    schema = _schema(row)
    assert schema["matched_text"] == ""        # 前端據此降級裸 URL
    assert schema["description"] == ""
