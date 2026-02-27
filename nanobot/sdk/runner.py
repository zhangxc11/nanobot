"""AgentRunner — high-level SDK for invoking the nanobot agent.

This module encapsulates all the boilerplate needed to set up an
``AgentLoop`` (config loading, provider creation, session management,
cron service, etc.) and exposes a simple ``run()`` method.

Thread-safety
-------------
``AgentRunner`` itself is *not* thread-safe — callers should create one
runner per thread, or serialise access.  However, the underlying
``UsageRecorder`` (SQLite WAL) is safe for concurrent writes from
multiple runners in separate threads.

Typical usage in web-chat Worker::

    runner = AgentRunner.from_config()
    result = await runner.run(
        message="Hello",
        session_key="web:abc123",
        callbacks=my_callbacks,
    )
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.callbacks import DefaultCallbacks
    from nanobot.agent.loop import AgentLoop


class AgentRunner:
    """High-level agent executor.

    Parameters
    ----------
    agent_loop:
        A fully-configured ``AgentLoop`` instance.
    """

    def __init__(self, agent_loop: AgentLoop):
        self._loop = agent_loop

    @classmethod
    def from_config(cls, config_path: str | None = None) -> AgentRunner:
        """Create an ``AgentRunner`` from the standard nanobot config.

        This mirrors the setup done in ``nanobot/cli/commands.py`` for the
        ``agent`` command, but packaged as a reusable factory.

        Parameters
        ----------
        config_path:
            Path to ``nanobot.yml``.  ``None`` uses the default search.
        """
        from nanobot.config.loader import load_config, get_data_dir
        from nanobot.bus.queue import MessageBus
        from nanobot.agent.loop import AgentLoop
        from nanobot.session.manager import SessionManager
        from nanobot.cron.service import CronService
        from nanobot.usage.recorder import UsageRecorder
        from nanobot.usage.detail_logger import LLMDetailLogger

        config = load_config(config_path)
        data_dir = get_data_dir()

        # Provider — reuse CLI's _make_provider which handles all provider types
        from nanobot.cli.commands import _make_provider
        provider = _make_provider(config)

        # Bus (unused in direct mode, but required by AgentLoop)
        bus = MessageBus()

        # Session manager — pass workspace root, not sessions_dir.
        # SessionManager internally appends "/sessions" to the workspace path.
        session_manager = SessionManager(config.workspace_path)

        # Cron service
        cron = CronService(data_dir / "cron")

        # Usage recorder
        usage_recorder = UsageRecorder()

        # LLM call detail logger
        detail_logger = LLMDetailLogger()

        # Audit logger for tool execution tracing
        from nanobot.audit.logger import AuditLogger
        audit_logger = AuditLogger()

        agent_loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=config.workspace_path,
            model=config.agents.defaults.model,
            temperature=config.agents.defaults.temperature,
            max_tokens=config.agents.defaults.max_tokens,
            max_iterations=config.agents.defaults.max_tool_iterations,
            memory_window=config.agents.defaults.memory_window,
            brave_api_key=config.tools.web.search.api_key or None,
            exec_config=config.tools.exec,
            cron_service=cron,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            session_manager=session_manager,
            mcp_servers=config.tools.mcp_servers,
            channels_config=config.channels,
            usage_recorder=usage_recorder,
            detail_logger=detail_logger,
            audit_logger=audit_logger,
        )

        logger.info("AgentRunner created (model={})", config.agents.defaults.model)
        return cls(agent_loop)

    async def run(
        self,
        message: str,
        session_key: str = "sdk:direct",
        channel: str = "web",
        chat_id: str = "sdk",
        media: list[str] | None = None,
        callbacks: DefaultCallbacks | None = None,
    ) -> str:
        """Execute one agent turn.

        Parameters
        ----------
        message:
            User message text.
        session_key:
            Session identifier (e.g. ``"web:abc123"``).
        channel:
            Channel name for tool context (default ``"web"``).
        chat_id:
            Chat ID for tool context.
        media:
            Optional list of local file paths for images/media attachments.
        callbacks:
            Optional ``DefaultCallbacks`` subclass to receive events.

        Returns
        -------
        str
            Final assistant response text.
        """
        return await self._loop.process_direct(
            content=message,
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            media=media,
            callbacks=callbacks,
        )

    async def close(self) -> None:
        """Release resources (MCP connections, etc.)."""
        await self._loop.close_mcp()
