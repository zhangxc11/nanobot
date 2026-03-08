"""Shared retry utilities for LLM API calls.

Phase 28: Extracted from AgentLoop._is_retryable() and subagent._is_retryable()
into a single shared module to ensure consistency. Enhanced with:
- "disconnected" / "connection reset" pattern matching
- Smart delay classification (fast vs slow retry)
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Retryable error detection
# ---------------------------------------------------------------------------

# Exception class names that litellm wraps for transient provider errors
_RETRYABLE_CLASSES = frozenset({
    "RateLimitError",
    "APIConnectionError",
    "APITimeoutError",
    "Timeout",
    "ServiceUnavailableError",
    "InternalServerError",
})

# HTTP status codes worth retrying
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504, 529})

# Error message substrings indicating retryable conditions
_RETRYABLE_MSG_PATTERNS = (
    "rate limit",
    "rate_limit",
    "overloaded",
    "capacity",
    "too many requests",
    "server disconnected",
    "connection reset",
    "connection closed",
    "broken pipe",
    "eof occurred",
    "incomplete chunked read",
    "remote end closed connection",
)

# Patterns that indicate a *fast-retryable* error (disconnected, not rate-limited)
_FAST_RETRY_PATTERNS = (
    "server disconnected",
    "connection reset",
    "connection closed",
    "broken pipe",
    "eof occurred",
    "incomplete chunked read",
    "remote end closed connection",
)

_FAST_RETRY_CLASSES = frozenset({
    "APIConnectionError",
    "InternalServerError",
})


def is_retryable(error: Exception) -> bool:
    """Check if an LLM error is transient and worth retrying.

    Matches rate-limit, connection, timeout, and disconnection errors
    from litellm and upstream providers without importing their exception
    classes directly.
    """
    cls_name = type(error).__name__
    if cls_name in _RETRYABLE_CLASSES:
        return True

    # Check HTTP status code if available (litellm attaches it)
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    if isinstance(status, int) and status in _RETRYABLE_STATUS_CODES:
        return True

    # Fallback: message-based detection
    msg_lower = str(error).lower()
    for pattern in _RETRYABLE_MSG_PATTERNS:
        if pattern in msg_lower:
            return True

    return False


def is_fast_retryable(error: Exception) -> bool:
    """Check if the error is a transient disconnection that warrants fast retry.

    Returns True for connection-level errors (server disconnected, reset, etc.)
    where waiting a long time is counterproductive.
    Returns False for rate-limit / overload errors that need longer backoff.
    """
    cls_name = type(error).__name__

    # InternalServerError with "disconnected" message → fast
    # InternalServerError with "overloaded" message → slow
    msg_lower = str(error).lower()
    for pattern in _FAST_RETRY_PATTERNS:
        if pattern in msg_lower:
            return True

    # APIConnectionError is always a connection issue → fast
    if cls_name == "APIConnectionError":
        return True

    # Timeout → fast (no point waiting longer)
    if cls_name in ("APITimeoutError", "Timeout"):
        return True

    return False


def compute_retry_delay(attempt: int, fast: bool) -> float:
    """Compute retry delay in seconds based on attempt number and error type.

    Fast retry (disconnected/timeout): 2, 4, 8, 16, 30 seconds
    Slow retry (rate limit/overload):  10, 20, 40, 60, 60 seconds
    """
    if fast:
        delay = 2 * (2 ** attempt)  # 2, 4, 8, 16, 32, ...
        return min(delay, 30.0)
    else:
        delay = 10 * (2 ** attempt)  # 10, 20, 40, 80, ...
        return min(delay, 60.0)
