---
name: check-return-validation
created: 2026-04-04T23:00:00Z
status: planned
---

# Completeness check return type validation

## Problem

Completeness check return values fall through to bool(raw) for unexpected types. An accidentally truthy object (e.g. a non-empty dict without a 'complete' key) could incorrectly complete a step. No validation or warning exists for unrecognized return types.

## Solution

Add explicit type checking in the completeness check result parsing. Accept bool, dict (with 'complete' key), and PollHint. For other types, log a warning and treat as not-complete (safer default than truthy evaluation).

## Acceptance criteria

- [ ] Unknown return types logged with a warning message
- [ ] Unknown return types treated as not-complete (safe default)
- [ ] Tests for None, custom object, and empty-list returns confirming not-complete behavior
- [ ] All existing tests pass without modification

## Scope

**In scope:** engine.py _poll_once and _recover_step completeness check parsing.

**Out of scope:** Handler return type validation (different concern).

## Files affected

- `workchain/engine.py`
- `tests/test_engine.py`

## Tasks

- [ ] validate-check-return-types: Add explicit type checking for completeness check returns with warning logging for unrecognized types
