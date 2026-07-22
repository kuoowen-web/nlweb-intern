"""Bug 1：rerunState 抽取子集 + 從 research_report 重建（round-trip 強斷言）。"""
import json
import pytest
from types import SimpleNamespace


def _fake_state():
    """模擬 phase 1 完成後的 ResearchState 關鍵欄位。source_map key 是 int（citation id）。

    [R5 剝欄位] source_map value 覆蓋兩種真實 item 格式：
    - item1 = dict（含 description/site/datePublished + 應被剝掉的肥欄位 vector/schema_json）。
    - item2 = **6-element list-row**（真實 retriever 回的格式，Task 5 Step 1 實測確認）：
      `[url, schema_json_str, title, source, vector, scores_dict]`——description/datePublished
      **藏在 item[1] 的 schema_json 字串裡**（不是頂層），schema_json 全文是 payload 大頭（~2.6KB）。
    build_rerun_state_subset 必須對 list-row 先 json.loads(item[1]) 抽 description/datePublished，
    剝成精簡 dict（只留 url/title/description/site/datePublished 5 欄），丟掉 schema_json 全文其餘 +
    vector + scores——這是 CEO 拍板選項一「剝欄位」的核心，payload 238KB → ~15-30KB。
    """
    item1 = {
        "url": "u1", "title": "文章一", "description": "內容一", "site": "來源A",
        "datePublished": "2024-07-25T00:00:00",
        # 以下是應被剝掉的肥欄位（rerun 不讀）
        "vector": [0.1] * 100, "score": 0.7, "extra_bloat": "x" * 3000,
    }
    item2 = [
        "u2",                                                    # [0] url
        json.dumps({"@type": "NewsArticle", "headline": "文章二",  # [1] schema_json（description 藏這裡）
                    "articleBody": "內容二" * 500,                  # 肥大全文
                    "datePublished": "2024-07-20T00:00:00"}, ensure_ascii=False),
        "文章二",                                                  # [2] title
        "來源B",                                                  # [3] source/site
        [0.2] * 100,                                             # [4] vector（應剝掉）
        {"vector_score": 0.65, "bm25_score": 0.0},              # [5] scores（應剝掉）
    ]
    return SimpleNamespace(
        query="台灣再生能源",
        mode="discovery",
        temporal_context={"is_temporal_query": False},
        enable_kg=True,
        enable_web_search=False,
        formatted_context="## 當前時間\n[1] 來源A - 文章一\n內容一\n",
        source_map={1: item1, 2: item2},          # int key；value 分別是 dict / list-row
        current_context=[item1, item2],
        items=[item1, item2, {"url": "u3"}],        # items 比 current_context 多（含被 pass-through 但…）
        query_id="query_1699999999999",             # [R3 修訂 S2-new] 綁定產生此 state 的 query_id
    )


