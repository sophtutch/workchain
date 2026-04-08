---
name: readme-documentation
created: 2026-04-04T23:00:00Z
completed: 2026-04-04T01:00:00Z
status: completed
---

# README documentation gaps

## Problem

README.md doesn't document: query API (list_workflows, count_by_status), PollHint.retry_after, engine tuning parameters, or the verify_completion hook for crash recovery. Users have no reference for these features without reading source code.

## Solution

Add sections to README covering these 4 features with runnable code examples.

## Acceptance criteria

- [x] Query API section with list_workflows and count_by_status examples
- [x] PollHint.retry_after usage documented with example
- [x] Engine tuning parameters (claim_interval, heartbeat_interval, sweep_interval, lock_ttl) documented
- [x] verify_completion hook for crash recovery documented with example
- [x] All examples are runnable code snippets
- [x] No production code changes needed

## Scope

**In scope:** README.md only.

**Out of scope:** CLAUDE.md (already up to date).

## Files affected

- `README.md`

## Tasks

- [x] update-readme-docs: Add documentation sections for query API, PollHint.retry_after, engine tuning parameters, and verify_completion hook
  - branch: `docs/update-readme-docs`
  - pr: #30

## PRs

- #30: Document query API, PollHint.retry_after, engine tuning, and verify_completion
