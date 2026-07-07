# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
OpenAI wrapper for LLM functionality.

WARNING: This code is under development and may undergo changes in future releases.
Backwards compatibility is not guaranteed at this time.
"""

import os
import json
import re
import logging
import asyncio
from typing import Dict, Any, List, Optional
import sentry_sdk

import openai
from openai import AsyncOpenAI
from core.config import CONFIG
import threading
from misc.logger.logging_config_helper import get_configured_logger
from misc.logger.logger import LogLevel

from llm_providers.llm_provider import LLMProvider
from core.openai_http import keepalive_timeout_enabled
from core.llm import LLMError, ERROR_KIND_TIMEOUT, ERROR_KIND_PROVIDER_ERROR

logger = get_configured_logger("llm")


class ConfigurationError(RuntimeError):
    """
    Raised when configuration is missing or invalid.
    """
    pass


class OpenAIProvider(LLMProvider):
    """Implementation of LLMProvider for OpenAI API.

    ⚠ startup-only flag 契約（AR round-4 根解）：
    `openai_keepalive_timeout`（core.openai_http.keepalive_timeout_enabled）為 **啟動時凍結**
    的 flag（lru_cache，啟動讀一次後不再變），運行中改 CONFIG 不生效，需**重啟 server** 才套新值。

    client 是 singleton，首次初始化時讀 flag 決定 timeout/retry 形態。call site（get_completion）
    每次請求也讀同一個 flag 決定要不要拆 asyncio.wait_for。因 flag 已在 core 層凍結，「client
    建立時讀的值」與「call site 讀的值」**保證一致** —— 不可能 mismatch，故不需要 round-3 的
    fail-loud 偵測（記 flag + 比對 + raise）。round-4 三家一致指出該 fail-loud 有兩個 blocker：
    (1) raise 的 RuntimeError 被上層 broad except 吞成 silent fallback；(2) 比對與分支選擇間
    TOCTOU race。凍結根解後這兩個 blocker 整個消失。
    """

    _client_lock = threading.Lock()
    _client = None

    @classmethod
    def get_api_key(cls) -> str:
        """
        Retrieve the OpenAI API key from environment or raise an error.
        """
        provider_config = CONFIG.llm_endpoints["openai"]
        api_key = provider_config.api_key
        return api_key

    @classmethod
    def get_client(cls) -> AsyncOpenAI:
        """
        Configure and return an asynchronous OpenAI client.
        """
        from core.openai_http import (
            make_keepalive_async_client, make_sliced_timeout,
            keepalive_timeout_enabled, get_read_timeout, get_write_timeout, get_max_retries,
        )
        with cls._client_lock:
            if cls._client is None:
                api_key = cls.get_api_key()
                # flag 已在 core 層凍結（startup-only），這裡讀的值與 call site 永遠一致。
                if keepalive_timeout_enabled():
                    # 收斂路徑：httpx 分項 timeout(read 唯一生效) + SDK retry 當唯一 timeout 機制。
                    http_client = make_keepalive_async_client(
                        timeout=make_sliced_timeout(read=get_read_timeout(), write=get_write_timeout())
                    )
                    cls._client = AsyncOpenAI(
                        api_key=api_key, http_client=http_client, max_retries=get_max_retries(),
                    )
                else:
                    # 現狀 + 純加 keepalive（零行為改變）。max_retries=3 維持現值。
                    http_client = make_keepalive_async_client()  # 無 timeout → SDK 預設
                    cls._client = AsyncOpenAI(api_key=api_key, http_client=http_client, max_retries=3)
        return cls._client

    @classmethod
    def _build_messages(cls, prompt: str, schema: Dict[str, Any]) -> List[Dict[str, str]]:
        """
        Construct the system and user message sequence enforcing a JSON schema.
        """
        # When schema is empty, don't add schema constraints (let prompt define structure)
        if not schema:
            return [
                {
                    "role": "system",
                    "content": "You are an AI that only responds with valid JSON."
                },
                {"role": "user", "content": prompt}
            ]

        # Create a more explicit system message with the exact field names
        schema_fields = ", ".join([f'"{k}"' for k in schema.keys()])
        return [
            {
                "role": "system",
                "content": (
                    f"You are an AI that only responds with valid JSON. "
                    f"CRITICAL: Your response MUST contain EXACTLY these fields: {schema_fields}. "
                    f"Do not add, remove, or rename any fields. "
                    f"Schema: {json.dumps(schema)}"
                )
            },
            {"role": "user", "content": prompt}
        ]

    @classmethod
    def clean_response(cls, content: str) -> Dict[str, Any]:
        """
        Strip markdown fences and extract the first JSON object.
        """
        cleaned = re.sub(r"```(?:json)?\s*", "", content).strip()
        match = re.search(r"(\{.*\})", cleaned, re.S)
        if not match:
            logger.error("Failed to parse JSON from content: %r", content)
            return {}
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError as e:
            logger.error("JSON decode error: %s", e)
            sentry_sdk.capture_exception(e)
            return {}

    async def get_completion(
        self,
        prompt: str,
        schema: Dict[str, Any],
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_completion_tokens: int = 2048,
        timeout: float = 120.0,  # Doubled: 60 -> 120 for GPT-5.1
        **kwargs
    ) -> Dict[str, Any]:
        """
        Send an async completion request using Responses API and return parsed JSON output.
        """
        if model is None:
            provider_config = CONFIG.llm_endpoints["openai"]
            model = provider_config.models.high

        # call site 讀 flag 決定拆不拆 wait_for。flag 已在 core 層凍結（startup-only），
        # 這裡讀的值與 get_client() 建 client 時讀的值保證一致 —— 不可能 mismatch。
        client = self.get_client()
        messages = self._build_messages(prompt, schema)

        try:
            if keepalive_timeout_enabled():
                # 收斂路徑：不包 asyncio.wait_for（讓 httpx read timeout + SDK retry 生效，
                # retry 不被外層 asyncio 砍）。timeout/retry 由 client 層(httpx.Timeout + max_retries)控制。
                #
                # B2（AR round-3）：必須補傳 timeout=timeout。caller _legacy_call_llm_validated
                # 傳了 timeout=self.timeout（writer 90 / critic 120 / analyst 300）—— 這個 per-agent
                # wall-clock 意圖在 flag-ON 下若不下傳會被丟掉，所有 agent 被壓成 client-level
                # httpx read=45。OpenAI SDK 的 request-level timeout 會覆蓋 client-level httpx
                # timeout，確保不突破 caller 的 wall-clock。與 instructor primary path 的 C1 同型修法。
                response = await client.responses.create(
                    model=model,
                    input=messages,
                    temperature=temperature,
                    max_output_tokens=max_completion_tokens,
                    text={"format": {"type": "json_object"}},
                    timeout=timeout,
                    **kwargs
                )
            else:
                # 現狀（舊 asyncio.wait_for 路徑，flag-OFF rollback 用，原封不動）。
                response = await asyncio.wait_for(
                    client.responses.create(
                        model=model,
                        input=messages,
                        temperature=temperature,
                        max_output_tokens=max_completion_tokens,
                        text={"format": {"type": "json_object"}},
                        **kwargs
                    ),
                    timeout
                )
        except asyncio.TimeoutError as e:
            # flag-OFF 路徑的逾時：維持現狀 return {}（向後相容；layer2 的 wait_for 會另接 timeout）。
            logger.error("Completion request timed out after %s seconds", timeout)
            sentry_sdk.capture_exception(e)
            if keepalive_timeout_enabled():
                # 理論上 flag-ON 不會走 asyncio.TimeoutError，但保險也分型。
                return LLMError(ERROR_KIND_TIMEOUT, f"completion asyncio timeout after {timeout}s")
            return {}
        except (openai.APITimeoutError, openai.APIConnectionError) as e:
            # 收斂的核心修：httpx read timeout / NAT drop → APITimeoutError/APIConnectionError
            # （非 asyncio.TimeoutError）。回 LLMError(timeout) 讓上層正確分型，禁 return {}（會被誤標 empty）。
            # ⚠ 對照 C2：此處裸 catch APITimeoutError 是**正確**的，因為 get_completion 直呼
            # `client.responses.create`（legacy path，**不經 instructor**）→ transport timeout 確實是裸
            # APITimeoutError。instructor primary path（generate_structured，Task 5.5）則相反 —— instructor
            # 把它包成 InstructorRetryException，故那邊必須 catch InstructorRetryException + unwrap __cause__。
            # 兩處 catch 寫法不同是刻意的（path 不同），非不一致。
            logger.error("OpenAI transport timeout/connection error: %s", e)
            sentry_sdk.capture_exception(e)
            return LLMError(ERROR_KIND_TIMEOUT, f"{type(e).__name__}: {e}")
        except Exception as e:
            logger.error("Error calling OpenAI API: %s", e)
            sentry_sdk.capture_exception(e)
            if keepalive_timeout_enabled():
                # 收斂路徑：禁 return {}（避免誤標 empty response）；回型別化 provider_error。
                return LLMError(ERROR_KIND_PROVIDER_ERROR, f"{type(e).__name__}: {e}")
            return {}  # flag-OFF：維持現狀

        # Responses API returns output_text directly
        content = getattr(response, "output_text", "") or ""

        # With json_object format, response should be valid JSON directly
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Fallback to clean_response for edge cases
            result = self.clean_response(content)
            if not result:
                logger.error("Failed to parse OpenAI response as JSON: %r", content[:500])
                return {}
            return result


# Create a singleton instance
provider = OpenAIProvider()
