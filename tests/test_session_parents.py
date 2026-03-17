"""Tests for session parent-child relationship resolution (§53)."""

import json
import pytest
from pathlib import Path

from nanobot.session.parents import (
    build_parent_map,
    is_child_of,
    load_manual_overrides,
    resolve_parent,
)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def sessions_dir(tmp_path):
    """Create a temporary sessions directory with sample .jsonl files."""
    d = tmp_path / "sessions"
    d.mkdir()
    return d


def _create_sessions(sessions_dir: Path, session_ids: list[str]):
    """Create empty .jsonl files for the given session IDs."""
    for sid in session_ids:
        (sessions_dir / f"{sid}.jsonl").touch()


def _write_parents_json(sessions_dir: Path, data: dict):
    """Write session_parents.json to the sessions directory."""
    with open(sessions_dir / "session_parents.json", "w") as f:
        json.dump(data, f)


# ── Tests: load_manual_overrides ─────────────────────────────────────


class TestLoadManualOverrides:
    def test_no_file(self, sessions_dir):
        """Returns empty dict when session_parents.json doesn't exist."""
        result = load_manual_overrides(sessions_dir)
        assert result == {}

    def test_filters_underscore_keys(self, sessions_dir):
        """Keys starting with '_' are filtered out."""
        _write_parents_json(sessions_dir, {
            "_comment": "this is a comment",
            "_metadata": {"version": 1},
            "webchat_child_1": "webchat_parent_1",
            "webchat_child_2": "webchat_parent_2",
        })
        result = load_manual_overrides(sessions_dir)
        assert result == {
            "webchat_child_1": "webchat_parent_1",
            "webchat_child_2": "webchat_parent_2",
        }

    def test_filters_non_string_values(self, sessions_dir):
        """Non-string values are filtered out."""
        _write_parents_json(sessions_dir, {
            "valid_child": "valid_parent",
            "bad_child": 123,
            "bad_child2": None,
        })
        result = load_manual_overrides(sessions_dir)
        assert result == {"valid_child": "valid_parent"}

    def test_invalid_json(self, sessions_dir):
        """Returns empty dict for invalid JSON."""
        (sessions_dir / "session_parents.json").write_text("not json {{{")
        result = load_manual_overrides(sessions_dir)
        assert result == {}

    def test_json_not_object(self, sessions_dir):
        """Returns empty dict when JSON is not an object."""
        (sessions_dir / "session_parents.json").write_text('["a", "b"]')
        result = load_manual_overrides(sessions_dir)
        assert result == {}


# ── Tests: resolve_parent — subagent heuristic ───────────────────────


class TestSubagentHeuristic:
    def test_basic_subagent(self):
        """subagent_{parent}_{8hex} resolves to parent."""
        all_ids = {"webchat_1772696251", "subagent_webchat_1772696251_abc12345"}
        result = resolve_parent("subagent_webchat_1772696251_abc12345", all_ids, {})
        assert result == "webchat_1772696251"

    def test_subagent_parent_not_in_sessions(self):
        """Subagent parent is returned even if parent doesn't exist in all_session_ids."""
        all_ids = {"subagent_webchat_1772696251_abc12345"}
        result = resolve_parent("subagent_webchat_1772696251_abc12345", all_ids, {})
        assert result == "webchat_1772696251"

    def test_subagent_format_not_matching(self):
        """Non-matching subagent format returns None."""
        all_ids = {"subagent_nohex"}
        # No 8-hex suffix
        result = resolve_parent("subagent_nohex", all_ids, {})
        assert result is None

    def test_subagent_with_complex_parent(self):
        """Subagent with multi-segment parent ID."""
        all_ids = {
            "feishu.lab_ou_abc_1772696251",
            "subagent_feishu.lab_ou_abc_1772696251_deadbeef",
        }
        result = resolve_parent(
            "subagent_feishu.lab_ou_abc_1772696251_deadbeef", all_ids, {}
        )
        assert result == "feishu.lab_ou_abc_1772696251"

    def test_subagent_hex_must_be_lowercase(self):
        """8-hex suffix must be lowercase hex characters."""
        all_ids = {"subagent_parent_ABCD1234"}
        # Uppercase hex should not match the pattern [0-9a-f]
        result = resolve_parent("subagent_parent_ABCD1234", all_ids, {})
        assert result is None


# ── Tests: resolve_parent — webchat API session heuristic ────────────


