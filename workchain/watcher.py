"""MongoDB Change Stream watcher for workflow events.

Provides real-time notifications when workflow runs are created or updated,
enabling event-driven processing instead of (or alongside) polling.

Requires a MongoDB replica set — even a single-node replica set for development.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from pydantic import BaseModel, Field

from workchain.models import WorkflowStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


class WorkflowEventType(str, Enum):
    """Types of workflow events emitted by the watcher."""

    RUN_CREATED = "run_created"
    RUN_UPDATED = "run_updated"


class WorkflowEvent(BaseModel):
    """A workflow change event from MongoDB Change Streams."""

    event_type: WorkflowEventType
    run_id: Any = None
    workflow_name: str | None = None
    status: WorkflowStatus | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------


class WorkflowWatcher:
    """
    Watches a MongoDB collection for workflow changes using Change Streams.

    Filters out changes made by the current owner to avoid self-triggering:
    any document whose ``lease_owner`` matches this watcher's ``owner_id``
    is silently ignored.

    Requires MongoDB replica set (even single-node for development).

    Usage::

        watcher = store.watcher()
        async with watcher:
            async for event in watcher:
                print(f"Event: {event.event_type} run={event.run_id}")
                await runner.tick()

    Or integrated with the runner::

        watcher = store.watcher()
        await runner.start(watcher=watcher)
    """

    def __init__(self, collection: Any, owner_id: str) -> None:
        self._collection = collection
        self._owner_id = owner_id
        self._stream: Any = None

    def _build_pipeline(self) -> list[dict[str, Any]]:
        """Build the change stream aggregation pipeline.

        Matches inserts, updates, and replaces where the resulting document
        is NOT currently leased by this owner — preventing self-triggering.
        """
        return [
            {
                "$match": {
                    "operationType": {"$in": ["insert", "update", "replace"]},
                    "$or": [
                        {"fullDocument.lease_owner": None},
                        {"fullDocument.lease_owner": {"$ne": self._owner_id}},
                    ],
                }
            }
        ]

    async def __aenter__(self) -> WorkflowWatcher:  # noqa: PYI034
        self._stream = self._collection.watch(
            pipeline=self._build_pipeline(),
            full_document="updateLookup",
        )
        await self._stream.__aenter__()
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._stream:
            await self._stream.__aexit__(*args)
            self._stream = None

    def __aiter__(self) -> AsyncIterator[WorkflowEvent]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[WorkflowEvent]:
        if self._stream is None:
            raise RuntimeError("Watcher not started. Use 'async with watcher:' first.")
        async for change in self._stream:
            event = self._parse_change(change)
            if event is not None:
                yield event

    def _parse_change(self, change: dict[str, Any]) -> WorkflowEvent | None:
        """Convert a raw MongoDB change document into a WorkflowEvent."""
        op = change.get("operationType")
        full_doc = change.get("fullDocument")

        if full_doc is None:
            return None

        event_type = WorkflowEventType.RUN_CREATED if op == "insert" else WorkflowEventType.RUN_UPDATED

        status = None
        status_value = full_doc.get("status")
        if status_value:
            with contextlib.suppress(ValueError):
                status = WorkflowStatus(status_value)

        return WorkflowEvent(
            event_type=event_type,
            run_id=full_doc.get("_id"),
            workflow_name=full_doc.get("workflow_name"),
            status=status,
        )

    async def close(self) -> None:
        """Explicitly close the change stream."""
        if self._stream:
            await self._stream.close()
            self._stream = None
