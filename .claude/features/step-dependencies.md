---
name: step-dependencies
created: 2026-04-04T00:00:00Z
status: in_progress
---

# Step Dependencies

## Problem

Workflows currently execute steps in strict sequential order via `current_step_index`. Independent steps that could run concurrently (e.g. "create VPC" and "provision database") must wait for each other, wasting time. There's no way to express that step C depends on steps A and B but A and B are independent. Locking is per-workflow, so only one engine instance can work on a workflow at a time — even when multiple steps are independently ready.

## Solution

Add `depends_on: list[str]` to the `Step` model and move locking from the workflow level to the step level. Steps become ready when all named dependencies are completed. Each ready step can be independently claimed and executed by any engine instance, enabling true distributed parallelism. Backward compatibility: steps without explicit `depends_on` default to depending on the previous step in the list, preserving current sequential behavior for existing workflows.

Key architectural changes:
- **Step-level locking**: `locked_by`, `lock_expires_at`, `fence_token` move from `Workflow` to `Step`
- **Per-step claims**: `find_claimable` returns ready steps (not workflows); each claim locks one step
- **Dependency graph**: `current_step_index` is replaced by `depends_on` + step status checks
- **Concurrent execution**: Multiple engine instances can work on different steps of the same workflow simultaneously
- **Workflow status derived**: COMPLETED when all steps done, FAILED when any step fails — determined atomically after each step transition

## Acceptance criteria

- [ ] Steps declare dependencies via `depends_on: list[str]` referencing other step names
- [ ] Validation rejects unknown step names, self-references, and cycles
- [ ] Steps without `depends_on` default to depending on the previous step (sequential backward compat)
- [ ] Steps with `depends_on: []` (empty list) are ready immediately (root steps)
- [ ] Lock fields (`locked_by`, `lock_expires_at`, `fence_token`) live on `Step`, not `Workflow`
- [ ] Engine claims and executes individual steps, not entire workflows
- [ ] Multiple engine instances can concurrently execute independent steps of the same workflow
- [ ] `_build_results()` provides results from completed dependencies, not positional slicing
- [ ] `find_claimable` discovers ready steps across all running workflows
- [ ] Heartbeat renews per-step locks
- [ ] Recovery and sweep logic work with multiple in-flight steps per workflow
- [ ] Workflow completes atomically when all steps are done
- [ ] Workflow fails atomically when any step fails; dependent steps are cancelled
- [ ] `current_step_index` is removed from the model
- [ ] All docs, examples, and command templates updated

## Scope

**In scope:** `depends_on` field, dependency validation, step-level locking, per-step claims, concurrent execution across instances, store query updates, recovery updates, sweep updates, docs/examples.

**Out of scope:** DAG visualization, conditional branching (if/else steps), dynamic step insertion at runtime, fan-out/fan-in operators, cross-workflow dependencies.

## Files affected

- `workchain/models.py` — `Step.depends_on`, step-level lock fields, `Workflow` readiness helpers, remove `current_step_index`
- `workchain/engine.py` — execution loop, `_build_results`, advancement, recovery, per-step claiming
- `workchain/store.py` — per-step claim/release/heartbeat, `find_claimable`, `find_anomalies`, fenced writes via array filters
- `workchain/audit.py` — audit events updated for step-level claims/releases
- `tests/test_models.py` — dependency validation, readiness tests
- `tests/test_engine.py` — concurrent execution, dependency ordering, recovery
- `tests/test_store.py` — per-step claim/release, claimable queries
- `README.md` — updated examples showing `depends_on` and concurrent steps
- `CLAUDE.md` — updated architecture notes (step-level locking, dependency model)
- `examples/` — update at least one example to showcase concurrent steps
- `.claude/commands/` — update scaffold templates

## Tasks

- [x] model-and-validation: Add `depends_on` and step-level lock fields to Step, dependency validation on Workflow, readiness helpers
  - branch: `step-dependencies/model-and-validation`
  - pr: #38
- [ ] store-step-claims: Rewrite store for per-step claiming, heartbeat, release, fenced writes via MongoDB array filters
- [ ] engine-rewrite: Rewrite engine to claim/execute individual ready steps, track active steps, derive workflow completion
- [ ] recovery-and-sweep: Update crash recovery and sweep for per-step locks and multiple in-flight steps
- [ ] docs-and-examples: Remove `current_step_index`, update all docs/examples/commands, add parallel-steps example

### Task 1: model-and-validation

**models.py — Step changes:**
```python
class Step(BaseModel):
    # ... existing fields ...
    depends_on: list[str] | None = None  # None = sequential default, [] = root step

    # Step-level lock fields (moved from Workflow)
    locked_by: str | None = None
    lock_expires_at: datetime | None = None
    fence_token: int = 0
```

