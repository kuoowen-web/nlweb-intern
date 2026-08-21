"""機械防線：ranking 類 prompt 必須帶「主題同一性」判準（UX 回報 row7）。

**病**：問「再生能源」卻回「再生水／海水淡化」。實測（真 prompt + 真 low 模型）舊版
RankingPrompt 給一篇與提問主題無關、只是同屬環境領域的報導 **85 分**，與真正切題的
報導同分 → 過 `ranking.py` 的 `score > 51` 閘門直達使用者。根因是判準只問「相關程度」，
沒有任何一條要求區分「共用字詞／同屬上位領域」與「真的在談同一個主題」。

**這組測試鎖什麼**（純解析，不呼叫 LLM，$0）：
1. 4 個**活**節點每一個都帶判準 + 低分區間指令 —— 少改一個就紅（sweep-all-exits）。
2. 判準不得寫死回報案例的領域字詞 —— 負面例字串會被弱模型鸚鵡學舌，且只治單一詞對。
3. Statistics 區的 RankingPrompt 目前**不可達**（sites.xml 十站全 Article）所以未改；
   本測試把「不可達」這個前提本身釘住，日後有人加 Statistics 站就會紅，提醒補改。

mutation 驗證：拿掉任一節點的判準區塊 → 對應案例轉紅（已驗）。
"""

import os
import xml.etree.ElementTree as ET

import pytest

from core.prompts import find_prompt

CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    os.pardir, os.pardir, "config",
)

# (site, prompt_name, 標記詞, 最低分區間, 「談的是別的主題」用語) —— site="all" 走
# Site id=all（英文版），其他 site 走 Site id=default（繁中版）。四個都是活路徑：
#   RankingPrompt            <- core/ranking.py:104 (搜尋模式)
#   RankingPromptForGenerate <- methods/generate_answer.py:38 (/ask generate)
#
# 為什麼鎖「分數區間表」而不是散文式判準：實測（真模型 3 次 × 4 節點）散文版只有
# all/RankingPrompt 壓得下去，繁中版與四維度加權版照樣給 75-88 分；改成「0-30 /
# 31-51 / 52-79 / 80-100」的區間表 + 對 final_score 的上限約束後才全數達標。
# 區間表是這條判準的**作用機制**，不是措辭偏好，所以要鎖。
LIVE_RANKING_PROMPTS = [
    ("nlweb", "RankingPrompt", "主題同一性", "0-30", "另一個主題"),
    ("nlweb", "RankingPromptForGenerate", "主題同一性", "0-30", "另一個主題"),
    ("all", "RankingPrompt", "SUBJECT-IDENTITY", "0-30", "DIFFERENT subject"),
    ("all", "RankingPromptForGenerate", "SUBJECT-IDENTITY", "0-30", "DIFFERENT subject"),
]

# 判準必須是通用表述，不得寫死回報案例的領域字詞（見 module docstring 第 2 點）。
FORBIDDEN_LITERALS = ["再生水", "再生能源", "海水淡化", "reclaimed water", "renewable energy"]


@pytest.mark.parametrize("site,name,marker,low_band,other_subj", LIVE_RANKING_PROMPTS)
def test_live_ranking_prompt_carries_subject_identity_rule(
    site, name, marker, low_band, other_subj
):
    """每個活節點都要有判準本體 + 最低分區間 + 「談的是別的主題」這個觸發條件。"""
    prompt_str, _ans = find_prompt(site, "Item", name)
    assert prompt_str, f"find_prompt({site}, Item, {name}) 取不到 prompt"
    assert marker in prompt_str, (
        f"[{site}/{name}] 缺「主題同一性」判準 —— 這個出口會把只共用字詞的報導評成相關"
    )
    assert low_band in prompt_str, (
        f"[{site}/{name}] 缺 0-30 分區間 —— 沒有低分區間，模型壓不到 51 閘門以下"
    )
    assert other_subj in prompt_str, (
        f"[{site}/{name}] 判準沒寫出觸發條件（報導談的是另一個主題）"
    )


@pytest.mark.parametrize("site,name,marker,low_band,other_subj", LIVE_RANKING_PROMPTS)
def test_rule_is_generic_not_case_specific(site, name, marker, low_band, other_subj):
    """不得把回報案例的領域字詞寫進 prompt（鸚鵡學舌 + 只治單一詞對）。"""
    prompt_str, _ans = find_prompt(site, "Item", name)
    for bad in FORBIDDEN_LITERALS:
        assert bad not in prompt_str, (
            f"[{site}/{name}] prompt 內出現案例字詞「{bad}」—— 判準要通用，不寫死個案"
        )


