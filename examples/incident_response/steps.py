"""Step handlers for the incident response workflow."""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from typing import cast

from pydantic import Field

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


class TicketConfig(StepConfig):
    service_name: str
    severity: str
    description: str


class TicketResult(StepResult):
    ticket_id: str = ""
    created_at: str = ""


class PageConfig(StepConfig):
    """No user-facing fields."""


class PageResult(StepResult):
    paged_user: str = ""
    acknowledged: bool = False


class DiagnosticsResult(StepResult):
    logs_collected: int = 0
    metrics_snapshot: dict = Field(default_factory=dict)


class RemediationConfig(StepConfig):
    """No user-facing fields."""


class RemediationResult(StepResult):
    remediation_id: str = ""
    action_taken: str = ""


class VerifyResult(StepResult):
    service_healthy: bool = False
    latency_ms: float = 0.0


class CloseTicketResult(StepResult):
    closed: bool = False
    resolution_time_minutes: float = 0.0


# ---------------------------------------------------------------------------
# Poll simulation state
# ---------------------------------------------------------------------------

# Tracks how many times each remediation_id has been polled (for demo).
_poll_counts: dict[str, int] = {}


# ---------------------------------------------------------------------------
# Step handlers
# ---------------------------------------------------------------------------


@step(category="Incident Response", description="Open an incident ticket in the tracking system")
async def create_ticket(
    config: TicketConfig,
    _results: dict[str, StepResult],
) -> TicketResult:
    """Open an incident ticket in the tracking system."""
    await asyncio.sleep(random.uniform(5, 20))
    ticket_id = f"INC-{uuid.uuid4().hex[:8].upper()}"
    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    logger.info(
        "Ticket created: %s  severity=%s  service=%s",
        ticket_id, config.severity, config.service_name,
    )
    return TicketResult(ticket_id=ticket_id, created_at=created_at)


@step(
    retry=RetryPolicy(max_attempts=3, wait_seconds=1.0, wait_multiplier=2.0),
    category="Incident Response",
    description="Page on-call engineer via escalation policy",
    depends_on=["create_ticket"],
)
async def page_oncall(
    _config: TicketConfig,
    results: dict[str, StepResult],
) -> PageResult:
    """Page the on-call engineer via the escalation policy."""
    await asyncio.sleep(random.uniform(5, 20))
    ticket_result = cast(TicketResult, results["create_ticket"])
    paged_user = "oncall-eng@example.com"
    logger.info(
        "Paged %s for ticket %s (policy=default)",
        paged_user, ticket_result.ticket_id,
    )
    return PageResult(paged_user=paged_user, acknowledged=True)


@step(category="Incident Response", description="Collect logs and metrics for the affected service", depends_on=["create_ticket"])
async def gather_diagnostics(
    _config: TicketConfig,
    results: dict[str, StepResult],
) -> DiagnosticsResult:
    """Collect logs and metrics snapshots for the affected service."""
    await asyncio.sleep(random.uniform(5, 20))
    ticket_result = cast(TicketResult, results["create_ticket"])
    logs_collected = 142
    metrics_snapshot = {
        "cpu_percent": 87.3,
        "memory_percent": 64.1,
        "error_rate": 12.5,
        "p99_latency_ms": 2400.0,
    }
    logger.info(
        "Diagnostics gathered for %s: %d log entries, %d metrics",
        ticket_result.ticket_id, logs_collected, len(metrics_snapshot),
    )
    return DiagnosticsResult(
        logs_collected=logs_collected,
        metrics_snapshot=metrics_snapshot,
    )


@completeness_check()
async def check_remediation(
    _config: TicketConfig,
    _results: dict[str, StepResult],
    result: RemediationResult,
) -> CheckResult:
    """Completeness check for remediation.

    Simulates an external remediation system that resolves after 3 polls.
    """
    await asyncio.sleep(random.uniform(3, 8))
    rem_id = result.remediation_id
    count = _poll_counts.get(rem_id, 0) + 1
    _poll_counts[rem_id] = count

    total_polls = 3
    progress = min(count / total_polls, 1.0)
    complete = count >= total_polls

    logger.info(
        "Remediation poll %d/%d for %s (progress=%.0f%%)",
        count, total_polls, rem_id, progress * 100,
    )
    return CheckResult(
        complete=complete,
        progress=progress,
        message=f"Remediation poll {count}/{total_polls}",
    )


@async_step(
    completeness_check=check_remediation,
    poll=PollPolicy(interval=2.0, timeout=120.0, max_polls=10),
    category="Incident Response",
    description="Execute automated remediation runbook",
    depends_on=["gather_diagnostics"],
)
async def apply_remediation(
    _config: TicketConfig,
    results: dict[str, StepResult],
) -> RemediationResult:
    """Submit the automated remediation runbook for execution."""
    await asyncio.sleep(random.uniform(5, 20))
    diagnostics = cast(DiagnosticsResult, results["gather_diagnostics"])
    remediation_id = f"REM-{uuid.uuid4().hex[:8].upper()}"
    action = "restart_service"
    if diagnostics.metrics_snapshot.get("error_rate", 0) > 10:
        action = "rollback_and_restart"
    logger.info(
        "Remediation submitted: id=%s action=%s",
        remediation_id, action,
    )
    return RemediationResult(remediation_id=remediation_id, action_taken=action)


@step(category="Incident Response", description="Verify service has recovered after remediation", depends_on=["apply_remediation"])
async def verify_resolution(
    _config: TicketConfig,
    results: dict[str, StepResult],
) -> VerifyResult:
    """Verify the service has recovered after remediation."""
    await asyncio.sleep(random.uniform(5, 20))
    remediation = cast(RemediationResult, results["apply_remediation"])
    healthy = True
    latency_ms = 45.2
    logger.info(
        "Verification after %s: healthy=%s latency=%.1fms",
        remediation.remediation_id, healthy, latency_ms,
    )
    return VerifyResult(service_healthy=healthy, latency_ms=latency_ms)


@step(category="Incident Response", description="Close incident ticket with resolution summary", depends_on=["create_ticket", "verify_resolution"])
async def close_ticket(
    _config: TicketConfig,
    results: dict[str, StepResult],
) -> CloseTicketResult:
    """Close the incident ticket with a resolution summary."""
    await asyncio.sleep(random.uniform(5, 20))
    ticket_result = cast(TicketResult, results["create_ticket"])
    verify = cast(VerifyResult, results["verify_resolution"])
    resolution_minutes = 8.5  # simulated
    logger.info(
        "Ticket %s closed: healthy=%s resolution_time=%.1f min",
        ticket_result.ticket_id, verify.service_healthy, resolution_minutes,
    )
    return CloseTicketResult(closed=True, resolution_time_minutes=resolution_minutes)
