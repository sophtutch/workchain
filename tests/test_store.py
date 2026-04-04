"""Tests for workchain.store — MongoWorkflowStore with mongomock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import GreetConfig, GreetResult
from workchain.models import (
    Step,
    StepConfig,
    StepResult,
    StepStatus,
    Workflow,
    WorkflowStatus,
)
from workchain.store import MongoWorkflowStore, _import_class

# ---------------------------------------------------------------------------
# _import_class
# ---------------------------------------------------------------------------


class TestImportClass:
    def test_valid_path(self):
        cls = _import_class("workchain.models.StepConfig")
        assert cls is StepConfig

    def test_no_dot_raises(self):
        with pytest.raises(ValueError, match="Invalid dotted path"):
            _import_class("NoDots")

    def test_bad_module_raises(self):
        with pytest.raises(ModuleNotFoundError):
            _import_class("nonexistent_module.SomeClass")

    def test_bad_class_raises(self):
        with pytest.raises(ImportError, match="Cannot find 'DoesNotExist'"):
            _import_class("workchain.models.DoesNotExist")


# ---------------------------------------------------------------------------
# Document conversion
# ---------------------------------------------------------------------------


class TestDocToWorkflow:
    def test_basic_document(self):
        doc = {
            "_id": "wf1",
            "name": "test",
            "status": "pending",
            "steps": [],
            "current_step_index": 0,
            "fence_token": 0,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        wf = MongoWorkflowStore._doc_to_workflow(doc)
        assert wf.id == "wf1"
        assert wf.name == "test"
        assert wf.status == WorkflowStatus.PENDING

    def test_typed_config_deserialization(self):
        doc = {
            "_id": "wf2",
            "name": "test",
            "status": "running",
            "steps": [
                {
                    "name": "s1",
                    "handler": "mod.func",
                    "config_type": "tests.conftest.GreetConfig",
                    "config": {"name": "Alice"},
                    "status": "pending",
                    "retry_policy": {"max_attempts": 3, "wait_seconds": 1.0, "wait_multiplier": 2.0, "wait_max": 60.0},
                    "poll_policy": {"interval": 5.0, "backoff_multiplier": 1.0, "max_interval": 60.0, "timeout": 3600.0, "max_polls": 0},
                    "attempt": 0,
                    "is_async": False,
                    "idempotent": True,
                    "poll_count": 0,
                }
            ],
            "current_step_index": 0,
            "fence_token": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        wf = MongoWorkflowStore._doc_to_workflow(doc)
        assert isinstance(wf.steps[0].config, GreetConfig)
        assert wf.steps[0].config.name == "Alice"

    def test_typed_result_deserialization(self):
        doc = {
            "_id": "wf3",
            "name": "test",
            "status": "running",
            "steps": [
                {
                    "name": "s1",
                    "handler": "mod.func",
                    "result_type": "tests.conftest.GreetResult",
                    "result": {"greeting": "Hello!", "error": None, "completed_at": None},
                    "status": "completed",
                    "retry_policy": {"max_attempts": 3, "wait_seconds": 1.0, "wait_multiplier": 2.0, "wait_max": 60.0},
                    "poll_policy": {"interval": 5.0, "backoff_multiplier": 1.0, "max_interval": 60.0, "timeout": 3600.0, "max_polls": 0},
                    "attempt": 1,
                    "is_async": False,
                    "idempotent": True,
                    "poll_count": 0,
                }
            ],
            "current_step_index": 1,
            "fence_token": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        wf = MongoWorkflowStore._doc_to_workflow(doc)
        assert isinstance(wf.steps[0].result, GreetResult)
        assert wf.steps[0].result.greeting == "Hello!"

    def test_missing_config_type_uses_base(self):
        doc = {
            "_id": "wf4",
            "name": "test",
            "status": "pending",
            "steps": [
                {
                    "name": "s1",
                    "handler": "mod.func",
                    "config": {"x": 1},
                    "status": "pending",
                    "retry_policy": {"max_attempts": 3, "wait_seconds": 1.0, "wait_multiplier": 2.0, "wait_max": 60.0},
                    "poll_policy": {"interval": 5.0, "backoff_multiplier": 1.0, "max_interval": 60.0, "timeout": 3600.0, "max_polls": 0},
                    "attempt": 0,
                    "is_async": False,
                    "idempotent": True,
                    "poll_count": 0,
                }
            ],
            "current_step_index": 0,
            "fence_token": 0,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        # No config_type → config stays as dict, Pydantic will handle it
        wf = MongoWorkflowStore._doc_to_workflow(doc)
        assert wf.steps[0].config is not None


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCrud:
    async def test_insert_and_get(self, store):
        wf = Workflow(name="crud_test")
        wf_id = await store.insert(wf)
        assert wf_id == wf.id

        loaded = await store.get(wf_id)
        assert loaded is not None
        assert loaded.id == wf_id
        assert loaded.name == "crud_test"

    async def test_get_missing_returns_none(self, store):
        result = await store.get("nonexistent_id")
        assert result is None

    async def test_insert_with_steps(self, store, sample_workflow):
        wf_id = await store.insert(sample_workflow)
        loaded = await store.get(wf_id)
        assert len(loaded.steps) == 2
        assert loaded.steps[0].name == "greet"

    async def test_round_trip_preserves_config(self, store):
        wf = Workflow(
            name="config_test",
            steps=[
                Step(
                    name="s1",
                    handler="mod.func",
                    config=GreetConfig(name="Bob"),
                ),
            ],
        )
        await store.insert(wf)
        loaded = await store.get(wf.id)
        assert isinstance(loaded.steps[0].config, GreetConfig)
        assert loaded.steps[0].config.name == "Bob"


# ---------------------------------------------------------------------------
# Explicit step-state transitions
# ---------------------------------------------------------------------------


class TestSubmitStep:
    async def test_submit_step(self, store):
        wf = Workflow(name="submit_test", fence_token=1)
        wf.steps = [Step(name="s1", handler="mod.func")]
        await store.insert(wf)

        result = await store.submit_step(wf.id, 0, 1, attempt=1)
        assert result is not None
        assert result.steps[0].status == StepStatus.SUBMITTED
        assert result.steps[0].attempt == 1

    async def test_submit_rejected_with_wrong_fence(self, store):
        wf = Workflow(name="fence_test", fence_token=1)
        wf.steps = [Step(name="s1", handler="mod.func")]
        await store.insert(wf)

        result = await store.submit_step(wf.id, 0, 999, attempt=1)
        assert result is None


class TestMarkStepRunning:
    async def test_mark_running(self, store):
        wf = Workflow(name="running_test", fence_token=1)
        wf.steps = [Step(name="s1", handler="mod.func")]
        await store.insert(wf)

        result = await store.mark_step_running(wf.id, 0, 1, attempt=1)
        assert result is not None
        assert result.steps[0].status == StepStatus.RUNNING
        assert result.steps[0].attempt == 1


class TestCompleteStep:
    async def test_complete_step(self, store):
        wf = Workflow(name="complete_test", fence_token=1)
        wf.steps = [Step(name="s1", handler="mod.func")]
        await store.insert(wf)

        result = await store.complete_step(
            wf.id, 0, 1,
            result=StepResult(),
        )
        assert result is not None
        assert result.steps[0].status == StepStatus.COMPLETED


class TestFailStep:
    async def test_fail_step(self, store):
        wf = Workflow(name="fail_test", fence_token=1)
        wf.steps = [Step(name="s1", handler="mod.func")]
        await store.insert(wf)

        result = await store.fail_step(
            wf.id, 0, 1,
            result=StepResult(error="boom"),
        )
        assert result is not None
        assert result.steps[0].status == StepStatus.FAILED
        assert result.steps[0].result_type is None


class TestBlockStep:
    async def test_block_step(self, store):
        from datetime import UTC, datetime
        wf = Workflow(name="block_test", fence_token=1)
        wf.steps = [Step(name="s1", handler="mod.func")]
        await store.insert(wf)
        now = datetime.now(UTC)

        result = await store.block_step(
            wf.id, 0, 1,
            result={"error": None, "completed_at": None},
            result_type=None,
            poll_started_at=now,
            next_poll_at=now,
            current_poll_interval=5.0,
        )
        assert result is not None
        assert result.steps[0].status == StepStatus.BLOCKED
        assert result.steps[0].poll_count == 0


class TestScheduleNextPoll:
    async def test_schedule_next_poll(self, store):
        from datetime import UTC, datetime
        wf = Workflow(name="poll_test", fence_token=1)
        wf.steps = [Step(name="s1", handler="mod.func", status=StepStatus.BLOCKED)]
        await store.insert(wf)
        now = datetime.now(UTC)

        result = await store.schedule_next_poll(
            wf.id, 0, 1,
            poll_count=1,
            last_poll_at=now,
            next_poll_at=now,
            current_poll_interval=10.0,
        )
        assert result is not None
        assert result.steps[0].poll_count == 1


class TestResetStep:
    async def test_reset_step(self, store):
        wf = Workflow(name="reset_test", fence_token=1)
        wf.steps = [Step(name="s1", handler="mod.func", status=StepStatus.SUBMITTED)]
        await store.insert(wf)

        result = await store.reset_step(wf.id, 0, 1)
        assert result is not None
        assert result.steps[0].status == StepStatus.PENDING


class TestFencedStepUpdate:
    async def test_changes_updated_at(self, store):
        wf = Workflow(name="ts_test", fence_token=1)
        wf.steps = [Step(name="s1", handler="mod.func")]
        await store.insert(wf)

        result = await store._fenced_step_update(wf.id, 0, 1, {"status": "submitted"})
        assert result.updated_at is not None


# ---------------------------------------------------------------------------
# advance_step
# ---------------------------------------------------------------------------


class TestAdvanceStep:
    async def test_advances_index(self, store):
        wf = Workflow(name="advance_test", fence_token=1)
        wf.steps = [Step(name="s1", handler="mod.func"), Step(name="s2", handler="mod.func")]
        await store.insert(wf)

        result = await store.advance_step(wf.id, 1, 1)
        assert result is not None
        assert result.current_step_index == 1

    async def test_advance_with_status(self, store):
        wf = Workflow(name="advance_status", fence_token=1)
        wf.steps = [Step(name="s1", handler="mod.func")]
        await store.insert(wf)

        result = await store.advance_step(wf.id, 1, 1, workflow_status=WorkflowStatus.COMPLETED)
        assert result.status == WorkflowStatus.COMPLETED

    async def test_advance_rejected_with_wrong_fence(self, store):
        wf = Workflow(name="advance_fence", fence_token=1)
        wf.steps = [Step(name="s1", handler="mod.func")]
        await store.insert(wf)

        result = await store.advance_step(wf.id, 999, 1)
        assert result is None


# ---------------------------------------------------------------------------
# Distributed locking
# ---------------------------------------------------------------------------


class TestTryClaim:
    async def test_claim_unlocked(self, store):
        wf = Workflow(name="claim_test")
        await store.insert(wf)

        claimed = await store.try_claim(wf.id, "instance_1")
        assert claimed is not None
        assert claimed.locked_by == "instance_1"
        assert claimed.fence_token == 1
        assert claimed.status == WorkflowStatus.RUNNING

    async def test_claim_already_locked(self, store):
        wf = Workflow(name="locked_test")
        await store.insert(wf)

        claimed1 = await store.try_claim(wf.id, "instance_1")
        assert claimed1 is not None

        claimed2 = await store.try_claim(wf.id, "instance_2")
        assert claimed2 is None

    async def test_claim_expired_lock(self, store):
        wf = Workflow(
            name="expired_test",
            locked_by="old_instance",
            lock_expires_at=datetime.now(UTC) - timedelta(seconds=10),
            fence_token=1,
            status=WorkflowStatus.RUNNING,
        )
        await store.insert(wf)

        claimed = await store.try_claim(wf.id, "new_instance")
        assert claimed is not None
        assert claimed.locked_by == "new_instance"
        assert claimed.fence_token == 2

    async def test_claim_nonexistent(self, store):
        result = await store.try_claim("doesnt_exist", "instance_1")
        assert result is None

    async def test_claim_increments_fence_token(self, store):
        wf = Workflow(name="fence_inc")
        await store.insert(wf)

        claimed = await store.try_claim(wf.id, "inst")
        assert claimed.fence_token == 1

        # Release and reclaim
        await store.release_lock(wf.id, "inst", 1)
        claimed2 = await store.try_claim(wf.id, "inst")
        assert claimed2.fence_token == 2


    async def test_claim_completed_workflow_rejected(self, store):
        """Terminal workflows cannot be re-claimed even if unlocked."""
        wf = Workflow(
            name="completed_wf",
            status=WorkflowStatus.COMPLETED,
            locked_by=None,
            lock_expires_at=None,
        )
        await store.insert(wf)

        result = await store.try_claim(wf.id, "instance_1")
        assert result is None

    async def test_claim_failed_workflow_rejected(self, store):
        wf = Workflow(
            name="failed_wf",
            status=WorkflowStatus.FAILED,
            locked_by=None,
        )
        await store.insert(wf)

        result = await store.try_claim(wf.id, "instance_1")
        assert result is None


class TestHeartbeat:
    async def test_heartbeat_succeeds(self, store):
        wf = Workflow(name="hb_test")
        await store.insert(wf)
        claimed = await store.try_claim(wf.id, "inst_1")

        ok = await store.heartbeat(wf.id, "inst_1", claimed.fence_token)
        assert ok is True

    async def test_heartbeat_wrong_instance(self, store):
        wf = Workflow(name="hb_wrong_inst")
        await store.insert(wf)
        claimed = await store.try_claim(wf.id, "inst_1")

        ok = await store.heartbeat(wf.id, "inst_2", claimed.fence_token)
        assert ok is False

    async def test_heartbeat_wrong_fence(self, store):
        wf = Workflow(name="hb_wrong_fence")
        await store.insert(wf)
        claimed = await store.try_claim(wf.id, "inst_1")

        ok = await store.heartbeat(wf.id, "inst_1", claimed.fence_token + 100)
        assert ok is False


class TestReleaseLock:
    async def test_release_succeeds(self, store):
        wf = Workflow(name="release_test")
        await store.insert(wf)
        claimed = await store.try_claim(wf.id, "inst_1")

        ok = await store.release_lock(wf.id, "inst_1", claimed.fence_token)
        assert ok is True

        loaded = await store.get(wf.id)
        assert loaded.locked_by is None
        assert loaded.lock_expires_at is None

    async def test_release_wrong_instance(self, store):
        wf = Workflow(name="release_wrong")
        await store.insert(wf)
        claimed = await store.try_claim(wf.id, "inst_1")

        ok = await store.release_lock(wf.id, "inst_2", claimed.fence_token)
        assert ok is False

    async def test_release_wrong_fence(self, store):
        wf = Workflow(name="release_fence")
        await store.insert(wf)
        await store.try_claim(wf.id, "inst_1")

        ok = await store.release_lock(wf.id, "inst_1", 999)
        assert ok is False


class TestForceReleaseLock:
    async def test_force_release(self, store):
        wf = Workflow(name="force_release")
        await store.insert(wf)
        await store.try_claim(wf.id, "inst_1")

        ok = await store.force_release_lock(wf.id)
        assert ok is True

        loaded = await store.get(wf.id)
        assert loaded.locked_by is None

    async def test_force_release_nonexistent(self, store):
        ok = await store.force_release_lock("doesnt_exist")
        assert ok is False


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


class TestCancelWorkflow:
    async def test_cancel_pending_workflow(self, store):
        wf = Workflow(name="cancel_pending")
        await store.insert(wf)

        result = await store.cancel_workflow(wf.id)
        assert result is not None
        assert result.status == WorkflowStatus.CANCELLED

    async def test_cancel_running_workflow(self, store):
        wf = Workflow(name="cancel_running")
        await store.insert(wf)
        await store.try_claim(wf.id, "inst_1")

        result = await store.cancel_workflow(wf.id)
        assert result is not None
        assert result.status == WorkflowStatus.CANCELLED
        assert result.locked_by is None
        assert result.lock_expires_at is None

    async def test_cancel_completed_workflow_rejected(self, store):
        wf = Workflow(name="cancel_completed", status=WorkflowStatus.COMPLETED)
        await store.insert(wf)

        result = await store.cancel_workflow(wf.id)
        assert result is None

    async def test_cancel_already_cancelled(self, store):
        wf = Workflow(name="cancel_twice", status=WorkflowStatus.CANCELLED)
        await store.insert(wf)

        result = await store.cancel_workflow(wf.id)
        assert result is None

    async def test_cancel_nonexistent(self, store):
        result = await store.cancel_workflow("doesnt_exist")
        assert result is None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestFindClaimable:
    async def test_finds_pending(self, store):
        wf = Workflow(name="pending_wf")
        await store.insert(wf)

        ids = await store.find_claimable()
        assert wf.id in ids

    async def test_finds_expired_lock(self, store):
        wf = Workflow(
            name="expired_lock",
            status=WorkflowStatus.RUNNING,
            locked_by="dead_inst",
            lock_expires_at=datetime.now(UTC) - timedelta(seconds=10),
        )
        await store.insert(wf)

        ids = await store.find_claimable()
        assert wf.id in ids

    async def test_excludes_active_lock(self, store):
        wf = Workflow(
            name="active_lock",
            status=WorkflowStatus.RUNNING,
            locked_by="alive_inst",
            lock_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )
        await store.insert(wf)

        ids = await store.find_claimable()
        assert wf.id not in ids

    async def test_respects_limit(self, store):
        for i in range(5):
            await store.insert(Workflow(name=f"lim_{i}"))

        ids = await store.find_claimable(limit=2)
        assert len(ids) <= 2

    async def test_excludes_future_poll(self, store):
        wf = Workflow(
            name="future_poll",
            status=WorkflowStatus.RUNNING,
            locked_by=None,
            steps=[
                Step(
                    name="s1",
                    handler="mod.func",
                    status=StepStatus.BLOCKED,
                    next_poll_at=datetime.now(UTC) + timedelta(seconds=60),
                ),
            ],
        )
        await store.insert(wf)

        ids = await store.find_claimable()
        assert wf.id not in ids

    async def test_includes_past_poll(self, store):
        wf = Workflow(
            name="past_poll",
            status=WorkflowStatus.RUNNING,
            locked_by=None,
            steps=[
                Step(
                    name="s1",
                    handler="mod.func",
                    status=StepStatus.BLOCKED,
                    next_poll_at=datetime.now(UTC) - timedelta(seconds=10),
                ),
            ],
        )
        await store.insert(wf)

        ids = await store.find_claimable()
        assert wf.id in ids


class TestFindAnomalies:
    async def test_detects_stuck_step(self, store):
        wf = Workflow(
            name="stuck",
            status=WorkflowStatus.RUNNING,
            locked_by="inst",
            lock_expires_at=datetime.now(UTC) + timedelta(seconds=30),
            updated_at=datetime.now(UTC) - timedelta(seconds=600),
            steps=[
                Step(name="s1", handler="mod.func", status=StepStatus.SUBMITTED),
            ],
        )
        await store.insert(wf)

        anomalies = await store.find_anomalies(step_stuck_seconds=300)
        assert any(a["workflow_id"] == wf.id for a in anomalies)

    async def test_no_anomalies(self, store):
        wf = Workflow(name="healthy", status=WorkflowStatus.PENDING)
        await store.insert(wf)

        anomalies = await store.find_anomalies()
        assert len(anomalies) == 0


class TestFindNeedsReview:
    async def test_finds_needs_review(self, store):
        wf = Workflow(name="review", status=WorkflowStatus.NEEDS_REVIEW)
        await store.insert(wf)

        ids = await store.find_needs_review()
        assert wf.id in ids

    async def test_empty_when_none(self, store):
        ids = await store.find_needs_review()
        assert ids == []


# ---------------------------------------------------------------------------
# Collection name configuration
# ---------------------------------------------------------------------------


class TestCollectionName:
    async def test_custom_collection_name(self, mongo_db):
        store = MongoWorkflowStore(mongo_db, collection_name="my_workflows")
        assert store._col.name == "my_workflows"

        wf = Workflow(name="custom_col_test")
        await store.insert(wf)

        loaded = await store.get(wf.id)
        assert loaded is not None
        assert loaded.name == "custom_col_test"

    async def test_default_collection_name(self, mongo_db):
        store = MongoWorkflowStore(mongo_db)
        assert store._col.name == "workflows"


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------


class TestQueryAPI:
    async def test_list_workflows_all(self, store):
        for i in range(3):
            await store.insert(Workflow(name=f"wf_{i}"))

        results = await store.list_workflows()
        assert len(results) == 3

    async def test_list_workflows_filter_by_status(self, store):
        await store.insert(Workflow(name="pending_wf"))
        await store.insert(Workflow(name="completed_wf", status=WorkflowStatus.COMPLETED))
        await store.insert(Workflow(name="failed_wf", status=WorkflowStatus.FAILED))

        results = await store.list_workflows(status=WorkflowStatus.COMPLETED)
        assert len(results) == 1
        assert results[0].name == "completed_wf"

    async def test_list_workflows_filter_by_name(self, store):
        await store.insert(Workflow(name="alpha"))
        await store.insert(Workflow(name="beta"))
        await store.insert(Workflow(name="alpha"))

        results = await store.list_workflows(name="alpha")
        assert len(results) == 2
        assert all(wf.name == "alpha" for wf in results)

    async def test_list_workflows_pagination(self, store):
        for i in range(5):
            await store.insert(Workflow(name=f"page_{i}"))

        page1 = await store.list_workflows(limit=2, skip=0)
        assert len(page1) == 2

        page2 = await store.list_workflows(limit=2, skip=2)
        assert len(page2) == 2

        page3 = await store.list_workflows(limit=2, skip=4)
        assert len(page3) == 1

        # No overlap between pages
        all_ids = [wf.id for wf in page1 + page2 + page3]
        assert len(set(all_ids)) == 5

    async def test_count_by_status(self, store):
        await store.insert(Workflow(name="p1"))
        await store.insert(Workflow(name="p2"))
        await store.insert(Workflow(name="c1", status=WorkflowStatus.COMPLETED))
        await store.insert(Workflow(name="f1", status=WorkflowStatus.FAILED))

        counts = await store.count_by_status()
        assert counts.get("pending") == 2
        assert counts.get("completed") == 1
        assert counts.get("failed") == 1

    async def test_delete_completed_workflow(self, store):
        wf = Workflow(name="done", status=WorkflowStatus.COMPLETED)
        await store.insert(wf)

        deleted = await store.delete_workflow(wf.id)
        assert deleted is True

        loaded = await store.get(wf.id)
        assert loaded is None

    async def test_delete_running_workflow_rejected(self, store):
        wf = Workflow(name="active", status=WorkflowStatus.RUNNING)
        await store.insert(wf)

        deleted = await store.delete_workflow(wf.id)
        assert deleted is False

        loaded = await store.get(wf.id)
        assert loaded is not None

    async def test_delete_nonexistent(self, store):
        deleted = await store.delete_workflow("doesnt_exist")
        assert deleted is False
