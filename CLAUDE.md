# workchain — Claude Agent Context

## What this is

`workchain` is a Python library for programmatic construction and execution of **persistent, multi-step workflows**. Steps declare dependencies via `depends_on`; independent steps execute concurrently across engine instances. State is persisted to MongoDB via `motor`, and distributed execution is safe via per-step TTL-based locks + fence tokens (optimistic locking).

See `README.md` for usage examples, quick start, and API documentation.

## Architecture

```
workchain/                      — core library
├── models.py                   — Pydantic models: Workflow, Step, StepConfig, StepResult, enums, policies
├── decorators.py               — @step / @async_step / @completeness_check decorators + handler registry
├── engine.py                   — WorkflowEngine: per-step claim loop, heartbeat, sweep, execution
├── store.py                    — MongoWorkflowStore: persistence, per-step distributed locking, typed deserialization
├── retry.py                    — Retry utilities wrapping tenacity with RetryPolicy
├── audit.py                    — AuditEvent model, AuditLogger protocol, MongoAuditLogger
├── audit_report.py             — HTML execution report generator from audit events
└── contrib/
    └── fastapi.py              — Optional FastAPI router (pip install workchain[fastapi])

workchain_server/               — standalone server (pip install workchain[server])
├── config.py                   — Environment variable configuration via pydantic-settings
├── plugins.py                  — Step handler discovery (entry points + env var)
├── app.py                      — FastAPI app with engine lifecycle and router mounting
└── ui.py                       — Management dashboard HTML + router
```

## Key design decisions

**Strongly typed config and results**
- `StepConfig` and `StepResult` are Pydantic base classes — subclass with typed fields
- Config type is stored as `config_type` (dotted path) on each step for MongoDB round-trip
- Result type is stored as `result_type` for the same reason
- The store resolves these paths at read time via `_doc_to_workflow()`, so handlers receive properly typed objects
- Handlers access preceding step results via `results: dict[str, StepResult]`, using `cast()` for per-key types

**Dependency-based step execution**
- Steps declare dependencies via `depends_on: list[str]` referencing other step names
- Steps without explicit `depends_on` default to sequential chain (each depends on previous)
- Steps with `depends_on: []` (empty list) are root steps, ready immediately
- Independent steps can execute concurrently across engine instances
- Workflow completes atomically when all steps are done; fails when any step fails

**Two step modes**
- **Sync steps** (`@step`): execute handler, persist result, complete step immediately
- **Async steps** (`@async_step`): submit work, set BLOCKED, release lock, poll `@completeness_check` on subsequent claims until complete

**Decorator-driven metadata**
- Handler names are auto-generated from `fn.__module__.fn.__qualname__` — no `name` parameter
- `needs_context: bool = False` on all three decorators declares whether the engine should pass the context dict
- `_step_meta` dict attached to each handler carries all metadata — the engine reads it, never uses `inspect.signature`
- Both sync and async handlers are supported via `asyncio.iscoroutine` safety net

**Distributed safety (step-level locking)**
- Lock fields (`locked_by`, `lock_expires_at`, `fence_token`) live on each `Step`, not on `Workflow`
- `try_claim_step()` atomically locks a single step via `findOneAndUpdate` with array filters
- Each step has its own `fence_token`; all per-step writes are fenced (`{"steps.$[s].fence_token": N}`)
- `find_claimable_steps()` discovers ready steps across all running workflows (two-phase: broad query → Python readiness filter)
- Multiple engine instances can concurrently execute independent steps of the same workflow
- Heartbeat loop renews per-step lock TTLs; stale locks expire and are reclaimed
- Sweep loop detects anomalies (stuck steps, stale step locks)

**Crash-safe state machine**
- Before execution: step is marked SUBMITTED (write-ahead)
- On recovery: `verify_completion` / `completeness_check` / idempotent re-run / NEEDS_REVIEW
- Recovery uses `CheckResult.complete` directly — the `@completeness_check` decorator normalizes all return types
- Each retry attempt is persisted to MongoDB before execution

