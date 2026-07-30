"""
Landing page E2E：sections 齊全、tab 切換、sticky CTA、表單 round-trip。
前置：本地 server 跑著（bash scripts/e2e.sh 的標準前置）、migration 已 apply。
"""
import random
import re
import uuid

import pytest
from playwright.sync_api import expect

# server 位址一律注入 conftest.py 的 session-scoped `base_url` fixture（讀 E2E_BASE_URL）。
# 不自帶 BASE 常數——自帶會讓 scripts/e2e.sh 的 E2E_BASE_URL 對本檔失效（R2-S1）。
#
# page fixture：用 pytest-playwright 內建 `page`（與 test_cachebust / test_basic_search
# 等既有 e2e 檔一致——plan Task 10.1「以 conftest / pytest-playwright 慣例為準」）。
# 不自帶 sync_playwright() 版：raw sync API 在整個 e2e 目錄同跑時會撞
# 「Playwright Sync API inside the asyncio loop」（其他 test 留下 running loop）→
# 本檔 5 則變 ERROR at setup。內建 page 跑在獨立 driver greenlet，無此衝突。
# 1920×1080 viewport 由下方 browser_context_args override 設定。

FORBIDDEN_COPY = ["六大媒體", "數百萬", "準確率", "省下", "全台首創", "PostgreSQL",
                  "Qwen", "XGBoost", "MMR", "台大", "台灣大學"]


@pytest.fixture(scope="module")
def browser_context_args(browser_context_args):
    """landing 桌面驗收固定 1920×1080（沿 v06 設計稿寬度）。"""
    return {**browser_context_args, "viewport": {"width": 1920, "height": 1080}}


def test_landing_sections_and_redlines(page, base_url):
    page.goto(f"{base_url}/")
    for sid in ["about", "audience", "products", "progress", "team", "contact"]:
        expect(page.locator(f"#{sid}")).to_be_attached()
    expect(page).to_have_title(re.compile("臺灣讀豹"))
    body_text = page.locator("body").inner_text()
    for word in FORBIDDEN_COPY:
        assert word not in body_text, f"紅線字樣出現在頁面：{word}"


def test_audience_tab_switch(page, base_url):
    page.goto(f"{base_url}/")
    page.click("#tab-manager")
    expect(page.locator("#panel-manager")).to_be_visible()
    expect(page.locator("#panel-worker")).to_be_hidden()
    expect(page.locator("#panel-manager")).to_contain_text("你敢直接往上送嗎")


def test_sticky_cta_appears_after_hero(page, base_url):
    page.goto(f"{base_url}/")
    expect(page.locator(".sticky-cta")).to_be_hidden()
    page.locator("#team").scroll_into_view_if_needed()
    expect(page.locator(".sticky-cta")).to_be_visible()


def test_form_roundtrip(page, base_url):
    # 冪等性設計（S4）：5/hr rate limit 是 server in-memory sliding window
    # （rate_limit.py `_windows`，key = f"{path}:{client_ip}"），E2E 清不到。
    # get_client_ip（ip_utils.py BP-5，已親驗）**只在 direct peer ∈ _TRUSTED_PROXIES
    # （預設僅 '127.0.0.1'）時**採信 XFF 第一值 → 隨機 XFF = 全新 rate-limit key，
    # email 也隨機避免資料表堆同值。
    # 冪等（重跑不累積撞 5/hr）**只在 peer 鎖定條件成立時成立**：E2E_BASE_URL 必須
    # 用 http://127.0.0.1:8000（IPv4 字面），勿用 localhost——localhost 若解析走
    # ::1，peer='::1' ∉ 信任集、XFF 不被採信，隨機 key 失效、重跑仍累積。
    # 條件不成立（peer 非 127.0.0.1，或 TRUSTED_PROXIES 被覆寫排除 127.0.0.1）時
    # fallback = 重啟 server 清計數。
    # （XFF header 掛在 module-scoped page 上會延續到後續測試，僅影響 rate limit
    #   keying，無害。）
    fake_ip = f"10.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"
    page.set_extra_http_headers({"X-Forwarded-For": fake_ip})
    page.goto(f"{base_url}/")
    page.fill("input[name=name]", "E2E 測試")
    page.fill("input[name=email]", f"e2e-{uuid.uuid4().hex[:8]}@example.com")
    page.fill("textarea[name=purpose]", "E2E round-trip")
    page.click(".form-submit")
    expect(page.locator(".form-msg")).to_contain_text("已收到", timeout=5000)


def test_app_route_serves_product(page, base_url):
    page.goto(f"{base_url}/app")
    expect(page).to_have_title(re.compile("新聞搜尋"))
