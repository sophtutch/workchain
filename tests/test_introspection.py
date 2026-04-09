"""Tests for workchain.introspection — handler descriptors + JSON schemas."""

from __future__ import annotations

from workchain.decorators import async_step, completeness_check, step
from workchain.introspection import (
    HandlerDescriptor,
    describe_handler,
    list_handlers,
)
from workchain.models import CheckResult, PollPolicy, RetryPolicy, StepConfig, StepResult

# ---------------------------------------------------------------------------
# Fixture handlers
# ---------------------------------------------------------------------------


class _IntroConfig(StepConfig):
    name: str
    count: int = 1


class _IntroResult(StepResult):
    greeting: str


class _SubmitResult(StepResult):
    job_id: str


@step()
async def _sync_handler(config: _IntroConfig, _results: dict[str, StepResult]) -> _IntroResult:
    """Return a greeting for the configured name."""
    return _IntroResult(greeting=f"hi {config.name}")


@completeness_check()
async def _intro_check(
    _config: StepConfig,
    _results: dict[str, StepResult],
    _result: StepResult,
) -> CheckResult:
    return CheckResult(complete=True)


@async_step(
    poll=PollPolicy(interval=1.0, timeout=30.0),
    completeness_check=_intro_check,
    retry=RetryPolicy(max_attempts=5),
)
async def _async_handler(
    _config: _IntroConfig, _results: dict[str, StepResult]
) -> _SubmitResult:
    # Exercises the first-positional fallback path in
    # `_config_param_annotation`: the parameter isn't literally named
    # "config", so introspection should still pick up the annotation.
    return _SubmitResult(job_id="j1")


@step()
async def _untyped_handler(_config, _results):  # type: ignore[no-untyped-def]
    return StepResult()


@step()
async def _base_config_handler(
    _config: StepConfig, _results: dict[str, StepResult]
) -> StepResult:
    return StepResult()


# ---------------------------------------------------------------------------
# describe_handler
# ---------------------------------------------------------------------------


class TestDescribeHandler:
    def test_sync_handler_has_schemas(self) -> None:
        name = _sync_handler._step_meta["handler"]
        desc = describe_handler(name)
        assert desc is not None
        assert desc.name == name
        assert desc.is_async is False
        assert desc.is_completeness_check is False
        assert desc.launchable is True
        assert desc.config_type is not None
        assert desc.config_type.endswith("._IntroConfig")
        assert desc.result_type is not None
        assert desc.result_type.endswith("._IntroResult")
        assert desc.config_schema is not None
        assert desc.config_schema["properties"]["name"]["type"] == "string"
        assert desc.config_schema["properties"]["count"]["type"] == "integer"
        assert desc.result_schema is not None
        assert "greeting" in desc.result_schema["properties"]
        assert desc.doc == "Return a greeting for the configured name."
        assert desc.retry_policy is not None
        assert desc.retry_policy["max_attempts"] == 3  # RetryPolicy default
        assert desc.poll_policy is None
        assert desc.completeness_check is None
        assert desc.introspection_warning is None

    def test_async_handler_includes_poll_and_check(self) -> None:
        name = _async_handler._step_meta["handler"]
        desc = describe_handler(name)
        assert desc is not None
        assert desc.is_async is True
        assert desc.launchable is True
        assert desc.poll_policy is not None
        assert desc.poll_policy["interval"] == 1.0
        assert desc.poll_policy["timeout"] == 30.0
        assert desc.retry_policy is not None
        assert desc.retry_policy["max_attempts"] == 5
        assert desc.completeness_check == _intro_check._step_meta["handler"]
        assert desc.result_type is not None
        assert desc.result_type.endswith("._SubmitResult")

    def test_completeness_check_excluded_by_default(self) -> None:
        name = _intro_check._step_meta["handler"]
        assert describe_handler(name) is None

    def test_completeness_check_included_when_asked(self) -> None:
        name = _intro_check._step_meta["handler"]
        desc = describe_handler(name, include_checks=True)
        assert desc is not None
        assert desc.is_completeness_check is True
        assert desc.launchable is False
        # Completeness checks deliberately skip config/result schema extraction.
        assert desc.config_schema is None
        assert desc.result_schema is None
        # The @completeness_check decorator always attaches a RetryPolicy
        # (defaults to RetryPolicy()); the descriptor should surface it.
        assert desc.retry_policy is not None
        assert desc.retry_policy["max_attempts"] == 3

    def test_unknown_handler_returns_none(self) -> None:
        assert describe_handler("nope.does.not.exist") is None

    def test_untyped_handler_not_launchable(self) -> None:
        name = _untyped_handler._step_meta["handler"]
        desc = describe_handler(name)
        assert desc is not None
        assert desc.launchable is False
        assert desc.config_type is None
        assert desc.config_schema is None

    def test_base_stepconfig_annotation_not_launchable(self) -> None:
        name = _base_config_handler._step_meta["handler"]
        desc = describe_handler(name)
        assert desc is not None
        # Base StepConfig/StepResult are markers — designer treats them as
        # missing schemas and greys out the handler.
        assert desc.launchable is False
        assert desc.config_type is None
        assert desc.result_type is None


# ---------------------------------------------------------------------------
# list_handlers
# ---------------------------------------------------------------------------


class TestListHandlers:
    def test_excludes_completeness_checks_by_default(self) -> None:
        handlers = list_handlers()
        names = {h.name for h in handlers}
        assert _sync_handler._step_meta["handler"] in names
        assert _async_handler._step_meta["handler"] in names
        assert _intro_check._step_meta["handler"] not in names

    def test_includes_completeness_checks_when_asked(self) -> None:
        handlers = list_handlers(include_checks=True)
        names = {h.name for h in handlers}
        assert _intro_check._step_meta["handler"] in names

    def test_sorted_stable_output(self) -> None:
        handlers = list_handlers()
        names = [h.name for h in handlers]
        assert names == sorted(names)

    def test_returns_handler_descriptors(self) -> None:
        handlers = list_handlers()
        assert all(isinstance(h, HandlerDescriptor) for h in handlers)
