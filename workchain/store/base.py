"""Abstract WorkflowStore protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from workchain.models import WorkflowRun


@runtime_checkable
class WorkflowStore(Protocol):
    """
    Protocol defining the persistence interface for WorkflowRuns.

    Implementations must provide all methods below. The MongoWorkflowStore
    is the reference implementation. Alternative backends (e.g. in-memory
    for testing) should implement this protocol.
    """

    async def save(self, run: WorkflowRun) -> WorkflowRun:
        """
        Persist a new WorkflowRun (insert).
        Returns the run with its assigned id populated.
        """
        ...

    async def save_with_version(self, run: WorkflowRun) -> WorkflowRun:
        """
        Update an existing WorkflowRun using an optimistic version check.
        Increments doc_version and updates updated_at.
        Raises ConcurrentModificationError if doc_version has changed.
        """
        ...

    async def load(self, run_id: str) -> WorkflowRun:
        """
        Load a WorkflowRun by its string ObjectId.
        Raises WorkflowRunNotFoundError if not found.
        """
        ...

    async def find_claimable(self) -> WorkflowRun | None:
        """
        Atomically find and lease one WorkflowRun that is eligible for processing:
        - status in LEASABLE_STATUSES
        - lease_expires_at is None or in the past

        Returns the leased run, or None if nothing is available.
        This must be implemented as a single atomic findOneAndUpdate.
        """
        ...

    async def find_due_polls(self) -> list[WorkflowRun]:
        """
        Return WorkflowRuns with status SUSPENDED that have at least one step
        in AWAITING_POLL status whose next_poll_at <= now.
        These runs are candidates for the poll scheduler to re-activate.
        """
        ...

    async def find_by_correlation_id(self, correlation_id: str) -> WorkflowRun | None:
        """
        Find a WorkflowRun containing a suspended step with the given
        resume_correlation_id.
        """
        ...

    async def renew_lease(self, run_id: str, owner_id: str, ttl_seconds: int) -> bool:
        """
        Extend the lease for the given run if owner_id still holds it.
        Returns True if renewed, False if the lease was lost.
        """
        ...

    async def release_lease(self, run_id: str, owner_id: str) -> None:
        """Clear the lease fields. Only takes effect if owner_id matches."""
        ...

    async def acquire_lease_for_resume(self, run_id, owner_id: str, lease_ttl_seconds: int) -> WorkflowRun | None:
        """
        Atomically acquire a lease on a specific WorkflowRun for the resume path.
        Unlike find_claimable(), this targets a specific run by ID regardless of status,
        but still requires the lease to be available (expired or None).
        Returns the leased run, or None if the lease could not be acquired.
        """
        ...
