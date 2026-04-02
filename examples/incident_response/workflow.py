"""Workflow builder for the incident response flow."""

from __future__ import annotations

# Import steps module to trigger handler registration via decorators.
from examples.incident_response import steps  # noqa: F401
from examples.incident_response.steps import TicketConfig
from workchain import PollPolicy, RetryPolicy, Step, Workflow


def build_workflow(
    service_name: str,
    severity: str,
    description: str,
) -> Workflow:
    """
    Build an incident response workflow for a service outage.

    Steps:
        1. create_ticket       -- sync, opens incident ticket
        2. page_oncall         -- sync, pages on-call engineer (retry x3)
        3. gather_diagnostics  -- sync, collects logs and metrics
        4. apply_remediation   -- async, polls until remediation completes
        5. verify_resolution   -- sync, checks service health post-fix
        6. close_ticket        -- sync, closes ticket with resolution summary
    """
    config = TicketConfig(
        service_name=service_name,
        severity=severity,
        description=description,
    )

    return Workflow(
        name="incident_response",
        steps=[
            Step(
                name="create_ticket",
                handler="examples.incident_response.steps.create_ticket",
                config=config,
            ),
            Step(
                name="page_oncall",
                handler="examples.incident_response.steps.page_oncall",
                config=config,
                retry_policy=RetryPolicy(
                    max_attempts=3,
                    wait_seconds=1.0,
                    wait_multiplier=2.0,
                ),
            ),
            Step(
                name="gather_diagnostics",
                handler="examples.incident_response.steps.gather_diagnostics",
                config=config,
            ),
            Step(
                name="apply_remediation",
                handler="examples.incident_response.steps.apply_remediation",
                config=config,
                is_async=True,
                completeness_check=(
                    "examples.incident_response.steps.check_remediation"
                ),
                poll_policy=PollPolicy(
                    interval=2.0,
                    timeout=120.0,
                    max_polls=10,
                ),
            ),
            Step(
                name="verify_resolution",
                handler="examples.incident_response.steps.verify_resolution",
                config=config,
            ),
            Step(
                name="close_ticket",
                handler="examples.incident_response.steps.close_ticket",
                config=config,
            ),
        ],
    )
