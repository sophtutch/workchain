---
name: typed-result-fail-step
created: 2026-04-04T10:00:00Z
status: in_progress
---

# Accept StepResult in fail_step

## Problem

`fail_step` in `workchain/store.py:284` accepts `result: dict` — a raw dictionary that every caller constructs by calling `fail_result.model_dump(mode="python", serialize_as_any=True)` on a `StepResult` object. This makes the API unclear: the caller must know the serialization convention, and the store signature gives no indication of what the dict should contain. Additionally, the `error` and `error_traceback` audit kwargs are passed separately by the engine, even though they can be derived from `result.error`.

## Solution

Change `fail_step` to accept `result: StepResult` instead of `result: dict`. The store handles serialization internally via `model_dump(mode="python", serialize_as_any=True)`. The store also derives `error` (brief, last line) and `error_traceback` (full `result.error`) for audit events from the `StepResult` object, removing those as separate parameters.

## Acceptance criteria

- [ ] `fail_step` signature uses `result: StepResult` not `result: dict`
- [ ] Store serializes the result internally — no `.model_dump()` at call sites
- [ ] `error` and `error_traceback` audit params removed — derived from `result.error`
- [ ] All 4 engine call sites updated (lines ~419, ~695, ~722, ~767)
- [ ] `TestFailStep` updated to pass `StepResult` object
- [ ] All existing tests pass (`hatch test`)

## Scope

**In scope:** `fail_step` signature, internal serialization, audit field derivation, engine call sites, store test.

**Out of scope:** `block_step` dict param (separate feature), `complete_step` (already done in PR #26).

## Files affected

- `workchain/store.py` — `fail_step` method signature and internals
- `workchain/engine.py` — 4 call sites removing `.model_dump()` and `error`/`error_traceback` kwargs
- `tests/test_store.py` — `TestFailStep`

## Tasks

- [-] refactor-fail-step: Change fail_step signature from result: dict to result: StepResult, derive audit error fields internally, update 4 engine call sites and test
