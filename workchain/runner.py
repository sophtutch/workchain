"""WorkflowRunner — execution engine for workchain."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from workchain.context import Context
from workchain.exceptions import (
    ConcurrentModificationError,
    StepNotFoundError,
    WorkflowRunNotFoundError,
)
from workchain.models import DependencyFailurePolicy, StepRun, StepStatus, WorkflowRun, WorkflowStatus
from workchain.steps import EventStep, PollingStep, Step, StepOutcome, StepResult
from workchain.store.base import WorkflowStore
from workchain.workflow import Workflow

logger = logging.getLogger(__name__)

# Type alias for the step registry
StepRegistry = dict[str, type[Step]]


class WorkflowRunner:
    """
    Execution engine for workchain workflows.

    Responsibilities:
    - Polling the store for claimable WorkflowRuns
    - Resolving the DAG to find ready steps
    - Executing steps and persisting state after each one
    - Managing distributed leases (acquisition, heartbeat, release)
    - Handling EventStep suspension and resumption
    - Handling PollingStep scheduling and re-checks

    Usage::

        registry = {"FetchStep": FetchStep, "ApprovalStep": ApprovalStep}
        store = MongoWorkflowStore(
            client=client, database="app"
        )  # owner_id defaults to hostname

        runner = WorkflowRunner(store=store, registry=registry)

        # Start a new run
        run = workflow.create_run()
        store.save(run)

        # Run the loop (blocking)
        await runner.start()

        # Or process a single tick
        await runner.tick()
    """

    def __init__(
        self,
        store: WorkflowStore,
        registry: StepRegistry,
        workflow: Workflow | None = None,
        instance_id: str | None = None,
        lease_ttl_seconds: int = 30,
        poll_interval_seconds: float = 5.0,
    ) -> None:
        self.store = store
        self.registry = registry
        self.workflow = workflow
        import platform

        self.instance_id = instance_id or platform.node()
        self.lease_ttl = lease_ttl_seconds
        self.poll_interval = poll_interval_seconds
        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Start the runner loop (blocking). Processes runs until stop() is called.
        Typically run as an asyncio task.
        """
        self._running = True
        logger.info("WorkflowRunner[%s] started.", self.instance_id)
        while self._running:
            try:
                await self.tick()
            except Exception:
                logger.exception("WorkflowRunner[%s] unhandled error in tick.", self.instance_id)
            await asyncio.sleep(self.poll_interval)

    async def stop(self) -> None:
        """Signal the runner loop to stop after the current tick."""
        self._running = False

    async def tick(self) -> bool:
        """
        Attempt to claim and process one WorkflowRun.
        Returns True if a run was processed, False if nothing was available.
        """
        run = await self.store.find_claimable()
        if run is None:
            return False

        logger.info(
            "WorkflowRunner[%s] claimed run %s (%s).",
            self.instance_id,
            run.id,
            run.workflow_name,
        )
        heartbeat = self._start_heartbeat(str(run.id))
        try:
            await self._process_run(run)
        except ConcurrentModificationError:
            logger.warning(
                "WorkflowRunner[%s] concurrent modification on run %s — aborting.",
                self.instance_id,
                run.id,
            )
        except Exception:
            logger.exception(
                "WorkflowRunner[%s] error processing run %s.",
                self.instance_id,
                run.id,
            )
        finally:
            heartbeat.stop()
            await self.store.release_lease(str(run.id), self.instance_id)

        return True

    async def resume(self, correlation_id: str, payload: dict[str, Any]) -> None:
        """
        Resume a suspended EventStep identified by its correlation_id.
        Locates the run, acquires a lease, calls on_resume(), then continues.
        """
        run = await self.store.find_by_correlation_id(correlation_id)
        if run is None:
            raise WorkflowRunNotFoundError(correlation_id)

        step_run = next((s for s in run.steps if s.resume_correlation_id == correlation_id), None)
        if step_run is None or step_run.status != StepStatus.SUSPENDED:
            raise WorkflowRunNotFoundError(f"No suspended step with correlation_id '{correlation_id}'")

        # Re-acquire lease for the resume path
        run = await self.store.acquire_lease_for_resume(run.id, self.instance_id, self.lease_ttl)
        if run is None:
            raise RuntimeError("Could not acquire lease for resume on run. " "It may be held by another runner.")

        heartbeat = self._start_heartbeat(str(run.id))
        try:
            context = Context.from_dict(run.context)
            step_instance = self._get_step_instance(step_run, run)

            assert isinstance(step_instance, EventStep)
            step_instance.on_resume(payload, context)

            step_run = run.get_step(step_run.step_id)
            assert step_run is not None
            self._complete_step(run, step_run, output={}, context=context)
            await self._continue_run(run, context)
        except ConcurrentModificationError:
            logger.warning("Concurrent modification during resume of run %s.", run.id)
        finally:
            heartbeat.stop()
            await self.store.release_lease(str(run.id), self.instance_id)

    # ------------------------------------------------------------------
    # Internal execution logic
    # ------------------------------------------------------------------

    async def _process_run(self, run: WorkflowRun) -> None:
        """Main processing loop for a single WorkflowRun."""
        run.status = WorkflowStatus.RUNNING
        context = Context.from_dict(run.context)

        # Handle any steps waking from AWAITING_POLL
        await self._check_due_polls(run, context)

        await self._continue_run(run, context)

    async def _continue_run(self, run: WorkflowRun, context: Context) -> None:
        """Execute all currently ready steps, then assess overall workflow state."""
        while True:
            ready = self._get_ready_steps(run)
            if not ready:
                break

            for step_run in ready:
                await self._execute_step(run, step_run, context)
                # Persist after every step
                run.context = context.to_dict()
                await self.store.save_with_version(run)

        self._assess_workflow_status(run, context)
        run.context = context.to_dict()
        await self.store.save_with_version(run)

    async def _execute_step(self, run: WorkflowRun, step_run: StepRun, context: Context) -> None:
        """Execute a single step and update its StepRun accordingly."""
        step_run.status = StepStatus.RUNNING
        step_run.started_at = datetime.now(UTC)
        logger.debug("Executing step '%s' (%s).", step_run.step_id, step_run.step_type)

        step_instance = self._get_step_instance(step_run, run)

        try:
            result: StepResult = step_instance.execute(context)
        except Exception as exc:
            logger.exception("Step '%s' raised an exception.", step_run.step_id)
            result = StepResult.fail(error=str(exc))

        await self._apply_result(run, step_run, result, context)

    async def _apply_result(
        self,
        run: WorkflowRun,
        step_run: StepRun,
        result: StepResult,
        context: Context,
    ) -> None:
        now = datetime.now(UTC)

        if result.outcome == StepOutcome.COMPLETED:
            self._complete_step(run, step_run, result.output, context)

        elif result.outcome == StepOutcome.SUSPEND:
            step_run.status = StepStatus.SUSPENDED
            step_run.resume_correlation_id = result.correlation_id
            run.status = WorkflowStatus.SUSPENDED
            logger.info(
                "Step '%s' suspended. correlation_id=%s",
                step_run.step_id,
                result.correlation_id,
            )

        elif result.outcome == StepOutcome.POLL:
            step_run.status = StepStatus.AWAITING_POLL
            step_run.next_poll_at = result.next_poll_at
            if step_run.poll_started_at is None:
                step_run.poll_started_at = now
            run.status = WorkflowStatus.SUSPENDED
            logger.info(
                "Step '%s' scheduled for poll at %s.",
                step_run.step_id,
                result.next_poll_at,
            )

        elif result.outcome == StepOutcome.FAILED:
            step_run.status = StepStatus.FAILED
            step_run.error = result.error
            step_run.completed_at = now
            logger.warning("Step '%s' failed: %s", step_run.step_id, result.error)
            self._propagate_failure(run, step_run.step_id)

    def _complete_step(
        self,
        run: WorkflowRun,
        step_run: StepRun,
        output: dict[str, Any],
        context: Context,
    ) -> None:
        step_run.status = StepStatus.COMPLETED
        step_run.output = output
        step_run.completed_at = datetime.now(UTC)
        context.set_step_output(step_run.step_id, output)
        logger.debug("Step '%s' completed.", step_run.step_id)

    async def _check_due_polls(self, run: WorkflowRun, context: Context) -> None:
        """Re-execute any AWAITING_POLL steps whose next_poll_at has passed."""
        now = datetime.now(UTC)
        for step_run in run.steps:
            if (
                step_run.status == StepStatus.AWAITING_POLL
                and step_run.next_poll_at is not None
                and step_run.next_poll_at <= now
            ):
                await self._execute_poll_check(run, step_run, context)

    async def _execute_poll_check(self, run: WorkflowRun, step_run: StepRun, context: Context) -> None:
        """Invoke check() on a PollingStep and handle the result."""
        step_instance = self._get_step_instance(step_run, run)
        assert isinstance(
            step_instance, PollingStep
        ), f"Step '{step_run.step_id}' is AWAITING_POLL but is not a PollingStep."

        # Timeout check
        if step_instance.timeout_seconds is not None and step_run.poll_started_at:
            elapsed = (datetime.now(UTC) - step_run.poll_started_at).total_seconds()
            if elapsed > step_instance.timeout_seconds:
                step_run.status = StepStatus.FAILED
                step_run.error = "PollingStep timed out."
                step_run.completed_at = datetime.now(UTC)
                self._propagate_failure(run, step_run.step_id)
                return

        try:
            done = step_instance.check(context)
        except Exception as exc:
            step_run.status = StepStatus.FAILED
            step_run.error = str(exc)
            step_run.completed_at = datetime.now(UTC)
            self._propagate_failure(run, step_run.step_id)
            return

        if done:
            output = step_instance.on_complete(context)
            self._complete_step(run, step_run, output, context)
        else:
            step_run.next_poll_at = datetime.now(UTC) + timedelta(seconds=step_instance.poll_interval_seconds)
            logger.debug(
                "Step '%s' poll check returned False. Next at %s.",
                step_run.step_id,
                step_run.next_poll_at,
            )

    # ------------------------------------------------------------------
    # DAG helpers
    # ------------------------------------------------------------------

    def _get_ready_steps(self, run: WorkflowRun) -> list[StepRun]:
        """Return steps whose dependencies are all COMPLETED and are themselves PENDING."""
        completed_ids = {s.step_id for s in run.steps if s.status == StepStatus.COMPLETED}
        return [s for s in run.steps if s.status == StepStatus.PENDING and set(s.depends_on).issubset(completed_ids)]

    def _propagate_failure(self, run: WorkflowRun, failed_step_id: str) -> None:
        """
        Mark dependents of a failed step according to their on_dependency_failure policy.
        Recurses until no more dependents are affected.
        """
        changed = True
        while changed:
            changed = False
            failed_ids = {s.step_id for s in run.steps if s.status in {StepStatus.FAILED, StepStatus.SKIPPED}}
            for step_run in run.steps:
                if step_run.status != StepStatus.PENDING:
                    continue
                if not set(step_run.depends_on) & failed_ids:
                    continue
                if step_run.on_dependency_failure == DependencyFailurePolicy.SKIP:
                    step_run.status = StepStatus.SKIPPED
                else:
                    step_run.status = StepStatus.FAILED
                    step_run.error = f"Dependency failed: {failed_ids & set(step_run.depends_on)}"
                changed = True

    def _assess_workflow_status(self, run: WorkflowRun, context: Context) -> None:
        """Determine and set the overall WorkflowRun status."""
        statuses = {s.status for s in run.steps}

        # If any step is actively in progress, keep RUNNING
        active = {StepStatus.RUNNING, StepStatus.PENDING}
        if statuses & active:
            run.status = WorkflowStatus.RUNNING
            return

        # If any step is suspended/polling, workflow is SUSPENDED
        waiting = {StepStatus.SUSPENDED, StepStatus.AWAITING_POLL}
        if statuses & waiting:
            run.status = WorkflowStatus.SUSPENDED
            return

        # All steps are terminal
        if StepStatus.FAILED in statuses:
            run.status = WorkflowStatus.FAILED
        else:
            run.status = WorkflowStatus.COMPLETED

        logger.info("WorkflowRun %s finished with status: %s", run.id, run.status)

    # ------------------------------------------------------------------
    # Step instantiation
    # ------------------------------------------------------------------

    def _get_step_instance(self, step_run: StepRun, run: WorkflowRun) -> Step:
        """
        Reconstruct a Step instance from the registry and its definition in the Workflow.
        Config is sourced from the Workflow blueprint (not stored on WorkflowRun).
        """
        step_class = self.registry.get(step_run.step_type)
        if step_class is None:
            raise StepNotFoundError(step_run.step_type)

        # If a workflow blueprint is provided, extract config from it
        if self.workflow is not None:
            step_def = self.workflow.get_step_definition(step_run.step_id)
            if step_def is not None:
                return step_def.step  # return the pre-configured instance

        # Fallback: instantiate without config (caller must ensure config is not needed)
        return step_class()

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def _start_heartbeat(self, run_id: str) -> _AsyncHeartbeat:
        hb = _AsyncHeartbeat(
            store=self.store,
            run_id=run_id,
            owner_id=self.instance_id,
            ttl=self.lease_ttl,
        )
        hb.start()
        return hb


# ---------------------------------------------------------------------------
# Async heartbeat task
# ---------------------------------------------------------------------------


class _AsyncHeartbeat:
    """
    Background asyncio task that renews the lease at ttl/2 intervals.
    Stops when stop() is called or if lease renewal fails (ownership lost).
    """

    def __init__(
        self,
        store: WorkflowStore,
        run_id: str,
        owner_id: str,
        ttl: int,
    ) -> None:
        self._store = store
        self._run_id = run_id
        self._owner_id = owner_id
        self._ttl = ttl
        self._task: asyncio.Task | None = None

    def start(self):
        self._task = asyncio.create_task(self._run())

    def stop(self):
        if self._task:
            self._task.cancel()

    async def _run(self):
        interval = max(1, self._ttl // 2)
        try:
            while True:
                await asyncio.sleep(interval)
                renewed = await self._store.renew_lease(self._run_id, self._owner_id, self._ttl)
                if not renewed:
                    logger.warning("Heartbeat lost lease for run %s", self._run_id)
                    return
        except asyncio.CancelledError:
            return
