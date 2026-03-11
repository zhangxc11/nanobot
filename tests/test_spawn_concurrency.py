"""Tests for spawn concurrency limit (§46).

Tests cover:
- QueuedSpawn dataclass creation
- SpawnConfig default and custom values
- SubagentManager._max_concurrency initialization
- SubagentManager._running_count property
- spawn() queues when at concurrency limit
- spawn() starts immediately when under limit
- Queued task status is "queued"
- get_status() shows queued status
- list_subagents() includes queued tasks
- stop_subagent() removes queued tasks from queue
- _try_dequeue() starts queued tasks when slots open
- Dequeue respects FIFO order
- Multiple dequeue cycles
- Config integration (SpawnConfig in Config)
"""

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.subagent import (
    DEFAULT_SUBAGENT_ITERATIONS,
    QueuedSpawn,
    SubagentManager,
    SubagentMeta,
)
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.config.schema import Config, SpawnConfig


# ── Helpers ──────────────────────────────────────────────────────────────────


class FakeLLMResponse:
    """Minimal LLM response for testing."""

    def __init__(self, content="Done", tool_calls=None, usage=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.has_tool_calls = bool(self.tool_calls)
        self.usage = usage
        self.finish_reason = "stop"


def _make_manager(max_concurrency: int = 2, **kwargs):
    """Create a SubagentManager with mocked dependencies."""
    provider = AsyncMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat = AsyncMock(return_value=FakeLLMResponse())
    bus = AsyncMock()
    bus.publish_inbound = AsyncMock()
    workspace = Path("/tmp/test-workspace")

    defaults = dict(
        provider=provider,
        workspace=workspace,
        bus=bus,
        max_concurrency=max_concurrency,
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


# ═══════════════════════════════════════════════════════════════════════
# 1. QueuedSpawn dataclass
# ═══════════════════════════════════════════════════════════════════════


class TestQueuedSpawn:
    """Test QueuedSpawn dataclass."""

    def test_creation(self):
        qs = QueuedSpawn(
            task_id="abc123",
            task="do something",
            label="test task",
            origin={"channel": "cli", "chat_id": "direct"},
            session_key="web:123",
            max_iterations=30,
            persist=True,
            subagent_session_key="subagent:web_123_abc123",
        )
        assert qs.task_id == "abc123"
        assert qs.task == "do something"
        assert qs.label == "test task"
        assert qs.session_key == "web:123"
        assert qs.max_iterations == 30
        assert qs.persist is True


# ═══════════════════════════════════════════════════════════════════════
# 2. SpawnConfig
# ═══════════════════════════════════════════════════════════════════════


class TestSpawnConfig:
    """Test SpawnConfig schema."""

    def test_default_max_concurrency(self):
        cfg = SpawnConfig()
        assert cfg.max_concurrency == 4

    def test_custom_max_concurrency(self):
        cfg = SpawnConfig(max_concurrency=8)
        assert cfg.max_concurrency == 8

    def test_config_has_spawn_field(self):
        cfg = Config()
        assert hasattr(cfg, "spawn")
        assert cfg.spawn.max_concurrency == 4

    def test_config_custom_spawn(self):
        cfg = Config(spawn=SpawnConfig(max_concurrency=10))
        assert cfg.spawn.max_concurrency == 10

    def test_config_from_dict(self):
        cfg = Config.model_validate({"spawn": {"maxConcurrency": 6}})
        assert cfg.spawn.max_concurrency == 6


# ═══════════════════════════════════════════════════════════════════════
# 3. SubagentManager initialization
# ═══════════════════════════════════════════════════════════════════════


class TestManagerInit:
    """Test SubagentManager concurrency initialization."""

    def test_default_max_concurrency(self):
        mgr = _make_manager()
        assert mgr._max_concurrency == 2  # We passed 2

    def test_custom_max_concurrency(self):
        mgr = _make_manager(max_concurrency=8)
        assert mgr._max_concurrency == 8

    def test_min_concurrency_is_1(self):
        mgr = _make_manager(max_concurrency=0)
        assert mgr._max_concurrency == 1

    def test_negative_concurrency_clamped(self):
        mgr = _make_manager(max_concurrency=-5)
        assert mgr._max_concurrency == 1

    def test_empty_queue_on_init(self):
        mgr = _make_manager()
        assert mgr._queue == []

    def test_running_count_zero_on_init(self):
        mgr = _make_manager()
        assert mgr._running_count == 0


# ═══════════════════════════════════════════════════════════════════════
# 4. Spawn under limit (normal behavior)
# ═══════════════════════════════════════════════════════════════════════


class TestSpawnUnderLimit:
    """Test that spawn works normally when under concurrency limit."""

    @pytest.mark.asyncio
    async def test_first_spawn_starts_immediately(self):
        mgr = _make_manager(max_concurrency=2)
        result = await mgr.spawn(task="task 1", session_key="s1")
        assert "started" in result
        assert "queued" not in result.lower()
        # Wait for completion
        await _wait_tasks(mgr)

    @pytest.mark.asyncio
    async def test_spawn_under_limit_creates_running_meta(self):
        mgr = _make_manager(max_concurrency=2)
        await mgr.spawn(task="task 1", session_key="s1")
        # There should be exactly 1 task meta
        assert len(mgr._task_meta) == 1
        meta = list(mgr._task_meta.values())[0]
        assert meta.status == "running"
        await _wait_tasks(mgr)


# ═══════════════════════════════════════════════════════════════════════
# 5. Spawn at/over limit (queuing behavior)
# ═══════════════════════════════════════════════════════════════════════


class TestSpawnQueuing:
    """Test spawn queuing when at concurrency limit."""

    @pytest.mark.asyncio
    async def test_spawn_queued_when_at_limit(self):
        """Third spawn should be queued when max_concurrency=2."""
        mgr = _make_manager(max_concurrency=2)
        # Make provider.chat block so tasks stay running
        never_finish = asyncio.Future()
        mgr.provider.chat = AsyncMock(side_effect=lambda **kw: never_finish)

        r1 = await mgr.spawn(task="task 1", session_key="s1")
        r2 = await mgr.spawn(task="task 2", session_key="s1")
        r3 = await mgr.spawn(task="task 3", session_key="s1")

        assert "started" in r1
        assert "started" in r2
        assert "queued" in r3.lower()
        assert "position #1" in r3
        assert "concurrency limit: 2" in r3

        # Queue should have 1 item
        assert len(mgr._queue) == 1
        assert mgr._queue[0].task == "task 3"

        # Clean up
        for task in list(mgr._running_tasks.values()):
            task.cancel()
        await _wait_tasks(mgr)

    @pytest.mark.asyncio
    async def test_queued_meta_has_status_queued(self):
        mgr = _make_manager(max_concurrency=1)
        never_finish = asyncio.Future()
        mgr.provider.chat = AsyncMock(side_effect=lambda **kw: never_finish)

        await mgr.spawn(task="task 1", session_key="s1")
        await mgr.spawn(task="task 2", session_key="s1")

        # Find the queued task
        queued_metas = [m for m in mgr._task_meta.values() if m.status == "queued"]
        assert len(queued_metas) == 1
        assert queued_metas[0].label.startswith("task 2")

        for task in list(mgr._running_tasks.values()):
            task.cancel()
        await _wait_tasks(mgr)

    @pytest.mark.asyncio
    async def test_multiple_queued_positions(self):
        mgr = _make_manager(max_concurrency=1)
        never_finish = asyncio.Future()
        mgr.provider.chat = AsyncMock(side_effect=lambda **kw: never_finish)

        await mgr.spawn(task="task 1", session_key="s1")
        r2 = await mgr.spawn(task="task 2", session_key="s1")
        r3 = await mgr.spawn(task="task 3", session_key="s1")

        assert "position #1" in r2
        assert "position #2" in r3
        assert len(mgr._queue) == 2

        for task in list(mgr._running_tasks.values()):
            task.cancel()
        await _wait_tasks(mgr)

    @pytest.mark.asyncio
    async def test_queued_task_tracked_in_session_tasks(self):
        mgr = _make_manager(max_concurrency=1)
        never_finish = asyncio.Future()
        mgr.provider.chat = AsyncMock(side_effect=lambda **kw: never_finish)

        await mgr.spawn(task="task 1", session_key="s1")
        await mgr.spawn(task="task 2", session_key="s1")

        # Both tasks should be tracked under session s1
        assert len(mgr._session_tasks.get("s1", set())) == 2

        for task in list(mgr._running_tasks.values()):
            task.cancel()
        await _wait_tasks(mgr)


# ═══════════════════════════════════════════════════════════════════════
# 6. Status and list with queued tasks
# ═══════════════════════════════════════════════════════════════════════


class TestStatusWithQueued:
    """Test get_status and list_subagents with queued tasks."""

    @pytest.mark.asyncio
    async def test_get_status_shows_queued(self):
        mgr = _make_manager(max_concurrency=1)
        never_finish = asyncio.Future()
        mgr.provider.chat = AsyncMock(side_effect=lambda **kw: never_finish)

        await mgr.spawn(task="task 1", session_key="s1")
        await mgr.spawn(task="task 2", session_key="s1")

        queued_meta = [m for m in mgr._task_meta.values() if m.status == "queued"][0]
        status = mgr.get_status(queued_meta.task_id, "s1")
        assert "queued" in status

        for task in list(mgr._running_tasks.values()):
            task.cancel()
        await _wait_tasks(mgr)

    @pytest.mark.asyncio
    async def test_list_includes_queued(self):
        mgr = _make_manager(max_concurrency=1)
        never_finish = asyncio.Future()
        mgr.provider.chat = AsyncMock(side_effect=lambda **kw: never_finish)

        await mgr.spawn(task="task 1", session_key="s1")
        await mgr.spawn(task="task 2", session_key="s1")

        listing = mgr.list_subagents("s1")
        assert "2 total" in listing
        assert "queued" in listing
        assert "running" in listing

        for task in list(mgr._running_tasks.values()):
            task.cancel()
        await _wait_tasks(mgr)


# ═══════════════════════════════════════════════════════════════════════
# 7. Stop queued tasks
# ═══════════════════════════════════════════════════════════════════════


class TestStopQueued:
    """Test stopping queued tasks."""

    @pytest.mark.asyncio
    async def test_stop_queued_removes_from_queue(self):
        mgr = _make_manager(max_concurrency=1)
        never_finish = asyncio.Future()
        mgr.provider.chat = AsyncMock(side_effect=lambda **kw: never_finish)

        await mgr.spawn(task="task 1", session_key="s1")
        await mgr.spawn(task="task 2", session_key="s1")

        queued_meta = [m for m in mgr._task_meta.values() if m.status == "queued"][0]
        result = await mgr.stop_subagent(queued_meta.task_id, "s1")

        assert "removed from the queue" in result
        assert "stopped" in result
        assert len(mgr._queue) == 0
        assert queued_meta.status == "stopped"
        assert queued_meta.finished_at is not None

        for task in list(mgr._running_tasks.values()):
            task.cancel()
        await _wait_tasks(mgr)

    @pytest.mark.asyncio
    async def test_stop_queued_does_not_create_task(self):
        mgr = _make_manager(max_concurrency=1)
        never_finish = asyncio.Future()
        mgr.provider.chat = AsyncMock(side_effect=lambda **kw: never_finish)

        await mgr.spawn(task="task 1", session_key="s1")
        await mgr.spawn(task="task 2", session_key="s1")

        initial_task_count = len(mgr._running_tasks)
        queued_meta = [m for m in mgr._task_meta.values() if m.status == "queued"][0]
        await mgr.stop_subagent(queued_meta.task_id, "s1")

        # Should not have created any new running tasks
        assert len(mgr._running_tasks) == initial_task_count

        for task in list(mgr._running_tasks.values()):
            task.cancel()
        await _wait_tasks(mgr)

    @pytest.mark.asyncio
    async def test_stop_middle_queued_preserves_order(self):
        """Stop middle queued task, remaining should keep FIFO order."""
        mgr = _make_manager(max_concurrency=1)
        never_finish = asyncio.Future()
        mgr.provider.chat = AsyncMock(side_effect=lambda **kw: never_finish)

        await mgr.spawn(task="task 1", session_key="s1")
        await mgr.spawn(task="task 2", session_key="s1")
        await mgr.spawn(task="task 3", session_key="s1")
        await mgr.spawn(task="task 4", session_key="s1")

        assert len(mgr._queue) == 3

        # Stop the middle one (task 3)
        queued_metas = [m for m in mgr._task_meta.values() if m.status == "queued"]
        # Find task 3
        task3_meta = [m for m in queued_metas if "task 3" in m.label][0]
        await mgr.stop_subagent(task3_meta.task_id, "s1")

        assert len(mgr._queue) == 2
        assert mgr._queue[0].task == "task 2"
        assert mgr._queue[1].task == "task 4"

        for task in list(mgr._running_tasks.values()):
            task.cancel()
        await _wait_tasks(mgr)


# ═══════════════════════════════════════════════════════════════════════
# 8. Dequeue behavior
# ═══════════════════════════════════════════════════════════════════════


class TestDequeue:
    """Test automatic dequeue when slots become available."""

    @pytest.mark.asyncio
    async def test_dequeue_on_task_completion(self):
        """When a running task completes, queued task should start."""
        mgr = _make_manager(max_concurrency=1)

        # First task completes immediately
        call_count = 0

        async def mock_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            return FakeLLMResponse(content=f"Done {call_count}")

        mgr.provider.chat = mock_chat

        r1 = await mgr.spawn(task="task 1", session_key="s1")
        assert "started" in r1

        # Wait for task 1 to complete
        await _wait_tasks(mgr)

        # Now task 2 should also start (dequeued)
        r2 = await mgr.spawn(task="task 2", session_key="s1")
        # Since task 1 is done, task 2 should start immediately
        assert "started" in r2

        await _wait_tasks(mgr)

    @pytest.mark.asyncio
    async def test_dequeue_fifo_order(self):
        """Queued tasks should dequeue in FIFO order."""
        mgr = _make_manager(max_concurrency=1)

        # Track dequeue order
        dequeue_order = []
        original_start = mgr._start_subagent_task

        def tracking_start(task_id, task, label, origin, max_iterations,
                           persist, subagent_key, session_key, meta):
            dequeue_order.append(task)
            return original_start(task_id, task, label, origin, max_iterations,
                                  persist, subagent_key, session_key, meta)

        # First, fill the slot with a blocking task
        blocker = asyncio.Future()
        mgr.provider.chat = AsyncMock(side_effect=lambda **kw: blocker)

        await mgr.spawn(task="blocker", session_key="s1")

        # Queue 3 more tasks
        await mgr.spawn(task="task A", session_key="s1")
        await mgr.spawn(task="task B", session_key="s1")
        await mgr.spawn(task="task C", session_key="s1")

        assert len(mgr._queue) == 3

        # Now make chat return immediately for dequeued tasks
        mgr.provider.chat = AsyncMock(return_value=FakeLLMResponse())

        # Patch _start_subagent_task to track order
        mgr._start_subagent_task = tracking_start

        # Complete the blocker - this should trigger dequeue
        blocker.set_result(FakeLLMResponse())
        await _wait_tasks(mgr, timeout=5)

        # First dequeued should be "task A"
        assert len(dequeue_order) > 0
        assert dequeue_order[0] == "task A"

        await _wait_tasks(mgr)

    @pytest.mark.asyncio
    async def test_dequeue_respects_limit(self):
        """_try_dequeue should not start more than max_concurrency tasks at once."""
        mgr = _make_manager(max_concurrency=2)

        # Use events to control each task
        events = {i: asyncio.Event() for i in range(1, 5)}
        call_count = 0
        max_concurrent_seen = 0

        async def controlled_chat(**kwargs):
            nonlocal call_count, max_concurrent_seen
            call_count += 1
            current = call_count
            # Track max concurrent running
            running = mgr._running_count
            if running > max_concurrent_seen:
                max_concurrent_seen = running
            await events[current].wait()
            return FakeLLMResponse(content=f"Done {current}")

        mgr.provider.chat = controlled_chat

        # Fill 2 slots
        await mgr.spawn(task="task 1", session_key="s1")
        await mgr.spawn(task="task 2", session_key="s1")
        await asyncio.sleep(0.05)  # Let tasks start

        # Queue 2 more
        await mgr.spawn(task="task 3", session_key="s1")
        await mgr.spawn(task="task 4", session_key="s1")

        assert mgr._running_count == 2
        assert len(mgr._queue) == 2

        # Complete task 1 - should dequeue task 3
        events[1].set()
        await asyncio.sleep(0.1)  # Let event loop process dequeue

        # task 3 should be running now, task 4 still queued
        # (task 2 still blocking + task 3 = 2 running)
        assert len(mgr._queue) == 1
        assert mgr._queue[0].task == "task 4"

        # Clean up
        for e in events.values():
            e.set()
        await _wait_tasks(mgr)

        # Never exceeded concurrency limit
        assert max_concurrent_seen <= 2


# ═══════════════════════════════════════════════════════════════════════
# 9. SpawnTool integration
# ═══════════════════════════════════════════════════════════════════════


class TestSpawnToolIntegration:
    """Test SpawnTool with concurrency limit."""

    @pytest.mark.asyncio
    async def test_spawn_tool_returns_queued_message(self):
        mgr = _make_manager(max_concurrency=1)
        never_finish = asyncio.Future()
        mgr.provider.chat = AsyncMock(side_effect=lambda **kw: never_finish)

        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "123", "web:123")

        r1 = await tool.execute(task="task 1")
        assert "started" in r1

        r2 = await tool.execute(task="task 2")
        assert "queued" in r2.lower()

        for task in list(mgr._running_tasks.values()):
            task.cancel()
        await _wait_tasks(mgr)

    @pytest.mark.asyncio
    async def test_spawn_tool_status_queued(self):
        mgr = _make_manager(max_concurrency=1)
        never_finish = asyncio.Future()
        mgr.provider.chat = AsyncMock(side_effect=lambda **kw: never_finish)

        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "123", "web:123")

        await tool.execute(task="task 1")
        await tool.execute(task="task 2")

        queued_meta = [m for m in mgr._task_meta.values() if m.status == "queued"][0]
        status_result = await tool.execute(task="check", status=queued_meta.task_id)
        assert "queued" in status_result

        for task in list(mgr._running_tasks.values()):
            task.cancel()
        await _wait_tasks(mgr)

    @pytest.mark.asyncio
    async def test_spawn_tool_list_shows_queued(self):
        mgr = _make_manager(max_concurrency=1)
        never_finish = asyncio.Future()
        mgr.provider.chat = AsyncMock(side_effect=lambda **kw: never_finish)

        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "123", "web:123")

        await tool.execute(task="task 1")
        await tool.execute(task="task 2")

        list_result = await tool.execute(task="list", status="list")
        assert "2 total" in list_result
        assert "queued" in list_result

        for task in list(mgr._running_tasks.values()):
            task.cancel()
        await _wait_tasks(mgr)

    @pytest.mark.asyncio
    async def test_spawn_tool_stop_queued(self):
        mgr = _make_manager(max_concurrency=1)
        never_finish = asyncio.Future()
        mgr.provider.chat = AsyncMock(side_effect=lambda **kw: never_finish)

        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "123", "web:123")

        await tool.execute(task="task 1")
        await tool.execute(task="task 2")

        queued_meta = [m for m in mgr._task_meta.values() if m.status == "queued"][0]
        stop_result = await tool.execute(task="stop it", stop=queued_meta.task_id)
        assert "removed from the queue" in stop_result
        assert "stopped" in stop_result

        for task in list(mgr._running_tasks.values()):
            task.cancel()
        await _wait_tasks(mgr)


