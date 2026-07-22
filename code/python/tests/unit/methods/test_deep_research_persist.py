"""層 3：DR server-side persist——report 寫進 search_sessions.research_report，reload 讀回。"""
import os
import sys
import time
import uuid
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))


def _mock_http_handler():
    """建 test 用的 mock http_handler，使 baseHandler `_resolve_trusted_identity` 走 fallback。

    ⚠️ merge 後 baseHandler 新增 `_resolve_trusted_identity(query_params, http_handler)`：
    identity 優先讀 `http_handler.request['user']` 的可信 JWT 身分，沒有才 fallback 回 query_params。
    若這裡用裸 `MagicMock()`，`getattr(h, "request", None)` 會拿到 MagicMock（非 None）、
    `request.get("user")`/`user.get("authenticated")`/`user.get("id")` 全回 truthy MagicMock →
    `self.user_id`/`self.org_id` 變 MagicMock → 綁進 SQLite 爆
    `Error binding parameter: type 'MagicMock' is not supported`。

    把 `.request` 明確設成 None（對齊資安自己的 test_baseHandler_trusted_identity.py
    `test_fallback_to_query_params_when_no_wrapper` 的 fallback pattern）→ `_resolve_trusted_identity`
    走 fallback、採 query_params 的 user_id/org_id，test 原本設的身分就生效。
    這是 test mock 對齊 baseHandler 新行為，不弱化任何 persist/round-trip/身分驗證斷言。
    """
    return SimpleNamespace(request=None)

os.environ.pop('DATABASE_URL', None)
os.environ.pop('ANALYTICS_DATABASE_URL', None)
os.environ.pop('POSTGRES_CONNECTION_STRING', None)

from auth.auth_db import AuthDB  # noqa: E402
from core.session_service import SessionService  # noqa: E402
from methods.deep_research import DeepResearchHandler  # noqa: E402

USER_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    # ⚠️ import methods.deep_research → core.config 的 load_dotenv(override=False) 會從 .env
    #   **重新注入** POSTGRES_CONNECTION_STRING/ANALYTICS_DATABASE_URL（module-top 的 pop 在 import
    #   之前跑、無效）。fixture 於此**再 pop 一次**（此時所有 import 已完成），確保 AuthDB(db_path=...)
    #   讀不到 postgres URL → db_type='sqlite'，真用 fixture 的 SQLite。這是 test infra 對齊，
    #   不弱化斷言（persist/round-trip/marker 邏輯完全不動）。
    for _pg in ('DATABASE_URL', 'ANALYTICS_DATABASE_URL', 'POSTGRES_CONNECTION_STRING'):
        os.environ.pop(_pg, None)
    db_path = str(tmp_path / "dr_persist_test.db")
    AuthDB._instance = None
    db = AuthDB(db_path=db_path)
    AuthDB._instance = db
    db._init_database_sync()
    db._initialized = True
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    now = time.time()
    conn.execute("INSERT INTO organizations (id, name, slug, created_at) VALUES (?, ?, ?, ?)",
                 (ORG_ID, "Test Org", "test-org", now))
    conn.execute("INSERT INTO users (id, email, password_hash, name, created_at) VALUES (?, ?, ?, ?, ?)",
                 (USER_ID, "dr@test.com", "fakehash", "DR Tester", now))
    conn.commit()
    conn.close()
    yield db
    AuthDB._instance = None


@pytest.mark.asyncio
async def test_create_dr_session_returns_uuid():
    """_create_dr_session 無有效 loaded_session_id → 建 DB row，回 server-owned UUID。"""
    qp = {"query": ["台灣再生能源"], "user_id": USER_ID, "org_id": ORG_ID}
    h = DeepResearchHandler(qp, _mock_http_handler())
    sid = await h._create_dr_session()
    assert sid is not None
    svc = SessionService()
    row = await svc.get_session(sid, USER_ID, ORG_ID)
    assert row is not None, "server 建的 session row 撈不回"


