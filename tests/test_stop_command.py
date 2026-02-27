"""Tests for /stop command — cancel running tasks in the agent loop."""

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
# Tests: /stop cancels running task (via run() loop)
# ---------------------------------------------------------------------------

class TestStopCancelsTask:
    """Test /stop cancelling a running task in the run() loop."""

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

    def test_handle_stop_cancels_matching_task(self, tmp_path):
        """_handle_stop cancels a running task for the same channel+chat_id."""
        bus = MessageBus()
        agent = _make_agent_loop(bus, tmp_path)

        cancelled = asyncio.Event()

        async def slow_task():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        async def _run():
            task = asyncio.create_task(slow_task())
            agent._active_task = task
            agent._active_task_msg = _make_msg("do something", channel="feishu", chat_id="chat1")
            agent._active_task_session_key = "feishu:chat1"

            stop_msg = _make_msg("/stop", channel="feishu", chat_id="chat1")
            await agent._handle_stop(stop_msg)

            # Task should be cancelled
            assert cancelled.is_set() or task.cancelled()

        asyncio.run(_run())

    def test_handle_stop_ignores_different_chat(self, tmp_path):
        """_handle_stop does not cancel task for a different chat_id."""
        bus = MessageBus()
        agent = _make_agent_loop(bus, tmp_path)

        async def slow_task():
            await asyncio.sleep(60)

        async def _run():
            task = asyncio.create_task(slow_task())
            agent._active_task = task
            agent._active_task_msg = _make_msg("do something", channel="feishu", chat_id="chat1")
            agent._active_task_session_key = "feishu:chat1"

            # /stop from a different chat
            stop_msg = _make_msg("/stop", channel="feishu", chat_id="chat2")
            await agent._handle_stop(stop_msg)

            # Task should NOT be cancelled
            assert not task.cancelled()

            # Should get "no active task" response
            out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            assert "No active task" in out.content

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

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
# Tests: _active_task tracking in run()
# ---------------------------------------------------------------------------

class TestActiveTaskTracking:
    """Test that run() properly tracks active tasks."""

    def test_active_task_set_during_processing(self, tmp_path):
        """During processing, _active_task should be set."""
        bus = MessageBus()
        agent = _make_agent_loop(bus, tmp_path)

        processing_started = asyncio.Event()

        original_process = agent._process_message

        async def mock_process(msg, **kwargs):
            if msg.content == "/help":
                return await original_process(msg, **kwargs)
            processing_started.set()
            await asyncio.sleep(60)  # Simulate long processing
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="done")

        agent._process_message = mock_process

        async def _run():
            # Send a long-running message
            await bus.publish_inbound(_make_msg("do something long"))

            # Start run() in background
            run_task = asyncio.create_task(agent.run())

            # Wait for processing to start
            await asyncio.wait_for(processing_started.wait(), timeout=2.0)

            # Active task should be set
            assert agent._active_task is not None
            assert not agent._active_task.done()

            # Clean up
            agent.stop()
            agent._active_task.cancel()
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Tests: End-to-end /stop via run() loop
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
