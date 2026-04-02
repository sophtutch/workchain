"""Build the infrastructure provisioning workflow definition."""

from __future__ import annotations

from examples.infra_provisioning.steps import (
    DatabaseConfig,
    DeployConfig,
    DnsConfig,
    HealthCheckConfig,
    TlsConfig,
    VpcConfig,
)
from workchain import PollPolicy, Step, Workflow


def build_workflow(
    domain: str,
    image: str,
    region: str = "us-east-1",
) -> Workflow:
    """Construct a 6-step infrastructure provisioning workflow.

    Args:
        domain: Target domain name, e.g. "app.example.com".
        image: Container image to deploy, e.g. "myorg/myapp:latest".
        region: AWS region for the infrastructure.

    Returns:
        A fully-configured Workflow ready to be inserted into the store.
    """
    return Workflow(
        name=f"infra-{domain}",
        steps=[
            # 1. Create VPC and subnets
            Step(
                name="create_vpc",
                handler="examples.infra_provisioning.steps.create_vpc",
                config=VpcConfig(cidr_block="10.0.0.0/16", region=region),
            ),
            # 2. Provision database (async -- polls until available)
            Step(
                name="provision_database",
                handler="examples.infra_provisioning.steps.provision_database",
                config=DatabaseConfig(engine="postgres", instance_class="db.t3.medium"),
                is_async=True,
                completeness_check=(
                    "examples.infra_provisioning.steps.check_database"
                ),
                poll_policy=PollPolicy(
                    interval=5.0,
                    backoff_multiplier=1.5,
                    max_interval=30.0,
                    timeout=600.0,
                    max_polls=15,
                ),
            ),
            # 3. Deploy application (async -- polls until healthy)
            Step(
                name="deploy_application",
                handler="examples.infra_provisioning.steps.deploy_application",
                config=DeployConfig(image=image, replicas=2),
                is_async=True,
                completeness_check=(
                    "examples.infra_provisioning.steps.check_deployment"
                ),
                poll_policy=PollPolicy(
                    interval=3.0,
                    backoff_multiplier=1.0,
                    timeout=300.0,
                    max_polls=10,
                ),
            ),
            # 4. Configure DNS records
            Step(
                name="configure_dns",
                handler="examples.infra_provisioning.steps.configure_dns",
                config=DnsConfig(domain=domain, record_type="A"),
            ),
            # 5. Issue TLS certificate (async -- polls until issued)
            Step(
                name="issue_tls_cert",
                handler="examples.infra_provisioning.steps.issue_tls_cert",
                config=TlsConfig(domain=domain),
                is_async=True,
                completeness_check=(
                    "examples.infra_provisioning.steps.check_tls_cert"
                ),
                poll_policy=PollPolicy(
                    interval=10.0,
                    backoff_multiplier=1.0,
                    timeout=900.0,
                    max_polls=20,
                ),
            ),
            # 6. Final health check
            Step(
                name="health_check",
                handler="examples.infra_provisioning.steps.health_check",
                config=HealthCheckConfig(
                    endpoint=f"https://{domain}/healthz",
                    expected_status=200,
                ),
            ),
        ],
    )
