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
from workchain import MongoWorkflowStore, WorkflowEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    # --- Setup ---
    client = AsyncMongoMockClient()
    db = client["ci_cd_demo"]
    store = MongoWorkflowStore(db, lock_ttl_seconds=30)
    await store.ensure_indexes()

    # --- Build and insert the workflow ---
    workflow = build_workflow(repo="myorg/myapp", branch="main")
    await store.insert(workflow)
    workflow_id = workflow.id
    print(f"\n{'='*60}")
    print(f"CI/CD Pipeline Workflow: {workflow.name}")
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

        if s.name == "lint_code":
            r = cast(LintResult, s.result)
            print(f"      files_checked={r.files_checked}, warnings={r.warnings}")
        elif s.name == "run_tests":
            r = cast(TestResult, s.result)
            print(f"      passed={r.tests_passed}, failed={r.tests_failed}, coverage={r.coverage}%")
        elif s.name == "build_artifact":
            r = cast(BuildResult, s.result)
            print(f"      build_id={r.build_id}, artifact_url={r.artifact_url}")
        elif s.name == "push_to_registry":
            r = cast(RegistryResult, s.result)
            print(f"      image_tag={r.image_tag}, registry_url={r.registry_url}")
        elif s.name == "deploy_staging":
            r = cast(DeployResult, s.result)
            print(f"      deployment_id={r.deployment_id}, environment={r.environment}")
        elif s.name == "run_smoke_tests":
            r = cast(SmokeTestResult, s.result)
            print(f"      all_passed={r.all_passed}, checks_run={r.checks_run}")

        if s.result.error:
            print(f"      error: {s.result.error}")

    print()


if __name__ == "__main__":
    asyncio.run(main())
