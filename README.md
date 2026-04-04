# workchain

Programmatic construction and execution of persistent, multi-step workflows.

## Features

- **Sequential step execution** -- steps run in order with typed configs and results
- **Two step modes** -- synchronous (`@step`) and async with polling (`@async_step`)
- **MongoDB persistence** -- workflow state survives process restarts via async motor driver
- **Distributed safety** -- TTL-based locks with fence tokens ensure only one instance processes a workflow at a time
- **Crash recovery** -- write-ahead logging, verify hooks, and idempotent re-run strategies
- **Retry policies** -- per-step exponential backoff via tenacity
- **Audit logging** -- structured event log for every state change, enough to reconstruct execution history
- **Engine context** -- optional dependency injection dict forwarded to handlers (DB clients, HTTP sessions, services) without framework coupling

## Installation

```bash
pip install -e ".[dev]"
```

## Quick Start

### 1. Define steps

Use `@step` for synchronous handlers, `@async_step` for handlers that submit external work, and `@completeness_check` for poll functions. Handler names are auto-generated from module + qualname:

```python
from workchain import StepConfig, StepResult, step, async_step, completeness_check, PollPolicy, PollHint

class ValidateConfig(StepConfig):
    email: str

class ValidateResult(StepResult):
    validated: bool

@step()
async def validate_input(config: ValidateConfig, results: dict[str, StepResult]) -> ValidateResult:
    if "@" not in config.email:
        raise ValueError(f"Invalid email: {config.email}")
    return ValidateResult(validated=True)

class ProvisionResult(StepResult):
    job_id: str

@completeness_check()
async def check_provisioning(config, results, result: ProvisionResult) -> PollHint:
    return PollHint(complete=False, progress=0.5)

@async_step(
    completeness_check=check_provisioning,
    poll=PollPolicy(interval=5.0, timeout=300.0),
)
async def provision(config: StepConfig, results: dict[str, StepResult]) -> ProvisionResult:
    return ProvisionResult(job_id="job_123")
```

### 2. Build and run a workflow

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

### 3. FastAPI integration

Use the lifespan context manager to start/stop the engine with the app:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
from workchain import MongoAuditLogger, MongoWorkflowStore, WorkflowEngine


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = AsyncIOMotorClient("mongodb://localhost:27017")
    db = client["myapp"]

    store = MongoWorkflowStore(db, lock_ttl_seconds=30)
    audit = MongoAuditLogger(db)

    engine = WorkflowEngine(
        store,
        audit_logger=audit,
        context={"db": db, "store": store},
    )
    
    await engine.start()
    
    app.state.engine = engine
    app.state.store = store
    app.state.audit = audit
    
    yield
    
    await engine.stop()


app = FastAPI(lifespan=lifespan)
```

## Engine Context (Dependency Injection)

Pass external resources to step handlers without globals or framework coupling:

```python
# Wire up at engine creation
engine = WorkflowEngine(store, context={"db": db_client, "http": http_session})

# Handlers opt in via needs_context=True
@step(needs_context=True)
async def fetch_user(config: UserConfig, results: dict[str, StepResult], ctx: dict[str, Any]) -> UserResult:
    db = cast(AsyncIOMotorDatabase, ctx["db"])
    user = await db.users.find_one({"id": config.user_id})
    return UserResult(name=user["name"])

# Completeness checks opt in the same way
@completeness_check(needs_context=True)
async def check_deploy(config, results, result: DeployResult, ctx: dict[str, Any]) -> PollHint:
    http = cast(httpx.AsyncClient, ctx["http"])
    resp = await http.get(f"/deployments/{result.job_id}")
    return PollHint(complete=resp.json()["status"] == "ready")
```

Handlers without `needs_context=True` receive only the standard arguments. The engine reads decorator metadata -- no runtime parameter inspection.

## Step Types

### Sync steps (`@step`)

Execute the handler, persist the result, advance to the next step -- all within a single lock hold:

```python
@step()
async def send_email(config: EmailConfig, results: dict[str, StepResult]) -> EmailResult:
    # Access preceding step results
    account = cast(AccountResult, results["create_account"])
    send(to=account.email)
    return EmailResult(sent=True)
```

### Async steps (`@async_step`)

Submit external work, release the lock, and poll until complete. Any engine instance can pick up each poll cycle:

```python
@async_step(
    completeness_check=check_deploy,
    poll=PollPolicy(interval=10.0, backoff_multiplier=1.5, timeout=600.0),
)
async def deploy(config: DeployConfig, results: dict[str, StepResult]) -> DeployResult:
    job_id = start_deployment(config.environment)
    return DeployResult(job_id=job_id)  # engine sets BLOCKED, releases lock

async def check_deploy(config, results, result: DeployResult) -> PollHint:
    status = get_deployment_status(result.job_id)
    return PollHint(complete=status == "ready", progress=status.percent)
```

## Audit Logging

Pass a `MongoAuditLogger` to capture every state change:

```python
from workchain import MongoAuditLogger

audit = MongoAuditLogger(client["myapp"])
engine = WorkflowEngine(store, audit_logger=audit)

