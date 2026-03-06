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
        self.usage_recorder = usage_recorder
        self.detail_logger = detail_logger
        self.audit_logger = audit_logger

        self.context = ContextBuilder(workspace)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
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
        )

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._consolidating: set[str] = set()  # Session keys with consolidation in progress
        self._consolidation_tasks: set[asyncio.Task] = set()  # Strong refs to in-flight tasks
        self._consolidation_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._processing_lock = asyncio.Lock()
        self._register_default_tools()
        if self.audit_logger is not None:
            self.tools.set_audit_logger(self.audit_logger)

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
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
                spawn_tool.set_context(channel, chat_id)

        if cron_tool := _tools.get("cron"):
            if isinstance(cron_tool, CronTool):
                cron_tool.set_context(channel, chat_id)

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

        Matches rate-limit, connection, and timeout errors from litellm
        and upstream providers without importing their exception classes.
        """
        error_type = type(error).__name__
        # litellm wraps provider errors with these class names
        if error_type in (
            "RateLimitError",
            "APIConnectionError",
            "APITimeoutError",
            "Timeout",
            "ServiceUnavailableError",
            "InternalServerError",
        ):
            return True
        # Check HTTP status code if available (litellm attaches it)
        status = getattr(error, "status_code", None)
        if status in (429, 500, 502, 503, 504, 529):
            return True
        # Fallback: check error message string
        error_str = str(error).lower()
        if "rate limit" in error_str or "rate_limit" in error_str:
            return True
        if "overloaded" in error_str or "capacity" in error_str:
            return True
        return False

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
        progress_fn: Callable[..., Awaitable[None]] | None = None,
    ):
        """Call provider.chat() with exponential backoff for transient errors.

        Retries up to 5 times with delays of 10s, 20s, 40s, 80s, 160s.
        Non-retryable errors are raised immediately.

        Parameters
        ----------
        provider:
            LLM provider to use.  Falls back to ``self.provider`` if None.
        reasoning_effort:
            Optional reasoning effort hint passed to the provider.
        """
        _provider = provider or self.provider
        max_retries = 5
        base_delay = 10  # seconds

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
                return await _provider.chat(**kwargs)
            except Exception as e:
                if not self._is_retryable(e) or attempt >= max_retries:
                    raise
                delay = base_delay * (2 ** attempt)  # 10, 20, 40, 80, 160
                logger.warning(
                    "LLM call failed (attempt {}/{}): {}. Retrying in {}s...",
                    attempt + 1, max_retries, str(e)[:200], delay,
                )
                if progress_fn:
                    try:
                        await progress_fn(
                            f"⏳ API 限流，等待 {delay}s 后重试 ({attempt + 1}/{max_retries})"
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
        }

        # Resolve progress callback: callbacks.on_progress takes precedence
        _progress_fn = on_progress
        if callbacks is not None:
            async def _cb_progress(text: str, *, tool_hint: bool = False) -> None:
                await callbacks.on_progress(text, tool_hint=tool_hint)
            _progress_fn = _cb_progress

        # How many messages existed before the loop — used to determine
        # which messages are "new" for realtime persistence.
        pre_loop_count = len(messages)

        while iteration < self.max_iterations:
            iteration += 1

            # ── Budget alert: warn LLM when iterations are running low ──
            remaining = self.max_iterations - iteration
            threshold = _budget_alert_threshold(self.max_iterations)
            if remaining == threshold:
                messages.append({
                    "role": "system",
                    "content": (
                        f"⚠️ Budget alert: You have {remaining} tool call iterations "
                        f"remaining (out of {self.max_iterations}). Please prioritize "
                        f"saving your work state and wrapping up gracefully."
                    ),
                })

            response = await self._chat_with_retry(
                provider=_provider,
                messages=messages,
                tools=_tools.get_definitions(),
                model=_model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                reasoning_effort=self.reasoning_effort,
                progress_fn=_progress_fn,
            )

            # Record token usage from this LLM call — immediately to SQLite
            # so that a crash mid-turn does not lose usage data.
            if response.usage:
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
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
                )

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

                # ── User injection checkpoint ──
                # After all tools in this round complete, check if the user
                # has sent supplementary input to inject before the next LLM call.
                if callbacks is not None:
                    injected = await callbacks.check_user_input()
                    if injected:
                        logger.info("User injected message: {}", injected[:120])
                        inject_msg = {
                            "role": "user",
                            "content": f"[User interjection during execution]\n{injected}",
                            "timestamp": datetime.now().isoformat(),
                        }
                        messages.append(inject_msg)

                        # Realtime persist: injected user message
                        if session is not None:
                            self.sessions.append_message(session, inject_msg)
                        await callbacks.on_message(inject_msg)
                        if _progress_fn:
                            await _progress_fn(f"📝 User: {injected[:80]}")
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

                # Realtime persist: final assistant message
                if session is not None:
                    self.sessions.append_message(session, messages[-1])
                if callbacks is not None:
                    await callbacks.on_message(messages[-1])
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
            if cmd == "/session":
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
                    await worker.callbacks.inject(msg.content)
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
        """Handle /session slash command: show current session info and status.

        Parameters
        ----------
        session_key:
            Resolved session key.  If None, derived from msg.session_key.
        active_sessions:
            Dict of active SessionWorker instances (gateway concurrent mode).
            If None (CLI/direct mode), status is always "idle".
        """
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
            except Exception:
                token_line = "查询失败"
        else:
            token_line = "N/A（未配置 UsageRecorder）"

        # ── Build output ──
        lines = [
            f"📋 **Session 信息**",
            f"",
            f"**Session Key**: `{key}`",
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
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"Sorry, I encountered an error: {str(e)}"
            ))


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
            history = session.get_history(max_messages=self.memory_window)
            messages = self.context.build_messages(
                history=history,
                current_message=msg.content, channel=channel, chat_id=chat_id,
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
                                  content="🐈 nanobot commands:\n/new — Start a new conversation (fresh session)\n/flush — Archive memory and clear current session\n/stop — Stop the currently running task\n/provider — View/switch active LLM provider\n/session — Show current session info and status\n/help — Show available commands")
        if cmd.startswith("/provider"):
            return self._handle_provider_command(msg)
        if cmd == "/session":
            return self._handle_session_command(msg, session_key=key)
        if cmd == "/stop":
            # When called via process_direct (not through run()), there's
            # no concurrent task to cancel. Just return a message.
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="No active task to stop.")

        unconsolidated = len(session.messages) - session.last_consolidated
        if (unconsolidated >= self.memory_window and session.key not in self._consolidating):
            self._consolidating.add(session.key)
            lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())

            async def _consolidate_and_unlock():
                try:
                    async with lock:
                        await self._consolidate_memory(
                            session, provider=_provider, model=_model,
                        )
                finally:
                    self._consolidating.discard(session.key)
                    _task = asyncio.current_task()
                    if _task is not None:
                        self._consolidation_tasks.discard(_task)

            _task = asyncio.create_task(_consolidate_and_unlock())
            self._consolidation_tasks.add(_task)

        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"),
                              session_key=key, tools=_tools)
        if message_tool := _tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        history = session.get_history(max_messages=self.memory_window)
        initial_messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel, chat_id=msg.chat_id,
        )

        # Realtime persist: user message (last element of initial_messages)
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
                                  model: str | None = None) -> bool:
        """Delegate to MemoryStore.consolidate(). Returns True on success."""
        _provider = provider or self.provider
        _model = model or self.model
        return await MemoryStore(self.workspace).consolidate(
            session, _provider, _model,
            archive_all=archive_all, memory_window=self.memory_window,
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
        """
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content,
                             media=media or [])
        response = await self._process_message(
            msg, session_key=session_key, on_progress=on_progress, callbacks=callbacks,
        )
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
