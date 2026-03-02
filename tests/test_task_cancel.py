"""Tests for /stop task cancellation and concurrent session architecture.

Local architecture notes:
- /stop in run() cancels the SessionWorker task for the matching session_key.
- _handle_stop() is a legacy stub for process_direct() — always returns
  "No active task" because process_direct has no concurrent task registry.
- There is no _dispatch() method — dispatching is done inline in run().
- Each session runs as an independent asyncio.Task (no _processing_lock
  serialization across sessions).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_loop():
    """Create a minimal AgentLoop with mocked dependencies."""
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    workspace = MagicMock()
    workspace.__truediv__ = MagicMock(return_value=MagicMock())

    with patch("nanobot.agent.loop.ContextBuilder"), \
         patch("nanobot.agent.loop.SessionManager"), \
         patch("nanobot.agent.loop.SubagentManager") as MockSubMgr:
        MockSubMgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(bus=bus, provider=provider, workspace=workspace)
    return loop, bus


class TestHandleStop:
    """Test _handle_stop() — the legacy stub used by process_direct().

    In the concurrent dispatcher (run()), /stop is handled inline and
    cancels the SessionWorker task.  _handle_stop() is only called from
    process_direct() which has no task registry, so it always returns
    "No active task".
    """

    @pytest.mark.asyncio
    async def test_stop_no_active_task(self):
        from nanobot.bus.events import InboundMessage

        loop, bus = _make_loop()
        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/stop")
        await loop._handle_stop(msg)
        out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        assert "No active task" in out.content

    @pytest.mark.asyncio
    async def test_stop_via_process_message(self):
        """process_message handles /stop directly, returning 'No active task'."""
        from nanobot.bus.events import InboundMessage

        loop, bus = _make_loop()
        # Mock sessions.resolve_session_key to return the key as-is
        loop.sessions.resolve_session_key = MagicMock(return_value="test:c1")
        loop.sessions.get_or_create = MagicMock()

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/stop")
        result = await loop._process_message(msg)

        assert result is not None
        assert "No active task" in result.content


class TestConcurrentSessionModel:
    """Test the concurrent session model used in run().

    In local's architecture, run() manages a dict of SessionWorker instances.
    Each session is an independent asyncio.Task.  /stop cancels the task for
    the matching session_key.  These tests verify the model conceptually.
    """

    @pytest.mark.asyncio
    async def test_active_tasks_dict_exists(self):
        """AgentLoop has _active_tasks dict for task tracking."""
        loop, _ = _make_loop()
        assert hasattr(loop, "_active_tasks")
        assert isinstance(loop._active_tasks, dict)

    @pytest.mark.asyncio
    async def test_task_cancellation_pattern(self):
        """Verify the cancellation pattern used by run() for /stop."""
        cancelled = asyncio.Event()

        async def slow_task():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(slow_task())
        await asyncio.sleep(0)

        # Simulate what run() does on /stop
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

        assert cancelled.is_set()

    @pytest.mark.asyncio
    async def test_multiple_task_cancellation_pattern(self):
        """Verify cancellation of multiple tasks (subagent scenario)."""
        events = [asyncio.Event(), asyncio.Event()]

        async def slow(idx):
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                events[idx].set()
                raise

        tasks = [asyncio.create_task(slow(i)) for i in range(2)]
        await asyncio.sleep(0)

        # Cancel all tasks
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        assert all(e.is_set() for e in events)


class TestSubagentCancellation:
    @pytest.mark.asyncio
    async def test_cancel_by_session(self):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(provider=provider, workspace=MagicMock(), bus=bus)

        cancelled = asyncio.Event()

        async def slow():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(slow())
        await asyncio.sleep(0)
        mgr._running_tasks["sub-1"] = task
        mgr._session_tasks["test:c1"] = {"sub-1"}

        count = await mgr.cancel_by_session("test:c1")
        assert count == 1
        assert cancelled.is_set()

    @pytest.mark.asyncio
    async def test_cancel_by_session_no_tasks(self):
        from nanobot.agent.subagent import SubagentManager
        from nanobot.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(provider=provider, workspace=MagicMock(), bus=bus)
        assert await mgr.cancel_by_session("nonexistent") == 0
