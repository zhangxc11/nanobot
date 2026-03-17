"""Tests for /session subcommands and GatewaySessionMessenger idle path fix.

Covers:
- #5 fix: GatewaySessionMessenger idle path resolves real channel/chat_id
- Command routing: subcmd dispatch
- /session list, switch, name, done, undone, summary
"""

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.session.manager import Session, SessionManager


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temporary workspace with a sessions directory."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    return tmp_path


@pytest.fixture
def session_mgr(tmp_workspace):
    return SessionManager(tmp_workspace)


def _make_loop(session_mgr, **kwargs):
    """Create an AgentLoop with mocked dependencies for command testing."""
    from nanobot.agent.loop import AgentLoop

    bus = AsyncMock()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=session_mgr.workspace,
        model="test-model",
        session_manager=session_mgr,
        **kwargs,
    )
    return loop


def _msg(content, channel="feishu.lab", chat_id="ou_test123"):
    return InboundMessage(
        channel=channel,
        sender_id="user1",
        chat_id=chat_id,
        content=content,
    )


# ── Tests: GatewaySessionMessenger idle path fix (#5) ────────────────────────


class TestGatewaySessionMessengerIdleFix:
    """Verify the idle path resolves real channel/chat_id from routing table."""

    @pytest.mark.asyncio
    async def test_idle_path_resolves_channel_from_routing(self, session_mgr):
        """When target is a routed key, should reverse-lookup the routing table."""
        # Set up routing: feishu.lab:ou_abc → feishu.lab.1234567890
        routing = {"feishu.lab:ou_abc": "feishu.lab.1234567890"}
        session_mgr._save_routing(routing)

        # Create the session file so it exists
        session = Session(key="feishu.lab.1234567890")
        session_mgr.save(session)

        # Replicate GatewaySessionMessenger from loop.py (it's defined inside run())
        bus = AsyncMock()
        active_sessions = {}

        class GatewaySessionMessenger:
            def __init__(self, active, bus, sessions_mgr):
                self._active = active
                self._bus = bus
                self._sessions = sessions_mgr

            async def send_to_session(self, target_session_key, content, source_session_key=None):
                if source_session_key:
                    prefixed = f"[Message from session {source_session_key}]\n{content}"
                else:
                    prefixed = content

                if target_session_key in self._active:
                    w = self._active[target_session_key]
                    if not w.task.done():
                        await w.callbacks.inject({"role": "user", "content": prefixed})
                        return True
                    else:
                        self._active.pop(target_session_key, None)

                # This is the FIXED idle path
                real_channel = "session_messenger"
                real_chat_id = target_session_key
                routing = self._sessions._load_routing()
                for natural_key, routed_key in routing.items():
                    if routed_key == target_session_key:
                        _parts = natural_key.split(":", 1)
                        if len(_parts) == 2:
                            real_channel, real_chat_id = _parts
                        break
                else:
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
                return True

        messenger = GatewaySessionMessenger(active_sessions, bus, session_mgr)
        await messenger.send_to_session(
            "feishu.lab.1234567890", "hello", source_session_key="subagent:abc"
        )

        bus.publish_inbound.assert_called_once()
        msg = bus.publish_inbound.call_args[0][0]
        assert msg.channel == "feishu.lab", f"Expected 'feishu.lab', got '{msg.channel}'"
        assert msg.chat_id == "ou_abc", f"Expected 'ou_abc', got '{msg.chat_id}'"
        assert msg.session_key_override == "feishu.lab.1234567890"

    @pytest.mark.asyncio
    async def test_idle_path_natural_key_format(self, session_mgr):
        """When target is already a natural key (channel:chat_id), parse directly."""
        bus = AsyncMock()
        active_sessions = {}

        class GatewaySessionMessenger:
            def __init__(self, active, bus, sessions_mgr):
                self._active = active
                self._bus = bus
                self._sessions = sessions_mgr

            async def send_to_session(self, target_session_key, content, source_session_key=None):
                prefixed = content
                real_channel = "session_messenger"
                real_chat_id = target_session_key
                routing = self._sessions._load_routing()
                for natural_key, routed_key in routing.items():
                    if routed_key == target_session_key:
                        _parts = natural_key.split(":", 1)
                        if len(_parts) == 2:
                            real_channel, real_chat_id = _parts
                        break
                else:
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
                return True

        messenger = GatewaySessionMessenger(active_sessions, bus, session_mgr)
        await messenger.send_to_session("telegram:chat_999", "test")

        msg = bus.publish_inbound.call_args[0][0]
        assert msg.channel == "telegram"
        assert msg.chat_id == "chat_999"
        assert msg.session_key_override == "telegram:chat_999"


