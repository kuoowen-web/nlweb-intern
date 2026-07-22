"""層 3 前置：DR route 應把認證 user 注入 query_params（對齊資安 Zoe 私文隔離規格）。

[CEO 裁決 2026-07-14] helper `inject_auth_user_into_params` 的精確規格由資安 Zoe branch land、
本 branch 自建字節一致的一份（merge 時 git 視為相同內容、不 drift）：
  - authenticated 時：user_id = user['id']；org_id **無條件覆蓋** = user.get('org_id')
    （JWT 無 org 時清成 None——清偽造殘留，資安根解正確性前提）。
  - 未 authenticated / None user：不動 query_params（維持既有 fallback 語義）。
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from webserver.routes.api import inject_auth_user_into_params  # noqa: E402


def test_inject_authenticated_user():
    """authenticated → user_id 覆蓋 + org_id 無條件覆蓋。"""
    qp = {"query": ["x"]}
    user = {"authenticated": True, "id": "u-123", "org_id": "o-456"}
    inject_auth_user_into_params(qp, user)
    assert qp["user_id"] == "u-123"
    assert qp["org_id"] == "o-456"


def test_inject_authenticated_user_overrides_spoofed():
    """authenticated → 覆蓋 client 傳入的偽造 user_id/org_id（P0 私文隔離）。"""
    qp = {"query": ["x"], "user_id": "SPOOFED", "org_id": "SPOOFED-ORG"}
    user = {"authenticated": True, "id": "u-real", "org_id": "o-real"}
    inject_auth_user_into_params(qp, user)
    assert qp["user_id"] == "u-real"
    assert qp["org_id"] == "o-real"


def test_inject_authenticated_user_without_org_clears_to_none():
    """authenticated 但 JWT 無 org → org_id **無條件覆蓋成 None**（清偽造殘留，資安根解）。"""
    qp = {"query": ["x"], "org_id": "SPOOFED-ORG"}
    inject_auth_user_into_params(qp, {"authenticated": True, "id": "u-1"})
    assert qp["user_id"] == "u-1"
    # 無條件覆蓋：JWT 無 org → org_id 清成 None（非保留 client 偽造值、非「不設」）
    assert qp["org_id"] is None


def test_inject_unauthenticated_user_noop():
    """未 authenticated → 不動 query_params（維持既有 fallback 語義）。"""
    qp = {"query": ["x"]}
    inject_auth_user_into_params(qp, {"authenticated": False})
    assert "user_id" not in qp
    assert "org_id" not in qp


def test_inject_none_user_noop():
    """None user → 不動 query_params。"""
    qp = {"query": ["x"]}
    inject_auth_user_into_params(qp, None)
    assert "user_id" not in qp
    assert "org_id" not in qp


def test_dr_handler_reads_injected_user_id():
    """整合驗證：注入後 DR handler 的 user_id/org_id 生效（get_param 對 scalar 可解）。

    [merge 適配 2026-07-14] 資安 L2 `_resolve_trusted_identity` 讓 baseHandler
    優先讀 `http_handler.request['user']`，只在無可信 request 身分時 fallback 回
    query_params。本 test 驗的是「query_params 注入這條 fallback 路」，故 mock 的
    http_handler 必須 `request=None`（走 fallback）——若用裸 MagicMock，
    `request['user']` 回 truthy MagicMock → 走 request 優先分支拿到 MagicMock，
    測不到注入。這與 test_deep_research_persist.py 的 _mock_http_handler 同一手法。
    """
    from types import SimpleNamespace
    from methods.deep_research import DeepResearchHandler
    qp = {"query": ["台灣再生能源"], "site": ["all"]}
    inject_auth_user_into_params(qp, {"authenticated": True, "id": "u-9", "org_id": "o-9"})
    h = DeepResearchHandler(qp, SimpleNamespace(request=None))
    assert getattr(h, "user_id", None) == "u-9"
    assert getattr(h, "org_id", None) == "o-9"
