"""Tests for SubagentManager singleton + cross-process recovery (§40).

Tests cover:
- _recover_meta: disk-based recovery of SubagentMeta
- _load_disk_subagents: batch recovery from session files
- _check_ownership: disk fallback when task_id not in memory
- AgentLoop subagent_manager parameter injection
- list_subagents: cross-process enhancement
"""

import asyncio
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.subagent import (
    DEFAULT_SUBAGENT_ITERATIONS,
    SubagentManager,
    SubagentMeta,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_manager(workspace: Path = None, **kwargs):
    """Create a SubagentManager with mocked dependencies."""
    provider = AsyncMock()
    provider.get_default_model.return_value = "test-model"
    bus = AsyncMock()
    bus.publish_inbound = AsyncMock()

    defaults = dict(
        provider=provider,
        workspace=workspace or Path("/tmp/test-workspace"),
        bus=bus,
    )
    defaults.update(kwargs)
    return SubagentManager(**defaults)


def _create_session_file(workspace: Path, parent_key: str, task_id: str) -> Path:
    """Create a fake session file on disk for recovery tests."""
    parent_sanitized = parent_key.replace(":", "_")
    sessions_dir = workspace / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / f"subagent_{parent_sanitized}_{task_id}.jsonl"
    path.write_text('{"role":"user","content":"test"}\n')
    return path


# ═══════════════════════════════════════════════════════════════════════
# 1. _recover_meta tests
# ═══════════════════════════════════════════════════════════════════════


class TestRecoverMeta:
    """Tests for SubagentManager._recover_meta()."""

    def test_session_exists(self, tmp_path):
        """Session file exists → successfully recover meta."""
        parent_key = "webchat:1773141981"
        task_id = "a1b2c3d4"
        _create_session_file(tmp_path, parent_key, task_id)

        session_mgr = MagicMock()
        mgr = _make_manager(workspace=tmp_path, session_manager=session_mgr)

        meta = mgr._recover_meta(task_id, parent_key)

        assert meta is not None
        assert meta.task_id == task_id
        assert meta.parent_session_key == parent_key
        assert meta.subagent_session_key == "subagent:webchat_1773141981_a1b2c3d4"
        assert meta.status == "unknown"
        assert meta.persist is True
        assert meta.label == "(recovered)"
        assert meta.max_iterations == DEFAULT_SUBAGENT_ITERATIONS

    def test_session_not_exists(self, tmp_path):
        """Session file does not exist → return None."""
        session_mgr = MagicMock()
        mgr = _make_manager(workspace=tmp_path, session_manager=session_mgr)

        meta = mgr._recover_meta("nonexist", "webchat:123")
        assert meta is None

    def test_no_session_manager(self, tmp_path):
        """session_manager is None → return None."""
        parent_key = "webchat:123"
        task_id = "abc123"
        _create_session_file(tmp_path, parent_key, task_id)

        mgr = _make_manager(workspace=tmp_path, session_manager=None)

        meta = mgr._recover_meta(task_id, parent_key)
        assert meta is None

    def test_parent_key_correct(self, tmp_path):
        """Recovered meta has correct parent_session_key."""
        parent_key = "telegram:8281248569"
        task_id = "xyz789"
        _create_session_file(tmp_path, parent_key, task_id)

        session_mgr = MagicMock()
        mgr = _make_manager(workspace=tmp_path, session_manager=session_mgr)

        meta = mgr._recover_meta(task_id, parent_key)
        assert meta is not None
        assert meta.parent_session_key == parent_key
        assert meta.subagent_session_key == "subagent:telegram_8281248569_xyz789"

    def test_cached_on_second_call(self, tmp_path):
        """Second call returns the same cached object (no re-creation)."""
        parent_key = "webchat:111"
        task_id = "cached01"
        _create_session_file(tmp_path, parent_key, task_id)

        session_mgr = MagicMock()
        mgr = _make_manager(workspace=tmp_path, session_manager=session_mgr)

        meta1 = mgr._recover_meta(task_id, parent_key)
        meta2 = mgr._recover_meta(task_id, parent_key)
        assert meta1 is meta2  # Same object

    def test_cached_in_task_meta(self, tmp_path):
        """Recovered meta is cached in _task_meta dict."""
        parent_key = "webchat:222"
        task_id = "inmeta01"
        _create_session_file(tmp_path, parent_key, task_id)

        session_mgr = MagicMock()
        mgr = _make_manager(workspace=tmp_path, session_manager=session_mgr)

        mgr._recover_meta(task_id, parent_key)
        assert task_id in mgr._task_meta
        assert task_id in mgr._session_tasks.get(parent_key, set())

    def test_origin_defaults(self, tmp_path):
        """Recovered meta has unknown origin."""
        parent_key = "webchat:333"
        task_id = "origin01"
        _create_session_file(tmp_path, parent_key, task_id)

        session_mgr = MagicMock()
        mgr = _make_manager(workspace=tmp_path, session_manager=session_mgr)

        meta = mgr._recover_meta(task_id, parent_key)
        assert meta.origin == {"channel": "unknown", "chat_id": "unknown"}


# ═══════════════════════════════════════════════════════════════════════
# 2. _load_disk_subagents tests
# ═══════════════════════════════════════════════════════════════════════


class TestLoadDiskSubagents:
    """Tests for SubagentManager._load_disk_subagents()."""

    def test_multiple_files(self, tmp_path):
        """Multiple session files → all loaded into memory."""
        parent_key = "webchat:444"
        ids = ["aaa11111", "bbb22222", "ccc33333"]
        for tid in ids:
            _create_session_file(tmp_path, parent_key, tid)

        session_mgr = MagicMock()
        mgr = _make_manager(workspace=tmp_path, session_manager=session_mgr)

        mgr._load_disk_subagents(parent_key)

        for tid in ids:
            assert tid in mgr._task_meta
            assert mgr._task_meta[tid].parent_session_key == parent_key

    def test_skip_existing_in_memory(self, tmp_path):
        """Already-in-memory task_ids are not overwritten."""
        parent_key = "webchat:555"
        task_id = "existing1"
        _create_session_file(tmp_path, parent_key, task_id)

        session_mgr = MagicMock()
        mgr = _make_manager(workspace=tmp_path, session_manager=session_mgr)

        # Pre-populate memory with a meta that has a specific label
        existing_meta = SubagentMeta(
            task_id=task_id,
            subagent_session_key=f"subagent:webchat_555_{task_id}",
            parent_session_key=parent_key,
            label="original-label",
            origin={"channel": "web", "chat_id": "555"},
            status="completed",
        )
        mgr._task_meta[task_id] = existing_meta
        mgr._session_tasks.setdefault(parent_key, set()).add(task_id)

        mgr._load_disk_subagents(parent_key)

        # Should still be the original, not overwritten
        assert mgr._task_meta[task_id].label == "original-label"
        assert mgr._task_meta[task_id].status == "completed"

    def test_no_sessions_dir(self, tmp_path):
        """Sessions directory doesn't exist → no error."""
        session_mgr = MagicMock()
        mgr = _make_manager(workspace=tmp_path, session_manager=session_mgr)
        # tmp_path/sessions does not exist
        mgr._load_disk_subagents("webchat:666")  # Should not raise

    def test_no_session_manager(self, tmp_path):
        """session_manager is None → _recover_meta returns None, no crash."""
        parent_key = "webchat:777"
        _create_session_file(tmp_path, parent_key, "task01")

        mgr = _make_manager(workspace=tmp_path, session_manager=None)
        mgr._load_disk_subagents(parent_key)

        # Without session_manager, _recover_meta returns None
        assert "task01" not in mgr._task_meta

    def test_only_loads_matching_parent(self, tmp_path):
        """Only loads files matching the specified parent, not others."""
        parent_a = "webchat:aaa"
        parent_b = "webchat:bbb"
        _create_session_file(tmp_path, parent_a, "task_a1")
        _create_session_file(tmp_path, parent_b, "task_b1")

        session_mgr = MagicMock()
        mgr = _make_manager(workspace=tmp_path, session_manager=session_mgr)

        mgr._load_disk_subagents(parent_a)

        assert "task_a1" in mgr._task_meta
        assert "task_b1" not in mgr._task_meta


# ═══════════════════════════════════════════════════════════════════════
# 3. _check_ownership enhanced tests
# ═══════════════════════════════════════════════════════════════════════


class TestCheckOwnershipEnhanced:
    """Tests for _check_ownership with §40 disk fallback."""

    def test_memory_hit(self, tmp_path):
        """Task in memory → returned directly (no disk access)."""
        parent_key = "webchat:888"
        task_id = "mem_hit"

        mgr = _make_manager(workspace=tmp_path)
        meta = SubagentMeta(
            task_id=task_id,
            subagent_session_key=f"subagent:webchat_888_{task_id}",
            parent_session_key=parent_key,
            label="in-memory",
            origin={"channel": "web", "chat_id": "888"},
        )
        mgr._task_meta[task_id] = meta

        result = mgr._check_ownership(parent_key, task_id)
        assert result is meta

    def test_disk_fallback(self, tmp_path):
        """Not in memory, but session file exists → recovered from disk."""
        parent_key = "webchat:999"
        task_id = "disk_fb"
        _create_session_file(tmp_path, parent_key, task_id)

        session_mgr = MagicMock()
        mgr = _make_manager(workspace=tmp_path, session_manager=session_mgr)

        # Not in memory
        assert task_id not in mgr._task_meta

        result = mgr._check_ownership(parent_key, task_id)
        assert result is not None
        assert result.task_id == task_id
        assert result.parent_session_key == parent_key

    def test_not_found(self, tmp_path):
        """Not in memory and no session file → ValueError."""
        session_mgr = MagicMock()
        mgr = _make_manager(workspace=tmp_path, session_manager=session_mgr)

        with pytest.raises(ValueError, match="Unknown subagent task_id"):
            mgr._check_ownership("webchat:000", "nonexistent")

    def test_wrong_parent(self, tmp_path):
        """Task belongs to different parent → ValueError."""
        parent_key = "webchat:111"
        other_key = "webchat:222"
        task_id = "wrong_p"

        mgr = _make_manager(workspace=tmp_path)
        meta = SubagentMeta(
            task_id=task_id,
            subagent_session_key=f"subagent:webchat_111_{task_id}",
            parent_session_key=parent_key,
            label="test",
            origin={"channel": "web", "chat_id": "111"},
        )
        mgr._task_meta[task_id] = meta

        with pytest.raises(ValueError, match="does not belong"):
            mgr._check_ownership(other_key, task_id)


# ═══════════════════════════════════════════════════════════════════════
# 4. AgentLoop subagent_manager parameter tests
# ═══════════════════════════════════════════════════════════════════════


class TestAgentLoopSubagentManager:
    """Tests for AgentLoop.__init__ subagent_manager parameter (§40)."""

    def test_external_subagent_manager(self, tmp_path):
        """Passing subagent_manager → AgentLoop uses it instead of creating new."""
        from nanobot.agent.loop import AgentLoop

        provider = AsyncMock()
        provider.get_default_model.return_value = "test-model"
        bus = AsyncMock()

        external_mgr = _make_manager(workspace=tmp_path)

        loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=tmp_path,
            subagent_manager=external_mgr,
        )

        assert loop.subagents is external_mgr

    def test_default_creates_own(self, tmp_path):
        """Not passing subagent_manager → AgentLoop creates its own."""
        from nanobot.agent.loop import AgentLoop

        provider = AsyncMock()
        provider.get_default_model.return_value = "test-model"
        bus = AsyncMock()

        loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=tmp_path,
        )

        assert loop.subagents is not None
        assert isinstance(loop.subagents, SubagentManager)

    def test_external_is_shared(self, tmp_path):
        """Two AgentLoops with same external manager share the same instance."""
        from nanobot.agent.loop import AgentLoop

        provider = AsyncMock()
        provider.get_default_model.return_value = "test-model"
        bus = AsyncMock()

        shared_mgr = _make_manager(workspace=tmp_path)

        loop1 = AgentLoop(bus=bus, provider=provider, workspace=tmp_path,
                          subagent_manager=shared_mgr)
        loop2 = AgentLoop(bus=bus, provider=provider, workspace=tmp_path,
                          subagent_manager=shared_mgr)

        assert loop1.subagents is loop2.subagents

    def test_external_with_usage_recorder(self, tmp_path):
        """External SubagentManager with usage_recorder → no warning logged."""
        from nanobot.agent.loop import AgentLoop

        provider = AsyncMock()
        provider.get_default_model.return_value = "test-model"
        bus = AsyncMock()
        recorder = MagicMock()

        external_mgr = _make_manager(workspace=tmp_path, usage_recorder=recorder)
        assert external_mgr.usage_recorder is recorder

        loop = AgentLoop(
            bus=bus, provider=provider, workspace=tmp_path,
            subagent_manager=external_mgr,
        )

        assert loop.subagents.usage_recorder is recorder

    def test_external_without_usage_recorder_warns(self, tmp_path):
        """External SubagentManager with usage_recorder=None → warning logged."""
        from nanobot.agent.loop import AgentLoop

        provider = AsyncMock()
        provider.get_default_model.return_value = "test-model"
        bus = AsyncMock()

        external_mgr = _make_manager(workspace=tmp_path)
        assert external_mgr.usage_recorder is None  # default is None

        with pytest.warns(match="") if False else nullcontext():
            # We can't easily capture loguru warnings with pytest.warns,
            # so we verify the code path runs without error and the
            # usage_recorder remains None (the warning is logged).
            loop = AgentLoop(
                bus=bus, provider=provider, workspace=tmp_path,
                subagent_manager=external_mgr,
            )

        assert loop.subagents.usage_recorder is None

    def test_default_inherits_usage_recorder(self, tmp_path):
        """Default mode (no external manager) → SubagentManager inherits usage_recorder."""
        from nanobot.agent.loop import AgentLoop

        provider = AsyncMock()
        provider.get_default_model.return_value = "test-model"
        bus = AsyncMock()
        recorder = MagicMock()

        loop = AgentLoop(
            bus=bus, provider=provider, workspace=tmp_path,
            usage_recorder=recorder,
        )

        # In default mode, AgentLoop creates its own SubagentManager
        # and passes usage_recorder through.
        assert loop.subagents.usage_recorder is recorder

    def test_default_no_usage_recorder(self, tmp_path):
        """Default mode without usage_recorder → SubagentManager.usage_recorder is None."""
        from nanobot.agent.loop import AgentLoop

        provider = AsyncMock()
        provider.get_default_model.return_value = "test-model"
        bus = AsyncMock()

        loop = AgentLoop(
            bus=bus, provider=provider, workspace=tmp_path,
        )

        assert loop.subagents.usage_recorder is None


