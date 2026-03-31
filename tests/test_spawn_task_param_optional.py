"""T-20260331-007: Verify spawn tool does NOT require `task` param for status/stop/follow_up.

This test validates that:
1. Schema: `required` is empty (no parameter is mandatory at schema level)
2. Runtime: status action works without `task` parameter
3. Runtime: stop action works without `task` parameter
4. Runtime: follow_up action DOES require `task` (the message to send)
5. Runtime: new spawn DOES require `task` (the task description)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.subagent import SubagentMeta


def _make_manager():
    """Create a minimal mock SubagentManager."""
    mgr = MagicMock()
    mgr._task_meta = {}
    mgr._session_tasks = {}
    mgr._running_tasks = {}

    def list_subagents(session_key):
        tasks = mgr._session_tasks.get(session_key, [])
        if not tasks:
            return "No subagents found for this session."
        return f"Found {len(tasks)} subagent(s)."

    def get_status(task_id, session_key):
        if task_id not in mgr._task_meta:
            raise ValueError(f"Unknown task_id: {task_id}")
        meta = mgr._task_meta[task_id]
        if meta.subagent_session_key.split(":")[0] != "subagent":
            raise ValueError("Not your subagent")
        return f"Status for {task_id}: running"

    mgr.list_subagents = list_subagents
    mgr.get_status = get_status
    mgr.stop_subagent = AsyncMock(return_value="Subagent stopped.")
    mgr.follow_up = AsyncMock(return_value="Follow-up sent.")
    return mgr


class TestSchemaNoRequiredTask:
    """Verify the JSON schema does not list `task` as required."""

    def test_required_is_empty(self):
        """Schema `required` should be an empty list — no params are mandatory."""
        tool = SpawnTool(manager=MagicMock())
        params = tool.parameters
        assert params.get("required") == [], (
            f"Expected empty required list, got: {params.get('required')}"
        )

    def test_task_not_in_required(self):
        """Explicitly confirm `task` is NOT in required."""
        tool = SpawnTool(manager=MagicMock())
        params = tool.parameters
        assert "task" not in params.get("required", [])

    def test_status_not_in_required(self):
        """Confirm `status` is NOT in required."""
        tool = SpawnTool(manager=MagicMock())
        params = tool.parameters
        assert "status" not in params.get("required", [])

    def test_stop_not_in_required(self):
        """Confirm `stop` is NOT in required."""
        tool = SpawnTool(manager=MagicMock())
        params = tool.parameters
        assert "stop" not in params.get("required", [])

    def test_follow_up_not_in_required(self):
        """Confirm `follow_up` is NOT in required."""
        tool = SpawnTool(manager=MagicMock())
        params = tool.parameters
        assert "follow_up" not in params.get("required", [])


class TestStatusWithoutTask:
    """Verify status action works without `task` parameter."""

    @pytest.mark.asyncio
    async def test_status_list_no_task(self):
        """spawn(status='list') without task should succeed."""
        mgr = _make_manager()
        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "test", "web:test")

        result = await tool.execute(status="list")
        assert "No subagents found" in result
        # Should NOT contain "Error"
        assert "Error" not in result

    @pytest.mark.asyncio
    async def test_status_list_explicit_empty_task(self):
        """spawn(task='', status='list') should succeed."""
        mgr = _make_manager()
        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "test", "web:test")

        result = await tool.execute(task="", status="list")
        assert "No subagents found" in result
        assert "Error" not in result

    @pytest.mark.asyncio
    async def test_status_task_id_no_task(self):
        """spawn(status='<task_id>') without task should succeed."""
        mgr = _make_manager()
        mgr._task_meta["abc123"] = SubagentMeta(
            task_id="abc123",
            subagent_session_key="subagent:web_test_abc123",
            parent_session_key="web:test",
            label="test-sub",
            origin={"channel": "web", "chat_id": "test"},
        )
        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "test", "web:test")

        result = await tool.execute(status="abc123")
        assert "Status for abc123" in result
        assert "Error" not in result


class TestStopWithoutTask:
    """Verify stop action works without `task` parameter."""

    @pytest.mark.asyncio
    async def test_stop_no_task(self):
        """spawn(stop='<task_id>') without task should succeed."""
        mgr = _make_manager()
        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "test", "web:test")

        result = await tool.execute(stop="abc123")
        assert result == "Subagent stopped."
        mgr.stop_subagent.assert_called_once_with(
            task_id="abc123",
            parent_session_key="web:test",
            reason="",  # empty task becomes empty reason
        )

    @pytest.mark.asyncio
    async def test_stop_with_reason(self):
        """spawn(stop='<task_id>', task='reason') should pass reason."""
        mgr = _make_manager()
        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "test", "web:test")

        result = await tool.execute(stop="abc123", task="no longer needed")
        assert result == "Subagent stopped."
        mgr.stop_subagent.assert_called_once_with(
            task_id="abc123",
            parent_session_key="web:test",
            reason="no longer needed",
        )


class TestFollowUpRequiresTask:
    """Verify follow_up DOES require `task` (the message to send)."""

    @pytest.mark.asyncio
    async def test_follow_up_no_task_errors(self):
        """spawn(follow_up='<id>') without task should error."""
        mgr = _make_manager()
        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "test", "web:test")

        result = await tool.execute(follow_up="abc123")
        assert "Error" in result
        assert "task" in result.lower()

    @pytest.mark.asyncio
    async def test_follow_up_empty_task_errors(self):
        """spawn(follow_up='<id>', task='') should error."""
        mgr = _make_manager()
        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "test", "web:test")

        result = await tool.execute(follow_up="abc123", task="")
        assert "Error" in result
        assert "task" in result.lower()

    @pytest.mark.asyncio
    async def test_follow_up_with_task_succeeds(self):
        """spawn(follow_up='<id>', task='hello') should succeed."""
        mgr = _make_manager()
        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "test", "web:test")

        result = await tool.execute(follow_up="abc123", task="hello subagent")
        assert result == "Follow-up sent."


class TestNewSpawnRequiresTask:
    """Verify new spawn DOES require `task` at runtime."""

    @pytest.mark.asyncio
    async def test_spawn_no_task_errors(self):
        """spawn() without task should error."""
        mgr = _make_manager()
        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "test", "web:test")

        result = await tool.execute()
        assert "Error" in result
        assert "task" in result.lower()

    @pytest.mark.asyncio
    async def test_spawn_empty_task_errors(self):
        """spawn(task='') should error."""
        mgr = _make_manager()
        tool = SpawnTool(manager=mgr)
        tool.set_context("web", "test", "web:test")

        result = await tool.execute(task="")
        assert "Error" in result
        assert "task" in result.lower()


class TestMutualExclusion:
    """Verify mutual exclusion between status/stop/follow_up."""

    @pytest.mark.asyncio
    async def test_status_and_stop_exclusive(self):
        """Cannot use status and stop together."""
        tool = SpawnTool(manager=MagicMock())
        tool.set_context("web", "test", "web:test")

        result = await tool.execute(status="list", stop="abc")
        assert "Error" in result
        assert "mutually exclusive" in result.lower()

    @pytest.mark.asyncio
    async def test_status_and_follow_up_exclusive(self):
        """Cannot use status and follow_up together."""
        tool = SpawnTool(manager=MagicMock())
        tool.set_context("web", "test", "web:test")

        result = await tool.execute(status="list", follow_up="abc", task="msg")
        assert "Error" in result
        assert "mutually exclusive" in result.lower()

    @pytest.mark.asyncio
    async def test_stop_and_follow_up_exclusive(self):
        """Cannot use stop and follow_up together."""
        tool = SpawnTool(manager=MagicMock())
        tool.set_context("web", "test", "web:test")

        result = await tool.execute(stop="abc", follow_up="def", task="msg")
        assert "Error" in result
        assert "mutually exclusive" in result.lower()
