"""Retry utilities wrapping tenacity with our RetryPolicy model."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from tenacity import (
    AsyncRetrying,
    stop_after_attempt,
    wait_exponential,
)

from .models import RetryPolicy

logger = logging.getLogger(__name__)


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


async def run_with_retry(
    fn: Callable[..., Coroutine[Any, Any, Any]],
    policy: RetryPolicy,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """
    Execute an async callable with retries according to the given policy.

    Returns the result on success.
    Raises the last exception if all attempts are exhausted.
    """
    retrying = retrying_from_policy(policy)
    attempt_num = 0
    async for attempt in retrying:
        with attempt:
            attempt_num += 1
            logger.debug("Attempt %d/%d for %s", attempt_num, policy.max_attempts, fn.__name__)
            return await fn(*args, **kwargs)
