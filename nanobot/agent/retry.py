"""Shared retry utilities for LLM API calls.

Phase 28: Extracted from AgentLoop._is_retryable() and subagent._is_retryable()
into a single shared module to ensure consistency. Enhanced with:
- "disconnected" / "connection reset" pattern matching
- Smart delay classification (fast vs slow retry)

§33: Added _NON_RETRYABLE_MSG_PATTERNS to exclude configuration/auth errors
that are wrapped in retryable exception classes (e.g. ServiceUnavailableError
with "model_not_found" message).
"""

from __future__ import annotations

import asyncio

from loguru import logger


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

# ---------------------------------------------------------------------------
# Non-retryable message patterns (§33)
#
# Even if the exception class or status code matches a retryable condition,
# these message patterns indicate configuration/auth/model errors that will
# never succeed on retry.  Checked BEFORE retryable patterns.
# ---------------------------------------------------------------------------
_NON_RETRYABLE_MSG_PATTERNS = (
    "model_not_found",
    "model not found",
    "无可用渠道",
    "invalid_api_key",
    "invalid api key",
    "invalid_request_error",
    "authentication",
    "unauthorized",
    "permission denied",
    "access denied",
    "does not exist",
    "not supported",
    "billing",
    "quota exceeded",
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
    "cannot connect",
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

    §33: Before checking retryable conditions, excludes errors whose
    message indicates a configuration/auth/model issue that will never
    succeed on retry (e.g. "model_not_found", "invalid_api_key").
    """
    msg_lower = str(error).lower()

    # §33: Non-retryable message patterns take priority.
    # Even if the class name or status code looks retryable, these errors
    # indicate permanent failures that should not be retried.
    for pattern in _NON_RETRYABLE_MSG_PATTERNS:
        if pattern in msg_lower:
            return False

    cls_name = type(error).__name__
    if cls_name in _RETRYABLE_CLASSES:
        return True

    # Check HTTP status code if available (litellm attaches it)
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    if isinstance(status, int) and status in _RETRYABLE_STATUS_CODES:
        return True

    # Fallback: message-based detection
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

    Fast retry (disconnected/timeout): 1, 2, 4, 8, 16, 30 seconds
    Slow retry (rate limit/overload):  5, 10, 20, 40, 60 seconds
    """
    if fast:
        delay = max(1, 2 ** attempt)  # 1, 2, 4, 8, 16, 32, ...
        return min(delay, 30.0)
    else:
        delay = 5 * (2 ** attempt)  # 5, 10, 20, 40, 80, ...
        return min(delay, 60.0)


# ---------------------------------------------------------------------------
# §61: Timeout diagnosis utilities
# ---------------------------------------------------------------------------

def is_timeout_error(error: Exception) -> bool:
    """Check if an error is specifically a timeout error (not rate-limit)."""
    cls_name = type(error).__name__
    if cls_name in ("APITimeoutError", "Timeout"):
        return True
    msg_lower = str(error).lower()
    # "timeout" in message but not "too many requests" (which is rate limit)
    return "timeout" in msg_lower and "too many" not in msg_lower


async def ping_api(api_base: str, timeout: float = 10.0) -> bool:
    """Lightweight reachability check — HTTP GET to api_base.

    Any HTTP response (200/401/404/503) means network is reachable.
    Only connection failure / no response means unreachable.
    Does not require valid API key or model name.
    """
    if not api_base:
        return True  # No api_base to check, assume reachable

    import aiohttp

    url = api_base.rstrip("/")
    if not url.startswith("http"):
        url = f"https://{url}"

    try:
        client_timeout = aiohttp.ClientTimeout(total=timeout, connect=timeout)
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.get(url) as resp:
                logger.debug("ping_api {} → HTTP {}", url, resp.status)
                return True
    except Exception as e:
        logger.warning("ping_api {} failed: {}", url, e)
        return False


async def wait_for_recovery(
    api_base: str,
    max_wait: float = 20.0,
    interval: float = 10.0,
    ping_timeout: float = 10.0,
) -> bool:
    """Poll ping_api until success or max_wait exceeded.

    Returns True if API becomes reachable, False if still unreachable.
    """
    import time as _time

    start = _time.monotonic()
    attempt = 0
    while True:
        elapsed = _time.monotonic() - start
        if elapsed >= max_wait:
            logger.warning("wait_for_recovery: gave up after {:.1f}s", elapsed)
            return False

        remaining = max_wait - elapsed
        wait_time = min(interval, remaining)
        if wait_time > 0:
            attempt += 1
            logger.info(
                "wait_for_recovery: attempt {} — waiting {:.0f}s before ping",
                attempt, wait_time,
            )
            await asyncio.sleep(wait_time)

        ping_to = min(ping_timeout, max_wait - (_time.monotonic() - start))
        if ping_to <= 0:
            break
        if await ping_api(api_base, timeout=ping_to):
            logger.info(
                "wait_for_recovery: API reachable after {:.1f}s",
                _time.monotonic() - start,
            )
            return True

    return False
