"""Agent callback protocol for SDK consumers.

Defines the ``AgentCallbacks`` Protocol that external callers (web-chat
Worker, custom integrations) implement to receive real-time events from
the agent loop.

All methods are async and have default no-op implementations, so callers
only need to override the events they care about.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from nanobot.bus.events import OutboundMessage
    from nanobot.bus.queue import MessageBus


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
class SessionMessenger(Protocol):
    """Protocol for inter-session message delivery.

    Implementations decide how to deliver a message to a target session:
    - If the target session is actively running → inject as pending input
    - If the target session is idle → trigger a new execution round

    Used by SubagentManager to announce results back to the parent session
    without relying on the message bus (which may not have a consumer in
    web-worker or CLI modes).
    """

    async def send_to_session(
        self,
        target_session_key: str,
        content: str,
        source_session_key: str | None = None,
    ) -> bool:
        """Send a message to the target session.

        Parameters
        ----------
        target_session_key:
            The session key of the recipient session.
        content:
            The message content to deliver.
        source_session_key:
            If provided, the content will be prefixed with
            ``[Message from session {source_session_key}]``.

        Returns
        -------
        bool
            True if the message was delivered (injected or triggered).
        """
        ...


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

    async def check_user_input(self) -> str | None:
        """Check if user has pending input to inject into the agent loop.

        Called between tool execution rounds — after all tools in the
        current round complete, before the next LLM call.  Must return
        immediately (non-blocking).

        Returns
        -------
        str or None
            User text to inject, or ``None`` if no input is pending.
        """
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

    async def check_user_input(self) -> str | None:
        return None


class GatewayCallbacks(DefaultCallbacks):
    """Per-session callbacks for gateway mode with user injection support.

    Each concurrent session task gets its own GatewayCallbacks instance.
    The dispatcher puts new messages into the inject queue; the agent loop
    checks it between tool rounds via ``check_user_input()``.

    Progress events are forwarded to the bus as OutboundMessages so that
    the IM channel can display them.
    """

    def __init__(self, bus: "MessageBus", channel: str, chat_id: str):
        self._inject_queue: asyncio.Queue[str] = asyncio.Queue()
        self._bus = bus
        self._channel = channel
        self._chat_id = chat_id

    async def check_user_input(self) -> str | None:
        """Non-blocking check for injected user messages."""
        try:
            return self._inject_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def inject(self, text: str) -> None:
        """Called by the dispatcher to inject a user message into this session."""
        await self._inject_queue.put(text)

    async def on_progress(self, text: str, *, tool_hint: bool = False) -> None:
        """Forward progress to bus as outbound message."""
        from nanobot.bus.events import OutboundMessage
        meta = {"_progress": True, "_tool_hint": tool_hint}
        await self._bus.publish_outbound(OutboundMessage(
            channel=self._channel, chat_id=self._chat_id,
            content=text, metadata=meta,
        ))
