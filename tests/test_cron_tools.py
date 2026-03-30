"""Tests for ReminderTool and CronTaskTool with isolation."""

import json

import pytest

from nanobot.cron.service import CronService
from nanobot.cron.types import CronJob, CronSchedule, CronPayload, CronJobState, CronStore
from nanobot.agent.tools.reminder import ReminderTool
from nanobot.agent.tools.cron_task import CronTaskTool


@pytest.fixture
def tmp_store(tmp_path):
    """Create a temporary cron store file."""
    store_path = tmp_path / "jobs.json"
    store_path.write_text(json.dumps({"version": 1, "jobs": []}))
    return store_path


@pytest.fixture
def cron_service(tmp_store):
    """Create a CronService with a temp store."""
    return CronService(store_path=tmp_store)


@pytest.fixture
def reminder_tool(cron_service):
    """Create a ReminderTool with context set."""
    tool = ReminderTool(cron_service)
    tool.set_context(
        channel="webchat",
        chat_id="12345",
        session_key="webchat:12345",
        session_id="webchat_12345",
    )
    return tool


@pytest.fixture
def cron_task_tool(cron_service):
    """Create a CronTaskTool with context set."""
    tool = CronTaskTool(cron_service)
    tool.set_context(
        channel="webchat",
        chat_id="12345",
        session_id="webchat_12345",
    )
    return tool


# ─── ReminderTool Tests ───


class TestReminderToolAdd:
    @pytest.mark.asyncio
    async def test_add_requires_target_session(self, reminder_tool):
        result = await reminder_tool.execute(action="add", message="hello")
        assert "target_session is required" in result

    @pytest.mark.asyncio
    async def test_add_requires_message(self, reminder_tool):
        result = await reminder_tool.execute(action="add", target_session="webchat_12345")
        assert "message is required" in result

    @pytest.mark.asyncio
    async def test_add_success_self_session(self, reminder_tool):
        result = await reminder_tool.execute(
            action="add",
            target_session="webchat_12345",
            message="Test reminder",
            every_seconds=60,
        )
        assert "Created reminder" in result
        assert "webchat_12345" in result

    @pytest.mark.asyncio
    async def test_add_rejects_colon_format(self, reminder_tool):
        result = await reminder_tool.execute(
            action="add",
            target_session="webchat:12345",
            message="Test",
            every_seconds=60,
        )
        assert "session_key format" in result

    @pytest.mark.asyncio
    async def test_add_rejects_unrelated_session(self, reminder_tool):
        result = await reminder_tool.execute(
            action="add",
            target_session="webchat_99999",
            message="Test",
            every_seconds=60,
        )
        assert "not allowed" in result

    @pytest.mark.asyncio
    async def test_add_allows_cron_session(self, reminder_tool):
        result = await reminder_tool.execute(
            action="add",
            target_session="cron_abc123",
            message="Test",
            every_seconds=60,
        )
        assert "Created reminder" in result

    @pytest.mark.asyncio
    async def test_add_cli_rejected(self, cron_service):
        """CLI channel should be rejected for reminders."""
        tool = ReminderTool(cron_service)
        tool.set_context(channel="cli", chat_id="local", session_key="cli:local", session_id="cli_local")
        result = await tool.execute(
            action="add",
            target_session="cli_local",
            message="Test",
            every_seconds=60,
        )
        assert "CLI" in result

    @pytest.mark.asyncio
    async def test_add_requires_schedule(self, reminder_tool):
        result = await reminder_tool.execute(
            action="add",
            target_session="webchat_12345",
            message="Test",
        )
        assert "either every_seconds, cron_expr, or at is required" in result


