---
name: engine-context-manager
created: 2026-04-04T23:30:00Z
status: planned
---

# Add async context manager to WorkflowEngine

## Problem

`WorkflowEngine` requires manual `await engine.start()` / `await engine.stop()` calls. If an exception occurs between start and stop, locks aren't released and background tasks aren't cancelled. Users must write try/finally blocks every time.

## Solution

Add `__aenter__` / `__aexit__` to `WorkflowEngine` so it can be used as `async with WorkflowEngine(...) as engine:`. The existing `start()`/`stop()` methods remain for backwards compatibility.

## Acceptance criteria

- [ ] `WorkflowEngine` implements `__aenter__` (calls `start()`, returns self) and `__aexit__` (calls `stop()`)
- [ ] `async with` usage works in tests
- [ ] Existing `start()`/`stop()` API still works unchanged
- [ ] All existing tests pass

## Scope

**In scope:** `__aenter__`/`__aexit__` on WorkflowEngine, test, README example update
**Out of scope:** Deprecating start/stop

## Files affected

- `workchain/engine.py`
- `tests/test_engine.py`

## Tasks

- [ ] add-context-manager: Add __aenter__/__aexit__ to WorkflowEngine with test
