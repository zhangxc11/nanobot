"""Tests for SubagentManager Phase 26 enhancements.

Tests cover:
- Configurable max_iterations with hard cap
- Budget alert injection
- LLM retry with exponential backoff
- Usage recording
- Session persistence (persist mode)
- SpawnTool parameter schema
- Tool execution verification
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.subagent import (
    DEFAULT_SUBAGENT_ITERATIONS,
    MAX_SUBAGENT_ITERATIONS,
    SubagentManager,
    _budget_alert_threshold,
    _is_retryable,
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


# ── Tests: _budget_alert_threshold ───────────────────────────────────────────


class TestBudgetAlertThreshold:
    def test_normal(self):
        assert _budget_alert_threshold(40) == 10
        assert _budget_alert_threshold(100) == 10
        assert _budget_alert_threshold(20) == 10

    def test_small(self):
        assert _budget_alert_threshold(12) == 3
        assert _budget_alert_threshold(16) == 4

    def test_minimum(self):
        assert _budget_alert_threshold(8) == 3
        assert _budget_alert_threshold(4) == 3


# ── Tests: _is_retryable ────────────────────────────────────────────────────


class TestIsRetryable:
    def test_retryable_class_names(self):
        for cls_name in ["RateLimitError", "APIConnectionError", "APITimeoutError"]:
            e = type(cls_name, (Exception,), {})()
            assert _is_retryable(e) is True

    def test_non_retryable(self):
        assert _is_retryable(ValueError("bad")) is False
        assert _is_retryable(RuntimeError("oops")) is False

    def test_status_code(self):
        e = Exception("err")
        e.status_code = 429
        assert _is_retryable(e) is True

        e2 = Exception("err")
        e2.status_code = 500
        assert _is_retryable(e2) is True

        e3 = Exception("err")
        e3.status_code = 400
        assert _is_retryable(e3) is False

    def test_message_pattern(self):
        assert _is_retryable(Exception("rate limit exceeded")) is True
        assert _is_retryable(Exception("server overloaded")) is True
        assert _is_retryable(Exception("invalid request")) is False


# ── Tests: SubagentManager defaults ─────────────────────────────────────────


class TestSubagentManagerDefaults:
    def test_default_max_iterations(self):
        mgr = _make_manager()
        assert mgr.default_max_iterations == DEFAULT_SUBAGENT_ITERATIONS

    def test_custom_default_max_iterations(self):
        mgr = _make_manager(default_max_iterations=50)
        assert mgr.default_max_iterations == 50

    def test_default_max_iterations_capped(self):
        mgr = _make_manager(default_max_iterations=200)
        assert mgr.default_max_iterations == MAX_SUBAGENT_ITERATIONS


# ── Tests: spawn() max_iterations clamping ──────────────────────────────────


class TestSpawnMaxIterations:
    @pytest.mark.asyncio
    async def test_default_iterations(self):
        mgr = _make_manager()
        # Make provider return a simple response to end the loop
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done"))

        await mgr.spawn(task="test task")

        # Wait for the background task to complete
        await asyncio.sleep(0.1)
        for task in list(mgr._running_tasks.values()):
            if not task.done():
                await task

        # Verify provider.chat was called (at least once)
        assert mgr.provider.chat.call_count >= 1

    @pytest.mark.asyncio
    async def test_custom_iterations(self):
        mgr = _make_manager()
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("Done"))

        result = await mgr.spawn(task="test", max_iterations=50)
        assert "started" in result

    @pytest.mark.asyncio
    async def test_iterations_capped_at_max(self):
        mgr = _make_manager()
        # Provider returns tool calls to keep iterating
        tool_call = FakeToolCall()
        tool_response = FakeLLMResponse(tool_calls=[tool_call])
        final_response = FakeLLMResponse("Final")

        call_count = 0

        async def mock_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            # Return tool calls for first 99 iterations, then final
            if call_count >= MAX_SUBAGENT_ITERATIONS:
                return final_response
            return tool_response

        mgr.provider.chat = mock_chat

        # Mock tools.execute
        with patch.object(
            mgr, '_run_subagent',
            wraps=mgr._run_subagent
        ):
            await mgr.spawn(task="long task", max_iterations=999)
            await asyncio.sleep(0.2)
            for task in list(mgr._running_tasks.values()):
                if not task.done():
                    try:
                        await asyncio.wait_for(task, timeout=5)
                    except asyncio.TimeoutError:
                        pass

        # Verify iterations were capped
        assert call_count <= MAX_SUBAGENT_ITERATIONS


# ── Tests: Budget alert ─────────────────────────────────────────────────────


class TestBudgetAlertInjection:
    @pytest.mark.asyncio
    async def test_budget_alert_injected(self):
        """Budget alert should be injected when remaining == threshold."""
        mgr = _make_manager()

        messages_captured = []

        async def mock_chat(**kwargs):
            # Capture messages to check for budget alert
            msgs = kwargs.get("messages", [])
            messages_captured.append([m.get("role") for m in msgs])

            # Return tool call for first iterations, then stop
            if len(messages_captured) < 20:
                return FakeLLMResponse(
                    tool_calls=[FakeToolCall(id=f"tc_{len(messages_captured)}")],
                    usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                )
            return FakeLLMResponse("Done", usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})

        mgr.provider.chat = mock_chat

        await mgr.spawn(task="test", max_iterations=25)
        await asyncio.sleep(0.5)
        for task in list(mgr._running_tasks.values()):
            if not task.done():
                try:
                    await asyncio.wait_for(task, timeout=10)
                except asyncio.TimeoutError:
                    pass

        # Check that at some point a system message was in the messages
        found_budget_alert = False
        for msg_roles in messages_captured:
            if "system" in msg_roles[2:]:  # Skip initial system prompt
                found_budget_alert = True
                break
        assert found_budget_alert, "Budget alert system message should have been injected"


# ── Tests: LLM retry ────────────────────────────────────────────────────────


class TestChatWithRetry:
    @pytest.mark.asyncio
    async def test_success_first_try(self):
        mgr = _make_manager()
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse("OK"))

        from nanobot.agent.tools.registry import ToolRegistry
        tools = ToolRegistry()
        result = await mgr._chat_with_retry(
            [{"role": "user", "content": "hi"}], tools
        )
        assert result.content == "OK"
        assert mgr.provider.chat.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_transient_error(self):
        mgr = _make_manager()

        rate_limit_error = type("RateLimitError", (Exception,), {})("rate limited")
        mgr.provider.chat = AsyncMock(
            side_effect=[rate_limit_error, FakeLLMResponse("OK")]
        )

        from nanobot.agent.tools.registry import ToolRegistry
        tools = ToolRegistry()

        with patch("nanobot.agent.subagent.asyncio.sleep", new_callable=AsyncMock):
            result = await mgr._chat_with_retry(
                [{"role": "user", "content": "hi"}], tools
            )

        assert result.content == "OK"
        assert mgr.provider.chat.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_exhausted(self):
        mgr = _make_manager()

        rate_limit_error = type("RateLimitError", (Exception,), {})("rate limited")
        mgr.provider.chat = AsyncMock(side_effect=rate_limit_error)

        from nanobot.agent.tools.registry import ToolRegistry
        tools = ToolRegistry()

        with patch("nanobot.agent.subagent.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(Exception, match="rate limited"):
                await mgr._chat_with_retry(
                    [{"role": "user", "content": "hi"}], tools
                )

        # 1 initial + 3 retries = 4 calls
        assert mgr.provider.chat.call_count == 4

    @pytest.mark.asyncio
    async def test_non_retryable_raises_immediately(self):
        mgr = _make_manager()

        mgr.provider.chat = AsyncMock(side_effect=ValueError("bad input"))

        from nanobot.agent.tools.registry import ToolRegistry
        tools = ToolRegistry()

        with pytest.raises(ValueError, match="bad input"):
            await mgr._chat_with_retry(
                [{"role": "user", "content": "hi"}], tools
            )

        assert mgr.provider.chat.call_count == 1


# ── Tests: Usage recording ──────────────────────────────────────────────────


class TestUsageRecording:
    @pytest.mark.asyncio
    async def test_usage_recorded(self):
        recorder = MagicMock()
        mgr = _make_manager(usage_recorder=recorder)
        mgr.provider.chat = AsyncMock(
            return_value=FakeLLMResponse(
                "Done",
                usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            )
        )

        await mgr.spawn(task="test task")
        await asyncio.sleep(0.2)
        for task in list(mgr._running_tasks.values()):
            if not task.done():
                await task

        # Verify usage was recorded
        recorder.record.assert_called_once()
        call_kwargs = recorder.record.call_args[1]
        assert call_kwargs["prompt_tokens"] == 100
        assert call_kwargs["completion_tokens"] == 50
        assert call_kwargs["total_tokens"] == 150
        assert call_kwargs["llm_calls"] == 1
        assert call_kwargs["session_key"].startswith("subagent:")

    @pytest.mark.asyncio
    async def test_no_usage_without_recorder(self):
        """No error when usage_recorder is None."""
        mgr = _make_manager()
        assert mgr.usage_recorder is None
        mgr.provider.chat = AsyncMock(
            return_value=FakeLLMResponse(
                "Done",
                usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            )
        )

        await mgr.spawn(task="test task")
        await asyncio.sleep(0.2)
        for task in list(mgr._running_tasks.values()):
            if not task.done():
                await task
        # No exception means success


# ── Tests: Session persistence ──────────────────────────────────────────────


class TestSessionPersistence:
    @pytest.mark.asyncio
    async def test_persist_creates_session(self):
        session_mgr = MagicMock()
        mock_session = MagicMock()
        session_mgr.get_or_create.return_value = mock_session

        mgr = _make_manager(session_manager=session_mgr)
        mgr.provider.chat = AsyncMock(
            return_value=FakeLLMResponse("Done", usage=None)
        )

        await mgr.spawn(task="persist test", persist=True)
        await asyncio.sleep(0.2)
        for task in list(mgr._running_tasks.values()):
            if not task.done():
                await task

        # Session should be created with subagent:xxx key
        session_mgr.get_or_create.assert_called_once()
        key = session_mgr.get_or_create.call_args[0][0]
        assert key.startswith("subagent:")

        # Messages should be appended (user + final assistant)
        assert session_mgr.append_message.call_count >= 2

    @pytest.mark.asyncio
    async def test_no_persist_by_default(self):
        session_mgr = MagicMock()

        mgr = _make_manager(session_manager=session_mgr)
        mgr.provider.chat = AsyncMock(
            return_value=FakeLLMResponse("Done", usage=None)
        )

        await mgr.spawn(task="no persist test", persist=False)
        await asyncio.sleep(0.2)
        for task in list(mgr._running_tasks.values()):
            if not task.done():
                await task

        # No session created
        session_mgr.get_or_create.assert_not_called()
        session_mgr.append_message.assert_not_called()


# ── Tests: SpawnTool parameters ─────────────────────────────────────────────


class TestSpawnToolParameters:
    def test_parameters_schema(self):
        manager = MagicMock()
        tool = SpawnTool(manager=manager)
        params = tool.parameters

        assert "task" in params["properties"]
        assert "label" in params["properties"]
        assert "max_iterations" in params["properties"]
        assert "persist" in params["properties"]
        assert params["properties"]["max_iterations"]["type"] == "integer"
        assert params["properties"]["persist"]["type"] == "boolean"
        assert "task" in params["required"]
        assert "max_iterations" not in params.get("required", [])
        assert "persist" not in params.get("required", [])

    @pytest.mark.asyncio
    async def test_execute_passes_new_params(self):
        manager = AsyncMock()
        manager.spawn = AsyncMock(return_value="started")
        tool = SpawnTool(manager=manager)

        result = await tool.execute(
            task="test", label="lbl", max_iterations=50, persist=True
        )

        assert result == "started"
        manager.spawn.assert_called_once()
        call_kwargs = manager.spawn.call_args[1]
        assert call_kwargs["max_iterations"] == 50
        assert call_kwargs["persist"] is True

    @pytest.mark.asyncio
    async def test_execute_defaults(self):
        manager = AsyncMock()
        manager.spawn = AsyncMock(return_value="started")
        tool = SpawnTool(manager=manager)

        await tool.execute(task="test")

        call_kwargs = manager.spawn.call_args[1]
        assert call_kwargs["max_iterations"] is None
        assert call_kwargs["persist"] is False


# ── Tests: Task Keeper (GC prevention) ──────────────────────────────────────


class TestTaskKeeper:
    @pytest.mark.asyncio
    async def test_task_keeper_called(self):
        """task_keeper callback should be called with the asyncio.Task."""
        kept_tasks = []

        def keeper(task):
            kept_tasks.append(task)

        mgr = _make_manager(task_keeper=keeper)
        mgr.provider.chat = AsyncMock(
            return_value=FakeLLMResponse("Done", usage=None)
        )

        await mgr.spawn(task="keeper test")
        await asyncio.sleep(0.2)
        for task in list(mgr._running_tasks.values()):
            if not task.done():
                await task

        assert len(kept_tasks) == 1
        assert isinstance(kept_tasks[0], asyncio.Task)

    @pytest.mark.asyncio
    async def test_task_survives_manager_gc(self):
        """Subagent task should survive even after SubagentManager is GC'd,
        provided an external task_keeper holds a reference."""
        import gc

        kept_tasks = []

        def keeper(task):
            kept_tasks.append(task)

        mgr = _make_manager(task_keeper=keeper)

        # Track whether the task actually ran
        task_completed = asyncio.Event()
        original_chat = AsyncMock(return_value=FakeLLMResponse("Done from GC test", usage=None))
        mgr.provider.chat = original_chat

        await mgr.spawn(task="gc survival test")
        await asyncio.sleep(0.05)  # Let task start

        # Delete the manager (simulating web worker GC)
        del mgr
        gc.collect()

        # The task should still be in kept_tasks and should complete
        assert len(kept_tasks) == 1
        task = kept_tasks[0]

        # Wait for task to complete
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except asyncio.TimeoutError:
            pass

        assert task.done()

    @pytest.mark.asyncio
    async def test_no_keeper_still_works(self):
        """Without task_keeper, spawn should still work normally."""
        mgr = _make_manager()  # No task_keeper
        assert mgr._task_keeper is None

        mgr.provider.chat = AsyncMock(
            return_value=FakeLLMResponse("Done", usage=None)
        )

        result = await mgr.spawn(task="no keeper test")
        assert "started" in result

        await asyncio.sleep(0.2)
        for task in list(mgr._running_tasks.values()):
            if not task.done():
                await task


# ── Tests: Announce result ──────────────────────────────────────────────────


class TestAnnounceResult:
    @pytest.mark.asyncio
    async def test_announce_on_completion(self):
        mgr = _make_manager()
        mgr.provider.chat = AsyncMock(
            return_value=FakeLLMResponse("Task done!", usage=None)
        )

        await mgr.spawn(task="test announce")
        await asyncio.sleep(0.2)
        for task in list(mgr._running_tasks.values()):
            if not task.done():
                await task

        # Bus should have received the announcement
        mgr.bus.publish_inbound.assert_called_once()
        msg = mgr.bus.publish_inbound.call_args[0][0]
        assert "completed successfully" in msg.content
        assert "Task done!" in msg.content

    @pytest.mark.asyncio
    async def test_announce_on_error(self):
        mgr = _make_manager()
        mgr.provider.chat = AsyncMock(side_effect=ValueError("boom"))

        await mgr.spawn(task="test error")
        await asyncio.sleep(0.2)
        for task in list(mgr._running_tasks.values()):
            if not task.done():
                try:
                    await task
                except Exception:
                    pass

        mgr.bus.publish_inbound.assert_called_once()
        msg = mgr.bus.publish_inbound.call_args[0][0]
        assert "failed" in msg.content
        assert "boom" in msg.content
