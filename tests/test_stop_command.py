"""Tests for /stop command — cancel running tasks in the agent loop.

Updated for Phase 19 concurrent dispatcher architecture.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent_loop(bus: MessageBus, workspace: Path):
    """Create a minimal AgentLoop with mocked provider."""
    from nanobot.agent.loop import AgentLoop

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    return AgentLoop(
        bus=bus,
        provider=provider,
        workspace=workspace,
        model="test-model",
    )


def _make_msg(content: str, channel: str = "feishu", chat_id: str = "chat1") -> InboundMessage:
    return InboundMessage(
        channel=channel,
        sender_id="user1",
        chat_id=chat_id,
        content=content,
    )


# ---------------------------------------------------------------------------
# Tests: /stop in _process_message (direct call, no concurrent task)
# ---------------------------------------------------------------------------

class TestStopCommandDirect:
    """Test /stop when called via process_direct (no concurrent task)."""

    def test_stop_returns_no_active_task(self, tmp_path):
        """When called directly (not through run()), /stop returns 'no active task'."""
        bus = MessageBus()
        agent = _make_agent_loop(bus, tmp_path)

        msg = _make_msg("/stop")

        async def _run():
            return await agent._process_message(msg)

        result = asyncio.run(_run())
        assert result is not None
        assert "No active task" in result.content

    def test_stop_case_insensitive(self, tmp_path):
        """/Stop and /STOP should also work."""
        bus = MessageBus()
        agent = _make_agent_loop(bus, tmp_path)

        async def _run():
            for cmd in ("/stop", "/Stop", "/STOP", " /stop "):
                msg = _make_msg(cmd)
                result = await agent._process_message(msg)
                assert result is not None
                assert "No active task" in result.content

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Tests: /help includes /stop
# ---------------------------------------------------------------------------

class TestHelpIncludesStop:
    """Test that /help output includes /stop."""

    def test_help_mentions_stop(self, tmp_path):
        bus = MessageBus()
        agent = _make_agent_loop(bus, tmp_path)

        msg = _make_msg("/help")

        async def _run():
            return await agent._process_message(msg)

        result = asyncio.run(_run())
        assert result is not None
        assert "/stop" in result.content
        assert "Stop" in result.content or "stop" in result.content


# ---------------------------------------------------------------------------
# Tests: /stop cancels running task (via run() concurrent dispatcher)
# ---------------------------------------------------------------------------

class TestStopCancelsTask:
    """Test /stop cancelling a running task in the concurrent dispatcher."""

    def test_handle_stop_no_active_task(self, tmp_path):
        """_handle_stop with no active task sends 'no active task' message."""
        bus = MessageBus()
        agent = _make_agent_loop(bus, tmp_path)

        msg = _make_msg("/stop")

        async def _run():
            await agent._handle_stop(msg)
            out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            assert "No active task" in out.content

        asyncio.run(_run())

    def test_process_message_safe_catches_cancelled(self, tmp_path):
        """_process_message_safe sends 'Task stopped' on CancelledError."""
        bus = MessageBus()
        agent = _make_agent_loop(bus, tmp_path)

        msg = _make_msg("do something long")

        async def _run():
            # Mock _process_message to sleep forever (simulating long task)
            async def mock_process(*args, **kwargs):
                await asyncio.sleep(60)
                return OutboundMessage(channel="feishu", chat_id="chat1", content="done")

            agent._process_message = mock_process

            task = asyncio.create_task(agent._process_message_safe(msg))
            await asyncio.sleep(0.05)  # Let it start
            task.cancel()

            try:
                await task
            except asyncio.CancelledError:
                pass

            # Should have sent "Task stopped" message
            out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            assert "stopped" in out.content.lower() or "stop" in out.content.lower()

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Tests: End-to-end /stop via run() concurrent dispatcher
# ---------------------------------------------------------------------------

class TestStopEndToEnd:
    """End-to-end test: /stop sent while task is running in run() loop."""

    def test_stop_during_run_loop(self, tmp_path):
        """Send /stop while a long task is running via run()."""
        bus = MessageBus()
        agent = _make_agent_loop(bus, tmp_path)

        processing_started = asyncio.Event()

        async def mock_process(msg, **kwargs):
            # /stop is handled by run() directly, not _process_message
            cmd = msg.content.strip().lower()
            if cmd in ("/help", "/stop"):
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content=f"Handled {cmd}",
                )
            processing_started.set()
            await asyncio.sleep(60)  # Simulate long task
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content="done",
            )

        agent._process_message = mock_process

        async def _run():
            # Queue a long-running message
            await bus.publish_inbound(
                _make_msg("long task", channel="feishu", chat_id="oc_123")
            )

            run_task = asyncio.create_task(agent.run())

            # Wait for the task to start processing
            await asyncio.wait_for(processing_started.wait(), timeout=2.0)

            # Now send /stop from the same chat
            await bus.publish_inbound(
                _make_msg("/stop", channel="feishu", chat_id="oc_123")
            )

            # Should get the "Task stopped" message
            out = await asyncio.wait_for(bus.consume_outbound(), timeout=3.0)
            assert "stop" in out.content.lower() or "stopped" in out.content.lower()

            # Clean up
            agent.stop()
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())

    def test_stop_different_session_not_affected(self, tmp_path):
        """Send /stop for one session while another is running — no effect."""
        bus = MessageBus()
        agent = _make_agent_loop(bus, tmp_path)

        processing_started = asyncio.Event()

        async def mock_process(msg, **kwargs):
            cmd = msg.content.strip().lower()
            if cmd in ("/help", "/stop"):
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content=f"Handled {cmd}",
                )
            processing_started.set()
            await asyncio.sleep(60)  # Simulate long task
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content="done",
            )

        agent._process_message = mock_process

        async def _run():
            # Start a long task on session feishu:chat1
            await bus.publish_inbound(
                _make_msg("long task", channel="feishu", chat_id="chat1")
            )

            run_task = asyncio.create_task(agent.run())
            await asyncio.wait_for(processing_started.wait(), timeout=2.0)

            # Send /stop from a DIFFERENT session (chat2)
            await bus.publish_inbound(
                _make_msg("/stop", channel="feishu", chat_id="chat2")
            )

            # Should get "No active task" for chat2
            out = await asyncio.wait_for(bus.consume_outbound(), timeout=3.0)
            assert "No active task" in out.content

            # Clean up
            agent.stop()
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())
