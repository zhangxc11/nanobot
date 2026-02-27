"""Tests for the audit logging system (Phase 7).

Tests cover:
1. AuditLogger — JSONL file writing, enable/disable
2. ToolRegistry audit integration — field extraction for each tool type
3. Audit context propagation
"""

import asyncio
import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from nanobot.audit.logger import AuditEntry, AuditLogger
from nanobot.agent.tools.registry import ToolRegistry, _extract_audit_fields, _truncate


# ── AuditLogger tests ──────────────────────────────────────────────


class TestAuditLogger:
    """Test AuditLogger JSONL writing."""

    def test_log_creates_file(self, tmp_path: Path):
        logger = AuditLogger(log_dir=tmp_path, enabled=True)
        entry = AuditEntry(
            timestamp="2026-02-27T12:00:00",
            session_key="test:session",
            channel="cli",
            chat_id="direct",
            tool="read_file",
            action="read",
            params={"path": "/tmp/test.txt"},
            result={"success": True, "size": 100},
        )
        logger.log(entry)

        log_file = tmp_path / "2026-02-27.jsonl"
        assert log_file.exists()

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1

        record = json.loads(lines[0])
        assert record["tool"] == "read_file"
        assert record["action"] == "read"
        assert record["session_key"] == "test:session"
        assert record["result"]["success"] is True

    def test_log_appends(self, tmp_path: Path):
        logger = AuditLogger(log_dir=tmp_path, enabled=True)
        for i in range(3):
            entry = AuditEntry(
                timestamp="2026-02-27T12:00:00",
                tool=f"tool_{i}",
                action="test",
            )
            logger.log(entry)

        log_file = tmp_path / "2026-02-27.jsonl"
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_log_disabled(self, tmp_path: Path):
        logger = AuditLogger(log_dir=tmp_path, enabled=False)
        entry = AuditEntry(timestamp="2026-02-27T12:00:00", tool="test")
        logger.log(entry)

        # No file should be created
        assert not list(tmp_path.iterdir())

    def test_log_different_dates(self, tmp_path: Path):
        logger = AuditLogger(log_dir=tmp_path, enabled=True)
        for date in ["2026-02-27", "2026-02-28"]:
            entry = AuditEntry(timestamp=f"{date}T12:00:00", tool="test")
            logger.log(entry)

        assert (tmp_path / "2026-02-27.jsonl").exists()
        assert (tmp_path / "2026-02-28.jsonl").exists()

    def test_log_creates_directory(self, tmp_path: Path):
        log_dir = tmp_path / "nested" / "audit"
        logger = AuditLogger(log_dir=log_dir, enabled=True)
        entry = AuditEntry(timestamp="2026-02-27T12:00:00", tool="test")
        logger.log(entry)

        assert (log_dir / "2026-02-27.jsonl").exists()


# ── Field extraction tests ──────────────────────────────────────────


class TestFieldExtraction:
    """Test _extract_audit_fields for each tool type."""

    def test_read_file_success(self):
        fields = _extract_audit_fields(
            "read_file",
            {"path": "/tmp/test.txt"},
            "file content here",
        )
        assert fields["action"] == "read"
        assert fields["params"]["path"] == "/tmp/test.txt"
        assert fields["result"]["success"] is True
        assert fields["result"]["size"] == len("file content here")
        assert fields["error"] is None

    def test_read_file_error(self):
        fields = _extract_audit_fields(
            "read_file",
            {"path": "/nonexistent"},
            "Error: File not found: /nonexistent",
        )
        assert fields["result"]["success"] is False
        assert fields["error"] is not None

    def test_write_file_success(self):
        fields = _extract_audit_fields(
            "write_file",
            {"path": "/tmp/out.txt", "content": "hello world"},
            "Successfully wrote 11 bytes to /tmp/out.txt",
        )
        assert fields["action"] == "write"
        assert fields["result"]["success"] is True
        assert fields["result"]["bytes_written"] == 11

    def test_edit_file_success(self):
        fields = _extract_audit_fields(
            "edit_file",
            {"path": "/tmp/test.txt", "old_text": "old content", "new_text": "new content"},
            "Successfully edited /tmp/test.txt",
        )
        assert fields["action"] == "edit"
        assert fields["result"]["success"] is True
        assert "old_text_preview" in fields["params"]
        assert "new_text_preview" in fields["params"]
        # Original old_text/new_text should NOT be in params (only previews)
        assert "old_text" not in fields["params"]
        assert "new_text" not in fields["params"]

    def test_list_dir_success(self):
        fields = _extract_audit_fields(
            "list_dir",
            {"path": "/tmp"},
            "📁 dir1\n📄 file1.txt\n📄 file2.txt",
        )
        assert fields["action"] == "list"
        assert fields["result"]["success"] is True
        assert fields["result"]["entry_count"] == 3

    def test_exec_success(self):
        fields = _extract_audit_fields(
            "exec",
            {"command": "ls -la", "working_dir": "/tmp"},
            "total 0\ndrwxr-xr-x  2 user  staff  64 Feb 27 12:00 .",
        )
        assert fields["action"] == "exec"
        assert fields["result"]["success"] is True
        assert fields["result"]["blocked"] is False
        assert fields["params"]["command"] == "ls -la"

    def test_exec_blocked(self):
        fields = _extract_audit_fields(
            "exec",
            {"command": "rm -rf /"},
            "Error: Command blocked by safety guard (dangerous pattern detected)",
        )
        assert fields["result"]["success"] is False
        assert fields["result"]["blocked"] is True

    def test_exec_with_exit_code(self):
        fields = _extract_audit_fields(
            "exec",
            {"command": "false"},
            "STDERR:\nsome error\n\nExit code: 1",
        )
        assert fields["result"]["exit_code"] == 1

    def test_web_search(self):
        fields = _extract_audit_fields(
            "web_search",
            {"query": "nanobot AI assistant"},
            "Results for: nanobot AI assistant\n1. ...",
        )
        assert fields["action"] == "search"
        assert fields["params"]["query"] == "nanobot AI assistant"

    def test_web_fetch(self):
        result = json.dumps({"url": "https://example.com", "status": 200, "text": "..."})
        fields = _extract_audit_fields(
            "web_fetch",
            {"url": "https://example.com"},
            result,
        )
        assert fields["action"] == "fetch"
        assert fields["result"]["status_code"] == 200

    def test_spawn(self):
        fields = _extract_audit_fields(
            "spawn",
            {"task": "Do something complex in the background"},
            "Spawned subagent: task-123",
        )
        assert fields["action"] == "spawn"
        assert "task_preview" in fields["params"]

    def test_cron(self):
        fields = _extract_audit_fields(
            "cron",
            {"action": "add", "message": "Remind me to check email"},
            "Created job 'Remind me' (id: abc123)",
        )
        assert fields["action"] == "cron"
        assert fields["params"]["cron_action"] == "add"

    def test_message(self):
        fields = _extract_audit_fields(
            "message",
            {"content": "Hello!", "channel": "telegram", "chat_id": "12345"},
            "Message sent to telegram:12345",
        )
        assert fields["action"] == "message"
        assert fields["params"]["channel"] == "telegram"

    def test_mcp_tool(self):
        fields = _extract_audit_fields(
            "mcp_server_tool",
            {"query": "some query", "limit": 10},
            "result data",
        )
        assert fields["action"] == "mcp"

    def test_unknown_tool(self):
        fields = _extract_audit_fields(
            "custom_tool",
            {"param1": "value1"},
            "some result",
        )
        assert fields["action"] == "custom_tool"