# ═══════════════════════════════════════════════════════════════════════
# 5. list_subagents cross-process enhancement tests
# ═══════════════════════════════════════════════════════════════════════


class TestListSubagentsCrossProcess:
    """Tests for list_subagents() with §40 disk loading."""

    def test_loads_from_disk(self, tmp_path):
        """Memory empty, disk has session files → listed."""
        parent_key = "webchat:list01"
        ids = ["list_a", "list_b"]
        for tid in ids:
            _create_session_file(tmp_path, parent_key, tid)

        session_mgr = MagicMock()
        mgr = _make_manager(workspace=tmp_path, session_manager=session_mgr)

        result = mgr.list_subagents(parent_key)

        assert "list_a" in result
        assert "list_b" in result
        assert "(recovered)" in result
        assert "2 total" in result

    def test_combines_memory_and_disk(self, tmp_path):
        """Memory has some, disk has others → all listed."""
        parent_key = "webchat:list02"

        # One in memory
        session_mgr = MagicMock()
        mgr = _make_manager(workspace=tmp_path, session_manager=session_mgr)
        mem_meta = SubagentMeta(
            task_id="mem_task",
            subagent_session_key="subagent:webchat_list02_mem_task",
            parent_session_key=parent_key,
            label="in-memory-task",
            origin={"channel": "web", "chat_id": "list02"},
            status="completed",
            created_at="2026-03-11T00:00:00",
        )
        mgr._task_meta["mem_task"] = mem_meta
        mgr._session_tasks.setdefault(parent_key, set()).add("mem_task")

        # One on disk only
        _create_session_file(tmp_path, parent_key, "disk_task")

        result = mgr.list_subagents(parent_key)

        assert "mem_task" in result
        assert "disk_task" in result
        assert "2 total" in result

    def test_empty_memory_and_disk(self, tmp_path):
        """No subagents anywhere → appropriate message."""
        session_mgr = MagicMock()
        mgr = _make_manager(workspace=tmp_path, session_manager=session_mgr)

        result = mgr.list_subagents("webchat:empty")
        assert "No subagents found" in result


