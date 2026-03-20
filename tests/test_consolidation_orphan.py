"""Tests for §63: Consolidation orphan tool_result cleanup + 400 not retryable."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.retry import is_retryable


# ---------------------------------------------------------------------------
# §63 Fix 2: 400 BadRequestError not retryable
# ---------------------------------------------------------------------------

class _FakeError(Exception):
    """Exception with configurable status_code for testing."""
    def __init__(self, msg: str = "error", status_code: int | None = None):
        super().__init__(msg)
        if status_code is not None:
            self.status_code = status_code


class BadRequestError(_FakeError):
    """Simulates litellm's BadRequestError (class name matters for retry logic)."""
    pass


class RateLimitError(_FakeError):
    """Simulates litellm's RateLimitError."""
    pass


class InternalServerError(_FakeError):
    """Simulates litellm's InternalServerError (in _RETRYABLE_CLASSES)."""
    pass


def test_400_not_retryable():
    """HTTP 400 should never be retried — it's a client error."""
    err = BadRequestError("invalid request", status_code=400)
    assert is_retryable(err) is False


def test_400_with_retryable_class_name_not_retried():
    """Even if exception class name is in _RETRYABLE_CLASSES, 400 should not be retried."""
    # InternalServerError is in _RETRYABLE_CLASSES, but with status_code=400
    # (hypothetical edge case where litellm wraps a 400 in a retryable class)
    err = InternalServerError("bad request body", status_code=400)
    assert is_retryable(err) is False


def test_400_with_retryable_message_not_retried():
    """400 with a message containing 'rate limit' should still not be retried."""
    err = BadRequestError("rate limit exceeded", status_code=400)
    assert is_retryable(err) is False


def test_429_still_retryable():
    """429 (rate limit) should still be retried — regression test."""
    err = RateLimitError("too many requests", status_code=429)
    assert is_retryable(err) is True


def test_500_still_retryable():
    """500 (internal server error) should still be retried — regression test."""
    err = InternalServerError("internal server error", status_code=500)
    assert is_retryable(err) is True


def test_502_still_retryable():
    """502 (bad gateway) should still be retried — regression test."""
    err = _FakeError("bad gateway", status_code=502)
    assert is_retryable(err) is True


def test_non_retryable_msg_pattern_takes_priority():
    """Non-retryable message patterns should still take priority over everything."""
    err = RateLimitError("model_not_found", status_code=429)
    assert is_retryable(err) is False


