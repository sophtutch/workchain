"""Tests for workchain.exceptions — exception hierarchy and importability."""

from __future__ import annotations

import pytest

from workchain.exceptions import (
    FenceRejectedError,
    HandlerError,
    LockError,
    RecoveryError,
    RetryExhaustedError,
    StepError,
    StepTimeoutError,
    WorkchainError,
)


class TestExceptionImports:
    """All exception classes are importable from workchain and workchain.exceptions."""

    def test_importable_from_package(self):
        import workchain

        assert workchain.WorkchainError is WorkchainError
        assert workchain.StepError is StepError
        assert workchain.StepTimeoutError is StepTimeoutError
        assert workchain.RetryExhaustedError is RetryExhaustedError
        assert workchain.HandlerError is HandlerError
        assert workchain.LockError is LockError
        assert workchain.FenceRejectedError is FenceRejectedError
        assert workchain.RecoveryError is RecoveryError


class TestExceptionHierarchy:
    """Exception classes follow the expected inheritance chain."""

    def test_workchain_error_is_exception(self):
        assert issubclass(WorkchainError, Exception)

    def test_step_error_is_workchain_error(self):
        assert issubclass(StepError, WorkchainError)

    def test_step_timeout_error_is_step_error(self):
        assert issubclass(StepTimeoutError, StepError)

    def test_retry_exhausted_error_is_step_error(self):
        assert issubclass(RetryExhaustedError, StepError)

    def test_handler_error_is_step_error(self):
        assert issubclass(HandlerError, StepError)

    def test_lock_error_is_workchain_error(self):
        assert issubclass(LockError, WorkchainError)

    def test_fence_rejected_error_is_lock_error(self):
        assert issubclass(FenceRejectedError, LockError)

    def test_recovery_error_is_workchain_error(self):
        assert issubclass(RecoveryError, WorkchainError)


class TestCatchAll:
    """WorkchainError catches all workchain-specific exceptions."""

    @pytest.mark.parametrize(
        "exc_class",
        [
            StepError,
            StepTimeoutError,
            RetryExhaustedError,
            HandlerError,
            LockError,
            FenceRejectedError,
            RecoveryError,
        ],
    )
    def test_workchain_error_catches_all(self, exc_class):
        with pytest.raises(WorkchainError):
            raise exc_class("test message")

    def test_lock_error_catches_fence_rejected(self):
        with pytest.raises(LockError):
            raise FenceRejectedError("stale fence")

    def test_step_error_catches_handler_error(self):
        with pytest.raises(StepError):
            raise HandlerError("bad return")

    def test_step_error_catches_retry_exhausted(self):
        with pytest.raises(StepError):
            raise RetryExhaustedError("all retries failed")

    def test_step_error_catches_timeout(self):
        with pytest.raises(StepError):
            raise StepTimeoutError("too slow")

    def test_message_preserved(self):
        msg = "something went wrong"
        err = WorkchainError(msg)
        assert str(err) == msg