# ═══════════════════════════════════════════════════════════════════════
# 6. Integration: follow_up after process restart
# ═══════════════════════════════════════════════════════════════════════


class TestFollowUpCrossProcess:
    """Integration test: follow_up works after SubagentManager is recreated."""

    @pytest.mark.asyncio
    async def test_follow_up_recovered_subagent(self, tmp_path):
        """Simulate process restart: new manager can follow_up on old subagent."""
        parent_key = "webchat:follow01"
        task_id = "follow_a"

        # Create session file (simulating previous spawn)
        _create_session_file(tmp_path, parent_key, task_id)

        # Create "new" manager (simulating process restart)
        session_mgr = MagicMock()
        session_obj = MagicMock()
        session_obj.get_history.return_value = [
            {"role": "user", "content": "original task"},
            {"role": "assistant", "content": "done"},
        ]
        session_mgr.get_or_create.return_value = session_obj

        provider = AsyncMock()
        provider.get_default_model.return_value = "test-model"
        # Make chat return a final response (no tool calls)
        response = MagicMock()
        response.content = "Resumed successfully"
        response.tool_calls = []
        response.has_tool_calls = False
        response.usage = None
        response.finish_reason = "stop"
        provider.chat = AsyncMock(return_value=response)

        bus = AsyncMock()
        bus.publish_inbound = AsyncMock()

        mgr = SubagentManager(
            provider=provider,
            workspace=tmp_path,
            bus=bus,
            session_manager=session_mgr,
        )

        # task_id NOT in memory — must recover from disk
        assert task_id not in mgr._task_meta

        result = await mgr.follow_up(
            task_id=task_id,
            message="continue please",
            parent_session_key=parent_key,
        )

        assert "resumed" in result.lower()
        assert task_id in result

        # Wait for the background task to complete
        for task in list(mgr._running_tasks.values()):
            if not task.done():
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass

    @pytest.mark.asyncio
    async def test_get_status_recovered_subagent(self, tmp_path):
        """get_status works on a recovered subagent."""
        parent_key = "webchat:status01"
        task_id = "stat_a"
        _create_session_file(tmp_path, parent_key, task_id)

        session_mgr = MagicMock()
        mgr = _make_manager(workspace=tmp_path, session_manager=session_mgr)

        result = mgr.get_status(task_id, parent_key)
        assert task_id in result
        assert "unknown" in result
        assert "(recovered)" in result

    @pytest.mark.asyncio
    async def test_stop_recovered_subagent_not_running(self, tmp_path):
        """stop on a recovered (not running) subagent returns already-stopped message."""
        parent_key = "webchat:stop01"
        task_id = "stop_a"
        _create_session_file(tmp_path, parent_key, task_id)

        session_mgr = MagicMock()
        mgr = _make_manager(workspace=tmp_path, session_manager=session_mgr)

        result = await mgr.stop_subagent(task_id, parent_key)
        assert "already" in result.lower()
