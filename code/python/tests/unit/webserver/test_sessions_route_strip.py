"""[R2] B4：sessions route 層 strip client 的 research_report（server 單一權威結構性根解）。"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from webserver.routes import sessions as sess_mod  # noqa: E402


def _fake_request(body: dict, session_id="sess-1"):
    req = MagicMock()
    req.json = AsyncMock(return_value=body)
    req.match_info = {"id": session_id}
    return req


@pytest.mark.asyncio
async def test_put_strips_client_research_report(monkeypatch):
    """PUT body 帶 research_report → 傳給 update_session 的 body 不含此 key（server 忽略）。"""
    captured = {}

    async def fake_update(session_id, user_id, org_id, body):
        captured["body"] = body
        return True

    svc = MagicMock()
    svc.update_session = fake_update
    monkeypatch.setattr(sess_mod, "_get_service", lambda: svc)
    monkeypatch.setattr(sess_mod, "_get_user_info",
                        lambda req: {"id": "u-1", "org_id": "o-1"})

    body = {"title": "改標題", "research_report": {"report": "client 想蓋掉 server 的髒值"}}
    req = _fake_request(body)
    await sess_mod.update_session_handler(req)

    assert "research_report" not in captured["body"], \
        "PUT handler 未 strip client 的 research_report（B4 未修，仍會覆蓋 server 權威值）"
    assert captured["body"].get("title") == "改標題", "其他欄位不應被誤刪"


@pytest.mark.asyncio
async def test_post_does_not_pass_client_research_report(monkeypatch):
    """POST body 帶 research_report → create_session 不以 client 值寫入（kwarg 已移除 / 傳 None）。"""
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return {"id": "new-sess", "title": kwargs.get("title")}

    svc = MagicMock()
    svc.create_session = fake_create
    monkeypatch.setattr(sess_mod, "_get_service", lambda: svc)
    monkeypatch.setattr(sess_mod, "_get_user_info",
                        lambda req: {"id": "u-1", "org_id": "o-1"})

    body = {"title": "新對話", "research_report": {"report": "client 髒值"}}
    req = _fake_request(body)
    await sess_mod.create_session_handler(req)

    # POST：research_report 不應以 client body 值傳入 create_session
    assert captured.get("research_report") in (None,), \
        "POST handler 仍以 client body 值寫 research_report（B4 未修）"
