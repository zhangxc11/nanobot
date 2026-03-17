"""Tests for §51: Runtime Context injects Session ID."""

from __future__ import annotations

from pathlib import Path

from nanobot.agent.context import ContextBuilder


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    return workspace


# ── _build_runtime_context tests ──


def test_runtime_context_includes_session_id() -> None:
    """Session ID should appear in runtime context when provided."""
    ctx = ContextBuilder._build_runtime_context(
        channel="web", chat_id="1773250094", session_id="webchat_1773250094",
    )
    assert "Session ID: webchat_1773250094" in ctx
    assert "Channel: web" in ctx
    assert "Chat ID: 1773250094" in ctx


def test_runtime_context_session_id_without_channel() -> None:
    """Subagent scenario: session_id present but no channel/chat_id."""
    ctx = ContextBuilder._build_runtime_context(
        channel=None, chat_id=None,
        session_id="subagent_webchat_1773250094_a1b2c3d4",
    )
    assert "Session ID: subagent_webchat_1773250094_a1b2c3d4" in ctx
    assert "Channel:" not in ctx
    assert "Chat ID:" not in ctx


def test_runtime_context_no_session_id() -> None:
    """When session_id is None, no Session ID line should appear."""
    ctx = ContextBuilder._build_runtime_context(
        channel="cli", chat_id="direct",
    )
    assert "Session ID:" not in ctx
    assert "Channel: cli" in ctx
    assert "Chat ID: direct" in ctx


def test_runtime_context_feishu_routed_session() -> None:
    """Feishu routed session: session_id differs from channel_chatid."""
    ctx = ContextBuilder._build_runtime_context(
        channel="feishu.lab", chat_id="ou_xxx",
        session_id="feishu.lab.1772376517",
    )
    assert "Session ID: feishu.lab.1772376517" in ctx
    assert "Channel: feishu.lab" in ctx
    assert "Chat ID: ou_xxx" in ctx


# ── build_messages integration tests ──


def test_build_messages_includes_session_id(tmp_path) -> None:
    """build_messages should pass session_id through to runtime context."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[],
        current_message="hello",
        channel="web",
        chat_id="1773250094",
        session_id="webchat_1773250094",
    )

    # Runtime context is the second-to-last message
    runtime_msg = messages[-2]
    assert runtime_msg["role"] == "user"
    assert "Session ID: webchat_1773250094" in runtime_msg["content"]


def test_build_messages_without_session_id(tmp_path) -> None:
    """build_messages without session_id should not include Session ID line."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[],
        current_message="hello",
        channel="cli",
        chat_id="direct",
    )

    runtime_msg = messages[-2]
    assert "Session ID:" not in runtime_msg["content"]


# ── session_key to session_id conversion tests ──


def test_session_key_colon_to_underscore() -> None:
    """Verify the key.replace(':', '_') convention produces correct session_id."""
    cases = [
        ("webchat:1773250094", "webchat_1773250094"),
        ("feishu.lab.1772376517", "feishu.lab.1772376517"),  # no colon
        ("telegram:8281248569", "telegram_8281248569"),
        ("cli:direct", "cli_direct"),
        ("subagent:webchat_1773250094_a1b2", "subagent_webchat_1773250094_a1b2"),
    ]
    for key, expected_id in cases:
        assert key.replace(":", "_") == expected_id, f"Failed for key={key}"
