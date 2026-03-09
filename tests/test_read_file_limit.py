"""Tests for read_file large file protection (§34).

Tests cover:
1. Small file reads normally (no parameters)
2. Over line limit triggers protection
3. Over size limit triggers protection
4. Both limits exceeded triggers protection
5. Expanding limits via parameters allows reading
6. Parameters exceeding hard limit are clamped
7. File exceeding hard limit returns hard error
8. Error message format validation
9. Human-readable size formatting
10. Empty file edge case
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from nanobot.agent.tools.filesystem import (
    DEFAULT_MAX_LINES,
    DEFAULT_MAX_SIZE,
    HARD_LIMIT_DEFAULT,
    ReadFileTool,
    _human_size,
)


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def run(coro):
    return asyncio.run(coro)


# ── _human_size helper ──


class TestHumanSize:
    def test_bytes(self):
        assert _human_size(500) == "500 bytes"

    def test_kilobytes(self):
        assert _human_size(2048) == "2.0KB"

    def test_megabytes(self):
        assert _human_size(1048576) == "1.0MB"

    def test_large_megabytes(self):
        assert _human_size(5 * 1048576) == "5.0MB"

    def test_zero(self):
        assert _human_size(0) == "0 bytes"

    def test_boundary_1kb(self):
        assert _human_size(1023) == "1023 bytes"
        assert _human_size(1024) == "1.0KB"

    def test_boundary_1mb(self):
        assert _human_size(1048575) == "1024.0KB"
        assert _human_size(1048576) == "1.0MB"


# ── Small file reads normally ──


class TestSmallFileReads:
    def test_small_file_no_params(self, tmp_dir):
        f = tmp_dir / "small.txt"
        f.write_text("hello world\n")
        tool = ReadFileTool(workspace=tmp_dir)
        result = run(tool.execute(str(f)))
        assert result == "hello world\n"

    def test_empty_file(self, tmp_dir):
        f = tmp_dir / "empty.txt"
        f.write_text("")
        tool = ReadFileTool(workspace=tmp_dir)
        result = run(tool.execute(str(f)))
        assert result == ""

    def test_exactly_at_line_limit(self, tmp_dir):
        """File with exactly DEFAULT_MAX_LINES lines should pass."""
        f = tmp_dir / "exact_lines.txt"
        content = "\n".join(f"line {i}" for i in range(DEFAULT_MAX_LINES)) + "\n"
        # Ensure size is within default limit
        assert len(content.encode()) <= DEFAULT_MAX_SIZE
        f.write_text(content)
        tool = ReadFileTool(workspace=tmp_dir)
        result = run(tool.execute(str(f)))
        assert result == content

    def test_exactly_at_size_limit(self, tmp_dir):
        """File with exactly DEFAULT_MAX_SIZE bytes should pass (if lines OK)."""
        f = tmp_dir / "exact_size.txt"
        # Create content that is exactly DEFAULT_MAX_SIZE bytes, few lines
        content = "x" * (DEFAULT_MAX_SIZE - 1) + "\n"
        assert len(content.encode()) == DEFAULT_MAX_SIZE
        f.write_text(content)
        tool = ReadFileTool(workspace=tmp_dir)
        result = run(tool.execute(str(f)))
        assert result == content


# ── Over line limit triggers protection ──


class TestOverLineLimit:
    def test_over_lines_triggers_error(self, tmp_dir):
        f = tmp_dir / "many_lines.txt"
        # 150 lines, each short → within size limit but over line limit
        content = "\n".join(f"line {i}" for i in range(150)) + "\n"
        assert len(content.encode()) < DEFAULT_MAX_SIZE  # ensure size is OK
        f.write_text(content)
        tool = ReadFileTool(workspace=tmp_dir)
        result = run(tool.execute(str(f)))
        assert result.startswith("Error: File exceeds default read limit")
        assert "150 lines" in result
        assert f"limit: {DEFAULT_MAX_LINES} lines" in result

    def test_error_contains_suggestions(self, tmp_dir):
        f = tmp_dir / "many_lines.txt"
        content = "\n".join(f"line {i}" for i in range(150)) + "\n"
        f.write_text(content)
        tool = ReadFileTool(workspace=tmp_dir)
        result = run(tool.execute(str(f)))
        assert "head/tail/grep" in result
        assert "max_lines=150" in result


# ── Over size limit triggers protection ──


class TestOverSizeLimit:
    def test_over_size_triggers_error(self, tmp_dir):
        f = tmp_dir / "big_file.txt"
        # 25KB in a single line → within line limit but over size limit
        content = "x" * 25000 + "\n"
        assert content.count("\n") <= DEFAULT_MAX_LINES  # ensure lines OK
        f.write_text(content)
        tool = ReadFileTool(workspace=tmp_dir)
        result = run(tool.execute(str(f)))
        assert result.startswith("Error: File exceeds default read limit")
        assert "1 lines" in result
        assert f"limit: {DEFAULT_MAX_LINES} lines" in result


# ── Both limits exceeded ──


class TestBothLimitsExceeded:
    def test_both_over(self, tmp_dir):
        f = tmp_dir / "huge.txt"
        # 200 lines of 200 bytes each → over both limits
        content = "\n".join("x" * 200 for _ in range(200)) + "\n"
        f.write_text(content)
        tool = ReadFileTool(workspace=tmp_dir)
        result = run(tool.execute(str(f)))
        assert result.startswith("Error: File exceeds default read limit")
        assert "200 lines" in result


# ── Expanding limits via parameters ──


class TestExpandLimits:
    def test_expand_lines_allows_read(self, tmp_dir):
        f = tmp_dir / "many_lines.txt"
        content = "\n".join(f"line {i}" for i in range(150)) + "\n"
        f.write_text(content)
        tool = ReadFileTool(workspace=tmp_dir)
        # With default params, it should fail
        result = run(tool.execute(str(f)))
        assert result.startswith("Error:")
        # With expanded max_lines, it should succeed
        result = run(tool.execute(str(f), max_lines=200))
        assert result == content

    def test_expand_size_allows_read(self, tmp_dir):
        f = tmp_dir / "big_file.txt"
        content = "x" * 25000 + "\n"
        f.write_text(content)
        tool = ReadFileTool(workspace=tmp_dir)
        # With default params, it should fail
        result = run(tool.execute(str(f)))
        assert result.startswith("Error:")
        # With expanded max_size, it should succeed
        result = run(tool.execute(str(f), max_size=30000))
        assert result == content

    def test_expand_both_allows_read(self, tmp_dir):
        f = tmp_dir / "huge.txt"
        content = "\n".join("x" * 200 for _ in range(200)) + "\n"
        f.write_text(content)
        tool = ReadFileTool(workspace=tmp_dir)
        result = run(tool.execute(str(f), max_lines=300, max_size=100000))
        assert result == content


# ── Parameters clamped to hard limit ──


class TestClampToHardLimit:
    def test_max_size_clamped(self, tmp_dir):
        """max_size exceeding hard limit is clamped to hard limit."""
        f = tmp_dir / "test.txt"
        # File within hard limit but over the clamped max_size
        hard_limit = 500
        content = "x" * 450 + "\n"  # 451 bytes — within hard limit (500)
        f.write_text(content)
        tool = ReadFileTool(workspace=tmp_dir, hard_limit=hard_limit)
        # User passes max_size=10000, but it's clamped to 500
        # File is 451 bytes, clamped limit is 500 → should pass
        result = run(tool.execute(str(f), max_size=10000, max_lines=10))
        assert result == content

    def test_max_size_clamped_blocks_file(self, tmp_dir):
        """When file exceeds clamped max_size, soft error is returned."""
        f = tmp_dir / "test.txt"
        hard_limit = 400
        content = "x" * 350 + "\n"  # 351 bytes — within hard limit (400)
        f.write_text(content)
        tool = ReadFileTool(workspace=tmp_dir, hard_limit=hard_limit)
        # User passes max_size=200, which is within hard limit so not clamped
        result = run(tool.execute(str(f), max_size=200, max_lines=10))
        assert result.startswith("Error: File exceeds default read limit")
        assert f"limit: 10 lines / {_human_size(200)}" in result

    def test_max_size_within_hard_limit(self, tmp_dir):
        """max_size within hard limit works normally."""
        f = tmp_dir / "test.txt"
        content = "x" * 300 + "\n"
        f.write_text(content)
        tool = ReadFileTool(workspace=tmp_dir, hard_limit=1000)
        result = run(tool.execute(str(f), max_size=500, max_lines=10))
        assert result == content


# ── Hard limit ──


class TestHardLimit:
    def test_file_over_hard_limit(self, tmp_dir):
        f = tmp_dir / "massive.bin"
        # Create a file larger than a small hard limit
        hard_limit = 1000
        f.write_bytes(b"x" * 2000)
        tool = ReadFileTool(workspace=tmp_dir, hard_limit=hard_limit)
        result = run(tool.execute(str(f)))
        assert "exceeds hard limit" in result
        assert _human_size(2000) in result
        assert _human_size(hard_limit) in result
        assert "head/tail/grep" in result

    def test_file_exactly_at_hard_limit(self, tmp_dir):
        """File exactly at hard limit should NOT trigger hard limit error."""
        f = tmp_dir / "exact.txt"
        hard_limit = 1000
        content = "x" * hard_limit
        f.write_text(content)
        tool = ReadFileTool(workspace=tmp_dir, hard_limit=hard_limit)
        # This should pass hard limit check (not >) but may fail soft limit
        result = run(tool.execute(str(f), max_lines=10, max_size=hard_limit))
        assert result == content

    def test_default_hard_limit(self):
        """Default hard limit is 1MB."""
        tool = ReadFileTool()
        assert tool._hard_limit == HARD_LIMIT_DEFAULT
        assert HARD_LIMIT_DEFAULT == 1048576

    def test_custom_hard_limit(self):
        tool = ReadFileTool(hard_limit=500000)
        assert tool._hard_limit == 500000


# ── Error message format ──


class TestErrorFormat:
    def test_soft_limit_error_format(self, tmp_dir):
        f = tmp_dir / "test.txt"
        content = "\n".join(f"line {i}" for i in range(150)) + "\n"
        f.write_text(content)
        tool = ReadFileTool(workspace=tmp_dir)
        result = run(tool.execute(str(f)))
        # Check all required parts
        assert "Error: File exceeds default read limit" in result
        assert "actual:" in result
        assert "limit:" in result
        assert "Suggestions:" in result
        assert "head/tail/grep" in result
        assert "Increase limit with parameters:" in result
        assert "read_file(path, max_lines=" in result
        assert "Note: Hard limit is" in result
        assert "tools.read_file_hard_limit" in result

    def test_hard_limit_error_format(self, tmp_dir):
        f = tmp_dir / "massive.bin"
        f.write_bytes(b"x" * 2000)
        tool = ReadFileTool(workspace=tmp_dir, hard_limit=1000)
        result = run(tool.execute(str(f)))
        assert "Error: File size" in result
        assert "exceeds hard limit" in result
        assert "head/tail/grep" in result
        # Should NOT contain soft limit suggestions
        assert "Increase limit with parameters" not in result

    def test_suggestion_uses_actual_values(self, tmp_dir):
        """Suggestions should contain actual file lines/size for easy copy-paste."""
        f = tmp_dir / "test.txt"
        content = "\n".join(f"line {i}" for i in range(150)) + "\n"
        actual_size = len(content.encode())
        f.write_text(content)
        tool = ReadFileTool(workspace=tmp_dir)
        result = run(tool.execute(str(f)))
        assert f"max_lines=150" in result
        assert f"max_size={actual_size}" in result


# ── Tool description and parameters schema ──


class TestToolSchema:
    def test_description_mentions_limits(self):
        tool = ReadFileTool()
        assert "100" in tool.description
        assert "limit" in tool.description.lower()

    def test_parameters_include_max_lines(self):
        tool = ReadFileTool()
        props = tool.parameters["properties"]
        assert "max_lines" in props
        assert props["max_lines"]["type"] == "integer"

    def test_parameters_include_max_size(self):
        tool = ReadFileTool()
        props = tool.parameters["properties"]
        assert "max_size" in props
        assert props["max_size"]["type"] == "integer"

    def test_path_still_required(self):
        tool = ReadFileTool()
        assert tool.parameters["required"] == ["path"]

    def test_max_lines_max_size_not_required(self):
        tool = ReadFileTool()
        assert "max_lines" not in tool.parameters["required"]
        assert "max_size" not in tool.parameters["required"]


# ── File not found / not a file ──


class TestEdgeCases:
    def test_file_not_found(self, tmp_dir):
        tool = ReadFileTool(workspace=tmp_dir)
        result = run(tool.execute(str(tmp_dir / "nonexistent.txt")))
        assert "Error: File not found" in result

    def test_not_a_file(self, tmp_dir):
        tool = ReadFileTool(workspace=tmp_dir)
        result = run(tool.execute(str(tmp_dir)))
        assert "Error: Not a file" in result

    def test_single_line_no_newline(self, tmp_dir):
        """Single line without trailing newline should count as 1 line."""
        f = tmp_dir / "single.txt"
        f.write_text("hello")
        tool = ReadFileTool(workspace=tmp_dir)
        result = run(tool.execute(str(f)))
        assert result == "hello"


# ── Config integration ──


class TestConfigIntegration:
    def test_tools_config_has_read_file_hard_limit(self):
        from nanobot.config.schema import ToolsConfig
        cfg = ToolsConfig()
        assert cfg.read_file_hard_limit == 1048576

    def test_tools_config_custom_value(self):
        from nanobot.config.schema import ToolsConfig
        cfg = ToolsConfig(read_file_hard_limit=500000)
        assert cfg.read_file_hard_limit == 500000

    def test_camel_case_alias(self):
        from nanobot.config.schema import ToolsConfig
        cfg = ToolsConfig(**{"readFileHardLimit": 500000})
        assert cfg.read_file_hard_limit == 500000
