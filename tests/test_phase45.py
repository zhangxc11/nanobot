"""Tests for §48: logging enhancement + system marker fix + budget alert.

Covers:
  1. budget.py — build_budget_alert() + budget_alert_threshold()
  2. detail_logger — provider field
  3. subagent announce — new message format with closing tags
  4. SubagentManager detail_logger plumbing
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nanobot.agent.budget import budget_alert_threshold, build_budget_alert
from nanobot.usage.detail_logger import LLMDetailLogger


# ── 1. budget.py ────────────────────────────────────────────────────────────


class TestBudgetAlertThreshold:
    """budget_alert_threshold() from the shared budget module."""

    def test_large_iterations(self):
        assert budget_alert_threshold(40) == 10
        assert budget_alert_threshold(100) == 10
        assert budget_alert_threshold(20) == 10

    def test_small_iterations(self):
        assert budget_alert_threshold(16) == 4
        assert budget_alert_threshold(12) == 3

    def test_minimum(self):
        assert budget_alert_threshold(8) == 3
        assert budget_alert_threshold(4) == 3
        assert budget_alert_threshold(3) == 3


class TestBuildBudgetAlert:
    """build_budget_alert() returns correct text and logs warning."""

    def test_contains_current_turn_budget(self):
        msg = build_budget_alert(10, 40, "test:session")
        assert "Current Turn Budget" in msg
        assert "10" in msg
        assert "40" in msg

    def test_contains_prioritize(self):
        msg = build_budget_alert(5, 20)
        assert "Prioritize" in msg

    def test_logs_warning(self):
        with patch("nanobot.agent.budget.logger") as mock_logger:
            build_budget_alert(10, 40, "test:s1")
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args
            assert "10" in str(call_args)
            assert "40" in str(call_args)
            assert "test:s1" in str(call_args)

    def test_empty_session_key(self):
        msg = build_budget_alert(3, 10)
        assert "Current Turn Budget" in msg


# ── 2. detail_logger — provider field ────────────────────────────────────────


class TestDetailLoggerProvider:
    """LLMDetailLogger.log_call() records provider field."""

    def test_provider_field_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = LLMDetailLogger(log_dir=tmpdir)
            result = logger.log_call(
                session_key="test:prov",
                model="claude-sonnet-4-20250514",
                iteration=1,
                messages=[{"role": "user", "content": "hello"}],
                response_content="hi",
                provider="anthropic-main",
            )
            assert result is not None
            file_path = Path(tmpdir) / result[0]
            with open(file_path) as f:
                record = json.loads(f.readline())
            assert record["provider"] == "anthropic-main"

    def test_provider_field_default_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = LLMDetailLogger(log_dir=tmpdir)
            result = logger.log_call(
                session_key="test:noprov",
                model="test-model",
                iteration=1,
                messages=[{"role": "user", "content": "hello"}],
                response_content="hi",
            )
            assert result is not None
            file_path = Path(tmpdir) / result[0]
            with open(file_path) as f:
                record = json.loads(f.readline())
            assert record["provider"] == ""

    def test_provider_field_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = LLMDetailLogger(log_dir=tmpdir)
            result = logger.log_call(
                session_key="test:none",
                model="test-model",
                iteration=1,
                messages=[{"role": "user", "content": "hello"}],
                response_content="hi",
                provider=None,
            )
            assert result is not None
            file_path = Path(tmpdir) / result[0]
            with open(file_path) as f:
                record = json.loads(f.readline())
            assert record["provider"] == ""


# ── 3. subagent announce — new message format ────────────────────────────────


class TestSubagentAnnounceFormat:
    """Test the new announce message format with closing tags."""

    def _build_announce(self, final_text: str, label: str = "test-label",
                        task_id: str = "abc123", status: str = "ok",
                        current_iteration: int = 10, max_iterations: int = 30) -> str:
        """Replicate the announce_content construction from subagent.py."""
        status_text = "completed successfully" if status == "ok" else "failed"
        ft = final_text.strip() if final_text else ""
        if not ft:
            ft = "(Subagent completed with no output)"

        return f"""{ft}

