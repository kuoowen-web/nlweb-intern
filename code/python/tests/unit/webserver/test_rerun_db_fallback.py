"""Bug 1 read path：rerun handler cache miss → 用 session UUID 讀 DB research_report 重建。"""
import os, sys, time, uuid, json
import pytest
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
for _pg in ('DATABASE_URL', 'ANALYTICS_DATABASE_URL', 'POSTGRES_CONNECTION_STRING'):
    os.environ.pop(_pg, None)

from auth.auth_db import AuthDB
from core.session_service import SessionService

USER_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())
# [R3 修訂 B1-2] 第二組 user/org（攻擊者）——B1-2 跨 user 讀防護 test 用真實第二 user，
# 測的是 get_session(sid, other_uid, other_oid) owner binding（回 None → 400），而非 auth 層失敗。
OTHER_UID = str(uuid.uuid4())
OTHER_OID = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    for _pg in ('DATABASE_URL', 'ANALYTICS_DATABASE_URL', 'POSTGRES_CONNECTION_STRING'):
        os.environ.pop(_pg, None)
    db_path = str(tmp_path / "rerun_fallback.db")
    AuthDB._instance = None
    db = AuthDB(db_path=db_path); AuthDB._instance = db
    db._init_database_sync(); db._initialized = True
    import sqlite3
    conn = sqlite3.connect(db_path); conn.execute("PRAGMA foreign_keys = ON")
    now = time.time()
    conn.execute("INSERT INTO organizations (id, name, slug, created_at) VALUES (?,?,?,?)", (ORG_ID, "Org", "org", now))
    conn.execute("INSERT INTO users (id, email, password_hash, name, created_at) VALUES (?,?,?,?,?)", (USER_ID, "a@b.c", "h", "N", now))
    # [R3 修訂 B1-2] seed 第二組 user/org（攻擊者），讓 B1-2 測的是 owner-mismatch（400）非 auth 401。
    # 註：現況 auth_middleware 只 decode JWT、不驗 DB（middleware/auth.py:142-183），故未 seed 時
    # 攻擊者 JWT 仍過 auth、owner-mismatch 在 get_session 攔 → 400 本就成立；此 seed 為
    # (a) 忠實模擬「真實登入的第二 user」語義；(b) 未來若 auth 改成驗 DB user 也不退化成 401。
    conn.execute("INSERT INTO organizations (id, name, slug, created_at) VALUES (?,?,?,?)", (OTHER_OID, "Org2", "org2", now))
    conn.execute("INSERT INTO users (id, email, password_hash, name, created_at) VALUES (?,?,?,?,?)", (OTHER_UID, "x@y.z", "h", "N2", now))
    conn.commit(); conn.close()
    yield db
    AuthDB._instance = None


@pytest.mark.asyncio
async def test_db_fallback_reconstructs_rerun_state():
    """cache 沒有（server 重啟模擬）→ 用 session UUID 讀 research_report → 重建成功。"""
    from reasoning.orchestrator import restore_rerun_state_from_report
    svc = SessionService()
    s = await svc.create_session(user_id=USER_ID, org_id=ORG_ID, title="t")
    sid = s["id"]
    # 塞一份含 rerunState 的 research_report（模擬主 DR run 已 persist）
    report_obj = {
        "report": "# 報告", "sources": ["u1"], "query": "台灣再生能源",
        "rerunState": {
            "query": "台灣再生能源", "mode": "discovery", "temporal_context": None,
            "enable_kg": True, "enable_web_search": False,
            "formatted_context": "[1] 來源 - 標題\n內容\n",
            "source_map": {"1": {"url": "u1", "title": "標題"}},
            "items_count": 3,
        },
    }
    await svc.update_session(sid, USER_ID, ORG_ID, {"research_report": report_obj})

    # 模擬 handler DB fallback：讀 session → 重建
    row = await svc.get_session(sid, USER_ID, ORG_ID)
    restored = restore_rerun_state_from_report(row["research_report"])
    assert restored is not None, "DB fallback 應能重建 rerunState"
    assert isinstance(next(iter(restored["source_map"].keys())), int)
    assert restored["formatted_context"].startswith("[1]")


@pytest.mark.asyncio
async def test_db_fallback_none_when_owner_mismatch():
    """session UUID 不屬本 user（get_session 回 None）→ 重建失敗（不跨 user 讀）。"""
    from reasoning.orchestrator import restore_rerun_state_from_report
    svc = SessionService()
    row = await svc.get_session(str(uuid.uuid4()), USER_ID, ORG_ID)  # 不存在
    assert row is None
    # handler 端：row None → restore 不呼叫、rerun 走 400


