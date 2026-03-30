# workchain

Programmatic construction and execution of persistent, multi-step workflows.

## Features

- **DAG-based workflows** — steps are arranged in a directed acyclic graph with automatic dependency resolution
- **Three step types** — synchronous (`Step`), event-driven (`EventStep`), and polling-based (`PollingStep`)
- **MongoDB persistence** — workflow state survives process restarts via async motor driver
- **Distributed safety** — TTL-based leases with atomic acquisition ensure only one runner processes a workflow at a time
- **Optimistic locking** — `doc_version` prevents concurrent modifications from corrupting state
- **Typed configuration** — each step declares a Pydantic config model for compile-time safety
- **Change stream notifications** — optional MongoDB Change Streams integration for event-driven processing
- **Failure propagation** — per-step policies (`fail` or `skip`) control how failures cascade through the DAG

## Installation

```bash
pip install workchain
```

For development:

```bash
pip install -e ".[dev]"
```

## Quick Start

### 1. Define a step

Subclass `Step`, `EventStep`, or `PollingStep` and implement `execute()`:

```python
from pydantic import BaseModel
from workchain import Step, StepResult, Context

class SendEmailConfig(BaseModel):
    to: str
    subject: str

class SendEmailStep(Step[SendEmailConfig]):
    Config = SendEmailConfig

    def execute(self, context: Context) -> StepResult:
        # Access config and upstream step outputs
        recipient = self.config.to
        data = context.step_output("fetch")
        send_email(recipient, self.config.subject, body=data)
        return StepResult.complete(output={"sent": True})
```

### 2. Build a workflow

Chain steps into a DAG using the `Workflow` builder:

```python
from workchain import Workflow, DependencyFailurePolicy

workflow = (
    Workflow(name="onboarding", version="1.0.0")
    .add("fetch", FetchStep(config=FetchConfig(url="https://...")))
    .add(
        "notify",
        SendEmailStep(config=SendEmailConfig(to="user@example.com", subject="Welcome")),
        depends_on=["fetch"],
        on_dependency_failure=DependencyFailurePolicy.SKIP,
    )
)
```

### 3. Run it

```python
from motor.motor_asyncio import AsyncIOMotorClient
from workchain import MongoWorkflowStore, WorkflowRunner

client = AsyncIOMotorClient("mongodb://localhost:27017")
store = MongoWorkflowStore(client=client, database="myapp")
await store.ensure_indexes()

registry = {"FetchStep": FetchStep, "SendEmailStep": SendEmailStep}

run = workflow.create_run()
await store.save(run)

runner = WorkflowRunner(store=store, registry=registry, workflow=workflow)
await runner.start()  # blocking loop; use runner.tick() for single-step processing
```

## Step Types

### Step

Executes synchronously and returns immediately:

```python
class FetchStep(Step[FetchConfig]):
    Config = FetchConfig

    def execute(self, context: Context) -> StepResult:
        data = fetch_from_api(self.config.url)
        return StepResult.complete(output={"data": data})
```

### EventStep

Suspends the workflow until an external signal arrives:

```python
class ApprovalStep(EventStep):
    def execute(self, context: Context) -> StepResult:
        return StepResult.suspend(correlation_id="approval-123")

    def on_resume(self, payload: dict, context: Context) -> None:
        context.set("approved", payload["approved"])
```

Resume from an external system:

```python
await runner.resume(correlation_id="approval-123", payload={"approved": True})
```

### PollingStep

Starts an async job, then polls periodically until it completes:

```python
class ProcessStep(PollingStep[ProcessConfig]):
    Config = ProcessConfig
    poll_interval_seconds = 5
    timeout_seconds = 300

    def execute(self, context: Context) -> StepResult:
        job_id = start_background_job()
        context.set("job_id", job_id)
        return super().execute(context)  # returns StepResult.poll(...)

    def check(self, context: Context) -> bool:
        return is_job_done(context.get("job_id"))

    def on_complete(self, context: Context) -> dict:
        return {"result": get_job_result(context.get("job_id"))}
```

## Event-Driven Mode

Use MongoDB Change Streams for reactive processing instead of polling:

```python
watcher = store.watcher()
await runner.start(watcher=watcher)  # requires MongoDB replica set
```

The watcher automatically filters out self-triggered events to avoid feedback loops.

## Architecture

```
workchain/
├── context.py        — JSON-safe shared runtime state between steps
├── exceptions.py     — All custom exception types
├── models.py         — Pydantic models: WorkflowRun, StepRun, enums
├── runner.py         — WorkflowRunner: DAG resolution, leasing, heartbeat
├── steps.py          — Step, EventStep, PollingStep base classes + StepResult
├── watcher.py        — MongoDB Change Stream watcher for workflow events
├── workflow.py       — Workflow builder + DAG validation (Kahn's algorithm)
├── store.py          — WorkflowStore protocol
└── mongo_store.py    — MongoWorkflowStore (motor, atomic ops)
```

## Full Specification

See [SPEC.md](SPEC.md) for the complete technical specification, including:

- Detailed model field definitions
- Runner execution loop internals
- Lease acquisition and heartbeat mechanics
- Failure propagation algorithm
- MongoDB index recommendations

## Running Tests

```bash
pytest tests/
```

Tests use [mongomock-motor](https://github.com/michaelkryukov/mongomock_motor) — no real MongoDB instance required.
