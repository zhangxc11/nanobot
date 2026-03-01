"""Tests for GatewayCallbacks — T19.3."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from nanobot.agent.callbacks import GatewayCallbacks
from nanobot.bus.queue import MessageBus


class TestGatewayCallbacks:
    """Tests for GatewayCallbacks inject queue and progress forwarding."""

    def _make_callbacks(self) -> tuple[GatewayCallbacks, MessageBus]:
        bus = MagicMock(spec=MessageBus)
        bus.publish_outbound = AsyncMock()
        cb = GatewayCallbacks(bus=bus, channel="feishu.lab", chat_id="ou_123")
        return cb, bus

    def test_check_user_input_empty(self):
        """Empty queue returns None."""
        cb, _ = self._make_callbacks()
        result = asyncio.new_event_loop().run_until_complete(cb.check_user_input())
        assert result is None

    def test_inject_and_check(self):
        """Injected text is returned by check_user_input."""
        cb, _ = self._make_callbacks()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(cb.inject("hello from user"))
        result = loop.run_until_complete(cb.check_user_input())
        assert result == "hello from user"
        # Queue should be empty now
        result2 = loop.run_until_complete(cb.check_user_input())
        assert result2 is None

    def test_inject_multiple(self):
        """Multiple injections are consumed in FIFO order."""
        cb, _ = self._make_callbacks()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(cb.inject("first"))
        loop.run_until_complete(cb.inject("second"))
        r1 = loop.run_until_complete(cb.check_user_input())
        r2 = loop.run_until_complete(cb.check_user_input())
        assert r1 == "first"
        assert r2 == "second"

    def test_on_progress_publishes_outbound(self):
        """on_progress forwards to bus.publish_outbound."""
        cb, bus = self._make_callbacks()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(cb.on_progress("thinking...", tool_hint=False))
        bus.publish_outbound.assert_called_once()
        msg = bus.publish_outbound.call_args[0][0]
        assert msg.channel == "feishu.lab"
        assert msg.chat_id == "ou_123"
        assert msg.content == "thinking..."
        assert msg.metadata["_progress"] is True
        assert msg.metadata["_tool_hint"] is False

    def test_on_progress_tool_hint(self):
        """on_progress with tool_hint=True sets metadata correctly."""
        cb, bus = self._make_callbacks()
        loop = asyncio.new_event_loop()
        loop.run_until_complete(cb.on_progress('exec("ls")', tool_hint=True))
        msg = bus.publish_outbound.call_args[0][0]
        assert msg.metadata["_tool_hint"] is True

    def test_check_user_input_non_blocking(self):
        """check_user_input should return immediately even if queue is empty."""
        cb, _ = self._make_callbacks()
        loop = asyncio.new_event_loop()
        # This should not block/hang
        result = loop.run_until_complete(
            asyncio.wait_for(cb.check_user_input(), timeout=0.1)
        )
        assert result is None
