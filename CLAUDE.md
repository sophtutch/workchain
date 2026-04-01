# workchain — Claude Agent Context

## What this is

`workchain` is a Python library for programmatic construction and execution of **persistent, multi-step workflows**. Steps execute sequentially, state is persisted to MongoDB via `motor`, and distributed execution is safe via TTL-based locks + fence tokens (optimistic locking).

## Architecture

```
workchain/
├── models.py       — Pydantic models: Workflow, Step, StepConfig, StepResult, enums, policies
├── decorators.py   — @step / @async_step decorators + handler registry
├── engine.py       — WorkflowEngine: claim loop, heartbeat, sweep, execution
├── store.py        — MongoWorkflowStore: persistence, distributed locking, typed deserialization
└── retry.py        — Retry utilities wrapping tenacity with RetryPolicy
```

## Key design decisions

**Strongly typed config and results**
- `StepConfig` and `StepResult` are Pydantic base classes — subclass with typed fields
- Config type is stored as `config_type` (dotted path) on each step for MongoDB round-trip
- Result type is stored as `result_type` for the same reason
- The store resolves these paths at read time via `_doc_to_workflow()`, so handlers receive properly typed objects
- Handlers access preceding step results via `results: dict[str, StepResult]`, using `cast()` for per-key types

**Sequential step execution**
- Steps execute in order (`current_step_index` advances linearly)
- No DAG — steps are a flat list

**Two step modes**
- **Sync steps** (`@step`): execute handler, persist result, advance immediately
- **Async steps** (`@async_step`): submit work, set BLOCKED, release lock, poll `completeness_check` on subsequent claims until complete

**Distributed safety**
- Lock acquisition via atomic `findOneAndUpdate` — only one instance wins
- `fence_token` increments on each claim; all writes are fenced (`{"fence_token": N}`)
- Heartbeat loop renews lock TTL; stale locks expire and are reclaimed
- Sweep loop detects anomalies (stuck steps, stale locks, unadvanced completed steps)

**Crash-safe state machine**
- Before execution: step is marked SUBMITTED (write-ahead)
- On recovery: `verify_completion` / `completeness_check` / idempotent re-run / NEEDS_REVIEW
- Each retry attempt is persisted to MongoDB before execution

**Claim-poll-release cycle (async steps)**
1. Claim workflow, execute handler (submission), set BLOCKED, schedule `next_poll_at`, release lock
2. Fast sweep rediscovers workflow when `next_poll_at` passes
3. Claim, run one `completeness_check`, if not done → schedule next poll, release lock
4. Repeat until complete or timeout/max_polls exceeded

## Development setup

```bash
pip install -e ".[dev]"
```

## Running tests

```bash
pytest tests/
```

## Defining steps

Use the `@step` and `@async_step` decorators. Config and result types are Pydantic models extending `StepConfig` and `StepResult`.

```python
from typing import cast
from workchain import StepConfig, StepResult, step, async_step, PollPolicy

class ValidateConfig(StepConfig):
    email: str

class ValidateResult(StepResult):
    validated: bool
    email: str

@step(name="validate_input")
async def validate_input(config: ValidateConfig, results: dict[str, StepResult]) -> ValidateResult:
    if "@" not in config.email:
        raise ValueError(f"Invalid email: {config.email}")
    return ValidateResult(validated=True, email=config.email)

class ProvisionResult(StepResult):
    job_id: str

async def check_provisioning(config: StepConfig, results: dict[str, StepResult], result: ProvisionResult) -> dict:
    return {"complete": False, "progress": 0.5, "message": "In progress"}

@async_step(
    name="provision",
    completeness_check=check_provisioning,
    poll=PollPolicy(interval=5.0, timeout=300.0),
)
async def provision(config: StepConfig, results: dict[str, StepResult]) -> ProvisionResult:
    validate = cast(ValidateResult, results["validate_input"])
    return ProvisionResult(job_id=f"job_{validate.email}")
```

## Building and running a workflow

```python
from motor.motor_asyncio import AsyncIOMotorClient
from workchain import Workflow, Step, WorkflowEngine, MongoWorkflowStore

client = AsyncIOMotorClient("mongodb://localhost:27017")
store = MongoWorkflowStore(client["myapp"], lock_ttl_seconds=30)

workflow = Workflow(
    name="onboarding",
    steps=[
        Step(name="validate", handler="validate_input",
             config=ValidateConfig(email="a@b.com")),
        Step(name="provision", handler="provision", is_async=True,
             completeness_check="myapp.steps.check_provisioning"),
    ],
)

await store.insert(workflow)
engine = WorkflowEngine(store)
await engine.start()   # runs claim loop, heartbeat, sweep
# ...
await engine.stop()    # graceful shutdown, releases all locks
```

## Conventions

- Step result and config fields must be JSON-serializable.
- `fence_token` is managed by the store — never set it manually.
- `WorkflowEngine.instance_id` should be unique per process (auto-generated if omitted).
- Step handlers must be async functions registered via decorators or importable by dotted path.
- `PollHint.progress` must be between 0.0 and 1.0.
- Config models extend `StepConfig`, result models extend `StepResult`.
- Use `cast()` when accessing specific result types from the `results` dict.

## Files to modify with care

- `store.py` — the lock acquisition query, fence-guarded writes, and `_doc_to_workflow` deserialization are carefully crafted; changes risk race conditions or type resolution failures
- `engine.py` `_recover_step()` — recovery logic handles multiple crash scenarios; understand all paths before changing
- `models.py` — changing field names affects all persisted MongoDB documents; `Step._set_type_paths` auto-populates `config_type`/`result_type`
