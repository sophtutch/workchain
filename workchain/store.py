"""MongoDB persistence layer with distributed locking via atomic updates."""

from __future__ import annotations

import asyncio
import importlib
import logging
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
        # workflow creation, so this never needs invalidation.
        self._step_index_cache: dict[tuple[str, str], int] = {}

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
        **kwargs: Any,
    ) -> None:
        """Construct and fire-and-forget an audit event."""
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
            **kwargs,
        )
        task = asyncio.ensure_future(self._audit.emit(event))
        self._audit_tasks.add(task)
        task.add_done_callback(self._audit_tasks.discard)

    async def emit(self, event: AuditEvent) -> None:
        """Public passthrough for events the engine needs to emit directly (e.g. STEP_TIMEOUT, RECOVERY_STARTED)."""
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
        await self._col.create_index("lock_expires_at")
        await self._col.create_index([("status", 1), ("lock_expires_at", 1)])

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
        doc = workflow.model_dump(mode="python", serialize_as_any=True)
        doc["_id"] = doc.pop("id")
        await self._col.insert_one(doc)
        self._emit(AuditEventType.WORKFLOW_CREATED, workflow)
        return workflow.id

    async def get(self, workflow_id: str) -> Workflow | None:
        doc = await self._col.find_one({"_id": workflow_id}, max_time_ms=self._op_timeout)
        if doc is None:
            return None
        return self._doc_to_workflow(doc)

    async def _fenced_step_update(
        self,
        workflow_id: str,
        step_index: int,
        fence_token: int,
        updates: dict,
    ) -> Workflow | None:
        """
        Low-level: update a step's fields atomically, guarded by the fence token.
        Prefer the explicit methods (submit_step, complete_step, etc.) over this.
        """
        set_fields = {f"steps.{step_index}.{k}": v for k, v in updates.items()}
        set_fields["updated_at"] = datetime.now(UTC)

        doc = await self._col.find_one_and_update(
            {"_id": workflow_id, "fence_token": fence_token},
            {"$set": set_fields},
            return_document=ReturnDocument.AFTER,
            maxTimeMS=self._op_timeout,
        )
        if doc is None:
            logger.warning(
                "Fenced write rejected for workflow=%s fence=%s (lock stolen?)",
                workflow_id, fence_token,
            )
            return None
        return self._doc_to_workflow(doc)

    # ------------------------------------------------------------------
    # Explicit step-state transitions
    # ------------------------------------------------------------------

    async def submit_step(
        self,
        workflow_id: str,
        step_index: int,
        fence_token: int,
        attempt: int,
    ) -> Workflow | None:
        """Mark a PENDING step as SUBMITTED with the given attempt number."""
        wf = await self._fenced_step_update(
            workflow_id, step_index, fence_token,
            {"status": StepStatus.SUBMITTED.value, "attempt": attempt},
        )
        if wf is not None:
            self._emit(
                AuditEventType.STEP_SUBMITTED, wf,
                step=wf.steps[step_index], idx=step_index,
                step_status_before=StepStatus.PENDING.value,
            )
        return wf

    async def mark_step_running(
        self,
        workflow_id: str,
        step_index: int,
        fence_token: int,
        attempt: int,
        *,
        max_attempts: int | None = None,
    ) -> Workflow | None:
        """Transition a SUBMITTED step to RUNNING for the given attempt number."""
        wf = await self._fenced_step_update(
            workflow_id, step_index, fence_token,
            {"status": StepStatus.RUNNING.value, "attempt": attempt},
        )
        if wf is not None:
            self._emit(
                AuditEventType.STEP_RUNNING, wf,
                step=wf.steps[step_index], idx=step_index,
                step_status_before=StepStatus.SUBMITTED.value,
                attempt=attempt,
                max_attempts=max_attempts,
            )
        return wf

    async def complete_step(
        self,
        workflow_id: str,
        step_index: int,
        fence_token: int,
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
        wf = await self._fenced_step_update(
            workflow_id, step_index, fence_token, updates,
        )
        if wf is not None:
            evt = audit_event_type or AuditEventType.STEP_COMPLETED
            self._emit(
                evt, wf,
                step=wf.steps[step_index], idx=step_index,
                step_status_before=step_status_before,
                result_summary=result.model_dump(exclude_none=True) if result else None,
                recovery_action=recovery_action,
                poll_count=poll_count,
                poll_progress=last_poll_progress,
                poll_message=last_poll_message,
                **audit_kwargs,
            )
        return wf

    async def fail_step(
        self,
        workflow_id: str,
        step_index: int,
        fence_token: int,
        result: StepResult,
        # Audit context
        audit_event_type: AuditEventType | None = None,
        step_status_before: str = StepStatus.RUNNING.value,
        poll_count: int | None = None,
        poll_elapsed_seconds: float | None = None,
        **audit_kwargs: Any,
    ) -> Workflow | None:
        """Mark a step as FAILED with an error result."""
        wf = await self._fenced_step_update(
            workflow_id, step_index, fence_token,
            {
                "status": StepStatus.FAILED.value,
                "result": result.model_dump(mode="python", serialize_as_any=True),
                "result_type": None,
            },
        )
        if wf is not None:
            evt = audit_event_type or AuditEventType.STEP_FAILED
            error_lines = (result.error or "").strip().splitlines()
            brief_error = error_lines[-1] if error_lines else None
            self._emit(
                evt, wf,
                step=wf.steps[step_index], idx=step_index,
                step_status_before=step_status_before,
                error=brief_error,
                error_traceback=result.error,
                poll_count=poll_count,
                poll_elapsed_seconds=poll_elapsed_seconds,
                **audit_kwargs,
            )
        return wf

    async def block_step(
        self,
        workflow_id: str,
        step_index: int,
        fence_token: int,
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
        wf = await self._fenced_step_update(
            workflow_id, step_index, fence_token, updates,
        )
        if wf is not None:
            evt = audit_event_type or AuditEventType.STEP_BLOCKED
            self._emit(
                evt, wf,
                step=wf.steps[step_index], idx=step_index,
                step_status_before=StepStatus.RUNNING.value,
                result_summary=result.model_dump(exclude_none=True),
                recovery_action=recovery_action,
                next_poll_at=next_poll_at,
                current_poll_interval=current_poll_interval,
                **audit_kwargs,
            )
        return wf

    async def schedule_next_poll(
        self,
        workflow_id: str,
        step_index: int,
        fence_token: int,
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
        wf = await self._fenced_step_update(
            workflow_id, step_index, fence_token, updates,
        )
        if wf is not None:
            self._emit(
                AuditEventType.POLL_CHECKED, wf,
                step=wf.steps[step_index], idx=step_index,
                poll_count=poll_count,
                poll_progress=last_poll_progress,
                poll_message=last_poll_message,
                next_poll_at=next_poll_at,
                current_poll_interval=current_poll_interval,
            )
        return wf

    async def reset_step(
        self,
        workflow_id: str,
        step_index: int,
        fence_token: int,
        status: StepStatus = StepStatus.PENDING,
    ) -> Workflow | None:
        """Reset a step to the given status (used in recovery)."""
        wf = await self._fenced_step_update(
            workflow_id, step_index, fence_token,
            {"status": status.value},
        )
        if wf is not None:
            self._emit(
                AuditEventType.RECOVERY_RESET, wf,
                step=wf.steps[step_index], idx=step_index,
                recovery_action="reset",
            )
        return wf

    async def advance_step(
        self,
        workflow_id: str,
        fence_token: int,
        new_step_index: int,
        workflow_status: WorkflowStatus | None = None,
        # Audit context
        workflow_status_before: str | None = None,
        recovery_action: str | None = None,
    ) -> Workflow | None:
        """Move to the next step (or mark workflow complete/failed)."""
        set_fields: dict = {
            "current_step_index": new_step_index,
            "updated_at": datetime.now(UTC),
        }
        if workflow_status is not None:
            set_fields["status"] = workflow_status.value

        doc = await self._col.find_one_and_update(
            {"_id": workflow_id, "fence_token": fence_token},
            {"$set": set_fields},
            return_document=ReturnDocument.AFTER,
            maxTimeMS=self._op_timeout,
        )
        if doc is None:
            return None
        wf = self._doc_to_workflow(doc)

        # Emit STEP_ADVANCED for normal advances
        if workflow_status is None:
            idx = new_step_index - 1 if new_step_index > 0 else 0
            step = wf.steps[idx] if idx < len(wf.steps) else None
            self._emit(
                AuditEventType.STEP_ADVANCED, wf,
                step=step, idx=idx,
            )
        elif workflow_status == WorkflowStatus.COMPLETED:
            self._emit(
                AuditEventType.WORKFLOW_COMPLETED, wf,
                workflow_status_before=workflow_status_before or WorkflowStatus.RUNNING.value,
            )
        elif workflow_status == WorkflowStatus.FAILED:
            self._emit(
                AuditEventType.WORKFLOW_FAILED, wf,
                workflow_status_before=workflow_status_before or WorkflowStatus.RUNNING.value,
            )
        elif workflow_status == WorkflowStatus.NEEDS_REVIEW:
            idx = new_step_index
            step = wf.steps[idx] if idx < len(wf.steps) else None
            self._emit(
                AuditEventType.RECOVERY_NEEDS_REVIEW, wf,
                step=step, idx=idx,
                recovery_action=recovery_action or "needs_review",
            )
        return wf

    # ------------------------------------------------------------------
    # Distributed locking
    # ------------------------------------------------------------------

    async def try_claim(
        self,
        workflow_id: str,
        instance_id: str,
    ) -> Workflow | None:
        """
        Atomically claim a workflow if it is unlocked or its lock has expired.
        Increments the fence token to invalidate any stale writers.
        """
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=self._lock_ttl)

        doc = await self._col.find_one_and_update(
            {
                "_id": workflow_id,
                "status": {"$in": [WorkflowStatus.PENDING.value, WorkflowStatus.RUNNING.value]},
                "$or": [
                    {"lock_expires_at": None},
                    {"lock_expires_at": {"$lt": now}},
                ],
            },
            {
                "$set": {
                    "locked_by": instance_id,
                    "lock_expires_at": expires,
                    "status": WorkflowStatus.RUNNING.value,
                    "updated_at": now,
                },
                "$inc": {"fence_token": 1},
            },
            return_document=ReturnDocument.AFTER,
            maxTimeMS=self._op_timeout,
        )
        if doc is None:
            return None
        logger.info("Claimed workflow=%s instance=%s fence=%s", workflow_id, instance_id, doc["fence_token"])
        wf = self._doc_to_workflow(doc)
        self._emit(
            AuditEventType.WORKFLOW_CLAIMED, wf,
            fence_token_before=wf.fence_token - 1,
            locked_by=instance_id,
        )
        return wf

    async def heartbeat(
        self,
        workflow_id: str,
        instance_id: str,
        fence_token: int,
    ) -> bool:
        """Extend the lock TTL. Returns False if the lock was stolen."""
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=self._lock_ttl)

        result = await self._col.update_one(
            {
                "_id": workflow_id,
                "locked_by": instance_id,
                "fence_token": fence_token,
            },
            {"$set": {"lock_expires_at": expires, "updated_at": now}},
        )
        if result.matched_count == 0:
            logger.warning("Heartbeat failed for workflow=%s (lock stolen?)", workflow_id)
            return False
        return True

    async def release_lock(
        self,
        workflow_id: str,
        instance_id: str,
        fence_token: int,
    ) -> bool:
        """Gracefully release a lock (e.g. on SIGTERM)."""
        result = await self._col.update_one(
            {
                "_id": workflow_id,
                "locked_by": instance_id,
                "fence_token": fence_token,
            },
            {
                "$set": {
                    "locked_by": None,
                    "lock_expires_at": None,
                    "updated_at": datetime.now(UTC),
                },
            },
        )
        return result.modified_count > 0

    # ------------------------------------------------------------------
    # Discovery — sweep-only, no change streams
    #
    # Two sweeps run at different intervals:
    #   Fast sweep (find_claimable): discovers new, abandoned, and poll-ready workflows
    #   Slow sweep (find_anomalies): catches stuck steps, stale locks, etc.
    # ------------------------------------------------------------------

    async def find_claimable(self, limit: int = 10) -> list[str]:
        """
        Fast sweep — find workflow IDs ready to be claimed:
        - pending (new, never started)
        - running but with an expired or missing lock (abandoned)
        - running with a BLOCKED current step whose next_poll_at has passed
          and lock is not held (lock was released after submission)
        """
        now = datetime.now(UTC)
        cursor = self._col.find(
            {
                "$or": [
                    # New workflows
                    {"status": WorkflowStatus.PENDING.value},
                    # Abandoned workflows (lock expired or missing)
                    {
                        "status": WorkflowStatus.RUNNING.value,
                        "locked_by": {"$ne": None},
                        "$or": [
                            {"lock_expires_at": None},
                            {"lock_expires_at": {"$lt": now}},
                        ],
                    },
                    # Async steps released for polling — unlocked and due for next poll
                    {
                        "status": WorkflowStatus.RUNNING.value,
                        "locked_by": None,
                    },
                ],
            },
            {"_id": 1, "locked_by": 1, "current_step_index": 1, "steps.next_poll_at": 1, "status": 1},
            max_time_ms=self._op_timeout,
        ).limit(limit)

        # Second-pass filter for unlocked RUNNING workflows: only include
        # them if the current step's next_poll_at has passed (or isn't set).
        # This avoids claiming BLOCKED workflows before their poll is due.
        results = []
        async for doc in cursor:
            wf_status = doc.get("status")
            if doc.get("locked_by") is None and wf_status != WorkflowStatus.PENDING.value:
                idx = doc.get("current_step_index", 0)
                steps = doc.get("steps", [])
                if idx < len(steps):
                    next_poll = steps[idx].get("next_poll_at")
                    if next_poll is not None:
                        if isinstance(next_poll, str):
                            next_poll = datetime.fromisoformat(next_poll)
                        if next_poll.tzinfo is None:
                            next_poll = next_poll.replace(tzinfo=UTC)
                    if next_poll is not None and next_poll > now:
                        continue  # not due yet
            results.append(doc["_id"])

        return results

    async def find_anomalies(
        self,
        step_stuck_seconds: float = 300.0,
        limit: int = 20,
    ) -> list[dict]:
        """
        Slow sweep — find workflows with anomalous state that the fast
        sweep won't catch:

        1. Steps stuck in SUBMITTED/RUNNING longer than step_stuck_seconds
           even though the workflow lock is still held (handler may have
           hung or the instance is zombie-locked).
        2. Workflows in RUNNING with a valid lock but no updated_at change
           for longer than step_stuck_seconds (engine may have died without
           releasing the lock and heartbeat is no longer extending it — but
           TTL hasn't expired yet due to a recent heartbeat).
        3. Workflows in RUNNING where current step is COMPLETED but
           current_step_index was never advanced (engine died between
           step completion and advance).

        Returns a list of dicts: {"workflow_id": str, "anomaly": str}
        """
        now = datetime.now(UTC)
        stale_cutoff = now - timedelta(seconds=step_stuck_seconds)
        # 1. Steps stuck in transient states too long
        cursor = self._col.find(
            {
                "status": WorkflowStatus.RUNNING.value,
                "steps": {
                    "$elemMatch": {
                        "status": {"$in": [
                            StepStatus.SUBMITTED.value,
                            StepStatus.RUNNING.value,
                        ]},
                    },
                },
                "updated_at": {"$lt": stale_cutoff},
            },
            {"_id": 1},
            max_time_ms=self._op_timeout,
        ).limit(limit)
        results: list[dict] = [
            {"workflow_id": doc["_id"], "anomaly": "step_stuck_in_transient_state"}
            async for doc in cursor
        ]

        # 2. Stale lock — running but no heartbeat activity
        cursor = self._col.find(
            {
                "status": WorkflowStatus.RUNNING.value,
                "locked_by": {"$ne": None},
                "updated_at": {"$lt": stale_cutoff},
            },
            {"_id": 1},
            max_time_ms=self._op_timeout,
        ).limit(limit)
        async for doc in cursor:
            wf_id = doc["_id"]
            if not any(r["workflow_id"] == wf_id for r in results):
                results.append({
                    "workflow_id": wf_id,
                    "anomaly": "stale_lock_no_heartbeat",
                })

        # 3. Completed step but index not advanced
        #    Use aggregation to compare current step status with index
        pipeline = [
            {"$match": {"status": WorkflowStatus.RUNNING.value}},
            {"$project": {
                "current_step_index": 1,
                "current_step_status": {
                    "$arrayElemAt": ["$steps.status", "$current_step_index"]
                },
                "updated_at": 1,
            }},
            {"$match": {
                "current_step_status": StepStatus.COMPLETED.value,
                "updated_at": {"$lt": stale_cutoff},
            }},
            {"$limit": limit},
        ]
        async for doc in self._col.aggregate(pipeline, maxTimeMS=self._op_timeout):
            wf_id = doc["_id"]
            if not any(r["workflow_id"] == wf_id for r in results):
                results.append({
                    "workflow_id": wf_id,
                    "anomaly": "completed_step_not_advanced",
                })

        return results

    async def force_release_lock(
        self,
        workflow_id: str,
    ) -> bool:
        """
        Unconditionally release a lock — used by the slow sweep to unstick
        workflows with stale locks. Ignores fence token so the next fast
        sweep can reclaim it.
        """
        result = await self._col.update_one(
            {"_id": workflow_id},
            {
                "$set": {
                    "locked_by": None,
                    "lock_expires_at": None,
                    "updated_at": datetime.now(UTC),
                },
            },
        )
        if result.modified_count > 0:
            logger.warning("Force-released lock on workflow=%s", workflow_id)
            return True
        return False

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
        doc = await self._col.find_one_and_update(
            {
                "_id": workflow_id,
                "status": {"$nin": terminal},
            },
            {
                "$set": {
                    "status": WorkflowStatus.CANCELLED.value,
                    "locked_by": None,
                    "lock_expires_at": None,
                    "updated_at": now,
                },
                "$inc": {"fence_token": 1},
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
            logger.info("Deleted workflow=%s", workflow_id)
            return True
        return False

    async def find_needs_review(self) -> list[str]:
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
        cached = self._step_index_cache.get(key)
        if cached is not None:
            return cached

        doc = await self._col.find_one(
            {"_id": workflow_id},
            {"steps.name": 1},
            max_time_ms=self._op_timeout,
        )
        if doc is None:
            return None
        # Prime cache for all steps in this workflow at once.
        for i, s in enumerate(doc.get("steps", [])):
            self._step_index_cache[(workflow_id, s["name"])] = i
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

        Returns the updated workflow, or None if already terminal.
        """
        doc = await self._col.find_one_and_update(
            {
                "_id": workflow_id,
                "status": {"$in": [
                    WorkflowStatus.PENDING.value,
                    WorkflowStatus.RUNNING.value,
                ]},
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
