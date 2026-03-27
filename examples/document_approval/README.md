# Document Approval Workflow Example

A realistic end-to-end example demonstrating **all three step types** of `workchain`: `Step`, `EventStep`, and `PollingStep`.

## Scenario

An organization receives documents for processing. The workflow:

1. **Fetch Document** (Step) — Retrieves document metadata from a remote API
2. **Approval** (EventStep) — Suspends and waits for human approval via external signal
3. **Process Job** (PollingStep) — Starts an async background job, polls for completion
4. **Notify** (Step) — Sends result notification email

The workflow DAG is linear:
```
fetch → approve → process → notify
```

If the document is rejected at approval, the notify step still runs but doesn't send an email.

## Features Demonstrated

### ✓ All Step Types

- **Step** — Synchronous execution, immediate return
- **EventStep** — Suspends workflow, waits for external signal (correlation_id-based resume)
- **PollingStep** — Periodically checks condition, supports configurable interval and timeout

### ✓ Distributed Safety

- **Atomic Lease Acquisition** — Only one runner processes a workflow at a time
- **Heartbeat** — Background thread renews lease every `ttl/2` seconds; dies with process
- **Optimistic Locking** — Concurrent modifications detected and surfaced, never silently lost
- **MongoDB Persistence** — Workflows survive runner crashes; multiple runners can claim work

### ✓ Context Flow

- Upstream step outputs stored in shared `Context`
- Downstream steps access via `context.step_output("step_id")`
- JSON-serializable enforcement ensures MongoDB round-trip safety

### ✓ Failure Propagation

- Steps can declare `on_dependency_failure: "fail" | "skip"`
- If a dependency fails, dependents with policy `fail` also fail; those with `skip` are skipped

## Running the Example

### Prerequisites

```bash
pip install -e ".[dev]"  # installs mongomock-motor for mock MongoDB
```

### Auto-Approve Mode (Quick Demo)

```bash
cd /path/to/workchain
python -m examples.document_approval.example --auto-approve
```

Output:
```
================================================================================
DOCUMENT APPROVAL WORKFLOW — AUTO-APPROVE MODE
================================================================================

✓ Created WorkflowRun: 67a8f2...

--- Executing: Fetch → Approval (will suspend) ---

  ✓ Fetched document 'DOC-12345' from https://api.example.com/documents
================================================================================
WorkflowRun: document_approval v1.0.0
  ID: 67a8f2...
  Status: running
  ...
  ⏸  Suspended workflow, awaiting approval (correlation_id: approval-DOC-12345-1711...)

--- Resuming: Approval with auto-approval ---

  ✓ APPROVED by demo@example.com

--- Continuing: Process job → Notify ---

  ⚙ Started async job (job_id: job-1711...)
  🔄 Poll check #1: Job job-1711... still running...
  🔄 Poll check #2: Job job-1711... still running...
  ✓ Poll check #3: Job job-1711... completed!
  📧 Sent email to user@example.com

✓ Workflow COMPLETED
```

### Manual Resume Mode (Educational)

```bash
python -m examples.document_approval.example --manual-resume
```

This mode suspends at the approval step and prints instructions for manually resuming:

```
--- Manual Resume Instructions ---
The workflow is now suspended waiting for external approval.
To resume it manually, call:

  await runner.resume(
      correlation_id="approval-DOC-12345-1711...",
      payload={
          "approved": True,
          "approver": "jane@example.com",
          "notes": "Looks good, approved."
      }
  )
```

For demo purposes, it auto-resumes after 2 seconds.

## Code Structure

```
document_approval/
├── __init__.py      — Package marker
├── steps.py         — Step definitions (FetchDocumentStep, ApprovalStep, ProcessJobStep, SendNotificationStep)
├── example.py       — Main executable, runner loop, pretty-printing
└── README.md        — This file
```

### steps.py

Four custom step classes:

1. **FetchDocumentStep(Step)** — Fetches document, returns metadata as output
2. **ApprovalStep(EventStep)** — Suspends with correlation_id, implements on_resume() to process decision
3. **ProcessJobStep(PollingStep)** — Starts job, checks completion, supports timeout
4. **SendNotificationStep(Step)** — Sends email based on approval decision

Each class includes extensive docstrings explaining:
- What it does
- Which step type it is
- Key concepts it demonstrates
- How it would work in a real scenario

### example.py

Main entry point with two execution modes:

1. **Auto-Approve** (`--auto-approve`) — Automatically approves, demonstrates full workflow flow
2. **Manual Resume** (`--manual-resume`) — Suspends at approval, prints instructions for manual resume

