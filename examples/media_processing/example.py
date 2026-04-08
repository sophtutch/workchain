"""
Runnable demo of the media processing pipeline.

Demonstrates multi-level parallelism: 3-wide fan-out → join →
2-wide fan-out → join → 2-wide fan-out.

Usage:
    python -m examples.media_processing.example
"""

from __future__ import annotations

import asyncio
import logging

from mongomock_motor import AsyncMongoMockClient

from examples.media_processing.workflow import build_workflow
from workchain import MongoAuditLogger, MongoWorkflowStore, WorkflowEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    client = AsyncMongoMockClient()
    db = client["workchain_demo"]

    audit = MongoAuditLogger(db)
    store = MongoWorkflowStore(db, lock_ttl_seconds=30, audit_logger=audit, instance_id="media-demo")

    wf = build_workflow(filename="conference-talk.mp4")
    await store.insert(wf)
    logger.info("Inserted workflow id=%s", wf.id)

    async with WorkflowEngine(
        store,
        claim_interval=1.0,
        heartbeat_interval=5.0,
        max_concurrent=5,
        context={"db": db, "store": store},
    ) as engine:
        await asyncio.sleep(30)

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
