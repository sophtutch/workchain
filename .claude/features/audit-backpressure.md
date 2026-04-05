---
name: audit-backpressure
created: 2026-04-04T17:55:58Z
status: planned
---

# Add backpressure to fire-and-forget audit writes

## Problem

Audit events are emitted as fire-and-forget `asyncio.Task`s in both `store.py` (lines 96-98) and `audit.py` (lines 199-200). There's no cap on concurrent tasks. Under high load with slow MongoDB, tasks accumulate in memory unboundedly — `_audit_tasks` set in store and `_pending` set in `MongoAuditLogger` grow without limit.

## Solution

Add a bounded semaphore to `MongoAuditLogger` that limits concurrent pending writes (e.g. 100). When the limit is reached, new `emit()` calls drop the event and log a warning rather than queueing unboundedly. This preserves the fire-and-forget contract while preventing memory leaks.

## Acceptance criteria

- [ ] `MongoAuditLogger` accepts a `max_pending` constructor parameter (default 100)
- [ ] When pending tasks >= max_pending, new events are dropped with a warning log
- [ ] A counter tracks dropped events (accessible for monitoring)
- [ ] Test verifies events are dropped when limit is reached
- [ ] All existing tests pass

## Scope

**In scope:** `MongoAuditLogger` backpressure, constructor param, tests
**Out of scope:** Store-level `_audit_tasks` set (it mirrors the logger's set — fixing the logger fixes both), batch writing optimization

## Files affected

- `workchain/audit.py`
- `tests/test_audit.py`

## Tasks

- [ ] add-audit-backpressure: Add max_pending limit to MongoAuditLogger with drop-and-warn behavior
