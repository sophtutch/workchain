---
name: replace-prints-with-logging
created: 2026-04-04T00:00:00Z
status: completed
completed: 2026-04-04T00:00:00Z
---

# Replace print() with logging in examples

## Problem
Example step handlers use bare `print()` for operational messages (e.g. `[tls] Certificate issued`, `[health] GET ...`). These show up in FastAPI harness output with no way to filter, silence, or route them. The user mistook infra_provisioning step output for unexpected app behavior.

## Solution
Replace `print()` calls in step handler files and example runner files with `logging.getLogger(__name__)` calls. Use `logger.info()` for step progress, `logger.info()` for CLI summaries in example runners, and keep `generate_diagrams.py` prints as-is (CLI script output).

## Acceptance criteria
- [x] All `print()` in step handler files (`*/steps.py`) replaced with `logger.info()`
- [x] All `print()` in example runner files (`*/example.py`) replaced with `logger.info()`
- [x] Each file uses `logger = logging.getLogger(__name__)` at module level
- [x] FastAPI harness (`app.py`) configures `logging.basicConfig()` so step logs appear in uvicorn output
- [x] `generate_diagrams.py` left unchanged (CLI script, print is appropriate)
- [x] All examples still run correctly

## Scope
**In scope:** `print()` → `logger` conversion in `examples/**/*.py` (except `generate_diagrams.py`)
**Out of scope:** Changes to workchain library code, adding structured logging, log levels other than INFO

## Files affected
- `examples/ci_cd_pipeline/steps.py` — 10 print statements
- `examples/infra_provisioning/steps.py` — 12 print statements
- `examples/ci_cd_pipeline/example.py` — 18 print statements
- `examples/infra_provisioning/example.py` — 18 print statements
- `examples/customer_onboarding/example.py` — 6 print statements
- `examples/incident_response/example.py` — 6 print statements
- `examples/app.py` — add logging.basicConfig() for harness

## Tasks

- [x] replace-all-prints: Replace print() with logger in all example step and runner files, add basicConfig to app.py
  - branch: `refactor/replace-prints-with-logging`
  - pr: #32

## PRs
- #32: Replace print() with logging in examples
