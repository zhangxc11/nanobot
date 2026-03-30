"""Reminder tool for scheduling messages to existing sessions."""

from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.cron.service import CronService
from nanobot.cron.types import CronSchedule
from nanobot.session.parents import is_child_of


class ReminderTool(Tool):
    """Tool to schedule reminders (messages to existing sessions)."""

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
        return "reminder"

    @property
    def description(self) -> str:
        return "Schedule reminders (messages to existing sessions). Actions: add, list, remove."

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
                "target_session": {
                    "type": "string",
                    "description": "Target session ID to send the message to (required for add, must be self or child session, use '_' separator e.g. 'webchat_1773591411')",
                },
                "message": {"type": "string", "description": "Reminder message (required for add)"},
                "name": {
                    "type": "string",
                    "description": "Short display name for the reminder. Auto-generated from message if omitted.",
                },
                "at": {
                    "type": "string",
                    "description": "ISO datetime for one-time execution (e.g. '2026-02-12T10:30:00')",
                },
                "cron_expr": {
                    "type": "string",
                    "description": "Cron expression like '0 9 * * *' (for recurring reminders)",
                },
                "every_seconds": {
                    "type": "integer",
                    "description": "Interval in seconds (for recurring reminders)",
                },
                "tz": {
                    "type": "string",
                    "description": "IANA timezone, only for cron_expr (NOT for 'at'). e.g. 'America/Vancouver'",
                },
                "job_id": {"type": "string", "description": "Job ID (for remove)"},
                "show_all": {
                    "type": "boolean",
                    "description": "If true, list shows all reminders; otherwise only those created by current session (default: false)",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        target_session: str | None = None,
        message: str = "",
        name: str = "",
        at: str | None = None,
        cron_expr: str | None = None,
        every_seconds: int | None = None,
        tz: str | None = None,
        job_id: str | None = None,
        show_all: bool = False,
        **kwargs: Any,
    ) -> str:
        if action == "add":
            return self._add(target_session, message, name, at, cron_expr, every_seconds, tz)
        elif action == "list":
            return self._list(show_all)
        elif action == "remove":
            return self._remove(job_id)
        return f"Unknown action: {action}"

    def _validate_target_session(self, target_session: str) -> str | None:
        """Validate target_session is self or a child session.

        Returns None if valid, or an error message if invalid.
        """
        if not target_session:
            return None

        if ":" in target_session:
            return (
                f"Error: target_session '{target_session}' uses session_key format (with ':'). "
                f"Please use session_id format (with '_'), e.g. '{target_session.replace(':', '_')}'."
            )

        if target_session == self._session_id:
            return None

        if target_session.startswith("cron_"):
            return None

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

    def _add(
        self,
        target_session: str | None,
        message: str,
        name: str,
        at: str | None,
        cron_expr: str | None,
        every_seconds: int | None,
        tz: str | None,
    ) -> str:
        if not target_session:
            return "Error: target_session is required for reminder add"
        if not message:
            return "Error: message is required for reminder add"
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
        error = self._validate_target_session(target_session)
        if error:
            return error

        # Resolve source channel and reject CLI reminders
        source_channel = self._resolve_source_channel()
        if source_channel == "cli":
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

        display_name = name if name else message[:50]

        job = self._cron.add_job(
            name=display_name,
            schedule=schedule,
            message=message,
            delete_after_run=delete_after,
            target_session=target_session,
            source_channel=source_channel,
            created_by_session=self._session_id,
        )
        return f"Created reminder '{job.name}' (id: {job.id}) [source={source_channel}] [target: {target_session}]"

    def _list(self, show_all: bool) -> str:
        jobs = self._cron.list_jobs()
        # Filter to reminders only (has target_session)
        reminders = [j for j in jobs if j.payload.target_session]

        if not show_all:
            # Only show reminders created by current session
            reminders = [j for j in reminders if j.created_by_session == self._session_id]

        if not reminders:
            if show_all:
                return "No active reminders."
            return f"No reminders created by this session. Use show_all=true to see all reminders."

        lines = []
        for j in reminders:
            line = f"- {j.name} (id: {j.id}, {j.schedule.kind}) → {j.payload.target_session}"
            if show_all and j.created_by_session:
                line += f" [by: {j.created_by_session}]"
            lines.append(line)
        return "Reminders:\n" + "\n".join(lines)

    def _remove(self, job_id: str | None) -> str:
        if not job_id:
            return "Error: job_id is required for remove"

        # Check isolation: only creator can remove
        job = self._cron.get_job(job_id)
        if not job:
            return f"Reminder {job_id} not found"

        # Must be a reminder (has target_session)
        if not job.payload.target_session:
            return f"Error: job {job_id} is a task, not a reminder. Use cron_task tool to manage tasks."

        # Owner isolation check
        if job.created_by_session and job.created_by_session != self._session_id:
            return (
                f"Error: cannot remove reminder {job_id} — it was created by session "
                f"'{job.created_by_session}', but current session is '{self._session_id}'."
            )

        if self._cron.remove_job(job_id):
            return f"Removed reminder {job_id} ('{job.name}')"
        return f"Reminder {job_id} not found"

    def clone(self) -> "ReminderTool":
        """Create a new ReminderTool instance sharing the same CronService."""
        tool = ReminderTool(self._cron)
        tool._channel = self._channel
        tool._chat_id = self._chat_id
        tool._session_key = self._session_key
        tool._session_id = self._session_id
        tool._sessions_dir = self._sessions_dir
        return tool
