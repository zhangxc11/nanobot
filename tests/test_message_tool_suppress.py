"""Test message tool suppress logic for final replies.

Local architecture notes:
- MessageTool.execute() deliberately ignores LLM-supplied channel/chat_id
  parameters.  It always routes to the context-set defaults (self._default_channel
  / self._default_chat_id) to prevent the LLM from misrouting messages.
- Therefore, _sent_in_turn is always True when the message tool is used,
  regardless of what channel/chat_id the LLM requests.
- The final reply is suppressed whenever the message tool was used in the turn.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.message import MessageTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse, ToolCallRequest


def _make_loop(tmp_path: Path) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model", memory_window=10)


class TestMessageToolSuppressLogic:
    """Final reply suppressed when message tool sends in the same turn.

    Because local's MessageTool always routes to the context channel (ignoring
    LLM-supplied channel/chat_id), the suppress logic triggers whenever the
    message tool is used, regardless of the LLM's intended target.
    """

    @pytest.mark.asyncio
    async def test_suppress_when_sent_to_same_target(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        tool_call = ToolCallRequest(
            id="call1", name="message",
            arguments={"content": "Hello", "channel": "feishu", "chat_id": "chat123"},
        )
        calls = iter([
            LLMResponse(content="", tool_calls=[tool_call]),
            LLMResponse(content="Done", tool_calls=[]),
        ])
        loop.provider.chat = AsyncMock(side_effect=lambda *a, **kw: next(calls))
        loop.tools.get_definitions = MagicMock(return_value=[])

        sent: list[OutboundMessage] = []
        mt = loop.tools.get("message")
        if isinstance(mt, MessageTool):
            mt.set_send_callback(AsyncMock(side_effect=lambda m: sent.append(m)))

        msg = InboundMessage(channel="feishu", sender_id="user1", chat_id="chat123", content="Send")
        result = await loop._process_message(msg)

        assert len(sent) == 1
        assert result is None  # suppressed

    @pytest.mark.asyncio
    async def test_suppress_even_when_llm_requests_different_channel(self, tmp_path: Path) -> None:
        """Local's MessageTool ignores LLM-supplied channel/chat_id.

        Even when the LLM requests sending to "email", the message is routed
        to the context channel (feishu:chat123).  Since the message was sent
        to the same target, the final reply IS suppressed.
        """
        loop = _make_loop(tmp_path)
        tool_call = ToolCallRequest(
            id="call1", name="message",
            # LLM requests "email" channel, but local ignores this
            arguments={"content": "Email content", "channel": "email", "chat_id": "user@example.com"},
        )
        calls = iter([
            LLMResponse(content="", tool_calls=[tool_call]),
            LLMResponse(content="I've sent the email.", tool_calls=[]),
        ])
        loop.provider.chat = AsyncMock(side_effect=lambda *a, **kw: next(calls))
        loop.tools.get_definitions = MagicMock(return_value=[])

        sent: list[OutboundMessage] = []
        mt = loop.tools.get("message")
        if isinstance(mt, MessageTool):
            mt.set_send_callback(AsyncMock(side_effect=lambda m: sent.append(m)))

        msg = InboundMessage(channel="feishu", sender_id="user1", chat_id="chat123", content="Send email")
        result = await loop._process_message(msg)

        assert len(sent) == 1
        # Local routes to context channel, not LLM-requested channel
        assert sent[0].channel == "feishu"
        assert sent[0].chat_id == "chat123"
        # Suppressed because message was sent to the same target (context channel)
        assert result is None

    @pytest.mark.asyncio
    async def test_not_suppress_when_no_message_tool_used(self, tmp_path: Path) -> None:
        loop = _make_loop(tmp_path)
        loop.provider.chat = AsyncMock(return_value=LLMResponse(content="Hello!", tool_calls=[]))
        loop.tools.get_definitions = MagicMock(return_value=[])

        msg = InboundMessage(channel="feishu", sender_id="user1", chat_id="chat123", content="Hi")
        result = await loop._process_message(msg)

        assert result is not None
        assert "Hello" in result.content


class TestMessageToolTurnTracking:

    def test_sent_in_turn_tracks_same_target(self) -> None:
        tool = MessageTool()
        tool.set_context("feishu", "chat1")
        assert not tool._sent_in_turn
        tool._sent_in_turn = True
        assert tool._sent_in_turn

    def test_start_turn_resets(self) -> None:
        tool = MessageTool()
        tool._sent_in_turn = True
        tool.start_turn()
        assert not tool._sent_in_turn


class TestMessageToolChannelOverride:
    """Verify that MessageTool ignores LLM-supplied channel/chat_id."""

    @pytest.mark.asyncio
    async def test_execute_ignores_llm_channel(self) -> None:
        sent: list[OutboundMessage] = []
        tool = MessageTool(send_callback=AsyncMock(side_effect=lambda m: sent.append(m)))
        tool.set_context("feishu", "chat123")

        # LLM tries to send to "email" channel
        result = await tool.execute(
            content="Hello",
            channel="email",
            chat_id="user@example.com",
        )

        assert len(sent) == 1
        # Context channel is used, not LLM-supplied
        assert sent[0].channel == "feishu"
        assert sent[0].chat_id == "chat123"
        assert "feishu:chat123" in result
