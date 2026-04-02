"""Tests for workchain.audit — AuditEvent, MongoAuditLogger, NullAuditLogger, engine integration."""

from __future__ import annotations

import asyncio

import pytest
from mongomock_motor import AsyncMongoMockClient

from tests.conftest import GreetConfig
from workchain.audit import (
    AuditEvent,
    AuditEventType,
    MongoAuditLogger,
    NullAuditLogger,
)
from workchain.engine import WorkflowEngine
from workchain.models import (
    RetryPolicy,
    Step,
    StepStatus,
    Workflow,
    WorkflowStatus,
)
from workchain.store import MongoWorkflowStore

# ---------------------------------------------------------------------------
# AuditEvent model
# ---------------------------------------------------------------------------


class TestAuditEvent:
    def test_defaults(self):
        e = AuditEvent(
            workflow_id="wf1",
            workflow_name="test",
            event_type=AuditEventType.WORKFLOW_CREATED,
        )
        assert len(e.id) == 32
        assert e.sequence == 0
        assert e.timestamp is not None
        assert e.instance_id is None
        assert e.lock_released is False

    def test_all_event_types(self):
        assert len(AuditEventType) == 22

    def test_event_with_step_context(self):
        e = AuditEvent(
            workflow_id="wf1",
            workflow_name="test",
            event_type=AuditEventType.STEP_COMPLETED,
            step_index=0,
            step_name="greet",
            step_handler="tests.greet",
            step_status="completed",
            step_status_before="running",
            result_summary={"greeting": "hi"},
        )
        assert e.step_name == "greet"
        assert e.result_summary == {"greeting": "hi"}

    def test_event_with_poll_context(self):
        e = AuditEvent(
            workflow_id="wf1",
            workflow_name="test",
            event_type=AuditEventType.POLL_CHECKED,
            poll_count=3,
            poll_progress=0.66,
            poll_message="in progress",
        )
        assert e.poll_count == 3
        assert e.poll_progress == 0.66


# ---------------------------------------------------------------------------
# NullAuditLogger
# ---------------------------------------------------------------------------


class TestNullAuditLogger:
    async def test_emit_is_noop(self):
        logger = NullAuditLogger()
        e = AuditEvent(
            workflow_id="wf1",
            workflow_name="test",
            event_type=AuditEventType.WORKFLOW_CREATED,
        )
        await logger.emit(e)  # should not raise

    async def test_get_events_returns_empty(self):
        logger = NullAuditLogger()
        events = await logger.get_events("wf1")
        assert events == []


# ---------------------------------------------------------------------------
# MongoAuditLogger
# ---------------------------------------------------------------------------


class TestMongoAuditLogger:
    @pytest.fixture
    def audit_db(self):
        return AsyncMongoMockClient()["test_audit"]

    @pytest.fixture
    def audit_logger(self, audit_db):
        return MongoAuditLogger(audit_db)

    async def test_emit_and_retrieve(self, audit_logger):
        e = AuditEvent(
            workflow_id="wf1",
            workflow_name="test",
            event_type=AuditEventType.WORKFLOW_CREATED,
            instance_id="inst_1",
        )
        await audit_logger.emit(e)
        # Wait for fire-and-forget task
        await asyncio.sleep(0.1)

        events = await audit_logger.get_events("wf1")
        assert len(events) == 1
        assert events[0].event_type == AuditEventType.WORKFLOW_CREATED
        assert events[0].instance_id == "inst_1"

    async def test_sequence_monotonic(self, audit_logger):
        for i in range(5):
            e = AuditEvent(
                workflow_id="wf1",
                workflow_name="test",
                event_type=AuditEventType.STEP_RUNNING,
                attempt=i + 1,
            )
            await audit_logger.emit(e)

        await asyncio.sleep(0.1)

        events = await audit_logger.get_events("wf1")
        assert len(events) == 5
        seqs = [e.sequence for e in events]
        assert seqs == [1, 2, 3, 4, 5]

    async def test_filter_by_event_type(self, audit_logger):
        for evt_type in [AuditEventType.WORKFLOW_CLAIMED, AuditEventType.STEP_SUBMITTED, AuditEventType.STEP_COMPLETED]:
            await audit_logger.emit(AuditEvent(
                workflow_id="wf1",
                workflow_name="test",
                event_type=evt_type,
            ))

        await asyncio.sleep(0.1)

        step_events = await audit_logger.get_events("wf1", event_type=AuditEventType.STEP_SUBMITTED)
        assert len(step_events) == 1
        assert step_events[0].event_type == AuditEventType.STEP_SUBMITTED

    async def test_separate_workflows(self, audit_logger):
        await audit_logger.emit(AuditEvent(
            workflow_id="wf1", workflow_name="a",
            event_type=AuditEventType.WORKFLOW_CREATED,
        ))
        await audit_logger.emit(AuditEvent(
            workflow_id="wf2", workflow_name="b",
            event_type=AuditEventType.WORKFLOW_CREATED,
        ))

        await asyncio.sleep(0.1)

        events_1 = await audit_logger.get_events("wf1")
        events_2 = await audit_logger.get_events("wf2")
        assert len(events_1) == 1
        assert len(events_2) == 1
        assert events_1[0].workflow_name == "a"
        assert events_2[0].workflow_name == "b"

    async def test_excludes_none_fields(self, audit_logger):
        """Fields set to None should be excluded from the MongoDB document."""
        await audit_logger.emit(AuditEvent(
            workflow_id="wf1", workflow_name="test",
            event_type=AuditEventType.WORKFLOW_CREATED,
        ))
        await asyncio.sleep(0.1)

        # Read raw document
        doc = await audit_logger._col.find_one({"workflow_id": "wf1"})
        assert "step_name" not in doc  # None fields excluded
        assert "error" not in doc


