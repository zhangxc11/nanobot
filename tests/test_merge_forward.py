"""Tests for merge_forward message resolution in FeishuChannel."""

import asyncio
import json
from unittest.mock import MagicMock, AsyncMock

import pytest


class MockSender:
    def __init__(self, sender_id="ou_sender1"):
        self.id = sender_id
        self.id_type = "open_id"
        self.sender_type = "user"
        self.tenant_key = "test"


class MockMessageBody:
    def __init__(self, content=""):
        self.content = content


class MockMessage:
    def __init__(self, message_id="msg_001", msg_type="text", content="",
                 sender_id="ou_sender1", create_time=1700000000):
        self.message_id = message_id
        self.msg_type = msg_type
        self.body = MockMessageBody(content)
        self.sender = MockSender(sender_id)
        self.create_time = create_time


class MockResponseBody:
    def __init__(self, items=None):
        self.items = items


class MockResponse:
    def __init__(self, success=True, items=None, code=0, msg="ok"):
        self._success = success
        self.data = MockResponseBody(items)
        self.code = code
        self.msg = msg

    def success(self):
        return self._success


@pytest.fixture
def feishu_channel():
    """Create a FeishuChannel instance with mocked dependencies."""
    from nanobot.config.schema import FeishuConfig
    from nanobot.channels.feishu import FeishuChannel

    config = FeishuConfig(
        enabled=True,
        app_id="test_app_id",
        app_secret="test_app_secret",
    )
    bus = MagicMock()
    channel = FeishuChannel(config, bus)
    channel._client = MagicMock()
    return channel


# ─── _get_message_detail_sync ───────────────────────────────────────────────

class TestGetMessageDetailSync:
    """Tests for _get_message_detail_sync method."""

    def test_success(self, feishu_channel):
        """Successfully fetch message detail."""
        msg = MockMessage(
            message_id="msg_001",
            msg_type="text",
            content=json.dumps({"text": "hello world"}),
            sender_id="ou_sender1",
            create_time=1700000000,
        )
        response = MockResponse(success=True, items=[msg])
        feishu_channel._client.im.v1.message.get.return_value = response

        result = feishu_channel._get_message_detail_sync("msg_001")

        assert result is not None
        assert result["msg_type"] == "text"
        assert result["content"] == {"text": "hello world"}
        assert result["sender_id"] == "ou_sender1"
        assert result["create_time"] == 1700000000
        assert result["message_id"] == "msg_001"

    def test_api_failure(self, feishu_channel):
        """API returns failure response."""
        response = MockResponse(success=False, code=99999, msg="permission denied")
        feishu_channel._client.im.v1.message.get.return_value = response

        result = feishu_channel._get_message_detail_sync("msg_001")
        assert result is None

    def test_empty_items(self, feishu_channel):
        """API returns success but no items."""
        response = MockResponse(success=True, items=[])
        feishu_channel._client.im.v1.message.get.return_value = response

        result = feishu_channel._get_message_detail_sync("msg_001")
        assert result is None

    def test_none_items(self, feishu_channel):
        """API returns success but items is None."""
        response = MockResponse(success=True, items=None)
        feishu_channel._client.im.v1.message.get.return_value = response

        result = feishu_channel._get_message_detail_sync("msg_001")
        assert result is None

    def test_exception(self, feishu_channel):
        """API call raises exception."""
        feishu_channel._client.im.v1.message.get.side_effect = Exception("network error")

        result = feishu_channel._get_message_detail_sync("msg_001")
        assert result is None

    def test_invalid_json_content(self, feishu_channel):
        """Message body has invalid JSON content."""
        msg = MockMessage(
            message_id="msg_002",
            msg_type="text",
            content="not valid json",
        )
        response = MockResponse(success=True, items=[msg])
        feishu_channel._client.im.v1.message.get.return_value = response

        result = feishu_channel._get_message_detail_sync("msg_002")
        assert result is not None
        assert result["content"] == {}  # Invalid JSON → empty dict

    def test_no_sender(self, feishu_channel):
        """Message has no sender info."""
        msg = MockMessage(message_id="msg_003", msg_type="text", content='{"text":"hi"}')
        msg.sender = None
        response = MockResponse(success=True, items=[msg])
        feishu_channel._client.im.v1.message.get.return_value = response

        result = feishu_channel._get_message_detail_sync("msg_003")
        assert result is not None
        assert result["sender_id"] == ""


# ─── _resolve_merge_forward ─────────────────────────────────────────────────

