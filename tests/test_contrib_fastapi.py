"""Tests for workchain.contrib.fastapi — reusable FastAPI router."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from workchain import MongoAuditLogger, MongoWorkflowStore, Step, Workflow
from workchain.contrib.fastapi import create_workchain_router

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _app_with_router():
    """Create a FastAPI app with the workchain contrib router mounted."""
    client = AsyncMongoMockClient()
    db = client["test_contrib"]
    audit_logger = MongoAuditLogger(db)
    store = MongoWorkflowStore(
        db,
        lock_ttl_seconds=10,
        audit_logger=audit_logger,
        instance_id="test-001",
    )

    app = FastAPI()
    router = create_workchain_router(store, audit_logger)
    app.include_router(router, prefix="/workflows")

    return app, store, audit_logger


@pytest.fixture
def client(_app_with_router):
    app, _store, _logger = _app_with_router
    return TestClient(app)


@pytest.fixture
def store(_app_with_router):
    _, store, _ = _app_with_router
    return store


@pytest.fixture
def audit_logger(_app_with_router):
    _, _, logger = _app_with_router
    return logger


def _make_workflow(name: str = "test_wf") -> Workflow:
    return Workflow(
        name=name,
        steps=[
            Step(name="step_a", handler="tests.noop", depends_on=[]),
            Step(name="step_b", handler="tests.noop"),
        ],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListWorkflows:
    def test_empty_list(self, client):
        resp = client.get("/workflows")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_after_insert(self, client, store):
        wf = _make_workflow()
        asyncio.get_event_loop().run_until_complete(store.insert(wf))

        resp = client.get("/workflows")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "test_wf"
        assert data[0]["status"] == "pending"
        assert data[0]["progress"] == "0/2"


class TestWorkflowStats:
    def test_stats_empty(self, client):
        resp = client.get("/workflows/stats")
        assert resp.status_code == 200

    def test_stats_after_insert(self, client, store):
        wf = _make_workflow()
        asyncio.get_event_loop().run_until_complete(store.insert(wf))

        resp = client.get("/workflows/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("pending", 0) >= 1


class TestGetWorkflow:
    def test_not_found(self, client):
        resp = client.get("/workflows/nonexistent")
        assert resp.status_code == 404

    def test_get_existing(self, client, store):
        wf = _make_workflow()
        asyncio.get_event_loop().run_until_complete(store.insert(wf))

        resp = client.get(f"/workflows/{wf.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "test_wf"
        assert len(data["steps"]) == 2
        assert data["steps"][0]["name"] == "step_a"


class TestCancelWorkflow:
    def test_cancel_not_found(self, client):
        resp = client.post("/workflows/nonexistent/cancel")
        assert resp.status_code == 404

    def test_cancel_pending(self, client, store):
        wf = _make_workflow()
        asyncio.get_event_loop().run_until_complete(store.insert(wf))

        resp = client.post(f"/workflows/{wf.id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"


class TestWorkflowReport:
    def test_report_not_found(self, client):
        resp = client.get("/workflows/nonexistent/report")
        assert resp.status_code == 404

    def test_report_no_events(self, client, store):
        """Workflow with no audit events returns a placeholder page."""
        wf = _make_workflow()
        # Insert directly to collection to bypass audit emission
        asyncio.get_event_loop().run_until_complete(
            store._col.insert_one({"_id": wf.id, **wf.model_dump(mode="python", exclude={"id"})})
        )

        resp = client.get(f"/workflows/{wf.id}/report")
        assert resp.status_code == 200
        assert "No audit events" in resp.text

    def test_report_with_events(self, client, store, audit_logger):
        """Workflow inserted via store (which emits WORKFLOW_CREATED) produces a report."""
        wf = _make_workflow()
        asyncio.get_event_loop().run_until_complete(store.insert(wf))
        # Drain pending audit writes
        asyncio.get_event_loop().run_until_complete(store.drain_audit_tasks(timeout=2.0))

        resp = client.get(f"/workflows/{wf.id}/report")
        assert resp.status_code == 200
        assert "<!DOCTYPE html>" in resp.text
        assert "Execution Report" in resp.text
