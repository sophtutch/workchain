"""Tests for workchain.steps — StepResult, Step, EventStep, PollingStep."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.conftest import AddConfig, AddStep, CountingPollStep, SuspendStep
from workchain import Context, PollingStep, StepOutcome, StepResult


class TestStepResult:
    def test_complete(self):
        r = StepResult.complete(output={"a": 1})
        assert r.outcome == StepOutcome.COMPLETED
        assert r.output == {"a": 1}
        assert r.error is None

    def test_complete_no_output(self):
        r = StepResult.complete()
        assert r.outcome == StepOutcome.COMPLETED
        assert r.output == {}

    def test_suspend(self):
        r = StepResult.suspend(correlation_id="abc-123")
        assert r.outcome == StepOutcome.SUSPEND
        assert r.correlation_id == "abc-123"

    def test_poll(self):
        t = datetime.now(UTC) + timedelta(seconds=30)
        r = StepResult.poll(next_poll_at=t)
        assert r.outcome == StepOutcome.POLL
        assert r.next_poll_at == t

    def test_fail(self):
        r = StepResult.fail(error="something broke")
        assert r.outcome == StepOutcome.FAILED
        assert r.error == "something broke"

    def test_repr(self):
        r = StepResult.complete()
        assert "completed" in repr(r)


class TestStep:
    def test_step_with_config(self):
        step = AddStep(config=AddConfig(a=3, b=4))
        ctx = Context()
        result = step.execute(ctx)
        assert result.outcome == StepOutcome.COMPLETED
        assert result.output == {"sum": 7}

    def test_step_without_config(self):
        from tests.conftest import NoOpStep

        step = NoOpStep()
        result = step.execute(Context())
        assert result.output == {"done": True}

    def test_step_repr(self):
        step = AddStep(config=AddConfig(a=1, b=2))
        r = repr(step)
        assert "AddStep" in r


class TestEventStep:
    def test_suspend_returns_correlation_id(self):
        step = SuspendStep()
        result = step.execute(Context())
        assert result.outcome == StepOutcome.SUSPEND
        assert result.correlation_id == "test-correlation-123"

    def test_on_resume_writes_to_context(self):
        step = SuspendStep()
        ctx = Context()
        step.on_resume({"approved": True}, ctx)
        assert ctx.get("resumed_with") == {"approved": True}


class TestPollingStep:
    def test_default_execute_schedules_poll(self):
        step = CountingPollStep()
        result = step.execute(Context())
        assert result.outcome == StepOutcome.POLL
        assert result.next_poll_at is not None
        assert result.next_poll_at > datetime.now(UTC) - timedelta(seconds=5)

    def test_check_completes_after_threshold(self):
        step = CountingPollStep(checks_until_done=2)
        ctx = Context()
        assert step.check(ctx) is False
        assert step.check(ctx) is True

    def test_on_complete_returns_output(self):
        step = CountingPollStep(checks_until_done=1)
        ctx = Context()
        step.check(ctx)
        output = step.on_complete(ctx)
        assert output == {"checks": 1}

    def test_default_on_complete_returns_empty(self):
        class BarePollingStep(PollingStep):
            def check(self, context):
                return True

        step = BarePollingStep()
        assert step.on_complete(Context()) == {}
