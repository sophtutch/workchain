"""MongoDB WorkflowStore implementation using motor (async)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

from workchain.exceptions import ConcurrentModificationError, WorkflowRunNotFoundError
from workchain.models import (
    LEASABLE_STATUSES,
    StepStatus,
    WorkflowRun,
    WorkflowStatus,
)

logger = logging.getLogger(__name__)


class MongoWorkflowStore:
    """
    MongoDB-backed WorkflowStore.

    Usage::

        client = AsyncIOMotorClient("mongodb://localhost:27017")
        store = MongoWorkflowStore(
            client=client, database="myapp"
        )  # owner_id defaults to hostname
    """

    def __init__(
        self,
        client: AsyncIOMotorClient,
        database: str,
        owner_id: str | None = None,
        lease_ttl_seconds: int = 30,
    ) -> None:
        import platform

        self._db = client[database]
        self._collection: AsyncIOMotorCollection = client[database]["workflow_runs"]
        self._owner_id = owner_id or platform.node()
        self._lease_ttl = lease_ttl_seconds

    # ------------------------------------------------------------------
    # Insert / Update
    # ------------------------------------------------------------------

    async def save(self, run: WorkflowRun) -> WorkflowRun:
        """Insert a new WorkflowRun. Populates run.id on return."""
        doc = run.model_dump(by_alias=True)
        if doc.get("_id") is None:
            doc.pop("_id", None)  # let MongoDB generate _id
        result = await self._collection.insert_one(doc)
        run.id = result.inserted_id
        return run

    async def save_with_version(self, run: WorkflowRun) -> WorkflowRun:
        """
        Replace the document with an optimistic version check.
        Increments doc_version. Raises ConcurrentModificationError on conflict.
        """
        current_version = run.doc_version
        run.doc_version += 1
        run.updated_at = datetime.now(UTC)

        doc = run.model_dump(by_alias=True)
        doc["_id"] = run.id  # ensure _id is set for replace

        result = await self._collection.replace_one(
            {"_id": run.id, "doc_version": current_version},
            doc,
        )

        if result.modified_count == 0:
            # Roll back version increment on our local object
            run.doc_version = current_version
            raise ConcurrentModificationError(str(run.id))

        return run

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    async def load(self, run_id: str) -> WorkflowRun:
        """Load by ObjectId string. Raises WorkflowRunNotFoundError if missing."""
        raw = await self._collection.find_one({"_id": ObjectId(run_id)})
        if raw is None:
            raise WorkflowRunNotFoundError(run_id)
        return WorkflowRun.model_validate(raw)

    # ------------------------------------------------------------------
    # Lease acquisition (atomic)
    # ------------------------------------------------------------------

    async def find_claimable(self) -> WorkflowRun | None:
        """
        Atomically find and claim one eligible WorkflowRun.
        Uses findOneAndUpdate to ensure only one runner wins.
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=self._lease_ttl)

        leasable = [s.value for s in LEASABLE_STATUSES]

        raw = await self._collection.find_one_and_update(
            {
                "status": {"$in": leasable},
                "$or": [
                    {"lease_expires_at": None},
                    {"lease_expires_at": {"$lt": now}},
                ],
            },
            {
                "$set": {
                    "lease_owner": self._owner_id,
                    "lease_expires_at": expires_at,
                    "lease_renewed_at": now,
                }
            },
            return_document=True,  # return the updated document
        )

        if raw is None:
            return None

        return WorkflowRun.model_validate(raw)

    # ------------------------------------------------------------------
    # Poll scheduling
    # ------------------------------------------------------------------

    async def find_due_polls(self) -> list[WorkflowRun]:
        """
        Return runs that have at least one AWAITING_POLL step whose
        next_poll_at is in the past, **or** a PENDING step whose retry_after
        is in the past. Lease must be expired or absent.
        """
        now = datetime.now(UTC)
        cursor = self._collection.find(
            {
                "status": WorkflowStatus.SUSPENDED.value,
                "$or": [
                    # Due poll checks
                    {
                        "steps": {
                            "$elemMatch": {
                                "status": StepStatus.AWAITING_POLL.value,
                                "next_poll_at": {"$lte": now},
                            }
                        },
                    },
                    # Due retries
                    {
                        "steps": {
                            "$elemMatch": {
                                "status": StepStatus.PENDING.value,
                                "retry_after": {"$lte": now},
                            }
                        },
                    },
                ],
                # Lease guard (separate top-level condition using $and)
                "$and": [
                    {
                        "$or": [
                            {"lease_expires_at": None},
                            {"lease_expires_at": {"$lt": now}},
                        ]
                    }
                ],
            }
        )
        docs = await cursor.to_list(length=None)
        return [WorkflowRun.model_validate(doc) for doc in docs]

    # ------------------------------------------------------------------
    # Event step resume
    # ------------------------------------------------------------------

    async def find_by_correlation_id(self, correlation_id: str) -> WorkflowRun | None:
        raw = await self._collection.find_one({"steps.resume_correlation_id": correlation_id})
        if raw is None:
            return None
        return WorkflowRun.model_validate(raw)

    # ------------------------------------------------------------------
    # Lease management
    # ------------------------------------------------------------------

    async def renew_lease(self, run_id: str, owner_id: str, ttl_seconds: int) -> bool:
        """Extend the lease. Returns False if we no longer own it."""
        now = datetime.now(UTC)
        result = await self._collection.update_one(
            {"_id": ObjectId(run_id), "lease_owner": owner_id},
            {
                "$set": {
                    "lease_expires_at": now + timedelta(seconds=ttl_seconds),
                    "lease_renewed_at": now,
                }
            },
        )
        return result.modified_count == 1

    async def release_lease(self, run_id: str, owner_id: str) -> None:
        """Clear lease fields if we still own the lease."""
        await self._collection.update_one(
            {"_id": ObjectId(run_id), "lease_owner": owner_id},
            {"$set": {"lease_owner": None, "lease_expires_at": None}},
        )

    # ------------------------------------------------------------------
    # Resume lease acquisition (atomic)
    # ------------------------------------------------------------------

    async def acquire_lease_for_resume(self, run_id, owner_id: str, lease_ttl_seconds: int) -> WorkflowRun | None:
        """
        Atomically acquire a lease on a specific WorkflowRun for the resume path.
        Unlike find_claimable(), this targets a specific run by ID regardless of status,
        but still requires the lease to be available (expired or None).
        Returns the leased run, or None if the lease could not be acquired.
        """
        now = datetime.now(UTC)
        raw = await self._collection.find_one_and_update(
            {
                "_id": run_id,
                "$or": [
                    {"lease_expires_at": None},
                    {"lease_expires_at": {"$lt": now}},
                ],
            },
            {
                "$set": {
                    "lease_owner": owner_id,
                    "lease_expires_at": now + timedelta(seconds=lease_ttl_seconds),
                    "lease_renewed_at": now,
                }
            },
            return_document=True,
        )
        if raw is None:
            return None
        return WorkflowRun.model_validate(raw)

    # ------------------------------------------------------------------
    # Change stream watcher
    # ------------------------------------------------------------------

    def watcher(self) -> WorkflowWatcher:  # noqa: F821
        """
        Create a WorkflowWatcher that listens for changes via MongoDB Change Streams.

        The watcher filters out changes made by this store's owner to avoid
        self-triggering. Requires a MongoDB replica set.

        Usage::

            watcher = store.watcher()
            async with watcher:
                async for event in watcher:
                    await runner.tick()

        Or integrated with the runner::

            await runner.start(watcher=store.watcher())
        """
        from workchain.watcher import WorkflowWatcher

        return WorkflowWatcher(self._collection, self._owner_id)

    # ------------------------------------------------------------------
    # Index helpers
    # ------------------------------------------------------------------

    async def ensure_indexes(self) -> None:
        """
        Create recommended indexes. Call once at application startup.
        Safe to call on an already-indexed collection.
        """
        await self._collection.create_index([("status", 1), ("lease_expires_at", 1)])
        await self._collection.create_index([("steps.resume_correlation_id", 1)], sparse=True)
        await self._collection.create_index([("steps.next_poll_at", 1), ("status", 1)], sparse=True)
        await self._collection.create_index([("steps.retry_after", 1), ("status", 1)], sparse=True)
        logger.info("workchain: MongoDB indexes ensured on 'workflow_runs'.")
