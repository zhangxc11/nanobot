"""Audit logger for tool execution tracing.

Records every tool invocation (file reads/writes, shell commands, web fetches,
etc.) to a daily JSONL file under ``~/.nanobot/workspace/audit-logs/``.

The logger is designed to be attached to :class:`ToolRegistry` so that *all*
registered tools — including dynamically-added MCP tools — are automatically
audited without modifying individual tool implementations.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class AuditEntry:
    """A single audit log record."""

    # ── When ──
    timestamp: str

    # ── Who / Where ──
    session_key: str = ""
    channel: str = ""
    chat_id: str = ""

    # ── What ──
    tool: str = ""              # Tool name (e.g. "read_file", "exec", "mcp_xxx")
    action: str = ""            # Semantic action: read / write / edit / list / exec / search / fetch / spawn / cron / message / mcp
    params: dict[str, Any] = field(default_factory=dict)   # Sanitised tool parameters
    result: dict[str, Any] = field(default_factory=dict)   # Result summary

    # ── File-specific ──
    resolved_path: str | None = None  # Absolute path after resolution (for file tools)

    # ── Outcome ──
    error: str | None = None          # Error message if the tool failed
    duration_ms: float = 0.0          # Wall-clock execution time in milliseconds


class AuditLogger:
    """Append-only JSONL audit logger, one file per calendar day.

    Parameters
    ----------
    log_dir : Path, optional
        Directory to store audit log files.  Defaults to
        ``~/.nanobot/workspace/audit-logs/``.
    enabled : bool
        Set to ``False`` to disable logging entirely (no I/O at all).
    """

    def __init__(self, log_dir: Path | None = None, enabled: bool = True) -> None:
        if log_dir is None:
            log_dir = Path.home() / ".nanobot" / "workspace" / "audit-logs"
        self.log_dir = log_dir
        self.enabled = enabled

    def log(self, entry: AuditEntry) -> None:
        """Write a single audit entry to today's JSONL file.

        The write is synchronous (``open("a")`` + ``flush``) — audit records
        are tiny (~500 bytes each) so the overhead is negligible compared to
        the tool execution itself.
        """
        if not self.enabled:
            return

        self.log_dir.mkdir(parents=True, exist_ok=True)
        date_str = entry.timestamp[:10]  # YYYY-MM-DD
        path = self.log_dir / f"{date_str}.jsonl"

        line = json.dumps(asdict(entry), ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
