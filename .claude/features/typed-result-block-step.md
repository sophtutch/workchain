---
name: typed-result-block-step
created: 2026-04-04
status: in_progress
---

# Accept StepResult in block_step

## Description

`block_step` accepts `result: dict` and `result_summary: dict | None`. Every caller constructs these by calling `.model_dump()` on a `StepResult`. Accept `StepResult` directly and let the store serialize. Derive `result_summary` internally.

## Tasks

- [ ] refactor-block-step: Change block_step signature from result: dict to result: StepResult, remove result_summary param, update 2 engine call sites and test
