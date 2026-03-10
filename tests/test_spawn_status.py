"""Tests for spawn status query capability (§38).

Tests cover:
- SubagentMeta new fields: created_at, finished_at, current_iteration, last_tool_name
- get_status(): single subagent detail query
- list_subagents(): list all subagents for a session
- Ownership check for status queries
- Status parameter in SpawnTool schema and routing
- Mutual exclusion: status + follow_up, status + stop
- Field updates during _run_subagent execution
- Resume resets §38 fields
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


# ═══════════════════════════════════════════════════════════════════════
# 1. SubagentMeta new fields
# ═══════════════════════════════════════════════════════════════════════


class TestSubagentMetaFields:
    """Test §38 new fields on SubagentMeta."""

    def test_default_values(self):
        """New fields have correct defaults."""
        meta = SubagentMeta(
            task_id="abc123",
            subagent_session_key="subagent:test_abc123",
            parent_session_key="web:123",
            label="test task",
            origin={"channel": "web", "chat_id": "123"},
        )
        assert meta.created_at == ""
        assert meta.finished_at is None
        assert meta.current_iteration == 0
        assert meta.last_tool_name is None

    def test_custom_values(self):
        """New fields can be set at construction."""
        now = datetime.now().isoformat()
        meta = SubagentMeta(
            task_id="abc123",
            subagent_session_key="subagent:test_abc123",
            parent_session_key="web:123",
            label="test task",
            origin={"channel": "web", "chat_id": "123"},
            created_at=now,
            finished_at=now,
            current_iteration=5,
            last_tool_name="exec",
        )
        assert meta.created_at == now
        assert meta.finished_at == now
        assert meta.current_iteration == 5
        assert meta.last_tool_name == "exec"


# ═══════════════════════════════════════════════════════════════════════
# 2. get_status()
# ═══════════════════════════════════════════════════════════════════════


class TestGetStatus:
    """Test SubagentManager.get_status()."""

    def _setup_meta(self, mgr, **overrides):
        """Helper to create and register a SubagentMeta."""
        defaults = dict(
            task_id="aabbccdd",
            subagent_session_key="subagent:web_123_aabbccdd",
            parent_session_key="web:123",
            label="test-label",
            origin={"channel": "web", "chat_id": "123"},
            status="running",
            max_iterations=30,
            created_at="2026-03-10T12:00:00",
            current_iteration=5,
            last_tool_name="exec",
        )
        defaults.update(overrides)
        meta = SubagentMeta(**defaults)
        mgr._task_meta[meta.task_id] = meta
        return meta

    def test_running_subagent(self):
        """Query a running subagent shows all fields."""
        mgr = _make_manager()
        self._setup_meta(mgr)
        result = mgr.get_status("aabbccdd", "web:123")
        assert "test-label" in result
        assert "aabbccdd" in result
        assert "running" in result
        assert "5/30" in result
        assert "2026-03-10T12:00:00" in result
        assert "exec" in result
        # finished_at should NOT appear for running
        assert "finished_at" not in result

    def test_completed_subagent_with_finished_at(self):
        """Completed subagent shows finished_at."""
        mgr = _make_manager()
        self._setup_meta(
            mgr, status="completed",
            finished_at="2026-03-10T12:05:00",
        )
        result = mgr.get_status("aabbccdd", "web:123")
        assert "completed" in result
        assert "2026-03-10T12:05:00" in result

    def test_no_last_tool(self):
        """Subagent with no tool calls omits last_tool."""
        mgr = _make_manager()
        self._setup_meta(mgr, last_tool_name=None, current_iteration=0)
        result = mgr.get_status("aabbccdd", "web:123")
        assert "last_tool" not in result

    def test_ownership_wrong_session(self):
        """Querying another session's subagent raises ValueError."""
        mgr = _make_manager()
        self._setup_meta(mgr)
        with pytest.raises(ValueError, match="does not belong"):
            mgr.get_status("aabbccdd", "web:999")

    def test_ownership_unknown_task_id(self):
        """Querying unknown task_id raises ValueError."""
        mgr = _make_manager()
        with pytest.raises(ValueError, match="Unknown subagent"):
            mgr.get_status("nonexist", "web:123")


# ═══════════════════════════════════════════════════════════════════════
# 3. list_subagents()
# ═══════════════════════════════════════════════════════════════════════


