---
name: update-store-docs
created: 2026-04-04T10:00:00Z
completed: 2026-04-04T22:30:00Z
status: completed
---

# Update CLAUDE.md for typed store params

## Problem

After complete_step, fail_step, and block_step were refactored to accept StepResult objects instead of dicts, CLAUDE.md did not document this pattern.

## Solution

Updated CLAUDE.md conventions section to document that store step-state methods accept StepResult objects directly and serialize internally.

## Acceptance criteria

- [x] CLAUDE.md conventions section documents the StepResult param convention
- [x] CLAUDE.md store.py section mentions internal serialization
- [x] Convention is clear: "pass StepResult objects, not dicts"

## Tasks

- [x] update-claude-md: Update CLAUDE.md conventions and store.py section to document StepResult params and internal serialization
  - branch: `update-store-docs/update-claude-md`
  - pr: #29

## PRs
- #29: Document StepResult convention for store methods
