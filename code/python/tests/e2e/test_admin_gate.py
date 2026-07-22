"""
E2E: W-1 儀表板 admin gate 真環境驗證（full-scan P1 批1）。

未登入（不帶 session cookie）打 analytics / ranking dashboard 端點 → 應 401
`Authentication required`（修復前：零 role 檢查、任一請求得 200 + 全平台資料）。

純 HTTP 探測，不寫資料、不燒 LLM。用 page.request（Playwright APIRequestContext，
不帶頁面 cookie = 未登入等效）。

「登入但非 admin → 403」那半需要非 admin 帳號，prod 環境通常只有 admin 帳號可用；
該半由 unit 層 test_analytics_admin_gate.py（fake_auth 注入非 admin role）覆蓋，
本 E2E 只驗真環境「未登入被 401 擋」這一不變式。
env：E2E_BASE_URL（預設 localhost:8000）。
"""
import pytest

# W-1 gate 覆蓋的 dashboard 端點（analytics 4 GET + ranking config；pipeline 需 query_id 略）
_GATED_ENDPOINTS = [
    "/api/analytics/stats",
    "/api/analytics/queries",
    "/api/analytics/top_clicks",
    "/api/analytics/export_training_data",
    "/api/ranking/config",
]


@pytest.mark.parametrize("endpoint", _GATED_ENDPOINTS)
def test_dashboard_endpoint_requires_auth(page, base_url, endpoint):
    """未登入打 dashboard 端點 → 401（不放行全平台資料）。"""
    # page.request 是獨立 APIRequestContext，不帶頁面登入 cookie = 未登入
    resp = page.request.get(f"{base_url}{endpoint}")
    status = resp.status
    body = resp.text()[:200]

    assert status == 401, (
        f"{endpoint} 未登入時回 {status}（預期 401）——admin gate 未擋未登入請求！"
        f"body={body}"
    )
    assert "auth" in body.lower() or "authentication" in body.lower(), (
        f"{endpoint} 回 401 但 body 非 auth 錯誤訊息：{body}"
    )
