# workchain Requirements Specification

A rebuild-quality specification for the `workchain` library: a Python framework for programmatic construction and execution of persistent, multi-step workflows with distributed step-level execution.

**Runtime dependencies:** `pydantic >= 2.0`, `motor >= 3.0`, `tenacity >= 8.2`
**Python:** `>= 3.11` (uses `from __future__ import annotations` throughout)
**Persistence:** MongoDB via `motor` (async driver)

**Design philosophy:** Steps declare dependencies via `depends_on`; independent steps execute concurrently across engine instances. State is persisted to MongoDB, and distributed execution is safe via per-step TTL-based locks with fence tokens (optimistic locking). No external coordination service is required.

---

## 1. Module Layout

```
workchain/
  __init__.py         Public API exports
  models.py           Pydantic models: Workflow, Step, StepConfig, StepResult, enums, policies
  decorators.py       @step / @async_step / @completeness_check + handler registry
  engine.py           WorkflowEngine: claim loop, heartbeat, sweep, execution, recovery
  store.py            MongoWorkflowStore: persistence, locking, fenced updates, discovery
  retry.py            retrying_from_policy — tenacity wrapper
  audit.py            AuditEvent model, AuditEventType enum, AuditLogger protocol, implementations
  audit_report.py     HTML execution report generator from audit events
  exceptions.py       Exception hierarchy
```

---

## 2. Constants and Defaults

| Constant | Location | Default | Purpose |
|---|---|---|---|
| `COLLECTION` | store | `"workflows"` | MongoDB collection for workflow documents |
| `AUDIT_COLLECTION` | audit | `"workflow_audit_log"` | MongoDB collection for audit events |
| `lock_ttl_seconds` | store init | `30` | Step lock TTL in seconds |
| `operation_timeout_ms` | store init | `30_000` | MongoDB operation timeout (must be > 0) |
| `_step_index_cache_max` | store | `10_000` | Max LRU cache entries for step name-to-index |
| `claim_interval` | engine init | `5.0` | Claim loop polling interval (seconds) |
| `heartbeat_interval` | engine init | `10.0` | Heartbeat loop interval (seconds) |
| `sweep_interval` | engine init | `60.0` | Sweep loop interval (seconds) |
| `step_stuck_seconds` | engine init | `300.0` | Threshold for stuck step detection (seconds) |
| `max_concurrent` | engine init | `5` | Max active steps per engine instance |
| `log_heartbeats` | engine init | `False` | Emit HEARTBEAT audit events |
| `max_pending` | MongoAuditLogger init | `100` | Max concurrent audit writes before backpressure |
| Workflow ID | models | `uuid4().hex` | 32-char hex, 128-bit entropy |
| Instance ID | engine | `{hostname}-{uuid4().hex[:8]}` | Auto-generated if not provided |
| Over-fetch multiplier | store `find_claimable_steps` | `3x` | MongoDB query limit multiplier |

### RetryPolicy Defaults

| Field | Type | Default | Purpose |
|---|---|---|---|
| `max_attempts` | `int` | `3` | Total execution attempts |
| `wait_seconds` | `float` | `1.0` | Initial wait between retries |
| `wait_multiplier` | `float` | `2.0` | Exponential backoff multiplier |
| `wait_max` | `float` | `60.0` | Ceiling for backoff |

### PollPolicy Defaults

| Field | Type | Default | Purpose |
|---|---|---|---|
| `interval` | `float` | `5.0` | Initial poll interval (seconds) |
| `backoff_multiplier` | `float` | `1.0` | 1.0 = fixed interval, > 1.0 = exponential |
| `max_interval` | `float` | `60.0` | Ceiling for backoff |
| `timeout` | `float` | `3600.0` | Max total poll duration; 0 = unlimited |
| `max_polls` | `int` | `0` | Max poll attempts; 0 = unlimited |

**PollPolicy validator:** `_validate_non_negative` applied to all 5 fields; rejects values < 0.

---

## 3. Exception Hierarchy

```
WorkchainError                   Base exception for all workchain errors
  StepError                      Error during step execution
    StepTimeoutError             Handler exceeded its per-attempt timeout
    RetryExhaustedError          All retry attempts exhausted
    HandlerError                 Handler returned invalid result or is misconfigured
  LockError                      Lock acquisition or fence token error
    FenceRejectedError           Write rejected because fence token doesn't match (lock stolen)
  RecoveryError                  Error during crash recovery
```

---

## 4. Data Models

All models use `from __future__ import annotations` and Pydantic v2 `BaseModel`.

### 4.1 Enumerations

Both enums inherit from `str, Enum`.

**StepStatus** — lifecycle states for a workflow step:

| Value | String | Description |
|---|---|---|
| `PENDING` | `"pending"` | Initial state, awaiting dependencies |
| `SUBMITTED` | `"submitted"` | Written to DB before execution; crash-safe write-ahead boundary |
| `RUNNING` | `"running"` | Handler currently executing |
| `BLOCKED` | `"blocked"` | Async step actively polling for completeness |
| `COMPLETED` | `"completed"` | Step succeeded (terminal) |
| `FAILED` | `"failed"` | Step failed after retries exhausted (terminal) |

**WorkflowStatus** — lifecycle states for a workflow:

| Value | String | Description |
|---|---|---|
| `PENDING` | `"pending"` | Awaiting execution |
| `RUNNING` | `"running"` | Has active steps |
| `COMPLETED` | `"completed"` | All steps succeeded (terminal) |
| `FAILED` | `"failed"` | Any step failed (terminal) |
| `NEEDS_REVIEW` | `"needs_review"` | Non-idempotent step crashed without verify hook (terminal) |
| `CANCELLED` | `"cancelled"` | Cancelled via `cancel_workflow()` (terminal) |

### 4.2 StepConfig

```python
class StepConfig(BaseModel):
    """Base class for step configuration. Subclass with typed fields."""
```

Empty body. Users subclass with typed fields. All fields must be JSON-serializable for MongoDB round-tripping.

### 4.3 CheckResult

Return type from `completeness_check` handlers. Provides engine scheduling hints.

| Field | Type | Default | Description |
|---|---|---|---|
| `complete` | `bool` | `False` | Is the async work complete? |
| `retry_after` | `float \| None` | `None` | Override next poll interval (seconds) |
| `progress` | `float \| None` | `None` | 0.0-1.0, for logging/dashboards |
| `message` | `str \| None` | `None` | Human-readable status |

**Validator:** `_clamp_progress` — rejects `NaN`, `Inf`, and values outside `[0.0, 1.0]`.

### 4.4 StepResult

```python
class StepResult(BaseModel):
    """Base class for step results. Subclass with typed fields."""
    error: str | None = None
    completed_at: datetime | None = None
```

Users subclass with typed fields. The engine sets `completed_at` on completion if not already set.

### 4.5 Step

A single step in a workflow DAG.