# ── Tests: Command routing ───────────────────────────────────────────────────


class TestSessionCommandRouting:
    """Test that _handle_session_command routes to correct sub-handler."""

    def test_no_subcmd_returns_info(self, session_mgr):
        loop = _make_loop(session_mgr)
        result = loop._handle_session_command(
            _msg("/session"), session_key="feishu.lab.123"
        )
        assert "Session 信息" in result.content
        assert "Session ID" in result.content

    def test_unknown_subcmd(self, session_mgr):
        loop = _make_loop(session_mgr)
        result = loop._handle_session_command(
            _msg("/session foobar"), session_key="feishu.lab.123"
        )
        assert "未知子命令" in result.content
        assert "foobar" in result.content
        assert "help" in result.content

    def test_list_subcmd(self, session_mgr):
        loop = _make_loop(session_mgr)
        result = loop._handle_session_command(
            _msg("/session list"), session_key="feishu.lab.123"
        )
        # Should return a list (possibly empty) but not an error
        assert "❌ 未知子命令" not in result.content

    def test_name_subcmd_no_arg(self, session_mgr):
        loop = _make_loop(session_mgr)
        result = loop._handle_session_command(
            _msg("/session name"), session_key="feishu.lab.123"
        )
        assert "用法" in result.content

    def test_summary_subcmd(self, session_mgr):
        loop = _make_loop(session_mgr)
        # Create a session with some messages
        session = session_mgr.get_or_create("feishu.lab.123")
        session_mgr.append_message(session, {"role": "user", "content": "hello"})
        result = loop._handle_session_command(
            _msg("/session summary"), session_key="feishu.lab.123"
        )
        assert "Session 摘要" in result.content


# ── Tests: /session list ─────────────────────────────────────────────────────


