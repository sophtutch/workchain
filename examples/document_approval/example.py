"""
Document Approval Workflow — Complete end-to-end example

This script demonstrates a realistic document processing workflow using workchain:

  1. FetchDocumentStep (Step)         — Fetches document from remote API
  2. ApprovalStep (EventStep)        — Suspends workflow waiting for human approval
  3. ProcessJobStep (PollingStep)     — Polls an async background job until done
  4. SendNotificationStep (Step)     — Sends result notification via email

The workflow demonstrates:
  - All three step types (Step, EventStep, PollingStep)
  - DAG dependency resolution
  - Shared context flow between steps
  - MongoDB persistence with mongomock-motor (no real DB needed)
  - EventStep suspension/resumption with external payloads
  - PollingStep periodic checks with timeout support

Usage:

  python -m examples.document_approval.example
  python -m examples.document_approval.example --manual-resume
"""

from __future__ import annotations

import argparse
import asyncio
import json

from mongomock_motor import AsyncMongoMockClient

from examples.document_approval.steps import (
    ApprovalStep,
    FetchDocumentConfig,
    FetchDocumentStep,
    ProcessJobConfig,
    ProcessJobStep,
    SendNotificationConfig,
    SendNotificationStep,
)
from workchain import (
    Context,
    DependencyFailurePolicy,
    MongoWorkflowStore,
    Workflow,
    WorkflowRunner,
    WorkflowStatus,
)

# ============================================================================
# Step registry — maps class names to classes for runner deserialization
# ============================================================================

STEP_REGISTRY = {
    "FetchDocumentStep": FetchDocumentStep,
    "ApprovalStep": ApprovalStep,
    "ProcessJobStep": ProcessJobStep,
    "SendNotificationStep": SendNotificationStep,
}


# ============================================================================
# Build Workflow
# ============================================================================


