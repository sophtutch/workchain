---
name: fix-examples-audit-logger
created: 2026-04-04T00:00:00Z
status: in_progress
---

# Fix examples audit logger wiring

## Problem

All 6 example files pass `audit_logger=` to `WorkflowEngine`, which doesn't accept that parameter after the audit refactor moved it to `MongoWorkflowStore`. This causes `TypeError: unexpected keyword argument 'audit_logger'` on startup. The store also doesn't receive `audit_logger` or `instance_id`, so audit events are silently dropped via `NullAuditLogger`.

## Solution

Move `audit_logger=` from `WorkflowEngine(...)` to `MongoWorkflowStore(...)` in all 6 example files. Also pass `instance_id=` to the store so audit events are tagged.

## Acceptance criteria

- [ ] All 6 example files pass audit_logger and instance_id to MongoWorkflowStore
- [ ] No example passes audit_logger to WorkflowEngine
- [ ] FastAPI app starts without TypeError
- [ ] All existing tests pass

## Scope

**In scope:** 6 example files only.

**Out of scope:** Production code, tests (no changes needed).

## Files affected

- `examples/app.py`
- `examples/customer_onboarding/example.py`
- `examples/ci_cd_pipeline/example.py`
- `examples/data_pipeline_etl/example.py`
- `examples/incident_response/example.py`
- `examples/infra_provisioning/example.py`

## Tasks

- [-] fix-wiring: Move audit_logger from WorkflowEngine to MongoWorkflowStore in all 6 example files, pass instance_id to the store
