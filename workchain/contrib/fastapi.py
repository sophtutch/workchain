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
from typing import TYPE_CHECKING, Any

try:
    from fastapi import APIRouter, HTTPException
    from fastapi.responses import HTMLResponse
except ImportError as e:
    raise ImportError(
        "FastAPI is required for workchain.contrib.fastapi. "
        "Install it with: pip install workchain[fastapi]"
    ) from e

from workchain.audit_report import generate_audit_report
from workchain.models import WorkflowStatus

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
    async def list_workflows(
        status: str | None = None,
        search: str | None = None,
        limit: int = 25,
        skip: int = 0,
    ) -> dict[str, Any]:
        """List workflows with optional filters and pagination.

        Args:
            status: Filter by workflow status (pending, running, etc.).
            search: Case-insensitive substring match on workflow name.
            limit: Page size (max 100).
            skip: Number of records to skip.
        """
        limit = min(limit, 100)
        ws = WorkflowStatus(status) if status else None
        wf_list = await store.list_workflows(
            status=ws, search=search, limit=limit, skip=skip,
        )
        total = await store.count_workflows(status=ws, search=search)

        items = []
        for wf in wf_list:
            total_steps = len(wf.steps)
            completed_steps = sum(1 for s in wf.steps if s.status.value == "completed")
            items.append({
                "id": wf.id,
                "name": wf.name,
                "status": wf.status.value,
                "progress": f"{completed_steps}/{total_steps}",
                "total_steps": total_steps,
                "completed_steps": completed_steps,
                "created_at": str(wf.created_at),
                "updated_at": str(wf.updated_at),
            })
        return {"items": items, "total": total}

    @router.get("/stats")
    async def workflow_stats() -> dict[str, int]:
        """Return workflow counts grouped by status."""
        return await store.count_by_status()

    @router.get("/analytics")
    async def workflow_analytics() -> dict[str, Any]:
        """Return aggregate analytics for all workflows."""
        return await store.get_analytics()

    @router.get("/activity")
    async def workflow_activity(
        limit: int = 10, status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return recently updated workflows.

        Args:
            limit: Maximum number of items to return (max 50).
            status: Optional status filter (e.g. ``"failed"``).
        """
        return await store.recent_activity(
            limit=min(limit, 50), status=status,
        )

    @router.get("/{workflow_id}/detail")
    async def get_workflow_detail(workflow_id: str) -> dict[str, Any]:
        """Return full workflow detail including steps and audit events.

        Provides everything needed for a rich workflow detail view:
        workflow metadata, full step state, audit event history, and
        dependency graph structure.
        """
        wf = await store.get(workflow_id)
        if wf is None:
            raise HTTPException(status_code=404, detail="Workflow not found")

        events = await audit_logger.get_events(workflow_id)

        # Build step details
        steps: list[dict[str, Any]] = []
        for s in wf.steps:
            step_info: dict[str, Any] = {
                "name": s.name,
                "handler": s.handler,
                "status": s.status.value,
                "attempt": s.attempt,
                "is_async": s.is_async,
                "depends_on": s.depends_on or [],
                "step_timeout": s.step_timeout,
                "config": s.config.model_dump(
                    exclude_none=True,
                ) if s.config else None,
                "result": s.result.model_dump(
                    exclude_none=True,
                ) if s.result else None,
                "retry_policy": {
                    "max_attempts": s.retry_policy.max_attempts,
                    "wait_seconds": s.retry_policy.wait_seconds,
                    "wait_multiplier": s.retry_policy.wait_multiplier,
                    "wait_max": s.retry_policy.wait_max,
                },
                "poll_policy": {
                    "interval": s.poll_policy.interval,
                    "backoff_multiplier": s.poll_policy.backoff_multiplier,
                    "max_interval": s.poll_policy.max_interval,
                    "timeout": s.poll_policy.timeout,
                    "max_polls": s.poll_policy.max_polls,
                } if s.poll_policy else None,
                "poll_count": s.poll_count,
                "last_poll_progress": s.last_poll_progress,
                "last_poll_message": s.last_poll_message,
                "locked_by": s.locked_by,
                "fence_token": s.fence_token,
            }
            steps.append(step_info)

        # Build dependency graph tiers
        deps: dict[str, list[str]] = {}
        for s in wf.steps:
            deps[s.name] = s.depends_on or []

        # Compute DAG depth for each step
        depth_cache: dict[str, int] = {}

        def _depth(name: str) -> int:
            if name in depth_cache:
                return depth_cache[name]
            parents = deps.get(name, [])
            depth_cache[name] = (
                max(_depth(p) for p in parents) + 1 if parents else 0
            )
            return depth_cache[name]

        for s in wf.steps:
            _depth(s.name)

        tier_map: dict[int, list[str]] = {}
        for s in wf.steps:
            d = depth_cache.get(s.name, 0)
            tier_map.setdefault(d, []).append(s.name)
        tiers = [tier_map[d] for d in sorted(tier_map.keys())]

        # Serialize events
        event_list: list[dict[str, Any]] = []
        for e in events:
            event_list.append({
                "event_type": e.event_type.value,
                "timestamp": str(e.timestamp),
                "sequence": e.sequence,
                "step_name": e.step_name,
                "step_status": e.step_status,
                "step_status_before": e.step_status_before,
                "instance_id": e.instance_id,
                "attempt": e.attempt,
                "error": e.error,
                "error_traceback": e.error_traceback,
                "poll_count": e.poll_count,
                "poll_progress": e.poll_progress,
                "poll_message": e.poll_message,
                "recovery_action": e.recovery_action,
                "result_summary": e.result_summary,
            })

        return {
            "workflow": {
                "id": wf.id,
                "name": wf.name,
                "status": wf.status.value,
                "created_at": str(wf.created_at),
                "updated_at": str(wf.updated_at),
            },
            "steps": steps,
            "events": event_list,
            "graph": {
                "dependencies": deps,
                "tiers": tiers,
            },
        }

    @router.get("/{workflow_id}")
    async def get_workflow(workflow_id: str) -> dict[str, Any]:
        """Get the current state of a workflow."""
        wf = await store.get(workflow_id)
        if wf is None:
            raise HTTPException(status_code=404, detail="Workflow not found")

        steps: list[dict[str, Any]] = []
        for s in wf.steps:
            step_info: dict[str, Any] = {
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
    async def cancel_workflow(workflow_id: str) -> dict[str, str]:
        """Cancel a running or pending workflow."""
        wf = await store.cancel_workflow(workflow_id)
        if wf is None:
            raise HTTPException(status_code=404, detail="Workflow not found or already terminal")
        return {"workflow_id": wf.id, "status": wf.status.value}

    @router.post("/{workflow_id}/steps/{step_name}/retry")
    async def retry_step(workflow_id: str, step_name: str) -> dict[str, str]:
        """Manually retry a failed step.

        Resets the step to PENDING and sets the workflow back to RUNNING
        so the engine can re-execute it.
        """
        wf = await store.retry_step_by_name(workflow_id, step_name)
        if wf is None:
            raise HTTPException(
                status_code=409,
                detail=f"Step '{step_name}' is not in a retryable state",
            )
        step = wf.step_by_name(step_name)
        return {
            "workflow_id": wf.id,
            "step_name": step_name,
            "step_status": step.status.value if step else "unknown",
            "workflow_status": wf.status.value,
        }

    @router.get("/{workflow_id}/report", response_class=HTMLResponse)
    async def get_workflow_report(workflow_id: str) -> HTMLResponse:
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
