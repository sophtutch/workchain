"""Tests for workchain.exceptions."""

from __future__ import annotations

from workchain.exceptions import (
    ConcurrentModificationError,
    LeaseAcquisitionError,
    StepNotFoundError,
    WorkchainError,
    WorkflowRunNotFoundError,
    WorkflowValidationError,
)


class TestExceptionHierarchy:
    def test_all_inherit_from_workchain_error(self):
        assert issubclass(WorkflowValidationError, WorkchainError)
        assert issubclass(ConcurrentModificationError, WorkchainError)
        assert issubclass(LeaseAcquisitionError, WorkchainError)
        assert issubclass(StepNotFoundError, WorkchainError)
        assert issubclass(WorkflowRunNotFoundError, WorkchainError)

    def test_workchain_error_is_exception(self):
        assert issubclass(WorkchainError, Exception)


class TestConcurrentModificationError:
    def test_stores_run_id(self):
        err = ConcurrentModificationError("abc-123")
        assert err.run_id == "abc-123"
        assert "abc-123" in str(err)


class TestStepNotFoundError:
    def test_stores_step_type(self):
        err = StepNotFoundError("MyMissingStep")
        assert err.step_type == "MyMissingStep"
        assert "MyMissingStep" in str(err)


class TestWorkflowRunNotFoundError:
    def test_stores_identifier(self):
        err = WorkflowRunNotFoundError("run-456")
        assert err.identifier == "run-456"
        assert "run-456" in str(err)
