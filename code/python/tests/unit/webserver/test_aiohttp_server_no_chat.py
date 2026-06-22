"""Guardrail: aiohttp_server 不再有 chat 啟動段，但現役 startup 完整，
且不得補 nlweb_handler app-key path（S1）。"""
import inspect

from webserver import aiohttp_server


def test_no_initialize_chat_system():
    src = inspect.getsource(aiohttp_server)
    assert "_initialize_chat_system" not in src
    assert "conversation_manager" not in src


def test_no_chat_imports_in_source():
    src = inspect.getsource(aiohttp_server)
    # chat 子系統 import 不該殘留
    assert "from chat" not in src
    assert "import chat" not in src


def test_no_nlweb_handler_app_key():
    # S1 guardrail（AR 第三輪）：刪 _initialize_chat_system 後不得補新的 app['nlweb_handler'] path
    src = inspect.getsource(aiohttp_server)
    assert "app['nlweb_handler']" not in src
    assert 'app["nlweb_handler"]' not in src


def test_live_startup_helpers_present():
    # 現役 startup 名稱仍在原始碼（不被誤砍）
    src = inspect.getsource(aiohttp_server)
    assert "AuthDB" in src
    assert "AnalyticsDB" in src
    assert "user_data_db" in src
