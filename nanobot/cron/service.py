"""Cron service for scheduling agent tasks."""

import asyncio
import fcntl
import json
import time
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine, Protocol, runtime_checkable

from loguru import logger

from nanobot.cron.types import CronJob, CronJobState, CronPayload, CronSchedule, CronStore


def _now_ms() -> int:
    return int(time.time() * 1000)


class JobPartition(Enum):
    """Which process is responsible for executing a job."""
    WEB = "web"
    GATEWAY = "gateway"


def classify_job(job: CronJob) -> JobPartition:
    """Classify a job into a partition based on source_channel."""
    if job.payload.source_channel == "gateway":
        return JobPartition.GATEWAY
    # "web", "cli", None (legacy compat) → WEB partition
    return JobPartition.WEB


def _compute_next_run(schedule: CronSchedule, now_ms: int) -> int | None:
    """Compute next run time in ms."""
    if schedule.kind == "at":
        return schedule.at_ms if schedule.at_ms and schedule.at_ms > now_ms else None

    if schedule.kind == "every":
        if not schedule.every_ms or schedule.every_ms <= 0:
            return None
        # Next interval from now
        return now_ms + schedule.every_ms

    if schedule.kind == "cron" and schedule.expr:
        try:
            from zoneinfo import ZoneInfo

            from croniter import croniter
            # Use caller-provided reference time for deterministic scheduling
            base_time = now_ms / 1000
            tz = ZoneInfo(schedule.tz) if schedule.tz else datetime.now().astimezone().tzinfo
            base_dt = datetime.fromtimestamp(base_time, tz=tz)
            cron = croniter(schedule.expr, base_dt)
            next_dt = cron.get_next(datetime)
            return int(next_dt.timestamp() * 1000)
        except Exception:
            return None

    return None


def _validate_schedule_for_add(schedule: CronSchedule) -> None:
    """Validate schedule fields that would otherwise create non-runnable jobs."""
    if schedule.tz and schedule.kind != "cron":
        raise ValueError("tz can only be used with cron schedules")

    if schedule.kind == "cron" and schedule.tz:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(schedule.tz)
        except Exception:
            raise ValueError(f"unknown timezone '{schedule.tz}'") from None


# ── CronExecutor Protocol ──


@runtime_checkable
class CronExecutor(Protocol):
    """Protocol for executing cron jobs.

    Gateway and Worker each provide their own implementation.
    CronService delegates job execution through this interface.
    """

    async def execute_job(self, job: CronJob) -> str | None:
        """Create a new cron session and execute the job."""
        ...

    async def send_to_session(self, target_session_key: str, message: str, source: str | None = None) -> bool:
        """Send a message to an existing session."""
        ...


# ── File Lock for Scheduler Arbitration ──

_WATCHDOG_INTERVAL_S = 60  # 1 minute


class _SchedulerLock:
    """File-based lock for scheduler arbitration.

    Only one process (gateway or worker) should schedule and execute jobs.
    The other stays in standby mode with a watchdog that periodically
    tries to acquire the lock.
    """

    def __init__(self, lock_path: Path):
        self._lock_path = lock_path
        self._fd: int | None = None
        self._acquired = False

    def try_acquire(self) -> bool:
        """Try to acquire the scheduler lock (non-blocking).

        Returns True if lock was acquired, False if another process holds it.
        """
        if self._acquired:
            return True

        self._lock_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            if self._fd is None:
                self._fd = open(self._lock_path, "w")  # noqa: SIM115
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._acquired = True
            # Write PID for debugging
            self._fd.seek(0)
            self._fd.truncate()
            self._fd.write(f"{__import__('os').getpid()}\n")
            self._fd.flush()
            return True
        except (OSError, IOError):
            return False

    def release(self) -> None:
        """Release the scheduler lock."""
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except (OSError, IOError):
                pass
            try:
                self._fd.close()
            except (OSError, IOError):
                pass
            self._fd = None
        self._acquired = False

    @property
    def is_acquired(self) -> bool:
        return self._acquired