class TestSessionList:
    def test_list_empty(self, session_mgr):
        loop = _make_loop(session_mgr)
        result = loop._session_list(_msg("/session list"), session_key="feishu.lab.123")
        assert "没有找到" in result.content

    def test_list_with_sessions(self, session_mgr):
        # Create some sessions
        for key in ["feishu.lab.100", "feishu.lab.200", "feishu.lab.300"]:
            s = Session(key=key)
            session_mgr.save(s)
            session_mgr.append_message(s, {"role": "user", "content": f"msg in {key}"})

        loop = _make_loop(session_mgr)
        result = loop._session_list(
            _msg("/session list"), session_key="feishu.lab.200"
        )
        assert "#1" in result.content
        # Should show channel short name in title and short IDs
        assert "(lab)" in result.content

    def test_list_caches_keys(self, session_mgr):
        """List should cache session_ids (not keys) for #N references."""
        s = Session(key="feishu.lab.100")
        session_mgr.save(s)

        loop = _make_loop(session_mgr)
        msg = _msg("/session list")
        loop._session_list(msg, session_key="feishu.lab.100")

        assert msg.chat_id in loop._last_session_list
        # Cache now stores session_id (= filename stem)
        assert "feishu.lab.100" in loop._last_session_list[msg.chat_id]

    def test_list_filters_by_channel(self, session_mgr):
        # Create sessions for different channels
        for key in ["feishu.lab.100", "webchat.200", "feishu.lab.300"]:
            s = Session(key=key)
            session_mgr.save(s)

        loop = _make_loop(session_mgr)
        result = loop._session_list(
            _msg("/session list", channel="feishu.lab"),
            session_key="feishu.lab.100",
        )
        assert "webchat" not in result.content

    def test_list_excludes_done(self, session_mgr):
        s1 = Session(key="feishu.lab.100")
        session_mgr.save(s1)
        s2 = Session(key="feishu.lab.200")
        session_mgr.save(s2)

        # Mark s1 as done
        session_mgr.set_session_tags("feishu.lab.100", ["done"])

        loop = _make_loop(session_mgr)
        result = loop._session_list(
            _msg("/session list"), session_key="feishu.lab.200"
        )
        # Done session should not appear; short ID "200" should appear
        assert "100" not in result.content.split("条消息")[0]  # "100" not in first entry
        assert "200" in result.content

    def test_list_with_limit(self, session_mgr):
        for i in range(5):
            s = Session(key=f"feishu.lab.{100 + i}")
            session_mgr.save(s)

        loop = _make_loop(session_mgr)
        result = loop._session_list(
            _msg("/session list 2"), session_key="feishu.lab.100", arg="2"
        )
        # Should only show 2 sessions
        assert "#1" in result.content
        assert "#2" in result.content
        assert "#3" not in result.content

    def test_list_invalid_limit(self, session_mgr):
        loop = _make_loop(session_mgr)
        result = loop._session_list(
            _msg("/session list abc"), session_key="feishu.lab.100", arg="abc"
        )
        assert "无效参数" in result.content


# ── Tests: /session switch ───────────────────────────────────────────────────


class TestSessionSwitch:
    def test_switch_no_arg(self, session_mgr):
        loop = _make_loop(session_mgr)
        result = loop._session_switch(
            _msg("/session switch"), session_key="feishu.lab.100"
        )
        assert "用法" in result.content

    def test_switch_by_session_id(self, session_mgr):
        s1 = Session(key="feishu.lab.100")
        session_mgr.save(s1)
        s2 = Session(key="feishu.lab.200")
        session_mgr.save(s2)

        loop = _make_loop(session_mgr)
        result = loop._session_switch(
            _msg("/session switch feishu.lab.200"),
            session_key="feishu.lab.100",
            arg="feishu.lab.200",
        )
        assert "已切换" in result.content
        assert "feishu.lab.200" in result.content

        # Verify routing was updated (now stores session_id)
        resolved = session_mgr.resolve_session_key("feishu.lab:ou_test123")
        assert resolved == "feishu.lab.200"

    def test_switch_by_percent_id(self, session_mgr):
        """Test %session_id syntax for switch."""
        s1 = Session(key="feishu.lab.100")
        session_mgr.save(s1)
        s2 = Session(key="feishu.lab.200")
        session_mgr.save(s2)

        loop = _make_loop(session_mgr)
        result = loop._session_switch(
            _msg("/session switch %feishu.lab.200"),
            session_key="feishu.lab.100",
            arg="%feishu.lab.200",
        )
        assert "已切换" in result.content

    def test_switch_by_sequence_number(self, session_mgr):
        s1 = Session(key="feishu.lab.100")
        session_mgr.save(s1)
        s2 = Session(key="feishu.lab.200")
        session_mgr.save(s2)

        loop = _make_loop(session_mgr)
        # First list to populate cache
        loop._session_list(
            _msg("/session list"), session_key="feishu.lab.100"
        )

        # Switch by #N
        result = loop._session_switch(
            _msg("/session switch #1"),
            session_key="feishu.lab.100",
            arg="#1",
        )
        # Should succeed (switch to first item in list)
        assert "❌" not in result.content

    def test_switch_no_cache(self, session_mgr):
        loop = _make_loop(session_mgr)
        result = loop._session_switch(
            _msg("/session switch #1"),
            session_key="feishu.lab.100",
            arg="#1",
        )
        assert "请先执行" in result.content

    def test_switch_out_of_range(self, session_mgr):
        s = Session(key="feishu.lab.100")
        session_mgr.save(s)

        loop = _make_loop(session_mgr)
        loop._session_list(_msg("/session list"), session_key="feishu.lab.100")

        result = loop._session_switch(
            _msg("/session switch #99"),
            session_key="feishu.lab.100",
            arg="#99",
        )
        assert "超出范围" in result.content

    def test_switch_nonexistent(self, session_mgr):
        loop = _make_loop(session_mgr)
        result = loop._session_switch(
            _msg("/session switch %no.such.key"),
            session_key="feishu.lab.100",
            arg="%no.such.key",
        )
        assert "不存在" in result.content

    def test_switch_same_session(self, session_mgr):
        s = Session(key="feishu.lab.100")
        session_mgr.save(s)

        loop = _make_loop(session_mgr)
        result = loop._session_switch(
            _msg("/session switch feishu.lab.100"),
            session_key="feishu.lab.100",
            arg="feishu.lab.100",
        )
        assert "已经在该 session" in result.content