@pytest.mark.asyncio
async def test_create_dr_session_adopts_valid_loaded_session():
    """B5：前端傳來 loaded_session_id 且屬本 user/org → 採用現有 row、不建新（不分裂側欄）。"""
    svc = SessionService()
    # 先建一個「當前 session」row，模擬前端已載入的 session
    existing = await svc.create_session(user_id=USER_ID, org_id=ORG_ID, title="當前對話")
    existing_id = existing["id"]

    qp = {"query": ["台灣再生能源"], "user_id": USER_ID, "org_id": ORG_ID,
          "loaded_session_id": existing_id}
    h = DeepResearchHandler(qp, _mock_http_handler())
    sid = await h._create_dr_session()
    assert sid == existing_id, "應採用前端當前 session，卻建了新 row（側欄會分裂）"


@pytest.mark.asyncio
async def test_create_dr_session_rejects_foreign_loaded_session():
    """B5：loaded_session_id 不屬當前 user（get_session 驗證回 None）→ 不採用，改建新 row。"""
    qp = {"query": ["x"], "user_id": USER_ID, "org_id": ORG_ID,
          "loaded_session_id": str(uuid.uuid4())}  # 不存在／非本人的 id
    h = DeepResearchHandler(qp, _mock_http_handler())
    sid = await h._create_dr_session()
    assert sid is not None
    assert sid != qp["loaded_session_id"], "不應採用非本人的 loaded_session_id"
    svc = SessionService()
    row = await svc.get_session(sid, USER_ID, ORG_ID)
    assert row is not None, "改建的新 row 撈不回"


@pytest.mark.asyncio
async def test_persist_research_report_roundtrip():
    """persist 後從 DB 讀回 research_report 內容一致（研究成功 results 非空）。"""
    qp = {"query": ["台灣再生能源"], "user_id": USER_ID, "org_id": ORG_ID}
    h = DeepResearchHandler(qp, _mock_http_handler())
    sid = await h._create_dr_session()
    # [R4 C3] report_obj 含 persist_marker（唯一 write marker）——正向路徑：讀回 marker 匹配 → 回 True。
    report_obj = {"report": "# 深度研究報告：台灣再生能源\n內容", "sources": ["u1"],
                  "query": "台灣再生能源", "persist_marker": str(uuid.uuid4())}
    # F24：persist gate 看 results 是否非空（研究實質成功），非看字串
    results = [{"url": "u1", "description": "..."}]  # 非空 = 研究成功
    ok = await h._persist_research_report(sid, report_obj, results)
    assert ok is True, "研究成功卻 persist 失敗（含 persist_marker，讀回應匹配）"
    svc = SessionService()
    row = await svc.get_session(sid, USER_ID, ORG_ID)
    # F23：get_session 回 snake_case dict、research_report 已反序列化為 dict
    got = row["research_report"]
    assert got is not None
    if isinstance(got, str):  # 保險（SQLite/PG 兩路徑都最終為 dict）
        got = json.loads(got)
    assert got["report"].startswith("# 深度研究報告")


