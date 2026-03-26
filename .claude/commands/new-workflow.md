# Scaffold a new Workflow

Create a new workflow definition module for the workchain project.

**Arguments:** `$ARGUMENTS` — the workflow name in snake_case (e.g. `document_approval`)

## Instructions

1. Create `workflows/$ARGUMENTS.py` with:
   - A `build_workflow() -> Workflow` factory function
   - Placeholder step imports (user will fill these in)
   - A call to `workflow.create_run()` shown in a docstring example

2. Show the user the generated file and remind them to:
   - Import and register their step classes in the runner registry
   - Call `store.save(run)` before starting the runner
   - Call `store.ensure_indexes()` once at app startup

## Template

```python
"""$ARGUMENTS workflow definition."""

from workchain import Workflow

# TODO: import your step classes here
# from workchain_steps.my_step import MyStep, MyStepConfig


def build_workflow() -> Workflow:
    """
    Build and return the $ARGUMENTS workflow.

    Usage::

        workflow = build_workflow()
        run = workflow.create_run()
        store.save(run)
        runner = WorkflowRunner(store=store, registry=REGISTRY, workflow=workflow)
        runner.start()
    """
    return (
        Workflow(name="$ARGUMENTS", version="1.0.0")
        # .add("step_one", MyStep(config=MyStepConfig(...)))
        # .add("step_two", AnotherStep(), depends_on=["step_one"])
    )


# Registry — map step_type strings to Step classes
REGISTRY = {
    # "MyStep": MyStep,
}
```
