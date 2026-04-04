# Scaffold a new Workflow

Create a new workflow example for the workchain project.

**Arguments:** `$ARGUMENTS` — the workflow name in snake_case (e.g. `document_approval`)

## Instructions

1. Create `examples/$ARGUMENTS/` directory with:
   - `__init__.py` (empty)
   - `steps.py` — step handlers decorated with `@step` / `@async_step`
   - `workflow.py` — `build_workflow()` factory function returning a `Workflow`
   - `example.py` — CLI runner using mongomock-motor

2. The workflow builder should:
   - Import the steps module to trigger handler registration
   - Accept typed parameters for configuration
   - Return a `Workflow` with a list of `Step` objects

3. The CLI runner should:
   - Create a mongomock MongoDB client
   - Create `MongoWorkflowStore` and `MongoAuditLogger`
   - Build and insert the workflow
   - Start a `WorkflowEngine` with `context={"db": db, "store": store}`
   - Wait for completion and print results

## Template — workflow.py

```python
"""$ARGUMENTS workflow definition."""

from __future__ import annotations

from examples.$ARGUMENTS import steps  # noqa: F401
from workchain import Step, Workflow


def build_workflow(name: str) -> Workflow:
    """Construct the $ARGUMENTS workflow."""
    return Workflow(
        name="$ARGUMENTS",
        steps=[
            Step(name="step_one", handler="examples.$ARGUMENTS.steps.step_one"),
            # Add more steps here
        ],
    )
```

## Template — example.py

```python
"""Runnable demo of the $ARGUMENTS workflow."""

from __future__ import annotations

import asyncio
import logging

from mongomock_motor import AsyncMongoMockClient

from examples.$ARGUMENTS.workflow import build_workflow
from workchain import MongoAuditLogger, MongoWorkflowStore, WorkflowEngine

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    client = AsyncMongoMockClient()
    db = client["workchain_demo"]

    store = MongoWorkflowStore(db, lock_ttl_seconds=30)
    audit = MongoAuditLogger(db)

    wf = build_workflow("example")
    await store.insert(wf)

    async with WorkflowEngine(
        store,
        claim_interval=1.0,
        audit_logger=audit,
        context={"db": db, "store": store},
    ) as engine:
        await asyncio.sleep(20)

    final = await store.get(wf.id)
    if final:
        for s in final.steps:
            print(f"  [{s.status.value}] {s.name}")


if __name__ == "__main__":
    asyncio.run(main())
```
