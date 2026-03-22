"""§70-R1: Singleton managing all consolidation state across AgentLoop instances.

Replaces four scattered module-level / instance variables:
  - _consolidating (set)
  - _consolidation_tasks (set)
  - _consolidation_locks (WeakValueDictionary)
  - _pending_consolidation_done (dict)

Design principles:
  - Single Source of Truth for consolidation lifecycle.
  - PendingResult carries only last_consolidated (not archive_cut).
    Trim computes its own cut from current messages.
  - Lock lifetime tied to _SessionState (no WeakValueDictionary GC surprises).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from loguru import logger


@dataclass
class PendingResult:
    """Consolidation completion info waiting for the next iteration's trim."""

    last_consolidated: int
    success: bool


@dataclass
class _SessionState:
    """Per-session consolidation state."""

    consolidating: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: PendingResult | None = None


class ConsolidationState:
    """Singleton managing all consolidation state across AgentLoop instances.

    Usage from loop.py:
        from nanobot.agent.consolidation_state import consolidation_state

        # Query
        consolidation_state.is_consolidating(session.key)
        pending = consolidation_state.pop_pending(session.key)

        # Lifecycle
        lock = consolidation_state.begin(session.key)
        consolidation_state.complete(session.key, last_consolidated=N, success=True)

        # Task tracking
        consolidation_state.track_task(task)

        # Cleanup (e.g. /flush)
        consolidation_state.cleanup(session.key)
    """

    def __init__(self) -> None:
        self._sessions: dict[str, _SessionState] = {}
        self._tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]

    def _get(self, key: str) -> _SessionState:
        if key not in self._sessions:
            self._sessions[key] = _SessionState()
        return self._sessions[key]

    # ── Query ────────────────────────────────────────────────────────

    def is_consolidating(self, key: str) -> bool:
        return self._get(key).consolidating

    def pop_pending(self, key: str) -> PendingResult | None:
        """Pop and return pending result, or None."""
        st = self._get(key)
        result = st.pending
        st.pending = None
        return result

    # ── Lifecycle ────────────────────────────────────────────────────

    def begin(self, key: str) -> asyncio.Lock:
        """Mark consolidation as started, return the per-session lock."""
        st = self._get(key)
        st.consolidating = True
        logger.debug("§70: consolidation_state.begin({})", key)
        return st.lock

    def complete(self, key: str, last_consolidated: int, success: bool) -> None:
        """Mark consolidation as done, store pending result for next trim."""
        st = self._get(key)
        st.consolidating = False
        st.pending = PendingResult(
            last_consolidated=last_consolidated,
            success=success,
        )
        logger.debug(
            "§70: consolidation_state.complete({}, lc={}, success={})",
            key, last_consolidated, success,
        )

    def track_task(self, task: asyncio.Task) -> None:  # type: ignore[type-arg]
        """Track async task for cleanup (prevent GC of in-flight tasks)."""
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ── Cleanup ──────────────────────────────────────────────────────

    def cleanup(self, key: str) -> None:
        """Remove state for a session (e.g. after /flush)."""
        self._sessions.pop(key, None)
        logger.debug("§70: consolidation_state.cleanup({})", key)


# Module-level singleton — shared across all AgentLoop instances.
consolidation_state = ConsolidationState()