class TestWebchatHeuristic:
    def test_root_match_priority_a(self):
        """Priority a: root session (no 10-digit number in prefix)."""
        all_ids = {
            "webchat_1772696251",                        # root parent
            "webchat_dispatch_1772696251_1772700001",    # child (dispatch)
        }
        result = resolve_parent(
            "webchat_dispatch_1772696251_1772700001", all_ids, {}
        )
        assert result == "webchat_1772696251"

    def test_three_level_tree_dispatch_to_root(self):
        """Dispatch session resolves to root session."""
        all_ids = {
            "webchat_1772696251",
            "webchat_qa_dispatch_1772696251",
            "webchat_qa_worker_1772696251_task1",
        }
        result = resolve_parent(
            "webchat_qa_dispatch_1772696251", all_ids, {}
        )
        assert result == "webchat_1772696251"

    def test_three_level_tree_worker_to_root(self):
        """Worker session resolves to root (shortest match in priority a)."""
        all_ids = {
            "webchat_1772696251",
            "webchat_qa_dispatch_1772696251",
            "webchat_qa_worker_1772696251_task1",
        }
        # Worker's first 10-digit ts is 1772696251 → matches root (shortest)
        result = resolve_parent(
            "webchat_qa_worker_1772696251_task1", all_ids, {}
        )
        assert result == "webchat_1772696251"

    def test_priority_b_suffix_match(self):
        """Priority b: suffix match when no root session exists."""
        all_ids = {
            "webchat_qa_dispatch_1772696251",            # intermediate parent
            "webchat_qa_worker_1772696251_1772700001",   # child
        }
        # Child's first 10-digit ts is 1772696251
        # No root session exists, but dispatch ends with _1772696251
        # However dispatch prefix "webchat_qa_dispatch" has no 10-digit number → priority a matches
        result = resolve_parent(
            "webchat_qa_worker_1772696251_1772700001", all_ids, {}
        )
        assert result == "webchat_qa_dispatch_1772696251"

    def test_pure_timestamp_session_is_root(self):
        """webchat_{10digits} is a root session — no parent."""
        all_ids = {"webchat_1772696251"}
        result = resolve_parent("webchat_1772696251", all_ids, {})
        # suffix is all digits → not an API session → no heuristic applied
        assert result is None

    def test_non_webchat_prefix_skipped(self):
        """Non-webchat sessions skip the webchat heuristic."""
        all_ids = {"cli_1772696251", "cli_worker_1772696251_task1"}
        result = resolve_parent("cli_worker_1772696251_task1", all_ids, {})
        assert result is None

    def test_cross_channel_parent(self):
        """Webchat child can resolve to a different-channel parent via timestamp."""
        all_ids = {
            "cli_1772696251",
            "webchat_worker_1772696251_task1",
        }
        result = resolve_parent(
            "webchat_worker_1772696251_task1", all_ids, {}
        )
        assert result == "cli_1772696251"


# ── Tests: resolve_parent — manual override priority ─────────────────


class TestManualOverridePriority:
    def test_manual_override_beats_subagent(self):
        """Manual override has highest priority, even for subagent patterns."""
        all_ids = {
            "webchat_real_parent",
            "subagent_webchat_1772696251_abc12345",
        }
        overrides = {"subagent_webchat_1772696251_abc12345": "webchat_real_parent"}
        result = resolve_parent(
            "subagent_webchat_1772696251_abc12345", all_ids, overrides
        )
        assert result == "webchat_real_parent"

    def test_manual_override_beats_webchat_heuristic(self):
        """Manual override has highest priority, even for webchat patterns."""
        all_ids = {
            "webchat_1772696251",
            "webchat_dispatch_1772696251",
            "webchat_custom_parent",
        }
        overrides = {"webchat_dispatch_1772696251": "webchat_custom_parent"}
        result = resolve_parent(
            "webchat_dispatch_1772696251", all_ids, overrides
        )
        assert result == "webchat_custom_parent"


# ── Tests: resolve_parent — root sessions ────────────────────────────


class TestRootSessions:
    def test_plain_webchat_root(self):
        """webchat_{timestamp} is a root session."""
        all_ids = {"webchat_1772696251"}
        assert resolve_parent("webchat_1772696251", all_ids, {}) is None

    def test_cli_root(self):
        """cli_{timestamp} is a root session."""
        all_ids = {"cli_1772696251"}
        assert resolve_parent("cli_1772696251", all_ids, {}) is None

    def test_feishu_root(self):
        """feishu.lab_{id}_{timestamp} is a root session (no heuristic matches)."""
        all_ids = {"feishu.lab_ou_abc_1772696251"}
        assert resolve_parent("feishu.lab_ou_abc_1772696251", all_ids, {}) is None

    def test_unknown_format(self):
        """Unknown session format returns None."""
        all_ids = {"random_session_name"}
        assert resolve_parent("random_session_name", all_ids, {}) is None


