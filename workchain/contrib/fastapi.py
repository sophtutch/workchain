"""Optional FastAPI integration for workchain.

Provides a ready-to-use router with workflow management endpoints.
Requires the ``fastapi`` extra::

    pip install workchain[fastapi]

Usage::

    from workchain.contrib.fastapi import create_workchain_router

    router = create_workchain_router(store, audit_logger)
    app.include_router(router, prefix="/workflows")
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

try:
    from fastapi import APIRouter, HTTPException
    from fastapi.responses import HTMLResponse
except ImportError as e:
    raise ImportError(
        "FastAPI is required for workchain.contrib.fastapi. "
        "Install it with: pip install workchain[fastapi]"
    ) from e

from workchain.audit_report import generate_audit_report

if TYPE_CHECKING:
    from workchain.audit import AuditLogger
    from workchain.store import MongoWorkflowStore


def create_workchain_router(
    store: MongoWorkflowStore,
    audit_logger: AuditLogger,
) -> APIRouter:
    """Create a FastAPI router with standard workflow management endpoints.

    Args:
        store: The workflow store for persistence and queries.
        audit_logger: The audit logger for retrieving execution events.

    Returns:
        An ``APIRouter`` with the following endpoints:

        - ``GET /`` — list all workflows with status and progress
        - ``GET /stats`` — workflow counts grouped by status
        - ``GET /{workflow_id}`` — full workflow state with step details
        - ``GET /{workflow_id}/report`` — HTML audit execution report
        - ``POST /{workflow_id}/cancel`` — cancel a running workflow
    """
    router = APIRouter()

    @router.get("")
    async def list_workflows():
        """List all workflows with their current status."""
        wf_list = await store.list_workflows()

        workflows = []
        for wf in wf_list:
            total_steps = len(wf.steps)
            completed_steps = sum(1 for s in wf.steps if s.status.value == "completed")
            workflows.append({
                "id": wf.id,
                "name": wf.name,
                "status": wf.status.value,
                "progress": f"{completed_steps}/{total_steps}",
                "total_steps": total_steps,
                "completed_steps": completed_steps,
                "created_at": str(wf.created_at),
            })
        return workflows

    @router.get("/stats")
    async def workflow_stats():
        """Return workflow counts grouped by status."""
        return await store.count_by_status()

    @router.get("/{workflow_id}")
    async def get_workflow(workflow_id: str):
        """Get the current state of a workflow."""
        wf = await store.get(workflow_id)
        if wf is None:
            raise HTTPException(status_code=404, detail="Workflow not found")

        steps = []
        for s in wf.steps:
            step_info = {
                "name": s.name,
                "handler": s.handler,
                "status": s.status.value,
                "attempt": s.attempt,
                "is_async": s.is_async,
            }
            if s.result:
                step_info["result"] = s.result.model_dump(exclude_none=True)
            steps.append(step_info)

        return {
            "id": wf.id,
            "name": wf.name,
            "status": wf.status.value,
            "steps": steps,
        }

    @router.post("/{workflow_id}/cancel")
    async def cancel_workflow(workflow_id: str):
        """Cancel a running or pending workflow."""
        wf = await store.cancel_workflow(workflow_id)
        if wf is None:
            raise HTTPException(status_code=404, detail="Workflow not found or already terminal")
        return {"workflow_id": wf.id, "status": wf.status.value}

    @router.get("/{workflow_id}/report", response_class=HTMLResponse)
    async def get_workflow_report(workflow_id: str):
        """Generate an HTML audit report for a workflow."""
        wf = await store.get(workflow_id)
        if wf is None:
            raise HTTPException(status_code=404, detail="Workflow not found")

        events = await audit_logger.get_events(workflow_id)
        if not events:
            return HTMLResponse(
                "<html><body><p>No audit events yet. "
                "The workflow may not have started.</p></body></html>"
            )

        # Allow fire-and-forget audit writes to land
        await asyncio.sleep(0.1)
        events = await audit_logger.get_events(workflow_id)

        return HTMLResponse(generate_audit_report(events, workflow=wf))

    return router
