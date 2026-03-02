"""Tool registry for dynamic tool management."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.audit.logger import AuditEntry, AuditLogger


# ── Audit field extraction helpers ──────────────────────────────────

def _truncate(text: str | None, max_len: int = 80) -> str:
    """Truncate a string for audit log display."""
    if text is None:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def _extract_audit_fields(tool_name: str, params: dict[str, Any], result: str) -> dict[str, Any]:
    """Extract structured audit fields from a tool call and its result.

    Returns a dict with keys: action, params (sanitised), result (summary),
    resolved_path, error.
    """
    is_error = isinstance(result, str) and result.startswith("Error")
    error_msg = result.split("\n")[0] if is_error else None

    # ── File-system tools ──

    if tool_name == "read_file":
        path = params.get("path", "")
        success = not is_error
        size = len(result) if success else 0
        return {
            "action": "read",
            "params": {"path": path},
            "result": {"success": success, "size": size},
            "resolved_path": _try_resolve(path),
            "error": error_msg,
        }

    if tool_name == "write_file":
        path = params.get("path", "")
        success = not is_error
        # write_file returns "Successfully wrote N bytes to ..."
        bytes_written = 0
        is_new = False
        if success:
            # Check if file existed before (heuristic: look at result text)
            bytes_written = len(params.get("content", ""))
            # We can't know is_new_file without checking before execution,
            # but we can parse the result message
            try:
                resolved = Path(path).expanduser().resolve()
                # The file already exists by now (just written), so we
                # cannot determine is_new retroactively. Leave as False.
            except Exception:
                pass
        return {
            "action": "write",
            "params": {"path": path},
            "result": {"success": success, "bytes_written": bytes_written},
            "resolved_path": _try_resolve(path),
            "error": error_msg,
        }

    if tool_name == "edit_file":
        path = params.get("path", "")
        old_preview = _truncate(params.get("old_text"), 80)
        new_preview = _truncate(params.get("new_text"), 80)
        success = not is_error
        return {
            "action": "edit",
            "params": {"path": path, "old_text_preview": old_preview, "new_text_preview": new_preview},
            "result": {"success": success},
            "resolved_path": _try_resolve(path),
            "error": error_msg,
        }

    if tool_name == "list_dir":
        path = params.get("path", "")
        success = not is_error
        entry_count = 0
        if success:
            entry_count = len([line for line in result.strip().split("\n") if line.strip()])
        return {
            "action": "list",
            "params": {"path": path},
            "result": {"success": success, "entry_count": entry_count},
            "resolved_path": _try_resolve(path),
            "error": error_msg,
        }

    # ── Shell execution ──

    if tool_name == "exec":
        command = params.get("command", "")
        working_dir = params.get("working_dir", "")
        blocked = is_error and ("blocked by safety guard" in result or "background operator" in result)
        exit_code = 0
        if not is_error and not blocked:
            # Try to parse exit code from result
            import re
            m = re.search(r"Exit code: (\d+)", result)
            if m:
                exit_code = int(m.group(1))
        return {
            "action": "exec",
            "params": {"command": command, "working_dir": working_dir},
            "result": {"success": not is_error, "exit_code": exit_code, "blocked": blocked},
            "resolved_path": None,
            "error": error_msg,
        }

    # ── Web tools ──

    if tool_name == "web_search":
        query = params.get("query", "")
        return {
            "action": "search",
            "params": {"query": query},
            "result": {"success": not is_error},
            "resolved_path": None,
            "error": error_msg,
        }

    if tool_name == "web_fetch":
        url = params.get("url", "")
        status_code = 0
        if not is_error:
            try:
                import json as _json
                parsed = _json.loads(result)
                status_code = parsed.get("status", 0)
            except Exception:
                pass
        return {
            "action": "fetch",
            "params": {"url": url},
            "result": {"success": not is_error, "status_code": status_code},
            "resolved_path": None,
            "error": error_msg,
        }

    # ── Spawn ──

    if tool_name == "spawn":
        task_preview = _truncate(params.get("task"), 120)
        return {
            "action": "spawn",
            "params": {"task_preview": task_preview},
            "result": {"success": not is_error},
            "resolved_path": None,
            "error": error_msg,
        }

    # ── Cron ──

    if tool_name == "cron":
        action = params.get("action", "")
        message_preview = _truncate(params.get("message"), 80)
        return {
            "action": "cron",
            "params": {"cron_action": action, "message_preview": message_preview},
            "result": {"success": not is_error},
            "resolved_path": None,
            "error": error_msg,
        }

    # ── Message ──

    if tool_name == "message":
        channel = params.get("channel", "")
        chat_id = params.get("chat_id", "")
        return {
            "action": "message",
            "params": {"channel": channel, "chat_id": chat_id},
            "result": {"success": not is_error},
            "resolved_path": None,
            "error": error_msg,
        }

    # ── MCP and other tools (generic fallback) ──

    if tool_name.startswith("mcp_"):
        # Truncate all param values for safety
        sanitised = {k: _truncate(str(v), 120) for k, v in params.items()}
        return {
            "action": "mcp",
            "params": sanitised,
            "result": {"success": not is_error},
            "resolved_path": None,
            "error": error_msg,
        }

    # Generic fallback for unknown tools
    sanitised = {k: _truncate(str(v), 120) for k, v in params.items()}
    return {
        "action": tool_name,
        "params": sanitised,
        "result": {"success": not is_error},
        "resolved_path": None,
        "error": error_msg,
    }


def _try_resolve(path: str) -> str | None:
    """Try to resolve a path to absolute. Returns None on failure."""
    try:
        return str(Path(path).expanduser().resolve())
    except Exception:
        return None


# ── ToolRegistry ────────────────────────────────────────────────────

class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    Optionally records audit logs for every tool invocation.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._audit_logger: AuditLogger | None = None
        self._audit_context: dict[str, str] = {
            "session_key": "",
            "channel": "",
            "chat_id": "",
        }

    # ── Audit configuration ──

    def set_audit_logger(self, logger: "AuditLogger") -> None:
        """Attach an audit logger.  All subsequent ``execute()`` calls will
        be logged."""
        self._audit_logger = logger

    def set_audit_context(self, **kwargs: str) -> None:
        """Update the audit context (session_key, channel, chat_id).

        Called by ``AgentLoop._process_message()`` at the start of each turn
        so that audit records carry the correct origin information.
        """
        self._audit_context.update(kwargs)

    # ── Tool management ──
    

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        return [tool.to_schema() for tool in self._tools.values()]

    # ── Execution ──
    

    async def execute(self, name: str, params: dict[str, Any]) -> str:
        """Execute a tool by name with given parameters.

        If an :class:`AuditLogger` is attached, every call is recorded with
        timing, parameters (sanitised), result summary, and session context.
        """
        _HINT = "\n\n[Analyze the error above and try a different approach.]"

        tool = self._tools.get(name)
        if not tool:
            result = f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"
            self._audit_record(name, params, result, 0.0)
            return result

        start = time.monotonic()
        try:
            errors = tool.validate_params(params)
            if errors:
                result = f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors) + _HINT
                elapsed = (time.monotonic() - start) * 1000
                self._audit_record(name, params, result, elapsed)
                return result
            result = await tool.execute(**params)
            elapsed = (time.monotonic() - start) * 1000
            if isinstance(result, str) and result.startswith("Error"):
                self._audit_record(name, params, result, elapsed)
                return result + _HINT
            self._audit_record(name, params, result, elapsed)
            return result
        except Exception as e:
            result = f"Error executing {name}: {str(e)}"
            elapsed = (time.monotonic() - start) * 1000
            self._audit_record(name, params, result, elapsed)
            return result + _HINT

    def _audit_record(self, tool_name: str, params: dict[str, Any], result: str, duration_ms: float) -> None:
        """Build and write an audit entry (if logger is attached)."""
        if self._audit_logger is None:
            return

        from nanobot.audit.logger import AuditEntry

        fields = _extract_audit_fields(tool_name, params, result)

        entry = AuditEntry(
            timestamp=datetime.now().isoformat(),
            session_key=self._audit_context.get("session_key", ""),
            channel=self._audit_context.get("channel", ""),
            chat_id=self._audit_context.get("chat_id", ""),
            tool=tool_name,
            action=fields["action"],
            params=fields["params"],
            result=fields["result"],
            resolved_path=fields.get("resolved_path"),
            error=fields.get("error"),
            duration_ms=round(duration_ms, 2),
        )
        self._audit_logger.log(entry)

    # ── Cloning for concurrent sessions ──

    # Tools that hold per-session state (channel/chat_id context) and must
    # be cloned for each concurrent session task.
    _STATEFUL_TOOL_NAMES = frozenset({"message", "spawn", "cron"})

    def clone_for_session(self) -> "ToolRegistry":
        """Create a shallow clone suitable for a concurrent session task.

        Stateless tools (read_file, write_file, exec, etc.) are **shared**
        by reference.  Stateful tools (MessageTool, SpawnTool, CronTool)
        are **cloned** so that ``set_context()`` and ``start_turn()`` on
        one session don't interfere with another.

        The audit logger reference is shared (it's thread-safe), but the
        audit context dict is independent.
        """
        clone = ToolRegistry()
        for name, tool in self._tools.items():
            if name in self._STATEFUL_TOOL_NAMES and hasattr(tool, "clone"):
                clone._tools[name] = tool.clone()
            else:
                # Share reference — these tools are stateless / thread-safe
                clone._tools[name] = tool
        # Share audit logger (thread-safe), but give independent context
        clone._audit_logger = self._audit_logger
        clone._audit_context = dict(self._audit_context)
        return clone

    # ── Introspection ──
    
    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
