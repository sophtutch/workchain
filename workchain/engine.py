"""
Core workflow engine: claims workflows, executes steps sequentially,
handles retries, async polling, heartbeats, crash recovery, and
graceful shutdown.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import platform
import signal
import traceback
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from workchain.audit import AuditEvent, AuditEventType, AuditLogger, NullAuditLogger
from workchain.decorators import get_handler
from workchain.exceptions import FenceRejectedError, HandlerError, RetryExhaustedError
from workchain.models import (
    PollHint,
    RetryPolicy,
    Step,
    StepResult,
    StepStatus,
    Workflow,
    WorkflowStatus,
)
from workchain.retry import retrying_from_policy
from workchain.store import MongoWorkflowStore

logger = logging.getLogger(__name__)


def _build_results(wf: Workflow, up_to_index: int) -> dict[str, StepResult]:
    """Build a dict of preceding step results keyed by step name."""
    return {s.name: s.result for s in wf.steps[:up_to_index] if s.result is not None}


def _wrap_handler_return(result_data: Any) -> tuple[StepResult, str | None]:
    """
    Normalise a handler's return value into a (StepResult, result_type) pair.

    If the handler returned a StepResult subclass, use it directly.
    Otherwise wrap a plain dict in a base StepResult (for backwards compat).
    """
    if not isinstance(result_data, StepResult):
        raise HandlerError(
            f"Step handler must return a StepResult subclass, got {type(result_data).__name__}"
        )

    result = result_data
    result.completed_at = result.completed_at or datetime.now(UTC)
    cls = type(result)
    result_type = (
        f"{cls.__module__}.{cls.__qualname__}" if cls is not StepResult else None
    )
    return result, result_type


class WorkflowEngine:
    """
    Multi-instance-safe workflow engine backed by MongoDB.

    Usage:
        store = MongoWorkflowStore(db)
        engine = WorkflowEngine(store)
        await engine.start()   # begins claim loop + heartbeat
        ...
        await engine.stop()    # graceful shutdown
    """

    def __init__(
        self,
        store: MongoWorkflowStore,
        instance_id: str | None = None,
        claim_interval: float = 5.0,
        heartbeat_interval: float = 10.0,
        sweep_interval: float = 60.0,
        step_stuck_seconds: float = 300.0,
        max_concurrent: int = 5,
        audit_logger: AuditLogger | None = None,
        log_heartbeats: bool = False,
        context: dict[str, Any] | None = None,
    ):
        self._store = store
        self._instance_id = instance_id or f"{platform.node()}-{uuid.uuid4().hex[:8]}"
        self._claim_interval = claim_interval
        self._heartbeat_interval = heartbeat_interval
        self._sweep_interval = sweep_interval
        self._step_stuck_seconds = step_stuck_seconds
        self._max_concurrent = max_concurrent
        self._audit: AuditLogger = audit_logger or NullAuditLogger()
        self._log_heartbeats = log_heartbeats
        self._audit_tasks: set[asyncio.Task] = set()
        self._context: dict[str, Any] = context or {}

        # Active workflows this instance is processing: wf_id -> (Workflow, task)
        self._active: dict[str, tuple[Workflow, asyncio.Task]] = {}
        self._shutdown_event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Handler calling — context injection
    # ------------------------------------------------------------------

    async def _call_handler(self, handler: Callable[..., Any], *args: Any) -> Any:
        """Call a handler, using decorator metadata to decide context injection.

        Reads ``_step_meta["needs_context"]`` from the handler (set by
        ``@step``, ``@async_step``, or ``@completeness_check`` decorators).
        If True, appends ``self._context`` as the final argument.

        Supports both async and sync handlers — sync results are returned
        directly without awaiting.
        """
        meta = getattr(handler, "_step_meta", {})
        result = handler(*args, self._context) if meta.get("needs_context", False) else handler(*args)

        if asyncio.iscoroutine(result):
            return await result
        return result

    # ------------------------------------------------------------------
    # Audit helpers
    # ------------------------------------------------------------------

    def _emit(
        self,
        event_type: AuditEventType,
        wf: Workflow,
        *,
        step: Step | None = None,
        idx: int | None = None,
        step_status_before: str | None = None,
        workflow_status_before: str | None = None,
        fence_token_before: int | None = None,
        fields_changed: dict | None = None,
        **kwargs: Any,
    ) -> None:
        """Construct and emit an audit event with common fields pre-populated."""
        event = AuditEvent(
            workflow_id=wf.id,
            workflow_name=wf.name,
            event_type=event_type,
            instance_id=self._instance_id,
            fence_token=wf.fence_token,
            fence_token_before=fence_token_before,
            workflow_status=wf.status.value,
            workflow_status_before=workflow_status_before,
            step_index=idx,
            step_name=step.name if step else None,
            step_handler=step.handler if step else None,
            step_status=step.status.value if step else None,
            step_status_before=step_status_before,
            is_async=step.is_async if step else None,
            idempotent=step.idempotent if step else None,
            fields_changed=fields_changed,
            **kwargs,
        )
        task = asyncio.ensure_future(self._audit.emit(event))
        self._audit_tasks.add(task)
        task.add_done_callback(self._audit_tasks.discard)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the engine: claim loop, heartbeat loop, and signal handlers."""
        await self._store.ensure_indexes()

        # Register signal handlers (POSIX only — Windows ProactorEventLoop
        # does not support add_signal_handler)
        loop = asyncio.get_running_loop()
        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, lambda: asyncio.ensure_future(self.stop()))
        except (NotImplementedError, OSError):
            pass  # Windows ProactorEventLoop doesn't support signal handlers

        self._tasks.append(asyncio.create_task(self._claim_loop(), name="claim_loop"))
        self._tasks.append(
            asyncio.create_task(self._heartbeat_loop(), name="heartbeat_loop")
        )
        self._tasks.append(asyncio.create_task(self._sweep_loop(), name="sweep_loop"))

        logger.info("WorkflowEngine started instance=%s", self._instance_id)

    async def stop(self) -> None:
        """Graceful shutdown: release all locks, cancel background tasks."""
        logger.info("Shutting down instance=%s ...", self._instance_id)
        self._shutdown_event.set()

        # Release all locks so peers can pick up immediately
        for wf_id, (wf, task) in list(self._active.items()):
            task.cancel()
            released = await self._store.release_lock(
                wf_id, self._instance_id, wf.fence_token
            )
            logger.info("Released lock on workflow=%s success=%s", wf_id, released)

        self._active.clear()

        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

        # Drain pending audit writes with a timeout — use asyncio.wait
        # (not wait_for) so timed-out tasks continue in the background
        # rather than being cancelled
        if self._audit_tasks:
            _done, pending = await asyncio.wait(self._audit_tasks, timeout=5.0)
            if pending:
                logger.warning("Timed out waiting for %d audit tasks during shutdown", len(pending))
        logger.info("Shutdown complete.")

    # ------------------------------------------------------------------
    # Claim loop — discovers and claims available workflows
    # ------------------------------------------------------------------

    async def _claim_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                if len(self._active) < self._max_concurrent:
                    claimable = await self._store.find_claimable(
                        limit=self._max_concurrent - len(self._active)
                    )
                    for wf_id in claimable:
                        if wf_id in self._active:
                            continue
                        wf = await self._store.try_claim(wf_id, self._instance_id)
                        if wf is not None:
                            self._emit(
                                AuditEventType.WORKFLOW_CLAIMED, wf,
                                fence_token_before=wf.fence_token - 1,
                                locked_by=self._instance_id,
                            )
                            task = asyncio.create_task(
                                self._run_workflow(wf),
                                name=f"workflow-{wf_id}",
                            )
                            self._active[wf_id] = (wf, task)
            except Exception:
                logger.exception("Error in claim loop")

            await self._wait(self._claim_interval)

    # ------------------------------------------------------------------
    # Heartbeat loop — keeps locks alive while processing
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        while not self._shutdown_event.is_set():
            for wf_id, (wf, task) in list(self._active.items()):
                try:
                    ok = await self._store.heartbeat(
                        wf_id, self._instance_id, wf.fence_token
                    )
                    if ok and self._log_heartbeats:
                        self._emit(AuditEventType.HEARTBEAT, wf)
                    if not ok:
                        # Lock was stolen — abort this workflow's task
                        logger.warning("Lost lock on workflow=%s, cancelling", wf_id)
                        task.cancel()
                        self._active.pop(wf_id, None)
                except Exception:
                    logger.exception("Heartbeat error for workflow=%s", wf_id)

            await self._wait(self._heartbeat_interval)

    # ------------------------------------------------------------------
    # Slow sweep — catches anomalies the fast claim loop misses
    # ------------------------------------------------------------------

    async def _sweep_loop(self) -> None:
        """
        Runs on a longer interval than the claim loop. Detects workflows
        that are stuck in inconsistent states and force-releases their
        locks so the fast claim loop can reclaim them.

        Anomalies detected:
        - Steps stuck in SUBMITTED/RUNNING with no updated_at progress
        - Stale locks with no heartbeat activity
        - Completed steps where current_step_index was never advanced
        """
        while not self._shutdown_event.is_set():
            try:
                anomalies = await self._store.find_anomalies(
                    step_stuck_seconds=self._step_stuck_seconds,
                )
                for entry in anomalies:
                    wf_id = entry["workflow_id"]
                    anomaly = entry["anomaly"]

                    # Don't force-release workflows this instance owns
                    if wf_id in self._active:
                        continue

                    logger.warning(
                        "Sweep detected anomaly=%s on workflow=%s, force-releasing lock",
                        anomaly,
                        wf_id,
                    )
                    await self._store.force_release_lock(wf_id)
                    # Emit audit events for the anomaly
                    sweep_wf = await self._store.get(wf_id)
                    if sweep_wf:
                        self._emit(
                            AuditEventType.SWEEP_ANOMALY, sweep_wf,
                            anomaly_type=anomaly,
                        )
                        self._emit(
                            AuditEventType.LOCK_FORCE_RELEASED, sweep_wf,
                            anomaly_type=anomaly, lock_released=True,
                        )
                    # The fast claim loop will pick it up on its next iteration

                if anomalies:
                    logger.info("Sweep released %d anomalous workflows", len(anomalies))

            except Exception:
                logger.exception("Error in sweep loop")

            await self._wait(self._sweep_interval)

    # ------------------------------------------------------------------
    # Workflow execution
    # ------------------------------------------------------------------

    async def _run_workflow(self, wf: Workflow) -> None:
        """
        Execute a workflow from its current step index.

        For sync steps: execute handler, persist result, advance.
        For async steps: two distinct phases —
          1. SUBMISSION: execute handler (initiates external work), persist
             result with job_id, set BLOCKED, schedule next_poll_at,
             RELEASE LOCK, return. The fast sweep reclaims when due.
          2. POLL: claim a BLOCKED workflow, run one completeness check.
             If complete → advance. If not → update state, schedule
             next_poll_at, RELEASE LOCK, return.

        The lock is never held while waiting between polls. Any instance
        can pick up the next poll cycle.
        """
        wf_id = wf.id
        fence = wf.fence_token

        try:
            while wf.current_step_index < len(wf.steps):
                if self._shutdown_event.is_set():
                    return

                # Check for external cancellation
                wf = await self._store.get(wf_id)
                if wf is None or wf.status == WorkflowStatus.CANCELLED:
                    if wf:
                        self._emit(AuditEventType.WORKFLOW_CANCELLED, wf)
                    logger.info("Workflow %s cancelled.", wf_id)
                    return

                step = wf.steps[wf.current_step_index]
                idx = wf.current_step_index

                # --- Recovery for SUBMITTED/RUNNING (crash during handler) ---
                if step.status in (StepStatus.SUBMITTED, StepStatus.RUNNING):
                    step = await self._recover_step(wf, idx, step, fence)
                    if step is None:
                        return  # lock lost or needs_review

                # --- BLOCKED: execute a single poll cycle, then release ---
                if step.status == StepStatus.BLOCKED:
                    poll_result = await self._poll_once(wf, idx, step, fence)
                    if poll_result == "complete":
                        # Refresh and continue to advance
                        wf = await self._store.get(wf_id)
                        if wf is None:
                            return
                        step = wf.steps[idx]
                        # Fall through to advance below
                    elif poll_result == "released":
                        # Lock released, next poll scheduled. Exit cleanly.
                        return
                    else:
                        # "failed" or "lost_lock"
                        return

                if step.status == StepStatus.COMPLETED:
                    # Advance to next step
                    wf = await self._advance(wf, fence)
                    if wf is None:
                        return
                    continue

                # --- Normal execution: PENDING step ---
                # Mark step as SUBMITTED (crash-safe write-ahead)
                wf = await self._store.submit_step(wf_id, idx, fence, attempt=step.attempt + 1)
                if wf is None:
                    return  # fence rejected

                step = wf.steps[idx]
                self._emit(
                    AuditEventType.STEP_SUBMITTED, wf,
                    step=step, idx=idx,
                    step_status_before=StepStatus.PENDING.value,
                )

                # Execute the step handler with retries
                try:
                    handler = get_handler(step.handler)
                    result_data = await self._run_step_with_retry(
                        handler,
                        step,
                        wf_id,
                        idx,
                        fence,
                    )
                    result, result_type = _wrap_handler_return(result_data)
                    # Refresh wf after retries may have updated attempt count
                    wf = await self._store.get(wf_id)
                    if wf is None:
                        return
                    step = wf.steps[idx]
                except Exception:
                    logger.exception(
                        "Step %s failed after %d attempts", step.name, step.attempt
                    )
                    wf = await self._store.get(wf_id)
                    if wf is None:
                        return
                    step = wf.steps[idx]
                    fail_result = StepResult(
                        error=traceback.format_exc(),
                        completed_at=datetime.now(UTC),
                    )
                    wf = await self._store.fail_step(
                        wf_id, idx, fence,
                        result=fail_result.model_dump(mode="python", serialize_as_any=True),
                    )
                    if wf:
                        error_lines = (fail_result.error or "").strip().splitlines()
                        brief_error = error_lines[-1] if error_lines else ""
                        self._emit(
                            AuditEventType.STEP_FAILED, wf,
                            step=wf.steps[idx], idx=idx,
                            step_status_before=StepStatus.RUNNING.value,
                            error=brief_error,
                            error_traceback=fail_result.error,
                        )
                    await self._store.advance_step(
                        wf_id,
                        fence,
                        idx,
                        workflow_status=WorkflowStatus.FAILED,
                    )
                    wf = await self._store.get(wf_id)
                    if wf:
                        self._emit(
                            AuditEventType.WORKFLOW_FAILED, wf,
                            workflow_status_before=WorkflowStatus.RUNNING.value,
                        )
                    self._active.pop(wf_id, None)
                    return

                # --- Async step: persist submission result, release lock ---
                if step.is_async and step.completeness_check:
                    now = datetime.now(UTC)
                    policy = step.poll_policy
                    next_poll = now + timedelta(seconds=policy.interval)

                    wf = await self._store.block_step(
                        wf_id, idx, fence,
                        result=result.model_dump(mode="python", serialize_as_any=True),
                        result_type=result_type,
                        poll_started_at=now,
                        next_poll_at=next_poll,
                        current_poll_interval=policy.interval,
                    )
                    if wf is None:
                        return

                    self._emit(
                        AuditEventType.STEP_BLOCKED, wf,
                        step=wf.steps[idx], idx=idx,
                        step_status_before=StepStatus.RUNNING.value,
                        result_summary=result.model_dump(exclude_none=True),
                        next_poll_at=next_poll,
                        current_poll_interval=policy.interval,
                    )

                    # Remove from active BEFORE releasing lock to prevent
                    # the heartbeat loop from heartbeating a released workflow
                    self._active.pop(wf_id, None)

                    # RELEASE LOCK — any instance can pick up the next poll
                    await self._store.release_lock(wf_id, self._instance_id, fence)
                    self._emit(
                        AuditEventType.LOCK_RELEASED, wf,
                        lock_released=True,
                    )
                    logger.info(
                        "Step %s submitted, lock released. Next poll at %s",
                        step.name,
                        next_poll.isoformat(),
                    )
                    return  # exit cleanly, fast sweep will reclaim when due

                # --- Sync step: mark completed, advance ---
                wf = await self._store.complete_step(
                    wf_id, idx, fence,
                    result=result.model_dump(mode="python", serialize_as_any=True),
                    result_type=result_type,
                )
                if wf is None:
                    return

                self._emit(
                    AuditEventType.STEP_COMPLETED, wf,
                    step=wf.steps[idx], idx=idx,
                    step_status_before=StepStatus.RUNNING.value,
                    result_summary=result.model_dump(exclude_none=True),
                )

                wf = await self._advance(wf, fence)
                if wf is None:
                    return
                self._emit(
                    AuditEventType.STEP_ADVANCED, wf,
                    step=wf.steps[idx], idx=idx,
                )

            # All steps done
            await self._store.advance_step(
                wf_id,
                fence,
                wf.current_step_index,
                workflow_status=WorkflowStatus.COMPLETED,
            )
            await self._store.release_lock(wf_id, self._instance_id, fence)
            wf = await self._store.get(wf_id)
            if wf:
                self._emit(
                    AuditEventType.WORKFLOW_COMPLETED, wf,
                    workflow_status_before=WorkflowStatus.RUNNING.value,
                    lock_released=True,
                )
            logger.info("Workflow %s completed.", wf_id)

        except asyncio.CancelledError:
            logger.info("Workflow %s execution cancelled (shutdown).", wf_id)
        except Exception:
            logger.exception("Unhandled error in workflow %s", wf_id)
        finally:
            self._active.pop(wf_id, None)

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    async def _recover_step(
        self,
        wf: Workflow,
        idx: int,
        step: Step,
        fence: int,
    ) -> Step | None:
        """
        Handle a step found in SUBMITTED or RUNNING state after a crash.

        For async steps in SUBMITTED/RUNNING: the handler may have already
        submitted the external work. Check via verify_completion or
        completeness_check before re-running.

        - If verify_completion is defined, call it to check if the step
          actually completed before the crash.
        - If the step is an async step with a completeness_check, call it
          to see if the submission already went through. If so, transition
          to BLOCKED (don't re-submit).
        - If the step is idempotent, re-run it.
        - Otherwise, mark workflow as NEEDS_REVIEW.
        """
        logger.info(
            "Recovering step %s (status=%s) in workflow %s",
            step.name,
            step.status,
            wf.id,
        )
        self._emit(
            AuditEventType.RECOVERY_STARTED, wf,
            step=step, idx=idx,
        )

        # Check if the step fully completed before the crash
        if step.verify_completion:
            try:
                checker = get_handler(step.verify_completion)
                is_done = await self._call_handler(
                    checker, step.config, _build_results(wf, idx), step.result or StepResult()
                )
                if is_done:
                    logger.info(
                        "Step %s verified as completed after recovery.", step.name
                    )
                    wf = await self._store.complete_step(wf.id, idx, fence)
                    if wf:
                        self._emit(
                            AuditEventType.RECOVERY_VERIFIED, wf,
                            step=wf.steps[idx], idx=idx,
                            recovery_action="verified",
                        )
                    return wf.steps[idx] if wf else None
            except Exception:
                logger.exception("verify_completion failed for step %s", step.name)

        # For async steps: the handler may have submitted work but we crashed
        # before transitioning to BLOCKED. Check if the external work exists.
        if step.is_async and step.completeness_check and step.result:
            try:
                checker = get_handler(step.completeness_check)
                raw = await self._call_handler(checker, step.config, _build_results(wf, idx), step.result)
                # If the check doesn't throw, the submission went through.
                # Transition to BLOCKED so we poll instead of re-submitting.
                is_complete = False
                if isinstance(raw, bool):
                    is_complete = raw
                elif isinstance(raw, dict):
                    is_complete = raw.get("complete", False)
                elif isinstance(raw, PollHint):
                    is_complete = raw.complete
                else:
                    is_complete = bool(raw)
                if is_complete:
                    logger.info("Step %s already complete after recovery.", step.name)
                    wf = await self._store.complete_step(wf.id, idx, fence)
                    if wf:
                        self._emit(
                            AuditEventType.RECOVERY_VERIFIED, wf,
                            step=wf.steps[idx], idx=idx,
                            recovery_action="verified",
                        )
                    return wf.steps[idx] if wf else None
                logger.info(
                    "Step %s submission confirmed, transitioning to BLOCKED.", step.name
                )
                now = datetime.now(UTC)
                policy = step.poll_policy
                wf = await self._store.block_step(
                    wf.id, idx, fence,
                    result=step.result.model_dump(mode="python", serialize_as_any=True) if step.result else {},
                    result_type=step.result_type,
                    poll_started_at=now,
                    next_poll_at=now + timedelta(seconds=policy.interval),
                    current_poll_interval=policy.interval,
                    poll_count=0,
                )
                if wf:
                    self._emit(
                        AuditEventType.RECOVERY_BLOCKED, wf,
                        step=wf.steps[idx], idx=idx,
                        recovery_action="blocked",
                    )
                return wf.steps[idx] if wf else None
            except Exception:
                logger.warning(
                    "completeness_check threw for step %s during recovery — "
                    "submission may not have gone through",
                    step.name,
                    exc_info=True,
                )

        if step.idempotent:
            logger.info("Re-running idempotent step %s", step.name)
            wf = await self._store.reset_step(wf.id, idx, fence)
            if wf is None:
                logger.warning("Fence rejected during idempotent reset for step %s", step.name)
                return None
            if wf:
                self._emit(
                    AuditEventType.RECOVERY_RESET, wf,
                    step=wf.steps[idx], idx=idx,
                    recovery_action="reset",
                )
            return wf.steps[idx] if wf else None

        # Non-idempotent, no verify hook — can't safely re-run
        logger.warning(
            "Step %s is non-idempotent with no verify hook. Marking NEEDS_REVIEW.",
            step.name,
        )
        wf_updated = await self._store.advance_step(
            wf.id,
            fence,
            idx,
            workflow_status=WorkflowStatus.NEEDS_REVIEW,
        )
        if wf_updated is None:
            logger.warning("Fence rejected marking NEEDS_REVIEW for step %s", step.name)
            self._active.pop(wf.id, None)
            return None
        if wf_updated:
            self._emit(
                AuditEventType.RECOVERY_NEEDS_REVIEW, wf_updated,
                step=step, idx=idx,
                recovery_action="needs_review",
            )
        self._active.pop(wf.id, None)
        return None

    # ------------------------------------------------------------------
    # Async step polling — claim, poll once, release
    # ------------------------------------------------------------------

    async def _poll_once(
        self,
        wf: Workflow,
        idx: int,
        step: Step,
        fence: int,
    ) -> str:
        """
        Execute a single poll cycle for a BLOCKED async step.

        Returns:
          "complete"   — step finished, status set to COMPLETED
          "released"   — not done, next_poll_at scheduled, lock released
          "failed"     — timeout/max_polls exceeded, step marked FAILED
          "lost_lock"  — fence rejected, another instance took over
        """
        if not step.completeness_check:
            return "complete"

        checker = get_handler(step.completeness_check)
        step_result = step.result or StepResult()
        policy = step.poll_policy
        now = datetime.now(UTC)

        # --- Check timeout ---
        poll_started_at = step.poll_started_at or now
        if poll_started_at.tzinfo is None:
            poll_started_at = poll_started_at.replace(tzinfo=UTC)
        if policy.timeout > 0:
            elapsed = (now - poll_started_at).total_seconds()
            if elapsed >= policy.timeout:
                logger.error(
                    "Step %s poll timeout after %.1fs (limit=%.1fs)",
                    step.name,
                    elapsed,
                    policy.timeout,
                )
                fail_result = StepResult(
                    error=f"Poll timeout after {elapsed:.1f}s",
                    completed_at=now,
                )
                wf = await self._store.fail_step(
                    wf.id, idx, fence,
                    result=fail_result.model_dump(mode="python", serialize_as_any=True),
                )
                if wf:
                    self._emit(
                        AuditEventType.POLL_TIMEOUT, wf,
                        step=wf.steps[idx], idx=idx,
                        poll_elapsed_seconds=elapsed,
                        error=fail_result.error,
                    )
                    await self._store.advance_step(
                        wf.id,
                        fence,
                        idx,
                        workflow_status=WorkflowStatus.FAILED,
                    )
                return "failed"

        # --- Check max polls ---
        if policy.max_polls > 0 and step.poll_count >= policy.max_polls:
            logger.error("Step %s exceeded max polls (%d)", step.name, policy.max_polls)
            fail_result = StepResult(
                error=f"Exceeded max poll count ({policy.max_polls})",
                completed_at=now,
            )
            wf = await self._store.fail_step(
                wf.id, idx, fence,
                result=fail_result.model_dump(mode="python", serialize_as_any=True),
            )
            if wf:
                self._emit(
                    AuditEventType.POLL_MAX_EXCEEDED, wf,
                    step=wf.steps[idx], idx=idx,
                    poll_count=step.poll_count,
                    error=fail_result.error,
                )
                await self._store.advance_step(
                    wf.id,
                    fence,
                    idx,
                    workflow_status=WorkflowStatus.FAILED,
                )
            return "failed"

        # --- Execute completeness check (with retries) ---
        check_meta = getattr(checker, "_step_meta", {})
        check_retry_policy = check_meta.get("retry", RetryPolicy())

        hint: PollHint | None = None
        is_complete = False
        try:
            retrying = retrying_from_policy(check_retry_policy)
            async for attempt in retrying:
                with attempt:
                    raw = await self._call_handler(
                        checker, step.config, _build_results(wf, idx), step_result
                    )
        except Exception:
            # All retries exhausted — fail the step (terminal)
            logger.exception(
                "completeness_check failed after %d attempts for step %s",
                check_retry_policy.max_attempts,
                step.name,
            )
            wf_id = wf.id
            fail_result = StepResult(
                error=traceback.format_exc(),
                completed_at=now,
            )
            wf = await self._store.fail_step(
                wf_id, idx, fence,
                result=fail_result.model_dump(mode="python", serialize_as_any=True),
            )
            if wf:
                self._emit(
                    AuditEventType.POLL_CHECK_ERRORS_EXCEEDED, wf,
                    step=wf.steps[idx], idx=idx,
                    poll_count=step.poll_count,
                    error=fail_result.error,
                )
                await self._store.advance_step(
                    wf_id,
                    fence,
                    idx,
                    workflow_status=WorkflowStatus.FAILED,
                )
            self._active.pop(wf_id, None)
            return "failed"

        # --- Parse check result (outside retry block to avoid masking) ---
        if isinstance(raw, bool):
            is_complete = raw
        elif isinstance(raw, dict):
            hint = PollHint.model_validate(raw)
            is_complete = hint.complete
        elif isinstance(raw, PollHint):
            hint = raw
            is_complete = hint.complete
        else:
            is_complete = bool(raw)

        # --- Persist poll state ---
        current_interval = step.current_poll_interval or policy.interval
        new_poll_count = step.poll_count + 1
        poll_progress = hint.progress if hint else None
        poll_message = hint.message if hint else None

        # --- Complete: mark step done, keep lock for advance ---
        if is_complete:
            completed_result = step_result.model_copy(update={"completed_at": now})
            wf = await self._store.complete_step(
                wf.id, idx, fence,
                result=completed_result.model_dump(mode="python", serialize_as_any=True),
                poll_count=new_poll_count,
                last_poll_at=now,
                last_poll_progress=poll_progress,
                last_poll_message=poll_message,
            )
            if wf is None:
                return "lost_lock"

            self._emit(
                AuditEventType.POLL_CHECKED, wf,
                step=wf.steps[idx], idx=idx,
                poll_count=new_poll_count,
                poll_progress=poll_progress,
                poll_message=poll_message,
            )
            logger.info(
                "Step %s completeness check passed (polls=%d)",
                step.name,
                new_poll_count,
            )
            return "complete"

        # --- Not complete: schedule next poll, release lock ---

        # Determine next interval
        if hint and hint.retry_after is not None:
            next_wait = hint.retry_after
            # Don't update current_poll_interval — retry_after is a one-shot override
        else:
            next_wait = current_interval
            # Apply backoff for future polls
            current_interval = min(
                current_interval * policy.backoff_multiplier,
                policy.max_interval,
            )

        next_poll_at = now + timedelta(seconds=next_wait)
        wf = await self._store.schedule_next_poll(
            wf.id, idx, fence,
            poll_count=new_poll_count,
            last_poll_at=now,
            next_poll_at=next_poll_at,
            current_poll_interval=current_interval,
            last_poll_progress=poll_progress,
            last_poll_message=poll_message,
        )
        if wf is None:
            return "lost_lock"

        self._emit(
            AuditEventType.POLL_CHECKED, wf,
            step=wf.steps[idx], idx=idx,
            poll_count=step.poll_count + 1,
            poll_progress=hint.progress if hint else None,
            poll_message=hint.message if hint else None,
            next_poll_at=next_poll_at,
            current_poll_interval=current_interval,
        )

        # Remove from active BEFORE releasing lock to prevent heartbeat race
        self._active.pop(wf.id, None)

        # Release lock — fast sweep will reclaim when next_poll_at passes
        await self._store.release_lock(wf.id, self._instance_id, fence)
        self._emit(AuditEventType.LOCK_RELEASED, wf, lock_released=True)

        logger.debug(
            "Step %s poll %d: not complete, next poll in %.1fs%s. Lock released.",
            step.name,
            step.poll_count + 1,
            next_wait,
            f" (progress={hint.progress:.0%})"
            if hint and hint.progress is not None
            else "",
        )
        return "released"

    # ------------------------------------------------------------------
    # Step execution with per-attempt persistence
    # ------------------------------------------------------------------

    async def _run_step_with_retry(
        self,
        handler: Callable[..., Any],
        step: Step,
        wf_id: str,
        idx: int,
        fence: int,
    ) -> Any:
        """
        Execute a step handler with retries, persisting each attempt to
        MongoDB so that retry progress is observable in the database.

        Raises the final exception if all attempts are exhausted.
        """
        retrying = retrying_from_policy(step.retry_policy)
        attempt_num = 0

        async for attempt in retrying:
            with attempt:
                attempt_num += 1

                # Persist attempt number before execution
                wf = await self._store.mark_step_running(
                    wf_id, idx, fence, attempt=attempt_num,
                )
                if wf is None:
                    raise FenceRejectedError(
                        f"Fence rejected during retry (attempt {attempt_num})"
                    )

                self._emit(
                    AuditEventType.STEP_RUNNING, wf,
                    step=wf.steps[idx], idx=idx,
                    step_status_before=StepStatus.SUBMITTED.value,
                    attempt=attempt_num,
                    max_attempts=step.retry_policy.max_attempts,
                )

                logger.info(
                    "Executing step %s attempt %d/%d",
                    step.name,
                    attempt_num,
                    step.retry_policy.max_attempts,
                )
                coro = self._call_handler(handler, step.config, _build_results(wf, idx))
                if step.step_timeout > 0:
                    try:
                        return await asyncio.wait_for(coro, timeout=step.step_timeout)
                    except TimeoutError:
                        self._emit(
                            AuditEventType.STEP_TIMEOUT, wf,
                            step=wf.steps[idx], idx=idx,
                            attempt=attempt_num,
                            max_attempts=step.retry_policy.max_attempts,
                            error=f"Step timed out after {step.step_timeout} seconds",
                        )
                        raise TimeoutError(  # noqa: B904
                            f"Step timed out after {step.step_timeout} seconds"
                        )
                return await coro

        # Unreachable with reraise=True, but satisfies type checker / RET503
        raise RetryExhaustedError(f"Step {step.name} exhausted all retry attempts")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _advance(self, wf: Workflow, fence: int) -> Workflow | None:
        """Advance to the next step index. Terminal status is set by the outer loop."""
        new_idx = wf.current_step_index + 1
        return await self._store.advance_step(wf.id, fence, new_idx)

    async def _wait(self, seconds: float) -> None:
        """Sleep that can be interrupted by shutdown."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                self._shutdown_event.wait(),
                timeout=seconds,
            )
