# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""P1 批2 評分型別全鏈根解 — ranking 消費層防偽整合測試（TH-3 涵蓋盲區）。

背景（full-scan-2026-07 CORE-2 / AF-1 / MP-2）：
LLM 回字串 score `"70"` 時，preferred provider=openai/anthropic 走 ask_llm 若不 coerce
→ ranking 兩層落點崩：
  - rankItem :171 `ranking.get("score",0) > EARLY_SEND_THRESHOLD(59)` 在 try 內 → 單件靜默丟
  - do() :388-389 `[r ... if r['ranking']['score'] > 51]` + `sorted(...)` 在 try 外 → 整批 TypeError 崩

TH-3 盲區：既有唯一 rankItem 測試 _fake_ask_llm 恆回整數 score，不注入字串。
本檔補「字串 score 注入」防偽——ask_llm coerce 上移後，字串分數在 ask_llm 回傳前已轉 int，
rankItem/do() 收到的永遠是 int，不再崩不再丟。

紀律：一律 mock ask_llm，絕不打真實 LLM。這裡 mock 的是 ask_llm（收斂點下游），
驗「經過收斂點 coerce 後的 ranking 消費行為」——搭配 test_llm_score_coercion.py 驗
「收斂點本身 coerce 正確」，兩層合起來即三層根解在 provider→ask_llm→ranking 的閉環。
"""

import os
import sys
import asyncio
import threading
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import core.ranking as ranking_mod
from core.ranking import Ranking
from core.llm import _coerce_numeric_fields


RANK_SCHEMA = {"score": "0-100 整數", "description": "項目簡短描述"}


class _Handler:
    def __init__(self):
        self.required_item_type = None
        self.generate_mode = 'list'  # not 'unified' → avoid batch send path
        self.query_params = {}
        self.query = "test query"
        self.site = "test_site"          # not 'all'/'nlws' → skip sites message
        self.item_type = "Item"
        self.connection_alive_event = threading.Event()
        self.connection_alive_event.set()
        self.pre_checks_done_event = asyncio.Event()
        self.pre_checks_done_event.set()
        self.final_ranked_answers = []
        # No query_id → analytics blocks skipped via hasattr.


def _make_ranking(items):
    r = object.__new__(Ranking)
    r.ranking_type = Ranking.REGULAR_TRACK
    r.ranking_type_str = "REGULAR_TRACK"
    r.handler = _Handler()
    r.level = "low"
    r.items = items
    r.EARLY_SEND_THRESHOLD = 59
    r.NUM_RESULTS_TO_SEND = 10
    r.num_results_sent = 0
    r.rankedAnswers = []
    r._sent_title_keys = set()
    return r


def _string_score_ask_llm(score_value):
    """模擬 coerce 上移後的 ask_llm：provider 回字串 score，ask_llm 依 schema coerce 後回傳。

    直接復用真實 _coerce_numeric_fields，故此測試也守住『收斂點被繞過就會紅』——
    若哪天有人把 coerce 從 ask_llm 拔掉，這裡的 int 化就消失、下游 sort 復崩。
    """
    async def _inner(prompt, ans_struc, level=None, query_params=None):
        raw = {"score": score_value, "description": "ok"}
        return _coerce_numeric_fields(raw, ans_struc)
    return _inner


def _make_dict_item(url, title, desc="d"):
    return {
        'url': url, 'title': title, 'site': 'S',
        'schema_json': f'{{"description":"{desc}"}}',
        'retrieval_scores': {}, 'vector': None,
    }


# ── rankItem 單件：字串 score 經 coerce 後不再靜默丟（AF-1 症狀根治）──

def test_rankitem_string_score_not_dropped():
    """provider 回字串 `"70"` → coerce → rankItem 正常回 ansr（不因 '70'>59 崩被吞）。"""
    r = _make_ranking([])
    item = _make_dict_item('http://a/1', 'T')
    with patch.object(ranking_mod, 'ask_llm', _string_score_ask_llm("70")), \
         patch.object(Ranking, 'get_ranking_prompt', lambda self: ("prompt", RANK_SCHEMA)):
        ansr = asyncio.run(r.rankItem(item))
    assert ansr is not None, "字串 score 經 coerce 後 rankItem 不得回 None（靜默丟件）"
    assert ansr['ranking']['score'] == 70
    assert isinstance(ansr['ranking']['score'], int)


# ── do() 整批：混字串 score 經 coerce 後 sort 不崩（整批崩根治）──

def test_do_batch_sort_no_crash_with_coerced_scores():
    """多 item（含字串 score）經 ask_llm coerce → do() :388-389 filter/sort 不崩、正確排序。"""
    items = [_make_dict_item(f'http://a/{i}', f'T{i}') for i in range(4)]
    r = _make_ranking(items)

    # 每個 item 回不同字串 score，coerce 後應正確參與 sort
    scores = iter(["90", "55", "72", "60"])

    async def _varying_ask_llm(prompt, ans_struc, level=None, query_params=None):
        raw = {"score": next(scores), "description": "ok"}
        return _coerce_numeric_fields(raw, ans_struc)

    with patch.object(ranking_mod, 'ask_llm', _varying_ask_llm), \
         patch.object(Ranking, 'get_ranking_prompt', lambda self: ("prompt", RANK_SCHEMA)):
        # do() 不得拋 TypeError（'90'>51 / sorted(mixed)）
        asyncio.run(r.do())

    final = r.handler.final_ranked_answers
    # score>51 的四篇（90/55/72/60）全入，且降序
    got_scores = [a['ranking']['score'] for a in final]
    assert got_scores == [90, 72, 60, 55], f"排序錯誤或漏件：{got_scores}"
    assert all(isinstance(s, int) for s in got_scores)


def test_do_batch_filters_low_scores_correctly():
    """coerce 後 score<=51 的字串分數（如 `"40"`）被正確 filter 掉（非因型別誤判保留/崩）。"""
    items = [_make_dict_item(f'http://a/{i}', f'T{i}') for i in range(3)]
    r = _make_ranking(items)
    scores = iter(["80", "40", "52"])  # 40 應被 >51 濾掉；52 保留

    async def _ask(prompt, ans_struc, level=None, query_params=None):
        return _coerce_numeric_fields({"score": next(scores), "description": "ok"}, ans_struc)

    with patch.object(ranking_mod, 'ask_llm', _ask), \
         patch.object(Ranking, 'get_ranking_prompt', lambda self: ("prompt", RANK_SCHEMA)):
        asyncio.run(r.do())

    got = [a['ranking']['score'] for a in r.handler.final_ranked_answers]
    assert got == [80, 52], f"filter/排序錯：{got}"


# ── R1 #1/#2：rankItem 防線一致化 + do() 防線真測試（mutation-proof）──

def _make_ranked_answer(name, score, sent=False):
    """直接構造 rankedAnswers entry（繞過 rankItem），供 do() 防線直打測試。"""
    return {
        'url': f'http://a/{name}', 'site': 'S', 'name': name,
        'ranking': {'score': score, 'description': 'x'},
        'schema_object': {}, 'sent': sent, 'retrieval_scores': {},
    }


def test_do_filter_sort_survives_injected_nonnumeric_score():
    """R1 #2 防線真測試：殘值**直接注入 rankedAnswers**（繞過 rankItem）直打 do()
    :412-413 的 filter/sort → 不崩、殘值降位濾掉。

    R1 mutation 實錘教訓：經 rankItem 的注入到不了 do() 防線（殘值在 rankItem 比較點
    先被吞），舊測試形狀拔掉 _safe_score 仍綠 = 假綠。本測試繞過 rankItem 直塞
    rankedAnswers——拔掉 do() 層 _safe_score（改回裸讀）此測試必紅（已親驗 mutation）。
    """
    r = _make_ranking([])  # items=[] → 無 rankItem tasks，rankedAnswers 全靠注入
    r.rankedAnswers = [
        _make_ranked_answer('bad', '70分'),   # 殘值（coerce 轉不動保留）
        _make_ranked_answer('good1', 88),
        _make_ranked_answer('good2', 62),
        _make_ranked_answer('low', 30),        # 正常低分，應被 >51 濾掉
    ]
    # 不得 TypeError（'70分' > 51 / sorted(mixed)）
    asyncio.run(r.do())
    got = [a['ranking']['score'] for a in r.handler.final_ranked_answers]
    assert got == [88, 62], f"殘值應降位濾掉、正常件正常排序：{got}"


def test_do_final_send_path_survives_injected_nonnumeric_score():
    """do() 尾端補送路徑（:527-528 sort/filter + sendAnswers/shouldSend 比較）也不被殘值崩。"""
    r = _make_ranking([])
    r.rankedAnswers = [
        _make_ranked_answer('bad', '70分'),
        _make_ranked_answer('good1', 90),
    ]
    asyncio.run(r.do())  # 尾端 sorted(results, key=...) + good_results filter 不崩
    got = [a['ranking']['score'] for a in r.handler.final_ranked_answers]
    assert got == [90]


def test_rankitem_nonnumeric_residual_score_item_preserved():
    """R1 #1：coerce 保留的殘值（'70分'）進 rankItem :195 比較點 → 不 TypeError、
    item 保留、score 原值保留（description 還在）、比較走 _safe_score 降位 + log。

    修 #1 前現況：'70分' > 59 TypeError → rankItem except 吞/raise → 丟件（AF-1 症狀回歸）。
    """
    r = _make_ranking([])
    item = _make_dict_item('http://a/1', 'T')
    with patch.object(ranking_mod, 'ask_llm', _string_score_ask_llm("70分")), \
         patch.object(Ranking, 'get_ranking_prompt', lambda self: ("prompt", RANK_SCHEMA)), \
         patch.object(ranking_mod.logger, 'warning') as mock_warn:
        ansr = asyncio.run(r.rankItem(item))
    assert ansr is not None, "殘值不得讓 rankItem TypeError 丟件"
    assert ansr['ranking']['score'] == "70分", "原值保留（不歸 0 覆寫，比較點降位即可）"
    assert ansr['ranking']['description'] == "ok", "description 保留"
    assert mock_warn.called, "殘值降位必須 log（不 silent）"


def test_rankitem_residual_score_flows_to_do_without_crash():
    """R1 #1 端到端：殘值經 rankItem 進 rankedAnswers → do() 全鏈不崩、殘值降位濾掉。

    修 #1 後：殘值件不再被 rankItem TypeError 吞——3 件全進 rankedAnswers；
    do() 層 _safe_score 降位把殘值濾掉，final = [88, 62]。
    """
    items = [_make_dict_item(f'http://a/{i}', f'T{i}') for i in range(3)]
    r = _make_ranking(items)
    values = iter(["70分", "88", "62"])

    async def _ask(prompt, ans_struc, level=None, query_params=None):
        # 直接復用 coerce：'70分' 保留為字串（轉不動），'88'/'62' 轉 int
        return _coerce_numeric_fields({"score": next(values), "description": "ok"}, ans_struc)

    with patch.object(ranking_mod, 'ask_llm', _ask), \
         patch.object(Ranking, 'get_ranking_prompt', lambda self: ("prompt", RANK_SCHEMA)):
        asyncio.run(r.do())

    assert len(r.rankedAnswers) == 3, \
        f"殘值件必須保留在 rankedAnswers（不 TypeError 丟件）：{len(r.rankedAnswers)}"
    got = [a['ranking']['score'] for a in r.handler.final_ranked_answers]
    assert got == [88, 62], f"殘值降位濾掉、正常件正常排序：{got}"


# ── R2 尾修：dedup_by_title_and_source（helper 公開面）殘值輸入不炸 ──

def test_dedup_direct_call_survives_nonnumeric_score():
    """R2：直呼 dedup helper 帶殘值輸入 → 不 TypeError、比較語義正確（殘值視 0 落敗）。

    Codex 指出：dedup :48-49 裸讀比較原依賴「dedup 在 do() filter 後（殘值已濾）」的
    順序不變式；helper 是模組級公開面（unit test / 未來 caller 直呼），輸入混殘值即炸。
    過 _safe_score 消滅順序耦合。
    """
    from core.ranking import dedup_by_title_and_source
    results = [
        _make_ranked_answer('T1', '70分'),   # 殘值——同 key 比較時視 0
        _make_ranked_answer('T1', 88),        # 同 (name,site) key，數值分應勝出
        _make_ranked_answer('T2', 62),
    ]
    out = dedup_by_title_and_source(results)  # 不得 '70分' > 88 TypeError
    assert len(out) == 2
    kept_t1 = next(r for r in out if r['name'] == 'T1')
    assert kept_t1['ranking']['score'] == 88, "殘值視 0 落敗，數值分勝出"


def test_dedup_residual_first_then_numeric_wins():
    """R2 邊界：殘值先進 seen、數值後到 → 數值（>0）應取代殘值（降位 0）。"""
    from core.ranking import dedup_by_title_and_source
    results = [
        _make_ranked_answer('T1', 55),
        _make_ranked_answer('T1', '亂'),      # 殘值後到，視 0 不取代 55
    ]
    out = dedup_by_title_and_source(results)
    assert len(out) == 1
    assert out[0]['ranking']['score'] == 55


def test_do_integer_score_unchanged():
    """既有整數 score 路徑（多數模型/gemini）行為不變。"""
    items = [_make_dict_item(f'http://a/{i}', f'T{i}') for i in range(2)]
    r = _make_ranking(items)
    scores = iter([88, 66])

    async def _ask(prompt, ans_struc, level=None, query_params=None):
        return _coerce_numeric_fields({"score": next(scores), "description": "ok"}, ans_struc)

    with patch.object(ranking_mod, 'ask_llm', _ask), \
         patch.object(Ranking, 'get_ranking_prompt', lambda self: ("prompt", RANK_SCHEMA)):
        asyncio.run(r.do())

    got = [a['ranking']['score'] for a in r.handler.final_ranked_answers]
    assert got == [88, 66]
