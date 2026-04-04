"""Runnable CI/CD pipeline demo using mongomock for local execution.

Usage:
    python -m examples.ci_cd_pipeline.example
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import cast

from mongomock_motor import AsyncMongoMockClient

# Import steps so decorators register handlers
from examples.ci_cd_pipeline.steps import (  # noqa: F401
    BuildResult,
    DeployResult,
    LintResult,
    RegistryResult,
    SmokeTestResult,
    TestResult,
    build_artifact,
    deploy_staging,
    lint_code,
    push_to_registry,
    run_smoke_tests,
    run_tests,
)
from examples.ci_cd_pipeline.workflow import build_workflow
from workchain import MongoAuditLogger, MongoWorkflowStore, WorkflowEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    # --- Setup ---
    client = AsyncMongoMockClient()
    db = client["ci_cd_demo"]
    audit = MongoAuditLogger(db)
    store = MongoWorkflowStore(db, lock_ttl_seconds=30, audit_logger=audit, instance_id="ci-cd-demo")
    await store.ensure_indexes()

    # --- Build and insert the workflow ---
    workflow = build_workflow(repo="myorg/myapp", branch="main")
    await store.insert(workflow)
    workflow_id = workflow.id
    logger.info("=" * 60)
    logger.info("CI/CD Pipeline Workflow: %s", workflow.name)
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

        if s.name == "lint_code":
            r = cast(LintResult, s.result)
            logger.info("      files_checked=%d, warnings=%d", r.files_checked, r.warnings)
        elif s.name == "run_tests":
            r = cast(TestResult, s.result)
            logger.info("      passed=%d, failed=%d, coverage=%s%%", r.tests_passed, r.tests_failed, r.coverage)
        elif s.name == "build_artifact":
            r = cast(BuildResult, s.result)
            logger.info("      build_id=%s, artifact_url=%s", r.build_id, r.artifact_url)
        elif s.name == "push_to_registry":
            r = cast(RegistryResult, s.result)
            logger.info("      image_tag=%s, registry_url=%s", r.image_tag, r.registry_url)
        elif s.name == "deploy_staging":
            r = cast(DeployResult, s.result)
            logger.info("      deployment_id=%s, environment=%s", r.deployment_id, r.environment)
        elif s.name == "run_smoke_tests":
            r = cast(SmokeTestResult, s.result)
            logger.info("      all_passed=%s, checks_run=%d", r.all_passed, r.checks_run)

        if s.result.error:
            logger.info("      error: %s", s.result.error)


if __name__ == "__main__":
    asyncio.run(main())
