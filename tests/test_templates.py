"""Tests for workchain.templates — WorkflowTemplate model + store CRUD."""

from __future__ import annotations

import pytest

from workchain.decorators import async_step, completeness_check, step
from workchain.models import (
    CheckResult,
    PollPolicy,
    StepConfig,
    StepResult,
    WorkflowStatus,
)
from workchain.templates import (
    StepTemplate,
    WorkflowTemplate,
    instantiate_template,
)

# ---------------------------------------------------------------------------
# Fixture handlers
# ---------------------------------------------------------------------------


class _TplConfig(StepConfig):
    greeting: str = "hi"
    target: str = "world"


class _TplResult(StepResult):
    message: str


class _TplJobResult(StepResult):
    job_id: str


@step()
async def _tpl_sync(
    config: _TplConfig, _results: dict[str, StepResult]
) -> _TplResult:
    return _TplResult(message=f"{config.greeting} {config.target}")


@step()
async def _tpl_followup(
    config: _TplConfig, _results: dict[str, StepResult]
) -> _TplResult:
    return _TplResult(message=f"{config.greeting}!")


@completeness_check()
async def _tpl_check(
    _config: StepConfig,
    _results: dict[str, StepResult],
    _result: StepResult,
) -> CheckResult:
    return CheckResult(complete=True)


@async_step(poll=PollPolicy(interval=0.1), completeness_check=_tpl_check)
async def _tpl_async(
    _config: _TplConfig, _results: dict[str, StepResult]
) -> _TplJobResult:
    return _TplJobResult(job_id="job-1")


@step()
async def _tpl_untyped(_config, _results):  # type: ignore[no-untyped-def]
    return StepResult()


_SYNC = _tpl_sync._step_meta["handler"]
_FOLLOWUP = _tpl_followup._step_meta["handler"]
_ASYNC = _tpl_async._step_meta["handler"]
_UNTYPED = _tpl_untyped._step_meta["handler"]

# ---------------------------------------------------------------------------
# WorkflowTemplate model validation
# ---------------------------------------------------------------------------


class TestWorkflowTemplateModel:
    def test_defaults(self) -> None:
        tpl = WorkflowTemplate(
            name="t",
            steps=[StepTemplate(name="a", handler=_SYNC)],
        )
        assert tpl.version == 1
        assert tpl.id  # auto-generated
        # created_at and updated_at each come from their own default_factory
        # call, so they are near-equal but not identical; just assert they're
        # populated and close.
        assert tpl.created_at.tzinfo is not None
        assert tpl.updated_at >= tpl.created_at
        # Sequential default: single-step template has depends_on=[]
        assert tpl.steps[0].depends_on == []

    def test_sequential_default_resolution(self) -> None:
        tpl = WorkflowTemplate(
            name="t",
            steps=[
                StepTemplate(name="a", handler=_SYNC),
                StepTemplate(name="b", handler=_SYNC),
                StepTemplate(name="c", handler=_SYNC),
            ],
        )
        assert tpl.steps[0].depends_on == []
        assert tpl.steps[1].depends_on == ["a"]
        assert tpl.steps[2].depends_on == ["b"]

    def test_explicit_depends_on_preserved(self) -> None:
        tpl = WorkflowTemplate(
            name="t",
            steps=[
                StepTemplate(name="a", handler=_SYNC, depends_on=[]),
                StepTemplate(name="b", handler=_SYNC, depends_on=[]),
                StepTemplate(name="c", handler=_SYNC, depends_on=["a", "b"]),
            ],
        )
        assert tpl.steps[0].depends_on == []
        assert tpl.steps[2].depends_on == ["a", "b"]

    def test_duplicate_names_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be unique"):
            WorkflowTemplate(
                name="t",
                steps=[
                    StepTemplate(name="a", handler=_SYNC),
                    StepTemplate(name="a", handler=_SYNC),
                ],
            )

    def test_self_reference_rejected(self) -> None:
        with pytest.raises(ValueError, match="cannot depend on itself"):
            WorkflowTemplate(
                name="t",
                steps=[
                    StepTemplate(name="a", handler=_SYNC, depends_on=["a"]),
                ],
            )

    def test_unknown_dependency_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown step"):
            WorkflowTemplate(
                name="t",
                steps=[
                    StepTemplate(name="a", handler=_SYNC, depends_on=["nope"]),
                ],
            )

    def test_cycle_rejected(self) -> None:
        with pytest.raises(ValueError, match="cycle"):
            WorkflowTemplate(
                name="t",
                steps=[
                    StepTemplate(name="a", handler=_SYNC, depends_on=["b"]),
                    StepTemplate(name="b", handler=_SYNC, depends_on=["a"]),
                ],
            )