# Later: retrieve structured events
events = await audit.get_events(workflow_id)
```

## Query API

The store provides methods to list and count workflows:

```python
# List workflows with optional filters (sorted by created_at descending)
workflows = await store.list_workflows(
    status=WorkflowStatus.RUNNING,
    name="onboarding",
    limit=20,
    skip=0,
)

# Get workflow counts grouped by status
counts = await store.count_by_status()
# {"pending": 5, "running": 2, "completed": 10}
```

## Adaptive Polling with PollHint.retry_after

A `completeness_check` can return `PollHint(retry_after=...)` to override the next poll interval (in seconds). This is a one-shot override -- subsequent polls resume normal backoff unless `retry_after` is set again.

```python
@completeness_check()
async def check_training(config, results, result: TrainResult) -> PollHint:
    status = await get_job_status(result.job_id)
    if status == "initializing":
        return PollHint(complete=False, retry_after=5.0)   # poll again quickly
    if status == "running":
        return PollHint(complete=False, retry_after=60.0, progress=status.percent)
    return PollHint(complete=True)
```

When `retry_after` is `None` (the default), the engine applies the normal `backoff_multiplier` from the step's `PollPolicy`.

## Engine Tuning

The engine and store accept timing parameters that control the claim-poll-release cycle:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `claim_interval` | `5.0s` | How often the claim loop discovers new workflows |
| `heartbeat_interval` | `10.0s` | How often locks are renewed on active workflows |
| `sweep_interval` | `60.0s` | How often the sweep detects stuck steps and stale locks |
| `step_stuck_seconds` | `300.0s` | Threshold before a step is flagged as stuck |
| `max_concurrent` | `5` | Maximum workflows processed concurrently per engine |
| `lock_ttl_seconds` | `30` | Lock expiry time (set on `MongoWorkflowStore`) |

```python
store = MongoWorkflowStore(db, lock_ttl_seconds=60)

engine = WorkflowEngine(
    store,
    claim_interval=2.0,        # faster discovery
    heartbeat_interval=15.0,   # less frequent lock renewal
    sweep_interval=30.0,       # faster anomaly detection
    step_stuck_seconds=120.0,  # flag stuck steps sooner
    max_concurrent=10,         # handle more workflows per instance
)
```

## Crash Recovery: verify_completion

When the engine reclaims a workflow with a step stuck in SUBMITTED or RUNNING state (after a crash), it runs a recovery cascade:

1. **`verify_completion`** -- if set, calls the hook to check if the step actually finished before the crash. If it returns `True`, the step is marked complete without re-running.
2. **`completeness_check`** (async steps) -- checks if the external submission went through. If so, transitions to BLOCKED for polling.
3. **Idempotent re-run** -- if the handler is marked `idempotent=True`, re-executes it.
4. **NEEDS_REVIEW** -- if none of the above apply, the workflow is flagged for manual intervention.

Set `verify_completion` on a step as a dotted-path string pointing to a handler:

```python
@step()
async def charge_payment(config: PaymentConfig, results: dict[str, StepResult]) -> PaymentResult:
    receipt = await payment_gateway.charge(config.amount)
    return PaymentResult(receipt_id=receipt.id)

@step()
async def verify_charge(config: PaymentConfig, results: dict[str, StepResult], result: PaymentResult) -> bool:
    """Check if the charge went through by querying the payment gateway."""
    return await payment_gateway.has_receipt(result.receipt_id)

workflow = Workflow(
    name="checkout",
    steps=[
        Step(
            name="charge",
            handler="myapp.steps.charge_payment",
            config=PaymentConfig(amount=99.99),
            verify_completion="myapp.steps.verify_charge",
        ),
    ],
)
```

The `verify_completion` handler receives `(config, results, result)` and returns a bool. It supports `needs_context=True` for dependency injection.

## Architecture

```
workchain/
├── models.py       -- Pydantic models: Workflow, Step, StepConfig, StepResult, enums, policies
├── decorators.py   -- @step / @async_step decorators + handler registry
├── engine.py       -- WorkflowEngine: claim loop, heartbeat, sweep, execution
├── store.py        -- MongoWorkflowStore: persistence, distributed locking
├── retry.py        -- Retry utilities wrapping tenacity with RetryPolicy
└── audit.py        -- AuditEvent model, AuditLogger protocol, MongoAuditLogger
```

## Claude Code Commands

The project includes slash commands for [Claude Code](https://claude.com/claude-code) in `.claude/commands/`:

| Command | Description |
|---------|-------------|
| `/add-step <name>` | Scaffold a new step handler (sync or async) with config/result models |
| `/new-workflow <name>` | Scaffold a new workflow example with steps, builder, and CLI runner |
| `/test` | Run `hatch test` + `hatch fmt` and report results |

## Test Harness

A FastAPI web app for interacting with the example workflows:

```bash
hatch run harness:serve
# Open http://localhost:8000
```

The landing page lets you create workflow instances, watch their progress, and generate HTML audit execution reports.

## Running Tests

```bash
hatch test
```

Or directly:

```bash
pip install -e ".[dev]"
pytest tests/
```

Tests use [mongomock-motor](https://github.com/michaelkryukov/mongomock_motor) -- no real MongoDB instance required.