def build_workflow() -> Workflow:
    """
    Construct the document approval workflow DAG.

    DAG structure::

        fetch
          |
        approve  (suspends — waits for external signal)
          |
        process  (polls — checks job status periodically)
          |
        notify
    """
    return (
        Workflow(name="document_approval", version="1.0.0")
        .add(
            step_id="fetch",
            step=FetchDocumentStep(
                config=FetchDocumentConfig(
                    document_id="DOC-12345",
                    source_url="https://api.example.com/documents",
                )
            ),
        )
        .add(
            step_id="approve",
            step=ApprovalStep(),
            depends_on=["fetch"],
        )
        .add(
            step_id="process",
            step=ProcessJobStep(config=ProcessJobConfig(job_type="document_processing", max_checks=3)),
            depends_on=["approve"],
        )
        .add(
            step_id="notify",
            step=SendNotificationStep(config=SendNotificationConfig(recipient_email="user@example.com")),
            depends_on=["process"],
            on_dependency_failure=DependencyFailurePolicy.SKIP,
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
        line = f"  {icon} {step.step_id:<12} ({step.step_type})"
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
            # Nothing claimable — workflow may be suspended or all done
            break
        run = await store.load(str(run.id))
        print_workflow_state(run)
        if run.status in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.SUSPENDED}:
            break
    return run


# ============================================================================
# Example: auto-approve mode
# ============================================================================


async def run_auto_approve() -> None:
    """
    Run the full workflow with automatic approval.

    1. Ticks until ApprovalStep suspends
    2. Immediately resumes with approval payload
    3. Continues ticking through ProcessJobStep (PollingStep) and notification
    """
    print("\n" + "=" * 70)
    print("  DOCUMENT APPROVAL WORKFLOW — AUTO-APPROVE MODE")
    print("=" * 70)

    store = create_store()
    workflow = build_workflow()
    run = workflow.create_run()
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
    print("\n--- Phase 1: Fetch → Approve (will suspend) ---")
    run = await tick_until_suspended_or_done(runner, store, run)

    if run.status != WorkflowStatus.SUSPENDED:
        print(f"Expected SUSPENDED, got {run.status.value}")
        return

    # Find the correlation ID for resumption
    approval_step = run.get_step("approve")
    correlation_id = approval_step.resume_correlation_id
    print(f"Workflow suspended. Correlation ID: {correlation_id}")

    # Phase 2: Resume with approval
    print("\n--- Phase 2: Resume with approval ---")
    await runner.resume(
        correlation_id=correlation_id,
        payload={
            "approved": True,
            "approver": "demo@example.com",
            "notes": "Auto-approved for demonstration",
        },
    )
    run = await store.load(str(run.id))
    print_workflow_state(run)

    # Phase 3: ProcessJobStep (PollingStep) needs repeated ticks
    print("--- Phase 3: Process (polling) → Notify ---")

    # Reset lease so runner can claim again
    run.lease_owner = None
    run.lease_expires_at = None
    # The runner already set status to RUNNING/SUSPENDED — find_claimable needs it claimable
    # After resume, the run has pending steps so it should be RUNNING
    run = await tick_until_suspended_or_done(runner, store, run)

    # If suspended for polling, keep ticking with poll intervals
    while run.status == WorkflowStatus.SUSPENDED:
        # Simulate time passing for poll checks
        poll_step = run.get_step("process")
        if poll_step and poll_step.next_poll_at:
            poll_step.next_poll_at = poll_step.next_poll_at.__class__.now(tz=None)
        run.lease_owner = None
        run.lease_expires_at = None
        # Drive poll checks directly
        context = Context.from_dict(run.context)
        await runner._check_due_polls(run, context)
        run.context = context.to_dict()
        await store.save_with_version(run)
        print_workflow_state(run)
        if run.status != WorkflowStatus.SUSPENDED:
            break
        run = await tick_until_suspended_or_done(runner, store, run)

    print(f"\nWorkflow finished: {run.status.value.upper()}")


# ============================================================================
# Example: manual-resume mode
# ============================================================================


async def run_manual_resume() -> None:
    """
    Run the workflow with a simulated manual resume.

    Shows how an external system would resume a suspended EventStep.
    """
    print("\n" + "=" * 70)
    print("  DOCUMENT APPROVAL WORKFLOW — MANUAL RESUME MODE")
    print("=" * 70)

    store = create_store()
    workflow = build_workflow()
    run = workflow.create_run()
    await store.save(run)
    print(f"\nCreated WorkflowRun: {run.id}")

    runner = WorkflowRunner(
        store=store,
        registry=STEP_REGISTRY,
        workflow=workflow,
        instance_id="demo-runner",
        poll_interval_seconds=0.1,
    )

    # Execute until suspended
    print("\n--- Executing: Fetch → Approve (will suspend) ---")
    run = await tick_until_suspended_or_done(runner, store, run)

    approval_step = run.get_step("approve")
    correlation_id = approval_step.resume_correlation_id

    print("\nWorkflow is SUSPENDED waiting for approval.")
    print(f"Correlation ID: {correlation_id}")
    print("\nIn a real system, an external actor would call:")
    print("  await runner.resume(")
    print(f'      correlation_id="{correlation_id}",')
    print('      payload={"approved": True, "approver": "jane@co.com"}')
    print("  )")
    print("\nAuto-resuming in 2 seconds...")
    await asyncio.sleep(2)

    # Resume
    print("\n--- Resuming with approval ---")
    await runner.resume(
        correlation_id=correlation_id,
        payload={
            "approved": True,
            "approver": "jane@example.com",
            "notes": "Approved via manual resume demo",
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
        description="Document Approval Workflow Example",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--manual-resume",
        action="store_true",
        help="Suspend and show manual resume instructions (default: auto-approve)",
    )

    args = parser.parse_args()

    if args.manual_resume:
        await run_manual_resume()
    else:
        await run_auto_approve()


if __name__ == "__main__":
    asyncio.run(main())
