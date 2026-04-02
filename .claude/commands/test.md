# Run tests

Run the workchain test suite and report results.

## Instructions

1. Run `hatch test` and capture the output.
2. If all tests pass, confirm with a summary of tests run.
3. If any tests fail:
   - Show the failure output clearly
   - Identify the root cause
   - Propose a fix and ask the user before applying it
4. After fixing, also run `hatch fmt` to check for lint errors.
   - Only the pre-existing FBT001/FBT002/SLF001 errors in decorators.py and engine.py are expected.
   - Any new errors should be fixed.
