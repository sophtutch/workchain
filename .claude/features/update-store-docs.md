---
name: update-store-docs
created: 2026-04-04T10:00:00Z
status: planned
---

# Update CLAUDE.md for typed store params

## Problem

After `complete_step`, `fail_step`, and `block_step` are refactored to accept `StepResult` objects instead of dicts, the `CLAUDE.md` conventions section does not document this pattern. Developers reading the architecture docs won't know that store methods accept typed Pydantic models and handle serialization internally.

## Solution

Update CLAUDE.md to document the convention that store step-state methods accept `StepResult` objects directly and serialize them internally using `model_dump(mode="python", serialize_as_any=True)`. Note that callers should never call `.model_dump()` before passing results to store methods.

## Acceptance criteria

- [ ] CLAUDE.md conventions section documents the StepResult param convention
- [ ] CLAUDE.md store.py section mentions internal serialization
- [ ] Convention is clear: "pass StepResult objects, not dicts"

## Scope

**In scope:** CLAUDE.md updates only.

**Out of scope:** Code changes (those are covered by the typed-result-* features).

**Depends on:** `typed-result-fail-step` and `typed-result-block-step` being completed first.

## Files affected

- `CLAUDE.md`

## Tasks

- [ ] update-claude-md: Update CLAUDE.md conventions and store.py section to document StepResult params and internal serialization
