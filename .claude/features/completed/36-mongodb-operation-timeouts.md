---
name: mongodb-operation-timeouts
created: 2026-04-04T23:30:00Z
status: completed
completed: 2026-04-04T19:10:00Z
---

# Add maxTimeMS to MongoDB operations

## Problem

All MongoDB operations in `store.py` (`find_one_and_update`, `update_one`, `insert_one`, etc.) lack explicit timeouts. If MongoDB is slow or deadlocked, the engine hangs — heartbeats stop, locks expire, and cascading failures follow as other instances reclaim stuck workflows.

## Solution

Add `maxTimeMS` to all MongoDB write and query operations in `MongoWorkflowStore`. Use a configurable default (e.g. 30s) passed to the store constructor, with per-operation overrides where appropriate (e.g. shorter timeout for heartbeats).

## Acceptance criteria

- [ ] All `find_one_and_update`, `update_one`, `insert_one`, `find` calls in store.py include `maxTimeMS`
- [ ] Timeout is configurable via store constructor parameter (default 30000ms)
- [ ] Heartbeat operations use a shorter timeout (e.g. 5000ms)
- [ ] Tests verify that the parameter is passed through (mock-level check)
- [ ] All existing tests pass

## Scope

**In scope:** store.py MongoDB operations, constructor parameter, tests
**Out of scope:** Retry-on-timeout logic (that's a separate concern), audit.py operations (fire-and-forget already)

## Files affected

- `workchain/store.py`
- `tests/test_store.py`

## Tasks

- [x] add-mongo-timeouts: Add maxTimeMS parameter to store constructor and all MongoDB operations
  - branch: `mongodb-operation-timeouts/add-mongo-timeouts`
  - pr: #36

## PRs

- #36 — Add operation_timeout_ms to MongoWorkflowStore