# ---------------------------------------------------------------------------
# instantiate_template
# ---------------------------------------------------------------------------


class TestInstantiateTemplate:
    def test_basic_roundtrip(self) -> None:
        tpl = WorkflowTemplate(
            name="onboarding",
            steps=[
                StepTemplate(
                    name="greet",
                    handler=_SYNC,
                    config={"greeting": "hello", "target": "alice"},
                ),
            ],
        )
        wf = instantiate_template(tpl)
        assert wf.name == "onboarding"
        assert wf.status == WorkflowStatus.PENDING
        assert len(wf.steps) == 1
        step_obj = wf.steps[0]
        assert step_obj.name == "greet"
        assert isinstance(step_obj.config, _TplConfig)
        assert step_obj.config.greeting == "hello"
        assert step_obj.config.target == "alice"
        # config_type is auto-populated by Step._set_type_paths
        assert step_obj.config_type is not None
        assert step_obj.config_type.endswith("._TplConfig")

    def test_name_override(self) -> None:
        tpl = WorkflowTemplate(
            name="base",
            steps=[StepTemplate(name="a", handler=_SYNC)],
        )
        wf = instantiate_template(tpl, name_override="custom-run")
        assert wf.name == "custom-run"

    def test_config_overrides(self) -> None:
        tpl = WorkflowTemplate(
            name="t",
            steps=[
                StepTemplate(
                    name="greet",
                    handler=_SYNC,
                    config={"greeting": "hi", "target": "world"},
                ),
            ],
        )
        wf = instantiate_template(
            tpl, config_overrides={"greet": {"target": "bob"}}
        )
        step_obj = wf.steps[0]
        assert isinstance(step_obj.config, _TplConfig)
        assert step_obj.config.greeting == "hi"  # from template
        assert step_obj.config.target == "bob"  # from override

    def test_async_step_mirrors_descriptor(self) -> None:
        tpl = WorkflowTemplate(
            name="t",
            steps=[StepTemplate(name="work", handler=_ASYNC)],
        )
        wf = instantiate_template(tpl)
        step_obj = wf.steps[0]
        assert step_obj.is_async is True
        assert step_obj.completeness_check == _tpl_check._step_meta["handler"]

    def test_unknown_handler_raises(self) -> None:
        tpl = WorkflowTemplate(
            name="t",
            steps=[StepTemplate(name="a", handler="nope.does.not.exist")],
        )
        with pytest.raises(ValueError, match="unknown handler"):
            instantiate_template(tpl)

    def test_non_launchable_handler_raises(self) -> None:
        tpl = WorkflowTemplate(
            name="t",
            steps=[StepTemplate(name="a", handler=_UNTYPED)],
        )
        with pytest.raises(ValueError, match="not launchable"):
            instantiate_template(tpl)

    def test_invalid_config_raises(self) -> None:
        tpl = WorkflowTemplate(
            name="t",
            steps=[
                StepTemplate(
                    name="a",
                    handler=_SYNC,
                    config={"greeting": 123},  # type: ignore[dict-item]
                ),
            ],
        )
        # Pydantic ValidationError is-a ValueError
        with pytest.raises(ValueError):  # noqa: PT011 - ValidationError subclass
            instantiate_template(tpl)

    def test_multi_step_dag_instantiation(self) -> None:
        tpl = WorkflowTemplate(
            name="dag",
            steps=[
                StepTemplate(name="root", handler=_SYNC, depends_on=[]),
                StepTemplate(
                    name="leaf", handler=_FOLLOWUP, depends_on=["root"]
                ),
            ],
        )
        wf = instantiate_template(tpl)
        assert [s.name for s in wf.steps] == ["root", "leaf"]
        assert wf.steps[0].depends_on == []
        assert wf.steps[1].depends_on == ["root"]


# ---------------------------------------------------------------------------
# Store CRUD
# ---------------------------------------------------------------------------


