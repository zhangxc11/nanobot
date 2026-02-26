"""Tests for realtime token usage recording (Phase 4).

Verifies that each LLM call produces an individual SQLite record,
rather than a single aggregate record at the end of the agent loop.
"""

import pytest
from nanobot.usage.recorder import UsageRecorder


@pytest.fixture
def recorder():
    """Create an in-memory UsageRecorder for testing."""
    return UsageRecorder(db_path=":memory:")


def test_single_call_produces_one_record(recorder: UsageRecorder):
    """A single LLM call should produce exactly one record with llm_calls=1."""
    recorder.record(
        session_key="test:session1",
        model="claude-sonnet-4-20250514",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        llm_calls=1,
        started_at="2026-02-26T21:00:00",
        finished_at="2026-02-26T21:00:00",
    )
    usage = recorder.get_session_usage("test:session1")
    assert usage["total_tokens"] == 150
    assert usage["llm_calls"] == 1


def test_multiple_calls_produce_multiple_records(recorder: UsageRecorder):
    """Multiple LLM calls should produce multiple records, each with llm_calls=1.
    SUM aggregation should give the correct total."""
    # Simulate 3 LLM calls in one agent loop turn
    recorder.record(
        session_key="test:session1",
        model="claude-sonnet-4-20250514",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        llm_calls=1,
        started_at="2026-02-26T21:00:00",
        finished_at="2026-02-26T21:00:00",
    )
    recorder.record(
        session_key="test:session1",
        model="claude-sonnet-4-20250514",
        prompt_tokens=200,
        completion_tokens=80,
        total_tokens=280,
        llm_calls=1,
        started_at="2026-02-26T21:00:05",
        finished_at="2026-02-26T21:00:05",
    )
    recorder.record(
        session_key="test:session1",
        model="claude-sonnet-4-20250514",
        prompt_tokens=300,
        completion_tokens=100,
        total_tokens=400,
        llm_calls=1,
        started_at="2026-02-26T21:00:10",
        finished_at="2026-02-26T21:00:10",
    )

    usage = recorder.get_session_usage("test:session1")
    assert usage["prompt_tokens"] == 600  # 100 + 200 + 300
    assert usage["completion_tokens"] == 230  # 50 + 80 + 100
    assert usage["total_tokens"] == 830  # 150 + 280 + 400
    assert usage["llm_calls"] == 3


def test_global_usage_aggregation(recorder: UsageRecorder):
    """Global usage should aggregate across all sessions correctly."""
    # Session 1: 2 LLM calls
    recorder.record(
        session_key="test:s1", model="claude-sonnet-4-20250514",
        prompt_tokens=100, completion_tokens=50, total_tokens=150,
        llm_calls=1, started_at="2026-02-26T21:00:00", finished_at="2026-02-26T21:00:00",
    )
    recorder.record(
        session_key="test:s1", model="claude-sonnet-4-20250514",
        prompt_tokens=200, completion_tokens=80, total_tokens=280,
        llm_calls=1, started_at="2026-02-26T21:00:05", finished_at="2026-02-26T21:00:05",
    )
    # Session 2: 1 LLM call
    recorder.record(
        session_key="test:s2", model="claude-sonnet-4-20250514",
        prompt_tokens=500, completion_tokens=200, total_tokens=700,
        llm_calls=1, started_at="2026-02-26T21:01:00", finished_at="2026-02-26T21:01:00",
    )

    global_usage = recorder.get_global_usage()
    assert global_usage["total_tokens"] == 1130  # 150 + 280 + 700
    assert global_usage["total_llm_calls"] == 3


def test_individual_records_have_distinct_timestamps(recorder: UsageRecorder):
    """Each record should have its own timestamp (not shared across the turn)."""
    ts1 = "2026-02-26T21:00:00.123456"
    ts2 = "2026-02-26T21:00:05.789012"

    recorder.record(
        session_key="test:s1", model="claude-sonnet-4-20250514",
        prompt_tokens=100, completion_tokens=50, total_tokens=150,
        llm_calls=1, started_at=ts1, finished_at=ts1,
    )
    recorder.record(
        session_key="test:s1", model="claude-sonnet-4-20250514",
        prompt_tokens=200, completion_tokens=80, total_tokens=280,
        llm_calls=1, started_at=ts2, finished_at=ts2,
    )

    # Query raw records to verify timestamps
    with recorder._connect() as conn:
        rows = conn.execute(
            "SELECT started_at, finished_at, total_tokens FROM token_usage "
            "WHERE session_key = ? ORDER BY id",
            ("test:s1",),
        ).fetchall()

    assert len(rows) == 2
    assert rows[0]["started_at"] == ts1
    assert rows[0]["total_tokens"] == 150
    assert rows[1]["started_at"] == ts2
    assert rows[1]["total_tokens"] == 280


def test_empty_session_usage(recorder: UsageRecorder):
    """Querying usage for a non-existent session should return zeros."""
    usage = recorder.get_session_usage("test:nonexistent")
    assert usage["total_tokens"] == 0
    assert usage["llm_calls"] == 0