# ── Tests: is_child_of ───────────────────────────────────────────────


class TestIsChildOf:
    def test_direct_child(self, sessions_dir):
        """is_child_of returns True for direct parent-child."""
        _create_sessions(sessions_dir, [
            "webchat_1772696251",
            "subagent_webchat_1772696251_abc12345",
        ])
        assert is_child_of(
            "subagent_webchat_1772696251_abc12345",
            "webchat_1772696251",
            sessions_dir,
        ) is True

    def test_not_child(self, sessions_dir):
        """is_child_of returns False for unrelated sessions."""
        _create_sessions(sessions_dir, [
            "webchat_1772696251",
            "webchat_1772700000",
        ])
        assert is_child_of(
            "webchat_1772700000",
            "webchat_1772696251",
            sessions_dir,
        ) is False

    def test_child_with_manual_override(self, sessions_dir):
        """is_child_of respects manual overrides."""
        _create_sessions(sessions_dir, [
            "webchat_parent",
            "webchat_child",
        ])
        _write_parents_json(sessions_dir, {"webchat_child": "webchat_parent"})
        assert is_child_of("webchat_child", "webchat_parent", sessions_dir) is True

    def test_child_wrong_parent(self, sessions_dir):
        """is_child_of returns False when parent doesn't match."""
        _create_sessions(sessions_dir, [
            "webchat_1772696251",
            "webchat_other_parent",
            "subagent_webchat_1772696251_abc12345",
        ])
        assert is_child_of(
            "subagent_webchat_1772696251_abc12345",
            "webchat_other_parent",
            sessions_dir,
        ) is False


# ── Tests: build_parent_map ──────────────────────────────────────────


class TestBuildParentMap:
    def test_end_to_end(self, sessions_dir):
        """build_parent_map returns complete mapping."""
        _create_sessions(sessions_dir, [
            "webchat_1772696251",
            "subagent_webchat_1772696251_abc12345",
            "webchat_dispatch_1772696251",
            "cli_1772700000",
        ])
        parent_map = build_parent_map(sessions_dir)
        assert parent_map["subagent_webchat_1772696251_abc12345"] == "webchat_1772696251"
        assert parent_map["webchat_dispatch_1772696251"] == "webchat_1772696251"
        # Root sessions should NOT be in the map
        assert "webchat_1772696251" not in parent_map
        assert "cli_1772700000" not in parent_map

    def test_with_manual_overrides(self, sessions_dir):
        """build_parent_map incorporates manual overrides."""
        _create_sessions(sessions_dir, [
            "webchat_parent",
            "webchat_child",
            "webchat_1772696251",
        ])
        _write_parents_json(sessions_dir, {
            "_comment": "test overrides",
            "webchat_child": "webchat_parent",
        })
        parent_map = build_parent_map(sessions_dir)
        assert parent_map["webchat_child"] == "webchat_parent"

    def test_empty_directory(self, sessions_dir):
        """build_parent_map returns empty dict for empty directory."""
        parent_map = build_parent_map(sessions_dir)
        assert parent_map == {}

    def test_nonexistent_directory(self, tmp_path):
        """build_parent_map returns empty dict for nonexistent directory."""
        parent_map = build_parent_map(tmp_path / "nonexistent")
        assert parent_map == {}

    def test_complex_tree(self, sessions_dir):
        """build_parent_map handles multi-level tree correctly."""
        _create_sessions(sessions_dir, [
            "webchat_1772696251",                        # root
            "webchat_qa_dispatch_1772696251",            # child of root
            "webchat_qa_worker_1772696251_task1",        # child of root (via ts, shortest match)
            "subagent_webchat_1772696251_deadbeef",      # subagent of root
        ])
        parent_map = build_parent_map(sessions_dir)
        assert parent_map["webchat_qa_dispatch_1772696251"] == "webchat_1772696251"
        assert parent_map["webchat_qa_worker_1772696251_task1"] == "webchat_1772696251"
        assert parent_map["subagent_webchat_1772696251_deadbeef"] == "webchat_1772696251"
        assert "webchat_1772696251" not in parent_map
