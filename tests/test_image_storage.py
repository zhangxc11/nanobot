"""Tests for Phase 15: image storage architecture improvements.

Tests cover:
1. base64 extraction and file saving (_extract_and_save_images)
2. file reference restoration (_restore_image_refs)
3. SessionManager._prepare_entry integration
4. Session.get_history integration
5. Backward compatibility with old base64 sessions
6. Graceful degradation when files are missing
"""

import base64
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from nanobot.session.manager import (
    Session,
    SessionManager,
    _extract_and_save_images,
    _load_file_as_data_url,
    _restore_image_refs,
    _save_base64_image,
)


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temporary workspace directory."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "sessions").mkdir()
    return ws


@pytest.fixture
def sample_image_bytes():
    """Create a minimal valid JPEG image (smallest possible)."""
    # Minimal JPEG: SOI + APP0 + minimal data + EOI
    # This is a 1x1 white pixel JPEG
    return bytes([
        0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46,
        0x49, 0x46, 0x00, 0x01, 0x01, 0x00, 0x00, 0x01,
        0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
        0x00, 0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08,
        0x07, 0x07, 0x07, 0x09, 0x09, 0x08, 0x0A, 0x0C,
        0x14, 0x0D, 0x0C, 0x0B, 0x0B, 0x0C, 0x19, 0x12,
        0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E, 0x1D,
        0x1A, 0x1C, 0x1C, 0x20, 0x24, 0x2E, 0x27, 0x20,
        0x22, 0x2C, 0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29,
        0x2C, 0x30, 0x31, 0x34, 0x34, 0x34, 0x1F, 0x27,
        0x39, 0x3D, 0x38, 0x32, 0x3C, 0x2E, 0x33, 0x34,
        0x32, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01,
        0x00, 0x01, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4,
        0x00, 0x1F, 0x00, 0x00, 0x01, 0x05, 0x01, 0x01,
        0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04,
        0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0xFF,
        0xDA, 0x00, 0x08, 0x01, 0x01, 0x00, 0x00, 0x3F,
        0x00, 0x7B, 0x40, 0x1B, 0xFF, 0xD9,
    ])


@pytest.fixture
def sample_data_url(sample_image_bytes):
    """Create a data: URL from sample image bytes."""
    b64 = base64.b64encode(sample_image_bytes).decode()
    return f"data:image/jpeg;base64,{b64}"


@pytest.fixture
def sample_png_bytes():
    """Create a minimal PNG image."""
    # 1x1 pixel red PNG
    import struct
    import zlib

    def chunk(chunk_type, data):
        c = chunk_type + data
        crc = struct.pack('>I', zlib.crc32(c) & 0xFFFFFFFF)
        return struct.pack('>I', len(data)) + c + crc

    signature = b'\x89PNG\r\n\x1a\n'
    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
    raw_data = b'\x00\xFF\x00\x00'  # filter byte + RGB
    idat = chunk(b'IDAT', zlib.compress(raw_data))
    iend = chunk(b'IEND', b'')
    return signature + ihdr + idat + iend


# ── Test _save_base64_image ───────────────────────────────────────

