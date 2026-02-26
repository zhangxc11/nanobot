"""Tests for LLMDetailLogger."""

import json
import os
import tempfile
from pathlib import Path

from nanobot.usage.detail_logger import LLMDetailLogger


def test_log_call_creates_file_and_writes_record():
    """A single log_call should create a JSONL file with one record."""
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = LLMDetailLogger(log_dir=tmpdir)
        result = logger.log_call(
            session_key="test:session1",
            model="test-model",
            iteration=1,
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello"},
            ],
            response_content="Hi there!",
            response_finish_reason="stop",
            response_usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        )

        assert result is not None
        filename, line_num = result
        assert filename.endswith(".jsonl")
        assert line_num == 1

        # Read and verify
        file_path = Path(tmpdir) / filename
        assert file_path.exists()
        with open(file_path) as f:
            record = json.loads(f.readline())

        assert record["session_key"] == "test:session1"
        assert record["model"] == "test-model"
        assert record["iteration"] == 1
        assert record["prompt_tokens"] == 100
        assert record["completion_tokens"] == 20
        assert record["total_tokens"] == 120
        assert record["messages_count"] == 2
        assert record["system_prompt_chars"] == len("You are a helpful assistant.")
        assert len(record["messages"]) == 2
        assert record["response"]["content"] == "Hi there!"
        assert record["response"]["finish_reason"] == "stop"


def test_multiple_calls_append_to_same_file():
    """Multiple calls on the same day should append to the same file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = LLMDetailLogger(log_dir=tmpdir)

        r1 = logger.log_call(
            session_key="test:s1",
            model="m1",
            iteration=1,
            messages=[{"role": "user", "content": "msg1"}],
            response_content="reply1",
        )
        r2 = logger.log_call(
            session_key="test:s2",
            model="m2",
            iteration=2,
            messages=[{"role": "user", "content": "msg2"}],
            response_content="reply2",
        )

        assert r1 is not None
        assert r2 is not None
        assert r1[0] == r2[0]  # Same filename (same day)
        assert r1[1] == 1
        assert r2[1] == 2

        # Verify both records
        file_path = Path(tmpdir) / r1[0]
        with open(file_path) as f:
            lines = f.readlines()
        assert len(lines) == 2
        rec1 = json.loads(lines[0])
        rec2 = json.loads(lines[1])
        assert rec1["session_key"] == "test:s1"
        assert rec2["session_key"] == "test:s2"


def test_tool_calls_in_response():
    """Tool calls should be recorded in the response."""
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = LLMDetailLogger(log_dir=tmpdir)
        result = logger.log_call(
            session_key="test:tools",
            model="m1",
            iteration=1,
            messages=[{"role": "user", "content": "search for X"}],
            response_content=None,
            response_tool_calls=[
                {"id": "tc1", "name": "web_search", "arguments": {"query": "X"}},
            ],
            response_finish_reason="tool_use",
            response_usage={"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
        )

        assert result is not None
        file_path = Path(tmpdir) / result[0]
        with open(file_path) as f:
            record = json.loads(f.readline())

        assert record["response"]["content"] is None
        assert len(record["response"]["tool_calls"]) == 1
        assert record["response"]["tool_calls"][0]["name"] == "web_search"
        assert record["response"]["finish_reason"] == "tool_use"


def test_disabled_logger_is_noop():
    """When disabled, log_call should return None and not create files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = LLMDetailLogger(log_dir=tmpdir, enabled=False)
        result = logger.log_call(
            session_key="test:disabled",
            model="m1",
            iteration=1,
            messages=[{"role": "user", "content": "hello"}],
            response_content="hi",
        )

        assert result is None
        # No files should be created
        assert len(os.listdir(tmpdir)) == 0


def test_system_prompt_chars_calculation():
    """system_prompt_chars should reflect the system message length."""
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = LLMDetailLogger(log_dir=tmpdir)
        long_system = "A" * 12345
        result = logger.log_call(
            session_key="test:sys",
            model="m1",
            iteration=1,
            messages=[
                {"role": "system", "content": long_system},
                {"role": "user", "content": "hi"},
            ],
            response_content="hello",
        )

        assert result is not None
        file_path = Path(tmpdir) / result[0]
        with open(file_path) as f:
            record = json.loads(f.readline())

        assert record["system_prompt_chars"] == 12345
        assert record["messages_count"] == 2


def test_no_system_message():
    """When there's no system message, system_prompt_chars should be 0."""
    with tempfile.TemporaryDirectory() as tmpdir:
        logger = LLMDetailLogger(log_dir=tmpdir)
        result = logger.log_call(
            session_key="test:nosys",
            model="m1",
            iteration=1,
            messages=[{"role": "user", "content": "hi"}],
            response_content="hello",
        )

        assert result is not None
        file_path = Path(tmpdir) / result[0]
        with open(file_path) as f:
            record = json.loads(f.readline())

        assert record["system_prompt_chars"] == 0
