"""Tests for workchain_server.designer_router — handlers, drafts, templates."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

from workchain import MongoAuditLogger, MongoWorkflowStore
from workchain.decorators import async_step, completeness_check, step
from workchain.models import (
    CheckResult,
    PollPolicy,
    StepConfig,
    StepResult,
)
from workchain_server.designer_router import create_designer_router

# ---------------------------------------------------------------------------
# Fixture handlers
# ---------------------------------------------------------------------------


class _DesignerConfig(StepConfig):
    name: str
    count: int = 1


class _DesignerResult(StepResult):
    message: str


class _DesignerJobResult(StepResult):
    job_id: str


@step()
async def _designer_sync(
    config: _DesignerConfig, _results: dict[str, StepResult]
) -> _DesignerResult:
    """Sync designer fixture handler."""
    return _DesignerResult(message=f"hi {config.name}")


@step()
async def _designer_sync_2(
    _config: _DesignerConfig, _results: dict[str, StepResult]
) -> _DesignerResult:
    return _DesignerResult(message="two")


@completeness_check()
async def _designer_check(
    _config: StepConfig,
    _results: dict[str, StepResult],
    _result: StepResult,
) -> CheckResult:
    return CheckResult(complete=True)


@async_step(poll=PollPolicy(interval=0.1), completeness_check=_designer_check)
async def _designer_async(
    _config: _DesignerConfig, _results: dict[str, StepResult]
) -> _DesignerJobResult:
    return _DesignerJobResult(job_id="job-x")


@step()
async def _designer_untyped(_config, _results):  # type: ignore[no-untyped-def]
    return StepResult()


_SYNC = _designer_sync._step_meta["handler"]
_SYNC2 = _designer_sync_2._step_meta["handler"]
_ASYNC = _designer_async._step_meta["handler"]
_CHECK = _designer_check._step_meta["handler"]
_UNTYPED = _designer_untyped._step_meta["handler"]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _app_with_designer():
    client = AsyncMongoMockClient()
    db = client["test_designer"]
    audit_logger = MongoAuditLogger(db)
    store = MongoWorkflowStore(
        db,
        lock_ttl_seconds=10,
        audit_logger=audit_logger,
        instance_id="designer-test",
    )

    app = FastAPI()
    app.include_router(create_designer_router(store), prefix="/api/v1")
    return app, store


@pytest.fixture
def client(_app_with_designer):
    app, _ = _app_with_designer
    return TestClient(app)


@pytest.fixture
def store(_app_with_designer):
    _, store = _app_with_designer
    return store


# ---------------------------------------------------------------------------
# GET /handlers
# ---------------------------------------------------------------------------


class TestHandlers:
    def test_lists_registered_handlers(self, client) -> None:
        resp = client.get("/api/v1/handlers")
        assert resp.status_code == 200
        body = resp.json()
        names = {h["name"] for h in body}
        assert _SYNC in names
        assert _ASYNC in names
        # Completeness checks excluded by default
        assert _CHECK not in names

    def test_handler_exposes_config_schema(self, client) -> None:
        resp = client.get("/api/v1/handlers")
        handlers = {h["name"]: h for h in resp.json()}
        sync = handlers[_SYNC]
        assert sync["launchable"] is True
        assert sync["config_schema"] is not None
        assert sync["config_schema"]["properties"]["name"]["type"] == "string"
        assert sync["result_schema"] is not None
        assert sync["is_async"] is False

    def test_async_handler_includes_poll_policy(self, client) -> None:
        resp = client.get("/api/v1/handlers")
        handlers = {h["name"]: h for h in resp.json()}
        async_h = handlers[_ASYNC]
        assert async_h["is_async"] is True
        assert async_h["poll_policy"] is not None
        assert async_h["completeness_check"] == _CHECK

    def test_untyped_handler_marked_not_launchable(self, client) -> None:
        resp = client.get("/api/v1/handlers")
        handlers = {h["name"]: h for h in resp.json()}
        untyped = handlers[_UNTYPED]
        assert untyped["launchable"] is False


# ---------------------------------------------------------------------------
# POST /workflows — happy + error paths
# ---------------------------------------------------------------------------


class TestCreateWorkflow:
    def test_creates_typed_workflow(self, client, store) -> None:
        resp = client.post(
            "/api/v1/workflows",
            json={
                "name": "draft-test",
                "steps": [
                    {
                        "name": "greet",
                        "handler": _SYNC,
                        "config": {"name": "alice", "count": 3},
                        "depends_on": [],
                    }
                ],
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "draft-test"
        assert body["status"] == "pending"
        assert body["id"]

    def test_round_trip_via_store(self, client, store) -> None:
        resp = client.post(
            "/api/v1/workflows",
            json={
                "name": "roundtrip",
                "steps": [
                    {
                        "name": "greet",
                        "handler": _SYNC,
                        "config": {"name": "bob"},
                        "depends_on": [],
                    }
                ],
            },
        )
        assert resp.status_code == 201
        wf_id = resp.json()["id"]

        async def _fetch():
            return await store.get(wf_id)

        import asyncio

        wf = asyncio.get_event_loop().run_until_complete(_fetch())
        assert wf is not None
        assert isinstance(wf.steps[0].config, _DesignerConfig)
        assert wf.steps[0].config.name == "bob"

    def test_multi_step_dag(self, client) -> None:
        resp = client.post(
            "/api/v1/workflows",
            json={
                "name": "dag",
                "steps": [
                    {
                        "name": "root",
                        "handler": _SYNC,
                        "config": {"name": "r"},
                        "depends_on": [],
                    },
                    {
                        "name": "leaf",
                        "handler": _SYNC2,
                        "config": {"name": "l"},
                        "depends_on": ["root"],
                    },
                ],
            },
        )
        assert resp.status_code == 201

    def test_unknown_handler_422(self, client) -> None:
        resp = client.post(
            "/api/v1/workflows",
            json={
                "name": "bad",
                "steps": [
                    {"name": "a", "handler": "nope.does.not.exist"},
                ],
            },
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "validation failed" in detail["detail"]
        assert any("Unknown handler" in e["error"] for e in detail["errors"])

    def test_untyped_handler_rejected(self, client) -> None:
        resp = client.post(
            "/api/v1/workflows",
            json={
                "name": "bad",
                "steps": [{"name": "a", "handler": _UNTYPED}],
            },
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert any("not launchable" in e["error"] for e in detail["errors"])

    def test_invalid_config_422(self, client) -> None:
        resp = client.post(
            "/api/v1/workflows",
            json={
                "name": "bad",
                "steps": [
                    {
                        "name": "a",
                        "handler": _SYNC,
                        "config": {"name": 123},  # wrong type
                    }
                ],
            },
        )
        assert resp.status_code == 422
        errors = resp.json()["detail"]["errors"]
        assert len(errors) == 1
        assert errors[0]["step"] == "a"
        assert errors[0]["field_errors"] is not None
        # Pydantic reports the offending field as "name"
        assert any("name" in fe["loc"] for fe in errors[0]["field_errors"])

    def test_duplicate_step_names_422(self, client) -> None:
        resp = client.post(
            "/api/v1/workflows",
            json={
                "name": "dupes",
                "steps": [
                    {"name": "a", "handler": _SYNC, "config": {"name": "x"}},
                    {"name": "a", "handler": _SYNC, "config": {"name": "y"}},
                ],
            },
        )
        assert resp.status_code == 422
        assert "DAG validation failed" in resp.json()["detail"]["detail"]

    def test_cycle_422(self, client) -> None:
        resp = client.post(
            "/api/v1/workflows",
            json={
                "name": "loop",
                "steps": [
                    {
                        "name": "a",
                        "handler": _SYNC,
                        "config": {"name": "x"},
                        "depends_on": ["b"],
                    },
                    {
                        "name": "b",
                        "handler": _SYNC,
                        "config": {"name": "y"},
                        "depends_on": ["a"],
                    },
                ],
            },
        )
        assert resp.status_code == 422
        assert any(
            "cycle" in e["error"].lower()
            for e in resp.json()["detail"]["errors"]
        )

    def test_unknown_depends_on_422(self, client) -> None:
        resp = client.post(
            "/api/v1/workflows",
            json={
                "name": "ghost",
                "steps": [
                    {
                        "name": "a",
                        "handler": _SYNC,
                        "config": {"name": "x"},
                        "depends_on": ["nobody"],
                    }
                ],
            },
        )
        assert resp.status_code == 422

    def test_collects_all_step_errors(self, client) -> None:
        """A draft with two bad steps returns both errors in one response."""
        resp = client.post(
            "/api/v1/workflows",
            json={
                "name": "multi-err",
                "steps": [
                    {"name": "a", "handler": "unknown.one"},
                    {"name": "b", "handler": "unknown.two"},
                ],
            },
        )
        assert resp.status_code == 422
        errors = resp.json()["detail"]["errors"]
        assert {e["step"] for e in errors} == {"a", "b"}


# ---------------------------------------------------------------------------
# Templates CRUD
# ---------------------------------------------------------------------------


def _template_payload(name: str = "tpl-1") -> dict:
    return {
        "name": name,
        "description": "designer fixture",
        "steps": [
            {
                "name": "greet",
                "handler": _SYNC,
                "config": {"name": "hello"},
            }
        ],
    }


class TestTemplatesCRUD:
    def test_create_and_get(self, client) -> None:
        created = client.post("/api/v1/templates", json=_template_payload())
        assert created.status_code == 201
        tpl = created.json()
        assert tpl["version"] == 1
        assert tpl["id"]

        fetched = client.get(f"/api/v1/templates/{tpl['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "tpl-1"

    def test_get_404(self, client) -> None:
        resp = client.get("/api/v1/templates/nonexistent")
        assert resp.status_code == 404

    def test_list(self, client) -> None:
        for n in range(3):
            client.post("/api/v1/templates", json=_template_payload(f"tpl-{n}"))
        resp = client.get("/api/v1/templates")
        assert resp.status_code == 200
        names = {t["name"] for t in resp.json()}
        assert names == {"tpl-0", "tpl-1", "tpl-2"}

    def test_list_honours_limit(self, client) -> None:
        for n in range(5):
            client.post("/api/v1/templates", json=_template_payload(f"tpl-{n}"))
        resp = client.get("/api/v1/templates?limit=2")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_update_success(self, client) -> None:
        created = client.post("/api/v1/templates", json=_template_payload()).json()
        resp = client.put(
            f"/api/v1/templates/{created['id']}",
            json={
                "expected_version": 1,
                "name": "renamed",
                "description": "new",
            },
        )
        assert resp.status_code == 200
        updated = resp.json()
        assert updated["name"] == "renamed"
        assert updated["description"] == "new"
        assert updated["version"] == 2

    def test_update_stale_version_409(self, client) -> None:
        created = client.post("/api/v1/templates", json=_template_payload()).json()
        # First update succeeds, bumps to version 2
        client.put(
            f"/api/v1/templates/{created['id']}",
            json={"expected_version": 1, "name": "v2"},
        )
        # Second update with stale version=1 returns 409
        resp = client.put(
            f"/api/v1/templates/{created['id']}",
            json={"expected_version": 1, "name": "v3"},
        )
        assert resp.status_code == 409

    def test_update_404(self, client) -> None:
        resp = client.put(
            "/api/v1/templates/nope",
            json={"expected_version": 1, "name": "x"},
        )
        assert resp.status_code == 404

    def test_delete(self, client) -> None:
        created = client.post("/api/v1/templates", json=_template_payload()).json()
        resp = client.delete(f"/api/v1/templates/{created['id']}")
        assert resp.status_code == 204
        assert client.get(f"/api/v1/templates/{created['id']}").status_code == 404

    def test_delete_404(self, client) -> None:
        assert client.delete("/api/v1/templates/nope").status_code == 404


# ---------------------------------------------------------------------------
# Template launch
# ---------------------------------------------------------------------------


class TestTemplateLaunch:
    def test_launch_creates_workflow(self, client, store) -> None:
        created = client.post("/api/v1/templates", json=_template_payload()).json()
        resp = client.post(f"/api/v1/templates/{created['id']}/launch", json={})
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "pending"
        assert body["name"] == "tpl-1"

        async def _fetch():
            return await store.get(body["id"])

        import asyncio

        wf = asyncio.get_event_loop().run_until_complete(_fetch())
        assert wf is not None
        assert isinstance(wf.steps[0].config, _DesignerConfig)

    def test_launch_with_override(self, client, store) -> None:
        created = client.post("/api/v1/templates", json=_template_payload()).json()
        resp = client.post(
            f"/api/v1/templates/{created['id']}/launch",
            json={
                "name_override": "custom-run",
                "config_overrides": {"greet": {"name": "override-name"}},
            },
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "custom-run"

        async def _fetch():
            return await store.get(resp.json()["id"])

        import asyncio

        wf = asyncio.get_event_loop().run_until_complete(_fetch())
        assert wf is not None
        assert isinstance(wf.steps[0].config, _DesignerConfig)
        assert wf.steps[0].config.name == "override-name"

    def test_launch_404(self, client) -> None:
        resp = client.post("/api/v1/templates/nope/launch", json={})
        assert resp.status_code == 404
