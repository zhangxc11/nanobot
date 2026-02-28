"""Tests for ProviderPool — runtime-switchable multi-provider proxy."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from nanobot.providers.pool import ProviderPool
from nanobot.providers.base import LLMProvider, LLMResponse


# ── Fixtures ──

def _make_mock_provider(name: str = "mock", default_model: str = "mock-model") -> LLMProvider:
    """Create a mock LLMProvider."""
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = default_model
    provider.chat = AsyncMock(return_value=LLMResponse(
        content=f"Response from {name}",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    ))
    return provider


def _make_pool(
    providers: dict[str, tuple[LLMProvider, str]] | None = None,
    active_provider: str = "anthropic",
    active_model: str = "claude-opus-4-6",
) -> ProviderPool:
    """Create a ProviderPool with sensible defaults."""
    if providers is None:
        providers = {
            "anthropic": (_make_mock_provider("anthropic", "claude-opus-4-6"), "claude-opus-4-6"),
            "deepseek": (_make_mock_provider("deepseek", "deepseek-chat"), "deepseek-chat"),
        }
    return ProviderPool(
        providers=providers,
        active_provider=active_provider,
        active_model=active_model,
    )


# ── Construction tests ──

class TestProviderPoolInit:
    """Tests for ProviderPool construction."""

    def test_basic_construction(self):
        pool = _make_pool()
        assert pool.active_provider == "anthropic"
        assert pool.active_model == "claude-opus-4-6"

    def test_empty_providers_raises(self):
        with pytest.raises(ValueError, match="at least one provider"):
            ProviderPool(providers={}, active_provider="x", active_model="y")

    def test_invalid_active_provider_raises(self):
        providers = {"anthropic": (_make_mock_provider(), "claude-opus-4-6")}
        with pytest.raises(ValueError, match="not in providers"):
            ProviderPool(providers=providers, active_provider="nonexistent", active_model="x")

    def test_single_provider(self):
        providers = {"anthropic": (_make_mock_provider("anthropic"), "claude-opus-4-6")}
        pool = ProviderPool(providers=providers, active_provider="anthropic", active_model="claude-opus-4-6")
        assert pool.active_provider == "anthropic"
        assert len(pool.available) == 1


# ── State query tests ──

class TestProviderPoolQueries:
    """Tests for state query properties."""

    def test_available_lists_all_providers(self):
        pool = _make_pool()
        available = pool.available
        assert len(available) == 2
        names = [item["name"] for item in available]
        assert "anthropic" in names
        assert "deepseek" in names

    def test_available_includes_models(self):
        pool = _make_pool()
        for item in pool.available:
            if item["name"] == "anthropic":
                assert item["model"] == "claude-opus-4-6"
            elif item["name"] == "deepseek":
                assert item["model"] == "deepseek-chat"

    def test_get_default_model_returns_active(self):
        pool = _make_pool()
        assert pool.get_default_model() == "claude-opus-4-6"


# ── Switching tests ──

class TestProviderPoolSwitch:
    """Tests for provider switching."""

    def test_switch_provider_with_default_model(self):
        pool = _make_pool()
        pool.switch("deepseek")
        assert pool.active_provider == "deepseek"
        assert pool.active_model == "deepseek-chat"

    def test_switch_provider_with_explicit_model(self):
        pool = _make_pool()
        pool.switch("deepseek", "deepseek-reasoner")
        assert pool.active_provider == "deepseek"
        assert pool.active_model == "deepseek-reasoner"

    def test_switch_back(self):
        pool = _make_pool()
        pool.switch("deepseek")
        pool.switch("anthropic")
        assert pool.active_provider == "anthropic"
        assert pool.active_model == "claude-opus-4-6"

    def test_switch_unknown_provider_raises(self):
        pool = _make_pool()
        with pytest.raises(ValueError, match="Unknown provider"):
            pool.switch("nonexistent")

    def test_switch_updates_get_default_model(self):
        pool = _make_pool()
        pool.switch("deepseek")
        assert pool.get_default_model() == "deepseek-chat"

    def test_switch_same_provider_different_model(self):
        pool = _make_pool()
        pool.switch("anthropic", "claude-sonnet-4-20250514")
        assert pool.active_provider == "anthropic"
        assert pool.active_model == "claude-sonnet-4-20250514"


# ── Chat routing tests ──

class TestProviderPoolChat:
    """Tests for chat() routing to active provider."""

    def test_chat_routes_to_active_provider(self):
        mock_anthropic = _make_mock_provider("anthropic")
        mock_deepseek = _make_mock_provider("deepseek")
        pool = ProviderPool(
            providers={
                "anthropic": (mock_anthropic, "claude-opus-4-6"),
                "deepseek": (mock_deepseek, "deepseek-chat"),
            },
            active_provider="anthropic",
            active_model="claude-opus-4-6",
        )

        result = asyncio.new_event_loop().run_until_complete(
            pool.chat([{"role": "user", "content": "hi"}])
        )
        assert result.content == "Response from anthropic"
        mock_anthropic.chat.assert_called_once()
        mock_deepseek.chat.assert_not_called()

    def test_chat_uses_active_model(self):
        mock_provider = _make_mock_provider("anthropic")
        pool = ProviderPool(
            providers={"anthropic": (mock_provider, "claude-opus-4-6")},
            active_provider="anthropic",
            active_model="claude-opus-4-6",
        )

        asyncio.new_event_loop().run_until_complete(
            pool.chat([{"role": "user", "content": "hi"}], model="ignored-model")
        )
        # Pool should use active_model, ignoring the passed model
        call_args = mock_provider.chat.call_args
        assert call_args.kwargs.get("model") == "claude-opus-4-6"

    def test_chat_after_switch(self):
        mock_anthropic = _make_mock_provider("anthropic")
        mock_deepseek = _make_mock_provider("deepseek")
        pool = ProviderPool(
            providers={
                "anthropic": (mock_anthropic, "claude-opus-4-6"),
                "deepseek": (mock_deepseek, "deepseek-chat"),
            },
            active_provider="anthropic",
            active_model="claude-opus-4-6",
        )

        pool.switch("deepseek")
        result = asyncio.new_event_loop().run_until_complete(
            pool.chat([{"role": "user", "content": "hi"}])
        )
        assert result.content == "Response from deepseek"
        mock_deepseek.chat.assert_called_once()

    def test_chat_passes_all_params(self):
        mock_provider = _make_mock_provider("anthropic")
        pool = ProviderPool(
            providers={"anthropic": (mock_provider, "claude-opus-4-6")},
            active_provider="anthropic",
            active_model="claude-opus-4-6",
        )

        messages = [{"role": "user", "content": "hi"}]
        tools = [{"type": "function", "function": {"name": "test"}}]
        asyncio.new_event_loop().run_until_complete(
            pool.chat(messages, tools=tools, max_tokens=2048, temperature=0.5)
        )

        mock_provider.chat.assert_called_once_with(
            messages,
            tools=tools,
            model="claude-opus-4-6",
            max_tokens=2048,
            temperature=0.5,
        )


# ── /provider command tests (AgentLoop integration) ──

class TestProviderCommand:
    """Tests for /provider slash command handling in AgentLoop."""

    def _make_loop_with_pool(self):
        """Create a minimal AgentLoop with a ProviderPool for testing."""
        from unittest.mock import MagicMock
        from nanobot.bus.events import InboundMessage

        pool = _make_pool()
        loop = MagicMock()
        loop.provider = pool
        loop.model = pool.active_model

        # Import the actual method
        from nanobot.agent.loop import AgentLoop
        loop._handle_provider_command = AgentLoop._handle_provider_command.__get__(loop, type(loop))

        return loop, pool

    def test_provider_status(self):
        loop, pool = self._make_loop_with_pool()
        from nanobot.bus.events import InboundMessage
        msg = InboundMessage(channel="cli", chat_id="direct", sender_id="user", content="/provider")

        result = loop._handle_provider_command(msg)
        assert "anthropic" in result.content
        assert "claude-opus-4-6" in result.content
        assert "deepseek" in result.content

    def test_provider_switch(self):
        loop, pool = self._make_loop_with_pool()
        from nanobot.bus.events import InboundMessage
        msg = InboundMessage(channel="cli", chat_id="direct", sender_id="user", content="/provider deepseek")

        result = loop._handle_provider_command(msg)
        assert "✅" in result.content
        assert "deepseek" in result.content
        assert pool.active_provider == "deepseek"
        assert loop.model == "deepseek-chat"

    def test_provider_switch_with_model(self):
        loop, pool = self._make_loop_with_pool()
        from nanobot.bus.events import InboundMessage
        msg = InboundMessage(channel="cli", chat_id="direct", sender_id="user", content="/provider deepseek deepseek-reasoner")

        result = loop._handle_provider_command(msg)
        assert "✅" in result.content
        assert pool.active_model == "deepseek-reasoner"

    def test_provider_switch_invalid(self):
        loop, pool = self._make_loop_with_pool()
        from nanobot.bus.events import InboundMessage
        msg = InboundMessage(channel="cli", chat_id="direct", sender_id="user", content="/provider nonexistent")

        result = loop._handle_provider_command(msg)
        assert "❌" in result.content
        assert pool.active_provider == "anthropic"  # unchanged

    def test_provider_not_pool(self):
        """When provider is not a ProviderPool, show warning."""
        from nanobot.bus.events import InboundMessage
        loop = MagicMock()
        loop.provider = _make_mock_provider()  # plain provider, not a pool

        from nanobot.agent.loop import AgentLoop
        loop._handle_provider_command = AgentLoop._handle_provider_command.__get__(loop, type(loop))

        msg = InboundMessage(channel="cli", chat_id="direct", sender_id="user", content="/provider")
        result = loop._handle_provider_command(msg)
        assert "not available" in result.content
