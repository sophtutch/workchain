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


# Statuses that indicate a workflow may still make progress (or be resumed)
ACTIVE_WORKFLOW_STATUSES = {
    WorkflowStatus.PENDING,
    WorkflowStatus.RUNNING,
    WorkflowStatus.SUSPENDED,
}

# Statuses eligible for lease acquisition by the runner
LEASABLE_STATUSES = {
    WorkflowStatus.PENDING,
    WorkflowStatus.RUNNING,
}

# A separate query handles AWAITING_POLL runs when their next_poll_at is due
POLLABLE_STATUS = WorkflowStatus.SUSPENDED


class DependencyFailurePolicy(str, Enum):
    FAIL = "fail"
    SKIP = "skip"


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

    # Optimistic concurrency
    doc_version: int = 0

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def get_step(self, step_id: str) -> StepRun | None:
        return next((s for s in self.steps if s.step_id == step_id), None)

    def is_terminal(self) -> bool:
        return self.status in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED}
