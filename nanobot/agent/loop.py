"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import re
import weakref
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.context import ContextBuilder
from nanobot.agent.memory import MemoryStore
from nanobot.agent.budget import build_budget_alert
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.agent.callbacks import AgentResult, DefaultCallbacks
from nanobot.session.manager import Session, SessionManager
from nanobot.usage.recorder import UsageRecorder
from nanobot.usage.detail_logger import LLMDetailLogger
from nanobot.audit.logger import AuditLogger

if TYPE_CHECKING:
    from nanobot.config.schema import ChannelsConfig, ExecToolConfig
    from nanobot.cron.service import CronService


def _format_tokens(n: int) -> str:
    """Format token count to human-readable string (e.g. 12.3K)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


_TRUNCATION_WARNING_TEMPLATE = """⚠️ [System — Context Approaching Limit]
This session has {current} messages. Archival triggers at {max}.

Immediately write a session summary using write_file to:
  `{workspace}/sessions/session_summary/{session_id}.md`

Structure: Current Task / Key Decisions / Completed Work / Pending Items / \
Important Constraints / Open Questions

After writing the summary, continue your current task without interruption."""

_TRUNCATION_NOTICE_WITH_SUMMARY = """⚠️ [Context Truncation Notice]
{archived_count}

--- Session Summary ---
{summary_content}
--- End Summary ---