class CronService:
    """Service for managing and executing scheduled jobs."""

    def __init__(
        self,
        store_path: Path,
        on_job: Callable[[CronJob], Coroutine[Any, Any, str | None]] | None = None,
        executor: CronExecutor | None = None,
        process_role: str = "web",
    ):
        self.store_path = store_path
        self.on_job = on_job
        self.executor = executor
        self._process_role = process_role
        self._partition = (
            JobPartition.GATEWAY if process_role == "gateway" else JobPartition.WEB
        )
        self._store: CronStore | None = None
        self._last_mtime: float = 0.0
        self._timer_task: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._running = False
        self._scheduling = False  # True if this instance holds the scheduler lock (for task/PUBLIC jobs)
        self._lock: _SchedulerLock | None = None

    def _load_store(self) -> CronStore:
        """Load jobs from disk. Reloads automatically if file was modified externally."""
        if self._store and self.store_path.exists():
            mtime = self.store_path.stat().st_mtime
            if mtime != self._last_mtime:
                logger.info("Cron: jobs.json modified externally, reloading")
                self._store = None
        if self._store:
            return self._store

        if self.store_path.exists():
            try:
                data = json.loads(self.store_path.read_text(encoding="utf-8"))
                jobs = []
                for j in data.get("jobs", []):
                    jobs.append(CronJob(
                        id=j["id"],
                        name=j["name"],
                        enabled=j.get("enabled", True),
                        schedule=CronSchedule(
                            kind=j["schedule"]["kind"],
                            at_ms=j["schedule"].get("atMs"),
                            every_ms=j["schedule"].get("everyMs"),
                            expr=j["schedule"].get("expr"),
                            tz=j["schedule"].get("tz"),
                        ),
                        payload=CronPayload(
                            kind=j["payload"].get("kind", "agent_turn"),
                            message=j["payload"].get("message", ""),
                            deliver=j["payload"].get("deliver", False),
                            channel=j["payload"].get("channel"),
                            to=j["payload"].get("to"),
                            target_session=j["payload"].get("targetSession"),
                            source_channel=j["payload"].get("sourceChannel") or "web",
                        ),
                        state=CronJobState(
                            next_run_at_ms=j.get("state", {}).get("nextRunAtMs"),
                            last_run_at_ms=j.get("state", {}).get("lastRunAtMs"),
                            last_status=j.get("state", {}).get("lastStatus"),
                            last_error=j.get("state", {}).get("lastError"),
                        ),
                        created_at_ms=j.get("createdAtMs", 0),
                        updated_at_ms=j.get("updatedAtMs", 0),
                        delete_after_run=j.get("deleteAfterRun", False),
                    ))
                self._store = CronStore(jobs=jobs)
            except Exception as e:
                logger.warning("Failed to load cron store: {}", e)
                self._store = CronStore()
        else:
            self._store = CronStore()

        return self._store

    def _save_store(self) -> None:
        """Save jobs to disk."""
        if not self._store:
            return

        self.store_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "version": self._store.version,
            "jobs": [
                {
                    "id": j.id,
                    "name": j.name,
                    "enabled": j.enabled,
                    "schedule": {
                        "kind": j.schedule.kind,
                        "atMs": j.schedule.at_ms,
                        "everyMs": j.schedule.every_ms,
                        "expr": j.schedule.expr,
                        "tz": j.schedule.tz,
                    },
                    "payload": {
                        "kind": j.payload.kind,
                        "message": j.payload.message,
                        "deliver": j.payload.deliver,
                        "channel": j.payload.channel,
                        "to": j.payload.to,
                        "targetSession": j.payload.target_session,
                        "sourceChannel": j.payload.source_channel,
                    },
                    "state": {
                        "nextRunAtMs": j.state.next_run_at_ms,
                        "lastRunAtMs": j.state.last_run_at_ms,
                        "lastStatus": j.state.last_status,
                        "lastError": j.state.last_error,
                    },
                    "createdAtMs": j.created_at_ms,
                    "updatedAtMs": j.updated_at_ms,
                    "deleteAfterRun": j.delete_after_run,
                }
                for j in self._store.jobs
            ]
        }

        self.store_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        self._last_mtime = self.store_path.stat().st_mtime
    
    async def start(self) -> None:
        """Start the cron service.

        Both processes arm their own partition timer immediately.
        The scheduler lock is used only for task (PUBLIC) jobs — the lock holder
        also executes WEB-partition jobs as fallback if the web worker is down.
        """
        self._running = True
        self._load_store()

        # Set up file lock (used for task/PUBLIC job arbitration)
        lock_path = self.store_path.parent / "scheduler.lock"
        self._lock = _SchedulerLock(lock_path)

        if self._lock.try_acquire():
            self._scheduling = True
            logger.info("Cron: acquired scheduler lock (role={})", self._process_role)
            self._recompute_next_runs()
            self._save_store()
        else:
            self._scheduling = False
            logger.info("Cron: scheduler lock held by another process (role={})", self._process_role)
            self._start_watchdog()

        # Both processes arm their partition timer
        self._arm_timer()

        logger.info("Cron service started (role={}, partition={}, scheduling={}, jobs={})",
                     self._process_role, self._partition.value, self._scheduling,
                     len(self._store.jobs if self._store else []))

    def _start_watchdog(self) -> None:
        """Start watchdog that periodically tries to acquire the scheduler lock."""
        if self._watchdog_task:
            self._watchdog_task.cancel()

        async def _watchdog_loop():
            while self._running and not self._scheduling:
                await asyncio.sleep(_WATCHDOG_INTERVAL_S)
                if not self._running:
                    break
                if self._lock and self._lock.try_acquire():
                    self._scheduling = True
                    logger.info("Cron: watchdog acquired scheduler lock — switching to scheduling mode")
                    self._load_store()
                    self._recompute_next_runs()
                    self._save_store()
                    self._arm_timer()
                    break

        self._watchdog_task = asyncio.create_task(_watchdog_loop())

    def stop(self) -> None:
        """Stop the cron service."""
        self._running = False
        self._scheduling = False
        if self._timer_task:
            self._timer_task.cancel()
            self._timer_task = None
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None
        if self._watchdog_task:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        if self._lock:
            self._lock.release()
            self._lock = None

    def _recompute_next_runs(self) -> None:
        """Recompute next run times for all enabled jobs."""
        if not self._store:
            return
        now = _now_ms()
        for job in self._store.jobs:
            if job.enabled:
                job.state.next_run_at_ms = _compute_next_run(job.schedule, now)

    def _get_next_wake_ms(self) -> int | None:
        """Get the earliest next run time for this partition's jobs."""
        if not self._store:
            return None
        times = [
            j.state.next_run_at_ms for j in self._store.jobs
            if j.enabled and j.state.next_run_at_ms
            and classify_job(j) == self._partition
        ]
        return min(times) if times else None

    _POLL_INTERVAL_S = 15

    def _ensure_poll_task(self) -> None:
        """Start periodic poll task to detect new jobs when no timer is armed."""
        if self._poll_task and not self._poll_task.done():
            return

        async def _poll_loop():
            last_mtime = self._last_mtime
            while self._running:
                await asyncio.sleep(self._POLL_INTERVAL_S)
                if not self._running:
                    break
                if self.store_path.exists():
                    mtime = self.store_path.stat().st_mtime
                    if mtime != last_mtime:
                        last_mtime = mtime
                        self._load_store()
                        next_wake = self._get_next_wake_ms()
                        if next_wake:
                            # New jobs found — switch to timer mode
                            self._arm_timer()
                            return

        self._poll_task = asyncio.create_task(_poll_loop())

    def _cancel_poll_task(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

    def _arm_timer(self) -> None:
        """Schedule the next timer tick for this partition."""
        if self._timer_task:
            self._timer_task.cancel()
            self._timer_task = None

        if not self._running:
            return

        next_wake = self._get_next_wake_ms()
        if not next_wake:
            # No pending jobs — start poll to detect future additions
            self._ensure_poll_task()
            return

        # Cancel poll since we have a real timer
        self._cancel_poll_task()

        delay_ms = max(0, next_wake - _now_ms())
        delay_s = delay_ms / 1000

        async def tick():
            await asyncio.sleep(delay_s)
            if self._running:
                await self._on_timer()

        self._timer_task = asyncio.create_task(tick())

    async def _on_timer(self) -> None:
        """Handle timer tick - run due jobs for this partition."""
        self._load_store()
        if not self._store:
            return

        now = _now_ms()
        due_jobs = [
            j for j in self._store.jobs
            if j.enabled
            and j.state.next_run_at_ms
            and now >= j.state.next_run_at_ms
            and classify_job(j) == self._partition
            # Bug4 防重入: skip if already executed this scheduled slot
            and not (j.state.last_run_at_ms and j.state.last_run_at_ms >= j.state.next_run_at_ms)
        ]

        for job in due_jobs:
            await self._execute_job(job)

        self._save_store()
        self._arm_timer()

    async def _execute_job(self, job: CronJob) -> None:
        """Execute a single job via executor or legacy on_job callback."""
        start_ms = _now_ms()
        logger.info("Cron: executing job '{}' ({})", job.name, job.id)

        try:
            response = None

            if job.payload.target_session:
                # Route to target session
                # target_session stores session_id format; convert to session_key for executor
                target_session_key = job.payload.target_session.replace("_", ":", 1)
                if self.executor:
                    ok = await self.executor.send_to_session(
                        target_session_key,
                        job.payload.message,
                        source=f"cron:{job.id}",
                    )
                    if not ok:
                        job.state.last_status = "error"
                        job.state.last_error = f"Failed to send to session {job.payload.target_session}"
                        logger.warning("Cron: job '{}' failed to send to target session '{}'",
                                       job.name, job.payload.target_session)
                        job.state.last_run_at_ms = start_ms
                        job.updated_at_ms = _now_ms()
                        self._advance_schedule(job)
                        return
                else:
                    logger.warning("Cron: job '{}' has target_session but no executor", job.name)
                    job.state.last_status = "skipped"
                    job.state.last_error = "No executor available for target_session"
                    job.state.last_run_at_ms = start_ms
                    job.updated_at_ms = _now_ms()
                    self._advance_schedule(job)
                    return
            elif self.executor:
                response = await self.executor.execute_job(job)
            elif self.on_job:
                response = await self.on_job(job)

            job.state.last_status = "ok"
            job.state.last_error = None
            logger.info("Cron: job '{}' completed", job.name)

        except Exception as e:
            job.state.last_status = "error"
            job.state.last_error = str(e)
            logger.error("Cron: job '{}' failed: {}", job.name, e)

        job.state.last_run_at_ms = start_ms
        job.updated_at_ms = _now_ms()
        self._advance_schedule(job)

    def _advance_schedule(self, job: CronJob) -> None:
        """Advance job schedule after execution (or handle one-shot cleanup)."""
        if job.schedule.kind == "at":
            if job.delete_after_run:
                self._store.jobs = [j for j in self._store.jobs if j.id != job.id]
            else:
                job.enabled = False
                job.state.next_run_at_ms = None
        else:
            # Compute next run
            job.state.next_run_at_ms = _compute_next_run(job.schedule, _now_ms())

    # ========== Public API ==========

    def list_jobs(self, include_disabled: bool = False) -> list[CronJob]:
        """List all jobs."""
        store = self._load_store()
        jobs = store.jobs if include_disabled else [j for j in store.jobs if j.enabled]
        return sorted(jobs, key=lambda j: j.state.next_run_at_ms or float('inf'))

    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        message: str,
        delete_after_run: bool = False,
        target_session: str | None = None,
        source_channel: str | None = None,
    ) -> CronJob:
        """Add a new job."""
        store = self._load_store()
        _validate_schedule_for_add(schedule)
        now = _now_ms()

        job = CronJob(
            id=str(uuid.uuid4())[:8],
            name=name,
            enabled=True,
            schedule=schedule,
            payload=CronPayload(
                message=message,
                target_session=target_session,
                source_channel=source_channel,
            ),
            state=CronJobState(next_run_at_ms=_compute_next_run(schedule, now)),
            created_at_ms=now,
            updated_at_ms=now,
            delete_after_run=delete_after_run,
        )

        store.jobs.append(job)
        self._save_store()
        # Both processes arm timer after add (partition-aware)
        self._arm_timer()

        logger.info("Cron: added job '{}' ({})", name, job.id)
        return job

    def remove_job(self, job_id: str) -> bool:
        """Remove a job by ID."""
        store = self._load_store()
        before = len(store.jobs)
        store.jobs = [j for j in store.jobs if j.id != job_id]
        removed = len(store.jobs) < before

        if removed:
            self._save_store()
            self._arm_timer()
            logger.info("Cron: removed job {}", job_id)

        return removed

    def enable_job(self, job_id: str, enabled: bool = True) -> CronJob | None:
        """Enable or disable a job."""
        store = self._load_store()
        for job in store.jobs:
            if job.id == job_id:
                job.enabled = enabled
                job.updated_at_ms = _now_ms()
                if enabled:
                    job.state.next_run_at_ms = _compute_next_run(job.schedule, _now_ms())
                else:
                    job.state.next_run_at_ms = None
                self._save_store()
                self._arm_timer()
                return job
        return None

    async def run_job(self, job_id: str, force: bool = False) -> bool:
        """Manually run a job."""
        store = self._load_store()
        for job in store.jobs:
            if job.id == job_id:
                if not force and not job.enabled:
                    return False
                await self._execute_job(job)
                self._save_store()
                self._arm_timer()
                return True
        return False

    def status(self) -> dict:
        """Get service status."""
        store = self._load_store()
        return {
            "enabled": self._running,
            "scheduling": self._scheduling,
            "jobs": len(store.jobs),
            "next_wake_at_ms": self._get_next_wake_ms(),
        }
