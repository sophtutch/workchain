"""Retry utilities wrapping tenacity with our RetryPolicy model."""

from __future__ import annotations

from tenacity import (
    AsyncRetrying,
    stop_after_attempt,
    wait_exponential,
)

from .models import RetryPolicy


def retrying_from_policy(policy: RetryPolicy) -> AsyncRetrying:
    """Build a tenacity AsyncRetrying instance from a RetryPolicy."""
    return AsyncRetrying(
        stop=stop_after_attempt(policy.max_attempts),
        wait=wait_exponential(
            multiplier=policy.wait_multiplier,
            min=policy.wait_seconds,
            max=policy.wait_max,
        ),
        reraise=True,
    )
