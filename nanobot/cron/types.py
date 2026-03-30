"""Cron types."""

from dataclasses import dataclass, field
from typing import Literal  # kept for CronJobState


@dataclass
class CronSchedule:
    """Schedule definition for a cron job."""
    kind: Literal["at", "every", "cron"]
    # For "at": timestamp in ms
    at_ms: int | None = None
    # For "every": interval in ms
    every_ms: int | None = None
    # For "cron": cron expression (e.g. "0 9 * * *")
    expr: str | None = None
    # Timezone for cron expressions
    tz: str | None = None


@dataclass
class CronPayload:
    """What to do when the job runs."""
    message: str = ""
    # Non-None → reminder mode (send to existing session); None → task mode (create new session)
    target_session: str | None = None
    # Executor binding (only meaningful for reminders): "gateway" | "web" | None (legacy → web)
    source_channel: str | None = None
    # === deprecated fields (kept for backward compat read/write) ===
    kind: str = "agent_turn"
    deliver: bool = False
    channel: str | None = None
    to: str | None = None


@dataclass
class CronJobState:
    """Runtime state of a job."""
    next_run_at_ms: int | None = None
    last_run_at_ms: int | None = None
    last_status: Literal["ok", "error", "skipped"] | None = None
    last_error: str | None = None


@dataclass
class CronJob:
    """A scheduled job."""
    id: str
    name: str
    enabled: bool = True
    schedule: CronSchedule = field(default_factory=lambda: CronSchedule(kind="every"))
    payload: CronPayload = field(default_factory=CronPayload)
    state: CronJobState = field(default_factory=CronJobState)
    created_at_ms: int = 0
    updated_at_ms: int = 0
    delete_after_run: bool = False
    # ── Owner & creator isolation (added for tool split) ──
    # For task jobs: namespace owner (e.g. "cil", "tushare")
    owner: str = ""
    # For reminder jobs: session_id that created this job
    created_by_session: str = ""


@dataclass
class CronStore:
    """Persistent store for cron jobs."""
    version: int = 1
    jobs: list[CronJob] = field(default_factory=list)
