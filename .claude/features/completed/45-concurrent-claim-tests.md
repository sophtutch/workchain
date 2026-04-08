---
name: concurrent-claim-tests
created: 2026-04-04T00:46:06Z
status: completed
completed: 2026-04-06T00:00:00Z
---

# Concurrent claim and anomaly detection tests

## Problem

No tests verify multi-instance claim race behavior, stale-lock anomaly detection, or completed-not-advanced anomaly detection. These are critical distributed safety paths that are currently untested.

## Solution

Add integration tests for: concurrent try_claim where only one instance wins, find_anomalies stale-lock detection, and find_anomalies completed-step-not-advanced detection.

## Acceptance criteria

- [ ] 3+ new tests covering concurrent claim, stale-lock anomaly, and completed-not-advanced anomaly scenarios
- [ ] All new tests pass
- [ ] All existing tests pass without modification

## Scope

**In scope:** Tests only, no production code changes.

**Out of scope:** Actual multi-process testing (mongomock is sufficient for verifying store-level behavior).

## Files affected

- `tests/test_store.py`

## Tasks

- [x] add-concurrent-claim-tests: Add integration tests for concurrent try_claim, stale-lock anomaly detection, and completed-not-advanced anomaly detection
  - branch: `concurrent-claim-tests`
  - pr: #45
