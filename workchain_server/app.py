"""Workchain Server — standalone FastAPI application.

Start with::

    pip install workchain[server]
    uvicorn workchain_server.app:app --host 0.0.0.0 --port 8000

Or via hatch::

    hatch run server:serve
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from motor.motor_asyncio import AsyncIOMotorClient

from workchain import MongoAuditLogger, MongoWorkflowStore, WorkflowEngine
from workchain.contrib.fastapi import create_workchain_router
from workchain_server.config import Settings
from workchain_server.plugins import discover_plugins
from workchain_server.ui import create_ui_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    force=True,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration and plugin discovery
# ---------------------------------------------------------------------------

settings = Settings()
instance_id = settings.get_instance_id()

loaded_plugins = discover_plugins(settings.workchain_plugins)
if loaded_plugins:
    logger.info("Loaded %d plugin(s): %s", len(loaded_plugins), ", ".join(loaded_plugins))

# ---------------------------------------------------------------------------
# MongoDB (Motor connects lazily — no I/O at module level)
# ---------------------------------------------------------------------------

_client = AsyncIOMotorClient(settings.mongo_uri)
_db = _client[settings.mongo_database]
audit_logger = MongoAuditLogger(_db)
store = MongoWorkflowStore(
    _db,
    audit_logger=audit_logger,
    instance_id=instance_id,
)

# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Start engine on startup, stop and close connections on shutdown."""
    await store.ensure_indexes()
    await audit_logger.ensure_indexes()
    logger.info(
        "Connecting to MongoDB: %s / %s", settings.mongo_uri, settings.mongo_database,
    )

    async with WorkflowEngine(
        store,
        instance_id=instance_id,
        claim_interval=settings.engine_claim_interval,
        heartbeat_interval=settings.engine_heartbeat_interval,
        sweep_interval=settings.engine_sweep_interval,
        step_stuck_seconds=settings.engine_step_stuck_seconds,
        max_concurrent=settings.engine_max_concurrent,
        context={"db": _db, "store": store, "audit_logger": audit_logger},
    ) as engine:
        application.state.engine = engine
        application.state.store = store
        application.state.audit_logger = audit_logger
        yield

    _client.close()
    logger.info("Workchain server shut down")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title=settings.server_title, lifespan=lifespan)

# Workflow CRUD + report endpoints
app.include_router(
    create_workchain_router(store, audit_logger),
    prefix="/api/v1/workflows",
    tags=["workflows"],
)

# Management dashboard
app.include_router(create_ui_router(settings.server_title, instance_id))


# Static assets (favicon etc.)
_static_dir = Path(__file__).resolve().parent.parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/healthz", tags=["ops"])
async def healthz():
    """Health check — pings MongoDB."""
    await _db.command("ping")
    return {"status": "ok", "instance_id": instance_id}