class TestListSubagents:
    """Test SubagentManager.list_subagents()."""

    def test_no_subagents(self):
        """Empty session returns message."""
        mgr = _make_manager()
        result = mgr.list_subagents("web:123")
        assert "No subagents found" in result

    def test_single_subagent(self):
        """Single subagent shows in table."""
        mgr = _make_manager()
        mgr._task_meta["aabb"] = SubagentMeta(
            task_id="aabb",
            subagent_session_key="subagent:web_123_aabb",
            parent_session_key="web:123",
            label="task-one",
            origin={"channel": "web", "chat_id": "123"},
            created_at="2026-03-10T12:00:00",
            current_iteration=3,
            max_iterations=30,
            last_tool_name="read_file",
        )
        result = mgr.list_subagents("web:123")
        assert "1 total" in result
        assert "aabb" in result
        assert "task-one" in result
        assert "read_file" in result

    def test_multiple_subagents_sorted(self):
        """Multiple subagents sorted by created_at (most recent first)."""
        mgr = _make_manager()
        mgr._task_meta["older"] = SubagentMeta(
            task_id="older",
            subagent_session_key="subagent:web_123_older",
            parent_session_key="web:123",
            label="old-task",
            origin={"channel": "web", "chat_id": "123"},
            created_at="2026-03-10T11:00:00",
        )
        mgr._task_meta["newer"] = SubagentMeta(
            task_id="newer",
            subagent_session_key="subagent:web_123_newer",
            parent_session_key="web:123",
            label="new-task",
            origin={"channel": "web", "chat_id": "123"},
            created_at="2026-03-10T12:00:00",
        )
        result = mgr.list_subagents("web:123")
        assert "2 total" in result
        # newer should appear before older
        newer_pos = result.index("newer")
        older_pos = result.index("older")
        assert newer_pos < older_pos

    def test_filters_by_session(self):
        """Only shows subagents belonging to the querying session."""
        mgr = _make_manager()
        mgr._task_meta["mine"] = SubagentMeta(
            task_id="mine",
            subagent_session_key="subagent:web_123_mine",
            parent_session_key="web:123",
            label="my-task",
            origin={"channel": "web", "chat_id": "123"},
            created_at="2026-03-10T12:00:00",
        )
        mgr._task_meta["theirs"] = SubagentMeta(
            task_id="theirs",
            subagent_session_key="subagent:web_999_theirs",
            parent_session_key="web:999",
            label="their-task",
            origin={"channel": "web", "chat_id": "999"},
            created_at="2026-03-10T12:00:00",
        )
        result = mgr.list_subagents("web:123")
        assert "1 total" in result
        assert "mine" in result
        assert "theirs" not in result

    def test_no_last_tool_shows_dash(self):
        """Subagent without last_tool shows '-' in table."""
        mgr = _make_manager()
        mgr._task_meta["aabb"] = SubagentMeta(
            task_id="aabb",
            subagent_session_key="subagent:web_123_aabb",
            parent_session_key="web:123",
            label="task-one",
            origin={"channel": "web", "chat_id": "123"},
            created_at="2026-03-10T12:00:00",
            last_tool_name=None,
        )
        result = mgr.list_subagents("web:123")
        assert "| - |" in result

    def test_long_label_truncated(self):
        """Labels longer than 30 chars are truncated."""
        mgr = _make_manager()
        long_label = "a" * 50
        mgr._task_meta["aabb"] = SubagentMeta(
            task_id="aabb",
            subagent_session_key="subagent:web_123_aabb",
            parent_session_key="web:123",
            label=long_label,
            origin={"channel": "web", "chat_id": "123"},
            created_at="2026-03-10T12:00:00",
        )
        result = mgr.list_subagents("web:123")
        assert long_label not in result
        assert "aaa..." in result


# ═══════════════════════════════════════════════════════════════════════
# 4. Field updates during _run_subagent
# ═══════════════════════════════════════════════════════════════════════


