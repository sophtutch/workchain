"""Decorators for defining workflow steps."""

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
    name: str | None = None,
    retry: RetryPolicy | None = None,
    idempotent: bool = True,
):
    """
    Decorator to register a step handler.

    The handler signature should be:
        async def my_step(config: MyConfig, results: dict[str, StepResult]) -> MyResult

    Config is a StepConfig subclass (deserialized at the store level), or None
    if the step has no configuration.
    Results is a dict of preceding step results keyed by step name.
    Return a StepResult subclass with typed fields.
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

    The handler should SUBMIT the work and return immediately with a
    StepResult subclass (e.g. containing a job_id). The engine will then
    poll the completeness_check callable until it returns True or a PollHint.

    completeness_check can be:
      - A dotted string path: "myapp.steps.check_provisioning"
      - A callable: the function itself (auto-registered)
      - None: no polling (step completes immediately)

    completeness_check signature:
        async def check(config: StepConfig, results: dict, result: MyResult) -> bool | dict | PollHint
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
