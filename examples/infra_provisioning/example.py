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

from workchain import MongoWorkflowStore, WorkflowEngine

# Import steps so decorators register handlers
from .steps import (  # noqa: F401
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
from .workflow import build_workflow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    # --- Setup ---
    client = AsyncMongoMockClient()
    db = client["infra_provisioning_demo"]
    store = MongoWorkflowStore(db, lock_ttl_seconds=30)
    await store.ensure_indexes()

    # --- Build and insert the workflow ---
    workflow = build_workflow(
        domain="app.example.com",
        image="myorg/myapp:latest",
        region="us-east-1",
    )
    await store.insert(workflow)
    workflow_id = workflow.id
    print(f"\n{'='*60}")
    print(f"Infrastructure Provisioning Workflow: {workflow.name}")
    print(f"Workflow ID: {workflow_id}")
    print(f"Steps: {', '.join(s.name for s in workflow.steps)}")
    print(f"{'='*60}\n")

    # --- Run the engine until the workflow completes ---
    engine = WorkflowEngine(store, claim_interval=0.5, sweep_interval=1.0)
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
        print("ERROR: workflow not found")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"Final status: {wf.status.value}")
    print(f"{'='*60}")

    for s in wf.steps:
        status_icon = {
            "completed": "+",
            "failed": "X",
            "pending": ".",
            "blocked": "~",
        }.get(s.status.value, "?")
        print(f"\n  [{status_icon}] {s.name} ({s.status.value})")

        if s.result is None:
            continue

        if s.name == "create_vpc":
            r = cast(VpcResult, s.result)
            print(f"      vpc_id={r.vpc_id}, subnets={r.subnet_ids}")
        elif s.name == "provision_database":
            r = cast(DatabaseResult, s.result)
            print(f"      db_instance_id={r.db_instance_id}, endpoint={r.endpoint}, port={r.port}")
        elif s.name == "deploy_application":
            r = cast(DeployResult, s.result)
            print(f"      deployment_id={r.deployment_id}, replicas_ready={r.replicas_ready}")
        elif s.name == "configure_dns":
            r = cast(DnsResult, s.result)
            print(f"      record_id={r.record_id}, fqdn={r.fqdn}")
        elif s.name == "issue_tls_cert":
            r = cast(TlsResult, s.result)
            print(f"      certificate_arn={r.certificate_arn}, valid_until={r.valid_until}")
        elif s.name == "health_check":
            r = cast(HealthCheckResult, s.result)
            print(f"      status_code={r.status_code}, response_time_ms={r.response_time_ms}, healthy={r.healthy}")

        if s.result.error:
            print(f"      error: {s.result.error}")

    print()


if __name__ == "__main__":
    asyncio.run(main())
