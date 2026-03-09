"""File system tools: read, write, edit."""

import difflib
import os
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool

# ── read_file defaults ──
DEFAULT_MAX_LINES = 100
DEFAULT_MAX_SIZE = 20000  # 20KB
HARD_LIMIT_DEFAULT = 1048576  # 1MB


def _human_size(n: int) -> str:
    """Format byte count as human-readable string."""
    if n < 1024:
        return f"{n} bytes"
    if n < 1048576:
        return f"{n / 1024:.1f}KB"
    return f"{n / 1048576:.1f}MB"


def _resolve_path(
    path: str, workspace: Path | None = None, allowed_dir: Path | None = None
) -> Path:
    """Resolve path against workspace (if relative) and enforce directory restriction."""
    p = Path(path).expanduser()
    if not p.is_absolute() and workspace:
        p = workspace / p
    resolved = p.resolve()
    if allowed_dir:
        try:
            resolved.relative_to(allowed_dir.resolve())
        except ValueError:
            raise PermissionError(f"Path {path} is outside allowed directory {allowed_dir}")
    return resolved


class ReadFileTool(Tool):
    """Tool to read file contents with large-file protection."""

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        hard_limit: int = HARD_LIMIT_DEFAULT,
    ):
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        self._hard_limit = hard_limit

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a file at the given path. "
            f"Default limits: ≤{DEFAULT_MAX_LINES} lines AND ≤{_human_size(DEFAULT_MAX_SIZE)}. "
            "If the file exceeds either limit, an error is returned with suggestions. "
            "Use max_lines/max_size parameters to increase limits when needed."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to read"},
                "max_lines": {
                    "type": "integer",
                    "description": (
                        f"Maximum number of lines allowed (default {DEFAULT_MAX_LINES}). "
                        "Increase to read larger files."
                    ),
                },
                "max_size": {
                    "type": "integer",
                    "description": (
                        f"Maximum file size in bytes allowed (default {DEFAULT_MAX_SIZE}). "
                        "Increase to read larger files."
                    ),
                },
            },
            "required": ["path"],
        }

    async def execute(
        self,
        path: str,
        max_lines: int | None = None,
        max_size: int | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            file_path = _resolve_path(path, self._workspace, self._allowed_dir)
            if not file_path.exists():
                return f"Error: File not found: {path}"
            if not file_path.is_file():
                return f"Error: Not a file: {path}"

            # ── Step 1: check file size via stat (no read) ──
            file_size = file_path.stat().st_size
            hard_limit = self._hard_limit

            if file_size > hard_limit:
                return (
                    f"Error: File size ({_human_size(file_size)}) exceeds hard limit "
                    f"({_human_size(hard_limit)}). "
                    "Use exec with head/tail/grep to read specific parts."
                )

            # ── Step 2: resolve effective limits (clamp to hard limit) ──
            eff_max_lines = max_lines if max_lines is not None else DEFAULT_MAX_LINES
            eff_max_size = max_size if max_size is not None else DEFAULT_MAX_SIZE
            # Clamp to hard limit
            eff_max_size = min(eff_max_size, hard_limit)

            # ── Step 3: read content ──
            content = file_path.read_text(encoding="utf-8")
            actual_size = len(content.encode("utf-8"))
            actual_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            if not content:
                actual_lines = 0

            # ── Step 4: check dual limits ──
            over_lines = actual_lines > eff_max_lines
            over_size = actual_size > eff_max_size

            if over_lines or over_size:
                return (
                    f"Error: File exceeds default read limit "
                    f"(actual: {actual_lines} lines / {_human_size(actual_size)}; "
                    f"limit: {eff_max_lines} lines / {_human_size(eff_max_size)}).\n\n"
                    f"Suggestions:\n"
                    f"  1. Use exec with head/tail/grep to read specific parts of the file\n"
                    f"  2. Increase limit with parameters: "
                    f"read_file(path, max_lines={actual_lines}, max_size={actual_size})\n\n"
                    f"Note: Hard limit is {_human_size(hard_limit)} "
                    f"(configurable in config.json under tools.read_file_hard_limit)."
                )

            return content
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading file: {str(e)}"


class WriteFileTool(Tool):
    """Tool to write content to a file."""

    def __init__(self, workspace: Path | None = None, allowed_dir: Path | None = None):
        self._workspace = workspace
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a file at the given path. Creates parent directories if needed."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to write to"},
                "content": {"type": "string", "description": "The content to write"},
            },
            "required": ["path", "content"],
        }

    async def execute(self, path: str, content: str, **kwargs: Any) -> str:
        try:
            file_path = _resolve_path(path, self._workspace, self._allowed_dir)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content)} bytes to {file_path}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error writing file: {str(e)}"


