"""Decorators for defining workflow steps and completeness checks."""

from __future__ import annotations

import asyncio
import functools
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Callable

from workchain.models import CheckResult, PollPolicy, RetryPolicy


class StepHandler(Protocol):
    """Protocol for decorated step/check handlers — callable with _step_meta."""

    _step_meta: dict[str, Any]

    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


_STEP_META_ATTR = "_step_meta"

# Global step registry: handler_name -> callable
_STEP_REGISTRY: dict[str, Callable[..., Any]] = {}


def _resolve_check_name(check: str | Callable[..., Any] | None) -> str | None:
    """
    Accept either a dotted string name or a callable for completeness_check.
    If a callable is passed, auto-register it in _STEP_REGISTRY and return
    its generated name.
    """
    if check is None:
        return None
    if isinstance(check, str):
        return check
    # It's a callable — register it automatically
    check_name = f"{check.__module__}.{check.__qualname__}"
    _STEP_REGISTRY[check_name] = check
    return check_name


def get_handler(name: str) -> Callable[..., Any]:
    """Look up a registered step handler by dotted name.

    Raises:
        ValueError: If the handler cannot be found, with diagnostic hints.
    """
    if name in _STEP_REGISTRY:
        return _STEP_REGISTRY[name]

    # Fallback: dynamic import and cache
    module_path, _, func_name = name.rpartition(".")
    if not module_path:
        registered = sorted(_STEP_REGISTRY.keys())
        hint = ""
        if registered:
            # Suggest close matches
            matches = [r for r in registered if r.endswith(f".{name}")]
            if matches:
                hint = f"\n  Did you mean: {matches[0]!r}?"
            else:
                max_suggest = 10
                hint = f"\n  Registered handlers: {', '.join(registered[:max_suggest])}"
                if len(registered) > max_suggest:
                    hint += f" ... ({len(registered)} total)"
        else:
            hint = (
                "\n  No handlers are registered. Ensure the module containing "
                "your @step/@async_step decorated functions has been imported."
            )
        raise ValueError(f"Unknown handler: {name!r}{hint}")

    import importlib

    try:
        mod = importlib.import_module(module_path)
    except ModuleNotFoundError as e:
        raise ValueError(
            f"Handler module not found: {module_path!r} "
            f"(from handler path {name!r})"
            f"\n  Hint: ensure the module is installed and importable. "
            f"Check for typos in the dotted path."
        ) from e

    try:
        fn = getattr(mod, func_name)
    except AttributeError:
        available = [
            attr for attr in dir(mod)
            if not attr.startswith("_") and callable(getattr(mod, attr, None))
        ]
        hint = ""
        if available:
            hint = f"\n  Available callables in {module_path}: {', '.join(available[:15])}"
        raise ValueError(
            f"Handler function {func_name!r} not found in module {module_path!r} "
            f"(from handler path {name!r}){hint}"
        ) from None

    handler: Callable[..., Any] = fn
    _STEP_REGISTRY[name] = handler
    return handler


def step(
    retry: RetryPolicy | None = None,
    idempotent: bool = True,
    needs_context: bool = False,
    category: str | None = None,
    description: str | None = None,
    depends_on: list[str] | None = None,
) -> Callable[[Callable[..., Any]], StepHandler]:
    """
    Decorator to register a step handler.

    The handler signature should be:
        async def my_step(config: MyConfig, results: dict[str, StepResult]) -> MyResult

    Or with engine context:
        async def my_step(config: MyConfig, results: dict[str, StepResult], ctx: dict[str, Any]) -> MyResult

    Args:
        retry: Retry policy for this step.
        idempotent: Whether the handler may safely be re-executed on recovery.
        needs_context: Set to ``True`` to receive the engine context dict as
            the third argument.
        category: Optional grouping label for UI organisation (e.g.
            ``"Data transformation"``, ``"Notification"``).  When ``None``
            the handler appears in an "Uncategorised" group.
        description: Short one-line summary shown in the designer palette.
            Falls back to the first line of the handler docstring if ``None``.
        depends_on: Handler short names this step requires results from.
            Used by the designer to auto-wire dependency edges.
            E.g. ``["validate_email", "create_account"]``.

    The handler name is auto-generated from module + qualname.
    """
    def decorator(fn: Callable[..., Any]) -> StepHandler:
        handler_name = f"{fn.__module__}.{fn.__qualname__}"
        setattr(fn, _STEP_META_ATTR, {
            "handler": handler_name,
            "retry": retry or RetryPolicy(),
            "is_async": False,
            "idempotent": idempotent,
            "needs_context": needs_context,
            "category": category,
            "description": description,
            "depends_on": depends_on,
        })
        _STEP_REGISTRY[handler_name] = fn
        return cast(StepHandler, fn)
    return decorator


