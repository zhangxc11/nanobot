"""Tests for the shared retry module (Phase 28)."""

import pytest

from nanobot.agent.retry import (
    compute_retry_delay,
    is_fast_retryable,
    is_retryable,
)


# ---------------------------------------------------------------------------
# Helpers: fake exception classes that mimic litellm naming
# ---------------------------------------------------------------------------

class RateLimitError(Exception):
    pass

class APIConnectionError(Exception):
    pass

class APITimeoutError(Exception):
    pass

class Timeout(Exception):
    pass

class InternalServerError(Exception):
    pass

class ServiceUnavailableError(Exception):
    pass

class SomeRandomError(Exception):
    pass


class StatusCodeError(Exception):
    def __init__(self, msg, status_code):
        super().__init__(msg)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# is_retryable tests
# ---------------------------------------------------------------------------

class TestIsRetryable:
    """Test retryable error detection."""

    def test_retryable_by_class_name(self):
        assert is_retryable(RateLimitError("rate limit"))
        assert is_retryable(APIConnectionError("conn error"))
        assert is_retryable(APITimeoutError("timeout"))
        assert is_retryable(Timeout("timeout"))
        assert is_retryable(InternalServerError("server error"))
        assert is_retryable(ServiceUnavailableError("unavailable"))

    def test_not_retryable_random_error(self):
        assert not is_retryable(SomeRandomError("something broke"))
        assert not is_retryable(ValueError("bad value"))

    def test_retryable_by_status_code(self):
        assert is_retryable(StatusCodeError("err", 429))
        assert is_retryable(StatusCodeError("err", 500))
        assert is_retryable(StatusCodeError("err", 502))
        assert is_retryable(StatusCodeError("err", 503))
        assert is_retryable(StatusCodeError("err", 504))
        assert is_retryable(StatusCodeError("err", 529))

    def test_not_retryable_by_status_code(self):
        assert not is_retryable(StatusCodeError("err", 400))
        assert not is_retryable(StatusCodeError("err", 401))
        assert not is_retryable(StatusCodeError("err", 404))

    def test_retryable_by_message_patterns(self):
        assert is_retryable(SomeRandomError("rate limit exceeded"))
        assert is_retryable(SomeRandomError("rate_limit_exceeded"))
        assert is_retryable(SomeRandomError("server is overloaded"))
        assert is_retryable(SomeRandomError("at capacity"))
        assert is_retryable(SomeRandomError("too many requests"))

    def test_retryable_disconnected_patterns(self):
        """Phase 28: new patterns for weak-network errors."""
        assert is_retryable(SomeRandomError("Server disconnected"))
        assert is_retryable(SomeRandomError("connection reset by peer"))
        assert is_retryable(SomeRandomError("Connection closed unexpectedly"))
        assert is_retryable(SomeRandomError("Broken pipe"))
        assert is_retryable(SomeRandomError("EOF occurred in violation of protocol"))
        assert is_retryable(SomeRandomError("incomplete chunked read"))
        assert is_retryable(SomeRandomError("remote end closed connection"))

    def test_real_error_messages(self):
        """Test with the actual error messages from production."""
        e1 = InternalServerError(
            "AnthropicException - Server disconnected. Handle with litellm.InternalServerError"
        )
        assert is_retryable(e1)

        e2 = Timeout(
            "AnthropicException - litellm.Timeout: Connection timed out. "
            "Timeout passed=600.0, time taken=600.063 seconds"
        )
        assert is_retryable(e2)


# ---------------------------------------------------------------------------
# §33: Non-retryable message pattern tests
# ---------------------------------------------------------------------------

