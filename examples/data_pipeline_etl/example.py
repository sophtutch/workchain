"""
Runnable demo of the 28-step Data Pipeline ETL workflow using mongomock.

Usage:
    python -m examples.data_pipeline_etl.example

The workflow fans out across 5 parallel ingestion sources, merges them
into a bronze landing zone, runs PII/quality/normalization branches in
parallel, enriches via two async polling steps, sessionizes, aggregates,
trains features (async), loads to two sinks (async), publishes a
dashboard, and finally notifies downstream consumers — 28 steps total.

Expected runtime: 5–10 minutes (each handler sleeps 5–20 seconds; many
steps run concurrently across engine workers, so wall-clock time is
dominated by the longest dependency chain plus polling cycles).
"""

from __future__ import annotations

import asyncio
import logging

from mongomock_motor import AsyncMongoMockClient

# Import steps so decorators register handlers
from examples.data_pipeline_etl import steps as _steps  # noqa: F401
from examples.data_pipeline_etl.workflow import build_workflow
from workchain import MongoAuditLogger, MongoWorkflowStore, WorkflowEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# Total poll budget for the whole workflow (seconds).
_MAX_WAIT_SECONDS = 900


async def main() -> None:
    # ---- Mongo setup (mongomock for local demo) ----
    client = AsyncMongoMockClient()
    db = client["etl_demo"]
    audit = MongoAuditLogger(db)
    store = MongoWorkflowStore(
        db, lock_ttl_seconds=30, audit_logger=audit, instance_id="etl-demo",
    )

    # ---- Build and insert workflow ----
    workflow = build_workflow(
        postgres_dsn="postgres://pg-demo:5432/core",
        kafka_bootstrap="kafka-demo:9092",
        s3_bucket="acme-demo-raw",
        lake_bucket="acme-demo-lake",
        snowflake_warehouse="DEMO_LOAD_WH",
    )
    await store.insert(workflow)
    logger.info(
        "Inserted workflow %s (%s) — %d steps",
        workflow.name, workflow.id, len(workflow.steps),
    )

    # ---- Run engine until workflow completes ----
    # Context dict makes db and store available to step handlers
    async with WorkflowEngine(
        store,
        context={"db": db, "store": store},
        max_concurrent=8,
    ) as engine:
        for elapsed in range(_MAX_WAIT_SECONDS):
            await asyncio.sleep(1)
            wf = await store.get(workflow.id)
            if wf and wf.is_terminal():
                logger.info("Workflow reached terminal state in %ds", elapsed)
                break
        else:
            logger.warning("Workflow did not reach terminal state within %ds", _MAX_WAIT_SECONDS)

    # ---- Report ----
    wf = await store.get(workflow.id)
    if wf is None:
        logger.error("Workflow not found!")
        return

    logger.info("=" * 72)
    logger.info("Workflow status: %s", wf.status.value)
    logger.info("=" * 72)
    for s in wf.steps:
        logger.info(
            "  Step %-26s  status=%-10s  attempt=%d",
            s.name, s.status.value, s.attempt,
        )


if __name__ == "__main__":
    asyncio.run(main())
