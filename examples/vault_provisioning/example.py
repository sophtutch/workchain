"""
Vault Access Provisioning Workflow — End-to-end example

Demonstrates a multi-step provisioning workflow:

  1. request_approval     (@async_step) — Submit approval, poll until granted
  2. write_service_details (@step)      — Write service record to MongoDB
  3. create_vault_policy   (@step)      — Create HCL policy in Vault
  4. apply_vault_policy    (@step)      — Bind policy to AppRole auth
  5. create_ad_group       (@step)      — Initiate AD group creation
  6. await_ad_group        (@async_step) — Poll AD until group exists

Usage:

  python -m examples.vault_provisioning.example
"""

from __future__ import annotations

import asyncio
import logging

from mongomock_motor import AsyncMongoMockClient

# Import step handlers to trigger decorator registration
import examples.vault_provisioning.steps as _steps  # noqa: F401
from workchain import (
    MongoWorkflowStore,
    PollPolicy,
    Step,
    StepConfig,
    Workflow,
    WorkflowEngine,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

SERVICE_NAME = "payment-api"
VAULT_PATH = f"secret/data/{SERVICE_NAME}"


# ============================================================================
# Build Workflow
# ============================================================================


def build_workflow() -> Workflow:
    """Construct the Vault access provisioning workflow."""
    return Workflow(
        name="vault_access_provisioning",
        context={
            "service_name": SERVICE_NAME,
            "requested_by": "james@company.com",
        },
        steps=[
            Step(
                name="request_approval",
                handler="request_approval",
                is_async=True,
                completeness_check="examples.vault_provisioning.steps.check_approval",
                poll_policy=PollPolicy(interval=2.0, backoff_multiplier=1.0, timeout=60.0),
            ),
            Step(
                name="write_service_details",
                handler="write_service_details",
                config=StepConfig(data={
                    "mongo_uri": "mongodb://localhost:27017",
                    "collection": "services",
                }),
            ),
            Step(
                name="create_vault_policy",
                handler="create_vault_policy",
                config=StepConfig(data={"secrets_path": VAULT_PATH}),
            ),
            Step(
                name="apply_vault_policy",
                handler="apply_vault_policy",
                config=StepConfig(data={"secrets_path": VAULT_PATH}),
            ),
            Step(
                name="create_ad_group",
                handler="create_ad_group",
                config=StepConfig(data={
                    "group_name": f"{SERVICE_NAME}-vault-readers",
                    "group_type": "readers",
                }),
            ),
            Step(
                name="await_ad_group",
                handler="await_ad_group",
                is_async=True,
                completeness_check="examples.vault_provisioning.steps.check_ad_group",
                config=StepConfig(data={
                    "group_name": f"{SERVICE_NAME}-vault-readers",
                }),
                poll_policy=PollPolicy(
                    interval=2.0,
                    backoff_multiplier=1.5,
                    max_interval=10.0,
                    timeout=120.0,
                ),
            ),
        ],
    )


# ============================================================================
# Main
# ============================================================================


async def main() -> None:
    print("\n" + "=" * 60)
    print("  VAULT ACCESS PROVISIONING WORKFLOW")
    print("=" * 60)

    # In-memory MongoDB
    client = AsyncMongoMockClient()
    db = client["workchain_demo"]
    store = MongoWorkflowStore(db, lock_ttl_seconds=30)

    # Build and insert workflow
    wf = build_workflow()
    await store.insert(wf)
    print(f"\nSubmitted workflow: {wf.id}")
    print(f"Service: {SERVICE_NAME}, Requested by: james@company.com")

    # Start the engine
    engine = WorkflowEngine(
        store,
        claim_interval=1.0,
        heartbeat_interval=5.0,
        max_concurrent=3,
    )
    await engine.start()

    # Let it run — approval (2 polls) + AD group (3 polls) need time
    await asyncio.sleep(30)
    await engine.stop()

    # Check final state
    final = await store.get(wf.id)
    print(f"\n{'=' * 60}")
    print(f"  Final status: {final.status.value}")
    print(f"  Context keys: {list(final.context.keys())}")
    print(f"{'=' * 60}")
    for s in final.steps:
        extra = f"attempts={s.attempt}"
        if s.is_async:
            extra += f", polls={s.poll_count}"
            if s.last_poll_progress is not None:
                extra += f", progress={s.last_poll_progress:.0%}"
        print(f"  {s.name}: {s.status.value} ({extra})")
    print()


if __name__ == "__main__":
    asyncio.run(main())
