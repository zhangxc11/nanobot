"""Cron tool for scheduling reminders and tasks."""

from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.cron.service import CronService
from nanobot.cron.types import CronSchedule
from nanobot.session.parents import is_child_of


class CronTool(Tool):
    """Tool to schedule reminders and recurring tasks."""

    def __init__(self, cron_service: CronService):
        self._cron = cron_service
        self._channel = ""
        self._chat_id = ""
        self._session_key = ""
        self._session_id = ""
        self._sessions_dir: str | Path | None = None

    def set_context(
        self,
        channel: str,
        chat_id: str,
        session_key: str = "",
        session_id: str | None = None,
        sessions_dir: str | Path | None = None,
    ) -> None:
        """Set the current session context for delivery."""
        self._channel = channel
        self._chat_id = chat_id
        self._session_key = session_key
        self._session_id = session_id or session_key.replace(":", "_")
        if sessions_dir is not None:
            self._sessions_dir = sessions_dir

    @property
    def name(self) -> str:
        return "cron"

    @property
    def description(self) -> str:
        return "Schedule reminders and recurring tasks. Actions: add, list, remove."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "remove"],
                    "description": "Action to perform",
                },
                "message": {"type": "string", "description": "Reminder message (for add)"},
                "every_seconds": {
                    "type": "integer",
                    "description": "Interval in seconds (for recurring tasks)",
                },
                "cron_expr": {
                    "type": "string",
                    "description": "Cron expression like '0 9 * * *' (for scheduled tasks)",
                },
                "name": {
                    "type": "string",
                    "description": "Short display name for the job (e.g. '午休提醒'). Auto-generated from message if omitted.",
                },
                "tz": {
                    "type": "string",
                    "description": "IANA timezone, only for cron_expr (NOT for 'at'). e.g. 'America/Vancouver'",
                },
                "at": {
                    "type": "string",
                    "description": "ISO datetime for one-time execution (e.g. '2026-02-12T10:30:00')",
                },
                "job_id": {"type": "string", "description": "Job ID (for remove)"},
                "target_session": {
                    "type": "string",
                    "description": "Target session ID to send the message to (optional, must be self or child session, use '_' separator e.g. 'webchat_1773591411')",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        message: str = "",
        name: str = "",
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        tz: str | None = None,
        at: str | None = None,
        job_id: str | None = None,
        target_session: str | None = None,
        **kwargs: Any,
    ) -> str:
        if action == "add":
            return self._add_job(message, name, every_seconds, cron_expr, tz, at, target_session)
        elif action == "list":
            return self._list_jobs()
        elif action == "remove":
            return self._remove_job(job_id)
        return f"Unknown action: {action}"

    def _validate_target_session(self, target_session: str) -> str | None:
        """Validate target_session is self or a child session.

        Uses session_id format (underscore-separated) for comparison.
        Parent-child relationship is resolved via ``session.parents.is_child_of``.
        Returns None if valid, or an error message if invalid.
        """
        if not target_session:
            return None

        # Reject session_key format (colon-separated) — must use session_id
        if ":" in target_session:
            return (
                f"Error: target_session '{target_session}' uses session_key format (with ':'). "
                f"Please use session_id format (with '_'), e.g. '{target_session.replace(':', '_')}'."
            )

        # Allow targeting self (exact session_id match)
        if target_session == self._session_id:
            return None

        # Allow targeting cron sessions
        if target_session.startswith("cron_"):
            return None

        # Allow targeting child sessions via parent-child resolution
        if self._sessions_dir and self._session_id and is_child_of(
            target_session, self._session_id, self._sessions_dir
        ):
            return None

        return (
            f"Error: target_session '{target_session}' is not allowed. "
            f"Must be self ('{self._session_id}') or a child session."
        )

    def _resolve_source_channel(self) -> str:
        """Infer the executor partition from the current channel."""
        ch = self._channel.lower()
        if ch.startswith(("feishu", "telegram", "whatsapp", "discord", "slack")):
            return "gateway"
        if ch.startswith(("webchat", "web")):
            return "web"
        return "cli"

    def _add_job(
        self,
        message: str,
        name: str,
        every_seconds: int | None,
        cron_expr: str | None,
        tz: str | None,
        at: str | None,
        target_session: str | None = None,
    ) -> str:
        if not message:
            return "Error: message is required for add"
        if not self._channel or not self._chat_id:
            return "Error: no session context (channel/chat_id)"
        if tz and not cron_expr:
            return "Error: tz can only be used with cron_expr"
        if tz:
            from zoneinfo import ZoneInfo

            try:
                ZoneInfo(tz)
            except (KeyError, Exception):
                return f"Error: unknown timezone '{tz}'"

        # Validate target_session security
        if target_session:
            error = self._validate_target_session(target_session)
            if error:
                return error

        # Resolve source channel and reject CLI reminders
        source_channel = self._resolve_source_channel()
        if target_session and source_channel == "cli":
            return "Error: reminder 只能在常驻进程（gateway/web）中创建，CLI 不支持。"

        # Build schedule
        delete_after = False
        if every_seconds:
            schedule = CronSchedule(kind="every", every_ms=every_seconds * 1000)
        elif cron_expr:
            schedule = CronSchedule(kind="cron", expr=cron_expr, tz=tz)
        elif at:
            from datetime import datetime

            dt = datetime.fromisoformat(at)
            at_ms = int(dt.timestamp() * 1000)
            schedule = CronSchedule(kind="at", at_ms=at_ms)
            delete_after = True
        else:
            return "Error: either every_seconds, cron_expr, or at is required"

        # Determine display name: use provided name, or fallback to message[:50]
        display_name = name if name else message[:50]

        job = self._cron.add_job(
            name=display_name,
            schedule=schedule,
            message=message,
            delete_after_run=delete_after,
            target_session=target_session,
            source_channel=source_channel,
        )
        result = f"Created job '{job.name}' (id: {job.id})"
        if target_session:
            result += f" [target: {target_session}]"
        return result

    def _list_jobs(self) -> str:
        jobs = self._cron.list_jobs()
        if not jobs:
            return "No scheduled jobs."
        lines = []
        for j in jobs:
            line = f"- {j.name} (id: {j.id}, {j.schedule.kind})"
            if j.payload.target_session:
                line += f" → {j.payload.target_session}"
            lines.append(line)
        return "Scheduled jobs:\n" + "\n".join(lines)

    def _remove_job(self, job_id: str | None) -> str:
        if not job_id:
            return "Error: job_id is required for remove"
        if self._cron.remove_job(job_id):
            return f"Removed job {job_id}"
        return f"Job {job_id} not found"

    def clone(self) -> "CronTool":
        """Create a new CronTool instance sharing the same CronService.

        The clone has independent context (channel/chat_id/session_key/session_id).
        """
        tool = CronTool(self._cron)
        tool._channel = self._channel
        tool._chat_id = self._chat_id
        tool._session_key = self._session_key
        tool._session_id = self._session_id
        tool._sessions_dir = self._sessions_dir
        return tool
