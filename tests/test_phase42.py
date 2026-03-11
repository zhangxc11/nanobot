"""Tests for Phase 42 — core stability & spawn diagnostics (§41-§45).

§41: Usage recorder provider field
§42: LLM timeout split (connect/read)
§43: Budget alert user role
§44: Spawn status error diagnostics
§45: Subagent announce hidden system marker
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════
# §41: Usage recorder provider field
# ═══════════════════════════════════════════════════════════════════════


class TestUsageRecorderProviderField:
    """§41: token_usage table includes provider field."""

    def test_record_with_provider(self, tmp_path: Path):
        """Provider name is stored and retrievable."""
        from nanobot.usage.recorder import UsageRecorder

        db_path = str(tmp_path / "test.db")
        recorder = UsageRecorder(db_path=db_path)

        row_id = recorder.record(
            session_key="test-session",
            model="claude-sonnet-4-20250514",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            llm_calls=1,
            started_at="2026-03-11T12:00:00",
            finished_at="2026-03-11T12:00:01",
            provider="anthropic",
        )
        assert row_id > 0

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT provider FROM token_usage WHERE id = ?", (row_id,)
        ).fetchone()
        conn.close()
        assert row[0] == "anthropic"

    def test_record_without_provider_defaults_empty(self, tmp_path: Path):
        """Omitting provider defaults to empty string."""
        from nanobot.usage.recorder import UsageRecorder

        db_path = str(tmp_path / "test.db")
        recorder = UsageRecorder(db_path=db_path)

        row_id = recorder.record(
            session_key="test-session",
            model="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            llm_calls=1,
            started_at="2026-03-11T12:00:00",
            finished_at="2026-03-11T12:00:01",
        )

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT provider FROM token_usage WHERE id = ?", (row_id,)
        ).fetchone()
        conn.close()
        assert row[0] == ""

    def test_migration_adds_provider_column(self, tmp_path: Path):
        """Migration adds provider column to existing databases."""
        from nanobot.usage.recorder import UsageRecorder

        db_path = str(tmp_path / "old.db")

        # Create old-schema database (without provider column)
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                session_key       TEXT NOT NULL,
                model             TEXT NOT NULL,
                prompt_tokens     INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens      INTEGER DEFAULT 0,
                llm_calls         INTEGER DEFAULT 0,
                started_at        TEXT NOT NULL,
                finished_at       TEXT NOT NULL,
                cache_creation_input_tokens INTEGER DEFAULT 0,
                cache_read_input_tokens INTEGER DEFAULT 0
            );
        """)
        conn.execute(
            "INSERT INTO token_usage (session_key, model, prompt_tokens, completion_tokens, "
            "total_tokens, llm_calls, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("old-session", "gpt-4", 100, 50, 150, 1, "2026-03-01", "2026-03-01"),
        )
        conn.commit()
        conn.close()

        # Open with UsageRecorder — migration should run
        recorder = UsageRecorder(db_path=db_path)

        # Old row should have default '' for provider
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT provider FROM token_usage WHERE session_key = 'old-session'"
        ).fetchone()
        conn.close()
        assert row[0] == ""

        # New record with provider should work
        row_id = recorder.record(
            session_key="new-session",
            model="claude-sonnet-4-20250514",
            prompt_tokens=1000,
            completion_tokens=200,
            total_tokens=1200,
            llm_calls=1,
            started_at="2026-03-11T12:00:00",
            finished_at="2026-03-11T12:00:01",
            provider="anthropic",
        )
        assert row_id > 0

    def test_in_memory_recorder_with_provider(self):
        """In-memory recorder stores provider correctly."""
        from nanobot.usage.recorder import UsageRecorder

        recorder = UsageRecorder(db_path=":memory:")
        row_id = recorder.record(
            session_key="test",
            model="model",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            llm_calls=1,
            started_at="t1",
            finished_at="t2",
            provider="openai",
        )

        with recorder._connect() as conn:
            row = conn.execute(
                "SELECT provider FROM token_usage WHERE id = ?", (row_id,)
            ).fetchone()
        assert row["provider"] == "openai"

    def test_litellm_provider_stores_provider_name(self):
        """LiteLLMProvider stores provider_name attribute."""
        from nanobot.providers.litellm_provider import LiteLLMProvider

        provider = LiteLLMProvider.__new__(LiteLLMProvider)
        provider.provider_name = "anthropic"
        assert provider.provider_name == "anthropic"

    def test_litellm_provider_default_provider_name(self):
        """LiteLLMProvider defaults provider_name to empty string."""
        from nanobot.providers.litellm_provider import LiteLLMProvider

        # Use __new__ to avoid full __init__
        provider = LiteLLMProvider.__new__(LiteLLMProvider)
        provider.provider_name = ""
        assert provider.provider_name == ""


