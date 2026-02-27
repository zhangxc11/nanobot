"""Session management for conversation history."""

import base64
import hashlib
import json
import mimetypes
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Any

from loguru import logger

from nanobot.utils.helpers import ensure_dir, safe_filename


# ── Image reference helpers ─────────────────────────────────────────
# These functions convert between inline base64 images (used by LLM APIs)
# and file:/// references (used in session JSONL for compact storage).

def _extract_and_save_images(
    content: Any,
    workspace: Path,
) -> Any:
    """Replace inline base64 image data with file:/// references.

    Scans multimodal content (list of dicts) for ``image_url`` items whose
    URL starts with ``data:``.  For each, the base64 payload is decoded and
    saved to ``workspace/uploads/<date>/<hash>.<ext>``, and the URL is
    replaced with ``file:///<path>?mime=<mime>``.

    Non-list content (plain strings) is returned unchanged.
    """
    if not isinstance(content, list):
        return content

    uploads_dir = workspace / "uploads"
    today = date.today().isoformat()
    day_dir = uploads_dir / today

    new_content = []
    for item in content:
        if (
            isinstance(item, dict)
            and item.get("type") == "image_url"
            and isinstance(item.get("image_url"), dict)
        ):
            url = item["image_url"].get("url", "")
            if url.startswith("data:"):
                saved = _save_base64_image(url, day_dir)
                if saved:
                    file_path, mime_type = saved
                    new_item = {
                        "type": "image_url",
                        "image_url": {
                            "url": f"file://{file_path}?mime={mime_type}"
                        },
                    }
                    new_content.append(new_item)
                    continue
        new_content.append(item)

    return new_content


def _save_base64_image(data_url: str, day_dir: Path) -> tuple[str, str] | None:
    """Decode a data: URL, save to disk, return (file_path, mime_type).

    Returns None if decoding or saving fails.
    """
    try:
        # Parse data:image/jpeg;base64,/9j/4AAQ...
        header, b64_data = data_url.split(",", 1)
        # header is like "data:image/jpeg;base64"
        mime_part = header.split(";")[0]  # "data:image/jpeg"
        mime_type = mime_part.split(":", 1)[1] if ":" in mime_part else "image/jpeg"

        raw = base64.b64decode(b64_data)

        # Determine file extension from MIME type
        ext = mimetypes.guess_extension(mime_type) or ".jpg"
        if ext == ".jpe":
            ext = ".jpg"

        # Use content hash for deduplication
        content_hash = hashlib.md5(raw).hexdigest()[:12]
        filename = f"{content_hash}{ext}"

        day_dir.mkdir(parents=True, exist_ok=True)
        file_path = day_dir / filename

        if not file_path.exists():
            file_path.write_bytes(raw)
            logger.debug("Saved base64 image to {} ({:.1f} KB)", file_path, len(raw) / 1024)
        else:
            logger.debug("Image already exists: {}", file_path)

        return str(file_path), mime_type

    except Exception as e:
        logger.warning("Failed to save base64 image: {}", e)
        return None


def _restore_image_refs(content: Any) -> Any:
    """Restore file:/// references back to inline base64 data: URLs.

    Scans multimodal content for ``image_url`` items whose URL starts with
    ``file://``.  For each, the file is read from disk and encoded as a
    ``data:<mime>;base64,...`` URL.

    If the file does not exist, the image item is dropped with a warning.
    Existing ``data:`` URLs (from old sessions) are passed through unchanged.
    """
    if not isinstance(content, list):
        return content

    new_content = []
    for item in content:
        if (
            isinstance(item, dict)
            and item.get("type") == "image_url"
            and isinstance(item.get("image_url"), dict)
        ):
            url = item["image_url"].get("url", "")
            if url.startswith("file://"):
                restored = _load_file_as_data_url(url)
                if restored:
                    new_content.append({
                        "type": "image_url",
                        "image_url": {"url": restored},
                    })
                else:
                    logger.warning("Dropping image ref (file not found): {}", url[:100])
                continue
        new_content.append(item)

    return new_content


