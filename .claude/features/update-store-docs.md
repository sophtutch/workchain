---
name: update-store-docs
created: 2026-04-04
status: in_progress
---

# Update CLAUDE.md for typed store params

## Description

After complete_step, fail_step, and block_step accept `StepResult` objects instead of dicts, update CLAUDE.md to document the convention that store methods accept typed Pydantic models and handle serialization internally.

## Tasks

- [ ] update-claude-md: Update CLAUDE.md conventions and store.py section to document StepResult params and internal serialization