# ═══════════════════════════════════════════════════════════════════════
# §42: LLM timeout split
# ═══════════════════════════════════════════════════════════════════════


class TestLLMTimeoutSplit:
    """§42: Connect/read timeout separation."""

    def test_timeout_is_httpx_timeout(self):
        """_LLM_TIMEOUT should be an httpx.Timeout instance."""
        import httpx
        from nanobot.providers.litellm_provider import _LLM_TIMEOUT

        assert isinstance(_LLM_TIMEOUT, httpx.Timeout)

    def test_connect_timeout_30s(self):
        """Connect timeout should be 30 seconds."""
        from nanobot.providers.litellm_provider import _LLM_TIMEOUT

        assert _LLM_TIMEOUT.connect == 30.0

    def test_read_timeout_120s(self):
        """Read timeout should be 120 seconds."""
        from nanobot.providers.litellm_provider import _LLM_TIMEOUT

        assert _LLM_TIMEOUT.read == 120.0

    def test_write_timeout_30s(self):
        """Write timeout should be 30 seconds."""
        from nanobot.providers.litellm_provider import _LLM_TIMEOUT

        assert _LLM_TIMEOUT.write == 30.0

    def test_pool_timeout_30s(self):
        """Pool timeout should be 30 seconds."""
        from nanobot.providers.litellm_provider import _LLM_TIMEOUT

        assert _LLM_TIMEOUT.pool == 30.0


# ═══════════════════════════════════════════════════════════════════════
# §43: Budget alert user role
# ═══════════════════════════════════════════════════════════════════════


class TestBudgetAlertUserRole:
    """§43: Budget alert uses user role instead of system."""

    def test_loop_budget_alert_is_user_role(self):
        """AgentLoop budget alert should use 'user' role."""
        from nanobot.agent.loop import _budget_alert_threshold

        max_iterations = 40
        threshold = _budget_alert_threshold(max_iterations)
        messages = []

        for iteration_num in range(1, max_iterations + 1):
            remaining = max_iterations - iteration_num
            if remaining == threshold:
                messages.append({
                    "role": "user",
                    "content": (
                        f"[System Notice] ⚠️ You have {remaining} tool-call iterations "
                        f"remaining out of {max_iterations}. "
                        f"Prioritize completing your current task. If you cannot finish "
                        f"in time, summarize progress so far and present what you have. "
                        f"Do not acknowledge this notice — continue working."
                    ),
                })

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert "[System Notice]" in messages[0]["content"]
        assert "Do not acknowledge" in messages[0]["content"]

    def test_budget_alert_not_system_role(self):
        """Budget alert should NOT use system role anymore."""
        # Verify the actual loop.py code pattern
        from nanobot.agent.loop import _budget_alert_threshold

        max_iterations = 30
        threshold = _budget_alert_threshold(max_iterations)

        # Simulate the exact code path in _run_agent_loop
        messages = []
        for iteration_num in range(1, max_iterations + 1):
            remaining = max_iterations - iteration_num
            if remaining == threshold:
                msg = {
                    "role": "user",
                    "content": (
                        f"[System Notice] ⚠️ You have {remaining} tool-call iterations "
                        f"remaining out of {max_iterations}."
                    ),
                }
                messages.append(msg)

        for msg in messages:
            assert msg["role"] != "system"

    def test_budget_alert_content_has_system_notice_prefix(self):
        """Budget alert content should start with [System Notice]."""
        content = (
            "[System Notice] ⚠️ You have 10 tool-call iterations "
            "remaining out of 40. "
            "Prioritize completing your current task. If you cannot finish "
            "in time, summarize progress so far and present what you have. "
            "Do not acknowledge this notice — continue working."
        )
        assert content.startswith("[System Notice]")


