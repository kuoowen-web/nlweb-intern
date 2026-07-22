"""
E2E test fixtures for Playwright-based regression suite.

紀律：
- module import 期零 env 依賴、零副作用——env 讀取一律在 fixture 執行期。
  （2026-07-06 health check 抓過 import 期爆炸炸 pytest collection 的同類病，
  本檔絕不可在 import 期讀 os.environ 必要值或做任何可能 raise 的事。）
- 未設定 env → pytest.fail 帶明確訊息：fail-loud，但只 fail e2e 測試本身，
  不炸 collection、不影響其他 suite。
- 真實帳號走真實 login flow，絕不 bypass。憑證由 Zoe/CEO 執行前當場確認
  目標環境現行值（文件記載可能過時、本地/prod 可能不同步）。
- 登入流程 selector 的「單一事實來源」在本檔常數區——MCP 探索後只回寫這裡，
  test 檔不得自帶登入 selector 副本（防兩處漂移）。
"""
import os
import time
from dataclasses import dataclass, fields
from typing import Any, Dict

import pytest
from playwright.sync_api import Browser, Page, expect

# 🔧 fix（登入 rate-limit，2026-07-22）：一次性標記——session 全程只應被 append 一次，
# 用來在驗收時證明「11 個 test 只觸發一次真實 login submit」（見
# _login_storage_state fixture 內的 append 呼叫）。
_LOGIN_SUBMIT_LOG: list = []

# ── 登入流程 selector 常數區（單一事實來源；Task 2 Step 1 MCP 探索後在此替換）──
# sentinel 防呆：未替換前 login_selectors fixture 會 fail-loud；
# commit 前另有 grep gate（grep 到 REPLACE_ME 禁 commit）。
_REPLACE_ME = "REPLACE_ME_AFTER_MCP_EXPLORATION"

# MCP 探索（2026-07-08，chrome-devtools CDP-native 實走登入流程）確認：
# - 未登入時 auth modal 於載入後自動開啟（#authModalOverlay），一般不需點 trigger。
# - 頁面共有 4 個 input[type='email']（invite/login/forgot/feedback）與 4 個
#   input[type='password']（current/new/confirm/login）——泛型 selector 會撞
#   Playwright strict mode，必須用 #loginEmail / #loginPassword 專屬 id。
# - 登入成功視覺標誌：#btnSettings（側欄左下使用者按鈕，顯示使用者名稱）；
#   已驗證登出態不可見、登入後可見。popover 內的 #userMenu / #btnLogout
#   平時隱藏（需展開 popover），不適合當 success 訊號。
LOGIN_TRIGGER_SELECTOR  = "#btnShowLogin"                    # 登入入口（popover 內登入鈕）
EMAIL_INPUT_SELECTOR    = "#loginEmail"                      # 登入 modal email input
PASSWORD_INPUT_SELECTOR = "#loginPassword"                   # 登入 modal password input
SUBMIT_SELECTOR         = "#loginForm button[type='submit']" # 登入 form 內唯一 submit
LOGIN_SUCCESS_SELECTOR  = "#btnSettings"                     # 已登入視覺元素（使用者按鈕）
# ──────────────────────────────────────────────────────────────────────────────

SCREENSHOTS_DIR = os.path.join(os.path.dirname(__file__), "screenshots")


def _ensure_screenshots_dir() -> str:
    """統一 mkdir helper——所有截圖寫入前經過這裡，路徑錨定本檔位置、與 cwd 無關。"""
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    return SCREENSHOTS_DIR


@dataclass(frozen=True)
class E2ECredentials:
    email: str
    password: str


@dataclass(frozen=True)
class LoginSelectors:
    trigger: str
    email_input: str
    password_input: str
    submit: str
    success: str


def _require_env(name: str) -> str:
    """fixture 執行期讀 env；未設定 → 明確 fail（不靜默、不炸 collection）。"""
    value = os.environ.get(name, "")
    if not value:
        pytest.fail(
            f"E2E 測試需要環境變數 {name}（未設定或為空）。"
            f"執行前由 Zoe/CEO 當場確認目標環境（本地/prod）的現行憑證後設入"
            f"環境變數——勿沿用文件記載的明文（可能過時，且本地/prod 憑證"
            f"可能不同步）。設定方式見 two-stage-e2e-plan.md 登入處理段。",
            pytrace=False,
        )
    return value


