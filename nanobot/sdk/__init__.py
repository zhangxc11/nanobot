"""nanobot SDK — programmatic agent invocation.

Provides ``AgentRunner``, the primary entry-point for calling the nanobot
agent from Python code (e.g. web-chat Worker, custom integrations).

Quick start::

    from nanobot.sdk import AgentRunner

    runner = AgentRunner.from_config()
    result = await runner.run("Hello!", session_key="web:my-session")
    print(result)  # final assistant response text
"""

from nanobot.sdk.runner import AgentRunner

__all__ = ["AgentRunner"]