Both modes:
- Create a `MongoWorkflowStore` with `mongomock-motor` (in-memory, no DB)
- Build the workflow DAG
- Tick the `WorkflowRunner` in a loop
- Pretty-print state after each step
- Handle EventStep resumption
- Handle PollingStep checks with polling interval

## Key Concepts Explained

### EventStep & Correlation IDs

When `ApprovalStep.execute()` returns `StepResult.suspend(correlation_id="approval-...")`, the runner:
1. Stores correlation_id in `StepRun.resume_correlation_id`
2. Marks step status as `suspended`
3. Marks workflow status as `suspended`
4. Releases the lease

Later, an external system (web API, message queue, etc.) calls:
```python
await runner.resume(correlation_id="approval-...", payload={"approved": true})
```

The runner:
1. Finds the run by correlation_id
2. Acquires a new lease
3. Calls `step.on_resume(payload, context)` — step can read payload and write to context
4. Marks step as `completed`
5. Continues execution to find next ready steps

This enables **human-in-the-loop workflows** where a person or external system makes decisions.

### PollingStep & Scheduling

When `ProcessJobStep.execute()` returns `StepResult.poll(next_poll_at=...)`, the runner:
1. Stores next_poll_at in `StepRun.next_poll_at`
2. Marks step status as `awaiting_poll`
3. Marks workflow status as `suspended`
4. Releases the lease

On the next runner tick (or a different runner):
1. If current time >= next_poll_at:
   - Calls `step.check(context)` to see if condition is met
   - If True: calls `on_complete()`, marks step `completed`
   - If False: calls `execute()` again to reschedule: `StepResult.poll(next_poll_at=...)`
2. If timeout_seconds exceeded: marks step `failed`, propagates failure

This enables **polling of async jobs** without holding resources. Another runner or a scheduled task can pick up the check later.

### Distributed Lease Mechanics

Multiple runners can be deployed against the same MongoDB. At any time:
- Only one runner holds a lease on a given WorkflowRun
- Lease is acquired atomically: `findOneAndUpdate({status: PENDING/RUNNING, lease_expires_at: null or past}, {$set: {lease_owner, lease_expires_at, ...}})`
- During execution, a heartbeat thread renews lease every `ttl/2` seconds
- If runner crashes: heartbeat stops, lease expires naturally after `ttl` seconds
- Another runner then picks it up

### Optimistic Locking & Concurrent Modification

Each WorkflowRun has `doc_version: int`. Every save increments it.

All writes: `replace_one({_id: ..., doc_version: N}, newDoc)`

If another runner modified the run concurrently:
- `modified_count == 0` (version mismatch)
- Raises `ConcurrentModificationError`
- Local version is rolled back
- Runner logs warning and aborts

This ensures consistency without distributed locks.

### Context & Step Outputs

`Context` is a JSON-serializable key/value store shared between steps.

When a step completes:
```python
runner._complete_step(run, step_run, output={"key": "value"}, context)
```

The runner calls:
```python
context.set_step_output("step_id", output)
```

Downstream steps access it:
```python
fetch_output = context.step_output("fetch")
print(fetch_output["id"])
```

## Extending the Example

### Add a Step Type

1. Create a new class in `steps.py`, subclassing `Step`, `EventStep`, or `PollingStep`
2. Implement required methods (`execute`, or `check`/`on_complete`, or `on_resume`)
3. Add to the workflow DAG: `.add("step_id", MyStep(...), depends_on=[...])`
4. Add to registry: `step_registry["MyStep"] = MyStep`

### Add Failure Handling

Modify the workflow to test failure propagation:

```python
.add("process", ProcessJobStep(...), depends_on=["approve"], on_dependency_failure="skip")
```

Now if approval is rejected, process is skipped instead of failed.

### Add Conditional Logic

Implement conditional branching in `on_resume` or `check`:

```python
def on_resume(self, payload, context):
    if not payload.get("approved"):
        # Could raise an exception, or set a flag, or write an error output
        pass
```

### Use a Real MongoDB

Replace:
```python
mongo_client = AsyncMongoMockClient()
```

With:
```python
from motor.motor_asyncio import AsyncIOMotorClient
mongo_client = AsyncIOMotorClient("mongodb://localhost:27017")
```

## Further Reading

- **[README.md](../../README.md)** — Library overview, installation, quick start
- **[SPEC.md](../../SPEC.md)** — Complete technical specification
- **[CLAUDE.md](../../CLAUDE.md)** — Architecture decisions, development setup
- **[tests/](../../tests/)** — Unit tests demonstrating each component

## License

Same as workchain library.

