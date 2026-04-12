"""Step handlers for the customer onboarding workflow."""

from __future__ import annotations

import logging
import re
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

# ---------------------------------------------------------------------------
# Typed configs and results
# ---------------------------------------------------------------------------


class ValidateEmailConfig(StepConfig):
    email: str


class ValidateEmailResult(StepResult):
    validated: bool = False
    email: str = ""


class CreateAccountConfig(StepConfig):
    """Config for account creation (no user-facing fields — config is derived from prior steps)."""


class CreateAccountResult(StepResult):
    user_id: str = ""


class ProvisionConfig(StepConfig):
    """Config for resource provisioning (no user-facing fields — config is derived from prior steps)."""


class ProvisionResult(StepResult):
    job_id: str = ""


class WelcomeEmailConfig(StepConfig):
    """Config for welcome email (no user-facing fields — config is derived from prior steps)."""


class WelcomeEmailResult(StepResult):
    email_sent: bool = False


# ---------------------------------------------------------------------------
# Poll simulation state
# ---------------------------------------------------------------------------

# Tracks how many times each job_id has been polled (for demo purposes).
_poll_counts: dict[str, int] = {}


# ---------------------------------------------------------------------------
# Step handlers
# ---------------------------------------------------------------------------


@step(category="Customer Onboarding", description="Validate email address format")
async def validate_email(
    config: ValidateEmailConfig,
    _results: dict[str, StepResult],
) -> ValidateEmailResult:
    """Validate that the provided email address is well-formed."""
    email = config.email
    pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
    if not re.match(pattern, email):
        raise ValueError(f"Invalid email address: {email}")
    logger.info("Email validated: %s", email)
    return ValidateEmailResult(validated=True, email=email)


@step(
    retry=RetryPolicy(max_attempts=5, wait_seconds=0.5, wait_multiplier=2.0),
    category="Customer Onboarding",
    description="Create a user account with exponential backoff retry",
    depends_on=["validate_email"],
)
async def create_account(
    _config: CreateAccountConfig,
    results: dict[str, StepResult],
) -> CreateAccountResult:
    """Create a user account. Retries up to 5 times with exponential backoff."""
    email_result = cast(ValidateEmailResult, results["validate_email"])
    user_id = uuid.uuid4().hex[:12]
    logger.info("Account created: user_id=%s email=%s", user_id, email_result.email)
    return CreateAccountResult(user_id=user_id)


@completeness_check()
async def check_provisioning(
    _config: ProvisionConfig,
    _results: dict[str, StepResult],
    result: ProvisionResult,
) -> CheckResult:
    """Completeness check for resource provisioning.

    Simulates an external system that completes after 3 polls.
    """
    job_id = result.job_id
    count = _poll_counts.get(job_id, 0) + 1
    _poll_counts[job_id] = count

    total_polls = 3
    progress = min(count / total_polls, 1.0)
    complete = count >= total_polls

    logger.info(
        "Provisioning poll %d/%d for job=%s (progress=%.0f%%)",
        count, total_polls, job_id, progress * 100,
    )
    return CheckResult(
        complete=complete,
        progress=progress,
        message=f"Provisioning poll {count}/{total_polls}",
    )


@async_step(
    completeness_check=check_provisioning,
    poll=PollPolicy(interval=2.0, timeout=60.0, max_polls=5),
    category="Customer Onboarding",
    description="Provision account resources asynchronously",
    depends_on=["create_account"],
)
async def provision_resources(
    _config: ProvisionConfig,
    results: dict[str, StepResult],
) -> ProvisionResult:
    """Submit resource provisioning for the new account."""
    account_result = cast(CreateAccountResult, results["create_account"])
    job_id = f"prov-{account_result.user_id}"
    logger.info("Provisioning submitted: job_id=%s", job_id)
    return ProvisionResult(job_id=job_id)


@step(category="Notification", description="Send welcome email to new customer", depends_on=["validate_email", "create_account", "provision_resources"])
async def send_welcome_email(
    _config: WelcomeEmailConfig,
    results: dict[str, StepResult],
) -> WelcomeEmailResult:
    """Send a welcome email to the newly onboarded customer."""
    email_result = cast(ValidateEmailResult, results["validate_email"])
    account_result = cast(CreateAccountResult, results["create_account"])
    logger.info(
        "Welcome email sent to %s (user_id=%s)",
        email_result.email,
        account_result.user_id,
    )
    return WelcomeEmailResult(email_sent=True)
