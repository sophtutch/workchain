"""
Runnable demo of the Data Pipeline ETL workflow using mongomock.

Usage:
    python -m examples.data_pipeline_etl.example
"""

from __future__ import annotations

import asyncio
import logging

from motor.motor_asyncio import AsyncIOMotorClient

# Import steps so decorators register handlers
from examples.data_pipeline_etl import steps as _steps  # noqa: F401
from examples.data_pipeline_etl.workflow import build_workflow
from workchain import MongoAuditLogger, MongoWorkflowStore, WorkflowEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    # ---- Mongo setup (mongomock for local demo) ----
    mongo_client: AsyncIOMotorClient = AsyncIOMotorClient(
        "mongodb://localhost:27017",
        # mongomock-motor intercepts this when installed
    )
    db = mongo_client["etl_demo"]
    audit = MongoAuditLogger(db)
    store = MongoWorkflowStore(db, lock_ttl_seconds=15, audit_logger=audit, instance_id="etl-demo")

    # ---- Build and insert workflow ----
    workflow = build_workflow(
        source_uri="postgres://src-db:5432/sales",
        target_table="fact_orders",
        columns=["order_id", "customer_id", "amount", "created_at"],
        batch_size=500,
    )
    await store.insert(workflow)
    logger.info("Inserted workflow %s (%s)", workflow.name, workflow.id)

    # ---- Run engine until workflow completes ----
    # Context dict makes db and store available to step handlers
    engine = WorkflowEngine(store, context={"db": db, "store": store})
    await engine.start()

    # Poll until the workflow reaches a terminal state
    for _ in range(60):
        await asyncio.sleep(1)
        wf = await store.get(workflow.id)
        if wf and wf.is_terminal():
            break

    await engine.stop()

    # ---- Report ----
    wf = await store.get(workflow.id)
    if wf is None:
        logger.error("Workflow not found!")
        return

    logger.info("Workflow status: %s", wf.status.value)
    for s in wf.steps:
        logger.info(
            "  Step %-25s  status=%-10s  result=%s",
            s.name,
            s.status.value,
            s.result.model_dump() if s.result else None,
        )


if __name__ == "__main__":
    asyncio.run(main())