Full session log: `{workspace}/sessions/{session_id}.jsonl` (for precise lookup if summary is insufficient)
Do NOT re-do work that may have been completed in the archived portion — check files and git history first."""

_TRUNCATION_NOTICE_NO_SUMMARY = """⚠️ [Context Truncation Notice]
{archived_count}
No session summary was written before archival.
Full session log: `{workspace}/sessions/{session_id}.jsonl` (use grep/parse for context recovery)
Do NOT re-do work that may have been completed in the archived portion — check files and git history first."""


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    _TOOL_RESULT_MAX_CHARS = 500

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        memory_window: int = 100,
        reasoning_effort: str | None = None,
        brave_api_key: str | None = None,
        web_proxy: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        usage_recorder: UsageRecorder | None = None,
        detail_logger: LLMDetailLogger | None = None,
        audit_logger: AuditLogger | None = None,
        subagent_task_keeper: "Callable[[asyncio.Task], None] | None" = None,
        session_messenger: "Any | None" = None,
        read_file_hard_limit: int | None = None,
        subagent_manager: "SubagentManager | None" = None,  # §40: external singleton
        spawn_max_concurrency: int = 4,  # §46: spawn concurrency limit
        on_iteration: "Callable[[int, int, str | None], None] | None" = None,  # §47: iteration callback
    ):
        from nanobot.config.schema import ExecToolConfig
        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_window = memory_window
        self.reasoning_effort = reasoning_effort
        self.brave_api_key = brave_api_key
        self.web_proxy = web_proxy
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self.read_file_hard_limit = read_file_hard_limit
        self.usage_recorder = usage_recorder
        self.detail_logger = detail_logger
        self.audit_logger = audit_logger
        self.session_messenger = session_messenger
        self._on_iteration = on_iteration  # §47: iteration callback

        self.context = ContextBuilder(workspace)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        # §40: Use external SubagentManager if provided (web worker singleton),
        # otherwise create a new one (gateway mode, default behavior).
        if subagent_manager is not None:
            self.subagents = subagent_manager
            # §40 fix: warn if external SubagentManager has no usage_recorder —
            # this was the root cause of subagent usage data loss.
            if self.subagents.usage_recorder is None:
                logger.warning(
                    "External SubagentManager has usage_recorder=None; "
                    "subagent usage will NOT be recorded. "
                    "Pass usage_recorder=UsageRecorder() when creating the singleton."
                )
            # §48: Inherit detail_logger if external manager doesn't have one
            if self.subagents.detail_logger is None and self.detail_logger is not None:
                self.subagents.detail_logger = self.detail_logger
        else:
            self.subagents = SubagentManager(
                provider=provider,
                workspace=workspace,
                bus=bus,
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                reasoning_effort=reasoning_effort,
                brave_api_key=brave_api_key,
                web_proxy=web_proxy,
                exec_config=self.exec_config,
                restrict_to_workspace=restrict_to_workspace,
                usage_recorder=usage_recorder,
                session_manager=self.sessions,
                task_keeper=subagent_task_keeper,
                session_messenger=session_messenger,
                read_file_hard_limit=read_file_hard_limit,
                max_concurrency=spawn_max_concurrency,
                detail_logger=detail_logger,  # §48
            )

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._consolidating: set[str] = set()  # Session keys with consolidation in progress
        self._consolidation_tasks: set[asyncio.Task] = set()  # Strong refs to in-flight tasks
        self._consolidation_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._pending_consolidation_done: dict[str, dict] = {}  # §59: consolidation result pending trim
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._processing_lock = asyncio.Lock()
        self._last_session_list: dict[str, list[str]] = {}  # chat_id -> [session_id, ...]
        self._register_default_tools()
        if self.audit_logger is not None:
            self.tools.set_audit_logger(self.audit_logger)

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        rf_kwargs: dict = dict(workspace=self.workspace, allowed_dir=allowed_dir)
        if self.read_file_hard_limit is not None:
            rf_kwargs["hard_limit"] = self.read_file_hard_limit
        self.tools.register(ReadFileTool(**rf_kwargs))
        for cls in (WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
            path_append=self.exec_config.path_append,
        ))
        self.tools.register(WebSearchTool(api_key=self.brave_api_key, proxy=self.web_proxy))
        self.tools.register(WebFetchTool(proxy=self.web_proxy))
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from nanobot.agent.tools.mcp import connect_mcp_servers
        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
            self._mcp_connected = True
        except Exception as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None,
                          session_key: str = "",
                          tools: ToolRegistry | None = None) -> None:
        """Update context for all tools that need routing info.

        Parameters
        ----------
        tools:
            ToolRegistry to operate on.  Falls back to ``self.tools`` if None.
            For concurrent sessions, pass the cloned registry.
        """
        _tools = tools or self.tools

        if message_tool := _tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.set_context(channel, chat_id, message_id)

        if spawn_tool := _tools.get("spawn"):
            if isinstance(spawn_tool, SpawnTool):
                spawn_tool.set_context(channel, chat_id, session_key=session_key)

        if cron_tool := _tools.get("cron"):
            if isinstance(cron_tool, CronTool):
                cron_tool.set_context(channel, chat_id, session_key=session_key, session_id=session_key.replace(":", "_"),
                                      sessions_dir=self.workspace / "sessions")

        # Audit context: session_key + channel + chat_id
        _tools.set_audit_context(
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
        )

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content."""
        if not text:
            return None
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip() or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hint, e.g. 'web_search("query")'."""
        def _fmt(tc):
            args = (tc.arguments[0] if isinstance(tc.arguments, list) else tc.arguments) or {}
            val = next(iter(args.values()), None) if isinstance(args, dict) else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}…")' if len(val) > 40 else f'{tc.name}("{val}")'
        return ", ".join(_fmt(tc) for tc in tool_calls)

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        """Check if an LLM error is transient and worth retrying.

        Delegates to the shared ``agent.retry`` module (Phase 28).
        """
        from nanobot.agent.retry import is_retryable
        return is_retryable(error)

    async def _chat_with_retry(
        self,
        *,
        provider: LLMProvider | None = None,
        messages: list[dict],
        tools: list[dict] | None,
        model: str,
        temperature: float,
        max_tokens: int,
        reasoning_effort: str | None = None,
        read_timeout: float | None = None,
        progress_fn: Callable[..., Awaitable[None]] | None = None,
        session_key: str = "",
    ):
        """Call provider.chat() with exponential backoff for transient errors.

        Phase 28: Enhanced with smart retry delays — fast for disconnected/
        timeout errors (2/4/8s), slow for rate-limit/overload (10/20/40s).
        Retries up to 7 times. Non-retryable errors are raised immediately.

        Parameters
        ----------
        provider:
            LLM provider to use.  Falls back to ``self.provider`` if None.
        reasoning_effort:
            Optional reasoning effort hint passed to the provider.
        """
        from nanobot.agent.retry import is_fast_retryable, compute_retry_delay, is_timeout_error, ping_api, wait_for_recovery

        _provider = provider or self.provider
        max_retries = 7

        # §60: Extract provider diagnostics for timeout/error logging
        _prov_name = getattr(_provider, "provider_name", None) or type(_provider).__name__
        # Get api_base from the active provider instance (ProviderPool wraps it)
        _active_providers = getattr(_provider, "_providers", {})
        if _active_providers:
            _active_name = getattr(_provider, "_active_provider", None)
            if _active_name and _active_name in _active_providers:
                _inner_provider, _ = _active_providers[_active_name]
                _prov_base = getattr(_inner_provider, "api_base", None) or "?"
            else:
                _prov_base = "?"
        else:
            _prov_base = getattr(_provider, "api_base", None) or "?"

        for attempt in range(max_retries + 1):
            try:
                kwargs: dict = dict(
                    messages=messages,
                    tools=tools,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                if reasoning_effort is not None:
                    kwargs["reasoning_effort"] = reasoning_effort
                # §61: Pass extended timeout if provided
                if read_timeout is not None:
                    import httpx as _httpx
                    kwargs["timeout"] = _httpx.Timeout(connect=30.0, read=read_timeout, write=30.0, pool=30.0)
                return await _provider.chat(**kwargs)
            except Exception as e:
                if not self._is_retryable(e) or attempt >= max_retries:
                    raise

                # §61: Timeout-specific diagnosis — ping to determine cause
                if is_timeout_error(e):
                    # Get api_base from inner provider if ProviderPool
                    _active_providers_inner = getattr(_provider, "_providers", {})
                    if _active_providers_inner:
                        _active_name_inner = getattr(_provider, "_active_provider", None)
                        if _active_name_inner and _active_name_inner in _active_providers_inner:
                            _inner_prov, _ = _active_providers_inner[_active_name_inner]
                            _api_base = getattr(_inner_prov, "api_base", None) or ""
                        else:
                            _api_base = ""
                    else:
                        _api_base = getattr(_provider, "api_base", None) or ""
                    reachable = await ping_api(_api_base)

                    if reachable:
                        # Network fine → content too large, retry won't help
                        logger.warning(
                            "§61: Timeout but API reachable (content too large). "
                            "provider={}, api_base={}, session={}",
                            _prov_name, _prov_base, session_key,
                        )
                        if progress_fn:
                            try:
                                await progress_fn("⏳ 请求超时（API 可达），将建议模型缩短输出...")
                            except Exception:
                                pass
                        from nanobot.providers.base import LLMResponse
                        return LLMResponse(content=None, finish_reason="timeout")
                    else:
                        # Network issue → wait for recovery then retry
                        logger.warning(
                            "§61: Timeout + API unreachable (network issue). "
                            "provider={}, api_base={}, session={}. Waiting for recovery...",
                            _prov_name, _prov_base, session_key,
                        )
                        if progress_fn:
                            try:
                                await progress_fn("⏳ 网络不可达，等待恢复...")
                            except Exception:
                                pass
                        recovered = await wait_for_recovery(_api_base, max_wait=20.0)
                        if recovered:
                            logger.info("§61: Network recovered, retrying LLM call")
                            if progress_fn:
                                try:
                                    await progress_fn("✅ 网络恢复，重试中...")
                                except Exception:
                                    pass
                            continue  # retry the for loop
                        else:
                            logger.warning(
                                "§61: Network still unreachable after recovery wait. "
                                "Falling through to retry loop."
                            )
                            # Fall through to normal retry logic below
                            # (network jitter may resolve with retry delays)

                fast = is_fast_retryable(e)
                delay = compute_retry_delay(attempt, fast)
                retry_type = "fast" if fast else "slow"
                _msg_count = len(messages) if messages else 0
                _msg_chars = sum(len(str(m.get("content", ""))) for m in messages) if messages else 0
                logger.warning(
                    "LLM call failed (attempt {}/{}, {} retry) [session={}]: {}. "
                    "provider={}, api_base={}, model={}, context={}msgs/{}chars. "
                    "Retrying in {:.0f}s...",
                    attempt + 1, max_retries, retry_type, session_key, str(e)[:200],
                    _prov_name, _prov_base, model, _msg_count, _msg_chars,
                    delay,
                )
                if progress_fn:
                    try:
                        label = "网络断连" if fast else ("API 过载" if "overload" in str(e).lower() else "API 错误")
                        await progress_fn(
                            f"⏳ {label}，等待 {delay:.0f}s 后重试 ({attempt + 1}/{max_retries})"
                        )
                    except Exception:
                        pass  # progress notification is best-effort
                await asyncio.sleep(delay)

        # Unreachable, but satisfies type checker
        raise RuntimeError("Exhausted retries")

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        session: Session | None = None,
        callbacks: DefaultCallbacks | None = None,
        *,
        provider: LLMProvider | None = None,
        model: str | None = None,
        tools: ToolRegistry | None = None,
    ) -> tuple[str | None, list[str], list[dict]]:
        """Run the agent iteration loop. Returns (final_content, tools_used, messages).

        When *session* is provided, each assistant / tool message is persisted
        to the session JSONL **immediately** via ``SessionManager.append_message``,
        so that a crash mid-turn does not lose data.

        When *callbacks* is provided, events are dispatched to the callback
        object (on_progress, on_message, on_usage).  If both *on_progress*
        and *callbacks* are given, *callbacks.on_progress* takes precedence.

        Parameters
        ----------
        provider:
            LLM provider to use.  Falls back to ``self.provider`` if None.
        model:
            Model name to use.  Falls back to ``self.model`` if None.
        tools:
            ToolRegistry to use.  Falls back to ``self.tools`` if None.
            For concurrent gateway sessions, this is a clone from
            ``ToolRegistry.clone_for_session()``.
        """
        _provider = provider or self.provider
        _model = model or self.model
        _tools = tools or self.tools

        from datetime import datetime
        loop_started_at = datetime.now().isoformat()
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        accumulated_usage: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "llm_calls": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }

        # §59: Turn-level warning state (reset each turn, not persisted)
        _warned_this_turn: bool = False
        _WARNING_LINE = self.memory_window - 10
        _CONSOLIDATION_LINE = self.memory_window

        # Resolve progress callback: callbacks.on_progress takes precedence
        _progress_fn = on_progress
        if callbacks is not None:
            async def _cb_progress(text: str, *, tool_hint: bool = False) -> None:
                await callbacks.on_progress(text, tool_hint=tool_hint)
            _progress_fn = _cb_progress

        # §61: Consecutive timeout counter — abort after too many consecutive timeouts
        consecutive_timeouts = 0
        _MAX_CONSECUTIVE_TIMEOUTS = 2  # max 2 timeout hints, 3rd time → abort
        # §61: Dynamic timeout extension — double read timeout after each timeout-but-reachable
        current_read_timeout: float | None = None  # None = use provider default

        # How many messages existed before the loop — used to determine
        # which messages are "new" for realtime persistence.
        pre_loop_count = len(messages)

        while iteration < self.max_iterations:
            iteration += 1

            # §47: Notify on_iteration callback
            if self._on_iteration is not None:
                try:
                    _last_tool = tools_used[-1] if tools_used else None
                    self._on_iteration(iteration, self.max_iterations, _last_tool)
                except Exception:
                    pass  # Callback errors must not break the agent loop

            # §59: Step 1 — Consolidation just completed? Trim messages.
            if session is not None and session.key in self._pending_consolidation_done:
                info = self._pending_consolidation_done.pop(session.key)
                _warned_this_turn = self._trim_consolidated_messages(messages, info, session, _warned_this_turn)

            # §59: Step 2 — Need to trigger consolidation?
            _msg_count = len(messages) - 1  # exclude system prompt at messages[0]
            if (session is not None
                    and _msg_count >= _CONSOLIDATION_LINE
                    and session.key not in self._consolidating):
                self._consolidating.add(session.key)
                _archive_cut = self._find_tool_aligned_cut(messages, 1, self.memory_window // 2)
                logger.info("§59: Triggering mid-turn consolidation (msg_count={}, archive_cut={})", _msg_count, _archive_cut)
                _task = asyncio.create_task(
                    self._do_mid_turn_consolidation(session, messages, _archive_cut,
                                                    provider=_provider, model=_model, tools=_tools)
                )
                self._consolidation_tasks.add(_task)
                _task.add_done_callback(self._consolidation_tasks.discard)

            # §59: Step 3 — Need to warn?
            _msg_count = len(messages) - 1
            if (session is not None
                    and _msg_count >= _WARNING_LINE
                    and not _warned_this_turn
                    and session.key not in self._consolidating):
                _session_id = session.key.replace(":", "_")
                _warn_msg = {
                    "role": "user",
                    "content": _TRUNCATION_WARNING_TEMPLATE.format(
                        current=_msg_count,
                        max=_CONSOLIDATION_LINE,
                        workspace=str(self.workspace),
                        session_id=_session_id,
                    ),
                    "timestamp": datetime.now().isoformat(),
                }
                messages.append(_warn_msg)
                _warned_this_turn = True
                logger.info("§59: Injected truncation warning (msg_count={})", _msg_count)

            # ── Budget alert: warn LLM when iterations are running low ──
            # §43: Use "user" role so the alert is visible to the LLM at the
            # conversation tail (compatible with §32 cache breakpoint #3).
            # §48: Extracted to shared build_budget_alert() function.
            remaining = self.max_iterations - iteration
            threshold = _budget_alert_threshold(self.max_iterations)
            if remaining == threshold:
                _session_key = session.key if session is not None else ""
                messages.append({
                    "role": "user",
                    "content": build_budget_alert(remaining, self.max_iterations, _session_key),
                })

            response = await self._chat_with_retry(
                provider=_provider,
                messages=messages,
                tools=_tools.get_definitions(),
                model=_model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                reasoning_effort=self.reasoning_effort,
                read_timeout=current_read_timeout,
                progress_fn=_progress_fn,
                session_key=session.key if session is not None else "",
            )

            # Record token usage from this LLM call — immediately to SQLite
            # so that a crash mid-turn does not lose usage data.
            if response.usage:
                for key in ("prompt_tokens", "completion_tokens", "total_tokens",
                            "cache_creation_input_tokens", "cache_read_input_tokens"):
                    accumulated_usage[key] += response.usage.get(key, 0)
                accumulated_usage["llm_calls"] += 1

                # Realtime usage persistence: write each LLM call individually
                if self.usage_recorder is not None:
                    call_ts = datetime.now().isoformat()
                    session_key = session.key if session is not None else "unknown"
                    self.usage_recorder.record(
                        session_key=session_key,
                        model=_model,
                        prompt_tokens=response.usage.get("prompt_tokens", 0),
                        completion_tokens=response.usage.get("completion_tokens", 0),
                        total_tokens=response.usage.get("total_tokens", 0),
                        llm_calls=1,
                        started_at=call_ts,
                        finished_at=call_ts,
                        cache_creation_input_tokens=response.usage.get("cache_creation_input_tokens", 0),
                        cache_read_input_tokens=response.usage.get("cache_read_input_tokens", 0),
                        provider=getattr(_provider, "provider_name", ""),  # §41
                    )

            # Log full LLM call details (messages + response) to JSONL
            if self.detail_logger is not None:
                _detail_session_key = session.key if session is not None else "unknown"
                _tc_dicts = None
                if response.has_tool_calls:
                    _tc_dicts = [
                        {
                            "id": tc.id,
                            "name": tc.name,
                            "arguments": tc.arguments,
                        }
                        for tc in response.tool_calls
                    ]
                self.detail_logger.log_call(
                    session_key=_detail_session_key,
                    model=_model,
                    iteration=iteration,
                    messages=messages,
                    response_content=response.content,
                    response_tool_calls=_tc_dicts,
                    response_finish_reason=response.finish_reason,
                    response_usage=response.usage if response.usage else None,
                    provider=getattr(_provider, "provider_name", ""),
                )

            # §60: Truncation detection — finish_reason=length with tool_calls
            # means output was cut off mid-JSON. json_repair may have produced
            # incomplete arguments. Skip execution, tell LLM to split the task.
            if response.finish_reason == "length" and response.has_tool_calls:
                logger.warning(
                    "Output truncated (finish_reason=length) with {} tool call(s) — "
                    "skipping execution, injecting split hint",
                    len(response.tool_calls),
                )
                attempted = ", ".join(
                    f"{tc.name}(...)" for tc in response.tool_calls
                )
                truncation_hint = (
                    f"[System] Your previous response was truncated (finish_reason=length) "
                    f"while generating tool calls: {attempted}. "
                    f"The tool call arguments were incomplete and could not be executed. "
                    f"Please break your response into smaller steps — "
                    f"for example, write code in smaller chunks, use exec to write files via "
                    f"heredoc, or split a large file into multiple sequential writes. "
                    f"Do NOT retry the same approach that caused truncation."
                )
                hint_msg = {
                    "role": "user",
                    "content": truncation_hint,
                    "timestamp": datetime.now().isoformat(),
                }
                messages.append(hint_msg)

                # §62: Do NOT persist — hint is ephemeral (only for current turn LLM context).
                # Persisting would cause it to display as a user message on session reload.
                if callbacks is not None:
                    await callbacks.on_message(hint_msg)

                continue  # skip to next iteration without executing tools

            # §61: Timeout detection — LLM request timed out but API was reachable,
            # meaning output was too large for the timeout window.
            if response.finish_reason == "timeout":
                consecutive_timeouts += 1

                # §61: Extend read timeout for next call (double, capped at 600s)
                from nanobot.providers.litellm_provider import _LLM_TIMEOUT as _default_timeout
                _base_read = current_read_timeout if current_read_timeout is not None else getattr(_default_timeout, 'read', 300.0)
                current_read_timeout = min(_base_read * 2, 600.0)
                logger.info("§61: Extended read timeout to {:.0f}s for next call", current_read_timeout)

                if consecutive_timeouts > _MAX_CONSECUTIVE_TIMEOUTS:
                    logger.error(
                        "§61: Consecutive timeout limit reached ({}/{}). Aborting.",
                        consecutive_timeouts, _MAX_CONSECUTIVE_TIMEOUTS,
                    )
                    error_msg = (
                        f"⏱️ 连续 {consecutive_timeouts} 次 LLM 请求超时，"
                        "模型输出过大无法在超时窗口内完成。请尝试简化任务或拆分为更小的步骤。"
                    )
                    if callbacks is not None:
                        try:
                            await callbacks.on_message({"role": "assistant", "content": error_msg})
                        except Exception:
                            pass
                    break  # exit iteration loop

                logger.warning(
                    "§61: LLM timeout (API reachable) — injecting timeout hint ({}/{})",
                    consecutive_timeouts, _MAX_CONSECUTIVE_TIMEOUTS,
                )
                timeout_hint = (
                    "[System] ⚠️ Your previous LLM call timed out — your intended output was too large. "
                    f"This is timeout #{consecutive_timeouts}/{_MAX_CONSECUTIVE_TIMEOUTS} — "
                    f"if this happens {_MAX_CONSECUTIVE_TIMEOUTS - consecutive_timeouts} more time(s), the task will be aborted. "
                    "You MUST reduce your output size. Strategies:\n"
                    "1. Write code to files using exec with heredoc (exec tool), not inline\n"
                    "2. Split large operations into multiple smaller tool calls\n"
                    "3. Process data in batches instead of all at once\n"
                    "Do NOT repeat the same large output — it will timeout again."
                )
                hint_msg = {
                    "role": "user",
                    "content": timeout_hint,
                    "timestamp": datetime.now().isoformat(),
                }
                messages.append(hint_msg)
                # §62: Do NOT persist — hint is ephemeral (counter is in-memory state).
                if callbacks is not None:
                    try:
                        await callbacks.on_message(hint_msg)
                    except Exception:
                        pass
                continue  # next iteration

            # §61: Reset consecutive timeout counter and read timeout on successful response
            consecutive_timeouts = 0
            current_read_timeout = None

            if response.has_tool_calls:
                if _progress_fn:
                    clean = self._strip_think(response.content)
                    if clean:
                        await _progress_fn(clean)
                    await _progress_fn(self._tool_hint(response.tool_calls), tool_hint=True)

                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )

                # §48: Add provider info to assistant message for session JSONL
                _provider_name = getattr(_provider, "provider_name", "")
                if _provider_name and isinstance(_provider_name, str):
                    messages[-1]["provider"] = _provider_name

                # Realtime persist: assistant message with tool_calls
                if session is not None:
                    self.sessions.append_message(session, messages[-1])
                if callbacks is not None:
                    await callbacks.on_message(messages[-1])

                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info("Tool call: {}({})", tool_call.name, args_str[:200])
                    result = await _tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )

                    # Realtime persist: tool result
                    if session is not None:
                        self.sessions.append_message(session, messages[-1])
                    if callbacks is not None:
                        await callbacks.on_message(messages[-1])

                # ── §64: Silent cleanup — remove warning + summary write from memory ──
                # When the model obediently wrote a session summary in response to
                # the truncation warning (and did nothing else), we remove the
                # 3 ephemeral messages (warning, assistant write_file call, tool result)
                # from in-memory messages to keep context clean.
                if (_warned_this_turn
                        and len(response.tool_calls) == 1
                        and response.tool_calls[0].name == "write_file"):
                    _tc_args = response.tool_calls[0].arguments
                    _tc_path = _tc_args.get("path", "") if isinstance(_tc_args, dict) else ""
                    if "session_summary/" in _tc_path and _tc_path.endswith(".md"):
                        # Check that the tool result (last message) indicates success
                        _last_msg = messages[-1] if messages else {}
                        _last_content = _last_msg.get("content", "")
                        _is_error = (isinstance(_last_content, str)
                                     and _last_content.startswith("Error"))
                        if not _is_error:
                            # Find and remove: warning (user) + assistant + tool result
                            # Search backwards for the warning message
                            _warn_idx = None
                            for _i in range(len(messages) - 1, -1, -1):
                                _m = messages[_i]
                                if (_m.get("role") == "user"
                                        and isinstance(_m.get("content"), str)
                                        and "Context Approaching Limit" in _m["content"]):
                                    _warn_idx = _i
                                    break
                            if (_warn_idx is not None
                                    and _warn_idx + 2 < len(messages)
                                    and messages[_warn_idx + 1].get("role") == "assistant"
                                    and messages[_warn_idx + 2].get("role") == "tool"):
                                del messages[_warn_idx:_warn_idx + 3]
                                # NOTE: Keep _warned_this_turn=True so the warning
                                # is NOT re-injected on the same turn (prevents
                                # infinite warn→write→cleanup→warn loop).
                                logger.info("§64: Silent cleanup — removed warning + summary write + tool result from memory")

                # ── User injection checkpoint ──
                # After all tools in this round complete, drain ALL pending
                # messages before the next LLM call.  Previously only one
                # message was consumed per checkpoint, which caused messages
                # to be silently lost when multiple arrived during a single
                # tool execution (e.g. subagent results + user inject).
                if callbacks is not None:
                    while True:
                        injected = await callbacks.check_user_input()
                        if not injected:
                            break
                        if isinstance(injected, dict):
                            # Structured inject (e.g. from SessionMessenger)
                            # — role is determined by the sender, not by content.
                            inject_msg = {
                                "role": injected.get("role", "user"),
                                "content": injected["content"],
                                "timestamp": datetime.now().isoformat(),
                            }
                        else:
                            # Plain string inject (user message during execution)
                            if not injected.startswith("["):
                                injected = f"[Message from user during execution]\n{injected}"
                            inject_msg = {
                                "role": "user",
                                "content": injected,
                                "timestamp": datetime.now().isoformat(),
                            }
                        logger.info("Injected message (role={}): {}",
                                    inject_msg["role"], inject_msg["content"][:120])
                        messages.append(inject_msg)

                        # Realtime persist: injected user message
                        if session is not None:
                            self.sessions.append_message(session, inject_msg)
                        await callbacks.on_message(inject_msg)
                        if _progress_fn:
                            await _progress_fn(f"📝 User: {inject_msg['content'][:80]}")
            else:
                clean = self._strip_think(response.content)
                # Error responses: persist to JSONL for display but prefix
                # with "Error calling LLM:" so get_history() Phase 2 strips
                # them from future LLM context (prevents poison loops #1303).
                if response.finish_reason == "error":
                    logger.error("LLM returned error: {}", (clean or "")[:200])
                    error_text = clean or "Sorry, I encountered an error calling the AI model."
                    final_content = error_text
                    prefixed = f"Error calling LLM: {error_text}"
                    error_msg = {
                        "role": "assistant",
                        "content": prefixed,
                        "timestamp": datetime.now().isoformat(),
                    }
                    if session is not None:
                        self.sessions.append_message(session, error_msg)
                    if callbacks is not None:
                        await callbacks.on_message(error_msg)
                    if _progress_fn:
                        await _progress_fn(f"❌ {error_text}")
                    break
                final_content = clean
                # Append the final assistant message so it gets persisted
                messages = self.context.add_assistant_message(
                    messages, clean,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )

                # §48: Add provider info to final assistant message
                _provider_name = getattr(_provider, "provider_name", "")
                if _provider_name and isinstance(_provider_name, str):
                    messages[-1]["provider"] = _provider_name

                # Realtime persist: final assistant message
                if session is not None:
                    self.sessions.append_message(session, messages[-1])
                if callbacks is not None:
                    await callbacks.on_message(messages[-1])

                # ── Final-response injection checkpoint ──
                # Before exiting the loop, drain any pending inject messages
                # that arrived while the LLM was generating its final response.
                # If messages are found, persist them and continue the loop so
                # the LLM can process them instead of silently dropping them.
                _has_pending = False
                if callbacks is not None:
                    while True:
                        injected = await callbacks.check_user_input()
                        if not injected:
                            break
                        _has_pending = True
                        if isinstance(injected, dict):
                            inject_msg = {
                                "role": injected.get("role", "user"),
                                "content": injected["content"],
                                "timestamp": datetime.now().isoformat(),
                            }
                        else:
                            if not injected.startswith("["):
                                injected = f"[Message from user during execution]\n{injected}"
                            inject_msg = {
                                "role": "user",
                                "content": injected,
                                "timestamp": datetime.now().isoformat(),
                            }
                        logger.info("Late-injected message (role={}): {}",
                                    inject_msg["role"], inject_msg["content"][:120])
                        messages.append(inject_msg)
                        if session is not None:
                            self.sessions.append_message(session, inject_msg)
                        await callbacks.on_message(inject_msg)
                        if _progress_fn:
                            await _progress_fn(f"📝 User: {inject_msg['content'][:80]}")
                if _has_pending:
                    # Messages were injected after the LLM's "final" response.
                    # Clear final_content so the loop continues and the LLM
                    # can respond to the newly injected messages.
                    final_content = None
                    continue
                break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            final_content = (
                f"I reached the maximum number of tool call iterations ({self.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )
            # Append as assistant message so it gets persisted
            messages = self.context.add_assistant_message(
                messages, final_content, None,
            )

            # Realtime persist: max-iterations message
            if session is not None:
                self.sessions.append_message(session, messages[-1])
            if callbacks is not None:
                await callbacks.on_message(messages[-1])

        # Print usage summary to stderr as a JSON line for external consumers
        # (e.g. worker.py) and notify callbacks.
        # NOTE: Individual LLM call usage is already written to SQLite in
        # realtime above.  This block only produces the *aggregate* summary
        # for stderr output and the on_usage callback.
        if accumulated_usage.get("llm_calls", 0) > 0:
            import sys
            finished_at = datetime.now().isoformat()
            session_key = session.key if session is not None else "unknown"
            usage_record = {
                "__usage__": True,
                "model": _model,
                "session_key": session_key,
                "prompt_tokens": accumulated_usage.get("prompt_tokens", 0),
                "completion_tokens": accumulated_usage.get("completion_tokens", 0),
                "total_tokens": accumulated_usage.get("total_tokens", 0),
                "llm_calls": accumulated_usage.get("llm_calls", 0),
                "cache_creation_input_tokens": accumulated_usage.get("cache_creation_input_tokens", 0),
                "cache_read_input_tokens": accumulated_usage.get("cache_read_input_tokens", 0),
                "started_at": loop_started_at,
                "finished_at": finished_at,
            }

            # stderr JSON output (backward compat for worker.py parsing)
            print(json.dumps(usage_record, ensure_ascii=False), file=sys.stderr)
            logger.info(
                "Usage: {} calls, {} prompt + {} completion = {} total tokens (model: {})",
                accumulated_usage["llm_calls"],
                accumulated_usage["prompt_tokens"],
                accumulated_usage["completion_tokens"],
                accumulated_usage["total_tokens"],
                _model,
            )

            # Notify callbacks of usage data
            if callbacks is not None:
                await callbacks.on_usage(usage_record)

        return final_content, tools_used, messages

    async def run(self) -> None:
        """Run the agent loop as a concurrent dispatcher.

        Different sessions are processed **in parallel** as independent
        ``asyncio.Task`` instances.  When a new message arrives for an
        already-active session, it is **injected** into the running task
        via ``GatewayCallbacks.inject()`` rather than queued.

        ``/stop`` cancels the task for the matching session.
        ``/provider`` switches the provider for the matching session.
        """
        from dataclasses import dataclass, field
        from nanobot.agent.callbacks import GatewayCallbacks
        from nanobot.providers.pool import ProviderPool

        @dataclass
        class SessionWorker:
            task: asyncio.Task
            callbacks: GatewayCallbacks
            session_key: str


        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started (concurrent dispatcher)")

        active_sessions: dict[str, SessionWorker] = {}

        # ── Phase 30: Create GatewaySessionMessenger for subagent announce ──
        # NOTE: GatewaySessionMessenger is defined inside run() because it
        # needs a reference to the local `active_sessions` dict. If this
        # class needs modification in the future, consider refactoring it
        # to a module-level class first — the current test_session_messenger.py
        # duplicates its implementation, so changes here won't automatically
        # break tests. See ARCHITECTURE.md §12.3 for details.
        class GatewaySessionMessenger:
            """SessionMessenger for gateway mode — inject into running sessions or trigger new ones."""

            def __init__(self, active, bus, sessions_mgr):
                self._active = active  # mutable dict reference
                self._bus = bus
                self._sessions = sessions_mgr

            async def send_to_session(self, target_session_key, content, source_session_key=None):
                if source_session_key:
                    prefixed = f"[Message from session {source_session_key}]\n{content}"
                else:
                    prefixed = content

                # Check if target is running
                if target_session_key in self._active:
                    worker = self._active[target_session_key]
                    if not worker.task.done():
                        # Inject as system role dict — the inject checkpoint
                        # will use the role from the dict rather than defaulting
                        # to "user", preventing the agent from treating subagent
                        # results as user instructions.
                        await worker.callbacks.inject({
                            "role": "user",
                            "content": prefixed,
                        })
                        logger.debug("GatewaySessionMessenger: injected into running session {}",
                                     target_session_key)
                        return True
                    else:
                        self._active.pop(target_session_key, None)

                # Idle → publish InboundMessage to trigger new task
                # Resolve real channel/chat_id from target_session_key so
                # outbound messages route to the correct channel (e.g. feishu).
                real_channel = "session_messenger"  # fallback
                real_chat_id = target_session_key    # fallback
                routing = self._sessions._load_routing()
                for natural_key, routed_key in routing.items():
                    if routed_key == target_session_key:
                        _parts = natural_key.split(":", 1)
                        if len(_parts) == 2:
                            real_channel, real_chat_id = _parts
                        break
                else:
                    # target_session_key might be in natural key format
                    _parts = target_session_key.split(":", 1)
                    if len(_parts) == 2:
                        real_channel, real_chat_id = _parts

                msg = InboundMessage(
                    channel=real_channel,
                    sender_id=source_session_key or "unknown",
                    chat_id=real_chat_id,
                    content=prefixed,
                    session_key_override=target_session_key,
                )
                await self._bus.publish_inbound(msg)
                logger.debug("GatewaySessionMessenger: published inbound for idle session {}",
                             target_session_key)
                return True

        messenger = GatewaySessionMessenger(active_sessions, self.bus, self.sessions)
        self.subagents.session_messenger = messenger

        def _on_task_done(session_key: str, task: asyncio.Task) -> None:
            """Callback when a session task finishes."""
            active_sessions.pop(session_key, None)
            if not task.cancelled():
                exc = task.exception()
                if exc is not None:
                    logger.error("Session {} task failed: {}", session_key, exc)

        while self._running:
            try:
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue

            # Resolve session key
            session_key = self.sessions.resolve_session_key(msg.session_key)
            cmd = msg.content.strip().lower()

            # ── /stop: cancel the active task for this session ──
            if cmd == "/stop":
                worker = active_sessions.get(session_key)
                if worker and not worker.task.done():
                    logger.info("/stop received for session {}", session_key)
                    worker.task.cancel()
                    try:
                        await asyncio.wait_for(asyncio.shield(worker.task), timeout=3.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass
                else:
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="No active task to stop.",
                    ))
                continue

            # ── /provider: per-session switch ──
            if cmd.startswith("/provider"):
                response = self._handle_provider_command(msg, session_key=session_key)
                await self.bus.publish_outbound(response)
                continue

            # ── /session: show session status ──
            if cmd == "/session" or cmd.startswith("/session "):
                response = self._handle_session_command(
                    msg, session_key=session_key, active_sessions=active_sessions,
                )
                await self.bus.publish_outbound(response)
                continue

            # ── Active session → inject ──
            if session_key in active_sessions:
                worker = active_sessions[session_key]
                if not worker.task.done():
                    logger.info("Injecting message into active session {}", session_key)
                    await worker.callbacks.inject(f"[Message from user during execution]\n{msg.content}")
                    continue
                else:
                    # Task already done, remove stale entry
                    active_sessions.pop(session_key, None)

            # ── New/idle session → start task ──
            # Resolve per-session provider/model
            pool = self.provider
            if isinstance(pool, ProviderPool):
                _provider_inst, _model = pool.get_for_session(session_key)
            else:
                _provider_inst = self.provider
                _model = self.model

            # Clone tools for this session
            tools_clone = self.tools.clone_for_session()

            # Create per-session callbacks
            gw_callbacks = GatewayCallbacks(
                bus=self.bus, channel=msg.channel, chat_id=msg.chat_id,
            )

            task = asyncio.create_task(
                self._process_message_safe(
                    msg,
                    provider=_provider_inst,
                    model=_model,
                    tools=tools_clone,
                    callbacks=gw_callbacks,
                )
            )
            active_sessions[session_key] = SessionWorker(
                task=task, callbacks=gw_callbacks, session_key=session_key,
            )
            task.add_done_callback(lambda t, k=session_key: _on_task_done(k, t))

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """Handle /stop command — legacy path for process_direct().

        In the concurrent dispatcher (run()), /stop is handled inline.
        This method is kept for backward compatibility with tests that
        call it directly.
        """
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content="No active task to stop.",
        ))

    def _handle_provider_command(self, msg: InboundMessage,
                                 session_key: str | None = None) -> OutboundMessage:
        """Handle /provider slash command: view or switch provider.

        When *session_key* is provided (gateway concurrent mode), the switch
        is per-session via ``ProviderPool.switch_for_session()``.
        When *session_key* is None (CLI/SDK mode), the switch is global.

        Usage:
            /provider              — show current provider and available list
            /provider <name>       — switch to provider (use its default model)
            /provider <name> <model> — switch to provider with specific model
        """
        from nanobot.providers.pool import ProviderPool

        pool = self.provider
        if not isinstance(pool, ProviderPool):
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content="⚠️ Provider switching not available (single provider mode).",
            )

        parts = msg.content.strip().split()
        if len(parts) == 1:
            # /provider — show status
            if session_key:
                current_name = pool.get_session_provider_name(session_key)
                current_model = pool.get_session_model(session_key)
            else:
                current_name = pool.active_provider
                current_model = pool.active_model
            lines = [f"🔌 当前: **{current_name}** / `{current_model}`"]
            lines.append("\n可用 providers:")
            for item in pool.available:
                marker = " ← 当前" if item["name"] == current_name else ""
                lines.append(f"  • **{item['name']}** (`{item['model']}`){marker}")
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content="\n".join(lines),
            )
        else:
            # /provider <name> [model] — switch
            provider_name = parts[1]
            model_arg = parts[2] if len(parts) > 2 else None
            try:
                if session_key:
                    pool.switch_for_session(session_key, provider_name, model_arg)
                    new_model = pool.get_session_model(session_key)
                    new_name = pool.get_session_provider_name(session_key)
                else:
                    pool.switch(provider_name, model_arg)
                    self.model = pool.active_model
                    new_name = pool.active_provider
                    new_model = pool.active_model
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content=f"✅ 已切换到 **{new_name}** / `{new_model}`",
                )
            except ValueError as e:
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content=f"❌ {e}",
                )

    def _handle_session_command(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        active_sessions: dict | None = None,
    ) -> OutboundMessage:
        """Route ``/session [subcmd]`` to the appropriate handler."""
        parts = msg.content.strip().split(maxsplit=2)
        subcmd = parts[1].lower() if len(parts) > 1 else None
        arg = parts[2] if len(parts) > 2 else None

        if subcmd is None:
            return self._session_info(msg, session_key, active_sessions)
        elif subcmd == "list":
            return self._session_list(msg, session_key, arg)
        elif subcmd == "switch":
            return self._session_switch(msg, session_key, active_sessions, arg)
        elif subcmd == "name":
            # Preserve original casing for the name value
            raw = msg.content.strip()
            name_idx = raw.lower().find(" name ")
            name_arg = raw[name_idx + 6:].strip() if name_idx >= 0 else None
            return self._session_name(msg, session_key, name_arg)
        elif subcmd == "summary":
            return self._session_summary(msg, session_key, arg)
        elif subcmd == "done":
            return self._session_done(msg, session_key, active_sessions, arg)
        elif subcmd == "undone":
            return self._session_undone(msg, session_key, arg)
        elif subcmd == "help":
            return self._session_help(msg)
        else:
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content=(
                    f"❌ 未知子命令: {subcmd}\n"
                    "用法: /session [list|switch|name|summary|done|undone|help]"
                ),
            )

    # ── /session (no subcommand) — original info display ─────────────

    def _session_info(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        active_sessions: dict | None = None,
    ) -> OutboundMessage:
        """Show current session info and status (original ``/session`` behaviour)."""
        from nanobot.providers.pool import ProviderPool

        key = session_key or self.sessions.resolve_session_key(msg.session_key)
        session = self.sessions.get_or_create(key)

        # ── Status ──
        is_active = False
        if active_sessions is not None and key in active_sessions:
            worker = active_sessions[key]
            if not worker.task.done():
                is_active = True

        if is_active:
            status_text = "🔄 执行中（正在处理任务）"
        else:
            status_text = "💤 空闲（等待输入）"

        # ── Provider/Model ──
        pool = self.provider
        if isinstance(pool, ProviderPool):
            provider_name = pool.get_session_provider_name(key)
            model_name = pool.get_session_model(key)
        else:
            provider_name = type(self.provider).__name__
            model_name = self.model

        # ── Message stats ──
        total_msgs = len(session.messages)
        unconsolidated = total_msgs - session.last_consolidated

        # ── Token usage ──
        if self.usage_recorder is not None:
            try:
                usage = self.usage_recorder.get_session_usage(key)
                token_line = (
                    f"{usage['prompt_tokens']:,} prompt + "
                    f"{usage['completion_tokens']:,} completion = "
                    f"**{usage['total_tokens']:,}** total "
                    f"({usage['llm_calls']} 次调用)"
                )
                # Append cache info if available
                cache_read = usage.get('cache_read_input_tokens', 0)
                cache_creation = usage.get('cache_creation_input_tokens', 0)
                if cache_read or cache_creation:
                    cache_parts = []
                    if cache_read:
                        cache_parts.append(f"缓存命中: {_format_tokens(cache_read)}")
                    if cache_creation:
                        cache_parts.append(f"缓存写入: {_format_tokens(cache_creation)}")
                    uncached = usage['prompt_tokens'] - cache_read - cache_creation
                    if uncached > 0:
                        cache_parts.append(f"未缓存: {_format_tokens(uncached)}")
                    token_line += "\n**缓存**: " + " · ".join(cache_parts)
            except Exception:
                token_line = "查询失败"
        else:
            token_line = "N/A（未配置 UsageRecorder）"

        # ── Build output ──
        sid = self.sessions.get_session_id(key)
        lines = [
            f"📋 **Session 信息**",
            f"",
            f"**Session ID**: `{sid}`",
            f"**状态**: {status_text}",
            f"**Provider**: {provider_name} / `{model_name}`",
            f"**Token 用量**: {token_line}",
            f"**消息数**: {total_msgs} 条（未归档: {unconsolidated}）",
            f"**创建时间**: {session.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"**最后更新**: {session.updated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        ]

        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content="\n".join(lines),
        )

    # ── /session help ───────────────────────────────────────────────

    def _session_help(self, msg: InboundMessage) -> OutboundMessage:
        """Show help for /session subcommands."""
        text = (
            "📖 /session 子命令:\n"
            "  /session           — 显示当前 session 状态\n"
            "  /session list [N]  — 列出最近 N 个 session（默认 10）\n"
            "  /session list M-N  — 列出第 M 到第 N 个 session\n"
            "  /session list --all — 列出所有 session（含已归档）\n"
            "  /session switch <#N|%id> — 切换到指定 session\n"
            "  /session name <名称>    — 给当前 session 命名\n"
            "  /session summary [#N|%id] [条数] — 显示 session 摘要\n"
            "  /session done [#N|%id]   — 归档 session\n"
            "  /session undone [#N|%id] — 取消归档\n"
            "  /session help           — 显示此帮助\n"
            "\n"
            "参数语法: #N = 序号引用（来自 list），%id = session_id 直接引用"
        )
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=text)

    # ── /session list [N] ────────────────────────────────────────────

    def _session_list(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        arg: str | None = None,
    ) -> OutboundMessage:
        """List recent sessions for the current channel.

        Supports:
        - ``/session list``          — last 10 sessions (excluding done)
        - ``/session list N``        — last N sessions
        - ``/session list M-N``      — sessions #M to #N (1-based, time-descending)
        - ``/session list --all``    — include done sessions (marked ✓)
        - ``/session list --all N``  — combine --all with limit
        - ``/session list --all M-N``— combine --all with range
        """
        limit: int | None = 10
        range_start: int | None = None
        range_end: int | None = None
        show_all = False

        # Parse arg
        if arg is not None:
            tokens = arg.strip().split()
            for token in tokens:
                if token == "--all":
                    show_all = True
                elif "-" in token and not token.startswith("-"):
                    # Range syntax M-N
                    parts = token.split("-", 1)
                    try:
                        range_start = int(parts[0])
                        range_end = int(parts[1])
                        limit = None  # range overrides limit
                    except ValueError:
                        return OutboundMessage(
                            channel=msg.channel, chat_id=msg.chat_id,
                            content=f"❌ 无效范围: {token}（格式: M-N，如 5-10）",
                        )
                    if range_start < 1 or range_end < range_start:
                        return OutboundMessage(
                            channel=msg.channel, chat_id=msg.chat_id,
                            content=f"❌ 无效范围: {token}（M 须 ≥ 1 且 N ≥ M）",
                        )
                else:
                    try:
                        limit = int(token)
                    except ValueError:
                        return OutboundMessage(
                            channel=msg.channel, chat_id=msg.chat_id,
                            content=f"❌ 无效参数: {token}（应为数字或 M-N 范围）",
                        )

        current_key = session_key or self.sessions.resolve_session_key(msg.session_key)
        current_sid = self.sessions.get_session_id(current_key)
        channel_prefix = msg.channel  # e.g. "feishu.lab", "webchat", "cli"

        all_sessions = self.sessions.list_sessions()
        # Filter by channel prefix (match session_id since key format varies)
        filtered = [s for s in all_sessions
                    if s["session_id"].startswith(channel_prefix.replace(":", "_"))]

        # Read names and tags
        names = self.sessions._read_json_file(self.sessions._session_names_path())
        tags_data = self.sessions._read_json_file(self.sessions._session_tags_path())

        # Annotate with done status
        for s in filtered:
            sid = s["session_id"]
            s["_done"] = "done" in tags_data.get(sid, [])

        total_all = len(filtered)
        total_done = sum(1 for s in filtered if s["_done"])
        total_active = total_all - total_done

        # Cache ALL session_ids (before range/limit slicing) for sequence-number references.
        # #N always refers to the N-th item in the full sorted list, regardless of displayed range.
        if not show_all:
            all_for_cache = [s for s in filtered if not s["_done"]]
        else:
            all_for_cache = list(filtered)
        self._last_session_list[msg.chat_id] = [s["session_id"] for s in all_for_cache]

        # Apply range or limit for display
        if range_start is not None and range_end is not None:
            # 1-based indices into the visible list
            visible = all_for_cache[range_start - 1:range_end]
        elif limit is not None:
            visible = all_for_cache[:limit]
        else:
            visible = all_for_cache

        if not visible:
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content="📋 没有找到匹配的 session。",
            )

        # Determine channel short name for title
        # e.g. "feishu.lab" -> "lab", "feishu.ST" -> "ST", "webchat" -> "webchat"
        ch_parts = channel_prefix.split(".")
        channel_short = ch_parts[-1] if len(ch_parts) > 1 else channel_prefix

        # Format output
        if range_start is not None:
            title = f"📋 ({channel_short}) Sessions（#{range_start}-#{range_end}）:"
        else:
            title = f"📋 ({channel_short}) Sessions（最近 {len(visible)} 个）:"
        lines = [title]

        enum_start = range_start if range_start is not None else 1
        for idx, s in enumerate(visible, enum_start):
            sid = s["session_id"]
            name = names.get(sid, "")
            is_current = (sid == current_sid)
            marker = " ●" if is_current else "  "
            done_marker = " ✓" if s.get("_done") else ""
            msg_count = self.sessions.get_session_message_count(sid)

            # Date: simplified format "26-03-07 16:34"
            updated = s.get("updated_at", "")
            if len(updated) >= 16:
                # "2026-03-07T16:34:..." -> "26-03-07 16:34"
                date_str = updated[2:10] + " " + updated[11:16]
            else:
                date_str = updated

            # Display: name if available, otherwise short ID
            if name:
                display = name
            else:
                # Short ID: remove channel prefix from session_id
                # channel_prefix "feishu.lab" -> file prefix "feishu.lab"
                # session_id "feishu.lab.1772855249" -> short "1772855249"
                # session_id "feishu.lab_ou_xxx_12345" -> short "ou_xxx_12345"
                safe_prefix = channel_prefix.replace(":", "_")
                if sid.startswith(safe_prefix + "."):
                    display = sid[len(safe_prefix) + 1:]
                elif sid.startswith(safe_prefix + "_"):
                    display = sid[len(safe_prefix) + 1:]
                else:
                    display = sid

            lines.append(
                f" #{idx}{marker} {display}{done_marker} — {msg_count}条消息 {date_str}"
            )

        lines.append("")
        lines.append(f"共 {total_active} 个 session，已归档 {total_done} 个")
        lines.append("● = 当前 session | 用 `/session switch #N` 切换")
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content="\n".join(lines),
        )

    # ── /session switch <#N|%id> ───────────────────────────────────────

    def _session_switch(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        active_sessions: dict | None = None,
        arg: str | None = None,
    ) -> OutboundMessage:
        """Switch to a different session."""
        if not arg:
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content="❌ 用法: /session switch <#序号|%session_id>",
            )

        current_key = session_key or self.sessions.resolve_session_key(msg.session_key)
        current_sid = self.sessions.get_session_id(current_key)

        target_sid = self._resolve_session_arg(msg, current_sid, arg)
        if isinstance(target_sid, OutboundMessage):
            return target_sid  # error message

        if target_sid == current_sid:
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content=f"ℹ️ 已经在该 session 中: `{target_sid}`",
            )

        # Update routing with session_id
        self.sessions.switch_session(msg.channel, msg.chat_id, target_sid)

        name = self.sessions.get_session_name(target_sid)
        display = f"{name} (`{target_sid}`)" if name else f"`{target_sid}`"
        msg_count = self.sessions.get_session_message_count(target_sid)

        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content=f"✅ 已切换到 session: {display}\n📊 {msg_count} 条消息",
        )

    # ── /session name <名称> ─────────────────────────────────────────

    def _session_name(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        arg: str | None = None,
    ) -> OutboundMessage:
        """Set a display name for the current session."""
        if not arg:
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content="❌ 用法: /session name <名称>",
            )

        key = session_key or self.sessions.resolve_session_key(msg.session_key)
        sid = self.sessions.get_session_id(key)
        self.sessions.set_session_name(sid, arg)

        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content=f"✅ Session 已命名为: **{arg}**\n(`{key}`)",
        )

    # ── /session done [#N|key] ───────────────────────────────────────

    def _session_done(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        active_sessions: dict | None = None,
        arg: str | None = None,
    ) -> OutboundMessage:
        """Mark a session as done (archived)."""
        current_key = session_key or self.sessions.resolve_session_key(msg.session_key)
        current_sid = self.sessions.get_session_id(current_key)

        target_sid = self._resolve_session_arg(msg, current_sid, arg)
        if isinstance(target_sid, OutboundMessage):
            return target_sid  # error message

        tags = self.sessions.get_session_tags(target_sid)
        if "done" not in tags:
            tags.append("done")
            self.sessions.set_session_tags(target_sid, tags)

        name = self.sessions.get_session_name(target_sid)
        display = name or target_sid

        # If archiving the current session, create a new one
        if target_sid == current_sid:
            new_key = self.sessions.create_new_session(
                channel=msg.channel, chat_id=msg.chat_id, old_key=current_key,
            )
            new_sid = self.sessions.get_session_id(new_key)
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content=(
                    f"✅ Session **{display}** 已归档\n"
                    f"🆕 已创建新 session: `{new_sid}`"
                ),
            )

        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content=f"✅ Session **{display}** 已归档",
        )

    # ── /session undone [#N|key] ─────────────────────────────────────

    def _session_undone(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        arg: str | None = None,
    ) -> OutboundMessage:
        """Remove the done tag from a session."""
        current_key = session_key or self.sessions.resolve_session_key(msg.session_key)
        current_sid = self.sessions.get_session_id(current_key)

        target_sid = self._resolve_session_arg(msg, current_sid, arg)
        if isinstance(target_sid, OutboundMessage):
            return target_sid  # error message

        tags = self.sessions.get_session_tags(target_sid)
        if "done" in tags:
            tags.remove("done")
            self.sessions.set_session_tags(target_sid, tags)

        name = self.sessions.get_session_name(target_sid)
        display = name or target_sid

        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content=f"✅ Session **{display}** 已取消归档",
        )

    # ── /session summary [#N|%id] [条数] ──────────────────────────────

    def _session_summary(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        arg: str | None = None,
    ) -> OutboundMessage:
        """Show a rule-based summary of a session (no LLM).

        Supports:
        - ``/session summary``         — current session, 5 user messages
        - ``/session summary N``       — current session, N user messages
        - ``/session summary #2``      — session #2, 5 user messages
        - ``/session summary #2 N``    — session #2, N user messages
        - ``/session summary %id``     — session by id, 5 user messages
        - ``/session summary %id N``   — session by id, N user messages
        """
        current_key = session_key or self.sessions.resolve_session_key(msg.session_key)
        current_sid = self.sessions.get_session_id(current_key)

        # Parse arg: may contain session ref + count
        target_ref: str | None = None
        user_msg_count = 5  # default

        if arg is not None:
            tokens = arg.strip().split()
            for token in tokens:
                if token.startswith("#") or token.startswith("%"):
                    target_ref = token
                else:
                    try:
                        user_msg_count = int(token)
                    except ValueError:
                        return OutboundMessage(
                            channel=msg.channel, chat_id=msg.chat_id,
                            content=f"❌ 无效参数: {token}",
                        )

        target_sid = self._resolve_session_arg(msg, current_sid, target_ref)
        if isinstance(target_sid, OutboundMessage):
            return target_sid  # error message

        # Load session via its key to get messages
        # session_id -> session_key: we need to find the key for get_or_create
        # The session_id IS the filename stem, and _get_session_path(session_id)
        # works because session_id has no colon, so replace(":", "_") is no-op.
        session = self.sessions.get_or_create(target_sid)
        messages = session.messages

        if not messages:
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content="📭 该 session 暂无消息",
            )

        # Count by role
        role_counts: dict[str, int] = {}
        user_messages: list[str] = []

        for m in messages:
            role = m.get("role", "unknown")
            role_counts[role] = role_counts.get(role, 0) + 1
            if role == "user":
                content = m.get("content", "")
                if isinstance(content, str) and content.strip():
                    user_messages.append(content[:120])

        total = len(messages)
        u_count = role_counts.get("user", 0)
        a_count = role_counts.get("assistant", 0)
        t_count = role_counts.get("tool", 0)

        name = self.sessions.get_session_name(target_sid)

        lines = [f"📊 **Session 摘要**"]
        if name:
            lines.append(f"**名称**: {name}")
        lines.append(f"**Session ID**: `{target_sid}`")
        lines.append(f"**消息数**: {total} 条（user: {u_count}, assistant: {a_count}, tool: {t_count}）")
        lines.append(f"**创建时间**: {session.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"**最后更新**: {session.updated_at.strftime('%Y-%m-%d %H:%M:%S')}")

        # Select user messages: first + evenly distributed
        if user_messages:
            selected = self._select_evenly(user_messages, user_msg_count)
            lines.append(f"\n**用户消息**（{len(selected)}/{len(user_messages)} 条）:")
            for i, text in enumerate(selected, 1):
                lines.append(f"  {i}. {text}")

        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content="\n".join(lines),
        )

    @staticmethod
    def _select_evenly(items: list[str], count: int) -> list[str]:
        """Select *count* items from *items*: first item + evenly spaced rest."""
        if len(items) <= count:
            return list(items)
        if count <= 0:
            return []
        if count == 1:
            return [items[0]]
        # First item always included, then pick (count-1) more evenly from the rest
        indices = [0]
        step = (len(items) - 1) / (count - 1)
        for i in range(1, count):
            indices.append(round(i * step))
        # Deduplicate while preserving order
        seen: set[int] = set()
        unique_indices: list[int] = []
        for idx in indices:
            if idx not in seen:
                seen.add(idx)
                unique_indices.append(idx)
        return [items[i] for i in unique_indices]

    # ── Helper: resolve #N or %session_id argument ────────────────────

    def _resolve_session_arg(
        self,
        msg: InboundMessage,
        current_session_id: str,
        arg: str | None,
    ) -> "str | OutboundMessage":
        """Resolve an optional ``#N`` or ``%session_id`` argument.

        Returns the resolved session_id (JSONL filename stem), or an
        ``OutboundMessage`` on error.  If *arg* is ``None``, returns
        *current_session_id*.

        Supported formats:
        - ``#N``  — sequence number from last ``/session list``
        - ``%session_id`` — direct session_id reference
        - Plain text is NOT accepted (pure numbers are treated as
          numeric params by callers, not session refs).
        """
        if arg is None:
            return current_session_id

        arg = arg.strip()
        if arg.startswith("#"):
            try:
                num = int(arg[1:])
            except ValueError:
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content=f"❌ 无效序号: {arg}",
                )
            cached = self._last_session_list.get(msg.chat_id)
            if not cached:
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="❌ 请先执行 `/session list` 获取列表",
                )
            if num < 1 or num > len(cached):
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content=f"❌ 序号 #{num} 超出范围（当前列表共 {len(cached)} 个）",
                )
            return cached[num - 1]

        if arg.startswith("%"):
            session_id = arg[1:]
            path = self.sessions.sessions_dir / f"{session_id}.jsonl"
            if not path.exists():
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content=f"❌ Session 不存在: `{session_id}`",
                )
            return session_id

        # Direct session_id (backward compat for switch command)
        path = self.sessions.sessions_dir / f"{arg}.jsonl"
        if not path.exists():
            # Also try via _get_session_path for legacy key format
            legacy_path = self.sessions._get_session_path(arg)
            if legacy_path.exists():
                return legacy_path.stem
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content=f"❌ Session 不存在: `{arg}`",
            )
        return arg

    async def _process_message_safe(
        self,
        msg: InboundMessage,
        *,
        provider: LLMProvider | None = None,
        model: str | None = None,
        tools: ToolRegistry | None = None,
        callbacks: DefaultCallbacks | None = None,
    ) -> None:
        """Wrapper around _process_message that handles CancelledError gracefully.

        For the concurrent dispatcher, provider/model/tools/callbacks are
        passed through.  For the legacy serial path, they default to None
        (falling back to self.*).
        """
        try:
            response = await self._process_message(
                msg, provider=provider, model=model, tools=tools, callbacks=callbacks,
            )
            if response is not None:
                await self.bus.publish_outbound(response)
            elif msg.channel == "cli":
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id, content="",
                    metadata=msg.metadata or {},
                ))
        except asyncio.CancelledError:
            logger.info("Task cancelled for {}:{}", msg.channel, msg.chat_id)
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content="⏹ Task stopped.",
            ))
        except Exception as e:
            logger.error("Error processing message: {}", e)
            # Persist core error to session JSONL so frontend can display it
            # after page refresh.  Uses the unified ``"Error calling LLM:"``
            # prefix format so get_history() filters it and frontend renders
            # it with the existing error bubble style.
            self._persist_error(msg.session_key, e)
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"Sorry, I encountered an error: {str(e)}"
            ))

    def _persist_error(self, session_key: str, error: Exception) -> None:
        """Persist an error message to the session JSONL.

        Uses the same ``role: "assistant"`` + ``"Error calling LLM:"``
        prefix format as the in-loop LLM error path, so that:
        - ``get_history()`` Phase 2 filters it out (no poison loops)
        - The frontend ``isErrorMessage()`` detects and renders it with
          the existing error bubble style (no extra logic needed)
        """
        from datetime import datetime

        try:
            key = self.sessions.resolve_session_key(session_key)
            session = self.sessions.get_or_create(key)
            error_entry = {
                "role": "assistant",
                "content": f"Error calling LLM: {error}",
                "timestamp": datetime.now().isoformat(),
            }
            self.sessions.append_message(session, error_entry)
            self.sessions.update_metadata(session)
        except Exception:
            logger.exception("Failed to persist error to session JSONL")

    async def close_mcp(self) -> None:
        """Close MCP connections."""
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        callbacks: DefaultCallbacks | None = None,
        *,
        provider: LLMProvider | None = None,
        model: str | None = None,
        tools: ToolRegistry | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response.

        Parameters
        ----------
        provider:
            LLM provider to use.  Falls back to ``self.provider`` if None.
        model:
            Model name to use.  Falls back to ``self.model`` if None.
        tools:
            ToolRegistry to use.  Falls back to ``self.tools`` if None.
            For concurrent gateway sessions, pass a cloned registry.
        """
        _provider = provider or self.provider
        _model = model or self.model
        _tools = tools or self.tools

        # System messages: parse origin from chat_id ("channel:chat_id")
        if msg.channel == "system":
            channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id
                                else ("cli", msg.chat_id))
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            session = self.sessions.get_or_create(key)
            self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"),
                                   session_key=key, tools=_tools)
            # §59: hard cap is a safety net, not the primary truncation mechanism.
            # Set it well above CONSOLIDATION_LINE to allow async consolidation time.
            history = session.get_history(max_messages=self.memory_window * 2)
            messages = self.context.build_messages(
                history=history,
                current_message=msg.content, channel=channel, chat_id=chat_id,
                session_id=key.replace(":", "_"),
            )

            # Realtime persist: user message (last element of initial_messages)
            self.sessions.append_message(session, messages[-1])

            final_content, _, all_msgs = await self._run_agent_loop(
                messages, session=session, callbacks=callbacks,
                provider=_provider, model=_model, tools=_tools,
            )
            self.sessions.update_metadata(session)
            return OutboundMessage(channel=channel, chat_id=chat_id,
                                  content=final_content or "Background task completed.")

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key = session_key or msg.session_key
        # Resolve through routing table (supports /new session switching)
        key = self.sessions.resolve_session_key(key)
        session = self.sessions.get_or_create(key)

        # Slash commands
        cmd = msg.content.strip().lower()
        if cmd == "/flush":
            lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())
            self._consolidating.add(session.key)
            try:
                async with lock:
                    snapshot = session.messages[session.last_consolidated:]
                    if snapshot:
                        temp = Session(key=session.key)
                        temp.messages = list(snapshot)
                        if not await self._consolidate_memory(
                            temp, archive_all=True,
                            provider=_provider, model=_model,
                            tools=_tools,
                        ):
                            return OutboundMessage(
                                channel=msg.channel, chat_id=msg.chat_id,
                                content="Memory archival failed, session not cleared. Please try again.",
                            )
            except Exception:
                logger.exception("/flush archival failed for {}", session.key)
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Memory archival failed, session not cleared. Please try again.",
                )
            finally:
                self._consolidating.discard(session.key)

            # Leave old session file in place (usage records stay matched).
            # Create a new session with a timestamped key and update routing.
            self.sessions.create_new_session(
                channel=msg.channel, chat_id=msg.chat_id, old_key=session.key,
            )
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="Session flushed — memory archived, conversation cleared.")
        if cmd == "/new":
            new_key = self.sessions.create_new_session(
                channel=msg.channel, chat_id=msg.chat_id, old_key=key,
            )
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content=f"New session started: {new_key}")
        if cmd == "/help":
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="🐈 nanobot commands:\n/new — Start a new conversation (fresh session)\n/flush — Archive memory and clear current session\n/stop — Stop the currently running task\n/provider — View/switch active LLM provider\n/session — Show current session info and status\n/session list [N] — List recent sessions\n/session switch <#N|%id> — Switch to another session\n/session name <名称> — Name current session\n/session done — Archive current session\n/session summary — Show session summary\n/session help — Show session subcommand help\n/help — Show available commands")
        if cmd.startswith("/provider"):
            return self._handle_provider_command(msg)
        if cmd == "/session" or cmd.startswith("/session "):
            return self._handle_session_command(msg, session_key=key)
        if cmd == "/stop":
            # When called via process_direct (not through run()), there's
            # no concurrent task to cancel. Just return a message.
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="No active task to stop.")

        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"),
                              session_key=key, tools=_tools)
        if message_tool := _tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        # §59: hard cap — same reasoning as the other call site.
        history = session.get_history(max_messages=self.memory_window * 2)
        initial_messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel, chat_id=msg.chat_id,
            session_id=key.replace(":", "_"),
        )

        # Realtime persist: user/system message (last element of initial_messages)
        self.sessions.append_message(session, initial_messages[-1])

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta,
            ))

        final_content, _, all_msgs = await self._run_agent_loop(
            initial_messages, on_progress=on_progress or _bus_progress,
            session=session, callbacks=callbacks,
            provider=_provider, model=_model, tools=_tools,
        )

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)

        # Messages already persisted in realtime; just update metadata.
        self.sessions.update_metadata(session)

        if message_tool := _tools.get("message"):
            if isinstance(message_tool, MessageTool) and message_tool._sent_in_turn:
                return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=final_content,
            metadata=msg.metadata or {},
        )

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """Save new-turn messages into session, truncating large tool results.

        .. deprecated::
            Replaced by realtime persistence via ``SessionManager.append_message``.
            Kept for backward compatibility but no longer called in the main flow.
        """
        from datetime import datetime
        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if role == "tool" and isinstance(content, str) and len(content) > self._TOOL_RESULT_MAX_CHARS:
                entry["content"] = content[:self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    continue
                if isinstance(content, list):
                    entry["content"] = [
                        {"type": "text", "text": "[image]"} if (
                            c.get("type") == "image_url"
                            and c.get("image_url", {}).get("url", "").startswith("data:image/")
                        ) else c for c in content
                    ]
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()

    async def _consolidate_memory(self, session, archive_all: bool = False,
                                  provider: LLMProvider | None = None,
                                  model: str | None = None,
                                  tools: ToolRegistry | None = None) -> bool:
        """Delegate to MemoryStore.consolidate(). Returns True on success."""
        _provider = provider or self.provider
        _model = model or self.model
        _tools = tools or self.tools

        # Build system message from context (same as normal chat) for cache-friendly consolidation.
        # NOTE: session.messages does NOT contain the system prompt — it's generated
        # dynamically by build_system_prompt() each turn. We must use context here.
        _system_msg = {"role": "system", "content": self.context.build_system_prompt()}

        _tool_defs = _tools.get_definitions() if _tools else None

        return await MemoryStore(self.workspace).consolidate(
            session, _provider, _model,
            archive_all=archive_all, memory_window=self.memory_window,
            detail_logger=self.detail_logger,
            usage_recorder=self.usage_recorder,
            session_system_msg=_system_msg,
            session_tools=_tool_defs,
            max_tokens=self.max_tokens,
        )

    async def _do_mid_turn_consolidation(
        self, session: Session, messages: list[dict], archive_cut: int,
        *, provider: LLMProvider | None = None, model: str | None = None,
        tools: ToolRegistry | None = None,
    ) -> None:
        """§59: Run consolidation asynchronously, store result in pending dict."""
        try:
            lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())
            async with lock:
                success = await self._consolidate_memory(
                    session, provider=provider, model=model, tools=tools,
                )
            if success:
                logger.info("§59: Mid-turn consolidation succeeded for {} (last_consolidated={})", session.key, session.last_consolidated)
            else:
                # Non-transient failure (e.g. LLM truncated, didn't call tool).
                # Advance last_consolidated anyway — session summary provides safety net.
                session.last_consolidated = len(session.messages) - (self.memory_window // 2)
                if session.last_consolidated < 0:
                    session.last_consolidated = 0
                logger.warning("§59: Mid-turn consolidation failed for {}, advancing last_consolidated to {} (non-retriable)",
                               session.key, session.last_consolidated)
            # Always schedule trim — whether consolidation succeeded or failed,
            # we need to trim the in-memory messages and inject truncation notice.
            self._pending_consolidation_done[session.key] = {
                "archive_cut": archive_cut,
                "last_consolidated": session.last_consolidated,
                "messages_len": len(messages),
            }
        except Exception:
            logger.exception("§59: Mid-turn consolidation exception for {}", session.key)
            # Same treatment as non-transient failure: advance and trim.
            session.last_consolidated = len(session.messages) - (self.memory_window // 2)
            if session.last_consolidated < 0:
                session.last_consolidated = 0
            self._pending_consolidation_done[session.key] = {
                "archive_cut": archive_cut,
                "last_consolidated": session.last_consolidated,
                "messages_len": len(messages),
            }
        finally:
            self._consolidating.discard(session.key)

    def _trim_consolidated_messages(
        self, messages: list[dict], info: dict, session: Session,
        warned_this_turn: bool,
    ) -> bool:
        """§59: Remove archived messages from in-memory list and inject truncation notice."""
        from datetime import datetime
        archive_cut = info["archive_cut"]
        saved_messages_len = info.get("messages_len")
        # If messages were rebuilt via get_history() between turns, skip trimming
        if saved_messages_len is not None and len(messages) < saved_messages_len - archive_cut:
            logger.info("§59: Skipping trim — messages rebuilt between turns (saved={}, current={}, cut={})",
                        saved_messages_len, len(messages), archive_cut)
            return warned_this_turn
        if archive_cut <= 0 or archive_cut >= len(messages):
            logger.debug("§59: Skipping trim — archive_cut={} out of range (messages={})", archive_cut, len(messages))
            return warned_this_turn

        # §59.1: Protect real user messages in the trim range [1:archive_cut]
        protected: list[dict] = []
        for i in range(1, archive_cut):
            msg = messages[i]
            if msg.get("role") == "user" and not self._is_system_injected(msg):
                protected.append(msg)

        del messages[1:archive_cut]
        notice = self._build_truncation_notice(session, self.workspace)
        messages.insert(1, {
            "role": "user",
            "content": notice,
            "timestamp": datetime.now().isoformat(),
        })

        # §59.1 v2: Consolidate protected user messages into at most 2 slots
        #   Slot 1: historical user messages (earlier turn triggers, NOT current turn)
        #   Slot 2: current turn messages (trigger + mid-turn injections)
        #
        # The trigger of the current turn is the LAST non-mid-turn user message
        # in the protected list (build_messages is chronological).
        if protected:
            _MID_TURN_PREFIXES = (
                "[Message from user during execution]",
                "[Message from parent session during execution]",
            )

            # Separate non-injection messages from injection messages
            non_injection: list[dict] = []
            injection: list[dict] = []
            for msg in protected:
                content = msg.get("content", "")
                if isinstance(content, str) and any(content.startswith(p) for p in _MID_TURN_PREFIXES):
                    injection.append(msg)
                else:
                    non_injection.append(msg)

            # The last non-injection message = current turn trigger
            # Everything before it = historical user messages from earlier turns
            if non_injection:
                trigger_msg = non_injection[-1]
                history_msgs = non_injection[:-1]
            else:
                # Edge case: all protected are mid-turn injections (no trigger in trim range)
                trigger_msg = None
                history_msgs = []

            # Current turn = trigger + mid-turn injections
            current_turn_msgs: list[dict] = []
            if trigger_msg:
                current_turn_msgs.append(trigger_msg)
            current_turn_msgs.extend(injection)

            insert_pos = 2  # right after truncation notice

            # Helper: extract text from a message (handles multimodal)
            def _extract_text(m: dict) -> str:
                c = m.get("content", "")
                if isinstance(c, list):
                    pieces = [item.get("text", "") for item in c
                              if isinstance(item, dict) and item.get("type") == "text" and item.get("text")]
                    return "\n".join(pieces) if pieces else "[non-text content]"
                return c if isinstance(c, str) and c else "[empty message]"

            # Slot 1: historical user messages from earlier turns
            if history_msgs:
                if len(history_msgs) == 1 and not current_turn_msgs:
                    # Only 1 total protected msg and it's historical → keep as-is
                    messages.insert(insert_pos, history_msgs[0])
                else:
                    _SEP_NEXT = "\n---- next message ----\n"
                    parts = [_extract_text(m) for m in history_msgs]
                    messages.insert(insert_pos, {
                        "role": "user",
                        "content": "[Preserved user messages from earlier turns]\n" + _SEP_NEXT.join(parts),
                        "timestamp": datetime.now().isoformat(),
                    })
                insert_pos += 1

            # Slot 2: current turn messages (trigger + mid-turn injections)
            if current_turn_msgs:
                if len(current_turn_msgs) == 1 and not history_msgs:
                    # Only 1 total protected msg and it's current turn → keep as-is
                    messages.insert(insert_pos, current_turn_msgs[0])
                elif len(current_turn_msgs) == 1:
                    messages.insert(insert_pos, current_turn_msgs[0])
                else:
                    _SEP_INJECTED = "\n---- injected during execution ----\n"
                    # First item is trigger (if present), rest are injections
                    assembled = _extract_text(current_turn_msgs[0])
                    for m in current_turn_msgs[1:]:
                        assembled += _SEP_INJECTED + _extract_text(m)
                    messages.insert(insert_pos, {
                        "role": "user",
                        "content": "[Preserved current-turn user messages]\n" + assembled,
                        "timestamp": datetime.now().isoformat(),
                    })

            slots_used = (1 if history_msgs else 0) + (1 if current_turn_msgs else 0)
            logger.info("§59: Protected {} user messages from trim "
                        "(history={}, current_turn={} [trigger={}, injection={}], {} slot(s))",
                        len(protected), len(history_msgs), len(current_turn_msgs),
                        1 if trigger_msg else 0, len(injection), slots_used)

        logger.info("§59: Trimmed {} messages, injected truncation notice", archive_cut)
        return False

    @staticmethod
    def _is_system_injected(msg: dict) -> bool:
        """§59.1: Check if a user-role message is system-injected (not a real user question).

        System-injected messages (returns True):
          - Content starts with '⚠️' (truncation notice, budget alert, etc.)
          - Content starts with '[Runtime Context' (runtime context metadata)

        Real user messages (returns False):
          - Ordinary user text
          - '[Message from user during execution]\\n...' (mid-turn injection containing real user content)
          - Multimodal content with no recognizable system prefix
        """
        content = msg.get("content", "")
        if isinstance(content, list):
            # Multi-modal content — check first text part
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    content = part.get("text", "")
                    break
            else:
                return False  # No text part found — treat as real user message
        if not isinstance(content, str):
            return False
        return content.startswith("⚠️") or content.startswith("[Runtime Context")

    def _find_tool_aligned_cut(self, messages: list[dict], start: int, target_count: int) -> int:
        """§59: Find a safe cut point that doesn't split tool_call/tool_result pairs.

        Archives messages[start:cut], so we check messages[cut-1] (the last archived message)
        to ensure it's not a tool result or an assistant message with tool_calls.
        """
        cut = min(start + target_count, len(messages) - 1)
        while cut > start:
            last_archived = messages[cut - 1]
            # Cannot end with a tool result (would break pairing with preceding assistant tool_calls)
            if last_archived.get("role") == "tool":
                cut -= 1
                continue
            # Cannot end with assistant(tool_calls) (corresponding tool results are after cut)
            if last_archived.get("role") == "assistant" and last_archived.get("tool_calls"):
                cut -= 1
                continue
            break
        return cut

    @staticmethod
    def _build_truncation_notice(session: Session, workspace: Path, *, hard_dropped: int = 0) -> str:
        """§59: Build truncation notice, expanding session summary if available."""
        session_id = session.key.replace(":", "_")
        archived_count = session.last_consolidated
        # Build the "what happened" description
        parts: list[str] = []
        if archived_count > 0:
            parts.append(f"{archived_count} earlier messages were archived via consolidation.")
        if hard_dropped > 0:
            parts.append(f"{hard_dropped} additional messages were dropped by hard truncation (consolidation may have failed).")
        if not parts:
            parts.append("Some earlier messages in this session are no longer in context.")
        context_line = " ".join(parts)

        summary_path = workspace / "sessions" / "session_summary" / f"{session_id}.md"
        try:
            if summary_path.exists():
                summary_content = summary_path.read_text(encoding="utf-8").strip()
                return _TRUNCATION_NOTICE_WITH_SUMMARY.format(
                    archived_count=context_line,
                    summary_content=summary_content,
                    workspace=str(workspace),
                    session_id=session_id,
                )
        except Exception:
            logger.debug("§59: Failed to read session summary from {}", summary_path)
        return _TRUNCATION_NOTICE_NO_SUMMARY.format(
            archived_count=context_line,
            workspace=str(workspace),
            session_id=session_id,
        )

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        media: list[str] | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        callbacks: DefaultCallbacks | None = None,
    ) -> str:
        """Process a message directly (for CLI, cron, or SDK usage).

        When *callbacks* is provided, events are dispatched to the callback
        object.  The ``on_done`` callback receives an ``AgentResult``.

        Errors are persisted to the session JSONL (as ``role: "assistant"``
        with ``"Error calling LLM:"`` prefix) so that the message survives
        a page refresh, then **re-raised** so the caller can handle task
        status as before.
        """
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content,
                             media=media or [])
        try:
            response = await self._process_message(
                msg, session_key=session_key, on_progress=on_progress, callbacks=callbacks,
            )
        except Exception as e:
            # Persist error to session JSONL before re-raising.
            self._persist_error(session_key or f"{channel}:{chat_id}", e)
            raise
        result_content = response.content if response else ""

        # Fire on_done callback
        if callbacks is not None:
            await callbacks.on_done(AgentResult(content=result_content))

        return result_content


# ── Module-level helpers ────────────────────────────────────────────


def _budget_alert_threshold(max_iterations: int) -> int:
    """Return the remaining-iterations count at which to inject a budget alert.

    - max_iterations >= 20  →  threshold = 10
    - max_iterations < 20   →  threshold = max(3, max_iterations // 4)
    """
    if max_iterations >= 20:
        return 10
    return max(3, max_iterations // 4)
