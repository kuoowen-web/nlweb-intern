"""P1 批2 評分型別全鏈根解 — core/llm.ask_llm 數值欄位 coerce 單一收斂點測試。

背景（full-scan-2026-07 CORE-2 / AF-1 / MP-2 三席同抓）：
LLM ranking prompt 的 ans_struc 宣告 `"score": "0-100 整數"`，但這只是文字指示；
弱模型常回字串 `"70"`。三家 provider clean_response 契約漂移——唯 gemini.py:110-114
有 ad-hoc coerce（且只治純數字字串），openai/anthropic 零 coerce。preferred provider
= openai/anthropic 時字串 score 直流入 ranking → `'70'>59` / `sorted(mixed)` TypeError
（單件靜默丟 / 整批崩）。

根解：coerce 上移到 ask_llm 回傳前的單一收斂點，依 schema 欄位「描述字串」識別數值
欄位，宣告為數值的欄位若回應是字串則試轉（int 優先，含 strip；轉不動保留原值 +
logger.warning，不 silent、不丟件）。

紀律：一律 mock provider（patch _get_provider），絕不打真實 LLM。
"""
import pytest
from unittest.mock import AsyncMock, patch

from core.llm import ask_llm, LLMError, _coerce_numeric_fields, _is_numeric_field_desc


# ── 收斂機制底層：欄位描述 → 是否數值欄位判定 ──

class TestNumericFieldDetection:
    """_is_numeric_field_desc：依 schema 欄位『描述字串』判斷是否為數值欄位。

    真實 schema 欄位值是文字描述（`"0-100 整數"`），不是範例值（`70`），
    故機制依描述文字裡工程師寫好的型別意圖（整數/integer/數值/float）判定。
    """

    @pytest.mark.parametrize("desc", [
        "0-100 整數",                    # ranking.py RANKING_PROMPT
        "0-100 整數，相關程度評分",       # prompts.xml RankingPrompt
        "0-100 整數（加權平均）",         # prompts.xml final_score
        "integer between 0 and 100",     # whoRanking.py / tools.xml search 等
        "數值分數",
        "a float between 0 and 1",
        "浮點數",
        # R1 #3/#5 措辭 FN 閉合後的實際 config 措辭（防再漂回無型別詞）
        "integer, always 100 for conversation history",  # tools.xml conv_history
        "0.0-1.0 浮點數",                                 # prompts.xml confidence x2
        "產生的查詢數量（0-4 整數）",                       # prompts.xml query_count
    ])
    def test_numeric_descriptions_recognized(self, desc):
        assert _is_numeric_field_desc(desc) is True

    @pytest.mark.parametrize("desc", [
        "True or False",                          # 布林欄位不可當數值
        "2-3 句事實摘要，含具體細節",              # 純文字
        "改寫後的繁中查詢，若不需改寫則留空",       # 純文字
        "記憶內容，若無則留空",
        "項目簡短描述",
    ])
    def test_nonnumeric_descriptions_rejected(self, desc):
        assert _is_numeric_field_desc(desc) is False

    def test_bool_not_treated_as_numeric(self):
        """'True or False' 含 'or' 但絕不能被當數值欄位（會誤把布林轉數字）。"""
        assert _is_numeric_field_desc("True or False") is False

    def test_nested_dict_desc_is_noop(self):
        """作用域邊界（R1 #4）：巢狀 schema 節點（欄位值為 dict）不判數值、不遞迴——
        QueryUnderstanding time_range 之類巢狀欄位刻意跳過（JSON Schema 家族由 Pydantic 治）。"""
        assert _is_numeric_field_desc({"confidence": "0.0-1.0 浮點數"}) is False
        nested_schema = {"time_range": {"confidence": "0.0-1.0 浮點數"}}
        result = {"time_range": {"confidence": "0.9"}}
        out = _coerce_numeric_fields(result, nested_schema)
        assert out["time_range"]["confidence"] == "0.9", "巢狀不遞迴（刻意），原樣保留"


# ── _coerce_numeric_fields：對一個 result dict 依 schema 就地 coerce 數值欄位 ──

