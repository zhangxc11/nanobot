"""Tests for cron file-lock arbitration, CronExecutor protocol, and target_session."""

import asyncio
import os
import fcntl

import pytest

from nanobot.cron.service import CronService, CronExecutor, _SchedulerLock, _WATCHDOG_INTERVAL_S
from nanobot.cron.types import CronJob, CronPayload, CronSchedule, CronJobState


# ── File Lock Tests ──


class TestSchedulerLock:
    """Tests for the _SchedulerLock class."""

    def test_acquire_lock_success(self, tmp_path):
        lock_path = tmp_path / "cron" / "scheduler.lock"
        lock = _SchedulerLock(lock_path)
        assert lock.try_acquire() is True
        assert lock.is_acquired is True
        lock.release()

    def test_acquire_lock_idempotent(self, tmp_path):
        lock_path = tmp_path / "cron" / "scheduler.lock"
        lock = _SchedulerLock(lock_path)
        assert lock.try_acquire() is True
        assert lock.try_acquire() is True  # Should return True if already acquired
        lock.release()

    def test_two_locks_mutual_exclusion(self, tmp_path):
        lock_path = tmp_path / "cron" / "scheduler.lock"
        lock1 = _SchedulerLock(lock_path)
        lock2 = _SchedulerLock(lock_path)

        assert lock1.try_acquire() is True
        assert lock2.try_acquire() is False  # Should fail - lock1 holds it
        assert lock2.is_acquired is False

        lock1.release()
        assert lock2.try_acquire() is True  # Now lock2 can acquire
        lock2.release()

    def test_release_allows_reacquire(self, tmp_path):
        lock_path = tmp_path / "cron" / "scheduler.lock"
        lock1 = _SchedulerLock(lock_path)
        lock2 = _SchedulerLock(lock_path)

        lock1.try_acquire()
        lock1.release()
        assert lock1.is_acquired is False

        assert lock2.try_acquire() is True
        lock2.release()

    def test_lock_creates_parent_dirs(self, tmp_path):
        lock_path = tmp_path / "deep" / "nested" / "scheduler.lock"
        lock = _SchedulerLock(lock_path)
        assert lock.try_acquire() is True
        assert lock_path.parent.exists()
        lock.release()


# ── CronExecutor Protocol Tests ──


class MockCronExecutor:
    """Mock implementation of CronExecutor for testing."""

    def __init__(self):
        self.executed_jobs: list[CronJob] = []
        self.sent_messages: list[tuple[str, str, str | None]] = []

    async def execute_job(self, job: CronJob) -> str | None:
        self.executed_jobs.append(job)
        return f"Executed {job.name}"

    async def send_to_session(self, target_session_key: str, message: str, source: str | None = None) -> bool:
        self.sent_messages.append((target_session_key, message, source))
        return True


class TestCronExecutorProtocol:
    """Tests for CronExecutor protocol compliance."""

    def test_mock_implements_protocol(self):
        executor = MockCronExecutor()
        assert isinstance(executor, CronExecutor)

    @pytest.mark.asyncio
    async def test_executor_execute_job(self):
        executor = MockCronExecutor()
        job = CronJob(
            id="test1",
            name="Test Job",
            payload=CronPayload(message="hello"),
        )
        result = await executor.execute_job(job)
        assert result == "Executed Test Job"
        assert len(executor.executed_jobs) == 1

    @pytest.mark.asyncio
    async def test_executor_send_to_session(self):
        executor = MockCronExecutor()
        ok = await executor.send_to_session("session:123", "hello", source="cron:abc")
        assert ok is True
        assert executor.sent_messages == [("session:123", "hello", "cron:abc")]


# ── CronService with File Lock Tests ──