@pytest.fixture(scope="session")
def e2e_credentials() -> E2ECredentials:
    """帳密 fixture——測試一律注入此 fixture 取得帳密，禁止顯式 import conftest。"""
    return E2ECredentials(
        email=_require_env("E2E_EMAIL"),
        password=_require_env("E2E_PASSWORD"),
    )


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.environ.get("E2E_BASE_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def screenshots_dir() -> str:
    """截圖目錄 fixture——test 檔用它組截圖路徑，不自己拼相對路徑（cwd 無關）。"""
    return _ensure_screenshots_dir()


@pytest.fixture(scope="session")
def login_selectors() -> LoginSelectors:
    """登入 selector fixture——執行期驗 sentinel 已替換，未替換 fail-loud。"""
    selectors = LoginSelectors(
        trigger=LOGIN_TRIGGER_SELECTOR,
        email_input=EMAIL_INPUT_SELECTOR,
        password_input=PASSWORD_INPUT_SELECTOR,
        submit=SUBMIT_SELECTOR,
        success=LOGIN_SUCCESS_SELECTOR,
    )
    unreplaced = [
        f.name for f in fields(selectors)
        if "REPLACE_ME" in getattr(selectors, f.name)
    ]
    if unreplaced:
        pytest.fail(
            f"登入 selector 尚未由 MCP 探索替換：{unreplaced}。"
            f"先跑 chrome-devtools MCP 探索段確認真實 selector，"
            f"回寫 conftest.py 常數區後再跑腳本"
            f"（見 two-stage-e2e-plan.md Task 2 Step 1 / Step 3）。",
            pytrace=False,
        )
    return selectors


def _wait_login_decidable(
    page: Page, selectors: LoginSelectors, timeout_ms: int = 15000
) -> None:
    """
    等頁面到達「可判定狀態」：已登入視覺元素或 login email input 任一可見。
    - 不用 networkidle：本 app 有 analytics beacon 常駐連線（2026-07-08 實測
      30s timeout，flaky）。
    - 不用 wait_for_selector 複合 selector：它只鎖定 DOM 順序第一個匹配元素
      等其可見（"Proceeding with the first one"），#btnSettings 常駐 DOM 但
      登出時隱藏 → 必 timeout（2026-07-08 實測）。
    - 改顯式輪詢 is_visible()（不等待、無 strict 陷阱），到 deadline 未達
      可判定狀態 → pytest.fail fail-loud。
    """
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if (
            page.locator(selectors.success).first.is_visible()
            or page.locator(selectors.email_input).first.is_visible()
        ):
            return
        page.wait_for_timeout(250)
    pytest.fail(
        f"頁面 {timeout_ms}ms 內未達可判定狀態（{selectors.success} 與 "
        f"{selectors.email_input} 皆不可見）——server 未起、前端載入異常、"
        f"或 selector 已過期（需重跑 MCP 探索段）。",
        pytrace=False,
    )


@pytest.fixture(scope="session")
def wait_login_decidable():
    """頁面載入等待 helper——test 檔以 fixture 注入取得（不 import conftest）。"""
    return _wait_login_decidable


def _perform_real_login(
    page: Page,
    base_url: str,
    e2e_credentials: E2ECredentials,
    login_selectors: LoginSelectors,
) -> None:
    """
    在給定 page 上跑一次完整真實 login flow（不 bypass）。
    return 前必驗「已登入視覺元素可見」——不憑 networkidle 假設登入成功。

    🔧 fix（登入 rate-limit，2026-07-22）：從舊版 logged_in_page fixture 原樣抽出，
    供 _login_storage_state（session 僅呼叫一次）與（未來若需要）其他呼叫點共用。
    邏輯本身不變，只是从「每個 test 都跑一次」改成「session 內只跑一次」。
    """
    page.goto(f"{base_url}/")
    _wait_login_decidable(page, login_selectors)

    # 已登入（已登入視覺元素「可見」，不只存在——AR R2 殘留 1 採納）→ 直接返回
    if page.locator(login_selectors.success).first.is_visible():
        return

    # 頁上尚無 email input → 主動點登入入口開 modal
    if page.locator(login_selectors.email_input).count() == 0:
        page.click(login_selectors.trigger)
        page.wait_for_selector(login_selectors.email_input, timeout=5000)

    page.fill(login_selectors.email_input, e2e_credentials.email)
    page.fill(login_selectors.password_input, e2e_credentials.password)
    page.click(login_selectors.submit)

    # 登入後必須驗到已登入視覺元素可見才 return
    expect(page.locator(login_selectors.success)).to_be_visible(timeout=10000)


@pytest.fixture(scope="session")
def _login_storage_state(
    browser: Browser,
    base_url: str,
    e2e_credentials: E2ECredentials,
    login_selectors: LoginSelectors,
) -> Dict[str, Any]:
    """
    🔧 fix（登入 rate-limit，2026-07-22）：session 全程只真實登入這一次。

    根因：舊版 logged_in_page 是 function-scoped 且依賴 function-scoped 內建
    `page` fixture——每個 test 都開全新 browser context（空 cookie/localStorage）
    → 每個 test 各跑一次完整 login flow → 打一次登入端點。test_kg_overhaul_flow.py
    A 組 11 個 test 短時間內就對登入端點發 11 次請求，撞伺服器端「登入 10 次/分鐘」
    限流。

    修法（Playwright 官方建議模式：storage_state 復用）：本 fixture 用 session-scoped
    `browser` 開一個獨立的臨時 context（不透過 pytest-playwright 的 function-scoped
    `new_context`/`context`/`page`——那些會在每個 test 結束時關閉，不適合裝 session
    唯一一次的登入態），走一次 `_perform_real_login`（絕不 bypass、沿用既有 selector
    + 驗證邏輯），登入成功後用 `context.storage_state()` 擷取 cookie + localStorage，
    立刻關閉這個臨時 context/page（不再需要），回傳 storage_state dict 供
    `logged_in_page` 在每個 test 的獨立 context 中注入複用。

    storage_state 綁定 origin：本 fixture 與 `logged_in_page` 都用 `page.goto(f"{base_url}/")`
    走訪同一個 `base_url` fixture 值，cookie domain 一致（此處 `browser.new_context()` 故意
    不傳 `base_url` kwarg——避免與 `page.goto` 的完整 URL 重複定義同一資訊產生漂移風險）。
    """
    context = browser.new_context()
    page = context.new_page()
    try:
        _perform_real_login(page, base_url, e2e_credentials, login_selectors)
        _LOGIN_SUBMIT_LOG.append(1)  # 一次性標記：證明真實 login flow 只跑這一次
        return context.storage_state()
    finally:
        context.close()


@pytest.fixture(scope="function")
def logged_in_page(
    new_context,
    base_url: str,
    login_selectors: LoginSelectors,
    _login_storage_state: Dict[str, Any],
) -> Page:
    """
    每個測試函式取得已登入的 page（獨立 context，保留 test 隔離性）。

    真實登入只在 `_login_storage_state`（session-scoped）發生一次；本 fixture
    用 pytest-playwright 內建 `new_context` callback fixture 開一個「注入已登入
    storage_state」的全新 context——每個 test 仍拿到彼此獨立、互不污染的 context
    （原 function scope 的正當理由：test 隔離性），但 context 一建立就已是登入態，
    不需再走 email/password 填寫 → 不再對登入端點發請求。

    `new_context` 是 pytest-playwright 提供的 function-scoped callback fixture，
    自動處理 tracing/screenshot/video 掛鉤與 test 結束後的 context.close()（沿用
    既有 artifact 機制，不用自己管生命週期）。

    return 前仍必驗「已登入視覺元素可見」——不假設 storage_state 一定生效
    （cookie 可能過期/被伺服器端提前失效），失效時 fail-loud 而非讓後續斷言
    在未登入頁面上瞎跑。
    """
    context = new_context(storage_state=_login_storage_state)
    page = context.new_page()
    page.goto(f"{base_url}/")
    _wait_login_decidable(page, login_selectors)

    if not page.locator(login_selectors.success).first.is_visible():
        pytest.fail(
            "logged_in_page：注入 session 登入態（storage_state）後，"
            f"{login_selectors.success} 仍不可見——storage_state 可能已被伺服器"
            "端使 cookie 失效，或前端登入態判斷邏輯已變更。不會靜默走完整 login "
            "flow 掩蓋（會重新觸發登入端點、違背本 fixture 存在的目的），"
            "請重新診斷。",
            pytrace=False,
        )
    return page


def pytest_sessionfinish(session, exitstatus):
    """
    🔧 fix（登入 rate-limit，2026-07-22）：session 結束時印出真實 login submit 次數。
    驗收證據用——`_LOGIN_SUBMIT_LOG` 只在 `_login_storage_state`（session-scoped，
    全程只執行一次）內被 append，故不論本 session 跑了幾個依賴 `logged_in_page` 的
    test，這裡印出的次數應恆為 0（完全沒 test 觸發過 _login_storage_state）或 1
    （至少一個 test 觸發，且僅觸發一次）。
    """
    print(f"\n[E2E] 真實 login submit 次數（本 session）：{len(_LOGIN_SUBMIT_LOG)}")


def pytest_runtest_makereport(item, call):
    """FAIL 時自動截圖存到 tests/e2e/screenshots/"""
    if call.when == "call" and call.excinfo:
        page = item.funcargs.get("page") or item.funcargs.get("logged_in_page")
        if page:
            out_dir = _ensure_screenshots_dir()
            fname = f"{item.name}_fail.png"
            page.screenshot(path=os.path.join(out_dir, fname))
