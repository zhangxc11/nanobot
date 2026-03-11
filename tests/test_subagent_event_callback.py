"""Tests for SubagentEventCallback protocol (§47).

Tests cover:
- Protocol definition and structural subtyping
- on_subagent_spawned called on spawn (running + queued)
- on_subagent_progress called each iteration
- on_subagent_retry called before retry sleep
- on_subagent_done called at terminal states (completed, failed, stopped, max_iterations)
- AgentLoop on_iteration callback
- Callback errors are swallowed (don't break agent loop)
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from nanobot.agent.subagent import (
    SubagentEventCallback,
    SubagentManager,
    SubagentMeta,
)


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


class FakeToolCall:
    """Minimal tool call for testing."""

    def __init__(self, id="tc_1", name="read_file", arguments=None):
        self.id = id
        self.name = name
        self.arguments = arguments or {"path": "/tmp/test.txt"}


class MockEventCallback:
    """Concrete implementation of SubagentEventCallback for testing."""

    def __init__(self):
        self.spawned_calls: list[dict] = []  # snapshot dicts
        self.progress_calls: list[tuple] = []
        self.retry_calls: list[tuple] = []
        self.done_calls: list[tuple] = []

    def on_subagent_spawned(self, meta: SubagentMeta) -> None:
        # Snapshot key fields at callback time (meta is mutable)
        self.spawned_calls.append({
            "task_id": meta.task_id,
            "status": meta.status,
            "label": meta.label,
            "parent_session_key": meta.parent_session_key,
            "max_iterations": meta.max_iterations,
        })

    def on_subagent_progress(self, task_id: str, iteration: int, max_iterations: int, last_tool: str | None) -> None:
        self.progress_calls.append((task_id, iteration, max_iterations, last_tool))

    def on_subagent_retry(self, task_id: str, attempt: int, max_retries: int, delay: float, error: str, is_fast: bool) -> None:
        self.retry_calls.append((task_id, attempt, max_retries, delay, error, is_fast))

    def on_subagent_done(self, task_id: str, status: str, error: str | None) -> None:
        self.done_calls.append((task_id, status, error))


def _make_manager(event_callback=None, **kwargs):
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
        event_callback=event_callback,
    )
    defaults.update(kwargs)
    return SubagentManager(**defaults)


# ── Tests: Protocol definition ───────────────────────────────────────────────


class TestSubagentEventCallbackProtocol:
    """Test that the protocol is correctly defined and supports structural subtyping."""

    def test_protocol_is_runtime_checkable(self):
        """SubagentEventCallback should be runtime_checkable."""
        assert isinstance(MockEventCallback(), SubagentEventCallback)

    def test_protocol_has_four_methods(self):
        """Protocol should define exactly 4 callback methods."""
        methods = [m for m in dir(SubagentEventCallback) if m.startswith("on_subagent_")]
        assert len(methods) == 4
        assert "on_subagent_spawned" in methods
        assert "on_subagent_progress" in methods
        assert "on_subagent_retry" in methods
        assert "on_subagent_done" in methods

    def test_manager_accepts_callback(self):
        """SubagentManager should accept event_callback parameter."""
        cb = MockEventCallback()
        mgr = _make_manager(event_callback=cb)
        assert mgr._event_callback is cb

    def test_manager_default_no_callback(self):
        """SubagentManager default has no callback."""
        mgr = _make_manager()
        assert mgr._event_callback is None


# ── Tests: on_subagent_spawned ───────────────────────────────────────────────


class TestOnSubagentSpawned:
    """Test on_subagent_spawned callback in spawn()."""

    @pytest.mark.asyncio
    async def test_spawned_called_on_spawn(self):
        """Callback should be called when a subagent is spawned (running)."""
        cb = MockEventCallback()
        mgr = _make_manager(event_callback=cb)
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done"))

        result = await mgr.spawn("test task", session_key="parent:1")
        # Wait briefly for async task to start
        await asyncio.sleep(0.1)

        assert len(cb.spawned_calls) == 1
        snap = cb.spawned_calls[0]
        assert snap["status"] == "running"
        assert snap["parent_session_key"] == "parent:1"
        assert snap["label"] == "test task"

    @pytest.mark.asyncio
    async def test_spawned_called_on_queued(self):
        """Callback should be called even when task is queued."""
        cb = MockEventCallback()
        mgr = _make_manager(event_callback=cb, max_concurrency=1)
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done"))

        # Spawn first task (running)
        await mgr.spawn("task 1", session_key="parent:1")
        # Spawn second task (should be queued since max_concurrency=1)
        await mgr.spawn("task 2", session_key="parent:1")

        assert len(cb.spawned_calls) == 2
        assert cb.spawned_calls[0]["status"] == "running"
        assert cb.spawned_calls[1]["status"] == "queued"

        # Clean up
        await asyncio.sleep(0.2)

    @pytest.mark.asyncio
    async def test_spawned_callback_error_swallowed(self):
        """Errors in spawned callback should not prevent spawn."""
        class BadCallback(MockEventCallback):
            def on_subagent_spawned(self, meta):
                raise RuntimeError("callback error")

        cb = BadCallback()
        mgr = _make_manager(event_callback=cb)
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done"))

        # Should not raise
        result = await mgr.spawn("test task", session_key="parent:1")
        assert "started" in result
        await asyncio.sleep(0.1)


# ── Tests: on_subagent_progress ──────────────────────────────────────────────


class TestOnSubagentProgress:
    """Test on_subagent_progress callback in _run_subagent iteration loop."""

    @pytest.mark.asyncio
    async def test_progress_called_each_iteration(self):
        """Callback should be called at the start of each iteration."""
        cb = MockEventCallback()
        mgr = _make_manager(event_callback=cb)

        # First call returns a tool call, second returns final response
        tool_call = FakeToolCall(id="tc_1", name="read_file")
        mgr.provider.chat = AsyncMock(side_effect=[
            FakeLLMResponse(content="", tool_calls=[tool_call]),
            FakeLLMResponse(content="Final result"),
        ])

        await mgr.spawn("test task", session_key="parent:1", max_iterations=10)
        await asyncio.sleep(0.3)

        # Should have 2 progress calls (2 iterations)
        assert len(cb.progress_calls) >= 2
        # First iteration: no last_tool yet
        assert cb.progress_calls[0][1] == 1  # iteration
        assert cb.progress_calls[0][2] == 10  # max_iterations
        assert cb.progress_calls[0][3] is None  # last_tool (none yet)
        # Second iteration: last_tool should be "read_file"
        assert cb.progress_calls[1][1] == 2
        assert cb.progress_calls[1][3] == "read_file"


# ── Tests: on_subagent_retry ────────────────────────────────────────────────


class TestOnSubagentRetry:
    """Test on_subagent_retry callback in _chat_with_retry."""

    @pytest.mark.asyncio
    async def test_retry_called_on_retryable_error(self):
        """Callback should be called when a retryable error triggers a retry."""
        cb = MockEventCallback()
        mgr = _make_manager(event_callback=cb)

        # Simulate: first call fails with retryable error, second succeeds
        RateLimitError = type("RateLimitError", (Exception,), {})
        error = RateLimitError("rate limited")
        mgr.provider.chat = AsyncMock(side_effect=[
            error,
            FakeLLMResponse("Done"),
        ])

        await mgr.spawn("test task", session_key="parent:1")
        # Wait for retry + completion
        await asyncio.sleep(2.0)

        assert len(cb.retry_calls) >= 1
        task_id, attempt, max_retries, delay, err_str, is_fast = cb.retry_calls[0]
        assert attempt == 1
        assert max_retries == 5
        assert "rate limit" in err_str
        assert isinstance(delay, (int, float))


# ── Tests: on_subagent_done ──────────────────────────────────────────────────


class TestOnSubagentDone:
    """Test on_subagent_done callback at terminal states."""

    @pytest.mark.asyncio
    async def test_done_called_on_completion(self):
        """Callback should be called when subagent completes normally."""
        cb = MockEventCallback()
        mgr = _make_manager(event_callback=cb)
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done"))

        await mgr.spawn("test task", session_key="parent:1")
        await asyncio.sleep(0.3)

        assert len(cb.done_calls) == 1
        task_id, status, error = cb.done_calls[0]
        assert status == "completed"
        assert error is None

    @pytest.mark.asyncio
    async def test_done_called_on_failure(self):
        """Callback should be called when subagent fails."""
        cb = MockEventCallback()
        mgr = _make_manager(event_callback=cb)
        mgr.provider.chat = AsyncMock(side_effect=ValueError("non-retryable error"))

        await mgr.spawn("test task", session_key="parent:1")
        await asyncio.sleep(0.3)

        assert len(cb.done_calls) == 1
        task_id, status, error = cb.done_calls[0]
        assert status == "failed"
        assert "non-retryable error" in error

    @pytest.mark.asyncio
    async def test_done_called_on_max_iterations(self):
        """Callback should be called when subagent hits max iterations."""
        cb = MockEventCallback()
        mgr = _make_manager(event_callback=cb)

        # Always return tool calls to exhaust iterations
        tool_call = FakeToolCall(id="tc_1", name="exec")
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse(content="", tool_calls=[tool_call]))

        await mgr.spawn("test task", session_key="parent:1", max_iterations=2)
        await asyncio.sleep(0.5)

        assert len(cb.done_calls) == 1
        task_id, status, error = cb.done_calls[0]
        assert status == "max_iterations"
        assert error is None

    @pytest.mark.asyncio
    async def test_done_called_on_stop(self):
        """Callback should be called when subagent is stopped."""
        cb = MockEventCallback()
        mgr = _make_manager(event_callback=cb)

        # Make the LLM call hang so we can stop it
        async def slow_chat(**kwargs):
            await asyncio.sleep(10)
            return FakeLLMResponse("Done")

        mgr.provider.chat = slow_chat

        await mgr.spawn("test task", session_key="parent:1")
        await asyncio.sleep(0.1)

        # Get task_id from spawned callback
        task_id = cb.spawned_calls[0]["task_id"]
        await mgr.stop_subagent(task_id, "parent:1")
        await asyncio.sleep(0.3)

        assert len(cb.done_calls) == 1
        _, status, _ = cb.done_calls[0]
        assert status == "stopped"

    @pytest.mark.asyncio
    async def test_done_called_on_queued_stop(self):
        """Callback should be called when a queued task is stopped."""
        cb = MockEventCallback()
        mgr = _make_manager(event_callback=cb, max_concurrency=1)

        # First task hangs
        async def slow_chat(**kwargs):
            await asyncio.sleep(10)
            return FakeLLMResponse("Done")

        mgr.provider.chat = slow_chat

        await mgr.spawn("task 1", session_key="parent:1")
        await mgr.spawn("task 2", session_key="parent:1")  # queued
        await asyncio.sleep(0.1)

        # Stop the queued task
        queued_task_id = cb.spawned_calls[1]["task_id"]
        await mgr.stop_subagent(queued_task_id, "parent:1")

        assert any(
            status == "stopped" and tid == queued_task_id
            for tid, status, _ in cb.done_calls
        )

        # Clean up
        first_task_id = cb.spawned_calls[0]["task_id"]
        await mgr.stop_subagent(first_task_id, "parent:1")
        await asyncio.sleep(0.3)


# ── Tests: AgentLoop on_iteration callback ───────────────────────────────────


class TestAgentLoopOnIteration:
    """Test the on_iteration callback in AgentLoop._run_agent_loop."""

    @pytest.mark.asyncio
    async def test_on_iteration_called(self):
        """on_iteration should be called at the start of each iteration."""
        from nanobot.agent.loop import AgentLoop

        calls = []

        def on_iter(iteration, max_iterations, last_tool):
            calls.append((iteration, max_iterations, last_tool))

        provider = AsyncMock()
        provider.get_default_model.return_value = "test-model"
        provider.chat = AsyncMock(return_value=FakeLLMResponse("Final answer"))
        bus = MagicMock()
        workspace = Path("/tmp/test-workspace")

        loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=workspace,
            max_iterations=10,
            on_iteration=on_iter,
        )

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
        ]
        result, tools_used, _ = await loop._run_agent_loop(messages)

        # Should have been called once (1 iteration before final answer)
        assert len(calls) == 1
        assert calls[0] == (1, 10, None)  # iteration=1, max=10, no last tool

    @pytest.mark.asyncio
    async def test_on_iteration_with_tool_calls(self):
        """on_iteration should report last_tool from previous iteration."""
        from nanobot.agent.loop import AgentLoop

        calls = []

        def on_iter(iteration, max_iterations, last_tool):
            calls.append((iteration, max_iterations, last_tool))

        provider = AsyncMock()
        provider.get_default_model.return_value = "test-model"

        tool_call = FakeToolCall(id="tc_1", name="exec")
        provider.chat = AsyncMock(side_effect=[
            FakeLLMResponse(content="", tool_calls=[tool_call]),
            FakeLLMResponse(content="Final answer"),
        ])
        bus = MagicMock()
        workspace = Path("/tmp/test-workspace")

        loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=workspace,
            max_iterations=10,
            on_iteration=on_iter,
        )

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
        ]
        result, tools_used, _ = await loop._run_agent_loop(messages)

        assert len(calls) == 2
        assert calls[0] == (1, 10, None)  # first iteration, no prior tool
        assert calls[1] == (2, 10, "exec")  # second iteration, last tool was exec

    @pytest.mark.asyncio
    async def test_on_iteration_error_swallowed(self):
        """Errors in on_iteration should not break the agent loop."""
        from nanobot.agent.loop import AgentLoop

        def bad_callback(iteration, max_iterations, last_tool):
            raise RuntimeError("callback error")

        provider = AsyncMock()
        provider.get_default_model.return_value = "test-model"
        provider.chat = AsyncMock(return_value=FakeLLMResponse("Final answer"))
        bus = MagicMock()
        workspace = Path("/tmp/test-workspace")

        loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=workspace,
            max_iterations=10,
            on_iteration=bad_callback,
        )

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
        ]
        # Should not raise
        result, tools_used, _ = await loop._run_agent_loop(messages)
        assert result == "Final answer"

    def test_on_iteration_default_none(self):
        """AgentLoop default has no on_iteration callback."""
        from nanobot.agent.loop import AgentLoop

        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        bus = MagicMock()
        workspace = Path("/tmp/test-workspace")

        loop = AgentLoop(bus=bus, provider=provider, workspace=workspace)
        assert loop._on_iteration is None