class TestSaveBase64Image:
    def test_saves_jpeg(self, tmp_path, sample_image_bytes, sample_data_url):
        """Test saving a JPEG base64 data URL to disk."""
        result = _save_base64_image(sample_data_url, tmp_path)
        assert result is not None
        file_path, mime_type = result
        assert mime_type == "image/jpeg"
        assert file_path.endswith(".jpg")
        assert Path(file_path).exists()
        assert Path(file_path).read_bytes() == sample_image_bytes

    def test_saves_png(self, tmp_path, sample_png_bytes):
        """Test saving a PNG base64 data URL to disk."""
        b64 = base64.b64encode(sample_png_bytes).decode()
        data_url = f"data:image/png;base64,{b64}"
        result = _save_base64_image(data_url, tmp_path)
        assert result is not None
        file_path, mime_type = result
        assert mime_type == "image/png"
        assert file_path.endswith(".png")
        assert Path(file_path).read_bytes() == sample_png_bytes

    def test_deduplication(self, tmp_path, sample_data_url):
        """Test that identical images are not saved twice."""
        result1 = _save_base64_image(sample_data_url, tmp_path)
        result2 = _save_base64_image(sample_data_url, tmp_path)
        assert result1 is not None
        assert result2 is not None
        assert result1[0] == result2[0]  # Same file path
        # Only one file should exist
        files = list(tmp_path.glob("*"))
        assert len(files) == 1

    def test_invalid_data_url(self, tmp_path):
        """Test graceful failure on invalid data URL."""
        result = _save_base64_image("not-a-data-url", tmp_path)
        assert result is None

    def test_creates_directory(self, tmp_path, sample_data_url):
        """Test that day directory is created if it doesn't exist."""
        day_dir = tmp_path / "2026-02-27"
        assert not day_dir.exists()
        result = _save_base64_image(sample_data_url, day_dir)
        assert result is not None
        assert day_dir.exists()


# ── Test _extract_and_save_images ─────────────────────────────────

class TestExtractAndSaveImages:
    def test_plain_string_unchanged(self, tmp_workspace):
        """Plain string content should pass through unchanged."""
        result = _extract_and_save_images("hello world", tmp_workspace)
        assert result == "hello world"

    def test_none_unchanged(self, tmp_workspace):
        """None content should pass through unchanged."""
        result = _extract_and_save_images(None, tmp_workspace)
        assert result is None

    def test_extracts_base64_image(self, tmp_workspace, sample_data_url):
        """Base64 image in multimodal content should be extracted to file."""
        content = [
            {"type": "image_url", "image_url": {"url": sample_data_url}},
            {"type": "text", "text": "What is this?"},
        ]
        result = _extract_and_save_images(content, tmp_workspace)
        assert isinstance(result, list)
        assert len(result) == 2
        # Image should be replaced with file:// reference
        img_item = result[0]
        assert img_item["type"] == "image_url"
        url = img_item["image_url"]["url"]
        assert url.startswith("file://")
        assert "?mime=image/jpeg" in url
        # Text should be unchanged
        assert result[1] == {"type": "text", "text": "What is this?"}

    def test_file_ref_already_present(self, tmp_workspace):
        """file:// references should pass through unchanged."""
        content = [
            {"type": "image_url", "image_url": {"url": "file:///some/path.jpg?mime=image/jpeg"}},
        ]
        result = _extract_and_save_images(content, tmp_workspace)
        assert result == content

    def test_multiple_images(self, tmp_workspace, sample_data_url, sample_png_bytes):
        """Multiple images should all be extracted."""
        png_b64 = base64.b64encode(sample_png_bytes).decode()
        content = [
            {"type": "image_url", "image_url": {"url": sample_data_url}},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png_b64}"}},
            {"type": "text", "text": "Compare these"},
        ]
        result = _extract_and_save_images(content, tmp_workspace)
        assert len(result) == 3
        assert result[0]["image_url"]["url"].startswith("file://")
        assert result[1]["image_url"]["url"].startswith("file://")
        assert result[2]["type"] == "text"


# ── Test _restore_image_refs ──────────────────────────────────────

