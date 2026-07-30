"""票 2026-07-28-m：relevance gate 共用判定核心純函式測試。

判定核心與池形狀解耦：吃 (query, digest, judged_ids) → 回已 clamp 的 irrelevant set。
🔧 R1 三態回傳：None = fail-open（LLM 失敗/無法解析）；set() = 全相關；非空 set = 不相關 id。
LLM call patch core.llm.ask_llm（函式內局部 import，call-time lookup）。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from reasoning.relevance_gate_core import judge_irrelevant_source_ids


def make_logger():
    return MagicMock()


class TestJudgeIrrelevantSourceIds:

    @pytest.mark.asyncio
    async def test_returns_clamped_irrelevant_set(self):
        """LLM 回 [2, 99]，judged_ids=[1,2,3] → 只回 {2}（99 幻覺編號 clamp 掉）。"""
        logger = make_logger()
        with patch(
            "core.llm.ask_llm",
            new=AsyncMock(return_value={"irrelevant_ids": [2, 99]}),
        ):
            result = await judge_irrelevant_source_ids(
                query="查邱啟新",
                digest="[1] a - t1：x\n[2] b - t2：y\n[3] c - t3：z",
                judged_ids=[1, 2, 3],
                query_params={},
                logger=logger,
            )
        assert result == {2}

    @pytest.mark.asyncio
    async def test_all_relevant_empty_set(self):
        """🔧 R1：正常判定全相關 → set()（非 None）——與 fail-open None 語義分明。"""
        logger = make_logger()
        with patch(
            "core.llm.ask_llm", new=AsyncMock(return_value={"irrelevant_ids": []})
        ):
            result = await judge_irrelevant_source_ids(
                query="q", digest="[1] a - t：x", judged_ids=[1],
                query_params={}, logger=logger,
            )
        assert result == set()  # 正常全相關 = 空 set（不是 None）

    @pytest.mark.asyncio
    async def test_bool_excluded_from_clamp(self):
        """bool 是 int subclass——True/False 不得被當 id 剔除。"""
        logger = make_logger()
        with patch(
            "core.llm.ask_llm",
            new=AsyncMock(return_value={"irrelevant_ids": [True, 1]}),
        ):
            result = await judge_irrelevant_source_ids(
                query="q", digest="[1] a - t：x", judged_ids=[1],
                query_params={}, logger=logger,
            )
        assert result == {1}  # True 不算，1 算

    @pytest.mark.asyncio
    async def test_llm_exception_fails_open(self):
        """🔧 R1：fail-open 回 None（非 set()）——與「全相關 set()」語義分明。"""
        logger = make_logger()
        with patch(
            "core.llm.ask_llm", new=AsyncMock(side_effect=RuntimeError("down"))
        ):
            result = await judge_irrelevant_source_ids(
                query="q", digest="[1] a - t：x", judged_ids=[1],
                query_params={}, logger=logger,
            )
        assert result is None  # 🔧 R1：fail-open → None
        logger.warning.assert_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_resp", [{"nope": 1}, {"irrelevant_ids": "not-a-list"}]
    )
    async def test_unparseable_fails_open(self, bad_resp):
        logger = make_logger()
        with patch("core.llm.ask_llm", new=AsyncMock(return_value=bad_resp)):
            result = await judge_irrelevant_source_ids(
                query="q", digest="[1] a - t：x", judged_ids=[1],
                query_params={}, logger=logger,
            )
        assert result is None  # 🔧 R1：fail-open → None
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_llm_error_sentinel_fails_open(self):
        from core.llm import LLMError
        logger = make_logger()
        with patch(
            "core.llm.ask_llm", new=AsyncMock(return_value=LLMError("timeout", "x"))
        ):
            result = await judge_irrelevant_source_ids(
                query="q", digest="[1] a - t：x", judged_ids=[1],
                query_params={}, logger=logger,
            )
        assert result is None  # 🔧 R1：fail-open → None
        logger.warning.assert_called()
