# Run tests

Run the workchain test suite and report results.

## Instructions

1. Run `pytest tests/ -v` and capture the output.
2. If all tests pass, confirm with a summary of tests run.
3. If any tests fail:
   - Show the failure output clearly
   - Identify the root cause
   - Propose a fix and ask the user before applying it
4. If no `tests/` directory exists, inform the user and offer to scaffold a basic test file covering:
   - `Workflow` DAG validation (valid DAG, cycle detection)
   - `Context` serialization enforcement
   - `WorkflowRunner._get_ready_steps()` logic
   - `WorkflowRunner._propagate_failure()` cascade behaviour
