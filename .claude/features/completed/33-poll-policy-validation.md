---
name: poll-policy-validation
created: 2026-04-04T23:00:00Z
status: completed
completed: 2026-04-04T00:00:00Z
---

# PollPolicy field validation

## Problem

PollPolicy fields (timeout, max_polls, interval, backoff_multiplier, max_interval) accept negative values. A negative timeout triggers immediate timeout on the first poll check. A negative interval causes a negative sleep duration. No field validators exist on PollPolicy, unlike Step.step_timeout which already has a non-negative validator.

## Solution

Add a single multi-field Pydantic field_validator to PollPolicy rejecting negative values for all 5 numeric fields, using ValidationInfo.field_name for dynamic error messages.

## Acceptance criteria

- [x] All 5 PollPolicy fields (timeout, max_polls, interval, backoff_multiplier, max_interval) validated as non-negative
- [x] Tests for each field confirming negative values raise ValidationError
- [x] All existing tests pass without modification

## Scope

**In scope:** models.py PollPolicy validators only.

**Out of scope:** RetryPolicy validation (already has reasonable defaults).

## Files affected

- `workchain/models.py`
- `tests/test_models.py`

## Tasks

- [x] add-poll-policy-validators: Add field_validators to PollPolicy for all 5 numeric fields and add corresponding tests
  - branch: `poll-policy-validation/add-poll-policy-validators`
  - pr: #33

## PRs
- #33: Add non-negative validators to PollPolicy fields
