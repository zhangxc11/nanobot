"""Subagent manager for background task execution.

Phase 26: Enhanced with configurable max_iterations, session persistence,
budget alerts, LLM retry, and usage recording.

Phase 26 fix: Added ``task_keeper`` callback to prevent asyncio.Task GC
when the host AgentLoop/SubagentManager is garbage collected (critical for
web worker where each request creates a short-lived AgentLoop).
"""

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ExecToolConfig
from nanobot.providers.base import LLMProvider

if TYPE_CHECKING:
    from nanobot.session.manager import SessionManager
    from nanobot.usage.recorder import UsageRecorder

# Hard ceiling to prevent runaway subagents
MAX_SUBAGENT_ITERATIONS = 100

# Default iterations — raised from 15 to 30 for practical usability
DEFAULT_SUBAGENT_ITERATIONS = 30

# Retry configuration for transient LLM errors (Phase 28: use shared module)
from nanobot.agent.retry import is_retryable as _is_retryable_shared
from nanobot.agent.retry import is_fast_retryable, compute_retry_delay
_MAX_RETRIES = 5


def _is_retryable(error: Exception) -> bool:
    """Check if an LLM error is transient and worth retrying.

    Delegates to shared ``agent.retry.is_retryable()`` (Phase 28).
    """
    return _is_retryable_shared(error)