class TestCoerceNumericFields:
    RANK_SCHEMA = {"score": "0-100 整數", "description": "項目簡短描述"}

    def test_string_int_coerced(self):
        """宣告數值的欄位回字串純數字 → 轉 int。"""
        result = {"score": "70", "description": "ok"}
        out = _coerce_numeric_fields(result, self.RANK_SCHEMA)
        assert out["score"] == 70
        assert isinstance(out["score"], int)
        assert out["description"] == "ok"  # 非數值欄位不動

    def test_string_int_with_whitespace_stripped(self):
        """含前後空白的數字字串 → strip 後轉 int。"""
        out = _coerce_numeric_fields({"score": "  85 "}, self.RANK_SCHEMA)
        assert out["score"] == 85

    def test_already_int_untouched(self):
        """已是 int → 保持不變（不重複轉）。"""
        out = _coerce_numeric_fields({"score": 70}, self.RANK_SCHEMA)
        assert out["score"] == 70
        assert isinstance(out["score"], int)

    def test_float_string_coerced_to_number(self):
        """`"0.7"` 之類小數字串 → 轉為數值（float，不因 int 優先而遺失小數）。"""
        schema = {"weight": "a float between 0 and 1"}
        out = _coerce_numeric_fields({"weight": "0.7"}, schema)
        assert out["weight"] == 0.7
        assert isinstance(out["weight"], float)

    def test_score_string_that_is_int_prefers_int(self):
        """int 優先：`"70"` 轉成 int 70 而非 float 70.0。"""
        out = _coerce_numeric_fields({"score": "70"}, self.RANK_SCHEMA)
        assert isinstance(out["score"], int)
        assert out["score"] == 70

    def test_uncoercible_preserved_with_warning(self):
        """邊界：`"70分"` 轉不動 → 保留原值（不歸 0、不丟件）+ logger.warning。

        本專案用自訂 async logger（get_configured_logger，走背景 thread queue），
        pytest caplog 抓不到；故直接 patch core.llm.logger.warning 驗『不 silent』。
        """
        with patch("core.llm.logger.warning") as mock_warn:
            out = _coerce_numeric_fields({"score": "70分"}, self.RANK_SCHEMA)
        assert out["score"] == "70分"  # 保留原值，下游既有防禦處理
        assert mock_warn.called, "轉不動必須 log warning，不可 silent"
        # warning 訊息含欄位名或原值，供診斷
        warn_args = mock_warn.call_args
        assert any("70分" in str(a) or "score" in str(a) for a in warn_args.args), \
            f"warning 應含欄位/原值線索：{warn_args.args}"

    def test_fullwidth_digits_coerced(self):
        """全形數字（`"７０"`）：Python int() 支援 Unicode 十進位數字 → 直接轉 int 70。

        親驗（Python 3.11）：int('７０')==70（int() 吃全形/Unicode 數字）。故全形分數被
        正確轉為 int、不保留字串——比 findings 原設想的『轉不動保留原值』更好（全形 70
        語義就是 70），且不炸不丟、無需 warning。
        """
        with patch("core.llm.logger.warning") as mock_warn:
            out = _coerce_numeric_fields({"score": "７０"}, self.RANK_SCHEMA)
        assert out["score"] == 70
        assert isinstance(out["score"], int)
        assert not mock_warn.called, "成功 coerce 不應 warn"

    def test_truly_uncoercible_mixed_string(self):
        """真正轉不動（`"高分"` 純中文、`"0.7abc"` 混雜）→ 保留原值 + warning。"""
        with patch("core.llm.logger.warning") as mock_warn:
            out = _coerce_numeric_fields({"score": "高分"}, self.RANK_SCHEMA)
        assert out["score"] == "高分"
        assert mock_warn.called
        with patch("core.llm.logger.warning") as mock_warn2:
            out2 = _coerce_numeric_fields({"score": "0.7abc"}, self.RANK_SCHEMA)
        assert out2["score"] == "0.7abc"
        assert mock_warn2.called

    def test_empty_schema_noop(self):
        """schema 為 {} → 無欄位聲明 → 完全 no-op（既有 ask_llm {} schema 相容）。"""
        result = {"foo": "bar", "n": "5"}
        out = _coerce_numeric_fields(result, {})
        assert out == {"foo": "bar", "n": "5"}  # 原封不動

    def test_nonnumeric_field_string_untouched(self):
        """非數值欄位即使值是數字字串也不動（description 欄位不 coerce）。"""
        schema = {"description": "項目簡短描述"}
        out = _coerce_numeric_fields({"description": "123"}, schema)
        assert out["description"] == "123"  # 保持字串

    def test_missing_field_no_crash(self):
        """schema 宣告的欄位在 result 缺席 → 不炸。"""
        out = _coerce_numeric_fields({"description": "ok"}, self.RANK_SCHEMA)
        assert out["description"] == "ok"

    def test_none_value_preserved(self):
        """數值欄位值為 None → 保留（不試轉 None）。"""
        out = _coerce_numeric_fields({"score": None}, self.RANK_SCHEMA)
        assert out["score"] is None