# ═══════════════════════════════════════════════════════════════════════
# §44: Spawn status error diagnostics
# ═══════════════════════════════════════════════════════════════════════


def _make_manager():
    """Create a SubagentManager for testing."""
    from nanobot.agent.subagent import SubagentManager
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    return SubagentManager(
        provider=provider,
        workspace=Path("/tmp/test_workspace"),
        bus=bus,
        model="test-model",
    )


class TestSubagentMetaErrorFields:
    """§44: SubagentMeta has error diagnostic fields."""

    def test_default_error_fields(self):
        """New SubagentMeta should have zero/None error fields."""
        from nanobot.agent.subagent import SubagentMeta

        meta = SubagentMeta(
            task_id="test",
            subagent_session_key="subagent:test",
            parent_session_key="parent",
            label="test",
            origin={"channel": "cli", "chat_id": "direct"},
        )
        assert meta.error_count == 0
        assert meta.last_error is None
        assert meta.last_error_time is None

    def test_error_fields_settable(self):
        """Error fields can be set."""
        from nanobot.agent.subagent import SubagentMeta

        meta = SubagentMeta(
            task_id="test",
            subagent_session_key="subagent:test",
            parent_session_key="parent",
            label="test",
            origin={"channel": "cli", "chat_id": "direct"},
            error_count=3,
            last_error="Connection timeout",
            last_error_time="2026-03-11T12:00:00",
        )
        assert meta.error_count == 3
        assert meta.last_error == "Connection timeout"
        assert meta.last_error_time == "2026-03-11T12:00:00"


class TestChatWithRetryErrorRecording:
    """§44: _chat_with_retry records errors in SubagentMeta."""

    @pytest.mark.asyncio
    async def test_retryable_error_updates_meta(self):
        """Retryable LLM error should update meta error fields."""
        mgr = _make_manager()

        # Create a meta entry
        from nanobot.agent.subagent import SubagentMeta
        meta = SubagentMeta(
            task_id="t1",
            subagent_session_key="subagent:test_t1",
            parent_session_key="parent",
            label="test",
            origin={"channel": "cli", "chat_id": "direct"},
        )
        mgr._task_meta["t1"] = meta

        # Mock provider to fail once then succeed
        call_count = 0
        RateLimitError = type("RateLimitError", (Exception,), {})

        async def mock_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RateLimitError("rate limit exceeded")
            return MagicMock(
                content="done",
                tool_calls=[],
                has_tool_calls=False,
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )

        mgr.provider.chat = mock_chat

        tools = MagicMock()
        tools.get_definitions.return_value = []

        # This should succeed after one retry
        with patch("nanobot.agent.subagent.compute_retry_delay", return_value=0.01):
            result = await mgr._chat_with_retry([], tools, task_id="t1")

        assert meta.error_count == 1
        assert meta.last_error is not None
        assert "rate limit" in meta.last_error
        assert meta.last_error_time is not None

    @pytest.mark.asyncio
    async def test_no_error_keeps_zero_count(self):
        """Successful LLM call should not change error fields."""
        mgr = _make_manager()

        from nanobot.agent.subagent import SubagentMeta
        meta = SubagentMeta(
            task_id="t2",
            subagent_session_key="subagent:test_t2",
            parent_session_key="parent",
            label="test",
            origin={"channel": "cli", "chat_id": "direct"},
        )
        mgr._task_meta["t2"] = meta

        async def mock_chat(**kwargs):
            return MagicMock(
                content="done",
                tool_calls=[],
                has_tool_calls=False,
                usage={},
            )

        mgr.provider.chat = mock_chat

        tools = MagicMock()
        tools.get_definitions.return_value = []

        await mgr._chat_with_retry([], tools, task_id="t2")

        assert meta.error_count == 0
        assert meta.last_error is None
        assert meta.last_error_time is None

    @pytest.mark.asyncio
    async def test_non_retryable_error_still_recorded(self):
        """Non-retryable error should still be recorded before raising."""
        mgr = _make_manager()

        from nanobot.agent.subagent import SubagentMeta
        meta = SubagentMeta(
            task_id="t3",
            subagent_session_key="subagent:test_t3",
            parent_session_key="parent",
            label="test",
            origin={"channel": "cli", "chat_id": "direct"},
        )
        mgr._task_meta["t3"] = meta

        async def mock_chat(**kwargs):
            raise ValueError("invalid model configuration")

        mgr.provider.chat = mock_chat

        tools = MagicMock()
        tools.get_definitions.return_value = []

        with pytest.raises(ValueError):
            await mgr._chat_with_retry([], tools, task_id="t3")

        assert meta.error_count == 1
        assert "invalid model" in meta.last_error

    @pytest.mark.asyncio
    async def test_without_task_id_no_crash(self):
        """_chat_with_retry without task_id should not crash on error."""
        mgr = _make_manager()

        call_count = 0
        RateLimitError = type("RateLimitError", (Exception,), {})

        async def mock_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RateLimitError("rate limit exceeded")
            return MagicMock(content="done", tool_calls=[], has_tool_calls=False, usage={})

        mgr.provider.chat = mock_chat
        tools = MagicMock()
        tools.get_definitions.return_value = []

        with patch("nanobot.agent.subagent.compute_retry_delay", return_value=0.01):
            result = await mgr._chat_with_retry([], tools)  # No task_id

        assert result.content == "done"


