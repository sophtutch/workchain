"""Shared fixtures for workchain tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from bson import ObjectId
from pydantic import BaseModel

from workchain import (
    Context,
    EventStep,
    PollingStep,
    Step,
    StepResult,
    WorkflowRun,
)
from workchain.exceptions import ConcurrentModificationError, WorkflowRunNotFoundError

# ---------------------------------------------------------------------------
# Sample step implementations for testing
# ---------------------------------------------------------------------------


class AddConfig(BaseModel):
    a: int
    b: int


class AddStep(Step[AddConfig]):
    Config = AddConfig

    def execute(self, context: Context) -> StepResult:
        result = self.config.a + self.config.b
        return StepResult.complete(output={"sum": result})


class NoOpStep(Step):
    def execute(self, context: Context) -> StepResult:
        return StepResult.complete(output={"done": True})


class FailingStep(Step):
    def execute(self, context: Context) -> StepResult:
        return StepResult.fail(error="intentional failure")


class ExplodingStep(Step):
    """Step that raises an exception."""

    def execute(self, context: Context) -> StepResult:
        raise RuntimeError("boom")


class SuspendStep(EventStep):
    def execute(self, context: Context) -> StepResult:
        return StepResult.suspend(correlation_id="test-correlation-123")

    def on_resume(self, payload: dict[str, Any], context: Context) -> dict[str, Any]:
        context.set("resumed_with", payload)
        return {"approved": payload.get("approved", False)}


class CountingPollStep(PollingStep):
    """PollingStep that completes after a set number of checks."""

    poll_interval_seconds = 1
    timeout_seconds = None

    def __init__(self, config=None, *, checks_until_done: int = 1):
        super().__init__(config=config)
        self._checks_until_done = checks_until_done
        self._check_count = 0

    def check(self, context: Context) -> bool:
        self._check_count += 1
        return self._check_count >= self._checks_until_done

    def on_complete(self, context: Context) -> dict[str, Any]:
        return {"checks": self._check_count}


class TimeoutPollStep(PollingStep):
    poll_interval_seconds = 1
    timeout_seconds = 0  # immediate timeout

    def check(self, context: Context) -> bool:
        return False


class ContextReaderStep(Step):
    """Reads upstream step output from context."""

    def execute(self, context: Context) -> StepResult:
        upstream = context.step_output("upstream")
        return StepResult.complete(output={"read_value": upstream.get("sum")})


class FlakeyStep(Step):
    """Fails a configurable number of times then succeeds."""

    def __init__(self, config=None, *, fail_count: int = 2):
        super().__init__(config=config)
        self._fail_count = fail_count
        self._call_count = 0

    def execute(self, context: Context) -> StepResult:
        self._call_count += 1
        if self._call_count <= self._fail_count:
            return StepResult.fail(error=f"flakey failure {self._call_count}")
        return StepResult.complete(output={"attempts": self._call_count})


class SlowStep(Step):
    """Step that sleeps for a given duration (for timeout testing)."""

    def __init__(self, config=None, *, duration: float = 5.0):
        super().__init__(config=config)
        self._duration = duration

    def execute(self, context: Context) -> StepResult:
        import time

        time.sleep(self._duration)
        return StepResult.complete(output={"slept": self._duration})


class ExplodingResumeStep(EventStep):
    """EventStep whose on_resume() always raises."""

    def execute(self, context: Context) -> StepResult:
        return StepResult.suspend(correlation_id="exploding-resume-123")

    def on_resume(self, payload: dict[str, Any], context: Context) -> dict[str, Any]:
        raise RuntimeError("on_resume exploded")


class ExplodingCompletePollStep(PollingStep):
    """PollingStep whose on_complete() always raises."""

    poll_interval_seconds = 1
    timeout_seconds = None

    def check(self, context: Context) -> bool:
        return True

    def on_complete(self, context: Context) -> dict[str, Any]:
        raise RuntimeError("on_complete exploded")


# ---------------------------------------------------------------------------
# In-memory WorkflowStore for runner unit tests (async)
# ---------------------------------------------------------------------------


class InMemoryWorkflowStore:
    """Minimal async in-memory store implementing the WorkflowStore protocol."""

    def __init__(self, owner_id: str = "test-runner", lease_ttl_seconds: int = 30):
        self._runs: dict[str, WorkflowRun] = {}
        self._owner_id = owner_id
        self._lease_ttl = lease_ttl_seconds

    async def save(self, run: WorkflowRun) -> WorkflowRun:
        if run.id is None:
            run.id = ObjectId()
        self._runs[str(run.id)] = run
        return run

    async def save_with_version(self, run: WorkflowRun) -> WorkflowRun:
        key = str(run.id)
        stored = self._runs.get(key)
        if stored is None or stored.doc_version != run.doc_version:
            raise ConcurrentModificationError(key)
        run.doc_version += 1
        run.updated_at = datetime.now(UTC)
        self._runs[key] = run
        return run

    async def load(self, run_id: str) -> WorkflowRun:
        run = self._runs.get(run_id)
        if run is None:
            raise WorkflowRunNotFoundError(run_id)
        return run

    async def find_actionable(self) -> WorkflowRun | None:
        now = datetime.now(UTC)
        candidates = [
            run for run in self._runs.values()
            if run.needs_work_after is not None
            and run.needs_work_after <= now
            and (run.lease_expires_at is None or run.lease_expires_at < now)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda r: r.needs_work_after)
        run = candidates[0]
        run.lease_owner = self._owner_id
        run.lease_expires_at = now + timedelta(seconds=self._lease_ttl)
        run.lease_renewed_at = now
        return run

    async def find_by_correlation_id(self, correlation_id: str) -> WorkflowRun | None:
        for run in self._runs.values():
            for step in run.steps:
                if step.resume_correlation_id == correlation_id:
                    return run
        return None

    async def renew_lease(self, run_id: str, owner_id: str, ttl_seconds: int) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.lease_owner != owner_id:
            return False
        run.lease_expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        run.lease_renewed_at = datetime.now(UTC)
        return True

    async def release_lease(self, run_id: str, owner_id: str) -> None:
        run = self._runs.get(run_id)
        if run is not None and run.lease_owner == owner_id:
            run.lease_owner = None
            run.lease_expires_at = None

    async def acquire_lease_for_resume(self, run_id, owner_id: str, lease_ttl_seconds: int) -> WorkflowRun | None:
        run = self._runs.get(str(run_id))
        if run is None:
            return None
        now = datetime.now(UTC)
        if run.lease_expires_at is not None and run.lease_expires_at >= now:
            return None
        run.lease_owner = owner_id
        run.lease_expires_at = now + timedelta(seconds=lease_ttl_seconds)
        run.lease_renewed_at = now
        return run


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def in_memory_store():
    return InMemoryWorkflowStore()


@pytest.fixture
def step_registry():
    return {
        "AddStep": AddStep,
        "NoOpStep": NoOpStep,
        "FailingStep": FailingStep,
        "ExplodingStep": ExplodingStep,
        "SuspendStep": SuspendStep,
        "CountingPollStep": CountingPollStep,
        "TimeoutPollStep": TimeoutPollStep,
        "ContextReaderStep": ContextReaderStep,
        "FlakeyStep": FlakeyStep,
        "SlowStep": SlowStep,
        "ExplodingResumeStep": ExplodingResumeStep,
        "ExplodingCompletePollStep": ExplodingCompletePollStep,
    }
