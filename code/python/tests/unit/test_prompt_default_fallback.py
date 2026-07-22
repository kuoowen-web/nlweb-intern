# -*- coding: utf-8 -*-
"""Unit tests for find_prompt flag-gated fallback to Site id="default" (4a).

行為契約（plan D2-D5）：
- flag OFF（預設）：matched-site miss -> (None, None) + 負向快取（現行行為鎖定）。
- flag ON：matched-site miss -> 再搜 Site id="default"；命中回 default 版；雙 miss 照舊 (None, None)。
- 命中路徑（matched site 內存在 prompt）flag ON/OFF 皆不變（fallback 不劫持）。
- unmatched-site 的既有 all-sites fallback 與 flag 無關（partial-site 回歸不變量）。
- flag 為 startup-frozen（lru_cache）：運行中改 CONFIG 不生效，需 cache_clear 模擬重啟。
"""
import os
import sys
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import core.prompts as prompts

SYNTH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<root xmlns="http://nlweb.ai/base">
  <Site id="default">
    <Item>
      <Prompt ref="OnlyInDefault">
        <promptString>DEFAULT-ONLY-TEXT</promptString>
        <returnStruc>{"k": "v"}</returnStruc>
      </Prompt>
      <Prompt ref="InBoth">
        <promptString>DEFAULT-BOTH-TEXT</promptString>
        <returnStruc>{}</returnStruc>
      </Prompt>
      <Prompt ref="DupAcrossChildren">
        <promptString>ITEM-CHILD-VERSION</promptString>
        <returnStruc>{}</returnStruc>
      </Prompt>
    </Item>
    <Statistics>
      <Prompt ref="DupAcrossChildren">
        <promptString>STATISTICS-CHILD-VERSION</promptString>
        <returnStruc>{}</returnStruc>
      </Prompt>
    </Statistics>
  </Site>
  <Site id="all">
    <Item>
      <Prompt ref="InBoth">
        <promptString>ALL-BOTH-TEXT</promptString>
        <returnStruc>{}</returnStruc>
      </Prompt>
    </Item>
  </Site>