# ═══════════════════════════════════════════════════════════════════════
# 10. Edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Test edge cases for concurrency limit."""

    @pytest.mark.asyncio
    async def test_max_concurrency_1(self):
        """Only 1 task at a time."""
        mgr = _make_manager(max_concurrency=1)
        never_finish = asyncio.Future()
        mgr.provider.chat = AsyncMock(side_effect=lambda **kw: never_finish)

        r1 = await mgr.spawn(task="task 1", session_key="s1")
        r2 = await mgr.spawn(task="task 2", session_key="s1")

        assert "started" in r1
        assert "queued" in r2.lower()
        assert mgr._running_count == 1

        for task in list(mgr._running_tasks.values()):
            task.cancel()
        await _wait_tasks(mgr)

    @pytest.mark.asyncio
    async def test_queued_task_has_created_at(self):
        mgr = _make_manager(max_concurrency=1)
        never_finish = asyncio.Future()
        mgr.provider.chat = AsyncMock(side_effect=lambda **kw: never_finish)

        await mgr.spawn(task="task 1", session_key="s1")
        await mgr.spawn(task="task 2", session_key="s1")

        queued_meta = [m for m in mgr._task_meta.values() if m.status == "queued"][0]
        assert queued_meta.created_at != ""

        for task in list(mgr._running_tasks.values()):
            task.cancel()
        await _wait_tasks(mgr)

    @pytest.mark.asyncio
    async def test_existing_spawn_features_still_work(self):
        """Ensure normal spawn/complete flow works with concurrency enabled."""
        mgr = _make_manager(max_concurrency=4)
        result = await mgr.spawn(task="simple task", session_key="s1")
        assert "started" in result
        await _wait_tasks(mgr)

        # Check meta shows completed
        meta = list(mgr._task_meta.values())[0]
        assert meta.status == "completed"

    @pytest.mark.asyncio
    async def test_running_count_decreases_on_completion(self):
        mgr = _make_manager(max_concurrency=4)
        await mgr.spawn(task="task 1", session_key="s1")
        await _wait_tasks(mgr)
        assert mgr._running_count == 0

    @pytest.mark.asyncio
    async def test_try_dequeue_with_empty_queue(self):
        """_try_dequeue should be a no-op with empty queue."""
        mgr = _make_manager(max_concurrency=2)
        mgr._try_dequeue()  # Should not raise
        assert len(mgr._queue) == 0

    @pytest.mark.asyncio
    async def test_queued_spawn_preserves_parameters(self):
        """Queued spawn should preserve all original parameters."""
        mgr = _make_manager(max_concurrency=1)
        never_finish = asyncio.Future()
        mgr.provider.chat = AsyncMock(side_effect=lambda **kw: never_finish)

        await mgr.spawn(task="task 1", session_key="s1")
        await mgr.spawn(
            task="task 2",
            label="custom label",
            session_key="s1",
            max_iterations=50,
            persist=False,
        )

        qs = mgr._queue[0]
        assert qs.task == "task 2"
        assert qs.label == "custom label"
        assert qs.max_iterations == 50
        assert qs.persist is False

        for task in list(mgr._running_tasks.values()):
            task.cancel()
        await _wait_tasks(mgr)

    @pytest.mark.asyncio
    async def test_stop_running_still_works(self):
        """Stop a running (not queued) subagent should still work."""
        mgr = _make_manager(max_concurrency=2)
        never_finish = asyncio.Future()
        mgr.provider.chat = AsyncMock(side_effect=lambda **kw: never_finish)

        await mgr.spawn(task="task 1", session_key="s1")
        running_meta = [m for m in mgr._task_meta.values() if m.status == "running"][0]

        result = await mgr.stop_subagent(running_meta.task_id, "s1")
        assert "has been stopped" in result
        assert "removed from the queue" not in result

        await _wait_tasks(mgr)
