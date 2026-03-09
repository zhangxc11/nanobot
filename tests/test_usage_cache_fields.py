"""Tests for cache usage fields in the recording pipeline (§32).

Verifies that cache_creation_input_tokens and cache_read_input_tokens
are correctly:
  1. Extracted from LLM response in _parse_response()
  2. Recorded in SQLite via UsageRecorder.record()
  3. Migrated for existing databases
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nanobot.usage.recorder import UsageRecorder


class TestUsageRecorderCacheFields:
    """Tests for UsageRecorder with cache fields."""

    def test_record_with_cache_fields(self, tmp_path: Path):
        """Cache fields are written and can be read back."""
        db_path = str(tmp_path / "test.db")
        recorder = UsageRecorder(db_path=db_path)

        row_id = recorder.record(
            session_key="test-session",
            model="claude-sonnet-4-20250514",
            prompt_tokens=1000,
            completion_tokens=200,
            total_tokens=1200,
            llm_calls=1,
            started_at="2026-03-09T12:00:00",
            finished_at="2026-03-09T12:00:01",
            cache_creation_input_tokens=500,
            cache_read_input_tokens=300,
        )
        assert row_id > 0

        # Read back
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT cache_creation_input_tokens, cache_read_input_tokens FROM token_usage WHERE id = ?",
            (row_id,),
        ).fetchone()
        conn.close()

        assert row == (500, 300)

    def test_record_without_cache_fields_defaults_to_zero(self, tmp_path: Path):
        """Omitting cache fields defaults to 0."""
        db_path = str(tmp_path / "test.db")
        recorder = UsageRecorder(db_path=db_path)

        row_id = recorder.record(
            session_key="test-session",
            model="gpt-4",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            llm_calls=1,
            started_at="2026-03-09T12:00:00",
            finished_at="2026-03-09T12:00:01",
        )

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT cache_creation_input_tokens, cache_read_input_tokens FROM token_usage WHERE id = ?",
            (row_id,),
        ).fetchone()
        conn.close()

        assert row == (0, 0)

    def test_migration_adds_cache_columns(self, tmp_path: Path):
        """Migration adds cache columns to an existing database without them."""
        db_path = str(tmp_path / "old.db")

        # Create old-schema database manually
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
                finished_at       TEXT NOT NULL
            );
        """)
        # Insert a row with old schema
        conn.execute(
            "INSERT INTO token_usage (session_key, model, prompt_tokens, completion_tokens, total_tokens, llm_calls, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("old-session", "gpt-4", 100, 50, 150, 1, "2026-03-01", "2026-03-01"),
        )
        conn.commit()
        conn.close()

        # Now open with UsageRecorder — migration should run
        recorder = UsageRecorder(db_path=db_path)

        # Old row should have default 0 for cache fields
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT cache_creation_input_tokens, cache_read_input_tokens FROM token_usage WHERE session_key = 'old-session'"
        ).fetchone()
        conn.close()
        assert row == (0, 0)

        # New record with cache fields should work
        row_id = recorder.record(
            session_key="new-session",
            model="claude-sonnet-4-20250514",
            prompt_tokens=1000,
            completion_tokens=200,
            total_tokens=1200,
            llm_calls=1,
            started_at="2026-03-09T12:00:00",
            finished_at="2026-03-09T12:00:01",
            cache_creation_input_tokens=500,
            cache_read_input_tokens=300,
        )
        assert row_id > 0

    def test_migration_idempotent(self, tmp_path: Path):
        """Running migration twice should not fail."""
        db_path = str(tmp_path / "test.db")
        recorder1 = UsageRecorder(db_path=db_path)
        recorder1.record(
            session_key="s1", model="m1",
            prompt_tokens=1, completion_tokens=1, total_tokens=2,
            llm_calls=1, started_at="t1", finished_at="t2",
            cache_creation_input_tokens=10, cache_read_input_tokens=20,
        )

        # Second recorder instance triggers migration again
        recorder2 = UsageRecorder(db_path=db_path)
        row_id = recorder2.record(
            session_key="s2", model="m2",
            prompt_tokens=2, completion_tokens=2, total_tokens=4,
            llm_calls=1, started_at="t3", finished_at="t4",
            cache_creation_input_tokens=30, cache_read_input_tokens=40,
        )
        assert row_id > 0


class TestParseResponseCacheFields:
    """Tests for _parse_response() cache field extraction."""

    def test_parse_response_with_cache_fields(self):
        """Cache fields are extracted from LiteLLM response."""
        from nanobot.providers.litellm_provider import LiteLLMProvider

        provider = LiteLLMProvider.__new__(LiteLLMProvider)

        # Mock a LiteLLM ModelResponse
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello"
        mock_response.choices[0].message.tool_calls = None
        mock_response.choices[0].finish_reason = "stop"
        mock_response.choices[0].message.reasoning_content = None

        # Mock usage with cache fields (Pydantic model_extra behavior)
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 1000
        mock_usage.completion_tokens = 200
        mock_usage.total_tokens = 1200
        mock_usage.cache_creation_input_tokens = 500
        mock_usage.cache_read_input_tokens = 300
        mock_response.usage = mock_usage

        result = provider._parse_response(mock_response)

        assert result.usage["cache_creation_input_tokens"] == 500
        assert result.usage["cache_read_input_tokens"] == 300
        assert result.usage["prompt_tokens"] == 1000
        assert result.usage["completion_tokens"] == 200
        assert result.usage["total_tokens"] == 1200

    def test_parse_response_without_cache_fields(self):
        """Non-Anthropic response without cache fields defaults to 0."""
        from nanobot.providers.litellm_provider import LiteLLMProvider

        provider = LiteLLMProvider.__new__(LiteLLMProvider)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello"
        mock_response.choices[0].message.tool_calls = None
        mock_response.choices[0].finish_reason = "stop"
        mock_response.choices[0].message.reasoning_content = None

        # Usage without cache fields — getattr should return 0
        mock_usage = MagicMock(spec=["prompt_tokens", "completion_tokens", "total_tokens"])
        mock_usage.prompt_tokens = 100
        mock_usage.completion_tokens = 50
        mock_usage.total_tokens = 150
        mock_response.usage = mock_usage

        result = provider._parse_response(mock_response)

        assert result.usage["cache_creation_input_tokens"] == 0
        assert result.usage["cache_read_input_tokens"] == 0
