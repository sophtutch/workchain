# Add a new Step

Scaffold a new step for the workchain library.

**Arguments:** `$ARGUMENTS` — the name of the step class (e.g. `SendEmailStep`)

## Instructions

1. Determine the appropriate base class from the user's description:
   - `Step` — synchronous, completes immediately
   - `EventStep` — suspends until an external signal is received
   - `PollingStep` — retries a condition check on an interval

2. Create the step file at `workchain/steps/<snake_case_name>.py` with:
   - A `Config(BaseModel)` inner class with typed fields
   - The `execute(self, context: Context) -> StepResult` method
   - For `EventStep`: also implement `on_resume(self, payload, context)`
   - For `PollingStep`: also implement `check(self, context) -> bool` and optionally `on_complete(self, context) -> dict`

3. Add the new step to `workchain/steps/__init__.py` exports (create the file if it doesn't exist yet).

4. Add the step class name to the example registry in `CLAUDE.md` if it isn't already there.

5. Remind the user to register it in their `WorkflowRunner` registry dict.

## Template — Standard Step

```python
from pydantic import BaseModel
from workchain.steps import Step, StepResult
from workchain.context import Context


class $ARGUMENTS Config(BaseModel):
    # TODO: define config fields
    pass


class $ARGUMENTS(Step["$ARGUMENTS Config"]):
    Config = $ARGUMENTS Config

    def execute(self, context: Context) -> StepResult:
        # TODO: implement step logic
        return StepResult.complete(output={})
```
