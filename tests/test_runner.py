"""Tests for workchain.runner — WorkflowRunner execution engine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import (
    AddConfig,
    AddStep,
    ContextReaderStep,
    CountingPollStep,
    ExplodingStep,
    FailingStep,
    NoOpStep,
    SuspendStep,
    TimeoutPollStep,
)
from workchain import (
    Context,
    DependencyFailurePolicy,
    StepStatus,
    Workflow,
    WorkflowRunner,
    WorkflowStatus,
)
from workchain.exceptions import WorkflowRunNotFoundError


def _make_runner(store, registry, workflow, **kwargs):
    return WorkflowRunner(
        store=store,
        registry=registry,
        workflow=workflow,
        instance_id="test-runner",
        lease_ttl_seconds=30,
        poll_interval_seconds=0.1,
        **kwargs,
    )


class TestSingleStepWorkflow:
    async def test_completes_single_step(self, in_memory_store, step_registry):
        wf = Workflow(name="single").add("s1", NoOpStep())
        run = wf.create_run()
        await in_memory_store.save(run)

        runner = _make_runner(in_memory_store, step_registry, wf)
        await runner.tick()

        assert run.status == WorkflowStatus.COMPLETED
        assert run.get_step("s1").status == StepStatus.COMPLETED

    async def test_step_output_stored(self, in_memory_store, step_registry):
        wf = Workflow(name="add").add("calc", AddStep(config=AddConfig(a=3, b=7)))
        run = wf.create_run()
        await in_memory_store.save(run)

        runner = _make_runner(in_memory_store, step_registry, wf)
        await runner.tick()

        assert run.get_step("calc").output == {"sum": 10}


class TestMultiStepDAG:
    async def test_linear_chain(self, in_memory_store, step_registry):
        wf = (
            Workflow(name="chain")
            .add("a", NoOpStep())
            .add("b", NoOpStep(), depends_on=["a"])
            .add("c", NoOpStep(), depends_on=["b"])
        )
        run = wf.create_run()
        await in_memory_store.save(run)

        runner = _make_runner(in_memory_store, step_registry, wf)
        await runner.tick()

        assert run.status == WorkflowStatus.COMPLETED
        for step in run.steps:
            assert step.status == StepStatus.COMPLETED

    async def test_diamond_dag(self, in_memory_store, step_registry):
        wf = (
            Workflow(name="diamond")
            .add("root", NoOpStep())
            .add("left", NoOpStep(), depends_on=["root"])
            .add("right", NoOpStep(), depends_on=["root"])
            .add("join", NoOpStep(), depends_on=["left", "right"])
        )
        run = wf.create_run()
        await in_memory_store.save(run)

        runner = _make_runner(in_memory_store, step_registry, wf)
        await runner.tick()

        assert run.status == WorkflowStatus.COMPLETED

    async def test_context_flows_between_steps(self, in_memory_store, step_registry):
        wf = (
            Workflow(name="flow")
            .add("upstream", AddStep(config=AddConfig(a=5, b=3)))
            .add("downstream", ContextReaderStep(), depends_on=["upstream"])
        )
        run = wf.create_run()
        await in_memory_store.save(run)

        runner = _make_runner(in_memory_store, step_registry, wf)
        await runner.tick()

        assert run.get_step("downstream").output == {"read_value": 8}


class TestFailurePropagation:
    async def test_failure_propagates_to_dependent(self, in_memory_store, step_registry):
        wf = Workflow(name="fail").add("bad", FailingStep()).add("next", NoOpStep(), depends_on=["bad"])
        run = wf.create_run()
        await in_memory_store.save(run)

        runner = _make_runner(in_memory_store, step_registry, wf)
        await runner.tick()

        assert run.status == WorkflowStatus.FAILED
        assert run.get_step("bad").status == StepStatus.FAILED
        assert run.get_step("next").status == StepStatus.FAILED
        assert "Dependency failed" in run.get_step("next").error

    async def test_skip_on_dependency_failure(self, in_memory_store, step_registry):
        wf = (
            Workflow(name="skip")
            .add("bad", FailingStep())
            .add("skippable", NoOpStep(), depends_on=["bad"], on_dependency_failure=DependencyFailurePolicy.SKIP)
        )
        run = wf.create_run()
        await in_memory_store.save(run)

        runner = _make_runner(in_memory_store, step_registry, wf)
        await runner.tick()

        assert run.get_step("skippable").status == StepStatus.SKIPPED

    async def test_cascading_failure(self, in_memory_store, step_registry):
        wf = (
            Workflow(name="cascade")
            .add("bad", FailingStep())
            .add("mid", NoOpStep(), depends_on=["bad"])
            .add("end", NoOpStep(), depends_on=["mid"])
        )
        run = wf.create_run()
        await in_memory_store.save(run)

        runner = _make_runner(in_memory_store, step_registry, wf)
        await runner.tick()

        assert run.get_step("mid").status == StepStatus.FAILED
        assert run.get_step("end").status == StepStatus.FAILED

    async def test_exception_in_step_creates_failure(self, in_memory_store, step_registry):
        wf = Workflow(name="explode").add("boom", ExplodingStep())
        run = wf.create_run()
        await in_memory_store.save(run)

        runner = _make_runner(in_memory_store, step_registry, wf)
        await runner.tick()

        assert run.get_step("boom").status == StepStatus.FAILED
        assert "boom" in run.get_step("boom").error
        assert run.status == WorkflowStatus.FAILED

    async def test_partial_failure_with_independent_branch(self, in_memory_store, step_registry):
        wf = (
            Workflow(name="partial")
            .add("bad", FailingStep())
            .add("good", NoOpStep())
            .add("after_bad", NoOpStep(), depends_on=["bad"])
        )
        run = wf.create_run()
        await in_memory_store.save(run)

        runner = _make_runner(in_memory_store, step_registry, wf)
        await runner.tick()

        assert run.get_step("good").status == StepStatus.COMPLETED
        assert run.get_step("after_bad").status == StepStatus.FAILED
        assert run.status == WorkflowStatus.FAILED


class TestEventStep:
    async def test_suspend_and_resume(self, in_memory_store, step_registry):
        wf = Workflow(name="event").add("wait", SuspendStep()).add("after", NoOpStep(), depends_on=["wait"])
        run = wf.create_run()
        await in_memory_store.save(run)

        runner = _make_runner(in_memory_store, step_registry, wf)
        await runner.tick()

        # After first tick, step is suspended but _assess_workflow_status sees
        # PENDING "after" step and sets workflow to RUNNING
        assert run.get_step("wait").status == StepStatus.SUSPENDED
        assert run.get_step("wait").resume_correlation_id == "test-correlation-123"
        assert run.get_step("after").status == StepStatus.PENDING

        # Resume — this internally creates a new run object, so reload from store
        await runner.resume(correlation_id="test-correlation-123", payload={"approved": True})

        reloaded = await in_memory_store.load(str(run.id))
        assert reloaded.get_step("wait").status == StepStatus.COMPLETED
        assert reloaded.get_step("after").status == StepStatus.COMPLETED
        assert reloaded.status == WorkflowStatus.COMPLETED

    async def test_resume_unknown_correlation_id_raises(self, in_memory_store, step_registry):
        runner = _make_runner(in_memory_store, step_registry, None)
        with pytest.raises(WorkflowRunNotFoundError):
            await runner.resume(correlation_id="no-such-id", payload={})


class TestPollingStep:
    async def test_polling_step_schedules_then_completes(self, in_memory_store, step_registry):
        poll_step = CountingPollStep(checks_until_done=1)
        wf = Workflow(name="poll").add("poll", poll_step)
        run = wf.create_run()
        await in_memory_store.save(run)

        runner = _make_runner(in_memory_store, step_registry, wf)
        await runner.tick()

        # After first tick, step should be awaiting poll
        assert run.get_step("poll").status == StepStatus.AWAITING_POLL
        assert run.status == WorkflowStatus.SUSPENDED

        # Set next_poll_at to the past so it's due
        run.get_step("poll").next_poll_at = datetime.now(UTC) - timedelta(seconds=1)
        # Reset lease so we can claim again
        run.lease_owner = None
        run.lease_expires_at = None
        run.status = WorkflowStatus.SUSPENDED

        # Call _check_due_polls directly since find_claimable checks LEASABLE_STATUSES
        context = Context.from_dict(run.context)
        await runner._check_due_polls(run, context)

        assert run.get_step("poll").status == StepStatus.COMPLETED
        assert run.get_step("poll").output == {"checks": 1}

    async def test_polling_step_timeout(self, in_memory_store, step_registry):
        poll_step = TimeoutPollStep()
        wf = Workflow(name="timeout").add("poll", poll_step)
        run = wf.create_run()
        await in_memory_store.save(run)

        runner = _make_runner(in_memory_store, step_registry, wf)
        await runner.tick()

        # Set poll_started_at to the past and next_poll_at to now
        step_run = run.get_step("poll")
        step_run.poll_started_at = datetime.now(UTC) - timedelta(seconds=10)
        step_run.next_poll_at = datetime.now(UTC) - timedelta(seconds=1)

        context = Context.from_dict(run.context)
        await runner._execute_poll_check(run, step_run, context)

        assert step_run.status == StepStatus.FAILED
        assert "timed out" in step_run.error


class TestTickBehavior:
    async def test_tick_returns_false_when_nothing_claimable(self, in_memory_store, step_registry):
        runner = _make_runner(in_memory_store, step_registry, None)
        assert await runner.tick() is False

    async def test_tick_returns_true_when_run_processed(self, in_memory_store, step_registry):
        wf = Workflow(name="test").add("s1", NoOpStep())
        run = wf.create_run()
        await in_memory_store.save(run)

        runner = _make_runner(in_memory_store, step_registry, wf)
        assert await runner.tick() is True

    async def test_lease_released_after_tick(self, in_memory_store, step_registry):
        wf = Workflow(name="test").add("s1", NoOpStep())
        run = wf.create_run()
        await in_memory_store.save(run)

        runner = _make_runner(in_memory_store, step_registry, wf)
        await runner.tick()

        assert run.lease_owner is None

    async def test_unknown_step_type_handled(self, in_memory_store):
        """StepNotFoundError raised from _get_step_instance is caught by tick()."""
        wf = Workflow(name="test").add("s1", NoOpStep())
        run = wf.create_run()
        await in_memory_store.save(run)

        runner = _make_runner(in_memory_store, {}, None)  # empty registry, no workflow
        result = await runner.tick()  # should not raise, error is caught at tick level

        assert result is True  # run was claimed even though processing failed


class TestStopBehavior:
    async def test_stop_sets_flag(self, in_memory_store, step_registry):
        runner = _make_runner(in_memory_store, step_registry, None)
        await runner.stop()
        assert runner._running is False


class TestStepTimestamps:
    async def test_started_at_set(self, in_memory_store, step_registry):
        wf = Workflow(name="test").add("s1", NoOpStep())
        run = wf.create_run()
        await in_memory_store.save(run)

        runner = _make_runner(in_memory_store, step_registry, wf)
        await runner.tick()

        assert run.get_step("s1").started_at is not None

    async def test_completed_at_set(self, in_memory_store, step_registry):
        wf = Workflow(name="test").add("s1", NoOpStep())
        run = wf.create_run()
        await in_memory_store.save(run)

        runner = _make_runner(in_memory_store, step_registry, wf)
        await runner.tick()

        assert run.get_step("s1").completed_at is not None
