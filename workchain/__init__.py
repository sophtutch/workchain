"""
workchain — programmatic construction and execution of persistent, multi-step workflows.
"""

from workchain.context import Context
from workchain.exceptions import (
    ConcurrentModificationError,
    LeaseAcquisitionError,
    StepNotFoundError,
    WorkchainError,
    WorkflowRunNotFoundError,
    WorkflowValidationError,
)
from workchain.models import DependencyFailurePolicy, StepRun, StepStatus, WorkflowRun, WorkflowStatus
from workchain.runner import WorkflowRunner
from workchain.steps import EventStep, PollingStep, Step, StepOutcome, StepResult
from workchain.store import MongoWorkflowStore, WorkflowStore
from workchain.watcher import WorkflowEvent, WorkflowEventType, WorkflowWatcher
from workchain.workflow import StepDefinition, Workflow

__all__ = [
    # Core workflow building
    "Workflow",
    "StepDefinition",
    # Step base classes
    "Step",
    "EventStep",
    "PollingStep",
    "StepResult",
    "StepOutcome",
    # Shared runtime state
    "Context",
    # Persistence models
    "WorkflowRun",
    "StepRun",
    "WorkflowStatus",
    "StepStatus",
    "DependencyFailurePolicy",
    # Store
    "WorkflowStore",
    "MongoWorkflowStore",
    # Runner
    "WorkflowRunner",
    # Watcher (change streams)
    "WorkflowWatcher",
    "WorkflowEvent",
    "WorkflowEventType",
    # Exceptions
    "WorkchainError",
    "WorkflowValidationError",
    "ConcurrentModificationError",
    "LeaseAcquisitionError",
    "StepNotFoundError",
    "WorkflowRunNotFoundError",
]

__version__ = "0.1.0"
