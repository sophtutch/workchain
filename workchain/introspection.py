"""Handler introspection — describe registered step handlers as JSON-schema
descriptors suitable for driving UIs and schema-aware tooling.

The core library keeps handler metadata in :data:`workchain.decorators._STEP_REGISTRY`
and attaches a ``_step_meta`` dict to each decorated function.  This module
combines that registry with :func:`typing.get_type_hints` to extract the
Pydantic ``StepConfig`` / ``StepResult`` subclasses declared in each handler's
signature and emit their JSON schemas via :meth:`pydantic.BaseModel.model_json_schema`.

The output is a list of :class:`HandlerDescriptor` objects — plain Pydantic
models that serialise cleanly to JSON for the designer UI's ``GET /api/v1/handlers``
endpoint.
"""

from __future__ import annotations

import inspect
import logging
import typing
from typing import Any

from pydantic import BaseModel

from workchain.decorators import _STEP_META_ATTR, _STEP_REGISTRY
from workchain.models import PollPolicy, RetryPolicy, StepConfig, StepResult

logger = logging.getLogger(__name__)


class HandlerDescriptor(BaseModel):
    """Public description of a registered step handler.

    Emitted by :func:`list_handlers` and :func:`describe_handler` to drive
    UI-facing handler palettes and schema-driven config forms.

    Attributes:
        name: Dotted handler path (matches ``Step.handler``).
        module: Python module that defines the handler.
        qualname: Function ``__qualname__``.
        doc: Cleaned ``__doc__`` of the handler (via :func:`inspect.cleandoc`).
        is_async: True if the handler is an ``@async_step`` (submits + polls).
        is_completeness_check: True if the handler is a ``@completeness_check``.
        needs_context: True if the handler opted into the engine context dict.
        idempotent: Whether the handler may safely be re-executed on recovery.
        config_type: Dotted path to the ``StepConfig`` subclass, or ``None``.
        config_schema: ``model_json_schema()`` of the config subclass, or ``None``.
        result_type: Dotted path to the ``StepResult`` subclass, or ``None``.
        result_schema: ``model_json_schema()`` of the result subclass, or ``None``.
        retry_policy: Serialised :class:`RetryPolicy` (``None`` for checks without one).
        poll_policy: Serialised :class:`PollPolicy` (async steps only).
        completeness_check: Dotted path to the check handler (async steps only).
        launchable: Whether the handler is safe to use in a designer-built
            workflow.  ``False`` if either the config or result annotation is
            missing or is the base :class:`StepConfig`/:class:`StepResult` class
            (UIs should grey these out).
        introspection_warning: Human-readable warning if type hints could not
            be resolved (e.g. unresolved forward reference).  ``None`` on success.
    """

    name: str
    module: str
    qualname: str
    doc: str | None = None
    is_async: bool = False
    is_completeness_check: bool = False
    needs_context: bool = False
    idempotent: bool = True
    config_type: str | None = None
    config_schema: dict[str, Any] | None = None
    result_type: str | None = None
    result_schema: dict[str, Any] | None = None
    retry_policy: dict[str, Any] | None = None
    poll_policy: dict[str, Any] | None = None
    completeness_check: str | None = None
    launchable: bool = False
    introspection_warning: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _dotted_path(cls: type) -> str:
    """Return the canonical dotted import path for ``cls``."""
    return f"{cls.__module__}.{cls.__qualname__}"


def _resolve_type_hints(fn: Any) -> tuple[dict[str, Any], str | None]:
    """Resolve ``fn``'s type hints with a graceful fallback.

    Returns a tuple ``(hints, warning)``: ``hints`` is the mapping of
    parameter name to type, ``warning`` is a human-readable message if the
    resolution used the unresolved ``__annotations__`` fallback (typically
    because a forward reference failed to resolve).
    """
    try:
        return typing.get_type_hints(fn, include_extras=False), None
    except Exception as exc:  # pragma: no cover - defensive
        warning = (
            f"Could not resolve type hints via typing.get_type_hints "
            f"({type(exc).__name__}: {exc}); falling back to raw annotations."
        )
        logger.debug("introspection fallback for %r: %s", fn, warning)
        raw: dict[str, Any] = dict(getattr(fn, "__annotations__", {}))
        return raw, warning


