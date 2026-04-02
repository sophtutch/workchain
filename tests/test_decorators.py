"""Tests for workchain.decorators — @step, @async_step, get_handler."""

from __future__ import annotations

import pytest

from workchain.decorators import _STEP_REGISTRY, async_step, get_handler, step
from workchain.models import PollPolicy, RetryPolicy, StepResult

# ---------------------------------------------------------------------------
# @step decorator
# ---------------------------------------------------------------------------


class TestStepDecorator:
    def test_registers_handler(self):
        @step(name="test.dec.one")
        async def my_step(_c, _r):
            return StepResult()

        assert "test.dec.one" in _STEP_REGISTRY
        assert _STEP_REGISTRY["test.dec.one"] is my_step

    def test_auto_name(self):
        @step()
        async def auto_named(_c, _r):
            return StepResult()

        expected = f"{auto_named.__module__}.{auto_named.__qualname__}"
        assert expected in _STEP_REGISTRY
        assert auto_named._step_meta["handler"] == expected

    def test_explicit_name(self):
        @step(name="test.dec.explicit")
        async def named_step(_c, _r):
            return StepResult()

        assert named_step._step_meta["handler"] == "test.dec.explicit"

    def test_meta_fields(self):
        @step(name="test.dec.meta")
        async def meta_step(_c, _r):
            return StepResult()

        meta = meta_step._step_meta
        assert meta["is_async"] is False
        assert meta["idempotent"] is True
        assert isinstance(meta["retry"], RetryPolicy)

    def test_custom_retry(self):
        policy = RetryPolicy(max_attempts=5, wait_seconds=0.5)

        @step(name="test.dec.custom_retry", retry=policy)
        async def custom_retry_step(_c, _r):
            return StepResult()

        assert custom_retry_step._step_meta["retry"].max_attempts == 5

    def test_idempotent_false(self):
        @step(name="test.dec.non_idempotent", idempotent=False)
        async def non_idem(_c, _r):
            return StepResult()

        assert non_idem._step_meta["idempotent"] is False

    def test_returns_original_function(self):
        @step(name="test.dec.original")
        async def original(_c, _r):
            return StepResult()

        # The decorator returns the same function (not a wrapper)
        assert callable(original)
        assert original.__name__ == "original"


# ---------------------------------------------------------------------------
# @async_step decorator
# ---------------------------------------------------------------------------


class TestAsyncStepDecorator:
    def test_registers_with_is_async(self):
        @async_step(name="test.dec.async_one")
        async def async_one(_c, _r):
            return StepResult()

        assert async_one._step_meta["is_async"] is True

    def test_completeness_check_none(self):
        @async_step(name="test.dec.async_no_check")
        async def no_check(_c, _r):
            return StepResult()

        assert no_check._step_meta["completeness_check"] is None

    def test_completeness_check_string(self):
        @async_step(name="test.dec.async_str_check", completeness_check="mymod.check")
        async def str_check(_c, _r):
            return StepResult()

        assert str_check._step_meta["completeness_check"] == "mymod.check"

    def test_completeness_check_callable(self):
        async def my_checker(_config, _results, _result):
            return True

        @async_step(name="test.dec.async_fn_check", completeness_check=my_checker)
        async def fn_check(_c, _r):
            return StepResult()

        check_name = fn_check._step_meta["completeness_check"]
        assert check_name is not None
        assert check_name in _STEP_REGISTRY
        assert _STEP_REGISTRY[check_name] is my_checker

    def test_poll_policy_default(self):
        @async_step(name="test.dec.async_poll_default")
        async def poll_default(_c, _r):
            return StepResult()

        assert isinstance(poll_default._step_meta["poll"], PollPolicy)

    def test_poll_policy_custom(self):
        policy = PollPolicy(interval=2.0, max_polls=5)

        @async_step(name="test.dec.async_poll_custom", poll=policy)
        async def poll_custom(_c, _r):
            return StepResult()

        assert poll_custom._step_meta["poll"].interval == 2.0
        assert poll_custom._step_meta["poll"].max_polls == 5


# ---------------------------------------------------------------------------
# get_handler
# ---------------------------------------------------------------------------


class TestGetHandler:
    def test_returns_registered_handler(self):
        @step(name="test.dec.get_registered")
        async def registered(_c, _r):
            return StepResult()

        handler = get_handler("test.dec.get_registered")
        assert handler is registered

    def test_dynamic_import_fallback(self):
        # Import a known stdlib function by dotted path
        handler = get_handler("os.path.exists")
        import os.path
        assert handler is os.path.exists

    def test_caches_imported_handler(self):
        # After dynamic import, should be in registry
        get_handler("os.path.isfile")
        assert "os.path.isfile" in _STEP_REGISTRY

    def test_raises_for_no_module_path(self):
        with pytest.raises(ValueError, match="Unknown handler"):
            get_handler("no_dots_here")