# ============================================================================
# [R2 修訂 B1 — BLOCKER] 真 handler-level test：驗 pre-check 不再 400 +
# run_research_rerun(restored_state=...) 真的被接上（非假綠）。
#
# 為何加：上面兩個 test 只呼叫 SessionService + restore 純函式，**從沒呼叫
# research_rerun_handler**——沒驗「cache miss 不再回 400」，也沒驗「execute_rerun 真的把
# restored_state 傳進 run_research_rerun」。若 executor 忘改 api.py pre-check / 忘傳
# session_id / 忘接 restored_state，上面兩 test 仍全綠（假綠，同 session 14 R8 坑）。
# 這三個 test 從 HTTP 入口打真 handler，鎖死接線本身。
#
# 手法沿用 test_dr_private_docs_isolation.py：aiohttp TestClient/TestServer + 真
# auth_middleware + JWT（真實登入，禁 auth bypass）+ patch DeepResearchHandler 內部
# 攔截，避免燒 LLM/耗時。composable_pipeline flag 用 CONFIG.reasoning_params.setdefault
# 開（沿用 test_lr_flag_wiring_continue.py:97 pattern）。
# ============================================================================

import time as _time
import jwt as _jwt
from unittest.mock import patch as _patch, AsyncMock as _AsyncMock
from aiohttp import web as _web
from aiohttp.test_utils import TestClient as _TestClient, TestServer as _TestServer

os.environ.setdefault('JWT_SECRET', 'test-rerun-fallback-secret')


def _jwt_for(user_id, org_id):
    _now = int(_time.time())
    return _jwt.encode(
        {'user_id': user_id, 'email': 'a@b.c', 'name': 'N', 'org_id': org_id,
         'role': 'member', 'iat': _now, 'exp': _now + 3600},
        os.environ['JWT_SECRET'], algorithm='HS256',
    )


async def _make_rerun_client():
    from webserver.middleware.auth import auth_middleware
    from webserver.routes.api import research_rerun_handler
    app = _web.Application(middlewares=[auth_middleware])
    app.router.add_post('/api/research/rerun', research_rerun_handler)
    client = _TestClient(_TestServer(app))
    await client.start_server()
    return client


@pytest.fixture(autouse=True)
def _enable_composable():
    """rerun handler + execute_rerun 都 gate 在 composable_pipeline flag（否則 501/RuntimeError）。"""
    from core.config import CONFIG
    features = CONFIG.reasoning_params.setdefault('features', {})
    _orig = features.get('composable_pipeline')
    features['composable_pipeline'] = True
    yield
    if _orig is None:
        features.pop('composable_pipeline', None)
    else:
        features['composable_pipeline'] = _orig


@pytest.fixture(autouse=True)
def _clear_rerun_cache():
    """模擬 server 重啟：清空記憶體 _research_state_cache（強制走 DB fallback）。"""
    import reasoning.orchestrator as _orch
    _orch._research_state_cache.clear()
    yield
    _orch._research_state_cache.clear()


async def _seed_session_with_rerunstate(svc, user_id, org_id, query_id="query_stale_gone"):
    """建一份含 rerunState 的 session research_report（模擬主 DR run 已 persist）。

    [R3 修訂 S2-new] rerunState 綁定 query_id（預設 'query_stale_gone'，與 handler test 送出的
    query_id 對齊）——DB fallback 分支驗 restored.query_id == original_query_id 才放行。
    """
    s = await svc.create_session(user_id=user_id, org_id=org_id, title="t")
    sid = s["id"]
    report_obj = {
        "report": "# 報告", "sources": ["u1"], "query": "台灣再生能源",
        "rerunState": {
            "query": "台灣再生能源", "mode": "discovery", "temporal_context": None,
            "enable_kg": True, "enable_web_search": False,
            "formatted_context": "[1] 來源 - 標題\n內容\n",
            "source_map": {"1": {"url": "u1", "title": "標題"}},
            "items_count": 3,
            "query_id": query_id,   # [R3 修訂 S2-new] 綁定產生此 rerunState 的 query_id
        },
    }
    await svc.update_session(sid, user_id, org_id, {"research_report": report_obj})
    return sid


