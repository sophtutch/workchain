---
name: shutdown-active-work-test
created: 2026-04-04T17:55:58Z
status: completed
completed: 2026-04-06T00:00:00Z
---

# Test graceful shutdown during active workflow execution

## Problem

The only shutdown tests verify that `_shutdown_event` is set and `_active` is cleared. There's no test that starts the engine, triggers workflow execution, calls `stop()` mid-execution, and verifies that locks are released and the workflow is left in a recoverable state.

## Solution

Add an integration test that:
1. Starts the engine with a slow-running handler (e.g. `asyncio.sleep(10)`)
2. Waits until the workflow is claimed and the handler is executing
3. Calls `engine.stop()`
4. Verifies: lock is released, workflow status is RUNNING (not COMPLETED), step is in a recoverable state

## Acceptance criteria

- [ ] Test starts engine, triggers workflow, stops mid-execution
- [ ] Test verifies lock is released after shutdown
- [ ] Test verifies workflow is in a recoverable state (not corrupted)
- [ ] All existing tests pass

## Scope

**In scope:** One new integration test
**Out of scope:** Changes to engine shutdown logic (that's heartbeat-cancel-await)

## Files affected

- `tests/test_engine.py`

## Tasks

- [x] add-shutdown-test: Add integration test for graceful shutdown during active workflow execution
  - branch: `shutdown-active-work-test`
  - pr: #47