def _budget_alert_threshold(max_iterations: int) -> int:
    """Calculate the budget alert threshold (remaining iterations).

    Replicates the logic from ``agent/loop.py::_budget_alert_threshold``.
    """
    if max_iterations >= 20:
        return 10
    return max(3, max_iterations // 4)


class SubagentManager:
    """Manages background subagent execution.

    Phase 26 enhancements:
    - Configurable ``max_iterations`` (default 30, hard cap 100)
    - Optional session persistence (``persist=True``)
    - Budget alert injection near iteration limit
    - LLM call retry with exponential backoff
    - Token usage recording to analytics.db
    """

    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,
        brave_api_key: str | None = None,
        web_proxy: str | None = None,
        exec_config: "ExecToolConfig | None" = None,
        restrict_to_workspace: bool = False,
        # Phase 26 additions
        default_max_iterations: int = DEFAULT_SUBAGENT_ITERATIONS,
        usage_recorder: "UsageRecorder | None" = None,
        session_manager: "SessionManager | None" = None,
        task_keeper: "Callable[[asyncio.Task], None] | None" = None,
    ):
        from nanobot.config.schema import ExecToolConfig
        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.model = model or provider.get_default_model()
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort
        self.brave_api_key = brave_api_key
        self.web_proxy = web_proxy
        self.exec_config = exec_config or ExecToolConfig()
        self.restrict_to_workspace = restrict_to_workspace
        self.default_max_iterations = min(default_max_iterations, MAX_SUBAGENT_ITERATIONS)
        self.usage_recorder = usage_recorder
        self.session_manager = session_manager
        self._task_keeper = task_keeper
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._session_tasks: dict[str, set[str]] = {}  # session_key -> {task_id, ...}

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
        # Phase 26 additions
        max_iterations: int | None = None,
        persist: bool = False,
    ) -> str:
        """Spawn a subagent to execute a task in the background.

        Parameters
        ----------
        task:
            The task description for the subagent.
        label:
            Short display label (defaults to first 30 chars of task).
        origin_channel / origin_chat_id:
            Where to announce the result.
        session_key:
            Parent session key (for tracking / cancellation).
        max_iterations:
            Maximum tool call iterations (default: ``self.default_max_iterations``,
            hard cap: ``MAX_SUBAGENT_ITERATIONS``).
        persist:
            If True, persist subagent messages to a session JSONL file.
            The session key will be ``subagent:<parent_key_sanitized>_<task_id>``
            so the frontend can identify and group subagent sessions.
        """
        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        origin = {"channel": origin_channel, "chat_id": origin_chat_id}

        # Build subagent session key with parent info for frontend identification
        # e.g. "subagent:webchat_1772030778_a1b2c3d4"
        if session_key:
            # Sanitize parent session key: replace ':' with '_' for filename safety
            parent_sanitized = session_key.replace(":", "_")
            subagent_key = f"subagent:{parent_sanitized}_{task_id}"
        else:
            subagent_key = f"subagent:{task_id}"

        # Clamp max_iterations
        effective_max = min(
            max_iterations if max_iterations is not None else self.default_max_iterations,
            MAX_SUBAGENT_ITERATIONS,
        )

        bg_task = asyncio.create_task(
            self._run_subagent(task_id, task, display_label, origin,
                               effective_max, persist, subagent_key)
        )
        self._running_tasks[task_id] = bg_task
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        # Register with external task keeper to prevent GC when SubagentManager
        # is garbage collected (critical for web worker short-lived AgentLoop).
        if self._task_keeper is not None:
            try:
                self._task_keeper(bg_task)
            except Exception as e:
                logger.warning("task_keeper registration failed: {}", e)

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]

        bg_task.add_done_callback(_cleanup)

        logger.info("Spawned subagent [{}]: {} (max_iterations={}, persist={})",
                     task_id, display_label, effective_max, persist)
        return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
        max_iterations: int,
        persist: bool,
        subagent_session_key: str,
    ) -> None:
        """Execute the subagent task and announce the result."""
        logger.info("Subagent [{}] starting task: {} (max_iterations={})",
                     task_id, label, max_iterations)

        try:
            # Build subagent tools (no message tool, no spawn tool, no cron tool)
            tools = ToolRegistry()
            allowed_dir = self.workspace if self.restrict_to_workspace else None
            tools.register(ReadFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(WriteFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(EditFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(ListDirTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.restrict_to_workspace,
                path_append=self.exec_config.path_append,
            ))
            tools.register(WebSearchTool(api_key=self.brave_api_key, proxy=self.web_proxy))
            tools.register(WebFetchTool(proxy=self.web_proxy))

            system_prompt = self._build_subagent_prompt()
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]

            # ── Session persistence setup ──
            session = None
            if persist and self.session_manager:
                session = self.session_manager.get_or_create(subagent_session_key)
                user_msg = {
                    "role": "user",
                    "content": task,
                    "timestamp": datetime.now().isoformat(),
                }
                self.session_manager.append_message(session, user_msg)

            # ── Main agent loop ──
            iteration = 0
            final_result: str | None = None
            threshold = _budget_alert_threshold(max_iterations)

            while iteration < max_iterations:
                iteration += 1

                # Budget alert injection (once, when remaining == threshold)
                remaining = max_iterations - iteration
                if remaining == threshold:
                    budget_msg = {
                        "role": "system",
                        "content": (
                            f"⚠️ Budget alert: You have {remaining} tool call iterations "
                            f"remaining (out of {max_iterations}). Please prioritize saving "
                            f"your work state and wrapping up gracefully."
                        ),
                    }
                    messages.append(budget_msg)

                # LLM call with retry
                response = await self._chat_with_retry(messages, tools)

                # ── Usage recording ──
                if response.usage and self.usage_recorder:
                    now = datetime.now().isoformat()
                    try:
                        self.usage_recorder.record(
                            session_key=subagent_session_key,
                            model=self.model,
                            prompt_tokens=response.usage.get("prompt_tokens", 0),
                            completion_tokens=response.usage.get("completion_tokens", 0),
                            total_tokens=response.usage.get("total_tokens", 0),
                            llm_calls=1,
                            started_at=now,
                            finished_at=now,
                        )
                    except Exception as e:
                        logger.warning("Subagent [{}] usage recording failed: {}", task_id, e)

                if response.has_tool_calls:
                    # Build assistant message with tool calls
                    tool_call_dicts = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in response.tool_calls
                    ]
                    assistant_msg = {
                        "role": "assistant",
                        "content": response.content or "",
                        "tool_calls": tool_call_dicts,
                        "timestamp": datetime.now().isoformat(),
                    }
                    messages.append(assistant_msg)

                    # Persist assistant message
                    if session and self.session_manager:
                        self.session_manager.append_message(session, assistant_msg)

                    # Execute tools
                    for tool_call in response.tool_calls:
                        args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                        logger.debug("Subagent [{}] executing: {} with arguments: {}",
                                     task_id, tool_call.name, args_str)
                        result = await tools.execute(tool_call.name, tool_call.arguments)
                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_call.name,
                            "content": result,
                            "timestamp": datetime.now().isoformat(),
                        }
                        messages.append(tool_msg)

                        # Persist tool message
                        if session and self.session_manager:
                            self.session_manager.append_message(session, tool_msg)
                else:
                    # Final response (no tool calls)
                    final_result = response.content

                    # Persist final assistant message
                    if session and self.session_manager:
                        final_msg = {
                            "role": "assistant",
                            "content": final_result or "",
                            "timestamp": datetime.now().isoformat(),
                        }
                        self.session_manager.append_message(session, final_msg)
                    break

            if final_result is None:
                final_result = (
                    f"I reached the maximum number of tool call iterations "
                    f"({max_iterations}) before completing the task. "
                    f"Partial progress may have been made."
                )

            logger.info("Subagent [{}] completed successfully (iterations: {}/{})",
                         task_id, iteration, max_iterations)
            await self._announce_result(task_id, label, task, final_result, origin, "ok")

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            logger.error("Subagent [{}] failed: {}", task_id, e)
            await self._announce_result(task_id, label, task, error_msg, origin, "error")

    async def _chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: ToolRegistry,
    ) -> Any:
        """Call provider.chat() with exponential backoff retry for transient errors.

        Phase 28: Enhanced with smart retry delays — fast for disconnected/
        timeout errors, slow for rate-limit/overload.
        Retries up to ``_MAX_RETRIES`` times.
        Non-retryable errors are raised immediately.
        """
        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                kwargs: dict[str, Any] = dict(
                    messages=messages,
                    tools=tools.get_definitions(),
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
                if self.reasoning_effort is not None:
                    kwargs["reasoning_effort"] = self.reasoning_effort
                return await self.provider.chat(**kwargs)
            except Exception as e:
                if attempt < _MAX_RETRIES and _is_retryable(e):
                    fast = is_fast_retryable(e)
                    delay = compute_retry_delay(attempt, fast)
                    retry_type = "fast" if fast else "slow"
                    logger.warning(
                        "Subagent LLM retry {}/{} ({}) after {:.0f}s: {}",
                        attempt + 1, _MAX_RETRIES, retry_type, delay, e,
                    )
                    await asyncio.sleep(delay)
                    last_error = e
                else:
                    raise
        # Should not reach here, but satisfy type checker
        assert last_error is not None
        raise last_error

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus."""
        status_text = "completed successfully" if status == "ok" else "failed"

        announce_content = f"""[Subagent '{label}' {status_text}]

Task: {task}

Result:
{result}

Summarize this naturally for the user. Keep it brief (1-2 sentences). Do not mention technical details like "subagent" or task IDs."""

        # Inject as system message to trigger main agent
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
        )

        await self.bus.publish_inbound(msg)
        logger.debug("Subagent [{}] announced result to {}:{}",
                      task_id, origin["channel"], origin["chat_id"])

    def _build_subagent_prompt(self) -> str:
        """Build a focused system prompt for the subagent."""
        from nanobot.agent.context import ContextBuilder
        from nanobot.agent.skills import SkillsLoader

        time_ctx = ContextBuilder._build_runtime_context(None, None)
        parts = [f"""# Subagent

{time_ctx}

You are a subagent spawned by the main agent to complete a specific task.
Stay focused on the assigned task. Your final response will be reported back to the main agent.

## Workspace
{self.workspace}"""]

        skills_summary = SkillsLoader(self.workspace).build_skills_summary()
        if skills_summary:
            parts.append(f"## Skills\n\nRead SKILL.md with read_file to use a skill.\n\n{skills_summary}")

        return "\n\n".join(parts)

    async def cancel_by_session(self, session_key: str) -> int:
        """Cancel all subagents for the given session. Returns count cancelled."""
        tasks = [self._running_tasks[tid] for tid in self._session_tasks.get(session_key, [])
                 if tid in self._running_tasks and not self._running_tasks[tid].done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)

    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._running_tasks)
