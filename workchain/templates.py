"""Workflow templates — persistable designer artifacts.

A :class:`WorkflowTemplate` is a *design-time* representation of a workflow.
It captures the shape of a DAG (steps, handler references, default config
dicts, dependency edges, retry/poll policies) without any of the runtime
fields (``status``, ``locked_by``, ``fence_token``, ``attempt``, ``result``,
polling timestamps, etc.) that belong to a live :class:`~workchain.models.Workflow`.

Templates are instantiated into runnable workflows via :func:`instantiate_template`,
which resolves each step's handler, looks up its ``StepConfig`` subclass via
:func:`~workchain.introspection.describe_handler`, and validates the raw
config dict with ``ConfigCls.model_validate`` so that the resulting
:class:`~workchain.models.Workflow` carries properly typed configs ready for
MongoDB persistence.
"""

from __future__ import annotations

import importlib
from datetime import datetime  # noqa: TCH003 — used at runtime by Pydantic field annotations
from typing import Any

from pydantic import BaseModel, Field, model_validator

from workchain.introspection import describe_handler
from workchain.models import (
    PollPolicy,
    RetryPolicy,
    Step,
    StepConfig,
    Workflow,
    _new_id,
    _utcnow,
    _validate_dag,
)


def _import_config_class(dotted_path: str) -> type[StepConfig]:
    """Import a ``StepConfig`` subclass by dotted path.

    Kept local to this module to avoid a circular import between
    :mod:`workchain.templates` and :mod:`workchain.store`.
    """
    module_path, _, class_name = dotted_path.rpartition(".")
    if not module_path:
        raise ValueError(f"Invalid dotted path: {dotted_path}")
    mod = importlib.import_module(module_path)
    try:
        cls = getattr(mod, class_name)
    except AttributeError as exc:
        raise ImportError(
            f"Cannot find '{class_name}' in module '{module_path}' "
            f"(full path: {dotted_path})"
        ) from exc
    if not (isinstance(cls, type) and issubclass(cls, StepConfig)):
        raise TypeError(
            f"{dotted_path} is not a StepConfig subclass"
        )
    return cls

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class StepTemplate(BaseModel):
    """A single step in a :class:`WorkflowTemplate`.

    Deliberately omits runtime fields (status, locks, fence_token, attempt,
    result, polling state) because they are meaningless at design time.  The
    ``config`` field is a raw ``dict`` here; the typed
    :class:`~workchain.models.StepConfig` subclass is resolved at
    instantiation time via :func:`instantiate_template`.

    Attributes:
        name: Unique step name within the template.
        handler: Dotted handler path (must resolve via ``get_handler``).
        config: Raw JSON config dict — validated against the handler's
            ``StepConfig`` subclass when the template is instantiated.
        depends_on: Dependency names.  ``None`` means "depend on the previous
            step" (sequential default); ``[]`` means root step.
        retry_policy: Optional per-step retry policy override.
        poll_policy: Optional per-step poll policy override (async steps only).
        step_timeout: Per-attempt timeout in seconds (0 = no timeout).
    """

    name: str
    handler: str
    config: dict[str, Any] | None = None
    depends_on: list[str] | None = None
    retry_policy: RetryPolicy | None = None
    poll_policy: PollPolicy | None = None
    step_timeout: float = 0


class WorkflowTemplate(BaseModel):
    """A reusable workflow design saved by the designer UI.

    Templates are persisted in their own MongoDB collection
    (``workflow_templates``) and instantiated into live :class:`Workflow`
    runs on demand.  They carry an optimistic-locking ``version`` counter
    that is bumped by :meth:`~workchain.store.MongoWorkflowStore.update_template`
    so concurrent edits surface as 409 conflicts rather than silent overwrites.

    Attributes:
        id: 32-char hex identifier (auto-generated).
        name: Human-readable template name.
        description: Optional longer description shown in the designer.
        steps: Ordered list of :class:`StepTemplate`.
        version: Optimistic locking counter; starts at 1 and is incremented
            by the store on every successful update.
        created_at: UTC datetime.
        updated_at: UTC datetime — refreshed on update.
    """

    id: str = Field(default_factory=_new_id)
    name: str
    description: str | None = None
    steps: list[StepTemplate] = Field(default_factory=list)
    version: int = 1
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @model_validator(mode="after")
    def _validate_unique_step_names(self) -> WorkflowTemplate:
        names = [s.name for s in self.steps]
        if len(names) != len(set(names)):
            dupes = [n for n in names if names.count(n) > 1]
            raise ValueError(
                f"StepTemplate names must be unique, found duplicates: {sorted(set(dupes))}"
            )
        return self

    @model_validator(mode="after")
    def _resolve_and_validate_depends_on(self) -> WorkflowTemplate:
        """Resolve ``None`` → sequential default and validate the DAG.

        Mirrors :class:`Workflow` semantics so a template and its
        instantiated workflow share the same dependency rules: ``None``
        means "depend on the previous step", ``[]`` means root, and
        cycles / unknown refs / self-loops are rejected.
        """
        if not self.steps:
            return self

        for i, step in enumerate(self.steps):
            if step.depends_on is None:
                step.depends_on = [self.steps[i - 1].name] if i > 0 else []

        _validate_dag(
            step_names=[s.name for s in self.steps],
            depends_on_by_name={s.name: s.depends_on or [] for s in self.steps},
            container="StepTemplate",
        )
        return self


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------


