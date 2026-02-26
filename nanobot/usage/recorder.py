"""Unified token usage recorder — SQLite backend.

All nanobot invocations (CLI, IM gateway, web worker, cron) record LLM
token usage through this module.  The database lives at
``~/.nanobot/workspace/analytics.db`` by default (shared with the
web-chat gateway's read-only analytics queries).

Thread-safety is provided by SQLite WAL mode and per-call connections.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from loguru import logger

DEFAULT_DB_PATH = Path.home() / ".nanobot" / "workspace" / "analytics.db"

SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS token_usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key       TEXT NOT NULL,
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens      INTEGER DEFAULT 0,
    llm_calls         INTEGER DEFAULT 0,
    started_at        TEXT NOT NULL,
    finished_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_session  ON token_usage(session_key);
CREATE INDEX IF NOT EXISTS idx_usage_started  ON token_usage(started_at);
CREATE INDEX IF NOT EXISTS idx_usage_finished ON token_usage(finished_at);
CREATE INDEX IF NOT EXISTS idx_usage_model    ON token_usage(model);
"""


class UsageRecorder:
    """SQLite-backed token usage recorder.

    Parameters
    ----------
    db_path:
        Path to the SQLite database.  Defaults to
        ``~/.nanobot/workspace/analytics.db``.  Pass ``":memory:"`` for
        testing.
    """

    def __init__(self, db_path: Path | str | None = None):
        if db_path is None:
            db_path = DEFAULT_DB_PATH
        self.db_path = str(db_path)
        self._persistent_conn: sqlite3.Connection | None = None
        if self.db_path == ":memory:":
            self._persistent_conn = sqlite3.connect(":memory:")
            self._persistent_conn.execute("PRAGMA journal_mode=WAL")
            self._persistent_conn.row_factory = sqlite3.Row
        else:
            os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._ensure_schema()

    # ── Connection helpers ──

    def _connect(self) -> sqlite3.Connection:
        """Return a connection.  For *:memory:* returns the persistent one."""
        if self._persistent_conn is not None:
            return self._persistent_conn
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)

    # ── Write ──

    def record(
        self,
        session_key: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        llm_calls: int = 0,
        started_at: str = "",
        finished_at: str = "",
    ) -> int:
        """Insert a usage record.  Returns the new row id.

        Thread-safe: each call opens its own connection (or reuses the
        persistent one for *:memory:*).
        """
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO token_usage
                        (session_key, model, prompt_tokens, completion_tokens,
                         total_tokens, llm_calls, started_at, finished_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (session_key, model, prompt_tokens, completion_tokens,
                     total_tokens, llm_calls, started_at, finished_at),
                )
                row_id = cur.lastrowid
                logger.debug(
                    "Recorded usage: session={} model={} tokens={} (row {})",
                    session_key, model, total_tokens, row_id,
                )
                return row_id
        except Exception:
            logger.exception("Failed to record usage for session={}", session_key)
            return -1

    # ── Read helpers (used by web-chat gateway via AnalyticsDB) ──
    # The gateway still uses its own AnalyticsDB wrapper for read queries.
    # These are provided for convenience / future use.

    def get_global_usage(self) -> dict[str, Any]:
        """Aggregate usage across all sessions."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(prompt_tokens), 0)     AS total_prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS total_completion_tokens,
                       COALESCE(SUM(total_tokens), 0)      AS total_tokens,
                       COALESCE(SUM(llm_calls), 0)         AS total_llm_calls
                FROM token_usage
                """
            ).fetchone()
            return {
                "total_prompt_tokens": row["total_prompt_tokens"],
                "total_completion_tokens": row["total_completion_tokens"],
                "total_tokens": row["total_tokens"],
                "total_llm_calls": row["total_llm_calls"],
            }

    def get_session_usage(self, session_key: str) -> dict[str, Any]:
        """Aggregate usage for a single session."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(prompt_tokens), 0)     AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                       COALESCE(SUM(total_tokens), 0)      AS total_tokens,
                       COALESCE(SUM(llm_calls), 0)         AS llm_calls
                FROM token_usage WHERE session_key = ?
                """,
                (session_key,),
            ).fetchone()
            return {
                "session_key": session_key,
                "prompt_tokens": row["prompt_tokens"],
                "completion_tokens": row["completion_tokens"],
                "total_tokens": row["total_tokens"],
                "llm_calls": row["llm_calls"],
            }
