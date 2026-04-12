"""Build the CI/CD pipeline workflow definition."""

from __future__ import annotations

from examples.ci_cd_pipeline.steps import BuildConfig
from workchain import PollPolicy, RetryPolicy, Step, Workflow


def build_workflow(repo: str, branch: str = "main") -> Workflow:
    """Construct a 13-step CI/CD pipeline workflow with asymmetric parallelism.

    After lint_code, the pipeline fans out into three lanes of different depth:

        Lane 0 (depth 1):  run_unit_tests
        Lane 1 (depth 3):  security_scan → [license_audit ‖ vulnerability_report] → compliance_sign_off
        Lane 2 (depth 4):  run_integration_tests → build_artifact → push_to_registry → deploy_staging

    All lanes join at generate_report, which then fans out to notify_team ‖ update_dashboard.

    Args:
        repo: Repository identifier, e.g. "myorg/myapp".
        branch: Git branch to build from.

    Returns:
        A fully-configured Workflow ready to be inserted into the store.
    """
    return Workflow(
        name=f"ci-cd-{repo}-{branch}",
        steps=[
            # 1. Lint (root step)
            Step(
                name="lint_code",
                handler="examples.ci_cd_pipeline.steps.lint_code",
                config={},
                depends_on=[],
            ),
            # --- Lane 0: unit tests (depth 1) ---
            # 2. Unit tests (with retry for flaky tests)
            Step(
                name="run_unit_tests",
                handler="examples.ci_cd_pipeline.steps.run_unit_tests",
                config={},
                depends_on=["lint_code"],
                retry_policy=RetryPolicy(
                    max_attempts=3,
                    wait_seconds=1.0,
                    wait_multiplier=2.0,
                ),
            ),
            # --- Lane 1: security (depth 3, with fork) ---
            # 3. Security scan
            Step(
                name="security_scan",
                handler="examples.ci_cd_pipeline.steps.security_scan",
                config={},
                depends_on=["lint_code"],
            ),
            # 4. Run integration tests  --- Lane 2 start ---
            Step(
                name="run_integration_tests",
                handler="examples.ci_cd_pipeline.steps.run_integration_tests",
                config={},
                depends_on=["lint_code"],
            ),
            # 5. License audit (depends on security_scan — lane 1, fork A)
            Step(
                name="license_audit",
                handler="examples.ci_cd_pipeline.steps.license_audit",
                config={},
                depends_on=["security_scan"],
            ),
            # 6. Vulnerability report (depends on security_scan — lane 1, fork B)
            Step(
                name="vulnerability_report",
                handler="examples.ci_cd_pipeline.steps.vulnerability_report",
                config={},
                depends_on=["security_scan"],
            ),
            # 7. Build artifact (async — lane 2, polls for completion)
            Step(
                name="build_artifact",
                handler="examples.ci_cd_pipeline.steps.build_artifact",
                config=BuildConfig(repo=repo, branch=branch),
                depends_on=["run_integration_tests"],
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
            # 8. Push to registry (lane 2)
            Step(
                name="push_to_registry",
                handler="examples.ci_cd_pipeline.steps.push_to_registry",
                config={},
                depends_on=["build_artifact"],
            ),
            # 9. Compliance sign-off (depends on vulnerability_report — lane 1)
            Step(
                name="compliance_sign_off",
                handler="examples.ci_cd_pipeline.steps.compliance_sign_off",
                config={},
                depends_on=["vulnerability_report"],
            ),
            # 10. Deploy to staging (async — lane 2, polls for healthy rollout)
            Step(
                name="deploy_staging",
                handler="examples.ci_cd_pipeline.steps.deploy_staging",
                config={},
                depends_on=["push_to_registry"],
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
            # --- Cross-lane join ---
            # 11. Generate report (joins all 3 lanes)
            Step(
                name="generate_report",
                handler="examples.ci_cd_pipeline.steps.generate_report",
                config={},
                depends_on=[
                    "run_unit_tests",
                    "license_audit",
                    "compliance_sign_off",
                    "deploy_staging",
                ],
            ),
            # --- Post-join fan-out ---
            # 12. Notify team
            Step(
                name="notify_team",
                handler="examples.ci_cd_pipeline.steps.notify_team",
                config={},
                depends_on=["generate_report"],
            ),
            # 13. Update dashboard
            Step(
                name="update_dashboard",
                handler="examples.ci_cd_pipeline.steps.update_dashboard",
                config={},
                depends_on=["generate_report"],
            ),
        ],
    )