def _config_param_annotation(fn: Any, hints: dict[str, Any]) -> Any:
    """Return the resolved annotation for the ``config`` parameter.

    Prefers the parameter literally named ``config``; falls back to the
    first positional parameter if that is missing (handlers in the codebase
    typically use ``config`` but the fallback keeps us robust).
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):  # pragma: no cover - builtins etc.
        return hints.get("config")
    if "config" in sig.parameters:
        return hints.get("config")
    for pname in sig.parameters:
        if pname == "return":
            continue
        return hints.get(pname)
    return None


def _result_annotation(hints: dict[str, Any]) -> Any:
    """Return the resolved return-type annotation from ``hints``."""
    return hints.get("return")


def _schema_for(annotation: Any, base: type[BaseModel]) -> tuple[str | None, dict[str, Any] | None]:
    """If ``annotation`` is a *strict* subclass of ``base``, return its dotted
    path and JSON schema.  Otherwise return ``(None, None)``.

    The ``base`` class itself is deliberately rejected — :class:`StepConfig`
    and :class:`StepResult` are empty markers and are not schema-carrying
    types for designer purposes.
    """
    if not isinstance(annotation, type):
        return None, None
    if annotation is base:
        return None, None
    if not issubclass(annotation, base):
        return None, None
    try:
        schema = annotation.model_json_schema()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "Failed to emit JSON schema for %s: %s", _dotted_path(annotation), exc
        )
        return _dotted_path(annotation), None
    return _dotted_path(annotation), schema


def _policy_dump(policy: RetryPolicy | PollPolicy | None) -> dict[str, Any] | None:
    """Serialise a retry or poll policy to a plain dict (or ``None``)."""
    if policy is None:
        return None
    return policy.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def describe_handler(name: str, *, include_checks: bool = False) -> HandlerDescriptor | None:
    """Build a :class:`HandlerDescriptor` for the registered handler ``name``.

    Args:
        name: Dotted handler path as stored in ``_STEP_REGISTRY``.
        include_checks: If ``False`` (default), completeness-check handlers
            return ``None`` — the designer palette only shows step handlers.

    Returns:
        A :class:`HandlerDescriptor`, or ``None`` if the handler is not
        registered or is a completeness check and ``include_checks=False``.
    """
    fn = _STEP_REGISTRY.get(name)
    if fn is None:
        return None

    meta: dict[str, Any] = getattr(fn, _STEP_META_ATTR, {}) or {}
    is_check = bool(meta.get("is_completeness_check", False))
    if is_check and not include_checks:
        return None

    hints, warning = _resolve_type_hints(fn)
    config_type, config_schema = (None, None)
    result_type, result_schema = (None, None)

    # Completeness checks have signature (config, results, result, [ctx]) and
    # return CheckResult; they are not launchable as standalone steps so we
    # deliberately skip schema extraction for them.
    if not is_check:
        config_ann = _config_param_annotation(fn, hints)
        config_type, config_schema = _schema_for(config_ann, StepConfig)

        result_ann = _result_annotation(hints)
        result_type, result_schema = _schema_for(result_ann, StepResult)

    launchable = (
        not is_check
        and config_type is not None
        and config_schema is not None
        and result_type is not None
        and result_schema is not None
    )

    doc = inspect.cleandoc(fn.__doc__) if fn.__doc__ else None

    return HandlerDescriptor(
        name=name,
        module=fn.__module__,
        qualname=fn.__qualname__,
        doc=doc,
        is_async=bool(meta.get("is_async", False)),
        is_completeness_check=is_check,
        needs_context=bool(meta.get("needs_context", False)),
        idempotent=bool(meta.get("idempotent", True)),
        config_type=config_type,
        config_schema=config_schema,
        result_type=result_type,
        result_schema=result_schema,
        retry_policy=_policy_dump(meta.get("retry")),
        poll_policy=_policy_dump(meta.get("poll")),
        completeness_check=meta.get("completeness_check"),
        launchable=launchable,
        introspection_warning=warning,
    )


def list_handlers(*, include_checks: bool = False) -> list[HandlerDescriptor]:
    """List all registered handlers as :class:`HandlerDescriptor` objects.

    Args:
        include_checks: If ``True``, include ``@completeness_check`` handlers
            in the result.  Defaults to ``False`` so designer palettes only
            see step handlers.

    Returns:
        A list of descriptors sorted by dotted handler name for stable output.
    """
    out: list[HandlerDescriptor] = []
    for handler_name in sorted(_STEP_REGISTRY):
        descriptor = describe_handler(handler_name, include_checks=include_checks)
        if descriptor is not None:
            out.append(descriptor)
    return out
