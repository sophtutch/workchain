"""FastAPI application for the order fulfilment workflow.

Demonstrates workchain integration with a web framework:

- POST /orders          -- Create an order and start its workflow
- GET  /orders/{id}     -- View workflow state and step progress
- GET  /health          -- Health check

The WorkflowEngine runs as a background task during application lifespan,
automatically discovering and processing workflows.

Usage:

    uvicorn examples.order_fulfilment.app:app --reload
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from mongomock_motor import AsyncMongoMockClient

from examples.order_fulfilment.logging_config import configure_logging
from examples.order_fulfilment.workflow import build_workflow
from workchain import MongoWorkflowStore, WorkflowEngine

# ============================================================================
# Logging
# ============================================================================

configure_logging()
logger = logging.getLogger("workchain.example.app")

# ============================================================================
# Application state (set during lifespan)
# ============================================================================

_store: MongoWorkflowStore | None = None
_engine: WorkflowEngine | None = None


def _get_store() -> MongoWorkflowStore:
    assert _store is not None  # noqa: S101
    return _store


# ============================================================================
# Lifespan
# ============================================================================


@asynccontextmanager
async def lifespan(_app: Any):
    """Start the WorkflowEngine as a background task."""
    global _store, _engine  # noqa: PLW0603

    client = AsyncMongoMockClient()
    db = client["workchain_orders"]

    _store = MongoWorkflowStore(db, lock_ttl_seconds=30)

    _engine = WorkflowEngine(
        _store,
        instance_id=f"fastapi-{uuid.uuid4().hex[:6]}",
        claim_interval=2.0,
        heartbeat_interval=5.0,
        max_concurrent=5,
    )
    await _engine.start()

    logger.info("=" * 60)
    logger.info("  Order Fulfilment API ready (mongomock in-memory store)")
    logger.info("  POST /orders            -- Create an order")
    logger.info("  GET  /orders/{wf_id}    -- Check order status")
    logger.info("=" * 60)

    yield

    logger.info("Shutting down engine...")
    await _engine.stop()
    logger.info("Engine stopped.")


# ============================================================================
# FastAPI app
# ============================================================================

try:
    from fastapi import FastAPI, HTTPException
except ImportError as exc:
    raise ImportError(
        "FastAPI is required for this example. Install with: pip install 'workchain[examples]'"
    ) from exc

app = FastAPI(
    title="Order Fulfilment Workflow",
    description="Demonstrates workchain with FastAPI — async step polling and sequential execution",
    version="2.0.0",
    lifespan=lifespan,
)


@app.post("/orders", status_code=201)
async def create_order(customer_email: str, shipping_region: str = "US") -> dict:
    """Create an order and start the fulfilment workflow."""
    store = _get_store()
    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"

    order_data = {
        "order_id": order_id,
        "customer_email": customer_email,
        "shipping_region": shipping_region,
        "items": [
            {"sku": "WIDGET-A", "quantity": 2, "price": 29.99},
            {"sku": "GADGET-B", "quantity": 1, "price": 49.99},
        ],
        "created_at": datetime.now(UTC).isoformat(),
    }

    wf = build_workflow(order_data)
    await store.insert(wf)
    logger.info("Order %s created -- workflow %s", order_id, wf.id)

    return {
        "order_id": order_id,
        "workflow_id": wf.id,
        "status": wf.status.value,
        "message": "Order created. Workflow will begin processing shortly.",
    }


@app.get("/orders/{wf_id}")
async def get_order_status(wf_id: str) -> dict:
    """Get the current state of an order's workflow."""
    store = _get_store()
    wf = await store.get(wf_id)
    if wf is None:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {wf_id}")

    steps = []
    for s in wf.steps:
        step_info = {
            "name": s.name,
            "status": s.status.value,
            "attempt": s.attempt,
        }
        if s.is_async:
            step_info["polls"] = s.poll_count
            if s.last_poll_progress is not None:
                step_info["progress"] = s.last_poll_progress
            if s.last_poll_message:
                step_info["message"] = s.last_poll_message
        if s.result and s.result.error:
            step_info["error"] = s.result.error
        steps.append(step_info)

    return {
        "workflow_id": wf.id,
        "name": wf.name,
        "status": wf.status.value,
        "current_step": wf.current_step_index,
        "steps": steps,
    }


@app.get("/health")
async def health_check() -> dict:
    return {"status": "healthy"}
