# workchain

Programmatic construction and execution of persistent, multi-step workflows.

## Features

- **Dependency-based step execution** -- steps declare dependencies via `depends_on`; independent steps run concurrently across engine instances
- **Two step modes** -- synchronous (`@step`) and async with polling (`@async_step`)
- **MongoDB persistence** -- workflow state survives process restarts via async motor driver
- **Distributed safety** -- per-step TTL-based locks with fence tokens; multiple instances can work on different steps of the same workflow simultaneously
- **Crash recovery** -- write-ahead logging, verify hooks, and idempotent re-run strategies
- **Retry policies** -- per-step exponential backoff via tenacity
- **Audit logging** -- structured event log for every state change, enough to reconstruct execution history
- **Engine context** -- optional dependency injection dict forwarded to handlers (DB clients, HTTP sessions, services) without framework coupling

## Installation

```bash
pip install workchain                # core library only
pip install workchain[fastapi]       # + reusable FastAPI router
pip install workchain[server]        # + standalone server with dashboard
pip install -e ".[dev]"              # development (editable + test deps)
```

## Quick Start

### 1. Define steps

Use `@step` for synchronous handlers, `@async_step` for handlers that submit external work, and `@completeness_check` for poll functions. Handler names are auto-generated from module + qualname:

```python
from workchain import StepConfig, StepResult, step, async_step, completeness_check, PollPolicy, CheckResult

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
async def check_provisioning(config, results, result: ProvisionResult) -> CheckResult:
    return CheckResult(complete=False, progress=0.5)

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
async with WorkflowEngine(store) as engine:  # start + auto-stop
    ...  # engine runs claim loop, heartbeat, sweep
```

## Step Dependencies

Steps declare dependencies via `depends_on`. Without explicit dependencies, steps run sequentially (each depends on the previous). With `depends_on`, independent steps can execute concurrently across engine instances.

### Sequential (default)

Steps without `depends_on` automatically depend on the previous step:

```python
workflow = Workflow(
    name="pipeline",
    steps=[
        Step(name="extract", handler="myapp.steps.extract"),
        Step(name="transform", handler="myapp.steps.transform"),  # depends on "extract"
        Step(name="load", handler="myapp.steps.load"),            # depends on "transform"
    ],
)
```

### Concurrent (diamond pattern)

Use `depends_on` to express parallelism. In this example, `create_vpc` and `provision_database` run concurrently, while `deploy_application` waits for both:

```python
workflow = Workflow(
    name="infra",
    steps=[
        Step(name="create_vpc", handler="myapp.steps.create_vpc", depends_on=[]), # root step — runs immediately
        Step(name="provision_database", handler="myapp.steps.provision_db", depends_on=[]), # root step — runs in parallel
        Step(name="deploy_application", handler="myapp.steps.deploy", depends_on=["create_vpc", "provision_database"]), # waits for both
        Step(name="health_check", handler="myapp.steps.health_check", depends_on=["deploy_application"]), # runs after deploy
    ],
)
```

### Accessing dependency results

Handlers receive a `results` dict keyed by step name. Only results from completed dependencies are included:

```python
@step(depends_on=["create_vpc", "provision_database"])
async def deploy(config: DeployConfig, results: dict[str, StepResult]) -> DeployResult:
    vpc = cast(VpcResult, results["create_vpc"])
    db = cast(DatabaseResult, results["provision_database"])
    return DeployResult(deployment_id=start_deploy(vpc.vpc_id, db.endpoint))
```

### Declaring handler dependencies (`depends_on` on decorators)

The `depends_on` parameter on `@step` and `@async_step` declares which step results the handler requires. This serves as a **validation contract**: at workflow construction time, the `Workflow` model checks that each step's resolved `depends_on` includes every name the handler declares. Missing dependencies raise `ValueError` immediately rather than causing a `KeyError` at runtime.