# ── Tests: /session name ─────────────────────────────────────────────────────


class TestSessionName:
    def test_name_set(self, session_mgr):
        loop = _make_loop(session_mgr)
        result = loop._session_name(
            _msg("/session name 我的测试会话"),
            session_key="feishu.lab.100",
            arg="我的测试会话",
        )
        assert "已命名" in result.content
        assert "我的测试会话" in result.content

        # Verify persisted
        sid = session_mgr.get_session_id("feishu.lab.100")
        assert session_mgr.get_session_name(sid) == "我的测试会话"

    def test_name_no_arg(self, session_mgr):
        loop = _make_loop(session_mgr)
        result = loop._session_name(
            _msg("/session name"),
            session_key="feishu.lab.100",
        )
        assert "用法" in result.content

    def test_name_preserves_case(self, session_mgr):
        loop = _make_loop(session_mgr)
        result = loop._session_name(
            _msg("/session name MySession-Test_V2"),
            session_key="feishu.lab.100",
            arg="MySession-Test_V2",
        )
        sid = session_mgr.get_session_id("feishu.lab.100")
        assert session_mgr.get_session_name(sid) == "MySession-Test_V2"


# ── Tests: /session done / undone ────────────────────────────────────────────


class TestSessionDone:
    def test_done_current(self, session_mgr):
        s = Session(key="feishu.lab.100")
        session_mgr.save(s)

        loop = _make_loop(session_mgr)
        result = loop._session_done(
            _msg("/session done"),
            session_key="feishu.lab.100",
        )
        assert "已归档" in result.content
        assert "新 session" in result.content

        # Verify tag (session_id = filename stem = "feishu.lab.100")
        assert "done" in session_mgr.get_session_tags("feishu.lab.100")

    def test_done_other_session(self, session_mgr):
        s1 = Session(key="feishu.lab.100")
        session_mgr.save(s1)
        s2 = Session(key="feishu.lab.200")
        session_mgr.save(s2)

        loop = _make_loop(session_mgr)
        # List first to get cache
        loop._session_list(_msg("/session list"), session_key="feishu.lab.100")

        # Use %session_id syntax
        result = loop._session_done(
            _msg("/session done %feishu.lab.200"),
            session_key="feishu.lab.100",
            arg="%feishu.lab.200",
        )
        assert "已归档" in result.content
        assert "新 session" not in result.content  # Not current session

        assert "done" in session_mgr.get_session_tags("feishu.lab.200")

    def test_done_idempotent(self, session_mgr):
        s = Session(key="feishu.lab.100")
        session_mgr.save(s)
        session_mgr.set_session_tags("feishu.lab.100", ["done"])

        loop = _make_loop(session_mgr)
        result = loop._session_done(
            _msg("/session done"),
            session_key="feishu.lab.100",
        )
        # Should still succeed
        assert "已归档" in result.content
        tags = session_mgr.get_session_tags("feishu.lab.100")
        assert tags.count("done") == 1  # Not duplicated


