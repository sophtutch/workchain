# workchain — Specification

Version: 0.1.0
Status: Draft
Date: 2026-03-26

---

## Overview

`workchain` is a Python library for the programmatic construction and execution of persistent, multi-step workflows. Workflows are composed of typed, configurable steps arranged in a directed acyclic graph (DAG). State is persisted to MongoDB, enabling workflows to survive process restarts, span multiple service instances, and block on external events.

---

## Core Concepts

### Workflow

A `Workflow` is a named, versioned definition of a process. It contains an ordered set of `StepDefinition` objects and describes how they depend on one another. A `Workflow` is a blueprint — it is not executable directly. Execution produces a `WorkflowRun`.

### StepDefinition

A `StepDefinition` is the static declaration of a step within a workflow. It specifies:

- A unique `step_id` within the workflow
- The `step_type` — a string key mapping to a registered `Step` class
- A typed Pydantic `config` object specific to that step type
- A list of `depends_on` step IDs (may be empty)
- A failure propagation mode: `skip` or `fail` (default: `fail`)

### Step

A `Step` is the executable unit. Each step subclass defines its own `Config` (a Pydantic `BaseModel`) and implements an `execute(context: Context) -> StepResult` method.

Three step base classes are provided:

**`Step`** — standard synchronous step. Executes and returns immediately.

**`EventStep`** — suspends the workflow until an external signal is received. On suspension, a `resume_correlation_id` is generated and stored. An external process calls `WorkflowRunner.resume(correlation_id, payload)` to continue execution. The step also implements `on_resume(payload, context)` to process the incoming payload.

**`PollingStep`** — periodically re-checks a condition. Implements `check(context) -> bool`. If `check` returns `False`, the step is rescheduled after `poll_interval` seconds. An optional `timeout` causes the step to fail if the condition is not met in time. On success, `on_complete(context)` is called.

### Context

`Context` is the shared runtime state passed between steps. It contains:

- A global key/value store (JSON-serializable values only)
- Step outputs, keyed by `step_id`, written by the runner after each step completes

Downstream steps access upstream outputs via `context.step_output("step_id")`.

### WorkflowRun

`WorkflowRun` is the persisted document representing a single execution of a workflow. It is a pydantic-mongo document stored in MongoDB. It holds:

- Reference to the originating workflow name and version
- Per-step runtime state (`StepRun` list)
- Shared context
- Workflow-level status
- Lease fields for distributed ownership
- An optimistic concurrency version counter

### StepRun

`StepRun` tracks the runtime state of a single step within a `WorkflowRun`:

- `step_id`, `step_type`, `depends_on`
- `status`: one of `pending`, `running`, `completed`, `failed`, `suspended`, `awaiting_poll`, `skipped`
- `output`: dict written by the step on completion
- `error`: string message on failure
- `resume_correlation_id`: set for suspended `EventStep`s
- `next_poll_at`: set for `PollingStep`s awaiting their next check
- `started_at`, `completed_at` timestamps

---

## Inter-Step Dependencies

Steps declare dependencies via `depends_on: list[str]`. The runner resolves a DAG from these declarations. On each execution tick:

1. All steps whose dependencies are entirely in `completed` status are considered **ready**.
2. Ready steps may be executed in parallel.
3. If a dependency transitions to `failed`:
   - Dependents with `on_dependency_failure = "fail"` are marked `failed` (default).
   - Dependents with `on_dependency_failure = "skip"` are marked `skipped`.

DAG validation (cycle detection via topological sort) is performed at workflow construction time, not at runtime. Constructing a workflow with a cycle raises `WorkflowValidationError`.

---

## Persistence — MongoDB via pydantic-mongo

`WorkflowRun` inherits from pydantic-mongo's `AbstractRepository` pattern. The `MongoWorkflowStore` class wraps this repository and exposes the operations needed by the runner:

- `save(run)` — persisted with optimistic version check
- `load(run_id)` — load by ObjectId
- `find_resumable()` — find runs with `status` in `[pending, running, awaiting_poll]` and no active lease
- `find_by_correlation_id(correlation_id)` — locate a suspended run by its resume correlation ID

### Optimistic Locking

Every `WorkflowRun` carries a `doc_version: int` field, incremented on every write. All writes use `replace_one({"_id": id, "doc_version": current_version}, ...)`. A `modified_count` of 0 raises `ConcurrentModificationError`, indicating another process modified the document concurrently.

---

## Distributed Execution — Leasing

`workchain` supports deployment across multiple service instances running against the same MongoDB collection. Ownership of a `WorkflowRun` is managed via a TTL-based lease:

### Lease Fields (on WorkflowRun)