**models.py — Workflow changes:**
```python
class Workflow(BaseModel):
    # REMOVE: current_step_index, locked_by, lock_expires_at, fence_token
    # ADD: model validator for depends_on

    @model_validator(mode="after")
    def _resolve_and_validate_depends_on(self) -> Workflow:
        """
        1. Resolve None → sequential default (each step depends on previous, first gets [])
        2. Validate all referenced names exist
        3. Reject self-references
        4. Detect cycles via topological sort
        """

    def ready_steps(self) -> list[Step]:
        """Steps that are PENDING and all depends_on steps are COMPLETED, and not locked."""

    def active_steps(self) -> list[Step]:
        """Steps currently SUBMITTED, RUNNING, or BLOCKED."""

    def is_complete(self) -> bool:
        """True if all steps are COMPLETED."""

    def has_failed(self) -> bool:
        """True if any step is FAILED."""
```

**tests/test_models.py additions:**
- `depends_on=None` resolves to sequential chain (`[]`, `["a"]`, `["b"]`, ...)
- Explicit `depends_on=["a", "b"]` preserved as-is
- `depends_on=[]` marks root step (ready immediately)
- Validation: unknown name → `ValueError`
- Validation: self-reference → `ValueError`
- Validation: cycle A→B→C→A → `ValueError`
- `ready_steps()` with diamond pattern: A(root) → B,C(depend on A) → D(depends on B,C)
- Step-level lock field defaults: `locked_by=None`, `fence_token=0`

### Task 2: store-step-claims

**store.py — New claim model:**

Replace workflow-level `try_claim` with per-step claiming:

```python
async def find_claimable(self, limit: int = 10) -> list[tuple[str, str]]:
    """Return (workflow_id, step_name) pairs for ready, unlocked steps.

    A step is claimable if:
    - Workflow status is PENDING or RUNNING
    - Step status is PENDING and all depends_on steps are COMPLETED and step is unlocked/expired
    - OR step status is BLOCKED and next_poll_at <= now and step is unlocked/expired

    Two-phase: broad MongoDB query for workflows with PENDING/BLOCKED steps,
    then Python-side readiness filtering using Workflow.ready_steps().
    """

async def try_claim_step(
    self, workflow_id: str, step_name: str, instance_id: str
) -> tuple[Workflow, int] | None:
    """Atomically lock a single step. Returns (workflow, step_fence_token) or None.

    MongoDB: find_one_and_update with array_filters:
    filter:  {"_id": wf_id}
    update:  {"$set": {"steps.$[s].locked_by": id, "steps.$[s].lock_expires_at": expires,
                        "status": RUNNING},
              "$inc": {"steps.$[s].fence_token": 1}}
    array_filters: [{"s.name": step_name, "s.locked_by": None | expired}]
    """

async def heartbeat_step(
    self, workflow_id: str, step_name: str, instance_id: str, fence_token: int
) -> bool:
    """Renew TTL on a single step's lock.

    MongoDB: find_one_and_update with array_filters:
    filter:  {"_id": wf_id}
    update:  {"$set": {"steps.$[s].lock_expires_at": new_expires}}
    array_filters: [{"s.name": step_name, "s.locked_by": instance_id, "s.fence_token": fence}]
    """

async def release_step_lock(
    self, workflow_id: str, step_name: str, instance_id: str, fence_token: int
) -> bool:
    """Release lock on a single step."""
```

**store.py — Fenced writes via array filters:**

```python
async def _fenced_step_update(
    self, workflow_id: str, step_name: str, fence_token: int, updates: dict
) -> Workflow | None:
    """Atomic step update guarded by step-level fence token.

    MongoDB: find_one_and_update with:
    filter:  {"_id": wf_id}
    update:  {"$set": {"steps.$[s].field": value, ...}}
    array_filters: [{"s.name": step_name, "s.fence_token": fence_token}]
    """
```

All existing step-state methods (`submit_step`, `mark_step_running`, `complete_step`, `fail_step`, `block_step`, `schedule_next_poll`, `reset_step`) change signature from `step_index: int` to `step_name: str` and use the new `_fenced_step_update`.

**store.py — Workflow status transitions (atomic):**

```python
async def _try_complete_workflow(self, workflow_id: str) -> bool:
    """Atomically set workflow COMPLETED if all steps are COMPLETED.

    MongoDB: find_one_and_update:
    filter:  {"_id": wf_id, "steps": {"$not": {"$elemMatch": {"status": {"$nin": ["completed"]}}}}}
    update:  {"$set": {"status": "completed"}}
    """

async def _try_fail_workflow(self, workflow_id: str) -> bool:
    """Atomically set workflow FAILED if any step FAILED.

    Also cancels dependent steps that can no longer run.
    """
```

**tests/test_store.py additions:**
- `try_claim_step` locks one step, returns fence token
- Two instances claim different steps of same workflow concurrently
- `try_claim_step` rejects if step already locked (not expired)
- `try_claim_step` succeeds if step lock expired (TTL passed)
- `heartbeat_step` renews step lock TTL
- `heartbeat_step` fails on fence mismatch (lock stolen)
- `release_step_lock` clears step lock fields
- `_fenced_step_update` rejects stale fence tokens
- `find_claimable` returns ready steps respecting dependencies
- `_try_complete_workflow` sets COMPLETED only when all steps done
- `_try_fail_workflow` sets FAILED and cancels unreachable steps

### Task 3: engine-rewrite

**engine.py — New execution model:**

The engine no longer manages "active workflows" — it manages "active steps":

