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
import threading
import importlib
import sentry_sdk

from core.openai_http import keepalive_timeout_enabled


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
