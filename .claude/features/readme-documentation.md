---
name: readme-documentation
created: 2026-04-04T23:00:00Z
status: planned
---

# README documentation gaps

## Problem

README.md doesn't document: query API (list_workflows, count_by_status), PollHint.retry_after, engine tuning parameters, or the verify_completion hook for crash recovery. Users have no reference for these features without reading source code.

## Solution

Add sections to README covering these 4 features with runnable code examples.

## Acceptance criteria

- [ ] Query API section with list_workflows and count_by_status examples
- [ ] PollHint.retry_after usage documented with example
- [ ] Engine tuning parameters (claim_interval, heartbeat_interval, sweep_interval, lock_ttl) documented
- [ ] verify_completion hook for crash recovery documented with example
- [ ] All examples are runnable code snippets
- [ ] No production code changes needed

## Scope

**In scope:** README.md only.

**Out of scope:** CLAUDE.md (already up to date).

## Files affected

- `README.md`

## Tasks

- [ ] update-readme-docs: Add documentation sections for query API, PollHint.retry_after, engine tuning parameters, and verify_completion hook