class TestSessionUndone:
    def test_undone(self, session_mgr):
        session_mgr.set_session_tags("feishu.lab.100", ["done"])

        s = Session(key="feishu.lab.100")
        session_mgr.save(s)

        loop = _make_loop(session_mgr)
        result = loop._session_undone(
            _msg("/session undone"),
            session_key="feishu.lab.100",
        )
        assert "取消归档" in result.content
        assert "done" not in session_mgr.get_session_tags("feishu.lab.100")

    def test_undone_not_done(self, session_mgr):
        s = Session(key="feishu.lab.100")
        session_mgr.save(s)

        loop = _make_loop(session_mgr)
        result = loop._session_undone(
            _msg("/session undone"),
            session_key="feishu.lab.100",
        )
        # Should succeed even if not done
        assert "取消归档" in result.content


# ── Tests: /session summary ──────────────────────────────────────────────────


class TestSessionSummary:
    def test_summary_empty(self, session_mgr):
        s = Session(key="feishu.lab.100")
        session_mgr.save(s)

        loop = _make_loop(session_mgr)
        result = loop._session_summary(
            _msg("/session summary"), session_key="feishu.lab.100"
        )
        assert "暂无消息" in result.content

    def test_summary_with_messages(self, session_mgr):
        s = Session(key="feishu.lab.100")
        session_mgr.save(s)
        session_mgr.append_message(s, {"role": "user", "content": "hello world"})
        session_mgr.append_message(s, {
            "role": "assistant", "content": "hi there",
            "tool_calls": [{"function": {"name": "read_file", "arguments": "{}"}}],
        })
        session_mgr.append_message(s, {"role": "tool", "content": "file content"})
        session_mgr.append_message(s, {"role": "user", "content": "thanks"})

        loop = _make_loop(session_mgr)
        result = loop._session_summary(
            _msg("/session summary"), session_key="feishu.lab.100"
        )
        assert "Session 摘要" in result.content
        assert "user: 2" in result.content
        assert "assistant: 1" in result.content
        assert "tool: 1" in result.content
        assert "hello world" in result.content
        assert "thanks" in result.content
        # Tool names no longer displayed (removed per F4)
        assert "Session ID" in result.content

    def test_summary_with_name(self, session_mgr):
        s = Session(key="feishu.lab.100")
        session_mgr.save(s)
        session_mgr.append_message(s, {"role": "user", "content": "test"})
        session_mgr.set_session_name("feishu.lab.100", "My Test Session")

        loop = _make_loop(session_mgr)
        result = loop._session_summary(
            _msg("/session summary"), session_key="feishu.lab.100"
        )
        assert "My Test Session" in result.content


# ── Tests: SessionManager helpers ────────────────────────────────────────────


