"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import re
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
        brave_api_key: str | None = None,
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
        self.brave_api_key = brave_api_key
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
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._consolidating: set[str] = set()  # Session keys with consolidation in progress
        self._consolidation_tasks: set[asyncio.Task] = set()  # Strong refs to in-flight tasks
        self._consolidation_locks: dict[str, asyncio.Lock] = {}
        self._active_task: asyncio.Task | None = None  # Currently running _process_message task
        self._active_task_msg: InboundMessage | None = None  # The message being processed
        self._active_task_session_key: str | None = None  # Session key of the active task
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
        ))
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())
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
            val = next(iter(tc.arguments.values()), None) if tc.arguments else None
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
        progress_fn: Callable[..., Awaitable[None]] | None = None,
    ):
        """Call provider.chat() with exponential backoff for transient errors.

        Retries up to 5 times with delays of 10s, 20s, 40s, 80s, 160s.
        Non-retryable errors are raised immediately.

        Parameters
        ----------
        provider:
            LLM provider to use.  Falls back to ``self.provider`` if None.
        """
        _provider = provider or self.provider
        max_retries = 5
        base_delay = 10  # seconds

        for attempt in range(max_retries + 1):
            try:
                return await _provider.chat(
                    messages=messages,
                    tools=tools,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
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

            response = await self._chat_with_retry(
                provider=_provider,
                messages=messages,
                tools=_tools.get_definitions(),
                model=_model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
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
                final_content = self._strip_think(response.content)
                # Append the final assistant message so it gets persisted
                messages = self.context.add_assistant_message(
                    messages, response.content, None,
                    reasoning_content=response.reasoning_content,
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
        """Run the agent loop, processing messages from the bus.

        Messages are processed sequentially, except for ``/stop`` which is
        handled immediately by cancelling the currently running task.
        """
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=1.0
                )

                # ── /stop: cancel the active task immediately ──
                cmd = msg.content.strip().lower()
                if cmd == "/stop":
                    await self._handle_stop(msg)
                    continue

                # ── Normal message: wrap in a Task so /stop can cancel it ──
                # Resolve session key for tracking
                session_key = msg.session_key
                session_key = self.sessions.resolve_session_key(session_key)
                self._active_task_session_key = session_key
                self._active_task_msg = msg

                task = asyncio.create_task(self._process_message_safe(msg))
                self._active_task = task

                # Wait for the task, but also keep consuming /stop commands
                await self._wait_with_stop_listener(task)

                self._active_task = None
                self._active_task_msg = None
                self._active_task_session_key = None

            except asyncio.TimeoutError:
                continue

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """Handle /stop command by cancelling the active task."""
        task = self._active_task
        if task is not None and not task.done():
            active_msg = self._active_task_msg
            # Check if the stop is for the same chat (same channel + chat_id)
            if (active_msg is not None
                    and active_msg.channel == msg.channel
                    and active_msg.chat_id == msg.chat_id):
                logger.info("/stop received from {}:{}, cancelling active task",
                            msg.channel, msg.chat_id)
                task.cancel()
                # Wait briefly for cancellation to take effect
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
                # The _process_message_safe wrapper sends the stop response
                return
            else:
                logger.info("/stop from {}:{} but active task is for {}:{}",
                            msg.channel, msg.chat_id,
                            active_msg.channel if active_msg else "?",
                            active_msg.chat_id if active_msg else "?")

        # No active task to stop
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content="No active task to stop.",
        ))

    async def _wait_with_stop_listener(self, task: asyncio.Task) -> None:
        """Wait for *task* to finish while still consuming /stop commands.

        Non-stop messages that arrive while the task is running are
        **put back** into the inbound queue so they are processed next.
        """
        while not task.done():
            try:
                incoming = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=0.5,
                )
                if incoming.content.strip().lower() == "/stop":
                    await self._handle_stop(incoming)
                else:
                    # Put it back for later processing
                    await self.bus.publish_inbound(incoming)
                    # Yield briefly so the running task can make progress
                    await asyncio.sleep(0.1)
            except asyncio.TimeoutError:
                continue

        # Propagate exceptions from the task (if any, not CancelledError)
        if task.done() and not task.cancelled():
            exc = task.exception()
            if exc is not None:
                logger.error("Task failed with exception: {}", exc)

    def _handle_provider_command(self, msg: InboundMessage) -> OutboundMessage:
        """Handle /provider slash command: view or switch active provider.

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
            lines = [f"🔌 当前: **{pool.active_provider}** / `{pool.active_model}`"]
            lines.append("\n可用 providers:")
            for item in pool.available:
                marker = " ← 当前" if item["name"] == pool.active_provider else ""
                lines.append(f"  • **{item['name']}** (`{item['model']}`){marker}")
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content="\n".join(lines),
            )
        else:
            # /provider <name> [model] — switch
            provider_name = parts[1]
            model = parts[2] if len(parts) > 2 else None
            try:
                pool.switch(provider_name, model)
                # Also update self.model so AgentLoop uses the new model
                self.model = pool.active_model
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content=f"✅ 已切换到 **{pool.active_provider}** / `{pool.active_model}`",
                )
            except ValueError as e:
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content=f"❌ {e}",
                )

    async def _process_message_safe(self, msg: InboundMessage) -> None:
        """Wrapper around _process_message that handles CancelledError gracefully."""
        try:
            response = await self._process_message(msg)
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

    def _get_consolidation_lock(self, session_key: str) -> asyncio.Lock:
        lock = self._consolidation_locks.get(session_key)
        if lock is None:
            lock = asyncio.Lock()
            self._consolidation_locks[session_key] = lock
        return lock

    def _prune_consolidation_lock(self, session_key: str, lock: asyncio.Lock) -> None:
        """Drop lock entry if no longer in use."""
        if not lock.locked():
            self._consolidation_locks.pop(session_key, None)

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
            lock = self._get_consolidation_lock(session.key)
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
                self._prune_consolidation_lock(session.key, lock)

            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)
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
                                  content="🐈 nanobot commands:\n/new — Start a new conversation (fresh session)\n/flush — Archive memory and clear current session\n/stop — Stop the currently running task\n/provider — View/switch active LLM provider\n/help — Show available commands")
        if cmd.startswith("/provider"):
            return self._handle_provider_command(msg)
        if cmd == "/stop":
            # When called via process_direct (not through run()), there's
            # no concurrent task to cancel. Just return a message.
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="No active task to stop.")

        unconsolidated = len(session.messages) - session.last_consolidated
        if (unconsolidated >= self.memory_window and session.key not in self._consolidating):
            self._consolidating.add(session.key)
            lock = self._get_consolidation_lock(session.key)

            async def _consolidate_and_unlock():
                try:
                    async with lock:
                        await self._consolidate_memory(
                            session, provider=_provider, model=_model,
                        )
                finally:
                    self._consolidating.discard(session.key)
                    self._prune_consolidation_lock(session.key, lock)
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

        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=final_content,
            metadata=msg.metadata or {},
        )

    _TOOL_RESULT_MAX_CHARS = 500

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """Save new-turn messages into session, truncating large tool results.

        .. deprecated::
            Replaced by realtime persistence via ``SessionManager.append_message``.
            Kept for backward compatibility but no longer called in the main flow.
        """
        from datetime import datetime
        for m in messages[skip:]:
            entry = {k: v for k, v in m.items() if k != "reasoning_content"}
            if entry.get("role") == "tool" and isinstance(entry.get("content"), str):
                content = entry["content"]
                if len(content) > self._TOOL_RESULT_MAX_CHARS:
                    entry["content"] = content[:self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
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
