"""Custom exception hierarchy for workchain.

Provides specific exception types so callers can distinguish between
lock errors, step execution errors, and recovery errors programmatically.
"""


class WorkchainError(Exception):
    """Base exception for all workchain errors."""


class StepError(WorkchainError):
    """Error during step execution."""


class StepTimeoutError(StepError):
    """Step handler exceeded its timeout."""


class RetryExhaustedError(StepError):
    """All retry attempts exhausted."""


class HandlerError(StepError):
    """Step handler returned invalid result or is misconfigured."""


class LockError(WorkchainError):
    """Lock acquisition or fence token error."""


class FenceRejectedError(LockError):
    """Write rejected because fence token doesn't match (lock stolen)."""


class RecoveryError(WorkchainError):
    """Error during crash recovery."""
