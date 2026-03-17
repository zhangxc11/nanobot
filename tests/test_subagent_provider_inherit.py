"""Tests for §56: Subagent provider inheritance from parent session.

Verifies that subagents use the parent session's per-session provider
instead of the global default when spawned via ProviderPool.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.subagent import SubagentManager
from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.providers.pool import ProviderPool


class FakeLLMResponse:
    """Minimal LLM response for testing."""
    def __init__(self, content="done", has_tool_calls=False):
        self.content = content
        self.has_tool_calls = has_tool_calls
        self.tool_calls = []
        self.finish_reason = "stop"
        self.usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def _make_pool(global_provider="anthropic", global_model="claude-sonnet-4-20250514"):
    """Create a ProviderPool with two mock providers."""
    prov_anthropic = AsyncMock(spec=LLMProvider)
    prov_anthropic.get_default_model.return_value = "claude-sonnet-4-20250514"
    prov_anthropic.chat = AsyncMock(return_value=FakeLLMResponse())
    prov_anthropic.provider_name = "anthropic"

    prov_proxy = AsyncMock(spec=LLMProvider)
    prov_proxy.get_default_model.return_value = "claude-sonnet-4-20250514"
    prov_proxy.chat = AsyncMock(return_value=FakeLLMResponse())
    prov_proxy.provider_name = "anthropic_proxy"

    pool = ProviderPool(
        providers={
            "anthropic": (prov_anthropic, "claude-sonnet-4-20250514"),
            "anthropic_proxy": (prov_proxy, "claude-sonnet-4-20250514"),
        },
        active_provider=global_provider,
        active_model=global_model,
    )
    return pool, prov_anthropic, prov_proxy


def _make_manager(pool, tmp_path):
    """Create a SubagentManager with the given pool."""
    bus = MagicMock()
    bus.publish_inbound = AsyncMock()
    return SubagentManager(
        provider=pool,
        workspace=tmp_path,
        bus=bus,
        model=pool.active_model,
        max_concurrency=4,
    )


class TestResolveProvider:
    """Test _resolve_provider() method."""

    def test_resolve_with_pool_and_session_override(self, tmp_path):
        """Per-session override should be resolved."""
        pool, prov_anthropic, prov_proxy = _make_pool()
        pool.switch_for_session("feishu:group_123", "anthropic_proxy")
        mgr = _make_manager(pool, tmp_path)

        provider, model = mgr._resolve_provider("feishu:group_123")
        assert provider is prov_proxy
        assert model == "claude-sonnet-4-20250514"

    def test_resolve_with_pool_no_override_uses_global(self, tmp_path):
        """No per-session override should fall back to global active."""
        pool, prov_anthropic, prov_proxy = _make_pool()
        mgr = _make_manager(pool, tmp_path)

        provider, model = mgr._resolve_provider("feishu:group_456")
        assert provider is prov_anthropic
        assert model == "claude-sonnet-4-20250514"

    def test_resolve_without_session_key_uses_self(self, tmp_path):
        """No session_key should fall back to self.provider/self.model."""
        pool, prov_anthropic, prov_proxy = _make_pool()
        mgr = _make_manager(pool, tmp_path)

        provider, model = mgr._resolve_provider(None)
        assert provider is pool  # Falls back to self.provider (the pool itself)
        assert model == pool.active_model

    def test_resolve_non_pool_provider_uses_self(self, tmp_path):
        """Non-ProviderPool provider should use self.provider/self.model."""
        plain_provider = AsyncMock(spec=LLMProvider)
        plain_provider.get_default_model.return_value = "gpt-4"
        bus = MagicMock()
        bus.publish_inbound = AsyncMock()
        mgr = SubagentManager(
            provider=plain_provider,
            workspace=tmp_path,
            bus=bus,
            model="gpt-4",
        )

        provider, model = mgr._resolve_provider("some:session")
        assert provider is plain_provider
        assert model == "gpt-4"


class TestSpawnInheritsProvider:
    """Test that spawn() passes the resolved provider to subagent."""

    @pytest.mark.asyncio
    async def test_spawn_uses_per_session_provider(self, tmp_path):
        """Subagent should use parent session's per-session provider."""
        pool, prov_anthropic, prov_proxy = _make_pool()
        pool.switch_for_session("feishu:group_123", "anthropic_proxy")
        mgr = _make_manager(pool, tmp_path)

        await mgr.spawn(task="test task", session_key="feishu:group_123")

        # Wait for subagent to complete
        await asyncio.sleep(0.1)

        # The proxy provider should have been called, not the default
        assert prov_proxy.chat.called
        # The default provider should NOT have been called
        assert not prov_anthropic.chat.called

    @pytest.mark.asyncio
    async def test_spawn_without_override_uses_global(self, tmp_path):
        """Without per-session override, subagent uses global active provider."""
        pool, prov_anthropic, prov_proxy = _make_pool()
        mgr = _make_manager(pool, tmp_path)

        await mgr.spawn(task="test task", session_key="feishu:group_456")
        await asyncio.sleep(0.1)

        assert prov_anthropic.chat.called
        assert not prov_proxy.chat.called


