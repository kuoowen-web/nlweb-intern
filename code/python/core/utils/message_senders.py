# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Message sending utilities for NLWebHandler.

This module contains helper classes for managing message sending operations,
extracted from NLWebHandler to improve code organization and maintainability.
"""

import asyncio
import time
from datetime import datetime
from typing import Dict, Any, Optional, Union, List
from core.config import CONFIG
from core.schemas import Message, SenderType, MessageType
from core.output.pii_filter import filter_message_pii
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger(__name__)

API_VERSION = "0.1"


def inject_user_id(message: Dict[str, Any], handler) -> Dict[str, Any]:
    """
    Stamp handler.user_id onto an SSE envelope dict in place and return it.

    Used by both ``MessageSender.add_message_metadata`` (full envelopes that
    flow through ``send_message``) and by ad-hoc emitters that build a dict
    and call ``http_handler.write_stream`` directly (e.g.
    ``send_begin_response``, ``send_end_response``, ``send_progress`` and
    completion / deep-research-begin envelopes assembled in
    ``webserver/routes/api.py``).

    Behaviour:
      * Idempotent — an explicit ``user_id`` already on the message is preserved.
      * If ``handler.user_id`` is falsy (anonymous query), the envelope is
        returned without a ``user_id`` field. This is the *correct* behaviour
        for anonymous traffic (NOT a silent failure): frontend Trigger G
        treats absent ``user_id`` as "no identity claim" and skips the
        envelope-level identity check for that envelope.

    Args:
        message: SSE envelope dictionary (mutated in place).
        handler: NLWebHandler-like object exposing ``user_id``.

    Returns:
        The same ``message`` dict, with ``user_id`` injected when available.
    """
    if not isinstance(message, dict):
        return message
    if "user_id" in message:
        return message
    uid = getattr(handler, "user_id", None) if handler is not None else None
    if uid:
        message["user_id"] = uid
    return message


class MessageSender:
    """
    Helper class for sending messages in NLWebHandler.
    
    This class encapsulates message sending utilities to reduce clutter
    in the main NLWebHandler class.
    """
    
    def __init__(self, handler):
        """
        Initialize the MessageSender with a reference to the NLWebHandler.
        
        Args:
            handler: The NLWebHandler instance this sender belongs to.
        """
        self.handler = handler
    
    def create_initial_user_message(self):
        """
        Create the initial user query message as a Message object.
        This message represents the user's original query.
        
        Returns:
            Message object containing the user message
        """
        from core.utils.utils import get_param
        from core.schemas import UserQuery
        
        # Create UserQuery for the content
        user_query = UserQuery(
            query=self.handler.query,
            site=self.handler.site,
            mode=self.handler.generate_mode,
            prev_queries=self.handler.prev_queries
        )
        
        # Create the Message object
        user_message = Message(
            message_id=f"{self.handler.handler_message_id}#0",
            conversation_id=self.handler.conversation_id,
            sender_type=SenderType.USER,
            message_type=MessageType.QUERY,
            content=user_query,
            timestamp=datetime.utcnow().isoformat(),
            sender_info={
                "id": self.handler.user_id or get_param(self.handler.query_params, "user_id", str, ""),
                "name": get_param(self.handler.query_params, "user_name", str, "User")
            }
        )
        
        return user_message
    
    async def send_time_to_first_result(self):
        """Send time-to-first-result header message."""
        return
    
    async def send_api_version(self):
        """Send API version message."""
        return
    
    async def send_begin_response(self):
        """Send begin-nlweb-response message at the start of query processing."""
        # 🔧R5F local import：破 message_senders⇄send 載入環（send.py 頂端已
        # from core.utils.message_senders import inject_user_id）。禁放 module 頂端。
        from core.sse.send import send_sse

        begin_message = {
            "message_type": "begin-nlweb-response",
            "conversation_id": self.handler.conversation_id,
            "query": self.handler.query,
            "query_id": getattr(self.handler, 'query_id', None),  # Include query_id for analytics
            "timestamp": int(time.time() * 1000)
        }
        # send_sse(path="ad_hoc") replicates the streaming+http_handler guard,
        # inject_user_id (Trigger G), raw write_stream, and swallow-on-exception
        # semantics of this ad-hoc emitter (plan §0.1 path 2 / Task 10).
        await send_sse(self.handler, begin_message, path="ad_hoc")
    
    async def send_end_response(self, error=False):
        """
        Send end-nlweb-response message at the end of query processing.

        Args:
            error: If True, indicates the query ended with an error
        """
        from core.sse.send import send_sse  # 🔧R5F local import：破載入環

        end_message = {
            "message_type": "end-nlweb-response",
            "conversation_id": self.handler.conversation_id,
            "timestamp": int(time.time() * 1000)
        }

        if error:
            end_message["error"] = True

        # send_sse(path="ad_hoc"): guard + inject_user_id + raw write_stream +
        # swallow-on-exception (plan §0.1 path 2 / Task 10).
        await send_sse(self.handler, end_message, path="ad_hoc")

    async def send_progress(self, stage: str, message: str, percent: int = None):
        """
        Send progress update message during query processing.

        Args:
            stage: Internal stage identifier (e.g., 'analyzing', 'searching', 'ranking')
            message: User-friendly message in Chinese
            percent: Optional progress percentage (0-100)
        """
        from core.sse.send import send_sse  # 🔧R5F local import：破載入環

        progress_message = {
            "message_type": "progress",
            "stage": stage,
            "message": message,
            "timestamp": int(time.time() * 1000)
        }

        if percent is not None:
            progress_message["percent"] = percent

        # send_sse(path="ad_hoc"): guard + inject_user_id + raw write_stream +
        # swallow-on-exception (plan §0.1 path 2 / Task 10). NOTE: this is the
        # MessageSender.send_progress ad-hoc emitter, distinct from the reasoning
        # orchestrator_base._send_progress (path=progress) handled in Task 12.
        await send_sse(self.handler, progress_message, path="ad_hoc")

    async def send_config_headers(self):
        """Send headers from configuration as messages."""
        return
    
    def store_message(self, message: Union[Dict[str, Any], Message]):
        """
        Store message in return_value for both streaming and non-streaming cases.
        Messages are now stored as Message objects in handler.messages.
        
        Args:
            message: The message to store (dict or Message object)
        """
        # Convert dict to Message object if needed
        if isinstance(message, dict):
            # Try to create a Message object from the dict
            try:
                message_obj = Message.from_dict(message)
            except Exception as e:
                logger.warning(f"Message.from_dict failed, using fallback: {e}")
                # If conversion fails, create a basic Message with the dict as content
                message_obj = Message(
                    sender_type=SenderType.SYSTEM,
                    message_type=message.get("message_type", MessageType.STATUS),
                    content=message.get("content", message),
                    conversation_id=message.get("conversation_id") or getattr(self.handler, 'conversation_id', None)
                )
        else:
            message_obj = message
            message = message_obj.to_dict()  # Keep dict form for legacy code
        
        # Store the Message object in the new messages list
        self.handler.messages.append(message_obj)
        
        # Legacy support: also update return_value with dict form
        message_type = message.get("message_type")
        
        if message_type == "result":
            # For result messages, accumulate in content array
            if "content" not in self.handler.return_value:
                self.handler.return_value["content"] = []
            
            content = message.get("content", [])
            for result in content:
                self.handler.return_value["content"].append(result)
        else:
            # For other message types, store under the message_type key
            val = {}
            for key in message:
                if key != "message_type":
                    val[key] = message[key]
            self.handler.return_value[message_type] = val
    
    async def _send_headers_if_needed(self, is_streaming=True):
        """
        Send headers if they haven't been sent yet.
        Handles both streaming and non-streaming modes.
        
        Args:
            is_streaming: True for streaming mode, False for non-streaming
        """
        if self.handler.headersSent:
            return
            
        self.handler.headersSent = True
        
        if is_streaming:
            # In streaming mode, send headers as messages
            # Send version number first
            if not self.handler.versionNumberSent:
                await self.send_api_version()
            
            # Send headers from config as messages
            await self.send_config_headers()
        else:
            # In non-streaming mode, add headers to return_value
            try:
                # Get configured headers from CONFIG and add them to return_value
                headers = CONFIG.get_headers()
                for header_key, header_value in headers.items():
                    self.handler.return_value[header_key] = {"message": header_value}
            except Exception as e:
                logger.debug(f"Failed to get config headers: {e}")
            
            # Also add nlweb headers if available
            if hasattr(CONFIG.nlweb, 'headers') and CONFIG.nlweb.headers:
                for header_key, header_value in CONFIG.nlweb.headers.items():
                    self.handler.return_value[header_key] = header_value
    
    def add_message_metadata(self, message, use_system_sender=False):
        """
        Add standard metadata fields to a message if not already present.
        
        Args:
            message: The message dictionary to add fields to
            use_system_sender: If True, use system sender info instead of nlweb_assistant
            
        Returns:
            The message with standard fields added
        """
        # Add timestamp
        if "timestamp" not in message:
            message["timestamp"] = int(time.time() * 1000)
        
        # Add message_id with counter for uniqueness
        if "message_id" not in message:
            # Increment counter and generate unique ID
            self.handler.message_counter += 1
            message["message_id"] = f"{self.handler.handler_message_id}#{self.handler.message_counter}"
        
        # Add conversation_id
        if "conversation_id" not in message:
            message["conversation_id"] = self.handler.conversation_id
        
        # Add sender_info - use different defaults based on context
        if "sender_info" not in message and "senderInfo" not in message:
            if use_system_sender:
                message["senderInfo"] = {"id": "system", "name": "NLWeb"}
            else:
                message["sender_info"] = {
                    "id": "nlweb_assistant",
                    "name": "NLWeb Assistant"
                }

        # Trigger G support (see frontend-init-sync-refactor-plan.md D-3 / Task 2A):
        # inject handler.user_id so frontend SSE handler can do envelope-level
        # deterministic identity check (instead of relying on stream-owner
        # snapshot which is probabilistic). Idempotent: explicit user_id in
        # message is preserved. Phase 4b.5: extracted to module-level helper
        # so ad-hoc emitters that bypass add_message_metadata can share it.
        inject_user_id(message, self.handler)

        return message
    
    async def send_message(self, message):
        """Send a message with appropriate metadata and routing."""
#        async with self.handler._send_lock:  # Protect send operation with lock
            # Add metadata to all messages (both streaming and non-streaming)
        message = self.add_message_metadata(message)

        # PII filter: scan LLM-generated summaries/analyses before storing or streaming.
        # NEVER filters result messages (original news cards). See P2-3 spec.
        message = await filter_message_pii(
            message, user_id=getattr(self.handler, 'user_id', None)
        )

        # 🔧R3 (R2-BLK-2): flag ON validates the **completed wire dict** here (shape
        # check only — returns payload unchanged, byte-identical to OFF). Local import
        # breaks the message_senders <-> core.sse.send load-time cycle. OFF (default) is
        # a no-op -> zero behavior change. See plan §Task 5 Step 2b.
        from core.sse.send import _typed_validate
        message = _typed_validate(message)

        # Always store the message (for both streaming and non-streaming)
        self.store_message(message)
            
        if (self.handler.streaming and self.handler.http_handler is not None):
                # Streaming mode: also send via write_stream
                
            # Check if this is the first result and add time-to-first-result header
            if message.get("message_type") == "result" and not self.handler.first_result_sent:
                self.handler.first_result_sent = True
                await self.send_time_to_first_result()
                
            # Send headers if not already sent
            await self._send_headers_if_needed(is_streaming=True)
                
            try:
                await self.handler.http_handler.write_stream(message)
            except Exception as e:
                self.handler.connection_alive_event.clear()  # Use event instead of flag
        else:
            # Non-streaming mode: just store (already done above)
            # Send headers if not already sent
            await self._send_headers_if_needed(is_streaming=False)