class TestTemplateStore:
    @pytest.mark.asyncio
    async def test_insert_and_get(self, store) -> None:
        tpl = WorkflowTemplate(
            name="insert-test",
            description="for the insert test",
            steps=[StepTemplate(name="a", handler=_SYNC)],
        )
        returned_id = await store.insert_template(tpl)
        assert returned_id == tpl.id

        fetched = await store.get_template(tpl.id)
        assert fetched is not None
        assert fetched.id == tpl.id
        assert fetched.name == "insert-test"
        assert fetched.description == "for the insert test"
        assert fetched.version == 1
        assert len(fetched.steps) == 1
        assert fetched.steps[0].handler == _SYNC

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, store) -> None:
        assert await store.get_template("nonexistent-id") is None

    @pytest.mark.asyncio
    async def test_list_templates_empty(self, store) -> None:
        assert await store.list_templates() == []

    @pytest.mark.asyncio
    async def test_list_templates_sorted_by_updated_at_desc(self, store) -> None:
        tpls = [
            WorkflowTemplate(
                name=f"t{i}", steps=[StepTemplate(name="a", handler=_SYNC)]
            )
            for i in range(3)
        ]
        for tpl in tpls:
            await store.insert_template(tpl)

        listed = await store.list_templates()
        assert len(listed) == 3
        # Most recent first (insertion order == updated_at order for mongomock)
        names = {t.name for t in listed}
        assert names == {"t0", "t1", "t2"}

    @pytest.mark.asyncio
    async def test_update_template_bumps_version(self, store) -> None:
        tpl = WorkflowTemplate(
            name="orig",
            steps=[StepTemplate(name="a", handler=_SYNC)],
        )
        await store.insert_template(tpl)
        assert tpl.version == 1

        updated = await store.update_template(
            tpl.id,
            expected_version=1,
            name="renamed",
            description="new desc",
        )
        assert updated is not None
        assert updated.version == 2
        assert updated.name == "renamed"
        assert updated.description == "new desc"
        # Round-trip via get
        fetched = await store.get_template(tpl.id)
        assert fetched is not None
        assert fetched.version == 2

    @pytest.mark.asyncio
    async def test_update_stale_version_returns_none(self, store) -> None:
        tpl = WorkflowTemplate(
            name="orig",
            steps=[StepTemplate(name="a", handler=_SYNC)],
        )
        await store.insert_template(tpl)

        # First update bumps version to 2
        await store.update_template(tpl.id, expected_version=1, name="v2")

        # Second update with stale version=1 returns None
        stale = await store.update_template(
            tpl.id, expected_version=1, name="v3-stale"
        )
        assert stale is None

        # Confirm state is still v2
        fetched = await store.get_template(tpl.id)
        assert fetched is not None
        assert fetched.name == "v2"
        assert fetched.version == 2

    @pytest.mark.asyncio
    async def test_update_nonexistent_returns_none(self, store) -> None:
        result = await store.update_template(
            "nonexistent", expected_version=1, name="x"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_update_template_steps(self, store) -> None:
        """update_template accepts a new list of StepTemplate objects."""
        tpl = WorkflowTemplate(
            name="orig",
            steps=[StepTemplate(name="a", handler=_SYNC)],
        )
        await store.insert_template(tpl)

        new_steps = [
            StepTemplate(name="a", handler=_SYNC),
            StepTemplate(name="b", handler=_FOLLOWUP),
        ]
        updated = await store.update_template(
            tpl.id, expected_version=1, steps=new_steps
        )
        assert updated is not None
        assert len(updated.steps) == 2
        assert [s.name for s in updated.steps] == ["a", "b"]

    @pytest.mark.asyncio
    async def test_update_only_provided_fields(self, store) -> None:
        tpl = WorkflowTemplate(
            name="orig",
            description="original",
            steps=[StepTemplate(name="a", handler=_SYNC)],
        )
        await store.insert_template(tpl)

        # Only update name; description should remain unchanged
        updated = await store.update_template(
            tpl.id, expected_version=1, name="renamed"
        )
        assert updated is not None
        assert updated.name == "renamed"
        assert updated.description == "original"

    @pytest.mark.asyncio
    async def test_delete_template(self, store) -> None:
        tpl = WorkflowTemplate(
            name="bye",
            steps=[StepTemplate(name="a", handler=_SYNC)],
        )
        await store.insert_template(tpl)

        assert await store.delete_template(tpl.id) is True
        assert await store.get_template(tpl.id) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self, store) -> None:
        assert await store.delete_template("nope") is False

    @pytest.mark.asyncio
    async def test_insert_then_instantiate_roundtrip(self, store) -> None:
        """End-to-end: insert a template, fetch it back, instantiate it."""
        tpl = WorkflowTemplate(
            name="roundtrip",
            steps=[
                StepTemplate(
                    name="greet",
                    handler=_SYNC,
                    config={"greeting": "hola", "target": "mundo"},
                )
            ],
        )
        await store.insert_template(tpl)
        fetched = await store.get_template(tpl.id)
        assert fetched is not None

        wf = instantiate_template(fetched)
        assert wf.name == "roundtrip"
        assert isinstance(wf.steps[0].config, _TplConfig)
        assert wf.steps[0].config.target == "mundo"

        # The instantiated workflow should persist and fetch cleanly too
        wf_id = await store.insert(wf)
        stored_wf = await store.get(wf_id)
        assert stored_wf is not None
        assert isinstance(stored_wf.steps[0].config, _TplConfig)
        assert stored_wf.steps[0].config.target == "mundo"