@pytest.mark.asyncio
async def test_persist_research_report_graph_chain_roundtrip():
    """[R6 BLOCKER2] persist 的 report_obj 若含 argumentGraph/chainAnalysis（camelCase）→ 讀回一致。

    R6 BLOCKER2：R5 方向 A（reload 優先序反轉）後，登入用戶 reload 走 server top-level，
    前端 top-level restore 分支（news-search.js:2720-2721）讀 `session.researchReport.argumentGraph`/
    `.chainAnalysis`（**camelCase**）——若 server report_obj 不含 graph/chain（或存 snake_case），
    reload 後 graph/chain 被設 null（regression）。本 case 鎖住「report_obj 帶 camelCase graph/chain →
    persist → 讀回仍是同一份 camelCase」，確保 reload 讀得到。（此為 unit 層防護；E2E 顯示層走 Task 9 Step 2c。）
    這裡直接餵已組好的 report_obj（模擬 runQuery 接線從 schema_object 提取後轉 camelCase 的結果）。
    """
    qp = {"query": ["台灣半導體"], "user_id": USER_ID, "org_id": ORG_ID}
    h = DeepResearchHandler(qp, _mock_http_handler())
    sid = await h._create_dr_session()
    # runQuery 接線會從 results[0].schema_object 的 snake_case 提取後轉 camelCase 塞 report_obj；
    # 此 test 直接餵已轉好的 camelCase，驗 persist→讀回不掉欄位、不改 key。
    argument_graph = [{"node_id": "n1", "claim": "主張1"}, {"node_id": "n2", "claim": "主張2"}]  # list
    chain_analysis = {"topological_order": ["n1", "n2"], "critical_nodes": [], "max_depth": 2}     # dict
    report_obj = {
        "report": "# 深度研究報告：台灣半導體\n內容", "sources": ["u1"], "query": "台灣半導體",
        "persist_marker": str(uuid.uuid4()),
        "argumentGraph": argument_graph,   # camelCase（對齊前端 :2720 讀法）
        "chainAnalysis": chain_analysis,    # camelCase（對齊前端 :2721 讀法）
    }
    results = [{"url": "u1", "description": "...", "schema_object": {
        "argument_graph": argument_graph, "reasoning_chain_analysis": chain_analysis}}]  # 非空 = 研究成功
    ok = await h._persist_research_report(sid, report_obj, results)
    assert ok is True
    svc = SessionService()
    row = await svc.get_session(sid, USER_ID, ORG_ID)
    got = row["research_report"]
    if isinstance(got, str):
        got = json.loads(got)
    # 讀回必須仍是 camelCase key + 原 shape（list / dict），不被掉/不被改 key
    assert got.get("argumentGraph") == argument_graph, \
        "argumentGraph（camelCase list）讀回不一致——前端 :2720 讀不到會使推論鏈消失（R6 BLOCKER2 regression）"
    assert got.get("chainAnalysis") == chain_analysis, \
        "chainAnalysis（camelCase dict）讀回不一致——前端 :2721 讀不到會使推論鏈分析消失"
    # 確認沒誤存 snake_case（若存 snake_case 前端讀不出來）
    assert "argument_graph" not in got, "report_obj 誤存 snake_case argument_graph（前端讀 camelCase，讀不到）"


@pytest.mark.asyncio
async def test_persist_research_report_kg_roundtrip():
    """[R7 BLOCKER1] persist 的 report_obj 若含 knowledgeGraph（camelCase dict）→ 讀回一致。

    R7 BLOCKER1：CEO 拍板 KG 也提升 server 權威（不留 nested 尾巴）。KG 存進**既有 research_report JSONB**
    （search_sessions 無獨立 knowledge_graph 欄位，走 research_report JSONB 零 migration）。三處 key 對齊：
    存 report_obj["knowledgeGraph"]（camelCase）→ hydrate 搬 s.research_report.knowledgeGraph → session.knowledgeGraph
    → reload selector 讀 serverReport.knowledgeGraph（news-search.js:2867 改後）。本 case 鎖住
    「report_obj 帶 camelCase knowledgeGraph → persist → 讀回仍是同一份 camelCase dict」，
    確保前端 reload 走 server 值時 displayKnowledgeGraph 讀得到（unit 層防護；E2E 顯示層走 Task 9 Step 2c）。
    """
    qp = {"query": ["台灣再生能源"], "user_id": USER_ID, "org_id": ORG_ID}
    h = DeepResearchHandler(qp, _mock_http_handler())
    sid = await h._create_dr_session()
    # KG shape 對齊 schema_object.knowledge_graph（kg-spec §2.1）= {entities, relationships, metadata}
    knowledge_graph = {
        "entities": [{"id": "e1", "name": "再生能源", "type": "concept"},
                     {"id": "e2", "name": "台電", "type": "org"}],
        "relationships": [{"source": "e2", "target": "e1", "type": "operates"}],
        "metadata": {"entity_count": 2, "relationship_count": 1},
    }
    report_obj = {
        "report": "# 深度研究報告：台灣再生能源\n內容", "sources": ["u1"], "query": "台灣再生能源",
        "persist_marker": str(uuid.uuid4()),
        "knowledgeGraph": knowledge_graph,   # camelCase（對齊 reload :2867 serverReport.knowledgeGraph 讀法）
    }
    # runQuery 接線從 results[0].schema_object.knowledge_graph（snake_case）提取後轉 camelCase 塞 report_obj
    results = [{"url": "u1", "description": "...", "schema_object": {
        "knowledge_graph": knowledge_graph}}]  # 非空 = 研究成功
    ok = await h._persist_research_report(sid, report_obj, results)
    assert ok is True
    svc = SessionService()
    row = await svc.get_session(sid, USER_ID, ORG_ID)
    got = row["research_report"]
    if isinstance(got, str):
        got = json.loads(got)
    # 讀回必須仍是 camelCase key + 原 dict shape（entities/relationships/metadata），不被掉/不被改 key
    assert got.get("knowledgeGraph") == knowledge_graph, \
        "knowledgeGraph（camelCase dict）讀回不一致——前端 :2867 serverReport.knowledgeGraph 讀不到會使知識圖譜消失（R7 BLOCKER1）"
    # 確認沒誤存 snake_case（若存 snake_case 前端讀 camelCase 讀不出來）
    assert "knowledge_graph" not in got, "report_obj 誤存 snake_case knowledge_graph（前端讀 camelCase knowledgeGraph，讀不到）"