class TestResolveMergeForward:
    """Tests for _resolve_merge_forward async method."""

    def test_text_messages(self, feishu_channel):
        """Resolve merge_forward with text sub-messages."""
        messages = [
            MockMessage("msg_001", "text", json.dumps({"text": "Hello"})),
            MockMessage("msg_002", "text", json.dumps({"text": "World"})),
        ]

        call_count = 0
        def mock_get(request):
            nonlocal call_count
            msg = messages[call_count]
            call_count += 1
            return MockResponse(success=True, items=[msg])

        feishu_channel._client.im.v1.message.get.side_effect = mock_get

        async def _run():
            content_json = {"message_id_list": ["msg_001", "msg_002"]}
            return await feishu_channel._resolve_merge_forward(content_json)

        text, media = asyncio.run(_run())

        assert "Hello" in text
        assert "World" in text
        assert "--- forwarded messages ---" in text
        assert "--- end forwarded messages ---" in text
        assert media == []

    def test_empty_message_id_list(self, feishu_channel):
        """Empty message_id_list returns fallback text."""
        async def _run():
            content_json = {"message_id_list": []}
            return await feishu_channel._resolve_merge_forward(content_json)

        text, media = asyncio.run(_run())
        assert "no message IDs found" in text
        assert media == []

    def test_no_message_id_list(self, feishu_channel):
        """No message_id_list field returns fallback text."""
        async def _run():
            content_json = {"some_other_field": "value"}
            return await feishu_channel._resolve_merge_forward(content_json)

        text, media = asyncio.run(_run())
        assert "no message IDs found" in text
        assert media == []

    def test_api_failure_graceful(self, feishu_channel):
        """API failure for some messages — graceful degradation."""
        call_count = 0
        def mock_get(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MockResponse(success=False, code=99999, msg="denied")
            else:
                msg = MockMessage("msg_002", "text", json.dumps({"text": "OK"}))
                return MockResponse(success=True, items=[msg])

        feishu_channel._client.im.v1.message.get.side_effect = mock_get

        async def _run():
            content_json = {"message_id_list": ["msg_001", "msg_002"]}
            return await feishu_channel._resolve_merge_forward(content_json)

        text, media = asyncio.run(_run())
        assert "failed to fetch" in text
        assert "OK" in text

    def test_mixed_types(self, feishu_channel):
        """Resolve merge_forward with mixed sub-message types."""
        messages = [
            MockMessage("msg_001", "text", json.dumps({"text": "Hello"})),
            MockMessage("msg_002", "interactive", json.dumps({
                "header": {"title": {"content": "Card Title"}},
                "elements": [{"tag": "markdown", "content": "card body"}],
            })),
            MockMessage("msg_003", "system", json.dumps({})),
        ]

        call_count = 0
        def mock_get(request):
            nonlocal call_count
            msg = messages[call_count]
            call_count += 1
            return MockResponse(success=True, items=[msg])

        feishu_channel._client.im.v1.message.get.side_effect = mock_get

        async def _run():
            content_json = {"message_id_list": ["msg_001", "msg_002", "msg_003"]}
            return await feishu_channel._resolve_merge_forward(content_json)

        text, media = asyncio.run(_run())
        assert "Hello" in text
        assert "[system message]" in text

    def test_image_sub_message(self, feishu_channel):
        """Resolve merge_forward with image sub-message — downloads media."""
        msg = MockMessage("msg_001", "image", json.dumps({"image_key": "img_v3_test"}))
        feishu_channel._client.im.v1.message.get.return_value = MockResponse(
            success=True, items=[msg]
        )

        # Mock _download_and_save_media as an async mock
        async def mock_download(msg_type, content_json, message_id):
            return "/tmp/test.jpg", "[image: test.jpg]"
        feishu_channel._download_and_save_media = mock_download

        async def _run():
            content_json = {"message_id_list": ["msg_001"]}
            return await feishu_channel._resolve_merge_forward(content_json)

        text, media = asyncio.run(_run())
        assert "[image: test.jpg]" in text
        assert "/tmp/test.jpg" in media

    def test_nested_merge_forward(self, feishu_channel):
        """Nested merge_forward is noted but not recursed."""
        msg = MockMessage("msg_001", "merge_forward", json.dumps({
            "message_id_list": ["msg_nested_1"]
        }))
        feishu_channel._client.im.v1.message.get.return_value = MockResponse(
            success=True, items=[msg]
        )

        async def _run():
            content_json = {"message_id_list": ["msg_001"]}
            return await feishu_channel._resolve_merge_forward(content_json)

        text, media = asyncio.run(_run())
        assert "nested merged forward messages" in text

    def test_post_sub_message(self, feishu_channel):
        """Resolve merge_forward with post (rich text) sub-message."""
        post_content = {
            "zh_cn": {
                "title": "Rich Text Title",
                "content": [
                    [{"tag": "text", "text": "paragraph one"}],
                    [{"tag": "text", "text": "paragraph two"}],
                ]
            }
        }
        msg = MockMessage("msg_001", "post", json.dumps(post_content))
        feishu_channel._client.im.v1.message.get.return_value = MockResponse(
            success=True, items=[msg]
        )

        async def _run():
            content_json = {"message_id_list": ["msg_001"]}
            return await feishu_channel._resolve_merge_forward(content_json)

        text, media = asyncio.run(_run())
        assert "Rich Text Title" in text
        assert "paragraph one" in text

    def test_all_messages_fail(self, feishu_channel):
        """All sub-messages fail to fetch."""
        feishu_channel._client.im.v1.message.get.return_value = MockResponse(
            success=False, code=99999, msg="denied"
        )

        async def _run():
            content_json = {"message_id_list": ["msg_001", "msg_002"]}
            return await feishu_channel._resolve_merge_forward(content_json)

        text, media = asyncio.run(_run())
        assert "failed to fetch" in text
        assert "--- forwarded messages ---" in text

    def test_skip_empty_message_ids(self, feishu_channel):
        """Empty string message IDs are skipped."""
        msg = MockMessage("msg_001", "text", json.dumps({"text": "Hello"}))
        feishu_channel._client.im.v1.message.get.return_value = MockResponse(
            success=True, items=[msg]
        )

        async def _run():
            content_json = {"message_id_list": ["", "msg_001", ""]}
            return await feishu_channel._resolve_merge_forward(content_json)

        text, media = asyncio.run(_run())
        assert "Hello" in text
        # Should only call API once (for msg_001)
        assert feishu_channel._client.im.v1.message.get.call_count == 1


# ─── _extract_share_card_content fallback ────────────────────────────────────

class TestExtractShareCardMergeForward:
    """Test that _extract_share_card_content still handles merge_forward as fallback."""

    def test_merge_forward_fallback(self):
        from nanobot.channels.feishu import _extract_share_card_content

        result = _extract_share_card_content({}, "merge_forward")
        assert result == "[merged forward messages]"