@pytest.mark.parametrize("site", ["nlweb", "all"])
def test_for_generate_declares_article_subject(site):
    """兩個加權節點要先寫出 article_subject（一句話說出報導在談什麼）再分類。

    實測：只有 subject_match 時，all/ForGenerate 對同一篇離題報導 5 次中翻 2 次
    （subject_match 在 different/same 間跳）；加上 article_subject 這個「先承諾」
    欄位後，default/ForGenerate 5/5 穩定、all/ForGenerate 4/5。單純評分的兩個
    RankingPrompt 節點本來就穩定，未加此欄（不動已經好的東西）。
    """
    _prompt, ans_struc = find_prompt(site, "Item", "RankingPromptForGenerate")
    assert "article_subject" in ans_struc, (
        f"[{site}/RankingPromptForGenerate] 缺 article_subject —— 分類穩定度會退回 3/5"
    )


@pytest.mark.parametrize("site,cap_phrase", [
    ("nlweb", "final_score 不得高於語意相關性所落區間的上限"),
    ("all", "Final Score must not exceed the ceiling of the Semantic Relevance band"),
])
def test_for_generate_caps_final_score_by_semantic_band(site, cap_phrase):
    """ForGenerate 是四維度加權；沒有這條上限，時效性/來源權威性會把離題報導推回 51 以上。

    實測：只加區間表、不加上限 → 離題報導 final_score 仍 70-82（加權把它抬上去）。
    """
    prompt_str, _ans = find_prompt(site, "Item", "RankingPromptForGenerate")
    assert cap_phrase in prompt_str, (
        f"[{site}/RankingPromptForGenerate] 缺 final_score 上限約束 —— "
        "區間表會被時效性/權威性加權蓋過"
    )


@pytest.mark.parametrize("site,name", [
    ("nlweb", "RankingPrompt"), ("nlweb", "RankingPromptForGenerate"),
    ("all", "RankingPrompt"), ("all", "RankingPromptForGenerate"),
])
def test_returnstruc_declares_subject_match(site, name):
    """returnStruc 必須宣告 subject_match —— code 端的上限完全靠這個欄位。

    欄位不見了 → clamp 永遠 fail-open → 判準寫得再好也不會生效。這條把
    prompt 契約與 core/ranking.clamp_score_by_subject_match 綁在一起。
    """
    _prompt, ans_struc = find_prompt(site, "Item", name)
    assert ans_struc and "subject_match" in ans_struc, (
        f"[{site}/{name}] returnStruc 缺 subject_match —— 分數上限會失效"
    )


class TestClampScoreBySubjectMatch:
    """code 端的硬上限（純函式，$0）。

    為什麼需要它：實測 RankingPromptForGenerate 判對了 passing_mention、semantic_score
    也給 40（31-51 內），四維度加權仍吐出 final_score=56 越過 >51 閘門。prompt 內寫
    「final_score 不得高於區間上限」被模型忽略 → 改由 code 強制。
    """

    def test_different_clamped_to_30(self):
        from core.ranking import clamp_score_by_subject_match
        assert clamp_score_by_subject_match(85, "different", "x") == 30

    def test_passing_mention_clamped_to_51(self):
        from core.ranking import clamp_score_by_subject_match
        assert clamp_score_by_subject_match(56, "passing_mention", "x") == 51

    def test_same_untouched(self):
        from core.ranking import clamp_score_by_subject_match
        assert clamp_score_by_subject_match(85, "same", "x") == 85

    def test_never_raises_a_low_score(self):
        """只壓不抬：判定 same 不代表要把 20 分拉上來。"""
        from core.ranking import clamp_score_by_subject_match
        assert clamp_score_by_subject_match(20, "different", "x") == 20
        assert clamp_score_by_subject_match(10, "same", "x") == 10

    def test_missing_field_fails_open_and_logs(self, caplog):
        """缺 subject_match → 不夾（fail-open）但必須留下訊息，不可靜默。"""
        from core.ranking import clamp_score_by_subject_match
        with caplog.at_level("WARNING"):
            assert clamp_score_by_subject_match(85, None, "某報導") == 85

    def test_unknown_value_fails_open(self):
        from core.ranking import clamp_score_by_subject_match
        assert clamp_score_by_subject_match(85, "weird_value", "x") == 85

    def test_non_numeric_score_fails_open(self):
        """'70分' 這類殘值不可炸——沿用 _safe_score 的殘值紀律。"""
        from core.ranking import clamp_score_by_subject_match
        assert clamp_score_by_subject_match("70分", "different", "x") == "70分"

    def test_bool_is_not_a_score(self):
        from core.ranking import clamp_score_by_subject_match
        assert clamp_score_by_subject_match(True, "different", "x") is True


