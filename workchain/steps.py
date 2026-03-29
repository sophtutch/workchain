"""Step base classes for workchain."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from workchain.context import Context

# ---------------------------------------------------------------------------
# StepResult
# ---------------------------------------------------------------------------


class StepOutcome(str, Enum):
    COMPLETED = "completed"
    SUSPEND = "suspend"
    POLL = "poll"
    FAILED = "failed"


class StepResult(BaseModel):
    """Return value from Step.execute()."""

    outcome: StepOutcome
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    correlation_id: str | None = None
    next_poll_at: datetime | None = None

    # Convenience constructors

    @classmethod
    def complete(cls, output: dict[str, Any] | None = None) -> StepResult:
        return cls(outcome=StepOutcome.COMPLETED, output=output or {})

    @classmethod
    def suspend(cls, correlation_id: str) -> StepResult:
        """Suspend the workflow until resume() is called with this correlation_id."""
        return cls(outcome=StepOutcome.SUSPEND, correlation_id=correlation_id)

    @classmethod
    def poll(cls, next_poll_at: datetime | None = None) -> StepResult:
        """Reschedule a poll check.

        If *next_poll_at* is omitted the runner derives the next poll time
        from the step's ``poll_interval_seconds``.
        """
        return cls(outcome=StepOutcome.POLL, next_poll_at=next_poll_at)

    @classmethod
    def fail(cls, error: str) -> StepResult:
        return cls(outcome=StepOutcome.FAILED, error=error)

    def __repr__(self) -> str:
        return f"StepResult(outcome={self.outcome.value})"


# ---------------------------------------------------------------------------
# Config type variable
# ---------------------------------------------------------------------------

ConfigT = TypeVar("ConfigT", bound=BaseModel)


# ---------------------------------------------------------------------------
# Step base classes
# ---------------------------------------------------------------------------


class Step(ABC, Generic[ConfigT]):
    """
    Base class for all synchronous steps.

    Subclasses must:
    - Define a nested `Config` class (a Pydantic BaseModel)
    - Implement `execute(context) -> StepResult`
    """

    Config: type[BaseModel] = BaseModel  # override in subclasses

    def __init__(self, config: ConfigT | None = None) -> None:
        self.config: ConfigT = config  # type: ignore[assignment]

    @abstractmethod
    def execute(self, context: Context) -> StepResult:
        """Execute the step. Must return a StepResult."""
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(config={self.config!r})"


class EventStep(Step[ConfigT]):
    """
    A step that suspends the workflow and waits for an external signal.

    The runner will:
    1. Call execute(), which should return StepResult.suspend(correlation_id)
    2. Persist the suspended state with the correlation_id
    3. When resume(correlation_id, payload) is called externally, invoke on_resume()
       then mark the step as COMPLETED.
    """

    @abstractmethod
    def execute(self, context: Context) -> StepResult:
        """
        Should return StepResult.suspend(correlation_id=...).
        Generate and return a stable correlation_id so callers know how to resume.
        """
        ...

    def on_resume(self, payload: dict[str, Any], context: Context) -> dict[str, Any]:
        """
        Called when the workflow is resumed via runner.resume(correlation_id, payload).
        Write any results into context as needed.
        Return a dict to be stored as the step's output.
        """
        return {}


class PollingStep(Step[ConfigT]):
    """
    A step that repeatedly checks a condition until it is satisfied.

    The runner will:
    1. Call execute() on first entry, which should return StepResult.poll(next_poll_at)
    2. At next_poll_at, call check(). If True, call on_complete() and mark COMPLETED.
    3. If False (and not timed out), return another StepResult.poll(next_poll_at).
    4. If timed out, mark the step as FAILED.
    """

    poll_interval_seconds: int = 30
    timeout_seconds: int | None = None

    def __init__(self, config: ConfigT | None = None) -> None:
        super().__init__(config)
        if self.poll_interval_seconds <= 0:
            raise ValueError(
                f"poll_interval_seconds must be positive, got {self.poll_interval_seconds}"
            )

    def execute(self, context: Context) -> StepResult:
        """Default implementation schedules the first poll.

        Returns ``StepResult.poll()`` without an explicit *next_poll_at*;
        the runner derives timing from ``poll_interval_seconds``.
        """
        return StepResult.poll()

    @abstractmethod
    def check(self, context: Context) -> bool:
        """Return True when the condition is met and the step should complete."""
        ...

    def on_complete(self, context: Context) -> dict[str, Any]:
        """
        Called when check() returns True.
        Return a dict to be stored as the step's output.
        """
        return {}
