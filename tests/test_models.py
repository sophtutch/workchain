"""Tests for workchain.models — StepRun, WorkflowRun, enums."""

from __future__ import annotations

from workchain import DependencyFailurePolicy, StepRun, StepStatus, WorkflowRun, WorkflowStatus
from workchain.models import ACTIVE_WORKFLOW_STATUSES, LEASABLE_STATUSES, POLLABLE_STATUS


class TestStepStatus:
    def test_all_values(self):
        expected = {"pending", "running", "completed", "failed", "suspended", "awaiting_poll", "skipped"}
        assert {s.value for s in StepStatus} == expected


class TestWorkflowStatus:
    def test_all_values(self):
        expected = {"pending", "running", "completed", "failed", "suspended"}
        assert {s.value for s in WorkflowStatus} == expected


class TestStatusSets:
    def test_active_statuses(self):
        assert WorkflowStatus.PENDING in ACTIVE_WORKFLOW_STATUSES
        assert WorkflowStatus.RUNNING in ACTIVE_WORKFLOW_STATUSES
        assert WorkflowStatus.SUSPENDED in ACTIVE_WORKFLOW_STATUSES
        assert WorkflowStatus.COMPLETED not in ACTIVE_WORKFLOW_STATUSES

    def test_leasable_statuses(self):
        assert WorkflowStatus.PENDING in LEASABLE_STATUSES
        assert WorkflowStatus.RUNNING in LEASABLE_STATUSES
        assert WorkflowStatus.SUSPENDED not in LEASABLE_STATUSES

    def test_pollable_status(self):
        assert POLLABLE_STATUS == WorkflowStatus.SUSPENDED


class TestStepRun:
    def test_defaults(self):
        sr = StepRun(step_id="s1", step_type="NoOpStep")
        assert sr.status == StepStatus.PENDING
        assert sr.depends_on == []
        assert sr.on_dependency_failure == DependencyFailurePolicy.FAIL
        assert sr.output == {}
        assert sr.error is None
        assert sr.resume_correlation_id is None
        assert sr.next_poll_at is None
        assert sr.started_at is None
        assert sr.completed_at is None


class TestWorkflowRun:
    def test_defaults(self):
        run = WorkflowRun(workflow_name="test", workflow_version="1.0")
        assert run.status == WorkflowStatus.PENDING
        assert run.steps == []
        assert run.context == {}
        assert run.doc_version == 0
        assert run.lease_owner is None

    def test_get_step_found(self):
        run = WorkflowRun(
            workflow_name="test",
            workflow_version="1.0",
            steps=[
                StepRun(step_id="a", step_type="X"),
                StepRun(step_id="b", step_type="Y"),
            ],
        )
        assert run.get_step("b").step_id == "b"

    def test_get_step_not_found(self):
        run = WorkflowRun(workflow_name="test", workflow_version="1.0")
        assert run.get_step("missing") is None

    def test_is_terminal_completed(self):
        run = WorkflowRun(workflow_name="t", workflow_version="1", status=WorkflowStatus.COMPLETED)
        assert run.is_terminal() is True

    def test_is_terminal_failed(self):
        run = WorkflowRun(workflow_name="t", workflow_version="1", status=WorkflowStatus.FAILED)
        assert run.is_terminal() is True

    def test_is_terminal_running(self):
        run = WorkflowRun(workflow_name="t", workflow_version="1", status=WorkflowStatus.RUNNING)
        assert run.is_terminal() is False

    def test_is_terminal_pending(self):
        run = WorkflowRun(workflow_name="t", workflow_version="1", status=WorkflowStatus.PENDING)
        assert run.is_terminal() is False