```python
class WorkflowEngine:
    _active: dict[tuple[str, str], asyncio.Task]  # (wf_id, step_name) → task

    async def _claim_loop(self) -> None:
        """Discover and claim ready steps (not workflows).

        slots = max_concurrent - len(self._active)
        claimable = await store.find_claimable(limit=slots)
        for (wf_id, step_name) in claimable:
            claimed = await store.try_claim_step(wf_id, step_name, instance_id)
            if claimed:
                task = asyncio.create_task(self._run_step(wf_id, step_name, fence))
                self._active[(wf_id, step_name)] = task
        """

    async def _run_step(self, wf_id: str, step_name: str, fence: int) -> None:
        """Execute a single step end-to-end.

        1. Load workflow from store
        2. Find step by name
        3. Build results from depends_on steps
        4. Submit → Run → Complete/Fail/Block
        5. On complete: call store._try_complete_workflow(wf_id)
        6. On fail: call store._try_fail_workflow(wf_id)
        7. Release step lock
        8. Remove from self._active
        """

    async def _heartbeat_loop(self) -> None:
        """Renew locks on all active steps (not workflows).

        for (wf_id, step_name), task in self._active.items():
            ok = await store.heartbeat_step(wf_id, step_name, instance_id, fence)
            if not ok: task.cancel()
        """

    @staticmethod
    def _build_results(wf: Workflow, step: Step) -> dict[str, StepResult]:
        """Collect results from step.depends_on names (not positional slice).

        return {
            dep_name: dep_step.result
            for dep_name in step.depends_on
            if (dep_step := wf.step_by_name(dep_name)) and dep_step.result
        }
        """
```

**Key behavior changes:**
- No `_advance()` method — there's no index to increment. When a step completes, newly-ready steps are discovered by `find_claimable` on the next claim loop iteration.
- No `_run_workflow` loop — each step is an independent unit of work.
- Async steps (BLOCKED): release step lock, remove from active. Claim loop rediscovers when `next_poll_at` passes.
- Workflow completion is a side-effect of the last step completing (atomic check in store).

**tests/test_engine.py additions:**
- Linear workflow (A→B→C) executes in order via sequential `depends_on` defaults
- Diamond workflow (A→B,C→D): B and C are both claimable after A completes
- `_build_results` returns dependency results by name
- Workflow marked COMPLETED when final step completes
- Workflow marked FAILED when any step fails
- Async step: claim, submit, block, release, re-claim for poll
- `max_concurrent` limits total active steps across all workflows

### Task 4: recovery-and-sweep

**engine.py — Recovery changes:**

When claiming a step that's in SUBMITTED/RUNNING state (crashed mid-execution), the same recovery cascade applies but scoped to individual steps:

```python
async def _recover_step(self, wf: Workflow, step: Step, fence: int) -> Step | None:
    """Same cascade as before, but:
    - Multiple steps in a workflow can be in recovery simultaneously
    - Each recovered by whichever instance claims it
    - NEEDS_REVIEW on one step → workflow NEEDS_REVIEW
    """
```

**engine.py — Sweep changes:**

```python
async def _sweep_loop(self) -> None:
    """Detect stuck steps and stale step-level locks.

    find_anomalies updated to:
    1. Stuck steps: any step in SUBMITTED/RUNNING for > step_stuck_seconds
    2. Stale step locks: step locked but lock_expires_at passed and no heartbeat
    3. No more "completed not advanced" check (no current_step_index)
    4. New: orphaned workflows — all steps terminal but workflow still RUNNING
    """
```

**store.py — find_anomalies changes:**

```python
async def find_anomalies(self, step_stuck_seconds: float = 300.0) -> list[dict]:
    """Return anomalies as list of {"workflow_id", "step_name", "anomaly"}.

    Queries:
    1. Stuck steps: workflows with any step in SUBMITTED/RUNNING, updated_at stale
    2. Stale step locks: steps with locked_by set but lock_expires_at passed
    3. Orphaned workflows: all steps terminal but workflow status is RUNNING
    """
```

**tests additions:**
- Recovery of single crashed step while other steps of same workflow are healthy
- Recovery of multiple crashed steps in same workflow
- Sweep detects stuck step-level locks
- Sweep detects orphaned workflow (all steps done but workflow not marked complete)
- NEEDS_REVIEW on one step propagates to workflow status

### Task 5: docs-and-examples

- **models.py**: Remove `current_step_index` from `Workflow` (if not done in task 1). Remove workflow-level `locked_by`, `lock_expires_at`, `fence_token`.
- **README.md**: Add `depends_on` usage examples. Show diamond pattern. Update quick start to show concurrent steps.
- **CLAUDE.md**: Rewrite architecture notes — step-level locking, dependency model, per-step claims, no `current_step_index`.
- **examples/**: Update one example (e.g. `infra_provisioning`) to use concurrent steps where natural (VPC + database in parallel). Others keep sequential default.
- **`.claude/commands/`**: Update `new-workflow.md` and `add-step.md` scaffold templates with `depends_on` field.
- **Audit report**: Update `audit_report.py` if needed to visualize concurrent step execution.
