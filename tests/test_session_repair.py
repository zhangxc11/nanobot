"""Tests for Session.get_history() — boundary alignment and repair logic.

Covers:
- Orphaned tool_result at start (existing behaviour)
- Incomplete tool-call chain at end (new Phase 8 fix)
- Error artefact stripping (new Phase 8 fix)
- Mixed scenarios
"""

import pytest
from nanobot.session.manager import Session


def _msg(role, content="", **kw):
    """Helper to build a message dict."""
    m = {"role": role, "content": content}
    m.update(kw)
    return m


def _assistant_with_tools(content, tool_ids):
    """Build an assistant message with tool_calls."""
    tool_calls = [
        {"id": tid, "type": "function", "function": {"name": "exec", "arguments": "{}"}}
        for tid in tool_ids
    ]
    return _msg("assistant", content, tool_calls=tool_calls)


def _tool_result(tool_call_id, result="ok"):
    """Build a tool result message."""
    return _msg("tool", result, tool_call_id=tool_call_id, name="exec")


class TestGetHistoryStartBoundary:
    """Existing behaviour: orphaned tool_result at start is trimmed."""

    def test_starts_with_user(self):
        s = Session(key="test")
        s.messages = [
            _msg("user", "hello"),
            _msg("assistant", "hi"),
        ]
        h = s.get_history()
        assert h[0]["role"] == "user"
        assert len(h) == 2

    def test_orphaned_tool_at_start(self):
        s = Session(key="test")
        s.messages = [
            _tool_result("t1"),
            _tool_result("t2"),
            _msg("user", "hello"),
            _msg("assistant", "hi"),
        ]
        h = s.get_history()
        assert h[0]["role"] == "user"
        assert len(h) == 2


class TestGetHistoryEndBoundary:
    """New Phase 8 fix: incomplete tool-call chain at end is trimmed."""

    def test_complete_chain_preserved(self):
        """A complete tool-call chain should be fully preserved."""
        s = Session(key="test")
        s.messages = [
            _msg("user", "do something"),
            _assistant_with_tools("ok", ["t1", "t2"]),
            _tool_result("t1"),
            _tool_result("t2"),
            _msg("assistant", "done"),
        ]
        h = s.get_history()
        assert len(h) == 5
        assert h[-1]["content"] == "done"

    def test_missing_all_tool_results(self):
        """Assistant with tool_calls but zero tool_results → trimmed."""
        s = Session(key="test")
        s.messages = [
            _msg("user", "do something"),
            _msg("assistant", "first reply"),
            _msg("user", "restart gateway"),
            _assistant_with_tools("killing", ["t_kill"]),
            # crash — no tool_result
        ]
        h = s.get_history()
        # Should trim the last assistant+tool_calls, keep up to "restart gateway"
        assert len(h) == 3
        assert h[-1]["role"] == "user"
        assert h[-1]["content"] == "restart gateway"

    def test_missing_partial_tool_results(self):
        """Assistant with 2 tool_calls but only 1 tool_result → trimmed."""
        s = Session(key="test")
        s.messages = [
            _msg("user", "do two things"),
            _assistant_with_tools("ok", ["t1", "t2"]),
            _tool_result("t1"),
            # t2 result missing — crash
        ]
        h = s.get_history()
        # Entire chain trimmed (assistant + partial results)
        assert len(h) == 1
        assert h[0]["role"] == "user"

    def test_user_after_incomplete_chain(self):
        """User message after incomplete chain — chain removed, user kept."""
        s = Session(key="test")
        s.messages = [
            _msg("user", "hello"),
            _msg("assistant", "hi"),
            _msg("user", "restart"),
            _assistant_with_tools("killing", ["t_kill"]),
            # crash — no tool_result
            _msg("user", "is it back?"),
        ]
        h = s.get_history()
        # The incomplete chain (assistant+tool_calls) is removed
        # But the user messages before and after are kept
        # [user "hello", assistant "hi", user "restart", user "is it back?"]
        assert len(h) == 4
        assert h[-1]["content"] == "is it back?"

    def test_multiple_incomplete_chains(self):
        """Multiple incomplete chains stacked — all trimmed."""
        s = Session(key="test")
        s.messages = [
            _msg("user", "hello"),
            _msg("assistant", "hi"),
            _msg("user", "try 1"),
            _assistant_with_tools("attempt 1", ["t1"]),
            # crash
            _msg("user", "try 2"),
            _assistant_with_tools("attempt 2", ["t2"]),
            # crash again
        ]
        h = s.get_history()
        # Second incomplete chain trimmed → [user, assistant, user, incomplete, user]
        # First incomplete chain then trimmed → [user "hello", assistant "hi", user "try 1", user "try 2"]
        assert h[-1]["role"] == "user"
        # All tool_calls assistants should be gone
        for m in h:
            assert "tool_calls" not in m