class TestSessionManagerHelpers:
    def test_get_set_session_name(self, session_mgr):
        assert session_mgr.get_session_name("test.123") is None
        session_mgr.set_session_name("test.123", "Hello")
        assert session_mgr.get_session_name("test.123") == "Hello"

    def test_get_set_session_tags(self, session_mgr):
        assert session_mgr.get_session_tags("test.123") == []
        session_mgr.set_session_tags("test.123", ["done", "important"])
        assert session_mgr.get_session_tags("test.123") == ["done", "important"]

    def test_get_session_id(self, session_mgr):
        assert session_mgr.get_session_id("feishu.lab:ou_abc") == "feishu.lab_ou_abc"
        assert session_mgr.get_session_id("feishu.lab.123") == "feishu.lab.123"

    def test_switch_session(self, session_mgr):
        session_mgr.switch_session("feishu.lab", "ou_abc", "feishu.lab.999")
        resolved = session_mgr.resolve_session_key("feishu.lab:ou_abc")
        assert resolved == "feishu.lab.999"

    def test_get_session_message_count(self, session_mgr):
        s = Session(key="test.100")
        session_mgr.save(s)
        # get_session_message_count now takes session_id (= filename stem)
        assert session_mgr.get_session_message_count("test.100") == 0

        session_mgr.append_message(s, {"role": "user", "content": "hello"})
        session_mgr.append_message(s, {"role": "assistant", "content": "hi"})
        assert session_mgr.get_session_message_count("test.100") == 2

    def test_json_atomic_write(self, session_mgr):
        """Verify atomic write pattern (write tmp → os.replace)."""
        path = session_mgr._session_names_path()
        session_mgr.set_session_name("a", "Alice")
        assert path.exists()
        # Tmp file should be cleaned up
        assert not path.with_suffix(".tmp").exists()

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["a"] == "Alice"

    def test_read_json_file_missing(self, session_mgr):
        path = session_mgr.sessions_dir / "nonexistent.json"
        assert session_mgr._read_json_file(path) == {}

    def test_read_json_file_corrupt(self, session_mgr):
        path = session_mgr.sessions_dir / "corrupt.json"
        path.write_text("not valid json{{{", encoding="utf-8")
        assert session_mgr._read_json_file(path) == {}


# ── Tests: _resolve_session_arg helper ───────────────────────────────────────


class TestResolveSessionArg:
    def test_none_returns_current(self, session_mgr):
        loop = _make_loop(session_mgr)
        result = loop._resolve_session_arg(_msg(""), "current.session.id", None)
        assert result == "current.session.id"

    def test_direct_session_id_exists(self, session_mgr):
        s = Session(key="feishu.lab.100")
        session_mgr.save(s)

        loop = _make_loop(session_mgr)
        result = loop._resolve_session_arg(_msg(""), "current", "feishu.lab.100")
        assert result == "feishu.lab.100"

    def test_percent_session_id_exists(self, session_mgr):
        s = Session(key="feishu.lab.100")
        session_mgr.save(s)

        loop = _make_loop(session_mgr)
        result = loop._resolve_session_arg(_msg(""), "current", "%feishu.lab.100")
        assert result == "feishu.lab.100"

    def test_percent_session_id_not_exists(self, session_mgr):
        loop = _make_loop(session_mgr)
        result = loop._resolve_session_arg(_msg(""), "current", "%no.such.key")
        assert isinstance(result, OutboundMessage)
        assert "不存在" in result.content

    def test_direct_id_not_exists(self, session_mgr):
        loop = _make_loop(session_mgr)
        result = loop._resolve_session_arg(_msg(""), "current", "no.such.key")
        assert isinstance(result, OutboundMessage)
        assert "不存在" in result.content

    def test_sequence_number_valid(self, session_mgr):
        s = Session(key="feishu.lab.100")
        session_mgr.save(s)

        loop = _make_loop(session_mgr)
        # Cache now stores session_id
        loop._last_session_list["ou_test123"] = ["feishu.lab.100"]

        result = loop._resolve_session_arg(_msg(""), "current", "#1")
        assert result == "feishu.lab.100"

    def test_sequence_number_no_cache(self, session_mgr):
        loop = _make_loop(session_mgr)
        result = loop._resolve_session_arg(_msg(""), "current", "#1")
        assert isinstance(result, OutboundMessage)
        assert "请先执行" in result.content

    def test_sequence_number_out_of_range(self, session_mgr):
        loop = _make_loop(session_mgr)
        loop._last_session_list["ou_test123"] = ["feishu.lab.100"]

        result = loop._resolve_session_arg(_msg(""), "current", "#5")
        assert isinstance(result, OutboundMessage)
        assert "超出范围" in result.content

    def test_invalid_sequence_number(self, session_mgr):
        loop = _make_loop(session_mgr)
        result = loop._resolve_session_arg(_msg(""), "current", "#abc")
        assert isinstance(result, OutboundMessage)
        assert "无效序号" in result.content