@pytest.mark.asyncio
async def test_handler_cache_miss_db_fallback_passes_restored_state():
    """[B1-1] cache miss（server 重啟）+ DB 有 rerunState → pre-check 不回 400，
    且 run_research_rerun 收到的 restored_state 非 None（證明重建結果真被接上、非接空）。"""
    svc = SessionService()
    sid = await _seed_session_with_rerunstate(svc, USER_ID, ORG_ID)
    token = _jwt_for(USER_ID, ORG_ID)

    captured = {}

    async def _fake_rerun(self, original_query_id, modified_query, restored_state=None):
        captured['restored_state'] = restored_state
        return []  # stub 掉 phase 2-4，不燒 LLM

    client = await _make_rerun_client()
    try:
        # patch orchestrator.run_research_rerun：execute_rerun 走完 DB fallback 才呼叫它，
        # 攔在這層可同時驗「不 400（走進 SSE）」+「restored_state 被傳入」。
        with _patch('reasoning.orchestrator.DeepResearchOrchestrator.run_research_rerun',
                    new=_fake_rerun):
            resp = await client.post(
                '/api/research/rerun',
                json={'query_id': 'query_stale_gone', 'kg_edits': {'edit_summary': {'nodes_modified': 1}},
                      'query': '台灣再生能源', 'session_id': sid},
                headers={'Authorization': f'Bearer {token}'},
            )
            await resp.read()
        # pre-check 通過 → 進 SSE（200），非 cache_miss 400
        assert resp.status == 200, f"cache miss + DB fallback 應通過 pre-check（非 400），得 {resp.status}"
        # 關鍵防偽：restored_state 真被接上（非 None）——證明 DB 重建結果餵進了 pipeline
        assert captured.get('restored_state') is not None, \
            "run_research_rerun 必須收到非 None restored_state（否則重建結果沒接上，假綠）"
        assert captured['restored_state']['formatted_context'].startswith("[1]")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_handler_owner_mismatch_returns_400():
    """[B1-2] 用另一 user/org 的 session UUID 打 handler → cache miss + get_session owner 不匹配
    回 None → 無 fallback → 400（同時是跨 user 讀防護 test）。"""
    svc = SessionService()
    # session 屬於 USER_ID/ORG_ID
    sid = await _seed_session_with_rerunstate(svc, USER_ID, ORG_ID)
    # [R3 修訂 B1-2] 用「另一個真實 seed 的 user/org」的 JWT 來打（攻擊者）——OTHER_UID/OTHER_OID
    # 已在 _fresh_db seed，確保測的是 owner-mismatch（get_session 回 None → 400），非 auth 401。
    token = _jwt_for(OTHER_UID, OTHER_OID)

    client = await _make_rerun_client()
    try:
        resp = await client.post(
            '/api/research/rerun',
            json={'query_id': 'query_stale_gone', 'kg_edits': {'edit_summary': {'nodes_modified': 1}},
                  'query': '台灣再生能源', 'session_id': sid},
            headers={'Authorization': f'Bearer {token}'},
        )
        body = await resp.json()
        # cache miss + get_session(sid, other_uid, other_oid) owner 不匹配回 None → 無 fallback → 400
        assert resp.status == 400, f"跨 user 讀他人 session 應回 400，得 {resp.status}"
        assert body.get('error') == 'cache_miss'
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_handler_session_query_id_mismatch_returns_400():
    """[B1-4 — R3 修訂 S2-new] cache 空 + session 有 rerunState 但 query_id 與請求不符 → 400。

    情境（資料正確性洞）：一個 session 跑了兩次 DR（query_A 然後 query_B），research_report 只存
    最後一次（query_B）的 rerunState。若前端 stale（KG 編輯用的還是 query_A），DB fallback 讀到
    session 的 rerunState（綁 query_B）→ 若不驗 query_id 就放行，會用 query_B 的 formatted_context/
    source_map 去 rerun query_A 的 KG 編輯 → 產出張冠李戴的報告。此 test 鎖死「不匹配 → 400，不是
    拿錯 state 跑成 200」。
    """
    svc = SessionService()
    # session 的 rerunState 綁定 query_B（最後一次 DR）
    sid = await _seed_session_with_rerunstate(svc, USER_ID, ORG_ID, query_id="query_B_latest")
    token = _jwt_for(USER_ID, ORG_ID)

    client = await _make_rerun_client()
    try:
        # 前端 stale：仍用 query_A（第一次 DR，已從 cache 淘汰）打 rerun
        resp = await client.post(
            '/api/research/rerun',
            json={'query_id': 'query_A_stale', 'kg_edits': {'edit_summary': {'nodes_modified': 1}},
                  'query': '台灣再生能源', 'session_id': sid},
            headers={'Authorization': f'Bearer {token}'},
        )
        body = await resp.json()
        # query_A_stale != rerunState.query_id(query_B_latest) → 不放行 → 400（非拿錯 state 跑 200）
        assert resp.status == 400, \
            f"session 的 rerunState query_id 與請求不符應回 400（不張冠李戴），得 {resp.status}"
        assert body.get('error') == 'cache_miss'
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_orchestrator_consumes_restored_state_when_cache_empty():
    """[B1-3] cache 空 + 傳入 restored_state → run_research_rerun 真的用它建 ResearchState
    （不走 cache、不 raise ValueError）。stub 掉 phase 2-4 只驗 state 建構來源。"""
    from reasoning.orchestrator import DeepResearchOrchestrator, restore_rerun_state_from_report
    # 從一份 rerunState 重建 restored_state（含 items placeholder / source_map int-key / current_context）
    restored = restore_rerun_state_from_report({
        "rerunState": {
            "query": "台灣再生能源", "mode": "discovery", "temporal_context": None,
            "enable_kg": True, "enable_web_search": False,
            "formatted_context": "[1] 來源 - 標題\n內容\n",
            "source_map": {"1": {"url": "u1", "title": "標題"}},
            "items_count": 3,
            "query_id": "query_gone_from_cache",   # [R3 修訂 S2-new] 帶 query_id（此 test 直傳 restored_state，不經 fallback 對齊驗證）
        }
    })
    assert restored is not None

    handler = SimpleNamespace(user_id=USER_ID, org_id=ORG_ID, site='all')
    orch = DeepResearchOrchestrator(handler=handler)

    captured_state = {}

    # 簽名含 self：patch.object(class, ..., new=fn) 是 class attribute 替換，呼叫時經 descriptor
    # 綁定傳入 (self, state)——plan 原稿漏 self（TypeError: takes 1 positional argument but 2 were given）
    async def _stub_actor_critic(self, state):
        captured_state['state'] = state
        # [R3 修訂 B1-3] early_return=[] 有效提早返回——已親讀 orchestrator.py（rerun path）
        # `if state.early_return is not None: return state.early_return`：`[] is not None → True`
        # → 空 list 觸發提早返回、不跑 writer/format phase（Codex 基於 `if state.early_return:` 的
        # 慢測疑慮是 FP，見 plan [R3 修訂 B1-3] 駁回說明）。
        state.early_return = []
        return state

    # cache 是空的（_clear_rerun_cache fixture 已清）→ 若沒接 restored_state 會 raise ValueError
    with _patch.object(DeepResearchOrchestrator, '_phase_actor_critic_loop', new=_stub_actor_critic):
        results = await orch.run_research_rerun(
            original_query_id='query_gone_from_cache',
            modified_query='台灣再生能源（改）',
            restored_state=restored,
        )
    st = captured_state['state']
    # ResearchState 由 restored_state 建（非 cache、非空）
    assert st.formatted_context.startswith("[1]"), "state 必須用 restored_state 的 formatted_context"
    assert st.source_map[1]["url"] == "u1", "source_map 來自 restored_state（int key）"
    assert len(st.items) == 3, "items placeholder 長度 = items_count（restored_state 重建）"


