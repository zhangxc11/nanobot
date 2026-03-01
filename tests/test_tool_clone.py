"""Tests for Tool clone mechanism — T19.2."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.registry import ToolRegistry


# ── MessageTool.clone() ──

class TestMessageToolClone:

    def test_clone_creates_new_instance(self):
        original = MessageTool(send_callback=AsyncMock(), default_channel="feishu.lab", default_chat_id="ou_123")
        clone = original.clone()
        assert clone is not original
        assert isinstance(clone, MessageTool)

    def test_clone_shares_send_callback(self):
        cb = AsyncMock()
        original = MessageTool(send_callback=cb)
        clone = original.clone()
        assert clone._send_callback is cb

    def test_clone_has_independent_context(self):
        original = MessageTool(send_callback=AsyncMock(), default_channel="feishu.lab", default_chat_id="ou_123")
        clone = original.clone()
        clone.set_context("feishu.ST", "ou_456")
        assert original._default_channel == "feishu.lab"
        assert original._default_chat_id == "ou_123"
        assert clone._default_channel == "feishu.ST"
        assert clone._default_chat_id == "ou_456"

    def test_clone_has_independent_turn_tracking(self):
        original = MessageTool(send_callback=AsyncMock())
        clone = original.clone()
        clone._sent_in_turn = True
        assert original._sent_in_turn is False


# ── SpawnTool.clone() ──

class TestSpawnToolClone:

    def test_clone_creates_new_instance(self):
        manager = MagicMock()
        original = SpawnTool(manager=manager)
        clone = original.clone()
        assert clone is not original
        assert isinstance(clone, SpawnTool)

    def test_clone_shares_manager(self):
        manager = MagicMock()
        original = SpawnTool(manager=manager)
        clone = original.clone()
        assert clone._manager is manager

    def test_clone_has_independent_context(self):
        manager = MagicMock()
        original = SpawnTool(manager=manager)
        original.set_context("feishu.lab", "ou_123")
        clone = original.clone()
        clone.set_context("feishu.ST", "ou_456")
        assert original._origin_channel == "feishu.lab"
        assert clone._origin_channel == "feishu.ST"


# ── CronTool.clone() ──

class TestCronToolClone:

    def test_clone_creates_new_instance(self):
        cron_service = MagicMock()
        original = CronTool(cron_service)
        clone = original.clone()
        assert clone is not original
        assert isinstance(clone, CronTool)

    def test_clone_shares_cron_service(self):
        cron_service = MagicMock()
        original = CronTool(cron_service)
        clone = original.clone()
        assert clone._cron is cron_service

    def test_clone_has_independent_context(self):
        cron_service = MagicMock()
        original = CronTool(cron_service)
        original.set_context("feishu.lab", "ou_123")
        clone = original.clone()
        clone.set_context("feishu.ST", "ou_456")
        assert original._channel == "feishu.lab"
        assert clone._channel == "feishu.ST"


# ── ToolRegistry.clone_for_session() ──

class TestToolRegistryClone:

    def _make_registry(self) -> ToolRegistry:
        """Create a ToolRegistry with both stateful and stateless tools."""
        registry = ToolRegistry()
        # Stateful tools
        registry.register(MessageTool(send_callback=AsyncMock()))
        registry.register(SpawnTool(manager=MagicMock()))
        registry.register(CronTool(MagicMock()))
        # Simulate a stateless tool (e.g. read_file)
        stateless = MagicMock()
        stateless.name = "read_file"
        stateless.to_schema.return_value = {"type": "function", "function": {"name": "read_file"}}
        registry.register(stateless)
        return registry

    def test_clone_returns_new_registry(self):
        registry = self._make_registry()
        clone = registry.clone_for_session()
        assert clone is not registry
        assert isinstance(clone, ToolRegistry)

    def test_clone_has_same_tools(self):
        registry = self._make_registry()
        clone = registry.clone_for_session()
        assert set(clone.tool_names) == set(registry.tool_names)

    def test_stateless_tools_shared(self):
        """Stateless tools (read_file) should be the same instance."""
        registry = self._make_registry()
        clone = registry.clone_for_session()
        assert clone.get("read_file") is registry.get("read_file")

    def test_stateful_tools_cloned(self):
        """Stateful tools (message, spawn, cron) should be new instances."""
        registry = self._make_registry()
        clone = registry.clone_for_session()
        assert clone.get("message") is not registry.get("message")
        assert clone.get("spawn") is not registry.get("spawn")
        assert clone.get("cron") is not registry.get("cron")

    def test_cloned_message_tool_independent_context(self):
        """Setting context on cloned MessageTool doesn't affect original."""
        registry = self._make_registry()
        msg_original = registry.get("message")
        msg_original.set_context("feishu.lab", "ou_111")

        clone = registry.clone_for_session()
        msg_clone = clone.get("message")
        msg_clone.set_context("feishu.ST", "ou_222")

        assert msg_original._default_channel == "feishu.lab"
        assert msg_clone._default_channel == "feishu.ST"

    def test_clone_shares_audit_logger(self):
        """Audit logger is shared (thread-safe), audit context is independent."""
        registry = self._make_registry()
        mock_logger = MagicMock()
        registry.set_audit_logger(mock_logger)
        registry.set_audit_context(session_key="session_a")

        clone = registry.clone_for_session()
        assert clone._audit_logger is mock_logger
        clone.set_audit_context(session_key="session_b")

        assert registry._audit_context["session_key"] == "session_a"
        assert clone._audit_context["session_key"] == "session_b"

    def test_clone_without_stateful_tools(self):
        """Clone works even if no stateful tools are registered."""
        registry = ToolRegistry()
        stateless = MagicMock()
        stateless.name = "read_file"
        registry.register(stateless)
        clone = registry.clone_for_session()
        assert clone.get("read_file") is registry.get("read_file")
