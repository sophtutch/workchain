"""Runnable infrastructure provisioning demo using mongomock for local execution.

Usage:
    python -m examples.infra_provisioning.example
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import cast

from mongomock_motor import AsyncMongoMockClient

# Import steps so decorators register handlers
from examples.infra_provisioning.steps import (  # noqa: F401
    DatabaseResult,
    DeployResult,
    DnsResult,
    HealthCheckResult,
    TlsResult,
    VpcResult,
    configure_dns,
    create_vpc,
    deploy_application,
    health_check,
    issue_tls_cert,
    provision_database,
)
from examples.infra_provisioning.workflow import build_workflow
from workchain import MongoAuditLogger, MongoWorkflowStore, WorkflowEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    # --- Setup ---
    client = AsyncMongoMockClient()
    db = client["infra_provisioning_demo"]
    audit = MongoAuditLogger(db)
    store = MongoWorkflowStore(db, lock_ttl_seconds=30, audit_logger=audit, instance_id="infra-demo")
    await store.ensure_indexes()

    # --- Build and insert the workflow ---
    workflow = build_workflow(
        domain="app.example.com",
        image="myorg/myapp:latest",
        region="us-east-1",
    )
    await store.insert(workflow)
    workflow_id = workflow.id
    logger.info("=" * 60)
    logger.info("Infrastructure Provisioning Workflow: %s", workflow.name)
    logger.info("Workflow ID: %s", workflow_id)
    logger.info("Steps: %s", ", ".join(s.name for s in workflow.steps))
    logger.info("=" * 60)

    # --- Run the engine until the workflow completes ---
    # Context dict makes db and store available to step handlers
    engine = WorkflowEngine(store, claim_interval=0.5, sweep_interval=1.0, context={"db": db, "store": store})
    await engine.start()

    # Poll until the workflow reaches a terminal state
    for _ in range(120):  # up to 60 seconds
        await asyncio.sleep(0.5)
        wf = await store.get(workflow_id)
        if wf is not None and wf.is_terminal():
            break

    await engine.stop()

    # --- Print results ---
    wf = await store.get(workflow_id)
    if wf is None:
        logger.error("Workflow not found")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("Final status: %s", wf.status.value)
    logger.info("=" * 60)

    for s in wf.steps:
        status_icon = {
            "completed": "+",
            "failed": "X",
            "pending": ".",
            "blocked": "~",
        }.get(s.status.value, "?")
        logger.info("  [%s] %s (%s)", status_icon, s.name, s.status.value)

        if s.result is None:
            continue

        if s.name == "create_vpc":
            r = cast(VpcResult, s.result)
            logger.info("      vpc_id=%s, subnets=%s", r.vpc_id, r.subnet_ids)
        elif s.name == "provision_database":
            r = cast(DatabaseResult, s.result)
            logger.info("      db_instance_id=%s, endpoint=%s, port=%d", r.db_instance_id, r.endpoint, r.port)
        elif s.name == "deploy_application":
            r = cast(DeployResult, s.result)
            logger.info("      deployment_id=%s, replicas_ready=%d", r.deployment_id, r.replicas_ready)
        elif s.name == "configure_dns":
            r = cast(DnsResult, s.result)
            logger.info("      record_id=%s, fqdn=%s", r.record_id, r.fqdn)
        elif s.name == "issue_tls_cert":
            r = cast(TlsResult, s.result)
            logger.info("      certificate_arn=%s, valid_until=%s", r.certificate_arn, r.valid_until)
        elif s.name == "health_check":
            r = cast(HealthCheckResult, s.result)
            logger.info("      status_code=%d, response_time_ms=%s, healthy=%s", r.status_code, r.response_time_ms, r.healthy)

        if s.result.error:
            logger.info("      error: %s", s.result.error)


if __name__ == "__main__":
    asyncio.run(main())