class TestFieldUpdates:
    """Test that §38 fields are updated during subagent execution."""

    @pytest.mark.asyncio
    async def test_created_at_set_on_spawn(self):
        """spawn() sets created_at on SubagentMeta."""
        mgr = _make_manager()
        # Make provider return a simple response (no tool calls)
        mgr.provider.chat.return_value = FakeLLMResponse(content="Done")

        before = datetime.now().isoformat()
        result = await mgr.spawn(
            task="test task",
            origin_channel="web",
            origin_chat_id="123",
            session_key="web:123",
        )
        after = datetime.now().isoformat()

        task_id = result.split("id: ")[1].split(")")[0]
        meta = mgr._task_meta[task_id]
        assert meta.created_at >= before
        assert meta.created_at <= after

    @pytest.mark.asyncio
    async def test_iteration_and_last_tool_updated(self):
        """current_iteration and last_tool_name updated during execution."""
        mgr = _make_manager()

        # First call: tool call, second call: final response
        tool_response = FakeLLMResponse(
            content="",
            tool_calls=[FakeToolCall(id="tc_1", name="exec", arguments={"command": "ls"})],
        )
        final_response = FakeLLMResponse(content="All done")
        mgr.provider.chat = AsyncMock(side_effect=[tool_response, final_response])

        result = await mgr.spawn(
            task="test task",
            origin_channel="web",
            origin_chat_id="123",
            session_key="web:123",
            persist=False,
        )
        task_id = result.split("id: ")[1].split(")")[0]
        await _wait_tasks(mgr)

        meta = mgr._task_meta[task_id]
        assert meta.current_iteration == 2  # 2 LLM calls
        assert meta.last_tool_name == "exec"

    @pytest.mark.asyncio
    async def test_finished_at_set_on_completion(self):
        """finished_at is set when subagent completes."""
        mgr = _make_manager()
        mgr.provider.chat.return_value = FakeLLMResponse(content="Done")

        result = await mgr.spawn(
            task="test task",
            origin_channel="web",
            origin_chat_id="123",
            session_key="web:123",
            persist=False,
        )
        task_id = result.split("id: ")[1].split(")")[0]
        await _wait_tasks(mgr)

        meta = mgr._task_meta[task_id]
        assert meta.status == "completed"
        assert meta.finished_at is not None

    @pytest.mark.asyncio
    async def test_finished_at_set_on_failure(self):
        """finished_at is set when subagent fails."""
        mgr = _make_manager()
        mgr.provider.chat = AsyncMock(side_effect=RuntimeError("LLM error"))

        result = await mgr.spawn(
            task="test task",
            origin_channel="web",
            origin_chat_id="123",
            session_key="web:123",
            persist=False,
        )
        task_id = result.split("id: ")[1].split(")")[0]
        await _wait_tasks(mgr)

        meta = mgr._task_meta[task_id]
        assert meta.status == "failed"
        assert meta.finished_at is not None

    @pytest.mark.asyncio
    async def test_finished_at_set_on_max_iterations(self):
        """finished_at is set when subagent hits max_iterations."""
        mgr = _make_manager()
        # Always return tool calls to exhaust iterations
        tool_response = FakeLLMResponse(
            content="",
            tool_calls=[FakeToolCall(id="tc_1", name="read_file")],
        )
        mgr.provider.chat.return_value = tool_response

        result = await mgr.spawn(
            task="test task",
            origin_channel="web",
            origin_chat_id="123",
            session_key="web:123",
            max_iterations=2,
            persist=False,
        )
        task_id = result.split("id: ")[1].split(")")[0]
        await _wait_tasks(mgr)

        meta = mgr._task_meta[task_id]
        assert meta.status == "max_iterations"
        assert meta.finished_at is not None
        assert meta.current_iteration == 2

    @pytest.mark.asyncio
    async def test_finished_at_set_on_stop(self):
        """finished_at is set when subagent is stopped."""
        mgr = _make_manager()

        # Slow tool call to keep subagent running
        async def slow_chat(*args, **kwargs):
            await asyncio.sleep(10)
            return FakeLLMResponse(content="Done")

        mgr.provider.chat = slow_chat

        result = await mgr.spawn(
            task="test task",
            origin_channel="web",
            origin_chat_id="123",
            session_key="web:123",
            persist=False,
        )
        task_id = result.split("id: ")[1].split(")")[0]
        await asyncio.sleep(0.1)

        await mgr.stop_subagent(task_id=task_id, parent_session_key="web:123")

        meta = mgr._task_meta[task_id]
        assert meta.status == "stopped"
        assert meta.finished_at is not None


# ═══════════════════════════════════════════════════════════════════════
# 5. Resume resets §38 fields
# ═══════════════════════════════════════════════════════════════════════


