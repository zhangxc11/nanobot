"""Agent callback protocol for SDK consumers.

Defines the ``AgentCallbacks`` Protocol that external callers (web-chat
Worker, custom integrations) implement to receive real-time events from
the agent loop.

All methods are async and have default no-op implementations, so callers
only need to override the events they care about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class AgentResult:
    """Result of an agent invocation."""

    content: str
    """Final assistant response text."""

    tools_used: list[str] = field(default_factory=list)
    """Names of tools invoked during the turn."""

    usage: dict[str, Any] | None = None
    """Token usage summary (prompt_tokens, completion_tokens, total_tokens, llm_calls)."""

    messages: list[dict] | None = None
    """Full message list at end of turn (optional, for debugging)."""


@runtime_checkable
class AgentCallbacks(Protocol):
    """Protocol for receiving agent loop events.

    All methods have default no-op implementations via the
    ``DefaultCallbacks`` mixin, so consumers only override what they need.
    """

    async def on_progress(self, text: str, *, tool_hint: bool = False) -> None:
        """Called when the agent produces intermediate output.

        Parameters
        ----------
        text:
            Progress text (e.g. tool invocation description, thinking output).
        tool_hint:
            If True, *text* describes a tool call rather than content.
        """
        ...

    async def on_message(self, message: dict) -> None:
        """Called when a new message is persisted to the session.

        The *message* dict has the standard ``role`` / ``content`` shape.
        Note: session persistence is handled by the core layer; this
        callback is purely informational.
        """
        ...

    async def on_usage(self, usage: dict[str, Any]) -> None:
        """Called when token usage data is available (at end of turn).

        *usage* contains: model, session_key, prompt_tokens,
        completion_tokens, total_tokens, llm_calls, started_at, finished_at.
        """
        ...

    async def on_done(self, result: AgentResult) -> None:
        """Called when the agent turn completes successfully."""
        ...

    async def on_error(self, error: Exception) -> None:
        """Called when the agent turn fails with an exception."""
        ...


class DefaultCallbacks:
    """Concrete base class with no-op implementations of all callbacks.

    Subclass this instead of implementing ``AgentCallbacks`` from scratch
    to get default no-op behavior for unneeded events.
    """

    async def on_progress(self, text: str, *, tool_hint: bool = False) -> None:
        pass

    async def on_message(self, message: dict) -> None:
        pass

    async def on_usage(self, usage: dict[str, Any]) -> None:
        pass

    async def on_done(self, result: AgentResult) -> None:
        pass

    async def on_error(self, error: Exception) -> None:
        pass
