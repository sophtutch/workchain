"""
Vault Access Provisioning Workflow — Complete end-to-end example

This script demonstrates a multi-step provisioning workflow:

  1. RequestApprovalStep (EventStep)     — Publish to Solace, suspend for approval
  2. WriteServiceDetailsStep (Step)      — Write service record to MongoDB
  3. CreateVaultPolicyStep (Step)         — Create HCL policy in Vault
  4. ApplyVaultPolicyStep (Step)          — Bind policy to auth method
  5. CreateADGroupStep (Step) x2         — Initiate readers + writers group creation
  6. AwaitADGroupStep (PollingStep) x2   — Poll AD until groups are confirmed

DAG structure::

    request_approval
          |
    write_service_details
          |
    create_vault_policy
          |
    apply_vault_policy
          |
     ┌────┴──────────────┐
     |                   |
  create_readers     create_writers
     |                   |
  await_readers      await_writers

Usage:

  python -m examples.vault_provisioning.example
  python -m examples.vault_provisioning.example --deny
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

from mongomock_motor import AsyncMongoMockClient

from examples.vault_provisioning.steps import (
    ADGroupConfig,
    ApplyVaultPolicyStep,
    ApprovalRequestConfig,
    AwaitADGroupStep,
    CreateADGroupStep,
    CreateVaultPolicyStep,
    RequestApprovalStep,
    ServiceDetailsConfig,
    VaultPolicyConfig,
    WriteServiceDetailsStep,
)
from workchain import (
    MongoWorkflowStore,
    Workflow,
    WorkflowRunner,
    WorkflowStatus,
)

# ============================================================================
# Constants
# ============================================================================

SERVICE_NAME = "payment-api"
VAULT_PATH = f"secret/data/{SERVICE_NAME}"

# ============================================================================
# Step registry — maps class names to classes for runner deserialization
# ============================================================================

STEP_REGISTRY = {
    "RequestApprovalStep": RequestApprovalStep,
    "WriteServiceDetailsStep": WriteServiceDetailsStep,
    "CreateVaultPolicyStep": CreateVaultPolicyStep,
    "ApplyVaultPolicyStep": ApplyVaultPolicyStep,
    "CreateADGroupStep": CreateADGroupStep,
    "AwaitADGroupStep": AwaitADGroupStep,
}


# ============================================================================
# Build Workflow
# ============================================================================


def build_workflow() -> Workflow:
    """
    Construct the Vault access provisioning workflow DAG.

    The readers and writers group branches run in parallel — both depend
    only on apply_vault_policy, not on each other.
    """
    return (
        Workflow(name="vault_access_provisioning", version="1.0.0")
        .add(
            step_id="request_approval",
            step=RequestApprovalStep(config=ApprovalRequestConfig(
                solace_queue="approvals.request",
                response_queue="approvals.response",
            )),
        )
        .add(
            step_id="write_service_details",
            step=WriteServiceDetailsStep(config=ServiceDetailsConfig(
                mongo_uri="mongodb://localhost:27017",
                database="provisioning",
                collection="services",
            )),
            depends_on=["request_approval"],
        )
        .add(
            step_id="create_vault_policy",
            step=CreateVaultPolicyStep(config=VaultPolicyConfig(
                vault_addr="https://vault.internal:8200",
                secrets_path=VAULT_PATH,
            )),
            depends_on=["write_service_details"],
        )
        .add(
            step_id="apply_vault_policy",
            step=ApplyVaultPolicyStep(config=VaultPolicyConfig(
                vault_addr="https://vault.internal:8200",
                secrets_path=VAULT_PATH,
            )),
            depends_on=["create_vault_policy"],
        )
        # --- Readers branch (parallel with writers) ---
        .add(
            step_id="create_readers_group",
            step=CreateADGroupStep(config=ADGroupConfig(
                group_name=f"{SERVICE_NAME}-vault-readers",
                group_type="readers",
                ad_server="ldap://ad.internal",
            )),
            depends_on=["apply_vault_policy"],
        )
        .add(
            step_id="await_readers_group",
            step=AwaitADGroupStep(config=ADGroupConfig(
                group_name=f"{SERVICE_NAME}-vault-readers",
                group_type="readers",
                ad_server="ldap://ad.internal",
            )),
            depends_on=["create_readers_group"],
        )
        # --- Writers branch (parallel with readers) ---
        .add(
            step_id="create_writers_group",
            step=CreateADGroupStep(config=ADGroupConfig(
                group_name=f"{SERVICE_NAME}-vault-writers",
                group_type="writers",
                ad_server="ldap://ad.internal",
            )),
            depends_on=["apply_vault_policy"],
        )
        .add(
            step_id="await_writers_group",
            step=AwaitADGroupStep(config=ADGroupConfig(
                group_name=f"{SERVICE_NAME}-vault-writers",
                group_type="writers",
                ad_server="ldap://ad.internal",
            )),
            depends_on=["create_writers_group"],
        )
    )


# ============================================================================
# Pretty print helpers
# ============================================================================

STATUS_ICONS = {
    "pending": "[ ]",
    "running": "[~]",
    "completed": "[+]",
    "failed": "[!]",
    "suspended": "[.]",
    "awaiting_poll": "[?]",
    "skipped": "[-]",
}


def print_workflow_state(run) -> None:
    """Pretty-print the current state of a WorkflowRun."""
    print(f"\n{'=' * 70}")
    print(f"  {run.workflow_name} v{run.workflow_version}  |  status: {run.status.value}  |  v{run.doc_version}")
    print(f"{'=' * 70}")
    for step in run.steps:
        icon = STATUS_ICONS.get(step.status.value, "[?]")
        line = f"  {icon} {step.step_id:<24} ({step.step_type})"
        print(line)
        if step.output:
            print(f"       output: {json.dumps(step.output)[:70]}...")
        if step.error:
            print(f"       error: {step.error}")
        if step.resume_correlation_id:
            print(f"       correlation_id: {step.resume_correlation_id}")
        if step.next_poll_at:
            print(f"       next_poll_at: {step.next_poll_at.isoformat()}")
    print()


# ============================================================================
# Runner helpers
# ============================================================================


def create_store() -> MongoWorkflowStore:
    """Create an async in-memory MongoDB store (no real DB needed)."""
    client = AsyncMongoMockClient()
    return MongoWorkflowStore(
        client=client,
        database="workchain_demo",
        owner_id="demo-runner",
        lease_ttl_seconds=30,
    )


async def tick_until_suspended_or_done(runner, store, run):
    """Tick the runner until the workflow suspends, completes, or fails."""
    max_ticks = 50
    for _ in range(max_ticks):
        processed = await runner.tick()
        if not processed:
            break
        run = await store.load(str(run.id))
        print_workflow_state(run)
        if run.status in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.SUSPENDED}:
            break
    return run


async def advance_polls(runner, store, run):
    """
    Advance polling steps past their wait intervals (for demo purposes).

    In production the runner.start() loop handles this automatically — it
    sleeps until needs_work_after and then ticks. Here we fast-forward
    next_poll_at so we don't have to wait 10 seconds per check.
    """
    while run.status == WorkflowStatus.SUSPENDED:
        # Fast-forward all poll timers to now
        for step in run.steps:
            if step.next_poll_at:
                step.next_poll_at = datetime.now(UTC) - timedelta(seconds=1)

        run.lease_owner = None
        run.lease_expires_at = None
        run.recompute_status()
        await store.save_with_version(run)

        await runner.tick()
        run = await store.load(str(run.id))
        print_workflow_state(run)

    return run


# ============================================================================
# Example: auto-approve mode
# ============================================================================


async def run_auto_approve() -> None:
    """
    Run the full provisioning workflow with automatic approval.

    1. Ticks until RequestApprovalStep suspends
    2. Immediately resumes with approval payload
    3. Continues through Vault + AD steps to completion
    """
    print("\n" + "=" * 70)
    print("  VAULT ACCESS PROVISIONING — AUTO-APPROVE MODE")
    print("=" * 70)

    store = create_store()
    workflow = build_workflow()
    run = workflow.create_run()
    run.context = {
        "service_name": SERVICE_NAME,
        "requested_by": "james@company.com",
    }
    await store.save(run)
    print(f"\nCreated WorkflowRun: {run.id}")

    runner = WorkflowRunner(
        store=store,
        registry=STEP_REGISTRY,
        workflow=workflow,
        instance_id="demo-runner",
        poll_interval_seconds=0.1,
    )

    # Phase 1: Execute until approval suspends
    print("\n--- Phase 1: Request approval (will suspend) ---")
    run = await tick_until_suspended_or_done(runner, store, run)

    if run.status != WorkflowStatus.SUSPENDED:
        print(f"Expected SUSPENDED, got {run.status.value}")
        return

    approval_step = run.get_step("request_approval")
    correlation_id = approval_step.resume_correlation_id
    print(f"Workflow suspended. Correlation ID: {correlation_id}")

    # Phase 2: Resume with approval
    print("\n--- Phase 2: Resume with approval ---")
    await runner.resume(
        correlation_id=correlation_id,
        payload={
            "approved": True,
            "approver": "manager@company.com",
            "approved_at": datetime.now(UTC).isoformat(),
        },
    )
    run = await store.load(str(run.id))
    print_workflow_state(run)

    # Phase 3: Continue through Vault and AD steps
    print("--- Phase 3: Vault policy + AD groups ---")
    run.lease_owner = None
    run.lease_expires_at = None
    run = await tick_until_suspended_or_done(runner, store, run)

    # Phase 4: Advance polling steps (fast-forward timers for demo)
    if run.status == WorkflowStatus.SUSPENDED:
        print("--- Phase 4: Awaiting AD group confirmation (polling) ---")
        run = await advance_polls(runner, store, run)

    print(f"\nWorkflow finished: {run.status.value.upper()}")


# ============================================================================
# Example: denial mode
# ============================================================================


async def run_denial() -> None:
    """
    Run the workflow where the approval is denied.

    Demonstrates failure cascading — the approval step fails and all
    downstream steps end up FAILED.
    """
    print("\n" + "=" * 70)
    print("  VAULT ACCESS PROVISIONING — DENIAL MODE")
    print("=" * 70)

    store = create_store()
    workflow = build_workflow()
    run = workflow.create_run()
    run.context = {
        "service_name": SERVICE_NAME,
        "requested_by": "james@company.com",
    }
    await store.save(run)
    print(f"\nCreated WorkflowRun: {run.id}")

    runner = WorkflowRunner(
        store=store,
        registry=STEP_REGISTRY,
        workflow=workflow,
        instance_id="demo-runner",
        poll_interval_seconds=0.1,
    )

    # Phase 1: Execute until suspended
    print("\n--- Phase 1: Request approval (will suspend) ---")
    run = await tick_until_suspended_or_done(runner, store, run)

    approval_step = run.get_step("request_approval")
    correlation_id = approval_step.resume_correlation_id

    # Phase 2: Resume with denial
    print("\n--- Phase 2: Resume with DENIAL ---")
    await runner.resume(
        correlation_id=correlation_id,
        payload={
            "approved": False,
            "reason": "Service not authorized for vault access",
        },
    )
    run = await store.load(str(run.id))
    print_workflow_state(run)

    print(f"\nWorkflow finished: {run.status.value.upper()}")


# ============================================================================
# CLI entry point
# ============================================================================


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Vault Access Provisioning Workflow Example",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--deny",
        action="store_true",
        help="Deny the approval to demonstrate failure cascading (default: auto-approve)",
    )

    args = parser.parse_args()

    if args.deny:
        await run_denial()
    else:
        await run_auto_approve()


if __name__ == "__main__":
    asyncio.run(main())
