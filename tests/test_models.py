"""Tests for workchain.models — enums, policies, Step, Workflow."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from workchain.models import (
    PollHint,
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
# PollHint
# ---------------------------------------------------------------------------


class TestPollHint:
    def test_defaults(self):
        h = PollHint()
        assert h.complete is False
        assert h.retry_after is None
        assert h.progress is None
        assert h.message is None

    def test_complete_true(self):
        h = PollHint(complete=True, progress=1.0, message="done")
        assert h.complete is True
        assert h.progress == 1.0

    @pytest.mark.parametrize("val", [0.0, 0.5, 1.0])
    def test_valid_progress(self, val):
        h = PollHint(progress=val)
        assert h.progress == val

    def test_progress_below_zero_raises(self):
        with pytest.raises(ValidationError):
            PollHint(progress=-0.1)

    def test_progress_above_one_raises(self):
        with pytest.raises(ValidationError):
            PollHint(progress=1.1)

    def test_progress_none_valid(self):
        h = PollHint(progress=None)
        assert h.progress is None

    def test_retry_after(self):
        h = PollHint(retry_after=10.0)
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

    def test_poll_policy_default(self):
        s = Step(name="s1", handler="mod.func")
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
        assert w.current_step_index == 0
        assert w.locked_by is None
        assert w.lock_expires_at is None
        assert w.fence_token == 0
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
