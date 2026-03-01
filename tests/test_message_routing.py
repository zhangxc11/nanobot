"""Tests for Phase 18: Feishu file attachment send fix.

Tests cover:
1. ChannelManager._resolve_channel — exact match, prefix match, ambiguous, no match
2. MessageTool — ignores LLM-supplied channel/chat_id, uses defaults
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# T18.1  _resolve_channel tests
# ---------------------------------------------------------------------------

from nanobot.channels.manager import ChannelManager


class _FakeChannel:
    """Minimal channel stub."""
    def __init__(self, name: str):
        self.name = name
        self.is_running = True

    async def send(self, msg):
        pass


def _make_manager_with_channels(channel_names: list[str]) -> ChannelManager:
    """Create a ChannelManager with pre-populated channels dict (skip __init__ channel setup)."""
    with patch.object(ChannelManager, "_init_channels"):
        mgr = ChannelManager.__new__(ChannelManager)
        mgr.channels = {}
        for n in channel_names:
            mgr.channels[n] = _FakeChannel(n)
        return mgr


class TestResolveChannel:
    """Tests for ChannelManager._resolve_channel."""

    def test_exact_match(self):
        mgr = _make_manager_with_channels(["feishu.lab", "feishu.ST", "telegram"])
        ch = mgr._resolve_channel("feishu.lab")
        assert ch is not None
        assert ch.name == "feishu.lab"

    def test_exact_match_telegram(self):
        mgr = _make_manager_with_channels(["feishu.lab", "telegram"])
        ch = mgr._resolve_channel("telegram")
        assert ch is not None
        assert ch.name == "telegram"

    def test_prefix_match_single(self):
        """When only one channel starts with 'feishu.', bare 'feishu' should resolve."""
        mgr = _make_manager_with_channels(["feishu.lab", "telegram"])
        ch = mgr._resolve_channel("feishu")
        assert ch is not None
        assert ch.name == "feishu.lab"

    def test_prefix_match_ambiguous(self):
        """When multiple channels match, should return None."""
        mgr = _make_manager_with_channels(["feishu.lab", "feishu.ST"])
        ch = mgr._resolve_channel("feishu")
        assert ch is None

    def test_no_match(self):
        mgr = _make_manager_with_channels(["feishu.lab", "telegram"])
        ch = mgr._resolve_channel("discord")
        assert ch is None

    def test_prefix_no_dot(self):
        """'feishu' should NOT match 'feishuX' (must have dot separator)."""
        mgr = _make_manager_with_channels(["feishuX"])
        ch = mgr._resolve_channel("feishu")
        assert ch is None

    def test_exact_takes_priority(self):
        """If 'feishu' is registered as exact name, use it even if 'feishu.lab' exists."""
        mgr = _make_manager_with_channels(["feishu", "feishu.lab"])
        ch = mgr._resolve_channel("feishu")
        assert ch is not None
        assert ch.name == "feishu"


# ---------------------------------------------------------------------------
# T18.2  MessageTool ignores LLM-supplied channel/chat_id
# ---------------------------------------------------------------------------

from nanobot.agent.tools.message import MessageTool


class TestMessageToolRouting:
    """Tests for MessageTool channel/chat_id handling."""

    def test_ignores_llm_channel(self):
        """LLM-supplied channel should be ignored; default used instead."""
        callback = AsyncMock()
        tool = MessageTool(send_callback=callback)
        tool.set_context(channel="feishu.lab", chat_id="ou_abc123")

        result = asyncio.run(tool.execute(
            content="hello",
            channel="feishu",  # LLM passes wrong channel
            chat_id="ou_wrong",  # LLM passes wrong chat_id
        ))

        assert "Message sent" in result
        assert "feishu.lab" in result
        msg = callback.call_args[0][0]
        assert msg.channel == "feishu.lab"
        assert msg.chat_id == "ou_abc123"

    def test_uses_default_channel(self):
        """When LLM doesn't pass channel, default is used."""
        callback = AsyncMock()
        tool = MessageTool(send_callback=callback)
        tool.set_context(channel="telegram", chat_id="12345")

        result = asyncio.run(tool.execute(content="hi"))

        msg = callback.call_args[0][0]
        assert msg.channel == "telegram"
        assert msg.chat_id == "12345"

    def test_media_passed_through(self):
        """Media parameter should be passed through to OutboundMessage."""
        callback = AsyncMock()
        tool = MessageTool(send_callback=callback)
        tool.set_context(channel="feishu.lab", chat_id="ou_abc")

        result = asyncio.run(tool.execute(
            content="here is the file",
            media=["/tmp/test.docx", "/tmp/test2.pdf"],
        ))

        assert "2 attachments" in result
        msg = callback.call_args[0][0]
        assert msg.media == ["/tmp/test.docx", "/tmp/test2.pdf"]

    def test_no_context_error(self):
        """When no context is set, should return error."""
        callback = AsyncMock()
        tool = MessageTool(send_callback=callback)

        result = asyncio.run(tool.execute(content="hello"))
        assert "Error" in result
        callback.assert_not_called()

    def test_sent_in_turn_tracking(self):
        """_sent_in_turn should be set after successful send."""
        callback = AsyncMock()
        tool = MessageTool(send_callback=callback)
        tool.set_context(channel="feishu.lab", chat_id="ou_abc")

        assert tool._sent_in_turn is False
        tool.start_turn()
        assert tool._sent_in_turn is False

        asyncio.run(tool.execute(content="hello"))
        assert tool._sent_in_turn is True

        tool.start_turn()
        assert tool._sent_in_turn is False

    def test_schema_no_channel_chat_id(self):
        """Parameters schema should NOT expose channel or chat_id."""
        tool = MessageTool()
        props = tool.parameters["properties"]
        assert "channel" not in props
        assert "chat_id" not in props
        assert "content" in props
        assert "media" in props
