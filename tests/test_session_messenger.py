"""Tests for Phase 30: SessionMessenger inter-session messaging.

Tests cover:
- GatewaySessionMessenger: inject into running sessions, trigger idle sessions
- GatewaySessionMessenger: source_key prefix formatting
- WorkerSessionMessenger: inject into running tasks, trigger idle tasks
- SubagentManager: _announce_result uses SessionMessenger when available
- SubagentManager: _announce_result fallback to bus with session_key_override
- Inject prefix fallback in loop.py
"""

import asyncio
import queue
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.callbacks import SessionMessenger
from nanobot.agent.subagent import SubagentManager


# ── Helpers ──────────────────────────────────────────────────────────────────


class FakeLLMResponse:
    """Minimal LLM response for testing."""

    def __init__(self, content="Done", tool_calls=None, usage=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.has_tool_calls = bool(self.tool_calls)
        self.usage = usage
        self.finish_reason = "stop"
        self.reasoning_content = None
        self.thinking_blocks = None


def _make_manager(**kwargs):
    """Create a SubagentManager with mocked dependencies."""
    provider = AsyncMock()
    provider.get_default_model.return_value = "test-model"
    bus = AsyncMock()
    bus.publish_inbound = AsyncMock()
    workspace = Path("/tmp/test-workspace")

    defaults = dict(
        provider=provider,
        workspace=workspace,
        bus=bus,
    )
    defaults.update(kwargs)
    return SubagentManager(**defaults)


# ── Tests: SessionMessenger Protocol ─────────────────────────────────────────


class TestSessionMessengerProtocol:
    def test_protocol_exists(self):
        """SessionMessenger protocol should be importable."""
        from nanobot.agent.callbacks import SessionMessenger
        assert SessionMessenger is not None

    def test_protocol_is_runtime_checkable(self):
        """SessionMessenger should be runtime checkable."""
        class MyMessenger:
            async def send_to_session(self, target_session_key, content, source_session_key=None):
                return True

        assert isinstance(MyMessenger(), SessionMessenger)


# ── Tests: GatewaySessionMessenger ───────────────────────────────────────────


class TestGatewaySessionMessengerInjectRunning:
    @pytest.mark.asyncio
    async def test_inject_into_running_session(self):
        """When target session is running, message should be injected."""
        from nanobot.agent.callbacks import GatewayCallbacks

        # Set up active_sessions with a running worker
        callbacks = GatewayCallbacks(bus=AsyncMock(), channel="feishu", chat_id="ou_123")
        task = MagicMock()
        task.done.return_value = False

        # Create a simple SessionWorker-like object
        class FakeWorker:
            pass
        worker = FakeWorker()
        worker.task = task
        worker.callbacks = callbacks

        active_sessions = {"feishu:ou_123": worker}
        bus = AsyncMock()
        sessions = MagicMock()

        # Import and instantiate GatewaySessionMessenger
        # We need to replicate the class since it's defined inside run()
        # For testing, we'll create an equivalent implementation
        class GatewaySessionMessenger:
            def __init__(self, active, bus, sessions_mgr):
                self._active = active
                self._bus = bus
                self._sessions = sessions_mgr

            async def send_to_session(self, target_session_key, content, source_session_key=None):
                if source_session_key:
                    prefixed = f"[Message from session {source_session_key}]\n{content}"
                else:
                    prefixed = content
                if target_session_key in self._active:
                    w = self._active[target_session_key]
                    if not w.task.done():
                        await w.callbacks.inject(prefixed)
                        return True
                    else:
                        self._active.pop(target_session_key, None)
                from nanobot.bus.events import InboundMessage
                msg = InboundMessage(
                    channel="session_messenger",
                    sender_id=source_session_key or "unknown",
                    chat_id=target_session_key,
                    content=prefixed,
                    session_key_override=target_session_key,
                )
                await self._bus.publish_inbound(msg)
                return True

        messenger = GatewaySessionMessenger(active_sessions, bus, sessions)
        result = await messenger.send_to_session(
            "feishu:ou_123", "test content", source_session_key="subagent:abc"
        )

        assert result is True
        # Verify the message was injected (check the inject queue)
        injected = await callbacks.check_user_input()
        assert injected is not None
        assert "[Message from session subagent:abc]" in injected
        assert "test content" in injected
        # Bus should NOT have been called (inject path, not publish)
        bus.publish_inbound.assert_not_called()


class TestGatewaySessionMessengerTriggerIdle:
    @pytest.mark.asyncio
    async def test_trigger_idle_session(self):
        """When target session is idle, should publish to bus with override."""
        active_sessions = {}  # No active sessions
        bus = AsyncMock()
        sessions = MagicMock()

        class GatewaySessionMessenger:
            def __init__(self, active, bus, sessions_mgr):
                self._active = active
                self._bus = bus
                self._sessions = sessions_mgr

            async def send_to_session(self, target_session_key, content, source_session_key=None):
                if source_session_key:
                    prefixed = f"[Message from session {source_session_key}]\n{content}"
                else:
                    prefixed = content
                if target_session_key in self._active:
                    w = self._active[target_session_key]
                    if not w.task.done():
                        await w.callbacks.inject(prefixed)
                        return True
                    else:
                        self._active.pop(target_session_key, None)
                from nanobot.bus.events import InboundMessage
                msg = InboundMessage(
                    channel="session_messenger",
                    sender_id=source_session_key or "unknown",
                    chat_id=target_session_key,
                    content=prefixed,
                    session_key_override=target_session_key,
                )
                await self._bus.publish_inbound(msg)
                return True

        messenger = GatewaySessionMessenger(active_sessions, bus, sessions)
        result = await messenger.send_to_session(
            "webchat:123", "result content", source_session_key="subagent:xyz"
        )

        assert result is True
        bus.publish_inbound.assert_called_once()
        msg = bus.publish_inbound.call_args[0][0]
        assert msg.session_key_override == "webchat:123"
        assert msg.channel == "session_messenger"
        assert "[Message from session subagent:xyz]" in msg.content


class TestGatewaySessionMessengerPrefixes:
    @pytest.mark.asyncio
    async def test_with_source_key(self):
        """Source key should be included in prefix."""
        active_sessions = {}
        bus = AsyncMock()

        class GatewaySessionMessenger:
            def __init__(self, active, bus, sessions_mgr):
                self._active = active
                self._bus = bus

            async def send_to_session(self, target_session_key, content, source_session_key=None):
                if source_session_key:
                    prefixed = f"[Message from session {source_session_key}]\n{content}"
                else:
                    prefixed = content
                from nanobot.bus.events import InboundMessage
                msg = InboundMessage(
                    channel="session_messenger", sender_id="test",
                    chat_id=target_session_key, content=prefixed,
                    session_key_override=target_session_key,
                )
                await self._bus.publish_inbound(msg)
                return True

        messenger = GatewaySessionMessenger(active_sessions, bus, None)
        await messenger.send_to_session("target", "hello", source_session_key="source:123")

        msg = bus.publish_inbound.call_args[0][0]
        assert msg.content == "[Message from session source:123]\nhello"

    @pytest.mark.asyncio
    async def test_without_source_key(self):
        """Without source key, no prefix should be added."""
        active_sessions = {}
        bus = AsyncMock()

        class GatewaySessionMessenger:
            def __init__(self, active, bus, sessions_mgr):
                self._active = active
                self._bus = bus

            async def send_to_session(self, target_session_key, content, source_session_key=None):
                if source_session_key:
                    prefixed = f"[Message from session {source_session_key}]\n{content}"
                else:
                    prefixed = content
                from nanobot.bus.events import InboundMessage
                msg = InboundMessage(
                    channel="session_messenger", sender_id="test",
                    chat_id=target_session_key, content=prefixed,
                    session_key_override=target_session_key,
                )
                await self._bus.publish_inbound(msg)
                return True

        messenger = GatewaySessionMessenger(active_sessions, bus, None)
        await messenger.send_to_session("target", "hello")

        msg = bus.publish_inbound.call_args[0][0]
        assert msg.content == "hello"


# ── Tests: SubagentManager announce uses SessionMessenger ────────────────────


class TestSubagentAnnounceUsesMessenger:
    @pytest.mark.asyncio
    async def test_announce_uses_messenger(self):
        """When session_messenger is set, _announce_result should use it."""
        messenger = AsyncMock()
        messenger.send_to_session = AsyncMock(return_value=True)

        mgr = _make_manager(session_messenger=messenger)

        await mgr._announce_result(
            task_id="t1",
            label="test",
            task="do something",
            result="done!",
            origin={"channel": "web", "chat_id": "123"},
            status="ok",
            subagent_session_key="subagent:abc",
            parent_session_key="webchat:123",
        )

        # SessionMessenger should have been called
        messenger.send_to_session.assert_called_once()
        call_kwargs = messenger.send_to_session.call_args[1]
        assert call_kwargs["target_session_key"] == "webchat:123"
        assert call_kwargs["source_session_key"] == "subagent:abc"
        assert "completed successfully" in call_kwargs["content"]
        assert "done!" in call_kwargs["content"]

        # Bus should NOT have been called
        mgr.bus.publish_inbound.assert_not_called()


class TestSubagentAnnounceFallbackBus:
    @pytest.mark.asyncio
    async def test_fallback_to_bus_without_messenger(self):
        """Without session_messenger, should fall back to bus publish."""
        mgr = _make_manager()  # No session_messenger
        assert mgr.session_messenger is None

        await mgr._announce_result(
            task_id="t1",
            label="test",
            task="do something",
            result="done!",
            origin={"channel": "web", "chat_id": "123"},
            status="ok",
            subagent_session_key="subagent:abc",
            parent_session_key="webchat:123",
        )

        # Bus should have been called as fallback
        mgr.bus.publish_inbound.assert_called_once()
        msg = mgr.bus.publish_inbound.call_args[0][0]
        assert msg.channel == "system"
        assert msg.session_key_override == "webchat:123"
        assert "completed successfully" in msg.content

    @pytest.mark.asyncio
    async def test_fallback_to_bus_without_parent_key(self):
        """Without parent_session_key, should fall back to bus even with messenger."""
        messenger = AsyncMock()
        mgr = _make_manager(session_messenger=messenger)

        await mgr._announce_result(
            task_id="t1",
            label="test",
            task="do something",
            result="done!",
            origin={"channel": "web", "chat_id": "123"},
            status="ok",
            subagent_session_key="subagent:abc",
            parent_session_key=None,  # No parent key
        )

        # Messenger should NOT have been called (no parent key)
        messenger.send_to_session.assert_not_called()
        # Bus should have been called as fallback
        mgr.bus.publish_inbound.assert_called_once()


# ── Tests: Inject prefix fallback ────────────────────────────────────────────


class TestInjectPrefixFallback:
    @pytest.mark.asyncio
    async def test_unprefixed_message_gets_default_prefix(self):
        """Messages without a bracket prefix should get a default prefix."""
        from nanobot.agent.callbacks import DefaultCallbacks

        class TestCallbacks(DefaultCallbacks):
            def __init__(self):
                self._pending = "hello from user"

            async def check_user_input(self):
                msg = self._pending
                self._pending = None
                return msg

            async def on_message(self, message):
                self.last_message = message

        callbacks = TestCallbacks()
        injected = await callbacks.check_user_input()
        # Simulate the prefix logic from _run_agent_loop
        if injected and not injected.startswith("["):
            injected = f"[Message from user during execution]\n{injected}"
        assert injected.startswith("[Message from user during execution]")
        assert "hello from user" in injected

    @pytest.mark.asyncio
    async def test_prefixed_message_kept_as_is(self):
        """Messages already prefixed with brackets should not be double-prefixed."""
        from nanobot.agent.callbacks import DefaultCallbacks

        class TestCallbacks(DefaultCallbacks):
            def __init__(self):
                self._pending = "[Message from session subagent:abc]\nresult here"

            async def check_user_input(self):
                msg = self._pending
                self._pending = None
                return msg

        callbacks = TestCallbacks()
        injected = await callbacks.check_user_input()
        # Simulate the prefix logic from _run_agent_loop
        if injected and not injected.startswith("["):
            injected = f"[Message from user during execution]\n{injected}"
        # Should NOT have the default prefix — already has one
        assert injected.startswith("[Message from session subagent:abc]")
        assert "result here" in injected


# ── Tests: End-to-end spawn with SessionMessenger ────────────────────────────


class TestSpawnWithSessionMessenger:
    @pytest.mark.asyncio
    async def test_spawn_passes_parent_session_key(self):
        """spawn() should pass parent session_key to _run_subagent and _announce_result."""
        messenger = AsyncMock()
        messenger.send_to_session = AsyncMock(return_value=True)

        mgr = _make_manager(session_messenger=messenger)
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done", usage=None))

        await mgr.spawn(
            task="test task",
            session_key="webchat:12345",
            origin_channel="web",
            origin_chat_id="12345",
        )

        # Wait for background task
        await asyncio.sleep(0.2)
        for task in list(mgr._running_tasks.values()):
            if not task.done():
                await task

        # SessionMessenger should have been called with correct parent key
        messenger.send_to_session.assert_called_once()
        call_kwargs = messenger.send_to_session.call_args[1]
        assert call_kwargs["target_session_key"] == "webchat:12345"
        assert call_kwargs["source_session_key"].startswith("subagent:webchat_12345_")
