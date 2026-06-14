"""Tests that user_id is stamped onto every SSE envelope path.

See plan D-3 / Task 2A: frontend Trigger G needs an envelope-level
deterministic identity check; relying on the stream-owner snapshot at
stream-open time is probabilistic.

Phase 4b.5 Fix 1: covers ad-hoc emitters that bypass ``add_message_metadata``
(``send_begin_response``, ``send_end_response``, ``send_progress``) via the
shared ``inject_user_id`` helper.
"""
import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock


class AddMessageMetadataUserIdTest(unittest.TestCase):
    def _make_sender(self, user_id):
        from core.utils.message_senders import MessageSender
        handler = MagicMock()
        handler.user_id = user_id
        # Mock required handler attributes used by add_message_metadata
        handler.message_counter = 0
        handler.handler_message_id = 'test-msg-id'
        handler.conversation_id = 'test-conv-id'
        sender = MessageSender(handler)
        return sender

    def test_injects_user_id_when_handler_has_one(self):
        sender = self._make_sender('user-uuid-aaa')
        out = sender.add_message_metadata({})
        self.assertIn('user_id', out)
        self.assertEqual(out['user_id'], 'user-uuid-aaa')

    def test_does_not_overwrite_existing_user_id(self):
        sender = self._make_sender('user-uuid-aaa')
        out = sender.add_message_metadata({'user_id': 'explicit-other'})
        self.assertEqual(out['user_id'], 'explicit-other')

    def test_omits_user_id_when_handler_has_none(self):
        sender = self._make_sender(None)
        out = sender.add_message_metadata({})
        self.assertNotIn('user_id', out)


class InjectUserIdHelperTest(unittest.TestCase):
    """Direct tests on the module-level ``inject_user_id`` helper used by
    both ``MessageSender.add_message_metadata`` and ad-hoc emitters in
    ``core/utils/message_senders.py`` and ``webserver/routes/api.py``."""

    def test_stamps_user_id_from_handler(self):
        from core.utils.message_senders import inject_user_id
        handler = MagicMock(user_id='uuid-x')
        msg = {"message_type": "begin-nlweb-response"}
        out = inject_user_id(msg, handler)
        self.assertEqual(out['user_id'], 'uuid-x')

    def test_idempotent_preserves_explicit_user_id(self):
        from core.utils.message_senders import inject_user_id
        handler = MagicMock(user_id='uuid-x')
        msg = {"message_type": "x", "user_id": "explicit"}
        inject_user_id(msg, handler)
        self.assertEqual(msg['user_id'], 'explicit')

    def test_anonymous_handler_omits_user_id(self):
        """Anonymous queries (handler.user_id = None) must NOT add user_id.

        This is correct behaviour, not silent failure: frontend Trigger G
        treats absent user_id as "no identity claim"."""
        from core.utils.message_senders import inject_user_id
        handler = MagicMock(user_id=None)
        msg = {"message_type": "begin-nlweb-response"}
        inject_user_id(msg, handler)
        self.assertNotIn('user_id', msg)

    def test_none_handler_is_safe(self):
        from core.utils.message_senders import inject_user_id
        msg = {"message_type": "x"}
        out = inject_user_id(msg, None)
        self.assertNotIn('user_id', out)


class AdHocEnvelopeUserIdTest(unittest.IsolatedAsyncioTestCase):
    """Phase 4b.5 Fix 1 regression: ad-hoc emitters in MessageSender that
    build dicts and push to ``http_handler.write_stream`` directly must also
    carry user_id (they previously bypassed ``add_message_metadata``)."""

    def _make_sender(self, user_id):
        from core.utils.message_senders import MessageSender
        handler = MagicMock()
        handler.user_id = user_id
        handler.streaming = True
        handler.conversation_id = 'conv-xyz'
        handler.query = 'demo query'
        handler.query_id = 'q-1'
        handler.message_counter = 0
        handler.handler_message_id = 'h-1'
        handler.http_handler = MagicMock()
        handler.http_handler.write_stream = AsyncMock()
        return MessageSender(handler), handler

    async def test_send_begin_response_stamps_user_id(self):
        sender, handler = self._make_sender('uuid-begin')
        await sender.send_begin_response()
        handler.http_handler.write_stream.assert_awaited_once()
        sent = handler.http_handler.write_stream.await_args.args[0]
        self.assertEqual(sent['message_type'], 'begin-nlweb-response')
        self.assertEqual(sent['user_id'], 'uuid-begin')

    async def test_send_end_response_stamps_user_id(self):
        sender, handler = self._make_sender('uuid-end')
        await sender.send_end_response()
        handler.http_handler.write_stream.assert_awaited_once()
        sent = handler.http_handler.write_stream.await_args.args[0]
        self.assertEqual(sent['message_type'], 'end-nlweb-response')
        self.assertEqual(sent['user_id'], 'uuid-end')

    async def test_send_progress_stamps_user_id(self):
        sender, handler = self._make_sender('uuid-prog')
        await sender.send_progress('analyzing', 'msg', percent=10)
        handler.http_handler.write_stream.assert_awaited_once()
        sent = handler.http_handler.write_stream.await_args.args[0]
        self.assertEqual(sent['message_type'], 'progress')
        self.assertEqual(sent['user_id'], 'uuid-prog')

    async def test_anonymous_begin_response_omits_user_id(self):
        """Anonymous queries: user_id absent on envelope is correct, not a bug."""
        sender, handler = self._make_sender(None)
        await sender.send_begin_response()
        sent = handler.http_handler.write_stream.await_args.args[0]
        self.assertNotIn('user_id', sent)


if __name__ == '__main__':
    unittest.main()
