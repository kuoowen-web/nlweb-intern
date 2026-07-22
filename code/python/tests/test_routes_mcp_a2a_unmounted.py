"""驗證拍板 3：/mcp /a2a route 已卸載，但 mcp.py/a2a.py 檔案保留可 import。

未來要復活只要在 setup_routes 加回兩行 setup_*_routes(app)。
"""
import os
import unittest

os.environ.pop('DATABASE_URL', None)
os.environ.pop('POSTGRES_CONNECTION_STRING', None)

from aiohttp import web

from webserver.routes import setup_routes


class McpA2aUnmountedTest(unittest.TestCase):
    def test_mcp_and_a2a_routes_not_mounted(self):
        app = web.Application()
        setup_routes(app)
        paths = {getattr(r.resource, 'canonical', None) for r in app.router.routes()}
        # /mcp 與 /a2a 不應出現在已掛載 route
        self.assertNotIn('/mcp', paths)
        self.assertNotIn('/a2a', paths)

    def test_mcp_module_still_importable(self):
        # 檔案保留：import 不得爆
        from webserver.routes import mcp  # noqa: F401
        from webserver.routes import a2a  # noqa: F401
        self.assertTrue(hasattr(mcp, 'setup_mcp_routes'))
        self.assertTrue(hasattr(a2a, 'setup_a2a_routes'))

    def test_ask_route_still_mounted(self):
        # 對照：確認拔的是 mcp/a2a，不是誤傷主路徑
        app = web.Application()
        setup_routes(app)
        paths = {getattr(r.resource, 'canonical', None) for r in app.router.routes()}
        self.assertIn('/ask', paths)


if __name__ == "__main__":
    unittest.main()
