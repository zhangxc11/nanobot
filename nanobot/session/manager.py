"""Session management for conversation history."""

import json
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger

from nanobot.utils.helpers import ensure_dir, safe_filename


@dataclass
class Session:
    """
    A conversation session.

    Stores messages in JSONL format for easy reading and persistence.

    Important: Messages are append-only for LLM cache efficiency.
    The consolidation process writes summaries to MEMORY.md/HISTORY.md
    but does NOT modify the messages list or get_history() output.
    """

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0  # Number of messages already consolidated to files
    
    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()
    
    def get_history(self, max_messages: int = 500) -> list[dict[str, Any]]:
        """Return unconsolidated messages for LLM input, aligned to a valid boundary.

        The slice is adjusted so it never starts with orphaned ``tool`` result
        messages (which would cause Anthropic's "unexpected tool_use_id" error).
        Valid start boundaries are:
        - A ``user`` message (preferred), or
        - An ``assistant`` message (start of a tool-call chain when no user
          message exists in the window).
        """
        unconsolidated = self.messages[self.last_consolidated:]
        sliced = unconsolidated[-max_messages:]

        # Find a valid starting point — prefer ``user``, fall back to ``assistant``.
        # Never start with a ``tool`` message (orphaned tool_result).
        start = 0
        found_user = False
        first_assistant = -1
        for i, m in enumerate(sliced):
            role = m.get("role")
            if role == "user":
                start = i
                found_user = True
                break
            if role == "assistant" and first_assistant < 0:
                first_assistant = i

        if not found_user:
            # No user message in the slice — fall back to first assistant message
            # to preserve as much context as possible without orphaned tool results.
            if first_assistant >= 0:
                start = first_assistant
            else:
                # Entire slice is tool messages (extremely unlikely) — drop all
                return []

        sliced = sliced[start:]

        out: list[dict[str, Any]] = []
        for m in sliced:
            entry: dict[str, Any] = {"role": m["role"], "content": m.get("content", "")}
            for k in ("tool_calls", "tool_call_id", "name"):
                if k in m:
                    entry[k] = m[k]
            out.append(entry)
        return out
    
    def clear(self) -> None:
        """Clear all messages and reset session to initial state."""
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()


class SessionManager:
    """
    Manages conversation sessions.

    Sessions are stored as JSONL files in the sessions directory.
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self.legacy_sessions_dir = Path.home() / ".nanobot" / "sessions"
        self._cache: dict[str, Session] = {}
    
    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"

    def _get_legacy_session_path(self, key: str) -> Path:
        """Legacy global session path (~/.nanobot/sessions/)."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.legacy_sessions_dir / f"{safe_key}.jsonl"
    
    def get_or_create(self, key: str) -> Session:
        """
        Get an existing session or create a new one.
        
        Args:
            key: Session key (usually channel:chat_id).
        
        Returns:
            The session.
        """
        if key in self._cache:
            return self._cache[key]
        
        session = self._load(key)
        if session is None:
            session = Session(key=key)
        
        self._cache[key] = session
        return session
    
    def _load(self, key: str) -> Session | None:
        """Load a session from disk."""
        path = self._get_session_path(key)
        if not path.exists():
            legacy_path = self._get_legacy_session_path(key)
            if legacy_path.exists():
                try:
                    shutil.move(str(legacy_path), str(path))
                    logger.info("Migrated session {} from legacy path", key)
                except Exception:
                    logger.exception("Failed to migrate session {}", key)

        if not path.exists():
            return None

        try:
            messages = []
            metadata = {}
            created_at = None
            last_consolidated = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
                        last_consolidated = data.get("last_consolidated", 0)
                    else:
                        messages.append(data)

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated
            )
        except Exception as e:
            logger.warning("Failed to load session {}: {}", key, e)
            return None
    
    def _prepare_entry(self, message: dict[str, Any]) -> dict[str, Any]:
        """Prepare a message dict for JSONL persistence.

        - Strips ``reasoning_content`` (internal LLM field, not for storage).
        - Truncates large tool results to keep JSONL files manageable.
        - Ensures a ``timestamp`` is present.
        """
        from datetime import datetime

        entry = {k: v for k, v in message.items() if k != "reasoning_content"}
        if entry.get("role") == "tool" and isinstance(entry.get("content"), str):
            content = entry["content"]
            if len(content) > self._TOOL_RESULT_MAX_CHARS:
                entry["content"] = content[:self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
        entry.setdefault("timestamp", datetime.now().isoformat())
        return entry

    _TOOL_RESULT_MAX_CHARS = 500

    def append_message(self, session: Session, message: dict[str, Any]) -> None:
        """Append a single message to the session JSONL file (incremental write).

        This is the core of realtime persistence: each message is flushed to
        disk immediately so that a crash mid-turn does not lose data.

        Also updates the in-memory ``session.messages`` list.
        """
        import os
        path = self._get_session_path(session.key)

        # If the file doesn't exist yet, write the metadata header first.
        if not path.exists():
            self._write_metadata_line(path, session)

        entry = self._prepare_entry(message)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

        session.messages.append(entry)
        session.updated_at = datetime.now()

    def update_metadata(self, session: Session) -> None:
        """Rewrite the session file to update the metadata header line.

        Called at the end of a turn (low frequency) to persist
        ``last_consolidated``, ``updated_at``, and other metadata fields.
        This rewrites the entire file — acceptable because it only happens
        once per turn, not per message.
        """
        self.save(session)

    def _write_metadata_line(self, path: Path, session: Session) -> None:
        """Write (or overwrite) just the metadata first-line to *path*."""
        metadata_line = {
            "_type": "metadata",
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata,
            "last_consolidated": session.last_consolidated,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")

    def save(self, session: Session) -> None:
        """Save a session to disk (full rewrite)."""
        path = self._get_session_path(session.key)

        with open(path, "w", encoding="utf-8") as f:
            metadata_line = {
                "_type": "metadata",
                "key": session.key,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "metadata": session.metadata,
                "last_consolidated": session.last_consolidated
            }
            f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
            for msg in session.messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        self._cache[session.key] = session
    
    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        self._cache.pop(key, None)
    
    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.
        
        Returns:
            List of session info dicts.
        """
        sessions = []
        
        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                # Read just the metadata line
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            key = data.get("key") or path.stem.replace("_", ":", 1)
                            sessions.append({
                                "key": key,
                                "created_at": data.get("created_at"),
                                "updated_at": data.get("updated_at"),
                                "path": str(path)
                            })
            except Exception:
                continue
        
        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)
