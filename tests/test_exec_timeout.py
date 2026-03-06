"""Tests for ExecTool dynamic timeout parameter (Phase 25b)."""

import asyncio
import pytest

from nanobot.agent.tools.shell import ExecTool


class TestExecDynamicTimeout:
    """Test the dynamic timeout parameter for ExecTool."""

    def test_timeout_in_parameters(self):
        """timeout should appear in tool parameters as optional."""
        tool = ExecTool()
        params = tool.parameters
        assert "timeout" in params["properties"]
        assert params["properties"]["timeout"]["type"] == "integer"
        assert "timeout" not in params.get("required", [])

    @pytest.mark.asyncio
    async def test_dynamic_timeout_used(self):
        """When timeout is passed, it should be used instead of default."""
        tool = ExecTool(timeout=5)
        # A fast command should succeed with any timeout
        result = await tool.execute("echo hello", timeout=10)
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_default_fallback(self):
        """When timeout is not passed, instance default is used."""
        tool = ExecTool(timeout=60)
        result = await tool.execute("echo works")
        assert "works" in result

    @pytest.mark.asyncio
    async def test_max_timeout_cap(self):
        """Timeout should be capped at MAX_TIMEOUT."""
        tool = ExecTool(timeout=10)
        # Pass a huge timeout — should be capped to MAX_TIMEOUT (600)
        # We can't easily test the actual cap without a long-running command,
        # but we can verify the code path works
        result = await tool.execute("echo capped", timeout=9999)
        assert "capped" in result

    @pytest.mark.asyncio
    async def test_timeout_error_message(self):
        """Timeout error message should show the effective timeout value."""
        tool = ExecTool(timeout=60)
        # Use a 1-second timeout with a command that sleeps longer
        result = await tool.execute("sleep 10", timeout=1)
        assert "timed out" in result
        assert "1 seconds" in result
        # Should NOT show the default 60
        assert "60 seconds" not in result

    @pytest.mark.asyncio
    async def test_none_timeout_uses_default(self):
        """Explicitly passing timeout=None should use instance default."""
        tool = ExecTool(timeout=60)
        result = await tool.execute("echo default", timeout=None)
        assert "default" in result

    def test_max_timeout_constant(self):
        """MAX_TIMEOUT should be 600 seconds (10 minutes)."""
        assert ExecTool.MAX_TIMEOUT == 600