class EditFileTool(Tool):
    """Tool to edit a file by replacing text."""

    def __init__(self, workspace: Path | None = None, allowed_dir: Path | None = None):
        self._workspace = workspace
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return "Edit a file by replacing old_text with new_text. The old_text must exist exactly in the file."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to edit"},
                "old_text": {"type": "string", "description": "The exact text to find and replace"},
                "new_text": {"type": "string", "description": "The text to replace with"},
            },
            "required": ["path", "old_text", "new_text"],
        }

    async def execute(self, path: str, old_text: str, new_text: str, **kwargs: Any) -> str:
        try:
            file_path = _resolve_path(path, self._workspace, self._allowed_dir)
            if not file_path.exists():
                return f"Error: File not found: {path}"

            content = file_path.read_text(encoding="utf-8")

            if old_text not in content:
                return self._not_found_message(old_text, content, path)

            # Count occurrences
            count = content.count(old_text)
            if count > 1:
                return f"Warning: old_text appears {count} times. Please provide more context to make it unique."

            new_content = content.replace(old_text, new_text, 1)
            file_path.write_text(new_content, encoding="utf-8")

            return f"Successfully edited {file_path}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error editing file: {str(e)}"

    @staticmethod
    def _not_found_message(old_text: str, content: str, path: str) -> str:
        """Build a helpful error when old_text is not found."""
        lines = content.splitlines(keepends=True)
        old_lines = old_text.splitlines(keepends=True)
        window = len(old_lines)

        best_ratio, best_start = 0.0, 0
        for i in range(max(1, len(lines) - window + 1)):
            ratio = difflib.SequenceMatcher(None, old_lines, lines[i : i + window]).ratio()
            if ratio > best_ratio:
                best_ratio, best_start = ratio, i

        if best_ratio > 0.5:
            diff = "\n".join(
                difflib.unified_diff(
                    old_lines,
                    lines[best_start : best_start + window],
                    fromfile="old_text (provided)",
                    tofile=f"{path} (actual, line {best_start + 1})",
                    lineterm="",
                )
            )
            return f"Error: old_text not found in {path}.\nBest match ({best_ratio:.0%} similar) at line {best_start + 1}:\n{diff}"
        return (
            f"Error: old_text not found in {path}. No similar text found. Verify the file content."
        )


class ListDirTool(Tool):
    """Tool to list directory contents."""

    def __init__(self, workspace: Path | None = None, allowed_dir: Path | None = None):
        self._workspace = workspace
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return "List the contents of a directory."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "The directory path to list"}},
            "required": ["path"],
        }

    async def execute(self, path: str, **kwargs: Any) -> str:
        try:
            dir_path = _resolve_path(path, self._workspace, self._allowed_dir)
            if not dir_path.exists():
                return f"Error: Directory not found: {path}"
            if not dir_path.is_dir():
                return f"Error: Not a directory: {path}"

            items = []
            for item in sorted(dir_path.iterdir()):
                prefix = "📁 " if item.is_dir() else "📄 "
                items.append(f"{prefix}{item.name}")

            if not items:
                return f"Directory {path} is empty"

            return "\n".join(items)
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error listing directory: {str(e)}"