def async_step(
    retry: RetryPolicy | None = None,
    idempotent: bool = True,
    needs_context: bool = False,
    poll: PollPolicy | None = None,
    completeness_check: str | Callable[..., Any] | None = None,
    category: str | None = None,
    description: str | None = None,
    depends_on: list[str] | None = None,
) -> Callable[[Callable[..., Any]], StepHandler]:
    """
    Decorator for async steps that submit work and poll until complete.

    The handler should SUBMIT the work and return immediately with a
    StepResult subclass (e.g. containing a job_id). The engine will then
    poll the completeness_check callable until it returns True or a CheckResult.

    Args:
        retry: Retry policy for this step.
        idempotent: Whether the handler may safely be re-executed on recovery.
        needs_context: Set to ``True`` to receive the engine context dict as
            the third argument.
        poll: Polling policy for completion checks.
        completeness_check: A callable decorated with ``@completeness_check``,
            a dotted string path, or ``None`` (step completes immediately).
        category: Optional grouping label for UI organisation (e.g.
            ``"Data transformation"``, ``"Notification"``).  When ``None``
            the handler appears in an "Uncategorised" group.
        description: Short one-line summary shown in the designer palette.
            Falls back to the first line of the handler docstring if ``None``.

    The handler name is auto-generated from module + qualname.
    """
    def decorator(fn: Callable[..., Any]) -> StepHandler:
        handler_name = f"{fn.__module__}.{fn.__qualname__}"
        check_name = _resolve_check_name(completeness_check)
        setattr(fn, _STEP_META_ATTR, {
            "handler": handler_name,
            "retry": retry or RetryPolicy(),
            "is_async": True,
            "idempotent": idempotent,
            "needs_context": needs_context,
            "poll": poll or PollPolicy(),
            "completeness_check": check_name,
            "category": category,
            "description": description,
            "depends_on": depends_on,
        })
        _STEP_REGISTRY[handler_name] = fn
        return cast(StepHandler, fn)
    return decorator


def _normalize_check_result(raw: object) -> CheckResult:
    """Coerce a completeness check return value to CheckResult.

    Accepts CheckResult (passthrough), dict (model_validate), or bool
    (converted to CheckResult(complete=v)).  Raises TypeError for
    anything else so bad return types fail fast at the call site.
    """
    if isinstance(raw, CheckResult):
        return raw
    if isinstance(raw, dict):
        return CheckResult.model_validate(raw)
    if isinstance(raw, bool):
        return CheckResult(complete=raw)
    got_type = type(raw).__name__
    hint = ""
    if callable(raw):
        hint = (
            f"\n  Hint: the check returned a callable ({got_type}) instead of "
            f"a result. Did you forget to await an async call?"
        )
    elif raw is None:
        hint = (
            "\n  Hint: the check returned None. "
            "Ensure all code paths return CheckResult, a dict, or a bool."
        )
    raise TypeError(
        f"completeness_check must return CheckResult, dict, or bool — "
        f"got {got_type}{hint}"
    )


def completeness_check(
    needs_context: bool = False,
    retry: RetryPolicy | None = None,
) -> Callable[[Callable[..., Any]], StepHandler]:
    """
    Decorator to register a completeness check function for async steps.

    The check signature should be:
        async def check(config: StepConfig, results: dict, result: MyResult) -> CheckResult

    Or with engine context:
        async def check(config, results, result, ctx: dict[str, Any]) -> CheckResult

    Handlers may also return ``bool`` or ``dict`` for convenience — the
    decorator normalizes all return values to ``CheckResult``.

    Set ``needs_context=True`` to receive the engine context dict as the
    fourth argument.  The handler name is auto-generated from module + qualname.

    Set ``retry`` to configure retry behavior when the check throws an
    exception.  Defaults to ``RetryPolicy()`` (3 attempts, exponential
    backoff).  If all retries are exhausted within a single poll cycle,
    the step fails immediately.
    """
    def decorator(fn: Callable[..., Any]) -> StepHandler:
        handler_name = f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> CheckResult:
            result = fn(*args, **kwargs)
            if asyncio.iscoroutine(result):
                result = await result
            try:
                return _normalize_check_result(result)
            except TypeError as e:
                raise TypeError(f"{e} (check={handler_name!r})") from e.__cause__

        setattr(wrapper, _STEP_META_ATTR, {"handler": handler_name, "is_completeness_check": True, "needs_context": needs_context, "retry": retry or RetryPolicy()})
        _STEP_REGISTRY[handler_name] = wrapper
        return cast(StepHandler, wrapper)
    return decorator
