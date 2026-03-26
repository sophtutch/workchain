# workchain

Programmatic construction and execution of persistent, multi-step workflows.

Workflows are composed of typed, configurable steps arranged in a DAG. State is persisted to MongoDB via `pydantic-mongo`, and distributed execution is safe via TTL leases and optimistic locking.

## Features

- **DAG-based workflows** — steps declare dependencies; cycle detection at build time
- **Three step types** — synchronous, event-driven (suspend/resume), and polling
- **Typed configuration** — each step declares a Pydantic `Config` model
- **MongoDB persistence** — workflows survive restarts and span multiple processes
- **Distributed execution** — atomic lease acquisition ensures only one runner processes a workflow at a time
- **Optimistic locking** — concurrent modification is detected and surfaced, never silently lost
- **Failure propagation** — per-step policy (`fail` or `skip`) controls how failures cascade through the DAG

## Installation

```bash
pip install workchain
```

For development:

```bash
pip install -e ".[dev]"
```

Requires Python 3.11+.

## Quick Start

### 1. Define your steps

```python
from pydantic import BaseModel
from workchain import Step, StepResult, Context

class FetchConfig(BaseModel):
    url: str

class FetchStep(Step[FetchConfig]):
    Config = FetchConfig

    def execute(self, context: Context) -> StepResult:
        data = fetch(self.config.url)
        return StepResult.complete(output={"data": data})
```

### 2. Build a workflow

```python
from workchain import Workflow

workflow = (
    Workflow(name="my_pipeline", version="1.0.0")
    .add("fetch", FetchStep(config=FetchConfig(url="https://example.com/doc")))
    .add("notify", NotifyStep(config=NotifyConfig(to="user@example.com")), depends_on=["fetch"])
)
```

### 3. Run it

```python
from pymongo import MongoClient
from workchain import WorkflowRunner, MongoWorkflowStore

store = MongoWorkflowStore(
    client=MongoClient("mongodb://localhost:27017"),
    database="myapp",
    owner_id="worker-1",
)
store.ensure_indexes()

registry = {"FetchStep": FetchStep, "NotifyStep": NotifyStep}

run = workflow.create_run()
store.save(run)

runner = WorkflowRunner(store=store, registry=registry, workflow=workflow)
runner.start()  # blocking loop; use runner.tick() for single-step processing
```

## Step Types

### Step

Synchronous execution. Implement `execute()` and return a result.

```python
class SendEmailStep(Step[SendEmailConfig]):
    Config = SendEmailConfig

    def execute(self, context: Context) -> StepResult:
        send_email(self.config.to, self.config.subject)
        return StepResult.complete(output={"sent": True})
```

### EventStep

Suspends the workflow until an external signal arrives. Useful for human approvals, webhooks, or async callbacks.

```python
class ApprovalStep(EventStep):
    def execute(self, context: Context) -> StepResult:
        ticket_id = create_approval_ticket()
        return StepResult.suspend(correlation_id=ticket_id)

    def on_resume(self, payload: dict, context: Context) -> StepResult:
        context.set("approved", payload.get("approved", False))
        return StepResult.complete(output=payload)
```

Resume externally:

```python
runner.resume(correlation_id="ticket-123", payload={"approved": True})
```

### PollingStep

Periodically checks a condition until it's met. Supports configurable intervals and optional timeouts.

```python
class JobCompletionConfig(BaseModel):
    job_id: str
    poll_interval_seconds: int = 10
    timeout_seconds: int | None = 300

class JobCompletionStep(PollingStep[JobCompletionConfig]):
    Config = JobCompletionConfig

    def check(self, context: Context) -> bool:
        return is_job_done(self.config.job_id)

    def on_complete(self, context: Context) -> dict:
        return {"job_id": self.config.job_id, "status": "done"}
```

## Shared Context

Steps share state through a JSON-serializable `Context` object. Downstream steps can access upstream outputs:

```python
def execute(self, context: Context) -> StepResult:
    fetch_output = context.step_output("fetch")
    data = fetch_output["data"]
    # ...
```

## Architecture

```
workchain/
├── exceptions.py     — Custom exception types
├── models.py         — Pydantic/Mongo documents: WorkflowRun, StepRun, enums
├── context.py        — JSON-safe shared runtime state between steps
├── steps.py          — Step, EventStep, PollingStep base classes + StepResult
├── workflow.py       — Workflow builder + DAG validation
├── runner.py         — WorkflowRunner: DAG resolution, leasing, heartbeat
└── store/
    ├── base.py       — WorkflowStore protocol
    └── mongo.py      — MongoWorkflowStore (pydantic-mongo, atomic ops)
```

## Specification

For the full technical specification — including model schemas, lease mechanics, runner execution loop, failure propagation, and MongoDB index recommendations — see [SPEC.md](SPEC.md).