def test_build_research_report_obj_converts_snake_to_camel():
    """[R8 BLOCKER2——測轉換本身，非測「存已對的物件」（防假綠）] `_build_research_report_obj`
    必須把 results[0].schema_object 的 **snake_case** graph/chain/KG 轉成 report_obj 的 **camelCase**。

    這是三個 round-trip test（餵已組好 camelCase report_obj）測不到的關鍵接縫：runQuery 接線
    從 schema_object.knowledge_graph（snake）提取 → report_obj["knowledgeGraph"]（camel）。
    executor 若讀錯來源 key（`.get("knowledgeGraph")` 讀不到 snake）或寫錯目標 key
    （`report_obj["knowledge_graph"]` 前端讀不到），round-trip test 仍全綠 = 假綠。本 test 直接
    餵 snake schema_object、驗 report_obj 的 camel key 有值 + **無任何 snake key**（存 snake 前端讀不到）。
    """
    qp = {"query": ["台灣再生能源"], "user_id": USER_ID, "org_id": ORG_ID}
    h = DeepResearchHandler(qp, _mock_http_handler())

    # input：results[0].schema_object 是 **snake_case**（orchestrator.py:2085/2089/2092 塞的原樣）
    argument_graph = [{"node_id": "n1", "claim": "主張1"}]
    reasoning_chain = {"topological_order": ["n1"], "critical_nodes": [], "max_depth": 1}
    knowledge_graph = {
        "entities": [{"id": "e1", "name": "再生能源", "type": "concept"}],
        "relationships": [],
        "metadata": {"entity_count": 1, "relationship_count": 0},
    }
    results = [{"url": "u1", "description": "...", "schema_object": {
        "argument_graph": argument_graph,                  # snake 來源
        "reasoning_chain_analysis": reasoning_chain,       # snake 來源
        "knowledge_graph": knowledge_graph,                # snake 來源
    }}]

    report_obj = h._build_research_report_obj("# 報告本文", ["u1"], results)

    # 轉換後 report_obj 必須是 **camelCase**（前端 reload 讀 camel，見 news-search.js:2720-2721/:2867）
    assert report_obj.get("argumentGraph") == argument_graph, \
        "graph 沒轉成 camelCase argumentGraph（來源 schema_object.argument_graph 讀錯 / 目標 key 寫錯）"
    assert report_obj.get("chainAnalysis") == reasoning_chain, \
        "chain 沒轉成 camelCase chainAnalysis（來源 schema_object.reasoning_chain_analysis 讀錯 / 目標 key 寫錯）"
    assert report_obj.get("knowledgeGraph") == knowledge_graph, \
        "KG 沒轉成 camelCase knowledgeGraph（來源 schema_object.knowledge_graph 讀錯 / 目標 key 寫錯，前端 :2867 讀不到 = 知識圖譜消失）"
    # **核心防假綠 assert**：report_obj 不得含任何 snake_case graph/chain/KG key
    #   （存 snake 前端讀 camel 讀不到 → 存了等於沒存）。
    for _snake in ("argument_graph", "reasoning_chain_analysis", "knowledge_graph"):
        assert _snake not in report_obj, \
            f"report_obj 誤含 snake_case key '{_snake}'——轉換沒把 snake→camel（前端讀 camel，此欄位形同白存）"
    # report 本體 + 內部 marker 齊全
    assert report_obj["report"] == "# 報告本文"
    assert "persist_marker" in report_obj, "helper 未塞 persist_marker（B3 讀回驗證用）"