<!-- nanobot:system -->
[Subagent Result Notification]
- Task ID: `{task_id}`
- Label: {label}
- Status: {status_text}
- Iterations used: {current_iteration}/{max_iterations}

Review this result in the context of your current session. Choose the appropriate response:
- If you were waiting for this result to continue a planned workflow, proceed accordingly.
- If the conversation has already moved on or the user has been informed, no output is needed.
- Do not repeat work that has already been done in this session.

(This is an automated system notification delivered as a user message for technical reasons. It is NOT a new user request. Do not execute the subagent's task again. Simply review the result and decide how to proceed in the context of your current conversation.)
<!-- /nanobot:system -->"""

    def test_final_text_before_marker(self):
        content = self._build_announce("Here is the result")
        marker_pos = content.index("<!-- nanobot:system -->")
        result_pos = content.index("Here is the result")
        assert result_pos < marker_pos

    def test_closing_tag_present(self):
        content = self._build_announce("Result text")
        assert "<!-- nanobot:system -->" in content
        assert "<!-- /nanobot:system -->" in content

    def test_closing_tag_after_open(self):
        content = self._build_announce("Result text")
        open_pos = content.index("<!-- nanobot:system -->")
        close_pos = content.index("<!-- /nanobot:system -->")
        assert close_pos > open_pos

    def test_empty_final_text_gets_default(self):
        content = self._build_announce("")
        assert "(Subagent completed with no output)" in content
        marker_pos = content.index("<!-- nanobot:system -->")
        default_pos = content.index("(Subagent completed with no output)")
        assert default_pos < marker_pos

    def test_none_final_text_gets_default(self):
        content = self._build_announce(None)
        assert "(Subagent completed with no output)" in content

    def test_whitespace_final_text_gets_default(self):
        content = self._build_announce("   \n  ")
        assert "(Subagent completed with no output)" in content

    def test_metadata_inside_markers(self):
        content = self._build_announce("Result")
        open_pos = content.index("<!-- nanobot:system -->")
        close_pos = content.index("<!-- /nanobot:system -->")
        between = content[open_pos:close_pos]
        assert "Task ID:" in between
        assert "Label:" in between
        assert "Status:" in between
        assert "Iterations used:" in between

    def test_iteration_info(self):
        content = self._build_announce("Result", current_iteration=15, max_iterations=50)
        assert "15/50" in content

    def test_status_ok(self):
        content = self._build_announce("Result", status="ok")
        assert "completed successfully" in content

    def test_status_error(self):
        content = self._build_announce("Result", status="error")
        assert "failed" in content

    def test_system_guidance_not_before_marker(self):
        """Review instructions should be inside markers, not before."""
        content = self._build_announce("My result")
        open_pos = content.index("<!-- nanobot:system -->")
        before_marker = content[:open_pos]
        assert "Review this result" not in before_marker
        assert "automated system notification" not in before_marker


# ── 4. SubagentManager detail_logger plumbing ────────────────────────────────


class TestSubagentManagerDetailLogger:
    """Test that SubagentManager accepts and stores detail_logger."""

    def test_init_with_detail_logger(self):
        from nanobot.agent.subagent import SubagentManager
        mock_provider = MagicMock()
        mock_provider.provider_name = "test-provider"
        mock_provider.get_default_model.return_value = "test-model"
        mock_bus = MagicMock()
        mock_logger = MagicMock(spec=LLMDetailLogger)

        mgr = SubagentManager(
            provider=mock_provider,
            workspace=Path("/tmp"),
            bus=mock_bus,
            model="test-model",
            detail_logger=mock_logger,
        )
        assert mgr.detail_logger is mock_logger

    def test_init_without_detail_logger(self):
        from nanobot.agent.subagent import SubagentManager
        mock_provider = MagicMock()
        mock_provider.provider_name = "test-provider"
        mock_provider.get_default_model.return_value = "test-model"
        mock_bus = MagicMock()

        mgr = SubagentManager(
            provider=mock_provider,
            workspace=Path("/tmp"),
            bus=mock_bus,
            model="test-model",
        )
        assert mgr.detail_logger is None
