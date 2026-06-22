"""Guardrail: schemas 移除 ConversationEntry，但 Message/MessageType 現役保留。"""
from core import schemas


def test_conversation_entry_removed():
    assert not hasattr(schemas, "ConversationEntry")


def test_message_and_messagetype_preserved():
    assert hasattr(schemas, "Message")
    assert hasattr(schemas, "MessageType")


def test_live_message_importers_still_work():
    # baseHandler / ranking / message_senders 的 Message import 不破
    from core.schemas import Message, MessageType  # noqa: F401
