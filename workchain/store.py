"""MongoDB persistence layer with distributed locking via atomic updates."""

from __future__ import annotations

import asyncio
import importlib
import logging
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from workchain.audit import AuditEvent, AuditEventType, NullAuditLogger
from workchain.models import Step, StepResult, StepStatus, Workflow, WorkflowStatus

if TYPE_CHECKING:
    from workchain.audit import AuditLogger

logger = logging.getLogger(__name__)

COLLECTION = "workflows"


def _import_class(dotted_path: str) -> type:
    """Import a class by dotted path (e.g. 'myapp.steps.ValidateConfig')."""
    module_path, _, class_name = dotted_path.rpartition(".")
    if not module_path:
        raise ValueError(f"Invalid dotted path: {dotted_path}")
    mod = importlib.import_module(module_path)
    try:
        return getattr(mod, class_name)
    except AttributeError as e:
        raise ImportError(
            f"Cannot find '{class_name}' in module '{module_path}' "
            f"(full path: {dotted_path})"
        ) from e


class MongoWorkflowStore:
    """
    Persists workflow state to MongoDB and provides distributed locking
    using atomic findOneAndUpdate with TTL-based locks.
    """

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        lock_ttl_seconds: int = 30,
        collection_name: str = COLLECTION,
        audit_logger: AuditLogger | None = None,
        instance_id: str | None = None,
        operation_timeout_ms: int = 30_000,
    ):
        if operation_timeout_ms <= 0:
            raise ValueError("operation_timeout_ms must be positive")
        self._db = db
        self._col = db[collection_name]
        self._lock_ttl = lock_ttl_seconds
        self._audit: AuditLogger = audit_logger or NullAuditLogger()
        self._instance_id = instance_id
        self._audit_tasks: set[asyncio.Task] = set()
        # pymongo uses max_time_ms (snake_case) for find/find_one but
        # maxTimeMS (camelCase) for find_one_and_update/aggregate kwargs.
        self._op_timeout = operation_timeout_ms
        # Step name → array index cache.  Step lists are immutable after
        # workflow creation, so entries never go stale.  Bounded via LRU
        # eviction to prevent unbounded memory growth in long-running engines.
        self._step_index_cache: OrderedDict[tuple[str, str], int] = OrderedDict()
        self._step_index_cache_max = 10_000

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
        fence_token_override: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Construct and fire-and-forget an audit event."""
        event = AuditEvent(
            workflow_id=wf.id,
            workflow_name=wf.name,
            event_type=event_type,
            instance_id=self._instance_id,
            fence_token=fence_token_override,
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
            step_depends_on=step.depends_on if step else None,
            **kwargs,
        )
        assign = getattr(self._audit, "assign_sequence", None)
        if assign is not None:
            assign(event)
        task = asyncio.ensure_future(self._audit.emit(event))
        self._audit_tasks.add(task)
        task.add_done_callback(self._audit_tasks.discard)

    async def emit(self, event: AuditEvent) -> None:
        """Public passthrough for events the engine needs to emit directly (e.g. STEP_TIMEOUT, RECOVERY_STARTED)."""
        assign = getattr(self._audit, "assign_sequence", None)
        if assign is not None:
            assign(event)
        task = asyncio.ensure_future(self._audit.emit(event))
        self._audit_tasks.add(task)
        task.add_done_callback(self._audit_tasks.discard)

    async def drain_audit_tasks(self, timeout: float = 5.0) -> None:
        """Wait for pending audit writes with a timeout. Called during shutdown."""
        if self._audit_tasks:
            _done, pending = await asyncio.wait(self._audit_tasks, timeout=timeout)
            if pending:
                logger.warning("Timed out waiting for %d audit tasks", len(pending))

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    async def ensure_indexes(self) -> None:
        """Create indexes for efficient queries."""
        await self._col.create_index("status")
        await self._col.create_index([("status", 1), ("steps.status", 1)])

    # ------------------------------------------------------------------
    # Document conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _doc_to_workflow(doc: dict) -> Workflow:
        """
        Convert a MongoDB document to a Workflow, resolving typed
        StepConfig and StepResult subclasses from their stored dotted paths.
        """
        doc["id"] = doc.pop("_id")
        for step_doc in doc.get("steps", []):
            ct = step_doc.get("config_type")
            if ct and step_doc.get("config") and isinstance(step_doc["config"], dict):
                step_doc["config"] = _import_class(ct)(**step_doc["config"])
            rt = step_doc.get("result_type")
            if rt and step_doc.get("result") and isinstance(step_doc["result"], dict):
                step_doc["result"] = _import_class(rt)(**step_doc["result"])
        return Workflow.model_validate(doc)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def insert(self, workflow: Workflow) -> str:
        """Persist a new workflow to MongoDB and emit WORKFLOW_CREATED."""
        doc = workflow.model_dump(mode="python", serialize_as_any=True)
        doc["_id"] = doc.pop("id")
        await self._col.insert_one(doc)
        self._emit(AuditEventType.WORKFLOW_CREATED, workflow)
        return workflow.id

    async def get(self, workflow_id: str) -> Workflow | None:
        """Retrieve a workflow by ID, or None if not found."""
        doc = await self._col.find_one({"_id": workflow_id}, max_time_ms=self._op_timeout)
        if doc is None:
            return None
        return self._doc_to_workflow(doc)

    # ------------------------------------------------------------------
    # Discovery — sweep-only, no change streams
    #
    # Two sweeps run at different intervals:
    #   Fast sweep (find_claimable_steps): discovers ready steps across all workflows
    #   Slow sweep (find_anomalies): catches stuck steps, stale locks, etc.
    # ------------------------------------------------------------------

    async def find_anomalies(
        self,
        step_stuck_seconds: float = 300.0,
        limit: int = 20,
    ) -> list[dict]:
        """
        Slow sweep — find steps and workflows with anomalous state that
        the fast claim loop won't catch:

        1. **Stuck steps** — steps in SUBMITTED/RUNNING whose workflow
           hasn't been updated for longer than *step_stuck_seconds*.
           The handler may have hung or the instance died without releasing.
        2. **Stale step locks** — steps with ``locked_by`` set but
           ``lock_expires_at`` already in the past and no recent heartbeat
           (workflow ``updated_at`` is stale). The lock TTL expired but
           was never cleaned up.
        3. **Orphaned workflows** — workflow status is RUNNING but every
           step is in a terminal state (COMPLETED or FAILED). The engine
           instance that ran the last step died before calling
           ``try_complete_workflow`` / ``try_fail_workflow``.

        Returns a list of dicts with keys:
        ``{"workflow_id": str, "step_name": str | None, "anomaly": str}``
        """
        now = datetime.now(UTC)
        stale_cutoff = now - timedelta(seconds=step_stuck_seconds)
        results: list[dict] = []
        seen: set[tuple[str, str | None]] = set()

        def _add(wf_id: str, step_name: str | None, anomaly: str) -> None:
            key = (wf_id, step_name)
            if key not in seen:
                seen.add(key)
                results.append({
                    "workflow_id": wf_id,
                    "step_name": step_name,
                    "anomaly": anomaly,
                })

        # 1. Steps stuck in transient states too long
        pipeline_stuck: list[dict] = [
            {"$match": {
                "status": WorkflowStatus.RUNNING.value,
                "updated_at": {"$lt": stale_cutoff},
                "steps": {"$elemMatch": {
                    "status": {"$in": [
                        StepStatus.SUBMITTED.value,
                        StepStatus.RUNNING.value,
                    ]},
                }},
            }},
            {"$project": {"steps": 1}},
            {"$unwind": "$steps"},
            {"$match": {"steps.status": {"$in": [
                StepStatus.SUBMITTED.value,
                StepStatus.RUNNING.value,
            ]}}},
            {"$limit": limit},
        ]
        async for doc in self._col.aggregate(pipeline_stuck, maxTimeMS=self._op_timeout):
            _add(doc["_id"], doc["steps"]["name"], "step_stuck_in_transient_state")

        # 2. Stale step locks — step locked but lock expired + no heartbeat
        pipeline_stale: list[dict] = [
            {"$match": {
                "status": WorkflowStatus.RUNNING.value,
                "updated_at": {"$lt": stale_cutoff},
                "steps": {"$elemMatch": {
                    "locked_by": {"$ne": None},
                    "lock_expires_at": {"$lt": now},
                }},
            }},
            {"$project": {"steps": 1}},
            {"$unwind": "$steps"},
            {"$match": {
                "steps.locked_by": {"$ne": None},
                "steps.lock_expires_at": {"$lt": now},
            }},
            {"$limit": limit},
        ]
        async for doc in self._col.aggregate(pipeline_stale, maxTimeMS=self._op_timeout):
            _add(doc["_id"], doc["steps"]["name"], "stale_step_lock")

        # 3. Orphaned workflows — all steps terminal but workflow still RUNNING.
        #    Use double-negation: no step exists that is NOT in a terminal state.
        non_terminal = [
            s.value for s in StepStatus
            if s not in (StepStatus.COMPLETED, StepStatus.FAILED)
        ]
        cursor_orphan = self._col.find(
            {
                "status": WorkflowStatus.RUNNING.value,
                "updated_at": {"$lt": stale_cutoff},
                "steps.0": {"$exists": True},
                "steps": {"$not": {"$elemMatch": {
                    "status": {"$in": non_terminal},
                }}},
            },
            {"_id": 1},
            max_time_ms=self._op_timeout,
        ).limit(limit)
        async for doc in cursor_orphan:
            _add(doc["_id"], None, "orphaned_workflow")

        return results

    async def cancel_workflow(self, workflow_id: str) -> Workflow | None:
        """
        Cancel a workflow. Only non-terminal workflows can be cancelled.
        Releases any held lock and sets status to CANCELLED.
        Returns the updated workflow, or None if already terminal / not found.
        """
        now = datetime.now(UTC)
        terminal = [
            WorkflowStatus.COMPLETED.value,
            WorkflowStatus.FAILED.value,
            WorkflowStatus.NEEDS_REVIEW.value,
            WorkflowStatus.CANCELLED.value,
        ]
        # Build step-lock clearing fields for all steps in the workflow.
        # We pre-fetch the doc to know how many steps exist, then clear
        # locks atomically in the same update using explicit indices
        # (mongomock doesn't support $[] or $[identifier] operators).
        existing = await self._col.find_one(
            {"_id": workflow_id, "status": {"$nin": terminal}},
            max_time_ms=self._op_timeout,
        )
        if existing is None:
            return None

        step_clears: dict[str, None] = {}
        fence_incs: dict[str, int] = {}
        for i in range(len(existing.get("steps", []))):
            step_clears[f"steps.{i}.locked_by"] = None
            step_clears[f"steps.{i}.lock_expires_at"] = None
            fence_incs[f"steps.{i}.fence_token"] = 1

        doc = await self._col.find_one_and_update(
            {
                "_id": workflow_id,
                "status": {"$nin": terminal},
            },
            {
                "$set": {
                    "status": WorkflowStatus.CANCELLED.value,
                    "updated_at": now,
                    **step_clears,
                },
                "$inc": fence_incs,
            },
            return_document=ReturnDocument.AFTER,
            maxTimeMS=self._op_timeout,
        )
        if doc is None:
            return None
        logger.info("Cancelled workflow=%s", workflow_id)
        wf = self._doc_to_workflow(doc)
        self._emit(AuditEventType.WORKFLOW_CANCELLED, wf)
        return wf

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    async def list_workflows(
        self,
        status: WorkflowStatus | None = None,
        name: str | None = None,
        limit: int = 50,
        skip: int = 0,
    ) -> list[Workflow]:
        """
        List workflows with optional filters, sorted by created_at descending.
        """
        query: dict = {}
        if status is not None:
            query["status"] = status.value
        if name is not None:
            query["name"] = name

        cursor = (
            self._col.find(query, max_time_ms=self._op_timeout)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        return [self._doc_to_workflow(doc) async for doc in cursor]

    async def count_by_status(self) -> dict[str, int]:
        """
        Return a count of workflows grouped by status.
        E.g. {"pending": 5, "running": 2, "completed": 10}
        """
        pipeline = [
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        ]
        result: dict[str, int] = {}
        async for doc in self._col.aggregate(pipeline, maxTimeMS=self._op_timeout):
            result[doc["_id"]] = doc["count"]
        return result

    async def delete_workflow(self, workflow_id: str) -> bool:
        """
        Delete a terminal workflow by ID.
        Only terminal workflows (COMPLETED, FAILED, NEEDS_REVIEW, CANCELLED)
        can be deleted.
        """
        terminal = [
            WorkflowStatus.COMPLETED.value,
            WorkflowStatus.FAILED.value,
            WorkflowStatus.NEEDS_REVIEW.value,
            WorkflowStatus.CANCELLED.value,
        ]
        result = await self._col.delete_one(
            {"_id": workflow_id, "status": {"$in": terminal}},
        )
        if result.deleted_count > 0:
            # Invalidate step index cache entries for this workflow.
            keys_to_remove = [k for k in self._step_index_cache if k[0] == workflow_id]
            for k in keys_to_remove:
                del self._step_index_cache[k]
            logger.info("Deleted workflow=%s", workflow_id)
            return True
        return False

    async def find_needs_review(self) -> list[str]:
        """Return IDs of workflows in NEEDS_REVIEW status."""
        cursor = self._col.find(
            {"status": WorkflowStatus.NEEDS_REVIEW.value},
            {"_id": 1},
            max_time_ms=self._op_timeout,
        )
        return [doc["_id"] async for doc in cursor]

    # ==================================================================
    # Per-step distributed locking (new model — step-level claims)
    #
    # These methods support the dependency-based execution model where
    # individual steps (not workflows) are claimed, executed, and released.
    # Multiple engine instances can work on different steps of the same
    # workflow concurrently.
    # ==================================================================

    # ------------------------------------------------------------------
    # Step index resolution
    # ------------------------------------------------------------------

    async def _step_index(self, workflow_id: str, step_name: str) -> int | None:
        """Resolve a step name to its array index.

        Step lists are immutable after workflow creation, so the result is
        cached in-memory to avoid repeated round-trips on hot paths like
        heartbeat.
        """
        key = (workflow_id, step_name)
        if key in self._step_index_cache:
            self._step_index_cache.move_to_end(key)
            return self._step_index_cache[key]

        doc = await self._col.find_one(
            {"_id": workflow_id},
            {"steps.name": 1},
            max_time_ms=self._op_timeout,
        )
        if doc is None:
            return None
        # Prime cache for all steps in this workflow at once.
        for i, s in enumerate(doc.get("steps", [])):
            entry = (workflow_id, s["name"])
            self._step_index_cache[entry] = i
            self._step_index_cache.move_to_end(entry)
        # Evict oldest entries if over capacity.
        while len(self._step_index_cache) > self._step_index_cache_max:
            self._step_index_cache.popitem(last=False)
        return self._step_index_cache.get(key)

    # ------------------------------------------------------------------
    # Step-level fenced updates
    # ------------------------------------------------------------------

    async def _fenced_step_update_by_name(
        self,
        workflow_id: str,
        step_name: str,
        step_fence_token: int,
        updates: dict,
    ) -> Workflow | None:
        """
        Low-level: update a step's fields atomically, guarded by the step's
        fence token. Uses explicit array index to target the step by name
        within the embedded array.

        Returns the updated Workflow or None if the fence was rejected.
        """
        idx = await self._step_index(workflow_id, step_name)
        if idx is None:
            logger.warning(
                "Step not found for fenced write: workflow=%s step=%s",
                workflow_id, step_name,
            )
            return None

        prefix = f"steps.{idx}"
        set_fields = {f"{prefix}.{k}": v for k, v in updates.items()}
        set_fields["updated_at"] = datetime.now(UTC)

        doc = await self._col.find_one_and_update(
            {
                "_id": workflow_id,
                f"{prefix}.name": step_name,
                f"{prefix}.fence_token": step_fence_token,
            },
            {"$set": set_fields},
            return_document=ReturnDocument.AFTER,
            maxTimeMS=self._op_timeout,
        )
        if doc is None:
            logger.warning(
                "Fenced step write rejected for workflow=%s step=%s fence=%s (lock stolen?)",
                workflow_id, step_name, step_fence_token,
            )
            return None
        return self._doc_to_workflow(doc)

    # ------------------------------------------------------------------
    # Per-step state transitions (name-based, step-level fence tokens)
    #
    # These mirror the index-based methods above but use step names and
    # step-level fence tokens via _fenced_step_update_by_name.  The old
    # index-based methods remain for backward compatibility during the
    # migration (removed in Task 5).
    # ------------------------------------------------------------------

    async def submit_step_by_name(
        self,
        workflow_id: str,
        step_name: str,
        step_fence_token: int,
        attempt: int,
    ) -> Workflow | None:
        """Mark a PENDING step as SUBMITTED with the given attempt number."""
        wf = await self._fenced_step_update_by_name(
            workflow_id, step_name, step_fence_token,
            {"status": StepStatus.SUBMITTED.value, "attempt": attempt},
        )
        if wf is not None:
            idx = await self._step_index(workflow_id, step_name)
            self._emit(
                AuditEventType.STEP_SUBMITTED, wf,
                step=wf.step_by_name(step_name), idx=idx,
                step_status_before=StepStatus.PENDING.value,
                fence_token_override=step_fence_token,
            )
        return wf

    async def mark_step_running_by_name(
        self,
        workflow_id: str,
        step_name: str,
        step_fence_token: int,
        attempt: int,
        *,
        max_attempts: int | None = None,
    ) -> Workflow | None:
        """Transition a SUBMITTED step to RUNNING for the given attempt number."""
        wf = await self._fenced_step_update_by_name(
            workflow_id, step_name, step_fence_token,
            {"status": StepStatus.RUNNING.value, "attempt": attempt},
        )
        if wf is not None:
            idx = await self._step_index(workflow_id, step_name)
            self._emit(
                AuditEventType.STEP_RUNNING, wf,
                step=wf.step_by_name(step_name), idx=idx,
                step_status_before=StepStatus.SUBMITTED.value,
                attempt=attempt,
                max_attempts=max_attempts,
                fence_token_override=step_fence_token,
            )
        return wf

    async def complete_step_by_name(
        self,
        workflow_id: str,
        step_name: str,
        step_fence_token: int,
        result: StepResult | None = None,
        result_type: str | None = None,
        poll_count: int | None = None,
        last_poll_at: datetime | None = None,
        last_poll_progress: float | None = None,
        last_poll_message: str | None = None,
        # Audit context
        audit_event_type: AuditEventType | None = None,
        step_status_before: str = StepStatus.RUNNING.value,
        recovery_action: str | None = None,
        **audit_kwargs: Any,
    ) -> Workflow | None:
        """Mark a step as COMPLETED with its result."""
        updates: dict = {"status": StepStatus.COMPLETED.value}
        if result is not None:
            updates["result"] = result.model_dump(mode="python", serialize_as_any=True)
        if result_type is not None:
            updates["result_type"] = result_type
        if poll_count is not None:
            updates["poll_count"] = poll_count
        if last_poll_at is not None:
            updates["last_poll_at"] = last_poll_at
        if last_poll_progress is not None:
            updates["last_poll_progress"] = last_poll_progress
        if last_poll_message is not None:
            updates["last_poll_message"] = last_poll_message
        wf = await self._fenced_step_update_by_name(
            workflow_id, step_name, step_fence_token, updates,
        )
        if wf is not None:
            idx = await self._step_index(workflow_id, step_name)
            evt = audit_event_type or AuditEventType.STEP_COMPLETED
            self._emit(
                evt, wf,
                step=wf.step_by_name(step_name), idx=idx,
                step_status_before=step_status_before,
                result_summary=result.model_dump(exclude_none=True) if result else None,
                recovery_action=recovery_action,
                poll_count=poll_count,
                poll_progress=last_poll_progress,
                poll_message=last_poll_message,
                fence_token_override=step_fence_token,
                **audit_kwargs,
            )
        return wf

    async def fail_step_by_name(
        self,
        workflow_id: str,
        step_name: str,
        step_fence_token: int,
        result: StepResult,
        # Audit context
        audit_event_type: AuditEventType | None = None,
        step_status_before: str = StepStatus.RUNNING.value,
        poll_count: int | None = None,
        poll_elapsed_seconds: float | None = None,
        **audit_kwargs: Any,
    ) -> Workflow | None:
        """Mark a step as FAILED with an error result."""
        wf = await self._fenced_step_update_by_name(
            workflow_id, step_name, step_fence_token,
            {
                "status": StepStatus.FAILED.value,
                "result": result.model_dump(mode="python", serialize_as_any=True),
                "result_type": None,
            },
        )
        if wf is not None:
            idx = await self._step_index(workflow_id, step_name)
            evt = audit_event_type or AuditEventType.STEP_FAILED
            error_lines = (result.error or "").strip().splitlines()
            brief_error = error_lines[-1] if error_lines else None
            self._emit(
                evt, wf,
                step=wf.step_by_name(step_name), idx=idx,
                step_status_before=step_status_before,
                error=brief_error,
                error_traceback=result.error,
                poll_count=poll_count,
                poll_elapsed_seconds=poll_elapsed_seconds,
                fence_token_override=step_fence_token,
                **audit_kwargs,
            )
        return wf

    async def block_step_by_name(
        self,
        workflow_id: str,
        step_name: str,
        step_fence_token: int,
        result: StepResult,
        result_type: str | None,
        poll_started_at: datetime,
        next_poll_at: datetime,
        current_poll_interval: float,
        poll_count: int = 0,
        # Audit context
        audit_event_type: AuditEventType | None = None,
        recovery_action: str | None = None,
        **audit_kwargs: Any,
    ) -> Workflow | None:
        """Transition a step to BLOCKED and initialise poll scheduling."""
        updates: dict = {
            "status": StepStatus.BLOCKED.value,
            "result": result.model_dump(mode="python", serialize_as_any=True),
            "poll_started_at": poll_started_at,
            "next_poll_at": next_poll_at,
            "current_poll_interval": current_poll_interval,
            "poll_count": poll_count,
        }
        if result_type is not None:
            updates["result_type"] = result_type
        wf = await self._fenced_step_update_by_name(
            workflow_id, step_name, step_fence_token, updates,
        )
        if wf is not None:
            idx = await self._step_index(workflow_id, step_name)
            evt = audit_event_type or AuditEventType.STEP_BLOCKED
            self._emit(
                evt, wf,
                step=wf.step_by_name(step_name), idx=idx,
                step_status_before=StepStatus.RUNNING.value,
                result_summary=result.model_dump(exclude_none=True),
                recovery_action=recovery_action,
                next_poll_at=next_poll_at,
                current_poll_interval=current_poll_interval,
                fence_token_override=step_fence_token,
                **audit_kwargs,
            )
        return wf

    async def schedule_next_poll_by_name(
        self,
        workflow_id: str,
        step_name: str,
        step_fence_token: int,
        poll_count: int,
        last_poll_at: datetime,
        next_poll_at: datetime,
        current_poll_interval: float,
        last_poll_progress: float | None = None,
        last_poll_message: str | None = None,
    ) -> Workflow | None:
        """Update poll scheduling for a BLOCKED step (not yet complete)."""
        updates: dict = {
            "poll_count": poll_count,
            "last_poll_at": last_poll_at,
            "next_poll_at": next_poll_at,
            "current_poll_interval": current_poll_interval,
        }
        if last_poll_progress is not None:
            updates["last_poll_progress"] = last_poll_progress
        if last_poll_message is not None:
            updates["last_poll_message"] = last_poll_message
        wf = await self._fenced_step_update_by_name(
            workflow_id, step_name, step_fence_token, updates,
        )
        if wf is not None:
            idx = await self._step_index(workflow_id, step_name)
            self._emit(
                AuditEventType.POLL_CHECKED, wf,
                step=wf.step_by_name(step_name), idx=idx,
                poll_count=poll_count,
                poll_progress=last_poll_progress,
                poll_message=last_poll_message,
                next_poll_at=next_poll_at,
                current_poll_interval=current_poll_interval,
                fence_token_override=step_fence_token,
            )
        return wf

    async def reset_step_by_name(
        self,
        workflow_id: str,
        step_name: str,
        step_fence_token: int,
        status: StepStatus = StepStatus.PENDING,
    ) -> Workflow | None:
        """Reset a step to the given status (used in recovery)."""
        wf = await self._fenced_step_update_by_name(
            workflow_id, step_name, step_fence_token,
            {"status": status.value},
        )
        if wf is not None:
            idx = await self._step_index(workflow_id, step_name)
            self._emit(
                AuditEventType.RECOVERY_RESET, wf,
                step=wf.step_by_name(step_name), idx=idx,
                recovery_action="reset",
                fence_token_override=step_fence_token,
            )
        return wf

    # ------------------------------------------------------------------
    # Per-step claiming and lock management
    # ------------------------------------------------------------------

    async def try_claim_step(
        self,
        workflow_id: str,
        step_name: str,
        instance_id: str,
    ) -> tuple[Workflow, int] | None:
        """
        Atomically claim a single step for execution. The step must be
        unlocked (or its lock expired) and the workflow must be non-terminal.

        Returns (workflow, step_fence_token) on success, None on failure.
        Increments the step's fence_token to invalidate stale writers.
        """
        idx = await self._step_index(workflow_id, step_name)
        if idx is None:
            return None

        now = datetime.now(UTC)
        expires = now + timedelta(seconds=self._lock_ttl)
        prefix = f"steps.{idx}"

        doc = await self._col.find_one_and_update(
            {
                "_id": workflow_id,
                "status": {"$in": [
                    WorkflowStatus.PENDING.value,
                    WorkflowStatus.RUNNING.value,
                ]},
                f"{prefix}.name": step_name,
                f"{prefix}.status": {"$in": [
                    StepStatus.PENDING.value,
                    StepStatus.BLOCKED.value,
                    StepStatus.SUBMITTED.value,
                    StepStatus.RUNNING.value,
                ]},
                "$or": [
                    {f"{prefix}.locked_by": None},
                    {f"{prefix}.lock_expires_at": {"$lt": now}},
                ],
            },
            {
                "$set": {
                    f"{prefix}.locked_by": instance_id,
                    f"{prefix}.lock_expires_at": expires,
                    "status": WorkflowStatus.RUNNING.value,
                    "updated_at": now,
                },
                "$inc": {f"{prefix}.fence_token": 1},
            },
            return_document=ReturnDocument.AFTER,
            maxTimeMS=self._op_timeout,
        )
        if doc is None:
            return None

        wf = self._doc_to_workflow(doc)
        step = wf.step_by_name(step_name)
        if step is None:
            return None  # should not happen

        logger.info(
            "Claimed step=%s workflow=%s instance=%s fence=%s",
            step_name, workflow_id, instance_id, step.fence_token,
        )
        self._emit(
            AuditEventType.STEP_CLAIMED, wf,
            step=step,
            idx=idx,
            fence_token_override=step.fence_token,
            fence_token_before=step.fence_token - 1,
            locked_by=instance_id,
        )
        return wf, step.fence_token

    async def heartbeat_step(
        self,
        workflow_id: str,
        step_name: str,
        instance_id: str,
        step_fence_token: int,
    ) -> bool:
        """Extend the lock TTL on a single step. Returns False if the lock was stolen."""
        idx = await self._step_index(workflow_id, step_name)
        if idx is None:
            return False

        now = datetime.now(UTC)
        expires = now + timedelta(seconds=self._lock_ttl)
        prefix = f"steps.{idx}"

        result = await self._col.update_one(
            {
                "_id": workflow_id,
                f"{prefix}.name": step_name,
                f"{prefix}.locked_by": instance_id,
                f"{prefix}.fence_token": step_fence_token,
            },
            {"$set": {f"{prefix}.lock_expires_at": expires, "updated_at": now}},
        )
        if result.matched_count == 0:
            logger.warning(
                "Step heartbeat failed for workflow=%s step=%s (lock stolen?)",
                workflow_id, step_name,
            )
            return False
        return True

    async def release_step_lock(
        self,
        workflow_id: str,
        step_name: str,
        instance_id: str,
        step_fence_token: int,
    ) -> bool:
        """Gracefully release a step's lock."""
        idx = await self._step_index(workflow_id, step_name)
        if idx is None:
            return False

        prefix = f"steps.{idx}"
        result = await self._col.update_one(
            {
                "_id": workflow_id,
                f"{prefix}.name": step_name,
                f"{prefix}.locked_by": instance_id,
                f"{prefix}.fence_token": step_fence_token,
            },
            {
                "$set": {
                    f"{prefix}.locked_by": None,
                    f"{prefix}.lock_expires_at": None,
                    "updated_at": datetime.now(UTC),
                },
            },
        )
        return result.modified_count > 0

    async def force_release_step_lock(
        self,
        workflow_id: str,
        step_name: str,
    ) -> bool:
        """
        Unconditionally release a step's lock — used by the sweep to
        unstick steps with stale locks. Ignores fence token.
        """
        idx = await self._step_index(workflow_id, step_name)
        if idx is None:
            return False

        prefix = f"steps.{idx}"
        result = await self._col.update_one(
            {
                "_id": workflow_id,
                f"{prefix}.name": step_name,
            },
            {
                "$set": {
                    f"{prefix}.locked_by": None,
                    f"{prefix}.lock_expires_at": None,
                    "updated_at": datetime.now(UTC),
                },
                "$inc": {f"{prefix}.fence_token": 1},
            },
        )
        if result.modified_count > 0:
            logger.warning(
                "Force-released step lock on workflow=%s step=%s",
                workflow_id, step_name,
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Per-step discovery
    # ------------------------------------------------------------------

    async def find_claimable_steps(self, limit: int = 10) -> list[tuple[str, str]]:
        """
        Find (workflow_id, step_name) pairs for steps that are ready to be
        claimed:
        - PENDING steps whose dependencies are all COMPLETED, and unlocked
        - BLOCKED steps whose next_poll_at has passed, and unlocked

        Uses a broad MongoDB query followed by Python-side readiness
        filtering via Workflow.ready_steps() and Workflow.pollable_steps().
        """
        cursor = self._col.find(
            {
                "status": {"$in": [
                    WorkflowStatus.PENDING.value,
                    WorkflowStatus.RUNNING.value,
                ]},
                "steps": {
                    "$elemMatch": {
                        "status": {"$in": [
                            StepStatus.PENDING.value,
                            StepStatus.BLOCKED.value,
                        ]},
                    },
                },
            },
            max_time_ms=self._op_timeout,
        ).limit(limit * 3)  # over-fetch since Python filter narrows results

        results: list[tuple[str, str]] = []
        async for doc in cursor:
            wf = self._doc_to_workflow(doc)
            for step in wf.ready_steps():
                results.append((wf.id, step.name))
                if len(results) >= limit:
                    return results
            for step in wf.pollable_steps():
                results.append((wf.id, step.name))
                if len(results) >= limit:
                    return results
        return results

    # ------------------------------------------------------------------
    # Workflow status transitions (atomic)
    # ------------------------------------------------------------------

    async def try_complete_workflow(self, workflow_id: str) -> Workflow | None:
        """
        Atomically set workflow status to COMPLETED if all steps are COMPLETED.

        Called after a step completes — checks whether that was the last step.
        Uses a MongoDB query that only matches if no non-completed step exists.
        Returns the updated workflow, or None if not all steps are done.
        """
        doc = await self._col.find_one_and_update(
            {
                "_id": workflow_id,
                "status": WorkflowStatus.RUNNING.value,
                "steps.0": {"$exists": True},  # must have at least one step
                "steps": {
                    "$not": {
                        "$elemMatch": {
                            "status": {"$ne": StepStatus.COMPLETED.value},
                        },
                    },
                },
            },
            {"$set": {
                "status": WorkflowStatus.COMPLETED.value,
                "updated_at": datetime.now(UTC),
            }},
            return_document=ReturnDocument.AFTER,
            maxTimeMS=self._op_timeout,
        )
        if doc is None:
            return None
        wf = self._doc_to_workflow(doc)
        logger.info("Workflow completed: %s", workflow_id)
        self._emit(
            AuditEventType.WORKFLOW_COMPLETED, wf,
            workflow_status_before=WorkflowStatus.RUNNING.value,
        )
        return wf

    async def try_fail_workflow(self, workflow_id: str) -> Workflow | None:
        """
        Atomically set workflow status to FAILED. Called when a step fails.

        Only matches RUNNING workflows — a step can only fail after the
        workflow has been claimed (which transitions it to RUNNING).
        Returns the updated workflow, or None if not RUNNING.
        """
        doc = await self._col.find_one_and_update(
            {
                "_id": workflow_id,
                "status": WorkflowStatus.RUNNING.value,
            },
            {"$set": {
                "status": WorkflowStatus.FAILED.value,
                "updated_at": datetime.now(UTC),
            }},
            return_document=ReturnDocument.AFTER,
            maxTimeMS=self._op_timeout,
        )
        if doc is None:
            return None
        wf = self._doc_to_workflow(doc)
        logger.info("Workflow failed: %s", workflow_id)
        self._emit(
            AuditEventType.WORKFLOW_FAILED, wf,
            workflow_status_before=WorkflowStatus.RUNNING.value,
        )
        return wf

    async def try_needs_review_workflow(self, workflow_id: str) -> Workflow | None:
        """
        Atomically set workflow status to NEEDS_REVIEW. Called when a step
        cannot be safely recovered (non-idempotent, no verify hook).

        Only matches RUNNING workflows.
        Returns the updated workflow, or None if not RUNNING.
        """
        doc = await self._col.find_one_and_update(
            {
                "_id": workflow_id,
                "status": WorkflowStatus.RUNNING.value,
            },
            {"$set": {
                "status": WorkflowStatus.NEEDS_REVIEW.value,
                "updated_at": datetime.now(UTC),
            }},
            return_document=ReturnDocument.AFTER,
            maxTimeMS=self._op_timeout,
        )
        if doc is None:
            return None
        wf = self._doc_to_workflow(doc)
        logger.info("Workflow needs review: %s", workflow_id)
        self._emit(
            AuditEventType.RECOVERY_NEEDS_REVIEW, wf,
            workflow_status_before=WorkflowStatus.RUNNING.value,
            recovery_action="needs_review",
        )
        return wf
