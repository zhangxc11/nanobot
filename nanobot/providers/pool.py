"""ProviderPool — runtime-switchable multi-provider proxy.

Implements the ``LLMProvider`` interface so that ``AgentLoop`` can use it
transparently.  Internally holds multiple provider instances and routes
``.chat()`` calls to the currently active one.

Usage::

    pool = ProviderPool(
        providers={
            "anthropic": (litellm_provider_1, "claude-opus-4-6"),
            "deepseek":  (litellm_provider_2, "deepseek-chat"),
        },
        active_provider="anthropic",
        active_model="claude-opus-4-6",
    )

    # Switch at runtime (e.g. from /provider command or API)
    pool.switch("deepseek")  # uses deepseek's default model
    pool.switch("anthropic", "claude-sonnet-4-20250514")  # explicit model

    # AgentLoop just calls pool.chat() — no awareness of switching
    response = await pool.chat(messages, model=pool.active_model, ...)
"""

from __future__ import annotations

from typing import Any

from nanobot.providers.base import LLMProvider, LLMResponse


class ProviderPool(LLMProvider):
    """Multi-provider proxy with runtime switching.

    Parameters
    ----------
    providers:
        Mapping of provider name → (LLMProvider instance, default model).
    active_provider:
        Name of the initially active provider.
    active_model:
        Initially active model name.
    """

    def __init__(
        self,
        providers: dict[str, tuple[LLMProvider, str]],
        active_provider: str,
        active_model: str,
    ):
        # Don't call super().__init__ with api_key/api_base — Pool doesn't own credentials
        super().__init__()
        if not providers:
            raise ValueError("ProviderPool requires at least one provider")
        if active_provider not in providers:
            raise ValueError(
                f"Active provider '{active_provider}' not in providers: {list(providers.keys())}"
            )
        self._providers = providers
        self._active_provider = active_provider
        self._active_model = active_model
        # Per-session overrides: session_key → (provider_name, model)
        self._session_overrides: dict[str, tuple[str, str]] = {}

    # ── State queries ──

    @property
    def active_provider(self) -> str:
        """Name of the currently active provider."""
        return self._active_provider

    @property
    def active_model(self) -> str:
        """Currently active model name."""
        return self._active_model

    @property
    def available(self) -> list[dict[str, str]]:
        """List of all available providers with their default models."""
        return [
            {"name": name, "model": default_model}
            for name, (_, default_model) in self._providers.items()
        ]

    # ── Switching ──

    def switch(self, provider: str, model: str | None = None) -> None:
        """Switch the active provider and optionally the model.

        Parameters
        ----------
        provider:
            Provider name (must exist in the pool).
        model:
            Model name. If ``None``, uses the provider's default model.

        Raises
        ------
        ValueError:
            If the provider name is not in the pool.
        """
        if provider not in self._providers:
            raise ValueError(
                f"Unknown provider: '{provider}'. "
                f"Available: {list(self._providers.keys())}"
            )
        self._active_provider = provider
        _, default_model = self._providers[provider]
        self._active_model = model or default_model

    # ── Per-session overrides ──

    def get_for_session(self, session_key: str) -> tuple[LLMProvider, str]:
        """Return the (provider_instance, model) for a given session.

        If the session has a per-session override, return that; otherwise
        return the global active provider and model.
        """
        if session_key in self._session_overrides:
            provider_name, model = self._session_overrides[session_key]
            provider_instance, _ = self._providers[provider_name]
            return provider_instance, model
        # Fallback to global active
        provider_instance, _ = self._providers[self._active_provider]
        return provider_instance, self._active_model

    def get_session_provider_name(self, session_key: str) -> str:
        """Return the provider name for a given session (override or global)."""
        if session_key in self._session_overrides:
            return self._session_overrides[session_key][0]
        return self._active_provider

    def get_session_model(self, session_key: str) -> str:
        """Return the model name for a given session (override or global)."""
        if session_key in self._session_overrides:
            return self._session_overrides[session_key][1]
        return self._active_model

    def switch_for_session(
        self, session_key: str, provider: str, model: str | None = None
    ) -> None:
        """Set a per-session provider/model override.

        Parameters
        ----------
        session_key:
            The session to override.
        provider:
            Provider name (must exist in the pool).
        model:
            Model name. If ``None``, uses the provider's default model.

        Raises
        ------
        ValueError:
            If the provider name is not in the pool.
        """
        if provider not in self._providers:
            raise ValueError(
                f"Unknown provider: '{provider}'. "
                f"Available: {list(self._providers.keys())}"
            )
        _, default_model = self._providers[provider]
        self._session_overrides[session_key] = (provider, model or default_model)

    def clear_session_override(self, session_key: str) -> None:
        """Remove the per-session override, reverting to global active."""
        self._session_overrides.pop(session_key, None)

    # ── LLMProvider interface ──

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        """Route the chat call to the active provider.

        The ``model`` parameter from the caller is **ignored** — the pool
        always uses ``self._active_model``.  This ensures that switching
        provider+model via ``switch()`` takes full effect without requiring
        AgentLoop code changes.
        """
        provider, _ = self._providers[self._active_provider]
        return await provider.chat(
            messages,
            tools=tools,
            model=self._active_model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )

    def get_default_model(self) -> str:
        """Return the currently active model."""
        return self._active_model
