"""Domain models for the workflow engine."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex  # full 32-char hex (128-bit entropy)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class StepStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"  # written to DB before execution (crash-safe boundary)
    RUNNING = "running"
    BLOCKED = "blocked"      # async step polling for completeness
    COMPLETED = "completed"
    FAILED = "failed"

class WorkflowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"  # non-idempotent step crashed without verify hook
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------

class RetryPolicy(BaseModel):
    max_attempts: int = 3
    wait_seconds: float = 1.0
    wait_multiplier: float = 2.0   # exponential backoff
    wait_max: float = 60.0


class PollPolicy(BaseModel):
    """Configurable polling behavior for async steps."""
    interval: float = 5.0              # initial poll interval in seconds
    backoff_multiplier: float = 1.0    # 1.0 = fixed interval, 2.0 = exponential
    max_interval: float = 60.0         # ceiling for backoff
    timeout: float = 3600.0            # max total seconds before poll failure (0 = no timeout)
    max_polls: int = 0                 # max poll attempts before failure (0 = unlimited)


# ---------------------------------------------------------------------------
# Step config and result base classes
# ---------------------------------------------------------------------------

class StepConfig(BaseModel):
    """Base class for step configuration. Subclass with typed fields."""


class PollHint(BaseModel):
    """
    Optional return type from completeness_check.
    Instead of returning a plain bool, the check can return a dict
    matching this shape to give the engine scheduling hints.
    """
    complete: bool = False
    retry_after: float | None = None   # override next poll interval (seconds)
    progress: float | None = None      # 0.0-1.0, for logging/dashboards
    message: str | None = None         # human-readable status

    @field_validator("progress")
    @classmethod
    def _clamp_progress(cls, v: float | None) -> float | None:
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError("progress must be between 0.0 and 1.0")
        return v


class StepResult(BaseModel):
    """Base class for step results. Subclass with typed fields."""
    error: str | None = None
    completed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Step model
# ---------------------------------------------------------------------------

class Step(BaseModel):
    name: str
    handler: str                          # dotted path to callable, e.g. "myapp.steps.validate"
    config: StepConfig | None = None
    config_type: str | None = None        # dotted path to StepConfig subclass
    status: StepStatus = StepStatus.PENDING
    result: StepResult | None = None
    result_type: str | None = None        # dotted path to StepResult subclass
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    step_timeout: float = 0  # per-attempt timeout in seconds (0 = no timeout)
    attempt: int = 0

    @field_validator("step_timeout")
    @classmethod
    def _validate_timeout(cls, v: float) -> float:
        if v < 0:
            raise ValueError("step_timeout must not be negative")
        return v

    is_async: bool = False                # if True, engine polls completeness_check
    completeness_check: str | None = None  # dotted path to async callable -> bool
    verify_completion: str | None = None   # used on crash recovery
    idempotent: bool = True               # safe to re-run on recovery?
    poll_policy: PollPolicy = Field(default_factory=PollPolicy)
    poll_count: int = 0
    poll_started_at: datetime | None = None   # when polling began (for timeout calc)
    next_poll_at: datetime | None = None      # when this step is next eligible for a poll claim
    last_poll_at: datetime | None = None
    current_poll_interval: float | None = None  # tracks backoff progression across claims
    last_poll_progress: float | None = None   # last reported progress 0.0-1.0
    last_poll_message: str | None = None      # last reported status message

    @model_validator(mode="after")
    def _set_type_paths(self) -> Step:
        if self.config_type is None and self.config is not None and type(self.config) is not StepConfig:
            cls = type(self.config)
            self.config_type = f"{cls.__module__}.{cls.__qualname__}"
        if self.result_type is None and self.result is not None and type(self.result) is not StepResult:
            cls = type(self.result)
            self.result_type = f"{cls.__module__}.{cls.__qualname__}"
        return self


# ---------------------------------------------------------------------------
# Workflow model
# ---------------------------------------------------------------------------

class Workflow(BaseModel):
    id: str = Field(default_factory=_new_id)
    name: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    steps: list[Step] = Field(default_factory=list)
    current_step_index: int = 0

    # Distributed locking (MongoDB-managed)
    locked_by: str | None = None
    lock_expires_at: datetime | None = None
    fence_token: int = 0

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def is_terminal(self) -> bool:
        return self.status in (
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.NEEDS_REVIEW,
            WorkflowStatus.CANCELLED,
        )
