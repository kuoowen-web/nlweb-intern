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
from core.prompts import find_prompt, fill_prompt
from core.utils.json_repair_utils import safe_parse_llm_json

# TypeAgent: instructor library for structured LLM output
_instructor_available = False
_instructor_client = None
_instructor_client_lock = asyncio.Lock()

try:
    import instructor
    from instructor import Mode
    from openai import AsyncOpenAI
    _instructor_available = True
except ImportError:
    pass


async def _get_instructor_client():
    """
    Lazily initialize and return the instructor-wrapped OpenAI client.
    Thread-safe singleton pattern.
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
            base_client = AsyncOpenAI(api_key=provider_config.api_key)
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
    """
    logger = get_configured_logger("typeagent")

    if not _instructor_available:
        raise ValueError("instructor library not available. Install with: pip install instructor")

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
        # max_retries is passed directly as an integer to instructor
        result = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                response_model=response_model,
                messages=[{"role": "user", "content": prompt}],
                max_retries=max_retries,
                max_tokens=max_tokens  # Critical: limit response length to prevent timeout
            ),
            timeout=timeout
        )

        logger.info(f"TypeAgent: Successfully generated {response_model.__name__}")
        return result, 0, False  # retry_count not tracked without custom callback

    except asyncio.TimeoutError:
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
