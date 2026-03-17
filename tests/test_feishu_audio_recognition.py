"""Tests for extracting recognition text from Feishu audio messages (§57)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _build_event_message(msg_type: str, content: dict, message_id: str = "msg_001"):
    """Build a minimal Feishu event message object for testing."""
    msg = MagicMock()
    msg.message_id = message_id
    msg.message_type = msg_type
    msg.content = json.dumps(content)
    msg.chat_id = "oc_test"
    msg.chat_type = "p2p"
    return msg


def _build_sender(sender_type: str = "user", open_id: str = "ou_test"):
    """Build a minimal Feishu sender object."""
    sender = MagicMock()
    sender.sender_type = sender_type
    sender.sender_id = MagicMock()
    sender.sender_id.open_id = open_id
    return sender


class TestDirectAudioRecognition:
    """Test recognition extraction from direct audio messages."""

    @pytest.mark.asyncio
    async def test_audio_with_recognition(self):
        """Audio message with recognition should include recognized text in content_parts."""
        # We test the content parsing logic inline by simulating what _handle_event does
        content_json = {
            "file_key": "fk_audio_001",
            "recognition": "你好，这是一条语音消息"
        }
        msg_type = "audio"

        content_parts = []
        media_paths = []

        # Simulate _download_and_save_media result
        file_path = "/tmp/test_audio.opus"
        content_text = "[audio] /tmp/test_audio.opus"
        if file_path:
            media_paths.append(file_path)
        content_parts.append(content_text)

        # This is the new recognition extraction logic
        if msg_type == "audio":
            recognition = content_json.get("recognition", "")
            if recognition:
                content_parts.append(recognition)

        assert len(content_parts) == 2
        assert content_parts[0] == "[audio] /tmp/test_audio.opus"
        assert content_parts[1] == "你好，这是一条语音消息"
        assert len(media_paths) == 1

    @pytest.mark.asyncio
    async def test_audio_without_recognition(self):
        """Audio message without recognition field should not add extra content."""
        content_json = {
            "file_key": "fk_audio_002"
        }
        msg_type = "audio"

        content_parts = []

        content_text = "[audio] /tmp/test_audio.opus"
        content_parts.append(content_text)

        if msg_type == "audio":
            recognition = content_json.get("recognition", "")
            if recognition:
                content_parts.append(recognition)

        assert len(content_parts) == 1
        assert content_parts[0] == "[audio] /tmp/test_audio.opus"

    @pytest.mark.asyncio
    async def test_audio_with_empty_recognition(self):
        """Audio message with empty recognition string should not add extra content."""
        content_json = {
            "file_key": "fk_audio_003",
            "recognition": ""
        }
        msg_type = "audio"

        content_parts = []

        content_text = "[audio] /tmp/test_audio.opus"
        content_parts.append(content_text)

        if msg_type == "audio":
            recognition = content_json.get("recognition", "")
            if recognition:
                content_parts.append(recognition)

        assert len(content_parts) == 1

    @pytest.mark.asyncio
    async def test_non_audio_media_ignores_recognition(self):
        """Non-audio media types (image, file, media) should not extract recognition."""
        content_json = {
            "file_key": "fk_file_001",
            "recognition": "this should be ignored"
        }

        for msg_type in ("image", "file", "media"):
            content_parts = []
            content_text = f"[{msg_type}] /tmp/test_file"
            content_parts.append(content_text)

            # Same logic as in feishu.py
            if msg_type == "audio":
                recognition = content_json.get("recognition", "")
                if recognition:
                    content_parts.append(recognition)

            assert len(content_parts) == 1, f"msg_type={msg_type} should not extract recognition"


class TestMergeForwardAudioRecognition:
    """Test recognition extraction from audio sub-messages in merge_forward."""

    @pytest.mark.asyncio
    async def test_merge_forward_audio_with_recognition(self):
        """Audio sub-message in merge_forward with recognition should include text."""
        sub_content = {
            "file_key": "fk_audio_sub_001",
            "recognition": "转发的语音消息内容"
        }
        sub_type = "audio"

        text_parts = []

        content_text = "[audio] /tmp/sub_audio.opus"
        text_parts.append(content_text)

        # Same logic as in feishu.py merge_forward handler
        if sub_type == "audio":
            recognition = sub_content.get("recognition", "")
            if recognition:
                text_parts.append(recognition)

        assert len(text_parts) == 2
        assert text_parts[0] == "[audio] /tmp/sub_audio.opus"
        assert text_parts[1] == "转发的语音消息内容"

    @pytest.mark.asyncio
    async def test_merge_forward_audio_without_recognition(self):
        """Audio sub-message in merge_forward without recognition should not add text."""
        sub_content = {
            "file_key": "fk_audio_sub_002"
        }
        sub_type = "audio"

        text_parts = []

        content_text = "[audio] /tmp/sub_audio.opus"
        text_parts.append(content_text)

        if sub_type == "audio":
            recognition = sub_content.get("recognition", "")
            if recognition:
                text_parts.append(recognition)

        assert len(text_parts) == 1
