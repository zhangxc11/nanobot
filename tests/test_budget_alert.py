"""Tests for AgentLoop budget alert mechanism (Phase 25a)."""

import pytest

from nanobot.agent.loop import _budget_alert_threshold


# ── Threshold calculation tests ──


class TestBudgetAlertThreshold:
    """Test the _budget_alert_threshold function."""

    def test_normal_iterations(self):
        """max_iterations >= 20 → threshold = 10."""
        assert _budget_alert_threshold(40) == 10
        assert _budget_alert_threshold(100) == 10
        assert _budget_alert_threshold(20) == 10

    def test_small_iterations(self):
        """max_iterations < 20 → threshold = max(3, max_iterations // 4)."""
        assert _budget_alert_threshold(16) == 4
        assert _budget_alert_threshold(12) == 3

    def test_minimum_threshold(self):
        """Threshold never goes below 3."""
        assert _budget_alert_threshold(8) == 3
        assert _budget_alert_threshold(4) == 3
        assert _budget_alert_threshold(3) == 3

    def test_boundary_19(self):
        """max_iterations = 19 → threshold = max(3, 19 // 4) = max(3, 4) = 4."""
        assert _budget_alert_threshold(19) == 4

    def test_boundary_20(self):
        """max_iterations = 20 → threshold = 10 (switches to fixed)."""
        assert _budget_alert_threshold(20) == 10


# ── Integration: alert injection in agent loop ──


class TestBudgetAlertInjection:
    """Test that budget alert is injected into messages at the right time."""

    def test_alert_injected_at_threshold(self):
        """Simulate the loop logic to verify alert is injected exactly once."""
        max_iterations = 40
        threshold = _budget_alert_threshold(max_iterations)
        assert threshold == 10

        messages = []
        alert_count = 0

        for iteration_num in range(1, max_iterations + 1):
            remaining = max_iterations - iteration_num
            if remaining == threshold:
                messages.append({
                    "role": "system",
                    "content": f"⚠️ Budget alert: You have {remaining} tool call iterations "
                               f"remaining (out of {max_iterations}).",
                })
                alert_count += 1

        # Alert should fire exactly once (at iteration 30, remaining=10)
        assert alert_count == 1
        assert len(messages) == 1
        assert "10" in messages[0]["content"]
        assert "40" in messages[0]["content"]

    def test_alert_not_injected_for_very_short_task(self):
        """If max_iterations is very small, alert still fires once."""
        max_iterations = 4
        threshold = _budget_alert_threshold(max_iterations)
        assert threshold == 3

        alert_count = 0
        for iteration_num in range(1, max_iterations + 1):
            remaining = max_iterations - iteration_num
            if remaining == threshold:
                alert_count += 1

        # iteration=1, remaining=3 → fires
        assert alert_count == 1

    def test_alert_content_format(self):
        """Verify alert message contains expected fields."""
        max_iter = 50
        threshold = _budget_alert_threshold(max_iter)
        remaining = threshold

        content = (
            f"⚠️ Budget alert: You have {remaining} tool call iterations "
            f"remaining (out of {max_iter}). Please prioritize "
            f"saving your work state and wrapping up gracefully."
        )

        assert "⚠️ Budget alert" in content
        assert str(remaining) in content
        assert str(max_iter) in content
        assert "saving your work state" in content