def test_build_research_report_obj_omits_missing_graph_kg():
    """[R8 BLOCKER2 對照] schema_object 缺 graph/chain/KG 時 report_obj 不塞對應 key（無值不塞，
    保持與前端「serverReport?.knowledgeGraph ||」fallback 相容——undefined 落 fallback、非塞 null）。"""
    qp = {"query": ["x"], "user_id": USER_ID, "org_id": ORG_ID}
    h = DeepResearchHandler(qp, _mock_http_handler())
    results = [{"url": "u1", "description": "...", "schema_object": {}}]  # 無 graph/chain/KG
    report_obj = h._build_research_report_obj("# 報告", ["u1"], results)
    assert "argumentGraph" not in report_obj
    assert "chainAnalysis" not in report_obj
    assert "knowledgeGraph" not in report_obj
    assert report_obj["report"] == "# 報告"  # report 本體仍在


@pytest.mark.asyncio
async def test_persist_skips_when_results_empty():
    """B2/F24 空覆蓋防護：斷線早退 results=[] → fallback markdown 非空 → 仍 skip，不覆蓋好報告。

    此 case 專攻 R1 blocker B2：現行 gate 只看 report.strip() 空不空會判錯，
    因為 _generate_final_report([]) 回「非空空洞 markdown」（標題+分析來源數:0+---）。
    正確判準是 len(results)==0。
    """
    qp = {"query": ["x"], "user_id": USER_ID, "org_id": ORG_ID}
    h = DeepResearchHandler(qp, _mock_http_handler())
    sid = await h._create_dr_session()
    # 先寫入一份好報告（研究成功）
    good = {"report": "# 好報告\n有內容", "sources": [], "query": "x"}
    await h._persist_research_report(sid, good, [{"url": "u1", "description": "d"}])
    # 模擬斷線早退：results=[]，但 report 是 _generate_final_report([]) 的非空空洞骨架
    empty_skeleton = {"report": "# 深度研究報告：x\n\n**分析來源數：** 0\n\n---\n", "sources": [], "query": "x"}
    ok = await h._persist_research_report(sid, empty_skeleton, [])  # results=[] → 應 skip
    assert ok is False, "results=[] 應 skip persist（回 False）"
    svc = SessionService()
    row = await svc.get_session(sid, USER_ID, ORG_ID)
    got = row["research_report"]
    if isinstance(got, str):
        got = json.loads(got)
    assert got["report"] == "# 好報告\n有內容", \
        "非空空洞骨架覆蓋了好報告（B2 空覆蓋 bug——gate 誤用字串空不空）"


