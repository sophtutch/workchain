"""Build the CI/CD pipeline workflow definition."""

from __future__ import annotations

from examples.ci_cd_pipeline.steps import (
    BuildConfig,
    DeployConfig,
    LintConfig,
    RegistryConfig,
    SmokeTestConfig,
    TestConfig,
)
from workchain import PollPolicy, RetryPolicy, Step, Workflow


def build_workflow(repo: str, branch: str = "main") -> Workflow:
    """Construct a 6-step CI/CD pipeline workflow.

    Args:
        repo: Repository identifier, e.g. "myorg/myapp".
        branch: Git branch to build from.

    Returns:
        A fully-configured Workflow ready to be inserted into the store.
    """
    return Workflow(
        name=f"ci-cd-{repo}-{branch}",
        steps=[
            # 1. Lint
            Step(
                name="lint_code",
                handler="lint_code",
                config=LintConfig(source_dir="src"),
            ),
            # 2. Tests (with retry for flaky tests)
            Step(
                name="run_tests",
                handler="run_tests",
                config=TestConfig(test_dir="tests", coverage_threshold=80.0),
                retry_policy=RetryPolicy(
                    max_attempts=3,
                    wait_seconds=1.0,
                    wait_multiplier=2.0,
                ),
            ),
            # 3. Build artifact (async -- polls for completion)
            Step(
                name="build_artifact",
                handler="build_artifact",
                config=BuildConfig(repo=repo, branch=branch),
                is_async=True,
                completeness_check=(
                    "examples.ci_cd_pipeline.steps.check_build"
                ),
                poll_policy=PollPolicy(
                    interval=3.0,
                    backoff_multiplier=1.0,
                    timeout=120.0,
                    max_polls=10,
                ),
            ),
            # 4. Push to container registry
            Step(
                name="push_to_registry",
                handler="push_to_registry",
                config=RegistryConfig(registry="ghcr.io"),
            ),
            # 5. Deploy to staging (async -- polls for healthy rollout)
            Step(
                name="deploy_staging",
                handler="deploy_staging",
                config=DeployConfig(environment="staging"),
                is_async=True,
                completeness_check=(
                    "examples.ci_cd_pipeline.steps.check_deployment"
                ),
                poll_policy=PollPolicy(
                    interval=5.0,
                    backoff_multiplier=1.0,
                    timeout=300.0,
                    max_polls=10,
                ),
            ),
            # 6. Smoke tests against staging
            Step(
                name="run_smoke_tests",
                handler="run_smoke_tests",
                config=SmokeTestConfig(
                    base_url="https://staging.example.com",
                ),
            ),
        ],
    )
