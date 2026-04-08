"""Tests for workchain.retry — retrying_from_policy."""

from __future__ import annotations

import pytest

from workchain.models import RetryPolicy
from workchain.retry import retrying_from_policy


class TestRetryingFromPolicy:
    async def test_no_retries_on_success(self):
        call_count = 0

        async def succeeds():
            nonlocal call_count
            call_count += 1
            return "ok"

        retrying = retrying_from_policy(RetryPolicy(max_attempts=3, wait_seconds=0.01))
        async for attempt in retrying:
            with attempt:
                result = await succeeds()

        assert call_count == 1
        assert result == "ok"

    async def test_retries_on_failure(self):
        call_count = 0

        async def fails_once():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("fail")
            return "ok"

        retrying = retrying_from_policy(RetryPolicy(max_attempts=3, wait_seconds=0.01))
        async for attempt in retrying:
            with attempt:
                result = await fails_once()

        assert call_count == 2
        assert result == "ok"

    async def test_exhausts_attempts(self):
        call_count = 0

        async def always_fails():
            nonlocal call_count
            call_count += 1
            raise ValueError("always fail")

        retrying = retrying_from_policy(RetryPolicy(max_attempts=3, wait_seconds=0.01))
        with pytest.raises(ValueError, match="always fail"):  # noqa: PT012
            async for attempt in retrying:
                with attempt:
                    await always_fails()

        assert call_count == 3

    async def test_single_attempt(self):
        call_count = 0

        async def always_fails():
            nonlocal call_count
            call_count += 1
            raise ValueError("fail")

        retrying = retrying_from_policy(RetryPolicy(max_attempts=1, wait_seconds=0.01))
        with pytest.raises(ValueError, match="fail"):  # noqa: PT012
            async for attempt in retrying:
                with attempt:
                    await always_fails()

        assert call_count == 1

    async def test_reraise_preserves_exception_type(self):
        async def raises_runtime():
            raise RuntimeError("specific error")

        retrying = retrying_from_policy(RetryPolicy(max_attempts=1, wait_seconds=0.01))
        with pytest.raises(RuntimeError, match="specific error"):  # noqa: PT012
            async for attempt in retrying:
                with attempt:
                    await raises_runtime()