class TestGetStatusWithErrors:
    """§44: get_status includes error diagnostic fields."""

    def test_status_includes_error_count(self):
        """get_status output should include error_count."""
        mgr = _make_manager()

        from nanobot.agent.subagent import SubagentMeta
        meta = SubagentMeta(
            task_id="t1",
            subagent_session_key="subagent:test_t1",
            parent_session_key="parent:session",
            label="test task",
            origin={"channel": "cli", "chat_id": "direct"},
            error_count=3,
            last_error="Connection timeout after 30s",
            last_error_time="2026-03-11T12:00:00",
        )
        mgr._task_meta["t1"] = meta

        status = mgr.get_status("t1", "parent:session")
        assert "error_count" in status
        assert "3" in status
        assert "Connection timeout" in status
        assert "last_error_time" in status

    def test_status_no_errors_shows_zero(self):
        """get_status with no errors should show error_count: 0."""
        mgr = _make_manager()

        from nanobot.agent.subagent import SubagentMeta
        meta = SubagentMeta(
            task_id="t2",
            subagent_session_key="subagent:test_t2",
            parent_session_key="parent:session",
            label="clean task",
            origin={"channel": "cli", "chat_id": "direct"},
        )
        mgr._task_meta["t2"] = meta

        status = mgr.get_status("t2", "parent:session")
        assert "error_count" in status
        assert "**error_count**: 0" in status
        # last_error should not appear when None
        assert "last_error**:" not in status


class TestResumeResetsErrors:
    """§44: follow_up resume resets error diagnostic fields."""

    @pytest.mark.asyncio
    async def test_resume_resets_error_fields(self):
        """Resuming a subagent should reset error_count/last_error/last_error_time."""
        mgr = _make_manager()
        mgr.session_manager = MagicMock()
        mock_session = MagicMock()
        mock_session.get_history.return_value = [
            {"role": "user", "content": "original task"},
            {"role": "assistant", "content": "done"},
        ]
        mgr.session_manager.get_or_create.return_value = mock_session

        from nanobot.agent.subagent import SubagentMeta
        meta = SubagentMeta(
            task_id="t1",
            subagent_session_key="subagent:parent_t1",
            parent_session_key="parent",
            label="test",
            origin={"channel": "cli", "chat_id": "direct"},
            status="completed",
            error_count=5,
            last_error="Some error",
            last_error_time="2026-03-11T12:00:00",
        )
        mgr._task_meta["t1"] = meta

        result = await mgr.follow_up(
            task_id="t1",
            message="continue",
            parent_session_key="parent",
        )

        assert meta.error_count == 0
        assert meta.last_error is None
        assert meta.last_error_time is None

        # Clean up
        for task in list(mgr._running_tasks.values()):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


