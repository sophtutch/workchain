"""Step handlers for the infrastructure provisioning workflow.

Steps:
  1. create_vpc          - Create a VPC with subnets in the target region
  2. provision_database  - Async: provision an RDS instance, poll until available
  3. deploy_application  - Async: deploy containers, poll until healthy
  4. configure_dns       - Create DNS records pointing to the deployment
  5. issue_tls_cert      - Async: request a TLS certificate, poll until issued
  6. health_check        - Verify the full stack is reachable and healthy
"""

from __future__ import annotations

import random
import uuid
from typing import cast

from pydantic import Field

from workchain import (
    PollPolicy,
    StepConfig,
    StepResult,
    async_step,
    step,
)

_rng = random.SystemRandom()

# ---------------------------------------------------------------------------
# Configs and Results
# ---------------------------------------------------------------------------


class VpcConfig(StepConfig):
    cidr_block: str = "10.0.0.0/16"
    region: str = "us-east-1"


class VpcResult(StepResult):
    vpc_id: str = ""
    subnet_ids: list[str] = Field(default_factory=list)


class DatabaseConfig(StepConfig):
    engine: str = "postgres"
    instance_class: str = "db.t3.medium"


class DatabaseResult(StepResult):
    db_instance_id: str = ""
    endpoint: str = ""
    port: int = 5432


class DeployConfig(StepConfig):
    image: str = ""
    replicas: int = 2


class DeployResult(StepResult):
    deployment_id: str = ""
    replicas_ready: int = 0


class DnsConfig(StepConfig):
    domain: str = ""
    record_type: str = "A"


class DnsResult(StepResult):
    record_id: str = ""
    fqdn: str = ""


class TlsConfig(StepConfig):
    domain: str = ""


class TlsResult(StepResult):
    certificate_arn: str = ""
    valid_until: str = ""


class HealthCheckConfig(StepConfig):
    endpoint: str = ""
    expected_status: int = 200


class HealthCheckResult(StepResult):
    status_code: int = 0
    response_time_ms: float = 0.0
    healthy: bool = False


# ---------------------------------------------------------------------------
# Step 1: create_vpc
# ---------------------------------------------------------------------------


@step(name="create_vpc")
async def create_vpc(config: VpcConfig, _results: dict[str, StepResult]) -> VpcResult:
    """Create a VPC with public and private subnets."""
    vpc_id = f"vpc-{uuid.uuid4().hex[:12]}"
    subnet_count = _rng.randint(2, 4)
    subnet_ids = [f"subnet-{uuid.uuid4().hex[:12]}" for _ in range(subnet_count)]
    print(
        f"  [vpc] Created {vpc_id} ({config.cidr_block}) in {config.region} "
        f"with {subnet_count} subnets"
    )
    return VpcResult(vpc_id=vpc_id, subnet_ids=subnet_ids)


# ---------------------------------------------------------------------------
# Step 2: provision_database (async -- polls until DB is available)
# ---------------------------------------------------------------------------


async def check_database(
    _config: DatabaseConfig,
    _results: dict[str, StepResult],
    result: DatabaseResult,
) -> dict:
    """Completeness check: simulates database becoming available after 3 polls."""
    stages = [
        (0.3, "creating", "Creating DB instance"),
        (0.6, "configuring", "Configuring parameter groups"),
        (1.0, "available", "Database available"),
    ]
    # Pick a stage based on random progress to simulate advancement
    progress = _rng.choice([s[0] for s in stages])

    if progress >= 1.0:
        print(f"  [db] Instance {result.db_instance_id} is available!")
        return {"complete": True, "progress": 1.0, "message": "Database available"}
    label = next(s[2] for s in stages if s[0] == progress)
    print(f"  [db] Instance {result.db_instance_id} -- {label} ({progress:.0%})")
    return {"complete": False, "progress": progress, "message": label}


@async_step(
    name="provision_database",
    completeness_check=check_database,
    poll=PollPolicy(interval=5.0, backoff_multiplier=1.5, max_interval=30.0, timeout=600.0, max_polls=15),
)
async def provision_database(
    config: DatabaseConfig,
    results: dict[str, StepResult],
) -> DatabaseResult:
    """Provision an RDS database instance."""
    vpc_result = cast(VpcResult, results["create_vpc"])
    db_instance_id = f"db-{uuid.uuid4().hex[:12]}"
    endpoint = f"{db_instance_id}.cluster.{config.engine}.amazonaws.com"
    print(
        f"  [db] Provisioning {config.engine} ({config.instance_class}) "
        f"in VPC {vpc_result.vpc_id} -- instance {db_instance_id}"
    )
    return DatabaseResult(
        db_instance_id=db_instance_id,
        endpoint=endpoint,
        port=5432,
    )