```python
@step(depends_on=["validate_email"])
async def create_account(config: AccountConfig, results: dict[str, StepResult]) -> AccountResult:
    email = cast(ValidateEmailResult, results["validate_email"])
    # ...

@async_step(
    completeness_check=check_provisioning,
    poll=PollPolicy(interval=2.0),
    depends_on=["create_account"],
)
async def provision_resources(config: ProvisionConfig, results: dict[str, StepResult]) -> ProvisionResult:
    account = cast(AccountResult, results["create_account"])
    # ...

@step(depends_on=["validate_email", "create_account", "provision_resources"])
async def send_welcome_email(config: EmailConfig, results: dict[str, StepResult]) -> EmailResult:
    email = cast(ValidateEmailResult, results["validate_email"])
    account = cast(AccountResult, results["create_account"])
    # ...
```

If a workflow is constructed where `send_welcome_email` depends only on `["provision_resources"]`, the `Workflow` model raises:

```
ValueError: Step 'send_welcome_email' handler requires dependencies
['validate_email', 'create_account', 'provision_resources'] but step
depends_on is missing ['validate_email', 'create_account']
```

Handlers without `depends_on` (or `depends_on=None`) are unconstrained — no validation is performed. The handler's `depends_on` is also exposed via `HandlerDescriptor.depends_on` in the introspection API, which the workflow designer uses to auto-wire dependency edges when handlers are dropped onto the canvas.

### Validation

The `Workflow` model validates the dependency graph at construction time:
- Unknown step names in `depends_on` raise `ValueError`
- Self-references raise `ValueError`
- Cycles (A→B→C→A) are detected via topological sort and raise `ValueError`
- Handler-declared dependencies missing from the step's `depends_on` raise `ValueError`

### 3. FastAPI integration

**Option A: Reusable router** (`pip install workchain[fastapi]`)

Mount the contrib router for instant workflow CRUD + audit report endpoints:

```python
from workchain.contrib.fastapi import create_workchain_router

router = create_workchain_router(store, audit_logger)
app.include_router(router, prefix="/api/v1/workflows")
```

Provides: list (with search/filter/pagination), stats, analytics, activity feed, detail (full step + event data), get, cancel, and HTML audit report endpoints. Add your own workflow creation routes on top.

**Option B: Standalone server** (`pip install workchain[server]`)

A ready-to-deploy service with management dashboard:

```bash
MONGO_URI=mongodb://localhost:27017 hatch run server:serve
# Open http://localhost:8000
```

