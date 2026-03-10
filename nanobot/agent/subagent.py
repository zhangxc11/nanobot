"""Subagent manager for background task execution.

Phase 26: Enhanced with configurable max_iterations, session persistence,
budget alerts, LLM retry, and usage recording.

Phase 26 fix: Added ``task_keeper`` callback to prevent asyncio.Task GC
when the host AgentLoop/SubagentManager is garbage collected (critical for
web worker where each request creates a short-lived AgentLoop).

§36: Added follow_up capability — append messages to existing subagents.
Auto-detects state: inject into running subagent, or resume finished one.
"""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
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
    from nanobot.agent.callbacks import SessionMessenger
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


@dataclass
class SubagentMeta:
    """Metadata for a spawned subagent, retained after completion for follow_up.

    §36: Created at spawn time, persists in memory until process restart.
    """
    task_id: str
    subagent_session_key: str
    parent_session_key: str | None
    label: str
    origin: dict[str, str]
    inject_queue: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    status: str = "running"           # running | completed | failed | max_iterations
    max_iterations: int = DEFAULT_SUBAGENT_ITERATIONS
    persist: bool = True


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
        # Phase 30 addition
        session_messenger: "SessionMessenger | None" = None,
        # §34 addition
        read_file_hard_limit: int | None = None,
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
        self.read_file_hard_limit = read_file_hard_limit
        self.default_max_iterations = min(default_max_iterations, MAX_SUBAGENT_ITERATIONS)
        self.usage_recorder = usage_recorder
        self.session_manager = session_manager
        self._task_keeper = task_keeper
        self.session_messenger = session_messenger
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._session_tasks: dict[str, set[str]] = {}  # session_key -> {task_id, ...}
        self._task_meta: dict[str, SubagentMeta] = {}   # task_id -> metadata (§36)

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
        # Phase 26 additions
        max_iterations: int | None = None,
        persist: bool = True,
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
            Default True — only set False for trivial throwaway tasks.
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

        # §36: Create metadata for follow_up support
        meta = SubagentMeta(
            task_id=task_id,
            subagent_session_key=subagent_key,
            parent_session_key=session_key,
            label=display_label,
            origin=origin,
            status="running",
            max_iterations=effective_max,
            persist=persist,
        )
        self._task_meta[task_id] = meta

        bg_task = asyncio.create_task(
            self._run_subagent(task_id, task, display_label, origin,
                               effective_max, persist, subagent_key,
                               parent_session_key=session_key,
                               inject_queue=meta.inject_queue)
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

        def _cleanup(_task: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            # §36: Do NOT remove from _session_tasks or _task_meta —
            # they are needed for follow_up ownership checks and resume.

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
        parent_session_key: str | None = None,
        inject_queue: asyncio.Queue[str] | None = None,
        resume_messages: list[dict[str, Any]] | None = None,
    ) -> None:
        """Execute the subagent task and announce the result.

        §36: Added inject_queue for mid-execution message injection,
        and resume_messages for resuming from session history.
        """
        logger.info("Subagent [{}] starting task: {} (max_iterations={}{})",
                     task_id, label, max_iterations,
                     ", resumed" if resume_messages else "")

        try:
            # Build subagent tools (no message tool, no spawn tool, no cron tool)
            tools = ToolRegistry()
            allowed_dir = self.workspace if self.restrict_to_workspace else None
            rf_kwargs: dict = dict(workspace=self.workspace, allowed_dir=allowed_dir)
            if self.read_file_hard_limit is not None:
                rf_kwargs["hard_limit"] = self.read_file_hard_limit
            tools.register(ReadFileTool(**rf_kwargs))
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

            # §36: Resume from history or start fresh
            if resume_messages is not None:
                messages = resume_messages
            else:
                messages: list[dict[str, Any]] = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": task},
                ]

            # ── Session persistence setup ──
            session = None
            if persist and self.session_manager:
                session = self.session_manager.get_or_create(subagent_session_key)
                if resume_messages is None:
                    # Only persist initial user message for fresh spawns;
                    # resume messages are already in the session history.
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
                            cache_creation_input_tokens=response.usage.get("cache_creation_input_tokens", 0),
                            cache_read_input_tokens=response.usage.get("cache_read_input_tokens", 0),
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

                    # ── §36: Inject checkpoint ──
                    # After all tools in this round complete, drain any messages
                    # injected by the parent session via follow_up.
                    if inject_queue is not None:
                        while not inject_queue.empty():
                            try:
                                injected_text = inject_queue.get_nowait()
                                inject_msg = {
                                    "role": "user",
                                    "content": f"[Message from parent session during execution]\n{injected_text}",
                                    "timestamp": datetime.now().isoformat(),
                                }
                                messages.append(inject_msg)
                                if session and self.session_manager:
                                    self.session_manager.append_message(session, inject_msg)
                                logger.info("Subagent [{}] injected message: {}",
                                            task_id, injected_text[:120])
                            except asyncio.QueueEmpty:
                                break
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
                # §36: Update meta status
                if task_id in self._task_meta:
                    self._task_meta[task_id].status = "max_iterations"
            else:
                # §36: Update meta status
                if task_id in self._task_meta:
                    self._task_meta[task_id].status = "completed"

            logger.info("Subagent [{}] completed successfully (iterations: {}/{})",
                         task_id, iteration, max_iterations)
            await self._announce_result(task_id, label, task, final_result, origin, "ok",
                                        subagent_session_key=subagent_session_key,
                                        parent_session_key=parent_session_key)

        except Exception as e:
            # §36: Update meta status
            if task_id in self._task_meta:
                self._task_meta[task_id].status = "failed"
            error_msg = f"Error: {str(e)}"
            logger.error("Subagent [{}] failed: {}", task_id, e)
            await self._announce_result(task_id, label, task, error_msg, origin, "error",
                                        subagent_session_key=subagent_session_key,
                                        parent_session_key=parent_session_key)

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

    # ── §36: Follow-up support ──

    def _check_ownership(self, parent_session_key: str, target_task_id: str) -> SubagentMeta:
        """Verify that the caller owns the target subagent.

        Raises ValueError if the task_id is unknown or belongs to another session.
        """
        meta = self._task_meta.get(target_task_id)
        if meta is None:
            raise ValueError(f"Unknown subagent task_id: {target_task_id}")
        if meta.parent_session_key != parent_session_key:
            raise ValueError(f"Subagent {target_task_id} does not belong to this session")
        return meta

    async def follow_up(
        self,
        task_id: str,
        message: str,
        parent_session_key: str,
        max_iterations: int | None = None,
    ) -> str:
        """Send a follow-up message to an existing subagent.

        Auto-detects the subagent's state:
        - Running → inject message into its execution flow (no new turn)
        - Finished → resume from session history with a new turn

        Parameters
        ----------
        task_id:
            The target subagent's task_id.
        message:
            The message content to send.
        parent_session_key:
            The caller's session key (for ownership verification).
        max_iterations:
            For resume: fresh iteration budget for the new turn.
            For inject: ignored (does not affect current turn).
        """
        # 1. Ownership check
        meta = self._check_ownership(parent_session_key, task_id)

        # 2. Determine state
        task = self._running_tasks.get(task_id)
        is_running = task is not None and not task.done()

        if is_running:
            # ── Inject: put into queue, no new turn ──
            meta.inject_queue.put_nowait(message)
            logger.info("Follow-up injected into running subagent [{}] (id: {}): {}",
                        meta.label, task_id, message[:120])
            return (
                f"Message injected into running subagent [{meta.label}] (id: {task_id}). "
                f"It will be read before the next LLM call."
            )
        else:
            # ── Resume: load history, append message, start new turn ──
            if not meta.persist:
                raise ValueError(
                    f"Cannot resume subagent {task_id}: session was not persisted "
                    f"(persist=False). No history to resume from."
                )
            if not self.session_manager:
                raise ValueError(
                    f"Cannot resume subagent {task_id}: no SessionManager available."
                )

            # Load session history
            session_obj = self.session_manager.get_or_create(meta.subagent_session_key)
            history = session_obj.get_history()
            if not history:
                raise ValueError(
                    f"Cannot resume subagent {task_id}: session history is empty."
                )

            # Rebuild messages: system prompt + history + new user message
            system_prompt = self._build_subagent_prompt()
            resume_msgs: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
            ]
            # Add history (skip system messages from history to avoid duplication)
            for msg in history:
                if msg.get("role") != "system":
                    resume_msgs.append(msg)

            # Append the follow-up message
            follow_up_msg = {
                "role": "user",
                "content": message,
                "timestamp": datetime.now().isoformat(),
            }
            resume_msgs.append(follow_up_msg)

            # Persist the follow-up message
            self.session_manager.append_message(session_obj, follow_up_msg)

            # Fresh iteration budget for the new turn
            effective_max = min(
                max_iterations if max_iterations is not None else meta.max_iterations,
                MAX_SUBAGENT_ITERATIONS,
            )

            # Reset inject_queue (create fresh one for the new turn)
            meta.inject_queue = asyncio.Queue()
            meta.status = "running"

            # Start new background task
            bg_task = asyncio.create_task(
                self._run_subagent(
                    task_id=task_id,
                    task=message,  # For announce: show the follow-up message
                    label=meta.label,
                    origin=meta.origin,
                    max_iterations=effective_max,
                    persist=meta.persist,
                    subagent_session_key=meta.subagent_session_key,
                    parent_session_key=meta.parent_session_key,
                    inject_queue=meta.inject_queue,
                    resume_messages=resume_msgs,
                )
            )
            self._running_tasks[task_id] = bg_task

            if self._task_keeper is not None:
                try:
                    self._task_keeper(bg_task)
                except Exception as e:
                    logger.warning("task_keeper registration failed: {}", e)

            def _cleanup(_t: asyncio.Task) -> None:
                self._running_tasks.pop(task_id, None)

            bg_task.add_done_callback(_cleanup)

            logger.info("Follow-up resumed subagent [{}] (id: {}, max_iterations={}): {}",
                        meta.label, task_id, effective_max, message[:120])
            return (
                f"Subagent [{meta.label}] resumed (id: {task_id}, max_iterations={effective_max}). "
                f"I'll notify you when it completes."
            )

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
        subagent_session_key: str | None = None,
        parent_session_key: str | None = None,
    ) -> None:
        """Announce the subagent result to the parent session.

        Phase 30: Prefers SessionMessenger (inject into running parent, or
        trigger new execution).  Falls back to bus publish with
        ``session_key_override`` to fix the key mismatch bug.
        """
        status_text = "completed successfully" if status == "ok" else "failed"

        announce_content = f"""[Subagent Result Notification]
A previously spawned subagent '{label}' has {status_text}.

Original task: {task}

Subagent result:
{result}

Review this result in the context of your current session. Choose the appropriate response:
- If you were waiting for this result to continue a planned workflow, proceed accordingly.
- If the conversation has already moved on or the user has been informed, no output is needed.
- Do not repeat work that has already been done in this session.

(This is an automated system notification delivered as a user message for technical reasons. It is NOT a new user request. Do not execute the subagent's task again. Simply review the result and decide how to proceed in the context of your current conversation.)"""

        # Prefer SessionMessenger if available (Phase 30)
        if self.session_messenger and parent_session_key:
            try:
                await self.session_messenger.send_to_session(
                    target_session_key=parent_session_key,
                    content=announce_content,
                    source_session_key=subagent_session_key,
                )
                logger.debug("Subagent [{}] announced via SessionMessenger to {}",
                             task_id, parent_session_key)
                return
            except Exception as e:
                logger.warning("SessionMessenger failed, falling back to bus: {}", e)

        # Fallback: bus publish (legacy behavior, with session_key_override fix)
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
            session_key_override=parent_session_key,
        )

        await self.bus.publish_inbound(msg)
        logger.debug("Subagent [{}] announced via bus to {}:{} (override={})",
                      task_id, origin["channel"], origin["chat_id"], parent_session_key)

    def _build_subagent_prompt(self) -> str:
        """Build a focused system prompt for the subagent."""
        from nanobot.agent.context import ContextBuilder
        from nanobot.agent.skills import SkillsLoader

        time_ctx = ContextBuilder._build_runtime_context(None, None)
        parts = [f"""# Subagent

{time_ctx}

You are a subagent spawned by the main agent to complete a specific task.
Stay focused on the assigned task.

## Result Reporting

When you finish (i.e. your response contains no tool calls), your **final text reply is automatically sent back to the parent session** as a notification message. The parent agent will see it and may relay it to the user.

- Write your final reply as a **clear, concise summary** of what you accomplished (or what failed).
- Include key outcomes, file paths, or actionable information the parent needs.
- Do NOT include internal details like iteration counts or tool call logs.

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
