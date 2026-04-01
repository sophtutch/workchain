"""
Document Approval Workflow — End-to-end example

Demonstrates a sequential workflow with sync and async steps:

  1. fetch_document   (@step)       — Fetches document from remote API
  2. request_approval (@async_step) — Submits approval request, polls until granted
  3. process_document (@step)       — Processes the approved document
  4. send_notification (@step)      — Sends result notification via email

Usage:

  python -m examples.document_approval.example
"""

from __future__ import annotations

import asyncio
import logging

from mongomock_motor import AsyncMongoMockClient

# Import step handlers to trigger decorator registration
import examples.document_approval.steps as _steps  # noqa: F401
from workchain import (
    MongoWorkflowStore,
    PollPolicy,
    RetryPolicy,
    Step,
    StepConfig,
    Workflow,
    WorkflowEngine,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


# ============================================================================
# Build Workflow
# ============================================================================


def build_workflow(document_id: str, recipient_email: str) -> Workflow:
    """Construct the document approval workflow."""
    return Workflow(
        name="document_approval",
        steps=[
            Step(
                name="fetch_document",
                handler="fetch_document",
                config=StepConfig(data={
                    "document_id": document_id,
                    "source_url": "https://api.example.com/documents",
                }),
            ),
            Step(
                name="request_approval",
                handler="request_approval",
                is_async=True,
                completeness_check="examples.document_approval.steps.check_approval_status",
                poll_policy=PollPolicy(
                    interval=2.0,
                    backoff_multiplier=1.0,
                    timeout=60.0,
                ),
            ),
            Step(
                name="process_document",
                handler="process_document",
                retry_policy=RetryPolicy(max_attempts=3),
            ),
            Step(
                name="send_notification",
                handler="send_notification",
                config=StepConfig(data={"recipient_email": recipient_email}),
            ),
        ],
    )


# ============================================================================
# Main
# ============================================================================


async def main() -> None:
    print("\n" + "=" * 60)
    print("  DOCUMENT APPROVAL WORKFLOW")
    print("=" * 60)

    # In-memory MongoDB (no real DB needed)
    client = AsyncMongoMockClient()
    db = client["workchain_demo"]
    store = MongoWorkflowStore(db, lock_ttl_seconds=30)

    # Build and insert workflow
    wf = build_workflow(
        document_id="DOC-12345",
        recipient_email="alice@example.com",
    )
    await store.insert(wf)
    print(f"\nSubmitted workflow: {wf.id}")

    # Start the engine — it will discover and run the workflow
    engine = WorkflowEngine(
        store,
        claim_interval=1.0,
        heartbeat_interval=5.0,
        max_concurrent=3,
    )
    await engine.start()

    # Let it run — the async approval step needs multiple claim-poll-release
    # cycles (claim_interval=1s, poll_interval=2s, completes after 3 polls)
    await asyncio.sleep(20)
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
