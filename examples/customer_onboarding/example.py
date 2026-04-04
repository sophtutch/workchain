"""
Runnable demo of the customer onboarding workflow.

Usage:
    python -m examples.customer_onboarding.example
"""

from __future__ import annotations

import asyncio
import logging

from mongomock_motor import AsyncMongoMockClient

from examples.customer_onboarding.workflow import build_workflow
from workchain import MongoAuditLogger, MongoWorkflowStore, WorkflowEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    # In-memory MongoDB mock — no real server required.
    client = AsyncMongoMockClient()
    db = client["workchain_demo"]

    audit = MongoAuditLogger(db)
    store = MongoWorkflowStore(db, lock_ttl_seconds=30, audit_logger=audit, instance_id="onboarding-demo")

    # Build and persist the workflow.
    wf = build_workflow("alice@example.com")
    await store.insert(wf)
    logger.info("Inserted workflow id=%s", wf.id)

    # Start the engine with aggressive intervals for the demo.
    # Context dict makes db and store available to step handlers
    # that opt in by accepting a 3rd argument.
    engine = WorkflowEngine(
        store,
        claim_interval=1.0,
        heartbeat_interval=5.0,
        max_concurrent=3,
        context={"db": db, "store": store},
    )
    await engine.start()

    # Let the engine run long enough to complete all steps
    # (including async polling cycles).
    await asyncio.sleep(20)

    await engine.stop()

    # Print final workflow state.
    final = await store.get(wf.id)
    if final is None:
        logger.error("Workflow not found!")
        return

    logger.info("=" * 60)
    logger.info("Workflow: %s", final.name)
    logger.info("Status:   %s", final.status.value)
    logger.info("Steps:")
    for s in final.steps:
        result_summary = ""
        if s.result is not None:
            data = s.result.model_dump(exclude={"completed_at"}, exclude_none=True)
            result_summary = f" -> {data}"
        logger.info("  [%9s] %s%s", s.status.value, s.name, result_summary)
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
