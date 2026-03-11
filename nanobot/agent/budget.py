"""Budget alert utilities for agent iteration limits.

§48: Extracted from loop.py and subagent.py to eliminate code duplication.
Both the main AgentLoop and SubagentManager call ``build_budget_alert()``
when remaining iterations reach the threshold.
"""

from __future__ import annotations

from loguru import logger


def budget_alert_threshold(max_iterations: int) -> int:
    """Return the remaining-iterations count at which to inject a budget alert.

    - max_iterations >= 20  →  threshold = 10
    - max_iterations < 20   →  threshold = max(3, max_iterations // 4)
    """
    if max_iterations >= 20:
        return 10
    return max(3, max_iterations // 4)


def build_budget_alert(
    remaining: int,
    max_iterations: int,
    session_key: str = "",
) -> str:
    """Build a budget alert message and log a warning.

    Parameters
    ----------
    remaining:
        Number of iterations remaining.
    max_iterations:
        Total iteration budget for this turn.
    session_key:
        Session identifier for log context (optional).

    Returns
    -------
    str
        The alert message text to inject as a user-role message.
    """
    logger.warning(
        "Budget alert: {}/{} remaining, session={}",
        remaining, max_iterations, session_key,
    )
    return (
        f"[System Notice — Current Turn Budget] ⚠️ You have {remaining} tool-call iterations "
        f"remaining out of {max_iterations}. "
        f"Prioritize completing your current task. If you cannot finish "
        f"in time, summarize progress so far and present what you have. "
        f"Do not acknowledge this notice — continue working."
    )