class TestRestoreImageRefs:
    def test_plain_string_unchanged(self):
        """Plain string content should pass through unchanged."""
        assert _restore_image_refs("hello") == "hello"

    def test_restores_file_ref(self, tmp_path, sample_image_bytes):
        """file:// reference should be restored to data: base64 URL."""
        # Save a file first
        img_path = tmp_path / "test.jpg"
        img_path.write_bytes(sample_image_bytes)
        content = [
            {"type": "image_url", "image_url": {"url": f"file://{img_path}?mime=image/jpeg"}},
        ]
        result = _restore_image_refs(content)
        assert len(result) == 1
        url = result[0]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")
        # Verify round-trip
        _, b64_part = url.split(",", 1)
        assert base64.b64decode(b64_part) == sample_image_bytes

    def test_drops_missing_file(self, tmp_path):
        """Missing file should be dropped with warning."""
        content = [
            {"type": "image_url", "image_url": {"url": "file:///nonexistent/image.jpg?mime=image/jpeg"}},
            {"type": "text", "text": "hello"},
        ]
        result = _restore_image_refs(content)
        assert len(result) == 1
        assert result[0]["type"] == "text"

    def test_data_url_passthrough(self, sample_data_url):
        """Existing data: URLs should pass through unchanged (backward compat)."""
        content = [
            {"type": "image_url", "image_url": {"url": sample_data_url}},
        ]
        result = _restore_image_refs(content)
        assert len(result) == 1
        assert result[0]["image_url"]["url"] == sample_data_url

    def test_mixed_refs(self, tmp_path, sample_image_bytes, sample_data_url):
        """Mix of file:// and data: URLs should both work."""
        img_path = tmp_path / "test.jpg"
        img_path.write_bytes(sample_image_bytes)
        content = [
            {"type": "image_url", "image_url": {"url": f"file://{img_path}?mime=image/jpeg"}},
            {"type": "image_url", "image_url": {"url": sample_data_url}},
            {"type": "text", "text": "compare"},
        ]
        result = _restore_image_refs(content)
        assert len(result) == 3
        assert result[0]["image_url"]["url"].startswith("data:")
        assert result[1]["image_url"]["url"] == sample_data_url
        assert result[2]["type"] == "text"


# ── Test SessionManager._prepare_entry integration ────────────────

class TestPrepareEntryIntegration:
    def test_user_message_with_image(self, tmp_workspace, sample_data_url):
        """User message with base64 image should have image extracted."""
        sm = SessionManager(tmp_workspace)
        message = {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": sample_data_url}},
                {"type": "text", "text": "What is this?"},
            ],
        }
        entry = sm._prepare_entry(message)
        content = entry["content"]
        assert isinstance(content, list)
        img_url = content[0]["image_url"]["url"]
        assert img_url.startswith("file://")
        assert "?mime=image/jpeg" in img_url

    def test_text_message_unchanged(self, tmp_workspace):
        """Plain text message should not be affected."""
        sm = SessionManager(tmp_workspace)
        message = {"role": "user", "content": "hello"}
        entry = sm._prepare_entry(message)
        assert entry["content"] == "hello"

    def test_assistant_message_unchanged(self, tmp_workspace):
        """Assistant message should not be affected."""
        sm = SessionManager(tmp_workspace)
        message = {"role": "assistant", "content": "I see an image"}
        entry = sm._prepare_entry(message)
        assert entry["content"] == "I see an image"

    def test_tool_result_truncation_still_works(self, tmp_workspace):
        """Tool result truncation should still work."""
        sm = SessionManager(tmp_workspace)
        long_content = "x" * 1000
        message = {"role": "tool", "content": long_content, "tool_call_id": "tc1", "name": "test"}
        entry = sm._prepare_entry(message)
        assert len(entry["content"]) < len(long_content)
        assert entry["content"].endswith("(truncated)")


# ── Test Session.get_history integration ──────────────────────────

