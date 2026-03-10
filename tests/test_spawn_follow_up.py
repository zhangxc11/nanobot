"""Tests for spawn follow_up capability (§36).

Tests cover:
- follow_up inject (running subagent)
- follow_up resume (finished subagent)
- Ownership check failures
- Resume with persist=False error
- Inject drains multiple messages
- Resume resets iteration budget
- SpawnTool follow_up parameter schema
- SpawnTool execute routing
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.subagent import (
    SubagentManager,
    SubagentMeta,
    MAX_SUBAGENT_ITERATIONS,
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
            except asyncio.TimeoutError:
                pass


# ── Tests: SubagentMeta ─────────────────────────────────────────────────────


class TestSubagentMeta:
    def test_defaults(self):
        meta = SubagentMeta(
            task_id="abc12345",
            subagent_session_key="subagent:test_abc12345",
            parent_session_key="web:123",
            label="test task",
            origin={"channel": "web", "chat_id": "123"},
        )
        assert meta.status == "running"
        assert meta.persist is True
        assert isinstance(meta.inject_queue, asyncio.Queue)
        assert meta.inject_queue.empty()

    def test_custom_values(self):
        meta = SubagentMeta(
            task_id="xyz",
            subagent_session_key="subagent:xyz",
            parent_session_key=None,
            label="custom",
            origin={"channel": "cli", "chat_id": "direct"},
            status="completed",
            max_iterations=50,
            persist=False,
        )
        assert meta.status == "completed"
        assert meta.max_iterations == 50
        assert meta.persist is False


# ── Tests: _task_meta lifecycle ─────────────────────────────────────────────


class TestTaskMetaLifecycle:
    @pytest.mark.asyncio
    async def test_meta_created_on_spawn(self):
        """spawn() should create a SubagentMeta entry."""
        mgr = _make_manager()
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done"))

        result = await mgr.spawn(task="test", session_key="web:123")
        # Extract task_id from result
        task_id = result.split("id: ")[1].split(")")[0]

        assert task_id in mgr._task_meta
        meta = mgr._task_meta[task_id]
        assert meta.parent_session_key == "web:123"
        assert meta.status == "running"
        assert meta.subagent_session_key.startswith("subagent:")

        await _wait_tasks(mgr)

    @pytest.mark.asyncio
    async def test_meta_retained_after_completion(self):
        """_task_meta should NOT be cleaned up after subagent finishes."""
        mgr = _make_manager()
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done"))

        result = await mgr.spawn(task="test", session_key="web:123")
        task_id = result.split("id: ")[1].split(")")[0]

        await _wait_tasks(mgr)

        # Meta should still exist
        assert task_id in mgr._task_meta
        # But running_tasks should be cleaned up
        assert task_id not in mgr._running_tasks

    @pytest.mark.asyncio
    async def test_session_tasks_retained_after_completion(self):
        """_session_tasks should NOT be cleaned up (needed for ownership check)."""
        mgr = _make_manager()
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done"))

        result = await mgr.spawn(task="test", session_key="web:123")
        task_id = result.split("id: ")[1].split(")")[0]

        await _wait_tasks(mgr)

        # session_tasks should still have the mapping
        assert "web:123" in mgr._session_tasks
        assert task_id in mgr._session_tasks["web:123"]

    @pytest.mark.asyncio
    async def test_meta_status_completed(self):
        """Status should be 'completed' when subagent finishes normally."""
        mgr = _make_manager()
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done"))

        result = await mgr.spawn(task="test", session_key="web:123")
        task_id = result.split("id: ")[1].split(")")[0]

        await _wait_tasks(mgr)

        assert mgr._task_meta[task_id].status == "completed"

    @pytest.mark.asyncio
    async def test_meta_status_max_iterations(self):
        """Status should be 'max_iterations' when budget exhausted."""
        mgr = _make_manager()
        # Always return tool calls → never finishes
        mgr.provider.chat = AsyncMock(
            return_value=FakeLLMResponse(
                tool_calls=[FakeToolCall()],
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )
        )

        result = await mgr.spawn(task="test", session_key="web:123", max_iterations=3)
        task_id = result.split("id: ")[1].split(")")[0]

        await _wait_tasks(mgr)

        assert mgr._task_meta[task_id].status == "max_iterations"

    @pytest.mark.asyncio
    async def test_meta_status_failed(self):
        """Status should be 'failed' when subagent raises an exception."""
        mgr = _make_manager()
        mgr.provider.chat = AsyncMock(side_effect=ValueError("boom"))

        result = await mgr.spawn(task="test", session_key="web:123")
        task_id = result.split("id: ")[1].split(")")[0]

        await _wait_tasks(mgr)

        assert mgr._task_meta[task_id].status == "failed"


# ── Tests: _check_ownership ─────────────────────────────────────────────────


class TestCheckOwnership:
    @pytest.mark.asyncio
    async def test_ownership_valid(self):
        mgr = _make_manager()
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done"))

        result = await mgr.spawn(task="test", session_key="web:123")
        task_id = result.split("id: ")[1].split(")")[0]
        await _wait_tasks(mgr)

        # Should not raise
        meta = mgr._check_ownership("web:123", task_id)
        assert meta.task_id == task_id

    @pytest.mark.asyncio
    async def test_ownership_wrong_session(self):
        mgr = _make_manager()
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done"))

        result = await mgr.spawn(task="test", session_key="web:123")
        task_id = result.split("id: ")[1].split(")")[0]
        await _wait_tasks(mgr)

        with pytest.raises(ValueError, match="does not belong"):
            mgr._check_ownership("web:456", task_id)

    def test_ownership_unknown_task(self):
        mgr = _make_manager()
        with pytest.raises(ValueError, match="Unknown subagent"):
            mgr._check_ownership("web:123", "nonexistent")


# ── Tests: follow_up inject ─────────────────────────────────────────────────


class TestFollowUpInject:
    @pytest.mark.asyncio
    async def test_inject_into_running_subagent(self):
        """follow_up on a running subagent should inject into queue."""
        mgr = _make_manager()

        # Use a gate to keep subagent running until we inject
        gate = asyncio.Event()
        call_count = 0

        async def slow_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: return tool call to keep running
                return FakeLLMResponse(
                    tool_calls=[FakeToolCall(id="tc_1")],
                    usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                )
            # Second call: wait for gate, then check if inject was received
            await gate.wait()
            return FakeLLMResponse("Done after inject")

        mgr.provider.chat = slow_chat

        result = await mgr.spawn(task="slow task", session_key="web:123", max_iterations=10)
        task_id = result.split("id: ")[1].split(")")[0]

        # Wait a bit for first iteration to start
        await asyncio.sleep(0.2)

        # Verify subagent is running
        assert task_id in mgr._running_tasks
        assert not mgr._running_tasks[task_id].done()

        # Inject message
        inject_result = await mgr.follow_up(
            task_id=task_id,
            message="Here is additional info",
            parent_session_key="web:123",
        )
        assert "injected" in inject_result.lower()
        assert task_id in inject_result

        # Release the gate
        gate.set()
        await _wait_tasks(mgr)

    @pytest.mark.asyncio
    async def test_inject_multiple_messages_drained(self):
        """Multiple injected messages should all be drained at checkpoint."""
        mgr = _make_manager()

        messages_at_llm_call = []
        # Use a gate to pause after first tool execution, giving us time to inject
        inject_ready = asyncio.Event()
        injected_done = asyncio.Event()
        call_count = 0

        async def capturing_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return FakeLLMResponse(
                    tool_calls=[FakeToolCall(id="tc_1")],
                    usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                )
            if call_count == 2:
                # After inject checkpoint — capture messages
                messages_at_llm_call.append(list(kwargs.get("messages", [])))
                return FakeLLMResponse("Done")
            return FakeLLMResponse("Done")

        mgr.provider.chat = capturing_chat

        result = await mgr.spawn(task="test", session_key="web:123", max_iterations=10)
        task_id = result.split("id: ")[1].split(")")[0]

        # We need to inject BEFORE the inject checkpoint runs.
        # The inject checkpoint runs after tool execution, before the next LLM call.
        # Since tool execution is fast (mocked), we put messages directly into the queue.
        # The subagent may have already completed by now, so let's check:
        await asyncio.sleep(0.05)

        # If the subagent already finished, the test concept doesn't apply.
        # Instead, directly test the drain logic by pre-loading the queue
        # before spawn so messages are there when checkpoint runs.

        await _wait_tasks(mgr)

        # Alternative approach: test the drain mechanism directly
        # by verifying inject_queue is checked in the loop.
        # We'll test this by pre-loading the queue via meta before the subagent runs.

        # Reset for a controlled test
        mgr2 = _make_manager()
        call_count2 = 0
        messages_captured2 = []

        async def controlled_chat(**kwargs):
            nonlocal call_count2
            call_count2 += 1
            if call_count2 == 1:
                # Return tool call; after tool execution, inject checkpoint will drain
                return FakeLLMResponse(
                    tool_calls=[FakeToolCall(id="tc_1")],
                    usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                )
            # Capture messages on second call
            messages_captured2.append(list(kwargs.get("messages", [])))
            return FakeLLMResponse("Done")

        mgr2.provider.chat = controlled_chat

        # Spawn and immediately pre-load the inject queue
        result2 = await mgr2.spawn(task="test2", session_key="web:123", max_iterations=10)
        task_id2 = result2.split("id: ")[1].split(")")[0]

        # Pre-load messages into inject queue immediately
        meta2 = mgr2._task_meta[task_id2]
        meta2.inject_queue.put_nowait("msg 1")
        meta2.inject_queue.put_nowait("msg 2")
        meta2.inject_queue.put_nowait("msg 3")

        await _wait_tasks(mgr2)

        # Verify messages were drained
        assert len(messages_captured2) >= 1
        msgs = messages_captured2[0]
        injected = [m for m in msgs if m.get("role") == "user" and "parent session" in m.get("content", "")]
        assert len(injected) == 3
        assert "msg 1" in injected[0]["content"]
        assert "msg 2" in injected[1]["content"]
        assert "msg 3" in injected[2]["content"]


# ── Tests: follow_up resume ─────────────────────────────────────────────────


class TestFollowUpResume:
    @pytest.mark.asyncio
    async def test_resume_finished_subagent(self):
        """follow_up on a finished subagent should resume it."""
        session_mgr = MagicMock()
        mock_session = MagicMock()
        # Simulate session with history
        mock_session.get_history.return_value = [
            {"role": "user", "content": "original task"},
            {"role": "assistant", "content": "I did something", "tool_calls": [
                {"id": "tc_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "tc_1", "name": "read_file", "content": "file content"},
            {"role": "assistant", "content": "Done with original task"},
        ]
        session_mgr.get_or_create.return_value = mock_session

        mgr = _make_manager(session_manager=session_mgr)
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done"))

        # First spawn and wait for completion
        result = await mgr.spawn(task="original task", session_key="web:123")
        task_id = result.split("id: ")[1].split(")")[0]
        await _wait_tasks(mgr)

        assert mgr._task_meta[task_id].status == "completed"

        # Reset mock for resume
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Resumed OK"))

        # Follow up (resume)
        resume_result = await mgr.follow_up(
            task_id=task_id,
            message="Please continue with step 2",
            parent_session_key="web:123",
        )
        assert "resumed" in resume_result.lower()
        assert task_id in resume_result

        await _wait_tasks(mgr)

        # Meta should be updated
        assert mgr._task_meta[task_id].status == "completed"

    @pytest.mark.asyncio
    async def test_resume_with_custom_max_iterations(self):
        """Resume should use fresh max_iterations budget."""
        session_mgr = MagicMock()
        mock_session = MagicMock()
        mock_session.get_history.return_value = [
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "done"},
        ]
        session_mgr.get_or_create.return_value = mock_session

        mgr = _make_manager(session_manager=session_mgr)
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done"))

        result = await mgr.spawn(task="task", session_key="web:123", max_iterations=10)
        task_id = result.split("id: ")[1].split(")")[0]
        await _wait_tasks(mgr)

        # Resume with different max_iterations
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Resumed"))
        resume_result = await mgr.follow_up(
            task_id=task_id,
            message="continue",
            parent_session_key="web:123",
            max_iterations=50,
        )
        assert "max_iterations=50" in resume_result

        await _wait_tasks(mgr)

    @pytest.mark.asyncio
    async def test_resume_persist_false_error(self):
        """Resume should fail if subagent was not persisted."""
        mgr = _make_manager()
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done"))

        result = await mgr.spawn(task="task", session_key="web:123", persist=False)
        task_id = result.split("id: ")[1].split(")")[0]
        await _wait_tasks(mgr)

        with pytest.raises(ValueError, match="not persisted"):
            await mgr.follow_up(
                task_id=task_id,
                message="continue",
                parent_session_key="web:123",
            )

    @pytest.mark.asyncio
    async def test_resume_no_session_manager_error(self):
        """Resume should fail if no SessionManager is available."""
        mgr = _make_manager()  # No session_manager
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done"))

        result = await mgr.spawn(task="task", session_key="web:123", persist=True)
        task_id = result.split("id: ")[1].split(")")[0]
        await _wait_tasks(mgr)

        with pytest.raises(ValueError, match="SessionManager"):
            await mgr.follow_up(
                task_id=task_id,
                message="continue",
                parent_session_key="web:123",
            )

    @pytest.mark.asyncio
    async def test_resume_then_follow_up_again(self):
        """After resume completes, should be able to follow_up again."""
        session_mgr = MagicMock()
        mock_session = MagicMock()
        mock_session.get_history.return_value = [
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "done"},
        ]
        session_mgr.get_or_create.return_value = mock_session

        mgr = _make_manager(session_manager=session_mgr)
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done"))

        # Spawn and complete
        result = await mgr.spawn(task="task", session_key="web:123")
        task_id = result.split("id: ")[1].split(")")[0]
        await _wait_tasks(mgr)
        assert mgr._task_meta[task_id].status == "completed"

        # First resume
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Resumed 1"))
        await mgr.follow_up(task_id=task_id, message="continue 1", parent_session_key="web:123")
        await _wait_tasks(mgr)
        assert mgr._task_meta[task_id].status == "completed"

        # Second resume
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Resumed 2"))
        await mgr.follow_up(task_id=task_id, message="continue 2", parent_session_key="web:123")
        await _wait_tasks(mgr)
        assert mgr._task_meta[task_id].status == "completed"


# ── Tests: follow_up ownership ──────────────────────────────────────────────


class TestFollowUpOwnership:
    @pytest.mark.asyncio
    async def test_follow_up_wrong_session(self):
        """follow_up should reject if caller is not the parent."""
        mgr = _make_manager()
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done"))

        result = await mgr.spawn(task="test", session_key="web:123")
        task_id = result.split("id: ")[1].split(")")[0]
        await _wait_tasks(mgr)

        with pytest.raises(ValueError, match="does not belong"):
            await mgr.follow_up(
                task_id=task_id,
                message="hijack attempt",
                parent_session_key="web:456",
            )

    @pytest.mark.asyncio
    async def test_follow_up_unknown_task(self):
        """follow_up should reject unknown task_id."""
        mgr = _make_manager()

        with pytest.raises(ValueError, match="Unknown subagent"):
            await mgr.follow_up(
                task_id="nonexistent",
                message="hello",
                parent_session_key="web:123",
            )


# ── Tests: SpawnTool follow_up parameter ────────────────────────────────────


class TestSpawnToolFollowUp:
    def test_parameters_include_follow_up(self):
        """SpawnTool parameters should include follow_up."""
        tool = SpawnTool(manager=MagicMock())
        params = tool.parameters
        assert "follow_up" in params["properties"]
        assert params["properties"]["follow_up"]["type"] == "string"
        # follow_up should NOT be required
        assert "follow_up" not in params.get("required", [])

    def test_description_mentions_follow_up(self):
        """SpawnTool description should mention follow_up capability."""
        tool = SpawnTool(manager=MagicMock())
        assert "follow_up" in tool.description.lower() or "follow-up" in tool.description.lower()

    @pytest.mark.asyncio
    async def test_execute_routes_to_follow_up(self):
        """execute with follow_up should call manager.follow_up."""
        manager = AsyncMock()
        manager.follow_up = AsyncMock(return_value="injected")
        tool = SpawnTool(manager=manager)
        tool.set_context("web", "123", "web:123")

        result = await tool.execute(task="continue please", follow_up="abc12345")

        assert result == "injected"
        manager.follow_up.assert_called_once_with(
            task_id="abc12345",
            message="continue please",
            parent_session_key="web:123",
            max_iterations=None,
        )

    @pytest.mark.asyncio
    async def test_execute_routes_to_spawn(self):
        """execute without follow_up should call manager.spawn."""
        manager = AsyncMock()
        manager.spawn = AsyncMock(return_value="started")
        tool = SpawnTool(manager=manager)
        tool.set_context("web", "123", "web:123")

        result = await tool.execute(task="new task")

        assert result == "started"
        manager.spawn.assert_called_once()
        manager.follow_up.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_follow_up_with_max_iterations(self):
        """follow_up should pass max_iterations to manager."""
        manager = AsyncMock()
        manager.follow_up = AsyncMock(return_value="resumed")
        tool = SpawnTool(manager=manager)
        tool.set_context("web", "123", "web:123")

        await tool.execute(task="continue", follow_up="abc12345", max_iterations=50)

        call_kwargs = manager.follow_up.call_args[1]
        assert call_kwargs["max_iterations"] == 50


# ── Tests: Task keeper with follow_up ───────────────────────────────────────


class TestFollowUpTaskKeeper:
    @pytest.mark.asyncio
    async def test_resume_registers_with_task_keeper(self):
        """Resume should register the new task with task_keeper."""
        kept_tasks = []

        def keeper(task):
            kept_tasks.append(task)

        session_mgr = MagicMock()
        mock_session = MagicMock()
        mock_session.get_history.return_value = [
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "done"},
        ]
        session_mgr.get_or_create.return_value = mock_session

        mgr = _make_manager(session_manager=session_mgr, task_keeper=keeper)
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done"))

        result = await mgr.spawn(task="task", session_key="web:123")
        task_id = result.split("id: ")[1].split(")")[0]
        await _wait_tasks(mgr)

        assert len(kept_tasks) == 1  # From initial spawn

        # Resume
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Resumed"))
        await mgr.follow_up(task_id=task_id, message="continue", parent_session_key="web:123")
        await _wait_tasks(mgr)

        assert len(kept_tasks) == 2  # Second task from resume
