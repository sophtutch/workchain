"""FastAPI router for the workflow designer UI.

Exposes three groups of endpoints under ``/api/v1`` (mounted without a
further prefix by :mod:`workchain_server.app`):

- ``GET /handlers`` — list registered step handlers with JSON schemas
- ``POST /workflows`` — create a runnable :class:`~workchain.models.Workflow`
  from a designer draft (server-derives ``config_type`` from the handler
  signature, so clients never send dotted paths)
- ``/templates*`` — full CRUD over :class:`~workchain.templates.WorkflowTemplate`,
  plus ``POST /templates/{id}/launch`` for template instantiation

The designer router is deliberately kept out of :mod:`workchain.contrib.fastapi`
— that module is a read-only public surface for library users who only want
monitoring; adding workflow creation + template CRUD would widen its contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, ValidationError

from workchain.introspection import HandlerDescriptor, describe_handler, list_handlers
from workchain.models import Step, Workflow
from workchain.templates import (
    StepTemplate,
    WorkflowTemplate,
    _import_config_class,
    instantiate_template,
)

if TYPE_CHECKING:
    from workchain.store import MongoWorkflowStore


# ---------------------------------------------------------------------------
# Request/response DTOs
# ---------------------------------------------------------------------------


class WorkflowDraft(BaseModel):
    """POST body for ``POST /api/v1/workflows``.

    The wire format is :class:`StepTemplate` — the same schema used for
    persistent template storage — so a React Flow graph can be submitted
    either as a one-shot run (this endpoint) or persisted as a reusable
    template (``POST /api/v1/templates``) without a second DTO.
    """

    name: str
    steps: list[StepTemplate] = Field(default_factory=list)


class DraftStepError(BaseModel):
    """Structured per-step validation error returned in a 422 response."""

    step: str
    error: str
    field_errors: list[dict[str, Any]] | None = None


class DraftErrorResponse(BaseModel):
    """422 body for an invalid :class:`WorkflowDraft`."""

    detail: str
    errors: list[DraftStepError]


class TemplateUpdate(BaseModel):
    """PUT body for ``PUT /api/v1/templates/{id}``.

    All fields are optional: only the keys provided are updated.  The
    ``expected_version`` is required and enforces optimistic locking — a
    mismatch returns 409 Conflict.
    """

    expected_version: int
    name: str | None = None
    description: str | None = None
    steps: list[StepTemplate] | None = None


class TemplateLaunchBody(BaseModel):
    """POST body for ``POST /api/v1/templates/{id}/launch``."""

    name_override: str | None = None
    config_overrides: dict[str, dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# Draft → Workflow conversion
# ---------------------------------------------------------------------------


def _build_workflow_from_draft(draft: WorkflowDraft) -> Workflow:
    """Resolve handlers, validate configs, and construct a :class:`Workflow`.

    Collects ALL per-step errors before raising so the designer can surface
    a complete error report in one round trip.

    Raises:
        HTTPException: 422 with a :class:`DraftErrorResponse` body if any
            step fails handler lookup, launchability check, or Pydantic
            config validation.  Workflow-level DAG validation (unique
            names, cycles, unknown deps) is left to the Workflow model's
            own validators and also surfaces as 422.
    """
    errors: list[DraftStepError] = []
    built_steps: list[Step] = []

    for step_draft in draft.steps:
        descriptor = describe_handler(step_draft.handler)
        if descriptor is None:
            errors.append(
                DraftStepError(
                    step=step_draft.name,
                    error=f"Unknown handler {step_draft.handler!r}",
                )
            )
            continue
        if not descriptor.launchable:
            errors.append(
                DraftStepError(
                    step=step_draft.name,
                    error=(
                        f"Handler {step_draft.handler!r} is not launchable: "
                        "it must declare a StepConfig subclass and a "
                        "StepResult subclass in its signature."
                    ),
                )
            )
            continue

        assert descriptor.config_type is not None  # noqa: S101 - launchable guarantees it
        try:
            config_cls = _import_config_class(descriptor.config_type)
            typed_config = config_cls.model_validate(step_draft.config or {})
        except ValidationError as exc:
            errors.append(
                DraftStepError(
                    step=step_draft.name,
                    error=f"Invalid config for handler {step_draft.handler!r}",
                    field_errors=[
                        {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                        for e in exc.errors()
                    ],
                )
            )
            continue

        # Build Step kwargs conditionally so draft-level overrides still
        # win but draft "unset" fields (None) fall through to the handler
        # decorator defaults via the Workflow validator's metadata
        # propagation. Dropping the unconditional descriptor mirroring of
        # ``is_async`` / ``completeness_check`` for the same reason.
        step_kwargs: dict[str, object] = {
            "name": step_draft.name,
            "handler": step_draft.handler,
            "config": typed_config,
            "step_timeout": step_draft.step_timeout,
        }
        if step_draft.depends_on is not None:
            step_kwargs["depends_on"] = step_draft.depends_on
        if step_draft.retry_policy is not None:
            step_kwargs["retry_policy"] = step_draft.retry_policy
        if step_draft.poll_policy is not None:
            step_kwargs["poll_policy"] = step_draft.poll_policy
        built_steps.append(Step(**step_kwargs))

    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "detail": "workflow draft validation failed",
                "errors": [e.model_dump() for e in errors],
            },
        )

    try:
        return Workflow(name=draft.name, steps=built_steps)
    except (ValueError, Exception) as exc:
        import re

        msg = str(exc)
        match = re.search(
            r"Step '([^']+)' handler requires dependencies (\[[^\]]+\]) "
            r"but step depends_on is missing (\[[^\]]+\])",
            msg,
        )

        dag_errors: list[DraftStepError] = []
        if match:
            step_name = match.group(1)
            missing = match.group(3)
            dag_errors.append(DraftStepError(
                step=step_name,
                error=f"Missing connections from: {missing}",
            ))
        else:
            # Extract the meaningful error from Pydantic's wrapper.
            # Format: "1 validation error for Workflow\n  Value error, <msg> ..."
            import re as _re
            value_match = _re.search(r"Value error,\s*(.+?)(?:\s*\[type=|\s*$)", msg)
            clean = value_match.group(1).strip() if value_match else msg.split("\n")[0][:120]
            dag_errors.append(DraftStepError(
                step="<workflow>",
                error=clean,
            ))

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "detail": "workflow DAG validation failed",
                "errors": [e.model_dump() for e in dag_errors],
            },
        ) from exc


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_designer_router(
    store: MongoWorkflowStore,
    *,
    server_title: str = "Workchain Server",
    instance_id: str = "",
) -> APIRouter:
    """Build the designer ``APIRouter`` bound to the given store.

    Args:
        store: The :class:`MongoWorkflowStore` used for workflow and
            template persistence.
        server_title: Human-readable server title exposed via ``GET /config``.
        instance_id: Engine instance identifier exposed via ``GET /config``.

    Returns:
        A FastAPI :class:`APIRouter` that should be mounted at ``/api/v1``.
    """
    router = APIRouter()

    # ------------------------------------------------------------------
    # Server config (for the SPA)
    # ------------------------------------------------------------------

    @router.get("/config", tags=["ops"])
    async def get_config() -> dict[str, str]:
        """Return server metadata for the frontend shell."""
        return {"server_title": server_title, "instance_id": instance_id}

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    @router.get("/handlers", response_model=list[HandlerDescriptor], tags=["designer"])
    async def list_registered_handlers() -> list[HandlerDescriptor]:
        """List all registered step handlers with their JSON schemas."""
        return list_handlers()

    # ------------------------------------------------------------------
    # Workflows (create from draft)
    # ------------------------------------------------------------------

    @router.post(
        "/workflows",
        status_code=status.HTTP_201_CREATED,
        tags=["designer"],
    )
    async def create_workflow_from_draft(draft: WorkflowDraft) -> dict[str, Any]:
        """Create and persist a :class:`Workflow` from a designer draft."""
        workflow = _build_workflow_from_draft(draft)
        workflow_id = await store.insert(workflow)
        return {
            "id": workflow_id,
            "name": workflow.name,
            "status": workflow.status.value,
        }

    # ------------------------------------------------------------------
    # Templates (CRUD + launch)
    # ------------------------------------------------------------------

    @router.get(
        "/templates",
        response_model=list[WorkflowTemplate],
        tags=["templates"],
    )
    async def list_templates(limit: int = 100) -> list[WorkflowTemplate]:
        """List workflow templates sorted by most-recently-updated first."""
        return await store.list_templates(limit=limit)

    @router.post(
        "/templates",
        response_model=WorkflowTemplate,
        status_code=status.HTTP_201_CREATED,
        tags=["templates"],
    )
    async def create_template(template: WorkflowTemplate) -> WorkflowTemplate:
        """Persist a new template.  Its model validators enforce DAG rules."""
        await store.insert_template(template)
        return template

    @router.get(
        "/templates/{template_id}",
        response_model=WorkflowTemplate,
        tags=["templates"],
    )
    async def get_template(template_id: str) -> WorkflowTemplate:
        """Fetch a single template or 404."""
        template = await store.get_template(template_id)
        if template is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Template {template_id!r} not found",
            )
        return template

    @router.put(
        "/templates/{template_id}",
        response_model=WorkflowTemplate,
        tags=["templates"],
    )
    async def update_template(
        template_id: str, body: TemplateUpdate
    ) -> WorkflowTemplate:
        """Update a template via optimistic locking.

        Returns 404 if the template does not exist, 409 if the caller's
        ``expected_version`` is stale, otherwise the updated template.
        The happy path issues a single Mongo round-trip; only the
        failure path reads the template again to distinguish 404 vs 409.
        """
        updated = await store.update_template(
            template_id,
            expected_version=body.expected_version,
            name=body.name,
            description=body.description,
            steps=body.steps,
        )
        if updated is not None:
            return updated

        # Update missed: either the id doesn't exist (404) or the version
        # didn't match (409). Read once to distinguish.
        current = await store.get_template(template_id)
        if current is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Template {template_id!r} not found",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Template {template_id!r} version mismatch: "
                f"expected {body.expected_version}, "
                f"current {current.version}"
            ),
        )

    @router.delete(
        "/templates/{template_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["templates"],
    )
    async def delete_template(template_id: str) -> None:
        """Delete a template, or 404 if not found."""
        deleted = await store.delete_template(template_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Template {template_id!r} not found",
            )

    @router.post(
        "/templates/{template_id}/launch",
        status_code=status.HTTP_201_CREATED,
        tags=["templates"],
    )
    async def launch_template(
        template_id: str, body: TemplateLaunchBody | None = None
    ) -> dict[str, Any]:
        """Instantiate a template into a runnable Workflow and persist it."""
        template = await store.get_template(template_id)
        if template is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Template {template_id!r} not found",
            )
        launch = body or TemplateLaunchBody()
        try:
            workflow = instantiate_template(
                template,
                name_override=launch.name_override,
                config_overrides=launch.config_overrides,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        workflow_id = await store.insert(workflow)
        return {
            "id": workflow_id,
            "name": workflow.name,
            "status": workflow.status.value,
        }

    return router
