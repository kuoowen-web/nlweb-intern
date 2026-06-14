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

from openai import AsyncOpenAI
from core.config import CONFIG
import threading
from misc.logger.logging_config_helper import get_configured_logger
from misc.logger.logger import LogLevel

from llm_providers.llm_provider import LLMProvider

logger = get_configured_logger("llm")


class ConfigurationError(RuntimeError):
    """
    Raised when configuration is missing or invalid.
    """
    pass


class OpenAIProvider(LLMProvider):
    """Implementation of LLMProvider for OpenAI API."""

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
        with cls._client_lock:
            if cls._client is None:
                api_key = cls.get_api_key()
                cls._client = AsyncOpenAI(api_key=api_key)
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

        client = self.get_client()
        messages = self._build_messages(prompt, schema)

        try:
            # Use Responses API for gpt-5.1 and newer models
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
            logger.error("Completion request timed out after %s seconds", timeout)
            sentry_sdk.capture_exception(e)
            return {}
        except Exception as e:
            logger.error("Error calling OpenAI API: %s", e)
            sentry_sdk.capture_exception(e)
            return {}

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
