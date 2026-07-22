"""
P0 安全前置回歸測試：free_conversation 私文檢索必須把 org_id 傳到底層 provider。

背景：私文檢索 provider 的 org filter 將由條件式改為強制隔離（org_id=None → IS NULL）。
若 generate_answer.py 的 free_conversation 私文檢索呼叫漏傳 org_id，有 org 的合法 user
會拿到 org_id=None → IS NULL，撞不上自己 org 的文件 → 搜不到自己私文（P1 regression）。

本測試驗證 GenerateAnswer.get_ranked_answers 的 free_conversation 私文檢索路徑
會把 handler 的 self.org_id 傳給 search_user_documents。

patch 點 = core.user_data_retriever.search_user_documents
（generate_answer.py 是函式內 `from core.user_data_retriever import search_user_documents`，
會 import 到被 mock 的物件；patch methods.generate_answer.search_user_documents 會失敗，
因為該名稱在 module level 不存在）。

不連 DB / 不連 embedding / 不燒 LLM 錢：裸實例 + 全 mock，純記憶體跑。
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# 讓 `import methods.generate_answer` / `import core.*` 可解析（tests/ 的上一層是 code/python）
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from methods.generate_answer import GenerateAnswer


class _StopPipeline(Exception):
    """Sentinel：私文檢索完成、synthesize 被觸發後用它提前終止 pipeline，避免跑完整條合成。"""


class SearchUserDocumentsOrgPassthroughTest(unittest.TestCase):
    def _make_handler(self):
        """繞過 __init__ 建最小 GenerateAnswer 裸實例，只設私文檢索路徑所需屬性。"""
        h = object.__new__(GenerateAnswer)

        # 私文檢索分支的 gate 條件
        h.org_id = "org-X"
        h.user_id = "user-A"
        h.include_private_sources = True
        h.free_conversation = True

        # 查詢與參數
        h.query = "測試查詢"
        h.decontextualized_query = "測試查詢"
        h.query_params = {"user_id": ["user-A"], "org_id": ["org-X"]}

        # 跳過兩個 cache 區：reuse_results=false + 無 cached results
        h.query_params["reuse_results"] = ["false"]
        h.conversation_id = None
        h.prev_queries = []
        h.site = "all"

        # get_ranked_answers 早段（query_logger.log_query_start）會讀到的屬性
        h.generate_mode = "generate"
        h.session_id = "sess-1"
        h.model = "test-model"
        h.parent_query_id = None

        # message_sender.send_begin_response 是 async，用 AsyncMock 避免真送
        h.message_sender = MagicMock()
        h.message_sender.send_begin_response = AsyncMock()

        return h

    def test_free_conversation_private_search_passes_org_id(self):
        handler = self._make_handler()

        fake_search = AsyncMock(return_value=[])

        async def _fake_synthesize():
            # 私文檢索已完成（org_id 已傳入 fake_search）→ 提前終止，不跑完整合成
            raise _StopPipeline()

        # synthesize_free_conversation 綁在實例上（instance-level）以攔截
        handler.synthesize_free_conversation = _fake_synthesize

        with patch(
            "core.user_data_retriever.search_user_documents",
            fake_search,
        ), patch(
            "core.query_logger.get_query_logger",
            return_value=MagicMock(),
        ):
            with self.assertRaises(_StopPipeline):
                asyncio.run(handler.get_ranked_answers())

        # 私文檢索確有被呼叫
        self.assertTrue(
            fake_search.called,
            "search_user_documents 未被呼叫 —— seam/patch 點可能錯，斷言無效",
        )

        # 核心斷言：呼叫時 org_id kwarg 必須等於 handler.self.org_id
        _, kwargs = fake_search.call_args
        self.assertIn(
            "org_id",
            kwargs,
            "search_user_documents 呼叫漏傳 org_id kwarg（P0 洩漏：會被強制 IS NULL）",
        )
        self.assertEqual(
            kwargs["org_id"],
            "org-X",
            f"org_id 應為 handler.org_id='org-X'，實得 {kwargs.get('org_id')!r}",
        )


if __name__ == "__main__":
    unittest.main()
