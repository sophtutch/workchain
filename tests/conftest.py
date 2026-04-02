"""Shared fixtures and sample step implementations for workchain tests."""

from __future__ import annotations

import pytest
from mongomock_motor import AsyncMongoMockClient

from workchain.decorators import async_step, completeness_check, step
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
_POLL_COUNTER: dict[str, int] = {}


@step()
async def greet_handler(config: GreetConfig, _results: dict[str, StepResult]) -> GreetResult:
    return GreetResult(greeting=f"Hello, {config.name}!")


@step()
async def noop_handler(_config: StepConfig, _results: dict[str, StepResult]) -> StepResult:
    return StepResult()


@step()
async def fail_handler(_config: StepConfig, _results: dict[str, StepResult]) -> StepResult:
    raise RuntimeError("intentional failure")


@step(retry=RetryPolicy(max_attempts=3, wait_seconds=0.01, wait_multiplier=1.0))
async def flaky_handler(_config: StepConfig, _results: dict[str, StepResult]) -> StepResult:
    """Fails on first call, succeeds on second."""
    key = "flaky"
    _FLAKY_COUNTER.setdefault(key, 0)
    _FLAKY_COUNTER[key] += 1
    if _FLAKY_COUNTER[key] <= 1:
        raise RuntimeError(f"flaky failure #{_FLAKY_COUNTER[key]}")
    return StepResult()


@completeness_check()
async def _check_complete_impl(
    _config: StepConfig, _results: dict[str, StepResult], _result: StepResult
) -> PollHint:
    key = "poll"
    _POLL_COUNTER.setdefault(key, 0)
    _POLL_COUNTER[key] += 1
    if _POLL_COUNTER[key] >= 2:
        return PollHint(complete=True, progress=1.0)
    return PollHint(complete=False, progress=0.5, message="in progress")


@async_step(
    completeness_check=_check_complete_impl._step_meta["handler"],
    poll=PollPolicy(interval=0.05, timeout=5.0, max_polls=10),
)
async def async_submit_handler(_config: StepConfig, _results: dict[str, StepResult]) -> SubmitResult:
    return SubmitResult(job_id="job_123")


@completeness_check()
async def check_complete_always_done(
    _config: StepConfig, _results: dict[str, StepResult], _result: StepResult
) -> bool:
    return True


@completeness_check()
async def verify_done(
    _config: StepConfig, _results: dict[str, StepResult], _result: StepResult
) -> bool:
    return True


@completeness_check()
async def verify_not_done(
    _config: StepConfig, _results: dict[str, StepResult], _result: StepResult
) -> bool:
    return False


# ---------------------------------------------------------------------------
# Context-aware sample handlers (3-arg step, 4-arg completeness check)
# ---------------------------------------------------------------------------


@step(needs_context=True)
async def greet_ctx_handler(
    config: GreetConfig, _results: dict[str, StepResult], ctx: dict[str, object]
) -> GreetResult:
    """Step handler that uses engine context."""
    prefix = ctx.get("greeting_prefix", "Hello")
    return GreetResult(greeting=f"{prefix}, {config.name}!")


@completeness_check(needs_context=True)
async def check_complete_ctx(
    _config: StepConfig,
    _results: dict[str, StepResult],
    _result: StepResult,
    ctx: dict[str, object],
) -> PollHint:
    """Completeness check that uses engine context."""
    threshold = ctx.get("complete_threshold", 2)
    key = "poll_ctx"
    _POLL_COUNTER.setdefault(key, 0)
    _POLL_COUNTER[key] += 1
    if _POLL_COUNTER[key] >= threshold:
        return PollHint(complete=True, progress=1.0)
    return PollHint(complete=False, progress=0.5)


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
                handler=greet_handler._step_meta["handler"],
                config=GreetConfig(name="World"),
            ),
            Step(
                name="noop",
                handler=noop_handler._step_meta["handler"],
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
                handler=noop_handler._step_meta["handler"],
            ),
            Step(
                name="async_submit",
                handler=async_submit_handler._step_meta["handler"],
                is_async=True,
                completeness_check=_check_complete_impl._step_meta["handler"],
                poll_policy=PollPolicy(interval=0.05, timeout=5.0, max_polls=10),
            ),
        ],
    )


@pytest.fixture(autouse=True)
def reset_counters():
    """Reset global mutable counters before each test."""
    _FLAKY_COUNTER.clear()
    _POLL_COUNTER.clear()