# ---------------------------------------------------------------------------
# §63 Fix 1: Consolidation orphan tool_result stripping
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temporary workspace with memory directory."""
    (tmp_path / "memory").mkdir()
    return tmp_path


def _make_session(messages: list[dict], last_consolidated: int = 0) -> MagicMock:
    """Create a mock Session with given messages."""
    session = MagicMock()
    session.messages = messages
    session.last_consolidated = last_consolidated
    session.key = "test:session"
    return session


def _make_provider_response(history_entry: str = "test entry", memory_update: str = "test memory"):
    """Create a mock LLM response that calls save_memory."""
    response = MagicMock()
    response.has_tool_calls = True
    response.usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    response.content = None
    response.finish_reason = "tool_calls"

    tool_call = MagicMock()
    tool_call.arguments = {
        "history_entry": history_entry,
        "memory_update": memory_update,
    }
    response.tool_calls = [tool_call]
    return response


@pytest.mark.asyncio
async def test_strip_orphan_tool_result_from_consolidation_input(tmp_workspace):
    """Orphan tool_result at the start of consolidation slice should be stripped."""
    from nanobot.agent.memory import MemoryStore

    store = MemoryStore(tmp_workspace)

    # Simulate messages where slice boundary cuts a tool_use/tool_result pair:
    # Messages 0-4: already consolidated (including assistant with tool_calls at index 3)
    # Messages 5-9: to be consolidated (starts with orphan tool_result at index 5)
    # Messages 10-14: to be kept (keep_count=5)
    messages = [
        {"role": "user", "content": "hello", "timestamp": "2026-03-20T10:00:00"},
        {"role": "assistant", "content": "hi there", "timestamp": "2026-03-20T10:00:01"},
        {"role": "user", "content": "do something", "timestamp": "2026-03-20T10:00:02"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "tc_1", "type": "function", "function": {"name": "exec", "arguments": "{}"}}], "timestamp": "2026-03-20T10:00:03"},
        {"role": "tool", "content": "result of exec", "tool_call_id": "tc_1", "name": "exec", "timestamp": "2026-03-20T10:00:04"},
        # --- slice boundary: last_consolidated=4, so slice starts at index 4 ---
        # index 5: orphan tool_result (its assistant is at index 3, already consolidated)
        {"role": "tool", "content": "orphan result", "tool_call_id": "tc_orphan", "name": "read_file", "timestamp": "2026-03-20T10:00:05"},
        {"role": "user", "content": "next question", "timestamp": "2026-03-20T10:00:06"},
        {"role": "assistant", "content": "answer", "timestamp": "2026-03-20T10:00:07"},
        {"role": "user", "content": "another question", "timestamp": "2026-03-20T10:00:08"},
        {"role": "assistant", "content": "another answer", "timestamp": "2026-03-20T10:00:09"},
        # --- keep boundary: last 5 messages are kept ---
        {"role": "user", "content": "keep 1", "timestamp": "2026-03-20T10:00:10"},
        {"role": "assistant", "content": "keep 2", "timestamp": "2026-03-20T10:00:11"},
        {"role": "user", "content": "keep 3", "timestamp": "2026-03-20T10:00:12"},
        {"role": "assistant", "content": "keep 4", "timestamp": "2026-03-20T10:00:13"},
        {"role": "user", "content": "keep 5", "timestamp": "2026-03-20T10:00:14"},
    ]

    session = _make_session(messages, last_consolidated=5)

    # Mock provider to capture what messages are sent
    captured_messages = []
    provider = AsyncMock()
    response = _make_provider_response()
    async def capture_chat(**kwargs):
        captured_messages.extend(kwargs.get("messages", []))
        return response
    provider.chat = capture_chat

    result = await store.consolidate(
        session, provider, "test-model",
        memory_window=10,  # keep_count = 10//2 = 5
    )

    assert result is True
    # Verify no tool_result messages at the start of what was sent to LLM
    # The captured messages should be: [system, ...old_messages_without_orphan..., user_instruction]
    # Find the consolidation instruction (last user message)
    assert len(captured_messages) > 0

    # In fallback path (no session_system_msg), messages = [system, user_with_prompt]
    # The old_messages are processed into text lines, so we check the prompt content
    # doesn't include the orphan tool_result
    user_prompt = None
    for m in captured_messages:
        if m.get("role") == "user":
            user_prompt = m.get("content", "")
            break
    assert user_prompt is not None
    assert "orphan result" not in user_prompt


@pytest.mark.asyncio
async def test_strip_multiple_orphan_tool_results(tmp_workspace):
    """Multiple consecutive orphan tool_results should all be stripped."""
    from nanobot.agent.memory import MemoryStore

    store = MemoryStore(tmp_workspace)

    messages = [
        # Orphan tool results at the beginning of the slice
        {"role": "tool", "content": "orphan 1", "tool_call_id": "tc_1", "name": "exec", "timestamp": "2026-03-20T10:00:00"},
        {"role": "tool", "content": "orphan 2", "tool_call_id": "tc_2", "name": "read_file", "timestamp": "2026-03-20T10:00:01"},
        {"role": "tool", "content": "orphan 3", "tool_call_id": "tc_3", "name": "write_file", "timestamp": "2026-03-20T10:00:02"},
        # Real messages
        {"role": "user", "content": "question", "timestamp": "2026-03-20T10:00:03"},
        {"role": "assistant", "content": "answer", "timestamp": "2026-03-20T10:00:04"},
        # Keep window
        {"role": "user", "content": "keep 1", "timestamp": "2026-03-20T10:00:05"},
        {"role": "assistant", "content": "keep 2", "timestamp": "2026-03-20T10:00:06"},
        {"role": "user", "content": "keep 3", "timestamp": "2026-03-20T10:00:07"},
        {"role": "assistant", "content": "keep 4", "timestamp": "2026-03-20T10:00:08"},
        {"role": "user", "content": "keep 5", "timestamp": "2026-03-20T10:00:09"},
    ]

    session = _make_session(messages, last_consolidated=0)

    captured_messages = []
    provider = AsyncMock()
    response = _make_provider_response()
    async def capture_chat(**kwargs):
        captured_messages.extend(kwargs.get("messages", []))
        return response
    provider.chat = capture_chat

    result = await store.consolidate(
        session, provider, "test-model",
        memory_window=10,
    )

    assert result is True
    # Verify no orphan content in the prompt
    user_prompt = ""
    for m in captured_messages:
        if m.get("role") == "user":
            user_prompt = m.get("content", "")
            break
    assert "orphan 1" not in user_prompt
    assert "orphan 2" not in user_prompt
    assert "orphan 3" not in user_prompt


@pytest.mark.asyncio
async def test_all_orphans_returns_true(tmp_workspace):
    """If all messages in the slice are orphan tool_results, consolidate returns True (no-op)."""
    from nanobot.agent.memory import MemoryStore

    store = MemoryStore(tmp_workspace)

    messages = [
        {"role": "tool", "content": "orphan 1", "tool_call_id": "tc_1", "name": "exec", "timestamp": "2026-03-20T10:00:00"},
        {"role": "tool", "content": "orphan 2", "tool_call_id": "tc_2", "name": "read_file", "timestamp": "2026-03-20T10:00:01"},
        # Keep window
        {"role": "user", "content": "keep 1", "timestamp": "2026-03-20T10:00:02"},
        {"role": "assistant", "content": "keep 2", "timestamp": "2026-03-20T10:00:03"},
        {"role": "user", "content": "keep 3", "timestamp": "2026-03-20T10:00:04"},
        {"role": "assistant", "content": "keep 4", "timestamp": "2026-03-20T10:00:05"},
        {"role": "user", "content": "keep 5", "timestamp": "2026-03-20T10:00:06"},
    ]

    session = _make_session(messages, last_consolidated=0)

    provider = AsyncMock()
    # Provider should NOT be called since all messages are orphans
    provider.chat = AsyncMock()

    result = await store.consolidate(
        session, provider, "test-model",
        memory_window=10,
    )

    assert result is True
    provider.chat.assert_not_called()


@pytest.mark.asyncio
async def test_cache_friendly_path_strips_orphans(tmp_workspace):
    """In cache-friendly path (with session_system_msg), orphans should also be stripped."""
    from nanobot.agent.memory import MemoryStore

    store = MemoryStore(tmp_workspace)

    messages = [
        {"role": "tool", "content": "orphan", "tool_call_id": "tc_1", "name": "exec", "timestamp": "2026-03-20T10:00:00"},
        {"role": "user", "content": "question", "timestamp": "2026-03-20T10:00:01"},
        {"role": "assistant", "content": "answer", "timestamp": "2026-03-20T10:00:02"},
        # Keep window
        {"role": "user", "content": "keep 1", "timestamp": "2026-03-20T10:00:03"},
        {"role": "assistant", "content": "keep 2", "timestamp": "2026-03-20T10:00:04"},
        {"role": "user", "content": "keep 3", "timestamp": "2026-03-20T10:00:05"},
        {"role": "assistant", "content": "keep 4", "timestamp": "2026-03-20T10:00:06"},
        {"role": "user", "content": "keep 5", "timestamp": "2026-03-20T10:00:07"},
    ]

    session = _make_session(messages, last_consolidated=0)

    captured_messages = []
    provider = AsyncMock()
    response = _make_provider_response()
    async def capture_chat(**kwargs):
        captured_messages.extend(kwargs.get("messages", []))
        return response
    provider.chat = capture_chat

    system_msg = {"role": "system", "content": "You are a helpful assistant."}
    tool_defs = [{"type": "function", "function": {"name": "test_tool", "parameters": {}}}]

    result = await store.consolidate(
        session, provider, "test-model",
        memory_window=10,
        session_system_msg=system_msg,
        session_tools=tool_defs,
    )

    assert result is True
    # In cache-friendly path, messages = [system_msg] + stripped + [user_instruction]
    # Check that no tool role message appears right after system_msg
    if len(captured_messages) > 1:
        # The second message should NOT be a tool role (orphan was stripped)
        assert captured_messages[1].get("role") != "tool", \
            f"Orphan tool_result should have been stripped, got: {captured_messages[1]}"


# ---------------------------------------------------------------------------
# §64: Silent cleanup tests
# ---------------------------------------------------------------------------

def test_truncation_warning_template_updated():
    """Verify the truncation warning template has the new system-style format."""
    from nanobot.agent.loop import _TRUNCATION_WARNING_TEMPLATE
    assert "System — Context Approaching Limit" in _TRUNCATION_WARNING_TEMPLATE
    assert "Immediately write a session summary" in _TRUNCATION_WARNING_TEMPLATE
    assert "continue your current task without interruption" in _TRUNCATION_WARNING_TEMPLATE
    # Verify placeholders are present
    assert "{current}" in _TRUNCATION_WARNING_TEMPLATE
    assert "{max}" in _TRUNCATION_WARNING_TEMPLATE
    assert "{workspace}" in _TRUNCATION_WARNING_TEMPLATE
    assert "{session_id}" in _TRUNCATION_WARNING_TEMPLATE
