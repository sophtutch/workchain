# Add a new Step

Scaffold a new step handler for a workchain workflow.

**Arguments:** `$ARGUMENTS` — the step name in snake_case (e.g. `send_welcome_email`)

## Instructions

1. Determine the step mode from the user's description:
   - **Sync** (`@step`) — executes and returns immediately
   - **Async** (`@async_step`) — submits external work, polls until complete

2. Create config and result Pydantic models extending `StepConfig` and `StepResult`.

3. Create the handler as an async function decorated with `@step` or `@async_step`.

4. If async, create a `completeness_check` function.

5. Handler signature supports optional engine context (3rd arg):
   - Without context: `async def handler(config, results) -> MyResult`
   - With context: `async def handler(config, results, ctx: dict[str, Any]) -> MyResult`

## Template — Sync Step

```python
from workchain import StepConfig, StepResult, step

class $ARGUMENTS_Config(StepConfig):
    # TODO: define config fields
    pass

class $ARGUMENTS_Result(StepResult):
    # TODO: define result fields
    pass

@step()
async def $ARGUMENTS(
    config: $ARGUMENTS_Config,
    _results: dict[str, StepResult],
) -> $ARGUMENTS_Result:
    # TODO: implement step logic
    return $ARGUMENTS_Result()
```

## Template — Async Step

```python
from workchain import StepConfig, StepResult, CheckResult, PollPolicy, async_step

class $ARGUMENTS_Result(StepResult):
    job_id: str

async def check_$ARGUMENTS(config, results, result: $ARGUMENTS_Result) -> CheckResult:
    # TODO: check if external work is done
    return CheckResult(complete=False, progress=0.5)

@async_step(
    completeness_check=check_$ARGUMENTS,
    poll=PollPolicy(interval=5.0, timeout=300.0),
)
async def $ARGUMENTS(
    config: StepConfig,
    _results: dict[str, StepResult],
) -> $ARGUMENTS_Result:
    # TODO: submit external work
    return $ARGUMENTS_Result(job_id="job_123")
```