class TestResumeResets:
    """Test that follow_up resume resets §38 tracking fields."""

    @pytest.mark.asyncio
    async def test_resume_resets_fields(self):
        """follow_up resume resets finished_at, current_iteration, last_tool_name."""
        mgr = _make_manager()
        session_mgr = MagicMock()
        session_mgr.get_or_create.return_value = MagicMock()
        session_mgr.get_history.return_value = [
            {"role": "user", "content": "original task"},
            {"role": "assistant", "content": "Done"},
        ]
        mgr.session_manager = session_mgr

        # Set up a completed subagent with §38 fields populated
        meta = SubagentMeta(
            task_id="aabbccdd",
            subagent_session_key="subagent:web_123_aabbccdd",
            parent_session_key="web:123",
            label="test",
            origin={"channel": "web", "chat_id": "123"},
            status="completed",
            persist=True,
            created_at="2026-03-10T12:00:00",
            finished_at="2026-03-10T12:05:00",
            current_iteration=10,
            last_tool_name="exec",
        )
        mgr._task_meta["aabbccdd"] = meta

        # Mock provider for resume
        mgr.provider.chat.return_value = FakeLLMResponse(content="Resumed")

        await mgr.follow_up(
            task_id="aabbccdd",
            message="continue",
            parent_session_key="web:123",
        )

        # After resume, fields should be reset
        assert meta.status == "running"
        assert meta.finished_at is None
        assert meta.current_iteration == 0
        assert meta.last_tool_name is None
        # created_at should be preserved
        assert meta.created_at == "2026-03-10T12:00:00"


# ═══════════════════════════════════════════════════════════════════════
# 6. SpawnTool status parameter
# ═══════════════════════════════════════════════════════════════════════


class TestSpawnToolStatus:
    """Test SpawnTool status parameter schema and routing."""

    def test_status_in_schema(self):
        """status parameter exists in tool schema."""
        mgr = _make_manager()
        tool = SpawnTool(manager=mgr)
        params = tool.parameters
        assert "status" in params["properties"]
        assert params["properties"]["status"]["type"] == "string"

    def test_status_in_description(self):
        """Tool description mentions status."""
        mgr = _make_manager()
        tool = SpawnTool(manager=mgr)
        assert "Status" in tool.description

    @pytest.mark.asyncio
    async def test_route_status_list(self):
        """status='list' routes to list_subagents."""
        mgr = _make_manager()
        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "123", "web:123")

        result = await tool.execute(task="", status="list")
        assert "No subagents found" in result

    @pytest.mark.asyncio
    async def test_route_status_task_id(self):
        """status='<task_id>' routes to get_status."""
        mgr = _make_manager()
        mgr._task_meta["aabb"] = SubagentMeta(
            task_id="aabb",
            subagent_session_key="subagent:web_123_aabb",
            parent_session_key="web:123",
            label="test",
            origin={"channel": "web", "chat_id": "123"},
            created_at="2026-03-10T12:00:00",
        )
        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "123", "web:123")

        result = await tool.execute(task="", status="aabb")
        assert "test" in result
        assert "aabb" in result

    @pytest.mark.asyncio
    async def test_route_status_unknown_id(self):
        """status with unknown task_id returns error."""
        mgr = _make_manager()
        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "123", "web:123")

        result = await tool.execute(task="", status="nonexist")
        assert "Error" in result
        assert "Unknown" in result

    @pytest.mark.asyncio
    async def test_route_status_wrong_session(self):
        """status query for another session's subagent returns error."""
        mgr = _make_manager()
        mgr._task_meta["aabb"] = SubagentMeta(
            task_id="aabb",
            subagent_session_key="subagent:web_999_aabb",
            parent_session_key="web:999",
            label="test",
            origin={"channel": "web", "chat_id": "999"},
            created_at="2026-03-10T12:00:00",
        )
        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "123", "web:123")

        result = await tool.execute(task="", status="aabb")
        assert "Error" in result
        assert "does not belong" in result

    @pytest.mark.asyncio
    async def test_mutual_exclusion_status_follow_up(self):
        """status + follow_up is rejected."""
        mgr = _make_manager()
        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "123", "web:123")

        result = await tool.execute(task="msg", status="list", follow_up="aabb")
        assert "Error" in result
        assert "mutually exclusive" in result

    @pytest.mark.asyncio
    async def test_mutual_exclusion_status_stop(self):
        """status + stop is rejected."""
        mgr = _make_manager()
        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "123", "web:123")

        result = await tool.execute(task="msg", status="list", stop="aabb")
        assert "Error" in result
        assert "mutually exclusive" in result

    @pytest.mark.asyncio
    async def test_mutual_exclusion_all_three(self):
        """status + follow_up + stop is rejected."""
        mgr = _make_manager()
        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "123", "web:123")

        result = await tool.execute(task="msg", status="list", follow_up="a", stop="b")
        assert "Error" in result
        assert "mutually exclusive" in result

    @pytest.mark.asyncio
    async def test_normal_spawn_still_works(self):
        """Without status/follow_up/stop, spawn works normally."""
        mgr = _make_manager()
        mgr.provider.chat.return_value = FakeLLMResponse(content="Done")
        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "123", "web:123")

        result = await tool.execute(task="do something")
        assert "started" in result