class TestCronServiceFileLock:
    """Tests for CronService file-lock arbitration."""

    @pytest.mark.asyncio
    async def test_start_acquires_lock(self, tmp_path):
        store_path = tmp_path / "cron" / "jobs.json"
        service = CronService(store_path)
        await service.start()
        try:
            assert service._scheduling is True
            assert service._lock is not None
            assert service._lock.is_acquired is True
        finally:
            service.stop()

    @pytest.mark.asyncio
    async def test_second_service_enters_standby(self, tmp_path):
        store_path = tmp_path / "cron" / "jobs.json"
        service1 = CronService(store_path)
        service2 = CronService(store_path)

        await service1.start()
        try:
            await service2.start()
            try:
                assert service1._scheduling is True
                assert service2._scheduling is False
                assert service2._watchdog_task is not None
            finally:
                service2.stop()
        finally:
            service1.stop()

    @pytest.mark.asyncio
    async def test_standby_takes_over_after_release(self, tmp_path):
        """Simulate watchdog takeover when the scheduler releases the lock."""
        store_path = tmp_path / "cron" / "jobs.json"

        # Use a very short watchdog interval for testing
        import nanobot.cron.service as svc
        original_interval = svc._WATCHDOG_INTERVAL_S
        svc._WATCHDOG_INTERVAL_S = 0.1  # 100ms for fast test

        service1 = CronService(store_path)
        service2 = CronService(store_path)

        await service1.start()
        await service2.start()

        assert service1._scheduling is True
        assert service2._scheduling is False

        # Release lock from service1
        service1.stop()

        # Wait for watchdog to pick up
        await asyncio.sleep(0.3)

        try:
            assert service2._scheduling is True
            assert service2._lock.is_acquired is True
        finally:
            service2.stop()
            svc._WATCHDOG_INTERVAL_S = original_interval

    @pytest.mark.asyncio
    async def test_stop_releases_lock(self, tmp_path):
        store_path = tmp_path / "cron" / "jobs.json"
        service = CronService(store_path)
        await service.start()
        assert service._scheduling is True

        service.stop()
        assert service._scheduling is False
        assert service._lock is None

    @pytest.mark.asyncio
    async def test_status_includes_scheduling_field(self, tmp_path):
        store_path = tmp_path / "cron" / "jobs.json"
        service = CronService(store_path)
        await service.start()
        try:
            status = service.status()
            assert "scheduling" in status
            assert status["scheduling"] is True
        finally:
            service.stop()


# ── CronService with Executor Tests ──


class TestCronServiceExecutor:
    """Tests for CronService using CronExecutor."""

    @pytest.mark.asyncio
    async def test_execute_job_via_executor(self, tmp_path):
        store_path = tmp_path / "cron" / "jobs.json"
        executor = MockCronExecutor()
        service = CronService(store_path, executor=executor)

        job = service.add_job(
            name="test-exec",
            schedule=CronSchedule(kind="every", every_ms=100),
            message="hello executor",
        )

        await service.start()
        try:
            await asyncio.sleep(0.2)
            assert len(executor.executed_jobs) >= 1
            assert executor.executed_jobs[0].name == "test-exec"
        finally:
            service.stop()

    @pytest.mark.asyncio
    async def test_target_session_routes_to_send_to_session(self, tmp_path):
        store_path = tmp_path / "cron" / "jobs.json"
        executor = MockCronExecutor()
        service = CronService(store_path, executor=executor)

        job = service.add_job(
            name="target-test",
            schedule=CronSchedule(kind="every", every_ms=100),
            message="hello session",
            target_session="webchat_user123",
        )

        await service.start()
        try:
            await asyncio.sleep(0.2)
            assert len(executor.sent_messages) >= 1
            target, msg, source = executor.sent_messages[0]
            # send_to_session receives session_key format (id→key conversion)
            assert target == "webchat:user123"
            assert msg == "hello session"
            assert source == f"cron:{job.id}"
            # Should NOT have called execute_job
            assert len(executor.executed_jobs) == 0
        finally:
            service.stop()

    @pytest.mark.asyncio
    async def test_legacy_on_job_callback_still_works(self, tmp_path):
        """Backward compatibility: on_job callback works when no executor."""
        store_path = tmp_path / "cron" / "jobs.json"
        called = []

        async def on_job(job):
            called.append(job.id)
            return "done"

        service = CronService(store_path, on_job=on_job)
        service.add_job(
            name="legacy-test",
            schedule=CronSchedule(kind="every", every_ms=100),
            message="hello legacy",
        )

        await service.start()
        try:
            await asyncio.sleep(0.2)
            assert len(called) >= 1
        finally:
            service.stop()

    @pytest.mark.asyncio
    async def test_executor_preferred_over_on_job(self, tmp_path):
        """When both executor and on_job are provided, executor is used."""
        store_path = tmp_path / "cron" / "jobs.json"
        executor = MockCronExecutor()
        on_job_called = []

        async def on_job(job):
            on_job_called.append(job.id)

        service = CronService(store_path, on_job=on_job, executor=executor)
        service.add_job(
            name="priority-test",
            schedule=CronSchedule(kind="every", every_ms=100),
            message="hello",
        )

        await service.start()
        try:
            await asyncio.sleep(0.2)
            assert len(executor.executed_jobs) >= 1
            assert len(on_job_called) == 0
        finally:
            service.stop()

    @pytest.mark.asyncio
    async def test_target_session_subagent_id_to_key_conversion(self, tmp_path):
        """Subagent session_id should be converted to session_key for send_to_session."""
        store_path = tmp_path / "cron" / "jobs.json"
        executor = MockCronExecutor()
        service = CronService(store_path, executor=executor)

        service.add_job(
            name="subagent-convert",
            schedule=CronSchedule(kind="every", every_ms=100),
            message="hello subagent",
            target_session="subagent_webchat_1773591411_a1b2c3d4",
        )

        await service.start()
        try:
            await asyncio.sleep(0.2)
            assert len(executor.sent_messages) >= 1
            target, msg, source = executor.sent_messages[0]
            # First underscore replaced with colon
            assert target == "subagent:webchat_1773591411_a1b2c3d4"
            assert msg == "hello subagent"
        finally:
            service.stop()


