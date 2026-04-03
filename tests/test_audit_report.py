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
            event_type=AuditEventType.WORKFLOW_CLAIMED,
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
            event_type=AuditEventType.WORKFLOW_CLAIMED,
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
            event_type=AuditEventType.WORKFLOW_CLAIMED,
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
        # Poll 2: complete
        AuditEvent(
            workflow_id="wf3", workflow_name="test_async",
            event_type=AuditEventType.POLL_CHECKED,
            instance_id="inst_a1", fence_token=3,
            step_index=0, step_name="deploy", step_handler="tests.deploy",
            step_status="completed", is_async=True,
            poll_count=2, poll_progress=1.0, poll_message="done",
            timestamp=_ts(10), sequence=8,
        ),
        AuditEvent(
            workflow_id="wf3", workflow_name="test_async",
            event_type=AuditEventType.STEP_ADVANCED,
            instance_id="inst_a1", fence_token=3,
            step_index=0, step_name="deploy",
            timestamp=_ts(10.1), sequence=9,
        ),
        AuditEvent(
            workflow_id="wf3", workflow_name="test_async",
            event_type=AuditEventType.WORKFLOW_COMPLETED,
            instance_id="inst_a1", fence_token=3,
            workflow_status="completed", workflow_status_before="running",
            lock_released=True,
            timestamp=_ts(10.2), sequence=10,
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
            event_type=AuditEventType.WORKFLOW_CLAIMED,
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
        assert "test_cancel" in html


class TestGenerateAuditReport:
    def test_empty_events(self):
        html = generate_audit_report([])
        assert "No audit events found" in html

    def test_sync_workflow_report(self):
        events = _make_sync_workflow_events()
        report = generate_audit_report(events)

        # Basic structure
        assert "<!DOCTYPE html>" in report
        assert "test_sync" in report
        assert "Execution Report" in report

        # Discovery section
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
        assert "Workflow Complete" in report

    def test_failed_workflow_report(self):
        events = _make_failed_workflow_events()
        report = generate_audit_report(events)

        assert "test_fail" in report
        assert "FAILED" in report
        assert "intentional failure" in report
        assert "Workflow Failed" in report

    def test_async_workflow_report(self):
        events = _make_async_workflow_events()
        report = generate_audit_report(events)

        assert "test_async" in report
        assert "deploy" in report

        # Async-specific
        assert "BLOCKED" in report
        assert "poll" in report.lower()
        assert "50%" in report or "0.5" in report

        # Multiple instances
        assert "inst_a1" in report
        assert "inst_b2" in report

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
                event_type=AuditEventType.WORKFLOW_CLAIMED,
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
            AuditEvent(
                workflow_id="wf5", workflow_name="multi",
                event_type=AuditEventType.WORKFLOW_CLAIMED,
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
