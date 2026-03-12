"""Tests for inject queue drain behavior.

Verifies that the agent loop correctly drains ALL pending inject messages
from the queue, not just the first one.  Also verifies that messages
arriving during the LLM's final (non-tool-call) response are not lost.

Bug report: When multiple messages (subagent results + user inject) arrive
during a long tool execution (e.g. sleep 60), the old code only consumed
one message per checkpoint, causing later messages to be silently dropped.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.callbacks import DefaultCallbacks
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse, ToolCallRequest


def _make_loop(tmp_path: Path) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return AgentLoop(
        bus=bus, provider=provider, workspace=tmp_path,
        model="test-model", memory_window=50,
    )


class _InjectCallbacks(DefaultCallbacks):
    """Callbacks that return pre-queued inject messages."""

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self.messages_received: list[dict] = []

    async def check_user_input(self):
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def on_message(self, message: dict) -> None:
        self.messages_received.append(message)

    def put(self, msg):
        self._queue.put_nowait(msg)


class TestInjectDrainAll:
    """Verify that the inject checkpoint drains ALL queued messages."""

    @pytest.mark.asyncio
    async def test_multiple_messages_drained_after_tool(self, tmp_path: Path) -> None:
        """When 3 messages are queued during tool execution, all 3 should be consumed."""
        loop = _make_loop(tmp_path)
        loop.tools.get_definitions = MagicMock(return_value=[
            {"type": "function", "function": {"name": "exec", "description": "run", "parameters": {}}}
        ])

        callbacks = _InjectCallbacks()

        call_count = 0

        async def mock_chat(model, messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: LLM requests a tool call
                return LLMResponse(
                    content=None,
                    tool_calls=[ToolCallRequest(id="tc1", name="exec", arguments={"command": "echo hi"})],
                    finish_reason="tool_calls",
                )
            else:
                # Second call: LLM gives final response (after seeing injected messages)
                return LLMResponse(
                    content="I see the injected messages.",
                    tool_calls=[],
                    finish_reason="stop",
                )

        loop.provider.chat = mock_chat

        # Mock tool execution — during which 3 messages arrive
        original_execute = loop.tools.execute

        async def mock_execute(name, args):
            # Simulate 3 messages arriving during tool execution
            callbacks.put({"role": "user", "content": "[Message from session subagent:sa1]\nResult 1"})
            callbacks.put({"role": "user", "content": "[Message from session subagent:sa2]\nResult 2"})
            callbacks.put("User inject: please also do X")
            return "tool output"

        loop.tools.execute = mock_execute

        msg = InboundMessage(
            channel="test", sender_id="u1", chat_id="drain1", content="start"
        )
        result = await loop._process_message(msg, callbacks=callbacks)

        assert result is not None

        # All 3 injected messages should appear in the session
        session = loop.sessions.get_or_create("test:drain1")
        user_msgs = [m for m in session.messages if m.get("role") == "user"]

        # user_msgs: [original "start", inject1, inject2, inject3]
        assert len(user_msgs) == 4, f"Expected 4 user messages, got {len(user_msgs)}: {[m['content'][:50] for m in user_msgs]}"
        assert "subagent:sa1" in user_msgs[1]["content"]
        assert "subagent:sa2" in user_msgs[2]["content"]
        assert "please also do X" in user_msgs[3]["content"]

    @pytest.mark.asyncio
    async def test_single_inject_still_works(self, tmp_path: Path) -> None:
        """Single inject message is still consumed correctly (regression)."""
        loop = _make_loop(tmp_path)
        loop.tools.get_definitions = MagicMock(return_value=[
            {"type": "function", "function": {"name": "exec", "description": "run", "parameters": {}}}
        ])

        callbacks = _InjectCallbacks()
        call_count = 0

        async def mock_chat(model, messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    content=None,
                    tool_calls=[ToolCallRequest(id="tc1", name="exec", arguments={"command": "echo"})],
                    finish_reason="tool_calls",
                )
            else:
                return LLMResponse(content="Done.", tool_calls=[], finish_reason="stop")

        loop.provider.chat = mock_chat

        async def mock_execute(name, args):
            callbacks.put("single inject message")
            return "ok"

        loop.tools.execute = mock_execute

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="single1", content="go")
        result = await loop._process_message(msg, callbacks=callbacks)

        session = loop.sessions.get_or_create("test:single1")
        user_msgs = [m for m in session.messages if m.get("role") == "user"]
        assert len(user_msgs) == 2  # original + 1 inject
        assert "single inject message" in user_msgs[1]["content"]


class TestLateInjectOnFinalResponse:
    """Verify that messages arriving during the LLM's final response are not lost."""

    @pytest.mark.asyncio
    async def test_inject_during_final_response_triggers_continuation(self, tmp_path: Path) -> None:
        """If a message arrives while LLM generates final text, the loop should continue."""
        loop = _make_loop(tmp_path)
        loop.tools.get_definitions = MagicMock(return_value=[
            {"type": "function", "function": {"name": "exec", "description": "run", "parameters": {}}}
        ])

        callbacks = _InjectCallbacks()
        call_count = 0

        async def mock_chat(model, messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: tool call
                return LLMResponse(
                    content=None,
                    tool_calls=[ToolCallRequest(id="tc1", name="exec", arguments={"command": "echo"})],
                    finish_reason="tool_calls",
                )
            elif call_count == 2:
                # Second call: "final" response — but a message will arrive
                # We simulate this by putting a message into the queue
                # right before returning the final response.
                # In reality, this happens during the LLM streaming.
                callbacks.put("Late user message: what about Y?")
                return LLMResponse(
                    content="Here is my summary.",
                    tool_calls=[],
                    finish_reason="stop",
                )
            else:
                # Third call: LLM responds to the late inject
                return LLMResponse(
                    content="Regarding Y, here is my answer.",
                    tool_calls=[],
                    finish_reason="stop",
                )

        loop.provider.chat = mock_chat

        async def mock_execute(name, args):
            return "ok"

        loop.tools.execute = mock_execute

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="late1", content="start")
        result = await loop._process_message(msg, callbacks=callbacks)

        # The LLM should have been called 3 times:
        # 1. tool call, 2. "final" response, 3. response to late inject
        assert call_count == 3, f"Expected 3 LLM calls, got {call_count}"

        # The final result should be from the 3rd call
        assert "Regarding Y" in result.content

        # Session should contain the late inject message
        session = loop.sessions.get_or_create("test:late1")
        user_msgs = [m for m in session.messages if m.get("role") == "user"]
        assert len(user_msgs) == 2  # original + late inject
        assert "what about Y" in user_msgs[1]["content"]

    @pytest.mark.asyncio
    async def test_no_inject_on_final_response_exits_normally(self, tmp_path: Path) -> None:
        """When no inject arrives during final response, loop exits normally."""
        loop = _make_loop(tmp_path)
        loop.tools.get_definitions = MagicMock(return_value=[])

        callbacks = _InjectCallbacks()

        async def mock_chat(model, messages, tools=None, **kwargs):
            return LLMResponse(content="Simple answer.", tool_calls=[], finish_reason="stop")

        loop.provider.chat = mock_chat

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="noinject1", content="hello")
        result = await loop._process_message(msg, callbacks=callbacks)

        assert result is not None
        assert result.content == "Simple answer."


