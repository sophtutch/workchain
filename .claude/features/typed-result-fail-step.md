---
name: typed-result-fail-step
created: 2026-04-04
status: in_progress
---

# Accept StepResult in fail_step

## Description

`fail_step` accepts `result: dict`. Every caller constructs this by calling `fail_result.model_dump(mode="python", serialize_as_any=True)` on a `StepResult`. Accept `StepResult` directly and let the store serialize. The store can also derive `error` and `error_traceback` from `result.error` for audit events, removing those params.

## Tasks

- [ ] refactor-fail-step: Change fail_step signature from result: dict to result: StepResult, derive audit error fields internally, update 4 engine call sites and test
