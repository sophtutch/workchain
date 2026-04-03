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
    ):
        self._db = db
        self._col = db[collection_name]
        self._lock_ttl = lock_ttl_seconds
        self._audit: AuditLogger = audit_logger or NullAuditLogger()
        self._instance_id = instance_id
        self._audit_tasks: set[asyncio.Task] = set()

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
        doc = await self._col.find_one({"_id": workflow_id})
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
        result: dict,
        # Audit context
        audit_event_type: AuditEventType | None = None,
        step_status_before: str = StepStatus.RUNNING.value,
        error: str | None = None,
        error_traceback: str | None = None,
        poll_count: int | None = None,
        poll_elapsed_seconds: float | None = None,
        **audit_kwargs: Any,
    ) -> Workflow | None:
        """Mark a step as FAILED with an error result."""
        wf = await self._fenced_step_update(
            workflow_id, step_index, fence_token,
            {
                "status": StepStatus.FAILED.value,
                "result": result,
                "result_type": None,
            },
        )
        if wf is not None:
            evt = audit_event_type or AuditEventType.STEP_FAILED
            self._emit(
                evt, wf,
                step=wf.steps[step_index], idx=step_index,
                step_status_before=step_status_before,
                error=error,
                error_traceback=error_traceback,
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
        result: dict,
        result_type: str | None,
        poll_started_at: datetime,
        next_poll_at: datetime,
        current_poll_interval: float,
        poll_count: int = 0,
        # Audit context
        audit_event_type: AuditEventType | None = None,
        result_summary: dict | None = None,
        recovery_action: str | None = None,
        **audit_kwargs: Any,
    ) -> Workflow | None:
        """Transition a step to BLOCKED and initialise poll scheduling."""
        updates: dict = {
            "status": StepStatus.BLOCKED.value,
            "result": result,
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
                result_summary=result_summary,
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
        async for doc in self._col.aggregate(pipeline):
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
            self._col.find(query)
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
        async for doc in self._col.aggregate(pipeline):
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
        result = await self._col.delete_one({
            "_id": workflow_id,
            "status": {"$in": terminal},
        })
        if result.deleted_count > 0:
            logger.info("Deleted workflow=%s", workflow_id)
            return True
        return False

    async def find_needs_review(self) -> list[str]:
        cursor = self._col.find(
            {"status": WorkflowStatus.NEEDS_REVIEW.value},
            {"_id": 1},
        )
        return [doc["_id"] async for doc in cursor]
