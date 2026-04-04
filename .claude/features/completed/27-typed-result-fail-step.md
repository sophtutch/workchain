---
name: typed-result-fail-step
created: 2026-04-04T10:00:00Z
completed: 2026-04-04T21:45:00Z
status: completed
---

# Accept StepResult in fail_step

## Problem

`fail_step` in `workchain/store.py` accepts `result: dict` — a raw dictionary that every caller constructs by calling `fail_result.model_dump(mode="python", serialize_as_any=True)` on a `StepResult` object. This makes the API unclear: the caller must know the serialization convention, and the store signature gives no indication of what the dict should contain. Additionally, the `error` and `error_traceback` audit kwargs are passed separately by the engine, even though they can be derived from `result.error`.

## Solution

Changed `fail_step` to accept `result: StepResult` instead of `result: dict`. The store handles serialization internally and derives `error` (brief, last line) and `error_traceback` (full `result.error`) for audit events.

## Acceptance criteria

- [x] `fail_step` signature uses `result: StepResult` not `result: dict`
- [x] Store serializes the result internally — no `.model_dump()` at call sites
- [x] `error` and `error_traceback` audit params removed — derived from `result.error`
- [x] All 4 engine call sites updated
- [x] `TestFailStep` updated to pass `StepResult` object
- [x] All existing tests pass (`hatch test`)

## Scope

**In scope:** `fail_step` signature, internal serialization, audit field derivation, engine call sites, store test.

**Out of scope:** `block_step` dict param (separate feature), `complete_step` (already done in PR #26).

## Files affected

- `workchain/store.py` — `fail_step` method signature and internals
- `workchain/engine.py` — 4 call sites
- `tests/test_store.py` — `TestFailStep`

## Tasks

- [x] refactor-fail-step: Change fail_step signature from result: dict to result: StepResult, derive audit error fields internally, update 4 engine call sites and test
  - branch: `typed-result-fail-step/refactor-fail-step`
  - pr: #27

## PRs
- #27: Accept StepResult in fail_step instead of dict