See [Workchain Server](#workchain-server) below.

**Option C: Manual wiring**

Use the lifespan context manager to start/stop the engine with your own app:

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

    async with WorkflowEngine(
        store,
        audit_logger=audit,
        context={"db": db, "store": store},
    ) as engine:
        app.state.engine = engine
        app.state.store = store
        app.state.audit = audit
        yield


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
async def check_deploy(config, results, result: DeployResult, ctx: dict[str, Any]) -> CheckResult:
    http = cast(httpx.AsyncClient, ctx["http"])
    resp = await http.get(f"/deployments/{result.job_id}")
    return CheckResult(complete=resp.json()["status"] == "ready")
```

Handlers without `needs_context=True` receive only the standard arguments. The engine reads decorator metadata -- no runtime parameter inspection.

## Step Types

### Sync steps (`@step`)

Execute the handler, persist the result, and complete the step -- all within a single lock hold:

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

@completeness_check()
async def check_deploy(config, results, result: DeployResult) -> CheckResult:
    status = get_deployment_status(result.job_id)
    return CheckResult(complete=status == "ready", progress=status.percent)
```

### Decorator defaults and explicit overrides

Arguments passed to `@step` / `@async_step` are automatically propagated onto the `Step` model at workflow construction time, so you rarely need to repeat them when building a workflow. The following decorator arguments flow onto the corresponding Step fields:

| Decorator argument | Step field |
|---|---|
| `retry` | `retry_policy` |
| `poll` | `poll_policy` |
| `depends_on` | `depends_on` |
| `idempotent` | `idempotent` |
| `completeness_check` | `completeness_check` |
| (implicit `is_async`) | `is_async` |

**Precedence rule: explicit `Step(...)` kwargs override decorator defaults.** If you want the decorator default, leave the Step field unset; if you want a per-workflow override, pass the field explicitly to the `Step` constructor.

```python
@step(retry=RetryPolicy(max_attempts=5, wait_seconds=2.0))
async def flaky_api_call(config, results): ...

# Uses the decorator's retry policy (max_attempts=5, wait_seconds=2.0)
Workflow(steps=[Step(name="call", handler="mypkg.flaky_api_call")])

# Overrides with a stricter policy just for this workflow
Workflow(steps=[
    Step(
        name="call",
        handler="mypkg.flaky_api_call",
        retry_policy=RetryPolicy(max_attempts=2, wait_seconds=0.5),
    ),
])
```

The same precedence applies to `WorkflowTemplate` and the designer's workflow drafts: template/draft fields that are left unset inherit the decorator defaults; explicitly set fields win. This is implemented in `Workflow._resolve_and_validate_depends_on` using Pydantic's `model_fields_set` to distinguish caller-supplied values from `default_factory` fallbacks. The propagation is **skipped for workflows loaded from MongoDB** (status ≠ `PENDING`), so persisted field values are never mutated by a subsequent library upgrade — stored workflows continue to run with whatever policy was in effect when they were created.

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

## Adaptive Polling with CheckResult.retry_after

A `completeness_check` can return `CheckResult(retry_after=...)` to override the next poll interval (in seconds). This is a one-shot override -- subsequent polls resume normal backoff unless `retry_after` is set again.

```python
@completeness_check()
async def check_training(config, results, result: TrainResult) -> CheckResult:
    status = await get_job_status(result.job_id)
    if status == "initializing":
        return CheckResult(complete=False, retry_after=5.0)   # poll again quickly
    if status == "running":
        return CheckResult(complete=False, retry_after=60.0)  # poll less frequently
    return CheckResult(complete=True)
```

When `retry_after` is `None` (the default), the engine applies the normal `backoff_multiplier` from the step's `PollPolicy`.

## Engine Tuning

The engine and store accept timing parameters that control the claim-poll-release cycle:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `claim_interval` | `5.0s` | How often the claim loop discovers ready steps |
| `heartbeat_interval` | `10.0s` | How often per-step locks are renewed |
| `sweep_interval` | `60.0s` | How often the sweep detects stuck steps and stale locks |
| `step_stuck_seconds` | `300.0s` | Threshold before a step is flagged as stuck |
| `max_concurrent` | `5` | Maximum steps processed concurrently per engine |
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

### FastAPI example

Wire the engine into FastAPI's lifespan so timers start/stop with the app:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
from workchain import MongoWorkflowStore, WorkflowEngine


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = AsyncIOMotorClient("mongodb://localhost:27017")
    db = client["myapp"]
    store = MongoWorkflowStore(db, lock_ttl_seconds=30)

    async with WorkflowEngine(
        store,
        claim_interval=2.0,
        sweep_interval=30.0,
        step_stuck_seconds=120.0,
        max_concurrent=10,
        context={"db": db, "store": store},
    ) as engine:
        app.state.engine = engine
        app.state.store = store
        yield


app = FastAPI(lifespan=lifespan)
```

## Crash Recovery: verify_completion

When the engine reclaims a step stuck in SUBMITTED or RUNNING state (after a crash), it runs a recovery cascade:

1. **`verify_completion`** -- if set, calls the hook to check if the step actually finished before the crash. If it returns `True`, the step is marked complete without re-running.
2. **`completeness_check`** (async steps) -- checks if the external submission went through. If so, transitions to BLOCKED for polling.
3. **Idempotent re-run** -- if the handler is marked `idempotent=True`, re-executes it.
4. **NEEDS_REVIEW** -- if none of the above apply, the step (and workflow) is flagged for manual intervention.

Set `verify_completion` on a step as a dotted-path string pointing to a handler:

```python
@step()
async def charge_payment(config: PaymentConfig, results: dict[str, StepResult]) -> PaymentResult:
    receipt = await payment_gateway.charge(config.amount)
    return PaymentResult(receipt_id=receipt.id)

@completeness_check()
async def verify_charge(config: PaymentConfig, results: dict[str, StepResult], result: PaymentResult) -> CheckResult:
    """Check if the charge went through by querying the payment gateway."""
    charged = await payment_gateway.has_receipt(result.receipt_id)
    return CheckResult(complete=charged)

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

The `verify_completion` handler receives `(config, results, result)` and returns a `CheckResult` (or `bool`/`dict` for convenience -- the `@completeness_check` decorator normalizes all return types). It supports `needs_context=True` for dependency injection.

## Handler Introspection

The `workchain.introspection` module exposes registered handlers as structured descriptors suitable for UIs and schema-aware tooling:

```python
from workchain.introspection import list_handlers, describe_handler

# List all registered handlers (excludes completeness checks by default)
for h in list_handlers():
    print(f"{h.name}  launchable={h.launchable}  category={h.category}")

# Get a specific handler descriptor
handler = describe_handler("myapp.steps.validate_input")
if handler and handler.launchable:
    print(handler.config_schema)   # JSON Schema for the handler's StepConfig subclass
    print(handler.result_schema)   # JSON Schema for the handler's StepResult subclass
```

A handler is **launchable** only when both its `config` and `result` type annotations are strict subclasses of `StepConfig` / `StepResult` and JSON schema extraction succeeds. The workflow designer UI uses this flag to enable or disable handler drag-and-drop.

The `depends_on` field exposes the handler's declared dependency requirements (from the `@step`/`@async_step` decorator). The designer uses this to auto-wire edges when handlers are dropped onto the canvas. Pass `include_checks=True` to `list_handlers()` or `describe_handler()` to include completeness check handlers in the results.

Pass `include_checks=True` to `list_handlers()` or `describe_handler()` to include completeness check handlers in the results.

## Workflow Templates

Templates are persistable, reusable workflow designs. Unlike live `Workflow` instances (which carry runtime state like step status, locks, and results), templates are **design-time artifacts** that can be stored, versioned, and launched into runnable workflows.

```python
from workchain.templates import WorkflowTemplate, StepTemplate, instantiate_template

template = WorkflowTemplate(
    name="data-pipeline",
    description="ETL pipeline with validation",
    steps=[
        StepTemplate(name="extract", handler="etl.steps.extract", config={"source": "s3"}),
        StepTemplate(name="transform", handler="etl.steps.transform", depends_on=["extract"]),
        StepTemplate(name="load", handler="etl.steps.load", depends_on=["transform"]),
    ],
)

# Instantiate into a runnable Workflow
workflow = instantiate_template(
    template,
    name_override="daily-etl-2026-04-12",
    config_overrides={"extract": {"source": "gcs"}},  # override per-step config
)
await store.insert(workflow)
```

Templates use **optimistic locking** via a `version` counter -- the store's `update_template` method accepts `expected_version` and returns `None` on version mismatch (409 Conflict via the API).

The server seeds 8 example templates on startup (Customer Onboarding, Data Pipeline ETL, CI/CD Pipeline, Media Processing, ML Training, Incident Response, Infrastructure Provisioning, Order Fulfillment). These are matched by name so user edits are never overwritten.

## Architecture

```
workchain/                          -- core library
├── models.py                       -- Pydantic models: Workflow, Step, StepConfig, StepResult, enums, policies
├── decorators.py                   -- @step / @async_step decorators + handler registry
├── engine.py                       -- WorkflowEngine: per-step claim loop, heartbeat, sweep, execution
├── store.py                        -- MongoWorkflowStore: persistence, per-step distributed locking
├── retry.py                        -- Retry utilities wrapping tenacity with RetryPolicy
├── audit.py                        -- AuditEvent model, AuditLogger protocol, MongoAuditLogger
├── audit_report.py                 -- HTML execution report generator from audit events
├── introspection.py                -- HandlerDescriptor + describe_handler/list_handlers (JSON schemas)
├── templates.py                    -- WorkflowTemplate / StepTemplate + instantiate_template
├── exceptions.py                   -- Exception hierarchy: WorkchainError, StepError, HandlerError, etc.
└── contrib/
    └── fastapi.py                  -- Optional FastAPI router (pip install workchain[fastapi])

workchain_server/                   -- standalone server (pip install workchain[server])
├── config.py                       -- Environment variable configuration via pydantic-settings
├── plugins.py                      -- Step handler discovery (entry points + env var)
├── app.py                          -- FastAPI app with engine lifecycle and router mounting
├── designer_router.py              -- /api/v1/handlers, /workflows (POST), /templates (CRUD + launch), /config
├── example_templates.py            -- 8 example WorkflowTemplates seeded into MongoDB on startup
├── frontend/                       -- React + Vite SPA source (dashboard + workflow designer)
└── static/app/                     -- built SPA assets (gitignored)
```

## Workchain Server

A standalone FastAPI service with a management dashboard for monitoring workflows.

```bash
pip install workchain[server]
MONGO_URI=mongodb://localhost:27017 hatch run server:serve
```

**Environment variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `MONGO_DATABASE` | `workchain` | Database name |
| `ENGINE_INSTANCE_ID` | auto-generated | Engine instance identifier |
| `ENGINE_CLAIM_INTERVAL` | `5.0` | Step discovery interval (seconds) |
| `ENGINE_HEARTBEAT_INTERVAL` | `10.0` | Lock heartbeat renewal interval (seconds) |
| `ENGINE_SWEEP_INTERVAL` | `60.0` | Anomaly detection sweep interval (seconds) |
| `ENGINE_STEP_STUCK_SECONDS` | `300.0` | Threshold before flagging a step as stuck (seconds) |
| `ENGINE_MAX_CONCURRENT` | `5` | Max concurrent steps per engine |
| `WORKCHAIN_PLUGINS` | | Comma-separated module paths to import at startup |
| `SERVER_TITLE` | `Workchain Server` | Dashboard page title |

**Plugin system:** Step handlers are registered by importing modules that use `@step`/`@async_step` decorators. Two mechanisms:
1. Python entry points under the `workchain.plugins` group
2. `WORKCHAIN_PLUGINS` env var with comma-separated module paths

### Designer API

The server mounts a designer router at `/api/v1` that powers the upcoming drag-and-drop workflow designer UI:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/config` | Server metadata (title + instance ID) for the SPA frontend |
| `GET` | `/api/v1/handlers` | List registered step handlers with JSON schemas for their `StepConfig` / `StepResult` types |
| `POST` | `/api/v1/workflows` | Create and persist a `Workflow` from a designer draft (handler refs + raw config dicts). Returns 422 with per-step errors on validation failure. |
| `GET` | `/api/v1/templates` | List workflow templates sorted by `updated_at` descending |
| `POST` | `/api/v1/templates` | Persist a new `WorkflowTemplate` |
| `GET` | `/api/v1/templates/{id}` | Fetch a single template |
| `PUT` | `/api/v1/templates/{id}` | Update a template via optimistic locking (`expected_version`). Returns 409 on version mismatch. |
| `DELETE` | `/api/v1/templates/{id}` | Delete a template |
| `POST` | `/api/v1/templates/{id}/launch` | Instantiate a template into a runnable `Workflow` (supports `name_override` + per-step `config_overrides`) |

**Web UI**: the server serves a React SPA at `/` with five pages — a full-bleed landing page (`/`, hero + feature grid + code sample + template-catalog CTA), a dashboard (`/dashboard`, key metrics + status breakdown + activity feed + template catalog), a workflows browser (`/workflows`, search + filter + paginated table), a workflow detail page (`/workflows/:id`, dependency graph + expandable step cards with error diagnostics + event timeline), and a drag-and-drop workflow designer (`/designer`). Build it once before running the server for the first time:

```bash
hatch run frontend:install   # npm install (one-time)
hatch run frontend:build     # tsc + vite build -> workchain_server/static/app/
hatch run server:serve       # open http://localhost:8000
```

For hot reload during frontend development:

```bash
hatch run server:serve       # FastAPI on :8000 (terminal 1)
hatch run frontend:dev       # Vite on :5173 with /api proxy (terminal 2)
```

The SPA uses React + Vite + react-router-dom + [React Flow](https://reactflow.dev/) for the canvas and [`@rjsf/core`](https://rjsf-team.github.io/react-jsonschema-form/) (Bootstrap 4 theme) for schema-driven config forms.

> ⚠️ **No auth.** The designer API can launch arbitrary registered workflows. Run the server behind a reverse proxy that enforces authentication before exposing it beyond localhost.

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
