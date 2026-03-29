"""Persistent data models for workchain."""

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_mongo import PydanticObjectId


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SUSPENDED = "suspended"  # EventStep waiting for external signal
    AWAITING_POLL = "awaiting_poll"  # PollingStep waiting for next check
    SKIPPED = "skipped"


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SUSPENDED = "suspended"


class DependencyFailurePolicy(str, Enum):
    FAIL = "fail"
    SKIP = "skip"


_ABSOLUTE_MAX_DELAY_SECONDS: float = 86_400.0  # 24 hours


class RetryPolicy(BaseModel):
    """Configurable retry behaviour for a step.

    Attributes:
        max_retries: Maximum number of retry attempts (0 = no retries).
        delay_seconds: Initial delay before the first retry.
        backoff_multiplier: Multiplier applied to delay after each attempt.
            1.0 = fixed delay, 2.0 = exponential backoff.
        max_delay_seconds: Upper bound on the computed delay. None = unlimited.
    """

    max_retries: int = 0
    delay_seconds: float = 0
    backoff_multiplier: float = 1.0
    max_delay_seconds: float | None = None

    def compute_delay(self, attempt: int) -> float:
        """Return the delay in seconds before retry number *attempt* (1-based)."""
        delay = self.delay_seconds * (self.backoff_multiplier ** (attempt - 1))
        if self.max_delay_seconds is not None:
            delay = min(delay, self.max_delay_seconds)
        return min(delay, _ABSOLUTE_MAX_DELAY_SECONDS)


class StepRun(BaseModel):
    """Runtime state of a single step within a WorkflowRun."""

    step_id: str
    step_type: str
    depends_on: list[str] = Field(default_factory=list)
    on_dependency_failure: DependencyFailurePolicy = DependencyFailurePolicy.FAIL

    status: StepStatus = StepStatus.PENDING
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

    # EventStep fields
    resume_correlation_id: str | None = None

    # PollingStep fields
    next_poll_at: datetime | None = None
    poll_started_at: datetime | None = None
    last_polled_at: datetime | None = None

    # Retry fields
    retry_count: int = 0
    retry_after: datetime | None = None

    # Timestamps
    started_at: datetime | None = None
    completed_at: datetime | None = None


class WorkflowRun(BaseModel):
    """Persisted MongoDB document representing a single workflow execution."""

    model_config = ConfigDict(populate_by_name=True)

    id: PydanticObjectId | None = Field(default=None, alias="_id")

    workflow_name: str
    workflow_version: str

    status: WorkflowStatus = WorkflowStatus.PENDING
    steps: list[StepRun] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)

    # Distributed lease
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    lease_renewed_at: datetime | None = None

    # Work scheduling — materialized for query efficiency
    needs_work_after: datetime | None = None

    # Optimistic concurrency
    doc_version: int = 0

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def get_step(self, step_id: str) -> StepRun | None:
        return next((s for s in self.steps if s.step_id == step_id), None)

    def is_terminal(self) -> bool:
        return self.status in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED}

    def compute_status(self) -> WorkflowStatus:
        """Derive workflow status from step states.

        Priority order:
        1. Any RUNNING step → RUNNING
        2. Any PENDING step that is ready (deps met, no future retry_after) → RUNNING
        3. Any waiting step (SUSPENDED, AWAITING_POLL, PENDING) → SUSPENDED
        4. All terminal: FAILED if any failed, else COMPLETED
        """
        statuses = {s.status for s in self.steps}

        if StepStatus.RUNNING in statuses:
            return WorkflowStatus.RUNNING

        if StepStatus.PENDING in statuses:
            now = datetime.now(UTC).replace(tzinfo=None)
            completed_ids = {s.step_id for s in self.steps if s.status == StepStatus.COMPLETED}
            for s in self.steps:
                if s.status != StepStatus.PENDING:
                    continue
                if not set(s.depends_on).issubset(completed_ids):
                    continue
                if s.retry_after is not None and s.retry_after.replace(tzinfo=None) > now:
                    continue
                return WorkflowStatus.RUNNING

        waiting = {StepStatus.SUSPENDED, StepStatus.AWAITING_POLL, StepStatus.PENDING}
        if statuses & waiting:
            return WorkflowStatus.SUSPENDED

        if StepStatus.FAILED in statuses:
            return WorkflowStatus.FAILED
        return WorkflowStatus.COMPLETED

    def compute_needs_work_after(self) -> datetime | None:
        """Return the earliest time this run may have actionable work, or None.

        Returns ``None`` when the run is terminal or only waiting on external
        events (suspended EventSteps).  A past or present datetime means work
        is available now.  A future datetime means scheduled work (poll check
        due, retry due).
        """
        if self.is_terminal():
            return None

        completed_ids = {s.step_id for s in self.steps if s.status == StepStatus.COMPLETED}
        now = datetime.now(UTC)
        earliest: datetime | None = None

        for s in self.steps:
            candidate: datetime | None = None

            if s.status == StepStatus.RUNNING:
                candidate = now
            elif s.status == StepStatus.PENDING:
                if set(s.depends_on).issubset(completed_ids):
                    candidate = s.retry_after if s.retry_after is not None else now
            elif s.status == StepStatus.AWAITING_POLL:
                candidate = s.next_poll_at if s.next_poll_at is not None else now

            if candidate is not None:
                earliest = min(earliest, candidate) if earliest is not None else candidate

        return earliest

    def recompute_status(self) -> None:
        """Recompute ``status`` and ``needs_work_after`` from step states."""
        self.status = self.compute_status()
        self.needs_work_after = self.compute_needs_work_after()
