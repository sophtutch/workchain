---
name: typed-result-complete-step
created: 2026-04-04
completed: 2026-04-04
status: completed
---

# Accept StepResult in complete_step

## Description

`complete_step` accepts `result: dict | None` and `result_summary: dict | None`. Every caller constructs these by calling `.model_dump()` on a `StepResult` object. Accept `StepResult` directly and let the store handle serialization. Derive `result_summary` internally.

## Tasks

- [x] refactor-complete-step: Change complete_step signature from result: dict to result: StepResult, remove result_summary param, serialize internally, update 4 engine call sites and test
  - branch: `typed-result-complete-step/refactor-complete-step`
  - pr: #26

## PRs
- #26: Accept StepResult in complete_step instead of dict
