"""Tests for workchain.store — MongoWorkflowStore with mongomock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import GreetConfig, GreetResult
from workchain.models import (
    Step,
    StepConfig,
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
        wf = Workflow(name="cancel_running", status=WorkflowStatus.RUNNING)
        wf.steps = [
            Step(name="s1", handler="mod.func", locked_by="inst_1"),
            Step(name="s2", handler="mod.func"),
        ]
        await store.insert(wf)

        result = await store.cancel_workflow(wf.id)
        assert result is not None
        assert result.status == WorkflowStatus.CANCELLED
        # Step-level locks must be cleared on cancellation
        assert result.steps[0].locked_by is None
        assert result.steps[0].lock_expires_at is None

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


class TestFindAnomalies:
    async def test_detects_stuck_step(self, store):
        wf = Workflow(
            name="stuck",
            status=WorkflowStatus.RUNNING,
            updated_at=datetime.now(UTC) - timedelta(seconds=600),
            steps=[
                Step(
                    name="s1", handler="mod.func", status=StepStatus.SUBMITTED,
                    locked_by="inst",
                    lock_expires_at=datetime.now(UTC) + timedelta(seconds=30),
                    depends_on=[],
                ),
            ],
        )
        await store.insert(wf)

        anomalies = await store.find_anomalies(step_stuck_seconds=300)
        assert any(
            a["workflow_id"] == wf.id and a["step_name"] == "s1"
            for a in anomalies
        )

    async def test_no_anomalies(self, store):
        wf = Workflow(name="healthy", status=WorkflowStatus.PENDING)
        await store.insert(wf)

        anomalies = await store.find_anomalies()
        assert len(anomalies) == 0

    async def test_detects_stale_step_lock(self, store):
        """A step with an expired lock and stale workflow updated_at is an anomaly."""
        wf = Workflow(
            name="stale_lock",
            status=WorkflowStatus.RUNNING,
            updated_at=datetime.now(UTC) - timedelta(seconds=600),
            steps=[
                Step(
                    name="s1", handler="mod.func", status=StepStatus.BLOCKED,
                    locked_by="dead_inst",
                    lock_expires_at=datetime.now(UTC) - timedelta(seconds=60),
                    depends_on=[],
                ),
            ],
        )
        await store.insert(wf)

        anomalies = await store.find_anomalies(step_stuck_seconds=300)
        stale = [a for a in anomalies if a["anomaly"] == "stale_step_lock"]
        assert any(a["workflow_id"] == wf.id and a["step_name"] == "s1" for a in stale)

    async def test_detects_orphaned_workflow(self, store):
        """A RUNNING workflow with all steps terminal is an orphaned workflow."""
        wf = Workflow(
            name="orphaned",
            status=WorkflowStatus.RUNNING,
            updated_at=datetime.now(UTC) - timedelta(seconds=600),
            steps=[
                Step(name="s1", handler="mod.func", status=StepStatus.COMPLETED, depends_on=[]),
                Step(name="s2", handler="mod.func", status=StepStatus.COMPLETED, depends_on=["s1"]),
            ],
        )
        await store.insert(wf)

        anomalies = await store.find_anomalies(step_stuck_seconds=300)
        orphaned = [a for a in anomalies if a["anomaly"] == "orphaned_workflow"]
        assert any(a["workflow_id"] == wf.id for a in orphaned)


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


# ---------------------------------------------------------------------------
# Operation timeouts
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Per-step distributed locking
# ---------------------------------------------------------------------------


class TestTryClaimStep:
    async def test_claim_pending_step(self, store):
        wf = Workflow(
            name="claim_step_test",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[]),
                Step(name="b", handler="mod.func", depends_on=["a"]),
            ],
        )
        await store.insert(wf)

        result = await store.try_claim_step(wf.id, "a", "inst_1")
        assert result is not None
        claimed_wf, fence = result
        assert fence == 1
        step_a = claimed_wf.step_by_name("a")
        assert step_a.locked_by == "inst_1"
        assert step_a.lock_expires_at is not None
        assert claimed_wf.status == WorkflowStatus.RUNNING

    async def test_claim_already_locked_step_rejected(self, store):
        wf = Workflow(
            name="locked_step",
            steps=[Step(name="a", handler="mod.func", depends_on=[])],
        )
        await store.insert(wf)

        result1 = await store.try_claim_step(wf.id, "a", "inst_1")
        assert result1 is not None

        result2 = await store.try_claim_step(wf.id, "a", "inst_2")
        assert result2 is None

    async def test_claim_expired_step_lock(self, store):
        wf = Workflow(
            name="expired_step_lock",
            steps=[
                Step(
                    name="a", handler="mod.func", depends_on=[],
                    locked_by="old_inst",
                    lock_expires_at=datetime.now(UTC) - timedelta(seconds=10),
                    fence_token=1,
                ),
            ],
        )
        await store.insert(wf)

        result = await store.try_claim_step(wf.id, "a", "new_inst")
        assert result is not None
        claimed_wf, fence = result
        assert fence == 2
        assert claimed_wf.step_by_name("a").locked_by == "new_inst"

    async def test_claim_two_different_steps_concurrently(self, store):
        """Two instances can claim different steps of the same workflow."""
        wf = Workflow(
            name="concurrent_claims",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[]),
                Step(name="b", handler="mod.func", depends_on=[]),
            ],
        )
        await store.insert(wf)

        result_a = await store.try_claim_step(wf.id, "a", "inst_1")
        result_b = await store.try_claim_step(wf.id, "b", "inst_2")
        assert result_a is not None
        assert result_b is not None

        # Verify both steps are locked by different instances
        final = await store.get(wf.id)
        assert final.step_by_name("a").locked_by == "inst_1"
        assert final.step_by_name("b").locked_by == "inst_2"

    async def test_claim_nonexistent_step_returns_none(self, store):
        wf = Workflow(
            name="no_step",
            steps=[Step(name="a", handler="mod.func", depends_on=[])],
        )
        await store.insert(wf)

        result = await store.try_claim_step(wf.id, "z", "inst_1")
        assert result is None

    async def test_claim_terminal_workflow_rejected(self, store):
        wf = Workflow(
            name="completed_wf",
            status=WorkflowStatus.COMPLETED,
            steps=[Step(name="a", handler="mod.func", depends_on=[])],
        )
        await store.insert(wf)

        result = await store.try_claim_step(wf.id, "a", "inst_1")
        assert result is None

    async def test_claim_completed_step_rejected(self, store):
        """Cannot reclaim a step that has already completed."""
        wf = Workflow(
            name="completed_step",
            status=WorkflowStatus.RUNNING,
            steps=[
                Step(name="a", handler="mod.func", depends_on=[], status=StepStatus.COMPLETED),
                Step(name="b", handler="mod.func", depends_on=["a"]),
            ],
        )
        await store.insert(wf)

        result = await store.try_claim_step(wf.id, "a", "inst_1")
        assert result is None

    async def test_claim_failed_step_rejected(self, store):
        """Cannot reclaim a step that has failed."""
        wf = Workflow(
            name="failed_step",
            status=WorkflowStatus.RUNNING,
            steps=[
                Step(name="a", handler="mod.func", depends_on=[], status=StepStatus.FAILED),
            ],
        )
        await store.insert(wf)

        result = await store.try_claim_step(wf.id, "a", "inst_1")
        assert result is None

    async def test_claim_increments_step_fence_token(self, store):
        wf = Workflow(
            name="fence_inc",
            steps=[Step(name="a", handler="mod.func", depends_on=[])],
        )
        await store.insert(wf)

        result = await store.try_claim_step(wf.id, "a", "inst_1")
        assert result is not None
        _, fence1 = result
        assert fence1 == 1

        # Release and re-claim
        await store.release_step_lock(wf.id, "a", "inst_1", fence1)
        result2 = await store.try_claim_step(wf.id, "a", "inst_1")
        assert result2 is not None
        _, fence2 = result2
        assert fence2 == 2

    async def test_concurrent_claim_same_step_only_one_wins(self, store):
        """When two instances race to claim the same step, only one succeeds."""
        wf = Workflow(
            name="race",
            steps=[Step(name="a", handler="mod.func", depends_on=[])],
        )
        await store.insert(wf)

        result_1 = await store.try_claim_step(wf.id, "a", "inst_1")
        result_2 = await store.try_claim_step(wf.id, "a", "inst_2")

        # Exactly one should succeed
        assert (result_1 is None) != (result_2 is None)

        winner = result_1 if result_1 is not None else result_2
        wf_after, fence = winner
        step = wf_after.step_by_name("a")
        assert step.locked_by in ("inst_1", "inst_2")
        assert fence == 1


class TestHeartbeatStep:
    async def test_heartbeat_succeeds(self, store):
        wf = Workflow(
            name="hb_step",
            steps=[Step(name="a", handler="mod.func", depends_on=[])],
        )
        await store.insert(wf)

        result = await store.try_claim_step(wf.id, "a", "inst_1")
        _, fence = result

        ok = await store.heartbeat_step(wf.id, "a", "inst_1", fence)
        assert ok is True

    async def test_heartbeat_wrong_instance(self, store):
        wf = Workflow(
            name="hb_wrong",
            steps=[Step(name="a", handler="mod.func", depends_on=[])],
        )
        await store.insert(wf)

        result = await store.try_claim_step(wf.id, "a", "inst_1")
        _, fence = result

        ok = await store.heartbeat_step(wf.id, "a", "inst_2", fence)
        assert ok is False

    async def test_heartbeat_wrong_fence(self, store):
        wf = Workflow(
            name="hb_fence",
            steps=[Step(name="a", handler="mod.func", depends_on=[])],
        )
        await store.insert(wf)

        result = await store.try_claim_step(wf.id, "a", "inst_1")
        _, fence = result

        ok = await store.heartbeat_step(wf.id, "a", "inst_1", fence + 100)
        assert ok is False


class TestReleaseStepLock:
    async def test_release_succeeds(self, store):
        wf = Workflow(
            name="release_step",
            steps=[Step(name="a", handler="mod.func", depends_on=[])],
        )
        await store.insert(wf)

        result = await store.try_claim_step(wf.id, "a", "inst_1")
        _, fence = result

        ok = await store.release_step_lock(wf.id, "a", "inst_1", fence)
        assert ok is True

        loaded = await store.get(wf.id)
        assert loaded.step_by_name("a").locked_by is None
        assert loaded.step_by_name("a").lock_expires_at is None

    async def test_release_wrong_instance(self, store):
        wf = Workflow(
            name="release_wrong",
            steps=[Step(name="a", handler="mod.func", depends_on=[])],
        )
        await store.insert(wf)

        result = await store.try_claim_step(wf.id, "a", "inst_1")
        _, fence = result

        ok = await store.release_step_lock(wf.id, "a", "inst_2", fence)
        assert ok is False

    async def test_release_wrong_fence(self, store):
        wf = Workflow(
            name="release_fence",
            steps=[Step(name="a", handler="mod.func", depends_on=[])],
        )
        await store.insert(wf)

        result = await store.try_claim_step(wf.id, "a", "inst_1")
        assert result is not None

        ok = await store.release_step_lock(wf.id, "a", "inst_1", 999)
        assert ok is False


class TestForceReleaseStepLock:
    async def test_force_release(self, store):
        wf = Workflow(
            name="force_release_step",
            steps=[Step(name="a", handler="mod.func", depends_on=[])],
        )
        await store.insert(wf)
        await store.try_claim_step(wf.id, "a", "inst_1")

        ok = await store.force_release_step_lock(wf.id, "a")
        assert ok is True

        loaded = await store.get(wf.id)
        assert loaded.step_by_name("a").locked_by is None
        # Force-release must bump fence to invalidate the old holder's writes
        assert loaded.step_by_name("a").fence_token == 2

    async def test_force_release_nonexistent_step(self, store):
        wf = Workflow(
            name="no_step",
            steps=[Step(name="a", handler="mod.func", depends_on=[])],
        )
        await store.insert(wf)

        ok = await store.force_release_step_lock(wf.id, "z")
        assert ok is False


# ---------------------------------------------------------------------------
# Per-step fenced updates
# ---------------------------------------------------------------------------


class TestFencedStepUpdateByName:
    async def test_update_succeeds(self, store):
        wf = Workflow(
            name="fenced_name",
            steps=[Step(name="a", handler="mod.func", depends_on=[], fence_token=1)],
        )
        await store.insert(wf)

        result = await store._fenced_step_update_by_name(
            wf.id, "a", 1, {"status": StepStatus.SUBMITTED.value},
        )
        assert result is not None
        assert result.step_by_name("a").status == StepStatus.SUBMITTED

    async def test_update_rejected_wrong_fence(self, store):
        wf = Workflow(
            name="fenced_reject",
            steps=[Step(name="a", handler="mod.func", depends_on=[], fence_token=1)],
        )
        await store.insert(wf)

        result = await store._fenced_step_update_by_name(
            wf.id, "a", 999, {"status": StepStatus.SUBMITTED.value},
        )
        assert result is None

    async def test_update_wrong_step_name(self, store):
        wf = Workflow(
            name="fenced_wrong_name",
            steps=[Step(name="a", handler="mod.func", depends_on=[], fence_token=1)],
        )
        await store.insert(wf)

        result = await store._fenced_step_update_by_name(
            wf.id, "z", 1, {"status": StepStatus.SUBMITTED.value},
        )
        assert result is None


# ---------------------------------------------------------------------------
# Per-step discovery
# ---------------------------------------------------------------------------


class TestFindClaimableSteps:
    async def test_finds_ready_root_steps(self, store):
        wf = Workflow(
            name="ready_roots",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[]),
                Step(name="b", handler="mod.func", depends_on=[]),
            ],
        )
        await store.insert(wf)

        claimable = await store.find_claimable_steps()
        assert (wf.id, "a") in claimable
        assert (wf.id, "b") in claimable

    async def test_excludes_dependent_steps(self, store):
        wf = Workflow(
            name="deps",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[]),
                Step(name="b", handler="mod.func", depends_on=["a"]),
            ],
        )
        await store.insert(wf)

        claimable = await store.find_claimable_steps()
        assert (wf.id, "a") in claimable
        assert (wf.id, "b") not in claimable

    async def test_excludes_locked_steps(self, store):
        wf = Workflow(
            name="locked_step",
            steps=[
                Step(name="a", handler="mod.func", depends_on=[], locked_by="inst_1"),
            ],
        )
        await store.insert(wf)

        claimable = await store.find_claimable_steps()
        assert (wf.id, "a") not in claimable

    async def test_includes_expired_lock_steps(self, store):
        """Steps with expired locks should be discoverable for reclaiming."""
        wf = Workflow(
            name="expired_lock",
            steps=[
                Step(
                    name="a", handler="mod.func", depends_on=[],
                    locked_by="crashed_inst",
                    lock_expires_at=datetime.now(UTC) - timedelta(seconds=10),
                ),
            ],
        )
        await store.insert(wf)

        claimable = await store.find_claimable_steps()
        assert (wf.id, "a") in claimable

    async def test_includes_pollable_blocked_step(self, store):
        wf = Workflow(
            name="pollable",
            status=WorkflowStatus.RUNNING,
            steps=[
                Step(
                    name="a", handler="mod.func", depends_on=[],
                    status=StepStatus.BLOCKED,
                    next_poll_at=datetime.now(UTC) - timedelta(seconds=10),
                ),
            ],
        )
        await store.insert(wf)

        claimable = await store.find_claimable_steps()
        assert (wf.id, "a") in claimable

    async def test_excludes_future_poll(self, store):
        wf = Workflow(
            name="future_poll",
            status=WorkflowStatus.RUNNING,
            steps=[
                Step(
                    name="a", handler="mod.func", depends_on=[],
                    status=StepStatus.BLOCKED,
                    next_poll_at=datetime.now(UTC) + timedelta(seconds=60),
                ),
            ],
        )
        await store.insert(wf)

        claimable = await store.find_claimable_steps()
        assert (wf.id, "a") not in claimable

    async def test_excludes_terminal_workflow(self, store):
        wf = Workflow(
            name="completed",
            status=WorkflowStatus.COMPLETED,
            steps=[Step(name="a", handler="mod.func", depends_on=[])],
        )
        await store.insert(wf)

        claimable = await store.find_claimable_steps()
        assert (wf.id, "a") not in claimable

    async def test_respects_limit(self, store):
        for i in range(5):
            wf = Workflow(
                name=f"lim_{i}",
                steps=[Step(name="a", handler="mod.func", depends_on=[])],
            )
            await store.insert(wf)

        claimable = await store.find_claimable_steps(limit=2)
        assert len(claimable) <= 2


# ---------------------------------------------------------------------------
# Workflow status transitions
# ---------------------------------------------------------------------------


class TestTryCompleteWorkflow:
    async def test_completes_when_all_steps_done(self, store):
        wf = Workflow(
            name="complete_test",
            status=WorkflowStatus.RUNNING,
            steps=[
                Step(name="a", handler="mod.func", depends_on=[], status=StepStatus.COMPLETED),
                Step(name="b", handler="mod.func", depends_on=["a"], status=StepStatus.COMPLETED),
            ],
        )
        await store.insert(wf)

        result = await store.try_complete_workflow(wf.id)
        assert result is not None
        assert result.status == WorkflowStatus.COMPLETED

    async def test_does_not_complete_with_pending_steps(self, store):
        wf = Workflow(
            name="incomplete",
            status=WorkflowStatus.RUNNING,
            steps=[
                Step(name="a", handler="mod.func", depends_on=[], status=StepStatus.COMPLETED),
                Step(name="b", handler="mod.func", depends_on=["a"], status=StepStatus.PENDING),
            ],
        )
        await store.insert(wf)

        result = await store.try_complete_workflow(wf.id)
        assert result is None

    async def test_does_not_complete_with_failed_step(self, store):
        wf = Workflow(
            name="failed_step",
            status=WorkflowStatus.RUNNING,
            steps=[
                Step(name="a", handler="mod.func", depends_on=[], status=StepStatus.COMPLETED),
                Step(name="b", handler="mod.func", depends_on=["a"], status=StepStatus.FAILED),
            ],
        )
        await store.insert(wf)

        result = await store.try_complete_workflow(wf.id)
        assert result is None

    async def test_does_not_complete_empty_workflow(self, store):
        wf = Workflow(name="empty", status=WorkflowStatus.RUNNING)
        await store.insert(wf)

        result = await store.try_complete_workflow(wf.id)
        assert result is None

    async def test_does_not_complete_already_completed(self, store):
        wf = Workflow(
            name="already_done",
            status=WorkflowStatus.COMPLETED,
            steps=[
                Step(name="a", handler="mod.func", depends_on=[], status=StepStatus.COMPLETED),
            ],
        )
        await store.insert(wf)

        result = await store.try_complete_workflow(wf.id)
        assert result is None


class TestTryFailWorkflow:
    async def test_fails_running_workflow(self, store):
        wf = Workflow(
            name="fail_test",
            status=WorkflowStatus.RUNNING,
            steps=[
                Step(name="a", handler="mod.func", depends_on=[], status=StepStatus.FAILED),
            ],
        )
        await store.insert(wf)

        result = await store.try_fail_workflow(wf.id)
        assert result is not None
        assert result.status == WorkflowStatus.FAILED

    async def test_does_not_fail_pending_workflow(self, store):
        """Only RUNNING workflows can be failed — PENDING should use cancel()."""
        wf = Workflow(
            name="fail_pending",
            steps=[Step(name="a", handler="mod.func", depends_on=[])],
        )
        await store.insert(wf)

        result = await store.try_fail_workflow(wf.id)
        assert result is None

    async def test_does_not_fail_already_terminal(self, store):
        wf = Workflow(
            name="already_failed",
            status=WorkflowStatus.COMPLETED,
            steps=[Step(name="a", handler="mod.func", depends_on=[])],
        )
        await store.insert(wf)

        result = await store.try_fail_workflow(wf.id)
        assert result is None


class TestOperationTimeouts:
    def test_default_timeout(self, mongo_db):
        store = MongoWorkflowStore(mongo_db)
        assert store._op_timeout == 30_000

    def test_custom_timeout(self, mongo_db):
        store = MongoWorkflowStore(mongo_db, operation_timeout_ms=10_000)
        assert store._op_timeout == 10_000

    def test_zero_timeout_rejected(self, mongo_db):
        with pytest.raises(ValueError, match="operation_timeout_ms must be positive"):
            MongoWorkflowStore(mongo_db, operation_timeout_ms=0)

    def test_negative_timeout_rejected(self, mongo_db):
        with pytest.raises(ValueError, match="operation_timeout_ms must be positive"):
            MongoWorkflowStore(mongo_db, operation_timeout_ms=-1)
