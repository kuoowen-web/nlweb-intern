"""
Base class for reasoning agents providing common LLM interaction patterns.

Includes TypeAgent integration for structured LLM output with automatic retry
and validation using the instructor library.
"""

import asyncio
from typing import Dict, Any, Optional, Type, Tuple
from pydantic import BaseModel, ValidationError
from misc.logger.logging_config_helper import get_configured_logger
from core.llm import ask_llm, LLMError, ERROR_KIND_TIMEOUT
from core.config import CONFIG
from core.openai_http import (
    make_keepalive_async_client, make_sliced_timeout,
    keepalive_timeout_enabled, get_read_timeout, get_write_timeout, get_max_retries,
)
from core.prompts import find_prompt, fill_prompt
from core.utils.json_repair_utils import safe_parse_llm_json

# TypeAgent: instructor library for structured LLM output
_instructor_available = False
_instructor_client = None
_instructor_client_lock = asyncio.Lock()
# openai_keepalive_timeout 為 startup-only flag，已在 core 層凍結（lru_cache，啟動讀一次後不變）。
# instructor client（singleton）建立時讀的 flag 與 generate_structured call site 讀的 flag 永遠
# 一致 —— 不可能 mismatch，故不需要 round-3 的 fail-loud 偵測（記 flag + 比對 + raise）。
# round-4 三家一致指出該 fail-loud 的 raise 會被上層 broad except 吞成 silent fallback + 有
# TOCTOU race；凍結根解後這兩個 blocker 消失。


def _outer_deadline(timeout: float) -> float:
    """should-fix 1（AR round-3）：flag-ON 終極死線 = timeout + 寬鬆 buffer。

    背景：C1 補傳 timeout= 後，instructor 內部 `stop_after_delay(timeout)` 只在 attempt 之間檢查、
    **不搶占 in-flight 的單次 call**。極端情況：第一次 ~timeout 後拋 ValidationError → tenacity
    檢查 (~timeout < timeout 邊界) 發起重試 → 第二次又 ~timeout → 總 wall-clock 近 2×timeout，
    打破 F1 拆掉的原 wait_for(timeout) 絕對保證。

    修法（方向 a）：保留一個**極寬鬆**的最外層 asyncio.wait_for(deadline) 當終極死線（flag-ON 也包），
    buffer 給足讓正常 retry 鏈（最壞 ~2×timeout）不被砍，但補回絕對 wall-clock 上限防無限飄。
    deadline = 2×timeout + 30：嚴格大於合理膨脹上限（2×timeout），+30s 留 backoff/排程 overhead margin。
    """
    return 2 * timeout + 30


try:
    import instructor
    from instructor import Mode
    from instructor.core import InstructorRetryException  # C2: 親驗頂層 `from instructor import` 會 ImportError
    import openai  # 給 openai.APITimeoutError / openai.APIConnectionError（F4a/C2 taxonomy）
    from openai import AsyncOpenAI
    _instructor_available = True
except ImportError:
    pass


