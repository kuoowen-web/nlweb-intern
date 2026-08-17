# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Very simple wrapper around the various LLM providers.  

WARNING: This code is under development and may undergo changes in future releases.
Backwards compatibility is not guaranteed at this time.

"""

from typing import Optional, Dict, Any, Literal
from core.config import CONFIG
import asyncio
import re
import threading
import importlib
import sentry_sdk

from core.openai_http import keepalive_timeout_enabled
from core.llm_coerce_signal import report_coerce_failure


from misc.logger.logging_config_helper import get_configured_logger, LogLevel
logger = get_configured_logger("llm_wrapper")

# Cache for loaded providers
_loaded_providers = {}

def init():
    """Initialize LLM providers based on configuration."""
    # Get all configured LLM endpoints
    for endpoint_name, endpoint_config in CONFIG.llm_endpoints.items():
        llm_type = endpoint_config.llm_type
        if llm_type and endpoint_name == CONFIG.preferred_llm_endpoint:
            try:
                # Use _get_provider which will load and cache the provider
                _get_provider(llm_type)
                logger.info(f"Successfully loaded preferred {llm_type} provider")
            except Exception as e:
                # F8 = B (CEO ruling): a missing PREFERRED-provider SDK must fail the
                # service at startup, not silently warn and fall over on first request.
                # fail-hard is the safety net; the real defense is the deploy checklist
                # forcing `uv sync --extra <preferred>` (Phase 2/3 hard steps).
                raise RuntimeError(
                    f"Preferred LLM provider '{llm_type}' failed to load at startup: {e}. "
                    f"Its optional package is likely not installed — provision it with "
                    f"`uv sync --extra <name>` (the extra matching the preferred provider) "
                    f"and redeploy."
                ) from e

# Map of llm_type -> the uv extra that provides its SDK, for fail-loud messaging.
# Only providers with a real module on disk are listed. `openai` is a CORE
# dependency (always installed) so it has no extra and never triggers this path.
_llm_type_extras = {
    "anthropic": ("anthropic", "anthropic"),   # (import_name, extra_name)
    "gemini": ("google.genai", "gemini"),       # gemini.py does `from google import genai`
}


def _check_optional_provider_available(llm_type: str):
    """Fail loud if an optional provider's SDK is not importable.

    Replaces the old runtime `pip install` auto-installer. We never install at
    runtime — the dependency must be provisioned ahead of time via
    `uv sync --extra <name>`. Raising here (not silently degrading) satisfies the
    'no silent fail' rule.
    """
    entry = _llm_type_extras.get(llm_type)
    if entry is None:
        return  # core provider (e.g. openai) or unknown type handled downstream
    import_name, extra_name = entry
    try:
        # F7: use importlib.import_module, NOT __import__, for dotted names.
        # `__import__("google.genai")` returns the top-level `google` package and
        # can FALSELY succeed when `google` exists as a namespace package but
        # `genai` is absent — so it would not fail-loud. importlib.import_module
        # actually imports the `google.genai` submodule and raises ImportError if
        # genai is missing.
        importlib.import_module(import_name)
    except ImportError as e:
        raise ImportError(
            f"LLM provider '{llm_type}' requires an optional package that is not "
            f"installed ({import_name}). Install it with:\n"
            f"    uv sync --extra {extra_name}\n"
            f"(original import error: {e})"
        ) from e

def _get_provider(llm_type: str):
    """
    Lazily load and return the provider for the given LLM type.
    
    Args:
        llm_type: The type of LLM provider to load
        
    Returns:
        The provider instance
        
    Raises:
        ValueError: If the LLM type is unknown
    """
    # Return cached provider if already loaded
    if llm_type in _loaded_providers:
        return _loaded_providers[llm_type]

    # Import the appropriate provider module if not already loaded
    try:
        # Fail loud if an optional provider SDK is missing (no runtime pip install).
        # MUST be inside this try so the raised ImportError is caught by the existing
        # `except ImportError` below and converted to ValueError — preserving ask_llm's
        # error classification (F2: ImportError -> ValueError -> config_error).
        _check_optional_provider_available(llm_type)

        if llm_type == "openai":
            from llm_providers.openai import provider as openai_provider
            _loaded_providers[llm_type] = openai_provider
        elif llm_type == "anthropic":
            from llm_providers.anthropic import provider as anthropic_provider
            _loaded_providers[llm_type] = anthropic_provider
        elif llm_type == "gemini":
            from llm_providers.gemini import provider as gemini_provider
            _loaded_providers[llm_type] = gemini_provider
        else:
            raise ValueError(f"Unknown LLM type: {llm_type}")
            
        return _loaded_providers[llm_type]
    except ImportError as e:
        logger.error(f"Failed to import provider for {llm_type}: {e}")
        raise ValueError(f"Failed to load provider for {llm_type}: {e}")


# error_kind 三值集中定義（FIX-4 / Architect I-2）：producer（本檔 5 建構點）與
# consumer（agents/base.py、methods/deep_research.py）統一引用，禁裸字串字面散落。
# 新增第四個 kind 時：(a) 在此加常數 + 補進 ERROR_KIND 的 Literal、(b) 掃 consumer
# 看要不要分支——type checker 會在 Literal 不符時報錯，省掉 silent typo 風險。
ERROR_KIND_TIMEOUT = "timeout"               # asyncio.TimeoutError（呼叫逾時）
ERROR_KIND_PROVIDER_ERROR = "provider_error"  # provider 其他 exception
ERROR_KIND_CONFIG_ERROR = "config_error"      # provider/model config 缺失或未知 provider

ErrorKind = Literal["timeout", "provider_error", "config_error"]


class LLMError(dict):
    """
    LLM 呼叫失敗的型別化 sentinel。

    繼承 dict 且實例為空 → falsy，與既有 27 個 caller 的
    `if not response:` / `(resp or {}).get(...)` / `isinstance(resp, dict)`
    判斷相容（行為等價於原本回傳的 None / 空 dict，不翻轉任何 caller 語意）。

    額外帶 error_kind 供需要分型的 caller（base.py legacy）讀取，
    禁止再把失敗誤標成「empty response」。

    error_kind（值集中於模組級常數 ERROR_KIND_*，見上）：
      - ERROR_KIND_TIMEOUT:        asyncio.TimeoutError（呼叫逾時）
      - ERROR_KIND_PROVIDER_ERROR: provider 其他 exception
      - ERROR_KIND_CONFIG_ERROR:   provider/model config 缺失或未知 provider
    """
    def __init__(self, error_kind: ErrorKind, detail: str = ""):
        super().__init__()
        self.error_kind = error_kind
        self.detail = detail

    def __bool__(self):
        # AR round 1（Codex #1）：顯式釘死 falsy 不變量。
        # 即使未來有人誤把 error_kind/detail 存成 dict item（len>0），
        # bool(LLMError(...)) 仍 False → 27-caller 的 `if not response:` 相容不破。
        return False

    def __repr__(self):
        return f"LLMError(kind={self.error_kind!r}, detail={self.detail!r})"


def llm_failure_detail(response: Any) -> Optional[str]:
    """LLMError sentinel → "kind: detail" 描述；非 LLMError → None。

    票 2026-08-04-e K-19/K-18/K-06：三種 error_kind 對只寫 `if not x` 的 caller
    完全等價（timeout 被記成 empty response）。ranking 家族的 empty-response
    分支用本 helper 恢復 log 層分型；改 raise 語義是另一張架構票的事。
    """
    if isinstance(response, LLMError):
        return f"{response.error_kind}: {response.detail}"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 數值欄位 coerce 單一收斂點（full-scan-2026-07 CORE-2 / AF-1 / MP-2 三層根解）
#
# 問題：ranking / DR associator 等 prompt 的 ans_struc 宣告 `"score": "0-100 整數"`，
# 但那只是**文字指示**；弱模型（level=low）常回字串 `"70"`。三家 provider clean_response
# 契約漂移——唯 gemini.py 有 ad-hoc coerce（且只治純數字字串），openai/anthropic 零 coerce。
# preferred provider=openai/anthropic 時字串 score 直流入 consumer：
#   - ranking.py:171 `'70'>59`（rankItem try 內 → 單件靜默丟）
#   - ranking.py:388-389 `sorted(mixed)`（do() try 外 → 整批 TypeError 崩）
#   - mmr / whoRanking / nlweb_client 同型比較/排序點
# 逐點加 coerce 是治標；根在「provider 契約不一致」。故 coerce 上移到此單一收斂點
# （ask_llm 回傳前），consumer 收到的數值欄位保證已 int/float 化。
#
# schema 聲明機制：真實 schema 欄位值是**文字描述**（`"0-100 整數"`）而非範例值（`70`），
# 故無法「依範例值型別推斷」。改依描述字串裡工程師寫好的型別意圖（整數/integer/數值/
# 浮點/float/number/小數）判定數值欄位——這是最小可行機制，不新增 schema 格式、不動
# 任何 prompt 資產。布林欄位（`"True or False"`）不含數值關鍵詞，自然被排除（且顯式防護）。
# ─────────────────────────────────────────────────────────────────────────────

# 數值型別意圖關鍵詞（涵蓋 repo 內既有 score/final_score/*_score 欄位的中英描述）。
_NUMERIC_DESC_PATTERN = re.compile(
    r"整數|數值|浮點|小數|分數|評分|得分|\binteger\b|\bint\b|\bfloat\b|\bnumber\b|\bnumeric\b",
    re.IGNORECASE,
)
# 布林意圖關鍵詞：即使描述含數值字樣也不得當數值欄位（防「True or False」等誤判）。
_BOOL_DESC_PATTERN = re.compile(r"true\s+or\s+false|布林|boolean|\bbool\b", re.IGNORECASE)


def _is_numeric_field_desc(desc: Any) -> bool:
    """依 schema 欄位『描述字串』判斷該欄位是否宣告為數值欄位。

    只有值為字串描述時才判定；布林描述（'True or False'）顯式排除，避免把布林欄位
    的字串值誤轉成數字。
    """
    if not isinstance(desc, str):
        return False
    if _BOOL_DESC_PATTERN.search(desc):
        return False
    return bool(_NUMERIC_DESC_PATTERN.search(desc))


# 票 2026-08-04-e K-115：描述關鍵詞是唯一判準時，涵蓋面靠「還沒人這樣寫過」的
# 歷史巧合（refuter 七例全漏網）。補兩個獨立訊號：
#   (a) 欄位名 score 家族（repo 現有 20 個 flat 數值欄位中 19 個的命名形狀；
#       第 20 個 confidence 靠「浮點」描述關鍵詞命中，不依賴本訊號——🔧R1）
#   (b) 純數值範圍描述（全字串錨定——"2-3 句事實摘要" 內嵌範圍不得命中，
#       誤判方向＝非數值欄位值被 coerce **刪除**，比漏網更危險）
# ⚠ 刻意不加 \bscore\b 描述關鍵詞：whoRanking 的 query 欄位描述含
#   "(only if score > 70)"，加了會刪掉 query 字串值。
# 涵蓋面機械防線＝ tests/unit/core/test_numeric_schema_inventory.py（golden map，
# 新欄位不分類即紅）——本 pattern 不再獨自扛涵蓋面。
_NUMERIC_FIELD_NAME_PATTERN = re.compile(r"(?:^|_)score$", re.IGNORECASE)
_NUMERIC_RANGE_ONLY_DESC_PATTERN = re.compile(
    r"^\s*\d+(?:\.\d+)?\s*(?:-|~|–|—|到|至)\s*\d+(?:\.\d+)?\s*分?\s*$"
)


def _is_numeric_field(field: Any, desc: Any) -> bool:
    """欄位是否宣告為數值：布林描述優先排除；desc 非字串（巢狀節點）維持
    no-op 邊界（R1 #4 分工不變）；然後 欄位名 ∨ 純範圍 ∨ 描述關鍵詞。"""
    if not isinstance(desc, str):
        return False
    if _BOOL_DESC_PATTERN.search(desc):
        return False
    if isinstance(field, str) and _NUMERIC_FIELD_NAME_PATTERN.search(field):
        return True
    if _NUMERIC_RANGE_ONLY_DESC_PATTERN.match(desc):
        return True
    return bool(_NUMERIC_DESC_PATTERN.search(desc))


