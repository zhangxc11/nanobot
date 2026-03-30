"""CronTask tool for scheduling tasks that create new sessions."""

from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.cron.service import CronService
from nanobot.cron.types import CronSchedule


class CronTaskTool(Tool):
    """Tool to schedule tasks (create new sessions on a schedule)."""

    def __init__(self, cron_service: CronService):
        self._cron = cron_service
        self._channel = ""
        self._chat_id = ""
        self._session_id = ""

    def set_context(
        self,
        channel: str,
        chat_id: str,
        session_id: str = "",
        **kwargs: Any,
    ) -> None:
        """Set the current session context."""
        self._channel = channel
        self._chat_id = chat_id
        self._session_id = session_id

    @property
    def name(self) -> str:
        return "cron_task"

    @property
    def description(self) -> str:
        return "Schedule tasks that create new agent sessions on a schedule. Actions: add, list, remove."

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
                "owner": {
                    "type": "string",
                    "description": "Owner namespace for the task (required for add, e.g. 'cil', 'tushare', 'paper'). Used for isolation — only matching owner can remove.",
                },
                "message": {"type": "string", "description": "Task message for the agent session (required for add)"},
                "name": {
                    "type": "string",
                    "description": "Short display name for the task. Auto-generated from message if omitted.",
                },
                "at": {
                    "type": "string",
                    "description": "ISO datetime for one-time execution (e.g. '2026-02-12T10:30:00')",
                },
                "cron_expr": {
                    "type": "string",
                    "description": "Cron expression like '0 9 * * *' (for recurring tasks)",
                },
                "every_seconds": {
                    "type": "integer",
                    "description": "Interval in seconds (for recurring tasks)",
                },
                "tz": {
                    "type": "string",
                    "description": "IANA timezone, only for cron_expr (NOT for 'at'). e.g. 'Asia/Shanghai'",
                },
                "job_id": {"type": "string", "description": "Job ID (for remove)"},
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        owner: str = "",
        message: str = "",
        name: str = "",
        at: str | None = None,
        cron_expr: str | None = None,
        every_seconds: int | None = None,
        tz: str | None = None,
        job_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        if action == "add":
            return self._add(owner, message, name, at, cron_expr, every_seconds, tz)
        elif action == "list":
            return self._list()
        elif action == "remove":
            return self._remove(job_id, owner)
        return f"Unknown action: {action}"

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
        owner: str,
        message: str,
        name: str,
        at: str | None,
        cron_expr: str | None,
        every_seconds: int | None,
        tz: str | None,
    ) -> str:
        if not owner:
            return "Error: owner is required for cron_task add (e.g. 'cil', 'tushare')"
        if not message:
            return "Error: message is required for cron_task add"
        if tz and not cron_expr:
            return "Error: tz can only be used with cron_expr"
        if tz:
            from zoneinfo import ZoneInfo
            try:
                ZoneInfo(tz)
            except (KeyError, Exception):
                return f"Error: unknown timezone '{tz}'"

        source_channel = self._resolve_source_channel()

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
            target_session=None,  # Tasks create new sessions
            source_channel=source_channel,
            owner=owner,
            created_by_session=self._session_id,
        )
        return f"Created task '{job.name}' (id: {job.id}, owner={owner}) [source={source_channel}]"

    def _list(self) -> str:
        jobs = self._cron.list_jobs()
        # Filter to tasks only (no target_session)
        tasks = [j for j in jobs if not j.payload.target_session]

        if not tasks:
            return "No scheduled tasks."

        # Group by owner
        from collections import defaultdict
        by_owner: dict[str, list] = defaultdict(list)
        for j in tasks:
            owner_key = j.owner or "(no owner)"
            by_owner[owner_key].append(j)

        lines = []
        for owner_key in sorted(by_owner.keys()):
            lines.append(f"[{owner_key}]")
            for j in by_owner[owner_key]:
                sched = self._format_schedule(j.schedule)
                status = ""
                if j.state.last_status:
                    status = f" last={j.state.last_status}"
                lines.append(f"  - {j.name} (id: {j.id}, {sched}{status})")
        return "Scheduled tasks:\n" + "\n".join(lines)

    def _format_schedule(self, schedule: CronSchedule) -> str:
        if schedule.kind == "every":
            secs = (schedule.every_ms or 0) // 1000
            return f"every {secs}s"
        elif schedule.kind == "cron":
            tz_str = f" {schedule.tz}" if schedule.tz else ""
            return f"{schedule.expr or ''}{tz_str}"
        else:
            return "one-time"

    def _remove(self, job_id: str | None, owner: str = "") -> str:
        if not job_id:
            return "Error: job_id is required for remove"

        job = self._cron.get_job(job_id)
        if not job:
            return f"Task {job_id} not found"

        # Must be a task (no target_session)
        if job.payload.target_session:
            return f"Error: job {job_id} is a reminder, not a task. Use reminder tool to manage reminders."

        # Owner isolation check
        if job.owner and owner != job.owner:
            return (
                f"Error: cannot remove task {job_id} — its owner is '{job.owner}', "
                f"but provided owner is '{owner}'. Pass the correct owner to remove."
            )

        if self._cron.remove_job(job_id):
            return f"Removed task {job_id} ('{job.name}', owner={job.owner})"
        return f"Task {job_id} not found"

    def clone(self) -> "CronTaskTool":
        """Create a new CronTaskTool instance sharing the same CronService."""
        tool = CronTaskTool(self._cron)
        tool._channel = self._channel
        tool._chat_id = self._chat_id
        tool._session_id = self._session_id
        return tool