async def _get_instructor_client():
    """
    Lazily initialize and return the instructor-wrapped OpenAI client.
    Thread-safe singleton pattern.

    ⚠ startup-only flag 契約（AR round-4 根解）：openai_keepalive_timeout 為**啟動時凍結**的
    flag（lru_cache），運行中改 CONFIG 不生效需**重啟 server**。client 建立時讀的 flag 與 call
    site 讀的 flag 已在 core 層凍結保證一致 —— 不可能 mismatch，故不需要 round-3 的 fail-loud。
    """
    global _instructor_client
    logger = get_configured_logger("typeagent")

    if not _instructor_available:
        logger.error("TypeAgent: instructor library not imported")
        return None

    async with _instructor_client_lock:
        if _instructor_client is None:
            # Get API key from config
            provider_config = CONFIG.llm_endpoints.get("openai")
            if not provider_config:
                logger.error("TypeAgent: 'openai' endpoint not found in config_llm.yaml")
                logger.error(f"TypeAgent: Available endpoints: {list(CONFIG.llm_endpoints.keys())}")
                return None
            if not provider_config.api_key:
                import os
                env_key = os.environ.get("OPENAI_API_KEY")
                logger.error(
                    "TypeAgent: OpenAI API key not set. "
                    f"provider_config.api_key={repr(provider_config.api_key)}, "
                    f"os.environ OPENAI_API_KEY={'set ('+env_key[:8]+'...)' if env_key else 'NOT SET'}"
                )
                return None

            logger.info(f"TypeAgent: Initializing instructor client with OpenAI (key starts with: {provider_config.api_key[:8]}...)")
            # Create instructor-wrapped async client with RESPONSES_TOOLS mode
            # This mode supports GPT-5.1 Responses API (client.responses.create)
            # keepalive 無條件套；flag-ON 時加 client-level 分項 timeout + SDK retry（收斂路徑）。
            # flag 已在 core 層凍結（startup-only），與 call site 讀的值保證一致。
            if keepalive_timeout_enabled():
                http_client = make_keepalive_async_client(
                    timeout=make_sliced_timeout(read=get_read_timeout(), write=get_write_timeout())
                )
                base_client = AsyncOpenAI(
                    api_key=provider_config.api_key,
                    http_client=http_client,
                    max_retries=get_max_retries(),
                )
            else:
                base_client = AsyncOpenAI(
                    api_key=provider_config.api_key,
                    http_client=make_keepalive_async_client(),  # 純 keepalive，零行為改變
                )
            _instructor_client = instructor.from_openai(base_client, mode=Mode.RESPONSES_TOOLS)
            logger.info("TypeAgent: Using Mode.RESPONSES_TOOLS for GPT-5.1 Responses API")

    return _instructor_client


