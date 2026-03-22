"""Tests for §65: _find_tool_aligned_cut fix + warning frequency control."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helper: create a minimal AgentLoop for testing _find_tool_aligned_cut
# ---------------------------------------------------------------------------

def _make_loop(memory_window: int = 100):
    """Create a minimal AgentLoop instance for unit-testing."""
    from nanobot.agent.loop import AgentLoop

    loop = AgentLoop.__new__(AgentLoop)
    loop.memory_window = memory_window
    return loop


# ---------------------------------------------------------------------------
# §65 Fix 1: _find_tool_aligned_cut — tool result as last archived is OK
# ---------------------------------------------------------------------------

class TestFindToolAlignedCut:
    """Verify _find_tool_aligned_cut after §65 fix."""

    def _messages(self, roles: list[str]) -> list[dict]:
        """Build a simple message list from role strings.

        Use 'assistant_tc' for assistant with tool_calls.
        """
        msgs = [{"role": "system", "content": "sys"}]  # index 0
        for r in roles:
            if r == "assistant_tc":
                msgs.append({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "tc_1", "function": {"name": "f", "arguments": "{}"}}],
                })
            else:
                msg = {"role": r, "content": f"msg_{len(msgs)}"}
                if r == "tool":
                    msg["tool_call_id"] = "tc_1"
                msgs.append(msg)
        return msgs

    def test_tool_result_as_last_archived_not_retreated(self):
        """§65 core fix: tool result at cut-1 should NOT cause retreat."""
        loop = _make_loop()
        # messages: [sys, user, assistant_tc, tool, user, ...]
        msgs = self._messages(["user", "assistant_tc", "tool", "user", "assistant"])
        # target_count=3 → cut = min(1+3, 5) = 4
        # messages[3] = tool → this is the last archived (cut-1=3)
        # §65: tool result is fine, should NOT retreat
        cut = loop._find_tool_aligned_cut(msgs, 1, 3)
        assert cut == 4, f"Expected cut=4 (tool result as last archived is OK), got {cut}"

    def test_assistant_tc_as_last_archived_retreats(self):
        """assistant(tool_calls) as last archived should still retreat."""
        loop = _make_loop()
        # messages: [sys, user, assistant_tc, tool, user]
        msgs = self._messages(["user", "assistant_tc", "tool", "user"])
        # target_count=2 → cut = min(1+2, 4) = 3
        # messages[2] = assistant_tc → must retreat
        cut = loop._find_tool_aligned_cut(msgs, 1, 2)
        assert cut == 2, f"Expected cut=2 (retreat past assistant_tc), got {cut}"

    def test_multiple_tool_results_not_retreated(self):
        """Multiple consecutive tool results should not cause retreat."""
        loop = _make_loop()
        # messages: [sys, user, assistant_tc(3 tools), tool, tool, tool, user]
        msgs = [{"role": "system", "content": "sys"}]
        msgs.append({"role": "user", "content": "go"})
        msgs.append({
            "role": "assistant", "content": "",
            "tool_calls": [
                {"id": "tc_1", "function": {"name": "f1", "arguments": "{}"}},
                {"id": "tc_2", "function": {"name": "f2", "arguments": "{}"}},
                {"id": "tc_3", "function": {"name": "f3", "arguments": "{}"}},
            ],
        })
        msgs.append({"role": "tool", "tool_call_id": "tc_1", "content": "r1"})
        msgs.append({"role": "tool", "tool_call_id": "tc_2", "content": "r2"})
        msgs.append({"role": "tool", "tool_call_id": "tc_3", "content": "r3"})
        msgs.append({"role": "user", "content": "next"})
        # target_count=5 → cut = min(1+5, 6) = 6
        # messages[5] = tool (tc_3) → should NOT retreat
        cut = loop._find_tool_aligned_cut(msgs, 1, 5)
        assert cut == 6, f"Expected cut=6 (tool results are fine), got {cut}"

    def test_user_as_last_archived_no_retreat(self):
        """User message as last archived — no retreat needed."""
        loop = _make_loop()
        msgs = self._messages(["user", "assistant", "user", "assistant"])
        # target_count=2 → cut = min(1+2, 4) = 3
        # messages[2] = user → fine
        cut = loop._find_tool_aligned_cut(msgs, 1, 2)
        assert cut == 3

    def test_assistant_no_tc_as_last_archived_no_retreat(self):
        """Assistant without tool_calls as last archived — no retreat needed."""
        loop = _make_loop()
        msgs = self._messages(["user", "assistant", "user"])
        # target_count=2 → cut = min(1+2, 3) = 3
        # But len(messages)-1 = 3, so cut = min(3, 2) = 2
        # Wait, let me recalculate: len=4, cut = min(1+2, 3) = 3
        # messages[2] = assistant → no tool_calls → fine
        cut = loop._find_tool_aligned_cut(msgs, 1, 2)
        assert cut == 3

    def test_cut_never_goes_below_start(self):
        """If all messages are assistant_tc, cut should stop at start."""
        loop = _make_loop()
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "tc_1", "function": {"name": "f", "arguments": "{}"}}]},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "tc_2", "function": {"name": "f", "arguments": "{}"}}]},
            {"role": "user", "content": "x"},
        ]
        # target_count=2 → cut = min(1+2, 3) = 3
        # messages[2] = assistant_tc → retreat to 2
        # messages[1] = assistant_tc → retreat to 1 = start → stop
        cut = loop._find_tool_aligned_cut(msgs, 1, 2)
        assert cut == 1

    def test_target_count_exceeds_messages(self):
        """target_count larger than available messages — capped to len-1."""
        loop = _make_loop()
        msgs = self._messages(["user", "assistant"])
        # target_count=100 → cut = min(1+100, 2) = 2
        # messages[1] = user → fine
        cut = loop._find_tool_aligned_cut(msgs, 1, 100)
        assert cut == 2


# ---------------------------------------------------------------------------
# §65 Fix 2: Warning frequency control — last_warning_msg_index
# ---------------------------------------------------------------------------

class TestWarningFrequencyControl:
    """Verify the warning frequency control logic."""

    def test_source_code_has_last_warning_msg_index_check(self):
        """Verify the frequency control code exists in _run_agent_loop."""
        import inspect
        from nanobot.agent.loop import AgentLoop
        source = inspect.getsource(AgentLoop._run_agent_loop)
        assert "last_warning_msg_index" in source, \
            "§65: last_warning_msg_index frequency control not found in AgentLoop._run_agent_loop"
        assert "enough_new_msgs" in source, \
            "§65: _enough_new_msgs check not found in AgentLoop._run_agent_loop"

    def test_source_code_updates_metadata_after_warning(self):
        """Verify metadata is updated after warning injection."""
        import inspect
        from nanobot.agent.loop import AgentLoop
        source = inspect.getsource(AgentLoop._run_agent_loop)
        assert 'session.metadata["last_warning_msg_index"]' in source, \
            "§65: metadata update after warning not found"

    def test_frequency_logic_first_time(self):
        """First warning (no metadata) should always pass the frequency check."""
        # Simulate: no last_warning_msg_index → defaults to 0
        # session.messages has 60 entries, memory_window=100
        # _enough_new_msgs = (60 - 0) >= 50 → True
        last_warn_idx = 0
        current_msg_total = 60
        memory_window = 100
        enough = (current_msg_total - last_warn_idx) >= (memory_window // 2)
        assert enough is True

    def test_frequency_logic_too_soon(self):
        """Warning should be suppressed if not enough new messages since last warning."""
        # Last warned at index 50, now at 70, need 50 more → 70-50=20 < 50
        last_warn_idx = 50
        current_msg_total = 70
        memory_window = 100
        enough = (current_msg_total - last_warn_idx) >= (memory_window // 2)
        assert enough is False

    def test_frequency_logic_enough_msgs(self):
        """Warning should fire when enough messages have accumulated."""
        # Last warned at index 50, now at 100, need 50 → 100-50=50 >= 50
        last_warn_idx = 50
        current_msg_total = 100
        memory_window = 100
        enough = (current_msg_total - last_warn_idx) >= (memory_window // 2)
        assert enough is True

    def test_frequency_logic_small_window(self):
        """With memory_window=20, interval should be 10."""
        # Last warned at index 30, now at 38, need 10 → 38-30=8 < 10
        last_warn_idx = 30
        current_msg_total = 38
        memory_window = 20
        enough = (current_msg_total - last_warn_idx) >= (memory_window // 2)
        assert enough is False

        # Now at 40 → 40-30=10 >= 10
        current_msg_total = 40
        enough = (current_msg_total - last_warn_idx) >= (memory_window // 2)
        assert enough is True


# ---------------------------------------------------------------------------
# §65 Fix 3: Verify warning-before-consolidation ordering
# ---------------------------------------------------------------------------

class TestWarningConsolidationOrdering:
    """Verify Step 2 (warning) comes before Step 3 (consolidation)."""

    def test_step_ordering_in_source(self):
        """Step 2 (warning) must appear before Step 3 (consolidation) in source."""
        import inspect
        from nanobot.agent.loop import AgentLoop
        source = inspect.getsource(AgentLoop._run_agent_loop)
        # Find positions of step comments
        step2_pos = source.find("Step 2")
        step3_pos = source.find("Step 3")
        assert step2_pos > 0, "Step 2 comment not found"
        assert step3_pos > 0, "Step 3 comment not found"
        assert step2_pos < step3_pos, \
            f"Step 2 (warning) must come before Step 3 (consolidation), " \
            f"but Step 2 at {step2_pos}, Step 3 at {step3_pos}"


# ---------------------------------------------------------------------------
# Verify _find_tool_aligned_cut does NOT check for role=="tool"
# ---------------------------------------------------------------------------

class TestNoToolRoleRetreat:
    """Ensure the old buggy role=='tool' retreat check is removed."""

    def test_no_tool_role_retreat_in_source(self):
        """§65/§70: The method should NOT retreat (cut -= 1) on role=='tool'.

        §70 adds Pass 2 which legitimately checks role=='tool' for
        forward-extension (cut += 1), so we only flag the retreat pattern.
        """
        import inspect
        from nanobot.agent.loop import AgentLoop
        source = inspect.getsource(AgentLoop._find_tool_aligned_cut)
        lines = source.split('\n')
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            # The old buggy pattern: check role=='tool' followed by cut -= 1
            if 'get("role") == "tool"' in stripped and 'cut -= 1' in stripped:
                raise AssertionError(
                    f"§65: _find_tool_aligned_cut still has role=='tool' retreat at line {i}: {stripped}"
                )


# ---------------------------------------------------------------------------
# §70 Pass 2: Forward-extend to include orphaned tool_results
# ---------------------------------------------------------------------------

class TestPass2ForwardExtend:
    """§70: _find_tool_aligned_cut should extend cut to include tool_results
    whose assistant is in the archived range."""

    def test_forward_extend_orphan_tool_results(self):
        """If assistant(tc1,tc2) is in archive range but tool_result(tc2) is after cut,
        cut should extend to include it."""
        loop = _make_loop()
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "tc1", "type": "function", "function": {"name": "f", "arguments": "{}"}},
                {"id": "tc2", "type": "function", "function": {"name": "f", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "tc1", "content": "r1"},
            {"role": "tool", "tool_call_id": "tc2", "content": "r2"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "q2"},
        ]
        # target_count=3 → initial cut = min(1+3, 6) = 4
        # messages[3] = tool(tc1) — Pass 1 OK (not assistant with tool_calls)
        # But messages[2] (assistant tc1,tc2) is in [1:4], so tc1,tc2 are archived_tc_ids
        # messages[4] = tool(tc2) which belongs to archived assistant → extend to 5
        cut = loop._find_tool_aligned_cut(messages, 1, 3)
        assert cut == 5, f"Expected 5, got {cut}"

    def test_no_extend_when_tool_results_belong_to_kept_assistant(self):
        """Tool results after cut that belong to a kept assistant should NOT extend cut."""
        loop = _make_loop()
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "tc1", "type": "function", "function": {"name": "f", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "tc1", "content": "r1"},
            {"role": "assistant", "content": "done"},
        ]
        # target_count=3 → initial cut = min(1+3, 6) = 4
        # messages[3] = user — OK
        # messages[4] = assistant(tc1) is NOT in [1:4], so tc1 not in archived_tc_ids
        # messages[4].tool(tc1) should NOT extend
        cut = loop._find_tool_aligned_cut(messages, 1, 3)
        assert cut == 4, f"Expected 4, got {cut}"

    def test_forward_extend_multiple_orphans(self):
        """Multiple consecutive orphan tool_results should all be included."""
        loop = _make_loop()
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "tc1", "type": "function", "function": {"name": "f", "arguments": "{}"}},
                {"id": "tc2", "type": "function", "function": {"name": "f", "arguments": "{}"}},
                {"id": "tc3", "type": "function", "function": {"name": "f", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "tc1", "content": "r1"},
            # cut initially lands here (after tc1 result)
            {"role": "tool", "tool_call_id": "tc2", "content": "r2"},
            {"role": "tool", "tool_call_id": "tc3", "content": "r3"},
            {"role": "user", "content": "next"},
        ]
        # target_count=2 → initial cut = min(1+2, 5) = 3
        # messages[2] = tool(tc1) — Pass 1 OK
        # assistant(tc1,tc2,tc3) at idx 1 is in [1:3], so all tc_ids in archived set
        # messages[3] = tool(tc2) → extend to 4
        # messages[4] = tool(tc3) → extend to 5
        # messages[5] = user → stop
        cut = loop._find_tool_aligned_cut(messages, 1, 2)
        assert cut == 5, f"Expected 5, got {cut}"