# ── target_session Payload Serialization Tests ──


class TestTargetSessionSerialization:
    """Tests for target_session field in CronPayload serialization."""

    def test_add_job_with_target_session(self, tmp_path):
        store_path = tmp_path / "cron" / "jobs.json"
        service = CronService(store_path)
        job = service.add_job(
            name="target-serial",
            schedule=CronSchedule(kind="every", every_ms=60000),
            message="hello",
            target_session="webchat_1773591411",
        )
        assert job.payload.target_session == "webchat_1773591411"

    def test_target_session_persisted_to_disk(self, tmp_path):
        import json
        store_path = tmp_path / "cron" / "jobs.json"
        service = CronService(store_path)
        service.add_job(
            name="persist-test",
            schedule=CronSchedule(kind="every", every_ms=60000),
            message="hello",
            target_session="webchat_1773591411",
        )

        # Read from disk
        data = json.loads(store_path.read_text())
        payload = data["jobs"][0]["payload"]
        assert payload["targetSession"] == "webchat_1773591411"

    def test_target_session_loaded_from_disk(self, tmp_path):
        import json
        store_path = tmp_path / "cron" / "jobs.json"
        service = CronService(store_path)
        service.add_job(
            name="load-test",
            schedule=CronSchedule(kind="every", every_ms=60000),
            message="hello",
            target_session="cli_direct",
        )

        # Create a new service to reload from disk
        service2 = CronService(store_path)
        jobs = service2.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].payload.target_session == "cli_direct"

    def test_cron_payload_target_session_stores_id(self, tmp_path):
        """Verify CronPayload stores session_id format (underscore-separated)."""
        store_path = tmp_path / "cron" / "jobs.json"
        service = CronService(store_path)
        job = service.add_job(
            name="id-format-test",
            schedule=CronSchedule(kind="every", every_ms=60000),
            message="hello",
            target_session="subagent_webchat_1773591411_a1b2c3d4",
        )
        # Should store session_id format (no colons)
        assert ":" not in job.payload.target_session
        assert job.payload.target_session == "subagent_webchat_1773591411_a1b2c3d4"


# ── CronTool name Parameter Tests ──


