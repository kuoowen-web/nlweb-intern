"""SSE registry 多維度一致性契約（🔧 AR R1 B1）。

各維度各自對帳，禁 assert 三方相等（見 plan §0.3 / Task 8 Step 5）：
- 維度 1：registry.SERVER_HISTORY_SANITIZE_TYPES == session_service._BAD_MESSAGE_TYPES
- 維度 2：registry.FRONTEND_SKIP_RENDER_TYPES == sse-types.js SKIP_TYPES（test-time 跨讀）
- 維度 3：registry.FRONTEND_KNOWN_TYPES == sse-dispatch.js KNOWN_TYPES（test-time 跨讀）
- 反向護欄：SERVER_HISTORY_SANITIZE_TYPES != FRONTEND_SKIP_RENDER_TYPES（remember 反例）

🔧R5F（R5-SF-E）：跨語言讀 JS `export const <NAME> = new Set([...])` 成員（test-time 對帳，
非瀏覽器 runtime——守 G1 無 build）。只在 CI/pytest 環境讀 JS 原始碼 regex 抽集合。
"""
import os
import re

# dirname = code/python/tests/unit/core → 5 個 .. 回 repo 根，再進 static/js/features。
_JS_DIR = os.path.join(os.path.dirname(__file__),
                       "..", "..", "..", "..", "..", "static", "js", "features")


def _js_set_members(js_filename, const_name):
    """從 sse-types.js / sse-dispatch.js 的 `export const <const_name> = new Set([...])`
    抽出字串字面量成員（單/雙引號皆收）。找不到該常數 → 明確 fail（不 silent 回空集假綠）。"""
    path = os.path.join(_JS_DIR, js_filename)
    with open(path, encoding="utf-8") as f:
        src = f.read()
    # 抓 `new Set([ ... ])` 的中括號內容（跨行）
    m = re.search(const_name + r"\s*=\s*new Set\(\s*\[(.*?)\]\s*\)", src, re.DOTALL)
    assert m, f"{js_filename} 找不到 `export const {const_name} = new Set([...])`"
    return frozenset(re.findall(r"""['"]([^'"]+)['"]""", m.group(1)))


def test_server_sanitize_matches_bad_message_types():
    # 維度 1 契約：registry 的 sanitize 集 == session_service 實際用的黑名單
    from core.session_service import SessionService
    from core.sse.registry import SERVER_HISTORY_SANITIZE_TYPES
    assert set(SessionService._BAD_MESSAGE_TYPES) == set(SERVER_HISTORY_SANITIZE_TYPES)


def test_frontend_skip_matches_registry():
    # 維度 2 契約：前端 SKIP_TYPES（sse-types.js）== registry 的 FRONTEND_SKIP_RENDER_TYPES
    # （test-time 跨讀，非瀏覽器 runtime；不涉維度 1）
    from core.sse.registry import FRONTEND_SKIP_RENDER_TYPES
    js_skip = _js_set_members("sse-types.js", "SKIP_TYPES")
    assert js_skip == frozenset(FRONTEND_SKIP_RENDER_TYPES), (
        f"前端 SKIP_TYPES 與 registry.FRONTEND_SKIP_RENDER_TYPES 漂移："
        f"僅 JS={js_skip - frozenset(FRONTEND_SKIP_RENDER_TYPES)}、"
        f"僅 registry={frozenset(FRONTEND_SKIP_RENDER_TYPES) - js_skip}")


def test_frontend_known_matches_registry():
    # 🔧R5F（R5-SF-E）維度 3 契約：前端 sse-dispatch.js 的 KNOWN_TYPES ==
    # registry.FRONTEND_KNOWN_TYPES（test-time 跨讀對帳）。缺此 test 時 KNOWN 兩側可獨立漂移——
    # 前端漏登記某 render 型別 → classifyEnvelope 判它 unknown → 該 live event 靜默走 unknown 分支。
    from core.sse.registry import FRONTEND_KNOWN_TYPES
    js_known = _js_set_members("sse-dispatch.js", "KNOWN_TYPES")
    assert js_known == frozenset(FRONTEND_KNOWN_TYPES), (
        f"前端 KNOWN_TYPES 與 registry.FRONTEND_KNOWN_TYPES 漂移："
        f"僅 JS={js_known - frozenset(FRONTEND_KNOWN_TYPES)}、"
        f"僅 registry={frozenset(FRONTEND_KNOWN_TYPES) - js_known}")


def test_sanitize_and_skip_are_NOT_forced_equal():
    # 🔧 B1 反向護欄：明確斷言兩維度**不同**（若有人日後想把它們對齊，此 test 擋下）
    from core.sse.registry import SERVER_HISTORY_SANITIZE_TYPES, FRONTEND_SKIP_RENDER_TYPES
    assert "remember" in SERVER_HISTORY_SANITIZE_TYPES
    assert "remember" not in FRONTEND_SKIP_RENDER_TYPES  # remember 要 render，不可被前端 skip