#### Identity and Configuration

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | required | Unique within workflow |
| `handler` | `str` | required | Dotted path to callable (e.g. `"myapp.steps.validate"`) |
| `config` | `StepConfig \| None` | `None` | Typed step configuration instance |
| `config_type` | `str \| None` | `None` | Dotted path to StepConfig subclass (auto-populated) |
| `result` | `StepResult \| None` | `None` | Typed step result instance |
| `result_type` | `str \| None` | `None` | Dotted path to StepResult subclass (auto-populated) |

#### Status and Execution

| Field | Type | Default | Description |
|---|---|---|---|
| `status` | `StepStatus` | `PENDING` | Current lifecycle state |
| `attempt` | `int` | `0` | Current execution attempt number |
| `retry_policy` | `RetryPolicy` | `RetryPolicy()` | Via `Field(default_factory=RetryPolicy)` |
| `step_timeout` | `float` | `0` | Per-attempt timeout in seconds; 0 = no timeout |

**Validator:** `_validate_timeout` — rejects `step_timeout < 0`.

#### Dependencies

| Field | Type | Default | Description |
|---|---|---|---|
| `depends_on` | `list[str] \| None` | `None` | Step names this depends on; `None` = sequential default; `[]` = root step |

Resolved by `Workflow._resolve_and_validate_depends_on` during validation.

#### Distributed Locking

| Field | Type | Default | Description |
|---|---|---|---|
| `locked_by` | `str \| None` | `None` | Lock owner identifier (engine instance_id) |
| `lock_expires_at` | `datetime \| None` | `None` | When lock becomes available |
| `fence_token` | `int` | `0` | Fence/generation token for optimistic locking |

#### Async Polling

| Field | Type | Default | Description |
|---|---|---|---|
| `is_async` | `bool` | `False` | Whether this is an async (polling) step |
| `completeness_check` | `str \| None` | `None` | Dotted path to check callable |
| `verify_completion` | `str \| None` | `None` | Dotted path to verify callable (crash recovery) |
| `idempotent` | `bool` | `True` | Safe to re-run on recovery? |
| `poll_policy` | `PollPolicy \| None` | `None` | Only needed for async steps; engine defaults to `PollPolicy()` when `None` |
| `poll_count` | `int` | `0` | Number of polls executed so far |
| `poll_started_at` | `datetime \| None` | `None` | When polling began (for timeout calculation) |
| `next_poll_at` | `datetime \| None` | `None` | When step is next eligible for poll claim |
| `last_poll_at` | `datetime \| None` | `None` | Timestamp of last poll attempt |
| `current_poll_interval` | `float \| None` | `None` | Current backoff interval (persisted across claims) |
| `last_poll_progress` | `float \| None` | `None` | Last reported progress 0.0-1.0 |
| `last_poll_message` | `str \| None` | `None` | Last reported status message |

#### Model Validator: `_set_type_paths`

Post-validation (`mode="after"`). Auto-populates `config_type` and `result_type` from the Python class path when the config/result is a subclass (not the base `StepConfig`/`StepResult`). Format: `"{cls.__module__}.{cls.__qualname__}"`.

### 4.6 Workflow

A persistent, multi-step workflow with a dependency DAG.

| Field | Type | Default | Description |
|---|---|---|---|
| `id` | `str` | `uuid4().hex` | 32-char hex UUID |
| `name` | `str` | required | Human-readable workflow name |
| `status` | `WorkflowStatus` | `PENDING` | Current lifecycle state |
| `steps` | `list[Step]` | `[]` | DAG of steps |
| `created_at` | `datetime` | `datetime.now(UTC)` | Creation timestamp |
| `updated_at` | `datetime` | `datetime.now(UTC)` | Last update timestamp |

#### Validators

Executed in declaration order:

1. **`_validate_unique_step_names`** (`model_validator`, `mode="after"`) — Rejects duplicate step names. Raises `ValueError` listing duplicates.

2. **`_resolve_and_validate_depends_on`** (`model_validator`, `mode="after"`):
   - If `depends_on is None` for step at index `i`: set to `[steps[i-1].name]` if `i > 0`, else `[]`
   - Reject self-references and unknown step names
   - Detect cycles via Kahn's algorithm (see Section 6)
   - Raises `ValueError` on violations

#### Methods

| Method | Signature | Description |
|---|---|---|
| `is_terminal()` | `-> bool` | True if status in `{COMPLETED, FAILED, NEEDS_REVIEW, CANCELLED}` |
| `step_by_name(name)` | `-> Step \| None` | Linear search for step by name |
| `ready_steps()` | `-> list[Step]` | PENDING steps with all deps COMPLETED and not locked |
| `pollable_steps()` | `-> list[Step]` | BLOCKED steps where `next_poll_at <= now` and not locked |
| `active_steps()` | `-> list[Step]` | Steps in SUBMITTED, RUNNING, or BLOCKED |
| `all_steps_terminal()` | `-> bool` | True if all steps are COMPLETED or FAILED (False if empty) |
| `all_steps_completed()` | `-> bool` | True if all steps are COMPLETED (False if empty) |
| `has_failed_step()` | `-> bool` | True if any step is FAILED |

#### Helper Functions (module-level)

- **`_utcnow() -> datetime`** — Returns `datetime.now(UTC)`.
- **`_tz_safe_le(a, b) -> bool`** — Compares two datetimes, normalizing to naive UTC if timezone awareness differs. MongoDB drivers may return naive or aware datetimes.
- **`_is_unlocked(step) -> bool`** — True if `locked_by is None` OR (`lock_expires_at is not None` AND `lock_expires_at <= now`). Note: if `locked_by` is set but `lock_expires_at` is None, the step is considered locked (returns False).
- **`_new_id() -> str`** — Returns `uuid.uuid4().hex`.

---

## 5. State Machines

### 5.1 Step State Machine

```
PENDING
  |
  | [claim + submit_step_by_name]
  v
SUBMITTED  <-- crash boundary (write-ahead marker)
  |
  | [mark_step_running_by_name]
  v
RUNNING
  |
  +--[sync: complete_step_by_name]--> COMPLETED
  |
  +--[async: block_step_by_name]--> BLOCKED --+
  |                                           |
  +--[exception: fail_step_by_name]--> FAILED |
                                              |
         +----[poll: complete_step_by_name]---+---> COMPLETED
         |                                    |
         +----[timeout/max_polls/errors]------+---> FAILED
         |                                    |
         +----[schedule_next_poll_by_name]----+  (stays BLOCKED, reschedule)

Recovery paths:
  SUBMITTED/RUNNING --[reset_step_by_name]--> PENDING  (idempotent re-run)
```

**Terminal states:** COMPLETED, FAILED.

### 5.2 Workflow State Machine

```
PENDING
  |
  | [first step claimed via try_claim_step]
  v
RUNNING
  |
  +--[try_complete_workflow: all steps COMPLETED]--> COMPLETED
  |
  +--[try_fail_workflow: any step FAILED]----------> FAILED
  |
  +--[try_needs_review_workflow]-------------------> NEEDS_REVIEW
  |
  +--[cancel_workflow]-----------------------------> CANCELLED
```