@pytest.mark.asyncio
async def test_persist_writes_when_results_has_error_item():
    """[R3 should-fix 1] B2 反向對照：results=[查無資料 error item]（len==1）→ 應 persist（非 skip）。

    B2 gate 判準是 len(results)==0 才 skip（斷線真空 list）。查無資料回的是 **len==1 的
    no-results item**（F31：@type='Item' / url='internal://no-results'，**非** ErrorReport top-level）
    ——這是合法研究結果（研究成功、只是查無資料），該存。少了這條對照，gate 可能被改壞成
    「results 有 error item 也 skip」而測不出（正向 test 只證 results 非空的一般情況、
    test_persist_skips_when_results_empty 只證 []，都沒鎖住「len==1 error item 邊界」）。
    """
    qp = {"query": ["冷門查詢無資料"], "user_id": USER_ID, "org_id": ORG_ID}
    h = DeepResearchHandler(qp, _mock_http_handler())
    sid = await h._create_dr_session()
    # F31 真 shape：no-results item 是 @type:"Item"、url:"internal://no-results"、無 schema_object
    results = [{
        "@type": "Item",
        "url": "internal://no-results",
        "name": "查無相關資料：冷門查詢無資料",
        "site": "系統訊息",
        "siteUrl": "internal",
        "score": 0,
        "description": "# 查無相關資料\n\n針對「冷門查詢無資料」的搜尋未找到任何相關文件。",
    }]  # len==1 → 研究成功（查無資料也是結果），非斷線空 list
    report_obj = {"report": "# 深度研究報告：冷門查詢無資料\n\n查無相關資料。",
                  "sources": [], "query": "冷門查詢無資料",
                  "timestamp": int(time.time() * 1000),
                  "persist_marker": str(uuid.uuid4())}  # [R4 C3] 唯一 write marker
    ok = await h._persist_research_report(sid, report_obj, results)
    assert ok is True, "results=[查無資料 error item]（len==1）應 persist（查無資料也是合法研究結果），卻被誤 skip"
    svc = SessionService()
    row = await svc.get_session(sid, USER_ID, ORG_ID)
    got = row["research_report"]
    if isinstance(got, str):
        got = json.loads(got)
    assert got is not None and got["report"].startswith("# 深度研究報告"), \
        "查無資料報告未寫入 DB（gate 被改壞成有 error item 也 skip）"


@pytest.mark.asyncio
async def test_persist_verify_readback_fails_on_owner_mismatch(monkeypatch):
    """B3/F22 silent-fail 防護 + [R3 should-fix 2 / R4 C3] 唯一 write marker 比對強化。

    **R3 強化重點**：R2 的讀回驗證只比 truthiness（research_report 非空就算成功）。
    但若 DB 本就有一份**舊的非空 report**（同 session 之前存過），這次 UPDATE 命中 0 rows
    （owner mismatch / 其他原因沒生效）時，讀回撈到**舊的非空 report** → 只比 truthiness 會
    **誤判「寫成功」**。正確判準：讀回的 `research_report.persist_marker == 這次 report_obj["persist_marker"]`
    （唯一 marker）才算真寫入。
    **[R4 C3]** marker 從毫秒 timestamp 改為 `persist_marker`（UUID）——毫秒理論上兩次 persist 可能撞，
    UUID 零成本消除。

    **[R6 BLOCKER1 修正——不可用 caplog]** DR handler 的 module-level logger 是
    `get_configured_logger("deep_research_handler")` = **LazyLogger**（deep_research.py:25，[verified]）——
    同 BLOCKER1 前提：LazyLogger 的 error/warning 走 async worker → 底層 `logging.getLogger` propagate=False
    → pytest **caplog 抓不到**。改用 monkeypatch 蒐集 `deep_research.logger.error`/`.warning` 呼叫。
    """
    import methods.deep_research as dr_mod
    svc = SessionService()
    # 先建 session 並寫入一份「舊 report」（非空，persist_marker=OLD_MARKER）
    existing = await svc.create_session(user_id=USER_ID, org_id=ORG_ID, title="有舊報告的 session")
    sid = existing["id"]
    OLD_MARKER = str(uuid.uuid4())
    await svc.update_session(sid, USER_ID, ORG_ID,
                             {"research_report": {"report": "# 舊報告\n舊內容", "persist_marker": OLD_MARKER}})

    qp = {"query": ["x"], "user_id": USER_ID, "org_id": ORG_ID}
    h = DeepResearchHandler(qp, _mock_http_handler())

    # [R6 BLOCKER1] monkeypatch DR handler 的 module-level logger.error/.warning 蒐集呼叫
    #（LazyLogger 走 async worker + propagate=False，caplog 抓不到——直接 patch logger 方法）。
    logged = []
    monkeypatch.setattr(dr_mod.logger, "error", lambda msg, *a, **k: logged.append(str(msg)))
    monkeypatch.setattr(dr_mod.logger, "warning", lambda msg, *a, **k: logged.append(str(msg)))

    # monkeypatch update_session → 假回 True 但**不真寫**（模擬 F22：UPDATE 命中 0 rows 仍回 True）。
    # 這樣 DB 裡仍是舊 report（persist_marker=OLD_MARKER），讀回撈到的是舊的。
    async def _fake_update(self, session_id, user_id, org_id, updates):
        return True  # 假成功，DB 未變
    monkeypatch.setattr(SessionService, "update_session", _fake_update)

    NEW_MARKER = str(uuid.uuid4())
    report_obj = {"report": "# 新報告\n新內容", "sources": [], "query": "x", "persist_marker": NEW_MARKER}
    ok = await h._persist_research_report(sid, report_obj, [{"url": "u1", "description": "d"}])
    # 讀回撈到舊 report（persist_marker=OLD ≠ NEW）→ marker 不符 → 應回 False（只比 truthiness 會誤判 True）
    assert ok is False, \
        "DB 有舊非空 report + UPDATE 未生效，讀回撈到舊的卻誤判寫成功（B3 只比 truthiness、未比 persist_marker）"
    # 必須留可見 log 指出 persist 未生效（no-silent-fail）——用 monkeypatch 蒐集的 logged（非 caplog）
    assert any(("persist" in m.lower() or "readback" in m.lower()
                or "verify" in m.lower() or "marker" in m.lower()
                or "mismatch" in m.lower() or "讀回" in m)
               for m in logged), \
        "讀回 marker 比對失敗未留 warning/error log（silent fail；已 monkeypatch logger.error/.warning 蒐集）"

    # 對照第二 case（owner mismatch → get_session 回 None → 也應回 False）：
    monkeypatch.undo()  # 一次還原全部 monkeypatch（真 update_session + logger.error/.warning）
    # 還原後 logger 已非 patched——第二 case 不需驗 log，只驗 return False
    h2 = DeepResearchHandler(qp, _mock_http_handler())
    foreign_sid = str(uuid.uuid4())  # 不屬 USER_ID/ORG_ID
    ok2 = await h2._persist_research_report(
        foreign_sid, {"report": "# 報告\n內容", "persist_marker": str(uuid.uuid4())}, [{"url": "u1"}])
    assert ok2 is False, "owner 不匹配、get_session 回 None，persist 卻回 True（B3 silent fail 未修）"


