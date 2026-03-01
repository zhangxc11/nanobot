"""Tests for /new and /flush session commands (Phase 12, updated Phase 21)."""

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
        table = {"feishu:ou_abc": "feishu.1700000000"}
        manager._save_routing(table)
        assert manager.resolve_session_key("feishu:ou_abc") == "feishu.1700000000"

    def test_unrelated_key_not_affected(self, manager):
        table = {"feishu:ou_abc": "feishu.1700000000"}
        manager._save_routing(table)
        assert manager.resolve_session_key("cli:direct") == "cli:direct"


# ── create_new_session ───────────────────────────────────────────


class TestCreateNewSession:
    def test_creates_new_session_file(self, manager):
        """After /new, a new session file with timestamped key should exist."""
        # Create an existing session with messages
        session = manager.get_or_create("cli:direct")
        session.add_message("user", "hello")
        manager.save(session)

        new_key = manager.create_new_session("cli", "direct", "cli:direct")

        # New key should be cli.<timestamp>
        assert new_key.startswith("cli.")
        assert new_key != "cli:direct"

        # New session file should exist and be empty (just metadata)
        new_path = manager._get_session_path(new_key)
        assert new_path.exists()
        new_session = manager.get_or_create(new_key)
        assert len(new_session.messages) == 0

    def test_old_session_stays_in_place(self, manager):
        """Old session file should NOT be renamed — it stays untouched."""
        session = manager.get_or_create("feishu.lab:ou_abc")
        session.add_message("user", "hello")
        manager.save(session)

        old_path = manager._get_session_path("feishu.lab:ou_abc")
        assert old_path.exists()

        manager.create_new_session("feishu.lab", "ou_abc", "feishu.lab:ou_abc")

        # Old file should still be there with original name
        assert old_path.exists()

        # Old file should still have the message
        with open(old_path, encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
        assert len(lines) >= 2
        msg = json.loads(lines[1])
        assert msg["content"] == "hello"

    def test_invalidates_cache(self, manager):
        """After /new, resolving the natural key should go to the new session."""
        session = manager.get_or_create("cli:direct")
        session.add_message("user", "cached message")
        manager.save(session)

        new_key = manager.create_new_session("cli", "direct", "cli:direct")

        # Routing should point to the new key
        resolved = manager.resolve_session_key("cli:direct")
        assert resolved == new_key

        # Getting the new session should return a fresh one
        new_session = manager.get_or_create(new_key)
        assert len(new_session.messages) == 0

    def test_updates_routing_entry(self, manager):
        """After /new, routing table should map natural_key → new_key."""
        session = manager.get_or_create("feishu.lab:ou_abc")
        session.add_message("user", "old message")
        manager.save(session)

        new_key = manager.create_new_session("feishu.lab", "ou_abc", "feishu.lab:ou_abc")

        # Routing should map natural key to new key
        assert manager.resolve_session_key("feishu.lab:ou_abc") == new_key
        assert new_key.startswith("feishu.lab.")

    def test_multiple_new_sessions(self, manager):
        """Multiple /new commands should create separate session files."""
        import time

        # Create initial session
        session = manager.get_or_create("cli:direct")
        session.add_message("user", "msg1")
        manager.save(session)
        new_key1 = manager.create_new_session("cli", "direct", "cli:direct")

        # Create second session with a message
        session2 = manager.get_or_create(new_key1)
        session2.add_message("user", "msg2")
        manager.save(session2)

        # Small delay to get different timestamp
        time.sleep(1.1)
        new_key2 = manager.create_new_session("cli", "direct", new_key1)

        # Keys should be different
        assert new_key1 != new_key2
        assert new_key1.startswith("cli.")
        assert new_key2.startswith("cli.")

        # Both session files should exist
        assert manager._get_session_path(new_key1).exists()
        assert manager._get_session_path(new_key2).exists()

        # Old original session file should still exist
        assert manager._get_session_path("cli:direct").exists()

        # Routing should point to the latest
        assert manager.resolve_session_key("cli:direct") == new_key2

    def test_new_session_from_routed_key(self, manager):
        """If old_key is already a routed key, /new should still work."""
        # Simulate: first /new created feishu.lab.100
        session = manager.get_or_create("feishu.lab.100")
        session.add_message("user", "msg in routed session")
        manager.save(session)

        table = {"feishu.lab:ou_abc": "feishu.lab.100"}
        manager._save_routing(table)

        # Second /new from the routed key
        new_key = manager.create_new_session("feishu.lab", "ou_abc", "feishu.lab.100")

        # Old routed session file should still exist
        assert manager._get_session_path("feishu.lab.100").exists()

        # New session should exist
        assert manager._get_session_path(new_key).exists()
        assert new_key != "feishu.lab.100"

        # Routing should point to the new key
        assert manager.resolve_session_key("feishu.lab:ou_abc") == new_key


# ── Routing table persistence ────────────────────────────────────


class TestRoutingPersistence:
    def test_save_and_load(self, manager):
        table = {"feishu:ou_abc": "feishu.123", "cli:direct": "cli.456"}
        manager._save_routing(table)

        loaded = manager._load_routing()
        assert loaded == table

    def test_empty_routing(self, manager):
        assert manager._load_routing() == {}

    def test_corrupt_routing_returns_empty(self, manager):
        manager._routing_path().write_text("not json!", encoding="utf-8")
        assert manager._load_routing() == {}
