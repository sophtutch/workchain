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
    ComplianceResult,
    DashboardResult,
    DeployResult,
    IntegrationTestResult,
    LicenseAuditResult,
    LintResult,
    NotifyResult,
    RegistryResult,
    ReportResult,
    SecurityScanResult,
    TestResult,
    VulnReportResult,
    build_artifact,
    compliance_sign_off,
    deploy_staging,
    generate_report,
    license_audit,
    lint_code,
    notify_team,
    push_to_registry,
    run_integration_tests,
    run_unit_tests,
    security_scan,
    update_dashboard,
    vulnerability_report,
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
    async with WorkflowEngine(store, claim_interval=0.5, sweep_interval=1.0, context={"db": db, "store": store}) as engine:
        for _ in range(120):  # up to 60 seconds
            await asyncio.sleep(0.5)
            wf = await store.get(workflow_id)
            if wf is not None and wf.is_terminal():
                break

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
        elif s.name == "run_unit_tests":
            r = cast(TestResult, s.result)
            logger.info("      passed=%d, failed=%d, coverage=%s%%", r.tests_passed, r.tests_failed, r.coverage)
        elif s.name == "security_scan":
            r = cast(SecurityScanResult, s.result)
            logger.info("      scan_id=%s, vulns=%d", r.scan_id, r.vulnerabilities_found)
        elif s.name == "run_integration_tests":
            r = cast(IntegrationTestResult, s.result)
            logger.info("      passed=%d, migrations=%d", r.tests_passed, r.db_migrations_applied)
        elif s.name == "license_audit":
            r = cast(LicenseAuditResult, s.result)
            logger.info("      packages=%d, violations=%d, approved=%s", r.packages_scanned, r.violations, r.approved)
        elif s.name == "vulnerability_report":
            r = cast(VulnReportResult, s.result)
            logger.info("      report_url=%s, cves=%d", r.report_url, r.cve_count)
        elif s.name == "build_artifact":
            r = cast(BuildResult, s.result)
            logger.info("      build_id=%s, artifact_url=%s", r.build_id, r.artifact_url)
        elif s.name == "push_to_registry":
            r = cast(RegistryResult, s.result)
            logger.info("      image_tag=%s, registry_url=%s", r.image_tag, r.registry_url)
        elif s.name == "compliance_sign_off":
            r = cast(ComplianceResult, s.result)
            logger.info("      approved=%s, sign_off_id=%s", r.approved, r.sign_off_id)
        elif s.name == "deploy_staging":
            r = cast(DeployResult, s.result)
            logger.info("      deployment_id=%s, environment=%s", r.deployment_id, r.environment)
        elif s.name == "generate_report":
            r = cast(ReportResult, s.result)
            logger.info("      report_url=%s, sections=%d", r.report_url, r.sections)
        elif s.name == "notify_team":
            r = cast(NotifyResult, s.result)
            logger.info("      message_id=%s, channel=%s", r.message_id, r.channel)
        elif s.name == "update_dashboard":
            r = cast(DashboardResult, s.result)
            logger.info("      metrics=%d, dashboard_url=%s", r.metrics_pushed, r.dashboard_url)

        if s.result.error:
            logger.info("      error: %s", s.result.error)


if __name__ == "__main__":
    asyncio.run(main())
