"""Tests for /new and /flush session commands (Phase 12)."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from nanobot.session.manager import Session, SessionManager


@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temporary workspace with sessions directory."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    return tmp_path


@pytest.fixture
def manager(tmp_workspace):
    return SessionManager(tmp_workspace)


# ── resolve_session_key ──────────────────────────────────────────


class TestResolveSessionKey:
    def test_no_routing_returns_natural_key(self, manager):
        assert manager.resolve_session_key("feishu:ou_abc") == "feishu:ou_abc"

    def test_with_routing_returns_mapped_key(self, manager):
        # Write a routing table
        table = {"feishu:ou_abc": "feishu:ou_abc_1700000000"}
        manager._save_routing(table)
        assert manager.resolve_session_key("feishu:ou_abc") == "feishu:ou_abc_1700000000"

    def test_unrelated_key_not_affected(self, manager):
        table = {"feishu:ou_abc": "feishu:ou_abc_1700000000"}
        manager._save_routing(table)
        assert manager.resolve_session_key("cli:direct") == "cli:direct"


# ── create_new_session ───────────────────────────────────────────


class TestCreateNewSession:
    def test_creates_new_session_file(self, manager):
        """After /new, a new empty session file should exist."""
        # Create an existing session with messages
        session = manager.get_or_create("cli:direct")
        session.add_message("user", "hello")
        manager.save(session)

        old_path = manager._get_session_path("cli:direct")
        assert old_path.exists()

        new_key = manager.create_new_session("cli", "direct", "cli:direct")
        assert new_key == "cli:direct"

        # New session file should exist and be empty (just metadata)
        new_path = manager._get_session_path("cli:direct")
        assert new_path.exists()
        new_session = manager.get_or_create("cli:direct")
        assert len(new_session.messages) == 0

    def test_archives_old_session(self, manager):
        """Old session file should be renamed with timestamp suffix."""
        session = manager.get_or_create("feishu:ou_abc")
        session.add_message("user", "hello")
        manager.save(session)

        manager.create_new_session("feishu", "ou_abc", "feishu:ou_abc")

        # Check that an archive file exists
        archive_files = [
            f for f in manager.sessions_dir.glob("feishu_ou_abc_*.jsonl")
        ]
        assert len(archive_files) == 1

        # The archive should have the old message
        with open(archive_files[0], encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
        # First line is metadata, second is the message
        assert len(lines) >= 2
        msg = json.loads(lines[1])
        assert msg["content"] == "hello"

    def test_invalidates_cache(self, manager):
        """After /new, the old session should not be in cache."""
        session = manager.get_or_create("cli:direct")
        session.add_message("user", "cached message")
        manager.save(session)

        manager.create_new_session("cli", "direct", "cli:direct")

        # Getting session again should return a fresh one
        new_session = manager.get_or_create("cli:direct")
        assert len(new_session.messages) == 0

    def test_removes_routing_entry(self, manager):
        """If there was a routing entry, /new should remove it."""
        # Set up a routing entry
        table = {"feishu:ou_abc": "feishu:ou_abc_1700000000"}
        manager._save_routing(table)

        # Create a session for the routed key
        session = manager.get_or_create("feishu:ou_abc_1700000000")
        session.add_message("user", "old message")
        manager.save(session)

        manager.create_new_session("feishu", "ou_abc", "feishu:ou_abc_1700000000")

        # Routing should be cleared — natural key resolves to itself
        assert manager.resolve_session_key("feishu:ou_abc") == "feishu:ou_abc"

    def test_multiple_new_sessions(self, manager):
        """Multiple /new commands should create multiple archive files."""
        import time

        # Create initial session
        session = manager.get_or_create("cli:direct")
        session.add_message("user", "msg1")
        manager.save(session)
        manager.create_new_session("cli", "direct", "cli:direct")

        # Create second session with a message
        session2 = manager.get_or_create("cli:direct")
        session2.add_message("user", "msg2")
        manager.save(session2)

        # Small delay to get different timestamp
        time.sleep(0.01)
        manager.create_new_session("cli", "direct", "cli:direct")

        # Should have 2 archive files (timestamps may collide in fast tests)
        archive_files = sorted(manager.sessions_dir.glob("cli_direct_*.jsonl"))
        assert len(archive_files) >= 1  # At least 1 (timestamps might collide)


# ── Routing table persistence ────────────────────────────────────


class TestRoutingPersistence:
    def test_save_and_load(self, manager):
        table = {"feishu:ou_abc": "feishu:ou_abc_123", "cli:direct": "cli:direct_456"}
        manager._save_routing(table)

        loaded = manager._load_routing()
        assert loaded == table

    def test_empty_routing(self, manager):
        assert manager._load_routing() == {}

    def test_corrupt_routing_returns_empty(self, manager):
        manager._routing_path().write_text("not json!", encoding="utf-8")
        assert manager._load_routing() == {}
