# workchain — Claude Agent Context

## What this is

`workchain` is a Python library for programmatic construction and execution of **persistent, multi-step workflows**. Steps are arranged in a DAG, state is persisted to MongoDB via `pydantic-mongo`, and distributed execution is safe via TTL leases + optimistic locking.

## Architecture

```
workchain/
├── exceptions.py     — All custom exception types
├── models.py         — Pydantic/Mongo documents: WorkflowRun, StepRun, enums
├── context.py        — JSON-safe shared runtime state between steps
├── steps.py          — Step, EventStep, PollingStep base classes + StepResult
├── workflow.py       — Workflow builder + DAG validation (Kahn's algorithm)
├── runner.py         — WorkflowRunner: DAG resolution, leasing, heartbeat thread
└── store/
    ├── base.py       — WorkflowStore protocol
    └── mongo.py      — MongoWorkflowStore (pydantic-mongo, atomic ops)
```

## Key design decisions

**Separation of static config and runtime state**
- `Workflow` / `StepDefinition` — immutable blueprint, holds step instances with config
- `WorkflowRun` / `StepRun` — mutable runtime state, persisted to MongoDB
- `Context` — JSON-serializable shared dict, persisted on `WorkflowRun.context`

**Three step types**
- `Step` — synchronous; returns `StepResult.complete(output={...})`
- `EventStep` — suspends workflow; returns `StepResult.suspend(correlation_id=...)`; resumed externally via `runner.resume(correlation_id, payload)`
- `PollingStep` — retries `check()` on an interval; returns `StepResult.poll(next_poll_at=...)`

**Distributed safety**
- Lease acquisition is a single atomic `findOneAndUpdate` — only one runner wins
- All saves use `replace_one({"_id": ..., "doc_version": N})` — raises `ConcurrentModificationError` if version mismatches
- Heartbeat thread renews lease every `ttl/2` seconds; dies with the process so leases naturally expire on crash

**Failure propagation**
- Each step has `on_dependency_failure: "fail" | "skip"`
- Propagation runs iteratively until no new dependents are affected

## Development setup

```bash
pip install -e ".[dev]"
```

## Running tests

```bash
pytest tests/
```

## Adding a new Step type

1. Subclass `Step`, `EventStep`, or `PollingStep` from `workchain.steps`
2. Define an inner `Config(BaseModel)` class with your step's fields
3. Implement `execute(context)` (and `on_resume` / `check` / `on_complete` as needed)
4. Register it in the runner's `registry` dict: `{"MyStep": MyStep}`

```python
from pydantic import BaseModel
from workchain import Step, StepResult, Context

class SendEmailConfig(BaseModel):
    to: str
    subject: str

class SendEmailStep(Step[SendEmailConfig]):
    Config = SendEmailConfig

    def execute(self, context: Context) -> StepResult:
        send_email(self.config.to, self.config.subject)
        return StepResult.complete(output={"sent": True})
```

## Building and starting a workflow

```python
from pymongo import MongoClient
from workchain import Workflow, WorkflowRunner, MongoWorkflowStore

store = MongoWorkflowStore(
    client=MongoClient("mongodb://localhost:27017"),
    database="myapp",
    owner_id="worker-1",
)
store.ensure_indexes()

registry = {"SendEmailStep": SendEmailStep, "FetchStep": FetchStep}

workflow = (
    Workflow(name="onboarding", version="1.0.0")
    .add("fetch",  FetchStep(config=FetchConfig(url="...")))
    .add("notify", SendEmailStep(config=SendEmailConfig(to="user@example.com", subject="Welcome")), depends_on=["fetch"])
)

run = workflow.create_run()
store.save(run)

runner = WorkflowRunner(store=store, registry=registry, workflow=workflow)
runner.start()  # blocking loop; call runner.tick() for single-step processing
```

## Resuming a suspended EventStep

```python
runner.resume(correlation_id="abc-123", payload={"approved": True})
```

## MongoDB index setup

Call `store.ensure_indexes()` once at application startup. Indexes are on:
- `{status, lease_expires_at}` — for efficient lease acquisition
- `{steps.resume_correlation_id}` — for fast event resume lookups
- `{steps.next_poll_at, status}` — for poll scheduling

## Conventions

- All context values must be JSON-serializable. `Context.set()` enforces this at write time.
- Step `output` dicts (stored on `StepRun`) follow the same constraint.
- `WorkflowRun.doc_version` must never be manually set — only `save_with_version()` should increment it.
- Never hold a lease longer than `lease_ttl` without the heartbeat running.
- `WorkflowRunner.instance_id` should be unique per process (defaults to `uuid4()` on startup).

## Files NOT to modify without reading SPEC.md first

- `models.py` — changing field names affects all persisted documents
- `store/mongo.py` — the lease acquisition query is carefully crafted; changes risk race conditions
- `runner.py` `_propagate_failure()` — iterative logic is intentional to handle cascading failures