class TestReminderToolIsolation:
    @pytest.mark.asyncio
    async def test_remove_own_reminder(self, reminder_tool, cron_service):
        """Creator can remove their own reminder."""
        result = await reminder_tool.execute(
            action="add",
            target_session="webchat_12345",
            message="My reminder",
            every_seconds=60,
        )
        # Extract job_id from result
        job_id = result.split("id: ")[1].split(")")[0]

        result = await reminder_tool.execute(action="remove", job_id=job_id)
        assert "Removed reminder" in result

    @pytest.mark.asyncio
    async def test_cannot_remove_other_session_reminder(self, cron_service):
        """Cannot remove a reminder created by another session."""
        # Session A creates a reminder
        tool_a = ReminderTool(cron_service)
        tool_a.set_context(channel="webchat", chat_id="111", session_key="webchat:111", session_id="webchat_111")

        result = await tool_a.execute(
            action="add",
            target_session="webchat_111",
            message="Session A's reminder",
            every_seconds=60,
        )
        job_id = result.split("id: ")[1].split(")")[0]

        # Session B tries to remove it
        tool_b = ReminderTool(cron_service)
        tool_b.set_context(channel="webchat", chat_id="222", session_key="webchat:222", session_id="webchat_222")

        result = await tool_b.execute(action="remove", job_id=job_id)
        assert "cannot remove" in result
        assert "webchat_111" in result

    @pytest.mark.asyncio
    async def test_list_filters_by_session(self, cron_service):
        """List without show_all only shows current session's reminders."""
        tool_a = ReminderTool(cron_service)
        tool_a.set_context(channel="webchat", chat_id="111", session_key="webchat:111", session_id="webchat_111")

        tool_b = ReminderTool(cron_service)
        tool_b.set_context(channel="webchat", chat_id="222", session_key="webchat:222", session_id="webchat_222")

        # A creates a reminder
        await tool_a.execute(action="add", target_session="webchat_111", message="A's", every_seconds=60)
        # B creates a reminder
        await tool_b.execute(action="add", target_session="webchat_222", message="B's", every_seconds=60)

        # A lists — should only see A's
        result = await tool_a.execute(action="list")
        assert "A's" in result
        assert "B's" not in result

        # A lists with show_all — should see both
        result = await tool_a.execute(action="list", show_all=True)
        assert "A's" in result
        assert "B's" in result


class TestReminderToolClone:
    def test_clone_preserves_context(self, reminder_tool):
        cloned = reminder_tool.clone()
        assert cloned._session_id == reminder_tool._session_id
        assert cloned._channel == reminder_tool._channel
        assert cloned._cron is reminder_tool._cron


# ─── CronTaskTool Tests ───


class TestCronTaskToolAdd:
    @pytest.mark.asyncio
    async def test_add_requires_owner(self, cron_task_tool):
        result = await cron_task_tool.execute(action="add", message="do something", every_seconds=60)
        assert "owner is required" in result

    @pytest.mark.asyncio
    async def test_add_requires_message(self, cron_task_tool):
        result = await cron_task_tool.execute(action="add", owner="cil")
        assert "message is required" in result

    @pytest.mark.asyncio
    async def test_add_success(self, cron_task_tool):
        result = await cron_task_tool.execute(
            action="add",
            owner="cil",
            message="Run daily scan",
            every_seconds=3600,
        )
        assert "Created task" in result
        assert "owner=cil" in result

    @pytest.mark.asyncio
    async def test_add_one_time(self, cron_task_tool):
        result = await cron_task_tool.execute(
            action="add",
            owner="tushare",
            message="Fetch data",
            at="2026-12-31T23:59:59",
        )
        assert "Created task" in result


class TestCronTaskToolIsolation:
    @pytest.mark.asyncio
    async def test_remove_with_correct_owner(self, cron_task_tool, cron_service):
        """Can remove a task when providing the correct owner."""
        result = await cron_task_tool.execute(
            action="add",
            owner="cil",
            message="CIL task",
            every_seconds=60,
        )
        job_id = result.split("id: ")[1].split(",")[0]

        result = await cron_task_tool.execute(action="remove", job_id=job_id, owner="cil")
        assert "Removed task" in result

    @pytest.mark.asyncio
    async def test_remove_wrong_owner_blocked(self, cron_task_tool, cron_service):
        """Cannot remove a task with wrong owner."""
        result = await cron_task_tool.execute(
            action="add",
            owner="cil",
            message="CIL task",
            every_seconds=60,
        )
        job_id = result.split("id: ")[1].split(",")[0]

        result = await cron_task_tool.execute(action="remove", job_id=job_id, owner="tushare")
        assert "cannot remove" in result
        assert "cil" in result

    @pytest.mark.asyncio
    async def test_list_grouped_by_owner(self, cron_task_tool, cron_service):
        """List shows tasks grouped by owner."""
        await cron_task_tool.execute(action="add", owner="cil", message="CIL task", every_seconds=60)
        await cron_task_tool.execute(action="add", owner="tushare", message="Tushare task", every_seconds=60)

        result = await cron_task_tool.execute(action="list")
        assert "[cil]" in result
        assert "[tushare]" in result
        assert "CIL task" in result
        assert "Tushare task" in result


