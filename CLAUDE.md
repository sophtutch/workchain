# workchain — Claude Agent Context

## What this is

`workchain` is a Python library for programmatic construction and execution of **persistent, multi-step workflows**. Steps execute sequentially, state is persisted to MongoDB via `motor`, and distributed execution is safe via TTL-based locks + fence tokens (optimistic locking).

See `README.md` for usage examples, quick start, and API documentation.

## Architecture

```
workchain/
├── models.py       — Pydantic models: Workflow, Step, StepConfig, StepResult, enums, policies
├── decorators.py   — @step / @async_step / @completeness_check decorators + handler registry
├── engine.py       — WorkflowEngine: claim loop, heartbeat, sweep, execution
├── store.py        — MongoWorkflowStore: persistence, distributed locking, typed deserialization
├── retry.py        — Retry utilities wrapping tenacity with RetryPolicy
├── audit.py        — AuditEvent model, AuditLogger protocol, MongoAuditLogger
└── audit_report.py — HTML execution report generator from audit events
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
- **Async steps** (`@async_step`): submit work, set BLOCKED, release lock, poll `@completeness_check` on subsequent claims until complete

**Decorator-driven metadata**
- Handler names are auto-generated from `fn.__module__.fn.__qualname__` — no `name` parameter
- `needs_context: bool = False` on all three decorators declares whether the engine should pass the context dict
- `_step_meta` dict attached to each handler carries all metadata — the engine reads it, never uses `inspect.signature`
- Both sync and async handlers are supported via `asyncio.iscoroutine` safety net

**Distributed safety**
- Lock acquisition via atomic `findOneAndUpdate` — only one instance wins
- `try_claim()` filters by status (`PENDING`/`RUNNING` only) to prevent re-claiming terminal workflows
- `fence_token` increments on each claim; all writes are fenced (`{"fence_token": N}`)
- Heartbeat loop renews lock TTL; stale locks expire and are reclaimed
- Sweep loop detects anomalies (stuck steps, stale locks, unadvanced completed steps)

**Crash-safe state machine**
- Before execution: step is marked SUBMITTED (write-ahead)
- On recovery: `verify_completion` / `completeness_check` / idempotent re-run / NEEDS_REVIEW
- Recovery handles all completeness_check return types: `bool`, `dict`, `PollHint`, and truthy values
- Each retry attempt is persisted to MongoDB before execution

**Claim-poll-release cycle (async steps)**
1. Claim workflow, execute handler (submission), set BLOCKED, schedule `next_poll_at`, release lock
2. Fast sweep rediscovers workflow when `next_poll_at` passes
3. Claim, run one `completeness_check`, if not done → schedule next poll, release lock
4. Repeat until complete or timeout/max_polls exceeded
5. If `completeness_check` throws, the engine retries using the check's `RetryPolicy` (configured via `@completeness_check(retry=...)`). If all retries are exhausted within a single poll cycle, the step fails immediately.

**Engine context (dependency injection)**
- `WorkflowEngine(store, context={"db": db})` passes a dict to handlers that declare `needs_context=True`
- The engine reads `_step_meta["needs_context"]` — no runtime parameter inspection
- Framework-agnostic: works with FastAPI, CLI scripts, or bare asyncio
- Context values should be accessed with `cast()` for type safety

## Conventions

- Step result and config fields must be JSON-serializable.
- `fence_token` is managed by the store — never set it manually.
- `WorkflowEngine.instance_id` should be unique per process (auto-generated if omitted).
- Step handlers must be registered via decorators or importable by dotted path.
- `PollHint.progress` must be between 0.0 and 1.0.
- Config models extend `StepConfig`, result models extend `StepResult`.
- Use `cast()` when accessing specific result types from the `results` dict.
- Store uses `model_dump(mode="python", serialize_as_any=True)` — never `mode="json"` (datetimes must be native for MongoDB queries).

## Files to modify with care

- `store.py` — the lock acquisition query, fence-guarded writes, and `_doc_to_workflow` deserialization are carefully crafted; changes risk race conditions or type resolution failures
- `engine.py` `_recover_step()` — recovery logic handles multiple crash scenarios and all completeness_check return types; understand all paths before changing
- `engine.py` `_call_handler()` — uses `_step_meta["needs_context"]` and `iscoroutine` safety net; do not reintroduce `inspect.signature`
- `models.py` — changing field names affects all persisted MongoDB documents; `Step._set_type_paths` auto-populates `config_type`/`result_type`
- `decorators.py` — `_step_meta` dict is the contract between decorators and engine; adding/removing keys affects both

## Audit logging

The engine emits structured `AuditEvent` objects for every MongoDB write that changes workflow or step state. Events capture enough context to reconstruct flow diagrams from the log alone.

- **`AuditLogger` protocol** — pluggable backend with `emit(event)` and `get_events(workflow_id)`
- **`MongoAuditLogger`** — fire-and-forget writes to `workflow_audit_log` collection. Failures log a warning but never block workflow execution.
- **`NullAuditLogger`** — no-op default when no logger is passed
- **25 event types** (`AuditEventType` enum): `WORKFLOW_CREATED`, `WORKFLOW_CLAIMED`, `WORKFLOW_COMPLETED`, `WORKFLOW_FAILED`, `WORKFLOW_CANCELLED`, `STEP_SUBMITTED`, `STEP_RUNNING`, `STEP_COMPLETED`, `STEP_FAILED`, `STEP_BLOCKED`, `STEP_ADVANCED`, `STEP_TIMEOUT`, `POLL_CHECKED`, `POLL_TIMEOUT`, `POLL_MAX_EXCEEDED`, `POLL_CHECK_ERRORS_EXCEEDED`, `LOCK_RELEASED`, `LOCK_FORCE_RELEASED`, `HEARTBEAT`, `RECOVERY_STARTED`, `RECOVERY_VERIFIED`, `RECOVERY_BLOCKED`, `RECOVERY_RESET`, `RECOVERY_NEEDS_REVIEW`, `SWEEP_ANOMALY`
- Events ordered by per-workflow `sequence` number (in-memory counter, causal within single instance)
- `generate_audit_report(events)` produces self-contained HTML execution reports

## Flow diagram generation

`examples/generate_diagrams.py` generates self-contained `flow_diagram.html` files for all 5 example workflows. Each HTML file shows the complete step execution flow with retry scenarios, polling phases, instance claim/release cycles, fence token progression, and MongoDB document diffs.

```bash
python examples/generate_diagrams.py
```

The generator uses Python dataclasses (`WorkflowData`, `StepData`, `RetryScenario`, `PollScenario`) to define per-example data, and renders all 5 files from a shared CSS/HTML template with no external dependencies.
