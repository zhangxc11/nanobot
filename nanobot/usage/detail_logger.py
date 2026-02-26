"""LLM call detail logger — JSONL file backend.

Records the full messages (prompt) and response for every LLM call,
enabling offline analysis of token consumption patterns.

Logs are stored as daily JSONL files under
``~/.nanobot/workspace/llm-logs/YYYY-MM-DD.jsonl``.

Thread-safety is provided by opening the file in append mode for each
write (OS-level atomic for reasonably sized lines on most filesystems).
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

DEFAULT_LOG_DIR = Path.home() / ".nanobot" / "workspace" / "llm-logs"


class LLMDetailLogger:
    """Append-only JSONL logger for LLM call details.

    Parameters
    ----------
    log_dir:
        Directory for daily JSONL files.  Defaults to
        ``~/.nanobot/workspace/llm-logs/``.
    enabled:
        If False, ``log_call`` is a no-op.  Useful for testing or
        disabling without removing the logger instance.
    """

    def __init__(
        self,
        log_dir: Path | str | None = None,
        enabled: bool = True,
    ):
        self.log_dir = Path(log_dir) if log_dir else DEFAULT_LOG_DIR
        self.enabled = enabled
        if self.enabled:
            os.makedirs(self.log_dir, exist_ok=True)

    def _get_file_path(self, dt: datetime | None = None) -> Path:
        """Return the JSONL file path for the given date (default: today)."""
        dt = dt or datetime.now()
        return self.log_dir / f"{dt.strftime('%Y-%m-%d')}.jsonl"

    def log_call(
        self,
        *,
        session_key: str,
        model: str,
        iteration: int,
        messages: list[dict[str, Any]],
        response_content: str | None,
        response_tool_calls: list[dict[str, Any]] | None = None,
        response_finish_reason: str = "stop",
        response_usage: dict[str, int] | None = None,
        timestamp: str | None = None,
    ) -> tuple[str, int] | None:
        """Record a single LLM call to the daily JSONL file.

        Parameters
        ----------
        session_key:
            Session identifier (e.g. ``"webchat:1772126509"``).
        model:
            Model name (e.g. ``"claude-opus-4-6"``).
        iteration:
            The iteration number within the agent loop (1-based).
        messages:
            The full messages list sent to the LLM (system + history + current).
        response_content:
            The LLM response text content.
        response_tool_calls:
            Tool calls from the response (already serialised as dicts).
        response_finish_reason:
            The finish reason (``"stop"``, ``"tool_use"``, etc.).
        response_usage:
            Token usage dict (``prompt_tokens``, ``completion_tokens``, ``total_tokens``).
        timestamp:
            ISO timestamp.  If None, ``datetime.now().isoformat()`` is used.

        Returns
        -------
        tuple[str, int] | None
            ``(filename, line_number)`` on success, ``None`` on failure or
            when disabled.  ``filename`` is just the basename (e.g.
            ``"2026-02-27.jsonl"``), ``line_number`` is 1-based.
        """
        if not self.enabled:
            return None

        now = datetime.now()
        ts = timestamp or now.isoformat()

        # Compute quick-analysis fields without parsing full messages later
        system_prompt_chars = 0
        if messages and messages[0].get("role") == "system":
            sys_content = messages[0].get("content", "")
            if isinstance(sys_content, str):
                system_prompt_chars = len(sys_content)

        usage = response_usage or {}
        record = {
            "timestamp": ts,
            "session_key": session_key,
            "model": model,
            "iteration": iteration,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "messages_count": len(messages),
            "system_prompt_chars": system_prompt_chars,
            "messages": messages,
            "response": {
                "content": response_content,
                "tool_calls": response_tool_calls,
                "finish_reason": response_finish_reason,
                "usage": usage,
            },
        }

        file_path = self._get_file_path(now)
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())

            # Count lines to determine line number
            # (slightly expensive but acceptable for ~50 calls/day)
            with open(file_path, "r", encoding="utf-8") as f:
                line_count = sum(1 for _ in f)

            filename = file_path.name
            logger.debug(
                "Logged LLM call detail: session={} model={} iter={} → {}:{}",
                session_key, model, iteration, filename, line_count,
            )
            return (filename, line_count)

        except Exception:
            logger.exception(
                "Failed to log LLM call detail for session={}", session_key
            )
            return None