class TestCronTaskToolClone:
    def test_clone_preserves_context(self, cron_task_tool):
        cloned = cron_task_tool.clone()
        assert cloned._session_id == cron_task_tool._session_id
        assert cloned._cron is cron_task_tool._cron


# ─── Cross-tool isolation ───


class TestCrossToolIsolation:
    @pytest.mark.asyncio
    async def test_reminder_cannot_remove_task(self, reminder_tool, cron_task_tool, cron_service):
        """Reminder tool cannot remove a task job."""
        result = await cron_task_tool.execute(action="add", owner="cil", message="Task", every_seconds=60)
        job_id = result.split("id: ")[1].split(",")[0]

        result = await reminder_tool.execute(action="remove", job_id=job_id)
        assert "is a task, not a reminder" in result

    @pytest.mark.asyncio
    async def test_task_cannot_remove_reminder(self, reminder_tool, cron_task_tool, cron_service):
        """Task tool cannot remove a reminder job."""
        result = await reminder_tool.execute(
            action="add",
            target_session="webchat_12345",
            message="Reminder",
            every_seconds=60,
        )
        job_id = result.split("id: ")[1].split(")")[0]

        result = await cron_task_tool.execute(action="remove", job_id=job_id, owner="cil")
        assert "is a reminder, not a task" in result


# ─── Data compatibility ───


class TestDataCompatibility:
    def test_load_legacy_jobs_without_new_fields(self, tmp_path):
        """Old jobs.json without owner/createdBySession should load fine."""
        store_path = tmp_path / "jobs.json"
        legacy_data = {
            "version": 1,
            "jobs": [{
                "id": "legacy1",
                "name": "Old Job",
                "enabled": True,
                "schedule": {"kind": "every", "everyMs": 60000},
                "payload": {"message": "test", "kind": "agent_turn"},
                "state": {},
                "createdAtMs": 1000,
                "updatedAtMs": 1000,
                "deleteAfterRun": False,
                # No "owner" or "createdBySession" fields
            }]
        }
        store_path.write_text(json.dumps(legacy_data))

        service = CronService(store_path=store_path)
        jobs = service.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].owner == ""
        assert jobs[0].created_by_session == ""

    def test_save_and_reload_new_fields(self, tmp_path):
        """New fields are persisted and reloaded correctly."""
        store_path = tmp_path / "jobs.json"
        store_path.write_text(json.dumps({"version": 1, "jobs": []}))

        service = CronService(store_path=store_path)
        job = service.add_job(
            name="test",
            schedule=CronSchedule(kind="every", every_ms=60000),
            message="hello",
            owner="cil",
            created_by_session="webchat_123",
        )

        # Force reload
        service._store = None
        jobs = service.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].owner == "cil"
        assert jobs[0].created_by_session == "webchat_123"

    def test_get_job(self, tmp_path):
        """get_job returns the correct job or None."""
        store_path = tmp_path / "jobs.json"
        store_path.write_text(json.dumps({"version": 1, "jobs": []}))

        service = CronService(store_path=store_path)
        job = service.add_job(
            name="findme",
            schedule=CronSchedule(kind="every", every_ms=60000),
            message="hello",
        )

        found = service.get_job(job.id)
        assert found is not None
        assert found.name == "findme"

        not_found = service.get_job("nonexistent")
        assert not_found is None