**Terminal states:** COMPLETED, FAILED, NEEDS_REVIEW, CANCELLED.

---

## 6. Dependency Resolution Algorithm

Implemented in `Workflow._resolve_and_validate_depends_on` using Kahn's algorithm:

```
FUNCTION resolve_and_validate(steps):
    IF steps is empty: RETURN

    step_names = {s.name for s in steps}

    # 1. Resolve sequential defaults
    FOR i, step IN enumerate(steps):
        IF step.depends_on IS None:
            step.depends_on = [steps[i-1].name] IF i > 0 ELSE []

    # 2. Validate references
    FOR step IN steps:
        FOR dep IN step.depends_on:
            IF dep == step.name: RAISE "self-reference"
            IF dep NOT IN step_names: RAISE "unknown step"

    # 3. Cycle detection via Kahn's algorithm
    in_degree = {s.name: 0 for s in steps}
    dependents = {s.name: [] for s in steps}
    FOR step IN steps:
        FOR dep IN step.depends_on:
            dependents[dep].append(step.name)
            in_degree[step.name] += 1

    queue = [name for name, deg in in_degree.items() if deg == 0]
    visited = 0
    WHILE queue is not empty:
        node = queue.pop()
        visited += 1
        FOR child IN dependents[node]:
            in_degree[child] -= 1
            IF in_degree[child] == 0:
                queue.append(child)

    IF visited != len(steps): RAISE "cycle detected"
```

---

## 7. Decorators and Handler Registry

### 7.1 Global Registry

`_STEP_REGISTRY: dict[str, Callable]` — maps dotted handler names to callables. Populated by decorators; `get_handler()` falls back to dynamic import via `importlib.import_module` and caches the result.

### 7.2 `@step` Decorator

```python
@step(retry=None, idempotent=True, needs_context=False)
```

**Handler signature:**
```python
async def handler(config: StepConfig, results: dict[str, StepResult]) -> StepResult
# With context:
async def handler(config: StepConfig, results: dict[str, StepResult], ctx: dict) -> StepResult
```

**Attaches `_step_meta` to the function:**
```python
{
    "handler": "{fn.__module__}.{fn.__qualname__}",
    "retry": RetryPolicy(),           # from parameter or default
    "is_async": False,
    "idempotent": True,               # from parameter
    "needs_context": False,           # from parameter
}
```

Registers the function in `_STEP_REGISTRY` using the generated handler name.

### 7.3 `@async_step` Decorator

```python
@async_step(retry=None, idempotent=True, needs_context=False, poll=None, completeness_check=None)
```

**Handler signature:** Same as `@step`. Handler should submit external work and return immediately with a `StepResult` subclass (e.g., containing a job_id).

**Additional `_step_meta` keys:**
```python
{
    ...,
    "is_async": True,
    "poll": PollPolicy(),             # from parameter or default
    "completeness_check": str | None, # resolved via _resolve_check_name
}
```

**`_resolve_check_name(check)`:** Accepts `str` (passthrough), `Callable` (auto-registers in `_STEP_REGISTRY`, returns `"{fn.__module__}.{fn.__qualname__}"`), or `None` (returns None).

### 7.4 `@completeness_check` Decorator

```python
@completeness_check(needs_context=False, retry=None)
```

**Handler signature:**
```python
async def check(config: StepConfig, results: dict, result: StepResult) -> CheckResult | dict | bool
# With context:
async def check(config, results, result, ctx: dict) -> CheckResult | dict | bool
```

Creates an async wrapper that:
1. Calls the original function and awaits if coroutine
2. Normalizes the return value via `_normalize_check_result`

**Wrapper `_step_meta`:**
```python
{
    "handler": "{fn.__module__}.{fn.__qualname__}",
    "is_completeness_check": True,
    "needs_context": False,           # from parameter
    "retry": RetryPolicy(),           # from parameter or default
}
```

The **wrapper** (not the original function) is registered in `_STEP_REGISTRY`.

### 7.5 Return Value Normalization

`_normalize_check_result(raw) -> CheckResult`:
- `CheckResult` — passthrough
- `dict` — `CheckResult.model_validate(dict)`
- `bool` — `CheckResult(complete=bool)`
- Anything else — raises `TypeError`

### 7.6 `get_handler(name) -> Callable`

1. If `name` in `_STEP_REGISTRY`: return immediately
2. Split `name` at last `.` into `(module_path, func_name)`
3. If no module path: raise `ValueError`
4. `importlib.import_module(module_path)`, `getattr(mod, func_name)`
5. Cache in `_STEP_REGISTRY` and return

---

## 8. Retry Mechanism

`retrying_from_policy(policy: RetryPolicy) -> AsyncRetrying`:

```python
AsyncRetrying(
    stop=stop_after_attempt(policy.max_attempts),
    wait=wait_exponential(
        multiplier=policy.wait_multiplier,
        min=policy.wait_seconds,
        max=policy.wait_max,
    ),
    reraise=True,
)
```

**Used in two places:**
1. Step handler execution (`_run_step_with_retry`)
2. Completeness check execution (`_poll_once`)