</root>
"""

ITEM_TYPE = "{http://nlweb.ai/base}Item"
STATISTICS_TYPE = "{http://nlweb.ai/base}Statistics"


@pytest.fixture()
def synth_roots(monkeypatch):
    """換上合成 prompt 樹 + 清空快取；teardown 由 monkeypatch 自動還原 + 再清快取。"""
    root = ET.fromstring(SYNTH_XML)
    monkeypatch.setattr(prompts, "prompt_roots", [root])
    prompts.cached_prompts.clear()
    prompts.prompt_default_fallback_enabled.cache_clear()
    yield
    prompts.cached_prompts.clear()
    prompts.prompt_default_fallback_enabled.cache_clear()


def _set_flag(monkeypatch, value: bool):
    """模擬「改 config + 重啟」：patch CONFIG helper 後清 lru_cache。"""
    monkeypatch.setattr(
        prompts.CONFIG, "is_prompt_default_fallback_enabled", lambda: value, raising=False
    )
    prompts.prompt_default_fallback_enabled.cache_clear()


def test_flag_off_matched_site_miss_returns_none(synth_roots, monkeypatch):
    """現行行為鎖定：flag OFF，site=all 缺 OnlyInDefault -> (None, None) + 負向快取。"""
    _set_flag(monkeypatch, False)
    result = prompts.find_prompt("all", ITEM_TYPE, "OnlyInDefault")
    assert result == (None, None)
    assert prompts.cached_prompts[("all", ITEM_TYPE, "OnlyInDefault")] == (None, None)


def test_flag_on_matched_site_miss_falls_back_to_default(synth_roots, monkeypatch):
    """flag ON：site=all 缺 OnlyInDefault -> 命中 default 版。"""
    _set_flag(monkeypatch, True)
    prompt_str, struc = prompts.find_prompt("all", ITEM_TYPE, "OnlyInDefault")
    assert prompt_str == "DEFAULT-ONLY-TEXT"
    assert struc == {"k": "v"}


def test_flag_on_matched_site_hit_not_hijacked(synth_roots, monkeypatch):
    """D4：matched site 內存在的 prompt，flag ON 仍命中 matched 版（:972 語義不變）。"""
    _set_flag(monkeypatch, True)
    prompt_str, _ = prompts.find_prompt("all", ITEM_TYPE, "InBoth")
    assert prompt_str == "ALL-BOTH-TEXT"


def test_flag_on_double_miss_returns_none(synth_roots, monkeypatch):
    """D5：matched site 與 default 都缺 -> 照舊 (None, None) + 負向快取。"""
    _set_flag(monkeypatch, True)
    result = prompts.find_prompt("all", ITEM_TYPE, "NowhereProm")
    assert result == (None, None)
    assert prompts.cached_prompts[("all", ITEM_TYPE, "NowhereProm")] == (None, None)


@pytest.mark.parametrize("flag_value", [False, True])
def test_unmatched_site_fallback_unchanged_by_flag(synth_roots, monkeypatch, flag_value):
    """partial-site 回歸不變量：site id 無匹配走既有 all-sites fallback，與 flag 無關。"""
    _set_flag(monkeypatch, flag_value)
    prompt_str, _ = prompts.find_prompt("cna", ITEM_TYPE, "OnlyInDefault")
    assert prompt_str == "DEFAULT-ONLY-TEXT"
    # document order：default 在前 -> InBoth 也是 default 版先命中（現行行為鎖定）
    prompts.cached_prompts.clear()
    prompt_str2, _ = prompts.find_prompt("cna", ITEM_TYPE, "InBoth")
    assert prompt_str2 == "DEFAULT-BOTH-TEXT"


def test_flag_on_default_site_miss_no_self_fallback(synth_roots, monkeypatch):
    """site='default' 自身 miss 不重複自搜（guard site != "default"）。"""
    _set_flag(monkeypatch, True)
    result = prompts.find_prompt("default", ITEM_TYPE, "NowhereProm")
    assert result == (None, None)


def test_flag_is_startup_frozen(synth_roots, monkeypatch):
    """D3：lru_cache 凍結——運行中改 CONFIG 不生效，cache_clear（=重啟）後才生效。"""
    _set_flag(monkeypatch, False)
    assert prompts.prompt_default_fallback_enabled() is False
    # 運行中翻 CONFIG，不清 cache -> 仍 False
    monkeypatch.setattr(
        prompts.CONFIG, "is_prompt_default_fallback_enabled", lambda: True, raising=False
    )
    assert prompts.prompt_default_fallback_enabled() is False
    # 模擬重啟
    prompts.prompt_default_fallback_enabled.cache_clear()
    assert prompts.prompt_default_fallback_enabled() is True


@pytest.mark.parametrize("flag_value", [False, True])
def test_last_match_wins_across_type_children(synth_roots, monkeypatch, flag_value):
    """B1 重構等價性鎖定：同一 Site 下 <Item> 與 <Statistics> 同名 prompt，
    item_type={ns}Statistics 時回 Statistics 版（後贏，last-match-wins）——
    _search_site_for_prompt 復刻現行 :285-295 語義，不得改為 first-match-wins。
    flag OFF/ON 皆須成立（此為 direct-hit path，不經 fallback 分支）。"""
    _set_flag(monkeypatch, flag_value)
    prompt_str, _ = prompts.find_prompt("default", STATISTICS_TYPE, "DupAcrossChildren")
    assert prompt_str == "STATISTICS-CHILD-VERSION"


def test_negative_cache_survives_runtime_flag_flip(synth_roots, monkeypatch):
    """Codex SF#4：D3 負向快取 key 不含 flag——OFF 期間寫入的 (None,None)
    在運行中翻 flag（不清 cache）後仍命中，唯有清 cached_prompts + cache_clear
    （=重啟）才復活 default。證明 startup-frozen 對負向快取的必要性。"""
    _set_flag(monkeypatch, False)
    assert prompts.find_prompt("all", ITEM_TYPE, "OnlyInDefault") == (None, None)
    assert prompts.cached_prompts[("all", ITEM_TYPE, "OnlyInDefault")] == (None, None)
    # 運行中翻 CONFIG 為 True 但不清 cache -> 負向快取仍命中
    monkeypatch.setattr(
        prompts.CONFIG, "is_prompt_default_fallback_enabled", lambda: True, raising=False
    )
    prompts.prompt_default_fallback_enabled.cache_clear()  # 即使 flag helper 已翻 True
    assert prompts.find_prompt("all", ITEM_TYPE, "OnlyInDefault") == (None, None)  # cache 仍舊
    # 模擬完整重啟：清 cached_prompts 才會走新 fallback
    prompts.cached_prompts.clear()
    prompt_str, _ = prompts.find_prompt("all", ITEM_TYPE, "OnlyInDefault")
    assert prompt_str == "DEFAULT-ONLY-TEXT"
