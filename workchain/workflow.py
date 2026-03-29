"""Workflow definition and builder."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from workchain.exceptions import WorkflowValidationError
from workchain.models import DependencyFailurePolicy, RetryPolicy, StepRun, WorkflowRun, WorkflowStatus
from workchain.steps import Step

# ---------------------------------------------------------------------------
# StepDefinition — static, build-time description of one step
# ---------------------------------------------------------------------------


class StepDefinition(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    step_id: str
    step: Step  # the step instance (carries config)
    depends_on: list[str] = Field(default_factory=list)
    on_dependency_failure: DependencyFailurePolicy = DependencyFailurePolicy.FAIL
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    timeout_seconds: float | None = None

    @property
    def step_type(self) -> str:
        return type(self.step).__name__


# ---------------------------------------------------------------------------
# Workflow — the immutable blueprint
# ---------------------------------------------------------------------------


class Workflow:
    """
    Fluent builder for workflow definitions.

    Usage::

        workflow = (
            Workflow(name="my_pipeline", version="1.0.0")
            .add("fetch", FetchStep(config=FetchConfig(url="...")))
            .add("process", ProcessStep(), depends_on=["fetch"])
        )
    """

    def __init__(self, name: str, version: str = "1.0.0") -> None:
        self.name = name
        self.version = version
        self._steps: list[StepDefinition] = []
        self._step_ids: set[str] = set()

    def add(
        self,
        step_id: str,
        step: Step,
        depends_on: list[str] | None = None,
        on_dependency_failure: DependencyFailurePolicy = DependencyFailurePolicy.FAIL,
        retry_policy: RetryPolicy | None = None,
        timeout_seconds: float | None = None,
    ) -> Workflow:
        """
        Add a step to the workflow.

        :param step_id: Unique identifier for this step within the workflow.
        :param step: An instantiated Step (with its config already set).
        :param depends_on: List of step_ids that must complete before this step runs.
        :param on_dependency_failure: DependencyFailurePolicy.FAIL (default) or DependencyFailurePolicy.SKIP.
        :param retry_policy: Optional RetryPolicy controlling retry behaviour on failure.
        :param timeout_seconds: Optional wall-clock timeout for step execution.
        :returns: self, for chaining.
        """
        if step_id in self._step_ids:
            raise WorkflowValidationError(f"Duplicate step_id: '{step_id}'")

        deps = depends_on or []
        self._steps.append(
            StepDefinition(
                step_id=step_id,
                step=step,
                depends_on=deps,
                on_dependency_failure=on_dependency_failure,
                retry_policy=retry_policy or RetryPolicy(),
                timeout_seconds=timeout_seconds,
            )
        )
        self._step_ids.add(step_id)
        return self

    def validate(self) -> None:
        """
        Validate the workflow DAG:
        - All depends_on references must point to known step_ids
        - No cycles (topological sort)

        Raises WorkflowValidationError on failure.
        """
        # Check all dependency references exist
        for step_def in self._steps:
            for dep in step_def.depends_on:
                if dep not in self._step_ids:
                    raise WorkflowValidationError(f"Step '{step_def.step_id}' depends on unknown step '{dep}'")

        # Cycle detection via Kahn's algorithm
        in_degree: dict[str, int] = {s.step_id: 0 for s in self._steps}
        adjacency: dict[str, list[str]] = {s.step_id: [] for s in self._steps}

        for step_def in self._steps:
            for dep in step_def.depends_on:
                adjacency[dep].append(step_def.step_id)
                in_degree[step_def.step_id] += 1

        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        processed = 0

        while queue:
            node = queue.pop(0)
            processed += 1
            for neighbour in adjacency[node]:
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

        if processed != len(self._steps):
            raise WorkflowValidationError("Workflow DAG contains a cycle.")

    def create_run(self) -> WorkflowRun:
        """
        Validate the workflow and return a new, unpersisted WorkflowRun.
        The caller is responsible for saving it to the store.
        """
        self.validate()
        steps = [
            StepRun(
                step_id=s.step_id,
                step_type=s.step_type,
                depends_on=s.depends_on,
                on_dependency_failure=s.on_dependency_failure,
            )
            for s in self._steps
        ]
        run = WorkflowRun(
            workflow_name=self.name,
            workflow_version=self.version,
            status=WorkflowStatus.PENDING,
            steps=steps,
        )
        run.recompute_status()
        return run

    def get_step_definitions(self) -> list[StepDefinition]:
        return list(self._steps)

    def get_step_definition(self, step_id: str) -> StepDefinition | None:
        return next((s for s in self._steps if s.step_id == step_id), None)

    def __repr__(self) -> str:
        return f"Workflow(name={self.name!r}, version={self.version!r}, steps={len(self._steps)})"