# ── Truncation helper ──────────────────────────────────────────────


class TestTruncate:
    def test_short_string(self):
        assert _truncate("hello", 80) == "hello"

    def test_long_string(self):
        result = _truncate("a" * 100, 80)
        assert len(result) == 81  # 80 chars + "…"
        assert result.endswith("…")

    def test_none(self):
        assert _truncate(None, 80) == ""


# ── ToolRegistry audit integration ─────────────────────────────────


class TestRegistryAudit:
    """Test ToolRegistry audit logging integration."""

    def test_set_audit_context(self):
        registry = ToolRegistry()
        registry.set_audit_context(
            session_key="test:123",
            channel="web",
            chat_id="user1",
        )
        assert registry._audit_context["session_key"] == "test:123"
        assert registry._audit_context["channel"] == "web"
        assert registry._audit_context["chat_id"] == "user1"

    def test_execute_with_audit(self, tmp_path: Path):
        """Test that tool execution produces audit log entries."""
        from nanobot.agent.tools.filesystem import ReadFileTool

        registry = ToolRegistry()
        audit_logger = AuditLogger(log_dir=tmp_path, enabled=True)
        registry.set_audit_logger(audit_logger)
        registry.set_audit_context(
            session_key="test:audit",
            channel="cli",
            chat_id="direct",
        )

        # Register a read_file tool
        tool = ReadFileTool(workspace=tmp_path)
        registry.register(tool)

        # Create a test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        # Execute
        result = asyncio.run(registry.execute("read_file", {"path": str(test_file)}))
        assert result == "hello world"

        # Check audit log
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = tmp_path / f"{today}.jsonl"
        assert log_file.exists()

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1

        record = json.loads(lines[0])
        assert record["tool"] == "read_file"
        assert record["action"] == "read"
        assert record["session_key"] == "test:audit"
        assert record["result"]["success"] is True
        assert record["duration_ms"] > 0

    def test_execute_without_audit(self, tmp_path: Path):
        """Test that execution works fine without audit logger."""
        from nanobot.agent.tools.filesystem import ReadFileTool

        registry = ToolRegistry()
        tool = ReadFileTool(workspace=tmp_path)
        registry.register(tool)

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        result = asyncio.run(registry.execute("read_file", {"path": str(test_file)}))
        assert result == "hello"

        # No audit files
        jsonl_files = list(tmp_path.glob("*.jsonl"))
        assert len(jsonl_files) == 0

    def test_execute_error_audited(self, tmp_path: Path):
        """Test that errors are also audited."""
        from nanobot.agent.tools.filesystem import ReadFileTool

        registry = ToolRegistry()
        audit_logger = AuditLogger(log_dir=tmp_path, enabled=True)
        registry.set_audit_logger(audit_logger)

        tool = ReadFileTool(workspace=tmp_path)
        registry.register(tool)

        # Try to read nonexistent file
        result = asyncio.run(registry.execute("read_file", {"path": "/nonexistent/file.txt"}))
        assert "Error" in result

        today = datetime.now().strftime("%Y-%m-%d")
        log_file = tmp_path / f"{today}.jsonl"
        record = json.loads(log_file.read_text().strip().split("\n")[0])
        assert record["result"]["success"] is False
        assert record["error"] is not None

    def test_tool_not_found_audited(self, tmp_path: Path):
        """Test that 'tool not found' is audited."""
        registry = ToolRegistry()
        audit_logger = AuditLogger(log_dir=tmp_path, enabled=True)
        registry.set_audit_logger(audit_logger)

        result = asyncio.run(registry.execute("nonexistent_tool", {}))
        assert "Error" in result

        today = datetime.now().strftime("%Y-%m-%d")
        log_file = tmp_path / f"{today}.jsonl"
        record = json.loads(log_file.read_text().strip().split("\n")[0])
        assert record["tool"] == "nonexistent_tool"
        assert record["error"] is not None