async def generate_structured(
    prompt: str,
    response_model: Type[BaseModel],
    max_retries: int = 3,
    model: Optional[str] = None,
    timeout: int = 120,
    max_tokens: int = 16384
) -> Tuple[BaseModel, int, bool]:
    """
    TypeAgent core function: Generate structured LLM output with automatic validation.

    Uses instructor library to:
    - Automatically retry on validation errors
    - Feed error messages back to LLM for correction
    - Guarantee return of valid Pydantic object

    Args:
        prompt: The text prompt to send to the LLM
        response_model: Pydantic model class for validation
        max_retries: Maximum retry attempts (instructor handles internally)
        model: Model ID to use (defaults to config high model)
        timeout: Request timeout in seconds
        max_tokens: Maximum tokens in response (default: 16384, same as legacy method)

    Returns:
        Tuple of (validated_model, retry_count, fallback_used)
        - validated_model: The validated Pydantic model instance
        - retry_count: Number of retries needed (0 if first attempt succeeded)
        - fallback_used: Always False when using instructor

    Raises:
        ValueError: If instructor is not available or client initialization fails
        ValidationError: If max retries exceeded
        TimeoutError: If request times out

    ⚠ startup-only flag 契約（B1 根解）：openai_keepalive_timeout 為**啟動時生效**，運行中改變需
        **重啟 server**，不支援熱切換。由 keepalive_timeout_enabled() 的 lru_cache 啟動凍結保證——
        client 建立與此 call site 讀的是同一個 frozen 值，不可能 mismatch（取代 round-3 的 fail-loud
        偵測機制，從根本消除不一致的可能）。
    """
    logger = get_configured_logger("typeagent")

    if not _instructor_available:
        raise ValueError("instructor library not available. Install with: pip install instructor")

    # B1 根解：flag 由 lru_cache 啟動凍結，client 建立與下面 flag-ON 分支讀的是同一個 frozen 值，
    # 不可能 mismatch（不需 fail-loud 偵測）。
    client = await _get_instructor_client()
    if client is None:
        raise ValueError("Failed to initialize instructor client. Check OpenAI API key configuration.")

    # Determine model
    if model is None:
        provider_config = CONFIG.llm_endpoints.get("openai")
        if provider_config and provider_config.models:
            model = provider_config.models.high
        else:
            model = "gpt-4o"  # Fallback default

    logger.info(f"TypeAgent: Generating structured output with {response_model.__name__}")
    logger.debug(f"TypeAgent: Using model {model}, max_retries={max_retries}")

    try:
        # Use instructor's automatic retry and validation
        # max_retries here = instructor VALIDATION retry（schema 正確性），非 SDK HTTP retry。
        if keepalive_timeout_enabled():
            # 收斂路徑（F1）：拆外層 asyncio.wait_for，讓 client 層 httpx read timeout + SDK HTTP retry
            # 控制每次 HTTP；instructor validation retry 維持（不是 timeout 機制，不勒死）。
            # 避免「外層 wait_for(90) 勒死 validation×3 ⊗ SDK×2」failure shape。
            #
            # C1 修補（AR round-2 收尾：B1/C1/S1）：必須補傳 `timeout=timeout` 給 instructor create()。
            # 補傳 timeout= 後該 kwarg **同時做兩件事**（兩者合力，缺一不可，措辞勿夸大成「能搶占任何卡住協程」）：
            #   (1) 截斷 retry budget：親驗 instructor 1.15.3 source（.venv/.../instructor/v2/core/retry.py:399-408）—
            #       instructor 只在 `kwargs.get("timeout")` 是 int/float 時，才把 `stop_after_delay(timeout)`
            #       併入它內部 tenacity AsyncRetrying 的 stop_condition（與 stop_after_attempt 取 OR）。
            #       這只停 retry progression（attempt 之間的累積時間），**不能搶占單一 in-flight 的 hung call**。
            #   (2) 截斷單次 in-flight call：instructor patch wrapper **沒有 pop 掉 timeout**（親驗
            #       .venv/.../instructor/v2/core/patch.py 的 _create_async_wrapper 只 pop cache/cache_ttl/
            #       autodetect_images），timeout 原樣傳給底層 OpenAI SDK create(timeout=...) → 成為 httpx
            #       per-request read timeout → 這才是真正截斷「單一卡住傳輸」的機制。
            # 加上 base.py 現狀的 AsyncOpenAI(api_key=...) 完全沒設 client-level timeout（預設 600s），
            # 拆掉外層 asyncio.wait_for 後若不傳 timeout=，retry budget 只剩 stop_after_attempt（次數），
            # 單次 read 又無 per-request 上限 → 慢速滴漏可長時間 hang，比現狀更糟（這正是 C1 要修的）。
            #
            # should-fix 1（AR round-3，方向 a）：C1 的 timeout= 仍維持（截斷 retry budget + per-request
            # read），但 stop_after_delay 不搶占 in-flight 單次 call → 最壞 wall-clock 可膨脹到 ~2×timeout，
            # 打破 F1 拆掉的原 wait_for(timeout) 絕對保證。故補回一個**極寬鬆**的最外層 asyncio.wait_for
            # 當終極死線（deadline = _outer_deadline(timeout) = 2×timeout+30，給足 buffer 不砍正常 retry），
            # 補回絕對 wall-clock 上限。注意：buffer 寬鬆是刻意的 —— F1 拆掉緊掐的 wait_for(timeout) 是為了
            # 不勒死 instructor validation×SDK 巢狀 retry；這裡的寬鬆死線只在 C1 機制全失效的 runaway 時兜底。
            result = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    response_model=response_model,
                    messages=[{"role": "user", "content": prompt}],
                    max_retries=max_retries,        # instructor validation retry，維持
                    max_tokens=max_tokens,
                    timeout=timeout,                # C1：instructor 裝 stop_after_delay(timeout) + 原樣下傳成 httpx read timeout
                ),
                timeout=_outer_deadline(timeout),   # should-fix 1：寬鬆終極死線（絕對 wall-clock 上限）
            )
        else:
            # 現狀（外層 asyncio.wait_for，flag-OFF rollback 用，原封不動）。
            result = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    response_model=response_model,
                    messages=[{"role": "user", "content": prompt}],
                    max_retries=max_retries,
                    max_tokens=max_tokens
                ),
                timeout=timeout
            )

        logger.info(f"TypeAgent: Successfully generated {response_model.__name__}")
        return result, 0, False  # retry_count not tracked without custom callback

    except InstructorRetryException as e:
        # C2 核心修（AR round-2 前）：instructor **不會原樣拋 APITimeoutError**。
        # 親驗 instructor 1.15.3 source（.venv/.../instructor/v2/core/retry.py:418-532）：
        # 整個 retry loop 包在外層 try，任何 API call 例外（含 transport timeout、含內部 tenacity
        # 的 stop 觸發）最終一律被外層 `except Exception` 包成
        # `InstructorRetryException(str(last_exception)) from last_exception`（retry.py:524-532），
        # 原始 transport timeout 在 `__cause__`。所以 F4a 原本直接 catch
        # `(openai.APITimeoutError, openai.APIConnectionError)` **永遠 miss**（實際冒出的是
        # InstructorRetryException），timeout 會落到 generic `except Exception` 被當 unexpected error。
        #
        # 親驗的觸發鏈（已用與 instructor 相同的 tenacity AsyncRetrying 設定 round-trip 跑過驗證）：
        #   - 沒到 timeout 上限、transport timeout（APITimeoutError）：非 retryable type，tenacity
        #     立刻 reraise → 外層包成 InstructorRetryException，__cause__ = APITimeoutError。
        #   - 傳了 timeout= 後 stop_after_delay(timeout) 命中：只在 retryable parse/validation error
        #     的重試流程中才會被檢查；tenacity（reraise=True）`raise self.last_attempt.result()` 拋出
        #     最後一次 attempt 的例外（通常是 ValidationError），仍被 instructor 外層 try 接住 →
        #     **還是** InstructorRetryException（__cause__ = 該 ValidationError）。
        #   - 結論：generate_structured 表面冒出的**永遠是 InstructorRetryException**，
        #     **不會**出現裸 tenacity `RetryError`、也**不會**出現裸 `openai.APITimeoutError`。
        #     故 C2 只需 catch InstructorRetryException 並 unwrap `e.__cause__` 判斷是否 transport timeout。
        cause = e.__cause__
        if isinstance(cause, (openai.APITimeoutError, openai.APIConnectionError)):
            # transport timeout / NAT drop 被 instructor 包裝。維持 F4a 原意：明確分型成 TimeoutError，
            # 讓上層當 timeout 處理（caller 仍會 fallback legacy，但語義正確且可觀測）。
            logger.error(
                f"TypeAgent: transport timeout/connection error (wrapped by instructor): "
                f"{type(cause).__name__}: {cause}"
            )
            raise TimeoutError(f"TypeAgent transport timeout: {type(cause).__name__}: {cause}")
        # 非 transport 的 instructor retry 耗盡（如 schema validation 連續失敗）：維持原 generic 語義
        # re-raise（不假裝成 timeout）。caller 仍會 fallback legacy。
        logger.error(f"TypeAgent: instructor retries exhausted: {type(e).__name__}: {e}")
        raise

    except (openai.APITimeoutError, openai.APIConnectionError) as e:
        # 防禦性保留：理論上 instructor 不會裸拋 transport timeout（一律包成 InstructorRetryException，
        # 見上），但保留此 catch 以防 (a) 未來 instructor 版本改變包裝行為、(b) 拆外層 wait_for 後若有
        # 任何繞過 instructor retry loop 的直呼路徑裸拋。語義與上面 unwrap 後一致：分型成 TimeoutError。
        logger.error(f"TypeAgent: transport timeout/connection error (unwrapped): {type(e).__name__}: {e}")
        raise TimeoutError(f"TypeAgent transport timeout: {type(e).__name__}: {e}")

    except asyncio.TimeoutError:
        # flag-OFF：外層 asyncio.wait_for(timeout) fire（緊掐 timeout，現狀路徑）。
        # flag-ON（should-fix 1）：外層寬鬆終極死線 asyncio.wait_for(_outer_deadline(timeout)) fire
        #   —— 僅 runaway（C1 的 stop_after_delay + per-request read 全失效）才會走到，屬絕對兜底。
        logger.error(f"TypeAgent: Request timed out after {timeout}s")
        raise TimeoutError(f"TypeAgent request timed out after {timeout} seconds")

    except ValidationError as e:
        logger.error(f"TypeAgent: Validation failed after {max_retries} retries: {e}")
        raise

    except Exception as e:
        logger.error(f"TypeAgent: Unexpected error: {type(e).__name__}: {e}")
        raise


