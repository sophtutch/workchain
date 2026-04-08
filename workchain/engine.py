"""
Core workflow engine: claims individual ready steps, executes them
with retries, handles async polling, heartbeats, crash recovery,
and graceful shutdown.

Steps are claimed independently — multiple engine instances can work
on different steps of the same workflow concurrently.
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
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, Self

if TYPE_CHECKING:
    import types
    from collections.abc import Callable

from workchain.audit import AuditEventType
from workchain.decorators import _normalize_check_result, get_handler
from workchain.exceptions import FenceRejectedError, HandlerError, RetryExhaustedError
from workchain.models import (
    PollPolicy,
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


class _ActiveStep(NamedTuple):
    """Tracks an in-flight step: the asyncio task and its fence token."""

    task: asyncio.Task
    fence: int


def _build_results(wf: Workflow, step_name: str) -> dict[str, StepResult]:
    """Build a dict of dependency step results for the given step."""
    step = wf.step_by_name(step_name)
    if step is None:
        return {}
    deps = step.depends_on or []
    return {
        dep_name: dep.result
        for dep_name in deps
        if (dep := wf.step_by_name(dep_name)) is not None
        and dep.result is not None
    }


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

    Claims and executes individual ready steps. Multiple engine instances
    can work on different steps of the same workflow concurrently.

    Usage (context manager — recommended):
        async with WorkflowEngine(store) as engine:
            ...  # engine runs claim loop, heartbeat, sweep

    Usage (manual):
        engine = WorkflowEngine(store)
        await engine.start()
        ...
        await engine.stop()
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
        self._log_heartbeats = log_heartbeats
        self._context: dict[str, Any] = context or {}

        # Active steps this instance is processing: (wf_id, step_name) -> (task, fence)
        self._active: dict[tuple[str, str], _ActiveStep] = {}
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
    # Engine-side audit helper
    # ------------------------------------------------------------------

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
        """Graceful shutdown: release all step locks, cancel background tasks."""
        logger.info("Shutting down instance=%s ...", self._instance_id)
        self._shutdown_event.set()

        # Snapshot active steps before cancelling — _run_step's finally
        # block pops from _active, so we need the snapshot for lock release.
        active_snapshot = list(self._active.items())

        # Cancel all active step tasks and await their cleanup
        for (_wf_id, _step_name), active in active_snapshot:
            active.task.cancel()
        if active_snapshot:
            await asyncio.gather(
                *(a.task for _, a in active_snapshot),
                return_exceptions=True,
            )

        # Release all step locks so peers can pick up immediately
        for (wf_id, step_name), active in active_snapshot:
            released = await self._store.release_step_lock(
                wf_id, step_name, self._instance_id, active.fence
            )
            logger.info(
                "Released step lock workflow=%s step=%s success=%s",
                wf_id, step_name, released,
            )

        self._active.clear()

        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

        # Drain pending audit writes via the store
        await self._store.drain_audit_tasks()
        logger.info("Shutdown complete.")

    async def __aenter__(self) -> Self:
        """Start the engine for use as an async context manager."""
        await self.start()
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: types.TracebackType | None) -> None:
        """Stop the engine on context exit, ensuring locks are released."""
        await self.stop()

    # ------------------------------------------------------------------
    # Claim loop — discovers and claims ready steps
    # ------------------------------------------------------------------

    async def _claim_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                slots = self._max_concurrent - len(self._active)
                if slots > 0:
                    claimable = await self._store.find_claimable_steps(limit=slots)
                    for wf_id, step_name in claimable:
                        key = (wf_id, step_name)
                        if key in self._active:
                            continue
                        result = await self._store.try_claim_step(
                            wf_id, step_name, self._instance_id
                        )
                        if result is not None:
                            wf, step_fence = result
                            task = asyncio.create_task(
                                self._run_step(wf_id, step_name, step_fence),
                                name=f"step-{wf_id[:8]}-{step_name}",
                            )
                            self._active[key] = _ActiveStep(task, step_fence)
            except Exception:
                logger.exception("Error in claim loop")

            await self._wait(self._claim_interval)

    # ------------------------------------------------------------------
    # Heartbeat loop — keeps step locks alive while processing
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        while not self._shutdown_event.is_set():
            for (wf_id, step_name), active in list(self._active.items()):
                try:
                    ok = await self._store.heartbeat_step(
                        wf_id, step_name, self._instance_id, active.fence,
                        emit_audit=self._log_heartbeats,
                    )
                    if not ok:
                        logger.warning(
                            "Lost lock on step=%s workflow=%s, cancelling",
                            step_name, wf_id,
                        )
                        active.task.cancel()
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(active.task), timeout=5.0,
                            )
                        except (TimeoutError, asyncio.CancelledError, Exception):
                            pass
                        # _run_step's finally block pops from _active;
                        # remove here as a safety net in case it didn't.
                        self._active.pop((wf_id, step_name), None)
                except Exception:
                    logger.exception(
                        "Heartbeat error for step=%s workflow=%s", step_name, wf_id,
                    )

            await self._wait(self._heartbeat_interval)

    # ------------------------------------------------------------------
    # Slow sweep — catches anomalies the fast claim loop misses
    # ------------------------------------------------------------------

    def _is_workflow_active(self, wf_id: str) -> bool:
        """Check if any step of this workflow is being processed by this instance."""
        return any(k[0] == wf_id for k in self._active)

    async def _sweep_loop(self) -> None:
        """
        Runs on a longer interval than the claim loop. Detects steps and
        workflows in inconsistent states and resolves them so the fast
        claim loop can reclaim them.

        Anomalies handled:
        - **step_stuck_in_transient_state** / **stale_step_lock**: force-release
          the step lock so it can be reclaimed and recovered.
        - **orphaned_workflow**: all steps are terminal but the workflow is
          still RUNNING — attempt to finalise via try_complete/try_fail.
        """
        while not self._shutdown_event.is_set():
            try:
                anomalies = await self._store.find_anomalies(
                    step_stuck_seconds=self._step_stuck_seconds,
                )
                resolved = 0
                for entry in anomalies:
                    wf_id = entry["workflow_id"]
                    step_name: str | None = entry.get("step_name")
                    anomaly = entry["anomaly"]

                    # Don't touch steps this instance is actively processing
                    if step_name and (wf_id, step_name) in self._active:
                        continue
                    if not step_name and self._is_workflow_active(wf_id):
                        continue

                    sweep_wf = await self._store.get(wf_id)
                    if sweep_wf is None:
                        continue

                    if anomaly == "orphaned_workflow":
                        if await self._resolve_orphaned_workflow(wf_id, sweep_wf, anomaly):
                            resolved += 1
                    elif step_name:
                        if await self._resolve_step_anomaly(wf_id, step_name, sweep_wf, anomaly):
                            resolved += 1

                if resolved:
                    logger.info("Sweep resolved %d anomalies", resolved)

            except Exception:
                logger.exception("Error in sweep loop")

            await self._wait(self._sweep_interval)

    async def _resolve_orphaned_workflow(
        self, wf_id: str, sweep_wf: Workflow, anomaly: str,
    ) -> bool:
        """Resolve an orphaned workflow where all steps are terminal but status is RUNNING."""
        # Re-validate: all steps must still be terminal
        if any(
            s.status not in (StepStatus.COMPLETED, StepStatus.FAILED)
            for s in sweep_wf.steps
        ):
            return False

        if sweep_wf.has_failed_step():
            result = await self._store.try_fail_workflow(wf_id)
        else:
            result = await self._store.try_complete_workflow(wf_id)
        if result is None:
            return False  # concurrent resolution by another instance
        logger.warning("Sweep resolved orphaned_workflow=%s", wf_id)
        self._store.emit_sweep_anomaly(sweep_wf, anomaly)
        return True

    async def _resolve_step_anomaly(
        self, wf_id: str, step_name: str, sweep_wf: Workflow, anomaly: str,  # noqa: ARG002
    ) -> bool:
        """Force-release a step lock for a stuck or stale-locked step."""
        logger.warning(
            "Sweep detected anomaly=%s on workflow=%s step=%s, force-releasing step lock",
            anomaly, wf_id, step_name,
        )
        return await self._store.force_release_step_lock(
            wf_id, step_name, anomaly_type=anomaly,
        )

    # ------------------------------------------------------------------
    # Step execution — single step claim-to-completion
    # ------------------------------------------------------------------

    async def _run_step(self, wf_id: str, step_name: str, step_fence: int) -> None:
        """
        Execute a single step end-to-end.

        For sync steps: submit → run handler → complete → try_complete_workflow.
        For async steps: submit → run handler → block → release lock.
          The claim loop rediscovers the step when next_poll_at passes.
        For poll cycles: run completeness_check → complete or reschedule.
        """
        key = (wf_id, step_name)
        try:
            wf = await self._store.get(wf_id)
            if wf is None or wf.status in (
                WorkflowStatus.CANCELLED,
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
                WorkflowStatus.NEEDS_REVIEW,
            ):
                if wf and wf.status == WorkflowStatus.CANCELLED:
                    logger.info("Workflow %s cancelled.", wf_id)
                await self._release_step_lock_safe(wf_id, step_name, step_fence)
                return

            step = wf.step_by_name(step_name)
            if step is None:
                await self._release_step_lock_safe(wf_id, step_name, step_fence)
                return

            if self._shutdown_event.is_set():
                return  # stop() handles lock release

            # --- Recovery for SUBMITTED/RUNNING (crash during handler) ---
            if step.status in (StepStatus.SUBMITTED, StepStatus.RUNNING):
                step = await self._recover_step(wf, step_name, step, step_fence)
                if step is None:
                    return  # lock lost or needs_review

            # --- BLOCKED: execute a single poll cycle ---
            if step.status == StepStatus.BLOCKED:
                poll_result = await self._poll_once(wf, step_name, step, step_fence)
                if poll_result == "complete":
                    # Refresh to get completed step state
                    wf = await self._store.get(wf_id)
                    if wf is None:
                        return
                    step = wf.step_by_name(step_name)
                    # Fall through to completion handling below
                elif poll_result == "released":
                    return  # Lock released, next poll scheduled
                else:
                    return  # "failed" or "lost_lock"

            if step.status == StepStatus.COMPLETED:
                # After completing a step, try to mark workflow done
                await self._store.try_complete_workflow(wf_id)
                return

            # --- Normal execution: PENDING step ---
            wf = await self._store.submit_step_by_name(
                wf_id, step_name, step_fence, attempt=step.attempt + 1,
            )
            if wf is None:
                return  # fence rejected

            step = wf.step_by_name(step_name)

            # Execute the step handler with retries
            try:
                handler = get_handler(step.handler)
                result_data = await self._run_step_with_retry(
                    handler, step, wf_id, step_name, step_fence,
                )
                result, result_type = _wrap_handler_return(result_data)
                # Refresh wf — check if a sibling step failed the workflow
                # while this handler was running
                wf = await self._store.get(wf_id)
                if wf is None:
                    return
                if wf.status in (
                    WorkflowStatus.CANCELLED,
                    WorkflowStatus.FAILED,
                    WorkflowStatus.NEEDS_REVIEW,
                ):
                    logger.info(
                        "Workflow %s is %s, discarding step %s result.",
                        wf_id, wf.status.value, step_name,
                    )
                    await self._release_step_lock_safe(wf_id, step_name, step_fence)
                    return
                step = wf.step_by_name(step_name)
            except Exception:
                logger.exception(
                    "Step %s failed after %d attempts", step.name, step.attempt
                )
                wf = await self._store.get(wf_id)
                if wf is None:
                    return
                fail_result = StepResult(
                    error=traceback.format_exc(),
                    completed_at=datetime.now(UTC),
                )
                await self._store.fail_step_by_name(
                    wf_id, step_name, step_fence, result=fail_result,
                )
                await self._store.try_fail_workflow(wf_id)
                return

            # --- Async step: persist submission result, release lock ---
            if step.is_async and step.completeness_check:
                now = datetime.now(UTC)
                policy = step.poll_policy or PollPolicy()
                next_poll = now + timedelta(seconds=policy.interval)

                wf = await self._store.block_step_by_name(
                    wf_id, step_name, step_fence,
                    result=result,
                    result_type=result_type,
                    poll_started_at=now,
                    next_poll_at=next_poll,
                    current_poll_interval=policy.interval,
                )
                if wf is None:
                    return

                await self._release_and_emit_lock(wf, wf_id, step_name, step_fence, key)
                logger.info(
                    "Step %s submitted, lock released. Next poll at %s",
                    step_name,
                    next_poll.isoformat(),
                )
                return  # exit cleanly, claim loop will rediscover when due

            # --- Sync step: mark completed ---
            wf = await self._store.complete_step_by_name(
                wf_id, step_name, step_fence,
                result=result,
                result_type=result_type,
            )
            if wf is None:
                return

            # Check if workflow is done
            await self._store.try_complete_workflow(wf_id)

        except asyncio.CancelledError:
            logger.info("Step %s/%s execution cancelled (shutdown).", wf_id, step_name)
        except Exception:
            logger.exception("Unhandled error in step %s/%s", wf_id, step_name)
            await self._release_step_lock_safe(wf_id, step_name, step_fence)
        finally:
            self._active.pop(key, None)

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    async def _recover_step(
        self,
        wf: Workflow,
        step_name: str,
        step: Step,
        step_fence: int,
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
        idx = next((i for i, s in enumerate(wf.steps) if s.name == step_name), None)
        self._store.emit_recovery_started(wf, step, idx, step_fence)

        # Check if the step fully completed before the crash
        if step.verify_completion:
            try:
                checker = get_handler(step.verify_completion)
                raw = await self._call_handler(
                    checker, step.config, _build_results(wf, step_name), step.result or StepResult()
                )
                check_result = _normalize_check_result(raw)
                if check_result.complete:
                    logger.info(
                        "Step %s verified as completed after recovery.", step.name
                    )
                    wf = await self._store.complete_step_by_name(
                        wf.id, step_name, step_fence,
                        audit_event_type=AuditEventType.RECOVERY_VERIFIED,
                        recovery_action="verified",
                    )
                    if wf is None:
                        return None
                    return wf.step_by_name(step_name)
            except Exception:
                logger.exception("verify_completion failed for step %s", step.name)

        # For async steps: the handler may have submitted work but we crashed
        # before transitioning to BLOCKED. Check if the external work exists.
        if step.is_async and step.completeness_check and step.result:
            try:
                checker = get_handler(step.completeness_check)
                check_result = await self._call_handler(
                    checker, step.config, _build_results(wf, step_name), step.result,
                )
                is_complete = check_result.complete
                if is_complete:
                    logger.info("Step %s already complete after recovery.", step.name)
                    wf = await self._store.complete_step_by_name(
                        wf.id, step_name, step_fence,
                        audit_event_type=AuditEventType.RECOVERY_VERIFIED,
                        recovery_action="verified",
                    )
                    if wf is None:
                        return None
                    return wf.step_by_name(step_name)
                logger.info(
                    "Step %s submission confirmed, transitioning to BLOCKED.", step.name
                )
                now = datetime.now(UTC)
                policy = step.poll_policy or PollPolicy()
                wf = await self._store.block_step_by_name(
                    wf.id, step_name, step_fence,
                    result=step.result or StepResult(),
                    result_type=step.result_type,
                    poll_started_at=now,
                    next_poll_at=now + timedelta(seconds=policy.interval),
                    current_poll_interval=policy.interval,
                    poll_count=0,
                    audit_event_type=AuditEventType.RECOVERY_BLOCKED,
                    recovery_action="blocked",
                )
                if wf is None:
                    return None
                return wf.step_by_name(step_name)
            except Exception:
                logger.warning(
                    "completeness_check threw for step %s during recovery — "
                    "submission may not have gone through",
                    step.name,
                    exc_info=True,
                )

        if step.idempotent:
            logger.info("Re-running idempotent step %s", step.name)
            wf = await self._store.reset_step_by_name(wf.id, step_name, step_fence)
            if wf is None:
                logger.warning("Fence rejected during idempotent reset for step %s", step.name)
                return None
            return wf.step_by_name(step_name)

        # Non-idempotent, no verify hook — can't safely re-run
        logger.warning(
            "Step %s is non-idempotent with no verify hook. Marking NEEDS_REVIEW.",
            step.name,
        )
        wf_updated = await self._store.try_needs_review_workflow(wf.id)
        if wf_updated is None:
            logger.warning("Failed to mark NEEDS_REVIEW for step %s", step.name)
        return None

    # ------------------------------------------------------------------
    # Async step polling — claim, poll once, release
    # ------------------------------------------------------------------

    async def _poll_once(
        self,
        wf: Workflow,
        step_name: str,
        step: Step,
        step_fence: int,
    ) -> Literal["complete", "released", "failed", "lost_lock"]:
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
        policy = step.poll_policy or PollPolicy()
        now = datetime.now(UTC)
        wf_id = wf.id
        key = (wf_id, step_name)
        step_idx = next((i for i, s in enumerate(wf.steps) if s.name == step_name), None)

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
                return await self._fail_poll_step(
                    wf_id, step_name, step_fence, step_idx,
                    error_msg=f"Poll timeout after {elapsed:.1f}s",
                    event_type=AuditEventType.POLL_TIMEOUT,
                    poll_elapsed_seconds=elapsed,
                )

        # --- Check max polls ---
        if policy.max_polls > 0 and step.poll_count >= policy.max_polls:
            logger.error("Step %s exceeded max polls (%d)", step.name, policy.max_polls)
            return await self._fail_poll_step(
                wf_id, step_name, step_fence, step_idx,
                error_msg=f"Exceeded max poll count ({policy.max_polls})",
                event_type=AuditEventType.POLL_MAX_EXCEEDED,
                poll_count=step.poll_count,
            )

        # --- Execute completeness check (with retries) ---
        check_meta = getattr(checker, "_step_meta", {})
        check_retry_policy = check_meta.get("retry", RetryPolicy())

        try:
            retrying = retrying_from_policy(check_retry_policy)
            async for attempt in retrying:
                with attempt:
                    check_result = await self._call_handler(
                        checker, step.config, _build_results(wf, step_name), step_result
                    )
        except Exception:
            # All retries exhausted — fail the step (terminal)
            logger.exception(
                "completeness_check failed after %d attempts for step %s",
                check_retry_policy.max_attempts,
                step.name,
            )
            return await self._fail_poll_step(
                wf_id, step_name, step_fence, step_idx,
                error_msg=traceback.format_exc(),
                event_type=AuditEventType.POLL_CHECK_ERRORS_EXCEEDED,
                poll_count=step.poll_count,
            )

        # --- Parse check result (outside retry block to avoid masking) ---
        is_complete = check_result.complete

        # --- Persist poll state ---
        current_interval = step.current_poll_interval or policy.interval
        new_poll_count = step.poll_count + 1
        poll_progress = check_result.progress
        poll_message = check_result.message

        # --- Complete: mark step done ---
        if is_complete:
            completed_result = step_result.model_copy(update={"completed_at": now})
            wf = await self._store.complete_step_by_name(
                wf_id, step_name, step_fence,
                result=completed_result,
                poll_count=new_poll_count,
                last_poll_at=now,
                last_poll_progress=poll_progress,
                last_poll_message=poll_message,
                step_status_before=StepStatus.BLOCKED.value,
            )
            if wf is None:
                return "lost_lock"
            self._store.emit_poll_checked(
                wf, wf.step_by_name(step_name), step_idx, step_fence,
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
        if check_result.retry_after is not None:
            next_wait = check_result.retry_after
        else:
            next_wait = current_interval
            current_interval = min(
                current_interval * policy.backoff_multiplier,
                policy.max_interval,
            )

        next_poll_at = now + timedelta(seconds=next_wait)
        wf = await self._store.schedule_next_poll_by_name(
            wf_id, step_name, step_fence,
            poll_count=new_poll_count,
            last_poll_at=now,
            next_poll_at=next_poll_at,
            current_poll_interval=current_interval,
            last_poll_progress=poll_progress,
            last_poll_message=poll_message,
        )
        if wf is None:
            return "lost_lock"

        # Release lock — claim loop will rediscover when next_poll_at passes
        await self._release_and_emit_lock(wf, wf_id, step_name, step_fence, key)

        logger.debug(
            "Step %s poll %d: not complete, next poll in %.1fs%s. Lock released.",
            step.name,
            step.poll_count + 1,
            next_wait,
            f" (progress={check_result.progress:.0%})"
            if check_result.progress is not None
            else "",
        )
        return "released"

    # ------------------------------------------------------------------
    # Shared poll / lock helpers
    # ------------------------------------------------------------------

    async def _fail_poll_step(
        self,
        wf_id: str,
        step_name: str,
        step_fence: int,
        step_idx: int | None,  # noqa: ARG002
        error_msg: str,
        event_type: AuditEventType,
        **event_kwargs: Any,
    ) -> Literal["failed"]:
        """Fail a BLOCKED step during polling and emit a diagnostic event.

        Used by poll timeout, max-polls-exceeded, and check-errors-exceeded
        paths, which differ only in error message and event type.
        """
        fail_result = StepResult(
            error=error_msg,
            completed_at=datetime.now(UTC),
        )
        wf = await self._store.fail_step_by_name(
            wf_id, step_name, step_fence,
            result=fail_result,
            step_status_before=StepStatus.BLOCKED.value,
            **{k: v for k, v in event_kwargs.items()
               if k in ("poll_elapsed_seconds", "poll_count")},
        )
        if wf:
            step = wf.step_by_name(step_name)
            idx = next((i for i, s in enumerate(wf.steps) if s.name == step_name), None)
            self._store.emit_poll_failure(
                wf, step, idx, step_fence, event_type,
                error=error_msg,
                **{k: v for k, v in event_kwargs.items()
                   if k in ("poll_elapsed_seconds", "poll_count")},
            )
            await self._store.try_fail_workflow(wf_id)
        return "failed"

    async def _release_and_emit_lock(
        self,
        wf: Workflow,  # noqa: ARG002
        wf_id: str,
        step_name: str,
        step_fence: int,
        key: tuple[str, str],
    ) -> None:
        """Pop step from active tracking and release lock.

        Must be called after removing the step from _active to prevent
        the heartbeat loop from heartbeating a released step.
        The store emits LOCK_RELEASED as part of release_step_lock().
        """
        self._active.pop(key, None)
        await self._store.release_step_lock(
            wf_id, step_name, self._instance_id, step_fence,
        )

    # ------------------------------------------------------------------
    # Step execution with per-attempt persistence
    # ------------------------------------------------------------------

    async def _run_step_with_retry(
        self,
        handler: Callable[..., Any],
        step: Step,
        wf_id: str,
        step_name: str,
        step_fence: int,
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
                wf = await self._store.mark_step_running_by_name(
                    wf_id, step_name, step_fence, attempt=attempt_num,
                    max_attempts=step.retry_policy.max_attempts,
                )
                if wf is None:
                    raise FenceRejectedError(
                        f"Fence rejected during retry (attempt {attempt_num})"
                    )

                logger.info(
                    "Executing step %s attempt %d/%d",
                    step.name,
                    attempt_num,
                    step.retry_policy.max_attempts,
                )
                coro = self._call_handler(handler, step.config, _build_results(wf, step_name))
                if step.step_timeout > 0:
                    try:
                        return await asyncio.wait_for(coro, timeout=step.step_timeout)
                    except TimeoutError:
                        step_obj = wf.step_by_name(step_name)
                        idx = next(
                            (i for i, s in enumerate(wf.steps) if s.name == step_name),
                            None,
                        )
                        self._store.emit_step_timeout(
                            wf, step_obj, idx, step_fence,
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
    # Lock helpers
    # ------------------------------------------------------------------

    async def _release_step_lock_safe(
        self, wf_id: str, step_name: str, step_fence: int,
    ) -> None:
        """Release a step lock, suppressing errors (best-effort cleanup)."""
        with contextlib.suppress(Exception):
            await self._store.release_step_lock(
                wf_id, step_name, self._instance_id, step_fence,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _wait(self, seconds: float) -> None:
        """Sleep that can be interrupted by shutdown."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                self._shutdown_event.wait(),
                timeout=seconds,
            )
