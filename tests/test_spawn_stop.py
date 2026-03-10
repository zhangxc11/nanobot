"""Tests for spawn stop capability (§37).

Tests cover:
- Stop a running subagent
- Stop an already-completed subagent (noop)
- Stop an already-failed subagent (noop)
- Stop an already-stopped subagent (noop)
- Ownership check for stop
- Mutual exclusion: stop + follow_up
- Stopped subagent does not announce
- Stopped subagent can be resumed via follow_up
- Session persistence on stop
- SpawnTool stop parameter schema
- SpawnTool execute routing for stop
"""

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.subagent import (
    SubagentManager,
    SubagentMeta,
)
from nanobot.agent.tools.spawn import SpawnTool


# ── Helpers ──────────────────────────────────────────────────────────────────


class FakeLLMResponse:
    """Minimal LLM response for testing."""

    def __init__(self, content="Done", tool_calls=None, usage=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.has_tool_calls = bool(self.tool_calls)
        self.usage = usage
        self.finish_reason = "stop"


class FakeToolCall:
    """Minimal tool call for testing."""

    def __init__(self, id="tc_1", name="read_file", arguments=None):
        self.id = id
        self.name = name
        self.arguments = arguments or {"path": "/tmp/test.txt"}


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


async def _wait_tasks(mgr, timeout=5.0):
    """Wait for all running tasks to complete."""
    for task in list(mgr._running_tasks.values()):
        if not task.done():
            try:
                await asyncio.wait_for(task, timeout=timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass


# ── Tests: stop_subagent basic ──────────────────────────────────────────────


class TestStopRunning:
    @pytest.mark.asyncio
    async def test_stop_running_subagent(self):
        """stop_subagent on a running subagent should cancel it and set status to 'stopped'."""
        mgr = _make_manager()

        # Use a gate to keep subagent running indefinitely
        gate = asyncio.Event()

        async def blocked_chat(**kwargs):
            await gate.wait()
            return FakeLLMResponse("Should not reach here")

        mgr.provider.chat = blocked_chat

        result = await mgr.spawn(task="long task", session_key="web:123", max_iterations=10)
        task_id = result.split("id: ")[1].split(")")[0]

        # Wait for subagent to start
        await asyncio.sleep(0.1)
        assert task_id in mgr._running_tasks
        assert not mgr._running_tasks[task_id].done()

        # Stop it
        stop_result = await mgr.stop_subagent(
            task_id=task_id,
            parent_session_key="web:123",
            reason="no longer needed",
        )

        assert "stopped" in stop_result.lower()
        assert task_id in stop_result
        assert mgr._task_meta[task_id].status == "stopped"
        # Task should be cleaned up from _running_tasks by done callback
        await asyncio.sleep(0.1)
        assert task_id not in mgr._running_tasks

    @pytest.mark.asyncio
    async def test_stop_running_subagent_empty_reason(self):
        """stop_subagent with empty reason should work."""
        mgr = _make_manager()
        gate = asyncio.Event()

        async def blocked_chat(**kwargs):
            await gate.wait()
            return FakeLLMResponse("nope")

        mgr.provider.chat = blocked_chat

        result = await mgr.spawn(task="task", session_key="web:123")
        task_id = result.split("id: ")[1].split(")")[0]
        await asyncio.sleep(0.1)

        stop_result = await mgr.stop_subagent(
            task_id=task_id,
            parent_session_key="web:123",
            reason="",
        )
        assert "stopped" in stop_result.lower()
        assert mgr._task_meta[task_id].status == "stopped"


class TestStopAlreadyFinished:
    @pytest.mark.asyncio
    async def test_stop_completed_subagent(self):
        """stop_subagent on a completed subagent should return noop message."""
        mgr = _make_manager()
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done"))

        result = await mgr.spawn(task="quick task", session_key="web:123")
        task_id = result.split("id: ")[1].split(")")[0]
        await _wait_tasks(mgr)

        assert mgr._task_meta[task_id].status == "completed"

        stop_result = await mgr.stop_subagent(
            task_id=task_id,
            parent_session_key="web:123",
        )
        assert "already" in stop_result.lower()
        assert "completed" in stop_result.lower()
        # Status should remain completed
        assert mgr._task_meta[task_id].status == "completed"

    @pytest.mark.asyncio
    async def test_stop_failed_subagent(self):
        """stop_subagent on a failed subagent should return noop message."""
        mgr = _make_manager()
        mgr.provider.chat = AsyncMock(side_effect=ValueError("boom"))

        result = await mgr.spawn(task="failing task", session_key="web:123")
        task_id = result.split("id: ")[1].split(")")[0]
        await _wait_tasks(mgr)

        assert mgr._task_meta[task_id].status == "failed"

        stop_result = await mgr.stop_subagent(
            task_id=task_id,
            parent_session_key="web:123",
        )
        assert "already" in stop_result.lower()
        assert "failed" in stop_result.lower()

    @pytest.mark.asyncio
    async def test_stop_already_stopped_subagent(self):
        """stop_subagent on an already stopped subagent should return noop."""
        mgr = _make_manager()
        gate = asyncio.Event()

        async def blocked_chat(**kwargs):
            await gate.wait()
            return FakeLLMResponse("nope")

        mgr.provider.chat = blocked_chat

        result = await mgr.spawn(task="task", session_key="web:123")
        task_id = result.split("id: ")[1].split(")")[0]
        await asyncio.sleep(0.1)

        # Stop it first time
        await mgr.stop_subagent(task_id=task_id, parent_session_key="web:123")
        assert mgr._task_meta[task_id].status == "stopped"

        # Stop it again
        stop_result = await mgr.stop_subagent(
            task_id=task_id,
            parent_session_key="web:123",
        )
        assert "already" in stop_result.lower()
        assert "stopped" in stop_result.lower()


# ── Tests: stop ownership ───────────────────────────────────────────────────


class TestStopOwnership:
    @pytest.mark.asyncio
    async def test_stop_wrong_session(self):
        """stop_subagent should reject if caller is not the parent."""
        mgr = _make_manager()
        gate = asyncio.Event()

        async def blocked_chat(**kwargs):
            await gate.wait()
            return FakeLLMResponse("nope")

        mgr.provider.chat = blocked_chat

        result = await mgr.spawn(task="task", session_key="web:123")
        task_id = result.split("id: ")[1].split(")")[0]
        await asyncio.sleep(0.1)

        with pytest.raises(ValueError, match="does not belong"):
            await mgr.stop_subagent(
                task_id=task_id,
                parent_session_key="web:456",
            )

        # Clean up
        gate.set()
        await _wait_tasks(mgr)

    @pytest.mark.asyncio
    async def test_stop_unknown_task(self):
        """stop_subagent should reject unknown task_id."""
        mgr = _make_manager()

        with pytest.raises(ValueError, match="Unknown subagent"):
            await mgr.stop_subagent(
                task_id="nonexistent",
                parent_session_key="web:123",
            )


# ── Tests: stop does not announce ────────────────────────────────────────────


class TestStopNoAnnounce:
    @pytest.mark.asyncio
    async def test_stopped_subagent_does_not_announce(self):
        """A stopped subagent should NOT send announce to parent."""
        mgr = _make_manager()
        gate = asyncio.Event()

        async def blocked_chat(**kwargs):
            await gate.wait()
            return FakeLLMResponse("nope")

        mgr.provider.chat = blocked_chat

        # Track announce calls
        announce_calls = []
        original_announce = mgr._announce_result

        async def tracking_announce(*args, **kwargs):
            announce_calls.append((args, kwargs))
            return await original_announce(*args, **kwargs)

        mgr._announce_result = tracking_announce

        result = await mgr.spawn(task="task", session_key="web:123")
        task_id = result.split("id: ")[1].split(")")[0]
        await asyncio.sleep(0.1)

        # Stop it
        await mgr.stop_subagent(task_id=task_id, parent_session_key="web:123")
        await asyncio.sleep(0.2)

        # No announce should have been called
        assert len(announce_calls) == 0

    @pytest.mark.asyncio
    async def test_normal_cancel_does_announce(self):
        """A non-stop cancel (e.g. cancel_by_session) should still announce."""
        mgr = _make_manager()
        gate = asyncio.Event()

        async def blocked_chat(**kwargs):
            await gate.wait()
            return FakeLLMResponse("nope")

        mgr.provider.chat = blocked_chat

        # Track announce calls
        announce_calls = []
        original_announce = mgr._announce_result

        async def tracking_announce(*args, **kwargs):
            announce_calls.append((args, kwargs))
            # Don't actually call original to avoid bus errors
            return None

        mgr._announce_result = tracking_announce

        result = await mgr.spawn(task="task", session_key="web:123")
        task_id = result.split("id: ")[1].split(")")[0]
        await asyncio.sleep(0.1)

        # Cancel via cancel_by_session (not stop)
        cancelled = await mgr.cancel_by_session("web:123")
        assert cancelled >= 1

        await asyncio.sleep(0.2)

        # Announce SHOULD have been called for non-stop cancel
        assert len(announce_calls) >= 1


# ── Tests: stop + follow_up resume ──────────────────────────────────────────


class TestStopThenResume:
    @pytest.mark.asyncio
    async def test_resume_stopped_subagent(self):
        """A stopped subagent should be resumable via follow_up."""
        session_mgr = MagicMock()
        mock_session = MagicMock()
        mock_session.get_history.return_value = [
            {"role": "user", "content": "original task"},
            {"role": "assistant", "content": "working on it..."},
            {"role": "user", "content": "[Stopped by parent session] no longer needed"},
        ]
        session_mgr.get_or_create.return_value = mock_session

        mgr = _make_manager(session_manager=session_mgr)
        gate = asyncio.Event()

        async def blocked_chat(**kwargs):
            await gate.wait()
            return FakeLLMResponse("nope")

        mgr.provider.chat = blocked_chat

        result = await mgr.spawn(task="task", session_key="web:123")
        task_id = result.split("id: ")[1].split(")")[0]
        await asyncio.sleep(0.1)

        # Stop it
        await mgr.stop_subagent(task_id=task_id, parent_session_key="web:123")
        assert mgr._task_meta[task_id].status == "stopped"

        # Resume via follow_up
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Resumed after stop"))
        resume_result = await mgr.follow_up(
            task_id=task_id,
            message="Actually, please continue",
            parent_session_key="web:123",
        )
        assert "resumed" in resume_result.lower()

        await _wait_tasks(mgr)
        assert mgr._task_meta[task_id].status == "completed"


# ── Tests: session persistence on stop ──────────────────────────────────────


class TestStopPersistence:
    @pytest.mark.asyncio
    async def test_stop_persists_message(self):
        """stop_subagent should persist a stop message to session."""
        session_mgr = MagicMock()
        mock_session = MagicMock()
        session_mgr.get_or_create.return_value = mock_session

        mgr = _make_manager(session_manager=session_mgr)
        gate = asyncio.Event()

        async def blocked_chat(**kwargs):
            await gate.wait()
            return FakeLLMResponse("nope")

        mgr.provider.chat = blocked_chat

        result = await mgr.spawn(task="task", session_key="web:123", persist=True)
        task_id = result.split("id: ")[1].split(")")[0]
        await asyncio.sleep(0.1)

        # Stop with reason
        await mgr.stop_subagent(
            task_id=task_id,
            parent_session_key="web:123",
            reason="user changed mind",
        )

        # Verify append_message was called with stop message
        calls = session_mgr.append_message.call_args_list
        # Find the stop message (last call should be the stop message)
        stop_calls = [
            c for c in calls
            if "Stopped by parent session" in str(c)
        ]
        assert len(stop_calls) >= 1
        stop_msg = stop_calls[-1][0][1]  # Second positional arg is the message dict
        assert "Stopped by parent session" in stop_msg["content"]
        assert "user changed mind" in stop_msg["content"]

    @pytest.mark.asyncio
    async def test_stop_no_persist_when_persist_false(self):
        """stop_subagent should not persist if persist=False."""
        session_mgr = MagicMock()
        mgr = _make_manager(session_manager=session_mgr)
        gate = asyncio.Event()

        async def blocked_chat(**kwargs):
            await gate.wait()
            return FakeLLMResponse("nope")

        mgr.provider.chat = blocked_chat

        result = await mgr.spawn(task="task", session_key="web:123", persist=False)
        task_id = result.split("id: ")[1].split(")")[0]
        await asyncio.sleep(0.1)

        # Stop it
        await mgr.stop_subagent(
            task_id=task_id,
            parent_session_key="web:123",
            reason="test",
        )

        # No session append should have happened for the stop message
        # (initial spawn message might have been appended if persist was True, but it's False)
        stop_calls = [
            c for c in session_mgr.append_message.call_args_list
            if "Stopped by parent session" in str(c)
        ]
        assert len(stop_calls) == 0


# ── Tests: _stop_flags cleanup ──────────────────────────────────────────────


class TestStopFlagsCleanup:
    @pytest.mark.asyncio
    async def test_stop_flag_cleaned_after_cancel(self):
        """_stop_flags should be cleaned up after the subagent handles CancelledError."""
        mgr = _make_manager()
        gate = asyncio.Event()

        async def blocked_chat(**kwargs):
            await gate.wait()
            return FakeLLMResponse("nope")

        mgr.provider.chat = blocked_chat

        result = await mgr.spawn(task="task", session_key="web:123")
        task_id = result.split("id: ")[1].split(")")[0]
        await asyncio.sleep(0.1)

        # Before stop
        assert task_id not in mgr._stop_flags

        # Stop
        await mgr.stop_subagent(task_id=task_id, parent_session_key="web:123")

        # After stop completes, flag should be cleaned
        assert task_id not in mgr._stop_flags


# ── Tests: SpawnTool stop parameter ─────────────────────────────────────────


class TestSpawnToolStop:
    def test_parameters_include_stop(self):
        """SpawnTool parameters should include stop."""
        tool = SpawnTool(manager=MagicMock())
        params = tool.parameters
        assert "stop" in params["properties"]
        assert params["properties"]["stop"]["type"] == "string"
        # stop should NOT be required
        assert "stop" not in params.get("required", [])

    def test_description_mentions_stop(self):
        """SpawnTool description should mention stop capability."""
        tool = SpawnTool(manager=MagicMock())
        assert "stop" in tool.description.lower()

    @pytest.mark.asyncio
    async def test_execute_routes_to_stop(self):
        """execute with stop should call manager.stop_subagent."""
        manager = AsyncMock()
        manager.stop_subagent = AsyncMock(return_value="stopped")
        tool = SpawnTool(manager=manager)
        tool.set_context("web", "123", "web:123")

        result = await tool.execute(task="no longer needed", stop="abc12345")

        assert result == "stopped"
        manager.stop_subagent.assert_called_once_with(
            task_id="abc12345",
            parent_session_key="web:123",
            reason="no longer needed",
        )

    @pytest.mark.asyncio
    async def test_execute_stop_with_empty_task(self):
        """execute with stop and empty task should work."""
        manager = AsyncMock()
        manager.stop_subagent = AsyncMock(return_value="stopped")
        tool = SpawnTool(manager=manager)
        tool.set_context("web", "123", "web:123")

        result = await tool.execute(task="", stop="abc12345")

        assert result == "stopped"
        manager.stop_subagent.assert_called_once_with(
            task_id="abc12345",
            parent_session_key="web:123",
            reason="",
        )

    @pytest.mark.asyncio
    async def test_execute_stop_and_follow_up_mutual_exclusion(self):
        """execute with both stop and follow_up should return error."""
        manager = AsyncMock()
        tool = SpawnTool(manager=manager)
        tool.set_context("web", "123", "web:123")

        result = await tool.execute(
            task="test",
            stop="abc12345",
            follow_up="def67890",
        )

        assert "error" in result.lower()
        assert "mutually exclusive" in result.lower()
        # Neither should have been called
        manager.stop_subagent.assert_not_called()
        manager.follow_up.assert_not_called()
        manager.spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_without_stop_routes_to_spawn(self):
        """execute without stop should still call manager.spawn."""
        manager = AsyncMock()
        manager.spawn = AsyncMock(return_value="started")
        tool = SpawnTool(manager=manager)
        tool.set_context("web", "123", "web:123")

        result = await tool.execute(task="new task")

        assert result == "started"
        manager.spawn.assert_called_once()
        manager.stop_subagent.assert_not_called()
