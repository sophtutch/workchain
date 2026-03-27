"""Self-contained demo that drives the full order fulfilment workflow.

Runs without FastAPI or uvicorn -- exercises the entire flow end-to-end:

1. Creates a workflow run with a sample order
2. Runner processes validate -> reserve (parallel with charge)
3. ChargePaymentStep (EventStep) suspends -- waiting for webhook
4. Script simulates the webhook by calling runner.resume()
5. ShipOrderStep (PollingStep) polls until tracking number assigned
6. SendConfirmationStep sends the final email
7. Workflow completes

Usage::

    python -m examples.order_fulfilment.demo

"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

from mongomock_motor import AsyncMongoMockClient

from examples.order_fulfilment.logging_config import configure_logging
from examples.order_fulfilment.workflow import STEP_REGISTRY, build_workflow
from workchain import (
    MongoWorkflowStore,
    WorkflowRunner,
    WorkflowStatus,
)

# ============================================================================
# Logging
# ============================================================================

configure_logging()
logger = logging.getLogger("workchain.example.demo")


# ============================================================================
# Pretty-print helpers
# ============================================================================

STATUS_ICONS = {
    "pending": "\033[90m[ ]\033[0m",
    "running": "\033[33m[~]\033[0m",
    "completed": "\033[32m[+]\033[0m",
    "failed": "\033[31m[!]\033[0m",
    "suspended": "\033[35m[.]\033[0m",
    "awaiting_poll": "\033[34m[?]\033[0m",
    "skipped": "\033[90m[-]\033[0m",
}


def print_workflow_state(run) -> None:
    """Pretty-print the current state of a WorkflowRun."""
    border = "-" * 62
    print(f"\n  +{border}+")
    print(
        f"  |  {run.workflow_name} v{run.workflow_version}  |  status: {run.status.value:<12} |  v{run.doc_version:<3} |"
    )
    print(f"  +{border}+")
    for step in run.steps:
        icon = STATUS_ICONS.get(step.status.value, "[?]")
        line = f"  |  {icon} {step.step_id:<16} ({step.step_type})"
        print(f"{line:<65}|")
        if step.output:
            out_str = json.dumps(step.output)
            if len(out_str) > 55:
                out_str = out_str[:52] + "..."
            print(f"  |       output: {out_str:<46}|")
        if step.error:
            print(f"  |       error: {step.error:<47}|")
        if step.resume_correlation_id:
            cid = step.resume_correlation_id
            if len(cid) > 44:
                cid = cid[:41] + "..."
            print(f"  |       correlation_id: {cid:<39}|")
        if step.next_poll_at:
            print(f"  |       next_poll_at: {step.next_poll_at.isoformat():<41}|")
    print(f"  +{border}+")


def print_banner(text: str) -> None:
    """Print a section banner."""
    print(f"\n\033[1m{'=' * 64}\033[0m")
    print(f"\033[1m  {text}\033[0m")
    print(f"\033[1m{'=' * 64}\033[0m")


# ============================================================================
# Store + runner helpers
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


async def tick_until_stable(runner, store, run):
    """Tick the runner until the workflow suspends, completes, or fails."""
    for _ in range(50):
        processed = await runner.tick()
        if not processed:
            break
        run = await store.load(str(run.id))
        if run.status in {
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.SUSPENDED,
        }:
            break
    return run


# ============================================================================
# Main demo
# ============================================================================


async def run_demo() -> None:
    print_banner("ORDER FULFILMENT WORKFLOW -- FULL DEMO")

    store = create_store()
    workflow = build_workflow()

    # -- Create the order ---------------------------------------------------
    order_data = {
        "order_id": "ORD-DEMO-001",
        "customer_email": "alice@example.com",
        "shipping_region": "US",
        "items": [
            {"sku": "WIDGET-A", "quantity": 2, "price": 29.99},
            {"sku": "GADGET-B", "quantity": 1, "price": 49.99},
        ],
        "created_at": datetime.now(UTC).isoformat(),
    }

    run = workflow.create_run()
    run.context = {"order": order_data}
    await store.save(run)
    logger.info("Created order %s -- run %s", order_data["order_id"], run.id)

    runner = WorkflowRunner(
        store=store,
        registry=STEP_REGISTRY,
        workflow=workflow,
        instance_id="demo-runner",
        poll_interval_seconds=0.1,
    )

    # -- Phase 1: validate -> reserve + charge (charge suspends) ------------
    print_banner("Phase 1: Validate -> Reserve + Charge (will suspend)")
    print("  The runner will execute validate, then reserve and charge.")
    print("  ChargePaymentStep is an EventStep -- it will SUSPEND the workflow.")
    print()

    run = await tick_until_stable(runner, store, run)
    print_workflow_state(run)

    if run.status != WorkflowStatus.SUSPENDED:
        logger.error("Expected SUSPENDED, got %s", run.status.value)
        return

    # Find the correlation_id for the payment step
    charge_step = run.get_step("charge")
    correlation_id = charge_step.resume_correlation_id
    logger.info("Workflow suspended -- awaiting payment webhook")
    logger.info("Correlation ID: %s", correlation_id)

    # -- Phase 2: Simulate payment webhook ----------------------------------
    print_banner("Phase 2: Payment Webhook (resume EventStep)")
    print("  Simulating a payment gateway callback...")
    print(f"  correlation_id: {correlation_id}")
    print()

    await asyncio.sleep(1.5)  # dramatic pause to show suspension

    logger.info("Calling runner.resume() with payment success payload...")
    await runner.resume(
        correlation_id=correlation_id,
        payload={
            "success": True,
            "charge_id": "ch_DEMO_12345",
            "provider_ref": "pi_stripe_abc",
        },
    )

    run = await store.load(str(run.id))
    print_workflow_state(run)

    # -- Phase 3: ShipOrderStep (PollingStep) polls for tracking ------------
    print_banner("Phase 3: Ship Order (PollingStep -- polls for tracking)")
    print("  ShipOrderStep submits to the carrier and polls for a tracking number.")
    print("  Each tick drives one poll cycle via runner.tick().")
    print()

    # The ship step should now be AWAITING_POLL after resume triggered it.
    # We need to tick through poll cycles.
    for _cycle in range(10):
        ship_step = run.get_step("ship")
        if ship_step and ship_step.status not in {"awaiting_poll", "running"}:
            # Check using enum
            from workchain import StepStatus

            if ship_step.status not in {StepStatus.AWAITING_POLL, StepStatus.RUNNING}:
                break

        # Make the poll due by setting next_poll_at to the past
        if ship_step and ship_step.next_poll_at:
            ship_step.next_poll_at = datetime.now(UTC) - timedelta(seconds=1)

        # Release lease so tick() can re-acquire
        run.lease_owner = None
        run.lease_expires_at = None

        await asyncio.sleep(0.8)  # simulate passage of time between polls

        processed = await runner.tick()
        if not processed:
            # tick didn't find work -- save our manual changes and retry
            await store.save_with_version(run)
            processed = await runner.tick()

        run = await store.load(str(run.id))
        print_workflow_state(run)

        if run.status in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED}:
            break

    # -- Final state --------------------------------------------------------
    print_banner("FINAL RESULT")
    run = await store.load(str(run.id))
    print_workflow_state(run)

    if run.status == WorkflowStatus.COMPLETED:
        print("\n  \033[32m[OK] Workflow completed successfully!\033[0m")
        confirm_output = run.get_step("confirm")
        if confirm_output and confirm_output.output:
            print(f"    Email sent to: {confirm_output.output.get('recipient')}")
            print(f"    Tracking:      {confirm_output.output.get('tracking_number')}")
    else:
        print(f"\n  \033[31m[FAIL] Workflow ended with status: {run.status.value}\033[0m")


# ============================================================================
# Entry point
# ============================================================================


if __name__ == "__main__":
    asyncio.run(run_demo())
