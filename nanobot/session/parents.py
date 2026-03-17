"""Session parent-child relationship resolution.

Unified logic for resolving session parent-child relationships,
ported from web-chat frontend's resolveParent() (SessionList.tsx).

Resolution priority:
1. Manual override from session_parents.json
2. Subagent heuristic: subagent_{parent_id}_{8hex}
3. Webchat API session heuristic: timestamp-based matching
4. None (root session)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from loguru import logger

# ── Constants ────────────────────────────────────────────────────────

_PARENTS_FILENAME = "session_parents.json"
_SUBAGENT_PREFIX = "subagent_"
_WEBCHAT_PREFIX = "webchat_"

# Regex: 8 lowercase hex chars at end after underscore
_SUBAGENT_SUFFIX_RE = re.compile(r"^(.+)_([0-9a-f]{8})$")

# Regex: first 10-digit timestamp in suffix (after webchat_ prefix)
_TIMESTAMP_RE = re.compile(r"_(\d{10})(?:_|$)")

# Regex: 10-digit number anywhere in string
_TEN_DIGIT_RE = re.compile(r"\d{10}")


# ── Public API ───────────────────────────────────────────────────────


def load_manual_overrides(sessions_dir: str | Path) -> dict[str, str]:
    """Load manual parent overrides from session_parents.json.

    Args:
        sessions_dir: Path to the sessions directory.

    Returns:
        Dict mapping child_session_id → parent_session_id.
        Keys starting with '_' (comments/metadata) are filtered out.
    """
    path = Path(sessions_dir) / _PARENTS_FILENAME
    if not path.exists():
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load {_PARENTS_FILENAME}: {e}")
        return {}

    if not isinstance(data, dict):
        logger.warning(f"{_PARENTS_FILENAME} is not a JSON object")
        return {}

    # Filter out keys starting with '_' (comments/metadata)
    return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, str)}


def resolve_parent(
    session_id: str,
    all_session_ids: set[str],
    manual_overrides: dict[str, str],
) -> Optional[str]:
    """Resolve the parent session ID for a given session.

    Resolution priority:
    1. Manual override from session_parents.json
    2. Subagent heuristic: subagent_{parent_id}_{8hex}
    3. Webchat API session heuristic (timestamp-based)
    4. None (root session)

    Args:
        session_id: The session ID to resolve parent for.
        all_session_ids: Set of all known session IDs.
        manual_overrides: Manual overrides from load_manual_overrides().

    Returns:
        Parent session ID, or None if this is a root session.
    """
    # 1. Manual override — highest priority
    if session_id in manual_overrides:
        return manual_overrides[session_id]

    # 2. Subagent heuristic: subagent_{parent_sanitized}_{8hex}
    if session_id.startswith(_SUBAGENT_PREFIX):
        suffix = session_id[len(_SUBAGENT_PREFIX):]
        match = _SUBAGENT_SUFFIX_RE.match(suffix)
        if match:
            parent_id = match.group(1)
            # Return parent_id regardless of whether it exists in all_session_ids
            # (consistent with frontend behavior)
            return parent_id

    # 3. Webchat API session heuristic
    if session_id.startswith(_WEBCHAT_PREFIX):
        suffix = session_id[len(_WEBCHAT_PREFIX):]
        # Only for API sessions: suffix must contain non-digit characters
        if re.search(r"[^0-9]", suffix):
            ts_match = _TIMESTAMP_RE.search(suffix)
            if ts_match:
                ts = ts_match.group(1)
                ts_suffix = "_" + ts

                # Priority a: root session — candidate ends with _ts AND
                # prefix doesn't contain any 10-digit number.
                # When multiple candidates match, prefer shortest (most
                # likely the root session, e.g. webchat_ts over
                # webchat_dispatch_ts).
                priority_a: list[str] = []
                for candidate in all_session_ids:
                    if candidate != session_id and candidate.endswith(ts_suffix):
                        prefix = candidate[: len(candidate) - len(ts_suffix)]
                        if not _TEN_DIGIT_RE.search(prefix):
                            priority_a.append(candidate)
                if priority_a:
                    # Shortest first → prefer root-like sessions
                    priority_a.sort(key=len)
                    return priority_a[0]

                # Priority b: any session ending with _ts (supports three-level trees)
                priority_b: list[str] = []
                for candidate in all_session_ids:
                    if candidate != session_id and candidate.endswith(ts_suffix):
                        priority_b.append(candidate)
                if priority_b:
                    priority_b.sort(key=len)
                    return priority_b[0]

    # 4. No parent found — root session
    return None


def build_parent_map(sessions_dir: str | Path) -> dict[str, str]:
    """Build a complete parent mapping for all sessions in a directory.

    Scans .jsonl filenames (without reading file contents) and resolves
    parent for each session ID.

    Args:
        sessions_dir: Path to the sessions directory.

    Returns:
        Dict mapping child_session_id → parent_session_id.
        Only includes sessions that have a parent (root sessions are omitted).
    """
    sessions_dir = Path(sessions_dir)
    if not sessions_dir.is_dir():
        logger.warning(f"Sessions directory not found: {sessions_dir}")
        return {}

    # Collect all session IDs from .jsonl filenames
    all_session_ids: set[str] = set()
    for path in sessions_dir.glob("*.jsonl"):
        session_id = path.stem  # filename without .jsonl extension
        all_session_ids.add(session_id)

    # Load manual overrides
    manual_overrides = load_manual_overrides(sessions_dir)

    # Resolve parent for each session
    parent_map: dict[str, str] = {}
    for session_id in all_session_ids:
        parent = resolve_parent(session_id, all_session_ids, manual_overrides)
        if parent is not None:
            parent_map[session_id] = parent

    return parent_map


def is_child_of(
    child_id: str,
    parent_id: str,
    sessions_dir: str | Path,
) -> bool:
    """Check if child_id is a direct child of parent_id.

    Convenience function for CronTool and other consumers.

    Args:
        child_id: The potential child session ID.
        parent_id: The potential parent session ID.
        sessions_dir: Path to the sessions directory.

    Returns:
        True if child_id's resolved parent is parent_id.
    """
    parent_map = build_parent_map(sessions_dir)
    return parent_map.get(child_id) == parent_id
