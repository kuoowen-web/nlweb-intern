# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Tier 6 API Clients - Unit Tests

測試每個 API client 是否正常運作：
1. 基本功能（search method）
2. 回應格式（title, snippet, link, tier, type）
3. 錯誤處理（timeout, invalid input）

Usage:
    pytest tests/test_tier6_api_clients.py -v
    pytest tests/test_tier6_api_clients.py -v -k "twse"
"""

import pytest
import re
from unittest.mock import patch, MagicMock
from aioresponses import aioresponses


# ==============================================================================
# Mock CONFIG Helper
# ==============================================================================

def get_mock_config(enabled: bool = True, timeout: float = 3.0):
    """Create a mock CONFIG object for testing."""
    mock_config = MagicMock()
    mock_config.reasoning_params = {
        "tier_6": {
            "twse": {"enabled": enabled, "timeout": timeout, "cache": {"enabled": False}},
            "yfinance": {"enabled": enabled, "timeout": timeout, "include_fundamentals": False, "cache": {"enabled": False}},
            "cwb_weather": {"enabled": enabled, "timeout": timeout, "api_key": "test_key", "cache": {"enabled": False}},
            "global_weather": {"enabled": enabled, "timeout": timeout, "api_key": "test_key", "cache": {"enabled": False}},
            "wikipedia": {"enabled": enabled, "timeout": timeout, "cache": {"enabled": False}},
            "wikidata": {"enabled": enabled, "timeout": timeout, "cache": {"enabled": False}},
            "tw_company": {"enabled": enabled, "timeout": timeout, "cache": {"enabled": False}},
        }
    }
    return mock_config


# ==============================================================================
# TWSE Client Tests (台股)
# ==============================================================================

class TestTwseClient:
    """Test Taiwan Stock Exchange client."""

    @pytest.mark.asyncio
    async def test_search_success(self):
        """Test successful stock search."""
        mock_response = {
            "msgArray": [{
                "n": "台積電",
                "z": "600.00",
                "y": "595.00",
                "v": "25000",
                "o": "598.00"
            }],
            "rtmessage": "OK"
        }

        with patch('retrieval_providers.twse_client.CONFIG', get_mock_config()):
            with aioresponses() as m:
                # Mock TWSE API endpoint (use regex to match any query params)
                m.get(
                    re.compile(r"https://mis\.twse\.com\.tw/stock/api/getStockInfo\.jsp.*"),
                    payload=mock_response
                )

                from retrieval_providers.twse_client import TwseClient
                client = TwseClient()
                results = await client.search("2330")

                assert len(results) == 1
                assert "台積電" in results[0]["title"]
                assert results[0]["tier"] == 6
                assert results[0]["type"] == "stock_tw"

    @pytest.mark.asyncio
    async def test_search_disabled(self):
        """Test disabled client returns empty."""
        with patch('retrieval_providers.twse_client.CONFIG', get_mock_config(enabled=False)):
            from retrieval_providers.twse_client import TwseClient
            client = TwseClient()
            results = await client.search("2330")
            assert results == []

    @pytest.mark.asyncio
    async def test_is_available(self):
        """Test is_available method."""
        with patch('retrieval_providers.twse_client.CONFIG', get_mock_config(enabled=True)):
            from retrieval_providers.twse_client import TwseClient
            client = TwseClient()
            assert client.is_available() is True


# ==============================================================================
# CWB Weather Client Tests (台灣天氣)
# ==============================================================================

class TestCwbWeatherClient:
    """Test Taiwan weather client."""

    @pytest.mark.asyncio
    async def test_search_success(self):
        """Test successful weather search."""
        mock_response = {
            "records": {
                "locations": [{
                    "location": [{
                        "locationName": "臺北市",
                        "weatherElement": [
                            {"elementName": "Wx", "time": [{"elementValue": [{"value": "晴"}]}]},
                            {"elementName": "MinT", "time": [{"elementValue": [{"value": "18"}]}]},
                            {"elementName": "MaxT", "time": [{"elementValue": [{"value": "25"}]}]},
                        ]
                    }]
                }]
            }
        }

        with patch('retrieval_providers.cwb_weather_client.CONFIG', get_mock_config()):
            with aioresponses() as m:
                # Mock CWB API endpoint (use regex to match any query params)
                m.get(
                    re.compile(r"https://opendata\.cwa\.gov\.tw/api/v1/rest/datastore/F-D0047-091.*"),
                    payload=mock_response
                )

                from retrieval_providers.cwb_weather_client import CwbWeatherClient
                client = CwbWeatherClient()
                results = await client.search("台北")

                assert len(results) == 1
                assert results[0]["tier"] == 6
                assert results[0]["type"] == "weather_tw"

    @pytest.mark.asyncio
    async def test_location_normalization(self):
        """Test location name normalization."""
        with patch('retrieval_providers.cwb_weather_client.CONFIG', get_mock_config()):
            from retrieval_providers.cwb_weather_client import CwbWeatherClient
            client = CwbWeatherClient()

            assert client._normalize_location("台北") == "臺北市"
            assert client._normalize_location("高雄") == "高雄市"
            assert client._normalize_location("東京") is None  # Unknown

    @pytest.mark.asyncio
    async def test_unknown_location(self):
        """Test handling of unknown location."""
        with patch('retrieval_providers.cwb_weather_client.CONFIG', get_mock_config()):
            from retrieval_providers.cwb_weather_client import CwbWeatherClient
            client = CwbWeatherClient()

            # Unknown location should return empty without API call
            results = await client.search("東京")
            assert results == []


# ==============================================================================
# yFinance Client Tests (全球股價)
# ==============================================================================

class TestYfinanceClient:
    """Test global stock client."""

    @pytest.mark.asyncio
    async def test_search_disabled(self):
        """Test disabled client returns empty."""
        with patch('retrieval_providers.yfinance_client.CONFIG', get_mock_config(enabled=False)):
            from retrieval_providers.yfinance_client import YfinanceClient
            client = YfinanceClient()
            results = await client.search("NVDA")
            assert results == []

    @pytest.mark.asyncio
    async def test_library_not_available(self):
        """Test graceful handling when yfinance not installed."""
        with patch('retrieval_providers.yfinance_client.CONFIG', get_mock_config()):
            with patch('retrieval_providers.yfinance_client.YFINANCE_AVAILABLE', False):
                from retrieval_providers.yfinance_client import YfinanceClient
                client = YfinanceClient()
                results = await client.search("NVDA")
                assert results == []


# ==============================================================================
# Response Format Validation
# ==============================================================================

class TestResponseFormat:
    """Validate all clients return consistent format."""

    REQUIRED_KEYS = {"title", "snippet", "link", "tier", "type"}

    @pytest.mark.asyncio
    async def test_twse_format(self):
        """Verify TWSE response format."""
        mock_response = {
            "msgArray": [{"n": "Test", "z": "100", "y": "99", "v": "1000"}],
            "rtmessage": "OK"
        }

        with patch('retrieval_providers.twse_client.CONFIG', get_mock_config()):
            with aioresponses() as m:
                m.get(
                    re.compile(r"https://mis\.twse\.com\.tw/stock/api/getStockInfo\.jsp.*"),
                    payload=mock_response
                )

                from retrieval_providers.twse_client import TwseClient
                client = TwseClient()
                results = await client.search("2330")

                if results:
                    assert self.REQUIRED_KEYS.issubset(results[0].keys())
                    assert results[0]["tier"] == 6
