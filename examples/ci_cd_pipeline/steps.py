"""Step handlers for the CI/CD pipeline workflow.

Steps:
  1.  lint_code               - Static analysis of source files
  2.  run_unit_tests          - Execute unit test suite with coverage (retry x3)
  3.  security_scan           - SAST/dependency vulnerability scan
  4.  run_integration_tests   - Integration tests against test database
  5.  license_audit           - Check dependency licenses for compliance
  6.  vulnerability_report    - Generate detailed CVE report from scan results
  7.  build_artifact          - Async: kick off container build, poll until done
  8.  push_to_registry        - Push built image to container registry
  9.  compliance_sign_off     - Verify all compliance checks passed
  10. deploy_staging          - Async: deploy to staging, poll for healthy rollout
  11. generate_report         - Aggregate results from all branches into report
  12. notify_team             - Send Slack/email notification
  13. update_dashboard        - Push metrics to CI/CD dashboard
"""

from __future__ import annotations

import logging
import random
import uuid
from typing import cast

from workchain import (
    CheckResult,
    PollPolicy,
    RetryPolicy,
    StepConfig,
    StepResult,
    async_step,
    completeness_check,
    step,
)

logger = logging.getLogger(__name__)
_rng = random.SystemRandom()

# ---------------------------------------------------------------------------
# Configs and Results
# ---------------------------------------------------------------------------


class LintConfig(StepConfig):
    source_dir: str = "src"


class LintResult(StepResult):
    files_checked: int = 0
    warnings: int = 0


class TestConfig(StepConfig):
    test_dir: str = "tests"
    coverage_threshold: float = 80.0


class TestResult(StepResult):
    tests_passed: int = 0
    tests_failed: int = 0
    coverage: float = 0.0


class SecurityScanConfig(StepConfig):
    scan_profile: str = "default"


class SecurityScanResult(StepResult):
    vulnerabilities_found: int = 0
    critical: int = 0
    high: int = 0
    scan_id: str = ""


class IntegrationTestConfig(StepConfig):
    db_url: str = "postgres://test/ci"


class IntegrationTestResult(StepResult):
    tests_passed: int = 0
    tests_failed: int = 0
    db_migrations_applied: int = 0


class LicenseAuditConfig(StepConfig):
    policy: str = "strict"


class LicenseAuditResult(StepResult):
    packages_scanned: int = 0
    violations: int = 0
    approved: bool = False


class VulnReportConfig(StepConfig):
    format: str = "sarif"


class VulnReportResult(StepResult):
    report_url: str = ""
    cve_count: int = 0
    remediation_count: int = 0


class BuildConfig(StepConfig):
    repo: str = ""
    branch: str = "main"


class BuildResult(StepResult):
    build_id: str = ""
    artifact_url: str = ""


class RegistryConfig(StepConfig):
    registry: str = "ghcr.io"


class RegistryResult(StepResult):
    image_tag: str = ""
    registry_url: str = ""


class ComplianceConfig(StepConfig):
    require_zero_critical: bool = True


class ComplianceResult(StepResult):
    approved: bool = False
    sign_off_id: str = ""


class DeployConfig(StepConfig):
    environment: str = "staging"


class DeployResult(StepResult):
    deployment_id: str = ""
    environment: str = ""


class ReportConfig(StepConfig):
    include_coverage: bool = True


class ReportResult(StepResult):
    report_url: str = ""
    sections: int = 0


class NotifyConfig(StepConfig):
    channel: str = "#ci-cd"


class NotifyResult(StepResult):
    message_id: str = ""
    channel: str = ""


class DashboardConfig(StepConfig):
    dashboard_id: str = "ci-main"


class DashboardResult(StepResult):
    metrics_pushed: int = 0
    dashboard_url: str = ""


# ---------------------------------------------------------------------------
# Step 1: lint_code
# ---------------------------------------------------------------------------

@step()
async def lint_code(config: LintConfig, _results: dict[str, StepResult]) -> LintResult:
    """Run static analysis / linting on source files."""
    files_checked = _rng.randint(40, 120)
    warnings = _rng.randint(0, 5)
    logger.info("[lint] Checked %d files in '%s/', %d warning(s)", files_checked, config.source_dir, warnings)
    return LintResult(files_checked=files_checked, warnings=warnings)


# ---------------------------------------------------------------------------
# Step 2: run_unit_tests (with retry policy for flaky tests)
# ---------------------------------------------------------------------------

