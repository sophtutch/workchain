"""Shared fixtures and sample step implementations for workchain tests."""

from __future__ import annotations

import pytest
from mongomock_motor import AsyncMongoMockClient

from workchain.decorators import _STEP_REGISTRY, async_step, step
from workchain.engine import WorkflowEngine
from workchain.models import (
    PollHint,
    PollPolicy,
    RetryPolicy,
    Step,
    StepConfig,
    StepResult,
    Workflow,
)
from workchain.store import MongoWorkflowStore

# ---------------------------------------------------------------------------
# Sample config / result types
# ---------------------------------------------------------------------------


class GreetConfig(StepConfig):
    name: str


class GreetResult(StepResult):
    greeting: str


class SubmitResult(StepResult):
    job_id: str


# ---------------------------------------------------------------------------
# Sample step handlers (registered via decorators)
# ---------------------------------------------------------------------------

_FLAKY_COUNTER: dict[str, int] = {}


@step(name="tests.greet")
async def greet_handler(config: GreetConfig, _results: dict[str, StepResult]) -> GreetResult:
    return GreetResult(greeting=f"Hello, {config.name}!")


@step(name="tests.noop")
async def noop_handler(_config: StepConfig, _results: dict[str, StepResult]) -> StepResult:
    return StepResult()


@step(name="tests.fail_always")
async def fail_handler(_config: StepConfig, _results: dict[str, StepResult]) -> StepResult:
    raise RuntimeError("intentional failure")


@step(name="tests.flaky", retry=RetryPolicy(max_attempts=3, wait_seconds=0.01, wait_multiplier=1.0))
async def flaky_handler(_config: StepConfig, _results: dict[str, StepResult]) -> StepResult:
    """Fails on first call, succeeds on second."""
    key = "flaky"
    _FLAKY_COUNTER.setdefault(key, 0)
    _FLAKY_COUNTER[key] += 1
    if _FLAKY_COUNTER[key] <= 1:
        raise RuntimeError(f"flaky failure #{_FLAKY_COUNTER[key]}")
    return StepResult()


@async_step(
    name="tests.async_submit",
    completeness_check="tests.check_complete",
    poll=PollPolicy(interval=0.05, timeout=5.0, max_polls=10),
)
async def async_submit_handler(_config: StepConfig, _results: dict[str, StepResult]) -> SubmitResult:
    return SubmitResult(job_id="job_123")


_POLL_COUNTER: dict[str, int] = {}


async def _check_complete_impl(
    _config: StepConfig, _results: dict[str, StepResult], _result: StepResult
) -> PollHint:
    key = "poll"
    _POLL_COUNTER.setdefault(key, 0)
    _POLL_COUNTER[key] += 1
    if _POLL_COUNTER[key] >= 2:
        return PollHint(complete=True, progress=1.0)
    return PollHint(complete=False, progress=0.5, message="in progress")


# Register the completeness check manually
_STEP_REGISTRY["tests.check_complete"] = _check_complete_impl


async def check_complete_always_done(
    _config: StepConfig, _results: dict[str, StepResult], _result: StepResult
) -> bool:
    return True


_STEP_REGISTRY["tests.check_always_done"] = check_complete_always_done


async def verify_done(
    _config: StepConfig, _results: dict[str, StepResult], _result: StepResult
) -> bool:
    return True


_STEP_REGISTRY["tests.verify_done"] = verify_done


async def verify_not_done(
    _config: StepConfig, _results: dict[str, StepResult], _result: StepResult
) -> bool:
    return False


_STEP_REGISTRY["tests.verify_not_done"] = verify_not_done


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mongo_db():
    client = AsyncMongoMockClient()
    return client["test_workchain"]


@pytest.fixture
def store(mongo_db):
    return MongoWorkflowStore(mongo_db, lock_ttl_seconds=5)


@pytest.fixture
def engine(store):
    return WorkflowEngine(
        store,
        instance_id="test-engine-001",
        claim_interval=0.05,
        heartbeat_interval=0.05,
        sweep_interval=0.1,
        step_stuck_seconds=1.0,
        max_concurrent=5,
    )


@pytest.fixture
def sample_workflow():
    """A simple 2-step sync workflow."""
    return Workflow(
        name="test_workflow",
        steps=[
            Step(
                name="greet",
                handler="tests.greet",
                config=GreetConfig(name="World"),
            ),
            Step(
                name="noop",
                handler="tests.noop",
            ),
        ],
    )


@pytest.fixture
def async_workflow():
    """A workflow with 1 sync step + 1 async step."""
    return Workflow(
        name="test_async_workflow",
        steps=[
            Step(
                name="noop",
                handler="tests.noop",
            ),
            Step(
                name="async_submit",
                handler="tests.async_submit",
                is_async=True,
                completeness_check="tests.check_complete",
                poll_policy=PollPolicy(interval=0.05, timeout=5.0, max_polls=10),
            ),
        ],
    )


@pytest.fixture(autouse=True)
def reset_counters():
    """Reset global mutable counters before each test."""
    _FLAKY_COUNTER.clear()
    _POLL_COUNTER.clear()