**Backoff formula:** `wait = min(wait_multiplier^attempt * wait_seconds, wait_max)` (tenacity's `wait_exponential` semantics: `min=` is the minimum wait, not the base).

---

## 9. Persistence Layer (MongoWorkflowStore)

### 9.1 Constructor

```python
MongoWorkflowStore(
    db: AsyncIOMotorDatabase,
    lock_ttl_seconds: int = 30,
    collection_name: str = "workflows",
    audit_logger: AuditLogger | None = None,      # defaults to NullAuditLogger
    instance_id: str | None = None,
    operation_timeout_ms: int = 30_000,            # must be > 0
)
```

**Internal state:**
- `_col` — Motor collection reference (`db[collection_name]`)
- `_lock_ttl` — lock TTL seconds
- `_audit` — AuditLogger instance
- `_instance_id` — engine instance identifier
- `_audit_tasks: set[asyncio.Task]` — fire-and-forget audit write tasks
- `_step_index_cache: OrderedDict[tuple[str, str], int]` — LRU cache
- `_op_timeout` — MongoDB operation timeout in milliseconds

### 9.2 MongoDB Document Schema

Workflows are serialized via `model_dump(mode="python", serialize_as_any=True)`. The `id` field is renamed to `_id` for MongoDB. Key serialization rules:
- **Datetimes** are stored as native Python `datetime` objects (not ISO strings) — `mode="python"` ensures this for MongoDB query compatibility
- **`serialize_as_any=True`** ensures subclass fields are included (StepConfig/StepResult subclasses)
- `config` and `result` are stored as plain dicts with `config_type`/`result_type` holding the dotted class path

### 9.3 Document Conversion (`_doc_to_workflow`)

Deserializes a MongoDB document back to a typed `Workflow`:

1. Pop `_id` from document, store as `id`
2. For each step in `doc["steps"]`:
   - If `config_type` is set and `config` is a dict: import the class via `_import_class(config_type)`, instantiate with `(**config)`
   - If `result_type` is set and `result` is a dict: import the class via `_import_class(result_type)`, instantiate with `(**result)`
3. Validate entire document as `Workflow` model

**`_import_class(dotted_path)`:** Splits at last `.`, calls `importlib.import_module(module)`, `getattr(mod, class_name)`. Raises `ImportError` if class not found.

### 9.4 Indexes

`ensure_indexes()` creates:
- Single field index on `"status"`
- Compound index on `[("status", 1), ("steps.status", 1)]`

### 9.5 CRUD Operations

#### `insert(workflow) -> str`
- Serialize: `workflow.model_dump(mode="python", serialize_as_any=True)`
- Pop `id`, set as `_id`
- `_col.insert_one(doc)`
- Emit `WORKFLOW_CREATED` audit event
- Return `workflow.id`

#### `get(workflow_id) -> Workflow | None`
- `_col.find_one({"_id": workflow_id}, max_time_ms=op_timeout)`
- Convert via `_doc_to_workflow` or return None

#### `list_workflows(status?, name?, limit=50, skip=0) -> list[Workflow]`
- Filter by optional `status` and `name`
- Sort by `created_at` descending
- Skip/limit pagination

#### `count_by_status() -> dict[str, int]`
- Aggregation: `[{"$group": {"_id": "$status", "count": {"$sum": 1}}}]`

#### `delete_workflow(workflow_id) -> bool`
- Only deletes terminal workflows (COMPLETED, FAILED, NEEDS_REVIEW, CANCELLED)
- Invalidates step index cache entries for this workflow

#### `find_needs_review() -> list[str]`
- Returns IDs of workflows with `status = "needs_review"`

### 9.6 Step Index Cache

`_step_index(workflow_id, step_name) -> int | None`

Resolves step name to array index within the `steps` array. Uses an LRU cache (`OrderedDict`):

1. **Cache hit:** move entry to end (LRU), return cached index
2. **Cache miss:** query `{"_id": workflow_id}` with projection `{"steps.name": 1}`
3. Prime cache for **all** steps in the workflow at once
4. Evict oldest entries if cache exceeds `_step_index_cache_max` (10,000) via `popitem(last=False)`

Step lists are immutable after workflow creation, so entries never go stale.

### 9.7 Fenced Step Updates

`_fenced_step_update_by_name(workflow_id, step_name, step_fence_token, updates) -> Workflow | None`

All step state transitions use this method. It guarantees atomicity and fence token protection:

```
findOneAndUpdate(
    filter: {
        _id: workflow_id,
        steps.{idx}.name: step_name,
        steps.{idx}.fence_token: expected_fence_token
    },
    update: {
        $set: {
            steps.{idx}.{field}: value,  // for each field in updates
            updated_at: now
        }
    },
    returnDocument: AFTER
)
```

Returns `Workflow | None`. `None` means fence rejected (lock was stolen or step already advanced).

The step array index (`idx`) is resolved via `_step_index()`.

### 9.8 Fence Token Mechanism

The fence token is the core of the optimistic locking protocol:

1. **On claim:** `fence_token` is atomically incremented via `$inc` in the same `findOneAndUpdate` that acquires the lock
2. **During execution:** all writes for that step include `fence_token: N` in the filter
3. **Lock stolen:** if another instance claims the step (incrementing the token), the stale writer's updates silently fail (return `None` / no documents matched)
4. **Force release:** also increments the token, invalidating any in-flight writer

No centralized lock service is required — MongoDB is the source of truth.

### 9.9 Per-Step Distributed Locking

#### `try_claim_step(workflow_id, step_name, instance_id) -> tuple[Workflow, int] | None`

Atomically claims a step for execution:

```
findOneAndUpdate(
    filter: {
        _id: workflow_id,
        status: {$in: ["pending", "running"]},
        steps.{idx}.name: step_name,
        steps.{idx}.status: {$in: ["pending", "blocked", "submitted", "running"]},
        $or: [
            {steps.{idx}.locked_by: null},
            {steps.{idx}.lock_expires_at: {$lt: now}}
        ]
    },
    update: {
        $set: {
            steps.{idx}.locked_by: instance_id,
            steps.{idx}.lock_expires_at: now + lock_ttl,
            status: "running",
            updated_at: now
        },
        $inc: {steps.{idx}.fence_token: 1}
    },
    returnDocument: AFTER
)
```

Returns `(Workflow, fence_token)` on success; `None` if already locked or not claimable.

Emits `STEP_CLAIMED` audit event with `locked_by`, `fence_token_before`, and `fence_token` (after).

#### `heartbeat_step(workflow_id, step_name, instance_id, step_fence_token, *, emit_audit=False) -> bool`

Extends lock TTL. Matches on `locked_by` AND `fence_token`. When `emit_audit=True`, emits a `HEARTBEAT` audit event after a successful renewal:

```
findOneAndUpdate(
    filter: {
        _id: workflow_id,
        steps.{idx}.name: step_name,
        steps.{idx}.locked_by: instance_id,
        steps.{idx}.fence_token: step_fence_token
    },
    update: {
        $set: {
            steps.{idx}.lock_expires_at: now + lock_ttl,
            updated_at: now
        }
    }
)
```

Returns `True` if matched (lock still held), `False` if lock stolen.

#### `release_step_lock(workflow_id, step_name, instance_id, step_fence_token) -> bool`

Clears lock fields. Matches on `locked_by` AND `fence_token`. Emits `LOCK_RELEASED` audit event on success:

```
update: {
    $set: {
        steps.{idx}.locked_by: null,
        steps.{idx}.lock_expires_at: null,
        updated_at: now
    }
}
```

#### `force_release_step_lock(workflow_id, step_name, *, anomaly_type=None) -> bool`

Unconditional release used by sweep only. **Ignores** fence token, **increments** it. Emits `SWEEP_ANOMALY` (when `anomaly_type` provided) and `LOCK_FORCE_RELEASED` audit events on success:

```
findOneAndUpdate(
    filter: {_id: workflow_id, steps.{idx}.name: step_name},
    update: {
        $set: {
            steps.{idx}.locked_by: null,
            steps.{idx}.lock_expires_at: null,
            updated_at: now
        },
        $inc: {steps.{idx}.fence_token: 1}
    }
)
```

### 9.10 Step State Transition Methods

All methods use `_fenced_step_update_by_name` internally. Each emits an audit event on success.

#### `submit_step_by_name(workflow_id, step_name, fence, attempt) -> Workflow | None`
- **Updates:** `status = SUBMITTED`, `attempt = attempt`
- **Audit:** `STEP_SUBMITTED`

#### `mark_step_running_by_name(workflow_id, step_name, fence, attempt, *, max_attempts=None) -> Workflow | None`
- **Updates:** `status = RUNNING`, `attempt = attempt`
- **Audit:** `STEP_RUNNING` with `attempt` and `max_attempts`

#### `complete_step_by_name(workflow_id, step_name, fence, result=None, result_type=None, poll_count=None, last_poll_at=None, last_poll_progress=None, last_poll_message=None, audit_event_type=None, step_status_before="running", recovery_action=None) -> Workflow | None`
- **Updates:** `status = COMPLETED`, `result` (serialized via `model_dump`), `result_type`, poll fields
- **Audit:** `audit_event_type` or default `STEP_COMPLETED`, with `result_summary` and optional `recovery_action`

#### `fail_step_by_name(workflow_id, step_name, fence, result, audit_event_type=None, step_status_before="running", poll_count=None, poll_elapsed_seconds=None) -> Workflow | None`
- **Updates:** `status = FAILED`, `result` (serialized), `result_type = None`
- **Audit:** `audit_event_type` or default `STEP_FAILED`, with error extracted from result

#### `block_step_by_name(workflow_id, step_name, fence, result, result_type, poll_started_at, next_poll_at, current_poll_interval, poll_count=0, audit_event_type=None, recovery_action=None) -> Workflow | None`
- **Updates:** `status = BLOCKED`, `result`, `result_type`, `poll_started_at`, `next_poll_at`, `current_poll_interval`, `poll_count`
- **Audit:** `audit_event_type` or default `STEP_BLOCKED`

#### `schedule_next_poll_by_name(workflow_id, step_name, fence, poll_count, last_poll_at, next_poll_at, current_poll_interval, last_poll_progress=None, last_poll_message=None) -> Workflow | None`
- **Updates:** poll scheduling fields (stays BLOCKED)
- **Audit:** `POLL_CHECKED`

#### `reset_step_by_name(workflow_id, step_name, fence, status=PENDING) -> Workflow | None`
- **Updates:** `status = status.value`
- **Audit:** `RECOVERY_RESET`

### 9.11 Workflow Status Transitions

#### `try_complete_workflow(workflow_id) -> Workflow | None`

Atomically completes workflow if all steps are COMPLETED. Uses double-negation query:

```
findOneAndUpdate(
    filter: {
        _id: workflow_id,
        status: "running",
        steps.0: {$exists: true},
        steps: {$not: {$elemMatch: {status: {$ne: "completed"}}}}
    },
    update: {$set: {status: "completed", updated_at: now}},
    returnDocument: AFTER
)
```

Emits `WORKFLOW_COMPLETED`.

#### `try_fail_workflow(workflow_id) -> Workflow | None`

```
findOneAndUpdate(
    filter: {_id: workflow_id, status: "running"},
    update: {$set: {status: "failed", updated_at: now}},
    returnDocument: AFTER
)
```

Emits `WORKFLOW_FAILED`.

#### `try_needs_review_workflow(workflow_id) -> Workflow | None`

```
findOneAndUpdate(
    filter: {_id: workflow_id, status: "running"},
    update: {$set: {status: "needs_review", updated_at: now}},
    returnDocument: AFTER
)
```

Emits `RECOVERY_NEEDS_REVIEW` with `recovery_action = "needs_review"`.

#### `cancel_workflow(workflow_id) -> Workflow | None`

1. Pre-fetch workflow to determine step count (for per-index updates)
2. Build per-step updates: clear `locked_by` and `lock_expires_at` for all steps
3. Build per-step fence increments: `steps.{i}.fence_token += 1` for all steps
4. Atomic update: `status = "cancelled"`, only if current status is not terminal
5. Emits `WORKFLOW_CANCELLED`

### 9.12 Discovery Queries

#### `find_claimable_steps(limit=10) -> list[tuple[str, str]]`

Returns `(workflow_id, step_name)` pairs ready for claiming:

1. Query MongoDB for workflows in `PENDING` or `RUNNING` status that have steps in `PENDING` or `BLOCKED` status
2. Over-fetch by `limit * 3`
3. For each workflow, apply Python-side filtering:
   - `wf.ready_steps()` — PENDING steps with all dependencies COMPLETED and unlocked
   - `wf.pollable_steps()` — BLOCKED steps where `next_poll_at <= now` and unlocked
4. Accumulate results up to `limit`

#### `find_anomalies(step_stuck_seconds=300.0, limit=20) -> list[dict]`

Returns list of `{"workflow_id": str, "step_name": str | None, "anomaly": str}`.

Three anomaly types detected via aggregation pipelines:

1. **`step_stuck_in_transient_state`** — Steps in SUBMITTED/RUNNING where `workflow.updated_at < now - step_stuck_seconds`
2. **`stale_step_lock`** — Steps with `locked_by` set, `lock_expires_at` in the past, and stale `updated_at`
3. **`orphaned_workflow`** — Workflow status is RUNNING, but **no** step has a non-terminal status (double-negation: `$not: {$elemMatch: {status: {$in: non_terminal}}}`). Returns `(workflow_id, None)`.

Deduplicates `(workflow_id, step_name)` pairs across categories.

### 9.13 Audit Emission (Store)

`_emit(event_type, wf, *, step=None, idx=None, step_status_before=None, workflow_status_before=None, fence_token_before=None, fence_token_override=None, **kwargs)`

1. Constructs `AuditEvent` with full context from workflow/step state
2. If audit logger has `assign_sequence`, calls it synchronously (causal ordering)
3. Creates fire-and-forget `asyncio.Task` for `audit.emit(event)`
4. Tracks task in `_audit_tasks` with done callback for cleanup

Public passthrough: `emit(event)` — assigns sequence if available, schedules fire-and-forget.

`drain_audit_tasks(timeout=5.0)` — awaits all pending audit tasks with timeout. Called during shutdown.

---

## 10. Workflow Engine

### 10.1 Constructor

```python
WorkflowEngine(
    store: MongoWorkflowStore,
    instance_id: str | None = None,           # default: "{hostname}-{uuid4().hex[:8]}"
    claim_interval: float = 5.0,
    heartbeat_interval: float = 10.0,
    sweep_interval: float = 60.0,
    step_stuck_seconds: float = 300.0,
    max_concurrent: int = 5,
    log_heartbeats: bool = False,
    context: dict[str, Any] | None = None,    # injected into handlers
)
```

**Internal state:**
- `_active: dict[tuple[str, str], _ActiveStep]` — `(wf_id, step_name) -> (task, fence)`
- `_shutdown_event: asyncio.Event` — signals graceful shutdown
- `_tasks: list[asyncio.Task]` — background loop tasks
- `_context: dict[str, Any]` — context injected into handlers

**`_ActiveStep`** is a `NamedTuple` with fields `task: asyncio.Task` and `fence: int`.

### 10.2 Lifecycle

#### `start()`
1. Call `store.ensure_indexes()`
2. Register POSIX signal handlers (SIGTERM, SIGINT) → `asyncio.ensure_future(self.stop())`
   - Silently skipped on Windows (`NotImplementedError`, `OSError`)
3. Create three named background tasks: `_claim_loop`, `_heartbeat_loop`, `_sweep_loop`

#### `stop()`
1. Set `_shutdown_event`
2. Snapshot `_active` (needed because `_run_step`'s finally block pops entries)
3. Cancel all active step tasks
4. Await cancellation with `return_exceptions=True`
5. Release all step locks for each snapshot entry via `release_step_lock`
6. Clear `_active`
7. Cancel background tasks and await
8. Call `store.drain_audit_tasks()` to drain pending audit writes

**Context manager:** `__aenter__` calls `start()`, `__aexit__` calls `stop()`.

### 10.3 Claim Loop

Runs every `claim_interval` seconds (interruptible by shutdown via `_wait`).

```
WHILE not shutdown:
    slots = max_concurrent - len(active)
    IF slots > 0:
        claimable = store.find_claimable_steps(limit=slots)
        FOR (wf_id, step_name) IN claimable:
            IF (wf_id, step_name) already in active: SKIP
            result = store.try_claim_step(wf_id, step_name, instance_id)
            IF result is not None:
                (wf, fence) = result
                task = create_task(_run_step(wf_id, step_name, fence))
                active[(wf_id, step_name)] = _ActiveStep(task, fence)
    WAIT claim_interval
```

All exceptions are caught, logged, and suppressed.

### 10.4 Heartbeat Loop

Runs every `heartbeat_interval` seconds.

```
WHILE not shutdown:
    FOR (wf_id, step_name), active IN snapshot(active.items()):
        ok = store.heartbeat_step(wf_id, step_name, instance_id, active.fence)
        IF ok AND log_heartbeats:
            emit HEARTBEAT event
        IF NOT ok:                     # lock stolen
            active.task.cancel()
            await with 5s timeout (suppress exceptions)
            active.pop((wf_id, step_name))  # safety net
    WAIT heartbeat_interval
```

### 10.5 Sweep Loop

Runs every `sweep_interval` seconds.

```
WHILE not shutdown:
    anomalies = store.find_anomalies(step_stuck_seconds)
    FOR entry IN anomalies:
        IF step is locally active: SKIP
        IF workflow is locally active: SKIP

        IF anomaly == "orphaned_workflow":
            Re-validate all steps terminal
            IF has_failed_step: try_fail_workflow
            ELSE: try_complete_workflow
            Emit SWEEP_ANOMALY event

        ELIF step_name exists:   # stuck or stale lock
            force_release_step_lock
            Emit SWEEP_ANOMALY + LOCK_FORCE_RELEASED events
    WAIT sweep_interval
```

### 10.6 Step Execution (`_run_step`)

Core execution method for a single step. Called as an asyncio task from the claim loop.

```
FUNCTION _run_step(wf_id, step_name, step_fence):
  TRY:
    wf = store.get(wf_id)
    IF wf is None OR wf.status is terminal:
        release_step_lock_safe; RETURN

    step = wf.step_by_name(step_name)
    IF step is None: release_step_lock_safe; RETURN
    IF shutdown: RETURN  // stop() handles lock release

    // --- Recovery path ---
    IF step.status IN (SUBMITTED, RUNNING):
        step = _recover_step(wf, step_name, step, step_fence)
        IF step is None: RETURN  // lock lost or needs_review

    // --- Poll path ---
    IF step.status == BLOCKED:
        poll_result = _poll_once(wf, step_name, step, step_fence)
        IF poll_result == "complete":
            Refresh wf and step; fall through to completion
        ELIF poll_result IN ("released", "failed", "lost_lock"):
            RETURN

    IF step.status == COMPLETED:
        try_complete_workflow(wf_id); RETURN

    // --- Normal execution: PENDING step ---
    wf = submit_step_by_name(wf_id, step_name, fence, attempt=step.attempt+1)
    IF wf is None: RETURN  // fence rejected

    TRY:
        handler = get_handler(step.handler)
        result_data = _run_step_with_retry(handler, step, wf_id, step_name, fence)
        (result, result_type) = _wrap_handler_return(result_data)

        Refresh wf  // check if sibling step failed/cancelled workflow
        IF wf.status is terminal (CANCELLED/FAILED/NEEDS_REVIEW):
            release_step_lock_safe; RETURN

    EXCEPT Exception:
        fail_result = StepResult(error=traceback, completed_at=now)
        fail_step_by_name(wf_id, step_name, fence, fail_result)
        try_fail_workflow(wf_id)
        RETURN

    // --- Async step: block and release ---
    IF step.is_async AND step.completeness_check:
        block_step_by_name(wf_id, step_name, fence,
            result, result_type,
            poll_started_at=now,
            next_poll_at=now + policy.interval,
            current_poll_interval=policy.interval)
        _release_and_emit_lock; RETURN

    // --- Sync step: complete ---
    complete_step_by_name(wf_id, step_name, fence, result, result_type)
    try_complete_workflow(wf_id)

  EXCEPT CancelledError: log
  EXCEPT Exception: release_step_lock_safe
  FINALLY:
    active.pop((wf_id, step_name))
```

### 10.7 Handler Calling (`_call_handler`)

```python
async def _call_handler(self, handler, *args):
    meta = getattr(handler, "_step_meta", {})
    if meta.get("needs_context", False):
        result = handler(*args, self._context)
    else:
        result = handler(*args)
    if asyncio.iscoroutine(result):
        return await result
    return result
```

Supports both sync and async handlers. Never uses `inspect.signature`.

### 10.8 Handler Return Normalization (`_wrap_handler_return`)

- Must return `StepResult` subclass; raises `HandlerError` otherwise
- Sets `completed_at = datetime.now(UTC)` if not already set
- Computes `result_type = "{cls.__module__}.{cls.__qualname__}"` for non-base subclasses; `None` for base `StepResult`

### 10.9 Retry Execution (`_run_step_with_retry`)

```
retrying = retrying_from_policy(step.retry_policy)
attempt_num = 0

FOR attempt IN retrying:
    attempt_num += 1
    wf = mark_step_running_by_name(wf_id, step_name, fence, attempt=attempt_num)
    IF wf is None: RAISE FenceRejectedError

    coro = _call_handler(handler, step.config, _build_results(wf, step_name))
    IF step.step_timeout > 0:
        TRY: return await wait_for(coro, timeout=step.step_timeout)
        EXCEPT TimeoutError:
            Emit STEP_TIMEOUT event
            RAISE TimeoutError("Step timed out after {timeout} seconds")
    ELSE:
        return await coro

// Unreachable with reraise=True, but satisfies type checker
RAISE RetryExhaustedError
```

### 10.10 Results Dict Construction (`_build_results`)

```python
def _build_results(wf, step_name) -> dict[str, StepResult]:
    step = wf.step_by_name(step_name)
    deps = step.depends_on or []
    return {
        dep_name: dep.result
        for dep_name in deps
        if (dep := wf.step_by_name(dep_name)) is not None and dep.result is not None
    }
```

Only includes dependency steps' results where result is not None.

### 10.11 Helpers

- **`_wait(seconds)`** — Sleeps interruptibly via `asyncio.wait_for(shutdown_event.wait(), timeout=seconds)`. Suppresses `TimeoutError`.
- **`_release_step_lock_safe(wf_id, step_name, fence)`** — Best-effort lock release; suppresses all exceptions via `contextlib.suppress(Exception)`.
- **`_release_and_emit_lock(wf, wf_id, step_name, fence, key)`** — Pops from `_active`, releases lock, emits `LOCK_RELEASED` event.
- **`_fail_poll_step(...)`** — Fails a BLOCKED step during polling, emits diagnostic event, calls `try_fail_workflow`. Always returns `"failed"`.
- **`_is_workflow_active(wf_id)`** — True if any step of this workflow is in `_active`.

---

## 11. Recovery Protocol

Recovery runs when `_run_step` finds a step in SUBMITTED or RUNNING state, indicating the previous engine instance crashed mid-execution.

### Decision Tree

```
1. Emit RECOVERY_STARTED audit event

2. IF verify_completion hook is defined:
     Call verify_completion(config, dependency_results, step.result or StepResult())
     Normalize result via _normalize_check_result
     IF check_result.complete:
       Mark step COMPLETED (audit: RECOVERY_VERIFIED, recovery_action="verified")
       RETURN step
     // Otherwise: fall through

3. IF step is async AND has completeness_check AND has a result:
     Call completeness_check(config, dependency_results, step.result)
     IF check_result.complete:
       Mark step COMPLETED (audit: RECOVERY_VERIFIED, recovery_action="verified")
       RETURN step
     ELSE (not complete):
       Transition to BLOCKED with poll scheduling
       (audit: RECOVERY_BLOCKED, recovery_action="blocked")
       RETURN step  // will be picked up by poll cycle
     IF check throws: log warning, fall through

4. IF step.idempotent:
     Reset to PENDING via reset_step_by_name
     (audit: RECOVERY_RESET, recovery_action="reset")
     RETURN step  // will be re-executed

5. ELSE (non-idempotent, no verify hook):
     Mark workflow NEEDS_REVIEW via try_needs_review_workflow
     RETURN None  // manual intervention required
```

---

## 12. Claim-Poll-Release Cycle (Async Steps)

Full lifecycle for a step with `is_async=True` and `completeness_check` defined:

### Phase 1: Initial Submission

1. Claim loop discovers PENDING step with dependencies satisfied
2. `try_claim_step` — acquires lock, increments `fence_token`
3. `submit_step_by_name` — PENDING -> SUBMITTED, increments `attempt`
4. `mark_step_running_by_name` — SUBMITTED -> RUNNING
5. Handler executes (submits external work), returns `StepResult` subclass
6. `block_step_by_name` — RUNNING -> BLOCKED, sets:
   - `poll_started_at = now`
   - `next_poll_at = now + policy.interval`
   - `current_poll_interval = policy.interval`
   - `poll_count = 0`
7. Release step lock — `_release_and_emit_lock`
8. Step is now unlocked and BLOCKED. The engine returns.

### Phase 2: Poll Discovery

1. Claim loop calls `find_claimable_steps`
2. Store returns BLOCKED steps where `next_poll_at <= now` and unlocked (via `Workflow.pollable_steps()`)
3. `try_claim_step` — acquires lock, increments `fence_token`

### Phase 3: Poll Execution (`_poll_once`)

1. **Timeout check:** if `policy.timeout > 0` and `(now - poll_started_at) >= policy.timeout`, fail step (`POLL_TIMEOUT`)
2. **Max polls check:** if `policy.max_polls > 0` and `poll_count >= policy.max_polls`, fail step (`POLL_MAX_EXCEEDED`)
3. **Execute completeness check** with its own `RetryPolicy` (from `@completeness_check(retry=...)`)
4. If all retries exhausted (check throws): fail step (`POLL_CHECK_ERRORS_EXCEEDED`)
5. Parse `CheckResult`

### Phase 4: Result Handling

**If complete (`check_result.complete == True`):**
- `complete_step_by_name` — BLOCKED -> COMPLETED, with poll metadata
- `try_complete_workflow` — check if entire workflow is done
- Return `"complete"`

**If not complete:**
- Compute next interval:
  ```
  IF check_result.retry_after is not None:
      next_wait = check_result.retry_after
  ELSE:
      next_wait = current_interval
      current_interval = min(current_interval * backoff_multiplier, max_interval)
  ```
- `schedule_next_poll_by_name` — update `poll_count`, `last_poll_at`, `next_poll_at`, `current_poll_interval`, progress/message
- Release step lock
- Return `"released"` — claim loop will rediscover when `next_poll_at` passes

---

## 13. Audit System

### 13.1 AuditEventType Enum

26 event types (all `str, Enum`):

**Workflow lifecycle:**
| Value | String |
|---|---|
| `WORKFLOW_CREATED` | `"workflow_created"` |
| `WORKFLOW_CLAIMED` | `"workflow_claimed"` |
| `WORKFLOW_COMPLETED` | `"workflow_completed"` |
| `WORKFLOW_FAILED` | `"workflow_failed"` |
| `WORKFLOW_CANCELLED` | `"workflow_cancelled"` |

**Step lifecycle:**
| Value | String |
|---|---|
| `STEP_CLAIMED` | `"step_claimed"` |
| `STEP_SUBMITTED` | `"step_submitted"` |
| `STEP_RUNNING` | `"step_running"` |
| `STEP_COMPLETED` | `"step_completed"` |
| `STEP_FAILED` | `"step_failed"` |
| `STEP_BLOCKED` | `"step_blocked"` |
| `STEP_ADVANCED` | `"step_advanced"` |
| `STEP_TIMEOUT` | `"step_timeout"` |

**Polling:**
| Value | String |
|---|---|
| `POLL_CHECKED` | `"poll_checked"` |
| `POLL_TIMEOUT` | `"poll_timeout"` |
| `POLL_MAX_EXCEEDED` | `"poll_max_exceeded"` |
| `POLL_CHECK_ERRORS_EXCEEDED` | `"poll_check_errors_exceeded"` |

**Locking:**
| Value | String |
|---|---|
| `LOCK_RELEASED` | `"lock_released"` |
| `LOCK_FORCE_RELEASED` | `"lock_force_released"` |
| `HEARTBEAT` | `"heartbeat"` |

**Recovery:**
| Value | String |
|---|---|
| `RECOVERY_STARTED` | `"recovery_started"` |
| `RECOVERY_VERIFIED` | `"recovery_verified"` |
| `RECOVERY_BLOCKED` | `"recovery_blocked"` |
| `RECOVERY_RESET` | `"recovery_reset"` |
| `RECOVERY_NEEDS_REVIEW` | `"recovery_needs_review"` |

**Sweep:**
| Value | String |
|---|---|
| `SWEEP_ANOMALY` | `"sweep_anomaly"` |

### 13.2 AuditEvent Model

| Category | Field | Type | Default |
|---|---|---|---|
| **Identity** | `id` | `str` | `uuid4().hex` |
| | `workflow_id` | `str` | required |
| | `workflow_name` | `str` | required |
| | `event_type` | `AuditEventType` | required |
| | `timestamp` | `datetime` | `datetime.now(UTC)` |
| | `sequence` | `int` | `0` (assigned by logger) |
| **Actor** | `instance_id` | `str \| None` | `None` |
| | `fence_token` | `int \| None` | `None` |
| | `fence_token_before` | `int \| None` | `None` |
| **Workflow** | `workflow_status` | `str \| None` | `None` |
| | `workflow_status_before` | `str \| None` | `None` |
| **Step** | `step_index` | `int \| None` | `None` |
| | `step_name` | `str \| None` | `None` |
| | `step_handler` | `str \| None` | `None` |
| | `step_status` | `str \| None` | `None` |
| | `step_status_before` | `str \| None` | `None` |
| | `is_async` | `bool \| None` | `None` |
| | `idempotent` | `bool \| None` | `None` |
| | `step_depends_on` | `list[str] \| None` | `None` |
| **Retry** | `attempt` | `int \| None` | `None` |
| | `max_attempts` | `int \| None` | `None` |
| **Poll** | `poll_count` | `int \| None` | `None` |
| | `poll_progress` | `float \| None` | `None` |
| | `poll_message` | `str \| None` | `None` |
| | `next_poll_at` | `datetime \| None` | `None` |
| | `current_poll_interval` | `float \| None` | `None` |
| | `poll_elapsed_seconds` | `float \| None` | `None` |
| **Result** | `result_summary` | `dict \| None` | `None` |
| | `error` | `str \| None` | `None` |
| | `error_traceback` | `str \| None` | `None` |
| **Lock** | `locked_by` | `str \| None` | `None` |
| | `lock_released` | `bool` | `False` |
| **Recovery** | `recovery_action` | `str \| None` | `None` |
| **Anomaly** | `anomaly_type` | `str \| None` | `None` |
| **Diff** | `fields_changed` | `dict \| None` | `None` |

### 13.3 AuditLogger Protocol

Runtime-checkable protocol with three methods:

| Method | Signature | Description |
|---|---|---|
| `assign_sequence` | `(event: AuditEvent) -> None` | Synchronous. Assign causal sequence number before scheduling |
| `emit` | `async (event: AuditEvent) -> None` | Record an audit event |
| `get_events` | `async (workflow_id, event_type?) -> list[AuditEvent]` | Retrieve events ordered by sequence |

### 13.4 NullAuditLogger

No-op implementation. `assign_sequence`: pass. `emit`: async pass. `get_events`: returns `[]`.

### 13.5 MongoAuditLogger

**Constructor:** `MongoAuditLogger(db, collection_name="workflow_audit_log", max_pending=100)`

**State:**
- `_col` — Motor collection
- `_pending: set[asyncio.Task]` — in-flight writes
- `_sequences: dict[str, int]` — per-workflow sequence counters
- `dropped_count: int` — total dropped events due to backpressure

**`ensure_indexes()`:** Creates compound index `(workflow_id, sequence)` and single index `timestamp`.

**`assign_sequence(event)`:** Synchronous. Increments per-workflow counter, assigns to `event.sequence`. Called **before** fire-and-forget to lock in causal order.

**`emit(event)`:**
1. Backpressure check: if `len(_pending) >= max_pending`, drop event, log warning, increment `dropped_count`, return
2. If `event.sequence == 0`, assign now (fallback)
3. Serialize: `event.model_dump(mode="python", exclude_none=True)`, rename `id` to `_id`
4. Fire-and-forget: `asyncio.create_task(self._safe_insert(doc))`
5. Track in `_pending` with done callback for cleanup

**`_safe_insert(doc)`:** `_col.insert_one(doc)`, catch all exceptions and log warning (fail-silent).

**`get_events(workflow_id, event_type?)`:**
- Query: `{workflow_id, [event_type]}`, sort by `sequence` ascending
- Rename `_id` to `id`, validate as `AuditEvent`

### 13.6 Emission Patterns

**Store emits** (via internal `_emit` helper): `WORKFLOW_CREATED`, `WORKFLOW_COMPLETED`, `WORKFLOW_FAILED`, `WORKFLOW_CANCELLED`, `STEP_CLAIMED`, `STEP_SUBMITTED`, `STEP_RUNNING`, `STEP_COMPLETED`, `STEP_FAILED`, `STEP_BLOCKED`, `POLL_CHECKED`, `RECOVERY_RESET`, `RECOVERY_NEEDS_REVIEW`

**All audit events are emitted by the store.** Store write methods (`complete_step_by_name`, `fail_step_by_name`, `try_claim_step`, etc.) emit events as part of the write. Lock management methods (`release_step_lock`, `force_release_step_lock`, `heartbeat_step`) emit events after successful updates. Diagnostic events without a DB write are emitted via `store.emit_recovery_started()` and `store.emit_step_timeout()`. The engine never constructs `AuditEvent` objects directly.

---

## 14. Audit Report Generator

`generate_audit_report(events: list[AuditEvent], *, workflow: Workflow | None = None) -> str`

Produces a self-contained HTML execution report from audit events. No external dependencies (CSS embedded in `<style>` tag).

**Input:** List of `AuditEvent` objects ordered by sequence (as returned by `MongoAuditLogger.get_events`). Optional `workflow` parameter: when provided, the dependency graph shows all steps including those not yet executed (unexecuted steps appear greyed-out with `pending` state).

**Output:** Complete HTML5 document string.

**Report sections:**
1. Header — workflow name and subtitle
2. Summary banner — final status, step count, duration, instance count
3. Dependency graph — visual horizontal DAG with concurrency tiers
4. Discovery section — first step claim (workflow entry point)
5. Per-step sections — 3-column layout: flow timeline, chronological transitions, MongoDB document diff
6. Completion section — workflow terminal event
7. State transitions table — from/to/trigger for all transitions

**Algorithms:**
- `_group_events` — separates workflow-level events from per-step events (keyed by `step_index`)
- `_extract_dep_info` — extracts dependency map from `step_depends_on` fields
- `_compute_tiers` — computes concurrency tiers (groups of parallelizable steps) via depth-first DAG analysis
- `_compute_lane_groups` — detects consecutive parallel tiers forming independent lanes
- `_compute_step_states` — determines final state of each step from event sequence
- `_workflow_final_state` — determines workflow final state from terminal events

**Visual design:** Dark theme (`#0a0e17` background), color-coded states (green=success, red=failure, amber=async/polling, indigo=transitions, purple=workflow-level).

---

## 15. Public API Surface

All exports from `workchain/__init__.py`:

**Models:**
`Workflow`, `Step`, `StepConfig`, `StepResult`, `RetryPolicy`, `PollPolicy`, `CheckResult`, `StepStatus`, `WorkflowStatus`

**Store:**
`MongoWorkflowStore`

**Engine:**
`WorkflowEngine`

**Decorators:**
`step`, `async_step`, `completeness_check`

**Audit:**
`AuditEvent`, `AuditEventType`, `AuditLogger`, `MongoAuditLogger`, `NullAuditLogger`

**Report:**
`generate_audit_report`

**Exceptions:**
`WorkchainError`, `StepError`, `StepTimeoutError`, `RetryExhaustedError`, `HandlerError`, `LockError`, `FenceRejectedError`, `RecoveryError`