def test_build_subset_excludes_full_items_keeps_count():
    """抽子集不含全量 items（省 payload），只留 items_count 供 stat 重建。"""
    from reasoning.orchestrator import build_rerun_state_subset
    subset = build_rerun_state_subset(_fake_state())
    assert "items" not in subset, "rerunState 不該存全量 items（phase 2-4 只用 len）"
    assert subset["items_count"] == 3
    assert subset["query"] == "台灣再生能源"
    assert subset["mode"] == "discovery"
    assert subset["enable_kg"] is True
    assert subset["formatted_context"].startswith("## 當前時間")
    # source_map 存成 str-key（JSON 相容）
    assert set(subset["source_map"].keys()) == {"1", "2"}
    # [R2 修訂 S1] 選項 A：build 不單存 current_context（省 payload、restore 從 source_map 重建）。
    # 鎖死此判準——防「靠 Task 5 之後記得刪」的雙倍 payload land（test 不會 fail 的漏洞）。
    assert "current_context" not in subset, "選項 A：不單存 current_context（restore 從 source_map 依 cid 排序重建）"
    # [R3 修訂 S2-new] build 綁定 query_id，供 DB fallback 驗 session↔query_id 對齊
    assert subset["query_id"] == "query_1699999999999", "rerunState 必須綁定產生它的 query_id"

    # === [R5 剝欄位 — CEO 拍板選項一] source_map value 必須剝成精簡 dict，只留 5 欄 ===
    # 防偽核心：payload 大頭 = 每個 item 的 schema_json 全文/articleBody 肥文 + vector + scores，
    # rerun 從不讀它們（欄位窮舉：url/link,title/name,description/articleBody,site,datePublished）。
    _ALLOWED = {"url", "title", "description", "site", "datePublished"}
    for cid, slim in subset["source_map"].items():
        assert isinstance(slim, dict), f"source_map[{cid}] 剝後必須是 dict（統一格式，restore 後走 dict 分支）"
        extra = set(slim.keys()) - _ALLOWED
        assert not extra, f"source_map[{cid}] 只能有 5 欄，多出肥欄位：{extra}"
        # 明確斷言肥欄位被剝掉（防「只是沒斷言到」的假綠）
        assert "vector" not in slim and "score" not in slim and "scores" not in slim
        assert "articleBody" not in slim, "articleBody 全文肥欄位必須剝掉（description 已抽出）"
    # dict item（cid=1）：頂層直接抽
    s1 = subset["source_map"]["1"]
    assert s1["url"] == "u1" and s1["title"] == "文章一"
    assert s1["description"] == "內容一" and s1["site"] == "來源A"
    assert s1["datePublished"] == "2024-07-25T00:00:00"
    # list-row item（cid=2）：description/datePublished 從 item[1] schema_json json.loads 抽出（關鍵！）
    s2 = subset["source_map"]["2"]
    assert s2["url"] == "u2", "list-row url = item[0]"
    assert s2["title"] == "文章二", "list-row title = item[2]"
    assert s2["site"] == "來源B", "list-row site = item[3]"
    assert s2["description"].startswith("內容二"), "list-row description 從 item[1] schema_json 抽出（不能丟）"
    assert s2["datePublished"] == "2024-07-20T00:00:00", "list-row datePublished 從 schema_json 抽出"


def test_restore_roundtrip_source_map_keys_are_int():
    """從 research_report 內層重建 → source_map key 強斷言轉回 int（G17 陷阱）。"""
    from reasoning.orchestrator import build_rerun_state_subset, restore_rerun_state_from_report
    subset = build_rerun_state_subset(_fake_state())
    # 模擬進 DB JSONB → 讀回（json.dumps/loads 一趟，str key 保持 str）
    research_report = {"report": "...", "rerunState": json.loads(json.dumps(subset, ensure_ascii=False))}
    restored = restore_rerun_state_from_report(research_report)
    assert restored is not None
    # 強斷言：source_map key 必須是 int（不是 str）——禁止容錯 int(k)，這裡就是防偽 gate
    for k in restored["source_map"].keys():
        assert isinstance(k, int), f"source_map key 必須轉回 int，得到 {type(k)}"
    assert restored["source_map"][1]["url"] == "u1"
    # current_context 重建（從 source_map 依 cid 排序）+ 內容一致
    assert isinstance(restored["current_context"], list)
    assert restored["current_context"][0]["url"] == "u1"
    # items 重建成長度 items_count 的 placeholder（phase 2-4 只用 len）
    assert len(restored["items"]) == 3
    # [R3 修訂 S2-new] query_id round-trip：restore 帶回 query_id 供上層驗 session↔query_id 對齊
    assert restored["query_id"] == "query_1699999999999"


