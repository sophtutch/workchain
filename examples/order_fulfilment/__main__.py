"""Self-contained demo that drives the full order fulfilment workflow.

Runs without FastAPI — exercises the entire flow end-to-end:

1. Creates a workflow with a sample order
2. Engine processes validate -> reserve -> charge (async, polls) ->
   ship (async, polls) -> confirm
3. Workflow completes

Usage:

    python -m examples.order_fulfilment
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from mongomock_motor import AsyncMongoMockClient

from examples.order_fulfilment.logging_config import configure_logging
from examples.order_fulfilment.workflow import build_workflow
from workchain import MongoWorkflowStore, WorkflowEngine

# ============================================================================
# Logging
# ============================================================================

configure_logging()
logger = logging.getLogger("workchain.example.demo")


# ============================================================================
# Main demo
# ============================================================================


async def run_demo() -> None:
    print(f"\n{'=' * 64}")
    print("  ORDER FULFILMENT WORKFLOW -- FULL DEMO")
    print(f"{'=' * 64}")

    # In-memory MongoDB
    client = AsyncMongoMockClient()
    db = client["workchain_demo"]
    store = MongoWorkflowStore(db, lock_ttl_seconds=30)

    # Create the order
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

    wf = build_workflow(order_data)
    await store.insert(wf)
    logger.info("Created order %s -- workflow %s", order_data["order_id"], wf.id)

    # Start the engine
    engine = WorkflowEngine(
        store,
        claim_interval=1.0,
        heartbeat_interval=5.0,
        max_concurrent=3,
    )
    await engine.start()

    # Let it run — charge (2 polls) + ship (3 polls) need multiple
    # claim-poll-release cycles
    await asyncio.sleep(30)
    await engine.stop()

    # Final state
    final = await store.get(wf.id)
    print(f"\n{'=' * 64}")
    print(f"  Final status: {final.status.value}")
    print(f"{'=' * 64}")
    for s in final.steps:
        extra = f"attempts={s.attempt}"
        if s.is_async:
            extra += f", polls={s.poll_count}"
            if s.last_poll_progress is not None:
                extra += f", progress={s.last_poll_progress:.0%}"
        print(f"  {s.name}: {s.status.value} ({extra})")

    if final.status.value == "completed":
        print(f"\n  Order {order_data['order_id']} fulfilled successfully!")
    else:
        print(f"\n  Order ended with status: {final.status.value}")
    print()


if __name__ == "__main__":
    asyncio.run(run_demo())