@pytest.mark.asyncio
async def test_create_dr_session_anonymous_returns_bare_uuid():
    """無 user/org（未登入）→ 回 bare UUID、不建 DB row、不炸。"""
    qp = {"query": ["x"]}  # 無 user_id/org_id
    h = DeepResearchHandler(qp, _mock_http_handler())
    sid = await h._create_dr_session()
    assert sid is not None  # bare UUID，pipeline 不被 block


def test_build_research_report_obj_embeds_rerun_state():
    """[假綠防線] report_obj 內層塞入 handler._rerun_state_subset，key='rerunState'、是 dict。"""
    from reasoning.orchestrator import build_rerun_state_subset
    from types import SimpleNamespace
    qp = {"query": ["台灣再生能源"], "user_id": USER_ID, "org_id": ORG_ID}
    h = DeepResearchHandler(qp, _mock_http_handler())
    # 模擬 orchestrator 在 _cache_research_state 後掛上的子集
    fake_state = SimpleNamespace(
        query="台灣再生能源", mode="discovery", temporal_context=None,
        enable_kg=True, enable_web_search=False,
        formatted_context="[1] 來源 - 標題\n內容\n",
        source_map={1: {"url": "u1", "title": "t1"}},
        current_context=[{"url": "u1", "title": "t1"}],
        items=[{"url": "u1"}, {"url": "u2"}],
        query_id="query_1699999999999",             # [R4 修訂 RC1] 補 query_id（build_rerun_state_subset 讀 state.query_id，缺則 AttributeError）
    )
    h._rerun_state_subset = build_rerun_state_subset(fake_state)
    results = [{"schema_object": {}, "description": "報告內容"}]
    report_obj = h._build_research_report_obj("# 報告", ["u1"], results)
    assert isinstance(report_obj.get("rerunState"), dict), "report_obj 內層必須含 rerunState dict"
    assert "items" not in report_obj["rerunState"], "rerunState 不存全量 items"
    assert report_obj["rerunState"]["items_count"] == 2
    # source_map 是 str-key（build 已轉，供 JSON）
    assert set(report_obj["rerunState"]["source_map"].keys()) == {"1"}