**Claim-poll-release cycle (async steps)**
1. Claim step, execute handler (submission), set BLOCKED, schedule `next_poll_at`, release step lock
2. Claim loop rediscovers step when `next_poll_at` passes
3. Claim step, run one `completeness_check`, if not done → schedule next poll, release step lock
4. Repeat until complete or timeout/max_polls exceeded
5. If `completeness_check` throws, the engine retries using the check's `RetryPolicy` (configured via `@completeness_check(retry=...)`). If all retries are exhausted within a single poll cycle, the step fails immediately.

**Engine context (dependency injection)**
- `WorkflowEngine(store, context={"db": db})` passes a dict to handlers that declare `needs_context=True`
- The engine reads `_step_meta["needs_context"]` — no runtime parameter inspection
- Framework-agnostic: works with FastAPI, CLI scripts, or bare asyncio
- Context values should be accessed with `cast()` for type safety

## Python style guide

### Module and method size
- **Target: ≤500 lines per module, ≤80 lines per function/method.** Files above 500 lines become hard to navigate; methods above 80 lines become hard to reason about.
- **Current debt:** `engine.py` (1,025 lines), `store.py` (1,089 lines), and `audit_report.py` (1,358 lines) exceed this. Do not make them larger — extract when adding functionality. Key offenders: `_poll_once` (218 lines), `_run_step` (169 lines), `_recover_step` (125 lines), `_render_step_section` (299 lines).
- When a method grows past ~80 lines, extract a well-named private helper. The engine and store already do this well in most places (e.g. `_build_results`, `_wrap_handler_return`, `_fenced_step_update_by_name`).
- Use `# ---------------------------------------------------------------------------` section dividers between logical groups within a module (already used in `engine.py` and `store.py` — maintain this pattern).

