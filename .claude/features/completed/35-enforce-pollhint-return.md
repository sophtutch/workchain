---
name: enforce-pollhint-return
created: 2026-04-04T00:00:00Z
status: completed
completed: 2026-04-04T00:00:00Z
---

# Enforce CheckResult as sole completeness check return type

## Problem

Completeness check return values were parsed via isinstance chains in two engine methods (`_poll_once` and `_recover_step`), supporting `bool`, `dict`, `PollHint`, and a silent `bool(raw)` fallthrough for anything else. This caused duplicated parsing logic, silent fallthrough for unexpected types, and no enforcement of the documented return type contract.

## Solution

1. Renamed `PollHint` to `CheckResult` across the entire codebase
2. Wrapped the `@completeness_check` decorator to normalize return values to `CheckResult`
3. Removed isinstance chains from engine — always receives `CheckResult`
4. Updated all 8 example completeness checks to return `CheckResult` directly

## Acceptance criteria

- [x] `PollHint` renamed to `CheckResult` everywhere (code, tests, docs)
- [x] `@completeness_check` decorator wraps handler to normalize return to `CheckResult`
- [x] `_poll_once` and `_recover_step` no longer contain isinstance chains for check results
- [x] Unexpected return types raise `TypeError` at the decorator level
- [x] All 8 example completeness checks updated to return `CheckResult`
- [x] All existing tests pass (bool and dict returns still work via decorator coercion)
- [x] New test confirming `TypeError` on invalid return type
- [x] CLAUDE.md, README.md, and example READMEs updated

## Tasks

- [x] rename-pollhint: Rename PollHint → CheckResult across all code, tests, and docs
  - branch: `enforce-pollhint-return/rename-pollhint`
  - pr: #34
- [x] decorator-enforcement: Wrap completeness_check to normalize returns to CheckResult, simplify engine, update examples to return CheckResult directly
  - branch: `enforce-pollhint-return/decorator-enforcement`
  - pr: #35

## PRs
- #34: Rename PollHint to CheckResult
- #35: Enforce CheckResult return type from completeness checks