class TestCronToolNameParameter:
    """Tests for the name parameter in CronTool."""

    def _make_tool(self):
        from nanobot.agent.tools.cron import CronTool
        from nanobot.cron.service import CronService
        from pathlib import Path
        import tempfile

        tmp = tempfile.mkdtemp()
        store_path = Path(tmp) / "cron" / "jobs.json"
        service = CronService(store_path)
        tool = CronTool(service)
        tool.set_context("webchat", "user1", session_key="webchat:user1")
        return tool, service

    @pytest.mark.asyncio
    async def test_name_used_when_provided(self):
        """When LLM passes name, job.name should use the provided name."""
        tool, service = self._make_tool()
        result = await tool.execute(
            action="add",
            message="Check GitHub stars and report the count",
            name="GitHub星数检查",
            every_seconds=600,
        )
        assert "Created job" in result
        assert "GitHub星数检查" in result
        jobs = service.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].name == "GitHub星数检查"

    @pytest.mark.asyncio
    async def test_name_fallback_to_message_prefix(self):
        """When name is not provided, job.name should fallback to message[:50]."""
        tool, service = self._make_tool()
        msg = "This is a moderately long message for the cron job"
        result = await tool.execute(
            action="add",
            message=msg,
            every_seconds=60,
        )
        assert "Created job" in result
        jobs = service.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].name == msg[:50]

    @pytest.mark.asyncio
    async def test_name_fallback_truncates_long_message(self):
        """Fallback name should truncate message at 50 chars."""
        tool, service = self._make_tool()
        long_msg = "A" * 100
        result = await tool.execute(
            action="add",
            message=long_msg,
            every_seconds=60,
        )
        assert "Created job" in result
        jobs = service.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].name == "A" * 50
        assert len(jobs[0].name) == 50

    @pytest.mark.asyncio
    async def test_name_empty_string_falls_back(self):
        """Empty string name should fallback to message[:50]."""
        tool, service = self._make_tool()
        result = await tool.execute(
            action="add",
            message="hello world",
            name="",
            every_seconds=60,
        )
        assert "Created job" in result
        jobs = service.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].name == "hello world"

    @pytest.mark.asyncio
    async def test_name_in_schema(self):
        """Tool schema should include name parameter."""
        from nanobot.agent.tools.cron import CronTool
        from nanobot.cron.service import CronService
        from pathlib import Path
        import tempfile

        tmp = tempfile.mkdtemp()
        store_path = Path(tmp) / "cron" / "jobs.json"
        service = CronService(store_path)
        tool = CronTool(service)
        props = tool.parameters["properties"]
        assert "name" in props
        assert props["name"]["type"] == "string"


# ── CronTool tz Description Tests ──


class TestCronToolTzDescription:
    """Tests for tz parameter description in CronTool schema."""

    def test_tz_description_mentions_cron_expr_only(self):
        from nanobot.agent.tools.cron import CronTool
        from nanobot.cron.service import CronService
        from pathlib import Path
        import tempfile

        tmp = tempfile.mkdtemp()
        store_path = Path(tmp) / "cron" / "jobs.json"
        service = CronService(store_path)
        tool = CronTool(service)
        tz_desc = tool.parameters["properties"]["tz"]["description"]
        assert "cron_expr" in tz_desc
        assert "NOT" in tz_desc or "not" in tz_desc.lower()


# ── CronTool target_session Security Tests ──


