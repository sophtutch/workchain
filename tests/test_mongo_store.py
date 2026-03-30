"""Tests for workchain.mongo_store — MongoWorkflowStore with mongomock-motor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from mongomock_motor import AsyncMongoMockClient

from workchain import MongoWorkflowStore, StepRun, StepStatus, WorkflowRun, WorkflowStatus
from workchain.exceptions import ConcurrentModificationError, WorkflowRunNotFoundError


@pytest.fixture
def mongo_store():
    client = AsyncMongoMockClient()
    return MongoWorkflowStore(
        client=client,
        database="test_workchain",
        owner_id="worker-1",
        lease_ttl_seconds=30,
    )


def _make_run(**kwargs) -> WorkflowRun:
    defaults = {
        "workflow_name": "test",
        "workflow_version": "1.0",
        "steps": [StepRun(step_id="s1", step_type="NoOpStep")],
    }
    defaults.update(kwargs)
    return WorkflowRun(**defaults)


class TestSaveAndLoad:
    async def test_save_assigns_id(self, mongo_store):
        run = _make_run()
        await mongo_store.save(run)
        assert run.id is not None

    async def test_load_returns_saved_run(self, mongo_store):
        run = _make_run()
        await mongo_store.save(run)
        loaded = await mongo_store.load(str(run.id))
        assert loaded.workflow_name == "test"
        assert loaded.workflow_version == "1.0"
        assert len(loaded.steps) == 1

    async def test_load_missing_raises(self, mongo_store):
        from bson import ObjectId

        with pytest.raises(WorkflowRunNotFoundError):
            await mongo_store.load(str(ObjectId()))


class TestSaveWithVersion:
    async def test_increments_version(self, mongo_store):
        run = _make_run()
        await mongo_store.save(run)
        assert run.doc_version == 0

        await mongo_store.save_with_version(run)
        assert run.doc_version == 1

    async def test_concurrent_modification_raises(self, mongo_store):
        run = _make_run()
        await mongo_store.save(run)

        # Simulate concurrent modification by bumping version in DB
        await mongo_store._collection.update_one(
            {"_id": run.id},
            {"$set": {"doc_version": 99}},
        )

        with pytest.raises(ConcurrentModificationError):
            await mongo_store.save_with_version(run)

        # Version should be rolled back on local object
        assert run.doc_version == 0

    async def test_updates_updated_at(self, mongo_store):
        run = _make_run()
        await mongo_store.save(run)
        original_updated = run.updated_at

        await mongo_store.save_with_version(run)
        assert run.updated_at >= original_updated


class TestFindByCorrelationId:
    async def test_finds_run_by_correlation_id(self, mongo_store):
        run = _make_run(
            steps=[
                StepRun(
                    step_id="wait",
                    step_type="SuspendStep",
                    status=StepStatus.SUSPENDED,
                    resume_correlation_id="corr-abc",
                )
            ]
        )
        await mongo_store.save(run)

        found = await mongo_store.find_by_correlation_id("corr-abc")
        assert found is not None
        assert found.workflow_name == "test"
        assert found.steps[0].resume_correlation_id == "corr-abc"

    async def test_returns_none_for_unknown_correlation(self, mongo_store):
        assert await mongo_store.find_by_correlation_id("no-such-id") is None


class TestLeaseManagement:
    async def test_renew_lease_success(self, mongo_store):
        run = _make_run(
            lease_owner="worker-1",
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=10),
        )
        await mongo_store.save(run)

        result = await mongo_store.renew_lease(str(run.id), "worker-1", 30)
        assert result is True

    async def test_renew_lease_wrong_owner(self, mongo_store):
        run = _make_run(
            lease_owner="other-worker",
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=10),
        )
        await mongo_store.save(run)

        result = await mongo_store.renew_lease(str(run.id), "worker-1", 30)
        assert result is False

    async def test_release_lease(self, mongo_store):
        run = _make_run(
            lease_owner="worker-1",
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=10),
        )
        await mongo_store.save(run)

        await mongo_store.release_lease(str(run.id), "worker-1")

        loaded = await mongo_store.load(str(run.id))
        assert loaded.lease_owner is None
        assert loaded.lease_expires_at is None

    async def test_release_lease_wrong_owner_noop(self, mongo_store):
        run = _make_run(
            lease_owner="worker-1",
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=10),
        )
        await mongo_store.save(run)

        await mongo_store.release_lease(str(run.id), "wrong-worker")

        loaded = await mongo_store.load(str(run.id))
        assert loaded.lease_owner == "worker-1"  # unchanged


class TestAcquireLeaseForResume:
    async def test_acquires_lease_on_unleased_run(self, mongo_store):
        run = _make_run(status=WorkflowStatus.SUSPENDED)
        await mongo_store.save(run)

        result = await mongo_store.acquire_lease_for_resume(run.id, "worker-1", 30)
        assert result is not None
        assert result.lease_owner == "worker-1"

    async def test_returns_none_when_actively_leased(self, mongo_store):
        run = _make_run(
            status=WorkflowStatus.SUSPENDED,
            lease_owner="other-worker",
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=300),
        )
        await mongo_store.save(run)

        result = await mongo_store.acquire_lease_for_resume(run.id, "worker-1", 30)
        assert result is None

    async def test_acquires_expired_lease(self, mongo_store):
        run = _make_run(
            status=WorkflowStatus.SUSPENDED,
            lease_owner="dead-worker",
            lease_expires_at=datetime.now(UTC) - timedelta(seconds=10),
        )
        await mongo_store.save(run)

        result = await mongo_store.acquire_lease_for_resume(run.id, "worker-1", 30)
        assert result is not None
        assert result.lease_owner == "worker-1"


class TestFindActionable:
    async def test_finds_run_with_past_needs_work_after(self, mongo_store):
        run = _make_run(needs_work_after=datetime.now(UTC) - timedelta(seconds=10))
        await mongo_store.save(run)

        claimed = await mongo_store.find_actionable()
        assert claimed is not None
        assert claimed.lease_owner == "worker-1"

    async def test_skips_future_needs_work_after(self, mongo_store):
        run = _make_run(needs_work_after=datetime.now(UTC) + timedelta(hours=1))
        await mongo_store.save(run)

        assert await mongo_store.find_actionable() is None

    async def test_skips_none_needs_work_after(self, mongo_store):
        run = _make_run(needs_work_after=None)
        await mongo_store.save(run)

        assert await mongo_store.find_actionable() is None

    async def test_skips_actively_leased(self, mongo_store):
        run = _make_run(
            needs_work_after=datetime.now(UTC) - timedelta(seconds=10),
            lease_owner="other-worker",
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=300),
        )
        await mongo_store.save(run)

        assert await mongo_store.find_actionable() is None

    async def test_claims_expired_lease(self, mongo_store):
        run = _make_run(
            needs_work_after=datetime.now(UTC) - timedelta(seconds=10),
            lease_owner="dead-worker",
            lease_expires_at=datetime.now(UTC) - timedelta(seconds=10),
        )
        await mongo_store.save(run)

        claimed = await mongo_store.find_actionable()
        assert claimed is not None
        assert claimed.lease_owner == "worker-1"

    async def test_returns_earliest_due_first(self, mongo_store):
        older = _make_run(needs_work_after=datetime.now(UTC) - timedelta(minutes=10))
        newer = _make_run(needs_work_after=datetime.now(UTC) - timedelta(seconds=1))
        await mongo_store.save(newer)
        await mongo_store.save(older)

        claimed = await mongo_store.find_actionable()
        assert claimed is not None
        assert claimed.id == older.id


class TestEnsureIndexes:
    async def test_ensure_indexes_does_not_raise(self, mongo_store):
        await mongo_store.ensure_indexes()  # should be idempotent

    async def test_ensure_indexes_idempotent(self, mongo_store):
        await mongo_store.ensure_indexes()
        await mongo_store.ensure_indexes()  # calling twice is safe