### Type annotations
- Every module starts with `from __future__ import annotations` (already enforced across the codebase).
- Use modern generics: `list[str]`, `dict[str, StepResult]` — never `typing.List` or `typing.Dict`.
- All function signatures must be fully typed. Avoid `Any` unless truly unavoidable (acceptable for `_call_handler`'s dynamic dispatch).
- Use `cast()` when accessing specific result types from the `results` dict — never downcast via indexing alone.

### Docstrings
- **Required** on every public function, class, method, and decorator. Use Google style (Args/Returns/Raises sections).
- **Current debt:** `store.py` has 17 public methods without docstrings; `models.py` has no class docstrings on `Step`, `Workflow`, `StepStatus`, `WorkflowStatus`; decorators `step()`, `async_step()`, `completeness_check()` lack docstrings. Add docstrings when touching these.
- Private methods (`_name`) need a docstring if their purpose is not obvious from name + context.

### Naming
- `snake_case` for functions, methods, variables, parameters.
- `PascalCase` for classes, models, enums.
- `UPPER_SNAKE_CASE` for module-level constants.
- Internal variables: be descriptive (`claimable_steps`, `fenced_update`, `next_poll_at`). Avoid cryptic abbreviations except where already established (`ttl`, `id`, `wf`).

### Async and concurrency
- Never block the event loop — no `time.sleep`, no synchronous I/O. Use `asyncio.sleep`.
- Heartbeat and sweep loops must be cancellation-safe (handle `CancelledError` gracefully).
- All MongoDB writes go through `MongoWorkflowStore` methods — never raw `collection.update_one` outside the store.

### Error handling
- No bare `except:`. Always catch specific exceptions or `except Exception:` with `logger.exception()`.
- Retry logic: use `retry.py` utilities or the `retry=` parameter on decorators — never manual retry loops.
- On step failure: populate `StepResult.error` and `StepResult.error_traceback`.

### Dependencies
- Keep the core library lightweight. Never add new third-party dependencies without explicit justification. Current runtime deps: `pydantic`, `motor`, `tenacity`. Optional extras: `fastapi` (contrib router), `server` (adds `uvicorn`, `pydantic-settings`).

## Conventions

- Step result and config fields must be JSON-serializable.
- `fence_token` is managed by the store — never set it manually.
- `WorkflowEngine.instance_id` should be unique per process (auto-generated if omitted).
- Step handlers must be registered via decorators or importable by dotted path.
- `CheckResult.progress` must be between 0.0 and 1.0.
- Config models extend `StepConfig`, result models extend `StepResult`.
- Use `cast()` when accessing specific result types from the `results` dict.
- Store step-state methods (`complete_step_by_name`, `fail_step_by_name`, `block_step_by_name`) accept `StepResult` objects directly — pass the model, not a dict. The store handles serialization internally via `model_dump(mode="python", serialize_as_any=True)`. Never call `.model_dump()` before passing results to store methods.
- Store uses `model_dump(mode="python", serialize_as_any=True)` — never `mode="json"` (datetimes must be native for MongoDB queries).

## Documentation update rules

When making changes to the library or server, the following documents **must** be kept in sync:

| Document | Scope | Update when... |
|----------|-------|---------------|
| `CLAUDE.md` | Entire project | Architecture, conventions, design decisions, or public API surface changes |
| `README.md` | Entire project | User-facing features, installation, usage examples, config, or CLI commands change |
| `REQUIREMENTS.md` | **`workchain/` library only** | Any change to core library code under `workchain/`. This is a rebuild-quality specification — detailed enough to reproduce the library from scratch. Covers: function signatures, state machines, error handling, edge cases, field defaults, validation rules, concurrency semantics. Does NOT cover `workchain_server/` or `workchain/contrib/` — those are documented in `CLAUDE.md` and `README.md` only. |

**Mandatory checks before completing any task:**
- If you changed core library code (`workchain/`) → update `REQUIREMENTS.md` with precise behavioral details
- If you added/removed/renamed a module → update the Architecture tree in both `CLAUDE.md` and `README.md`
- If you added/changed an optional extra or dependency → update Installation section in `README.md` and Dependencies in `CLAUDE.md`
- If you added/changed environment variables or config → update the relevant env var tables in `README.md` and `CLAUDE.md`
- If you added/changed API routes → update route tables in `CLAUDE.md`

## Files to modify with care

- `store.py` — the per-step lock acquisition query, fence-guarded writes via `_fenced_step_update_by_name`, and `_doc_to_workflow` deserialization are carefully crafted; changes risk race conditions or type resolution failures
- `engine.py` `_recover_step()` — recovery logic handles multiple crash scenarios; understand all paths before changing. Recovery operates per-step, not per-workflow
- `engine.py` `_call_handler()` — uses `_step_meta["needs_context"]` and `iscoroutine` safety net; do not reintroduce `inspect.signature`
- `models.py` — changing field names affects all persisted MongoDB documents; `Step._set_type_paths` auto-populates `config_type`/`result_type`
- `decorators.py` — `_step_meta` dict is the contract between decorators and engine; adding/removing keys affects both

## Audit logging

The **store** emits structured `AuditEvent` objects for every MongoDB write that changes workflow or step state. Events capture enough context to reconstruct flow diagrams from the log alone.

- **`AuditLogger` is configured on `MongoWorkflowStore`**, not on the engine — pass `audit_logger=` and `instance_id=` to the store constructor
- **`WORKFLOW_CREATED` is emitted automatically** on `store.insert()` — no caller action needed
- **`AuditLogger` protocol** — pluggable backend with `emit(event)` and `get_events(workflow_id)`
- **`MongoAuditLogger`** — fire-and-forget writes to `workflow_audit_log` collection. Failures log a warning but never block workflow execution.
- **`NullAuditLogger`** — no-op default when no logger is passed
- **`store.emit_recovery_started()`** / **`store.emit_step_timeout()`** / **`store.emit_sweep_anomaly()`** / **`store.emit_poll_failure()`** / **`store.emit_poll_checked()`** — diagnostic audit events (no DB write) for engine-only lifecycle events
- **`store.drain_audit_tasks(timeout)`** — called by engine during shutdown to drain pending writes
- Store methods accept optional audit context kwargs (`audit_event_type`, `result_summary`, `error`, `error_traceback`, `recovery_action`, etc.) to customize the emitted event
- **26 event types** (`AuditEventType` enum): `WORKFLOW_CREATED`, `WORKFLOW_CLAIMED`, `WORKFLOW_COMPLETED`, `WORKFLOW_FAILED`, `WORKFLOW_CANCELLED`, `STEP_CLAIMED`, `STEP_SUBMITTED`, `STEP_RUNNING`, `STEP_COMPLETED`, `STEP_FAILED`, `STEP_BLOCKED`, `STEP_ADVANCED`, `STEP_TIMEOUT`, `POLL_CHECKED`, `POLL_TIMEOUT`, `POLL_MAX_EXCEEDED`, `POLL_CHECK_ERRORS_EXCEEDED`, `LOCK_RELEASED`, `LOCK_FORCE_RELEASED`, `HEARTBEAT`, `RECOVERY_STARTED`, `RECOVERY_VERIFIED`, `RECOVERY_BLOCKED`, `RECOVERY_RESET`, `RECOVERY_NEEDS_REVIEW`, `SWEEP_ANOMALY`
- Events ordered by per-workflow `sequence` number (in-memory counter, causal within single instance)
- `generate_audit_report(events)` produces self-contained HTML execution reports

## Optional extras (contrib)

The `workchain/contrib/` subpackage contains optional integrations gated behind pip extras. Each module guards its imports and raises a clear `ImportError` if the extra is not installed.

**FastAPI** (`pip install workchain[fastapi]`):
- `workchain.contrib.fastapi.create_workchain_router(store, audit_logger)` — returns an `APIRouter` with standard workflow CRUD + report endpoints
- Mount on any FastAPI app: `app.include_router(router, prefix="/workflows")`
- Endpoints: list, stats, get, cancel, HTML audit report
- Does NOT include workflow creation (app-specific)

## Workchain Server (`pip install workchain[server]`)

Standalone deployable FastAPI service with management dashboard. Lives in `workchain_server/` within the same package.

```
workchain_server/
├── __init__.py
├── config.py       — pydantic-settings: env var configuration
├── plugins.py      — step handler discovery (entry points + WORKCHAIN_PLUGINS env var)
├── app.py          — FastAPI app: lifespan, engine lifecycle, router mounting
└── ui.py           — management dashboard (workflow table, stats, report links)
```

**Quick start**: `hatch run server:serve` (requires MongoDB at `MONGO_URI`, default `mongodb://localhost:27017`)

**Configuration** (all via environment variables):
- `MONGO_URI` — MongoDB connection string (default `mongodb://localhost:27017`)
- `MONGO_DATABASE` — database name (default `workchain`)
- `ENGINE_INSTANCE_ID` — engine instance identifier (auto-generated if empty)
- `ENGINE_CLAIM_INTERVAL`, `ENGINE_HEARTBEAT_INTERVAL`, `ENGINE_SWEEP_INTERVAL`, `ENGINE_STEP_STUCK_SECONDS`, `ENGINE_MAX_CONCURRENT` — engine tuning
- `WORKCHAIN_PLUGINS` — comma-separated dotted module paths to import at startup (triggers handler registration)
- `SERVER_TITLE` — dashboard page title (default `Workchain Server`)

**API routes**:
- `GET /` — management dashboard UI
- `GET /healthz` — health check (pings MongoDB)
- `GET /api/v1/workflows` — list workflows (from contrib router)
- `GET /api/v1/workflows/stats` — workflow counts by status
- `GET /api/v1/workflows/{id}` — workflow detail
- `GET /api/v1/workflows/{id}/report` — HTML audit report
- `POST /api/v1/workflows/{id}/cancel` — cancel workflow

**Plugin system**: Step handlers are registered by importing modules that use `@step`/`@async_step` decorators. Two discovery mechanisms:
1. Python entry points under `workchain.plugins` group (for installed packages)
2. `WORKCHAIN_PLUGINS` env var with comma-separated module paths (for Docker/dev use)

**Key design decisions**:
- The server does NOT define workflow creation endpoints — those are app-specific. Workflows are submitted via external callers or plugins.
- Motor client is instantiated at module level (connects lazily) and closed in lifespan teardown
- The contrib router is mounted at `/api/v1/workflows` (versioned from day one)
- The dashboard UI fetches data from the API via JavaScript — no server-side rendering

