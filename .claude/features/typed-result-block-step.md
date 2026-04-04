---
name: typed-result-block-step
created: 2026-04-04T10:00:00Z
status: in_progress
---

# Accept StepResult in block_step

## Problem

`block_step` in `workchain/store.py:322` accepts `result: dict` and `result_summary: dict | None`. Every caller constructs these by calling `.model_dump()` on a `StepResult` object with different serialization options. This duplicates the serialization convention at each call site and makes the store API unclear about what the dict should contain.

## Solution

Change `block_step` to accept `result: StepResult` instead of `result: dict`. The store handles serialization internally. Remove `result_summary` parameter — the store derives it from `result.model_dump(exclude_none=True)` for audit events, matching the pattern established in `complete_step` (PR #26).

## Acceptance criteria

- [ ] `block_step` signature uses `result: StepResult` not `result: dict`
- [ ] Store serializes the result internally — no `.model_dump()` at call sites
- [ ] `result_summary` parameter removed — derived internally for audit
- [ ] Both engine call sites updated (lines ~443, ~600)
- [ ] `TestBlockStep` updated to pass `StepResult` object
- [ ] All existing tests pass (`hatch test`)

## Scope

**In scope:** `block_step` signature, internal serialization, audit `result_summary` derivation, engine call sites, store test.

**Out of scope:** `fail_step` (separate feature), `complete_step` (already done in PR #26).

## Files affected

- `workchain/store.py` — `block_step` method signature and internals
- `workchain/engine.py` — 2 call sites removing `.model_dump()` and `result_summary` kwargs
- `tests/test_store.py` — `TestBlockStep`

## Tasks

- [-] refactor-block-step: Change block_step signature from result: dict to result: StepResult, remove result_summary param, update 2 engine call sites and test
