"""Decorators for defining workflow steps and completeness checks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from workchain.models import PollPolicy, RetryPolicy

if TYPE_CHECKING:
    from collections.abc import Callable

# Global step registry: handler_name -> callable
_STEP_REGISTRY: dict[str, Callable] = {}


def _resolve_check_name(check: str | Callable | None) -> str | None:
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


def get_handler(name: str) -> Callable:
    """Look up a registered step handler by dotted name."""
    if name in _STEP_REGISTRY:
        return _STEP_REGISTRY[name]
    # Fallback: dynamic import and cache
    module_path, _, func_name = name.rpartition(".")
    if not module_path:
        raise ValueError(f"Unknown handler: {name}")
    import importlib
    mod = importlib.import_module(module_path)
    fn = getattr(mod, func_name)
    _STEP_REGISTRY[name] = fn
    return fn


def step(
    retry: RetryPolicy | None = None,
    idempotent: bool = True,
    needs_context: bool = False,
):
    """
    Decorator to register a step handler.

    The handler signature should be:
        async def my_step(config: MyConfig, results: dict[str, StepResult]) -> MyResult

    Or with engine context:
        async def my_step(config: MyConfig, results: dict[str, StepResult], ctx: dict[str, Any]) -> MyResult

    Set ``needs_context=True`` to receive the engine context dict as the
    third argument.  The handler name is auto-generated from module + qualname.
    """
    def decorator(fn: Callable) -> Callable:
        handler_name = f"{fn.__module__}.{fn.__qualname__}"
        fn._step_meta = {
            "handler": handler_name,
            "retry": retry or RetryPolicy(),
            "is_async": False,
            "idempotent": idempotent,
            "needs_context": needs_context,
        }
        _STEP_REGISTRY[handler_name] = fn
        return fn
    return decorator


def async_step(
    retry: RetryPolicy | None = None,
    idempotent: bool = True,
    needs_context: bool = False,
    poll: PollPolicy | None = None,
    completeness_check: str | Callable | None = None,
):
    """
    Decorator for async steps that submit work and poll until complete.

    The handler should SUBMIT the work and return immediately with a
    StepResult subclass (e.g. containing a job_id). The engine will then
    poll the completeness_check callable until it returns True or a PollHint.

    completeness_check can be:
      - A callable decorated with @completeness_check
      - A dotted string path: "myapp.steps.check_provisioning"
      - None: no polling (step completes immediately)

    Set ``needs_context=True`` to receive the engine context dict as the
    third argument.  The handler name is auto-generated from module + qualname.
    """
    def decorator(fn: Callable) -> Callable:
        handler_name = f"{fn.__module__}.{fn.__qualname__}"
        check_name = _resolve_check_name(completeness_check)
        fn._step_meta = {
            "handler": handler_name,
            "retry": retry or RetryPolicy(),
            "is_async": True,
            "idempotent": idempotent,
            "needs_context": needs_context,
            "poll": poll or PollPolicy(),
            "completeness_check": check_name,
        }
        _STEP_REGISTRY[handler_name] = fn
        return fn
    return decorator


def completeness_check(
    needs_context: bool = False,
    retry: RetryPolicy | None = None,
):
    """
    Decorator to register a completeness check function for async steps.

    The check signature should be:
        async def check(config: StepConfig, results: dict, result: MyResult) -> bool | dict | PollHint

    Or with engine context:
        async def check(config, results, result, ctx: dict[str, Any]) -> bool | dict | PollHint

    Set ``needs_context=True`` to receive the engine context dict as the
    fourth argument.  The handler name is auto-generated from module + qualname.

    Set ``retry`` to configure retry behavior when the check throws an
    exception.  Defaults to ``RetryPolicy()`` (3 attempts, exponential
    backoff).  If all retries are exhausted within a single poll cycle,
    the step fails immediately.
    """
    def decorator(fn: Callable) -> Callable:
        handler_name = f"{fn.__module__}.{fn.__qualname__}"
        fn._step_meta = {
            "handler": handler_name,
            "is_completeness_check": True,
            "needs_context": needs_context,
            "retry": retry or RetryPolicy(),
        }
        _STEP_REGISTRY[handler_name] = fn
        return fn
    return decorator
