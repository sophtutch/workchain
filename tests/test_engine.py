"""Tests for workchain.engine — WorkflowEngine, helpers, execution paths."""

from __future__ import annotations

import asyncio
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
)
from workchain.decorators import _STEP_REGISTRY
from workchain.engine import WorkflowEngine, _build_results, _wrap_handler_return
from workchain.exceptions import HandlerError
from workchain.models import (
    PollHint,
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
    def test_empty_workflow(self):
        wf = Workflow(name="test", steps=[])
        assert _build_results(wf, 0) == {}

    def test_steps_with_results(self):
        wf = Workflow(
            name="test",
            steps=[
                Step(name="s1", handler="mod.f", result=GreetResult(greeting="hi")),
                Step(name="s2", handler="mod.f"),
            ],
        )
        results = _build_results(wf, 2)
        assert "s1" in results
        assert isinstance(results["s1"], GreetResult)
        assert "s2" not in results  # no result

    def test_respects_up_to_index(self):
        wf = Workflow(
            name="test",
            steps=[
                Step(name="s1", handler="mod.f", result=StepResult()),
                Step(name="s2", handler="mod.f", result=StepResult()),
            ],
        )
        results = _build_results(wf, 1)
        assert "s1" in results
        assert "s2" not in results

    def test_skips_none_results(self):
        wf = Workflow(
            name="test",
            steps=[
                Step(name="s1", handler="mod.f"),
                Step(name="s2", handler="mod.f", result=StepResult()),
            ],
        )
        results = _build_results(wf, 2)
        assert "s1" not in results
        assert "s2" in results


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

    async def test_workflow_advances_step_index(self, store, engine):
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
        assert loaded.current_step_index == 2

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
            return PollHint(complete=False, progress=0.1)

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
            return PollHint(complete=False)

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
    async def test_recover_idempotent_reruns(self, store, engine):
        """An idempotent step found in SUBMITTED state gets re-run."""
        wf = Workflow(
            name="recovery_idem",
            status=WorkflowStatus.RUNNING,
            fence_token=1,
            steps=[
                Step(
                    name="noop",
                    handler=noop_handler._step_meta["handler"],
                    status=StepStatus.SUBMITTED,
                    idempotent=True,
                ),
            ],
        )
        await store.insert(wf)

        # Claim and run
        claimed = await store.try_claim(wf.id, "test-engine-001")
        assert claimed is not None

        await engine._run_workflow(claimed)

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        assert loaded.steps[0].status == StepStatus.COMPLETED

    async def test_recover_non_idempotent_needs_review(self, store, engine):
        """A non-idempotent step without verify hook marks NEEDS_REVIEW."""
        wf = Workflow(
            name="recovery_non_idem",
            status=WorkflowStatus.RUNNING,
            fence_token=1,
            steps=[
                Step(
                    name="danger",
                    handler=noop_handler._step_meta["handler"],
                    status=StepStatus.SUBMITTED,
                    idempotent=False,
                ),
            ],
        )
        await store.insert(wf)

        claimed = await store.try_claim(wf.id, "test-engine-001")
        await engine._run_workflow(claimed)

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.NEEDS_REVIEW

    async def test_recover_with_verify_completion(self, store, engine):
        """Step with verify_completion that returns True marks COMPLETED."""
        wf = Workflow(
            name="recovery_verify",
            status=WorkflowStatus.RUNNING,
            fence_token=1,
            steps=[
                Step(
                    name="verified",
                    handler=noop_handler._step_meta["handler"],
                    status=StepStatus.SUBMITTED,
                    verify_completion=verify_done._step_meta["handler"],
                    idempotent=False,
                ),
            ],
        )
        await store.insert(wf)

        claimed = await store.try_claim(wf.id, "test-engine-001")
        await engine._run_workflow(claimed)

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
        assert loaded.steps[0].status == StepStatus.COMPLETED

    async def test_recover_async_with_completeness_check(self, store, engine):
        """Async step in SUBMITTED with result: completeness_check transitions to BLOCKED."""
        wf = Workflow(
            name="recovery_async",
            status=WorkflowStatus.RUNNING,
            fence_token=1,
            steps=[
                Step(
                    name="async_step",
                    handler=async_submit_handler._step_meta["handler"],
                    status=StepStatus.SUBMITTED,
                    is_async=True,
                    completeness_check=check_complete_always_done._step_meta["handler"],
                    result=SubmitResult(job_id="existing_job"),
                    idempotent=False,
                ),
            ],
        )
        await store.insert(wf)

        claimed = await store.try_claim(wf.id, "test-engine-001")
        await engine._run_workflow(claimed)

        loaded = await store.get(wf.id)
        # check_always_done returns True, so step should be COMPLETED
        assert loaded.steps[0].status == StepStatus.COMPLETED

    async def test_recover_async_with_poll_hint(self, store, engine):
        """Async recovery handles PollHint(complete=True) correctly."""

        async def check_returns_poll_hint(_config, _results, _result):
            return PollHint(complete=True, progress=1.0)

        check_returns_poll_hint._step_meta = {"needs_context": False}
        _STEP_REGISTRY["tests.check_poll_hint_done"] = check_returns_poll_hint

        wf = Workflow(
            name="recovery_poll_hint",
            status=WorkflowStatus.RUNNING,
            fence_token=1,
            steps=[
                Step(
                    name="async_step",
                    handler=async_submit_handler._step_meta["handler"],
                    status=StepStatus.SUBMITTED,
                    is_async=True,
                    completeness_check="tests.check_poll_hint_done",
                    result=SubmitResult(job_id="existing_job"),
                    idempotent=False,
                ),
            ],
        )
        await store.insert(wf)

        claimed = await store.try_claim(wf.id, "test-engine-001")
        await engine._run_workflow(claimed)

        loaded = await store.get(wf.id)
        # PollHint(complete=True) should be recognized as complete
        assert loaded.steps[0].status == StepStatus.COMPLETED


# ---------------------------------------------------------------------------
# Sweep loop
# ---------------------------------------------------------------------------


class TestSweepLoop:
    async def test_sweep_force_releases_anomaly(self, store):
        """Sweep detects stuck workflow and force-releases its lock."""
        wf = Workflow(
            name="stuck_wf",
            status=WorkflowStatus.RUNNING,
            locked_by="dead_instance",
            lock_expires_at=datetime.now(UTC) + timedelta(seconds=30),
            updated_at=datetime.now(UTC) - timedelta(seconds=600),
            fence_token=1,
            steps=[
                Step(name="s1", handler=noop_handler._step_meta["handler"], status=StepStatus.SUBMITTED),
            ],
        )
        await store.insert(wf)

        # Run anomaly detection directly instead of via engine loop
        anomalies = await store.find_anomalies(step_stuck_seconds=300)
        assert any(a["workflow_id"] == wf.id for a in anomalies)

        released = await store.force_release_lock(wf.id)
        assert released is True

        loaded = await store.get(wf.id)
        assert loaded.locked_by is None


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
            return PollHint(complete=True, progress=1.0)

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
        wf = Workflow(name="already_cancelled", status=WorkflowStatus.CANCELLED)
        await store.insert(wf)

        ids = await store.find_claimable()
        assert wf.id not in ids

        result = await store.try_claim(wf.id, "inst_1")
        assert result is None

    async def test_is_terminal_includes_cancelled(self):
        wf = Workflow(name="terminal_check", status=WorkflowStatus.CANCELLED)
        assert wf.is_terminal() is True
