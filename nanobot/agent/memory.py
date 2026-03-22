"""Memory system for persistent agent memory."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from loguru import logger

from nanobot.utils.helpers import ensure_dir

if TYPE_CHECKING:
    from nanobot.providers.base import LLMProvider
    from nanobot.session.manager import Session
    from nanobot.usage.detail_logger import LLMDetailLogger
    from nanobot.usage.recorder import UsageRecorder

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONSOLIDATION_DEFAULT_MAX_TOKENS = 16384
_CONSOLIDATION_TIMEOUT = httpx.Timeout(connect=30.0, read=600.0, write=30.0, pool=30.0)
_MAX_RETRIES = 3

_RETRIABLE_TIMEOUT_KEYWORDS = ("timeout", "connection", "network")
_RETRIABLE_RATE_KEYWORDS = ("429", "rate", "too many", "负载")

_SAVE_MEMORY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save the memory consolidation result to persistent storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "history_entry": {
                        "type": "string",
                        "description": (
                            "A paragraph (2-5 sentences) summarizing key events/decisions/topics. "
                            "Start with [YYYY-MM-DD HH:MM]. Include detail useful for grep search."
                        ),
                    },
                    "memory_update": {
                        "type": "string",
                        "description": (
                            "Full updated long-term memory as markdown. Include all existing "
                            "facts plus new ones. Return unchanged if nothing new."
                        ),
                    },
                },
                "required": ["history_entry", "memory_update"],
            },
        },
    }
]

# Keys to keep when stripping session messages for the consolidation prompt.
_KEEP_KEYS = {"role", "content", "tool_calls", "tool_call_id", "name"}


class MemoryStore:
    """Two-layer memory: MEMORY.md (long-term facts) + HISTORY.md (grep-searchable log)."""

    def __init__(self, workspace: Path):
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "HISTORY.md"

    # ------------------------------------------------------------------
    # Basic I/O
    # ------------------------------------------------------------------

    def read_long_term(self) -> str:
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    def write_long_term(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    def append_history(self, entry: str) -> None:
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")

    def get_memory_context(self) -> str:
        long_term = self.read_long_term()
        return f"## Long-term Memory\n{long_term}" if long_term else ""

    # ------------------------------------------------------------------
    # Consolidation
    # ------------------------------------------------------------------

    async def consolidate(
        self,
        session: Session,
        provider: LLMProvider,
        model: str,
        *,
        archive_all: bool = False,
        memory_window: int = 50,
        detail_logger: LLMDetailLogger | None = None,
        usage_recorder: UsageRecorder | None = None,
        session_system_msg: dict | None = None,
        session_tools: list[dict] | None = None,
        max_tokens: int | None = None,
    ) -> bool:
        """Consolidate old messages into MEMORY.md + HISTORY.md via LLM tool call.

        Returns True on success (including no-op), False on failure.
        """
        # ── 1. Determine message slice ────────────────────────────────
        old_messages, keep_messages, keep_count = self._select_messages(
            session, archive_all, memory_window,
        )
        if old_messages is None:
            return True  # nothing to consolidate

        # ── 2. Sanitize message slice ─────────────────────────────────
        old_messages = self._sanitize_slice(old_messages, keep_messages)
        if not old_messages:
            return True

        logger.info(
            "Memory consolidation: {} to consolidate, {} to keep",
            len(old_messages), keep_count,
        )

        # ── 3. Build consolidation prompt ─────────────────────────────
        current_memory = self.read_long_term()
        messages, tools = self._build_prompt(
            old_messages, current_memory, session_system_msg, session_tools,
        )

        # ── 4. Call LLM with retry ────────────────────────────────────
        effective_max_tokens = max_tokens or _CONSOLIDATION_DEFAULT_MAX_TOKENS
        try:
            response = await self._call_with_retry(
                provider, messages, tools, model, effective_max_tokens,
            )
        except Exception:
            logger.exception("Memory consolidation failed (all retries exhausted)")
            return False

        # ── 5. Record usage / logging ─────────────────────────────────
        self._record_usage(
            response, session, model, provider, messages,
            detail_logger, usage_recorder,
        )

        # ── 6. Validate response ─────────────────────────────────────
        if not response.has_tool_calls:
            _fr = getattr(response, "finish_reason", "unknown")
            _preview = str(response.content)[:200] if response.content else ""
            logger.warning(
                "Memory consolidation: LLM did not call save_memory "
                "(finish_reason={}, content_preview={}), skipping",
                _fr, _preview,
            )
            return False

        called_tool = response.tool_calls[0].name
        if called_tool != "save_memory":
            logger.warning(
                "Memory consolidation: LLM called '{}' instead of save_memory, skipping",
                called_tool,
            )
            return False

        # ── 7. Extract and save results ───────────────────────────────
        args = response.tool_calls[0].arguments
        if isinstance(args, str):
            args = json.loads(args)
        if not isinstance(args, dict):
            logger.warning(
                "Memory consolidation: unexpected arguments type {}",
                type(args).__name__,
            )
            return False

        if entry := args.get("history_entry"):
            if not isinstance(entry, str):
                entry = json.dumps(entry, ensure_ascii=False)
            self.append_history(entry)
        if update := args.get("memory_update"):
            if not isinstance(update, str):
                update = json.dumps(update, ensure_ascii=False)
            if update != current_memory:
                self.write_long_term(update)

        # ── 8. Update session bookmark ────────────────────────────────
        session.last_consolidated = (
            0 if archive_all else len(session.messages) - keep_count
        )
        logger.info(
            "Memory consolidation done: {} messages, last_consolidated={}",
            len(session.messages), session.last_consolidated,
        )
        return True

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _select_messages(
        session: Session, archive_all: bool, memory_window: int,
    ) -> tuple[list[dict] | None, list[dict], int]:
        """Return (old_messages, keep_messages, keep_count) or (None, [], 0) if nothing to do."""
        if archive_all:
            logger.info(
                "Memory consolidation (archive_all): {} messages",
                len(session.messages),
            )
            return session.messages, [], 0

        keep_count = memory_window // 2
        if len(session.messages) <= keep_count:
            return None, [], 0
        if len(session.messages) - session.last_consolidated <= 0:
            return None, [], 0
        old = session.messages[session.last_consolidated:-keep_count]
        keep = session.messages[-keep_count:]
        return (old or None), keep, keep_count

    @staticmethod
    def _sanitize_slice(
        messages: list[dict], keep_messages: list[dict] | None = None,
    ) -> list[dict]:
        """Clean up message slice boundaries for valid LLM input.

        Three passes:
        1. Strip orphan tool_results from the start (§63).
        2. Strip trailing assistant messages whose tool_calls are entirely orphaned (§66).
        3. For partially matched assistant tool_calls, pull missing tool_results
           from the keep segment so no information is lost between consolidation
           rounds (§66 improvement).  Falls back to trimming if the keep segment
           does not contain the needed results.
        """
        # Pass 1: Strip leading orphan tool_results
        stripped_head = 0
        while messages and messages[0].get("role") == "tool":
            messages = messages[1:]
            stripped_head += 1
        if stripped_head:
            logger.debug(
                "Consolidation: stripped {} orphan tool_result(s) from start",
                stripped_head,
            )
        if not messages:
            return messages

        # Pass 2: Strip trailing assistant messages with tool_calls (no matching results)
        stripped_tail = 0
        while (
            messages
            and messages[-1].get("role") == "assistant"
            and messages[-1].get("tool_calls")
        ):
            messages = messages[:-1]
            stripped_tail += 1
        if stripped_tail:
            logger.debug(
                "Consolidation: stripped {} trailing orphan tool_use(s) from end",
                stripped_tail,
            )
        if not messages:
            return messages

        # Pass 3: Fix partial tool_call / tool_result mismatches
        # Build set of tool_result ids already in the slice
        result_ids = {
            m.get("tool_call_id")
            for m in messages
            if m.get("role") == "tool" and m.get("tool_call_id")
        }
        # Build lookup of tool_results available in the keep segment
        keep_results_by_id: dict[str, dict] = {}
        if keep_messages:
            for m in keep_messages:
                if m.get("role") == "tool" and m.get("tool_call_id"):
                    keep_results_by_id[m["tool_call_id"]] = m

        idx = -1
        while idx + 1 < len(messages):
            idx += 1
            msg = messages[idx]
            if msg.get("role") != "assistant" or not msg.get("tool_calls"):
                continue
            # Find tool_calls whose results are missing from the slice
            missing_tcs = [
                tc for tc in msg["tool_calls"]
                if tc.get("id") and tc["id"] not in result_ids
            ]
            if not missing_tcs:
                continue

            # Try to pull missing results from keep segment
            pulled = []
            still_missing = []
            for tc in missing_tcs:
                tc_id = tc["id"]
                if tc_id in keep_results_by_id:
                    pulled.append(keep_results_by_id[tc_id])
                    result_ids.add(tc_id)
                else:
                    still_missing.append(tc)

            if pulled:
                # Find insertion point: after the last existing tool_result for this assistant
                insert_at = idx + 1
                while (
                    insert_at < len(messages)
                    and messages[insert_at].get("role") == "tool"
                ):
                    insert_at += 1
                for i, tr in enumerate(pulled):
                    messages.insert(insert_at + i, tr)
                logger.debug(
                    "Consolidation: pulled {} tool_result(s) from keep segment "
                    "into slice for assistant message",
                    len(pulled),
                )

            # Trim tool_calls that are truly orphaned (not in slice AND not in keep)
            if still_missing:
                original_count = len(msg["tool_calls"])
                orphan_ids = {tc["id"] for tc in still_missing}
                msg["tool_calls"] = [
                    tc for tc in msg["tool_calls"]
                    if tc.get("id") not in orphan_ids
                ]
                logger.debug(
                    "Consolidation: trimmed {} truly orphan tool_call(s) from "
                    "assistant message (had {}, kept {})",
                    len(still_missing), original_count, len(msg["tool_calls"]),
                )
                if not msg["tool_calls"]:
                    del msg["tool_calls"]

        return messages

    @staticmethod
    def _build_prompt(
        old_messages: list[dict],
        current_memory: str,
        session_system_msg: dict | None,
        session_tools: list[dict] | None,
    ) -> tuple[list[dict], list[dict]]:
        """Build the consolidation messages and tool list.

        Two paths:
        - Cache-friendly: reuse session system prompt for Anthropic cache hits.
        - Fallback: standalone system prompt (no cache).

        In both paths, only ``_SAVE_MEMORY_TOOL`` is passed as tools to prevent
        the LLM from calling session tools (exec, read_file, etc.).
        """
        if session_system_msg is not None and session_tools is not None:
            # Cache-friendly path
            stripped = [
                {k: v for k, v in m.items() if k in _KEEP_KEYS}
                for m in old_messages
            ]
            instruction = (
                "The preceding messages are being archived from the active session context. "
                "You MUST call the save_memory tool with:\n"
                "- history_entry: A 2-5 sentence summary starting with [YYYY-MM-DD HH:MM], "
                "grep-searchable, covering key events/decisions/topics from the archived messages.\n"
                "- memory_update: Full updated long-term memory as markdown (existing facts plus any new ones). "
                "Return unchanged if nothing new.\n\n"
                "IMPORTANT: You MUST call the save_memory tool. Do NOT respond with text. "
                "Do NOT call any other tools. Only call save_memory.\n\n"
                f"## Current Long-term Memory\n{current_memory or '(empty)'}"
            )
            messages = (
                [session_system_msg]
                + stripped
                + [{"role": "user", "content": instruction}]
            )
        else:
            # Fallback path
            lines = []
            for m in old_messages:
                if not m.get("content"):
                    continue
                tools_used = (
                    f" [tools: {', '.join(m['tools_used'])}]"
                    if m.get("tools_used")
                    else ""
                )
                lines.append(
                    f"[{m.get('timestamp', '?')[:16]}] "
                    f"{m['role'].upper()}{tools_used}: {m['content']}"
                )
            prompt = (
                "Process this conversation and call the save_memory tool with your consolidation.\n\n"
                f"## Current Long-term Memory\n{current_memory or '(empty)'}\n\n"
                f"## Conversation to Process\n{chr(10).join(lines)}"
            )
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a memory consolidation agent. "
                        "Call the save_memory tool with your consolidation of the conversation."
                    ),
                },
                {"role": "user", "content": prompt},
            ]

        return messages, _SAVE_MEMORY_TOOL

    @staticmethod
    async def _call_with_retry(
        provider: LLMProvider,
        messages: list[dict],
        tools: list[dict],
        model: str,
        max_tokens: int,
    ):
        """Call provider.chat() with unified retry for transient errors.

        Retries up to ``_MAX_RETRIES`` times for timeout/connection errors
        (base wait 2 s) and rate-limit errors (base wait 5 s) with exponential
        backoff.  Non-retriable errors are raised immediately.
        """
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return await provider.chat(
                    messages=messages,
                    tools=tools,
                    model=model,
                    max_tokens=max_tokens,
                    timeout=_CONSOLIDATION_TIMEOUT,
                )
            except Exception as err:
                if attempt >= _MAX_RETRIES:
                    raise

                err_lower = str(err).lower()
                base_wait: int | None = None

                if any(kw in err_lower for kw in _RETRIABLE_TIMEOUT_KEYWORDS):
                    base_wait = 2
                elif any(kw in err_lower for kw in _RETRIABLE_RATE_KEYWORDS):
                    base_wait = 5

                if base_wait is None:
                    raise  # non-retriable

                wait = base_wait * (2 ** attempt)
                logger.warning(
                    "Memory consolidation: transient error '{}' "
                    "(attempt {}/{}), retrying in {}s…",
                    type(err).__name__, attempt + 1, _MAX_RETRIES, wait,
                )
                await asyncio.sleep(wait)

    @staticmethod
    def _record_usage(
        response,
        session: Session,
        model: str,
        provider: LLMProvider,
        messages: list[dict],
        detail_logger: LLMDetailLogger | None,
        usage_recorder: UsageRecorder | None,
    ) -> None:
        """Record consolidation LLM call to detail logger and usage analytics."""
        from datetime import datetime as _dt

        call_ts = _dt.now().isoformat()
        usage = response.usage or {}
        provider_name = getattr(provider, "provider_name", "")

        if detail_logger is not None:
            try:
                log_messages = []
                for m in messages:
                    content = m.get("content", "")
                    if isinstance(content, str) and len(content) > 300:
                        content = content[:300] + "..."
                    log_messages.append({"role": m.get("role", "unknown"), "content": content})

                detail_logger.log_call(
                    session_key=session.key,
                    model=model,
                    iteration=0,
                    messages=log_messages,
                    response_content=(
                        response.content if isinstance(response.content, str) else None
                    ),
                    response_tool_calls=(
                        [{"name": tc.name, "arguments": tc.arguments} for tc in response.tool_calls]
                        if response.has_tool_calls
                        else None
                    ),
                    response_finish_reason=(
                        response.finish_reason
                        if hasattr(response, "finish_reason")
                        else "stop"
                    ),
                    response_usage=usage,
                    provider=provider_name,
                )
            except Exception:
                logger.exception("Failed to log consolidation LLM call to detail_logger")

        if usage_recorder is not None and usage:
            try:
                usage_recorder.record(
                    session_key=session.key,
                    model=model,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                    llm_calls=1,
                    started_at=call_ts,
                    finished_at=call_ts,
                    cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
                    cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
                    provider=provider_name,
                )
            except Exception:
                logger.debug("Failed to record consolidation usage")