@pytest.mark.asyncio
async def test_orchestrator_deep_defense_rejects_mismatched_query_id():
    """[B1-3b — R4 修訂 RC3] 深度防禦（縱深）：restored_state.query_id 與 original_query_id 不符
    → run_research_rerun 棄用 restored_state → cache 空 → raise ValueError（不張冠李戴）。
    這道 guard 在建 ResearchState 前就攔（進不到 phase 2-4），故不需 stub actor-critic。
    模擬「上游對齊驗證因故失效／被繞過」——縱深防線仍守住。"""
    from reasoning.orchestrator import DeepResearchOrchestrator, restore_rerun_state_from_report
    restored = restore_rerun_state_from_report({
        "rerunState": {
            "query": "台灣再生能源", "mode": "discovery", "temporal_context": None,
            "enable_kg": True, "enable_web_search": False,
            "formatted_context": "[1] 來源 - 標題\n內容\n",
            "source_map": {"1": {"url": "u1", "title": "標題"}},
            "items_count": 3,
            "query_id": "query_B_latest",   # 綁的是 query_B
        }
    })
    assert restored is not None
    handler = SimpleNamespace(user_id=USER_ID, org_id=ORG_ID, site='all')
    orch = DeepResearchOrchestrator(handler=handler)
    # cache 空 + 傳入 query_id 不符的 restored_state → guard 棄用 → raise ValueError（非拿錯 state 跑）
    with pytest.raises(ValueError):
        await orch.run_research_rerun(
            original_query_id='query_A_stale',   # 請求 query_A，但 restored_state 綁 query_B
            modified_query='台灣再生能源（改）',
            restored_state=restored,
        )
