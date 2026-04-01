"""Workflow builder for the customer onboarding flow."""

from __future__ import annotations

from workchain import PollPolicy, RetryPolicy, Step, Workflow

# Import steps module to trigger handler registration via decorators.
from . import steps  # noqa: F401
from .steps import ValidateEmailConfig


def build_workflow(email: str) -> Workflow:
    """
    Build a customer onboarding workflow for the given email address.

    Steps:
        1. validate_email   -- sync, validates email format
        2. create_account   -- sync, retry up to 5 times with backoff
        3. provision_resources -- async, polls completeness 3 times
        4. send_welcome_email  -- sync, sends welcome message
    """
    config = ValidateEmailConfig(email=email)

    return Workflow(
        name="customer_onboarding",
        steps=[
            Step(
                name="validate_email",
                handler="validate_email",
                config=config,
            ),
            Step(
                name="create_account",
                handler="create_account",
                retry_policy=RetryPolicy(
                    max_attempts=5,
                    wait_seconds=0.5,
                    wait_multiplier=2.0,
                ),
            ),
            Step(
                name="provision_resources",
                handler="provision_resources",
                is_async=True,
                completeness_check=(
                    "examples.customer_onboarding.steps.check_provisioning"
                ),
                poll_policy=PollPolicy(
                    interval=2.0,
                    timeout=60.0,
                    max_polls=5,
                ),
            ),
            Step(
                name="send_welcome_email",
                handler="send_welcome_email",
            ),
        ],
    )