class BaseReasoningAgent:
    """
    Abstract base class for reasoning agents.

    Provides common LLM interaction pattern with retry logic,
    timeout handling, and error management.
    """

    def __init__(
        self,
        handler: Any,
        agent_name: str,
        timeout: int = 120,  # Doubled: 60 -> 120 for GPT-5.1
        max_retries: int = 3
    ):
        """
        Initialize base reasoning agent.

        Args:
            handler: The request handler with LLM configuration
            agent_name: Name of the agent (for logging)
            timeout: Timeout in seconds for LLM calls
            max_retries: Maximum number of retry attempts for parse errors
        """
        self.handler = handler
        self.agent_name = agent_name
        self.timeout = timeout
        self.max_retries = max_retries
        self.logger = get_configured_logger(f"reasoning.{agent_name}")
        # 防禦：config timeout 若被誤設 ≤10，inner_timeout(=timeout-10) 會變負/零，
        # asyncio.wait_for 立即 timeout 形成 silent fail。明確 warn（不 raise，降級可跑）。
        if timeout <= 10:
            self.logger.warning(
                f"{agent_name} timeout={timeout}s <= 10s — inner_timeout 將退回 80% "
                f"({int(timeout * 0.8)}s)；請檢查 config 是否誤設過小的 timeout。"
            )

    async def ask(
        self,
        prompt_name: str,
        custom_vars: Optional[Dict[str, Any]] = None,
        level: str = "high"
    ) -> Dict[str, Any]:
        """
        Ask LLM using a named prompt template.

        Args:
            prompt_name: Name of the prompt in prompts.xml (e.g., "AnalystAgentPrompt")
            custom_vars: Dictionary of variables to fill in the prompt
            level: LLM quality level ("high" or "low")

        Returns:
            Parsed JSON response from LLM

        Raises:
            TimeoutError: If LLM call exceeds timeout
            ValueError: If prompt not found or max retries exceeded
        """
        # Find prompt template
        prompt_template = find_prompt(prompt_name, site="reasoning")
        if not prompt_template:
            raise ValueError(f"Prompt '{prompt_name}' not found in prompts.xml")

        # Fill prompt with custom variables
        filled_prompt = fill_prompt(prompt_template, custom_vars or {})

        # Retry loop for parse errors
        for attempt in range(self.max_retries):
            try:
                # Call LLM with timeout
                self.logger.info(f"{self.agent_name} calling LLM (attempt {attempt + 1}/{self.max_retries})")

                response = await asyncio.wait_for(
                    ask_llm(
                        filled_prompt,
                        schema={},
                        level=level,
                        query_params=getattr(self.handler, 'query_params', {})
                    ),
                    timeout=self.timeout
                )

                self.logger.info(f"{self.agent_name} received response")
                return response

            except asyncio.TimeoutError:
                self.logger.error(f"{self.agent_name} LLM call timed out after {self.timeout}s")
                raise TimeoutError(f"LLM call timed out after {self.timeout} seconds")

            except (ValueError, KeyError) as e:
                # Parse error - retry
                self.logger.warning(
                    f"{self.agent_name} parse error (attempt {attempt + 1}/{self.max_retries}): {e}"
                )
                if attempt == self.max_retries - 1:
                    # Last attempt failed
                    self.logger.error(f"{self.agent_name} max retries exceeded")
                    raise ValueError(f"Max retries exceeded for {prompt_name}: {e}")

                # Wait before retry (exponential backoff)
                await asyncio.sleep(2 ** attempt)

            except Exception as e:
                # Unexpected error - don't retry
                self.logger.error(f"{self.agent_name} unexpected error: {e}")
                raise

        # Should not reach here
        raise ValueError(f"Failed to get response for {prompt_name}")

    def _is_typeagent_enabled(self) -> bool:
        """Check if TypeAgent is enabled in configuration."""
        typeagent_config = CONFIG.reasoning_params.get("typeagent", {})
        config_enabled = typeagent_config.get("enabled", False)

        self.logger.info(
            f"{self.agent_name} TypeAgent check: config_enabled={config_enabled}, "
            f"instructor_available={_instructor_available}"
        )

        if config_enabled and not _instructor_available:
            self.logger.warning(
                f"{self.agent_name} TypeAgent is enabled in config but instructor library is not available. "
                "Install with: pip install instructor"
            )
            return False

        return config_enabled and _instructor_available

    async def call_llm_validated(
        self,
        prompt: str,
        response_schema: Type[BaseModel],
        level: str = "high"
    ) -> Tuple[BaseModel, int, bool]:
        """
        Call LLM with Pydantic validation.

        This method calls the LLM with a direct prompt string (not a template)
        and validates the response against a Pydantic schema.

        When TypeAgent is enabled, uses instructor library for automatic
        validation and retry. Falls back to legacy method if TypeAgent fails
        or is disabled.

        Args:
            prompt: Direct prompt string (not template name)
            response_schema: Pydantic model class for validation
            level: LLM quality level ("high" or "low")

        Returns:
            Tuple of (validated_model, retry_count, fallback_used)
            - validated_model: The validated Pydantic model instance
            - retry_count: Number of retries needed (for analytics)
            - fallback_used: True if legacy method was used

        Raises:
            ValidationError: If max retries exceeded
            TimeoutError: If LLM call exceeds timeout
        """
        # Try TypeAgent first if enabled
        if self._is_typeagent_enabled():
            try:
                self.logger.info(
                    f"{self.agent_name} using TypeAgent for {response_schema.__name__}"
                )

                # Get model from config
                typeagent_config = CONFIG.reasoning_params.get("typeagent", {})
                max_retries = typeagent_config.get("max_retries", self.max_retries)

                result, retry_count, _ = await generate_structured(
                    prompt=prompt,
                    response_model=response_schema,
                    max_retries=max_retries,
                    timeout=self.timeout
                )

                self.logger.info(
                    f"{self.agent_name} TypeAgent success for {response_schema.__name__} "
                    f"(retries: {retry_count})"
                )
                return result, retry_count, False

            except Exception as e:
                self.logger.warning(
                    f"{self.agent_name} TypeAgent failed, falling back to legacy: {e}"
                )
                # Fall through to legacy method

        # Legacy method (fallback or TypeAgent disabled)
        return await self._legacy_call_llm_validated(prompt, response_schema, level)

    async def _legacy_call_llm_validated(
        self,
        prompt: str,
        response_schema: Type[BaseModel],
        level: str = "high"
    ) -> Tuple[BaseModel, int, bool]:
        """
        Legacy LLM call with Pydantic validation (fallback method).

        This is the original implementation with manual retry logic,
        JSON repair, and exponential backoff.

        Args:
            prompt: Direct prompt string (not template name)
            response_schema: Pydantic model class for validation
            level: LLM quality level ("high" or "low")

        Returns:
            Tuple of (validated_model, retry_count, fallback_used)

        Raises:
            ValidationError: If max retries exceeded
            TimeoutError: If LLM call exceeds timeout
        """
        retry_count = 0
        response = None

        for attempt in range(self.max_retries):
            try:
                # Call LLM
                self.logger.info(
                    f"{self.agent_name} [legacy] calling LLM with {response_schema.__name__} "
                    f"validation (attempt {attempt + 1}/{self.max_retries})"
                )

                if keepalive_timeout_enabled():
                    # 收斂路徑：拆掉 layer1a 外層 wait_for + RSN-2 inner timeout。改走純 SDK retry
                    # （_use_sdk_retry=True → ask_llm 不包 layer2 wait_for → httpx read timeout + SDK
                    # retry 成為唯一機制，retry 不被 asyncio 砍）。RSN-2 的「inner<outer」防禦在
                    # 收斂模型不再適用（沒有雙層 asyncio 了），由 httpx read timeout 取代。
                    response = await ask_llm(
                        prompt,
                        schema={},
                        level=level,
                        timeout=self.timeout,  # 傳給 get_completion（flag-ON 下 get_completion 不用它做 wait_for，但保留簽名相容）
                        query_params=getattr(self.handler, 'query_params', {}),
                        max_length=16384,
                        _use_sdk_retry=True,
                    )
                else:
                    # 現狀（RSN-2 雙層 asyncio，flag-OFF rollback 用，原封不動）。
                    # RSN-2: Inner timeout must be smaller than outer timeout
                    # so inner fires first and outer is only a safety net
                    inner_timeout = self.timeout - 10 if self.timeout > 10 else int(self.timeout * 0.8)
                    response = await asyncio.wait_for(
                        ask_llm(
                            prompt,
                            schema={},  # Schema enforcement via Pydantic post-validation
                            level=level,
                            timeout=inner_timeout,  # Inner timeout fires first
                            query_params=getattr(self.handler, 'query_params', {}),
                            max_length=16384  # Large buffer for research outputs
                        ),
                        timeout=self.timeout  # Outer timeout as safety net
                    )

                # Log raw response for debugging
                self.logger.info(f"{self.agent_name} raw LLM response type: {type(response)}")
                self.logger.debug(f"{self.agent_name} raw LLM response: {response}")

                # --- LLMError sentinel 分型（先於 empty 檢查）---
                # core/llm.ask_llm 失敗時回 LLMError（帶 error_kind），不可再被
                # 誤標成「empty response」。timeout 類錯誤不 retry（重試只會再撞同一
                # 預算的牆，徒增延遲與成本）；provider/config error 帶型別 raise。
                if isinstance(response, LLMError):
                    if response.error_kind == ERROR_KIND_TIMEOUT:
                        self.logger.error(
                            f"{self.agent_name} [legacy] ask_llm timed out: {response.detail}"
                        )
                        raise TimeoutError(
                            f"{self.agent_name} LLM call timed out: {response.detail}"
                        )
                    # provider_error / config_error：明確型別，不 retry，不誤標 empty
                    self.logger.error(
                        f"{self.agent_name} [legacy] ask_llm failed "
                        f"(kind={response.error_kind}): {response.detail}"
                    )
                    raise RuntimeError(
                        f"{self.agent_name} LLM call failed "
                        f"(kind={response.error_kind}): {response.detail}"
                    )

                # Check if response is empty (genuine empty, 非 LLMError)
                if not response or (isinstance(response, dict) and len(response) == 0):
                    raise ValueError(
                        f"LLM returned empty response. This usually indicates an error in the LLM provider. "
                        f"Check logs above for LLM error messages."
                    )

                # Parse and validate
                if isinstance(response, dict):
                    validated = response_schema.model_validate(response)
                elif isinstance(response, str):
                    # Response is JSON string - try direct parse first
                    try:
                        validated = response_schema.model_validate_json(response)
                    except (ValidationError, ValueError) as parse_error:
                        # Direct parse failed - try repair
                        self.logger.debug(f"Direct JSON parse failed, attempting repair: {parse_error}")
                        repaired = safe_parse_llm_json(response)
                        if repaired:
                            validated = response_schema.model_validate(repaired)
                        else:
                            raise ValueError("Failed to parse or repair JSON response")
                else:
                    raise ValueError(f"Unexpected response type: {type(response)}")

                self.logger.info(
                    f"{self.agent_name} [legacy] response validated against {response_schema.__name__}"
                )
                return validated, retry_count, True  # fallback_used = True

            except ValidationError as e:
                retry_count = attempt + 1
                self.logger.error(
                    f"{self.agent_name} [legacy] validation failed "
                    f"(attempt {attempt+1}/{self.max_retries}): {e}"
                )
                self.logger.error(f"Failed response content: {response}")

                # Try JSON repair before giving up
                if isinstance(response, str):
                    self.logger.info(f"{self.agent_name} attempting JSON repair on string response")
                    repaired = safe_parse_llm_json(response)
                    if repaired:
                        try:
                            validated = response_schema.model_validate(repaired)
                            self.logger.info(
                                f"{self.agent_name} [legacy] validation successful after JSON repair"
                            )
                            return validated, retry_count, True

                        except ValidationError as repair_error:
                            self.logger.debug(f"Validation still failed after repair: {repair_error}")

                if attempt == self.max_retries - 1:
                    # Last attempt - raise error
                    self.logger.error(
                        f"{self.agent_name} [legacy] max retries exceeded for {response_schema.__name__}"
                    )
                    raise
                # Exponential backoff
                await asyncio.sleep(2 ** attempt)

            except asyncio.TimeoutError:
                self.logger.error(
                    f"{self.agent_name} [legacy] LLM call timed out after {self.timeout}s"
                )
                raise TimeoutError(f"LLM call timed out after {self.timeout} seconds")

            except Exception as e:
                # Unexpected error
                self.logger.error(f"{self.agent_name} [legacy] unexpected error: {e}", exc_info=True)
                raise

        # Should not reach here
        raise ValueError(f"Max retries exceeded for {response_schema.__name__}")