| Field | Type | Description |
|---|---|---|
| `lease_owner` | `str \| None` | ID of the owning runner instance |
| `lease_expires_at` | `datetime \| None` | Expiry of current lease |
| `lease_renewed_at` | `datetime \| None` | Last heartbeat time |

### Lease Acquisition

Performed atomically via `findOneAndUpdate`. A runner may claim a `WorkflowRun` only if:
- Its status is eligible (`pending`, `running`, `awaiting_poll`)
- `lease_expires_at` is `None` or in the past

Only one runner wins the race. If no eligible run is found, the runner sleeps and retries.

### Lease Renewal (Heartbeat)

During step execution, a background thread renews the lease every `ttl / 2` seconds by calling `update_one({"_id": id, "lease_owner": owner_id}, {$set: {lease_expires_at: ...}})`. Only the owner may renew. On process crash, the thread dies and the lease expires naturally.

### Lease Release

On clean completion or error, the runner explicitly clears `lease_owner` and `lease_expires_at`.

---

## Runner

`WorkflowRunner` is the execution engine. It is instantiated with:

- A `WorkflowStore` (e.g. `MongoWorkflowStore`)
- A step registry mapping `step_type` strings to `Step` classes
- `instance_id` — unique per process (defaults to `uuid4()` at startup)
- `lease_ttl` — lease duration in seconds (default: 30)
- `poll_interval` — how often to check for available work (default: 5s)

### Execution Loop

```
loop:
    run = acquire_lease(instance_id)
    if not run: sleep(poll_interval); continue

    start heartbeat thread

    try:
        ready_steps = get_ready_steps(run)
        for step in ready_steps:
            mark step RUNNING; save
            result = step.execute(context)

            match result:
                COMPLETED  → write output to context; mark COMPLETED
                SUSPEND    → store correlation_id; mark SUSPENDED
                POLL       → set next_poll_at; mark AWAITING_POLL
                FAILED     → mark FAILED; propagate to dependents

            save_with_version(run)

        if workflow is fully resolved:
            mark workflow COMPLETED or FAILED
            save_with_version(run)

    except ConcurrentModificationError:
        log and abort — another runner modified this run
    finally:
        stop heartbeat thread
        release_lease(run.id, instance_id)
```

### Resuming an EventStep

```python
runner.resume(correlation_id="abc123", payload={"result": "approved"})
```

This locates the suspended run via `correlation_id`, acquires a lease, calls `step.on_resume(payload, context)`, marks the step `COMPLETED`, and re-enters the execution loop.

---

## Exceptions

| Exception | Raised when |
|---|---|
| `WorkflowValidationError` | DAG contains a cycle or references an unknown step_id |
| `ConcurrentModificationError` | Optimistic lock fails on save |
| `LeaseAcquisitionError` | Lease claim fails unexpectedly (not simply unavailable) |
| `StepNotFoundError` | `step_type` not present in the step registry |
| `WorkflowRunNotFoundError` | `load(run_id)` finds no matching document |

---

## MongoDB Index Recommendations

```javascript
db.workflow_runs.createIndex({ status: 1, lease_expires_at: 1 })
db.workflow_runs.createIndex({ "steps.resume_correlation_id": 1 }, { sparse: true })
db.workflow_runs.createIndex({ "steps.next_poll_at": 1, status: 1 }, { sparse: true })
```

---

## Public API Summary

```python
from workchain import (
    Workflow,
    Step, EventStep, PollingStep,
    Context,
    StepResult,
    WorkflowRunner,
    MongoWorkflowStore,
)
```

---

## Example

```python
from workchain import Workflow, Step, EventStep, PollingStep, Context, StepResult
from pydantic import BaseModel

class FetchConfig(BaseModel):
    url: str

class FetchStep(Step):
    Config = FetchConfig

    def execute(self, context: Context) -> StepResult:
        data = fetch(self.config.url)
        return StepResult.complete(output={"data": data})


class ApprovalStep(EventStep):
    def on_resume(self, payload: dict, context: Context) -> None:
        context["approved"] = payload.get("approved", False)


class PollConfig(BaseModel):
    job_id: str
    poll_interval: int = 10

class JobCompletionStep(PollingStep):
    Config = PollConfig

    def check(self, context: Context) -> bool:
        return job_is_done(self.config.job_id)


workflow = (
    Workflow(name="document_pipeline", version="1.0.0")
    .add("fetch",    FetchStep(config=FetchConfig(url="https://example.com/doc")))
    .add("approve",  ApprovalStep(), depends_on=["fetch"])
    .add("process",  JobCompletionStep(config=PollConfig(job_id="job-42")), depends_on=["approve"])
)
```