class TestStructuredInjectDrain:
    """Verify drain works with both dict and string inject messages."""

    @pytest.mark.asyncio
    async def test_mixed_dict_and_string_inject(self, tmp_path: Path) -> None:
        """Mix of dict (subagent) and string (user) injects are all consumed."""
        loop = _make_loop(tmp_path)
        loop.tools.get_definitions = MagicMock(return_value=[
            {"type": "function", "function": {"name": "exec", "description": "run", "parameters": {}}}
        ])

        callbacks = _InjectCallbacks()
        call_count = 0

        async def mock_chat(model, messages, tools=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMResponse(
                    content=None,
                    tool_calls=[ToolCallRequest(id="tc1", name="exec", arguments={"command": "echo"})],
                    finish_reason="tool_calls",
                )
            else:
                return LLMResponse(content="All processed.", tool_calls=[], finish_reason="stop")

        loop.provider.chat = mock_chat

        async def mock_execute(name, args):
            # Dict inject (subagent result)
            callbacks.put({"role": "user", "content": "[Message from session sub:abc]\nSubagent done."})
            # String inject (user message)
            callbacks.put("User says: check this too")
            return "ok"

        loop.tools.execute = mock_execute

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="mixed1", content="go")
        result = await loop._process_message(msg, callbacks=callbacks)

        session = loop.sessions.get_or_create("test:mixed1")
        user_msgs = [m for m in session.messages if m.get("role") == "user"]
        assert len(user_msgs) == 3  # original + dict inject + string inject
        assert "Subagent done" in user_msgs[1]["content"]
        assert "check this too" in user_msgs[2]["content"]
