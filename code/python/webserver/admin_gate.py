"""Shared admin-only gate for dashboard-style read endpoints.

full-scan-2026-07 W-1（D-2026-07-20 規則 2）：analytics / ranking 儀表板
讀取端點對「任一登入者」開放全平台資料。根解＝加 role==admin gate，堵住
「任一登入 member 可匯出全平台跨 org 資料」。

判定邏輯與 webserver/routes/admin.py:41-44 完全對齊（單一 source of truth
的 admin gate 慣用寫法，不自創）：
  - request['user'] 無 / 未 authenticated → 401
  - role != 'admin' → 403
  - admin → 放行

用法（decorator，套在 aiohttp handler method 上）：

    @admin_only
    async def get_stats(self, request):
        ...

⚠️ 儀表板對外前 checklist（現階段這些端點僅 CEO 自用、不對租戶開放，故
org_id SQL 邊界暫不做；真開放給客戶前必須補齊，見 analytics_handler.py /
ranking_analytics_handler.py 檔頭 checklist）：
  1. org_id SQL 邊界：所有 analytics/ranking SQL 補 org_id 條件（W-1 延後項）。
  2. W-6：admin session-count 跨 org count 洩漏。
  3. W-7：analytics/help error str(e) 外洩內部細節。
"""
from functools import wraps

from aiohttp import web


def is_admin_request(request: web.Request) -> bool:
    """Return True iff request carries an authenticated admin identity."""
    user_info = request.get('user')
    return bool(
        user_info
        and user_info.get('authenticated')
        and user_info.get('role') == 'admin'
    )


def admin_gate_response(request: web.Request):
    """Return a 401/403 web.Response if the caller is not an admin, else None.

    與 routes/admin.py:41-44 對齊：未登入→401，非 admin→403。
    """
    user_info = request.get('user')
    if not user_info or not user_info.get('authenticated'):
        return web.json_response({'error': 'Not authenticated'}, status=401)
    if user_info.get('role') != 'admin':
        return web.json_response({'error': 'Admin only'}, status=403)
    return None


def admin_only(handler):
    """Decorator: gate an aiohttp handler (method or function) to admins only.

    支援兩種簽名：
      - bound method `async def h(self, request)`
      - plain function `async def h(request)`
    以最後一個位置參數為 aiohttp Request 判定。
    """
    @wraps(handler)
    async def wrapper(*args, **kwargs):
        request = args[-1]
        denied = admin_gate_response(request)
        if denied is not None:
            return denied
        return await handler(*args, **kwargs)
    return wrapper
