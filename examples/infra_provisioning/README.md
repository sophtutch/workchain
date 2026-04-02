# Infrastructure Provisioning Example

A 6-step cloud infrastructure provisioning pipeline built with **workchain**, demonstrating multiple async polling steps with varying intervals and timeouts, typed configs/results, and result passing between steps.

## Pipeline Steps

```mermaid
flowchart LR
    A[create_vpc] --> B[provision_database]
    B --> C[deploy_application]
    C --> D[configure_dns]
    D --> E[issue_tls_cert]
    E --> F[health_check]

    style A fill:#e8f5e9
    style B fill:#e3f2fd
    style C fill:#e3f2fd
    style D fill:#e8f5e9
    style E fill:#e3f2fd
    style F fill:#e8f5e9
```

| Step | Type | Poll Interval | Timeout | Notes |
|------|------|---------------|---------|-------|
| `create_vpc` | sync | -- | -- | Creates VPC with subnets in target region |
| `provision_database` | **async** | 5s (backoff 1.5x) | 600s | Provisions RDS instance, polls until available (~3 polls) |
| `deploy_application` | **async** | 3s (fixed) | 300s | Deploys containers, polls until all replicas healthy (~2 polls) |
| `configure_dns` | sync | -- | -- | Creates DNS records pointing to the deployment |
| `issue_tls_cert` | **async** | 10s (fixed) | 900s | Requests TLS certificate, polls until issued (~2 polls) |
| `health_check` | sync | -- | -- | Verifies the full stack is reachable and healthy |

## Running the Example

```bash
pip install mongomock-motor
python -m examples.infra_provisioning.example
```

## Key Features Demonstrated

- **Three async steps** -- `provision_database`, `deploy_application`, and `issue_tls_cert` each poll with different intervals and timeouts
- **Backoff multiplier** -- `provision_database` uses 1.5x backoff (5s, 7.5s, 11.25s...) while others use fixed intervals
- **Result chaining** -- each step reads from its predecessors (VPC ID flows to DB, DB endpoint flows to deploy, etc.)
- **Typed configs and results** -- all 6 steps have dedicated `StepConfig` and `StepResult` subclasses
- **Long pipeline** -- 6 sequential steps modeling a realistic cloud provisioning flow
