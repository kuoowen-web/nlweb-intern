"""
Landing subpages E2E：未登入訪 /blog、/blog/<slug>、/faq、GET /api/faq 皆 200；
FAQ 頁 render 出內容；slug 白名單防遍歷自動化守護（encoded traversal + bad chars +
尾隨換行 %0a，非法 slug 由 middleware fail-closed 回 404 非 401）；公開頁 GET-only
（未登入非 GET → 401）；/、/app 行為不變。
前置：本地 server 跑著（bash scripts/e2e.sh 前置）、migration 已 apply（faqs 已 seed）。
server 位址注入 conftest 的 session-scoped base_url fixture（讀 E2E_BASE_URL）。
本檔全部 case 未登入、用內建 page fixture（非 logged_in_page）。
"""
import re

import pytest
from playwright.sync_api import expect

PLACEHOLDER_SLUG = "hello-dubao"  # 與 Task 5 placeholder 檔名一致


def test_faq_page_renders(page, base_url):
    """未登入訪 /faq → 200、無 401 redirect、render 出 FAQ 內容。"""
    resp = page.goto(f"{base_url}/faq")
    assert resp.status == 200
    expect(page).to_have_title(re.compile("常見問題"))
    # faq.js fetch /api/faq 後至少渲染出一個 FAQ 項（seed 後非空）
    expect(page.locator(".faq-item").first).to_be_visible(timeout=5000)


def test_faq_api_public(page, base_url):
    """GET /api/faq 未登入 200，回 seed 後的條目（faqs 非空陣列）。"""
    resp = page.request.get(f"{base_url}/api/faq")
    assert resp.status == 200
    data = resp.json()
    # 🔧 land-diff R1（Codex SF-2）：>= 21 鎖 seed 完整性下限（help.js 當下 21 條）。
    # 不用 == 21——faqs 是活資料，未來 admin 加條目後 == 會脆化；>= 守住「seed 沒漏塞」。
    assert len(data["faqs"]) >= 21
    # 欄位契約
    first = data["faqs"][0]
    assert set(first.keys()) == {"id", "question", "answer", "category", "sort_order"}


def test_blog_list_public(page, base_url):
    """未登入訪 /blog → 200，render 列表。"""
    resp = page.goto(f"{base_url}/blog")
    assert resp.status == 200
    expect(page).to_have_title(re.compile("部落格"))
    expect(page.locator(".blog-card").first).to_be_visible()


def test_blog_post_public(page, base_url):
    """未登入訪 /blog/<placeholder-slug> → 200，render 單篇。"""
    resp = page.goto(f"{base_url}/blog/{PLACEHOLDER_SLUG}")
    assert resp.status == 200
    expect(page.locator(".post-main")).to_be_visible()


def test_blog_bad_slug_404(page, base_url):
    """不存在（但合法）slug → 404（不 500），驗檔案不存在路徑。"""
    resp = page.request.get(f"{base_url}/blog/definitely-not-a-real-post")
    assert resp.status == 404


# 🔧 R1（SF-2）+ R2（B-R2-1 / nit-1）：slug 白名單自動化守護——非法 /blog/ path 由
# middleware fail-closed 回 404（不是 401「請登入」）。把白名單拿掉這幾個仍 404 才有意義。
# 用 page.request.get（不經瀏覽器 URL 正規化那層，直接打 server）；未登入 → 若 middleware
# 沒 fail-closed 會回 401，斷言 404 同時守護「非法 URL 語義=找不到、非要登入」。
def test_blog_traversal_guard_encoded(page, base_url):
    """守護：encoded path traversal 必 404（middleware fail-closed，非 401）。"""
    resp = page.request.get(f"{base_url}/blog/..%2f..%2fetc%2fpasswd")
    assert resp.status == 404


def test_blog_traversal_guard_bad_chars(page, base_url):
    """守護：大寫+底線（未過 ^[a-z0-9-]+\\Z）必 404（middleware fail-closed，非 401）。"""
    resp = page.request.get(f"{base_url}/blog/Foo_Bar")
    assert resp.status == 404


def test_blog_guard_trailing_newline(page, base_url):
    """🔧 R2（nit-1）守護：%0a 尾隨換行（decode 後 hello\\n）必 404，\\Z 錨封 $ 縫。"""
    resp = page.request.get(f"{base_url}/blog/hello%0a")
    assert resp.status == 404


def test_public_subpages_get_only(page, base_url):
    """🔧 R3-post（N3-1）守護：公開頁只放行 GET/HEAD——未登入 POST 打 /blog、/faq →
    middleware 短路回 401（auth 層，在 router 405 之前）。若哪天有人把 /blog/faq 誤放
    all-method PUBLIC_ENDPOINTS，POST 會變 405 或 200 → 本 case 紅、擋回。"""
    for path in ["/blog", "/faq"]:
        resp = page.request.post(f"{base_url}{path}")
        assert resp.status == 401, f"POST {path} 應 401（GET/HEAD-only 放行），實得 {resp.status}"


# 🔧 land-diff R1（Codex SF-1 + agy nit）：public read 放行 HEAD——crawler / link
# checker / uptime probe 的 HEAD 探測公開頁不該回 401。
def test_public_subpages_head_ok(page, base_url):
    """守護：未登入 HEAD 打公開頁 → 200（非 401）。"""
    for path in ["/faq", "/blog", f"/blog/{PLACEHOLDER_SLUG}", "/api/faq"]:
        resp = page.request.fetch(f"{base_url}{path}", method="HEAD")
        assert resp.status == 200, f"HEAD {path} 應 200（public read 含 HEAD），實得 {resp.status}"


# 🔧 land-diff R1（Codex SF-1）：非法 /blog/ path 的 fail-closed 404 不綁 method——
# 非 GET 打非法 slug 也該是「找不到」，非「請登入」401。
def test_blog_bad_slug_non_get_404(page, base_url):
    """守護：POST（非 GET）打非法 slug /blog/Foo_Bar → 404（fail-closed 不綁 method）。"""
    resp = page.request.post(f"{base_url}/blog/Foo_Bar")
    assert resp.status == 404, f"POST /blog/Foo_Bar 應 404（非法 slug fail-closed），實得 {resp.status}"


def test_root_and_app_unchanged(page, base_url):
    """回歸：/ 仍是 landing、/app 仍是產品頁。"""
    r1 = page.goto(f"{base_url}/")
    assert r1.status == 200
    expect(page).to_have_title(re.compile("臺灣讀豹"))
    r2 = page.goto(f"{base_url}/app")
    assert r2.status == 200
    expect(page).to_have_title(re.compile("新聞搜尋"))


def test_landing_nav_links_to_subpages(page, base_url):
    """未登入訪 / → landing nav 有指向 /blog 與 /faq 的連結（件 1 接線）。"""
    resp = page.goto(f"{base_url}/")
    assert resp.status == 200
    expect(page.locator('.header-nav a[href="/blog"]')).to_be_visible()
    expect(page.locator('.header-nav a[href="/faq"]')).to_be_visible()