class TestClampIsWiredIntoCallSites:
    """純函式測綠 ≠ 呼叫點有接上（R1 假綠教訓）。這組打真正的消費層。"""

    @staticmethod
    def _ranking(items):
        import asyncio
        import threading
        from core.ranking import Ranking

        class _H:
            required_item_type = None
            generate_mode = "list"
            query_params = {}
            query = "再生能源"
            site = "test_site"
            item_type = "Item"

            def __init__(self):
                self.connection_alive_event = threading.Event()
                self.connection_alive_event.set()
                self.pre_checks_done_event = asyncio.Event()
                self.pre_checks_done_event.set()
                self.final_ranked_answers = []

        r = object.__new__(Ranking)
        r.ranking_type = Ranking.REGULAR_TRACK
        r.ranking_type_str = "REGULAR_TRACK"
        r.handler = _H()
        r.level = "low"
        r.items = items
        r.num_results_sent = 0
        r.rankedAnswers = []
        r._sent_title_keys = set()
        return r

    @staticmethod
    def _item(url="http://a/1", title="T"):
        return {"url": url, "title": title, "site": "S",
                "schema_json": '{"description":"d"}', "retrieval_scores": {}, "vector": None}

    def test_rankitem_applies_ceiling(self):
        """LLM 回 score=85 + subject_match=different → rankItem output 必須是 30。"""
        import asyncio
        from unittest.mock import patch
        import core.ranking as rmod
        from core.ranking import Ranking

        async def _ask(prompt, ans_struc, level=None, query_params=None):
            return {"score": 85, "subject_match": "different", "description": "ok"}

        r = self._ranking([])
        with patch.object(rmod, "ask_llm", _ask), \
             patch.object(Ranking, "get_ranking_prompt",
                          lambda self: ("p", {"score": "0-100 整數", "description": "d"})):
            ansr = asyncio.run(r.rankItem(self._item()))
        assert ansr["ranking"]["score"] == 30, "呼叫點沒接上上限，離題報導照樣拿 85 分"

    def test_offtopic_item_is_filtered_out_end_to_end(self):
        """端到端：離題報導（85 分 + different）被夾到 30 → 過不了 do() 的 >51 閘門。

        這條測的是**使用者看不看得到它**，不是中間數字。
        """
        import asyncio
        from unittest.mock import patch
        import core.ranking as rmod
        from core.ranking import Ranking

        verdicts = iter([("different", 85), ("same", 80)])

        async def _ask(prompt, ans_struc, level=None, query_params=None):
            sm, sc = next(verdicts)
            return {"score": sc, "subject_match": sm, "description": "ok"}

        r = self._ranking([self._item("http://a/1", "離題"), self._item("http://a/2", "切題")])
        with patch.object(rmod, "ask_llm", _ask), \
             patch.object(Ranking, "get_ranking_prompt",
                          lambda self: ("p", {"score": "0-100 整數", "description": "d"})):
            asyncio.run(r.do())
        names = [a["name"] for a in r.handler.final_ranked_answers]
        assert names == ["切題"], f"離題報導不該送到使用者面前：{names}"

    @pytest.mark.parametrize("relpath,func", [
        ("core/ranking.py", "rankItem"),
        ("methods/generate_answer.py", "rankItem"),
    ])
    def test_both_call_sites_invoke_the_clamp(self, relpath, func):
        """結構性檢查：兩個 rankItem 的函式體內都必須出現 clamp 呼叫。

        （structural，不是行為測試——generate_answer.rankItem 需要一整個
        NLWebHandler 才跑得起來，behaviour 由 core/ranking.py 那條端到端測試代表；
        這條負責「有人把某個呼叫點刪掉就會紅」。）
        """
        import ast
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        src = open(os.path.join(base, relpath), encoding="utf-8").read()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == func:
                body = ast.dump(node)
                assert "clamp_score_by_subject_match" in body, (
                    f"{relpath}::{func} 沒有套用主題同一性上限 —— 該出口漏接"
                )
                return
        raise AssertionError(f"{relpath} 找不到 {func}")


def test_statistics_ranking_prompt_is_still_unreachable():
    """釘住「Statistics 區 RankingPrompt 不可達」這個未改的前提。

    ranking.py 用 handler.item_type 查 prompt；sites.xml 十站 itemType 全是 Article，
    Statistics 區永遠查不到。哪天有人加了 Statistics 站，本測試轉紅 = 提醒該區的
    RankingPrompt 也要補同一條判準。
    """
    tree = ET.parse(os.path.join(CONFIG_DIR, "sites.xml"))
    item_types = {el.text.strip() for el in tree.getroot().iter("itemType") if el.text}
    assert item_types == {"Article"}, (
        f"sites.xml 出現非 Article itemType {item_types - {'Article'}}："
        "Statistics 區的 RankingPrompt 可能已可達，需補「主題同一性」判準"
    )
