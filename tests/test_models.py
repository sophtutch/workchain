"""Tests for workchain.models — StepRun, WorkflowRun, enums."""

from __future__ import annotations

from workchain import DependencyFailurePolicy, RetryPolicy, StepRun, StepStatus, WorkflowRun, WorkflowStatus


class TestStepStatus:
    def test_all_values(self):
        expected = {"pending", "running", "completed", "failed", "suspended", "awaiting_poll", "skipped"}
        assert {s.value for s in StepStatus} == expected


class TestWorkflowStatus:
    def test_all_values(self):
        expected = {"pending", "running", "completed", "failed", "suspended"}
        assert {s.value for s in WorkflowStatus} == expected


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
        assert sr.last_polled_at is None
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


class TestComputeStatus:
    def _run(self, steps: list[StepRun]) -> WorkflowRun:
        return WorkflowRun(workflow_name="t", workflow_version="1", steps=steps)

    def test_all_completed(self):
        run = self._run([StepRun(step_id="a", step_type="X", status=StepStatus.COMPLETED)])
        assert run.compute_status() == WorkflowStatus.COMPLETED

    def test_any_running(self):
        run = self._run([
            StepRun(step_id="a", step_type="X", status=StepStatus.COMPLETED),
            StepRun(step_id="b", step_type="X", status=StepStatus.RUNNING),
        ])
        assert run.compute_status() == WorkflowStatus.RUNNING

    def test_ready_pending_deps_met(self):
        run = self._run([
            StepRun(step_id="a", step_type="X", status=StepStatus.COMPLETED),
            StepRun(step_id="b", step_type="X", status=StepStatus.PENDING, depends_on=["a"]),
        ])
        assert run.compute_status() == WorkflowStatus.RUNNING

    def test_blocked_pending_deps_not_met(self):
        run = self._run([
            StepRun(step_id="a", step_type="X", status=StepStatus.SUSPENDED),
            StepRun(step_id="b", step_type="X", status=StepStatus.PENDING, depends_on=["a"]),
        ])
        assert run.compute_status() == WorkflowStatus.SUSPENDED

    def test_pending_with_future_retry_after(self):
        from datetime import UTC, datetime, timedelta

        run = self._run([
            StepRun(
                step_id="a", step_type="X", status=StepStatus.PENDING,
                retry_after=datetime.now(UTC) + timedelta(hours=1),
            ),
        ])
        assert run.compute_status() == WorkflowStatus.SUSPENDED

    def test_all_terminal_with_failed(self):
        run = self._run([
            StepRun(step_id="a", step_type="X", status=StepStatus.COMPLETED),
            StepRun(step_id="b", step_type="X", status=StepStatus.FAILED),
        ])
        assert run.compute_status() == WorkflowStatus.FAILED

    def test_awaiting_poll(self):
        run = self._run([
            StepRun(step_id="a", step_type="X", status=StepStatus.AWAITING_POLL),
        ])
        assert run.compute_status() == WorkflowStatus.SUSPENDED

    def test_recompute_status_sets_field(self):
        run = self._run([StepRun(step_id="a", step_type="X", status=StepStatus.COMPLETED)])
        run.status = WorkflowStatus.PENDING  # stale
        run.recompute_status()
        assert run.status == WorkflowStatus.COMPLETED


class TestComputeNeedsWorkAfter:
    def _run(self, steps: list[StepRun], **kwargs) -> WorkflowRun:
        return WorkflowRun(workflow_name="t", workflow_version="1", steps=steps, **kwargs)

    def test_terminal_returns_none(self):
        run = self._run(
            [StepRun(step_id="a", step_type="X", status=StepStatus.COMPLETED)],
            status=WorkflowStatus.COMPLETED,
        )
        assert run.compute_needs_work_after() is None

    def test_ready_pending_returns_now_or_past(self):
        from datetime import UTC, datetime

        run = self._run([StepRun(step_id="a", step_type="X", status=StepStatus.PENDING)])
        result = run.compute_needs_work_after()
        assert result is not None
        assert result <= datetime.now(UTC)

    def test_pending_with_future_retry_after(self):
        from datetime import UTC, datetime, timedelta

        future = datetime.now(UTC) + timedelta(hours=1)
        run = self._run([
            StepRun(step_id="a", step_type="X", status=StepStatus.PENDING, retry_after=future),
        ])
        assert run.compute_needs_work_after() == future

    def test_awaiting_poll_returns_next_poll_at(self):
        from datetime import UTC, datetime, timedelta

        poll_time = datetime.now(UTC) + timedelta(minutes=5)
        run = self._run([
            StepRun(step_id="a", step_type="X", status=StepStatus.AWAITING_POLL, next_poll_at=poll_time),
        ])
        assert run.compute_needs_work_after() == poll_time

    def test_suspended_event_only_returns_none(self):
        run = self._run([StepRun(step_id="a", step_type="X", status=StepStatus.SUSPENDED)])
        assert run.compute_needs_work_after() is None

    def test_multiple_actionable_returns_earliest(self):
        from datetime import UTC, datetime, timedelta

        soon = datetime.now(UTC) + timedelta(minutes=1)
        later = datetime.now(UTC) + timedelta(hours=1)
        run = self._run([
            StepRun(step_id="a", step_type="X", status=StepStatus.AWAITING_POLL, next_poll_at=later),
            StepRun(step_id="b", step_type="X", status=StepStatus.AWAITING_POLL, next_poll_at=soon),
        ])
        assert run.compute_needs_work_after() == soon

    def test_blocked_pending_returns_none(self):
        run = self._run([
            StepRun(step_id="a", step_type="X", status=StepStatus.SUSPENDED),
            StepRun(step_id="b", step_type="X", status=StepStatus.PENDING, depends_on=["a"]),
        ])
        assert run.compute_needs_work_after() is None


class TestRetryPolicy:
    def test_compute_delay_clamped_at_absolute_max(self):
        from workchain.models import _ABSOLUTE_MAX_DELAY_SECONDS

        policy = RetryPolicy(max_retries=10, delay_seconds=1.0, backoff_multiplier=100.0)
        delay = policy.compute_delay(attempt=5)
        assert delay == _ABSOLUTE_MAX_DELAY_SECONDS

    def test_compute_delay_respects_explicit_max(self):
        policy = RetryPolicy(
            max_retries=10, delay_seconds=1.0, backoff_multiplier=10.0, max_delay_seconds=5.0
        )
        delay = policy.compute_delay(attempt=5)
        assert delay == 5.0