# ── ask_llm 收斂點整合：provider 回字串 score → ask_llm 回傳前已 coerce ──

def _mk_provider(return_value):
    prov = AsyncMock()
    prov.get_completion = AsyncMock(return_value=return_value)
    return prov


@pytest.mark.asyncio
async def test_ask_llm_coerces_string_score_at_return():
    """核心根解：provider（openai/anthropic 路徑）回 `{"score": "70"}` →
    ask_llm 回傳前 coerce → caller 收到 int 70（字串永不流入 ranking）。"""
    schema = {"score": "0-100 整數", "description": "項目簡短描述"}
    with patch("core.llm._get_provider",
               return_value=_mk_provider({"score": "70", "description": "ok"})):
        resp = await ask_llm("p", schema, provider="openai", timeout=5)
    assert resp["score"] == 70
    assert isinstance(resp["score"], int)


@pytest.mark.asyncio
async def test_ask_llm_uncoercible_score_preserved_no_crash():
    """provider 回 `{"score": "70分"}`（轉不動）→ ask_llm 不炸、保留原值（下游防禦）。"""
    schema = {"score": "0-100 整數"}
    with patch("core.llm._get_provider",
               return_value=_mk_provider({"score": "70分"})):
        resp = await ask_llm("p", schema, provider="openai", timeout=5)
    assert resp["score"] == "70分"  # 保留，不丟件


@pytest.mark.asyncio
async def test_ask_llm_empty_schema_result_untouched():
    """schema={} → coerce no-op → 既有 test_ask_llm_success_returns_plain_result 語義不破。"""
    with patch("core.llm._get_provider",
               return_value=_mk_provider({"foo": "bar"})):
        resp = await ask_llm("p", {}, provider="openai", timeout=5)
    assert resp == {"foo": "bar"}


@pytest.mark.asyncio
async def test_ask_llm_llmerror_not_coerced():
    """provider 失敗回 LLMError（falsy dict sentinel）→ coerce 必須跳過（不迭代污染）。"""
    async def _boom(*a, **k):
        raise RuntimeError("boom")
    prov = AsyncMock()
    prov.get_completion = AsyncMock(side_effect=_boom)
    with patch("core.llm._get_provider", return_value=prov):
        resp = await ask_llm("p", {"score": "0-100 整數"}, provider="openai", timeout=5)
    assert isinstance(resp, LLMError)
    assert not resp  # falsy 不變量不破


@pytest.mark.asyncio
async def test_ask_llm_multi_numeric_fields_all_coerced():
    """多數值欄位 schema（DR associator 型）→ 全部字串分數 coerce。"""
    schema = {
        "semantic_score": "0-100 整數",
        "keyword_score": "0-100 整數",
        "final_score": "0-100 整數（加權平均）",
        "description": "2-3 句事實摘要",
    }
    with patch("core.llm._get_provider", return_value=_mk_provider({
        "semantic_score": "80", "keyword_score": "60",
        "final_score": "72", "description": "摘要",
    })):
        resp = await ask_llm("p", schema, provider="openai", timeout=5)
    assert resp["semantic_score"] == 80
    assert resp["keyword_score"] == 60
    assert resp["final_score"] == 72
    assert resp["description"] == "摘要"
