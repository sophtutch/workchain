"""Tests for workchain.engine — WorkflowEngine, helpers, execution paths."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tests.conftest import (
    GreetConfig,
    GreetResult,
    SubmitResult,
    _check_complete_impl,
    async_submit_handler,
    check_complete_always_done,
    fail_handler,
    flaky_handler,
    greet_handler,
    noop_handler,
    verify_done,
    verify_not_done,
)
from workchain.decorators import _STEP_REGISTRY, _normalize_check_result
from workchain.engine import WorkflowEngine, _ActiveStep, _build_results, _wrap_handler_return
from workchain.exceptions import HandlerError
from workchain.models import (
    CheckResult,
    PollPolicy,
    RetryPolicy,
    Step,
    StepResult,
    StepStatus,
    Workflow,
    WorkflowStatus,
)

# ---------------------------------------------------------------------------
# _build_results
# ---------------------------------------------------------------------------


class TestBuildResults:
    def test_no_dependencies(self):
        wf = Workflow(
            name="test",
            steps=[Step(name="s1", handler="mod.f", depends_on=[])],
        )
        assert _build_results(wf, "s1") == {}

    def test_collects_dependency_results(self):
        wf = Workflow(
            name="test",
            steps=[
                Step(name="s1", handler="mod.f", depends_on=[], result=GreetResult(greeting="hi")),
                Step(name="s2", handler="mod.f", depends_on=["s1"]),
            ],
        )
        results = _build_results(wf, "s2")
        assert "s1" in results
        assert isinstance(results["s1"], GreetResult)

    def test_only_includes_declared_dependencies(self):
        """Only results from depends_on are included, not all prior steps."""
        wf = Workflow(
            name="test",
            steps=[
                Step(name="s1", handler="mod.f", depends_on=[], result=StepResult()),
                Step(name="s2", handler="mod.f", depends_on=[], result=StepResult()),
                Step(name="s3", handler="mod.f", depends_on=["s2"]),
            ],
        )
        results = _build_results(wf, "s3")
        assert "s1" not in results  # not a dependency of s3
        assert "s2" in results

    def test_skips_none_results(self):
        wf = Workflow(
            name="test",
            steps=[
                Step(name="s1", handler="mod.f", depends_on=[]),
                Step(name="s2", handler="mod.f", depends_on=[], result=StepResult()),
                Step(name="s3", handler="mod.f", depends_on=["s1", "s2"]),
            ],
        )
        results = _build_results(wf, "s3")
        assert "s1" not in results  # no result
        assert "s2" in results

    def test_unknown_step_name(self):
        wf = Workflow(name="test", steps=[Step(name="s1", handler="mod.f", depends_on=[])])
        assert _build_results(wf, "nonexistent") == {}


# ---------------------------------------------------------------------------
# _wrap_handler_return
# ---------------------------------------------------------------------------


class TestWrapHandlerReturn:
    def test_step_result_subclass(self):
        result_data = GreetResult(greeting="hi")
        result, result_type = _wrap_handler_return(result_data)
        assert isinstance(result, GreetResult)
        assert result_type is not None
        assert "GreetResult" in result_type

    def test_base_step_result(self):
        result_data = StepResult()
        result, result_type = _wrap_handler_return(result_data)
        assert result_type is None

    def test_non_step_result_raises(self):
        with pytest.raises(HandlerError, match="StepResult subclass"):
            _wrap_handler_return({"key": "value"})

    def test_sets_completed_at_if_missing(self):
        result_data = StepResult()
        assert result_data.completed_at is None
        result, _ = _wrap_handler_return(result_data)
        assert result.completed_at is not None

    def test_preserves_existing_completed_at(self):
        ts = datetime(2025, 1, 1, tzinfo=UTC)
        result_data = StepResult(completed_at=ts)
        result, _ = _wrap_handler_return(result_data)
        assert result.completed_at == ts


# ---------------------------------------------------------------------------
# _normalize_check_result
# ---------------------------------------------------------------------------


class TestNormalizeCheckResult:
    def test_check_result_passthrough(self):
        cr = CheckResult(complete=True, progress=1.0)
        assert _normalize_check_result(cr) is cr

    def test_dict_to_check_result(self):
        result = _normalize_check_result({"complete": True, "progress": 0.8, "message": "done"})
        assert isinstance(result, CheckResult)
        assert result.complete is True
        assert result.progress == 0.8

    def test_bool_true_to_check_result(self):
        result = _normalize_check_result(True)
        assert isinstance(result, CheckResult)
        assert result.complete is True

    def test_bool_false_to_check_result(self):
        result = _normalize_check_result(False)
        assert isinstance(result, CheckResult)
        assert result.complete is False

    def test_invalid_type_raises_type_error(self):
        with pytest.raises(TypeError, match="got str"):
            _normalize_check_result("done")

    def test_invalid_int_raises_type_error(self):
        with pytest.raises(TypeError, match="got int"):
            _normalize_check_result(42)

    def test_invalid_none_raises_type_error(self):
        with pytest.raises(TypeError, match="got NoneType"):
            _normalize_check_result(None)


# ---------------------------------------------------------------------------
# Engine lifecycle
# ---------------------------------------------------------------------------


class TestEngineLifecycle:
    async def test_start_creates_tasks(self, engine):
        await engine.start()
        assert len(engine._tasks) == 3
        await engine.stop()

    async def test_stop_sets_shutdown(self, engine):
        await engine.start()
        await engine.stop()
        assert engine._shutdown_event.is_set()

    async def test_stop_clears_active(self, engine, store, sample_workflow):
        await store.insert(sample_workflow)
        await engine.start()
        # Give claim loop time to pick up the workflow
        await asyncio.sleep(0.2)
        await engine.stop()
        assert len(engine._active) == 0

    async def test_context_manager(self, store):
        engine = WorkflowEngine(
            store, instance_id="ctx-test",
            claim_interval=0.05, heartbeat_interval=0.05, sweep_interval=10,
        )
        async with engine as e:
            assert e is engine
            assert len(e._tasks) == 3
        # After exiting, shutdown should have fired
        assert engine._shutdown_event.is_set()

    async def test_context_manager_stops_on_exception(self, store):
        engine = WorkflowEngine(
            store, instance_id="ctx-err-test",
            claim_interval=0.05, heartbeat_interval=0.05, sweep_interval=10,
        )
        with pytest.raises(RuntimeError, match="boom"):
            async with engine:
                raise RuntimeError("boom")
        # Engine should still be stopped even after exception
        assert engine._shutdown_event.is_set()


# ---------------------------------------------------------------------------
# Sync workflow execution
# ---------------------------------------------------------------------------


class TestSyncExecution:
    async def test_single_sync_step(self, store, engine):
        wf = Workflow(
            name="single_sync",
            steps=[Step(name="noop", handler=noop_handler._step_meta["handler"])],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        assert loaded.steps[0].status == StepStatus.COMPLETED

    async def test_multiple_sync_steps(self, store, engine):
        wf = Workflow(
            name="multi_sync",
            steps=[
                Step(name="greet", handler=greet_handler._step_meta["handler"], config=GreetConfig(name="Test")),
                Step(name="noop", handler=noop_handler._step_meta["handler"]),
            ],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        assert all(s.status == StepStatus.COMPLETED for s in loaded.steps)

    async def test_step_result_persisted(self, store, engine):
        wf = Workflow(
            name="result_test",
            steps=[
                Step(name="greet", handler=greet_handler._step_meta["handler"], config=GreetConfig(name="World")),
            ],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.steps[0].result is not None
        assert isinstance(loaded.steps[0].result, GreetResult)
        assert loaded.steps[0].result.greeting == "Hello, World!"

    async def test_workflow_completes_after_all_steps(self, store, engine):
        wf = Workflow(
            name="advance_test",
            steps=[
                Step(name="s1", handler=noop_handler._step_meta["handler"]),
                Step(name="s2", handler=noop_handler._step_meta["handler"]),
            ],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        assert all(s.status == StepStatus.COMPLETED for s in loaded.steps)

    async def test_sync_handler_works(self, store):
        """A plain sync (non-async) handler registered via _STEP_REGISTRY completes without TypeError."""

        def sync_handler(_config, _results):
            return StepResult()

        sync_handler._step_meta = {"needs_context": False}
        _STEP_REGISTRY["tests.test_engine.sync_handler"] = sync_handler

        engine = WorkflowEngine(
            store, instance_id="sync-test",
            claim_interval=0.05, heartbeat_interval=0.05, sweep_interval=10,
        )
        wf = Workflow(
            name="sync_handler_test",
            steps=[Step(name="sync_step", handler="tests.test_engine.sync_handler")],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        assert loaded.steps[0].status == StepStatus.COMPLETED


# ---------------------------------------------------------------------------
# Retry execution
# ---------------------------------------------------------------------------


class TestRetryExecution:
    async def test_flaky_step_succeeds_on_retry(self, store, engine):
        wf = Workflow(
            name="flaky_test",
            steps=[
                Step(
                    name="flaky",
                    handler=flaky_handler._step_meta["handler"],
                    retry_policy=RetryPolicy(max_attempts=3, wait_seconds=0.01, wait_multiplier=0.01),
                ),
            ],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        assert loaded.steps[0].attempt >= 2

    async def test_always_failing_step_marks_failed(self, store, engine):
        wf = Workflow(
            name="fail_test",
            steps=[
                Step(
                    name="fail",
                    handler=fail_handler._step_meta["handler"],
                    retry_policy=RetryPolicy(max_attempts=2, wait_seconds=0.01, wait_multiplier=0.01),
                ),
            ],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.FAILED
        assert loaded.steps[0].status == StepStatus.FAILED
        assert loaded.steps[0].result is not None
        assert loaded.steps[0].result.error is not None


# ---------------------------------------------------------------------------
# Async workflow execution (poll cycle)
# ---------------------------------------------------------------------------


class TestAsyncExecution:
    async def test_async_step_submits_and_blocks(self, store, engine):
        wf = Workflow(
            name="async_submit_test",
            steps=[
                Step(
                    name="async_step",
                    handler=async_submit_handler._step_meta["handler"],
                    is_async=True,
                    completeness_check=_check_complete_impl._step_meta["handler"],
                    poll_policy=PollPolicy(interval=0.05, timeout=5.0, max_polls=10),
                ),
            ],
        )
        await store.insert(wf)

        await engine.start()
        # Give time for submission + at least one poll
        await asyncio.sleep(0.3)

        loaded = await store.get(wf.id)
        # Should be either BLOCKED (waiting for poll) or COMPLETED (if polls finished)
        assert loaded.steps[0].status in (StepStatus.BLOCKED, StepStatus.COMPLETED)
        assert loaded.steps[0].result is not None

        await engine.stop()

    async def test_async_step_completes_after_polls(self, store, engine):
        wf = Workflow(
            name="async_complete_test",
            steps=[
                Step(
                    name="async_step",
                    handler=async_submit_handler._step_meta["handler"],
                    is_async=True,
                    completeness_check=_check_complete_impl._step_meta["handler"],
                    poll_policy=PollPolicy(interval=0.05, timeout=5.0, max_polls=10),
                ),
            ],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(1.0)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        assert loaded.steps[0].status == StepStatus.COMPLETED

    async def test_async_step_no_completeness_check(self, store, engine):
        """Async step without completeness_check completes immediately."""

        async def async_no_check(_config, _results):
            return SubmitResult(job_id="j1")

        async_no_check._step_meta = {"needs_context": False}
        _STEP_REGISTRY["tests.async_no_check"] = async_no_check

        wf = Workflow(
            name="async_no_check_test",
            steps=[
                Step(
                    name="async_no_check",
                    handler="tests.async_no_check",
                    is_async=True,
                    # No completeness_check
                ),
            ],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()

        loaded = await store.get(wf.id)
        # Without completeness_check, treated as sync
        assert loaded.status == WorkflowStatus.COMPLETED


# ---------------------------------------------------------------------------
# Poll timeout and max_polls
# ---------------------------------------------------------------------------


class TestPollLimits:
    async def test_poll_timeout(self, store, engine):
        async def never_complete(_config, _results, _result):
            return CheckResult(complete=False, progress=0.1)

        never_complete._step_meta = {"needs_context": False}
        _STEP_REGISTRY["tests.never_complete"] = never_complete

        wf = Workflow(
            name="timeout_test",
            steps=[
                Step(
                    name="timeout_step",
                    handler=async_submit_handler._step_meta["handler"],
                    is_async=True,
                    completeness_check="tests.never_complete",
                    poll_policy=PollPolicy(interval=0.05, timeout=0.2, max_polls=0),
                ),
            ],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(1.0)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.FAILED
        assert "timeout" in (loaded.steps[0].result.error or "").lower()

    async def test_max_polls_exceeded(self, store, engine):
        async def never_complete(_config, _results, _result):
            return CheckResult(complete=False)

        never_complete._step_meta = {"needs_context": False}
        _STEP_REGISTRY["tests.never_complete_max"] = never_complete

        wf = Workflow(
            name="max_polls_test",
            steps=[
                Step(
                    name="max_poll_step",
                    handler=async_submit_handler._step_meta["handler"],
                    is_async=True,
                    completeness_check="tests.never_complete_max",
                    poll_policy=PollPolicy(interval=0.05, timeout=0, max_polls=2),
                ),
            ],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(1.0)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.FAILED
        assert "max poll" in (loaded.steps[0].result.error or "").lower()


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


class TestRecovery:
    async def _claim_and_run_step(self, store, engine, wf_id, step_name):
        """Helper: claim a step and run it via _run_step."""
        result = await store.try_claim_step(wf_id, step_name, "test-engine-001")
        assert result is not None, f"Failed to claim step {step_name}"
        _wf, step_fence = result
        await engine._run_step(wf_id, step_name, step_fence)

    async def test_recover_idempotent_reruns(self, store, engine):
        """An idempotent step found in SUBMITTED state gets re-run."""
        wf = Workflow(
            name="recovery_idem",
            status=WorkflowStatus.RUNNING,
            steps=[
                Step(
                    name="noop",
                    handler=noop_handler._step_meta["handler"],
                    status=StepStatus.SUBMITTED,
                    idempotent=True,
                    depends_on=[],
                ),
            ],
        )
        await store.insert(wf)

        await self._claim_and_run_step(store, engine, wf.id, "noop")

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        assert loaded.steps[0].status == StepStatus.COMPLETED

    async def test_recover_non_idempotent_needs_review(self, store, engine):
        """A non-idempotent step without verify hook marks NEEDS_REVIEW."""
        wf = Workflow(
            name="recovery_non_idem",
            status=WorkflowStatus.RUNNING,
            steps=[
                Step(
                    name="danger",
                    handler=noop_handler._step_meta["handler"],
                    status=StepStatus.SUBMITTED,
                    idempotent=False,
                    depends_on=[],
                ),
            ],
        )
        await store.insert(wf)

        await self._claim_and_run_step(store, engine, wf.id, "danger")

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.NEEDS_REVIEW

    async def test_recover_with_verify_completion(self, store, engine):
        """Step with verify_completion that returns True marks COMPLETED."""
        wf = Workflow(
            name="recovery_verify",
            status=WorkflowStatus.RUNNING,
            steps=[
                Step(
                    name="verified",
                    handler=noop_handler._step_meta["handler"],
                    status=StepStatus.SUBMITTED,
                    verify_completion=verify_done._step_meta["handler"],
                    idempotent=False,
                    depends_on=[],
                ),
            ],
        )
        await store.insert(wf)

        await self._claim_and_run_step(store, engine, wf.id, "verified")

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        assert loaded.steps[0].status == StepStatus.COMPLETED

    async def test_recover_verify_completion_not_done(self, store, engine):
        """Step with verify_completion that returns False does NOT mark COMPLETED."""
        wf = Workflow(
            name="recovery_verify_not_done",
            status=WorkflowStatus.RUNNING,
            steps=[
                Step(
                    name="not_verified",
                    handler=noop_handler._step_meta["handler"],
                    status=StepStatus.SUBMITTED,
                    verify_completion=verify_not_done._step_meta["handler"],
                    idempotent=False,
                    depends_on=[],
                ),
            ],
        )
        await store.insert(wf)

        await self._claim_and_run_step(store, engine, wf.id, "not_verified")

        loaded = await store.get(wf.id)
        # verify_not_done returns False → step should NOT be completed
        assert loaded.steps[0].status != StepStatus.COMPLETED

    async def test_recover_async_with_completeness_check(self, store, engine):
        """Async step in SUBMITTED with result: completeness_check transitions to BLOCKED."""
        wf = Workflow(
            name="recovery_async",
            status=WorkflowStatus.RUNNING,
            steps=[
                Step(
                    name="async_step",
                    handler=async_submit_handler._step_meta["handler"],
                    status=StepStatus.SUBMITTED,
                    is_async=True,
                    completeness_check=check_complete_always_done._step_meta["handler"],
                    result=SubmitResult(job_id="existing_job"),
                    idempotent=False,
                    depends_on=[],
                ),
            ],
        )
        await store.insert(wf)

        await self._claim_and_run_step(store, engine, wf.id, "async_step")

        loaded = await store.get(wf.id)
        # check_always_done returns True, so step should be COMPLETED
        assert loaded.steps[0].status == StepStatus.COMPLETED

    async def test_recover_async_with_poll_hint(self, store, engine):
        """Async recovery handles CheckResult(complete=True) correctly."""

        async def check_returns_poll_hint(_config, _results, _result):
            return CheckResult(complete=True, progress=1.0)

        check_returns_poll_hint._step_meta = {"needs_context": False}
        _STEP_REGISTRY["tests.check_poll_hint_done"] = check_returns_poll_hint

        wf = Workflow(
            name="recovery_poll_hint",
            status=WorkflowStatus.RUNNING,
            steps=[
                Step(
                    name="async_step",
                    handler=async_submit_handler._step_meta["handler"],
                    status=StepStatus.SUBMITTED,
                    is_async=True,
                    completeness_check="tests.check_poll_hint_done",
                    result=SubmitResult(job_id="existing_job"),
                    idempotent=False,
                    depends_on=[],
                ),
            ],
        )
        await store.insert(wf)

        await self._claim_and_run_step(store, engine, wf.id, "async_step")

        loaded = await store.get(wf.id)
        # CheckResult(complete=True) should be recognized as complete
        assert loaded.steps[0].status == StepStatus.COMPLETED

    async def test_recover_crashed_step_with_healthy_sibling(self, store, engine):
        """One step crashed (SUBMITTED), sibling step is COMPLETED — recovery fixes only the crashed one."""
        wf = Workflow(
            name="recovery_sibling",
            status=WorkflowStatus.RUNNING,
            steps=[
                Step(
                    name="done",
                    handler=noop_handler._step_meta["handler"],
                    status=StepStatus.COMPLETED,
                    depends_on=[],
                ),
                Step(
                    name="crashed",
                    handler=noop_handler._step_meta["handler"],
                    status=StepStatus.SUBMITTED,
                    idempotent=True,
                    depends_on=[],
                ),
            ],
        )
        await store.insert(wf)

        await self._claim_and_run_step(store, engine, wf.id, "crashed")

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        assert loaded.step_by_name("done").status == StepStatus.COMPLETED
        assert loaded.step_by_name("crashed").status == StepStatus.COMPLETED

    async def test_recover_multiple_crashed_steps(self, store, engine):
        """Two crashed steps in the same workflow are each recovered independently."""
        wf = Workflow(
            name="recovery_multi",
            status=WorkflowStatus.RUNNING,
            steps=[
                Step(
                    name="a",
                    handler=noop_handler._step_meta["handler"],
                    status=StepStatus.SUBMITTED,
                    idempotent=True,
                    depends_on=[],
                ),
                Step(
                    name="b",
                    handler=noop_handler._step_meta["handler"],
                    status=StepStatus.RUNNING,
                    idempotent=True,
                    depends_on=[],
                ),
            ],
        )
        await store.insert(wf)

        # Recover each independently
        await self._claim_and_run_step(store, engine, wf.id, "a")
        await self._claim_and_run_step(store, engine, wf.id, "b")

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        assert loaded.step_by_name("a").status == StepStatus.COMPLETED
        assert loaded.step_by_name("b").status == StepStatus.COMPLETED

    async def test_needs_review_propagates_to_workflow(self, store, engine):
        """NEEDS_REVIEW on a non-idempotent crashed step propagates to workflow."""
        wf = Workflow(
            name="recovery_needs_review",
            status=WorkflowStatus.RUNNING,
            steps=[
                Step(
                    name="healthy",
                    handler=noop_handler._step_meta["handler"],
                    status=StepStatus.COMPLETED,
                    depends_on=[],
                ),
                Step(
                    name="broken",
                    handler=noop_handler._step_meta["handler"],
                    status=StepStatus.SUBMITTED,
                    idempotent=False,
                    depends_on=[],
                ),
            ],
        )
        await store.insert(wf)

        await self._claim_and_run_step(store, engine, wf.id, "broken")

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.NEEDS_REVIEW


# ---------------------------------------------------------------------------
# Sweep loop
# ---------------------------------------------------------------------------


class TestSweepLoop:
    async def test_sweep_detects_stuck_step(self, store):
        """Sweep detects a step stuck in SUBMITTED and force-releases its lock."""
        wf = Workflow(
            name="stuck_step",
            status=WorkflowStatus.RUNNING,
            updated_at=datetime.now(UTC) - timedelta(seconds=600),
            steps=[
                Step(
                    name="s1", handler=noop_handler._step_meta["handler"],
                    status=StepStatus.SUBMITTED,
                    locked_by="dead_instance",
                    lock_expires_at=datetime.now(UTC) + timedelta(seconds=30),
                    fence_token=1,
                    depends_on=[],
                ),
            ],
        )
        await store.insert(wf)

        anomalies = await store.find_anomalies(step_stuck_seconds=300)
        assert any(
            a["workflow_id"] == wf.id and a["step_name"] == "s1"
            for a in anomalies
        )

        released = await store.force_release_step_lock(wf.id, "s1")
        assert released is True

        loaded = await store.get(wf.id)
        assert loaded.steps[0].locked_by is None
        # fence_token incremented by force release
        assert loaded.steps[0].fence_token == 2

    async def test_sweep_detects_stale_step_lock(self, store):
        """Sweep detects a step with an expired lock and stale workflow."""
        wf = Workflow(
            name="stale_lock",
            status=WorkflowStatus.RUNNING,
            updated_at=datetime.now(UTC) - timedelta(seconds=600),
            steps=[
                Step(
                    name="s1", handler=noop_handler._step_meta["handler"],
                    status=StepStatus.RUNNING,
                    locked_by="dead_instance",
                    lock_expires_at=datetime.now(UTC) - timedelta(seconds=10),
                    fence_token=1,
                    depends_on=[],
                ),
            ],
        )
        await store.insert(wf)

        anomalies = await store.find_anomalies(step_stuck_seconds=300)
        step_anomalies = [
            a for a in anomalies
            if a["workflow_id"] == wf.id and a["step_name"] == "s1"
        ]
        assert len(step_anomalies) >= 1

    async def test_sweep_detects_orphaned_workflow(self, store):
        """Sweep detects a workflow where all steps are done but status is still RUNNING."""
        wf = Workflow(
            name="orphaned",
            status=WorkflowStatus.RUNNING,
            updated_at=datetime.now(UTC) - timedelta(seconds=600),
            steps=[
                Step(
                    name="s1", handler=noop_handler._step_meta["handler"],
                    status=StepStatus.COMPLETED, depends_on=[],
                ),
                Step(
                    name="s2", handler=noop_handler._step_meta["handler"],
                    status=StepStatus.COMPLETED, depends_on=["s1"],
                ),
            ],
        )
        await store.insert(wf)

        anomalies = await store.find_anomalies(step_stuck_seconds=300)
        orphan = [a for a in anomalies if a["anomaly"] == "orphaned_workflow"]
        assert any(a["workflow_id"] == wf.id for a in orphan)

    async def test_sweep_resolves_orphaned_completed(self, store, engine):
        """Sweep loop resolves orphaned workflow by calling try_complete_workflow."""
        wf = Workflow(
            name="orphaned_complete",
            status=WorkflowStatus.RUNNING,
            updated_at=datetime.now(UTC) - timedelta(seconds=600),
            steps=[
                Step(
                    name="s1", handler=noop_handler._step_meta["handler"],
                    status=StepStatus.COMPLETED, depends_on=[],
                ),
            ],
        )
        await store.insert(wf)

        # Run engine with short sweep interval to trigger the sweep
        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED

    async def test_sweep_resolves_orphaned_failed(self, store, engine):
        """Sweep loop resolves orphaned workflow where all steps are terminal (one failed)."""
        wf = Workflow(
            name="orphaned_fail",
            status=WorkflowStatus.RUNNING,
            updated_at=datetime.now(UTC) - timedelta(seconds=600),
            steps=[
                Step(
                    name="s1", handler=noop_handler._step_meta["handler"],
                    status=StepStatus.FAILED, depends_on=[],
                ),
                Step(
                    name="s2", handler=noop_handler._step_meta["handler"],
                    status=StepStatus.COMPLETED, depends_on=[],
                ),
            ],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.FAILED

    async def test_sweep_skips_active_steps(self, store):
        """Sweep does not force-release steps actively processed by this instance."""
        wf = Workflow(
            name="active_step",
            status=WorkflowStatus.RUNNING,
            updated_at=datetime.now(UTC) - timedelta(seconds=600),
            steps=[
                Step(
                    name="s1", handler=noop_handler._step_meta["handler"],
                    status=StepStatus.SUBMITTED,
                    locked_by="test-engine-001",
                    lock_expires_at=datetime.now(UTC) + timedelta(seconds=30),
                    fence_token=1,
                    depends_on=[],
                ),
            ],
        )
        await store.insert(wf)

        engine = WorkflowEngine(
            store, instance_id="test-engine-001",
            claim_interval=10, heartbeat_interval=10, sweep_interval=0.05,
            step_stuck_seconds=1.0,
        )
        # Simulate an active step by adding it to _active
        dummy_task = asyncio.create_task(asyncio.sleep(100))
        engine._active[(wf.id, "s1")] = _ActiveStep(dummy_task, 1)

        try:
            # Start engine (only sweep runs quickly; claim/heartbeat are slow)
            await engine.start()
            await asyncio.sleep(0.3)

            # Check DB before stop() — sweep should have skipped this step
            loaded = await store.get(wf.id)
            assert loaded.steps[0].locked_by == "test-engine-001"
            assert loaded.steps[0].fence_token == 1

            # Clean up _active so stop() doesn't release the lock we're checking
            engine._active.pop((wf.id, "s1"), None)
            await engine.stop()
        finally:
            dummy_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await dummy_task


# ---------------------------------------------------------------------------
# Context injection
# ---------------------------------------------------------------------------


class TestContextInjection:
    async def test_handler_without_context_still_works(self, store):
        """Existing 2-arg handlers work when engine has context."""
        engine = WorkflowEngine(
            store, instance_id="ctx-test",
            claim_interval=0.05, heartbeat_interval=0.05, sweep_interval=10,
            context={"db": "fake_db"},
        )
        wf = Workflow(name="ctx_compat", steps=[Step(name="noop", handler=noop_handler._step_meta["handler"])])
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED

    async def test_handler_receives_context(self, store):
        """A 3-arg handler receives the engine's context dict."""
        received_ctx = {}

        async def ctx_handler(_config, _results, ctx: dict[str, Any]):
            received_ctx.update(ctx)
            return StepResult()

        ctx_handler._step_meta = {"needs_context": True}
        _STEP_REGISTRY["tests.ctx_handler"] = ctx_handler

        engine = WorkflowEngine(
            store, instance_id="ctx-test",
            claim_interval=0.05, heartbeat_interval=0.05, sweep_interval=10,
            context={"db": "my_db", "api_key": "secret123"},
        )
        wf = Workflow(name="ctx_inject", steps=[Step(name="ctx_step", handler="tests.ctx_handler")])
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        assert received_ctx["db"] == "my_db"
        assert received_ctx["api_key"] == "secret123"

    async def test_completeness_check_receives_context(self, store):
        """A 4-arg completeness check receives the engine's context dict."""
        check_ctx = {}

        async def async_submit(_config, _results):
            return SubmitResult(job_id="j1")

        async def check_with_ctx(_config, _results, _result, ctx: dict[str, Any]):
            check_ctx.update(ctx)
            return CheckResult(complete=True, progress=1.0)

        async_submit._step_meta = {"needs_context": False}
        check_with_ctx._step_meta = {"needs_context": True}
        _STEP_REGISTRY["tests.async_submit_ctx"] = async_submit
        _STEP_REGISTRY["tests.check_with_ctx"] = check_with_ctx

        engine = WorkflowEngine(
            store, instance_id="ctx-test",
            claim_interval=0.05, heartbeat_interval=0.05, sweep_interval=10,
            context={"service": "my_service"},
        )
        wf = Workflow(
            name="ctx_poll",
            steps=[Step(
                name="async_ctx",
                handler="tests.async_submit_ctx",
                is_async=True,
                completeness_check="tests.check_with_ctx",
                poll_policy=PollPolicy(interval=0.05, timeout=5.0, max_polls=10),
            )],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(1.0)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        assert check_ctx["service"] == "my_service"

    async def test_no_context_by_default(self, store):
        """Engine without context param uses empty dict (backward compat)."""
        engine = WorkflowEngine(
            store, instance_id="no-ctx",
            claim_interval=0.05, heartbeat_interval=0.05, sweep_interval=10,
        )
        wf = Workflow(name="no_ctx", steps=[Step(name="noop", handler=noop_handler._step_meta["handler"])])
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED


# ---------------------------------------------------------------------------
# Workflow cancellation
# ---------------------------------------------------------------------------


class TestCancellation:
    async def test_cancellation_stops_execution(self, store):
        """Cancel a workflow mid-execution — engine should stop after detecting it."""
        call_count = 0

        async def slow_handler(_config, _results):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.3)
            return StepResult()

        slow_handler._step_meta = {"handler": "tests.slow_cancel", "needs_context": False}
        _STEP_REGISTRY["tests.slow_cancel"] = slow_handler

        engine = WorkflowEngine(
            store, instance_id="cancel-test",
            claim_interval=0.05, heartbeat_interval=0.05, sweep_interval=10,
        )
        wf = Workflow(
            name="cancel_test",
            steps=[
                Step(name="s1", handler="tests.slow_cancel"),
                Step(name="s2", handler="tests.slow_cancel"),
                Step(name="s3", handler="tests.slow_cancel"),
            ],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(0.5)

        await store.cancel_workflow(wf.id)
        await asyncio.sleep(1.0)

        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.CANCELLED
        assert call_count < 3

    async def test_cancelled_workflow_not_claimed(self, store):
        wf = Workflow(
            name="already_cancelled",
            status=WorkflowStatus.CANCELLED,
            steps=[Step(name="s1", handler=noop_handler._step_meta["handler"], depends_on=[])],
        )
        await store.insert(wf)

        claimable = await store.find_claimable_steps()
        assert not any(wf_id == wf.id for wf_id, _ in claimable)

        result = await store.try_claim_step(wf.id, "s1", "inst_1")
        assert result is None

    async def test_is_terminal_includes_cancelled(self):
        wf = Workflow(name="terminal_check", status=WorkflowStatus.CANCELLED)
        assert wf.is_terminal() is True


# ---------------------------------------------------------------------------
# Step timeouts
# ---------------------------------------------------------------------------


class TestStepTimeout:
    async def test_step_timeout_fails_step(self, store, engine):
        """A handler that sleeps forever should be killed by step_timeout."""

        async def hang_forever(_config, _results):
            await asyncio.sleep(3600)
            return StepResult()

        hang_forever._step_meta = {"needs_context": False}
        _STEP_REGISTRY["tests.hang_forever"] = hang_forever

        wf = Workflow(
            name="timeout_test",
            steps=[
                Step(
                    name="hanging",
                    handler="tests.hang_forever",
                    step_timeout=0.2,
                    retry_policy=RetryPolicy(max_attempts=1, wait_seconds=0.01),
                ),
            ],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(1.0)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.FAILED
        assert loaded.steps[0].status == StepStatus.FAILED
        assert "timed out" in (loaded.steps[0].result.error or "").lower()

    async def test_step_timeout_zero_means_no_timeout(self, store, engine):
        """step_timeout=0 means no timeout — handler completes normally."""
        wf = Workflow(
            name="no_timeout_test",
            steps=[
                Step(
                    name="noop",
                    handler=noop_handler._step_meta["handler"],
                    step_timeout=0,
                ),
            ],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        assert loaded.steps[0].status == StepStatus.COMPLETED


# ---------------------------------------------------------------------------
# Completeness check retries
# ---------------------------------------------------------------------------


class TestCompletenessCheckRetries:
    async def test_check_retries_on_error_then_succeeds(self, store, engine):
        """Check raises once then returns True — step completes."""
        call_count = 0

        async def flaky_check(_config, _results, _result):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise RuntimeError("transient error")
            return CheckResult(complete=True)

        flaky_check._step_meta = {
            "needs_context": False,
            "retry": RetryPolicy(max_attempts=3, wait_seconds=0.01, wait_multiplier=1.0),
        }
        _STEP_REGISTRY["tests.flaky_check"] = flaky_check

        wf = Workflow(
            name="check_retry_test",
            steps=[
                Step(
                    name="async_step",
                    handler=async_submit_handler._step_meta["handler"],
                    is_async=True,
                    completeness_check="tests.flaky_check",
                    poll_policy=PollPolicy(interval=0.05, timeout=5.0, max_polls=10),
                ),
            ],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(2.0)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        assert loaded.steps[0].status == StepStatus.COMPLETED
        assert call_count == 2  # 1 failure + 1 success

    async def test_check_retry_exhaustion_fails_step(self, store, engine):
        """Check always raises — step fails after retries exhausted."""
        call_count = 0

        async def always_fail_check(_config, _results, _result):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("persistent error")

        always_fail_check._step_meta = {
            "needs_context": False,
            "retry": RetryPolicy(max_attempts=2, wait_seconds=0.01, wait_multiplier=1.0),
        }
        _STEP_REGISTRY["tests.always_fail_check"] = always_fail_check

        wf = Workflow(
            name="check_exhaust_test",
            steps=[
                Step(
                    name="async_step",
                    handler=async_submit_handler._step_meta["handler"],
                    is_async=True,
                    completeness_check="tests.always_fail_check",
                    poll_policy=PollPolicy(interval=0.05, timeout=5.0, max_polls=10),
                ),
            ],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(2.0)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.FAILED
        assert loaded.steps[0].status == StepStatus.FAILED
        assert "persistent error" in (loaded.steps[0].result.error or "")
        # Should have been called exactly max_attempts times in one poll cycle
        assert call_count == 2

    async def test_check_no_retry_on_false_return(self, store, engine):
        """Check returns False (not complete) — no retry, schedules next poll."""
        call_count = 0

        async def slow_check(_config, _results, _result):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                return CheckResult(complete=True)
            return CheckResult(complete=False)

        slow_check._step_meta = {
            "needs_context": False,
            "retry": RetryPolicy(max_attempts=3, wait_seconds=0.01, wait_multiplier=1.0),
        }
        _STEP_REGISTRY["tests.slow_check"] = slow_check

        wf = Workflow(
            name="no_retry_false_test",
            steps=[
                Step(
                    name="async_step",
                    handler=async_submit_handler._step_meta["handler"],
                    is_async=True,
                    completeness_check="tests.slow_check",
                    poll_policy=PollPolicy(interval=0.05, timeout=5.0, max_polls=10),
                ),
            ],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(1.0)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        # Each call returns immediately (no retry on False), so 3 poll cycles
        assert call_count == 3

    async def test_check_default_retry_policy_fails_after_max_attempts(self, store, engine):
        """Check with no explicit retry uses default RetryPolicy (max_attempts=3).

        We verify the default policy is applied by making the check always
        fail.  The step should fail after 3 attempts (default max_attempts).
        """
        call_count = 0

        async def always_fail_default(_config, _results, _result):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("always fails")

        # No retry in _step_meta — engine falls back to RetryPolicy()
        always_fail_default._step_meta = {"needs_context": False}
        _STEP_REGISTRY["tests.always_fail_default"] = always_fail_default

        wf = Workflow(
            name="default_retry_test",
            steps=[
                Step(
                    name="async_step",
                    handler=async_submit_handler._step_meta["handler"],
                    is_async=True,
                    completeness_check="tests.always_fail_default",
                    poll_policy=PollPolicy(interval=0.05, timeout=30.0, max_polls=10),
                ),
            ],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(8.0)
        await engine.stop()

        loaded = await store.get(wf.id)
        # Default RetryPolicy has max_attempts=3, step should fail
        assert loaded.status == WorkflowStatus.FAILED
        assert loaded.steps[0].status == StepStatus.FAILED
        assert "always fails" in (loaded.steps[0].result.error or "")
        # Default max_attempts=3: called 3 times then exhausted
        assert call_count == 3


# ---------------------------------------------------------------------------
# Concurrent step execution (dependency-based)
# ---------------------------------------------------------------------------


class TestConcurrentSteps:
    async def test_parallel_root_steps(self, store):
        """Two root steps (depends_on=[]) both complete concurrently."""
        wf = Workflow(
            name="parallel_roots",
            steps=[
                Step(name="a", handler=noop_handler._step_meta["handler"], depends_on=[]),
                Step(name="b", handler=noop_handler._step_meta["handler"], depends_on=[]),
            ],
        )
        await store.insert(wf)

        engine = WorkflowEngine(
            store, instance_id="parallel-test",
            claim_interval=0.05, heartbeat_interval=0.05, sweep_interval=10,
            max_concurrent=5,
        )
        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        assert all(s.status == StepStatus.COMPLETED for s in loaded.steps)

    async def test_diamond_dependency(self, store):
        """Diamond: A(root) → B,C(depend on A) → D(depends on B,C)."""
        wf = Workflow(
            name="diamond",
            steps=[
                Step(name="a", handler=noop_handler._step_meta["handler"], depends_on=[]),
                Step(name="b", handler=noop_handler._step_meta["handler"], depends_on=["a"]),
                Step(name="c", handler=noop_handler._step_meta["handler"], depends_on=["a"]),
                Step(name="d", handler=noop_handler._step_meta["handler"], depends_on=["b", "c"]),
            ],
        )
        await store.insert(wf)

        engine = WorkflowEngine(
            store, instance_id="diamond-test",
            claim_interval=0.05, heartbeat_interval=0.05, sweep_interval=10,
            max_concurrent=5,
        )
        await engine.start()
        await asyncio.sleep(1.0)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        assert all(s.status == StepStatus.COMPLETED for s in loaded.steps)

    async def test_step_failure_fails_workflow(self, store):
        """A step failure triggers try_fail_workflow."""
        wf = Workflow(
            name="fail_propagation",
            steps=[
                Step(
                    name="doomed",
                    handler=fail_handler._step_meta["handler"],
                    depends_on=[],
                    retry_policy=RetryPolicy(max_attempts=1, wait_seconds=0.01),
                ),
                Step(name="never_runs", handler=noop_handler._step_meta["handler"], depends_on=["doomed"]),
            ],
        )
        await store.insert(wf)

        engine = WorkflowEngine(
            store, instance_id="fail-test",
            claim_interval=0.05, heartbeat_interval=0.05, sweep_interval=10,
        )
        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.FAILED
        assert loaded.steps[0].status == StepStatus.FAILED
        # The dependent step should still be PENDING (never executed)
        assert loaded.steps[1].status == StepStatus.PENDING

    async def test_sequential_default_depends_on(self, store, engine):
        """Steps without explicit depends_on default to sequential chain."""
        wf = Workflow(
            name="seq_default",
            steps=[
                Step(name="s1", handler=noop_handler._step_meta["handler"]),
                Step(name="s2", handler=noop_handler._step_meta["handler"]),
                Step(name="s3", handler=noop_handler._step_meta["handler"]),
            ],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(1.0)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        assert all(s.status == StepStatus.COMPLETED for s in loaded.steps)

    async def test_results_from_dependencies(self, store, engine):
        """Handler receives results from its declared dependencies only."""
        received_results = {}

        async def capture_results(_config, results):
            received_results.update(results)
            return StepResult()

        capture_results._step_meta = {"needs_context": False}
        _STEP_REGISTRY["tests.capture_results"] = capture_results

        wf = Workflow(
            name="dep_results",
            steps=[
                Step(name="greet", handler=greet_handler._step_meta["handler"],
                     config=GreetConfig(name="World"), depends_on=[]),
                Step(name="noop", handler=noop_handler._step_meta["handler"], depends_on=[]),
                Step(name="capture", handler="tests.capture_results", depends_on=["greet"]),
            ],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(1.0)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        # capture step should only see greet's result, not noop's
        assert "greet" in received_results
        assert "noop" not in received_results
        assert isinstance(received_results["greet"], GreetResult)
