"""Tests for the precise 3-breakpoint cache_control strategy (§32).

Verifies that _apply_cache_control() places breakpoints only on:
  #1  tools[-1]
  #2  messages[0] (system prompt)
  #3  messages[-1] (conversation tail)
and does NOT add breakpoints to intermediate system messages.
"""

from __future__ import annotations

import pytest

from nanobot.providers.litellm_provider import LiteLLMProvider


def _make_provider() -> LiteLLMProvider:
    """Create a minimal LiteLLMProvider for testing."""
    return LiteLLMProvider.__new__(LiteLLMProvider)


CC = {"type": "ephemeral"}


class TestApplyCacheControl:
    """Tests for _apply_cache_control()."""

    def test_basic_3_breakpoints(self):
        """All 3 breakpoints placed correctly on a typical conversation."""
        p = _make_provider()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "What is 2+2?"},
        ]
        tools = [
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "write_file"}},
        ]
        new_msgs, new_tools = p._apply_cache_control(messages, tools)

        # Breakpoint #2: messages[0] system prompt
        assert new_msgs[0]["content"] == [
            {"type": "text", "text": "You are helpful.", "cache_control": CC}
        ]

        # Middle messages: no cache_control
        assert new_msgs[1] == {"role": "user", "content": "Hello"}
        assert new_msgs[2] == {"role": "assistant", "content": "Hi there!"}

        # Breakpoint #3: last message
        assert new_msgs[3]["content"] == [
            {"type": "text", "text": "What is 2+2?", "cache_control": CC}
        ]

        # Breakpoint #1: tools[-1]
        assert "cache_control" not in new_tools[0]
        assert new_tools[1]["cache_control"] == CC

    def test_no_breakpoint_on_intermediate_system_messages(self):
        """Intermediate system messages (subagent results, budget alerts) get NO breakpoint."""
        p = _make_provider()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "spawn a task"},
            {"role": "assistant", "content": "OK spawning..."},
            {"role": "system", "content": "[Subagent Result] Task completed."},  # injected
            {"role": "system", "content": "[Budget Alert] 80% used."},  # injected
            {"role": "user", "content": "Thanks"},
        ]
        new_msgs, _ = p._apply_cache_control(messages, None)

        # messages[0] gets breakpoint
        assert isinstance(new_msgs[0]["content"], list)
        assert new_msgs[0]["content"][0].get("cache_control") == CC

        # Intermediate system messages do NOT get breakpoint
        assert new_msgs[3]["content"] == "[Subagent Result] Task completed."
        assert new_msgs[4]["content"] == "[Budget Alert] 80% used."

        # Last message gets breakpoint
        assert isinstance(new_msgs[5]["content"], list)
        assert new_msgs[5]["content"][0].get("cache_control") == CC

    def test_single_message_no_breakpoint_3(self):
        """With only 1 message, only breakpoint #2 (system prompt) is applied, not #3."""
        p = _make_provider()
        messages = [{"role": "system", "content": "You are helpful."}]
        new_msgs, _ = p._apply_cache_control(messages, None)

        # Only breakpoint #2
        assert isinstance(new_msgs[0]["content"], list)
        assert new_msgs[0]["content"][0].get("cache_control") == CC
        # Should have exactly 1 message
        assert len(new_msgs) == 1

    def test_no_tools_no_breakpoint_1(self):
        """With no tools, breakpoint #1 is skipped."""
        p = _make_provider()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        new_msgs, new_tools = p._apply_cache_control(messages, None)
        assert new_tools is None

    def test_empty_tools_list(self):
        """Empty tools list should not crash."""
        p = _make_provider()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        new_msgs, new_tools = p._apply_cache_control(messages, [])
        assert new_tools == []

    def test_system_prompt_with_list_content(self):
        """System prompt with list content gets breakpoint on last block."""
        p = _make_provider()
        messages = [
            {"role": "system", "content": [
                {"type": "text", "text": "Part 1"},
                {"type": "text", "text": "Part 2"},
            ]},
            {"role": "user", "content": "Hello"},
        ]
        new_msgs, _ = p._apply_cache_control(messages, None)

        # Breakpoint on last content block of messages[0]
        assert "cache_control" not in new_msgs[0]["content"][0]
        assert new_msgs[0]["content"][1].get("cache_control") == CC

    def test_last_message_with_list_content(self):
        """Last message with list content gets breakpoint on last block."""
        p = _make_provider()
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": [
                {"type": "text", "text": "Part A"},
                {"type": "image_url", "image_url": {"url": "data:..."}},
            ]},
        ]
        new_msgs, _ = p._apply_cache_control(messages, None)

        # Last content block of last message
        assert "cache_control" not in new_msgs[1]["content"][0]
        assert new_msgs[1]["content"][1].get("cache_control") == CC

    def test_last_message_empty_content_skipped(self):
        """Last message with empty content is not modified."""
        p = _make_provider()
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": ""},
        ]
        new_msgs, _ = p._apply_cache_control(messages, None)
        # Empty string content — breakpoint #3 skipped
        assert new_msgs[1]["content"] == ""

    def test_original_messages_not_mutated(self):
        """Original messages and tools should not be mutated."""
        p = _make_provider()
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Hello"},
        ]
        tools = [{"type": "function", "function": {"name": "tool1"}}]

        orig_msg0_content = messages[0]["content"]
        orig_tool0 = dict(tools[0])

        p._apply_cache_control(messages, tools)

        # Originals unchanged
        assert messages[0]["content"] == orig_msg0_content
        assert isinstance(messages[0]["content"], str)
        assert "cache_control" not in tools[0]

    def test_max_breakpoint_count(self):
        """At most 3 breakpoints are placed (well under Anthropic's 4 limit)."""
        p = _make_provider()
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "system", "content": "Extra system 1"},
            {"role": "system", "content": "Extra system 2"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "Bye"},
        ]
        tools = [
            {"type": "function", "function": {"name": "t1"}},
            {"type": "function", "function": {"name": "t2"}},
            {"type": "function", "function": {"name": "t3"}},
        ]
        new_msgs, new_tools = p._apply_cache_control(messages, tools)

        # Count total breakpoints
        bp_count = 0
        for msg in new_msgs:
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and "cache_control" in block:
                        bp_count += 1
            elif isinstance(content, str):
                pass  # no breakpoint on plain strings
        for tool in (new_tools or []):
            if "cache_control" in tool:
                bp_count += 1

        assert bp_count == 3  # exactly 3: tools[-1], messages[0], messages[-1]

        # Intermediate system messages: no breakpoint
        assert new_msgs[1]["content"] == "Extra system 1"
        assert new_msgs[2]["content"] == "Extra system 2"
