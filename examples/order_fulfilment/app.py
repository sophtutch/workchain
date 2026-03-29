"""FastAPI application for the order fulfilment workflow.

Demonstrates workchain integration with a real web framework:

- POST /orders          -- Create an order and start the workflow
- GET  /orders/{id}     -- View workflow state and step progress
- POST /webhooks/payment -- Payment gateway callback (resumes EventStep)
- GET  /health          -- Health check

The WorkflowRunner starts as a background task during application lifespan
and polls for work on an interval.

Usage::

    uvicorn examples.order_fulfilment.app:app --reload

"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from mongomock_motor import AsyncMongoMockClient

from examples.order_fulfilment import config as app_config
from examples.order_fulfilment.logging_config import configure_logging
from examples.order_fulfilment.routes import register_routes
from examples.order_fulfilment.workflow import STEP_REGISTRY, build_workflow
from workchain import MongoWorkflowStore, WorkflowRunner

# ============================================================================
# Logging
# ============================================================================

configure_logging()
logger = logging.getLogger("workchain.example.app")

# ============================================================================
# Application state (module-level singletons, set during lifespan)
# ============================================================================

_store: MongoWorkflowStore | None = None
_runner: WorkflowRunner | None = None
_workflow = build_workflow()


def _get_store() -> MongoWorkflowStore:
    assert _store is not None  # noqa: S101
    return _store


def _get_runner() -> WorkflowRunner:
    assert _runner is not None  # noqa: S101
    return _runner


def _get_workflow():
    return _workflow


# ============================================================================
# Lifespan -- start runner as a background task
# ============================================================================


@asynccontextmanager
async def lifespan(_app: Any):
    """
    FastAPI lifespan context manager.

    1. Creates an in-memory MongoDB store (mongomock)
    2. Ensures indexes
    3. Starts the WorkflowRunner as a background task
    """
    global _store, _runner  # noqa: PLW0603

    client = AsyncMongoMockClient()
    owner_id = f"fastapi-{uuid.uuid4().hex[:6]}"

    _store = MongoWorkflowStore(
        client=client,
        database=app_config.DATABASE_NAME,
        owner_id=owner_id,
        lease_ttl_seconds=app_config.LEASE_TTL_SECONDS,
    )
    await _store.ensure_indexes()

    _runner = WorkflowRunner(
        store=_store,
        registry=STEP_REGISTRY,
        workflow=_workflow,
        instance_id=owner_id,
        poll_interval_seconds=app_config.POLL_INTERVAL_SECONDS,
    )

    runner_task = asyncio.create_task(_run_runner(_runner))

    logger.info("=" * 60)
    logger.info("  Order Fulfilment API ready (mongomock in-memory store)")
    logger.info("  POST /orders            -- Create an order")
    logger.info("  GET  /orders/{run_id}   -- Check order status")
    logger.info("  POST /webhooks/payment  -- Payment gateway callback")
    logger.info("=" * 60)

    yield

    # Shutdown
    logger.info("Shutting down runner...")
    await _runner.stop()
    runner_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await runner_task
    logger.info("Runner stopped.")


async def _run_runner(runner: WorkflowRunner) -> None:
    """Run the workflow runner, logging any unexpected errors."""
    try:
        await runner.start()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("Runner crashed unexpectedly")


# ============================================================================
# FastAPI app
# ============================================================================

try:
    from fastapi import FastAPI
except ImportError as exc:
    raise ImportError("FastAPI is required for this example. Install with: pip install fastapi uvicorn") from exc

app = FastAPI(
    title="Order Fulfilment Workflow",
    description="Demonstrates workchain with FastAPI -- EventStep, PollingStep, and parallel DAG execution",
    version="1.0.0",
    lifespan=lifespan,
)

register_routes(app, _get_store, _get_runner, _get_workflow)