@step(
    retry=RetryPolicy(max_attempts=3, wait_seconds=1.0, wait_multiplier=2.0),
)
async def run_unit_tests(config: TestConfig, _results: dict[str, StepResult]) -> TestResult:
    """Execute the unit test suite. May flake on first attempt."""
    total = _rng.randint(80, 200)
    if _rng.random() < 0.2:
        raise RuntimeError("Flaky test failure -- transient network timeout in test_api_integration")
    coverage = round(_rng.uniform(config.coverage_threshold, 98.0), 1)
    logger.info("[unit-test] %d passed, 0 failed, coverage %s%%", total, coverage)
    return TestResult(tests_passed=total, tests_failed=0, coverage=coverage)


# ---------------------------------------------------------------------------
# Step 3: security_scan
# ---------------------------------------------------------------------------

@step()
async def security_scan(config: SecurityScanConfig, _results: dict[str, StepResult]) -> SecurityScanResult:
    """Run SAST and dependency vulnerability scan."""
    scan_id = uuid.uuid4().hex[:10]
    vulns = _rng.randint(0, 12)
    critical = _rng.randint(0, min(vulns, 2))
    high = _rng.randint(0, min(vulns - critical, 4))
    logger.info("[security] Scan %s: %d vulnerabilities (%d critical, %d high)", scan_id, vulns, critical, high)
    return SecurityScanResult(vulnerabilities_found=vulns, critical=critical, high=high, scan_id=scan_id)


# ---------------------------------------------------------------------------
# Step 4: run_integration_tests
# ---------------------------------------------------------------------------

@step()
async def run_integration_tests(
    config: IntegrationTestConfig,
    _results: dict[str, StepResult],
) -> IntegrationTestResult:
    """Run integration tests against a test database."""
    total = _rng.randint(30, 80)
    migrations = _rng.randint(2, 8)
    logger.info("[integration] %d passed, 0 failed, %d migrations applied (%s)", total, migrations, config.db_url)
    return IntegrationTestResult(tests_passed=total, tests_failed=0, db_migrations_applied=migrations)


# ---------------------------------------------------------------------------
# Step 5: license_audit
# ---------------------------------------------------------------------------

@step()
async def license_audit(config: LicenseAuditConfig, results: dict[str, StepResult]) -> LicenseAuditResult:
    """Audit dependency licenses against policy."""
    scan_result = cast(SecurityScanResult, results["security_scan"])
    packages = _rng.randint(80, 200)
    violations = 0  # clean audit
    logger.info(
        "[license] Scanned %d packages (policy=%s, scan=%s), %d violations",
        packages, config.policy, scan_result.scan_id, violations,
    )
    return LicenseAuditResult(packages_scanned=packages, violations=violations, approved=True)


# ---------------------------------------------------------------------------
# Step 6: vulnerability_report
# ---------------------------------------------------------------------------

@step()
async def vulnerability_report(config: VulnReportConfig, results: dict[str, StepResult]) -> VulnReportResult:
    """Generate a detailed vulnerability report from scan results."""
    scan_result = cast(SecurityScanResult, results["security_scan"])
    report_url = f"https://reports.example.com/vuln/{scan_result.scan_id}.{config.format}"
    remediation = _rng.randint(0, scan_result.vulnerabilities_found)
    logger.info("[vuln-report] Generated %s (%d CVEs, %d remediations)", report_url, scan_result.vulnerabilities_found, remediation)
    return VulnReportResult(report_url=report_url, cve_count=scan_result.vulnerabilities_found, remediation_count=remediation)


# ---------------------------------------------------------------------------
# Step 7: build_artifact (async step -- polls for build completion)
# ---------------------------------------------------------------------------

@completeness_check()
async def check_build(
    _config: BuildConfig,
    _results: dict[str, StepResult],
    result: BuildResult,
) -> CheckResult:
    """Completeness check: simulates build completing after a few polls."""
    progress_steps = [0.3, 0.6, 1.0]
    progress = _rng.choice(progress_steps)

    if progress >= 1.0:
        logger.info("[build] Build %s completed!", result.build_id)
        return CheckResult(complete=True, progress=1.0, message="Build finished")
    logger.info("[build] Build %s in progress (%.0f%%)", result.build_id, progress * 100)
    return CheckResult(
        complete=False,
        progress=progress,
        message=f"Compiling and packaging ({progress:.0%})",
    )


@async_step(
    completeness_check=check_build,
    poll=PollPolicy(interval=3.0, backoff_multiplier=1.0, timeout=120.0, max_polls=10),
)
async def build_artifact(config: BuildConfig, _results: dict[str, StepResult]) -> BuildResult:
    """Kick off a container image build."""
    build_id = uuid.uuid4().hex[:12]
    artifact_url = f"https://builds.example.com/{config.repo}/{build_id}"
    logger.info("[build] Started build %s for %s@%s", build_id, config.repo, config.branch)
    return BuildResult(build_id=build_id, artifact_url=artifact_url)


# ---------------------------------------------------------------------------
# Step 8: push_to_registry
# ---------------------------------------------------------------------------

