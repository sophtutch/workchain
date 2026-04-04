---
name: typed-result-block-step
created: 2026-04-04T10:00:00Z
completed: 2026-04-04T22:15:00Z
status: completed
---

# Accept StepResult in block_step

## Problem

`block_step` in `workchain/store.py` accepts `result: dict` and `result_summary: dict | None`. Every caller constructs these by calling `.model_dump()` on a `StepResult` object with different serialization options.

## Solution

Changed `block_step` to accept `result: StepResult` instead of `result: dict`. The store handles serialization internally and derives `result_summary` for audit events.

## Acceptance criteria

- [x] `block_step` signature uses `result: StepResult` not `result: dict`
- [x] Store serializes the result internally — no `.model_dump()` at call sites
- [x] `result_summary` parameter removed — derived internally for audit
- [x] Both engine call sites updated
- [x] `TestBlockStep` updated to pass `StepResult` object
- [x] All existing tests pass (`hatch test`)

## Tasks

- [x] refactor-block-step: Change block_step signature from result: dict to result: StepResult, remove result_summary param, update 2 engine call sites and test
  - branch: `typed-result-block-step/refactor-block-step`
  - pr: #28

## PRs
- #28: Accept StepResult in block_step instead of dict