class TestCronToolTargetSessionSecurity:
    """Tests for target_session security validation in CronTool."""

    def _make_tool(self, session_key: str = "webchat:user1", session_id: str | None = None,
                   sessions_dir=None):
        from nanobot.agent.tools.cron import CronTool
        from nanobot.cron.service import CronService
        from pathlib import Path
        import tempfile

        tmp = tempfile.mkdtemp()
        store_path = Path(tmp) / "cron" / "jobs.json"
        service = CronService(store_path)
        tool = CronTool(service)
        tool.set_context("webchat", "user1", session_key=session_key,
                         session_id=session_id or session_key.replace(":", "_"),
                         sessions_dir=sessions_dir)
        return tool

    @staticmethod
    def _make_sessions_dir(tmp_path, session_ids: list[str]):
        """Create a fake sessions dir with empty .jsonl files for given session IDs."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir(exist_ok=True)
        for sid in session_ids:
            (sessions_dir / f"{sid}.jsonl").touch()
        return sessions_dir

    @pytest.mark.asyncio
    async def test_validate_target_session_self_id(self):
        """Self session_id should be allowed."""
        tool = self._make_tool("webchat:1773591411")
        result = await tool.execute(
            action="add",
            message="test",
            every_seconds=60,
            target_session="webchat_1773591411",
        )
        assert "Created job" in result

    @pytest.mark.asyncio
    async def test_validate_target_session_subagent_id(self, tmp_path):
        """Subagent session_id should be allowed via is_child_of resolution."""
        parent_id = "webchat_1773591411"
        child_id = "subagent_webchat_1773591411_73d5e1c5"
        sessions_dir = self._make_sessions_dir(tmp_path, [parent_id, child_id])
        tool = self._make_tool("webchat:1773591411", sessions_dir=sessions_dir)
        result = await tool.execute(
            action="add",
            message="test",
            every_seconds=60,
            target_session=child_id,
        )
        assert "Created job" in result

    @pytest.mark.asyncio
    async def test_validate_target_session_cron_id(self):
        """Cron session_id should be allowed."""
        tool = self._make_tool("webchat:1773591411")
        result = await tool.execute(
            action="add",
            message="test",
            every_seconds=60,
            target_session="cron_abc123",
        )
        assert "Created job" in result

    @pytest.mark.asyncio
    async def test_validate_target_session_foreign_id(self, tmp_path):
        """Unrelated session_id should be rejected."""
        parent_id = "webchat_1773591411"
        foreign_id = "webchat_9999999999"
        sessions_dir = self._make_sessions_dir(tmp_path, [parent_id, foreign_id])
        tool = self._make_tool("webchat:1773591411", sessions_dir=sessions_dir)
        result = await tool.execute(
            action="add",
            message="test",
            every_seconds=60,
            target_session=foreign_id,
        )
        assert "Error" in result
        assert "not allowed" in result

    @pytest.mark.asyncio
    async def test_validate_target_session_key_format_rejected(self):
        """Session_key format (with ':') should be rejected."""
        tool = self._make_tool("webchat:1773591411")
        result = await tool.execute(
            action="add",
            message="test",
            every_seconds=60,
            target_session="webchat:1773591411",
        )
        assert "Error" in result
        assert "session_key format" in result

    @pytest.mark.asyncio
    async def test_no_target_session_works(self):
        tool = self._make_tool("webchat:user1")
        result = await tool.execute(
            action="add",
            message="test",
            every_seconds=60,
        )
        assert "Created job" in result

    @pytest.mark.asyncio
    async def test_validate_target_session_no_sessions_dir_rejects(self):
        """Without sessions_dir, non-self/non-cron targets should be rejected."""
        tool = self._make_tool("webchat:1773591411")  # no sessions_dir
        result = await tool.execute(
            action="add",
            message="test",
            every_seconds=60,
            target_session="subagent_webchat_1773591411_73d5e1c5",
        )
        assert "Error" in result
        assert "not allowed" in result

    @pytest.mark.asyncio
    async def test_list_shows_target_session(self, tmp_path):
        tool = self._make_tool("webchat:1773591411")
        await tool.execute(
            action="add",
            message="test job",
            every_seconds=60,
            target_session="webchat_1773591411",
        )
        result = await tool.execute(action="list")
        assert "webchat_1773591411" in result
        assert "→" in result


# ── Existing Tests (preserved) ──


def test_add_job_rejects_unknown_timezone(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")

    with pytest.raises(ValueError, match="unknown timezone 'America/Vancovuer'"):
        service.add_job(
            name="tz typo",
            schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="America/Vancovuer"),
            message="hello",
        )

    assert service.list_jobs(include_disabled=True) == []


def test_add_job_accepts_valid_timezone(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")

    job = service.add_job(
        name="tz ok",
        schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="America/Vancouver"),
        message="hello",
    )

    assert job.schedule.tz == "America/Vancouver"
    assert job.state.next_run_at_ms is not None


@pytest.mark.asyncio
async def test_running_service_honors_external_disable(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    called: list[str] = []

    async def on_job(job) -> None:
        called.append(job.id)

    service = CronService(store_path, on_job=on_job)
    job = service.add_job(
        name="external-disable",
        schedule=CronSchedule(kind="every", every_ms=200),
        message="hello",
    )
    await service.start()
    try:
        external = CronService(store_path)
        updated = external.enable_job(job.id, enabled=False)
        assert updated is not None
        assert updated.enabled is False

        await asyncio.sleep(0.35)
        assert called == []
    finally:
        service.stop()
