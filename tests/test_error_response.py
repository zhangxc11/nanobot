"""Tests for LLM error response handling.

When the LLM returns finish_reason="error", the error content should be:
1. Persisted to session JSONL (prefixed with "Error calling LLM:")
2. Sent via callbacks (on_message + on_progress)
3. Returned as final_content to the caller
4. Filtered out by get_history() Phase 2 on subsequent turns
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse


def _make_loop(tmp_path: Path) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return AgentLoop(
        bus=bus, provider=provider, workspace=tmp_path,
        model="test-model", memory_window=50,
    )


class TestErrorResponsePersistence:
    """Error responses are persisted to JSONL but filtered from LLM context."""

    @pytest.mark.asyncio
    async def test_error_persisted_to_session(self, tmp_path: Path) -> None:
        """finish_reason='error' stores prefixed message in session."""
        loop = _make_loop(tmp_path)
        loop.provider.chat = AsyncMock(return_value=LLMResponse(
            content="Rate limit exceeded",
            tool_calls=[],
            finish_reason="error",
        ))
        loop.tools.get_definitions = MagicMock(return_value=[])

        msg = InboundMessage(
            channel="test", sender_id="u1", chat_id="c1", content="hello"
        )
        result = await loop._process_message(msg)

        assert result is not None
        assert "Rate limit exceeded" in result.content

        # Check session has the error message persisted
        session = loop.sessions.get_or_create("test:c1")
        assistant_msgs = [
            m for m in session.messages if m.get("role") == "assistant"
        ]
        assert len(assistant_msgs) >= 1
        error_msg = assistant_msgs[-1]
        assert error_msg["content"].startswith("Error calling LLM:")
        assert "Rate limit exceeded" in error_msg["content"]

    @pytest.mark.asyncio
    async def test_error_filtered_from_history(self, tmp_path: Path) -> None:
        """get_history() Phase 2 strips error messages from LLM context."""
        loop = _make_loop(tmp_path)
        loop.provider.chat = AsyncMock(return_value=LLMResponse(
            content="Some error",
            tool_calls=[],
            finish_reason="error",
        ))
        loop.tools.get_definitions = MagicMock(return_value=[])

        msg = InboundMessage(
            channel="test", sender_id="u1", chat_id="c1", content="hello"
        )
        await loop._process_message(msg)

        session = loop.sessions.get_or_create("test:c1")
        history = session.get_history()

        # Error message should be filtered out of history
        for m in history:
            if m.get("role") == "assistant":
                assert not m["content"].startswith("Error calling LLM:"), (
                    "Error messages should be filtered from get_history()"
                )

    @pytest.mark.asyncio
    async def test_error_triggers_callbacks(self, tmp_path: Path) -> None:
        """Error response fires on_message and on_progress callbacks."""
        loop = _make_loop(tmp_path)
        loop.provider.chat = AsyncMock(return_value=LLMResponse(
            content="Context too long",
            tool_calls=[],
            finish_reason="error",
        ))
        loop.tools.get_definitions = MagicMock(return_value=[])

        progress_msgs = []
        on_message_msgs = []

        class TestCallbacks:
            async def on_progress(self, text, **kwargs):
                progress_msgs.append(text)

            async def on_message(self, msg):
                on_message_msgs.append(msg)

            async def on_done(self, result):
                pass

            async def check_user_input(self):
                return None

        msg = InboundMessage(
            channel="test", sender_id="u1", chat_id="c1", content="hello"
        )
        await loop._process_message(msg, callbacks=TestCallbacks())

        # on_progress should have received the error
        assert any("Context too long" in p for p in progress_msgs)
        # on_message should have the error message
        assert any(
            m.get("content", "").startswith("Error calling LLM:")
            for m in on_message_msgs
        )

    @pytest.mark.asyncio
    async def test_error_default_message(self, tmp_path: Path) -> None:
        """Empty error content gets a default message."""
        loop = _make_loop(tmp_path)
        loop.provider.chat = AsyncMock(return_value=LLMResponse(
            content="",
            tool_calls=[],
            finish_reason="error",
        ))
        loop.tools.get_definitions = MagicMock(return_value=[])

        msg = InboundMessage(
            channel="test", sender_id="u1", chat_id="c1", content="hello"
        )
        result = await loop._process_message(msg)

        assert result is not None
        assert "error" in result.content.lower()

    @pytest.mark.asyncio
    async def test_normal_response_not_affected(self, tmp_path: Path) -> None:
        """Normal responses (no error) are persisted without prefix."""
        loop = _make_loop(tmp_path)
        loop.provider.chat = AsyncMock(return_value=LLMResponse(
            content="Hello! How can I help?",
            tool_calls=[],
        ))
        loop.tools.get_definitions = MagicMock(return_value=[])

        msg = InboundMessage(
            channel="test", sender_id="u1", chat_id="c1", content="hello"
        )
        result = await loop._process_message(msg)

        assert result is not None
        assert result.content == "Hello! How can I help?"

        session = loop.sessions.get_or_create("test:c1")
        assistant_msgs = [
            m for m in session.messages if m.get("role") == "assistant"
        ]
        assert len(assistant_msgs) >= 1
        # Normal message should NOT have the error prefix
        assert not assistant_msgs[-1]["content"].startswith("Error calling LLM:")
