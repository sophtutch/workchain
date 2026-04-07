"""Tests for workchain.audit_report — HTML report generation from audit events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from workchain.audit import AuditEvent, AuditEventType
from workchain.audit_report import generate_audit_report


def _ts(offset_s: float = 0) -> datetime:
    """Create a UTC timestamp with an optional offset in seconds."""
    return datetime(2026, 4, 2, 10, 0, 0, tzinfo=UTC) + timedelta(seconds=offset_s)


def _make_sync_workflow_events() -> list[AuditEvent]:
    """Build a minimal set of events for a completed sync workflow."""
    return [
        AuditEvent(
            workflow_id="wf1", workflow_name="test_sync",
            event_type=AuditEventType.STEP_CLAIMED,
            step_index=0, step_name="greet",
            instance_id="inst_a1", fence_token=1, fence_token_before=0,
            workflow_status="running", workflow_status_before="pending",
            timestamp=_ts(0), sequence=1,
        ),
        AuditEvent(
            workflow_id="wf1", workflow_name="test_sync",
            event_type=AuditEventType.STEP_SUBMITTED,
            instance_id="inst_a1", fence_token=1,
            step_index=0, step_name="greet", step_handler="tests.greet",
            step_status="submitted", step_status_before="pending",
            timestamp=_ts(0.1), sequence=2,
        ),
        AuditEvent(
            workflow_id="wf1", workflow_name="test_sync",
            event_type=AuditEventType.STEP_RUNNING,
            instance_id="inst_a1", fence_token=1,
            step_index=0, step_name="greet", step_handler="tests.greet",
            step_status="running", step_status_before="submitted",
            attempt=1, max_attempts=3,
            timestamp=_ts(0.2), sequence=3,
        ),
        AuditEvent(
            workflow_id="wf1", workflow_name="test_sync",
            event_type=AuditEventType.STEP_COMPLETED,
            instance_id="inst_a1", fence_token=1,
            step_index=0, step_name="greet", step_handler="tests.greet",
            step_status="completed", step_status_before="running",
            result_summary={"greeting": "Hello, World!"},
            timestamp=_ts(0.3), sequence=4,
        ),
        AuditEvent(
            workflow_id="wf1", workflow_name="test_sync",
            event_type=AuditEventType.STEP_ADVANCED,
            instance_id="inst_a1", fence_token=1,
            step_index=0, step_name="greet",
            timestamp=_ts(0.4), sequence=5,
        ),
        AuditEvent(
            workflow_id="wf1", workflow_name="test_sync",
            event_type=AuditEventType.WORKFLOW_COMPLETED,
            instance_id="inst_a1", fence_token=1,
            workflow_status="completed", workflow_status_before="running",
            lock_released=True,
            timestamp=_ts(0.5), sequence=6,
        ),
    ]


def _make_failed_workflow_events() -> list[AuditEvent]:
    """Build events for a workflow where a step failed."""
    return [
        AuditEvent(
            workflow_id="wf2", workflow_name="test_fail",
            event_type=AuditEventType.STEP_CLAIMED,
            step_index=0, step_name="fail",
            instance_id="inst_a1", fence_token=1, fence_token_before=0,
            workflow_status="running", workflow_status_before="pending",
            timestamp=_ts(0), sequence=1,
        ),
        AuditEvent(
            workflow_id="wf2", workflow_name="test_fail",
            event_type=AuditEventType.STEP_SUBMITTED,
            instance_id="inst_a1", fence_token=1,
            step_index=0, step_name="fail", step_handler="tests.fail_always",
            step_status="submitted", step_status_before="pending",
            timestamp=_ts(0.1), sequence=2,
        ),
        AuditEvent(
            workflow_id="wf2", workflow_name="test_fail",
            event_type=AuditEventType.STEP_RUNNING,
            instance_id="inst_a1", fence_token=1,
            step_index=0, step_name="fail", step_handler="tests.fail_always",
            step_status="running", step_status_before="submitted",
            attempt=1, max_attempts=1,
            timestamp=_ts(0.2), sequence=3,
        ),
        AuditEvent(
            workflow_id="wf2", workflow_name="test_fail",
            event_type=AuditEventType.STEP_FAILED,
            instance_id="inst_a1", fence_token=1,
            step_index=0, step_name="fail", step_handler="tests.fail_always",
            step_status="failed", step_status_before="running",
            error="RuntimeError: intentional failure",
            timestamp=_ts(0.3), sequence=4,
        ),
        AuditEvent(
            workflow_id="wf2", workflow_name="test_fail",
            event_type=AuditEventType.WORKFLOW_FAILED,
            instance_id="inst_a1", fence_token=1,
            workflow_status="failed", workflow_status_before="running",
            timestamp=_ts(0.4), sequence=5,
        ),
    ]


def _make_async_workflow_events() -> list[AuditEvent]:
    """Build events for a workflow with an async step + polls."""
    return [
        AuditEvent(
            workflow_id="wf3", workflow_name="test_async",
            event_type=AuditEventType.STEP_CLAIMED,
            step_index=0, step_name="deploy",
            instance_id="inst_a1", fence_token=1, fence_token_before=0,
            workflow_status="running", workflow_status_before="pending",
            timestamp=_ts(0), sequence=1,
        ),
        AuditEvent(
            workflow_id="wf3", workflow_name="test_async",
            event_type=AuditEventType.STEP_SUBMITTED,
            instance_id="inst_a1", fence_token=1,
            step_index=0, step_name="deploy", step_handler="tests.deploy",
            step_status="submitted", step_status_before="pending",
            is_async=True,
            timestamp=_ts(0.1), sequence=2,
        ),
        AuditEvent(
            workflow_id="wf3", workflow_name="test_async",
            event_type=AuditEventType.STEP_RUNNING,
            instance_id="inst_a1", fence_token=1,
            step_index=0, step_name="deploy", step_handler="tests.deploy",
            step_status="running", step_status_before="submitted",
            is_async=True, attempt=1, max_attempts=1,
            timestamp=_ts(0.2), sequence=3,
        ),
        AuditEvent(
            workflow_id="wf3", workflow_name="test_async",
            event_type=AuditEventType.STEP_BLOCKED,
            instance_id="inst_a1", fence_token=1,
            step_index=0, step_name="deploy", step_handler="tests.deploy",
            step_status="blocked", step_status_before="running",
            is_async=True,
            result_summary={"job_id": "job_42"},
            timestamp=_ts(0.3), sequence=4,
        ),
        AuditEvent(
            workflow_id="wf3", workflow_name="test_async",
            event_type=AuditEventType.LOCK_RELEASED,
            instance_id="inst_a1", fence_token=1,
            lock_released=True,
            timestamp=_ts(0.4), sequence=5,
        ),
        # Poll 1: not complete
        AuditEvent(
            workflow_id="wf3", workflow_name="test_async",
            event_type=AuditEventType.POLL_CHECKED,
            instance_id="inst_b2", fence_token=2,
            step_index=0, step_name="deploy", step_handler="tests.deploy",
            step_status="blocked", is_async=True,
            poll_count=1, poll_progress=0.5, poll_message="deploying...",
            timestamp=_ts(5), sequence=6,
        ),
        AuditEvent(
            workflow_id="wf3", workflow_name="test_async",
            event_type=AuditEventType.LOCK_RELEASED,
            instance_id="inst_b2", fence_token=2,
            lock_released=True,
            timestamp=_ts(5.1), sequence=7,
        ),
        # Poll 2: complete — STEP_COMPLETED records the state transition, POLL_CHECKED emitted after fenced write
        AuditEvent(
            workflow_id="wf3", workflow_name="test_async",
            event_type=AuditEventType.STEP_COMPLETED,
            instance_id="inst_a1", fence_token=3,
            step_index=0, step_name="deploy", step_handler="tests.deploy",
            step_status="completed", step_status_before="blocked",
            is_async=True,
            poll_count=2, poll_progress=1.0, poll_message="done",
            result_summary={"job_id": "job_42", "completed_at": "2026-04-02T10:00:10"},
            timestamp=_ts(10), sequence=8,
        ),
        AuditEvent(
            workflow_id="wf3", workflow_name="test_async",
            event_type=AuditEventType.POLL_CHECKED,
            instance_id="inst_a1", fence_token=3,
            step_index=0, step_name="deploy", step_handler="tests.deploy",
            step_status="completed", is_async=True,
            poll_count=2, poll_progress=1.0, poll_message="done",
            timestamp=_ts(10.1), sequence=9,
        ),
        AuditEvent(
            workflow_id="wf3", workflow_name="test_async",
            event_type=AuditEventType.STEP_ADVANCED,
            instance_id="inst_a1", fence_token=3,
            step_index=0, step_name="deploy",
            timestamp=_ts(10.2), sequence=10,
        ),
        AuditEvent(
            workflow_id="wf3", workflow_name="test_async",
            event_type=AuditEventType.WORKFLOW_COMPLETED,
            instance_id="inst_a1", fence_token=3,
            workflow_status="completed", workflow_status_before="running",
            lock_released=True,
            timestamp=_ts(10.3), sequence=11,
        ),
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _make_cancelled_workflow_events() -> list[AuditEvent]:
    """Build events for a cancelled workflow."""
    return [
        AuditEvent(
            workflow_id="wf_cancel", workflow_name="test_cancel",
            event_type=AuditEventType.STEP_CLAIMED,
            step_index=0, step_name="setup",
            instance_id="inst_a1", fence_token=1, fence_token_before=0,
            workflow_status="running", workflow_status_before="pending",
            timestamp=_ts(0), sequence=1,
        ),
        AuditEvent(
            workflow_id="wf_cancel", workflow_name="test_cancel",
            event_type=AuditEventType.STEP_SUBMITTED,
            instance_id="inst_a1", fence_token=1,
            step_index=0, step_name="setup", step_handler="tests.setup",
            step_status="submitted", step_status_before="pending",
            timestamp=_ts(0.1), sequence=2,
        ),
        AuditEvent(
            workflow_id="wf_cancel", workflow_name="test_cancel",
            event_type=AuditEventType.WORKFLOW_CANCELLED,
            instance_id="inst_a1", fence_token=1,
            workflow_status="cancelled", workflow_status_before="running",
            timestamp=_ts(0.5), sequence=3,
        ),
    ]


class TestCancelledWorkflowReport:
    def test_cancelled_workflow_report(self):
        events = _make_cancelled_workflow_events()
        html = generate_audit_report(events)
        assert html, "Report should not be empty"
        assert "cancelled" in html.lower()
        assert "Test Cancel" in html
        assert "Workflow Cancelled" in html


class TestGenerateAuditReport:
    def test_empty_events(self):
        html = generate_audit_report([])
        assert "No audit events found" in html

    def test_sync_workflow_report(self):
        events = _make_sync_workflow_events()
        report = generate_audit_report(events)

        # Basic structure
        assert "<!DOCTYPE html>" in report
        assert "Test Sync" in report
        assert "Execution Report" in report

        # Start section
        assert "lock acquired" in report.lower() or "lock-claim" in report
        assert "fence" in report.lower()

        # Step section
        assert "greet" in report
        assert "SUBMITTED" in report
        assert "COMPLETED" in report

        # Summary
        assert "completed" in report.lower()
        assert "1 steps" in report

        # Completion section
        assert "Workflow End" in report

    def test_failed_workflow_report(self):
        events = _make_failed_workflow_events()
        report = generate_audit_report(events)

        assert "Test Fail" in report
        assert "FAILED" in report
        assert "intentional failure" in report
        assert "Workflow Failed" in report

    def test_async_workflow_report(self):
        events = _make_async_workflow_events()
        report = generate_audit_report(events)

        assert "Test Async" in report
        assert "deploy" in report

        # Async-specific
        assert "BLOCKED" in report
        assert "poll" in report.lower()
        assert "50%" in report or "0.5" in report

        # Multiple instances
        assert "inst_a1" in report
        assert "inst_b2" in report

    def test_async_step_shows_completed_status(self):
        """Async steps that finish via POLL_CHECKED should show completed."""
        events = _make_async_workflow_events()
        report = generate_audit_report(events)

        # Should show completed status in transitions column
        assert "&rarr; completed" in report
        # Should show result data in doc panel
        assert "job_42" in report

    def test_state_transitions_table(self):
        events = _make_sync_workflow_events()
        report = generate_audit_report(events)

        assert "State Transitions" in report
        assert "state-table" in report
        assert "state-badge" in report

    def test_retry_sub_track(self):
        """A step with 2 STEP_RUNNING events should show retry dots."""
        events = [
            AuditEvent(
                workflow_id="wf4", workflow_name="test_retry",
                event_type=AuditEventType.STEP_CLAIMED,
                step_index=0, step_name="flaky",
                instance_id="inst_a1", fence_token=1, fence_token_before=0,
                workflow_status="running", workflow_status_before="pending",
                timestamp=_ts(0), sequence=1,
            ),
            AuditEvent(
                workflow_id="wf4", workflow_name="test_retry",
                event_type=AuditEventType.STEP_SUBMITTED,
                instance_id="inst_a1", fence_token=1,
                step_index=0, step_name="flaky", step_handler="tests.flaky",
                step_status="submitted", step_status_before="pending",
                timestamp=_ts(0.1), sequence=2,
            ),
            AuditEvent(
                workflow_id="wf4", workflow_name="test_retry",
                event_type=AuditEventType.STEP_RUNNING,
                instance_id="inst_a1", fence_token=1,
                step_index=0, step_name="flaky", step_handler="tests.flaky",
                step_status="running", step_status_before="submitted",
                attempt=1, max_attempts=3,
                timestamp=_ts(0.2), sequence=3,
            ),
            AuditEvent(
                workflow_id="wf4", workflow_name="test_retry",
                event_type=AuditEventType.STEP_RUNNING,
                instance_id="inst_a1", fence_token=1,
                step_index=0, step_name="flaky", step_handler="tests.flaky",
                step_status="running", step_status_before="submitted",
                attempt=2, max_attempts=3,
                timestamp=_ts(0.3), sequence=4,
            ),
            AuditEvent(
                workflow_id="wf4", workflow_name="test_retry",
                event_type=AuditEventType.STEP_COMPLETED,
                instance_id="inst_a1", fence_token=1,
                step_index=0, step_name="flaky", step_handler="tests.flaky",
                step_status="completed", step_status_before="running",
                result_summary={"ok": True},
                timestamp=_ts(0.4), sequence=5,
            ),
            AuditEvent(
                workflow_id="wf4", workflow_name="test_retry",
                event_type=AuditEventType.STEP_ADVANCED,
                instance_id="inst_a1", fence_token=1,
                step_index=0, step_name="flaky",
                timestamp=_ts(0.5), sequence=6,
            ),
            AuditEvent(
                workflow_id="wf4", workflow_name="test_retry",
                event_type=AuditEventType.WORKFLOW_COMPLETED,
                instance_id="inst_a1", fence_token=1,
                workflow_status="completed", workflow_status_before="running",
                lock_released=True,
                timestamp=_ts(0.6), sequence=7,
            ),
        ]
        report = generate_audit_report(events)

        # Should show retry track
        assert "retry-track" in report
        assert "Attempt 1" in report
        assert "Attempt 2" in report
        assert "1 retry" in report

    def test_multi_step_workflow(self):
        """A workflow with 2 steps should have 2 step sections."""
        events = [
            # Step 0 claimed (per-step claiming)
            AuditEvent(
                workflow_id="wf5", workflow_name="multi",
                event_type=AuditEventType.STEP_CLAIMED,
                step_index=0, step_name="s1",
                instance_id="inst_a1", fence_token=1, fence_token_before=0,
                workflow_status="running", workflow_status_before="pending",
                timestamp=_ts(0), sequence=1,
            ),
            # Step 0
            AuditEvent(
                workflow_id="wf5", workflow_name="multi",
                event_type=AuditEventType.STEP_SUBMITTED,
                step_index=0, step_name="s1", step_handler="tests.noop",
                instance_id="inst_a1", fence_token=1,
                step_status="submitted", step_status_before="pending",
                timestamp=_ts(0.1), sequence=2,
            ),
            AuditEvent(
                workflow_id="wf5", workflow_name="multi",
                event_type=AuditEventType.STEP_RUNNING,
                step_index=0, step_name="s1", step_handler="tests.noop",
                instance_id="inst_a1", fence_token=1,
                step_status="running", attempt=1, max_attempts=1,
                timestamp=_ts(0.2), sequence=3,
            ),
            AuditEvent(
                workflow_id="wf5", workflow_name="multi",
                event_type=AuditEventType.STEP_COMPLETED,
                step_index=0, step_name="s1", step_handler="tests.noop",
                instance_id="inst_a1", fence_token=1,
                step_status="completed",
                timestamp=_ts(0.3), sequence=4,
            ),
            AuditEvent(
                workflow_id="wf5", workflow_name="multi",
                event_type=AuditEventType.STEP_ADVANCED,
                step_index=0, step_name="s1",
                instance_id="inst_a1", fence_token=1,
                timestamp=_ts(0.4), sequence=5,
            ),
            # Step 1
            AuditEvent(
                workflow_id="wf5", workflow_name="multi",
                event_type=AuditEventType.STEP_SUBMITTED,
                step_index=1, step_name="s2", step_handler="tests.noop",
                instance_id="inst_a1", fence_token=1,
                step_status="submitted", step_status_before="pending",
                timestamp=_ts(0.5), sequence=6,
            ),
            AuditEvent(
                workflow_id="wf5", workflow_name="multi",
                event_type=AuditEventType.STEP_RUNNING,
                step_index=1, step_name="s2", step_handler="tests.noop",
                instance_id="inst_a1", fence_token=1,
                step_status="running", attempt=1, max_attempts=1,
                timestamp=_ts(0.6), sequence=7,
            ),
            AuditEvent(
                workflow_id="wf5", workflow_name="multi",
                event_type=AuditEventType.STEP_COMPLETED,
                step_index=1, step_name="s2", step_handler="tests.noop",
                instance_id="inst_a1", fence_token=1,
                step_status="completed",
                timestamp=_ts(0.7), sequence=8,
            ),
            AuditEvent(
                workflow_id="wf5", workflow_name="multi",
                event_type=AuditEventType.STEP_ADVANCED,
                step_index=1, step_name="s2",
                instance_id="inst_a1", fence_token=1,
                timestamp=_ts(0.8), sequence=9,
            ),
            AuditEvent(
                workflow_id="wf5", workflow_name="multi",
                event_type=AuditEventType.WORKFLOW_COMPLETED,
                instance_id="inst_a1", fence_token=1,
                workflow_status="completed", workflow_status_before="running",
                lock_released=True,
                timestamp=_ts(0.9), sequence=10,
            ),
        ]
        report = generate_audit_report(events)

        assert "Step 1" in report
        assert "Step 2" in report
        assert report.count("step-section") >= 6  # 3 sections * 2 (opening + class ref)

    def test_doc_diff_from_result_summary(self):
        """Completed step with result_summary should show doc diff."""
        events = _make_sync_workflow_events()
        report = generate_audit_report(events)

        assert "Hello, World!" in report
        assert "mongo-doc" in report

    def test_report_is_self_contained_html(self):
        """Report should be valid self-contained HTML with CSS."""
        events = _make_sync_workflow_events()
        report = generate_audit_report(events)

        assert report.startswith("<!DOCTYPE html>")
        assert "<style>" in report
        assert "</style>" in report
        assert "</html>" in report
        # No external dependencies
        assert "href=" not in report or 'href="http' not in report
        assert "<script" not in report


# ---------------------------------------------------------------------------
# Parallel / dependency-aware report tests
# ---------------------------------------------------------------------------


def _make_parallel_workflow_events() -> list[AuditEvent]:
    """Build events for a diamond-pattern workflow: 2 root steps -> join step.

    step_a (root) ──┐
                     ├──> step_c (depends on both)
    step_b (root) ──┘
    """
    return [
        # step_a claimed and completed
        AuditEvent(
            workflow_id="wf_par", workflow_name="parallel_test",
            event_type=AuditEventType.STEP_CLAIMED,
            step_index=0, step_name="step_a",
            step_depends_on=[],
            instance_id="inst_1", fence_token=1, fence_token_before=0,
            workflow_status="running", workflow_status_before="pending",
            timestamp=_ts(0), sequence=1,
        ),
        AuditEvent(
            workflow_id="wf_par", workflow_name="parallel_test",
            event_type=AuditEventType.STEP_SUBMITTED,
            step_index=0, step_name="step_a", step_handler="tests.step_a",
            step_depends_on=[],
            instance_id="inst_1", fence_token=1,
            step_status="submitted", step_status_before="pending",
            timestamp=_ts(0.1), sequence=2,
        ),
        AuditEvent(
            workflow_id="wf_par", workflow_name="parallel_test",
            event_type=AuditEventType.STEP_RUNNING,
            step_index=0, step_name="step_a", step_handler="tests.step_a",
            step_depends_on=[],
            instance_id="inst_1", fence_token=1,
            step_status="running", step_status_before="submitted",
            attempt=1, max_attempts=1,
            timestamp=_ts(0.2), sequence=3,
        ),
        AuditEvent(
            workflow_id="wf_par", workflow_name="parallel_test",
            event_type=AuditEventType.STEP_COMPLETED,
            step_index=0, step_name="step_a", step_handler="tests.step_a",
            step_depends_on=[],
            instance_id="inst_1", fence_token=1,
            step_status="completed", step_status_before="running",
            result_summary={"value": "a_done"},
            timestamp=_ts(0.5), sequence=4,
        ),
        # step_b claimed and completed (concurrent with step_a on another instance)
        AuditEvent(
            workflow_id="wf_par", workflow_name="parallel_test",
            event_type=AuditEventType.STEP_CLAIMED,
            step_index=1, step_name="step_b",
            step_depends_on=[],
            instance_id="inst_2", fence_token=1, fence_token_before=0,
            workflow_status="running",
            timestamp=_ts(0.05), sequence=5,
        ),
        AuditEvent(
            workflow_id="wf_par", workflow_name="parallel_test",
            event_type=AuditEventType.STEP_SUBMITTED,
            step_index=1, step_name="step_b", step_handler="tests.step_b",
            step_depends_on=[],
            instance_id="inst_2", fence_token=1,
            step_status="submitted", step_status_before="pending",
            timestamp=_ts(0.15), sequence=6,
        ),
        AuditEvent(
            workflow_id="wf_par", workflow_name="parallel_test",
            event_type=AuditEventType.STEP_RUNNING,
            step_index=1, step_name="step_b", step_handler="tests.step_b",
            step_depends_on=[],
            instance_id="inst_2", fence_token=1,
            step_status="running", step_status_before="submitted",
            attempt=1, max_attempts=1,
            timestamp=_ts(0.25), sequence=7,
        ),
        AuditEvent(
            workflow_id="wf_par", workflow_name="parallel_test",
            event_type=AuditEventType.STEP_COMPLETED,
            step_index=1, step_name="step_b", step_handler="tests.step_b",
            step_depends_on=[],
            instance_id="inst_2", fence_token=1,
            step_status="completed", step_status_before="running",
            result_summary={"value": "b_done"},
            timestamp=_ts(0.6), sequence=8,
        ),
        # step_c: depends on step_a and step_b
        AuditEvent(
            workflow_id="wf_par", workflow_name="parallel_test",
            event_type=AuditEventType.STEP_CLAIMED,
            step_index=2, step_name="step_c",
            step_depends_on=["step_a", "step_b"],
            instance_id="inst_1", fence_token=1, fence_token_before=0,
            workflow_status="running",
            timestamp=_ts(1.0), sequence=9,
        ),
        AuditEvent(
            workflow_id="wf_par", workflow_name="parallel_test",
            event_type=AuditEventType.STEP_SUBMITTED,
            step_index=2, step_name="step_c", step_handler="tests.step_c",
            step_depends_on=["step_a", "step_b"],
            instance_id="inst_1", fence_token=1,
            step_status="submitted", step_status_before="pending",
            timestamp=_ts(1.1), sequence=10,
        ),
        AuditEvent(
            workflow_id="wf_par", workflow_name="parallel_test",
            event_type=AuditEventType.STEP_RUNNING,
            step_index=2, step_name="step_c", step_handler="tests.step_c",
            step_depends_on=["step_a", "step_b"],
            instance_id="inst_1", fence_token=1,
            step_status="running", step_status_before="submitted",
            attempt=1, max_attempts=1,
            timestamp=_ts(1.2), sequence=11,
        ),
        AuditEvent(
            workflow_id="wf_par", workflow_name="parallel_test",
            event_type=AuditEventType.STEP_COMPLETED,
            step_index=2, step_name="step_c", step_handler="tests.step_c",
            step_depends_on=["step_a", "step_b"],
            instance_id="inst_1", fence_token=1,
            step_status="completed", step_status_before="running",
            result_summary={"merged": True},
            timestamp=_ts(1.5), sequence=12,
        ),
        AuditEvent(
            workflow_id="wf_par", workflow_name="parallel_test",
            event_type=AuditEventType.WORKFLOW_COMPLETED,
            instance_id="inst_1", fence_token=1,
            workflow_status="completed", workflow_status_before="running",
            lock_released=True,
            timestamp=_ts(1.6), sequence=13,
        ),
    ]


class TestParallelWorkflowReport:
    def test_dependency_graph_displayed(self):
        """Parallel workflows should show a dependency graph overview."""
        events = _make_parallel_workflow_events()
        report = generate_audit_report(events)
        assert "dep-graph" in report
        assert "Dependency Graph" in report

    def test_root_steps_indicated(self):
        """Root steps should be marked as root."""
        events = _make_parallel_workflow_events()
        report = generate_audit_report(events)
        assert "root step" in report.lower() or "root-tag" in report

    def test_dependency_info_on_join_step(self):
        """Join step should show its dependencies."""
        events = _make_parallel_workflow_events()
        report = generate_audit_report(events)
        assert "Depends on" in report
        assert "step_a" in report
        assert "step_b" in report

    def test_parallel_group_wrapper(self):
        """Parallel root steps should be wrapped in a parallel group."""
        events = _make_parallel_workflow_events()
        report = generate_audit_report(events)
        assert "parallel-group" in report
        assert "Parallel execution" in report

    def test_parallel_label_in_graph(self):
        """Dependency graph should indicate which tier is parallel."""
        events = _make_parallel_workflow_events()
        report = generate_audit_report(events)
        assert "parallel" in report.lower()

    def test_graph_shown_for_sequential(self):
        """Sequential workflows with dependency info should also show a graph."""
        # Build a 2-step sequential workflow with step_depends_on
        events = [
            AuditEvent(
                workflow_id="wf_seq", workflow_name="seq_test",
                event_type=AuditEventType.STEP_CLAIMED,
                step_index=0, step_name="first",
                step_depends_on=[],
                instance_id="inst_1", fence_token=1, fence_token_before=0,
                workflow_status="running", workflow_status_before="pending",
                timestamp=_ts(0), sequence=1,
            ),
            AuditEvent(
                workflow_id="wf_seq", workflow_name="seq_test",
                event_type=AuditEventType.STEP_COMPLETED,
                step_index=0, step_name="first", step_handler="tests.first",
                step_depends_on=[],
                instance_id="inst_1", fence_token=1,
                step_status="completed", step_status_before="running",
                result_summary={"ok": True},
                timestamp=_ts(0.5), sequence=2,
            ),
            AuditEvent(
                workflow_id="wf_seq", workflow_name="seq_test",
                event_type=AuditEventType.STEP_CLAIMED,
                step_index=1, step_name="second",
                step_depends_on=["first"],
                instance_id="inst_1", fence_token=2, fence_token_before=1,
                workflow_status="running",
                timestamp=_ts(1.0), sequence=3,
            ),
            AuditEvent(
                workflow_id="wf_seq", workflow_name="seq_test",
                event_type=AuditEventType.STEP_COMPLETED,
                step_index=1, step_name="second", step_handler="tests.second",
                step_depends_on=["first"],
                instance_id="inst_1", fence_token=2,
                step_status="completed", step_status_before="running",
                result_summary={"ok": True},
                timestamp=_ts(1.5), sequence=4,
            ),
            AuditEvent(
                workflow_id="wf_seq", workflow_name="seq_test",
                event_type=AuditEventType.WORKFLOW_COMPLETED,
                instance_id="inst_1", fence_token=2,
                workflow_status="completed", workflow_status_before="running",
                timestamp=_ts(2.0), sequence=5,
            ),
        ]
        report = generate_audit_report(events)
        assert "Dependency Graph" in report
        assert "dep-flow" in report


# ---------------------------------------------------------------------------
# Async step failure report tests
# ---------------------------------------------------------------------------


def _make_async_poll_timeout_events() -> list[AuditEvent]:
    """Build events for an async step that fails due to poll timeout."""
    return [
        AuditEvent(
            workflow_id="wf_to", workflow_name="test_timeout",
            event_type=AuditEventType.STEP_CLAIMED,
            step_index=0, step_name="slow_job",
            instance_id="inst_a1", fence_token=1, fence_token_before=0,
            workflow_status="running", workflow_status_before="pending",
            timestamp=_ts(0), sequence=1,
        ),
        AuditEvent(
            workflow_id="wf_to", workflow_name="test_timeout",
            event_type=AuditEventType.STEP_SUBMITTED,
            instance_id="inst_a1", fence_token=1,
            step_index=0, step_name="slow_job", step_handler="tests.slow_job",
            step_status="submitted", step_status_before="pending",
            is_async=True,
            timestamp=_ts(0.1), sequence=2,
        ),
        AuditEvent(
            workflow_id="wf_to", workflow_name="test_timeout",
            event_type=AuditEventType.STEP_RUNNING,
            instance_id="inst_a1", fence_token=1,
            step_index=0, step_name="slow_job", step_handler="tests.slow_job",
            step_status="running", step_status_before="submitted",
            is_async=True, attempt=1, max_attempts=1,
            timestamp=_ts(0.2), sequence=3,
        ),
        AuditEvent(
            workflow_id="wf_to", workflow_name="test_timeout",
            event_type=AuditEventType.STEP_BLOCKED,
            instance_id="inst_a1", fence_token=1,
            step_index=0, step_name="slow_job", step_handler="tests.slow_job",
            step_status="blocked", step_status_before="running",
            is_async=True,
            result_summary={"job_id": "job_slow"},
            timestamp=_ts(0.3), sequence=4,
        ),
        AuditEvent(
            workflow_id="wf_to", workflow_name="test_timeout",
            event_type=AuditEventType.LOCK_RELEASED,
            instance_id="inst_a1", fence_token=1,
            lock_released=True,
            timestamp=_ts(0.4), sequence=5,
        ),
        # Intermediate poll — not complete
        AuditEvent(
            workflow_id="wf_to", workflow_name="test_timeout",
            event_type=AuditEventType.POLL_CHECKED,
            instance_id="inst_b2", fence_token=2,
            step_index=0, step_name="slow_job", step_handler="tests.slow_job",
            step_status="blocked", is_async=True,
            poll_count=1, poll_progress=0.3, poll_message="still running...",
            timestamp=_ts(30), sequence=6,
        ),
        AuditEvent(
            workflow_id="wf_to", workflow_name="test_timeout",
            event_type=AuditEventType.LOCK_RELEASED,
            instance_id="inst_b2", fence_token=2,
            lock_released=True,
            timestamp=_ts(30.1), sequence=7,
        ),
        # State transition: step failed (fenced write)
        AuditEvent(
            workflow_id="wf_to", workflow_name="test_timeout",
            event_type=AuditEventType.STEP_FAILED,
            instance_id="inst_a1", fence_token=3,
            step_index=0, step_name="slow_job", step_handler="tests.slow_job",
            step_status="failed", step_status_before="blocked",
            is_async=True,
            error="Poll timeout after 120.0s",
            error_traceback="Poll timeout after 120.0s",
            poll_elapsed_seconds=120.0,
            timestamp=_ts(120), sequence=8,
        ),
        # Diagnostic: poll timeout (emitted after successful fenced write)
        AuditEvent(
            workflow_id="wf_to", workflow_name="test_timeout",
            event_type=AuditEventType.POLL_TIMEOUT,
            instance_id="inst_a1", fence_token=3,
            step_index=0, step_name="slow_job", step_handler="tests.slow_job",
            step_status="failed", is_async=True,
            error="Poll timeout after 120.0s",
            poll_elapsed_seconds=120.0,
            timestamp=_ts(120.1), sequence=9,
        ),
        AuditEvent(
            workflow_id="wf_to", workflow_name="test_timeout",
            event_type=AuditEventType.WORKFLOW_FAILED,
            instance_id="inst_a1", fence_token=3,
            workflow_status="failed", workflow_status_before="running",
            lock_released=True,
            timestamp=_ts(120.2), sequence=10,
        ),
    ]


class TestAsyncFailureReport:
    def test_poll_timeout_shows_failed_status(self):
        """Poll timeout should produce a STEP_FAILED transition badge."""
        events = _make_async_poll_timeout_events()
        report = generate_audit_report(events)
        assert "&rarr; failed" in report

    def test_poll_timeout_shows_diagnostic_node(self):
        """Poll timeout should render a diagnostic 'Poll Timeout' node."""
        events = _make_async_poll_timeout_events()
        report = generate_audit_report(events)
        assert "Poll Timeout" in report

    def test_poll_timeout_doc_panel_shows_error(self):
        """Poll timeout should show error in the doc panel."""
        events = _make_async_poll_timeout_events()
        report = generate_audit_report(events)
        assert "Poll timeout after 120.0s" in report

    def test_max_polls_shows_failed_status(self):
        """Max polls exceeded should produce a STEP_FAILED transition badge."""
        events = [
            AuditEvent(
                workflow_id="wf_mp", workflow_name="test_maxpoll",
                event_type=AuditEventType.STEP_CLAIMED,
                step_index=0, step_name="poller",
                instance_id="inst_a1", fence_token=1, fence_token_before=0,
                workflow_status="running", workflow_status_before="pending",
                timestamp=_ts(0), sequence=1,
            ),
            AuditEvent(
                workflow_id="wf_mp", workflow_name="test_maxpoll",
                event_type=AuditEventType.STEP_BLOCKED,
                instance_id="inst_a1", fence_token=1,
                step_index=0, step_name="poller", step_handler="tests.poller",
                step_status="blocked", step_status_before="running",
                is_async=True,
                timestamp=_ts(0.3), sequence=2,
            ),
            # State transition (fenced write)
            AuditEvent(
                workflow_id="wf_mp", workflow_name="test_maxpoll",
                event_type=AuditEventType.STEP_FAILED,
                instance_id="inst_a1", fence_token=2,
                step_index=0, step_name="poller", step_handler="tests.poller",
                step_status="failed", step_status_before="blocked",
                is_async=True,
                error="Exceeded max poll count (3)",
                error_traceback="Exceeded max poll count (3)",
                poll_count=3,
                timestamp=_ts(60), sequence=3,
            ),
            # Diagnostic (emitted after successful fenced write)
            AuditEvent(
                workflow_id="wf_mp", workflow_name="test_maxpoll",
                event_type=AuditEventType.POLL_MAX_EXCEEDED,
                instance_id="inst_a1", fence_token=2,
                step_index=0, step_name="poller", step_handler="tests.poller",
                step_status="failed", is_async=True,
                error="Exceeded max poll count (3)",
                poll_count=3,
                timestamp=_ts(60.1), sequence=4,
            ),
            AuditEvent(
                workflow_id="wf_mp", workflow_name="test_maxpoll",
                event_type=AuditEventType.WORKFLOW_FAILED,
                instance_id="inst_a1", fence_token=2,
                workflow_status="failed", workflow_status_before="running",
                timestamp=_ts(60.2), sequence=5,
            ),
        ]
        report = generate_audit_report(events)
        assert "&rarr; failed" in report
        assert "Max Polls Exceeded" in report
