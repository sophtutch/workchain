"""workchain exceptions."""


class WorkchainError(Exception):
    """Base exception for all workchain errors."""


class WorkflowValidationError(WorkchainError):
    """Raised when a workflow definition is invalid (e.g. DAG cycle, unknown step_id)."""


class ConcurrentModificationError(WorkchainError):
    """Raised when an optimistic lock check fails on save."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"WorkflowRun '{run_id}' was modified concurrently.")


class LeaseAcquisitionError(WorkchainError):
    """Raised when a lease claim fails unexpectedly (not simply unavailable)."""


class StepNotFoundError(WorkchainError):
    """Raised when a step_type is not present in the step registry."""

    def __init__(self, step_type: str) -> None:
        self.step_type = step_type
        super().__init__(f"Step type '{step_type}' is not registered.")


class WorkflowRunNotFoundError(WorkchainError):
    """Raised when a WorkflowRun cannot be found by its ID or correlation ID."""

    def __init__(self, identifier: str) -> None:
        self.identifier = identifier
        super().__init__(f"WorkflowRun not found: '{identifier}'.")
