---
name: heartbeat-cancel-await
created: 2026-04-04T23:30:00Z
status: planned
---

# Await cancelled tasks in heartbeat loop

## Problem

When the heartbeat loop detects a stolen lock (`engine.py:247-248`), it calls `task.cancel()` and immediately removes the task from `_active`. But `cancel()` only requests cancellation — the task may be mid-step-execution. Removing it from `_active` means its cleanup won't be tracked, leaving the step in an inconsistent state until the sweep loop catches it.

The same pattern exists in `stop()` (`engine.py:188`) — tasks are cancelled but not awaited before releasing locks.

## Solution

In the heartbeat loop, defer removal from `_active` — track cancelled tasks separately and await them (with a short timeout) before discarding. In `stop()`, await cancelled workflow tasks before releasing locks and clearing `_active`.

## Acceptance criteria

- [ ] Heartbeat loop awaits cancelled tasks (with timeout) before removing from `_active`
- [ ] `stop()` awaits active workflow tasks before clearing `_active`
- [ ] Lock release still happens promptly (shield/timeout pattern)
- [ ] Test verifies that a cancelled task's cleanup runs before removal
- [ ] All existing tests pass

## Scope

**In scope:** `_heartbeat_loop()` cancel handling, `stop()` shutdown ordering
**Out of scope:** Sweep loop changes, new recovery logic

## Files affected

- `workchain/engine.py`
- `tests/test_engine.py`

## Tasks

- [ ] await-cancelled-tasks: Add await-with-timeout for cancelled tasks in heartbeat loop and stop()