# ---------------------------------------------------------------------------
# Step 3: deploy_application (async -- polls until replicas healthy)
# ---------------------------------------------------------------------------


async def check_deployment(
    config: DeployConfig,
    _results: dict[str, StepResult],
    result: DeployResult,
) -> dict:
    """Completeness check: simulates deployment becoming healthy after 2 polls."""
    if _rng.random() < 0.5:
        ready = max(1, config.replicas - 1)
        print(
            f"  [deploy] Deployment {result.deployment_id} -- "
            f"{ready}/{config.replicas} replicas ready"
        )
        return {
            "complete": False,
            "progress": ready / config.replicas,
            "message": f"{ready}/{config.replicas} replicas ready",
        }
    print(
        f"  [deploy] Deployment {result.deployment_id} -- "
        f"{config.replicas}/{config.replicas} replicas healthy!"
    )
    return {
        "complete": True,
        "progress": 1.0,
        "message": "All replicas healthy",
    }


@async_step(
    name="deploy_application",
    completeness_check=check_deployment,
    poll=PollPolicy(interval=3.0, backoff_multiplier=1.0, timeout=300.0, max_polls=10),
)
async def deploy_application(
    config: DeployConfig,
    results: dict[str, StepResult],
) -> DeployResult:
    """Deploy the application containers."""
    db_result = cast(DatabaseResult, results["provision_database"])
    deployment_id = f"deploy-{uuid.uuid4().hex[:12]}"
    print(
        f"  [deploy] Deploying {config.image} ({config.replicas} replicas) "
        f"with DB endpoint {db_result.endpoint} -- deployment {deployment_id}"
    )
    return DeployResult(deployment_id=deployment_id, replicas_ready=0)


# ---------------------------------------------------------------------------
# Step 4: configure_dns
# ---------------------------------------------------------------------------


@step(name="configure_dns")
async def configure_dns(
    config: DnsConfig,
    results: dict[str, StepResult],
) -> DnsResult:
    """Create DNS records pointing to the deployed application."""
    deploy_result = cast(DeployResult, results["deploy_application"])
    record_id = f"rec-{uuid.uuid4().hex[:12]}"
    fqdn = f"{config.domain}."
    print(
        f"  [dns] Created {config.record_type} record {record_id} "
        f"for {fqdn} -> deployment {deploy_result.deployment_id}"
    )
    return DnsResult(record_id=record_id, fqdn=fqdn)


# ---------------------------------------------------------------------------
# Step 5: issue_tls_cert (async -- polls until certificate is issued)
# ---------------------------------------------------------------------------


async def check_tls_cert(
    config: TlsConfig,
    _results: dict[str, StepResult],
    _result: TlsResult,
) -> dict:
    """Completeness check: simulates certificate issued after 2 polls."""
    if _rng.random() < 0.5:
        print(f"  [tls] Certificate for {config.domain} -- pending validation")
        return {
            "complete": False,
            "progress": 0.5,
            "message": "Pending domain validation",
        }
    print(f"  [tls] Certificate for {config.domain} -- issued!")
    return {"complete": True, "progress": 1.0, "message": "Certificate issued"}


@async_step(
    name="issue_tls_cert",
    completeness_check=check_tls_cert,
    poll=PollPolicy(interval=10.0, backoff_multiplier=1.0, timeout=900.0, max_polls=20),
)
async def issue_tls_cert(
    _config: TlsConfig,
    results: dict[str, StepResult],
) -> TlsResult:
    """Request a TLS certificate for the domain."""
    dns_result = cast(DnsResult, results["configure_dns"])
    certificate_arn = f"arn:aws:acm:us-east-1:123456789:certificate/{uuid.uuid4().hex[:12]}"
    print(
        f"  [tls] Requesting TLS certificate for {dns_result.fqdn} "
        f"-- ARN {certificate_arn}"
    )
    return TlsResult(certificate_arn=certificate_arn, valid_until="2027-04-01T00:00:00Z")


# ---------------------------------------------------------------------------
# Step 6: health_check
# ---------------------------------------------------------------------------


@step(name="health_check")
async def health_check(
    config: HealthCheckConfig,
    results: dict[str, StepResult],
) -> HealthCheckResult:
    """Verify the full stack is reachable and returns the expected status."""
    tls_result = cast(TlsResult, results["issue_tls_cert"])
    response_time = round(_rng.uniform(50.0, 200.0), 1)
    print(
        f"  [health] GET {config.endpoint} -> {config.expected_status} "
        f"({response_time}ms), cert {tls_result.certificate_arn[:40]}..."
    )
    return HealthCheckResult(
        status_code=config.expected_status,
        response_time_ms=response_time,
        healthy=True,
    )