class TestSpawnProviderSnapshot:
    """Test that provider is snapshotted at spawn time."""

    @pytest.mark.asyncio
    async def test_provider_switch_after_spawn_no_effect(self, tmp_path):
        """Switching provider after spawn should not affect running subagent."""
        pool, prov_anthropic, prov_proxy = _make_pool()
        pool.switch_for_session("feishu:group_123", "anthropic_proxy")
        mgr = _make_manager(pool, tmp_path)

        # Make chat slow so we can switch provider mid-flight
        call_count = 0
        async def slow_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call returns tool call to force a second iteration
                resp = FakeLLMResponse(content="", has_tool_calls=True)
                tc = MagicMock()
                tc.id = "tc1"
                tc.name = "read_file"
                tc.arguments = {"path": "/dev/null"}
                resp.tool_calls = [tc]
                return resp
            return FakeLLMResponse(content="done")

        prov_proxy.chat = AsyncMock(side_effect=slow_chat)

        await mgr.spawn(task="multi-step task", session_key="feishu:group_123")

        # Switch provider after spawn
        pool.switch_for_session("feishu:group_123", "anthropic")

        await asyncio.sleep(0.3)

        # Proxy should have been called (snapshotted at spawn time)
        assert prov_proxy.chat.call_count >= 1
        # Default should NOT have been called
        assert not prov_anthropic.chat.called


class TestFollowUpInheritsProvider:
    """Test that follow_up resume also resolves parent provider."""

    @pytest.mark.asyncio
    async def test_follow_up_resume_uses_parent_provider(self, tmp_path):
        """Resumed subagent should use parent session's current provider."""
        pool, prov_anthropic, prov_proxy = _make_pool()
        pool.switch_for_session("feishu:group_123", "anthropic_proxy")
        mgr = _make_manager(pool, tmp_path)

        # Create a session manager mock for follow_up
        session_mgr = MagicMock()
        session_obj = MagicMock()
        session_obj.get_history.return_value = [
            {"role": "user", "content": "original task"},
            {"role": "assistant", "content": "done"},
        ]
        session_mgr.get_or_create.return_value = session_obj
        session_mgr.append_message = MagicMock()
        mgr.session_manager = session_mgr

        # Spawn and wait for completion
        await mgr.spawn(task="original task", session_key="feishu:group_123", persist=True)
        await asyncio.sleep(0.1)

        # Reset call counts
        prov_proxy.chat.reset_mock()
        prov_anthropic.chat.reset_mock()

        # Get the task_id
        task_id = list(mgr._task_meta.keys())[0]

        # Follow up (resume)
        await mgr.follow_up(
            task_id=task_id,
            message="continue please",
            parent_session_key="feishu:group_123",
        )
        await asyncio.sleep(0.1)

        # Proxy should be used for the resumed subagent too
        assert prov_proxy.chat.called
        assert not prov_anthropic.chat.called


class TestQueuedSpawnInheritsProvider:
    """Test that queued spawns carry the provider snapshot."""

    @pytest.mark.asyncio
    async def test_queued_spawn_uses_snapshotted_provider(self, tmp_path):
        """Queued spawn should use provider snapshotted at queue time."""
        pool, prov_anthropic, prov_proxy = _make_pool()
        pool.switch_for_session("feishu:group_123", "anthropic_proxy")

        bus = MagicMock()
        bus.publish_inbound = AsyncMock()
        mgr = SubagentManager(
            provider=pool,
            workspace=tmp_path,
            bus=bus,
            model=pool.active_model,
            max_concurrency=1,  # Only 1 concurrent
        )

        # Block the first slot
        blocker = asyncio.Future()
        prov_proxy.chat = AsyncMock(side_effect=lambda **kw: blocker)

        await mgr.spawn(task="blocker", session_key="feishu:group_123")

        # Queue a second task
        result = await mgr.spawn(task="queued task", session_key="feishu:group_123")
        assert "queued" in result.lower()

        # Verify the queued spawn has the provider snapshot
        assert len(mgr._queue) == 1
        queued = mgr._queue[0]
        assert queued.resolved_provider is prov_proxy
        assert queued.resolved_model == "claude-sonnet-4-20250514"

        # Clean up
        blocker.set_result(FakeLLMResponse())
        await asyncio.sleep(0.1)