def test_build_research_report_obj_no_rerun_state_when_absent():
    """handler 無 _rerun_state_subset（如 rerun 自己不再 embed）→ report_obj 不塞 rerunState（不塞 None）。"""
    qp = {"query": ["x"], "user_id": USER_ID, "org_id": ORG_ID}
    h = DeepResearchHandler(qp, _mock_http_handler())
    # 不設 h._rerun_state_subset
    results = [{"schema_object": {}, "description": "報告"}]
    report_obj = h._build_research_report_obj("# 報告", ["u1"], results)
    assert "rerunState" not in report_obj, "無子集時不該塞 rerunState 空殼"


@pytest.mark.asyncio
async def test_rerun_state_persists_nested_in_research_report():
    """rerunState 隨 research_report persist 進 DB → get_session 讀回內層完整（強斷言 isinstance）。"""
    from reasoning.orchestrator import build_rerun_state_subset, restore_rerun_state_from_report
    from types import SimpleNamespace
    qp = {"query": ["台灣再生能源"], "user_id": USER_ID, "org_id": ORG_ID}
    h = DeepResearchHandler(qp, _mock_http_handler())
    sid = await h._create_dr_session()

    fake_state = SimpleNamespace(
        query="台灣再生能源", mode="discovery", temporal_context={"is_temporal_query": False},
        enable_kg=True, enable_web_search=False,
        formatted_context="[1] 來源A - 文章一\n內容一\n",
        source_map={1: {"url": "u1", "title": "文章一", "description": "內容一"}},
        current_context=[{"url": "u1", "title": "文章一", "description": "內容一"}],
        items=[{"url": "u1"}, {"url": "u2"}, {"url": "u3"}],
        query_id="query_1699999999999",             # [R4 修訂 RC1] 補 query_id（build_rerun_state_subset 讀 state.query_id，缺則 AttributeError）
    )
    h._rerun_state_subset = build_rerun_state_subset(fake_state)
    results = [{"schema_object": {}, "description": "報告內容"}]
    report_obj = h._build_research_report_obj("# 報告內容", ["u1"], results)

    ok = await h._persist_research_report(sid, report_obj, results)
    assert ok is True, "persist 應成功（results 非空 + marker 匹配）"

    # 讀回：get_session 走 _deserialize_session → research_report 是 dict、內層 rerunState 完整
    svc = SessionService()
    row = await svc.get_session(sid, USER_ID, ORG_ID)
    rr = row["research_report"]
    assert isinstance(rr, dict), "research_report 讀回必須是 dict（jsonb_fields deserialize）"
    assert isinstance(rr.get("rerunState"), dict), "內層 rerunState 必須是 dict（禁 str 容錯）"
    # nested list of dict 完整還原（強斷言，禁 if isinstance(str): json.loads 掩蓋）
    assert isinstance(rr["rerunState"]["source_map"], dict)
    assert isinstance(rr["rerunState"]["source_map"]["1"], dict)  # JSON key 是 str
    assert rr["rerunState"]["source_map"]["1"]["url"] == "u1"
    assert rr["rerunState"]["items_count"] == 3

    # 端到端：從讀回的 research_report 重建 rerunState → source_map key 轉回 int
    restored = restore_rerun_state_from_report(rr)
    assert restored is not None
    # [R2 修訂 N1] 全 key 檢查（不只第一個）——與 Task 2 test 對齊
    for k in restored["source_map"].keys():
        assert isinstance(k, int), f"重建後 source_map key 必須全是 int，得到 {type(k)}"
    assert restored["formatted_context"].startswith("[1]")
    assert len(restored["items"]) == 3  # placeholder，長度 = items_count