@step()
async def push_to_registry(
    config: RegistryConfig,
    results: dict[str, StepResult],
) -> RegistryResult:
    """Push the built artifact to a container registry."""
    build_result = cast(BuildResult, results["build_artifact"])
    image_tag = f"{build_result.build_id[:8]}-latest"
    registry_url = f"{config.registry}/myorg/myapp:{image_tag}"
    logger.info("[registry] Pushed %s", registry_url)
    return RegistryResult(image_tag=image_tag, registry_url=registry_url)


# ---------------------------------------------------------------------------
# Step 9: compliance_sign_off
# ---------------------------------------------------------------------------

@step()
async def compliance_sign_off(config: ComplianceConfig, results: dict[str, StepResult]) -> ComplianceResult:
    """Verify all compliance checks passed before deployment."""
    vuln_result = cast(VulnReportResult, results["vulnerability_report"])
    approved = vuln_result.cve_count == 0 or not config.require_zero_critical
    sign_off_id = uuid.uuid4().hex[:10]
    logger.info("[compliance] Sign-off %s: approved=%s (%d CVEs)", sign_off_id, approved, vuln_result.cve_count)
    if not approved:
        raise RuntimeError(
            f"Compliance rejected: {vuln_result.cve_count} CVEs found (sign-off {sign_off_id})"
        )
    return ComplianceResult(approved=True, sign_off_id=sign_off_id)


# ---------------------------------------------------------------------------
# Step 10: deploy_staging (async -- polls for healthy rollout)
# ---------------------------------------------------------------------------

@completeness_check()
async def check_deployment(
    _config: DeployConfig,
    _results: dict[str, StepResult],
    result: DeployResult,
) -> CheckResult:
    """Completeness check: deployment becomes healthy (50% chance per poll)."""
    if _rng.random() < 0.5:
        logger.info("[deploy] Deployment %s rolling out...", result.deployment_id)
        return CheckResult(complete=False, progress=0.5, message="Rolling update in progress")
    logger.info("[deploy] Deployment %s is healthy!", result.deployment_id)
    return CheckResult(complete=True, progress=1.0, message="All replicas healthy")


@async_step(
    completeness_check=check_deployment,
    poll=PollPolicy(interval=5.0, backoff_multiplier=1.0, timeout=300.0, max_polls=10),
)
async def deploy_staging(
    config: DeployConfig,
    results: dict[str, StepResult],
) -> DeployResult:
    """Deploy the container image to staging."""
    registry_result = cast(RegistryResult, results["push_to_registry"])
    deployment_id = uuid.uuid4().hex[:12]
    logger.info(
        "[deploy] Deploying %s to %s (deployment %s)",
        registry_result.registry_url, config.environment, deployment_id,
    )
    return DeployResult(deployment_id=deployment_id, environment=config.environment)


# ---------------------------------------------------------------------------
# Step 11: generate_report
# ---------------------------------------------------------------------------

@step()
async def generate_report(config: ReportConfig, results: dict[str, StepResult]) -> ReportResult:
    """Aggregate results from all pipeline branches into a final report."""
    test_result = cast(TestResult, results["run_unit_tests"])
    deploy_result = cast(DeployResult, results["deploy_staging"])
    report_url = f"https://ci.example.com/reports/{deploy_result.deployment_id}"
    sections = 4 + (1 if config.include_coverage else 0)
    logger.info(
        "[report] Generated %s (%d sections, coverage=%.1f%%)",
        report_url, sections, test_result.coverage,
    )
    return ReportResult(report_url=report_url, sections=sections)


# ---------------------------------------------------------------------------
# Step 12: notify_team
# ---------------------------------------------------------------------------

@step()
async def notify_team(config: NotifyConfig, results: dict[str, StepResult]) -> NotifyResult:
    """Send pipeline completion notification to team channel."""
    report_result = cast(ReportResult, results["generate_report"])
    message_id = uuid.uuid4().hex[:10]
    logger.info("[notify] Sent to %s (msg=%s, report=%s)", config.channel, message_id, report_result.report_url)
    return NotifyResult(message_id=message_id, channel=config.channel)


# ---------------------------------------------------------------------------
# Step 13: update_dashboard
# ---------------------------------------------------------------------------

@step()
async def update_dashboard(config: DashboardConfig, results: dict[str, StepResult]) -> DashboardResult:
    """Push pipeline metrics to CI/CD monitoring dashboard."""
    report_result = cast(ReportResult, results["generate_report"])
    metrics = report_result.sections + _rng.randint(3, 8)
    dashboard_url = f"https://dashboard.example.com/{config.dashboard_id}"
    logger.info("[dashboard] Pushed %d metrics to %s", metrics, dashboard_url)
    return DashboardResult(metrics_pushed=metrics, dashboard_url=dashboard_url)
