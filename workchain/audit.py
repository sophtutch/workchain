"""Audit log for workflow state changes.

Captures every MongoDB write that changes workflow or step state,
with enough detail to reconstruct flow diagrams from the log alone.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

AUDIT_COLLECTION = "workflow_audit_log"


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


class AuditEventType(str, Enum):
    # Workflow lifecycle
    WORKFLOW_CREATED = "workflow_created"
    WORKFLOW_CLAIMED = "workflow_claimed"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED = "workflow_failed"

    # Step lifecycle
    STEP_SUBMITTED = "step_submitted"
    STEP_RUNNING = "step_running"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    STEP_BLOCKED = "step_blocked"
    STEP_ADVANCED = "step_advanced"

    # Polling
    POLL_CHECKED = "poll_checked"
    POLL_TIMEOUT = "poll_timeout"
    POLL_MAX_EXCEEDED = "poll_max_exceeded"

    # Locking
    LOCK_RELEASED = "lock_released"
    LOCK_FORCE_RELEASED = "lock_force_released"
    HEARTBEAT = "heartbeat"

    # Recovery
    RECOVERY_STARTED = "recovery_started"
    RECOVERY_VERIFIED = "recovery_verified"
    RECOVERY_BLOCKED = "recovery_blocked"
    RECOVERY_RESET = "recovery_reset"
    RECOVERY_NEEDS_REVIEW = "recovery_needs_review"

    # Sweep
    SWEEP_ANOMALY = "sweep_anomaly"


# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------


class AuditEvent(BaseModel):
    """A single audit log entry capturing a state-changing operation."""

    # Identity
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    workflow_id: str
    workflow_name: str
    event_type: AuditEventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    sequence: int = 0  # monotonic per workflow, assigned by logger

    # Actor
    instance_id: str | None = None
    fence_token: int | None = None
    fence_token_before: int | None = None

    # Workflow state transition
    workflow_status: str | None = None
    workflow_status_before: str | None = None

    # Step context
    step_index: int | None = None
    step_name: str | None = None
    step_handler: str | None = None
    step_status: str | None = None
    step_status_before: str | None = None
    is_async: bool | None = None
    idempotent: bool | None = None

    # Retry
    attempt: int | None = None
    max_attempts: int | None = None

    # Poll
    poll_count: int | None = None
    poll_progress: float | None = None
    poll_message: str | None = None
    next_poll_at: datetime | None = None
    current_poll_interval: float | None = None
    poll_elapsed_seconds: float | None = None

    # Result / error
    result_summary: dict | None = None
    error: str | None = None
    error_traceback: str | None = None

    # Lock
    locked_by: str | None = None
    lock_released: bool = False

    # Recovery
    recovery_action: str | None = None

    # Anomaly
    anomaly_type: str | None = None

    # MongoDB diff — the exact fields that changed
    fields_changed: dict | None = None


# ---------------------------------------------------------------------------
# Protocol + implementations
# ---------------------------------------------------------------------------


@runtime_checkable
class AuditLogger(Protocol):
    """Protocol for audit log backends."""

    async def emit(self, event: AuditEvent) -> None:
        """Record an audit event."""
        ...

    async def get_events(
        self,
        workflow_id: str,
        event_type: AuditEventType | None = None,
    ) -> list[AuditEvent]:
        """Retrieve audit events for a workflow, ordered by sequence."""
        ...


class NullAuditLogger:
    """No-op audit logger for tests and environments that don't need auditing."""

    async def emit(self, _event: AuditEvent) -> None:
        pass

    async def get_events(
        self,
        _workflow_id: str,
        _event_type: AuditEventType | None = None,
    ) -> list[AuditEvent]:
        return []


class MongoAuditLogger:
    """Writes audit events to a MongoDB collection with fire-and-forget semantics."""

    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._col = db[AUDIT_COLLECTION]
        self._pending: set[asyncio.Task] = set()
        self._sequences: dict[str, int] = {}

    async def ensure_indexes(self) -> None:
        """Create indexes for efficient queries."""
        await self._col.create_index([("workflow_id", 1), ("timestamp", 1)])
        await self._col.create_index("timestamp")

    def _next_sequence(self, workflow_id: str) -> int:
        seq = self._sequences.get(workflow_id, 0) + 1
        self._sequences[workflow_id] = seq
        return seq

    async def emit(self, event: AuditEvent) -> None:
        """Record an audit event (fire-and-forget)."""
        event.sequence = self._next_sequence(event.workflow_id)
        doc = event.model_dump(mode="python", exclude_none=True)
        doc["_id"] = doc.pop("id")
        task = asyncio.create_task(self._safe_insert(doc))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _safe_insert(self, doc: dict) -> None:
        try:
            await self._col.insert_one(doc)
        except Exception:
            logger.warning("Failed to write audit event", exc_info=True)

    async def get_events(
        self,
        workflow_id: str,
        event_type: AuditEventType | None = None,
    ) -> list[AuditEvent]:
        """Retrieve audit events for a workflow, ordered by timestamp."""
        query: dict = {"workflow_id": workflow_id}
        if event_type is not None:
            query["event_type"] = event_type.value
        cursor = self._col.find(query).sort([("timestamp", 1), ("_id", 1)])
        events = []
        async for doc in cursor:
            doc["id"] = doc.pop("_id")
            events.append(AuditEvent.model_validate(doc))
        return events
