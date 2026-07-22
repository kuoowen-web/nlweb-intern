"""
登入流程 E2E 回歸腳本。
Pilot 腳本——驗可行性，不求覆蓋所有登入邊界。
- 真實帳密由 e2e_credentials fixture 注入（fixture 執行期讀環境變數）。
- 登入 selector 單一事實來源在 conftest.py 常數區，由 login_selectors fixture
  注入（sentinel 未替換時 fixture fail-loud）——本檔不自帶登入 selector 副本，
  故本檔沒有 module-level sentinel assert（防呆由 fixture + commit 前 grep gate 涵蓋）。
- 禁止 `from conftest import ...`——conftest 不該被顯式 import
（pytest anti-pattern，模組解析路徑不穩定），一律 fixture 注入。
"""
import os

from playwright.sync_api import Page, expect


def test_login_with_valid_credentials_succeeds(
    page: Page, base_url: str, e2e_credentials, login_selectors, screenshots_dir,
    wait_login_decidable,
) -> None:
    # e2e_credentials / login_selectors / screenshots_dir / wait_login_decidable
    # 皆為 conftest fixture
    # （不加型別註記——加註記就得 import conftest，正是要避免的 anti-pattern）
    """
    使用真實帳號登入，驗證成功後看到已登入狀態。
    判定標準：截圖可見 user menu / 已登入視覺元素。
    """
    # 1. 前往首頁
    page.goto(base_url)
    # networkidle 在本 app 不可靠（analytics beacon 常駐連線）——改等
    # 「可判定狀態」：已登入視覺元素或 login email input 任一可見。
    wait_login_decidable(page, login_selectors)

    # 2. 頁上尚無 email input → 點登入入口開 modal
    #    （本地 UI：未登入時 auth modal 自動開啟，通常直接進第 3 步）
    if page.locator(login_selectors.email_input).count() == 0:
        page.click(login_selectors.trigger)
        page.wait_for_selector(login_selectors.email_input, timeout=5000)

    # 3. 填入帳密（帳密由 e2e_credentials fixture 注入，執行期讀 env）
    page.fill(login_selectors.email_input, e2e_credentials.email)
    page.fill(login_selectors.password_input, e2e_credentials.password)

    # 4. 點擊送出（不用 Enter，避免 isTrusted 問題）
    page.click(login_selectors.submit)

    # 5. 視覺驗證：必須看到已登入元素，之後才截圖留存
    expect(page.locator(login_selectors.success)).to_be_visible(timeout=10000)
    page.screenshot(path=os.path.join(screenshots_dir, "login_success.png"))