class TestGetHistoryErrorStripping:
    """New Phase 8 fix: error artefact messages are stripped."""

    def test_error_messages_stripped(self):
        """Messages starting with 'Error calling LLM:' are removed."""
        s = Session(key="test")
        s.messages = [
            _msg("user", "hello"),
            _assistant_with_tools("killing", ["t_kill"]),
            # crash, no tool_result
            _msg("user", "is it back?"),
            _msg("assistant", "Error calling LLM: litellm.BadRequestError: ..."),
            _msg("user", "try again"),
            _msg("assistant", "Error calling LLM: litellm.BadRequestError: ..."),
        ]
        h = s.get_history()
        # Error messages should be stripped
        for m in h:
            if m["role"] == "assistant":
                assert not m["content"].startswith("Error calling LLM:")
        # Should have: user "hello", user "is it back?", user "try again"
        # (assistant+tool_calls trimmed, error assistants stripped)
        assert len(h) == 3
        assert all(m["role"] == "user" for m in h)


class TestGetHistoryRealWorldScenario:
    """Test the exact scenario from the feishu session crash."""

    def test_feishu_crash_scenario(self):
        """Simulates the real crash: kill command with no tool_result + error messages."""
        s = Session(key="feishu:ou_xxx")
        s.messages = [
            # Normal conversation
            _msg("user", "发文件"),
            _assistant_with_tools("好的", ["t_exec"]),
            _tool_result("t_exec", "ok"),
            _assistant_with_tools("发送", ["t_msg"]),
            _tool_result("t_msg", "sent"),
            _msg("assistant", "发送成功了"),
            # User asks to restart
            _msg("user", "重启nanobot gateway吧"),
            _assistant_with_tools("找进程", ["t_ps"]),
            _tool_result("t_ps", "PID 72027"),
            _assistant_with_tools("试 & 命令", ["t_bg"]),
            _tool_result("t_bg", "Error: & blocked"),
            _assistant_with_tools("直接 kill", ["t_kill"]),
            # CRASH — gateway killed itself, no tool_result
            # After restart:
            _msg("user", "恢复了嘛"),
            _msg("assistant", "Error calling LLM: litellm.BadRequestError: ..."),
            _msg("user", "再试试"),
            _msg("assistant", "Error calling LLM: litellm.BadRequestError: ..."),
        ]
        h = s.get_history()
        # Should have valid history without the broken chain or error messages
        for m in h:
            if m["role"] == "assistant":
                assert not m["content"].startswith("Error calling LLM:")
                if m.get("tool_calls"):
                    # Every assistant with tool_calls should have all results
                    tc_ids = {tc["id"] for tc in m["tool_calls"]}
                    idx = h.index(m)
                    result_ids = set()
                    for j in range(idx + 1, len(h)):
                        if h[j].get("role") == "tool":
                            result_ids.add(h[j].get("tool_call_id"))
                        else:
                            break
                    assert tc_ids == result_ids, f"Incomplete chain at {idx}"

        # The "恢复了嘛" and "再试试" user messages should still be present
        user_msgs = [m["content"] for m in h if m["role"] == "user"]
        assert "恢复了嘛" in user_msgs
        assert "再试试" in user_msgs