def _coerce_numeric_fields(result: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    """對 result 中被 schema 宣告為數值的欄位就地 coerce（int 優先，退 float）。

    行為契約：
      - schema 為空 / 非 dict → no-op（既有 `ask_llm(..., {})` 呼叫語義不破）。
      - result 非 dict（含 LLMError falsy sentinel）→ 原樣回，不迭代污染。
      - 欄位值已是 int/float/bool → 不動。
      - 欄位值是字串：strip 後試 int（純整數字串）→ 退 float（含小數點）；
        兩者皆失敗（'70分' / '高分' / '0.7abc'）→ **移除該欄位** + logger.warning
        + 品質訊號事件（票 2026-08-01-k）。不 silent、不歸 0、不丟件——欄位不存在
        使下游 `.get(k, default)` 走 default，TypeError 整批崩路徑消失。
        （全形數字如 '７０' 可被 Python int() 轉換，走成功分支不受影響。）
      - 欄位值為 None / 缺席 → 保留（不試轉）。

    作用域邊界（R1 #4 裁決，刻意設計）：
      - 只治**扁平描述字串 schema**（ranking / router tools.xml / detector 家族——
        欄位值為 `"0-100 整數"` 之類文字描述）。只掃 schema 頂層；欄位值為 dict/list
        的巢狀節點 `_is_numeric_field_desc` 回 False 自然跳過，**不做巢狀遞迴**。
      - JSON Schema 格式呼叫點（reasoning 家族 `{"type": "integer"}` 之類）由
        Pydantic model_validate 層負責轉型（親驗字串數字可被 Pydantic coerce），
        本 helper 對其 no-op 屬刻意分工，非涵蓋缺口。
    """
    if not isinstance(result, dict) or not isinstance(schema, dict) or not schema:
        return result

    for field, desc in schema.items():
        if not _is_numeric_field(field, desc):
            continue
        if field not in result:
            continue
        value = result[field]
        # bool 是 int 子類，但語義上不是要轉的數字欄位值；已是 int/float 亦不動。
        if isinstance(value, bool) or isinstance(value, (int, float)) or value is None:
            continue
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        coerced = None
        try:
            coerced = int(stripped)
        except (TypeError, ValueError):
            try:
                coerced = float(stripped)
            except (TypeError, ValueError):
                coerced = None
        if coerced is None:
            # 票 2026-08-01-k：轉不動 → **移除該欄位**，不保留原字串、不設 None。
            #
            # 為什麼是「移除」不是「設 None」：dict.get(k, default) 的 default 只在
            # key **不存在**時生效。key 存在而值為 None 時回傳 None，下游六個讀取點
            # （mmr.py:236,254,270 / whoRanking.py:171,172 / router.py:246 /
            #  generate_answer.py:155,438,491 / xgboost_ranker.py:154,177）照樣
            # `None > 50` TypeError——其中 whoRanking.py:171 與 generate_answer.py:438
            # 在 try **外**，會讓整批 query 的 ranking 全滅。移除 key 才讓那些
            # `.get(k, 0)` 真的走 default 分支，TypeError 路徑就此消失。
            #
            # ⚠ **用詞校正（land-diff R1 in-house，已逐行親驗；票 2026-08-04-e 再校）**：
            # 上列讀取點的 score **值層**皆為 `.get(k, default)` 形式，但 mmr.py 的
            # **容器層**（`r['ranking']`×5、`['name']`×2）曾為裸下標——已於票
            # 2026-08-04-e 改 `.get` chain 防禦形（缺欄→0/空名，同語義）。值層危險
            # 不在「沒寫 default」，而在「**寫了 default 卻被 present-but-None 打敗**」。
            # 這正是本修法選 `del` 而非設 `None` 的全部理由。真正的裸下標在別處
            # （ranking.py:228 / whoRanking.py:83,125 / generate_answer.py:965 /
            #  router.py:350-526），已逐條判可達性**全部安全**（heal-before-consume
            # 前置補值 / `_safe_score()==0` 被 `>59` 擋死 / router 重包新 dict）。
            #
            # 語義：把「未定義」正確表達成未定義。不是排序策略變更——欄位不存在時
            # 下游本來就當 0 處理（`_safe_score` 亦然）。
            #
            # ⚠ 這裡若有人改回保留原值或改成設 None，
            # tests/unit/core/test_llm_score_coercion.py::
            #   test_removed_field_survives_all_downstream_read_shapes 會紅
            #   （它逐一施加六個裸讀點的實際運算形狀）。
            #
            # 為什麼要 report_coerce_failure 而不只 logger.warning：warning 印在
            # server log ＝ 無人看得到，且該 log 不持久化（票 2026-08-01-j：
            # /app/logs 未掛 volume，redeploy 全滅）。訊號落 DB（guardrail_events）
            # 才留得住、才有巡檢可查。**本模組刻意不依賴 log 檔持久化。**
            logger.warning(
                "LLM numeric field '%s' returned non-coercible string %r "
                "(schema desc %r); removing field so downstream .get(k, default) "
                "yields its default instead of raising TypeError.",
                field, value, desc,
            )
            report_coerce_failure(field=field, raw_value=value, schema_desc=desc)
            del result[field]
        else:
            result[field] = coerced
    return result


async def ask_llm(
    prompt: str,
    schema: Dict[str, Any],
    provider: Optional[str] = None,
    level: str = "low",
    timeout: int = 60,
    query_params: Optional[Dict[str, Any]] = None,
    max_length: int = 512,
    *,
    _use_sdk_retry: bool = False,   # 內部旗標：high-tier(經 base.py layer1a)設 True → 走純 SDK retry 路徑
) -> Dict[str, Any]:
    """
    Route an LLM request to the specified endpoint, with dispatch based on llm_type.
    
    Args:
        prompt: The text prompt to send to the LLM
        schema: JSON schema that the response should conform to
        provider: The LLM endpoint to use (if None, use preferred endpoint from config)
        level: The model tier to use ('low' or 'high')
        timeout: Request timeout in seconds
        query_params: Optional query parameters for development mode provider override
        max_length: Maximum length of the response in tokens (default: 512)
        
    Returns:
        Parsed JSON response from the LLM
        
    Raises:
        ValueError: If the endpoint is unknown or response cannot be parsed
        TimeoutError: If the request times out
    """
    # Determine provider, with development mode override support
    provider_name = provider or CONFIG.preferred_llm_endpoint
    
    # In development mode, allow query param override
    if CONFIG.is_development_mode() and query_params:
        from core.utils.utils import get_param
        override_provider = get_param(query_params, "llm_provider", str, None)
        if override_provider:
            provider_name = override_provider
            logger.debug(f"Development mode: LLM provider overridden to {provider_name}")
        
        # Also allow level override in development mode
        override_level = get_param(query_params, "llm_level", str, None)
        if override_level:
            level = override_level
            logger.debug(f"Development mode: LLM level overridden to {level}")
    logger.debug(f"Initiating LLM request with provider: {provider_name}, level: {level}")
    logger.debug(f"Prompt preview: {prompt[:100]}...")
    logger.debug(f"Schema: {schema}")
    
    if provider_name not in CONFIG.llm_endpoints:
        error_msg = f"Unknown provider '{provider_name}'"
        logger.error(error_msg)
        return LLMError(ERROR_KIND_CONFIG_ERROR, error_msg)

    # Get provider config using the helper method
    provider_config = CONFIG.get_llm_provider(provider_name)
    if not provider_config or not provider_config.models:
        error_msg = f"Missing model configuration for provider '{provider_name}'"
        logger.error(error_msg)
        return LLMError(ERROR_KIND_CONFIG_ERROR, error_msg)

    # Get llm_type for dispatch
    llm_type = provider_config.llm_type
    logger.debug(f"Using LLM type: {llm_type}")

    model_id = getattr(provider_config.models, level)
    logger.debug(f"Using model: {model_id}")
    
    # Initialize variables for exception handling
    llm_type_for_error = llm_type

    try:

        # Get the provider instance based on llm_type
        try:
            provider_instance = _get_provider(llm_type)
            logger.debug(f"DEBUG: Using provider_name='{provider_name}', llm_type='{llm_type}', model_id='{model_id}'")
        except ValueError as e:
            error_msg = str(e)
            logger.error(error_msg)
            return LLMError(ERROR_KIND_CONFIG_ERROR, error_msg)
        
        # Simply call the provider's get_completion method without locking
        # Each provider should handle thread-safety internally
        logger.debug(f"Calling {llm_type} provider completion for endpoint {provider_name} with max_completion_tokens={max_length}")
        if keepalive_timeout_enabled() and _use_sdk_retry:
            # 收斂 high-tier 路徑：不包外層 wait_for，讓 get_completion 內的 httpx read timeout
            # + SDK retry 成為唯一 timeout 機制（retry 不被 asyncio 砍）。get_completion 失敗
            # 已回 LLMError（Task 3），直接上傳。
            result = await provider_instance.get_completion(
                prompt, schema, model=model_id, timeout=timeout, max_completion_tokens=max_length
            )
        else:
            # low-tier（flag-ON 但無 _use_sdk_retry）保留 asyncio 安全網保住 60s 不變量；
            # flag-OFF 走完全相同的舊路徑（行為逐字等價現狀）。
            result = await asyncio.wait_for(
                provider_instance.get_completion(prompt, schema, model=model_id, timeout=timeout, max_completion_tokens=max_length),
                timeout=timeout
            )
        logger.debug(f"{provider_name} response received, size: {len(str(result))} chars")
        # 數值欄位 coerce 單一收斂點（CORE-2 / AF-1 / MP-2 三層根解）：依 schema 宣告把
        # 弱模型回的字串分數 int/float 化，字串永不流入 ranking/mmr/whoRanking sort。
        # LLMError（provider 失敗 sentinel）不 coerce——其為錯誤而非合法結果，且空 dict
        # 迭代無意義；跳過保住 falsy 契約與型別分辨。
        if not isinstance(result, LLMError):
            result = _coerce_numeric_fields(result, schema)
        return result

    except asyncio.TimeoutError as e:
        timeout_msg = f"LLM call timed out after {timeout}s with provider {provider_name}"
        logger.error(timeout_msg)
        sentry_sdk.capture_exception(e)
        return LLMError(ERROR_KIND_TIMEOUT, timeout_msg)
    except Exception as e:
        error_msg = f"LLM call failed: {type(e).__name__}: {str(e)}"
        logger.error(f"Error with provider {provider_name}: {error_msg}")

        logger.log_with_context(
            LogLevel.ERROR,
            "LLM call failed",
            {
                "endpoint": provider_name,
                "llm_type": llm_type_for_error,
                "model": model_id,
                "level": level,
                "error_type": type(e).__name__,
                "error_message": str(e)
            }
        )

        sentry_sdk.capture_exception(e)
        return LLMError(ERROR_KIND_PROVIDER_ERROR, error_msg)


def get_available_providers() -> list:
    """
    Get a list of LLM providers that have their required API keys available.
    
    Returns:
        List of provider names that are available for use.
    """
    available_providers = []
    
    for provider_name, provider_config in CONFIG.llm_endpoints.items():
        # Check if provider config exists and has required fields
        if (provider_config and 
            hasattr(provider_config, 'api_key') and provider_config.api_key and 
            provider_config.api_key.strip() != "" and
            hasattr(provider_config, 'models') and provider_config.models and
            provider_config.models.high and provider_config.models.low):
            available_providers.append(provider_name)
    
    return available_providers
