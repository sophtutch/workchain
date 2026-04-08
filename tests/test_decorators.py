"""Tests for workchain.decorators — @step, @async_step, @completeness_check, get_handler."""

from __future__ import annotations

import pytest

from workchain.decorators import _STEP_REGISTRY, async_step, completeness_check, get_handler, step
from workchain.models import PollPolicy, RetryPolicy, StepResult

# ---------------------------------------------------------------------------
# @step decorator
# ---------------------------------------------------------------------------


class TestStepDecorator:
    def test_registers_handler(self):
        @step()
        async def my_step(_c, _r):
            return StepResult()

        expected = f"{my_step.__module__}.{my_step.__qualname__}"
        assert expected in _STEP_REGISTRY
        assert _STEP_REGISTRY[expected] is my_step

    def test_auto_name(self):
        @step()
        async def auto_named(_c, _r):
            return StepResult()

        expected = f"{auto_named.__module__}.{auto_named.__qualname__}"
        assert expected in _STEP_REGISTRY
        assert auto_named._step_meta["handler"] == expected

    def test_meta_fields(self):
        @step()
        async def meta_step(_c, _r):
            return StepResult()

        meta = meta_step._step_meta
        assert meta["is_async"] is False
        assert meta["idempotent"] is True
        assert meta["needs_context"] is False
        assert isinstance(meta["retry"], RetryPolicy)

    def test_custom_retry(self):
        policy = RetryPolicy(max_attempts=5, wait_seconds=0.5)

        @step(retry=policy)
        async def custom_retry_step(_c, _r):
            return StepResult()

        assert custom_retry_step._step_meta["retry"].max_attempts == 5

    def test_idempotent_false(self):
        @step(idempotent=False)
        async def non_idem(_c, _r):
            return StepResult()

        assert non_idem._step_meta["idempotent"] is False

    def test_returns_original_function(self):
        @step()
        async def original(_c, _r):
            return StepResult()

        # The decorator returns the same function (not a wrapper)
        assert callable(original)
        assert original.__name__ == "original"

    def test_step_needs_context_default(self):
        @step()
        async def no_ctx(_c, _r):
            return StepResult()

        assert no_ctx._step_meta["needs_context"] is False

    def test_step_needs_context_true(self):
        @step(needs_context=True)
        async def with_ctx(_c, _r, _ctx):
            return StepResult()

        assert with_ctx._step_meta["needs_context"] is True


# ---------------------------------------------------------------------------
# @async_step decorator
# ---------------------------------------------------------------------------


class TestAsyncStepDecorator:
    def test_registers_with_is_async(self):
        @async_step()
        async def async_one(_c, _r):
            return StepResult()

        assert async_one._step_meta["is_async"] is True

    def test_completeness_check_none(self):
        @async_step()
        async def no_check(_c, _r):
            return StepResult()

        assert no_check._step_meta["completeness_check"] is None

    def test_completeness_check_string(self):
        @async_step(completeness_check="mymod.check")
        async def str_check(_c, _r):
            return StepResult()

        assert str_check._step_meta["completeness_check"] == "mymod.check"

    def test_completeness_check_callable(self):
        async def my_checker(_config, _results, _result):
            return True

        @async_step(completeness_check=my_checker)
        async def fn_check(_c, _r):
            return StepResult()

        check_name = fn_check._step_meta["completeness_check"]
        assert check_name is not None
        assert check_name in _STEP_REGISTRY
        assert _STEP_REGISTRY[check_name] is my_checker

    def test_poll_policy_default(self):
        @async_step()
        async def poll_default(_c, _r):
            return StepResult()

        assert isinstance(poll_default._step_meta["poll"], PollPolicy)

    def test_poll_policy_custom(self):
        policy = PollPolicy(interval=2.0, max_polls=5)

        @async_step(poll=policy)
        async def poll_custom(_c, _r):
            return StepResult()

        assert poll_custom._step_meta["poll"].interval == 2.0
        assert poll_custom._step_meta["poll"].max_polls == 5


# ---------------------------------------------------------------------------
# @completeness_check decorator
# ---------------------------------------------------------------------------


class TestCompletenessCheckDecorator:
    def test_registers_completeness_check(self):
        @completeness_check()
        async def my_check(_config, _results, _result):
            return True

        expected = f"{my_check.__module__}.{my_check.__qualname__}"
        assert expected in _STEP_REGISTRY
        assert _STEP_REGISTRY[expected] is my_check

    def test_completeness_check_meta(self):
        @completeness_check()
        async def check_meta(_config, _results, _result):
            return True

        meta = check_meta._step_meta
        assert meta["is_completeness_check"] is True
        assert meta["needs_context"] is False

    def test_completeness_check_with_context(self):
        @completeness_check(needs_context=True)
        async def check_ctx(_config, _results, _result, _ctx):
            return True

        assert check_ctx._step_meta["needs_context"] is True


# ---------------------------------------------------------------------------
# get_handler
# ---------------------------------------------------------------------------


class TestGetHandler:
    def test_returns_registered_handler(self):
        @step()
        async def registered(_c, _r):
            return StepResult()

        handler_name = registered._step_meta["handler"]
        handler = get_handler(handler_name)
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

    def test_no_dot_suggests_close_match(self):
        @step()
        async def my_unique_func(_c, _r):
            return StepResult()

        handler_name = my_unique_func._step_meta["handler"]
        short_name = handler_name.rsplit(".", 1)[-1]
        with pytest.raises(ValueError, match="Did you mean"):
            get_handler(short_name)

    def test_no_dot_lists_registered_when_no_match(self):
        # "zzz_no_match" won't match any registered handler suffix
        with pytest.raises(ValueError, match="Registered handlers"):
            get_handler("zzz_no_match")

    def test_module_not_found_includes_path(self):
        with pytest.raises(ValueError, match="Handler module not found.*'nonexistent.mod'"):
            get_handler("nonexistent.mod.func")

    def test_func_not_in_module_lists_callables(self):
        with pytest.raises(ValueError, match=r"(?s)not found in module.*Available callables"):
            get_handler("os.path.zzz_nonexistent")