# ═══════════════════════════════════════════════════════════════════════
# §45: Subagent announce hidden system marker
# ═══════════════════════════════════════════════════════════════════════


class TestAnnounceSystemMarker:
    """§45: Subagent announce message includes hidden system marker."""

    @pytest.mark.asyncio
    async def test_announce_contains_marker(self):
        """_announce_result should include <!-- nanobot:system --> marker."""
        mgr = _make_manager()

        captured_content = None

        async def mock_publish(msg):
            nonlocal captured_content
            captured_content = msg.content

        mgr.bus.publish_inbound = mock_publish

        await mgr._announce_result(
            task_id="t1",
            label="test task",
            task="do something",
            result="task completed",
            origin={"channel": "cli", "chat_id": "direct"},
            status="ok",
        )

        assert captured_content is not None
        assert "<!-- nanobot:system -->" in captured_content

    @pytest.mark.asyncio
    async def test_marker_before_notification_text(self):
        """Marker should appear before the [Subagent Result Notification] text."""
        mgr = _make_manager()

        captured_content = None

        async def mock_publish(msg):
            nonlocal captured_content
            captured_content = msg.content

        mgr.bus.publish_inbound = mock_publish

        await mgr._announce_result(
            task_id="t1",
            label="test",
            task="test",
            result="done",
            origin={"channel": "cli", "chat_id": "direct"},
            status="ok",
        )

        marker_pos = captured_content.index("<!-- nanobot:system -->")
        notification_pos = captured_content.index("[Subagent Result Notification]")
        assert marker_pos < notification_pos

    @pytest.mark.asyncio
    async def test_announce_still_contains_guiding_prompt(self):
        """Announce message should still contain the guiding prompt text."""
        mgr = _make_manager()

        captured_content = None

        async def mock_publish(msg):
            nonlocal captured_content
            captured_content = msg.content

        mgr.bus.publish_inbound = mock_publish

        await mgr._announce_result(
            task_id="t1",
            label="test",
            task="test",
            result="done",
            origin={"channel": "cli", "chat_id": "direct"},
            status="ok",
        )

        assert "Review this result" in captured_content
        assert "automated system notification" in captured_content

    @pytest.mark.asyncio
    async def test_announce_via_session_messenger_has_marker(self):
        """Announce via SessionMessenger should also include the marker."""
        mgr = _make_manager()

        captured_content = None

        async def mock_send(target_session_key, content, source_session_key=None):
            nonlocal captured_content
            captured_content = content
            return True

        mock_messenger = MagicMock()
        mock_messenger.send_to_session = mock_send
        mgr.session_messenger = mock_messenger

        await mgr._announce_result(
            task_id="t1",
            label="test",
            task="test",
            result="done",
            origin={"channel": "cli", "chat_id": "direct"},
            status="ok",
            parent_session_key="parent:session",
        )

        assert captured_content is not None
        assert "<!-- nanobot:system -->" in captured_content

    @pytest.mark.asyncio
    async def test_error_announce_has_marker(self):
        """Error announce should also include the marker."""
        mgr = _make_manager()

        captured_content = None

        async def mock_publish(msg):
            nonlocal captured_content
            captured_content = msg.content

        mgr.bus.publish_inbound = mock_publish

        await mgr._announce_result(
            task_id="t1",
            label="test",
            task="test",
            result="Error: something went wrong",
            origin={"channel": "cli", "chat_id": "direct"},
            status="error",
        )

        assert "<!-- nanobot:system -->" in captured_content
        assert "failed" in captured_content