def instantiate_template(
    template: WorkflowTemplate,
    *,
    name_override: str | None = None,
    config_overrides: dict[str, dict[str, Any]] | None = None,
) -> Workflow:
    """Build a runnable :class:`Workflow` from a :class:`WorkflowTemplate`.

    For each :class:`StepTemplate`:

    1. Resolve the handler via :func:`~workchain.decorators.get_handler` so
       missing handlers fail fast with a clear error.
    2. Look up its ``StepConfig`` subclass via
       :func:`~workchain.introspection.describe_handler` and coerce the raw
       config dict (merged with any ``config_overrides[step.name]``) through
       ``ConfigCls.model_validate``.  Handlers whose ``describe_handler``
       returns ``None`` or ``launchable=False`` raise ``ValueError`` —
       templates should only reference registered, typed handlers.
    3. Construct a :class:`Step` with ``is_async`` / ``completeness_check``
       mirrored from the descriptor so the template never needs to duplicate
       handler metadata.

    Args:
        template: The template to instantiate.
        name_override: Optional workflow name — defaults to the template name.
        config_overrides: Optional mapping of step name → dict of config
            fields to overlay on top of the template's stored config.
            Fields not present in the override are taken from the template.

    Returns:
        A :class:`Workflow` ready for :meth:`~workchain.store.MongoWorkflowStore.insert`.

    Raises:
        ValueError: If a handler is unknown, not launchable, or raises a
            Pydantic validation error when constructing its config.
    """
    overrides = config_overrides or {}
    built_steps: list[Step] = []

    for tpl_step in template.steps:
        descriptor = describe_handler(tpl_step.handler)
        if descriptor is None:
            raise ValueError(
                f"Template step '{tpl_step.name}' references unknown handler "
                f"{tpl_step.handler!r}. Ensure the module defining the handler "
                f"has been imported."
            )
        if not descriptor.launchable:
            raise ValueError(
                f"Template step '{tpl_step.name}' references handler "
                f"{tpl_step.handler!r} which is not launchable: its signature "
                f"must declare a StepConfig subclass and a StepResult subclass."
            )

        # describe_handler already verified the handler is registered; no
        # need to call get_handler() as well.
        merged_config = dict(tpl_step.config or {})
        merged_config.update(overrides.get(tpl_step.name, {}))

        # `launchable=True` guarantees `config_type` is populated, but we
        # narrow explicitly for the type-checker (rather than `assert`, which
        # is stripped under -O and trips S101 in the linter).
        if descriptor.config_type is None:  # pragma: no cover - defensive
            raise ValueError(
                f"Handler {tpl_step.handler!r} is launchable but has no config_type"
            )
        config_cls = _import_config_class(descriptor.config_type)
        typed_config = config_cls.model_validate(merged_config)

        # Build Step kwargs conditionally so template-level overrides still
        # win but template "unset" fields (None) fall through to the handler
        # decorator defaults via the Workflow validator's metadata
        # propagation. Dropping the unconditional ``is_async`` /
        # ``completeness_check`` mirroring from the descriptor for the same
        # reason — the validator now handles it.
        step_kwargs: dict[str, object] = {
            "name": tpl_step.name,
            "handler": tpl_step.handler,
            "config": typed_config,
            "step_timeout": tpl_step.step_timeout,
        }
        if tpl_step.depends_on is not None:
            step_kwargs["depends_on"] = list(tpl_step.depends_on)
        if tpl_step.retry_policy is not None:
            step_kwargs["retry_policy"] = tpl_step.retry_policy
        if tpl_step.poll_policy is not None:
            step_kwargs["poll_policy"] = tpl_step.poll_policy
        built_steps.append(Step(**step_kwargs))

    return Workflow(
        name=name_override or template.name,
        steps=built_steps,
    )