class TestGetHistoryIntegration:
    def test_restores_file_refs_in_history(self, tmp_path, sample_image_bytes):
        """get_history should restore file:// refs to data: base64."""
        img_path = tmp_path / "test.jpg"
        img_path.write_bytes(sample_image_bytes)
        session = Session(key="test:1")
        session.messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"file://{img_path}?mime=image/jpeg"}},
                    {"type": "text", "text": "What is this?"},
                ],
                "timestamp": "2026-02-27T12:00:00",
            },
            {
                "role": "assistant",
                "content": "It's a test image.",
                "timestamp": "2026-02-27T12:00:01",
            },
        ]
        history = session.get_history()
        assert len(history) == 2
        user_content = history[0]["content"]
        assert isinstance(user_content, list)
        assert user_content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    def test_backward_compat_data_urls(self, sample_data_url):
        """Old sessions with inline data: URLs should still work."""
        session = Session(key="test:2")
        session.messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": sample_data_url}},
                    {"type": "text", "text": "old style"},
                ],
                "timestamp": "2026-02-27T12:00:00",
            },
        ]
        history = session.get_history()
        assert len(history) == 1
        user_content = history[0]["content"]
        assert user_content[0]["image_url"]["url"] == sample_data_url

    def test_missing_file_graceful(self):
        """Missing file reference should be dropped gracefully."""
        session = Session(key="test:3")
        session.messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "file:///nonexistent.jpg?mime=image/jpeg"}},
                    {"type": "text", "text": "image was here"},
                ],
                "timestamp": "2026-02-27T12:00:00",
            },
        ]
        history = session.get_history()
        assert len(history) == 1
        user_content = history[0]["content"]
        # Image should be dropped, text preserved
        assert len(user_content) == 1
        assert user_content[0]["type"] == "text"


# ── Test round-trip: save → load → get_history ────────────────────

class TestRoundTrip:
    def test_full_round_trip(self, tmp_workspace, sample_data_url, sample_image_bytes):
        """Full round-trip: append_message (extracts) → save → load → get_history (restores)."""
        sm = SessionManager(tmp_workspace)
        session = sm.get_or_create("test:roundtrip")

        # Simulate a user message with base64 image
        user_msg = {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": sample_data_url}},
                {"type": "text", "text": "Describe this image"},
            ],
            "timestamp": "2026-02-27T12:00:00",
        }
        sm.append_message(session, user_msg)

        # Add assistant response
        assistant_msg = {
            "role": "assistant",
            "content": "I see a test image.",
            "timestamp": "2026-02-27T12:00:01",
        }
        sm.append_message(session, assistant_msg)

        # Verify JSONL file does NOT contain base64
        session_path = sm._get_session_path("test:roundtrip")
        jsonl_content = session_path.read_text()
        assert "base64" not in jsonl_content
        assert "file://" in jsonl_content

        # Reload session from disk
        sm.invalidate("test:roundtrip")
        reloaded = sm.get_or_create("test:roundtrip")
        history = reloaded.get_history()

        assert len(history) == 2
        # User message should have base64 restored
        user_content = history[0]["content"]
        assert isinstance(user_content, list)
        img_url = user_content[0]["image_url"]["url"]
        assert img_url.startswith("data:image/jpeg;base64,")
        # Verify the actual image data
        _, b64_part = img_url.split(",", 1)
        assert base64.b64decode(b64_part) == sample_image_bytes
        # Text preserved
        assert user_content[1]["text"] == "Describe this image"

    def test_jsonl_size_reduction(self, tmp_workspace):
        """Verify that JSONL file size is significantly smaller without base64."""
        sm = SessionManager(tmp_workspace)
        session = sm.get_or_create("test:size")

        # Create a ~100KB "image"
        fake_image = b"\xff\xd8" + os.urandom(100_000) + b"\xff\xd9"
        b64 = base64.b64encode(fake_image).decode()
        data_url = f"data:image/jpeg;base64,{b64}"

        user_msg = {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": "big image"},
            ],
        }
        sm.append_message(session, user_msg)

        session_path = sm._get_session_path("test:size")
        file_size = session_path.stat().st_size
        # Without extraction, the JSONL would be ~133KB (base64 overhead)
        # With extraction, it should be < 1KB (just the file reference)
        assert file_size < 2000, f"JSONL file too large: {file_size} bytes (expected < 2000)"
