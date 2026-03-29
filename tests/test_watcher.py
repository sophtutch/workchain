"""Tests for WorkflowWatcher."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from workchain.models import WorkflowStatus
from workchain.watcher import WorkflowEventType, WorkflowWatcher


def _make_watcher(owner_id: str = "runner-1") -> WorkflowWatcher:
    collection = MagicMock()
    return WorkflowWatcher(collection=collection, owner_id=owner_id)


# ---------------------------------------------------------------------------
# _parse_change
# ---------------------------------------------------------------------------


class TestParseChange:
    def test_insert_creates_run_created_event(self):
        watcher = _make_watcher()
        event = watcher._parse_change(
            {
                "operationType": "insert",
                "fullDocument": {
                    "_id": "abc123",
                    "workflow_name": "onboarding",
                    "status": "pending",
                },
            }
        )
        assert event is not None
        assert event.event_type == WorkflowEventType.RUN_CREATED
        assert event.run_id == "abc123"
        assert event.workflow_name == "onboarding"
        assert event.status == WorkflowStatus.PENDING

    def test_update_creates_run_updated_event(self):
        watcher = _make_watcher()
        event = watcher._parse_change(
            {
                "operationType": "update",
                "fullDocument": {
                    "_id": "abc123",
                    "workflow_name": "onboarding",
                    "status": "running",
                },
            }
        )
        assert event is not None
        assert event.event_type == WorkflowEventType.RUN_UPDATED
        assert event.status == WorkflowStatus.RUNNING

    def test_replace_creates_run_updated_event(self):
        watcher = _make_watcher()
        event = watcher._parse_change(
            {
                "operationType": "replace",
                "fullDocument": {
                    "_id": "abc123",
                    "workflow_name": "onboarding",
                    "status": "completed",
                },
            }
        )
        assert event is not None
        assert event.event_type == WorkflowEventType.RUN_UPDATED

    def test_missing_full_document_returns_none(self):
        watcher = _make_watcher()
        assert watcher._parse_change({"operationType": "delete"}) is None
        assert watcher._parse_change({"operationType": "update", "fullDocument": None}) is None

    def test_invalid_status_sets_none(self):
        watcher = _make_watcher()
        event = watcher._parse_change(
            {
                "operationType": "insert",
                "fullDocument": {
                    "_id": "abc123",
                    "workflow_name": "test",
                    "status": "not_a_real_status",
                },
            }
        )
        assert event is not None
        assert event.status is None

    def test_valid_status_parsed(self):
        watcher = _make_watcher()
        for status in WorkflowStatus:
            event = watcher._parse_change(
                {
                    "operationType": "insert",
                    "fullDocument": {
                        "_id": "id",
                        "workflow_name": "w",
                        "status": status.value,
                    },
                }
            )
            assert event is not None
            assert event.status == status


# ---------------------------------------------------------------------------
# _build_pipeline
# ---------------------------------------------------------------------------


class TestBuildPipeline:
    def test_pipeline_matches_insert_update_replace(self):
        watcher = _make_watcher("runner-1")
        pipeline = watcher._build_pipeline()
        assert len(pipeline) == 1
        match_stage = pipeline[0]["$match"]
        assert match_stage["operationType"] == {"$in": ["insert", "update", "replace"]}

    def test_pipeline_excludes_own_owner(self):
        watcher = _make_watcher("my-runner")
        pipeline = watcher._build_pipeline()
        match_stage = pipeline[0]["$match"]
        or_clauses = match_stage["$or"]
        # One clause matches None, the other excludes the owner via $ne
        ne_clause = next(
            c for c in or_clauses
            if isinstance(c.get("fullDocument.lease_owner"), dict)
            and "$ne" in c["fullDocument.lease_owner"]
        )
        assert ne_clause["fullDocument.lease_owner"]["$ne"] == "my-runner"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestWatcherLifecycle:
    @pytest.mark.asyncio
    async def test_iterate_without_start_raises(self):
        watcher = _make_watcher()
        with pytest.raises(RuntimeError, match="Watcher not started"):
            async for _ in watcher:
                pass

    @pytest.mark.asyncio
    async def test_context_manager_opens_and_closes_stream(self):
        mock_stream = AsyncMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=False)

        collection = MagicMock()
        collection.watch = MagicMock(return_value=mock_stream)

        watcher = WorkflowWatcher(collection=collection, owner_id="r1")
        async with watcher:
            assert watcher._stream is not None

        assert watcher._stream is None
        mock_stream.__aexit__.assert_awaited_once()
