"""票 2026-07-28-m：relevance gate 共用判定核心純函式測試。

判定核心與池形狀解耦：吃 (query, digest, judged_ids) → 回已 clamp 的 irrelevant set。
🔧 R1 三態回傳：None = fail-open（LLM 失敗/無法解析）；set() = 全相關；非空 set = 不相關 id。
LLM call patch core.llm.ask_llm（函式內局部 import，call-time lookup）。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from reasoning.relevance_gate_core import (
    _build_relevance_prompt,
    judge_irrelevant_source_ids,
)


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


class TestSubjectIdentityRule:
    """機械防線：gate prompt 的「主題同一性」條（UX 回報 row7）。

    病：問「再生能源」卻回「再生水／海水淡化」。舊版負面表列只涵蓋**具名人物**張冠李戴，
    概念/主題類查詢無對應條款 → 落進「間接相關要保留」+「不確定就保留」兩條預設，
    實測真模型 3/3 全保留該來源。本組鎖住新條存在、與人物條同級、且**排在
    「不確定就保留」之前**（順序決定它讀起來是例外還是被預設吃掉）。

    純字串斷言、不呼叫 LLM（$0）。mutation：刪掉該條 → 4 條全紅（已驗）。
    """

    def _prompt(self):
        return _build_relevance_prompt("再生能源", "[1] moea - 某報導：內文")

    def test_subject_identity_rule_present(self):
        p = self._prompt()
        assert "另一個主題" in p, "gate prompt 缺「主題同一性」條 —— 概念類查詢無守門"
        assert "共用字詞不等於同一個主題" in p

    def test_person_rule_not_clobbered(self):
        """新條不得取代原本的人物條（兩條並存，各守一類查詢主體）。"""
        p = self._prompt()
        assert "另一個具名人物" in p
        assert "同機構不等於相關" in p

    def test_rule_precedes_uncertainty_fallback(self):
        """新條必須排在「不確定就保留」之前，否則會被 fail-open 預設抵銷。"""
        p = self._prompt()
        assert p.index("另一個主題") < p.index("不確定就保留")

    def test_fail_open_default_preserved(self):
        """收嚴判準不得順手拆掉 fail-open —— 誤刪真證據的代價仍高於留噪音。"""
        p = self._prompt()
        assert "不確定就保留" in p
