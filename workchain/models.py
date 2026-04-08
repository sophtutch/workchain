"""Domain models for the workflow engine."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _tz_safe_le(a: datetime, b: datetime) -> bool:
    """Compare two datetimes, normalising to naive UTC if tz-awareness differs.

    MongoDB drivers may return naive or aware datetimes depending on the
    driver and codec configuration.  This avoids ``TypeError`` when
    comparing across the boundary.
    """
    if (a.tzinfo is None) != (b.tzinfo is None):
        a = a.replace(tzinfo=None)
        b = b.replace(tzinfo=None)
    return a <= b


def _is_unlocked(s: Step) -> bool:
    """True if the step has no lock or its lock has expired."""
    if s.locked_by is None:
        return True
    if s.lock_expires_at is None:
        return False
    return _tz_safe_le(s.lock_expires_at, _utcnow())


def _new_id() -> str:
    return uuid.uuid4().hex  # full 32-char hex (128-bit entropy)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class StepStatus(str, Enum):
    """Lifecycle states for a workflow step.

    PENDING → SUBMITTED → RUNNING → COMPLETED or FAILED.
    Async steps go through RUNNING → BLOCKED (polling) → COMPLETED.
    SUBMITTED is a crash-safe write-ahead boundary: if the engine dies
    between SUBMITTED and RUNNING, recovery knows the handler hasn't run.
    """

    PENDING = "pending"
    SUBMITTED = "submitted"  # written to DB before execution (crash-safe boundary)
    RUNNING = "running"
    BLOCKED = "blocked"      # async step polling for completeness
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowStatus(str, Enum):
    """Lifecycle states for a workflow.

    PENDING → RUNNING → COMPLETED or FAILED.
    NEEDS_REVIEW is set when a non-idempotent step crashes without a
    verify_completion hook — manual intervention is required.
    CANCELLED is terminal and set via cancel_workflow().
    """

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

    @field_validator("interval", "backoff_multiplier", "max_interval", "timeout", "max_polls")
    @classmethod
    def _validate_non_negative(cls, v: float, info: ValidationInfo) -> float:
        if v < 0:
            raise ValueError(f"{info.field_name} must not be negative")
        return v


# ---------------------------------------------------------------------------
# Step config and result base classes
# ---------------------------------------------------------------------------

class StepConfig(BaseModel):
    """Base class for step configuration. Subclass with typed fields."""


class CheckResult(BaseModel):
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
        if v is not None:
            if math.isnan(v) or math.isinf(v):
                raise ValueError("progress must be a finite number")
            if not (0.0 <= v <= 1.0):
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
    """A single step in a workflow DAG.

    Each step has a handler (dotted path), optional typed config/result,
    retry and poll policies, dependency declarations, and per-step lock
    fields for distributed execution. The ``config_type`` and ``result_type``
    are auto-populated by ``_set_type_paths`` for MongoDB round-trip
    deserialization.
    """

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

    # Dependency graph — resolved by Workflow._resolve_and_validate_depends_on
    depends_on: list[str] | None = None   # None = sequential default, [] = root step

    # Step-level locking (for per-step distributed claims)
    locked_by: str | None = None
    lock_expires_at: datetime | None = None
    fence_token: int = 0

    is_async: bool = False                # if True, engine polls completeness_check
    completeness_check: str | None = None  # dotted path to async callable -> bool
    verify_completion: str | None = None   # used on crash recovery
    idempotent: bool = True               # safe to re-run on recovery?
    poll_policy: PollPolicy | None = None  # only needed for async steps
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
            result_cls = type(self.result)
            self.result_type = f"{result_cls.__module__}.{result_cls.__qualname__}"
        return self


# ---------------------------------------------------------------------------
# Workflow model
# ---------------------------------------------------------------------------

class Workflow(BaseModel):
    """A persistent, multi-step workflow with a dependency DAG.

    Steps declare dependencies via ``depends_on``; the validator
    ``_resolve_and_validate_depends_on`` resolves ``None`` to sequential
    ordering and checks for cycles. The workflow is the unit of
    persistence in MongoDB.
    """

    id: str = Field(default_factory=_new_id)
    name: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    steps: list[Step] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_unique_step_names(self) -> Workflow:
        names = [s.name for s in self.steps]
        if len(names) != len(set(names)):
            dupes = [n for n in names if names.count(n) > 1]
            raise ValueError(f"Step names must be unique, found duplicates: {sorted(set(dupes))}")
        return self

    @model_validator(mode="after")
    def _resolve_and_validate_depends_on(self) -> Workflow:
        """Resolve None → sequential default, then validate the dependency graph.

        - ``depends_on=None`` (the default) means "depend on the previous step",
          or ``[]`` for the first step.  This preserves backward compatibility.
        - Rejects unknown step names, self-references, and cycles.
        """
        if not self.steps:
            return self

        step_names = {s.name for s in self.steps}

        # --- Resolve sequential defaults ---
        for i, step in enumerate(self.steps):
            if step.depends_on is None:
                step.depends_on = [self.steps[i - 1].name] if i > 0 else []

        # --- Validate references (depends_on is guaranteed non-None after resolution) ---
        for step in self.steps:
            for dep in step.depends_on:  # type: ignore[union-attr]
                if dep == step.name:
                    raise ValueError(
                        f"Step '{step.name}' cannot depend on itself"
                    )
                if dep not in step_names:
                    raise ValueError(
                        f"Step '{step.name}' depends on unknown step '{dep}'"
                    )

        # --- Detect cycles via topological sort (Kahn's algorithm) ---
        in_degree: dict[str, int] = {s.name: 0 for s in self.steps}
        dependents: dict[str, list[str]] = {s.name: [] for s in self.steps}
        for step in self.steps:
            for dep in step.depends_on:  # type: ignore[union-attr]
                dependents[dep].append(step.name)
                in_degree[step.name] += 1

        queue = [name for name, deg in in_degree.items() if deg == 0]
        visited = 0
        while queue:
            node = queue.pop()
            visited += 1
            for child in dependents[node]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if visited != len(self.steps):
            raise ValueError("Dependency cycle detected among steps")

        return self

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    def is_terminal(self) -> bool:
        return self.status in (
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.NEEDS_REVIEW,
            WorkflowStatus.CANCELLED,
        )

    # --- Dependency-aware helpers ---

    def step_by_name(self, name: str) -> Step | None:
        """Look up a step by name, or ``None`` if not found."""
        for s in self.steps:
            if s.name == name:
                return s
        return None

    def ready_steps(self) -> list[Step]:
        """Return steps that are PENDING with all dependencies COMPLETED and not locked.

        A step is "ready" when:
        - Its status is PENDING
        - Every step named in ``depends_on`` has status COMPLETED
        - It is not currently locked (or its lock has expired)
        """
        by_name = {s.name: s for s in self.steps}
        ready = []
        for step in self.steps:
            if step.status != StepStatus.PENDING or not _is_unlocked(step):
                continue
            deps = step.depends_on or []
            if all(
                (dep := by_name.get(d)) is not None
                and dep.status == StepStatus.COMPLETED
                for d in deps
            ):
                ready.append(step)
        return ready

    def pollable_steps(self) -> list[Step]:
        """Return BLOCKED steps whose ``next_poll_at`` has passed and are not locked (or lock expired)."""
        now = _utcnow()
        return [
            s for s in self.steps
            if s.status == StepStatus.BLOCKED
            and _is_unlocked(s)
            and s.next_poll_at is not None
            and _tz_safe_le(s.next_poll_at, now)
        ]

    def active_steps(self) -> list[Step]:
        """Return steps currently in-flight (SUBMITTED, RUNNING, or BLOCKED)."""
        return [
            s for s in self.steps
            if s.status in (StepStatus.SUBMITTED, StepStatus.RUNNING, StepStatus.BLOCKED)
        ]

    def all_steps_terminal(self) -> bool:
        """True if every step is in a terminal state (COMPLETED or FAILED)."""
        return bool(self.steps) and all(
            s.status in (StepStatus.COMPLETED, StepStatus.FAILED)
            for s in self.steps
        )

    def all_steps_completed(self) -> bool:
        """True if every step has status COMPLETED."""
        return bool(self.steps) and all(
            s.status == StepStatus.COMPLETED for s in self.steps
        )

    def has_failed_step(self) -> bool:
        """True if any step has status FAILED."""
        return any(s.status == StepStatus.FAILED for s in self.steps)