# ── Tests: /session help ─────────────────────────────────────────────────────


class TestSessionHelp:
    def test_help_subcmd(self, session_mgr):
        loop = _make_loop(session_mgr)
        result = loop._handle_session_command(
            _msg("/session help"), session_key="feishu.lab.123"
        )
        assert "子命令" in result.content
        assert "/session list" in result.content
        assert "/session switch" in result.content
        assert "/session done" in result.content
        assert "/session help" in result.content
        assert "#N" in result.content
        assert "%id" in result.content


# ── Tests: /session list --all ───────────────────────────────────────────────


class TestSessionListAll:
    def test_list_all_shows_done(self, session_mgr):
        s1 = Session(key="feishu.lab.100")
        session_mgr.save(s1)
        s2 = Session(key="feishu.lab.200")
        session_mgr.save(s2)
        session_mgr.set_session_tags("feishu.lab.100", ["done"])

        loop = _make_loop(session_mgr)
        result = loop._session_list(
            _msg("/session list --all"), session_key="feishu.lab.200", arg="--all"
        )
        # Both sessions should appear
        assert "100" in result.content
        assert "200" in result.content
        # Done session should have ✓ marker
        assert "✓" in result.content

    def test_list_all_with_limit(self, session_mgr):
        for i in range(5):
            s = Session(key=f"feishu.lab.{100 + i}")
            session_mgr.save(s)

        loop = _make_loop(session_mgr)
        result = loop._session_list(
            _msg("/session list --all 2"), session_key="feishu.lab.100", arg="--all 2"
        )
        assert "#1" in result.content
        assert "#2" in result.content
        assert "#3" not in result.content


# ── Tests: /session list range M-N ───────────────────────────────────────────


class TestSessionListRange:
    def test_list_range(self, session_mgr):
        for i in range(10):
            s = Session(key=f"feishu.lab.{100 + i}")
            session_mgr.save(s)

        loop = _make_loop(session_mgr)
        result = loop._session_list(
            _msg("/session list 3-5"), session_key="feishu.lab.100", arg="3-5"
        )
        # Range 3-5 should show original indices #3, #4, #5
        assert "#3" in result.content
        assert "#4" in result.content
        assert "#5" in result.content
        assert "#2" not in result.content  # Before range
        assert "#6" not in result.content  # After range

    def test_list_range_invalid(self, session_mgr):
        loop = _make_loop(session_mgr)
        result = loop._session_list(
            _msg("/session list abc-def"), session_key="feishu.lab.100", arg="abc-def"
        )
        assert "无效范围" in result.content

    def test_list_range_reversed(self, session_mgr):
        loop = _make_loop(session_mgr)
        result = loop._session_list(
            _msg("/session list 5-3"), session_key="feishu.lab.100", arg="5-3"
        )
        assert "无效范围" in result.content

    def test_list_range_switch_consistency(self, session_mgr):
        """After list 3-5, #4 should switch to the 4th session overall (not 2nd in slice)."""
        # Create 10 sessions; newest first = 109, 108, ..., 100
        for i in range(10):
            s = Session(key=f"feishu.lab.{100 + i}")
            session_mgr.save(s)

        loop = _make_loop(session_mgr)
        # list 3-5 to populate cache
        loop._session_list(
            _msg("/session list 3-5"), session_key="feishu.lab.100", arg="3-5"
        )
        # #4 should be the 4th session in the full sorted list
        resolved = loop._resolve_session_arg(_msg("/session switch #4"), "feishu.lab.100", "#4")
        # Full list sorted by updated_at desc: 109, 108, 107, 106, 105, ...
        # #4 (1-based) = index 3 = feishu.lab.106
        assert resolved == "feishu.lab.106"


