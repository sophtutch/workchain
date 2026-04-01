"""Step handlers for the CI/CD pipeline workflow.

Steps:
  1. lint_code        - Static analysis of source files
  2. run_tests        - Execute test suite with coverage (retry x3)
  3. build_artifact   - Async: kick off container build, poll until done
  4. push_to_registry - Push built image to container registry
  5. deploy_staging   - Async: deploy to staging, poll for healthy rollout
  6. run_smoke_tests  - Execute smoke test suite against staging
"""

from __future__ import annotations

import random
import uuid
from typing import cast

from workchain import (
    PollPolicy,
    RetryPolicy,
    StepConfig,
    StepResult,
    async_step,
    step,
)

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


class DeployConfig(StepConfig):
    environment: str = "staging"


class DeployResult(StepResult):
    deployment_id: str = ""
    environment: str = ""


class SmokeTestConfig(StepConfig):
    base_url: str = "https://staging.example.com"


class SmokeTestResult(StepResult):
    all_passed: bool = False
    checks_run: int = 0


# ---------------------------------------------------------------------------
# Step 1: lint_code
# ---------------------------------------------------------------------------

@step(name="lint_code")
async def lint_code(config: LintConfig, results: dict[str, StepResult]) -> LintResult:
    """Run static analysis / linting on source files."""
    files_checked = random.randint(40, 120)
    warnings = random.randint(0, 5)
    print(f"  [lint] Checked {files_checked} files in '{config.source_dir}/', {warnings} warning(s)")
    return LintResult(files_checked=files_checked, warnings=warnings)


# ---------------------------------------------------------------------------
# Step 2: run_tests (with retry policy for flaky tests)
# ---------------------------------------------------------------------------

@step(
    name="run_tests",
    retry=RetryPolicy(max_attempts=3, wait_seconds=1.0, wait_multiplier=2.0),
)
async def run_tests(config: TestConfig, results: dict[str, StepResult]) -> TestResult:
    """Execute the test suite. May flake on first attempt."""
    total = random.randint(80, 200)
    # Simulate occasional flaky failure (20% chance)
    if random.random() < 0.2:
        raise RuntimeError("Flaky test failure -- transient network timeout in test_api_integration")
    coverage = round(random.uniform(config.coverage_threshold, 98.0), 1)
    print(f"  [test] {total} passed, 0 failed, coverage {coverage}%")
    return TestResult(tests_passed=total, tests_failed=0, coverage=coverage)


# ---------------------------------------------------------------------------
# Step 3: build_artifact (async step -- polls for build completion)
# ---------------------------------------------------------------------------

async def check_build(
    config: BuildConfig,
    results: dict[str, StepResult],
    result: BuildResult,
) -> dict:
    """Completeness check: simulates build completing after 3 polls."""
    # Use poll_count-like tracking via a simple counter embedded in result
    # The engine tracks poll_count for us; we simulate progress here.
    progress_steps = [0.3, 0.6, 1.0]
    # Determine current progress based on how many times we've been called
    # We store state in result fields -- but since result is immutable from
    # engine perspective we rely on random progress simulation.
    progress = random.choice(progress_steps)

    if progress >= 1.0:
        print(f"  [build] Build {result.build_id} completed!")
        return {"complete": True, "progress": 1.0, "message": "Build finished"}
    else:
        print(f"  [build] Build {result.build_id} in progress ({progress:.0%})")
        return {
            "complete": False,
            "progress": progress,
            "message": f"Compiling and packaging ({progress:.0%})",
        }


@async_step(
    name="build_artifact",
    completeness_check=check_build,
    poll=PollPolicy(interval=3.0, backoff_multiplier=1.0, timeout=120.0, max_polls=10),
)
async def build_artifact(config: BuildConfig, results: dict[str, StepResult]) -> BuildResult:
    """Kick off a container image build."""
    build_id = uuid.uuid4().hex[:12]
    artifact_url = f"https://builds.example.com/{config.repo}/{build_id}"
    print(f"  [build] Started build {build_id} for {config.repo}@{config.branch}")
    return BuildResult(build_id=build_id, artifact_url=artifact_url)


# ---------------------------------------------------------------------------
# Step 4: push_to_registry
# ---------------------------------------------------------------------------

@step(name="push_to_registry")
async def push_to_registry(
    config: RegistryConfig,
    results: dict[str, StepResult],
) -> RegistryResult:
    """Push the built artifact to a container registry."""
    build_result = cast(BuildResult, results["build_artifact"])
    image_tag = f"{build_result.build_id[:8]}-latest"
    registry_url = f"{config.registry}/myorg/myapp:{image_tag}"
    print(f"  [registry] Pushed {registry_url}")
    return RegistryResult(image_tag=image_tag, registry_url=registry_url)


# ---------------------------------------------------------------------------
# Step 5: deploy_staging (async step -- polls for healthy deployment)
# ---------------------------------------------------------------------------

async def check_deployment(
    config: DeployConfig,
    results: dict[str, StepResult],
    result: DeployResult,
) -> dict:
    """Completeness check: simulates deployment becoming healthy after 2 polls."""
    # Simulate: first poll = rolling out, second poll = healthy
    if random.random() < 0.5:
        print(f"  [deploy] Deployment {result.deployment_id} rolling out...")
        return {
            "complete": False,
            "progress": 0.5,
            "message": "Rolling update in progress",
        }
    else:
        print(f"  [deploy] Deployment {result.deployment_id} is healthy!")
        return {"complete": True, "progress": 1.0, "message": "All replicas healthy"}


@async_step(
    name="deploy_staging",
    completeness_check=check_deployment,
    poll=PollPolicy(interval=5.0, backoff_multiplier=1.0, timeout=300.0, max_polls=10),
)
async def deploy_staging(
    config: DeployConfig,
    results: dict[str, StepResult],
) -> DeployResult:
    """Deploy the container image to the staging environment."""
    registry_result = cast(RegistryResult, results["push_to_registry"])
    deployment_id = uuid.uuid4().hex[:12]
    print(
        f"  [deploy] Deploying {registry_result.registry_url} "
        f"to {config.environment} (deployment {deployment_id})"
    )
    return DeployResult(deployment_id=deployment_id, environment=config.environment)


# ---------------------------------------------------------------------------
# Step 6: run_smoke_tests
# ---------------------------------------------------------------------------

@step(name="run_smoke_tests")
async def run_smoke_tests(
    config: SmokeTestConfig,
    results: dict[str, StepResult],
) -> SmokeTestResult:
    """Run smoke tests against the staging deployment."""
    deploy_result = cast(DeployResult, results["deploy_staging"])
    checks = random.randint(5, 15)
    print(
        f"  [smoke] Ran {checks} checks against {config.base_url} "
        f"(deployment {deploy_result.deployment_id})"
    )
    return SmokeTestResult(all_passed=True, checks_run=checks)
