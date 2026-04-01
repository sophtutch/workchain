"""Decorators for defining workflow steps."""

from __future__ import annotations

from collections.abc import Callable

from .models import PollPolicy, RetryPolicy


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


# Global step registry: handler_name -> callable
_STEP_REGISTRY: dict[str, Callable] = {}


def get_handler(name: str) -> Callable:
    """Look up a registered step handler by dotted name."""
    if name in _STEP_REGISTRY:
        return _STEP_REGISTRY[name]
    # Fallback: dynamic import
    module_path, _, func_name = name.rpartition(".")
    if not module_path:
        raise ValueError(f"Unknown handler: {name}")
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, func_name)


def step(
    name: str | None = None,
    retry: RetryPolicy | None = None,
    idempotent: bool = True,
):
    """
    Decorator to register a synchronous-style step handler.

    The handler signature should be:
        async def my_step(config: dict, context: dict) -> dict

    It receives the step config and shared workflow context,
    and returns a result dict.
    """
    def decorator(fn: Callable) -> Callable:
        handler_name = name or f"{fn.__module__}.{fn.__qualname__}"
        fn._step_meta = {
            "handler": handler_name,
            "retry": retry or RetryPolicy(),
            "is_async": False,
            "idempotent": idempotent,
        }
        _STEP_REGISTRY[handler_name] = fn
        return fn
    return decorator


def async_step(
    name: str | None = None,
    retry: RetryPolicy | None = None,
    idempotent: bool = True,
    poll: PollPolicy | None = None,
    completeness_check: str | Callable | None = None,
):
    """
    Decorator for async steps that submit work and poll until complete.

    The handler should SUBMIT the work and return immediately with a result
    dict (e.g. containing a job_id). The engine will then poll the
    completeness_check callable until it returns True or a PollHint.

    completeness_check can be:
      - A dotted string path: "myapp.steps.check_provisioning"
      - A callable: the function itself (auto-registered)
      - None: no polling (step completes immediately)

    completeness_check signature:
        async def check(config, context, result) -> bool | dict | PollHint

    Returning a dict or PollHint allows the checker to provide scheduling
    hints (retry_after, progress, message) back to the engine.
    """
    def decorator(fn: Callable) -> Callable:
        handler_name = name or f"{fn.__module__}.{fn.__qualname__}"
        check_name = _resolve_check_name(completeness_check)
        fn._step_meta = {
            "handler": handler_name,
            "retry": retry or RetryPolicy(),
            "is_async": True,
            "idempotent": idempotent,
            "poll": poll or PollPolicy(),
            "completeness_check": check_name,
        }
        _STEP_REGISTRY[handler_name] = fn
        return fn
    return decorator
