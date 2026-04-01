"""
Example: User onboarding workflow with a mix of sync and async steps.

Run with:
    python -m workflow_engine.example

Requires a running MongoDB instance at localhost:27017.
"""

from __future__ import annotations

import asyncio
import logging

from motor.motor_asyncio import AsyncIOMotorClient
from workflow_engine.decorators import async_step, step
from workflow_engine.engine import WorkflowEngine
from workflow_engine.models import (
    PollPolicy,
    RetryPolicy,
    Step,
    StepConfig,
    Workflow,
)
from workflow_engine.store import MongoWorkflowStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


# ---------------------------------------------------------------------------
# Step handlers
# ---------------------------------------------------------------------------

@step(name="validate_input")
async def validate_input(config: dict, context: dict) -> dict:
    """Validate the user payload."""
    email = config.get("email", "")
    if "@" not in email:
        raise ValueError(f"Invalid email: {email}")
    return {"validated": True, "email": email}


@step(name="create_account", retry=RetryPolicy(max_attempts=5))
async def create_account(config: dict, context: dict) -> dict:
    """Create the user account (simulated)."""
    email = context.get("email", config.get("email"))
    # In reality: call your user service / write to DB
    user_id = f"user_{hash(email) % 10000}"
    return {"user_id": user_id}


# Track poll count to simulate eventual completion
_poll_counts: dict[str, int] = {}


async def check_provisioning(config: dict, context: dict, result: dict) -> bool | dict:
    """
    Completeness check — returns a PollHint dict with progress info.
    Simulates completion after 3 polls.

    Can return:
      - bool: simple True/False
      - dict: {"complete": bool, "retry_after": float, "progress": float, "message": str}
    """
    job_id = result.get("job_id", "")
    _poll_counts[job_id] = _poll_counts.get(job_id, 0) + 1
    count = _poll_counts[job_id]
    done = count >= 3

    if done:
        logging.getLogger(__name__).info("Provisioning complete for %s", job_id)
        return {"complete": True, "progress": 1.0, "message": "All resources ready"}

    # Return a hint with progress and optional retry_after override
    return {
        "complete": False,
        "progress": count / 3.0,
        "message": f"Provisioning step {count}/3",
        # "retry_after": 1.0,  # uncomment to override the engine's backoff schedule
    }


# completeness_check accepts a callable directly — no manual _STEP_REGISTRY needed
@async_step(
    name="provision_resources",
    completeness_check=check_provisioning,
    poll=PollPolicy(interval=2.0, backoff_multiplier=1.5, max_interval=10.0, timeout=60.0),
    retry=RetryPolicy(max_attempts=3),
)
async def provision_resources(config: dict, context: dict) -> dict:
    """
    Kick off async resource provisioning (e.g. cloud infra, mailbox).
    Returns a job ID; the engine will poll check_provisioning until done.
    """
    user_id = context.get("user_id", "unknown")
    job_id = f"job_{user_id}_provision"
    # In reality: POST to a provisioning API
    return {"job_id": job_id}


@step(name="send_welcome_email")
async def send_welcome_email(config: dict, context: dict) -> dict:
    """Send a welcome email (simulated)."""
    email = context.get("email", "unknown")
    logging.getLogger(__name__).info("Sending welcome email to %s", email)
    return {"email_sent": True}


# ---------------------------------------------------------------------------
# Build and submit the workflow
# ---------------------------------------------------------------------------

def build_onboarding_workflow(email: str) -> Workflow:
    return Workflow(
        name="user_onboarding",
        steps=[
            Step(
                name="validate_input",
                handler="validate_input",
                config=StepConfig(data={"email": email}),
            ),
            Step(
                name="create_account",
                handler="create_account",
                retry_policy=RetryPolicy(max_attempts=5),
            ),
            Step(
                name="provision_resources",
                handler="provision_resources",
                is_async=True,
                # Matches the auto-registered name from @async_step decorator
                completeness_check="workflow_engine.example.check_provisioning",
                poll_policy=PollPolicy(
                    interval=2.0,
                    backoff_multiplier=1.5,
                    max_interval=10.0,
                    timeout=60.0,
                ),
            ),
            Step(
                name="send_welcome_email",
                handler="send_welcome_email",
            ),
        ],
    )


async def main():
    # Connect to MongoDB
    client = AsyncIOMotorClient("mongodb://localhost:27017")
    db = client["workflow_demo"]
    store = MongoWorkflowStore(db, lock_ttl_seconds=30)

    # Submit a new workflow
    wf = build_onboarding_workflow("alice@example.com")
    await store.insert(wf)
    print(f"Submitted workflow: {wf.id}")

    # Start the engine — it will discover and run the workflow
    engine = WorkflowEngine(
        store,
        claim_interval=2.0,
        heartbeat_interval=5.0,
        max_concurrent=3,
    )
    await engine.start()

    # Let it run for a bit, then shut down.
    # With claim-poll-release, each poll cycle requires a claim loop iteration
    # (claim_interval=2s) plus the poll_policy interval, so allow enough time.
    await asyncio.sleep(30)
    await engine.stop()

    # Check final state
    final = await store.get(wf.id)
    print(f"Final status: {final.status}")
    print(f"Context: {final.context}")
    for s in final.steps:
        extra = f"attempts={s.attempt}"
        if s.is_async:
            extra += f", polls={s.poll_count}, progress={s.last_poll_progress}"
        print(f"  {s.name}: {s.status} ({extra})")


if __name__ == "__main__":
    asyncio.run(main())
