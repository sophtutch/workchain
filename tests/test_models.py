"""Tests for workchain.models — enums, policies, Step, Workflow."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from workchain.models import (
    CheckResult,
    PollPolicy,
    RetryPolicy,
    Step,
    StepConfig,
    StepResult,
    StepStatus,
    Workflow,
    WorkflowStatus,
)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestStepStatus:
    def test_values(self):
        assert StepStatus.PENDING.value == "pending"
        assert StepStatus.SUBMITTED.value == "submitted"
        assert StepStatus.RUNNING.value == "running"
        assert StepStatus.BLOCKED.value == "blocked"
        assert StepStatus.COMPLETED.value == "completed"
        assert StepStatus.FAILED.value == "failed"

    def test_str_enum(self):
        assert StepStatus.PENDING == "pending"

    def test_all_members(self):
        assert len(StepStatus) == 6


class TestWorkflowStatus:
    def test_values(self):
        assert WorkflowStatus.PENDING.value == "pending"
        assert WorkflowStatus.RUNNING.value == "running"
        assert WorkflowStatus.COMPLETED.value == "completed"
        assert WorkflowStatus.FAILED.value == "failed"
        assert WorkflowStatus.NEEDS_REVIEW.value == "needs_review"

    def test_all_members(self):
        assert len(WorkflowStatus) == 6


# ---------------------------------------------------------------------------
# CheckResult
# ---------------------------------------------------------------------------


class TestCheckResult:
    def test_defaults(self):
        h = CheckResult()
        assert h.complete is False
        assert h.retry_after is None
        assert h.progress is None
        assert h.message is None

    def test_complete_true(self):
        h = CheckResult(complete=True, progress=1.0, message="done")
        assert h.complete is True
        assert h.progress == 1.0

    @pytest.mark.parametrize("val", [0.0, 0.5, 1.0])
    def test_valid_progress(self, val):
        h = CheckResult(progress=val)
        assert h.progress == val

    def test_progress_below_zero_raises(self):
        with pytest.raises(ValidationError):
            CheckResult(progress=-0.1)

    def test_progress_above_one_raises(self):
        with pytest.raises(ValidationError):
            CheckResult(progress=1.1)

    def test_progress_nan_raises(self):
        with pytest.raises(ValidationError, match="finite number"):
            CheckResult(progress=float("nan"))

    def test_progress_inf_raises(self):
        with pytest.raises(ValidationError, match="finite number"):
            CheckResult(progress=float("inf"))

    def test_progress_neg_inf_raises(self):
        with pytest.raises(ValidationError, match="finite number"):
            CheckResult(progress=float("-inf"))

    def test_progress_none_valid(self):
        h = CheckResult(progress=None)
        assert h.progress is None

    def test_retry_after(self):
        h = CheckResult(retry_after=10.0)
        assert h.retry_after == 10.0


# ---------------------------------------------------------------------------
# RetryPolicy
# ---------------------------------------------------------------------------


class TestRetryPolicy:
    def test_defaults(self):
        p = RetryPolicy()
        assert p.max_attempts == 3
        assert p.wait_seconds == 1.0
        assert p.wait_multiplier == 2.0
        assert p.wait_max == 60.0

    def test_custom_values(self):
        p = RetryPolicy(max_attempts=5, wait_seconds=0.5, wait_multiplier=1.5, wait_max=30.0)
        assert p.max_attempts == 5
        assert p.wait_multiplier == 1.5


# ---------------------------------------------------------------------------
# PollPolicy
# ---------------------------------------------------------------------------


class TestPollPolicy:
    def test_defaults(self):
        p = PollPolicy()
        assert p.interval == 5.0
        assert p.backoff_multiplier == 1.0
        assert p.max_interval == 60.0
        assert p.timeout == 3600.0
        assert p.max_polls == 0

    def test_custom_values(self):
        p = PollPolicy(interval=2.0, backoff_multiplier=2.0, max_polls=10)
        assert p.interval == 2.0
        assert p.max_polls == 10

    def test_negative_interval_rejected(self):
        with pytest.raises(ValidationError, match="interval must not be negative"):
            PollPolicy(interval=-1.0)

    def test_negative_backoff_multiplier_rejected(self):
        with pytest.raises(ValidationError, match="backoff_multiplier must not be negative"):
            PollPolicy(backoff_multiplier=-0.5)

    def test_negative_max_interval_rejected(self):
        with pytest.raises(ValidationError, match="max_interval must not be negative"):
            PollPolicy(max_interval=-10.0)

    def test_negative_timeout_rejected(self):
        with pytest.raises(ValidationError, match="timeout must not be negative"):
            PollPolicy(timeout=-1.0)

    def test_negative_max_polls_rejected(self):
        with pytest.raises(ValidationError, match="max_polls must not be negative"):
            PollPolicy(max_polls=-1)

    def test_zero_values_accepted(self):
        p = PollPolicy(interval=0, backoff_multiplier=0, max_interval=0, timeout=0, max_polls=0)
        assert p.interval == 0
        assert p.backoff_multiplier == 0
        assert p.max_interval == 0
        assert p.timeout == 0
        assert p.max_polls == 0


# ---------------------------------------------------------------------------
# StepConfig / StepResult
# ---------------------------------------------------------------------------


class TestStepConfig:
    def test_base_instantiation(self):
        c = StepConfig()
        assert c is not None

    def test_subclass(self):
        class MyConfig(StepConfig):
            x: int
        c = MyConfig(x=42)
        assert c.x == 42

    def test_json_round_trip(self):
        class MyConfig(StepConfig):
            x: int
        c = MyConfig(x=42)
        data = c.model_dump(mode="json")
        c2 = MyConfig.model_validate(data)
        assert c2.x == 42


class TestStepResult:
    def test_defaults(self):
        r = StepResult()
        assert r.error is None
        assert r.completed_at is None

    def test_with_error(self):
        r = StepResult(error="oops")
        assert r.error == "oops"

    def test_subclass(self):
        class MyResult(StepResult):
            count: int
        r = MyResult(count=5)
        assert r.count == 5
        assert r.error is None

    def test_json_round_trip(self):
        class MyResult(StepResult):
            count: int
        r = MyResult(count=5, completed_at=datetime.now(UTC))
        data = r.model_dump(mode="json")
        r2 = MyResult.model_validate(data)
        assert r2.count == 5


# ---------------------------------------------------------------------------
# Step
# ---------------------------------------------------------------------------


class TestStep:
    def test_defaults(self):
        s = Step(name="s1", handler="mod.func")
        assert s.status == StepStatus.PENDING
        assert s.config is None
        assert s.config_type is None
        assert s.result is None
        assert s.result_type is None
        assert s.step_timeout == 0
        assert s.attempt == 0
        assert s.is_async is False
        assert s.idempotent is True
        assert s.poll_count == 0

    def test_config_type_auto_set(self):
        class MyConfig(StepConfig):
            x: int
        s = Step(name="s1", handler="mod.func", config=MyConfig(x=1))
        assert s.config_type is not None
        assert "MyConfig" in s.config_type

    def test_config_type_not_set_for_base(self):
        s = Step(name="s1", handler="mod.func", config=StepConfig())
        assert s.config_type is None

    def test_config_type_not_overwritten(self):
        class MyConfig(StepConfig):
            x: int
        s = Step(name="s1", handler="mod.func", config=MyConfig(x=1), config_type="custom.path")
        assert s.config_type == "custom.path"

    def test_result_type_auto_set(self):
        class MyResult(StepResult):
            val: str
        s = Step(name="s1", handler="mod.func", result=MyResult(val="ok"))
        assert s.result_type is not None
        assert "MyResult" in s.result_type

    def test_result_type_not_set_for_base(self):
        s = Step(name="s1", handler="mod.func", result=StepResult())
        assert s.result_type is None

    def test_config_none_no_type_path(self):
        s = Step(name="s1", handler="mod.func")
        assert s.config_type is None

    def test_retry_policy_default(self):
        s = Step(name="s1", handler="mod.func")
        assert s.retry_policy.max_attempts == 3

    def test_negative_timeout_raises(self):
        with pytest.raises(ValidationError, match="step_timeout must not be negative"):
            Step(name="s1", handler="mod.func", step_timeout=-1.0)

    def test_zero_timeout_valid(self):
        s = Step(name="s1", handler="mod.func", step_timeout=0)
        assert s.step_timeout == 0

    def test_positive_timeout_valid(self):
        s = Step(name="s1", handler="mod.func", step_timeout=30.0)
        assert s.step_timeout == 30.0

    def test_poll_policy_none_for_sync_step(self):
        s = Step(name="s1", handler="mod.func")
        assert s.poll_policy is None

    def test_poll_policy_set_for_async_step(self):
        s = Step(name="s1", handler="mod.func", is_async=True, poll_policy=PollPolicy())
        assert s.poll_policy.interval == 5.0


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


class TestWorkflow:
    def test_defaults(self):
        w = Workflow(name="test")
        assert len(w.id) == 32
        assert w.status == WorkflowStatus.PENDING
        assert w.steps == []
        assert w.created_at is not None
        assert w.updated_at is not None

    def test_unique_ids(self):
        w1 = Workflow(name="a")
        w2 = Workflow(name="b")
        assert w1.id != w2.id

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (WorkflowStatus.PENDING, False),
            (WorkflowStatus.RUNNING, False),
            (WorkflowStatus.COMPLETED, True),
            (WorkflowStatus.FAILED, True),
            (WorkflowStatus.NEEDS_REVIEW, True),
        ],
    )
    def test_is_terminal(self, status, expected):
        w = Workflow(name="test", status=status)
        assert w.is_terminal() is expected

    def test_duplicate_step_names_raises(self):
        with pytest.raises(ValidationError, match="Step names must be unique"):
            Workflow(
                name="test",
                steps=[
                    Step(name="same", handler="mod.func"),
                    Step(name="same", handler="mod.func2"),
                ],
            )

    def test_unique_step_names_valid(self):
        w = Workflow(
            name="test",
            steps=[
                Step(name="s1", handler="mod.func"),
                Step(name="s2", handler="mod.func2"),
            ],
        )
        assert len(w.steps) == 2

    def test_with_steps(self):
        w = Workflow(
            name="test",
            steps=[
                Step(name="s1", handler="mod.func"),
                Step(name="s2", handler="mod.func2"),
            ],
        )
        assert len(w.steps) == 2
        assert w.steps[0].name == "s1"


# ---------------------------------------------------------------------------
# Step — depends_on and step-level lock fields
# ---------------------------------------------------------------------------


class TestStepDependsOn:
    def test_depends_on_default_is_none(self):
        s = Step(name="s1", handler="mod.func")
        assert s.depends_on is None

    def test_explicit_empty_list(self):
        s = Step(name="s1", handler="mod.func", depends_on=[])
        assert s.depends_on == []

    def test_explicit_dependencies(self):
        s = Step(name="s1", handler="mod.func", depends_on=["a", "b"])
        assert s.depends_on == ["a", "b"]


class TestStepLockFields:
    def test_defaults(self):
        s = Step(name="s1", handler="mod.func")
        assert s.locked_by is None
        assert s.lock_expires_at is None
        assert s.fence_token == 0


# ---------------------------------------------------------------------------
# Workflow — depends_on resolution and validation
# ---------------------------------------------------------------------------


class TestWorkflowDependsOnResolution:
    """Tests for the automatic resolution of None → sequential defaults."""

    def test_empty_workflow(self):
        w = Workflow(name="test")
        assert w.steps == []

    def test_single_step_gets_empty_depends_on(self):
        w = Workflow(name="test", steps=[Step(name="a", handler="mod.func")])
        assert w.steps[0].depends_on == []

    def test_sequential_default_two_steps(self):
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func"),
                Step(name="b", handler="mod.func"),
            ],
        )
        assert w.steps[0].depends_on == []
        assert w.steps[1].depends_on == ["a"]

    def test_sequential_default_three_steps(self):
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func"),
                Step(name="b", handler="mod.func"),
                Step(name="c", handler="mod.func"),
            ],
        )
        assert w.steps[0].depends_on == []
        assert w.steps[1].depends_on == ["a"]
        assert w.steps[2].depends_on == ["b"]

    def test_explicit_depends_on_preserved(self):
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[]),
                Step(name="b", handler="mod.func", depends_on=[]),
                Step(name="c", handler="mod.func", depends_on=["a", "b"]),
            ],
        )
        assert w.steps[0].depends_on == []
        assert w.steps[1].depends_on == []
        assert w.steps[2].depends_on == ["a", "b"]

    def test_mixed_none_and_explicit(self):
        """Steps with None get sequential default; explicit ones are preserved."""
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func"),  # None → []
                Step(name="b", handler="mod.func", depends_on=[]),  # explicit root
                Step(name="c", handler="mod.func"),  # None → ["b"]
            ],
        )
        assert w.steps[0].depends_on == []
        assert w.steps[1].depends_on == []
        assert w.steps[2].depends_on == ["b"]


class TestWorkflowDependsOnValidation:
    """Tests for dependency validation: unknown names, self-refs, cycles."""

    def test_unknown_dependency_raises(self):
        with pytest.raises(ValidationError, match="depends on unknown step 'z'"):
            Workflow(
                name="test",
                steps=[
                    Step(name="a", handler="mod.func", depends_on=[]),
                    Step(name="b", handler="mod.func", depends_on=["z"]),
                ],
            )

    def test_self_reference_raises(self):
        with pytest.raises(ValidationError, match="cannot depend on itself"):
            Workflow(
                name="test",
                steps=[
                    Step(name="a", handler="mod.func", depends_on=["a"]),
                ],
            )

    def test_two_step_cycle_raises(self):
        with pytest.raises(ValidationError, match="Dependency cycle detected"):
            Workflow(
                name="test",
                steps=[
                    Step(name="a", handler="mod.func", depends_on=["b"]),
                    Step(name="b", handler="mod.func", depends_on=["a"]),
                ],
            )

    def test_three_step_cycle_raises(self):
        with pytest.raises(ValidationError, match="Dependency cycle detected"):
            Workflow(
                name="test",
                steps=[
                    Step(name="a", handler="mod.func", depends_on=["c"]),
                    Step(name="b", handler="mod.func", depends_on=["a"]),
                    Step(name="c", handler="mod.func", depends_on=["b"]),
                ],
            )

    def test_valid_diamond_pattern(self):
        """A → (B, C) → D is a valid DAG."""
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[]),
                Step(name="b", handler="mod.func", depends_on=["a"]),
                Step(name="c", handler="mod.func", depends_on=["a"]),
                Step(name="d", handler="mod.func", depends_on=["b", "c"]),
            ],
        )
        assert len(w.steps) == 4

    def test_valid_all_roots(self):
        """All steps independent (no dependencies)."""
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[]),
                Step(name="b", handler="mod.func", depends_on=[]),
                Step(name="c", handler="mod.func", depends_on=[]),
            ],
        )
        assert all(s.depends_on == [] for s in w.steps)


# ---------------------------------------------------------------------------
# Workflow — decorator metadata propagation onto Step
# ---------------------------------------------------------------------------


class TestDecoratorMetadataPropagation:
    """Tests that ``@step`` / ``@async_step`` arguments flow onto Step.

    The Workflow validator ``_resolve_and_validate_depends_on`` copies
    decorator-declared values into each Step at construction time when the
    caller did not explicitly set them (detected via ``model_fields_set``).
    Skipped for non-PENDING workflows so Mongo reloads are never mutated.
    """

    def test_picks_up_decorator_retry_policy(self):
        from workchain.decorators import async_step, step

        @step(retry=RetryPolicy(max_attempts=7, wait_seconds=0.25))
        async def custom_retry(_c, _r):
            return StepResult()

        wf = Workflow(
            name="t",
            steps=[Step(name="s", handler=custom_retry._step_meta["handler"])],
        )
        assert wf.steps[0].retry_policy.max_attempts == 7
        assert wf.steps[0].retry_policy.wait_seconds == 0.25

        @async_step(
            retry=RetryPolicy(max_attempts=4),
            completeness_check="pkg.mod.check",
        )
        async def custom_async_retry(_c, _r):
            return StepResult()

        wf2 = Workflow(
            name="t2",
            steps=[Step(name="s", handler=custom_async_retry._step_meta["handler"])],
        )
        assert wf2.steps[0].retry_policy.max_attempts == 4

    def test_picks_up_decorator_poll_policy(self):
        from workchain.decorators import async_step

        @async_step(
            poll=PollPolicy(interval=45.0, max_polls=100),
            completeness_check="pkg.mod.check",
        )
        async def custom_poll(_c, _r):
            return StepResult()

        wf = Workflow(
            name="t",
            steps=[Step(name="s", handler=custom_poll._step_meta["handler"])],
        )
        assert wf.steps[0].poll_policy is not None
        assert wf.steps[0].poll_policy.interval == 45.0
        assert wf.steps[0].poll_policy.max_polls == 100

    def test_picks_up_decorator_is_async_and_completeness_check(self):
        from workchain.decorators import async_step

        @async_step(completeness_check="pkg.mod.check")
        async def async_handler(_c, _r):
            return StepResult()

        wf = Workflow(
            name="t",
            steps=[Step(name="s", handler=async_handler._step_meta["handler"])],
        )
        assert wf.steps[0].is_async is True
        assert wf.steps[0].completeness_check == "pkg.mod.check"

    def test_picks_up_decorator_idempotent_false(self):
        from workchain.decorators import step

        @step(idempotent=False)
        async def non_idem(_c, _r):
            return StepResult()

        wf = Workflow(
            name="t",
            steps=[Step(name="s", handler=non_idem._step_meta["handler"])],
        )
        assert wf.steps[0].idempotent is False

    def test_picks_up_decorator_depends_on(self):
        """Handler-declared depends_on is copied onto the step, preempting
        the sequential-default fallback.
        """
        from workchain.decorators import step

        @step()
        async def first(_c, _r):
            return StepResult()

        @step()
        async def second(_c, _r):
            return StepResult()

        @step(depends_on=["first"])
        async def third(_c, _r):
            return StepResult()

        wf = Workflow(
            name="t",
            steps=[
                Step(name="first", handler=first._step_meta["handler"]),
                Step(name="second", handler=second._step_meta["handler"]),
                # third's handler declares depends_on=["first"]; this
                # should override the sequential default that would have
                # made it depend on "second".
                Step(name="third", handler=third._step_meta["handler"]),
            ],
        )
        assert wf.steps[2].depends_on == ["first"]

    def test_explicit_retry_policy_wins(self):
        """Explicit ``retry_policy=...`` on Step wins over decorator."""
        from workchain.decorators import step

        @step(retry=RetryPolicy(max_attempts=7))
        async def override_me(_c, _r):
            return StepResult()

        wf = Workflow(
            name="t",
            steps=[
                Step(
                    name="s",
                    handler=override_me._step_meta["handler"],
                    retry_policy=RetryPolicy(max_attempts=2),
                ),
            ],
        )
        assert wf.steps[0].retry_policy.max_attempts == 2

    def test_explicit_depends_on_wins(self):
        """Explicit ``depends_on=...`` on Step wins over decorator-declared deps."""
        from workchain.decorators import step

        @step()
        async def root_a(_c, _r):
            return StepResult()

        @step(depends_on=["root_a"])
        async def dependent(_c, _r):
            return StepResult()

        wf = Workflow(
            name="t",
            steps=[
                Step(name="root_a", handler=root_a._step_meta["handler"]),
                Step(name="other", handler=root_a._step_meta["handler"]),
                # Explicit override: caller wants dependent to depend on
                # "other", not on handler-declared "root_a". This still
                # satisfies the required-deps check because "other" is
                # not in the required list — wait, actually the handler
                # requires "root_a" and caller passes ["other"], so this
                # SHOULD raise. Test moved to a failing-case test below.
                Step(
                    name="dependent",
                    handler=dependent._step_meta["handler"],
                    depends_on=["root_a", "other"],
                ),
            ],
        )
        # Explicit depends_on on the step is preserved verbatim.
        assert wf.steps[2].depends_on == ["root_a", "other"]

    def test_mongo_reload_preserves_stored_fields(self):
        """When status != PENDING (Mongo reload), the validator must not
        mutate mirrored fields. The decorator policy is ignored and
        whatever was stored on the Step is kept as-is.
        """
        from workchain.decorators import step

        @step(retry=RetryPolicy(max_attempts=7, wait_seconds=0.25))
        async def some_handler(_c, _r):
            return StepResult()

        # Simulate a reload: workflow is RUNNING with a step that has an
        # explicitly different retry policy from the decorator.
        wf = Workflow(
            name="t",
            status=WorkflowStatus.RUNNING,
            steps=[
                Step(
                    name="s",
                    handler=some_handler._step_meta["handler"],
                    retry_policy=RetryPolicy(max_attempts=99),
                    idempotent=False,
                ),
            ],
        )
        # Both the explicit override AND any "unset" field stay untouched.
        assert wf.steps[0].retry_policy.max_attempts == 99
        assert wf.steps[0].idempotent is False
        # is_async was not set and not propagated because status != PENDING.
        assert wf.steps[0].is_async is False

    def test_handler_not_registered_falls_back_to_defaults(self):
        """Synthetic handler paths (common in tests) must not trip the
        validator. The Step keeps its field defaults.
        """
        wf = Workflow(
            name="t",
            steps=[Step(name="s", handler="some.unknown.handler")],
        )
        assert wf.steps[0].retry_policy.max_attempts == RetryPolicy().max_attempts
        assert wf.steps[0].poll_policy is None
        assert wf.steps[0].is_async is False
        assert wf.steps[0].idempotent is True

    def test_required_deps_validation_still_fires_on_explicit_mismatch(self):
        """If the caller explicitly passes ``depends_on`` omitting a
        handler-required dep, the validator still raises (phase 3b).
        """
        from workchain.decorators import step

        @step()
        async def required_dep(_c, _r):
            return StepResult()

        @step(depends_on=["required_dep"])
        async def dependent(_c, _r):
            return StepResult()

        with pytest.raises(ValidationError, match="requires dependencies"):
            Workflow(
                name="t",
                steps=[
                    Step(
                        name="required_dep",
                        handler=required_dep._step_meta["handler"],
                    ),
                    Step(
                        name="other",
                        handler=required_dep._step_meta["handler"],
                    ),
                    # Handler declares depends_on=["required_dep"] but
                    # caller explicitly passes ["other"] — missing "required_dep".
                    Step(
                        name="dependent",
                        handler=dependent._step_meta["handler"],
                        depends_on=["other"],
                    ),
                ],
            )


# ---------------------------------------------------------------------------
# Workflow — dependency-aware helpers
# ---------------------------------------------------------------------------


class TestWorkflowReadySteps:
    def test_all_pending_roots_are_ready(self):
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[]),
                Step(name="b", handler="mod.func", depends_on=[]),
            ],
        )
        ready = w.ready_steps()
        assert [s.name for s in ready] == ["a", "b"]

    def test_dependent_step_not_ready(self):
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[]),
                Step(name="b", handler="mod.func", depends_on=["a"]),
            ],
        )
        ready = w.ready_steps()
        assert [s.name for s in ready] == ["a"]

    def test_dependent_step_ready_after_dependency_completed(self):
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[], status=StepStatus.COMPLETED),
                Step(name="b", handler="mod.func", depends_on=["a"]),
            ],
        )
        ready = w.ready_steps()
        assert [s.name for s in ready] == ["b"]

    def test_diamond_ready_steps(self):
        """After A completes, both B and C become ready. D is not ready yet."""
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[], status=StepStatus.COMPLETED),
                Step(name="b", handler="mod.func", depends_on=["a"]),
                Step(name="c", handler="mod.func", depends_on=["a"]),
                Step(name="d", handler="mod.func", depends_on=["b", "c"]),
            ],
        )
        ready = w.ready_steps()
        assert [s.name for s in ready] == ["b", "c"]

    def test_diamond_d_ready_when_b_and_c_completed(self):
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[], status=StepStatus.COMPLETED),
                Step(name="b", handler="mod.func", depends_on=["a"], status=StepStatus.COMPLETED),
                Step(name="c", handler="mod.func", depends_on=["a"], status=StepStatus.COMPLETED),
                Step(name="d", handler="mod.func", depends_on=["b", "c"]),
            ],
        )
        ready = w.ready_steps()
        assert [s.name for s in ready] == ["d"]

    def test_locked_step_not_ready(self):
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[], locked_by="instance-1"),
            ],
        )
        ready = w.ready_steps()
        assert ready == []

    def test_completed_step_not_ready(self):
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[], status=StepStatus.COMPLETED),
            ],
        )
        ready = w.ready_steps()
        assert ready == []

    def test_failed_dependency_blocks_dependent(self):
        """If a dependency failed, the dependent step is not ready."""
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[], status=StepStatus.FAILED),
                Step(name="b", handler="mod.func", depends_on=["a"]),
            ],
        )
        ready = w.ready_steps()
        assert ready == []

    def test_no_steps(self):
        w = Workflow(name="test")
        assert w.ready_steps() == []


class TestWorkflowPollableSteps:
    def test_blocked_step_past_poll_time(self):
        past = datetime(2020, 1, 1, tzinfo=UTC)
        w = Workflow(
            name="test",
            steps=[
                Step(
                    name="a", handler="mod.func", depends_on=[],
                    status=StepStatus.BLOCKED, next_poll_at=past,
                ),
            ],
        )
        pollable = w.pollable_steps()
        assert [s.name for s in pollable] == ["a"]

    def test_blocked_step_future_poll_not_pollable(self):
        future = datetime(2099, 1, 1, tzinfo=UTC)
        w = Workflow(
            name="test",
            steps=[
                Step(
                    name="a", handler="mod.func", depends_on=[],
                    status=StepStatus.BLOCKED, next_poll_at=future,
                ),
            ],
        )
        assert w.pollable_steps() == []

    def test_locked_blocked_step_not_pollable(self):
        past = datetime(2020, 1, 1, tzinfo=UTC)
        w = Workflow(
            name="test",
            steps=[
                Step(
                    name="a", handler="mod.func", depends_on=[],
                    status=StepStatus.BLOCKED, next_poll_at=past,
                    locked_by="instance-1",
                ),
            ],
        )
        assert w.pollable_steps() == []


class TestWorkflowActiveSteps:
    def test_active_steps(self):
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[], status=StepStatus.COMPLETED),
                Step(name="b", handler="mod.func", depends_on=["a"], status=StepStatus.RUNNING),
                Step(name="c", handler="mod.func", depends_on=["a"], status=StepStatus.BLOCKED),
                Step(name="d", handler="mod.func", depends_on=["b", "c"], status=StepStatus.PENDING),
            ],
        )
        active = w.active_steps()
        assert [s.name for s in active] == ["b", "c"]


class TestWorkflowTerminalHelpers:
    def test_all_steps_terminal(self):
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[], status=StepStatus.COMPLETED),
                Step(name="b", handler="mod.func", depends_on=["a"], status=StepStatus.FAILED),
            ],
        )
        assert w.all_steps_terminal() is True

    def test_all_steps_terminal_empty(self):
        w = Workflow(name="test")
        assert w.all_steps_terminal() is False

    def test_not_all_terminal(self):
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[], status=StepStatus.COMPLETED),
                Step(name="b", handler="mod.func", depends_on=["a"], status=StepStatus.PENDING),
            ],
        )
        assert w.all_steps_terminal() is False

    def test_all_steps_completed(self):
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[], status=StepStatus.COMPLETED),
                Step(name="b", handler="mod.func", depends_on=["a"], status=StepStatus.COMPLETED),
            ],
        )
        assert w.all_steps_completed() is True

    def test_all_steps_completed_empty(self):
        w = Workflow(name="test")
        assert w.all_steps_completed() is False

    def test_has_failed_step(self):
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[], status=StepStatus.COMPLETED),
                Step(name="b", handler="mod.func", depends_on=["a"], status=StepStatus.FAILED),
            ],
        )
        assert w.has_failed_step() is True

    def test_no_failed_step(self):
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[], status=StepStatus.COMPLETED),
                Step(name="b", handler="mod.func", depends_on=["a"], status=StepStatus.COMPLETED),
            ],
        )
        assert w.has_failed_step() is False


class TestWorkflowStepByName:
    def test_found(self):
        w = Workflow(
            name="test",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[]),
                Step(name="b", handler="mod.func", depends_on=["a"]),
            ],
        )
        assert w.step_by_name("b") is w.steps[1]

    def test_not_found(self):
        w = Workflow(
            name="test",
            steps=[Step(name="a", handler="mod.func", depends_on=[])],
        )
        assert w.step_by_name("z") is None