class TestNonRetryablePatterns:
    """§33: Errors with config/auth messages should NOT be retried,
    even when wrapped in retryable exception classes."""

    def test_model_not_found_in_service_unavailable(self):
        """The exact production error that triggered §33."""
        e = ServiceUnavailableError(
            'AnthropicException - {"error":{"type":"model_not_found",'
            '"message":"分组 全模型纯官key 下模型 claude-opus-4-6 '
            '无可用渠道（distributor）"}}'
        )
        assert not is_retryable(e)

    def test_model_not_found_english(self):
        e = ServiceUnavailableError("model not found: gpt-5-turbo")
        assert not is_retryable(e)

    def test_no_available_channel_chinese(self):
        e = ServiceUnavailableError("无可用渠道 for model xyz")
        assert not is_retryable(e)

    def test_invalid_api_key_in_retryable_class(self):
        e = ServiceUnavailableError("invalid_api_key: sk-xxx is not valid")
        assert not is_retryable(e)

    def test_invalid_api_key_space(self):
        e = InternalServerError("Invalid API key provided")
        assert not is_retryable(e)

    def test_authentication_error(self):
        e = ServiceUnavailableError("Authentication failed for user")
        assert not is_retryable(e)

    def test_unauthorized(self):
        e = InternalServerError("Unauthorized access to model")
        assert not is_retryable(e)

    def test_permission_denied(self):
        e = ServiceUnavailableError("Permission denied for this resource")
        assert not is_retryable(e)

    def test_access_denied(self):
        e = InternalServerError("Access denied: insufficient permissions")
        assert not is_retryable(e)

    def test_does_not_exist(self):
        e = ServiceUnavailableError("The model does not exist")
        assert not is_retryable(e)

    def test_not_supported(self):
        e = InternalServerError("This operation is not supported")
        assert not is_retryable(e)

    def test_invalid_request_error(self):
        e = ServiceUnavailableError("invalid_request_error: bad parameter")
        assert not is_retryable(e)

    def test_billing_error(self):
        e = ServiceUnavailableError("billing account suspended")
        assert not is_retryable(e)

    def test_quota_exceeded(self):
        e = ServiceUnavailableError("quota exceeded for this month")
        assert not is_retryable(e)

    def test_status_code_also_excluded(self):
        """Non-retryable pattern takes priority over status code match."""
        e = StatusCodeError("model_not_found: no such model", 503)
        assert not is_retryable(e)

    def test_genuine_service_unavailable_still_retryable(self):
        """A real transient ServiceUnavailableError should still be retried."""
        e = ServiceUnavailableError("Service temporarily unavailable, please try again later")
        assert is_retryable(e)

    def test_genuine_internal_server_error_still_retryable(self):
        """A real transient InternalServerError should still be retried."""
        e = InternalServerError("Internal server error")
        assert is_retryable(e)

    def test_rate_limit_not_affected(self):
        """RateLimitError without non-retryable patterns should still be retried."""
        e = RateLimitError("Rate limit exceeded, please slow down")
        assert is_retryable(e)


# ---------------------------------------------------------------------------
# is_fast_retryable tests
# ---------------------------------------------------------------------------

class TestIsFastRetryable:
    """Test fast vs slow retry classification."""

    def test_disconnected_is_fast(self):
        e = InternalServerError("AnthropicException - Server disconnected")
        assert is_fast_retryable(e)

    def test_connection_reset_is_fast(self):
        e = SomeRandomError("connection reset by peer")
        assert is_fast_retryable(e)

    def test_api_connection_error_is_fast(self):
        e = APIConnectionError("connection failed")
        assert is_fast_retryable(e)

    def test_timeout_is_fast(self):
        e = Timeout("Connection timed out")
        assert is_fast_retryable(e)
        e2 = APITimeoutError("request timed out")
        assert is_fast_retryable(e2)

    def test_rate_limit_is_not_fast(self):
        e = RateLimitError("rate limit exceeded")
        assert not is_fast_retryable(e)

    def test_overloaded_is_not_fast(self):
        e = InternalServerError("server is overloaded")
        assert not is_fast_retryable(e)

    def test_service_unavailable_is_not_fast(self):
        e = ServiceUnavailableError("service unavailable")
        assert not is_fast_retryable(e)


# ---------------------------------------------------------------------------
# compute_retry_delay tests
# ---------------------------------------------------------------------------

class TestComputeRetryDelay:
    """Test retry delay computation."""

    def test_fast_delays(self):
        delays = [compute_retry_delay(i, fast=True) for i in range(8)]
        assert delays == [1, 2, 4, 8, 16, 30.0, 30.0, 30.0]

    def test_slow_delays(self):
        delays = [compute_retry_delay(i, fast=False) for i in range(8)]
        assert delays == [5, 10, 20, 40, 60.0, 60.0, 60.0, 60.0]

    def test_fast_capped_at_30(self):
        assert compute_retry_delay(100, fast=True) == 30.0

    def test_slow_capped_at_60(self):
        assert compute_retry_delay(100, fast=False) == 60.0