def _load_file_as_data_url(file_url: str) -> str | None:
    """Read a file:// URL and return a data: base64 URL.

    The file_url format is: ``file:///path/to/image.jpg?mime=image/jpeg``
    """
    try:
        from urllib.parse import urlparse, parse_qs

        parsed = urlparse(file_url)
        file_path = parsed.path  # /absolute/path/to/image.jpg
        query = parse_qs(parsed.query)
        mime_type = query.get("mime", ["image/jpeg"])[0]

        p = Path(file_path)
        if not p.is_file():
            return None

        raw = p.read_bytes()
        b64 = base64.b64encode(raw).decode()
        return f"data:{mime_type};base64,{b64}"

    except Exception as e:
        logger.warning("Failed to restore image from {}: {}", file_url[:80], e)
        return None


# ── Session & SessionManager ────────────────────────────────────────


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
        """Return unconsolidated messages for LLM input, aligned to valid boundaries.

        The slice is adjusted so that:

        1. **Start boundary** — never starts with orphaned ``tool`` result
           messages (which would cause Anthropic's "unexpected tool_use_id"
           error).  Valid start boundaries are a ``user`` or ``assistant``
           message.

        2. **End boundary** — never ends with an incomplete tool-call chain.
           If the last ``assistant`` message has ``tool_calls`` but some (or
           all) corresponding ``tool`` result messages are missing (e.g. due
           to a mid-turn crash or self-restart), the incomplete tail is
           trimmed back to the last complete message.  This prevents
           Anthropic's "tool_use ids were found without tool_result blocks"
           error.

        3. **Error messages** — assistant messages whose ``content`` starts
           with ``"Error calling LLM:"`` are stripped, as they are diagnostic
           artefacts from previous failed turns that would confuse the model.
        """
        unconsolidated = self.messages[self.last_consolidated:]
        sliced = unconsolidated[-max_messages:]

        # ── Phase 1: Align start boundary ──
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

        # ── Phase 2: Strip error artefacts ──
        # Remove assistant messages that are LLM error diagnostics from
        # previous failed turns (e.g. "Error calling LLM: ...").
        sliced = [
            m for m in sliced
            if not (
                m.get("role") == "assistant"
                and isinstance(m.get("content"), str)
                and m["content"].startswith("Error calling LLM:")
            )
        ]

        # ── Phase 3: Trim incomplete tool-call tail ──
        # Walk backwards from the end.  If we find an assistant message with
        # tool_calls whose tool_result messages are missing (partially or
        # fully), trim everything from that assistant message onwards.
        sliced = self._trim_incomplete_tool_tail(sliced)

        out: list[dict[str, Any]] = []
        for m in sliced:
            entry: dict[str, Any] = {"role": m["role"], "content": m.get("content", "")}
            for k in ("tool_calls", "tool_call_id", "name"):
                if k in m:
                    entry[k] = m[k]
            # Restore file:/// references back to data: base64 for LLM input
            entry["content"] = _restore_image_refs(entry["content"])
            out.append(entry)
        return out

    @staticmethod
    def _trim_incomplete_tool_tail(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove incomplete tool-call chains from the message list.

        Scans for ``assistant`` messages with ``tool_calls`` and verifies
        that all expected ``tool`` result messages exist immediately after.
        If any are missing (e.g. due to a mid-turn crash), the assistant
        message and its partial tool results are **removed** while
        preserving subsequent user messages and other valid content.

        Returns a (possibly shorter) list with all tool-call chains complete.
        """
        result: list[dict[str, Any]] = []
        i = 0
        while i < len(messages):
            m = messages[i]
            if m.get("role") == "assistant" and m.get("tool_calls"):
                # Collect expected tool_call ids
                expected_ids = {tc["id"] for tc in m["tool_calls"]}

                # Scan ahead for tool results belonging to this chain
                chain_end = i + 1
                actual_ids: set[str] = set()
                while chain_end < len(messages):
                    nm = messages[chain_end]
                    if nm.get("role") == "tool" and nm.get("tool_call_id"):
                        actual_ids.add(nm["tool_call_id"])
                        chain_end += 1
                    else:
                        break

                if expected_ids == actual_ids:
                    # Complete chain — keep assistant + all tool results
                    result.append(m)
                    for j in range(i + 1, chain_end):
                        result.append(messages[j])
                    i = chain_end
                else:
                    # Incomplete chain — skip assistant + partial tool results
                    logger.warning(
                        "Removing incomplete tool-call chain at index {}: "
                        "expected {} tool_results, found {}",
                        i,
                        len(expected_ids),
                        len(actual_ids),
                    )
                    i = chain_end  # Skip past the partial results
            else:
                result.append(m)
                i += 1

        return result
    
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
        - Extracts base64 images and saves them as files, replacing with
          ``file:///`` references to keep JSONL compact.
        - Ensures a ``timestamp`` is present.
        """
        from datetime import datetime

        entry = {k: v for k, v in message.items() if k != "reasoning_content"}
        if entry.get("role") == "tool" and isinstance(entry.get("content"), str):
            content = entry["content"]
            if len(content) > self._TOOL_RESULT_MAX_CHARS:
                entry["content"] = content[:self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
        # Extract base64 images from multimodal content and save as files
        entry["content"] = _extract_and_save_images(entry.get("content"), self.workspace)
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

    # ── Session routing (for /new command) ──────────────────────────

    _ROUTING_FILE = "_routing.json"

    def _routing_path(self) -> Path:
        return self.sessions_dir / self._ROUTING_FILE

    def _load_routing(self) -> dict[str, str]:
        """Load the routing table from disk.

        The routing table maps a *natural* session key (``channel:chat_id``)
        to the *actual* session key currently in use (which may have a
        timestamp suffix after ``/new``).
        """
        path = self._routing_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to load routing table, resetting")
            return {}

    def _save_routing(self, table: dict[str, str]) -> None:
        """Persist the routing table to disk."""
        path = self._routing_path()
        path.write_text(json.dumps(table, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def resolve_session_key(self, natural_key: str) -> str:
        """Resolve a natural session key to its routed key.

        If there is a routing entry for *natural_key*, return the mapped key.
        Otherwise return *natural_key* unchanged.
        """
        table = self._load_routing()
        return table.get(natural_key, natural_key)

    def create_new_session(self, channel: str, chat_id: str, old_key: str) -> str:
        """Create a new session for a channel/chat_id pair (``/new`` command).

        * Archives the current session file by renaming it with a timestamp
          suffix so it is preserved but no longer the "active" session.
        * Creates a fresh empty session with the *natural* key
          (``channel:chat_id``) so that subsequent messages land in the new
          session without requiring a routing table entry.
        * Removes any existing routing entry for this natural key (the new
          session uses the natural key directly).

        Returns the new session key.
        """
        import time

        ts = int(time.time())
        natural_key = f"{channel}:{chat_id}"

        # The old_key might already be the natural key or a previously routed key.
        # Archive the file behind old_key (if it exists).
        old_path = self._get_session_path(old_key)
        if old_path.exists():
            archive_key = f"{old_key}_{ts}"
            archive_path = self._get_session_path(archive_key)
            try:
                old_path.rename(archive_path)
                logger.info("Archived session {} → {}", old_key, archive_path.name)
            except Exception:
                logger.exception("Failed to archive session {}", old_key)

        # Invalidate old session from cache
        self.invalidate(old_key)
        self.invalidate(natural_key)

        # Remove routing entry — the new session uses the natural key directly
        table = self._load_routing()
        if natural_key in table:
            del table[natural_key]
            self._save_routing(table)

        # Create fresh session with the natural key
        new_session = Session(key=natural_key)
        self.save(new_session)

        return natural_key
