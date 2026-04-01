# workchain — Claude Agent Context

## What this is

`workchain` is a Python library for programmatic construction and execution of **persistent, multi-step workflows**. Steps execute sequentially, state is persisted to MongoDB via `motor`, and distributed execution is safe via TTL-based locks + fence tokens (optimistic locking).

## Architecture

```
workchain/
├── models.py       — Pydantic models: Workflow, Step, enums, policies
├── decorators.py   — @step / @async_step decorators + handler registry
├── engine.py       — WorkflowEngine: claim loop, heartbeat, sweep, execution
├── store.py        — MongoWorkflowStore: persistence + distributed locking
├── retry.py        — Retry utilities wrapping tenacity with RetryPolicy
└── example.py      — Complete working example (user onboarding)
```

## Key design decisions

**Sequential step execution**
- Steps execute in order (`current_step_index` advances linearly)
- `Workflow` holds the full step list and shared `context` dict
- No DAG — steps are a flat list

**Two step modes**
- **Sync steps** (`@step`): execute handler, persist result, advance immediately
- **Async steps** (`@async_step`): submit work, set BLOCKED, release lock, poll `completeness_check` on subsequent claims until complete

**Distributed safety**
- Lock acquisition via atomic `findOneAndUpdate` — only one instance wins
- `fence_token` increments on each claim; all writes are fenced (`{"fence_token": N}`)
- Heartbeat loop renews lock TTL; stale locks expire and are reclaimed
- Sweep loop detects anomalies (stuck steps, stale locks, unadvanaced completed steps)

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

Use the `@step` and `@async_step` decorators. Handlers receive `(config: dict, context: dict)` and return a result dict.

```python
from workchain import step, async_step, RetryPolicy, PollPolicy

@step(name="validate_input")
async def validate_input(config: dict, context: dict) -> dict:
    email = config["email"]
    if "@" not in email:
        raise ValueError(f"Invalid email: {email}")
    return {"validated": True, "email": email}

async def check_provisioning(config, context, result) -> bool | dict:
    # Return True, or a PollHint dict with {complete, progress, message, retry_after}
    return {"complete": False, "progress": 0.5, "message": "In progress"}

@async_step(
    name="provision",
    completeness_check=check_provisioning,
    poll=PollPolicy(interval=5.0, timeout=300.0),
)
async def provision(config: dict, context: dict) -> dict:
    job_id = start_provisioning(context["user_id"])
    return {"job_id": job_id}
```

## Building and running a workflow

```python
from motor.motor_asyncio import AsyncIOMotorClient
from workchain import Workflow, Step, StepConfig, WorkflowEngine, MongoWorkflowStore

client = AsyncIOMotorClient("mongodb://localhost:27017")
store = MongoWorkflowStore(client["myapp"], lock_ttl_seconds=30)

workflow = Workflow(
    name="onboarding",
    steps=[
        Step(name="validate", handler="validate_input", config=StepConfig(data={"email": "a@b.com"})),
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

- All context values and step result dicts must be JSON-serializable.
- `fence_token` is managed by the store — never set it manually.
- `WorkflowEngine.instance_id` should be unique per process (auto-generated if omitted).
- Step handlers must be async functions registered via decorators or importable by dotted path.
- `PollHint.progress` must be between 0.0 and 1.0.

## Files to modify with care

- `store.py` — the lock acquisition query and fence-guarded writes are carefully crafted; changes risk race conditions
- `engine.py` `_recover_step()` — recovery logic handles multiple crash scenarios; understand all paths before changing
- `models.py` — changing field names affects all persisted MongoDB documents