# ---------------------------------------------------------------------------
# Engine integration — verify audit events are emitted
# ---------------------------------------------------------------------------


class TestEngineAuditIntegration:
    @pytest.fixture
    def audit_db(self):
        return AsyncMongoMockClient()["test_engine_audit"]

    @pytest.fixture
    def audit_logger(self, audit_db):
        return MongoAuditLogger(audit_db)

    @pytest.fixture
    def store(self, audit_db):
        return MongoWorkflowStore(audit_db, lock_ttl_seconds=5)

    @pytest.fixture
    def engine(self, store, audit_logger):
        return WorkflowEngine(
            store,
            instance_id="test-audit-001",
            claim_interval=0.05,
            heartbeat_interval=0.05,
            sweep_interval=10,
            max_concurrent=5,
            audit_logger=audit_logger,
        )

    async def test_sync_workflow_emits_events(self, store, engine, audit_logger):
        """A simple sync workflow should emit: CLAIMED, SUBMITTED, RUNNING, COMPLETED, ADVANCED, WORKFLOW_COMPLETED."""
        wf = Workflow(
            name="audit_sync",
            steps=[Step(name="noop", handler="tests.noop")],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()
        await asyncio.sleep(0.2)  # let fire-and-forget writes land

        events = await audit_logger.get_events(wf.id)
        event_types = [e.event_type for e in events]

        assert AuditEventType.WORKFLOW_CLAIMED in event_types
        assert AuditEventType.STEP_SUBMITTED in event_types
        assert AuditEventType.STEP_RUNNING in event_types
        assert AuditEventType.STEP_COMPLETED in event_types
        assert AuditEventType.STEP_ADVANCED in event_types
        assert AuditEventType.WORKFLOW_COMPLETED in event_types

    async def test_claimed_event_has_fence_token(self, store, engine, audit_logger):
        wf = Workflow(name="audit_fence", steps=[Step(name="noop", handler="tests.noop")])
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()
        await asyncio.sleep(0.2)

        events = await audit_logger.get_events(wf.id, event_type=AuditEventType.WORKFLOW_CLAIMED)
        assert len(events) >= 1
        assert events[0].fence_token == 1
        assert events[0].fence_token_before == 0
        assert events[0].instance_id == "test-audit-001"

    async def test_step_events_have_step_context(self, store, engine, audit_logger):
        wf = Workflow(
            name="audit_step_ctx",
            steps=[Step(name="greet", handler="tests.greet", config=GreetConfig(name="Test"))],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()
        await asyncio.sleep(0.2)

        completed = await audit_logger.get_events(wf.id, event_type=AuditEventType.STEP_COMPLETED)
        assert len(completed) >= 1
        e = completed[0]
        assert e.step_name == "greet"
        assert e.step_handler == "tests.greet"
        assert e.step_index == 0

    async def test_failed_step_emits_failure_events(self, store, engine, audit_logger):
        wf = Workflow(
            name="audit_fail",
            steps=[Step(
                name="fail",
                handler="tests.fail_always",
                retry_policy=RetryPolicy(max_attempts=1, wait_seconds=0.01, wait_multiplier=0.01),
            )],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()
        await asyncio.sleep(0.2)

        events = await audit_logger.get_events(wf.id)
        event_types = [e.event_type for e in events]

        assert AuditEventType.STEP_FAILED in event_types
        assert AuditEventType.WORKFLOW_FAILED in event_types

        failed = [e for e in events if e.event_type == AuditEventType.STEP_FAILED]
        assert failed[0].error is not None

    async def test_recovery_emits_events(self, store, engine, audit_logger):
        """An idempotent step found in SUBMITTED state emits RECOVERY_STARTED + RECOVERY_RESET."""
        wf = Workflow(
            name="audit_recovery",
            status=WorkflowStatus.RUNNING,
            fence_token=1,
            steps=[Step(
                name="noop",
                handler="tests.noop",
                status=StepStatus.SUBMITTED,
                idempotent=True,
            )],
        )
        await store.insert(wf)

        claimed = await store.try_claim(wf.id, "test-audit-001")
        assert claimed is not None
        await engine._run_workflow(claimed)
        await asyncio.sleep(0.2)

        events = await audit_logger.get_events(wf.id)
        event_types = [e.event_type for e in events]

        assert AuditEventType.RECOVERY_STARTED in event_types
        assert AuditEventType.RECOVERY_RESET in event_types

    async def test_events_ordered_by_sequence(self, store, engine, audit_logger):
        wf = Workflow(
            name="audit_order",
            steps=[
                Step(name="s1", handler="tests.noop"),
                Step(name="s2", handler="tests.noop"),
            ],
        )
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()
        await asyncio.sleep(0.2)

        events = await audit_logger.get_events(wf.id)
        seqs = [e.sequence for e in events]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)  # all unique

    async def test_no_audit_by_default(self, store):
        """Engine without audit_logger should still work (NullAuditLogger)."""
        engine = WorkflowEngine(
            store,
            instance_id="no-audit",
            claim_interval=0.05,
            heartbeat_interval=0.05,
            sweep_interval=10,
        )
        wf = Workflow(name="no_audit", steps=[Step(name="noop", handler="tests.noop")])
        await store.insert(wf)

        await engine.start()
        await asyncio.sleep(0.5)
        await engine.stop()

        loaded = await store.get(wf.id)
        assert loaded.status == WorkflowStatus.COMPLETED
