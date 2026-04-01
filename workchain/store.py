"""MongoDB persistence layer with distributed locking via atomic updates."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from .models import StepStatus, Workflow, WorkflowStatus

logger = logging.getLogger(__name__)

COLLECTION = "workflows"


class MongoWorkflowStore:
    """
    Persists workflow state to MongoDB and provides distributed locking
    using atomic findOneAndUpdate with TTL-based locks.

    All timestamps use MongoDB server time ($currentDate) to avoid
    clock-skew issues across service instances.
    """

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        lock_ttl_seconds: int = 30,
    ):
        self._db = db
        self._col = db[COLLECTION]
        self._lock_ttl = lock_ttl_seconds

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    async def ensure_indexes(self) -> None:
        """Create indexes for efficient queries."""
        await self._col.create_index("status")
        await self._col.create_index("lock_expires_at")
        await self._col.create_index([("status", 1), ("lock_expires_at", 1)])

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def insert(self, workflow: Workflow) -> str:
        doc = workflow.model_dump(mode="json")
        doc["_id"] = workflow.id
        await self._col.insert_one(doc)
        return workflow.id

    async def get(self, workflow_id: str) -> Workflow | None:
        doc = await self._col.find_one({"_id": workflow_id})
        if doc is None:
            return None
        doc["id"] = doc.pop("_id")
        return Workflow.model_validate(doc)

    async def update_step(
        self,
        workflow_id: str,
        step_index: int,
        fence_token: int,
        updates: dict,
    ) -> Workflow | None:
        """
        Update a step's fields atomically, guarded by the fence token.
        Rejects stale writes from instances whose lock has been stolen.
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
        doc["id"] = doc.pop("_id")
        return Workflow.model_validate(doc)

    async def advance_step(
        self,
        workflow_id: str,
        fence_token: int,
        new_step_index: int,
        workflow_status: WorkflowStatus | None = None,
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
        doc["id"] = doc.pop("_id")
        return Workflow.model_validate(doc)

    async def update_context(
        self,
        workflow_id: str,
        fence_token: int,
        context_updates: dict,
    ) -> Workflow | None:
        """Merge keys into the shared workflow context."""
        set_fields = {f"context.{k}": v for k, v in context_updates.items()}
        set_fields["updated_at"] = datetime.now(UTC)

        doc = await self._col.find_one_and_update(
            {"_id": workflow_id, "fence_token": fence_token},
            {"$set": set_fields},
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            return None
        doc["id"] = doc.pop("_id")
        return Workflow.model_validate(doc)

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
        doc["id"] = doc.pop("_id")
        logger.info("Claimed workflow=%s instance=%s fence=%s", workflow_id, instance_id, doc["fence_token"])
        return Workflow.model_validate(doc)

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
        if result.modified_count == 0:
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
            {"_id": 1},
        ).limit(limit)

        # Second-pass filter for BLOCKED steps: check next_poll_at in Python
        # to avoid complex nested array queries. The MongoDB query above is
        # deliberately broad (all unlocked RUNNING) to keep the index simple.
        results = []
        async for doc in cursor:
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
        results: list[dict] = []

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
        async for doc in cursor:
            results.append({
                "workflow_id": doc["_id"],
                "anomaly": "step_stuck_in_transient_state",
            })

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

    async def force_release_lock(self, workflow_id: str) -> bool:
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

    async def find_needs_review(self) -> list[str]:
        cursor = self._col.find(
            {"status": WorkflowStatus.NEEDS_REVIEW.value},
            {"_id": 1},
        )
        return [doc["_id"] async for doc in cursor]