def test_slim_item_degradation_paths_log_warning():
    """[AR R1 should-fix，Codex+in-house 獨立同抓] _slim_item 降級不可 silent——
    corrupt schema_json 與未知型別兩路徑必須留 warning（可降級但必須有明確訊息）。
    註：repo logger 是 async queue 包裝（caplog 跨 thread 不可靠）→ 直接 mock 驗呼叫。"""
    from unittest.mock import patch
    import reasoning.orchestrator as _orch
    from reasoning.orchestrator import _slim_item

    # (a) list-row 的 item[1] 非法 JSON → description/datePublished 降空，必須 warn
    with patch.object(_orch, 'logger') as mock_log:
        out = _slim_item(["u1", "{corrupt json", "標題", "來源A", None, {}])
        assert out["description"] == "" and out["url"] == "u1"
        assert mock_log.warning.called, "corrupt schema_json 降級必須 logger.warning（不可 silent fail）"
    # (b) 非 dict/list 未知型別 → 空 5 欄殼，必須 warn
    with patch.object(_orch, 'logger') as mock_log2:
        out2 = _slim_item(12345)
        assert out2 == {'url': '', 'title': '', 'description': '', 'site': '', 'datePublished': ''}
        assert mock_log2.warning.called, "未知型別降級必須 logger.warning（不可 silent fail）"


def test_restore_returns_none_when_no_rerunstate():
    """research_report 沒有 rerunState 內層（如 session 14 舊 row）→ 回 None（不假裝有）。"""
    from reasoning.orchestrator import restore_rerun_state_from_report
    assert restore_rerun_state_from_report({"report": "只有報告沒有 rerunState"}) is None
    assert restore_rerun_state_from_report({}) is None
    # {}／空內層 truthiness 坑（模組陷阱 #3）：rerunState 是空 dict / 無 formatted_context → 不算有效
    assert restore_rerun_state_from_report({"rerunState": {}}) is None
    assert restore_rerun_state_from_report({"rerunState": {"source_map": {}}}) is None


def test_restore_corrupt_source_map_key_returns_none_not_crash():
    """[R2 修訂 S4] DB 存了非數字 source_map key（corrupt/舊資料）→ int(k) 不炸、回 None。

    優雅降級（不 silent fail：log warning）：上層走「無有效 rerunState → 400」正常路徑，
    不讓 ValueError 冒泡把預期的 400 變 500。
    """
    from reasoning.orchestrator import restore_rerun_state_from_report
    corrupt = {
        "rerunState": {
            "formatted_context": "[1] x\n",
            "source_map": {"not-a-number": {"url": "u1"}},   # corrupt key
            "items_count": 1,
        }
    }
    # 不拋例外、回 None（優雅降級）
    assert restore_rerun_state_from_report(corrupt) is None


def test_restore_corrupt_source_map_not_dict_returns_none_not_crash():
    """[R3 修訂 S4-補] source_map 不是 dict（corrupt/舊資料，如 list/str）→ .items() 不炸、回 None。

    原 R2 只包 int(k)，若 source_map 是 list → .items() 拋 AttributeError（未被涵蓋）會 500。
    S4-補：型別檢查納入無效判定 predicate（isinstance dict）→ 判無效回 None，不炸。
    """
    from reasoning.orchestrator import restore_rerun_state_from_report
    corrupt = {
        "rerunState": {
            "formatted_context": "[1] x\n",
            "source_map": [{"url": "u1"}],   # corrupt：list 不是 dict
            "items_count": 1,
        }
    }
    assert restore_rerun_state_from_report(corrupt) is None


def test_restore_corrupt_items_count_returns_none_not_crash():
    """[R3 修訂 S4-補] items_count 非數字（corrupt/舊資料，如 "abc"）→ int(...) 不炸、回 None。

    原本 int(rs.get('items_count', ...)) 裸露在 return 段外 → ValueError 冒泡把 400 變 500。
    S4-補：items_count parse 納入同一 try/except（涵蓋整類 corrupt）→ 判無效回 None。
    """
    from reasoning.orchestrator import restore_rerun_state_from_report
    corrupt = {
        "rerunState": {
            "formatted_context": "[1] x\n",
            "source_map": {"1": {"url": "u1"}},
            "items_count": "abc",   # corrupt：非數字
        }
    }
    assert restore_rerun_state_from_report(corrupt) is None