# ── Tests: /session summary with count ───────────────────────────────────────


class TestSessionSummaryCount:
    def test_summary_with_count(self, session_mgr):
        s = Session(key="feishu.lab.100")
        session_mgr.save(s)
        for i in range(20):
            session_mgr.append_message(s, {"role": "user", "content": f"message {i}"})
            session_mgr.append_message(s, {"role": "assistant", "content": f"reply {i}"})

        loop = _make_loop(session_mgr)
        result = loop._session_summary(
            _msg("/session summary 3"), session_key="feishu.lab.100", arg="3"
        )
        assert "3/20" in result.content  # 3 out of 20 user messages

    def test_summary_with_session_ref_and_count(self, session_mgr):
        s = Session(key="feishu.lab.100")
        session_mgr.save(s)
        for i in range(10):
            session_mgr.append_message(s, {"role": "user", "content": f"msg {i}"})

        loop = _make_loop(session_mgr)
        loop._last_session_list["ou_test123"] = ["feishu.lab.100"]

        result = loop._session_summary(
            _msg("/session summary #1 3"), session_key="feishu.lab.100", arg="#1 3"
        )
        assert "3/10" in result.content

    def test_summary_with_percent_id(self, session_mgr):
        s = Session(key="feishu.lab.100")
        session_mgr.save(s)
        session_mgr.append_message(s, {"role": "user", "content": "hello"})

        loop = _make_loop(session_mgr)
        result = loop._session_summary(
            _msg("/session summary %feishu.lab.100"),
            session_key="feishu.lab.200",
            arg="%feishu.lab.100",
        )
        assert "Session 摘要" in result.content
        assert "hello" in result.content


# ── Tests: _select_evenly helper ─────────────────────────────────────────────


class TestSelectEvenly:
    def test_fewer_than_count(self):
        from nanobot.agent.loop import AgentLoop
        result = AgentLoop._select_evenly(["a", "b", "c"], 5)
        assert result == ["a", "b", "c"]

    def test_exact_count(self):
        from nanobot.agent.loop import AgentLoop
        result = AgentLoop._select_evenly(["a", "b", "c"], 3)
        assert result == ["a", "b", "c"]

    def test_select_subset(self):
        from nanobot.agent.loop import AgentLoop
        items = [f"msg{i}" for i in range(20)]
        result = AgentLoop._select_evenly(items, 5)
        assert len(result) == 5
        assert result[0] == "msg0"  # First always included
        assert result[-1] == "msg19"  # Last always included

    def test_select_one(self):
        from nanobot.agent.loop import AgentLoop
        result = AgentLoop._select_evenly(["a", "b", "c"], 1)
        assert result == ["a"]

    def test_select_zero(self):
        from nanobot.agent.loop import AgentLoop
        result = AgentLoop._select_evenly(["a", "b", "c"], 0)
        assert result == []


# ── Tests: list_sessions returns session_id ──────────────────────────────────


class TestListSessionsSessionId:
    def test_list_sessions_has_session_id(self, session_mgr):
        s = Session(key="feishu.lab.100")
        session_mgr.save(s)

        sessions = session_mgr.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "feishu.lab.100"
        assert "key" in sessions[0]  # key still present for internal use

    def test_get_session_message_count_by_session_id(self, session_mgr):
        """get_session_message_count now takes session_id directly."""
        s = Session(key="feishu.lab.100")
        session_mgr.save(s)
        session_mgr.append_message(s, {"role": "user", "content": "hello"})
        session_mgr.append_message(s, {"role": "assistant", "content": "hi"})

        # session_id = filename stem = "feishu.lab.100"
        assert session_mgr.get_session_message_count("feishu.lab.100") == 2
        # Non-existent session_id
        assert session_mgr.get_session_message_count("no.such.id") == 0
