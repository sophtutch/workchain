---
name: enforce-pollhint-return
created: 2026-04-04T00:00:00Z
status: in_progress
---

# Enforce CheckResult as sole completeness check return type

## Problem

Completeness check return values are parsed via isinstance chains in two engine methods (`_poll_once` and `_recover_step`), supporting `bool`, `dict`, `PollHint`, and a silent `bool(raw)` fallthrough for anything else. This causes:

1. **Duplicated parsing logic** — two isinstance chains doing the same thing inconsistently (`_poll_once` uses `PollHint.model_validate(dict)`, `_recover_step` uses `dict.get("complete", False)`)
2. **Silent fallthrough** — unexpected return types are silently coerced via `bool(raw)`, potentially completing a step incorrectly
3. **No enforcement** — the decorator documents `-> bool | dict | PollHint` but doesn't enforce it
4. **Bad naming** — `PollHint` describes the engine's perspective, not the handler author's; `CheckResult` is clearer

`PollHint` already has `complete: bool`, so it subsumes the `bool` case. Dicts are just untyped PollHints.

## Solution

1. Rename `PollHint` → `CheckResult` across the entire codebase (models, engine, decorators, examples, tests, docs)
2. Wrap the completeness check handler in the `@completeness_check` decorator to normalize return values to `CheckResult`
3. Remove isinstance chains from engine — always receives `CheckResult`
4. Update example completeness checks to return `CheckResult` directly

## Acceptance criteria

- [x] `PollHint` renamed to `CheckResult` everywhere (code, tests, docs)
- [ ] `@completeness_check` decorator wraps handler to normalize return to `CheckResult`
- [ ] `_poll_once` and `_recover_step` no longer contain isinstance chains for check results
- [ ] Unexpected return types raise `TypeError` at the decorator level
- [ ] All 8 example completeness checks updated to return `CheckResult`
- [ ] All existing tests pass (bool and dict returns still work via decorator coercion)
- [ ] New test confirming `TypeError` on invalid return type
- [x] CLAUDE.md, README.md, and example READMEs updated

## Scope

**In scope:** Rename + decorator enforcement + engine simplification + example updates + doc updates
**Out of scope:** Changing CheckResult fields, changing step handler return types

## Files affected

- `workchain/models.py` — rename class
- `workchain/engine.py` — rename refs + remove isinstance chains
- `workchain/decorators.py` — rename refs + wrap handler return
- `workchain/__init__.py` — rename export
- `examples/*/steps.py` — 8 checks: dict → CheckResult, rename imports
- `tests/conftest.py` — rename imports
- `tests/test_engine.py` — rename imports + add TypeError test
- `tests/test_models.py` — rename imports
- `README.md` — rename refs
- `CLAUDE.md` — rename refs
- `examples/*/README.md` — rename refs
- `.claude/commands/add-step.md` — rename refs

## Tasks

- [x] rename-pollhint: Rename PollHint → CheckResult across all code, tests, and docs
  - branch: `enforce-pollhint-return/rename-pollhint`
  - pr: #34
- [ ] decorator-enforcement: Wrap completeness_check to normalize returns to CheckResult, simplify engine, update examples to return CheckResult directly
