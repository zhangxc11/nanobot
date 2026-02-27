"""Tests for LLM API retry mechanism in AgentLoop."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from nanobot.agent.loop import AgentLoop


# ── _is_retryable tests ──

class TestIsRetryable:
    """Test the _is_retryable static method."""

    def _make_error(self, cls_name: str, message: str = "test error", status_code: int | None = None):
        """Create a mock exception with a given class name and optional status_code."""
        attrs = {}
        if status_code is not None:
            attrs["status_code"] = status_code
        exc_cls = type(cls_name, (Exception,), attrs)
        exc = exc_cls(message)
        if status_code is not None:
            exc.status_code = status_code
        return exc

    def test_rate_limit_error_by_class_name(self):
        err = self._make_error("RateLimitError", "rate limit exceeded")
        assert AgentLoop._is_retryable(err) is True

    def test_api_connection_error_by_class_name(self):
        err = self._make_error("APIConnectionError", "connection reset")
        assert AgentLoop._is_retryable(err) is True

    def test_api_timeout_error_by_class_name(self):
        err = self._make_error("APITimeoutError", "request timed out")
        assert AgentLoop._is_retryable(err) is True

    def test_timeout_by_class_name(self):
        err = self._make_error("Timeout", "timeout")
        assert AgentLoop._is_retryable(err) is True

    def test_service_unavailable_by_class_name(self):
        err = self._make_error("ServiceUnavailableError", "service down")
        assert AgentLoop._is_retryable(err) is True

    def test_internal_server_error_by_class_name(self):
        err = self._make_error("InternalServerError", "internal error")
        assert AgentLoop._is_retryable(err) is True

    def test_http_429_by_status_code(self):
        err = self._make_error("SomeError", "too many requests", status_code=429)
        assert AgentLoop._is_retryable(err) is True

    def test_http_500_by_status_code(self):
        err = self._make_error("SomeError", "server error", status_code=500)
        assert AgentLoop._is_retryable(err) is True

    def test_http_502_by_status_code(self):
        err = self._make_error("SomeError", "bad gateway", status_code=502)
        assert AgentLoop._is_retryable(err) is True

    def test_http_503_by_status_code(self):
        err = self._make_error("SomeError", "service unavailable", status_code=503)
        assert AgentLoop._is_retryable(err) is True

    def test_http_529_by_status_code(self):
        err = self._make_error("SomeError", "overloaded", status_code=529)
        assert AgentLoop._is_retryable(err) is True

    def test_rate_limit_in_message(self):
        err = Exception("This request would exceed your organization's rate limit of 400,000 output tokens")
        assert AgentLoop._is_retryable(err) is True

    def test_overloaded_in_message(self):
        err = Exception("The API is overloaded, please try again later")
        assert AgentLoop._is_retryable(err) is True

    def test_capacity_in_message(self):
        err = Exception("Not enough capacity to serve this request")
        assert AgentLoop._is_retryable(err) is True

    # Non-retryable errors

    def test_authentication_error_not_retryable(self):
        err = self._make_error("AuthenticationError", "invalid api key")
        assert AgentLoop._is_retryable(err) is False

    def test_invalid_request_error_not_retryable(self):
        err = self._make_error("InvalidRequestError", "invalid model")
        assert AgentLoop._is_retryable(err) is False

    def test_bad_request_not_retryable(self):
        err = self._make_error("BadRequestError", "malformed request", status_code=400)
        assert AgentLoop._is_retryable(err) is False

    def test_not_found_not_retryable(self):
        err = self._make_error("NotFoundError", "model not found", status_code=404)
        assert AgentLoop._is_retryable(err) is False

    def test_generic_exception_not_retryable(self):
        err = ValueError("some value error")
        assert AgentLoop._is_retryable(err) is False


# Helper exception classes for retry tests
_RateLimitError = type("RateLimitError", (Exception,), {})
_AuthenticationError = type("AuthenticationError", (Exception,), {})


def _make_loop():
    """Create a minimal AgentLoop with mocked dependencies."""
    provider = MagicMock()
    provider.chat = AsyncMock()
    loop = object.__new__(AgentLoop)
    loop.provider = provider
    return loop, provider


# ── _chat_with_retry tests (using asyncio.run for sync test runner) ──

class TestChatWithRetry:
    """Test the _chat_with_retry method."""

    def test_success_on_first_try(self):
        loop, provider = _make_loop()
        expected = MagicMock(name="response")
        provider.chat.return_value = expected

        result = asyncio.run(loop._chat_with_retry(
            messages=[], tools=None, model="test",
            temperature=0.7, max_tokens=1024,
        ))

        assert result is expected
        assert provider.chat.call_count == 1

    def test_retry_on_rate_limit_then_succeed(self):
        loop, provider = _make_loop()
        rate_err = _RateLimitError("rate limit exceeded")
        expected = MagicMock(name="response")
        provider.chat.side_effect = [rate_err, expected]

        async def _run():
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await loop._chat_with_retry(
                    messages=[], tools=None, model="test",
                    temperature=0.7, max_tokens=1024,
                )
                return result, mock_sleep

        result, mock_sleep = asyncio.run(_run())
        assert result is expected
        assert provider.chat.call_count == 2
        mock_sleep.assert_called_once_with(10)  # first retry: 10s

    def test_exponential_backoff_delays(self):
        loop, provider = _make_loop()
        rate_err = _RateLimitError("rate limit")
        expected = MagicMock(name="response")
        provider.chat.side_effect = [rate_err, rate_err, rate_err, expected]

        async def _run():
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await loop._chat_with_retry(
                    messages=[], tools=None, model="test",
                    temperature=0.7, max_tokens=1024,
                )
                return result, mock_sleep

        result, mock_sleep = asyncio.run(_run())
        assert result is expected
        assert provider.chat.call_count == 4
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [10, 20, 40]

    def test_max_retries_exceeded(self):
        loop, provider = _make_loop()
        rate_err = _RateLimitError("rate limit")
        provider.chat.side_effect = rate_err

        async def _run():
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await loop._chat_with_retry(
                    messages=[], tools=None, model="test",
                    temperature=0.7, max_tokens=1024,
                )

        with pytest.raises(_RateLimitError, match="rate limit"):
            asyncio.run(_run())

        assert provider.chat.call_count == 6  # 1 + 5 retries

    def test_non_retryable_error_raises_immediately(self):
        loop, provider = _make_loop()
        auth_err = _AuthenticationError("invalid api key")
        provider.chat.side_effect = auth_err

        with pytest.raises(_AuthenticationError, match="invalid api key"):
            asyncio.run(loop._chat_with_retry(
                messages=[], tools=None, model="test",
                temperature=0.7, max_tokens=1024,
            ))

        assert provider.chat.call_count == 1  # no retry

    def test_progress_notification_on_retry(self):
        loop, provider = _make_loop()
        rate_err = _RateLimitError("rate limit")
        expected = MagicMock(name="response")
        provider.chat.side_effect = [rate_err, expected]
        progress_fn = AsyncMock()

        async def _run():
            with patch("asyncio.sleep", new_callable=AsyncMock):
                return await loop._chat_with_retry(
                    messages=[], tools=None, model="test",
                    temperature=0.7, max_tokens=1024,
                    progress_fn=progress_fn,
                )

        result = asyncio.run(_run())
        assert result is expected
        progress_fn.assert_called_once()
        call_msg = progress_fn.call_args[0][0]
        assert "10s" in call_msg
        assert "1/5" in call_msg

    def test_progress_fn_error_does_not_break_retry(self):
        """Progress notification failure should not prevent retry."""
        loop, provider = _make_loop()
        rate_err = _RateLimitError("rate limit")
        expected = MagicMock(name="response")
        provider.chat.side_effect = [rate_err, expected]
        progress_fn = AsyncMock(side_effect=RuntimeError("progress broken"))

        async def _run():
            with patch("asyncio.sleep", new_callable=AsyncMock):
                return await loop._chat_with_retry(
                    messages=[], tools=None, model="test",
                    temperature=0.7, max_tokens=1024,
                    progress_fn=progress_fn,
                )

        result = asyncio.run(_run())
        assert result is expected
        assert provider.chat.call_count == 2
