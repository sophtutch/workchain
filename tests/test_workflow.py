"""Tests for workchain.workflow — Workflow builder and DAG validation."""

from __future__ import annotations

import pytest

from tests.conftest import AddConfig, AddStep, FailingStep, NoOpStep
from workchain import DependencyFailurePolicy, StepStatus, Workflow, WorkflowStatus
from workchain.exceptions import WorkflowValidationError
from workchain.workflow import StepDefinition


class TestStepDefinition:
    def test_step_type_from_class_name(self):
        step = AddStep(config=AddConfig(a=1, b=2))
        sd = StepDefinition(step_id="add", step=step)
        assert sd.step_type == "AddStep"

    def test_defaults(self):
        sd = StepDefinition(step_id="x", step=NoOpStep())
        assert sd.depends_on == []
        assert sd.on_dependency_failure == DependencyFailurePolicy.FAIL


class TestWorkflowBuilder:
    def test_add_single_step(self):
        wf = Workflow(name="test", version="1.0")
        wf.add("s1", NoOpStep())
        defs = wf.get_step_definitions()
        assert len(defs) == 1
        assert defs[0].step_id == "s1"

    def test_fluent_chaining(self):
        wf = Workflow(name="test").add("a", NoOpStep()).add("b", NoOpStep(), depends_on=["a"])
        assert len(wf.get_step_definitions()) == 2

    def test_duplicate_step_id_raises(self):
        wf = Workflow(name="test").add("s1", NoOpStep())
        with pytest.raises(WorkflowValidationError, match="Duplicate step_id"):
            wf.add("s1", NoOpStep())

    def test_get_step_definition(self):
        wf = Workflow(name="test").add("s1", NoOpStep())
        assert wf.get_step_definition("s1") is not None
        assert wf.get_step_definition("missing") is None


class TestDAGValidation:
    def test_valid_linear_dag(self):
        wf = (
            Workflow(name="test")
            .add("a", NoOpStep())
            .add("b", NoOpStep(), depends_on=["a"])
            .add("c", NoOpStep(), depends_on=["b"])
        )
        wf.validate()  # should not raise

    def test_valid_diamond_dag(self):
        wf = (
            Workflow(name="test")
            .add("a", NoOpStep())
            .add("b", NoOpStep(), depends_on=["a"])
            .add("c", NoOpStep(), depends_on=["a"])
            .add("d", NoOpStep(), depends_on=["b", "c"])
        )
        wf.validate()

    def test_unknown_dependency_raises(self):
        wf = Workflow(name="test").add("a", NoOpStep(), depends_on=["missing"])
        with pytest.raises(WorkflowValidationError, match="unknown step 'missing'"):
            wf.validate()

    def test_cycle_raises(self):
        wf = Workflow(name="test").add("a", NoOpStep(), depends_on=["b"]).add("b", NoOpStep(), depends_on=["a"])
        with pytest.raises(WorkflowValidationError, match="cycle"):
            wf.validate()

    def test_self_cycle_raises(self):
        wf = Workflow(name="test").add("a", NoOpStep(), depends_on=["a"])
        with pytest.raises(WorkflowValidationError, match="cycle"):
            wf.validate()

    def test_no_steps_is_valid(self):
        wf = Workflow(name="empty")
        wf.validate()


class TestCreateRun:
    def test_creates_workflow_run(self):
        wf = Workflow(name="pipeline", version="2.0").add("a", NoOpStep()).add("b", NoOpStep(), depends_on=["a"])
        run = wf.create_run()
        assert run.workflow_name == "pipeline"
        assert run.workflow_version == "2.0"
        # Status is computed: step "a" is PENDING with no deps → ready → RUNNING
        assert run.status == WorkflowStatus.RUNNING
        assert run.needs_work_after is not None
        assert len(run.steps) == 2

    def test_step_runs_have_correct_metadata(self):
        wf = (
            Workflow(name="test")
            .add("s1", NoOpStep())
            .add("s2", FailingStep(), depends_on=["s1"], on_dependency_failure=DependencyFailurePolicy.SKIP)
        )
        run = wf.create_run()
        s1 = run.get_step("s1")
        s2 = run.get_step("s2")
        assert s1.step_type == "NoOpStep"
        assert s1.depends_on == []
        assert s2.step_type == "FailingStep"
        assert s2.depends_on == ["s1"]
        assert s2.on_dependency_failure == DependencyFailurePolicy.SKIP

    def test_all_steps_start_pending(self):
        wf = Workflow(name="test").add("a", NoOpStep()).add("b", NoOpStep())
        run = wf.create_run()
        for step in run.steps:
            assert step.status == StepStatus.PENDING

    def test_create_run_validates(self):
        wf = Workflow(name="test").add("a", NoOpStep(), depends_on=["missing"])
        with pytest.raises(WorkflowValidationError):
            wf.create_run()
